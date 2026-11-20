"""Collection evidence distinguishes quiet blogs from failed acquisitions."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
import full_scraper as scraper

URLS = {"hammers": "https://www.mikomoto.com/divelog/100/",
        "ms": "https://mikomotodivers.com/logs/2026/11/01120000.html",
        "290": "https://www.290.jp/archives/100"}
COLLECTORS = {"hammers": "collect_hammers_urls", "ms": "collect_ms_urls", "290": "collect_290_urls"}


class ScrapeHealthTests(unittest.TestCase):
    def sandbox(self, root, stack, *, cached=False):
        for name, filename in [("ROOT", None), ("STATE_F", "scrape_state.json"),
                               ("JSONL_F", "articles.jsonl"), ("OUT_F", "dive_logs_raw_full.json"),
                               ("HEALTH_F", "scrape_health.json")]:
            stack.enter_context(patch.object(scraper, name, root / filename if filename else root))
        state = {"done_urls": list(URLS.values()), "failed_urls": [],
                 "url_lists": {shop: [url] for shop, url in URLS.items()} if cached else {},
                 "retry_after": {}, "retry_count": {}}
        (root / "scrape_state.json").write_text(json.dumps(state), encoding="utf-8")
        (root / "dive_logs_structured_full.json").write_text(json.dumps({"logs": [
            {"shop": shop, "url": url, "date": "2026-11-01"} for shop, url in URLS.items()]}), encoding="utf-8")
        collectors = {shop: stack.enter_context(patch.object(scraper, name, return_value=[URLS[shop]]))
                      for shop, name in COLLECTORS.items()}
        stack.enter_context(redirect_stdout(io.StringIO()))
        return collectors

    def test_old_reports_with_successful_listing_checks_are_not_acquisition_failures(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            collectors = self.sandbox(root, stack)
            fetch = stack.enter_context(patch.object(scraper, "get"))
            self.assertEqual(scraper.main([]), 0)
            health = json.loads((root / "scrape_health.json").read_text())
            self.assertEqual(health["schema_version"], 1)
            for shop, row in health["sources"].items():
                self.assertEqual(row["status"], "ok")
                self.assertEqual(row["listing_status"], "ok")
                self.assertEqual(row["latest_article_date"], "2026-11-01")
                self.assertEqual(row["new_url_count"], 0)
                self.assertTrue(row["checked_at"].endswith("+09:00"))
                self.assertNotIn("hammer_seen", row)
                self.assertNotIn("departure", row)
                collectors[shop].assert_called_once_with(known=set(URLS.values()))
            fetch.assert_not_called()

    def test_cached_listing_cannot_claim_a_new_source_check(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            collectors = self.sandbox(root, stack, cached=True)
            self.assertEqual(scraper.main([]), 1)
            health = json.loads((root / "scrape_health.json").read_text())
            for row in health["sources"].values():
                self.assertEqual(row["status"], "not_checked")
                self.assertIsNone(row["checked_at"])
            for collector in collectors.values():
                collector.assert_not_called()

    def test_refresh_preserves_incremental_history_and_retry_backoff(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            self.sandbox(root, stack, cached=True)
            state = json.loads((root / "scrape_state.json").read_text())
            failed = "https://www.290.jp/archives/99"
            state.update(failed_urls=[failed], retry_after={failed: 10 ** 12}, retry_count={failed: 2})
            (root / "scrape_state.json").write_text(json.dumps(state))
            fetch = stack.enter_context(patch.object(scraper, "get"))
            self.assertEqual(scraper.main(["--refresh-lists"]), 0)
            updated = json.loads((root / "scrape_state.json").read_text())
            self.assertEqual(set(updated["done_urls"]), set(URLS.values()))
            self.assertEqual(updated["retry_after"], {failed: 10 ** 12})
            self.assertEqual(updated["retry_count"], {failed: 2})
            fetch.assert_not_called()

    def test_failed_or_empty_listing_never_becomes_healthy(self):
        for failure in [[], scraper.requests.HTTPError("HTTP 503")]:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                root = Path(directory)
                collectors = self.sandbox(root, stack)
                if isinstance(failure, Exception):
                    collectors["hammers"].side_effect = failure
                else:
                    collectors["hammers"].return_value = failure
                self.assertEqual(scraper.main([]), 1)
                health = json.loads((root / "scrape_health.json").read_text())
                self.assertEqual(health["sources"]["hammers"]["status"], "error")
                self.assertEqual(health["sources"]["hammers"]["listing_status"], "error")
                self.assertEqual(health["sources"]["290"]["status"], "ok")

    def test_rediscovered_failure_in_backoff_remains_partial_without_retrying(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            collectors = self.sandbox(root, stack)
            failed = "https://www.290.jp/archives/101"
            collectors["290"].return_value = [failed, URLS["290"]]
            path = root / "scrape_state.json"
            state = json.loads(path.read_text())
            state.update(failed_urls=[failed], retry_after={failed: 10 ** 12}, retry_count={failed: 2})
            path.write_text(json.dumps(state))
            fetch = stack.enter_context(patch.object(scraper, "get"))
            self.assertEqual(scraper.main([]), 1)
            health = json.loads((root / "scrape_health.json").read_text())
            self.assertEqual(health["sources"]["290"]["status"], "partial")
            self.assertEqual(health["sources"]["290"]["unresolved_url_count"], 1)
            fetch.assert_not_called()

    def test_listing_rollback_is_visible_even_when_every_discovered_url_parses(self):
        row = {"listing_status": "ok", "article_failed": 0}
        scraper.finish_source_health(row, ["older"], {"older", "newer"},
                                     {"older": "2026-10-31", "newer": "2026-11-01"}, "2026-11-01")
        self.assertEqual(row["unresolved_url_count"], 0)
        self.assertEqual(row["latest_discovered_article_date"], "2026-10-31")
        self.assertTrue(row["listing_regressed"])
        self.assertEqual(row["status"], "partial")

    def test_invalid_article_date_is_not_marked_done_and_successful_sources_continue(self):
        for article_date in [None, "invalid", "2099-01-01"]:
            with self.subTest(article_date=article_date), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                root = Path(directory)
                collectors = self.sandbox(root, stack)
                new_url = "https://www.mikomoto.com/divelog/101/"
                collectors["hammers"].return_value = [new_url, URLS["hammers"]]
                stack.enter_context(patch.object(scraper, "get", return_value=Mock(text="fixture")))
                stack.enter_context(patch.dict(scraper.PARSERS, {"hammers": lambda url, html: {
                    "url": url, "shop": "hammers", "date": article_date, "body": "test"}}))
                self.assertEqual(scraper.main([]), 1)
                updated = json.loads((root / "scrape_state.json").read_text())
                self.assertNotIn(new_url, updated["done_urls"])
                self.assertIn(new_url, updated["failed_urls"])
                self.assertFalse((root / "articles.jsonl").exists())
                health = json.loads((root / "scrape_health.json").read_text())
                self.assertEqual(health["sources"]["hammers"]["status"], "partial")
                self.assertEqual(health["sources"]["hammers"]["article_failed"], 1)

    def test_future_date_and_empty_body_are_rejected(self):
        with self.assertRaises(ValueError):
            scraper.validate_article({"date": "2026-11-22", "body": "test"}, today=date(2026, 11, 21))
        with self.assertRaises(ValueError):
            scraper.validate_article({"date": "2026-11-21", "body": ""}, today=date(2026, 11, 21))

    def test_real_listing_parser_error_propagates_instead_of_looking_like_no_news(self):
        for collector in [scraper.collect_hammers_urls, scraper.collect_290_urls, scraper.collect_ms_urls]:
            with self.subTest(collector=collector.__name__), patch.object(scraper, "get", side_effect=scraper.requests.HTTPError("HTTP 503")):
                with redirect_stdout(io.StringIO()), self.assertRaises(scraper.requests.HTTPError):
                    collector(known=set(URLS.values()))

    def test_global_request_spacing_is_at_least_two_seconds(self):
        clock = [100.0]
        starts = []
        def fetch(*args, **kwargs):
            starts.append(clock[0])
            return Mock(status_code=200)
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(scraper, "_last_req", [0.0]), patch.object(scraper.time, "time", side_effect=lambda: clock[0]), \
             patch.object(scraper.time, "sleep", side_effect=sleep), patch.object(scraper.session, "get", side_effect=fetch):
            scraper.get("https://example.invalid/one")
            scraper.get("https://example.invalid/two")
            scraper.get("https://example.invalid/three")
        self.assertTrue(all(b - a >= 2 for a, b in zip(starts, starts[1:])))


if __name__ == "__main__":
    unittest.main()
