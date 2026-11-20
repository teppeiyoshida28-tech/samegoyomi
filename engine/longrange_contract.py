"""Publication checks independent of short-range weather and Kuroshio classification."""
from datetime import date
import math
from urllib.parse import urlparse

CALENDAR_DAYS = 183
KNOWN_KUROSHIO = {"LM", "nNLM", "oNLM"}


def iso_day(value):
    if not isinstance(value, str):
        raise ValueError("date must be an ISO date string")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("date must use YYYY-MM-DD")
    return parsed


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate_kuroshio(data):
    """Return structural errors. Expiry and an unclassified source are not errors."""
    errors = []
    if not isinstance(data, dict):
        return ["Kuroshio document must be an object"]
    version = data.get("schema_version", 1)
    if version not in (1, 2):
        errors.append("Unsupported Kuroshio schema version")
    for key in ("updated", "valid_until"):
        try:
            iso_day(data.get(key))
        except (ValueError, TypeError):
            errors.append(f"Invalid Kuroshio {key}")
    url = urlparse(data.get("source_url") if isinstance(data.get("source_url"), str) else "")
    if url.scheme != "https" or url.hostname != "www.jamstec.go.jp" or not url.path.startswith("/aplinfo/kowatch/"):
        errors.append("Kuroshio source must be the official JAMSTEC kowatch site")
    score, status = data.get("score"), data.get("status")
    if status == "unknown":
        if score is not None:
            errors.append("Unknown Kuroshio classification must have score:null")
    elif status not in KNOWN_KUROSHIO or not finite(score) or not 0 <= score <= 1:
        errors.append("Invalid Kuroshio classification or score")
    if version == 2:
        from datetime import datetime
        for key in ("issued_on", "valid_from"):
            try:
                iso_day(data.get(key))
            except (ValueError, TypeError):
                errors.append(f"Invalid Kuroshio {key}")
        try:
            retrieved = datetime.fromisoformat(data.get("retrieved_at", ""))
            if retrieved.utcoffset() is None:
                raise ValueError("naive timestamp")
        except (ValueError, TypeError):
            errors.append("Kuroshio retrieved_at must include a timezone")
        if data.get("source_status") != "available":
            errors.append("A successful source snapshot must be source_status:available")
        assessment = data.get("assessment")
        if not isinstance(assessment, dict) or assessment.get("status") not in ("reviewed", "unreviewed"):
            errors.append("Kuroshio assessment status is required")
        elif status != "unknown" and assessment.get("status") != "reviewed":
            errors.append("A known classification requires manual review")
        if not isinstance(data.get("extraction_version"), str):
            errors.append("Kuroshio extraction_version is required")
        try:
            if not iso_day(data["valid_from"]) <= iso_day(data["issued_on"]) <= iso_day(data["valid_until"]):
                errors.append("Kuroshio validity dates are inconsistent")
        except (KeyError, ValueError, TypeError):
            pass
    return errors


def validate_longrange(data, now=None):
    """Strict publication gate: today's complete calendar, truthful Kuroshio factors."""
    from datetime import datetime, timedelta, timezone
    jst = timezone(timedelta(hours=9))
    now = now or datetime.now(jst)
    if now.utcoffset() is None:
        return ["Long-range validation requires an aware current timestamp"]
    today = now.astimezone(jst).date()
    if not isinstance(data, dict):
        return ["Long-range document must be an object"]
    errors = []
    if data.get("schema_version") != 2:
        errors.append("Unsupported long-range schema version")
    try:
        generated = datetime.fromisoformat(data.get("generated_at", ""))
        if generated.utcoffset() is None:
            raise ValueError("naive timestamp")
        if generated > now + timedelta(minutes=5) or now - generated >= timedelta(hours=36):
            errors.append("Long-range generation is expired or in the future")
        if generated.astimezone(jst).date() != today:
            errors.append("Long-range calendar must be generated today for publication")
    except (ValueError, TypeError):
        errors.append("Long-range generated_at must include a timezone")
    rows = data.get("days")
    calendar_errors = validate_calendar(rows, today)
    errors.extend(calendar_errors)
    expected_coverage = {"start": today.isoformat(), "end": (today + timedelta(days=CALENDAR_DAYS - 1)).isoformat(), "days": CALENDAR_DAYS, "complete": True}
    if data.get("coverage") != expected_coverage:
        errors.append("Long-range coverage metadata does not match today's 183 days")
    kuro = data.get("kuroshio")
    kuro_errors = validate_kuroshio(kuro)
    errors.extend(kuro_errors)
    if not kuro_errors and not calendar_errors:
        for row in rows:
            state = kuroshio_state(kuro, iso_day(row["date"]), today)
            expected_factor = round(state["score"], 3) if state["known"] else None
            if row.get("kuroshio_known") is not state["known"] or row.get("f_kuroshio") != expected_factor or row.get("kuroshio_state") != state["reason"]:
                errors.append(f"Kuroshio factor/known/state mismatch: {row['date']}")
    return errors


def kuroshio_state(data, target, as_of):
    """Keep unknown null in public data; only calculation uses neutral 0.5."""
    errors = validate_kuroshio(data)
    if errors:
        raise ValueError("; ".join(errors))
    start = iso_day(data.get("valid_from", data["updated"]))
    end = iso_day(data["valid_until"])
    issue = iso_day(data.get("issued_on", data["updated"]))
    reason = "known"
    if issue > as_of:
        reason = "not_yet_issued"
    elif end < as_of:
        reason = "expired"
    elif target < start or target > end:
        reason = "outside_period"
    elif data.get("status") == "unknown":
        reason = "unassessed"
    known = reason == "known"
    return {"known": known, "source_available": True, "reason": reason,
            "score": data["score"] if known else None,
            "calculation_factor": data["score"] if known else 0.5}


def validate_calendar(days, start, count=CALENDAR_DAYS):
    """Fail a truncated/gapped calendar rather than silently publishing fewer days."""
    from datetime import timedelta
    errors = []
    expected = [(start + timedelta(days=i)).isoformat() for i in range(count)]
    if not isinstance(days, list) or [d.get("date") for d in days if isinstance(d, dict)] != expected:
        return [f"Calendar must contain {count} consecutive dates starting {start}"]
    for row in days:
        if not finite(row.get("moon_age")) or not 0 <= row["moon_age"] < 30:
            errors.append(f"Invalid moon age: {row['date']}")
        curve = row.get("tide_curve")
        if not isinstance(curve, list) or len(curve) != 11 or not all(finite(v) for v in curve):
            errors.append(f"Invalid astronomical tide curve: {row['date']}")
        if not finite(row.get("aim_score")) or not 0 <= row["aim_score"] <= 100:
            errors.append(f"Invalid reference score: {row['date']}")
        if row.get("kuroshio_known") is False and row.get("f_kuroshio") is not None:
            errors.append(f"Unknown Kuroshio factor must be null: {row['date']}")
    return errors
