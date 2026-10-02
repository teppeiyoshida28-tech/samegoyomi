"""
過去実測データを潮汐位相ごとに集計し、
「大潮/中潮/小潮/長潮/若潮」×「規模」のヒートマップを生成

出力: tide_hammer_heatmap.json → UI表示用
"""
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from domain import today_jst, now_jst, size_target, BIAS_NOTE

ROOT = Path(__file__).parent


def moon_age(dt):
    """簡易 月齢 (0-29.53)"""
    ref = datetime(2000, 1, 6, 18, 14)  # 新月基準
    days = (datetime.fromisoformat(dt if len(dt) > 10 else dt + "T00:00:00") - ref).total_seconds() / 86400.0
    return days % 29.530588


def tide_phase(mage):
    """月齢から潮汐位相"""
    if mage < 1.5 or mage > 27.5: return "新月大潮"
    if mage < 6: return "中潮"
    if 6 <= mage < 8: return "小潮"
    if 8 <= mage < 10: return "長潮"
    if 10 <= mage < 12: return "若潮"
    if 12 <= mage < 17: return "満月大潮"
    if 17 <= mage < 22: return "中潮"
    if 22 <= mage < 24: return "小潮"
    if 24 <= mage < 26: return "長潮"
    return "若潮"


WINDOW_DAYS = 365  # 集計期間: 過去1年 (半年以上)


def build_heatmap():
    data = json.load(open(ROOT / "dive_logs_structured.json"))
    cutoff = (today_jst() - timedelta(days=WINDOW_DAYS)).isoformat()
    daily = [s for s in data["daily_summary"]
             if s["date"] >= cutoff and s.get("hammer_seen") is not None]

    size_map = {"large": 4, "medium": 3, "small": 2, "single": 1, None: 0}

    # 位相ごとに集計
    phase_stats = {}
    entries = []
    for s in daily:
        d = s["date"]
        mage = moon_age(d)
        phase = tide_phase(mage)
        size_num = size_target(s)
        seen = s.get("hammer_seen")
        entries.append({
            "date": d, "moon_age": round(mage, 1), "phase": phase,
            "seen": seen, "size": s.get("hammer_size"), "size_num": size_num,
            "vis_hi": s.get("visibility_hi"),
            "wt_hi": s.get("water_temp_hi"),
            "tropical_count": s.get("tropical_count", 0),
        })
        phase_stats.setdefault(phase, {"days": 0, "seen_days": 0, "size_sum": 0, "size_list": []})
        phase_stats[phase]["days"] += 1
        if seen:
            phase_stats[phase]["seen_days"] += 1
        if size_num is not None:
            phase_stats[phase]["size_sum"] += size_num
            phase_stats[phase]["size_list"].append(size_num)

    # サマリ
    # 加重規模スコア: 大群日を強調するため size^2 で加重 (0,1,4,9,16) → /4 で 0-4 スケール
    summary = []
    for phase, s in phase_stats.items():
        n = s["days"] or 1
        size_n = len(s["size_list"])
        weighted = sum(x * x for x in s["size_list"]) / (size_n or 1) / 4.0
        large_days = sum(1 for x in s["size_list"] if x >= 4)
        summary.append({
            "phase": phase,
            "days": s["days"],
            "seen_days": s["seen_days"],
            "encounter_rate": s["seen_days"] / n,
            "avg_size": s["size_sum"] / (size_n or 1),
            "n_known_size": size_n,
            "weighted_size": round(weighted, 2),
            "large_days": large_days,
            "large_rate": round(large_days / (size_n or 1), 3),
        })
    summary.sort(key=lambda x: -x["weighted_size"])

    result = {
        "generated_at": now_jst().isoformat(),
        "observation_bias_note": BIAS_NOTE,
        "n_days": len(daily),
        "window_days": WINDOW_DAYS,
        "entries": entries[-16:],  # 表示用は直近のみ (統計は全期間)
        "phase_summary": summary,
    }
    with open(ROOT / "tide_hammer_heatmap.json", "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"=== 潮汐位相別 目撃統計 (n={len(daily)}日) ===")
    print(f"{'位相':<8} {'日数':>4} {'加重規模':>8} {'平均規模':>8} {'大群率':>8}")
    for s in summary:
        print(f"{s['phase']:<8} {s['days']:>4} {s['weighted_size']:>8.2f} {s['avg_size']:>8.1f} {s['large_rate']:>8.1%}")

    print(f"\n=== 直近16日の詳細 ===")
    print(f"{'Date':<12} {'月齢':>5} {'位相':<8} {'見':>3} {'規模':>7} {'水温':>5} {'透明':>5} {'南方':>4}")
    for e in entries[-16:]:
        wt = e["wt_hi"] or "-"
        vis = e["vis_hi"] or "-"
        seen = "○" if e["seen"] else "✕"
        sz = e["size"] or "-"
        print(f"{e['date']:<12} {e['moon_age']:>5} {e['phase']:<8} {seen:>3} {sz:>7} {wt:>5} {vis:>5} {e['tropical_count']:>4}")

    return result


if __name__ == "__main__":
    build_heatmap()
