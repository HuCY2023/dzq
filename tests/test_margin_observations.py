import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.diagnosis import _margin_item
from data_source.dp_cycle.margin import margin_signal_at


def sample():
    dates = pd.bdate_range(end="2026-10-23", periods=30)
    frame = pd.DataFrame({"date": dates, "融资余额_亿元": [1000.0 + 10 * i for i in range(len(dates))]})
    calendar = {"dates": dates.strftime("%Y-%m-%d").tolist(), "coverage_start": str(dates[0].date()),
                "coverage_end": str(dates[-1].date()), "source": "synthetic calendar"}
    return frame, calendar


class MarginObservationTests(unittest.TestCase):
    def test_five_and_twenty_trading_day_changes_are_distinct(self):
        frame, calendar = sample()
        signal = margin_signal_at(frame, trade_calendar=calendar)
        self.assertEqual((signal["delta_5"], signal["delta"]), (50.0, 200.0))
        item = _margin_item(frame, trade_calendar=calendar)
        self.assertEqual((item["delta_5"], item["delta_20"]), (50.0, 200.0))
        self.assertIn("5个交易日间隔", item["note"])

    def test_missing_five_day_baseline_is_not_replaced_by_another_day(self):
        frame, calendar = sample()
        frame = frame.drop(index=frame.index[-6])
        signal = margin_signal_at(frame, trade_calendar=calendar)
        self.assertIsNone(signal["delta_5"])
        self.assertEqual(signal["delta"], 200.0)

    def test_without_calendar_the_short_interval_is_labeled_observed_days(self):
        frame, _ = sample()
        item = _margin_item(frame)
        self.assertEqual(item["delta_5"], 50.0)
        self.assertIn("5个观测日间隔", item["note"])
        self.assertIn("未核验交易日历", item["note"])


if __name__ == "__main__":
    unittest.main()
