"""Validated, timestamp-aligned environmental inputs for live forecasts.

SMOC supplies SST/current/sea level; wave models supply waves separately.
Retrieval timestamps are not model-run timestamps. Missing values stay missing.
"""
from datetime import datetime, timedelta
import math

import requests

from domain import now_jst
from fetch_ocean_current import API_MODEL, API_URL, LATITUDE, LONGITUDE

INPUT_VERSION = "2026-11-explicit-smoc-v1"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
OCEAN_UNITS = {"sea_surface_temperature": "°C", "ocean_current_velocity": "km/h",
               "ocean_current_direction": "°", "sea_level_height_msl": "m"}
WAVE_UNITS = {"wave_height": "m", "wave_period": "s"}
WEATHER_UNITS = {"wind_speed_10m": "m/s", "wind_direction_10m": "°",
                 "visibility": "m", "precipitation": "mm"}
NONNEGATIVE = {"ocean_current_velocity", "wave_height", "wave_period",
               "wind_speed_10m", "visibility", "precipitation"}
DIRECTIONS = {"ocean_current_direction", "wind_direction_10m"}


def finite(value):
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def normalized_time(value):
    if not isinstance(value, str):
        raise ValueError("Environmental target time must be an ISO string")
    value = datetime.fromisoformat(value)
    if value.tzinfo is not None and value.utcoffset() != timedelta(hours=9):
        raise ValueError("Environmental timestamps must use JST")
    if value.minute or value.second or value.microsecond:
        raise ValueError("Environmental targets must be whole hours")
    return value.replace(tzinfo=None).isoformat(timespec="minutes")


def validate_payload(payload, expected_units):
    if not isinstance(payload, dict):
        raise ValueError("Environmental API response must be an object")
    if payload.get("timezone") != "Asia/Tokyo" or payload.get("utc_offset_seconds") != 32400:
        raise ValueError("Environmental API timezone is not JST")
    for key, requested in (("latitude", LATITUDE), ("longitude", LONGITUDE)):
        value = payload.get(key)
        if not finite(value) or abs(value - requested) > .2:
            raise ValueError("Environmental API returned a different region")
    units = payload.get("hourly_units", {})
    if units.get("time") != "iso8601":
        raise ValueError("Environmental time unit must be iso8601")
    for key, unit in expected_units.items():
        if units.get(key) != unit:
            raise ValueError(f"Unexpected environmental unit: {key}={units.get(key)!r}; expected {unit}")
    hourly = payload.get("hourly", {})
    raw_times = hourly.get("time")
    if not isinstance(raw_times, list) or not raw_times:
        raise ValueError("Environmental hourly targets are empty")
    times = [normalized_time(value) for value in raw_times]
    for previous, current in zip(times, times[1:]):
        if datetime.fromisoformat(current) - datetime.fromisoformat(previous) != timedelta(hours=1):
            raise ValueError("Environmental targets must be unique, ordered and hourly")
    result, invalid = {"time": times}, {}
    for key in expected_units:
        values = hourly.get(key)
        if not isinstance(values, list) or len(values) != len(times):
            raise ValueError(f"Environmental time/value array mismatch: {key}")
        cleaned = []
        for value in values:
            valid = finite(value)
            valid = valid and (key not in NONNEGATIVE or value >= 0)
            valid = valid and (key not in DIRECTIONS or 0 <= value <= 360)
            cleaned.append((value % 360 if key in DIRECTIONS else value) if valid else None)
        result[key] = cleaned
        invalid[key] = sum(value is None for value in cleaned)
    return {**payload, "hourly": result, "invalid_value_counts": invalid}


def indexed_hours(payload):
    hourly = payload["hourly"]
    keys = [key for key in hourly if key != "time"]
    return {time: {key: hourly[key][i] for key in keys}
            for i, time in enumerate(hourly["time"])}


def _fetch(url, units, *, model, forecast_days, past_days=0, weather=False):
    params = {"latitude": LATITUDE, "longitude": LONGITUDE,
              "hourly": ",".join(units), "forecast_days": forecast_days,
              "timezone": "Asia/Tokyo", "wind_speed_unit": "ms" if weather else "kmh",
              "models": model}
    if not weather:
        params["cell_selection"] = "sea"
    if past_days:
        params["past_days"] = past_days
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    payload = validate_payload(response.json(), units)
    payload["source_metadata"] = {
        "provider": "Open-Meteo", "api_url": url, "api_model": model,
        "retrieved_at": now_jst().isoformat(), "model_run_at": None,
        "requested_coordinates": {"latitude": LATITUDE, "longitude": LONGITUDE},
        "grid_coordinates": {key: payload[key] for key in ("latitude", "longitude")},
        "timezone": "Asia/Tokyo", "units": dict(units),
        "coverage_start": payload["hourly"]["time"][0],
        "coverage_end": payload["hourly"]["time"][-1],
    }
    return payload


def fetch_marine(*, forecast_days=14, past_days=0):
    ocean = _fetch(API_URL, OCEAN_UNITS, model=API_MODEL, forecast_days=forecast_days, past_days=past_days)
    waves = _fetch(API_URL, WAVE_UNITS, model="best_match", forecast_days=forecast_days, past_days=past_days)
    by_time = indexed_hours(waves)
    hourly = dict(ocean["hourly"])
    for key in WAVE_UNITS:
        hourly[key] = [by_time.get(t, {}).get(key) for t in hourly["time"]]
    return {**ocean, "hourly": hourly,
            "hourly_units": {**ocean["hourly_units"], **waves["hourly_units"]},
            "source_metadata": {"ocean": ocean["source_metadata"], "waves": waves["source_metadata"]}}


def fetch_weather(*, forecast_days=14, past_days=0):
    return _fetch(WEATHER_URL, WEATHER_UNITS, model="best_match", forecast_days=forecast_days,
                  past_days=past_days, weather=True)
