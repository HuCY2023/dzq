import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.diagnosis import diagnose_position, score_history_weekly
from test_data_availability import core_samples


def holder_rows():
    days = pd.date_range(end="2026-10-19", periods=10, freq="W-MON")
    return pd.DataFrame({"date": days, "notice_date": days + pd.Timedelta(days=1),
                         "notice_known": True, "amount_yi": 10.0})


class HolderDiagnosisTests(unittest.TestCase):
    def test_future_notice_does_not_change_current_or_historical_score(self):
        bundle = core_samples("2026-10-26")
        bundle["holder_reduce"] = holder_rows()
        before = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        before_curve = score_history_weekly(bundle, as_of="2026-10-27")
        future = pd.DataFrame([{"date": "2026-10-23", "notice_date": "2026-10-28",
                                "notice_known": True, "amount_yi": 1000.0}])
        bundle["holder_reduce"] = pd.concat([bundle["holder_reduce"], future], ignore_index=True)
        after = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        after_curve = score_history_weekly(bundle, as_of="2026-10-27")
        self.assertEqual(before["score"], after["score"])
        self.assertEqual(before_curve, after_curve)

    def test_unverified_same_day_notice_makes_current_factor_partial(self):
        bundle = core_samples("2026-10-26")
        unknown = pd.DataFrame([{"date": "2026-10-20", "notice_date": "2026-10-20",
                                 "notice_known": None, "amount_yi": 100.0}])
        bundle["holder_reduce"] = pd.concat([holder_rows(), unknown], ignore_index=True)
        result = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        item = next(i for i in result["items"] if i["id"] == "holder_reduce")
        self.assertEqual(item["status"], "partial")
        self.assertIsNone(item["score"])
        without = dict(bundle, holder_reduce=pd.DataFrame())
        self.assertEqual(score_history_weekly(bundle, as_of="2026-10-27"),
                         score_history_weekly(without, as_of="2026-10-27"))

    def test_week_with_missing_amount_is_partial_in_current_and_history(self):
        bundle = core_samples("2026-10-26")
        missing = pd.DataFrame([{"date": "2026-10-20", "notice_date": "2026-10-21",
                                 "notice_known": True, "amount_yi": None}])
        bundle["holder_reduce"] = pd.concat([holder_rows(), missing], ignore_index=True)
        result = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        item = next(i for i in result["items"] if i["id"] == "holder_reduce")
        self.assertEqual(item["status"], "partial")
        self.assertIsNone(item["score"])
        self.assertIn("金额缺失", item["note"])
        without = dict(bundle, holder_reduce=pd.DataFrame())
        self.assertEqual(score_history_weekly(bundle, as_of="2026-10-27"),
                         score_history_weekly(without, as_of="2026-10-27"))

    def test_empty_target_week_cannot_reuse_a_prior_week(self):
        bundle = core_samples("2026-10-26")
        bundle["holder_reduce"] = holder_rows().iloc[:-1].copy()
        result = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        item = next(i for i in result["items"] if i["id"] == "holder_reduce")
        self.assertNotEqual(item["status"], "ok")
        self.assertIsNone(item["score"])


if __name__ == "__main__":
    unittest.main()
