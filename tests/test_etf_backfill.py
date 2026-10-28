import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.ingest import ingest_etf_shares
from data_source.dp_cycle.national_team_etf import (
    _merge_history, backfill_core_etf_month_ends, fetch_sse_etf_scale, snapshot_core_etfs,
)
from data_source.dp_cycle.store import init_schema


SSE_CODES = ("510300", "510310", "510330")


def cached_rows(day, codes=SSE_CODES):
    return pd.DataFrame([{"date": day, "code": c, "name": c, "基金份额": 100000000,
                          "份额_亿份": 1.0} for c in codes])


def sse_table(day, codes=SSE_CODES):
    return pd.DataFrame([{"统计日期": day, "基金代码": c, "基金份额": 200000000} for c in codes])


def calendar():
    dates = pd.bdate_range("2026-09-01", "2026-09-30")
    return pd.DataFrame({"date": dates}), {"ok": True, "start_date": "2026-09-01", "end_date": "2026-09-30"}


class ETFBackfillTests(unittest.TestCase):
    def test_exact_month_end_source_failure_is_not_retried_on_earlier_dates(self):
        with patch("data_source.dp_cycle.national_team_etf.call_with_retry", side_effect=RuntimeError("offline")) as call:
            frame, meta = fetch_sse_etf_scale(date(2026, 9, 30), exact_date=True)
        self.assertTrue(frame.empty)
        self.assertFalse(meta["ok"])
        self.assertIn("offline", meta["error"])
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs["date"], "20260930")
        self.assertEqual(call.call_args.kwargs["retries"], 1)

    def test_daily_snapshot_keeps_backward_date_fallback_by_default(self):
        with patch("data_source.dp_cycle.national_team_etf.call_with_retry",
                   side_effect=[pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), sse_table("2026-10-23")]) as call:
            frame, meta = fetch_sse_etf_scale(date(2026, 10, 26))
        self.assertFalse(frame.empty)
        self.assertTrue(meta["ok"])
        self.assertEqual(meta["date"], "2026-10-23")
        self.assertEqual(call.call_count, 4)

    def test_merge_updates_both_units_from_a_single_new_value(self):
        old = cached_rows("2026-09-30", ("510300",))
        new = old.copy()
        new["基金份额"] = None
        new["份额_亿份"] = 2.0
        merged = _merge_history(old, new)
        self.assertEqual((merged.iloc[0]["基金份额"], merged.iloc[0]["份额_亿份"]), (200000000, 2.0))

        new["份额_亿份"] = 0.0
        merged = _merge_history(old, new)
        self.assertEqual((merged.iloc[0]["基金份额"], merged.iloc[0]["份额_亿份"]), (0, 0))

    def test_snapshot_failure_keeps_cache_but_is_not_reported_as_refresh_success(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "data_source.dp_cycle.national_team_etf._store_dir", return_value=tmp
        ), patch("data_source.dp_cycle.national_team_etf.fetch_sse_etf_scale",
                 return_value=(pd.DataFrame(), {"ok": False, "error": "offline"})):
            cached_rows("2026-10-23").to_csv(Path(tmp, "core_shares_daily.csv"), index=False)
            hist, meta = snapshot_core_etfs(as_of=date(2026, 10, 27))
            self.assertEqual(len(hist), 3)
            self.assertFalse(meta["ok"])
            self.assertTrue(meta["from_cache"])

    def test_snapshot_updates_same_fund_without_duplicate_rows(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "data_source.dp_cycle.national_team_etf._store_dir", return_value=tmp
        ), patch("data_source.dp_cycle.national_team_etf.fetch_sse_etf_scale",
                 return_value=(sse_table("2026-10-26"), {"ok": True, "date": "2026-10-26"})):
            cached_rows("2026-10-26").to_csv(Path(tmp, "core_shares_daily.csv"), index=False)
            hist, meta = snapshot_core_etfs(as_of=date(2026, 10, 27))
            self.assertEqual(len(hist), 3)
            self.assertEqual(set(hist["份额_亿份"]), {2.0})
            self.assertTrue(meta["partial"])
            self.assertEqual(meta["missing_codes"], ["159919"])

    def test_snapshot_does_not_relabel_a_different_source_observation_day(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "data_source.dp_cycle.national_team_etf._store_dir", return_value=tmp
        ), patch("data_source.dp_cycle.national_team_etf.fetch_sse_etf_scale",
                 return_value=(sse_table("2026-10-23"), {"ok": True, "date": "2026-10-26"})):
            hist, meta = snapshot_core_etfs(as_of=date(2026, 10, 27))
            self.assertTrue(hist.empty)
            self.assertFalse(meta["ok"])

    def test_midmonth_observation_does_not_satisfy_month_end(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "data_source.dp_cycle.national_team_etf._store_dir", return_value=tmp
        ), patch("data_source.dp_cycle.trade_calendar.load_trade_calendar", return_value=calendar()), patch(
            "data_source.dp_cycle.national_team_etf.fetch_sse_etf_scale",
            return_value=(sse_table("2026-09-30"), {"ok": True, "date": "2026-09-30"}),
        ) as fetch:
            cached_rows("2026-09-15").to_csv(Path(tmp, "core_shares_daily.csv"), index=False)
            hist, meta = backfill_core_etf_month_ends(months=1, as_of=date(2026, 10, 27))
            fetch.assert_called_once_with(date(2026, 9, 30), exact_date=True)
            self.assertEqual(set(hist["date"].dt.strftime("%Y-%m-%d")), {"2026-09-15", "2026-09-30"})
            self.assertEqual(len(hist), 6)
            self.assertTrue(meta["partial"])
            self.assertFalse(meta["ok"])
            self.assertEqual(meta["missing_targets"][0]["missing_codes"], ["159919"])

    def test_month_end_missing_funds_is_retried_but_available_rows_remain(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "data_source.dp_cycle.national_team_etf._store_dir", return_value=tmp
        ), patch("data_source.dp_cycle.trade_calendar.load_trade_calendar", return_value=calendar()), patch(
            "data_source.dp_cycle.national_team_etf.fetch_sse_etf_scale",
            return_value=(sse_table("2026-09-30"), {"ok": True, "date": "2026-09-30"}),
        ) as fetch:
            cached_rows("2026-09-30", ("510300",)).to_csv(Path(tmp, "core_shares_daily.csv"), index=False)
            hist, meta = backfill_core_etf_month_ends(months=1, as_of=date(2026, 10, 27))
            fetch.assert_called_once()
            self.assertEqual(set(hist["code"]), set(SSE_CODES))
            self.assertEqual(len(hist), 3)
            self.assertEqual(meta["missing_targets"][0]["missing_codes"], ["159919"])

    def test_complete_sse_month_is_not_refetched_for_shenzhen_only(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "data_source.dp_cycle.national_team_etf._store_dir", return_value=tmp
        ), patch("data_source.dp_cycle.trade_calendar.load_trade_calendar", return_value=calendar()), patch(
            "data_source.dp_cycle.national_team_etf.fetch_sse_etf_scale"
        ) as fetch:
            cached_rows("2026-09-30").to_csv(Path(tmp, "core_shares_daily.csv"), index=False)
            _, meta = backfill_core_etf_month_ends(months=1, as_of=date(2026, 10, 27))
            fetch.assert_not_called()
            self.assertTrue(meta["partial"])
            self.assertEqual(meta["missing_targets"][0]["missing_codes"], ["159919"])

    def test_month_end_with_all_four_funds_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "data_source.dp_cycle.national_team_etf._store_dir", return_value=tmp
        ), patch("data_source.dp_cycle.trade_calendar.load_trade_calendar", return_value=calendar()), patch(
            "data_source.dp_cycle.national_team_etf.fetch_sse_etf_scale"
        ) as fetch:
            cached_rows("2026-09-30", SSE_CODES + ("159919",)).to_csv(Path(tmp, "core_shares_daily.csv"), index=False)
            _, meta = backfill_core_etf_month_ends(months=1, as_of=date(2026, 10, 27))
            fetch.assert_not_called()
            self.assertTrue(meta["ok"])
            self.assertFalse(meta["partial"])

    def test_weekend_month_end_uses_last_trading_day(self):
        cal = (pd.DataFrame({"date": pd.bdate_range("2025-08-01", "2025-09-01")}),
               {"ok": True, "start_date": "2025-08-01", "end_date": "2025-09-01"})
        with tempfile.TemporaryDirectory() as tmp, patch(
            "data_source.dp_cycle.national_team_etf._store_dir", return_value=tmp
        ), patch("data_source.dp_cycle.trade_calendar.load_trade_calendar", return_value=cal), patch(
            "data_source.dp_cycle.national_team_etf.fetch_sse_etf_scale",
            return_value=(sse_table("2025-08-29"), {"ok": True, "date": "2025-08-29"}),
        ) as fetch:
            hist, meta = backfill_core_etf_month_ends(months=1, as_of=date(2025, 9, 5))
            fetch.assert_called_once_with(date(2025, 8, 29), exact_date=True)
            self.assertEqual(set(hist["date"].dt.strftime("%Y-%m-%d")), {"2025-08-29"})
            self.assertEqual(meta["missing_targets"][0]["last_trading_day"], "2025-08-29")

    def test_earlier_source_date_is_not_claimed_as_month_end(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "data_source.dp_cycle.national_team_etf._store_dir", return_value=tmp
        ), patch("data_source.dp_cycle.trade_calendar.load_trade_calendar", return_value=calendar()), patch(
            "data_source.dp_cycle.national_team_etf.fetch_sse_etf_scale",
            return_value=(sse_table("2026-09-29"), {"ok": True, "date": "2026-09-29"}),
        ):
            hist, meta = backfill_core_etf_month_ends(months=1, as_of=date(2026, 10, 27))
            self.assertTrue(hist.empty)
            self.assertFalse(meta["ok"])
            self.assertEqual(meta["hit"], 0)
            self.assertEqual(meta["source_failures"][0]["month"], "2026-09")
            self.assertEqual(meta["source_failures"][0]["returned_date"], "2026-09-29")

    def test_failed_month_end_source_reason_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "data_source.dp_cycle.national_team_etf._store_dir", return_value=tmp
        ), patch("data_source.dp_cycle.trade_calendar.load_trade_calendar", return_value=calendar()), patch(
            "data_source.dp_cycle.national_team_etf.fetch_sse_etf_scale",
            return_value=(pd.DataFrame(), {"ok": False, "error": "源请求失败"}),
        ):
            _, meta = backfill_core_etf_month_ends(months=1, as_of=date(2026, 10, 27))
            self.assertEqual(meta["source_failures"][0]["month"], "2026-09")
            self.assertEqual(meta["source_failures"][0]["error"], "源请求失败")

    def test_reported_rows_must_match_the_actual_month_end(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "data_source.dp_cycle.national_team_etf._store_dir", return_value=tmp
        ), patch("data_source.dp_cycle.trade_calendar.load_trade_calendar", return_value=calendar()), patch(
            "data_source.dp_cycle.national_team_etf.fetch_sse_etf_scale",
            return_value=(sse_table("2026-09-29"), {"ok": True, "date": "2026-09-30"}),
        ):
            hist, meta = backfill_core_etf_month_ends(months=1, as_of=date(2026, 10, 27))
            self.assertTrue(hist.empty)
            self.assertFalse(meta["ok"])

    def test_calendar_unavailable_keeps_old_cache_and_marks_unknown(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "data_source.dp_cycle.national_team_etf._store_dir", return_value=tmp
        ), patch("data_source.dp_cycle.trade_calendar.load_trade_calendar", return_value=(pd.DataFrame(), {"ok": False})), patch(
            "data_source.dp_cycle.national_team_etf.fetch_sse_etf_scale"
        ) as fetch:
            cached_rows("2026-09-15").to_csv(Path(tmp, "core_shares_daily.csv"), index=False)
            hist, meta = backfill_core_etf_month_ends(months=1, as_of=date(2026, 10, 27))
            fetch.assert_not_called()
            self.assertEqual(len(hist), 3)
            self.assertTrue(meta["partial"])
            self.assertFalse(meta["ok"])
            self.assertEqual(meta["unknown_calendar_months"], ["2026-09"])

    def test_ingest_keeps_both_current_snapshot_and_returned_backfill(self):
        current = cached_rows("2026-10-26", ("510300",))
        previous = cached_rows("2026-09-30", ("510310",))
        with tempfile.TemporaryDirectory() as tmp, patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
            "data_source.dp_cycle.national_team_etf.snapshot_core_etfs", return_value=(current, {"ok": True})
        ), patch("data_source.dp_cycle.national_team_etf.backfill_core_etf_month_ends",
                 return_value=(previous, {"ok": False, "partial": True})):
            con = duckdb.connect(":memory:")
            init_schema(con)
            meta = ingest_etf_shares(con, as_of=date(2026, 10, 27), backfill_months=1)
            rows = con.execute("SELECT date, code FROM etf_shares_daily ORDER BY date").fetchall()
            con.close()
        self.assertEqual([str(day) for day, _ in rows], ["2026-09-30", "2026-10-26"])
        self.assertTrue(meta["partial"])
        self.assertFalse(meta["ok"])

    def test_fresh_backfill_overrides_older_snapshot_values_for_same_day(self):
        current = cached_rows("2026-09-30", ("510300",))
        previous = cached_rows("2026-09-30", ("510300",))
        previous["基金份额"] = 200000000
        previous["份额_亿份"] = 2.0
        with tempfile.TemporaryDirectory() as tmp, patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
            "data_source.dp_cycle.national_team_etf.snapshot_core_etfs", return_value=(current, {"ok": True})
        ), patch("data_source.dp_cycle.national_team_etf.backfill_core_etf_month_ends",
                 return_value=(previous, {"ok": False, "partial": True})):
            con = duckdb.connect(":memory:")
            init_schema(con)
            ingest_etf_shares(con, as_of=date(2026, 10, 27), backfill_months=1)
            shares = con.execute("SELECT shares_yi FROM etf_shares_daily").fetchone()[0]
            con.close()
        self.assertEqual(shares, 2.0)

    def test_snapshot_single_unit_reconstructs_same_quantity(self):
        current = cached_rows("2026-10-26", ("510300",))
        current["基金份额"] = None
        with tempfile.TemporaryDirectory() as tmp, patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
            "data_source.dp_cycle.national_team_etf.snapshot_core_etfs", return_value=(current, {"ok": False, "partial": True})
        ):
            con = duckdb.connect(":memory:")
            init_schema(con)
            con.execute("INSERT INTO etf_shares_daily (date, code, name, shares, shares_yi) "
                        "VALUES ('2026-10-26', '510300', 'old', 200000000, 2)")
            ingest_etf_shares(con, as_of=date(2026, 10, 27), backfill_months=0)
            row = con.execute("SELECT shares, shares_yi FROM etf_shares_daily").fetchone()
            con.close()
        self.assertEqual(row, (100000000, 1.0))

    def test_missing_snapshot_amounts_do_not_erase_database_value(self):
        current = cached_rows("2026-10-26", ("510300",))
        current["基金份额"] = None
        current["份额_亿份"] = None
        with tempfile.TemporaryDirectory() as tmp, patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
            "data_source.dp_cycle.national_team_etf.snapshot_core_etfs", return_value=(current, {"ok": False, "partial": True})
        ):
            con = duckdb.connect(":memory:")
            init_schema(con)
            con.execute("INSERT INTO etf_shares_daily (date, code, name, shares, shares_yi) "
                        "VALUES ('2026-10-26', '510300', 'old', 200000000, 2)")
            ingest_etf_shares(con, as_of=date(2026, 10, 27), backfill_months=0)
            row = con.execute("SELECT shares, shares_yi FROM etf_shares_daily").fetchone()
            con.close()
        self.assertEqual(row, (200000000, 2.0))


if __name__ == "__main__":
    unittest.main()
