"""共有予測モデル (バックテストと予報本体の両方から使用)

- EnvModels: hindcast(2015〜) + Open-Meteo API (past 31日 + future 16日) の環境ストア
- 学習モデル (時系列を分けた回帰・ロジスティック回帰):
    * size   : 群れ規模クラス 0-4
    * vis    : 透明度 (m)  — 改善版: 直近実測透明度+経過日数, 72h降水, 48h波, 流速 を追加
    * seen / large : 既知ラベルだけによる目撃・大群の確率
- 出現ポイント: 地形モデル。予報公開時はforecast_engineの実績根拠付き推薦と統一。
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
from domain import (size_target, observed_points, point_names, current_vector,
                    sea_safety, rank_from_count, today_jst, read_json, write_json, now_jst)

ROOT = Path(__file__).parent
PH1 = ROOT.parent / "data"

SIZE_NUM = {"large": 4, "medium": 3, "small": 2, "single": 1}
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


def blended_score(size_num, vis_m, weather_risk, tier=None):
    """0-100 の総合スコア (規模60% + 透明度25% + 海況評価15%（欠航確率ではない）)。
    tier を渡すとランク帯 (S:90+ A:80s B:70s C:55-69 D:40-54 E:<40) に収めて
    「スコア75なのにD」のような矛盾を防ぐ。帯内の位置は raw スコアで決まる。"""
    raw = (0.60 * size_num / 4.0
           + 0.25 * min(1.0, vis_m / 25.0)
           + 0.15 * (1.0 - weather_risk))
    if tier is None:
        return round(100 * raw, 1)
    lo, hi = TIER_BANDS.get(tier, (10, 100))
    return round(lo + (hi - lo) * raw, 1)


def tier_matrix(vis, cnt):
    return rank_from_count(vis, cnt)


def fit_ridge(X, y):
    """標準化 + ridge。predict(row) を返す"""
    if not X:
        raise ValueError("学習可能な観測がありません")
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


def fit_probability(X, y):
    """L2 logistic regression on known labels only; calibration is evaluated out of time.

    These probabilities concern recorded sightings, not unobserved shark presence.
    """
    if not X:
        return lambda row: 0.5
    rate = (sum(y) + 1) / (len(y) + 2)
    if len(set(y)) < 2:
        return lambda row: rate
    mu = [statistics.mean(c) for c in zip(*X)]
    sd = [statistics.pstdev(c) or 1 for c in zip(*X)]
    rows = [[(v - m) / s for v, m, s in zip(row, mu, sd)] for row in X]
    ws = [0.0] * len(mu)
    bias = math.log(rate / (1 - rate))
    for _ in range(180):
        errors = [fe.sigmoid(bias + sum(w * v for w, v in zip(ws, row))) - target
                  for row, target in zip(rows, y)]
        bias -= 0.15 * statistics.mean(errors)
        for j in range(len(ws)):
            ws[j] -= 0.15 * (sum(e * row[j] for e, row in zip(errors, rows)) / len(rows) + 0.02 * ws[j])
    return lambda row: fe.sigmoid(bias + sum(w * (v - m) / s for w, v, m, s in zip(ws, row, mu, sd)))


class EnvModels:
    def __init__(self, future_days=16, quiet=False, fetch_live=True, current_mode="smoc", persist_env=False):
        self.quiet = quiet
        self.current_mode = current_mode
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
        # Daily archived API values keep the historical store from developing a
        # permanent gap after the initial hindcast. Future forecasts are not archived here.
        cache_path = PH1 / "recent_environment.json"
        cache = read_json(cache_path) if cache_path.exists() else {"marine": {}, "weather": {}}
        self.M.update(cache["marine"])
        self.W.update(cache["weather"])
        fresh_m, fresh_w = {}, {}
        # API で直近ギャップ + 未来を補完
        try:
            if not fetch_live:
                raise RuntimeError("offline evaluation")
            r = requests.get("https://marine-api.open-meteo.com/v1/marine", params={
                "latitude": fe.LAT, "longitude": fe.LON,
                "hourly": "sea_surface_temperature,ocean_current_velocity,ocean_current_direction,"
                          "wave_height,sea_level_height_msl",
                "past_days": 31, "forecast_days": future_days, "timezone": fe.TZ,
                "cell_selection": "sea"}, timeout=60)
            r.raise_for_status()
            h = r.json()["hourly"]
            for i, t in enumerate(h["time"]):
                self.M[t] = (h["sea_surface_temperature"][i], h["ocean_current_velocity"][i],
                             h["ocean_current_direction"][i], h["wave_height"][i],
                             h["sea_level_height_msl"][i])
                fresh_m[t] = self.M[t]
        except Exception as e:
            self._log(f"  marine fetch failed: {e}")
        try:
            if not fetch_live:
                raise RuntimeError("offline evaluation")
            r = requests.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": fe.LAT, "longitude": fe.LON,
                "hourly": "wind_speed_10m,wind_direction_10m,precipitation",
                "past_days": 31, "forecast_days": future_days, "timezone": fe.TZ,
                "wind_speed_unit": "ms"}, timeout=60)
            r.raise_for_status()
            h = r.json()["hourly"]
            for i, t in enumerate(h["time"]):
                self.W[t] = (h["wind_speed_10m"][i], h["wind_direction_10m"][i], h["precipitation"][i])
                fresh_w[t] = self.W[t]
        except Exception as e:
            self._log(f"  weather fetch failed: {e}")

        if persist_env and fresh_m and fresh_w:
            for key, incoming in (("marine", fresh_m), ("weather", fresh_w)):
                cache[key].update({t: rec for t, rec in incoming.items()
                                   if t[:10] < today_jst().isoformat() and all(v is not None for v in rec)})
            cache["updated_at"] = now_jst().isoformat()
            cache["source"] = "Open-Meteo past_days API; past environment estimates, not original issued forecasts"
            write_json(cache_path, cache, indent=None)

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
            if any(v is None for v in (sst, cv, cd, wave, lvl, wind, pr)):
                return None
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
            speed, cdir, _ = current_vector(base_v, base_d, dh, self.current_mode)
            warm = (base_d + 180) % 360 if base_v > 0.3 else None
            recs.append({"sst": sst, "speed": speed, "cdir": cdir, "warm": warm,
                         "wave": wave, "wind": wind, "precip": pr})
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
        sizes = [size_target(s) for s in recent if size_target(s) is not None]
        size3 = max(sizes) if sizes else None
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
            if pd_ in self.obs and self.obs[pd_].get("hammer_seen") is not None:
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
                (hf["size3"] / 4.0 if hf["size3"] is not None else 0.5), hf["seen14"], fe.score_current(agg["avg_speed"]),
                agg["wea_min"], msin, mcos]

    def x_vis(self, d, agg, hf):
        m_ = int(d[5:7])
        msin, mcos = math.sin(2 * math.pi * m_ / 12), math.cos(2 * math.pi * m_ / 12)
        return [hf["last_vis"], hf["last_age"], hf["vis3"], agg["wave48"], agg["max_wave"],
                agg["max_wind"], agg["precip72"], agg["precip_sum"], agg["anom"],
                agg["avg_speed"], msin, mcos]

    # ---------- train ----------
    def train(self, cutoff):
        """cutoff (ISO日付) より前の実測のみで学習"""
        self._log(f"Training models (obs < {cutoff})...")
        Xs, ys, Xv, yv = [], [], [], []
        Xseen, yseen = [], []
        n = 0
        for d in self.obs_dates:
            if d >= cutoff or d < "2015-01-10":
                continue
            agg = self.day_agg(d)
            if agg is None:
                continue
            hf = self.hist_features(d)
            s = self.obs[d]
            if s.get("hammer_seen") is not None:
                Xseen.append(self.x_size(d, agg, hf))
                yseen.append(float(s["hammer_seen"]))
            ysize = size_target(s)
            if ysize is not None:
                Xs.append(self.x_size(d, agg, hf))
                ys.append(float(ysize))
            if s.get("visibility_hi") is not None:
                Xv.append((self.x_vis(d, agg, hf), hf["last_vis"]))
                yv.append(min(30.0, float(s["visibility_hi"])))
            n += 1
        self._log(f"  train: known size={len(Xs)}, known seen={len(Xseen)}, visibility={len(Xv)}")
        self.pred_size_f = fit_ridge(Xs, ys)
        self.pred_seen_f = fit_probability(Xseen, yseen)
        self.pred_large_f = fit_probability(Xs, [float(y == 4) for y in ys])
        # 透明度: 対数変化量 log(y/last_vis) を学習 (実験で最良 MAE)
        vis_ratio_f = fit_ridge([x for x, _ in Xv],
                                [math.log(t / max(1.0, lv)) for (x, lv), t in zip(Xv, yv)]) \
            if Xv else (lambda row: 0.0)
        self.pred_vis_f = lambda row, last_vis: max(
            1.0, min(30.0, max(1.0, last_vis) * math.exp(vis_ratio_f(row))))
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
        # 過去の調整で採用したブレンド。現行ラベル定義の成績は backtest / 公開予報評価を参照。
        # 直近実測の重みは報告からの経過日数で減衰 (翌日0.5 → 8日後で0)
        # — 減衰なしだと「最後の報告が大群」だけで14日先まで全日同じスコアになる
        p_size_model = max(0.0, min(4.0, self.pred_size_f(self.x_size(d, agg, hf))))
        w_recent = 0.5 * max(0.0, 1.0 - max(0.0, hf["obs_age"] - 1.0) / 7.0)
        if hf["size3"] is None:
            w_recent = 0.0
        p_size = max(0.0, min(4.0, (1.0 - w_recent) * p_size_model + w_recent * (hf["size3"] or 0)))
        p_vis = self.pred_vis_f(self.x_vis(d, agg, hf), hf["last_vis"])
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
        # 訪問頻度を出現確率として再利用しない。将来予報ではenrichで
        # エンジンの実績根拠付き推薦と統一。過去再計算は地形モデルのみ。
        top3 = [k for k, _ in sorted(engine_pts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
        diveable, sea_note = sea_safety(agg["max_wind"], agg["max_wave"])
        return {
            "seen_probability": round(self.pred_seen_f(self.x_size(d, agg, hf)), 4),
            "large_probability": round(self.pred_large_f(self.x_size(d, agg, hf)), 4),
            "probability_note": "既知ラベルで学習した目撃確率。観測バイアスあり。校正は検証ページを参照。",
            "size_num": round(p_size, 2),
            "size_label": SIZE_JA[int(round(p_size))],
            "count_est": round(p_cnt),
            "visibility_m": round(p_vis, 1),
            "water_temp": round(agg["avg_sst"], 1),
            "weather_risk": 1.0 if diveable is False else (0.5 if agg["wea_min"] < 1 else 0.0),
            "sea_status": sea_note,
            "tier": tier, "tier_label": tier_label,
            "points": top3,
            "avg_anomaly": round(agg["anom"], 2),
            "avg_current_kmh": round(agg["avg_speed"], 2),
            "max_wave": round(agg["max_wave"], 2),
            "max_wind": round(agg["max_wind"], 1),
            "diveable": diveable,
            "hf": hf,
        }
