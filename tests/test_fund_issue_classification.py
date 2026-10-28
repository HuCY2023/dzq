import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.diagnosis import _fund_issue_observation, diagnose_position, score_history_weekly
from data_source.dp_cycle.fund_issue import classify_fund_issue


class FundIssueClassificationTests(unittest.TestCase):
    def test_groups_bonds_qdii_fof_and_cross_border_names_separately(self):
        cases = [
            ("指数型-股票", "沪深300ETF", "domestic_equity_candidate"),
            ("混合型-偏股", "偏股基金", "domestic_equity_candidate"),
            ("指数型-股票", "港股通消费ETF", "cross_border_candidate"),
            ("指数型-海外股票", "海外ETF", "cross_border_candidate"),
            ("QDII-混合偏股", "QDII基金", "qdii"),
            ("债券型-长债", "债券基金", "bond"),
            ("指数型-固收", "固定收益基金", "bond"),
            ("FOF-稳健型", "FOF基金", "fof"),
            ("混合型-偏债", "混合基金", "other_mixed"),
            ("", "未知基金", "unknown"),
        ]
        for fund_type, name, expected in cases:
            with self.subTest(fund_type=fund_type, name=name):
                self.assertEqual(classify_fund_issue(fund_type, name), expected)

    def test_latest_week_reports_only_observed_equity_candidates_and_missing_counts(self):
        frame = pd.DataFrame({
            "date": pd.to_datetime(["2026-10-21"] * 7),
            "name": ["沪深300ETF", "偏股基金", "债券基金", "港股通ETF", "QDII", "FOF", "未知"],
            "fund_type": ["指数型-股票", "混合型-偏股", "债券型-长债", "指数型-股票", "QDII-普通股票", "FOF-稳健型", ""],
            "shares_yi": [10.0, None, 1000.0, 200.0, 300.0, 400.0, 5.0],
        })
        item = _fund_issue_observation(frame, period_end=pd.Timestamp("2026-10-23"))
        self.assertEqual(item["status"], "partial")
        self.assertIsNone(item["score"])
        groups = item["groups"]
        self.assertEqual(groups["domestic_equity_candidate"]["observed"], 2)
        self.assertEqual(groups["domestic_equity_candidate"]["missing_shares"], 1)
        self.assertEqual(groups["domestic_equity_candidate"]["known_shares_yi"], 10.0)
        self.assertEqual(groups["cross_border_candidate"]["known_shares_yi"], 200.0)
        self.assertEqual(groups["bond"]["known_shares_yi"], 1000.0)
        self.assertEqual(groups["qdii"]["known_shares_yi"], 300.0)
        self.assertIn("仅观察", item["note"])
        self.assertIn("未证明", item["note"])

    def test_missing_week_does_not_reuse_previous_week_or_claim_zero(self):
        frame = pd.DataFrame({"date": ["2026-10-14"], "name": ["沪深300ETF"],
                              "fund_type": ["指数型-股票"], "shares_yi": [10.0]})
        item = _fund_issue_observation(frame, period_end=pd.Timestamp("2026-10-23"))
        self.assertEqual(item["status"], "gap")
        self.assertIsNone(item["score"])

    def test_unverified_issue_history_is_excluded_from_improved_curve(self):
        bundle = {
            "weekly": pd.DataFrame({"date": ["2026-10-16", "2026-10-23"], "close": [100, 100]}),
            "fund_issue": pd.DataFrame({"date": ["2026-10-14", "2026-10-21"],
                                        "shares_yi": [400.0, 100.0],
                                        "fund_type": ["债券型-长债", "指数型-股票"],
                                        "name": ["债券基金", "沪深300ETF"]}),
        }
        self.assertEqual(score_history_weekly(bundle, as_of="2026-10-23"), [])
        author = score_history_weekly(bundle, as_of="2026-10-23", rule_version="author_v1")
        self.assertEqual(author, [{"time": "2026-10-23", "value": -1}])

    def test_current_improved_rule_is_observation_and_author_rule_is_preserved(self):
        dates = pd.date_range(end="2026-10-21", periods=8, freq="7D")
        frame = pd.DataFrame({"date": dates, "name": ["沪深300ETF"] * 8,
                              "fund_type": ["指数型-股票"] * 8,
                              "shares_yi": [100, 100, 100, 100, 100, 100, 400, 100]})
        bundle = {"fund_issue": frame}
        improved = diagnose_position(bundle, as_of="2026-10-27", heavy=False)
        author = diagnose_position(bundle, as_of="2026-10-27", heavy=False, rule_version="author_v1")
        first = next(item for item in improved["items"] if item["id"] == "fund_issue")
        second = next(item for item in author["items"] if item["id"] == "fund_issue")
        self.assertEqual((first["status"], first["score"]), ("info", None))
        self.assertEqual((second["status"], second["score"]), ("ok", -1))


if __name__ == "__main__":
    unittest.main()
