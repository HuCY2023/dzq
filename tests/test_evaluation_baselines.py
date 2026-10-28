import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.evaluation import evaluate_allocation


def inputs(count=30):
    dates = pd.bdate_range("2024-01-02", periods=count)
    price = [100.0 + i for i in range(count)]
    frame = pd.DataFrame({"date": dates, "actual_open": price, "total_return_open": price,
                          "tradable": [True] * count})
    signals = pd.DataFrame({"signal_at": ["2024-01-02 16:00:00"],
                            "available_at": ["2024-01-02 15:00:00"], "target_equity": [0.6]})
    evidence = {"symbol": "TEST-ETF", "instrument_type": "ETF", "market": "CN_A_SHARE",
                "price_source": "synthetic", "calendar_source": "synthetic", "calendar_verified": True,
                "price_adjustment_evidence": "synthetic", "dividend_treatment": "synthetic",
                "signal_vintage_evidence": "synthetic", "point_in_time_verified": True,
                "execution_prices_verified": True, "total_return_verified": True}
    return frame, signals, evidence, dates


class EvaluationBaselineTests(unittest.TestCase):
    def test_trend_and_volatility_wait_for_past_observations_and_next_open(self):
        price, signals, evidence, dates = inputs()
        result = evaluate_allocation(price, signals, evidence, trading_dates=dates)
        trend = result["strategies"]["trend_20"]
        volatility = result["strategies"]["volatility_20"]
        self.assertEqual(trend["status"], "OK")
        self.assertEqual(volatility["status"], "OK")
        self.assertEqual(trend["trades"][0]["date"], str(dates[20].date()))
        self.assertEqual(volatility["trades"][0]["date"], str(dates[21].date()))
        self.assertEqual(result["assumptions"]["one_way_cost"], 0.0005)

    def test_future_prices_do_not_change_earlier_baseline_results(self):
        price, signals, evidence, dates = inputs()
        first = evaluate_allocation(price, signals, evidence, trading_dates=dates)
        later = price.copy()
        later.loc[25:, ["actual_open", "total_return_open"]] *= 0.5
        second = evaluate_allocation(later, signals, evidence, trading_dates=dates)
        for name in ("trend_20", "volatility_20"):
            with self.subTest(name=name):
                self.assertEqual(first["strategies"][name]["daily"][:25],
                                 second["strategies"][name]["daily"][:25])

    def test_suspension_does_not_count_as_an_observed_open(self):
        price, signals, evidence, dates = inputs()
        price.loc[5, "tradable"] = False
        price.loc[5, "actual_open"] = float("nan")
        result = evaluate_allocation(price, signals, evidence, trading_dates=dates)
        self.assertEqual(result["strategies"]["trend_20"]["trades"][0]["date"], str(dates[21].date()))

    def test_insufficient_warmup_is_unavailable_not_zero_return(self):
        price, signals, evidence, dates = inputs(10)
        result = evaluate_allocation(price, signals, evidence, trading_dates=dates)
        self.assertEqual(result["strategies"]["trend_20"]["status"], "unavailable")
        self.assertNotIn("metrics", result["strategies"]["trend_20"])
        self.assertEqual(result["strategies"]["volatility_20"]["status"], "unavailable")

    def test_average_exposure_match_is_labeled_as_ex_post_comparison(self):
        price, signals, evidence, dates = inputs()
        result = evaluate_allocation(price, signals, evidence, trading_dates=dates)
        matched = result["strategies"]["matched_exposure"]
        self.assertAlmostEqual(matched["target_equity"], result["strategies"]["rule"]["metrics"]["average_equity"])
        self.assertIn("事后", matched["note"])
        self.assertIn("不是", matched["note"])

    def test_all_executable_baselines_use_the_same_cost_sensitivity(self):
        price, signals, evidence, dates = inputs()
        normal = evaluate_allocation(price, signals, evidence, trading_dates=dates)
        higher = evaluate_allocation(price, signals, evidence, trading_dates=dates, one_way_cost=0.001)
        for name in ("trend_20", "volatility_20", "matched_exposure"):
            with self.subTest(name=name):
                self.assertLess(higher["strategies"][name]["daily"][-1]["nav"],
                                normal["strategies"][name]["daily"][-1]["nav"])


if __name__ == "__main__":
    unittest.main()
