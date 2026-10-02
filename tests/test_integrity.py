import copy
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "engine"), str(ROOT / "data")]
import domain
import forecast_engine as fe
import structure_full
from models import EnvModels, tier_matrix, fit_probability
from evaluation import actual_record, binary_metrics, baselines, summarize
from forecast_archive import save_snapshot, build_issued_history
from validation import validate_forecast
from full_scraper import retry_due, SLEEP


def fixture():
    day = {"date": "2026-10-02", "forecast_kind": "full", "score": 65, "tier": "C",
           "max_wave": 2.08, "max_wind": 14.6, "diveable": False,
           "best_point": "jab_ne", "best_point_score": .8,
           "best_point_evidence": {"stats_key": "jab_ne"},
           "top3_points": [{"point": "jab_ne", "score": .8}],
           "ml": {"points": ["jab_ne"], "size_num": 2, "tier": "C", "visibility_m": 15,
                  "water_temp": 25, "seen_probability": .8, "large_probability": .2}}
    hourly = [{"time": f"2026-10-02T{h:02d}:00", "hour": h, "sst": 25, "wave_height": 2.08,
               "wind_ms": 14.6, "current_velocity_kmh": 3, "current_direction": 90, "sea_level": .5}
              for h in range(8, 15)]
    return {"issued_at": "2026-10-01T06:30:00+09:00", "model_version": domain.MODEL_VERSION,
            "daily": [day], "hourly": hourly}


class IntegrityTests(unittest.TestCase):
    def test_sea_override_and_missing(self):
        self.assertFalse(domain.sea_safety(14.6, 2.08)[0])
        self.assertFalse(domain.sea_safety(None, 2.1)[0])
        self.assertIsNone(domain.sea_safety(3, None)[0])
        self.assertTrue(domain.sea_safety(3, 0)[0])

    def test_unknown_labels_are_not_absent(self):
        for obs in [{"hammer_seen": None}, {"hammer_seen": True, "hammer_size": None}]:
            self.assertIsNone(domain.size_target(obs))
            self.assertIsNone(actual_record(obs)["tier"])
        self.assertEqual(domain.size_target({"hammer_seen": False}), 0)

    def test_legacy_places_not_confirmed_sightings(self):
        self.assertEqual(domain.observed_points({"hammer_seen": True, "hammer_points": [["kame_ne", 3]]}), [])
        self.assertEqual(domain.observed_points({"hammer_seen": True, "sighting_points": ["ao_ne"]}), ["jab_ne"])

    def test_current_has_no_extra_tide(self):
        a = domain.current_vector(3.704, 45, .4)
        b = domain.current_vector(3.704, 45, -.4)
        self.assertEqual(a, b)
        self.assertAlmostEqual(a[0], 3.704)
        self.assertNotEqual(a, domain.current_vector(3.704, 45, .4, "legacy"))
        with self.assertRaises(ValueError): domain.current_vector(2, 90, mode="unverified")

    def test_missing_environment_does_not_become_zero(self):
        model = EnvModels.__new__(EnvModels)
        model.M = {"2026-10-02T08:00": (25, 2, 90, None, 1)}
        model.W = {"2026-10-02T08:00": (4, 0, 0)}
        self.assertIsNone(model.day_env("2026-10-02"))

    def test_baseline_uses_only_issuance_information(self):
        obs = {"2026-09-30": {"hammer_seen": False}, "2026-10-01": {"hammer_seen": True},
               "2026-10-06": {"hammer_seen": True}}
        a = baselines(obs, "2026-10-07", "2026-10-01")
        self.assertEqual(a["persistence"], 0)
        self.assertAlmostEqual(a["seasonal"], 1 / 3)

    def test_metrics_expose_constant_positive_predictor(self):
        m = binary_metrics([(1, True)] * 182 + [(1, False)] * 11)
        self.assertEqual(m["accuracy"], .943)
        self.assertEqual(m["specificity"], 0)
        self.assertEqual(m["auc"], .5)
        self.assertEqual(m["balanced_accuracy"], .5)

    def test_auc_ties_and_missing_class(self):
        self.assertEqual(binary_metrics([(.1, False), (.9, True)])["auc"], 1)
        self.assertIsNone(binary_metrics([(.9, True)])["auc"])
        self.assertEqual(binary_metrics([])["n"], 0)

    def test_unknown_summary_denominators(self):
        p = fixture()["daily"][0]["ml"]
        rows = [{"predicted": p, "actual": actual_record({"hammer_seen": None, "visibility_hi": 15})}]
        s = summarize(rows)
        self.assertEqual(s["n_seen_days"], 0)
        self.assertEqual(s["n_tier_days"], 0)
        self.assertEqual(s["n_point_days"], 0)

    def test_rank_definitions_match(self):
        for vis in (9, 10, 15, 20):
            for size, count in [("single", 3), ("small", 25), ("medium", 60), ("large", 150)]:
                actual = fe.classify_rank(vis, True, size, 0, True)[0]
                self.assertEqual(actual, tier_matrix(vis, count)[0])

    def test_invalid_forecast_is_blocked(self):
        fc = fixture()
        self.assertEqual(validate_forecast(fc), [])
        for mutate in [lambda x: x["daily"][0].update(diveable=True),
                       lambda x: x["daily"][0].update(best_point="A_point"),
                       lambda x: x["daily"][0].update(best_point_evidence={"stats_key": "kame_ne"}),
                       lambda x: x["hourly"].pop(),
                       lambda x: x["hourly"][0].update(wave_height=None)]:
            bad = copy.deepcopy(fc)
            mutate(bad)
            self.assertTrue(validate_forecast(bad))

    def test_archive_immutable_and_lead_time(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            fc = fixture()
            p = save_snapshot(fc, {}, tmp, published_at=fc["issued_at"])
            self.assertEqual(save_snapshot(fc, {}, tmp, published_at=fc["issued_at"]), p)
            fc["daily"][0]["score"] = 99
            with self.assertRaises(ValueError): save_snapshot(fc, {}, tmp, published_at=fc["issued_at"])
            with patch('forecast_archive.today_jst', return_value=date(2026, 10, 3)):
                result = build_issued_history({"2026-10-02": {"hammer_seen": True, "hammer_size": "small", "visibility_hi": 15}}, tmp)
            self.assertEqual(result["days"][0]["lead_days"], 1)
            self.assertEqual(result["by_lead"]["1"]["n_seen_days"], 1)

    def test_archive_excludes_observed_and_late_same_day(self):
        fc = fixture()
        fc["issued_at"] = "2026-10-02T12:00:00+09:00"
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            p = save_snapshot(fc, {}, tmp, published_at=fc["issued_at"])
            self.assertEqual(domain.read_json(p)["days"], [])

    def test_archive_uses_publication_time_not_generation_time(self):
        fc = fixture()
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            p = save_snapshot(fc, {}, tmp, published_at="2026-10-02T07:30:00+09:00")
            saved = domain.read_json(p)
            self.assertEqual(saved["days"][0]["lead_days"], 0)
            self.assertEqual(saved["generated_at"], fc["issued_at"])
            with self.assertRaises(ValueError):
                save_snapshot(fc, {}, tmp, published_at="2026-09-30T07:30:00+09:00")

    def test_corrupt_existing_logs_are_preserved(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root = Path(tmp)
            p = root / "dive_logs_structured_full.json"
            p.write_text('{broken', encoding='utf-8')
            with patch.object(structure_full, 'ROOT', root):
                with self.assertRaises(RuntimeError): structure_full.structure()
            self.assertEqual(p.read_text(), '{broken')

    def test_exact_point_attribution_only(self):
        self.assertEqual(structure_full.sighting_points("カメ根でハンマーを観察しました。"), ["kame_ne"])
        self.assertEqual(structure_full.sighting_points("カメ根、ジャブ根でハンマーを観察しました。"), [])
        self.assertEqual(structure_full.sighting_points("カメ根でハンマーは見られず。"), [])

    def test_retry_respects_backoff_and_rate_limit(self):
        self.assertGreaterEqual(SLEEP, 2)
        self.assertFalse(retry_due("u", {"u": 100}, 99))
        self.assertTrue(retry_due("u", {"u": 100}, 100))

    def test_probability_fit_bounds(self):
        f = fit_probability([[-1], [0], [1], [2]], [0, 0, 1, 1])
        self.assertLess(f([-1]), f([2]))
        self.assertTrue(0 < f([0]) < 1)


if __name__ == '__main__':
    unittest.main()
