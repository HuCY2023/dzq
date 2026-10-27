import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.etf_flow import estimate_core_etf_flows


CODES = ("510300", "510310", "510330", "159919")


def rows_for(day, previous_day):
    return pd.DataFrame([
        {"date": day, "code": code, "previous_date": previous_day,
         "shares": 100_000_000.0, "previous_shares": 100_000_000.0,
         "split_factor": 1.0, "nav": 4.0}
        for code in CODES
    ])


def calendar(dates):
    return {"dates": dates, "coverage_start": dates[0], "coverage_end": dates[-1],
            "source": "test_calendar", "coverage_verified": True}


class EtfFlowEstimateTests(unittest.TestCase):
    def test_mechanical_510310_consolidation_is_not_outflow(self):
        rows = rows_for("2024-09-20", "2024-09-19")
        row = rows["code"] == "510310"
        rows.loc[row, "split_factor"] = 0.49977589
        rows.loc[row, "shares"] = 49_977_589.0
        result = estimate_core_etf_flows(rows, trading_dates=calendar(["2024-09-19", "2024-09-20"]))
        day = result["daily"].iloc[0]
        self.assertTrue(day["complete"])
        self.assertEqual(day["comparable_count"], 4)
        self.assertAlmostEqual(day["flow_yi"], 0.0)
        item = result["components"].set_index("code").loc["510310"]
        self.assertEqual(item["status"], "ok")
        self.assertAlmostEqual(item["flow_yi"], 0.0)

    def test_mechanical_159919_split_is_not_inflow(self):
        rows = rows_for("2019-01-11", "2019-01-10")
        row = rows["code"] == "159919"
        rows.loc[row, "split_factor"] = 1.110680861
        rows.loc[row, "shares"] = 111_068_086.1
        result = estimate_core_etf_flows(rows, trading_dates=calendar(["2019-01-10", "2019-01-11"]))
        self.assertAlmostEqual(result["daily"].iloc[0]["flow_yi"], 0.0)

    def test_amount_uses_same_day_nav_and_shares_in_yuan(self):
        rows = rows_for("2026-10-26", "2026-10-23")
        rows.loc[rows["code"] == "510300", ["shares", "nav"]] = [110_000_000.0, 4.0]
        result = estimate_core_etf_flows(rows, trading_dates=calendar(["2026-10-23", "2026-10-26"]))
        self.assertAlmostEqual(result["daily"].iloc[0]["flow_yi"], 0.4)
        self.assertAlmostEqual(result["components"].set_index("code").loc["510300", "flow_yi"], 0.4)
        self.assertAlmostEqual(result["components"].set_index("code").loc["510330", "flow_yi"], 0.0)

    def test_missing_fund_does_not_turn_subset_into_total(self):
        rows = rows_for("2026-10-26", "2026-10-23")
        result = estimate_core_etf_flows(rows[rows["code"] != "159919"],
                                         trading_dates=calendar(["2026-10-23", "2026-10-26"]))
        day = result["daily"].iloc[0]
        self.assertFalse(day["complete"])
        self.assertEqual(day["comparable_count"], 3)
        self.assertTrue(pd.isna(day["flow_yi"]))
        self.assertEqual(result["components"].set_index("code").loc["159919", "status"], "missing_fund")

    def test_missing_nav_and_previous_shares_remain_missing(self):
        rows = rows_for("2026-10-26", "2026-10-23")
        rows.loc[rows["code"] == "510300", "nav"] = None
        rows.loc[rows["code"] == "510310", "previous_shares"] = None
        result = estimate_core_etf_flows(rows, trading_dates=calendar(["2026-10-23", "2026-10-26"]))
        items = result["components"].set_index("code")
        self.assertEqual(items.loc["510300", "status"], "missing_nav")
        self.assertEqual(items.loc["510310", "status"], "missing_previous_shares")
        self.assertTrue(pd.isna(items.loc["510300", "flow_yi"]))
        self.assertTrue(pd.isna(result["daily"].iloc[0]["flow_yi"]))

    def test_missing_month_cannot_be_treated_as_one_trading_day(self):
        rows = rows_for("2026-03-02", "2026-01-30")
        result = estimate_core_etf_flows(rows, trading_dates=calendar(["2026-01-30", "2026-02-27", "2026-03-02"]))
        self.assertTrue((result["components"]["status"] == "previous_date_not_consecutive").all())
        self.assertTrue(pd.isna(result["daily"].iloc[0]["flow_yi"]))

    def test_day_with_no_fund_rows_is_visible_as_missing(self):
        first = rows_for("2026-03-02", "2026-02-27")
        last = rows_for("2026-03-04", "2026-03-03")
        result = estimate_core_etf_flows(pd.concat([first, last], ignore_index=True),
            trading_dates=calendar(["2026-02-27", "2026-03-02", "2026-03-03", "2026-03-04"]))
        days = result["daily"].set_index("date")
        missing = days.loc[pd.Timestamp("2026-03-03")]
        self.assertEqual(missing["observed_count"], 0)
        self.assertEqual(missing["comparable_count"], 0)
        self.assertFalse(missing["complete"])
        self.assertTrue(pd.isna(missing["flow_yi"]))

    def test_latest_day_with_only_other_funds_is_still_visible(self):
        first = rows_for("2026-03-02", "2026-02-27")
        other = rows_for("2026-03-03", "2026-03-02").iloc[[0]].copy()
        other["code"] = "510500"
        result = estimate_core_etf_flows(pd.concat([first, other], ignore_index=True),
            trading_dates=calendar(["2026-02-27", "2026-03-02", "2026-03-03"]))
        days = result["daily"].set_index("date")
        latest = days.loc[pd.Timestamp("2026-03-03")]
        self.assertEqual(latest["observed_count"], 0)
        self.assertFalse(latest["complete"])
        self.assertTrue(pd.isna(latest["flow_yi"]))

    def test_missing_calendar_does_not_prove_consecutive_trading_days(self):
        result = estimate_core_etf_flows(rows_for("2026-10-26", "2026-10-23"))
        self.assertTrue((result["components"]["status"] == "calendar_unavailable").all())
        self.assertTrue(pd.isna(result["daily"].iloc[0]["flow_yi"]))

    def test_disclosure_explains_estimate_and_non_backtest_boundary(self):
        result = estimate_core_etf_flows(rows_for("2026-10-26", "2026-10-23"),
                                         trading_dates=calendar(["2026-10-23", "2026-10-26"]))
        text = " ".join(result["limitations"])
        for term in ("公告时点", "净值", "转托管", "实际现金流", "机构身份", "可交易回测"):
            self.assertIn(term, text)

    def test_source_status_is_preserved_and_incomplete_source_does_not_count(self):
        rows = rows_for("2026-10-26", "2026-10-23")
        rows["status"] = "ok"
        rows.loc[rows["code"] == "510310", "status"] = "split_adjusted"
        rows.loc[rows["code"] == "159919", "status"] = "partial"
        result = estimate_core_etf_flows(rows, trading_dates=calendar(["2026-10-23", "2026-10-26"]))
        items = result["components"].set_index("code")
        self.assertEqual(items.loc["510310", "source_status"], "split_adjusted")
        self.assertEqual(items.loc["510310", "status"], "ok")
        self.assertEqual(items.loc["159919", "source_status"], "partial")
        self.assertEqual(items.loc["159919", "status"], "source_incomplete")
        self.assertTrue(pd.isna(result["daily"].iloc[0]["flow_yi"]))

    def test_duplicate_fund_date_does_not_count_as_comparable(self):
        rows = rows_for("2026-10-26", "2026-10-23")
        rows = pd.concat([rows, rows.iloc[[0]]], ignore_index=True)
        result = estimate_core_etf_flows(rows, trading_dates=calendar(["2026-10-23", "2026-10-26"]))
        self.assertEqual(result["components"].set_index("code").loc["510300", "status"], "duplicate_fund")
        self.assertTrue(pd.isna(result["daily"].iloc[0]["flow_yi"]))

    def test_empty_input_has_no_synthetic_zero_total(self):
        result = estimate_core_etf_flows(pd.DataFrame(columns=["date", "code"]),
                                         trading_dates=calendar(["2026-10-23", "2026-10-26"]))
        self.assertTrue(result["components"].empty)
        self.assertTrue(result["daily"].empty)

    def test_existing_previous_day_shares_must_match_input(self):
        previous = rows_for("2026-10-23", "2026-10-22")
        today = rows_for("2026-10-26", "2026-10-23")
        today.loc[today["code"] == "510300", "previous_shares"] = 1.0
        result = estimate_core_etf_flows(pd.concat([previous, today], ignore_index=True),
            trading_dates=calendar(["2026-10-22", "2026-10-23", "2026-10-26"]))
        item = result["components"].set_index(["date", "code"]).loc[(pd.Timestamp("2026-10-26"), "510300")]
        self.assertEqual(item["status"], "previous_shares_mismatch")
        self.assertTrue(pd.isna(result["daily"].set_index("date").loc[pd.Timestamp("2026-10-26"), "flow_yi"]))

    def test_existing_previous_day_with_missing_shares_is_not_comparable(self):
        previous = rows_for("2026-10-23", "2026-10-22")
        previous.loc[previous["code"] == "510300", "shares"] = None
        today = rows_for("2026-10-26", "2026-10-23")
        result = estimate_core_etf_flows(pd.concat([previous, today], ignore_index=True),
            trading_dates=calendar(["2026-10-22", "2026-10-23", "2026-10-26"]))
        item = result["components"].set_index(["date", "code"]).loc[(pd.Timestamp("2026-10-26"), "510300")]
        self.assertEqual(item["status"], "previous_record_unavailable")
        self.assertTrue(pd.isna(item["flow_yi"]))

    def test_ambiguous_previous_day_record_is_not_comparable(self):
        previous = rows_for("2026-10-23", "2026-10-22")
        today = rows_for("2026-10-26", "2026-10-23")
        rows = pd.concat([previous, previous.iloc[[0]], today], ignore_index=True)
        result = estimate_core_etf_flows(rows,
            trading_dates=calendar(["2026-10-22", "2026-10-23", "2026-10-26"]))
        item = result["components"].set_index(["date", "code"]).loc[(pd.Timestamp("2026-10-26"), "510300")]
        self.assertEqual(item["status"], "previous_record_unavailable")
        self.assertTrue(pd.isna(item["flow_yi"]))

    def test_registered_event_factor_must_match_registry(self):
        for code, event, previous in (("159919", "2019-01-11", "2019-01-10"),
                                      ("510310", "2024-09-20", "2024-09-19")):
            with self.subTest(code=code):
                rows = rows_for(event, previous)
                result = estimate_core_etf_flows(rows, trading_dates=calendar([previous, event]))
                item = result["components"].set_index("code").loc[code]
                self.assertEqual(item["status"], "event_factor_mismatch")
                self.assertTrue(pd.isna(result["daily"].iloc[0]["flow_yi"]))

    def test_sparse_list_without_coverage_cannot_verify_calendar(self):
        rows = rows_for("2026-03-02", "2026-01-30")
        result = estimate_core_etf_flows(rows, trading_dates=["2026-01-30", "2026-03-02"])
        self.assertTrue((result["components"]["status"] == "calendar_unavailable").all())
        self.assertTrue(pd.isna(result["daily"].iloc[0]["flow_yi"]))

    def test_calendar_coverage_must_include_prior_trading_day(self):
        dates = calendar(["2026-10-23", "2026-10-26"])
        dates["coverage_start"] = "2026-10-26"
        result = estimate_core_etf_flows(rows_for("2026-10-26", "2026-10-23"), trading_dates=dates)
        self.assertTrue((result["components"]["status"] == "calendar_unavailable").all())
        self.assertTrue(pd.isna(result["daily"].iloc[0]["flow_yi"]))

    def test_unregistered_action_factor_is_not_assumed_valid(self):
        rows = rows_for("2026-10-26", "2026-10-23")
        rows.loc[rows["code"] == "510300", "split_factor"] = 0.5
        result = estimate_core_etf_flows(rows, trading_dates=calendar(["2026-10-23", "2026-10-26"]))
        self.assertEqual(result["components"].set_index("code").loc["510300", "status"], "event_factor_unverified")
        self.assertTrue(pd.isna(result["daily"].iloc[0]["flow_yi"]))

    def test_nav_and_asof_are_explicit_caller_contract(self):
        self.assertIn("同日", estimate_core_etf_flows.__doc__)
        self.assertIn("as_of", estimate_core_etf_flows.__doc__)


if __name__ == "__main__":
    unittest.main()
