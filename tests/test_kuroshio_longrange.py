"""Calendar continuity and honest Kuroshio metadata; no network in regression tests."""
import copy
import html
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
import build_longrange
import fetch_kuroshio
from longrange_contract import kuroshio_state, validate_kuroshio, validate_longrange

NOW = datetime(2026, 11, 21, 12, tzinfo=timezone(timedelta(hours=9)))
TITLE = "2027年1月16日までの黒潮「長期」予測(2026年11月19日発表)"
URL = "https://www.jamstec.go.jp/aplinfo/kowatch/?p=16366"


def feed(entries=None):
    entries = entries or [(TITLE, URL)]
    return "<rss><channel>" + "".join(f"<item><title>{html.escape(title)}</title><link>{html.escape(link)}</link></item>" for title, link in entries) + "</channel></rss>"


def snapshot():
    return fetch_kuroshio.extract_snapshot(feed(), NOW)


class KuroshioContractTests(unittest.TestCase):
    def test_official_issue_coverage_and_retrieval_are_distinct_without_prose_classification(self):
        s = snapshot()
        self.assertEqual(s["issued_on"], "2026-11-19")
        self.assertEqual(s["valid_until"], "2027-01-16")
        self.assertEqual(s["retrieved_at"], NOW.isoformat(timespec="seconds"))
        self.assertEqual(s["source_url"], URL)
        self.assertEqual(s["source_status"], "available")
        self.assertEqual(s["status"], "unknown")
        self.assertIsNone(s["score"])
        self.assertEqual(validate_kuroshio(s), [])
        state = kuroshio_state(s, NOW.date(), NOW.date())
        self.assertFalse(state["known"])
        self.assertIsNone(state["score"])
        self.assertEqual(state["calculation_factor"], .5)
        self.assertEqual(state["reason"], "unassessed")
        self.assertNotIn("body", s)

    def test_latest_issue_is_selected_by_explicit_title_date_not_feed_order(self):
        older = "2027年1月9日までの黒潮「長期」予測(2026年11月12日発表)"
        s = fetch_kuroshio.extract_snapshot(feed([(older, URL.replace("16366", "16200")), (TITLE, URL)]), NOW)
        self.assertEqual(s["issued_on"], "2026-11-19")

    def test_malformed_titles_links_future_dates_and_ambiguity_do_not_invent_metadata(self):
        for payload in [
            feed([("黒潮「長期」予測(日付未記載)", URL)]),
            feed([(TITLE, "https://example.com/?p=16366")]),
            feed([(TITLE.replace("2026年11月19日発表", "2026年11月22日発表"), URL)]),
            feed([(TITLE, URL), (TITLE, URL.replace("16366", "16367"))]),
            "<rss><channel/></rss>",
        ]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                fetch_kuroshio.extract_snapshot(payload, NOW)

    def test_unknown_and_expired_are_publishable_but_structural_corruption_is_not(self):
        s = snapshot()
        later = date(2027, 1, 17)
        self.assertEqual(validate_kuroshio(s), [])
        state = kuroshio_state(s, later, later)
        self.assertEqual(state["reason"], "expired")
        self.assertIsNone(state["score"])
        for changes in [{"score": .5}, {"source_url": "javascript:alert(1)"}, {"source_url": []}, {"valid_until": "2027-1-16"}, {"retrieved_at": "2026-11-21T00:00:00"}, {"status": "nNLM", "score": 1.0}]:
            with self.subTest(changes=changes):
                self.assertTrue(validate_kuroshio({**s, **changes}))

    def test_reviewed_classification_is_target_date_limited_and_never_extended(self):
        s = snapshot()
        s.update(status="nNLM", score=1.0)
        s["assessment"] = {"status": "reviewed", "method": "manual"}
        self.assertTrue(kuroshio_state(s, date(2027, 1, 16), NOW.date())["known"])
        self.assertFalse(kuroshio_state(s, date(2027, 1, 17), NOW.date())["known"])
        self.assertFalse(kuroshio_state(s, date(2026, 11, 18), NOW.date())["known"])
        self.assertFalse(kuroshio_state(s, date(2026, 11, 21), date(2026, 11, 18))["known"])

    def test_failed_extraction_and_regressed_feed_leave_previous_file_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "kuroshio.json"
            fetch_kuroshio.atomic_write(output, snapshot())
            original = output.read_bytes()
            client = Mock()
            client.get.return_value.content = b"<rss><channel/></rss>"
            with self.assertRaises(ValueError):
                fetch_kuroshio.fetch(output, client)
            self.assertEqual(output.read_bytes(), original)
            older = "2027年1月9日までの黒潮「長期」予測(2026年11月12日発表)"
            client.get.return_value.content = feed([(older, URL.replace("16366", "16200"))]).encode()
            with patch.object(fetch_kuroshio, "now_jst", return_value=NOW), self.assertRaises(ValueError):
                fetch_kuroshio.fetch(output, client)
            self.assertEqual(output.read_bytes(), original)


class LongrangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixed_source = snapshot()
        with patch.object(build_longrange, "now_jst", return_value=NOW), patch.object(build_longrange, "kuroshio_state", side_effect=lambda _source, target, as_of: kuroshio_state(fixed_source, target, as_of)):
            cls.base = build_longrange.build_data(NOW.date())
        # Use a controlled unassessed source regardless of the live repository snapshot.
        cls.base["kuroshio"] = snapshot()
        for row in cls.base["days"]:
            state = kuroshio_state(cls.base["kuroshio"], date.fromisoformat(row["date"]), NOW.date())
            row.update(kuroshio_known=False, f_kuroshio=None, kuroshio_state=state["reason"])

    def test_exact_183_day_coverage_and_unchanged_neutral_score_definition(self):
        data = self.base
        self.assertEqual(validate_longrange(data, NOW), [])
        self.assertEqual(len(data["days"]), 183)
        self.assertEqual(data["days"][0]["date"], "2026-11-21")
        self.assertEqual(data["days"][-1]["date"], "2027-05-22")
        self.assertNotIn("MAE", data["tide_note"])
        for row in data["days"]:
            expected = 100 * (.35 * row["f_month"] + .25 * row["f_ebb"] + .2 * .5 + .2 * row["f_moon"])
            self.assertAlmostEqual(row["aim_score"], expected, delta=.11)
            self.assertIsNone(row["f_kuroshio"])

    def test_publication_rejects_gap_duplicate_staleness_or_old_kuroshio_bonus(self):
        mutations = [
            lambda d: d["days"].pop(),
            lambda d: d["days"].__setitem__(1, copy.deepcopy(d["days"][0])),
            lambda d: d.update(generated_at="2026-11-20T23:59:00+09:00"),
            lambda d: d.update(generated_at="2026-11-21T12:06:00+09:00"),
            lambda d: d["coverage"].update(complete=False),
            lambda d: d["days"][0].update(f_kuroshio=1, kuroshio_known=True),
            lambda d: d["kuroshio"].update(score=.5),
        ]
        for mutate in mutations:
            data = copy.deepcopy(self.base)
            mutate(data)
            self.assertTrue(validate_longrange(data, NOW))

    def test_missing_moon_day_fails_before_replacing_existing_calendar(self):
        moon = json.loads((build_longrange.PH1 / "moon_tide_calendar.json").read_text(encoding="utf-8"))
        del moon["calendar"]["2027-05-22"]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "moon_tide_calendar.json").write_text(json.dumps(moon), encoding="utf-8")
            output = folder / "longrange.json"
            output.write_text("previous snapshot", encoding="utf-8")
            with patch.object(build_longrange, "PH1", folder), patch.object(build_longrange, "OUT", output), patch.object(build_longrange, "today_jst", return_value=NOW.date()), self.assertRaises(ValueError):
                build_longrange.main()
            self.assertEqual(output.read_text(encoding="utf-8"), "previous snapshot")


if __name__ == "__main__":
    unittest.main()
