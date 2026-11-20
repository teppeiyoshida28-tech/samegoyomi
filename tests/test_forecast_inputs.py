import contextlib
import copy
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
import domain
import environment_inputs as inputs
import forecast_engine as fe
import models
from enrich_forecast import require_fresh_forecast
from validation import validate_input_sources

NOW = datetime(2026, 11, 21, 6, 30, tzinfo=domain.JST)
TODAY = "2026-11-21"


def payload(units, start=8, stop=15):
    defaults = {"sea_surface_temperature": 21.0, "ocean_current_velocity": 3.7,
                "ocean_current_direction": 45.0, "sea_level_height_msl": .2,
                "wave_height": 1.0, "wave_period": 6.0, "wind_speed_10m": 3.0,
                "wind_direction_10m": 90.0, "visibility": 10000.0, "precipitation": 0.0}
    return {"latitude": inputs.LATITUDE, "longitude": inputs.LONGITUDE,
            "timezone": "Asia/Tokyo", "utc_offset_seconds": 32400,
            "hourly_units": {"time": "iso8601", **units},
            "hourly": {"time": [f"{TODAY}T{h:02d}:00" for h in range(start, stop)],
                       **{key: [defaults[key]] * (stop - start) for key in units}},
            "source_metadata": {"api_model": "fixture"}}


class ForecastInputTests(unittest.TestCase):
    def test_smoc_and_waves_are_separate_and_joined_by_time(self):
        ocean, waves = payload(inputs.OCEAN_UNITS), payload(inputs.WAVE_UNITS, 9)
        responses = [Mock(json=Mock(return_value=p)) for p in (ocean, waves)]
        with patch.object(inputs.requests, "get", side_effect=responses) as get:
            result = inputs.fetch_marine()
        self.assertEqual(get.call_args_list[0].kwargs["params"]["models"], "meteofrance_currents")
        self.assertNotIn("wave_height", get.call_args_list[0].kwargs["params"]["hourly"])
        self.assertEqual(get.call_args_list[1].kwargs["params"]["models"], "best_match")
        self.assertIsNone(result["hourly"]["wave_height"][0])
        self.assertEqual(result["hourly"]["wave_height"][1], 1.0)
        self.assertIsNone(result["source_metadata"]["ocean"]["model_run_at"])

    def test_invalid_transport_is_rejected(self):
        good = payload(inputs.OCEAN_UNITS)
        for mutate in [lambda p: p["hourly_units"].update(ocean_current_velocity="m/s"),
                       lambda p: p.update(timezone="UTC", utc_offset_seconds=0),
                       lambda p: p.update(latitude=0),
                       lambda p: p["hourly"]["time"].__setitem__(1, p["hourly"]["time"][0]),
                       lambda p: p["hourly"]["sea_surface_temperature"].pop()]:
            bad = copy.deepcopy(good)
            mutate(bad)
            with self.assertRaises(ValueError):
                inputs.validate_payload(bad, inputs.OCEAN_UNITS)

    def test_invalid_values_stay_missing_not_calm(self):
        data = payload(inputs.OCEAN_UNITS)
        data["hourly"]["ocean_current_velocity"] = [None, float("nan"), -1, True, 0, 3, 4]
        data["hourly"]["ocean_current_direction"][-1] = 360
        cleaned = inputs.validate_payload(data, inputs.OCEAN_UNITS)["hourly"]
        self.assertEqual(cleaned["ocean_current_velocity"], [None, None, None, None, 0, 3, 4])
        self.assertEqual(cleaned["ocean_current_direction"][-1], 0)

    def run_fixture(self, *, missing=False):
        marine = payload({**inputs.OCEAN_UNITS, **inputs.WAVE_UNITS})
        weather = payload(inputs.WEATHER_UNITS, 7)
        weather["hourly"]["wind_speed_10m"][1] = 20  # actual 08:00, not index 0
        if missing:
            marine["hourly"]["ocean_current_velocity"][0] = None
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            for name, value in (("fetch_marine", marine), ("fetch_weather", weather),
                                ("load_climatology", {"daily_climatology": {"11-21": {"mean": 20}}, "source": "fixture", "years_used": []}),
                                ("load_blog_observations", {}), ("load_learned_weights", {"raw_weights": {}, "bias": 0}),
                                ("load_point_stats", None), ("load_moon_tide_calendar", {}), ("now_jst", NOW)):
                stack.enter_context(patch.object(fe, name, return_value=value))
            write = stack.enter_context(patch.object(fe, "write_json"))
            if missing:
                with self.assertRaises(RuntimeError):
                    fe.run()
                write.assert_not_called()
                return None
            fe.run()
            return write.call_args.args[1]

    def test_weather_is_aligned_by_timestamp(self):
        result = self.run_fixture()
        self.assertEqual(result["daily"][0]["max_wind"], 20)
        self.assertFalse(result["daily"][0]["diveable"])
        self.assertEqual(result["hourly"][0]["wind_ms"], 20)

    def test_incomplete_today_preserves_previous_output(self):
        self.run_fixture(missing=True)

    def test_old_reports_are_not_recent_priors(self):
        obs = {"2026-09-01": {"visibility_hi": 30, "hammer_seen": True, "hammer_size": "large"},
               "2026-11-20": {"visibility_hi": 12, "hammer_seen": False},
               "2026-11-22": {"visibility_hi": 30}}
        self.assertEqual([d for d, _ in fe.recent_observation_items(obs, TODAY, 3)], ["2026-11-20"])
        model = models.EnvModels.__new__(models.EnvModels)
        model.obs, model.obs_dates = obs, sorted(obs)
        features = model.hist_features(TODAY)
        self.assertEqual(features["vis3"], 12)
        self.assertEqual(features["recent_observation_dates"], ["2026-11-20"])
        self.assertEqual(features["latest_observation_date"], "2026-11-20")
        model.temp_dates = ["2026-09-01"]
        model.obs_temp_days = [("2026-09-01", 30)]
        model.om_daily = {"2026-09-01": 25}
        self.assertEqual(model.sst_offset(TODAY), 0)

    def test_live_failure_cannot_reuse_cached_forecast(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            folder = Path(directory)
            for name in ("hindcast_marine.json", "hindcast_weather.json"):
                (folder / name).write_text(json.dumps({"hourly": {"time": []}}), encoding="utf-8")
            cached = {"marine": {f"{TODAY}T{h:02d}:00": [21, 3, 45, 1, .2] for h in range(8, 15)},
                      "weather": {f"{TODAY}T{h:02d}:00": [3, 90, 0] for h in range(8, 15)}}
            (folder / "recent_environment.json").write_text(json.dumps(cached), encoding="utf-8")
            with patch.object(models, "PH1", folder), patch.object(models, "today_jst", return_value=NOW.date()), \
                    patch.object(fe, "fetch_marine", side_effect=RuntimeError("offline")), \
                    patch.object(fe, "fetch_weather", side_effect=RuntimeError("offline")), \
                    patch.object(fe, "load_climatology", return_value={}), \
                    patch.object(fe, "load_blog_observations", return_value={}):
                with self.assertRaises(RuntimeError):
                    models.EnvModels(quiet=True, require_fresh=True)
                fallback = models.EnvModels(quiet=True)
                self.assertIsNone(fallback.day_env(TODAY))

    def test_enrichment_rejects_stale_or_unproven_input(self):
        data = {"issued_at": NOW.isoformat(), "daily": [{"date": TODAY}],
                "input_pipeline_version": inputs.INPUT_VERSION,
                "input_sources": {"marine": {"ocean": {"api_model": inputs.API_MODEL}}}}
        require_fresh_forecast(data, NOW)
        with self.assertRaises(RuntimeError):
            require_fresh_forecast({**data, "issued_at": (NOW - timedelta(days=1)).isoformat()}, NOW)
        with self.assertRaises(RuntimeError):
            require_fresh_forecast({**data, "input_sources": {}}, NOW)

    def test_publication_requires_fresh_sources_for_forecast_and_ml(self):
        def source(model, url, units):
            return {"provider": "Open-Meteo", "api_model": model, "api_url": url,
                    "timezone": "Asia/Tokyo", "units": units, "model_run_at": None,
                    "retrieved_at": NOW.isoformat(),
                    "requested_coordinates": {"latitude": inputs.LATITUDE, "longitude": inputs.LONGITUDE},
                    "grid_coordinates": {"latitude": inputs.LATITUDE, "longitude": inputs.LONGITUDE},
                    "coverage_start": f"{TODAY}T00:00", "coverage_end": f"{TODAY}T23:00"}
        sources = {"marine": {
            "ocean": source(inputs.API_MODEL, inputs.API_URL, dict(inputs.OCEAN_UNITS)),
            "waves": source("best_match", inputs.API_URL, dict(inputs.WAVE_UNITS))},
            "weather": source("best_match", inputs.WEATHER_URL, dict(inputs.WEATHER_UNITS))}
        data = {"issued_at": NOW.isoformat(), "input_pipeline_version": inputs.INPUT_VERSION,
                "hourly": [{"time": f"{TODAY}T{h:02d}:00", "hour": h} for h in range(8, 15)],
                "input_sources": copy.deepcopy(sources),
                "ml_meta": {"generated_at": NOW.isoformat(), "input_sources": copy.deepcopy(sources)}}
        self.assertEqual(validate_input_sources(data, NOW), [])
        mutations = [
            lambda d: d.update(input_pipeline_version="legacy"),
            lambda d: d["input_sources"]["marine"]["ocean"].update(api_model="best_match"),
            lambda d: d["input_sources"]["marine"]["ocean"]["units"].update(ocean_current_velocity="m/s"),
            lambda d: d["ml_meta"]["input_sources"]["weather"].update(retrieved_at=(NOW - timedelta(days=2)).isoformat()),
            lambda d: d["ml_meta"]["input_sources"]["weather"].update(coverage_end=f"{TODAY}T12:00"),
            lambda d: d["input_sources"]["marine"]["ocean"].update(model_run_at=NOW.isoformat()),
            lambda d: d["input_sources"]["weather"]["grid_coordinates"].update(latitude=0),
            lambda d: d["ml_meta"].update(generated_at=(NOW - timedelta(days=1)).isoformat()),
            lambda d: d.pop("ml_meta"),
        ]
        for mutate in mutations:
            bad = copy.deepcopy(data)
            mutate(bad)
            self.assertTrue(validate_input_sources(bad, NOW))


if __name__ == "__main__":
    unittest.main()
