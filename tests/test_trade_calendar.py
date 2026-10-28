import importlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import akshare as ak
import pandas as pd
from pandas.testing import assert_frame_equal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TradeCalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / "trade_calendar.csv"
        self.paths = patch("output_paths.dp_cycle_store_dir", return_value=self.temp.name)
        self.paths.start()
        self.addCleanup(self.paths.stop)
        # Avoid changing the test process's requests settings or touching the network.
        self.http = patch("data_source.dp_cycle._http.ensure_http")
        self.http.start()
        self.addCleanup(self.http.stop)

    def load(self, *, refresh=False):
        name = "data_source.dp_cycle.trade_calendar"
        self.assertIsNotNone(importlib.util.find_spec(name), "trade calendar loader is missing")
        return importlib.import_module(name).load_trade_calendar(refresh=refresh)

    def test_refresh_saves_exact_dates_and_offline_read_restores_them(self):
        dates = ["2026-10-12", "2026-09-30", "2026-10-09", "2026-10-12"]
        with patch.object(ak, "tool_trade_date_hist_sina", return_value=pd.DataFrame({"trade_date": dates})):
            actual, meta = self.load(refresh=True)

        expected = pd.DataFrame({"date": pd.to_datetime(["2026-09-30", "2026-10-09", "2026-10-12"])})
        assert_frame_equal(actual, expected)
        self.assertEqual(pd.read_csv(self.cache)["date"].tolist(), ["2026-09-30", "2026-10-09", "2026-10-12"])
        self.assertEqual((meta["ok"], meta["status"], meta["refresh_status"]), (True, "updated", "updated"))
        self.assertEqual(meta["source"], "akshare.tool_trade_date_hist_sina")
        self.assertEqual(meta["origin"], "akshare.tool_trade_date_hist_sina")
        self.assertEqual((meta["start_date"], meta["end_date"]), ("2026-09-30", "2026-10-12"))

        with patch.object(ak, "tool_trade_date_hist_sina", side_effect=AssertionError("offline read called network")) as fetch:
            cached, cache_meta = self.load()
        fetch.assert_not_called()
        assert_frame_equal(cached, expected)
        self.assertEqual((cache_meta["ok"], cache_meta["status"], cache_meta["refresh_status"]), (True, "cached", "not_requested"))
        self.assertEqual(cache_meta["source"], "cache")
        self.assertEqual(cache_meta["origin"], "akshare.tool_trade_date_hist_sina")
        self.assertEqual((cache_meta["start_date"], cache_meta["end_date"]), ("2026-09-30", "2026-10-12"))

    def test_failed_refresh_uses_valid_cache(self):
        original = "date\n2026-10-09\n2026-10-12\n"
        self.cache.write_text(original, encoding="utf-8")
        with patch.object(ak, "tool_trade_date_hist_sina", side_effect=RuntimeError("unavailable")):
            actual, meta = self.load(refresh=True)
        self.assertEqual(actual["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-10-09", "2026-10-12"])
        self.assertEqual((meta["ok"], meta["status"], meta["refresh_status"]), (True, "cached", "failed"))
        self.assertEqual(meta["source"], "cache")
        self.assertTrue(meta["error"])
        self.assertEqual(self.cache.read_text(encoding="utf-8"), original)

    def test_calendar_origin_survives_offline_reload(self):
        response = pd.DataFrame({"trade_date": ["2026-10-09", "2026-10-12"]})
        with patch.object(ak, "tool_trade_date_hist_sina", return_value=response):
            self.load(refresh=True)
        _, metadata = self.load()
        self.assertEqual(metadata["origin"], "akshare.tool_trade_date_hist_sina")

    def test_empty_or_invalid_refresh_does_not_replace_cache(self):
        original = "date\n2026-10-09\n2026-10-12\n"
        self.cache.write_text(original, encoding="utf-8")
        for response in (
            pd.DataFrame({"trade_date": []}),
            pd.DataFrame({"trade_date": ["2026-10-09", "not-a-date"]}),
            pd.DataFrame({"trade_date": [1]}),
            pd.DataFrame({"wrong_column": ["2026-10-09"]}),
        ):
            with self.subTest(response=response.to_dict()), patch.object(ak, "tool_trade_date_hist_sina", return_value=response):
                actual, meta = self.load(refresh=True)
                self.assertEqual(actual["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-10-09", "2026-10-12"])
                self.assertEqual((meta["status"], meta["refresh_status"]), ("cached", "failed"))
                self.assertEqual(self.cache.read_text(encoding="utf-8"), original)

    def test_refresh_with_shrunken_coverage_preserves_existing_history(self):
        original = "date\n2025-12-31\n2026-10-09\n2026-12-31\n"
        self.cache.write_text(original, encoding="utf-8")
        for dates in (
            ["2026-10-09", "2026-10-12"],
            ["2026-10-09", "2026-12-31"],
            ["2025-12-31", "2026-10-12"],
        ):
            with self.subTest(dates=dates), patch.object(ak, "tool_trade_date_hist_sina", return_value=pd.DataFrame({"trade_date": dates})):
                actual, meta = self.load(refresh=True)
                self.assertEqual(actual["date"].dt.strftime("%Y-%m-%d").tolist(), ["2025-12-31", "2026-10-09", "2026-12-31"])
                self.assertEqual((meta["status"], meta["refresh_status"]), ("cached", "failed"))
                self.assertEqual(meta["error"], "refresh failed: coverage shrank")
                self.assertEqual((meta["start_date"], meta["end_date"]), ("2025-12-31", "2026-12-31"))
                self.assertEqual(self.cache.read_text(encoding="utf-8"), original)

    def test_refresh_can_extend_cached_coverage(self):
        self.cache.write_text("date\n2026-10-09\n2026-10-12\n", encoding="utf-8")
        response = pd.DataFrame({"trade_date": pd.to_datetime(["2025-12-31", "2026-10-09", "2026-10-12", "2026-12-31"])})
        with patch.object(ak, "tool_trade_date_hist_sina", return_value=response):
            actual, meta = self.load(refresh=True)
        self.assertEqual(actual["date"].dt.strftime("%Y-%m-%d").tolist(), ["2025-12-31", "2026-10-09", "2026-10-12", "2026-12-31"])
        self.assertEqual((meta["status"], meta["refresh_status"]), ("updated", "updated"))
        self.assertEqual((meta["start_date"], meta["end_date"]), ("2025-12-31", "2026-12-31"))

    def test_missing_or_corrupt_cache_is_not_reported_as_success(self):
        for content in (None, "date\n", "date\n1\n", "date\n2026-10-09\nnot-a-date\n", "wrong\n2026-10-09\n"):
            with self.subTest(content=content):
                if content is not None:
                    self.cache.write_text(content, encoding="utf-8")
                with patch.object(ak, "tool_trade_date_hist_sina", side_effect=AssertionError("network called")) as fetch:
                    actual, meta = self.load()
                fetch.assert_not_called()
                self.assertEqual(list(actual.columns), ["date"])
                self.assertTrue(actual.empty)
                self.assertEqual((meta["ok"], meta["status"], meta["refresh_status"]), (False, "failed", "not_requested"))
                self.assertIsNone(meta["source"])
                self.assertIsNone(meta["start_date"])
                self.assertIsNone(meta["end_date"])
                self.assertTrue(meta["error"])

    def test_failed_refresh_without_cache_is_reported_as_failed(self):
        with patch.object(ak, "tool_trade_date_hist_sina", side_effect=RuntimeError("unavailable")):
            actual, meta = self.load(refresh=True)
        self.assertTrue(actual.empty)
        self.assertEqual((meta["ok"], meta["status"], meta["refresh_status"]), (False, "failed", "failed"))
        self.assertFalse(self.cache.exists())

    def test_failed_atomic_replace_preserves_previous_cache(self):
        original = "date\n2026-10-09\n"
        self.cache.write_text(original, encoding="utf-8")
        response = pd.DataFrame({"trade_date": ["2026-10-12"]})
        with patch.object(ak, "tool_trade_date_hist_sina", return_value=response), patch("os.replace", side_effect=PermissionError("busy")):
            actual, meta = self.load(refresh=True)
        self.assertEqual(actual["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-10-09"])
        self.assertEqual((meta["status"], meta["refresh_status"]), ("cached", "failed"))
        self.assertEqual(self.cache.read_text(encoding="utf-8"), original)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [self.cache])


if __name__ == "__main__":
    unittest.main()
