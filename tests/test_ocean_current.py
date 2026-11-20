"""Transport, truthfulness and preservation checks for independent current data."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
import fetch_ocean_current as current

NOW = datetime(2026, 11, 21, 3, 30, tzinfo=current.JST)


def response_fixture():
    start = NOW.replace(hour=2, minute=0, tzinfo=None)
    return {"latitude": 34.541664, "longitude": 138.95836,
            "timezone": "Asia/Tokyo", "utc_offset_seconds": 32400,
            "hourly_units": {"time": "iso8601", current.SPEED: "km/h", current.DIRECTION: "°"},
            "hourly": {"time": [(start + timedelta(hours=hour)).isoformat(timespec="minutes")
                                for hour in range(52)],
                       current.SPEED: [0, 0.1852, 3.704, 12.964] + [1.0] * 48,
                       current.DIRECTION: [0, 90, 180, 270] + [90] * 48}}


class OceanCurrentTests(unittest.TestCase):
    def snapshot(self, response=None):
        return current.build_snapshot(response or response_fixture(), retrieved_at=NOW)

    def test_units_direction_and_values_are_preserved_without_display_clamping(self):
        result = self.snapshot()
        self.assertEqual(current.validate_snapshot(result, now=NOW), [])
        self.assertEqual([row["current_velocity_kmh"] for row in result["hourly"][:4]], [0, 0.1852, 3.704, 12.964])
        self.assertEqual([row["current_direction"] for row in result["hourly"][:4]], [0, 90, 180, 270])
        self.assertEqual(result["source"]["direction_convention"], "towards")
        self.assertIsNone(result["source"]["model_run_at"])
        self.assertNotEqual(result["source"]["requested_coordinates"], result["source"]["grid_coordinates"])
        self.assertEqual(current.aware_jst(result["expires_at"]) - NOW, timedelta(hours=36))

    def test_transport_units_timezone_lengths_and_timestamp_order_are_strict(self):
        changes = [lambda d: d["hourly_units"].update({current.SPEED: "m/s"}),
                   lambda d: d.update(timezone="GMT"),
                   lambda d: d.update(utc_offset_seconds=0),
                   lambda d: d["hourly"][current.SPEED].pop(),
                   lambda d: d["hourly"]["time"].reverse(),
                   lambda d: d["hourly"]["time"].__setitem__(1, d["hourly"]["time"][0]),
                   lambda d: d["hourly"]["time"].__setitem__(1, "2026-11-21T03:15"),
                   lambda d: d.update(latitude=float("nan"))]
        for change in changes:
            with self.subTest(change=change):
                data = response_fixture()
                change(data)
                with self.assertRaises(ValueError):
                    self.snapshot(data)

    def test_missing_or_invalid_pair_is_null_not_zero_or_invented_direction(self):
        for speed, direction in [(None, 45), (1, None), (-1, 45), (float("nan"), 45),
                                 (float("inf"), 45), (1, float("nan")), (1, -1),
                                 (1, 361), (True, 90), ("1", 90), (10 ** 1000, 90)]:
            with self.subTest(speed=speed, direction=direction):
                data = response_fixture()
                data["hourly"][current.SPEED][1] = speed
                data["hourly"][current.DIRECTION][1] = direction
                result = self.snapshot(data)
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["hourly"][1], {"time": "2026-11-21T03:00+09:00",
                                 "current_velocity_kmh": None, "current_direction": None, "status": "missing"})
                self.assertEqual(current.validate_snapshot(result, now=NOW), [])
                json.dumps(result, allow_nan=False)

    def test_360_degrees_normalizes_to_north(self):
        data = response_fixture()
        data["hourly"][current.DIRECTION][0] = 360
        self.assertEqual(self.snapshot(data)["hourly"][0]["current_direction"], 0)

    def test_distant_grid_is_rejected_but_normal_sea_cell_offset_is_allowed(self):
        for latitude, longitude in [(35, 138.95836), (34.541664, 139.5)]:
            with self.subTest(latitude=latitude, longitude=longitude):
                data = response_fixture()
                data.update(latitude=latitude, longitude=longitude)
                with self.assertRaisesRegex(ValueError, "outside the requested region"):
                    self.snapshot(data)
                result = self.snapshot()
                result["source"]["grid_coordinates"] = {"latitude": latitude, "longitude": longitude}
                self.assertIn("outside the requested region", current.validate_snapshot(result, now=NOW)[0])
        data = response_fixture()
        data.update(latitude=current.LATITUDE + 0.19, longitude=current.LONGITUDE - 0.19)
        self.assertEqual(current.validate_snapshot(self.snapshot(data), now=NOW), [])

    def test_null_tail_retains_requested_coverage_but_last_valid_time_is_separate(self):
        data = response_fixture()
        data["hourly"][current.SPEED][-1] = None
        result = self.snapshot(data)
        self.assertEqual(result["coverage"]["end"], "2026-11-23T05:00+09:00")
        self.assertEqual(result["coverage"]["last_valid_time"], "2026-11-23T04:00+09:00")
        self.assertEqual(current.validate_snapshot(result, now=NOW), [])

    def test_unavailable_payload_is_representable_but_cannot_pass_publication_validation(self):
        data = response_fixture()
        data["hourly"][current.SPEED] = [None] * len(data["hourly"]["time"])
        result = self.snapshot(data)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["coverage"]["last_valid_time"])
        self.assertEqual(current.validate_snapshot(result, require_current=False), [])
        self.assertTrue(current.validate_snapshot(result, now=NOW))

    def test_expiry_is_exclusive_and_future_retrieval_is_rejected(self):
        result = self.snapshot()
        self.assertTrue(current.validate_snapshot(result, now=NOW + timedelta(hours=36)))
        self.assertTrue(current.validate_snapshot(result, now=NOW - timedelta(hours=1)))

    def test_freshness_alone_does_not_prove_today_and_future_coverage(self):
        result = self.snapshot()
        self.assertTrue(current.validate_snapshot(result, now=NOW + timedelta(hours=3)))
        data = response_fixture()
        data["hourly"]["time"] = [(datetime.fromisoformat(value) + timedelta(days=1)).isoformat(timespec="minutes")
                                 for value in data["hourly"]["time"]]
        self.assertTrue(current.validate_snapshot(self.snapshot(data), now=NOW))

    def test_48_hour_horizon_rejects_short_coverage_but_allows_partial_intermediate_hours(self):
        data = response_fixture()
        for key in data["hourly"]:
            data["hourly"][key] = data["hourly"][key][:4]
        self.assertIn("48 hours", current.validate_snapshot(self.snapshot(data), now=NOW)[0])
        data = response_fixture()
        data["hourly"][current.SPEED][2:-1] = [None] * 49
        partial = self.snapshot(data)
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(current.validate_snapshot(partial, now=NOW), [])
        # A whole-hour target exactly 48 h ahead is inclusive.
        self.assertEqual(current.validate_snapshot(partial, now=NOW.replace(minute=0) + timedelta(hours=2)), [])
        self.assertIn("48 hours", current.validate_snapshot(partial, now=NOW.replace(minute=0) + timedelta(hours=2, seconds=1))[0])

    def test_saved_schema_rejects_inconsistent_status_coverage_and_invalid_numbers(self):
        changes = [lambda d: d.update(status="partial"),
                   lambda d: d["coverage"].update(last_valid_time=d["coverage"]["start"]),
                   lambda d: d["source"].update(model_run_at=d["retrieved_at"]),
                   lambda d: d["hourly"][0].update(current_velocity_kmh=float("nan")),
                   lambda d: d["hourly"][0].update(status="missing"),
                   lambda d: d["hourly"][0].update(time="2026-11-21T02:00"),
                   lambda d: d.update(schema_version=True)]
        for change in changes:
            result = self.snapshot()
            change(result)
            self.assertTrue(current.validate_snapshot(result, now=NOW))

    @patch.object(current, "now_jst", return_value=NOW)
    def test_fetch_uses_explicit_current_model_and_current_only_variables(self, clock):
        reply = Mock()
        reply.json.return_value = response_fixture()
        with patch.object(current.requests, "get", return_value=reply) as request:
            result = current.fetch_snapshot()
        params = request.call_args.kwargs["params"]
        self.assertEqual(params["models"], "meteofrance_currents")
        self.assertEqual(params["hourly"], "ocean_current_velocity,ocean_current_direction")
        self.assertEqual(params["wind_speed_unit"], "kmh")
        reply.raise_for_status.assert_called_once()
        self.assertEqual(current.validate_snapshot(result, now=NOW), [])

    @patch.object(current, "now_jst", return_value=NOW)
    def test_http_or_data_failure_keeps_last_good_file(self, clock):
        for error in [current.requests.HTTPError("HTTP 503"), ValueError("Unexpected units")]:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "snapshot.json"
                previous = b"last good content\n"
                path.write_bytes(previous)
                with patch.object(current, "fetch_snapshot", side_effect=error), redirect_stderr(io.StringIO()):
                    self.assertEqual(current.main(["--output", str(path)]), 1)
                self.assertEqual(path.read_bytes(), previous)
                self.assertEqual(list(Path(directory).iterdir()), [path])

    @patch.object(current, "now_jst", return_value=NOW)
    def test_atomic_write_and_read_only_validation_cli(self, clock):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            with patch.object(current, "fetch_snapshot", return_value=self.snapshot()), redirect_stdout(io.StringIO()):
                self.assertEqual(current.main(["--output", str(path)]), 0)
                before = path.read_bytes()
                self.assertEqual(current.main(["--validate-only", "--input", str(path)]), 0)
                self.assertEqual(path.read_bytes(), before)
            with patch.object(current, "now_jst", return_value=NOW + timedelta(hours=36)), redirect_stderr(io.StringIO()):
                self.assertEqual(current.main(["--validate-only", "--input", str(path)]), 1)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    @patch.object(current, "now_jst", return_value=NOW)
    def test_replace_failure_preserves_previous_file_and_removes_temporary_file(self, clock):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text("previous", encoding="utf-8")
            with patch.object(current.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    current.write_snapshot(path, self.snapshot())
            self.assertEqual(path.read_text(encoding="utf-8"), "previous")
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
