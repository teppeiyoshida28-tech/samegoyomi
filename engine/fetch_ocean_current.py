"""Fetch an independent, validated SMOC surface-current snapshot.

This does not run the sighting model, scrape blogs, or modify forecast_data.json.
CLI: python engine/fetch_ocean_current.py
     python engine/fetch_ocean_current.py --validate-only
The API does not expose its model-run timestamp; retrieval time is not a model run.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

JST = timezone(timedelta(hours=9), name="JST")
API_URL = "https://marine-api.open-meteo.com/v1/marine"
API_MODEL = "meteofrance_currents"
LATITUDE, LONGITUDE = 34.5717, 138.9433
# Accept normal nearest-sea-cell selection, while rejecting another region.
MAX_GRID_OFFSET_DEGREES = 0.2
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "ocean_current_forecast.json"
MAX_AGE = timedelta(hours=36)
MIN_FORECAST_HORIZON = timedelta(hours=48)
SPEED = "ocean_current_velocity"
DIRECTION = "ocean_current_direction"
EXPECTED_UNITS = {"time": "iso8601", SPEED: "km/h", DIRECTION: "°"}


def now_jst():
    return datetime.now(JST)


def finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def require(condition, message):
    if not condition:
        raise ValueError(message)


def aware_jst(value, *, allow_naive=False):
    require(isinstance(value, str), "Timestamp must be an ISO string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid ISO timestamp") from exc
    if allow_naive and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    require(parsed.utcoffset() == timedelta(hours=9), "Timestamp must use JST (+09:00)")
    return parsed.astimezone(JST)


def coordinates_valid(value):
    return (isinstance(value, dict)
            and finite_number(value.get("latitude"))
            and finite_number(value.get("longitude"))
            and -90 <= value["latitude"] <= 90
            and -180 <= value["longitude"] <= 180)


def grid_near_request(value):
    return (coordinates_valid(value)
            and abs(value["latitude"] - LATITUDE) <= MAX_GRID_OFFSET_DEGREES
            and abs(value["longitude"] - LONGITUDE) <= MAX_GRID_OFFSET_DEGREES)


def build_snapshot(payload, *, retrieved_at=None):
    """Validate the transport contract; retain missing/invalid pairs as nulls.

    Values above 6 kt remain unchanged. Six knots is a display scale, not a data
    limit. Direction 360 is the same as north and is normalized to 0 degrees.
    """
    require(isinstance(payload, dict), "Marine response must be an object")
    require(payload.get("timezone") == "Asia/Tokyo", "Unexpected marine timezone")
    require(payload.get("utc_offset_seconds") == 32400, "Unexpected marine UTC offset")
    units = payload.get("hourly_units")
    require(isinstance(units, dict), "Marine units are missing")
    for key, unit in EXPECTED_UNITS.items():
        require(units.get(key) == unit, f"Unexpected unit for {key}: expected {unit}")
    grid = {"latitude": payload.get("latitude"), "longitude": payload.get("longitude")}
    require(coordinates_valid(grid), "Invalid returned grid coordinates")
    require(grid_near_request(grid), "Returned grid is outside the requested region")
    hourly = payload.get("hourly")
    require(isinstance(hourly, dict), "Marine hourly data are missing")
    times, speeds, directions = (hourly.get(key) for key in ("time", SPEED, DIRECTION))
    require(all(isinstance(values, list) for values in (times, speeds, directions)),
            "Marine hourly variables must be arrays")
    require(len(times) > 0 and len(times) == len(speeds) == len(directions),
            "Marine time/value array lengths must match and be nonempty")

    fetched = retrieved_at or now_jst()
    require(isinstance(fetched, datetime) and fetched.utcoffset() is not None,
            "Retrieval time must be timezone-aware")
    fetched = fetched.astimezone(JST).replace(microsecond=0)
    rows, previous = [], None
    for raw_time, speed, direction in zip(times, speeds, directions):
        target = aware_jst(raw_time, allow_naive=True)
        require(target.minute == target.second == target.microsecond == 0,
                "Marine targets must be whole hours")
        require(previous is None or target - previous == timedelta(hours=1),
                "Marine timestamps must be ordered, unique, and hourly")
        previous = target
        valid = (finite_number(speed) and speed >= 0 and finite_number(direction)
                 and 0 <= direction <= 360)
        rows.append({
            "time": target.isoformat(timespec="minutes"),
            "current_velocity_kmh": float(speed) if valid else None,
            "current_direction": float(direction % 360) if valid else None,
            "status": "available" if valid else "missing",
        })
    available = [row for row in rows if row["status"] == "available"]
    status = "unavailable" if not available else "available" if len(available) == len(rows) else "partial"
    return {
        "schema_version": 1,
        "retrieved_at": fetched.isoformat(),
        "expires_at": (fetched + MAX_AGE).isoformat(),
        "status": status,
        "source": {
            "provider": "Open-Meteo",
            "model": "Copernicus Marine SMOC",
            "api_model": API_MODEL,
            "source_url": "https://open-meteo.com/en/docs/marine-weather-api",
            "model_source_url": "https://data.marine.copernicus.eu/product/GLOBAL_ANALYSISFORECAST_PHY_001_024/description",
            "model_run_at": None,
            "resolution_km": 8,
            "depth": "surface",
            "requested_coordinates": {"latitude": LATITUDE, "longitude": LONGITUDE},
            "grid_coordinates": grid,
            "velocity_unit": "km/h",
            "direction_convention": "towards",
            "timezone": "Asia/Tokyo",
            "tides_included": True,
            "waves_included": True,
        },
        "hourly": rows,
        "coverage": {
            "start": rows[0]["time"],
            "end": rows[-1]["time"],
            "last_valid_time": available[-1]["time"] if available else None,
        },
    }


def validate_snapshot(snapshot, *, now=None, require_current=True):
    """Return errors; default requires fresh data covering today and 48h ahead.

    Schema-only validation is available for diagnostics. An unavailable payload
    is representable but may not replace the last good published input.
    """
    try:
        require(isinstance(snapshot, dict), "Snapshot must be an object")
        require(type(snapshot.get("schema_version")) is int and snapshot["schema_version"] == 1,
                "Unsupported ocean-current schema")
        retrieved = aware_jst(snapshot.get("retrieved_at"))
        expires = aware_jst(snapshot.get("expires_at"))
        require(expires == retrieved + MAX_AGE, "Expiration must be exactly 36 hours after retrieval")
        source = snapshot.get("source")
        require(isinstance(source, dict), "Current source metadata are missing")
        expected = {"provider": "Open-Meteo", "model": "Copernicus Marine SMOC",
                    "api_model": API_MODEL, "velocity_unit": "km/h",
                    "direction_convention": "towards", "timezone": "Asia/Tokyo", "depth": "surface"}
        for key, value in expected.items():
            require(source.get(key) == value, f"Unexpected source {key}")
        require(source.get("source_url") == "https://open-meteo.com/en/docs/marine-weather-api",
                "Unexpected marine source URL")
        require("model_run_at" in source and source["model_run_at"] is None,
                "API model-run timestamp is unknown; do not substitute retrieval time")
        require(finite_number(source.get("resolution_km")) and source["resolution_km"] == 8,
                "Unexpected source resolution")
        require(source.get("tides_included") is True and source.get("waves_included") is True,
                "SMOC total-current component metadata are missing")
        for key in ("requested_coordinates", "grid_coordinates"):
            require(coordinates_valid(source.get(key)), f"Invalid {key}")
        require(source["requested_coordinates"] == {"latitude": LATITUDE, "longitude": LONGITUDE},
                "Snapshot is for a different requested location")
        require(grid_near_request(source["grid_coordinates"]),
                "Returned grid is outside the requested region")
        rows = snapshot.get("hourly")
        require(isinstance(rows, list) and rows, "Current hourly records are empty")
        previous, valid_times = None, []
        for row in rows:
            require(isinstance(row, dict), "Invalid current hourly record")
            target = aware_jst(row.get("time"))
            require(target.minute == target.second == target.microsecond == 0,
                    "Current targets must be whole hours")
            require(previous is None or target - previous == timedelta(hours=1),
                    "Current timestamps must be ordered, unique, and hourly")
            previous = target
            require("current_velocity_kmh" in row and "current_direction" in row,
                    "Current pair fields are missing")
            speed, direction = row["current_velocity_kmh"], row["current_direction"]
            if row.get("status") == "available":
                require(finite_number(speed) and speed >= 0 and finite_number(direction)
                        and 0 <= direction < 360, "Invalid available current pair")
                valid_times.append(target)
            else:
                require(row.get("status") == "missing" and speed is None and direction is None,
                        "Missing currents require a null speed/direction pair")
        expected_status = "unavailable" if not valid_times else "available" if len(valid_times) == len(rows) else "partial"
        require(snapshot.get("status") == expected_status, "Current status does not match its records")
        coverage = snapshot.get("coverage")
        require(isinstance(coverage, dict), "Current coverage metadata are missing")
        require(coverage.get("start") == rows[0]["time"] and coverage.get("end") == rows[-1]["time"],
                "Coverage bounds do not match hourly records")
        expected_last = valid_times[-1].isoformat(timespec="minutes") if valid_times else None
        require(coverage.get("last_valid_time") == expected_last, "Last valid time does not match current records")
        if require_current:
            current = now or now_jst()
            require(isinstance(current, datetime) and current.utcoffset() is not None,
                    "Validation time must be timezone-aware")
            current = current.astimezone(JST)
            require(retrieved <= current + timedelta(minutes=5), "Retrieval time is in the future")
            require(current < expires, "Ocean-current snapshot has expired")
            require(any(t.date() == current.date() for t in valid_times), "No valid currents for today (JST)")
            require(any(t >= current + MIN_FORECAST_HORIZON for t in valid_times),
                    "No valid ocean-current forecast at least 48 hours ahead")
        return []
    except (ValueError, TypeError, KeyError) as exc:
        return [str(exc)]


def fetch_snapshot():
    response = requests.get(API_URL, params={
        "latitude": LATITUDE, "longitude": LONGITUDE,
        "hourly": f"{SPEED},{DIRECTION}", "models": API_MODEL,
        "forecast_days": 10, "timezone": "Asia/Tokyo",
        "cell_selection": "sea", "wind_speed_unit": "kmh",
    }, timeout=60)
    response.raise_for_status()
    return build_snapshot(response.json(), retrieved_at=now_jst())


def write_snapshot(path, snapshot):
    """Replace only a validated snapshot, atomically, without truncating the old one."""
    problems = validate_snapshot(snapshot)
    require(not problems, "; ".join(problems))
    serialized = json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true", help="Check the existing snapshot without fetching")
    parser.add_argument("--input", type=Path, default=DEFAULT_OUTPUT, help="Snapshot checked by --validate-only")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Destination of a successful fetch")
    args = parser.parse_args(argv)
    try:
        if args.validate_only:
            snapshot = json.loads(args.input.read_text(encoding="utf-8"))
            problems = validate_snapshot(snapshot)
            require(not problems, "; ".join(problems))
            print(f"[ocean-current] valid: {snapshot['status']}; last valid {snapshot['coverage']['last_valid_time']}")
        else:
            snapshot = fetch_snapshot()
            write_snapshot(args.output, snapshot)
            print(f"[ocean-current] saved {args.output.name}: {snapshot['status']}; "
                  f"retrieved {snapshot['retrieved_at']}; last valid {snapshot['coverage']['last_valid_time']}")
        return 0
    except (requests.RequestException, OSError, ValueError, TypeError) as exc:
        print(f"[ocean-current] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
