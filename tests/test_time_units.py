import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.diagnosis import score_history_weekly


class HistoricalTimeTests(unittest.TestCase):
    def test_identical_dates_in_microseconds_and_nanoseconds_match(self):
        weekly = pd.DataFrame({"date": pd.date_range("2020-02-07", "2020-04-24", freq="W-FRI"), "close": 1.0})
        dates = pd.date_range("2018-01-31", "2020-05-31", freq="ME")
        unlock = pd.DataFrame({"date": dates, "universe": "all", "amount_yi": 100.0})
        unlock.loc[unlock["date"] == pd.Timestamp("2020-04-30"), "amount_yi"] = 200.0

        def calculate(unit):
            frames = {"weekly": weekly.copy(), "unlock_daily": unlock.copy()}
            for frame in frames.values():
                frame["date"] = frame["date"].astype(f"datetime64[{unit}]")
            return score_history_weekly(frames)

        microseconds = calculate("us")
        nanoseconds = calculate("ns")
        self.assertTrue(any(point["value"] == -1 for point in nanoseconds))
        self.assertEqual(microseconds, nanoseconds)

    def test_divergence_uses_the_value_at_least_60_days_earlier(self):
        weekly = pd.DataFrame({"date": [pd.Timestamp("2020-04-10")], "close": [1.0]})
        divergence = pd.DataFrame({
            "date": [pd.Timestamp("2020-02-01"), pd.Timestamp("2020-04-01"), pd.Timestamp("2020-04-10")],
            "偏离度": [20.0, 10.0, 12.0],
        })
        result = score_history_weekly({"weekly": weekly, "growth_value": divergence})
        self.assertEqual(result[-1]["value"], -1)


if __name__ == "__main__":
    unittest.main()
