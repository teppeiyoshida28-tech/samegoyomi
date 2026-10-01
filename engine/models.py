"""共有予測モデル (バックテストと予報本体の両方から使用)

- EnvModels: hindcast(2015〜) + Open-Meteo API (past 31日 + future 16日) の環境ストア
- 学習モデル (リッジ回帰, leak-free):
    * size   : 群れ規模クラス 0-4
    * vis    : 透明度 (m)  — 改善版: 直近実測透明度+経過日数, 72h降水, 48h波, 流速 を追加
    * cancel : 欠航(ショップ報告なし)確率 — 波・風・降水・季節から学習
- 出現ポイント: 月別出現気候値 × 潮流エンジンのブレンド Top3
"""
import bisect
import json
import math
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

import forecast_engine as fe
import weight_learner as wl

ROOT = Path(__file__).parent
PH1 = ROOT.parent / "data"

SIZE_NUM = {"large": 4, "medium": 3, "small": 2, "single": 1, None: 0}
SIZE_JA = {4: "大群(100+)", 3: "中群(50前後)", 2: "小群(20前後)", 1: "単体〜数匹", 0: "なし"}


def size_to_count(x):
    pts = [(0, 0), (1, 3), (2, 25), (3, 60), (4, 150)]
    x = max(0.0, min(4.0, x))
    for (a, ca), (b, cb) in zip(pts, pts[1:]):
        if x <= b:
            return ca + (cb - ca) * (x - a) / (b - a)
    return 150


TIER_BANDS = {"S": (90, 100), "A": (80, 89), "B": (70, 79),
              "C": (55, 69), "D": (40, 54), "E": (10, 39)}


def blended_score(size_num, vis_m, cancel_prob, tier=None):
    """0-100 の総合スコア (規模60% + 透明度25% + 出航見込み15%)。
    tier を渡すとランク帯 (S:90+ A:80s B:70s C:55-69 D:40-54 E:<40) に収めて
    「スコア75なのにD」のような矛盾を防ぐ。帯内の位置は raw スコアで決まる。"""
    raw = (0.60 * size_num / 4.0
           + 0.25 * min(1.0, vis_m / 25.0)
           + 0.15 * (1.0 - cancel_prob))
    if tier is None:
        return round(100 * raw, 1)
    lo, hi = TIER_BANDS.get(tier, (10, 100))
    return round(lo + (hi - lo) * raw, 1)


def tier_matrix(vis, cnt):
    if vis >= 20 and cnt >= 100: return "S", "◆◆◆ 青潮×大群"
    if cnt >= 100: return "A", "◆◆ 大群"
    if vis >= 20 and cnt >= 50: return "B", "◆◆ 青い海×中群"
    if vis >= 15 and cnt >= 20: return "C", "◆ 中規模群"
    if vis >= 10 and cnt >= 40: return "C", "◆ 中群(濁り気味)"
    if vis >= 10 and cnt >= 1: return "D", "△ 単体〜小群"
    return "E", "✕ 濁り/不在"


def fit_ridge(X, y):
    """標準化 + ridge。predict(row) を返す"""
    m = len(X[0])
    mu = [statistics.mean(col) for col in zip(*X)]
    sd = [statistics.pstdev(col) or 1.0 for col in zip(*X)]
    Xs = [[(row[j] - mu[j]) / sd[j] for j in range(m)] for row in X]
    coefs = wl.linreg_lsq(Xs, y)
    if coefs is None:
        mean_y = statistics.mean(y)
        return lambda row: mean_y
    bias, ws = coefs[0], coefs[1:]

    def predict(row):
        return bias + sum(w * (row[j] - mu[j]) / sd[j] for j, w in enumerate(ws))
    return predict


class EnvModels:
    def __init__(self, future_days=16, quiet=False):
        self.quiet = quiet
        self._log("Loading env hindcast...")
        m = json.load(open(PH1 / "hindcast_marine.json"))["hourly"]
        w = json.load(open(PH1 / "hindcast_weather.json"))["hourly"]
        self.M, self.W = {}, {}
        for i, t in enumerate(m["time"]):
            self.M[t] = (m["sea_surface_temperature"][i], m["ocean_current_velocity"][i],
                         m["ocean_current_direction"][i], m["wave_height"][i],
                         m["sea_level_height_msl"][i])
        for i, t in enumerate(w["time"]):
            self.W[t] = (w["wind_speed_10m"][i], w["wind_direction_10m"][i], w["precipitation"][i])
        # API で直近ギャップ + 未来を補完
        try:
            r = requests.get("https://marine-api.open-meteo.com/v1/marine", params={
                "latitude": fe.LAT, "longitude": fe.LON,
                "hourly": "sea_surface_temperature,ocean_current_velocity,ocean_current_direction,"
                          "wave_height,sea_level_height_msl",
                "past_days": 31, "forecast_days": future_days, "timezone": fe.TZ,
                "cell_selection": "sea"}, timeout=60)
            h = r.json()["hourly"]
            for i, t in enumerate(h["time"]):
                self.M[t] = (h["sea_surface_temperature"][i], h["ocean_current_velocity"][i],
                             h["ocean_current_direction"][i], h["wave_height"][i],
                             h["sea_level_height_msl"][i])
        except Exception as e:
            self._log(f"  marine fetch failed: {e}")
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": fe.LAT, "longitude": fe.LON,
                "hourly": "wind_speed_10m,wind_direction_10m,precipitation",
                "past_days": 31, "forecast_days": future_days, "timezone": fe.TZ,
                "wind_speed_unit": "ms"}, timeout=60)
            h = r.json()["hourly"]
            for i, t in enumerate(h["time"]):
                self.W[t] = (h["wind_speed_10m"][i], h["wind_direction_10m"][i], h["precipitation"][i])
        except Exception as e:
            self._log(f"  weather fetch failed: {e}")

        self.clim = fe.load_climatology()
        self.obs = fe.load_blog_observations()
        self.obs_dates = sorted(self.obs.keys())
        self.obs_temp_days = [(d, (s["water_temp_lo"] + s["water_temp_hi"]) / 2)
                              for d, s in sorted(self.obs.items())
                              if s.get("water_temp_lo") is not None]
        self.temp_dates = [x[0] for x in self.obs_temp_days]
        # OM 日平均 SST (8-14時)
        acc = {}
        for t, rec in self.M.items():
            h = int(t[11:13])
            if 8 <= h <= 14 and rec[0] is not None:
                acc.setdefault(t[:10], []).append(rec[0])
        self.om_daily = {d: statistics.mean(v) for d, v in acc.items()}
        self._agg_cache = {}
        self.trained = False
        self._log(f"  env hours: marine={len(self.M)}, weather={len(self.W)}, obs={len(self.obs)}")

    def _log(self, msg):
        if not self.quiet:
            print(msg, flush=True)

    # ---------- env ----------
    def day_env(self, d):
        recs = []
        for h in range(8, 15):
            t = f"{d}T{h:02d}:00"
            if t not in self.M or t not in self.W:
                return None
            sst, cv, cd, wave, lvl = self.M[t]
            wind, wdir, pr = self.W[t]
            tm = datetime.fromisoformat(t)
            lp = self.M.get((tm + timedelta(hours=1)).isoformat(timespec="minutes"), (None,) * 5)[4]
            ln = self.M.get((tm - timedelta(hours=1)).isoformat(timespec="minutes"), (None,) * 5)[4]
            if lp is not None and ln is not None:
                dh = (lp - ln) / 2.0
            elif lp is not None and lvl is not None:
                dh = lp - lvl
            elif ln is not None and lvl is not None:
                dh = lvl - ln
            else:
                dh = 0.0
            base_v = cv or 0
            base_d = cd if cd is not None else 0
            ue, vn = fe.deg_to_vec(base_d)
            ue *= base_v
            vn *= base_v
            tid = -dh * 20.0
            speed = math.hypot(ue + tid, vn)
            cdir = math.degrees(math.atan2(ue + tid, vn)) % 360
            warm = (base_d + 180) % 360 if base_v > 0.3 else None
            recs.append({"sst": sst, "speed": speed, "cdir": cdir, "warm": warm,
                         "wave": wave or 0, "wind": wind or 0, "precip": pr or 0})
        return recs

    def sst_offset(self, d):
        i = bisect.bisect_left(self.temp_dates, d)
        diffs = []
        for j in range(max(0, i - 5), i):
            rd, ot = self.obs_temp_days[j]
            if rd in self.om_daily:
                diffs.append(ot - self.om_daily[rd])
        return statistics.mean(diffs) if diffs else 0.0

    def _range_env(self, d, back_days, end_hour):
        """[D-back_days T00, D T end_hour] の (max_wave, precip_sum, max_wind)"""
        d_dt = date.fromisoformat(d)
        max_wave = 0.0
        precip = 0.0
        max_wind = 0.0
        for db in range(back_days, -1, -1):
            dd = (d_dt - timedelta(days=db)).isoformat()
            last_h = 23 if db > 0 else end_hour
            for h in range(0, last_h + 1):
                t = f"{dd}T{h:02d}:00"
                mrec = self.M.get(t)
                wrec = self.W.get(t)
                if mrec and mrec[3] is not None:
                    max_wave = max(max_wave, mrec[3])
                if wrec:
                    if wrec[2] is not None:
                        precip += wrec[2]
                    if wrec[0] is not None:
                        max_wind = max(max_wind, wrec[0])
        return max_wave, precip, max_wind

    def day_agg(self, d):
        if d in self._agg_cache:
            return self._agg_cache[d]
        env = self.day_env(d)
        if env is None:
            self._agg_cache[d] = None
            return None
        off = self.sst_offset(d)
        key = d[5:]
        clim_mean = self.clim["daily_climatology"].get(key, {}).get("mean")
        ssts = [(r["sst"] + off) if r["sst"] is not None else (clim_mean or 22.0) for r in env]
        avg_sst = statistics.mean(ssts)
        anom = avg_sst - clim_mean if clim_mean is not None else 0.0
        wave48, _, _ = self._range_env(d, 1, 14)
        _, precip72, _ = self._range_env(d, 3, 14)
        agg = {
            "env": env,
            "avg_sst": avg_sst, "anom": anom,
            "avg_speed": statistics.mean(r["speed"] for r in env),
            "max_wave": max(r["wave"] for r in env),
            "max_wind": max(r["wind"] for r in env),
            "precip_sum": sum(r["precip"] for r in env),
            "wea_min": min(fe.score_weather(r["wind"], r["wave"], r["precip"])[0] for r in env),
            "wave48": wave48, "precip72": precip72,
            "sig": 1.0 / (1.0 + math.exp(-anom / 1.5)),
        }
        self._agg_cache[d] = agg
        return agg

    # ---------- history features (obs < d のみ) ----------
    def hist_features(self, d):
        d_dt = date.fromisoformat(d)
        i = bisect.bisect_left(self.obs_dates, d)
        recent = [self.obs[self.obs_dates[j]] for j in range(max(0, i - 3), i)]
        vis_vals = [s["visibility_hi"] for s in recent if s.get("visibility_hi") is not None]
        vis3 = statistics.mean(vis_vals) if vis_vals else 10.0
        trop3 = statistics.mean(min(1.0, s.get("tropical_count", 0) / 3.0) for s in recent) if recent else 0.3
        size3 = max((SIZE_NUM.get(s.get("hammer_size"), 1 if s.get("hammer_seen") else 0)
                     for s in recent), default=0)
        # 直近1件の透明度と経過日数 (7日以内)
        last_vis, last_age = 10.0, 7.0
        for j in range(i - 1, max(-1, i - 8), -1):
            if j < 0:
                break
            dd = self.obs_dates[j]
            age = (d_dt - date.fromisoformat(dd)).days
            if age > 7:
                break
            s = self.obs[dd]
            if s.get("visibility_hi") is not None:
                last_vis = min(30.0, s["visibility_hi"])
                last_age = float(age)
                break
        streak3 = 0
        for back in range(1, 4):
            pd_ = (d_dt - timedelta(days=back)).isoformat()
            if pd_ in self.obs and self.obs[pd_].get("hammer_seen"):
                streak3 += 1
        seen14_n, seen14_y = 0, 0
        for back in range(1, 15):
            pd_ = (d_dt - timedelta(days=back)).isoformat()
            if pd_ in self.obs:
                seen14_n += 1
                if self.obs[pd_].get("hammer_seen"):
                    seen14_y += 1
        seen14 = seen14_y / seen14_n if seen14_n else 0.5
        # 直近報告の鮮度: 最新報告からの経過日数 (未来日の予測で減衰に使う)
        obs_age = 99.0
        if i > 0:
            obs_age = float((d_dt - date.fromisoformat(self.obs_dates[i - 1])).days)
        return {"vis3": vis3, "trop3": trop3, "size3": size3, "streak3": streak3,
                "seen14": seen14, "last_vis": last_vis, "last_age": last_age,
                "obs_age": obs_age}

    # ---------- feature vectors ----------
    def x_size(self, d, agg, hf):
        m_ = int(d[5:7])
        msin, mcos = math.sin(2 * math.pi * m_ / 12), math.cos(2 * math.pi * m_ / 12)
        return [agg["sig"], fe.score_visibility(hf["vis3"]), hf["trop3"], hf["streak3"] / 3.0,
                hf["size3"] / 4.0, hf["seen14"], fe.score_current(agg["avg_speed"]),
                agg["wea_min"], msin, mcos]

    def x_vis(self, d, agg, hf):
        m_ = int(d[5:7])
        msin, mcos = math.sin(2 * math.pi * m_ / 12), math.cos(2 * math.pi * m_ / 12)
        return [hf["last_vis"], hf["last_age"], hf["vis3"], agg["wave48"], agg["max_wave"],
                agg["max_wind"], agg["precip72"], agg["precip_sum"], agg["anom"],
                agg["avg_speed"], msin, mcos]

    def x_cancel(self, d, agg):
        m_ = int(d[5:7])
        msin, mcos = math.sin(2 * math.pi * m_ / 12), math.cos(2 * math.pi * m_ / 12)
        wd = date.fromisoformat(d).weekday()
        weekend = 1.0 if wd >= 5 else 0.0
        # 直近の報告頻度 (営業・シーズン性の代理, D-1以前のみ)
        d_dt = date.fromisoformat(d)
        rep14 = sum(1 for back in range(1, 15)
                    if (d_dt - timedelta(days=back)).isoformat() in self.obs) / 14.0
        wave_over = max(0.0, agg["max_wave"] - 1.5)  # 欠航閾値超過量
        wind_over = max(0.0, agg["max_wind"] - 10.0)
        return [agg["max_wave"], wave_over, agg["wave48"], agg["max_wind"], wind_over,
                agg["precip_sum"], agg["wea_min"], weekend, rep14, msin, mcos]

    # ---------- train ----------
    def train(self, cutoff):
        """cutoff (ISO日付) より前の実測のみで学習"""
        self._log(f"Training models (obs < {cutoff})...")
        Xs, ys, Xv, yv = [], [], [], []
        month_points = {m_: {} for m_ in range(1, 13)}
        n = 0
        for d in self.obs_dates:
            if d >= cutoff or d < "2015-01-10":
                continue
            agg = self.day_agg(d)
            if agg is None:
                continue
            hf = self.hist_features(d)
            s = self.obs[d]
            seen = bool(s.get("hammer_seen"))
            ysize = SIZE_NUM.get(s.get("hammer_size"), 1 if seen else 0) if seen else 0
            Xs.append(self.x_size(d, agg, hf))
            ys.append(float(ysize))
            if s.get("visibility_hi") is not None:
                Xv.append((self.x_vis(d, agg, hf), hf["last_vis"]))
                yv.append(min(30.0, float(s["visibility_hi"])))
            m_ = int(d[5:7])
            for pt in (s.get("hammer_points") or []):
                name = pt[0] if isinstance(pt, (list, tuple)) else pt
                month_points[m_][name] = month_points[m_].get(name, 0) + 1
            n += 1
        # 欠航モデル: 営業期間の全暦日 (報告なし=1)
        Xc, yc = [], []
        if self.obs_dates:
            d0 = max(date.fromisoformat(self.obs_dates[0]), date(2015, 1, 10))
            d1 = min(date.fromisoformat(cutoff), date.today()) - timedelta(days=1)
            d_cur = d0
            while d_cur <= d1:
                ds = d_cur.isoformat()
                agg = self.day_agg(ds)
                if agg is not None:
                    Xc.append(self.x_cancel(ds, agg))
                    yc.append(0.0 if ds in self.obs else 1.0)
                d_cur += timedelta(days=1)
        self._log(f"  train: size={len(Xs)}, vis={len(Xv)}, cancel={len(Xc)} "
                  f"(no-report rate {statistics.mean(yc):.2f})" if yc else "  no cancel data")
        self.pred_size_f = fit_ridge(Xs, ys)
        # 透明度: 対数変化量 log(y/last_vis) を学習 (実験で最良 MAE)
        vis_ratio_f = fit_ridge([x for x, _ in Xv],
                                [math.log(t / max(1.0, lv)) for (x, lv), t in zip(Xv, yv)]) \
            if Xv else (lambda row: 0.0)
        self.pred_vis_f = lambda row, last_vis: max(
            1.0, min(30.0, max(1.0, last_vis) * math.exp(vis_ratio_f(row))))
        self.pred_cancel_f = fit_ridge(Xc, yc) if Xc else (lambda row: 0.3)
        # 欠航判定のしきい値を学習データ上で最適化 (balanced accuracy 最大)
        self.cancel_threshold = 0.5
        if Xc:
            preds = [self.pred_cancel_f(row) for row in Xc]
            best_t, best_ba = 0.5, -1.0
            pos = [p for p, y_ in zip(preds, yc) if y_ > 0.5]
            neg = [p for p, y_ in zip(preds, yc) if y_ <= 0.5]
            if pos and neg:
                for t100 in range(20, 81):
                    t = t100 / 100.0
                    tpr = sum(1 for p in pos if p >= t) / len(pos)
                    tnr = sum(1 for p in neg if p < t) / len(neg)
                    ba = (tpr + tnr) / 2
                    if ba > best_ba:
                        best_ba, best_t = ba, t
            self.cancel_threshold = best_t
            self._log(f"  cancel threshold={best_t:.2f} (balanced acc {best_ba:.3f} on train)")
        self.month_pt_norm = {}
        for m_, cnt in month_points.items():
            mx = max(cnt.values()) if cnt else 1
            self.month_pt_norm[m_] = {k: v / mx for k, v in cnt.items()}
        self.n_train = n
        self.trained = True
        return self

    # ---------- predict ----------
    def predict_day(self, d):
        assert self.trained
        agg = self.day_agg(d)
        if agg is None:
            return None
        hf = self.hist_features(d)
        # 規模: リッジ回帰と直近実測規模(3日max)のブレンド
        # (回帰単独は系統的過小予測 bias-0.54 → ブレンドで-0.06, ±1精度 80%→89%)
        # 直近実測の重みは報告からの経過日数で減衰 (翌日0.5 → 8日後で0)
        # — 減衰なしだと「最後の報告が大群」だけで14日先まで全日同じスコアになる
        p_size_model = max(0.0, min(4.0, self.pred_size_f(self.x_size(d, agg, hf))))
        w_recent = 0.5 * max(0.0, 1.0 - max(0.0, hf["obs_age"] - 1.0) / 7.0)
        p_size = max(0.0, min(4.0, (1.0 - w_recent) * p_size_model + w_recent * hf["size3"]))
        p_vis = self.pred_vis_f(self.x_vis(d, agg, hf), hf["last_vis"])
        p_cancel = max(0.0, min(1.0, self.pred_cancel_f(self.x_cancel(d, agg))))
        p_cnt = size_to_count(p_size)
        tier, tier_label = tier_matrix(p_vis, p_cnt)
        # ポイント予測
        m_ = int(d[5:7])
        engine_pts = {}
        for r in agg["env"]:
            mu_prior = 0.5 + 0.4 * hf["trop3"]
            ps = fe.compute_point_scores(r["cdir"], r["speed"], r["warm"], mu_prior)
            for k, v in ps.items():
                engine_pts[k] = engine_pts.get(k, 0) + v
        if engine_pts:
            mx = max(engine_pts.values()) or 1
            engine_pts = {k: v / mx for k, v in engine_pts.items()}
        combined = {}
        for k in set(list(engine_pts) + list(self.month_pt_norm[m_])):
            combined[k] = 0.5 * engine_pts.get(k, 0) + 0.5 * self.month_pt_norm[m_].get(k, 0)
        top3 = [k for k, _ in sorted(combined.items(), key=lambda kv: -kv[1])[:3]]
        return {
            "size_num": round(p_size, 2),
            "size_label": SIZE_JA[int(round(p_size))],
            "count_est": round(p_cnt),
            "visibility_m": round(p_vis, 1),
            "water_temp": round(agg["avg_sst"], 1),
            "cancel_prob": round(p_cancel, 2),
            "tier": tier, "tier_label": tier_label,
            "points": top3,
            "avg_anomaly": round(agg["anom"], 2),
            "avg_current_kmh": round(agg["avg_speed"], 2),
            "max_wave": round(agg["max_wave"], 2),
            "max_wind": round(agg["max_wind"], 1),
            "cancel_flag": p_cancel >= self.cancel_threshold,
            "diveable": p_cancel < self.cancel_threshold,
            "hf": hf,
        }
