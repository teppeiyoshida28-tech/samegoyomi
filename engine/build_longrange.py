"""半年先までの「狙い目カレンダー」データ生成 → longrange_data.json

合成する因子:
  1. 月別の群れ規模気候値 (12年実測: 月別平均規模クラス・大群率)
  2. 月齢因子 (過去1年の月齢別平均規模、移動平均平滑化)
  3. 日中下げ潮 (調和分解モデル tide_model.json による8-13時の潮位下降量。
     海流は長期予測不能のため使わず、天文潮汐のみ)
  4. 黒潮長期予測 (JAMSTEC 黒潮親潮ウォッチ: 手動更新の設定ファイル
     kuroshio_forecast.json — 予測有効期限つき。期限後は「不明(中立)」)

出力: 各日 {date, weekday, moon_age, lunar_day, tide_name, phase_icon,
             f_month, f_moon, f_ebb, f_kuroshio, aim_score, kuroshio_known,
             tide_curve(6-16時潮位), ebb_m(8-13時最大下降), flow_label}
"""
import json
import statistics
from datetime import date, timedelta
from pathlib import Path

import tide_predict

ROOT = Path(__file__).parent
PH1 = ROOT.parent / "data"
OUT = ROOT / "longrange_data.json"
DAYS = 183

SIZE_NUM = {"large": 4, "medium": 3, "small": 2, "single": 1}


def main():
    moon = json.load(open(PH1 / "moon_tide_calendar.json"))["calendar"]
    obs = {s["date"]: s for s in
           json.load(open(ROOT / "dive_logs_structured.json"))["daily_summary"]}

    # ---- 黒潮長期予測 (手動更新設定) ----
    kfile = ROOT / "kuroshio_forecast.json"
    if kfile.exists():
        kuro = json.load(open(kfile))
    else:
        kuro = {
            "updated": "2026-08-25",
            "source": "JAMSTEC 黒潮親潮ウォッチ 長期予測 (2026-08-20発表, JCOPE3M)",
            "source_url": "https://www.jamstec.go.jp/aplinfo/kowatch/",
            "status": "nNLM",
            "label": "接岸流路 (八丈島の北) 継続予測",
            "valid_until": "2026-10-17",
            "score": 1.0,
            "note": "大蛇行は2025年に終息、非大蛇行接岸型が継続。毎週水曜更新をチェック。",
        }
        json.dump(kuro, open(kfile, "w"), ensure_ascii=False, indent=2)

    # ---- 潮汐調和モデル (なければフィット) ----
    tm_path = ROOT / "tide_model.json"
    if tm_path.exists():
        tide_model = json.load(open(tm_path))
    else:
        tide_model = tide_predict.fit()
        json.dump(tide_model, open(tm_path, "w"))

    # ---- 1. 月別気候値 (12年) : 平均規模クラス ----
    month_sizes = {m: [] for m in range(1, 13)}
    for d_str, s in obs.items():
        seen = bool(s.get("hammer_seen"))
        if s.get("hammer_seen") is None:
            continue
        size = SIZE_NUM.get(s.get("hammer_size"), 1 if seen else 0) if seen else 0
        m = int(d_str[5:7])
        month_sizes[m].append(size)

    month_avg = {m: statistics.mean(v) if v else 2.0 for m, v in month_sizes.items()}
    mn, mx = min(month_avg.values()), max(month_avg.values())
    f_month = {m: (v - mn) / (mx - mn) if mx > mn else 0.5 for m, v in month_avg.items()}

    # ---- 2. 月齢因子 (過去1年, 月齢1日ビン, 円環移動平均±1) ----
    today = date.today()
    yr_start = today - timedelta(days=365)
    age_bins = {b: [] for b in range(30)}
    for d_str, s in obs.items():
        d = date.fromisoformat(d_str)
        if not (yr_start <= d < today):
            continue
        if s.get("hammer_seen") is None:
            continue
        mo = moon.get(d_str)
        if not mo:
            continue
        seen = bool(s.get("hammer_seen"))
        size = SIZE_NUM.get(s.get("hammer_size"), 1 if seen else 0) if seen else 0
        age_bins[min(29, int(mo["moon_age"]))].append(size)
    raw = {b: statistics.mean(v) if v else None for b, v in age_bins.items()}
    # 円環移動平均で欠損補完+平滑化
    smooth = {}
    for b in range(30):
        vals = [raw[(b + k) % 30] for k in (-1, 0, 1) if raw[(b + k) % 30] is not None]
        smooth[b] = statistics.mean(vals) if vals else 2.0
    an, ax = min(smooth.values()), max(smooth.values())
    f_moon = {b: (v - an) / (ax - an) if ax > an else 0.5 for b, v in smooth.items()}

    def phase_icon(age):
        idx = int((age / 29.53) * 8 + 0.5) % 8
        return "🌑🌒🌓🌔🌕🌖🌗🌘"[idx]

    def phase_name(age):
        if age < 1.5 or age >= 28.5: return "新月"
        if 6 <= age < 9: return "上弦"
        if 13.5 <= age < 16.5: return "満月"
        if 21 <= age < 24: return "下弦"
        return ""

    # ---- 3. 潮汐プロファイルを先に全日計算 (f_ebb 正規化のため) ----
    tide_days = {}
    for n in range(DAYS):
        d = today + timedelta(days=n)
        prof = tide_predict.day_profile(tide_model, d, 4, 16)
        levels = {h: v for h, v in prof}
        # === 現場ロジック (ユーザー知見) ===
        # ・未明から下げていて、朝の時点でしっかり下げ進行中が最重要
        # ・下げが昼ごろまで続けば2本目3本目まで群れ期待 (昼過ぎまでは不要)
        # ・8-9時で下げ止まる日は低評価
        morning_ebb = levels[8] - levels[12]      # 朝→昼の下げ継続 (最重要)
        predawn_ebb = levels[5] - levels[8]       # 未明→朝に既に下げているか
        ebb_score_m = max(0.0, morning_ebb) + 0.5 * max(0.0, predawn_ebb)
        # 下げ止まり時刻 (6-16時の最小潮位の時刻)
        stop_h = min(range(6, 17), key=lambda h: levels[h])
        # 午後の上げ (代替狙い: 弱い加点材料としてラベルのみ)
        pm_rise = levels[16] - levels[12]
        am_rise = levels[12] - levels[8]
        if ebb_score_m >= 0.15 and stop_h >= 11:
            flow = f"下げ 〜{stop_h}時"
            flow_type = "ebb"
        elif max(0.0, levels[6] - levels[min(16, stop_h)]) >= 0.25 and stop_h <= 9:
            flow = f"下げ止まり{stop_h}時" + ("・午後上げ" if pm_rise >= 0.3 else "")
            flow_type = "ebbstop"
        elif am_rise >= 0.25:
            flow = "上げ主体"
            flow_type = "flood"
        else:
            flow = "動き小"
            flow_type = "slack"
        tide_days[d.isoformat()] = {
            "curve": [round(levels[h], 2) for h in range(6, 17)],
            "ebb_m": round(ebb_score_m, 2),
            "morning_ebb_m": round(morning_ebb, 2),
            "predawn_ebb_m": round(predawn_ebb, 2),
            "stop_h": stop_h,
            "rise_m": round(max(0.0, am_rise), 2),
            "flow_label": flow,
            "flow_type": flow_type,
        }
    ebbs = [v["ebb_m"] for v in tide_days.values()]
    emin, emax = min(ebbs), max(ebbs)

    # ---- 4. 日別合成 ----
    kuro_until = kuro.get("valid_until", "1900-01-01")
    kuro_score = float(kuro.get("score", 0.5))
    days = []
    for n in range(DAYS):
        d = today + timedelta(days=n)
        d_str = d.isoformat()
        mo = moon.get(d_str)
        if not mo:
            continue
        age = mo["moon_age"]
        b = min(29, int(age))
        m = d.month
        fm = f_month[m]
        fmo = f_moon[b]
        td = tide_days[d_str]
        # 午前下げ因子: 「朝に下げ進行中 + 昼まで継続」を正規化。
        # 8-9時下げ止まり(ebbstop)は ebb_m が小さくなるので自然に低評価される
        fe = (td["ebb_m"] - emin) / (emax - emin) if emax > emin else 0.5
        known = d_str <= kuro_until
        fk = kuro_score if known else 0.5
        # 重み: 季節 35% / 日中下げ潮 25% / 黒潮 20% / 月齢 20%
        aim = 100 * (0.35 * fm + 0.25 * fe + 0.20 * fk + 0.20 * fmo)
        days.append({
            "date": d_str,
            "weekday": d.weekday(),  # 0=月
            "moon_age": round(age, 1),
            "lunar_day": mo["lunar_day"],
            "tide_name": mo["tide_name"],
            "phase_icon": phase_icon(age),
            "phase_name": phase_name(age),
            "tide_curve": td["curve"],
            "ebb_m": td["ebb_m"],
            "morning_ebb_m": td["morning_ebb_m"],
            "predawn_ebb_m": td["predawn_ebb_m"],
            "stop_h": td["stop_h"],
            "rise_m": td["rise_m"],
            "flow_label": td["flow_label"],
            "flow_type": td["flow_type"],
            "f_month": round(fm, 3),
            "f_moon": round(fmo, 3),
            "f_ebb": round(fe, 3),
            "f_kuroshio": round(fk, 3),
            "kuroshio_known": known,
            "aim_score": round(aim, 1),
        })

    # 月ごとの Top3 に印
    by_month = {}
    for x in days:
        by_month.setdefault(x["date"][:7], []).append(x)
    for ym, arr in by_month.items():
        for x in sorted(arr, key=lambda v: -v["aim_score"])[:3]:
            x["is_top"] = True

    out = {
        "generated_at": date.today().isoformat(),
        "kuroshio": kuro,
        "month_climatology": {str(m): round(month_avg[m], 2) for m in range(1, 13)},
        "moon_smooth": {str(b): round(smooth[b], 2) for b in range(30)},
        "tide_hours": list(range(6, 17)),
        "weights": {"month": 0.35, "ebb": 0.25, "kuroshio": 0.20, "moon": 0.20},
        "tide_note": ("潮位は調和分解(主要8分潮)による天文潮予測。検証MAE≈0.09m。"
                      "下げ因子=「朝(8時)に下げ進行中×昼まで継続」。未明からの下げは加点、"
                      "8-9時下げ止まりは低評価。流れのタイムラグを考慮し昼過ぎ以降の下げは要求しない。"),
        "days": days,
    }
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"Wrote {OUT} ({len(days)} days)")
    # 月別トップ日を表示
    for ym in sorted(by_month):
        tops = [x for x in by_month[ym] if x.get("is_top")]
        tops.sort(key=lambda v: -v["aim_score"])
        s = ", ".join(f"{x['date'][5:]}({x['phase_icon']}{x['tide_name']} {x['flow_label']} {x['aim_score']:.0f})" for x in tops)
        print(f"  {ym}: {s}")


if __name__ == "__main__":
    main()
