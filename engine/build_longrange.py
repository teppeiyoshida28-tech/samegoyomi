"""半年先までの「狙い目カレンダー」データ生成 → longrange_data.json

合成する因子:
  1. 月別の群れ規模気候値 (12年実測: 月別平均規模クラス・大群率)
  2. 月齢因子 (過去1年の月齢別平均規模、移動平均平滑化)
  3. 日中下げ潮 (調和分解モデル tide_model.json による8-13時の潮位下降量。
     海流は長期予測不能のため使わず、天文潮汐のみ)
  4. 黒潮長期予測 (JAMSTEC 黒潮親潮ウォッチ: 手動更新の設定ファイル
     kuroshio_forecast.json — 予測有効期限つき。未評価・期限後は公開値null、計算上のみ中立0.5)

出力: 各日 {date, weekday, moon_age, lunar_day, tide_name, phase_icon,
             f_month, f_moon, f_ebb, f_kuroshio, aim_score, kuroshio_known,
             tide_curve(6-16時潮位), ebb_m(8-13時最大下降), flow_label}
"""
import json
import statistics
from datetime import date, timedelta
from pathlib import Path
from domain import today_jst, now_jst, size_target, BIAS_NOTE

import tide_predict
from fetch_kuroshio import atomic_write
from longrange_contract import CALENDAR_DAYS, finite, kuroshio_state, validate_calendar, validate_kuroshio

ROOT = Path(__file__).parent
PH1 = ROOT.parent / "data"
OUT = ROOT / "longrange_data.json"
DAYS = CALENDAR_DAYS

SIZE_NUM = {"large": 4, "medium": 3, "small": 2, "single": 1}


def build_data(today=None):
    today = today or today_jst()
    moon = json.loads((PH1 / "moon_tide_calendar.json").read_text(encoding="utf-8"))["calendar"]
    expected = [(today + timedelta(days=n)).isoformat() for n in range(DAYS)]
    missing_moon = [day for day in expected if not isinstance(moon.get(day), dict)]
    if missing_moon:
        raise ValueError(f"月齢カレンダーの{DAYS}日カバレッジが不足: {missing_moon[0]} (欠け{len(missing_moon)}日)。再生成してから公開してください")
    for day in expected:
        row = moon[day]
        if (not finite(row.get("moon_age")) or not 0 <= row["moon_age"] < 30
                or type(row.get("lunar_day")) is not int or not 1 <= row["lunar_day"] <= 30
                or row.get("tide_name") not in {"大潮", "中潮", "小潮", "長潮", "若潮"}):
            raise ValueError(f"月齢カレンダーの値が不正: {day}")
    obs = {s["date"]: s for s in
           json.loads((ROOT / "dive_logs_structured.json").read_text(encoding="utf-8"))["daily_summary"]}

    # ---- 黒潮長期予測 (手動更新設定) ----
    kfile = ROOT / "kuroshio_forecast.json"
    if not kfile.exists():
        raise FileNotFoundError("黒潮予測の設定がありません。古い仮定で自動生成せず停止します")
    kuro = json.loads(kfile.read_text(encoding="utf-8"))
    errors = validate_kuroshio(kuro)
    if errors:
        raise ValueError("黒潮資料の形式が不正: " + "; ".join(errors))

    # ---- 潮汐調和モデル (なければフィット) ----
    tm_path = ROOT / "tide_model.json"
    if tm_path.exists():
        tide_model = json.loads(tm_path.read_text(encoding="utf-8"))
    else:
        tide_model = tide_predict.fit()
        atomic_write(tm_path, tide_model)

    # ---- 1. 月別気候値 (12年) : 平均規模クラス ----
    month_sizes = {m: [] for m in range(1, 13)}
    for d_str, s in obs.items():
        seen = bool(s.get("hammer_seen"))
        if s.get("hammer_seen") is None:
            continue
        size = size_target(s)
        if size is None:
            continue
        m = int(d_str[5:7])
        month_sizes[m].append(size)

    month_avg = {m: statistics.mean(v) if v else 2.0 for m, v in month_sizes.items()}
    mn, mx = min(month_avg.values()), max(month_avg.values())
    f_month = {m: (v - mn) / (mx - mn) if mx > mn else 0.5 for m, v in month_avg.items()}

    # ---- 2. 月齢因子 (過去1年, 月齢1日ビン, 円環移動平均±1) ----
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
        size = size_target(s)
        if size is None:
            continue
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
    days = []
    for n in range(DAYS):
        d = today + timedelta(days=n)
        d_str = d.isoformat()
        mo = moon[d_str]
        age = mo["moon_age"]
        b = min(29, int(age))
        m = d.month
        fm = f_month[m]
        fmo = f_moon[b]
        td = tide_days[d_str]
        # 午前下げ因子: 「朝に下げ進行中 + 昼まで継続」を正規化。
        # 8-9時下げ止まり(ebbstop)は ebb_m が小さくなるので自然に低評価される
        fe = (td["ebb_m"] - emin) / (emax - emin) if emax > emin else 0.5
        kstate = kuroshio_state(kuro, d, today)
        known = kstate["known"]
        fk = kstate["calculation_factor"]
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
            "f_kuroshio": round(fk, 3) if known else None,
            "kuroshio_known": known,
            "kuroshio_state": kstate["reason"],
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
        "schema_version": 2,
        "generated_at": now_jst().isoformat(),
        "coverage": {"start": expected[0], "end": expected[-1], "days": DAYS, "complete": True},
        "calendar_kind": "astronomical_seasonal_reference",
        "observation_bias_note": BIAS_NOTE,
        "kuroshio": kuro,
        "kuroshio_unknown_note": "黒潮未評価（計算上は中立0.5）。資料の取得可否と黒潮条件を評価できることは別です。",
        "month_climatology": {str(m): round(month_avg[m], 2) for m in range(1, 13)},
        "moon_smooth": {str(b): round(smooth[b], 2) for b in range(30)},
        "tide_hours": list(range(6, 17)),
        "weights": {"month": 0.35, "ebb": 0.25, "kuroshio": 0.20, "moon": 0.20},
        "tide_note": ("潮位は主要8分潮の調和近似による天文潮の参考値。風・気圧等の気象効果は含めず、"
                      "将来期間の独立した精度検証は未実施。"
                      "下げ因子=「朝(8時)に下げ進行中×昼まで継続」。未明からの下げは加点、"
                      "8-9時下げ止まりは低評価。流れのタイムラグを考慮し昼過ぎ以降の下げは要求しない。"),
        "days": days,
    }
    errors = validate_calendar(days, today)
    if errors:
        raise ValueError("; ".join(errors))
    return out


def main():
    out = build_data()
    atomic_write(OUT, out)
    print(f"Wrote {OUT} ({len(out['days'])} days)")
    # 月別トップ日を表示
    by_month = {}
    for row in out["days"]:
        by_month.setdefault(row["date"][:7], []).append(row)
    for ym in sorted(by_month):
        tops = [x for x in by_month[ym] if x.get("is_top")]
        tops.sort(key=lambda v: -v["aim_score"])
        s = ", ".join(f"{x['date'][5:]}({x['phase_icon']}{x['tide_name']} {x['flow_label']} {x['aim_score']:.0f})" for x in tops)
        print(f"  {ym}: {s}")


if __name__ == "__main__":
    main()
