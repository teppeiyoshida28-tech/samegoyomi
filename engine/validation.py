"""Reject inconsistent generated forecasts before publication."""
from domain import MODEL_VERSION, sea_safety
import forecast_engine as fe


def validate_forecast(data):
    problems = []
    if data.get("model_version") != MODEL_VERSION:
        problems.append("予報のモデル版が一致しません")
    if not data.get("daily") or not data.get("hourly"):
        return problems + ["予報が空です"]
    for d in data["daily"]:
        day = d["date"]
        hours = [h for h in data["hourly"] if h["time"][:10] == day and 8 <= h["hour"] <= 14]
        if d.get("forecast_kind") != "full" or {h["hour"] for h in hours} != set(range(8, 15)):
            problems.append(f"{day}: フル予報の時間範囲が不足")
        if "ml" not in d:
            problems.append(f"{day}: ML予報なし")
        safe, _ = sea_safety(d.get("max_wind"), d.get("max_wave"))
        if safe is None or d.get("diveable") != safe:
            problems.append(f"{day}: 海況判定が不整合")
        top = d.get("top3_points", [])
        if not top or d.get("best_point") != top[0]["point"] or d.get("best_point_score") != top[0]["score"]:
            problems.append(f"{day}: 推奨地点と順位/スコアが不一致")
        evidence = d.get("best_point_evidence")
        if evidence and evidence.get("stats_key") != fe.STATS_KEY_FOR_POINT.get(d.get("best_point")):
            problems.append(f"{day}: 根拠が別地点")
        if "ml" in d and d["ml"].get("points") != [p["point"] for p in top]:
            problems.append(f"{day}: ML側の推薦が不一致")
        for h in hours:
            if any(h.get(k) is None for k in ("sst", "wave_height", "wind_ms", "current_velocity_kmh", "current_direction", "sea_level")):
                problems.append(f"{day}: 海況に欠損")
    return problems
