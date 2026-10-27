import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.cross_check import validate_tables, check_main_force, check_margin


class QualityLabelsTest(unittest.TestCase):
    def test_read_only_checks_describe_scope_and_northbound_historical_state(self):
        dates = pd.bdate_range(end="2024-08-16", periods=120)
        northbound = pd.DataFrame({"date": dates, "净额_亿元": [1.0] * len(dates)})
        result = validate_tables({"northbound_daily": northbound}, live=False)
        self.assertIn("表内一致性检查", result["summary"])
        badge = next(x for x in result["badges"] if x["id"] == "northbound")
        self.assertEqual(badge["source_status"], "historical")
        self.assertIn("2024-08-19", badge["note"])

    def test_latest_missing_main_force_net_is_not_stable(self):
        dates = pd.bdate_range(end="2026-10-23", periods=20)
        frame = pd.DataFrame({"date": list(dates) + [pd.Timestamp("2026-10-26")],
                              "净额_亿元": [1.0] * 20 + [float("nan")]})
        result = check_main_force(frame)
        self.assertFalse(result["stable"])
        self.assertIn("2026-10-23", result["note"])
        self.assertIn("2026-10-26", result["note"])

    def test_margin_with_only_missing_values_is_reported_missing(self):
        result = check_margin(pd.DataFrame({"date": ["2026-10-26"], "融资余额_亿元": [None]}))
        self.assertFalse(result.get("ok", False))

    def test_etf_badge_discloses_missing_fund_even_if_values_are_consistent(self):
        dates = pd.date_range(end="2026-10-26", periods=6)
        hist = pd.DataFrame([{"date": day, "code": code, "份额_亿份": 10.0}
                             for day in dates for code in ("510300", "510310", "510330")])
        daily = hist.groupby("date", as_index=False)["份额_亿份"].sum()
        result = validate_tables({"etf_daily": daily, "etf_hist": hist}, live=False)
        badge = next(x for x in result["badges"] if x["id"] == "etf")
        self.assertEqual(badge["source_status"], "partial")
        self.assertIn("3/4", badge["note"])


if __name__ == "__main__":
    unittest.main()
