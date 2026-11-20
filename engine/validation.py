"""Reject inconsistent generated forecasts before publication."""
from datetime import datetime, timedelta
from domain import MODEL_VERSION, sea_safety, now_jst
import forecast_engine as fe
from environment_inputs import (INPUT_VERSION, API_MODEL, API_URL, WEATHER_URL,
                                OCEAN_UNITS, WAVE_UNITS, WEATHER_UNITS,
                                LATITUDE, LONGITUDE, finite, normalized_time)


def validate_input_sources(data, now=None):
    """Publication-only checks; retrieval freshness is not model-run freshness."""
    now = now or now_jst()
    errors = []
    if data.get("input_pipeline_version") != INPUT_VERSION:
        errors.append("入力取得パイプライン版が一致しません")
    try:
        issued = datetime.fromisoformat(data["issued_at"])
        if (issued.utcoffset() != timedelta(hours=9) or issued.date() != now.date()
                or issued > now + timedelta(minutes=5)):
            raise ValueError("invalid issuance")
    except (KeyError, TypeError, ValueError):
        return errors + ["本日のJST発表時刻を確認できません"]
    hours = [h.get("time") for h in data.get("hourly", [])
             if h.get("hour") in range(8, 15)]
    try:
        targets = [normalized_time(t) for t in hours]
        if not targets:
            raise ValueError("no targets")
        first, last = min(targets), max(targets)
    except (TypeError, ValueError):
        return errors + ["入力検査の予報対象時刻が不正です"]
    specifications = (("marine", "ocean", API_MODEL, API_URL, OCEAN_UNITS),
                      ("marine", "waves", "best_match", API_URL, WAVE_UNITS),
                      ("weather", None, "best_match", WEATHER_URL, WEATHER_UNITS))
    for owner, sources in (("予報", data.get("input_sources", {})),
                           ("ML", data.get("ml_meta", {}).get("input_sources", {}))):
        for kind, sub, model, url, units in specifications:
            label = f"{owner}/{sub or kind}"
            try:
                source = sources[kind][sub] if sub else sources[kind]
                if (source.get("api_model") != model or source.get("api_url") != url
                        or source.get("provider") != "Open-Meteo"
                        or source.get("timezone") != "Asia/Tokyo"
                        or source.get("units") != units):
                    raise ValueError("source contract")
                # The API exposes no model publication timestamp. A retrieval
                # must not be repackaged as that timestamp.
                if "model_run_at" not in source or source["model_run_at"] is not None:
                    raise ValueError("unavailable model publication time")
                retrieved = datetime.fromisoformat(source["retrieved_at"])
                if (retrieved.utcoffset() != timedelta(hours=9)
                        or not -timedelta(minutes=5) <= now - retrieved < timedelta(hours=36)
                        or retrieved < issued - timedelta(minutes=5)):
                    raise ValueError("stale retrieval")
                for key, expected in (("latitude", LATITUDE), ("longitude", LONGITUDE)):
                    requested = source["requested_coordinates"][key]
                    grid = source["grid_coordinates"][key]
                    if (not finite(requested) or abs(requested - expected) > 1e-6
                            or not finite(grid) or abs(grid - expected) > .2):
                        raise ValueError("different location")
                start, end = (normalized_time(source[key])
                              for key in ("coverage_start", "coverage_end"))
                if not start <= first <= last <= end:
                    raise ValueError("target outside source coverage")
            except (KeyError, TypeError, ValueError, AttributeError):
                errors.append(f"{label}: 最新入力の出典・単位・座標・対象期間を確認できません")
    try:
        ml_time = datetime.fromisoformat(data["ml_meta"]["generated_at"])
        if ml_time.utcoffset() != timedelta(hours=9) or not issued <= ml_time <= now + timedelta(minutes=5):
            raise ValueError("ML generation time")
    except (KeyError, TypeError, ValueError):
        errors.append("ML生成時刻が今回の予報と一致しません")
    return errors


def validate_forecast(data, require_fresh=False):
    problems = []
    if require_fresh:
        problems.extend(validate_input_sources(data))
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
