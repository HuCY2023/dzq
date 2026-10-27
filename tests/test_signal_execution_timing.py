import sys
import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.evaluation import evaluate_allocation


def prices():
    return pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        "actual_open": [100.0, 110.0, 121.0, 133.1],
        "total_return_open": [100.0, 110.0, 121.0, 133.1],
        "tradable": [True, True, True, True],
    })


def signals():
    return pd.DataFrame({
        "signal_at": ["2024-01-02 16:00:00"],
        "available_at": ["2024-01-02 15:00:00"],
        "target_equity": [1.0],
    })


def evidence():
    return {
        "symbol": "TEST-ETF", "instrument_type": "ETF", "market": "CN_A_SHARE", "price_source": "synthetic",
        "calendar_source": "synthetic", "calendar_verified": True,
        "price_adjustment_evidence": "synthetic total-return adjustment",
        "dividend_treatment": "reinvested in total-return prices",
        "signal_vintage_evidence": "synthetic versioned inputs",
        "point_in_time_verified": True, "execution_prices_verified": True,
        "total_return_verified": True,
    }


def evaluate(p=None, s=None, e=None, calendar=None):
    p = prices() if p is None else p
    return evaluate_allocation(
        p, signals() if s is None else s, evidence() if e is None else e,
        trading_dates=prices()["date"].tolist() if calendar is None else calendar,
    )


class ExecutionTimingTests(unittest.TestCase):
    def test_signal_executes_at_next_open_and_cost_is_applied(self):
        result = evaluate()
        self.assertEqual(result["status"], "OK")
        strategy = result["strategies"]["rule"]
        self.assertEqual(strategy["trades"][0]["date"], "2024-01-03")
        self.assertAlmostEqual(strategy["daily"][0]["equity_fraction"], 0.0)
        self.assertAlmostEqual(strategy["daily"][1]["nav"], 1 / 1.0005)
        self.assertAlmostEqual(strategy["daily"][-1]["nav"], 1.21 / 1.0005)
        self.assertEqual(result["assumptions"]["one_way_cost"], 0.0005)

    def test_signal_at_open_waits_for_the_following_open(self):
        s = signals()
        s.loc[0, "signal_at"] = "2024-01-03 09:30:00"
        s.loc[0, "available_at"] = "2024-01-03 09:30:00"
        result = evaluate(s=s)
        self.assertEqual(result["strategies"]["rule"]["trades"][0]["date"], "2024-01-04")

    def test_flat_prices_do_not_trigger_spurious_fixed_weight_transactions(self):
        p = prices()
        p["actual_open"] = 100.0
        p["total_return_open"] = 100.0
        result = evaluate(p=p)
        self.assertEqual(len(result["strategies"]["fixed_50"]["trades"]), 1)

    def test_suspension_defers_execution_and_zero_target_is_real(self):
        p = prices()
        p.loc[1, "tradable"] = False
        p.loc[1, "actual_open"] = float("nan")
        s = pd.concat([signals(), pd.DataFrame({
            "signal_at": ["2024-01-04 10:00:00"],
            "available_at": ["2024-01-04 09:59:00"], "target_equity": [0.0],
        })], ignore_index=True)
        result = evaluate(p=p, s=s)
        self.assertEqual(result["status"], "OK")
        self.assertEqual([t["date"] for t in result["strategies"]["rule"]["trades"]],
                         ["2024-01-04", "2024-01-05"])
        self.assertEqual(result["strategies"]["rule"]["trades"][-1]["target_equity"], 0.0)
        self.assertAlmostEqual(result["strategies"]["rule"]["daily"][-1]["nav"],
                               1.1 * 0.9995 / 1.0005)

    def test_unknown_latest_signal_cancels_pending_without_selling_existing_position(self):
        p = prices()
        p.loc[1, "tradable"] = False
        p.loc[1, "actual_open"] = float("nan")
        s = pd.DataFrame({
            "signal_at": ["2024-01-01 16:00:00", "2024-01-02 16:00:00", "2024-01-03 17:00:00"],
            "available_at": ["2024-01-01 15:00:00", "2024-01-02 15:00:00", None],
            "target_equity": [0.5, 1.0, None],
        })
        result = evaluate(p=p, s=s)
        self.assertEqual(result["status"], "DATA_PARTIAL")
        strategy = result["strategies"]["rule"]
        self.assertEqual([(t["date"], t["side"], t["target_equity"]) for t in strategy["trades"]],
                         [("2024-01-02", "buy", 0.5)])
        self.assertIsNone(strategy["unfilled_signal"])
        self.assertEqual(strategy["cancelled_signals"], [{"signal_at": "2024-01-02 16:00:00",
                                                         "cancelled_at": "2024-01-03 17:00:00"}])
        self.assertGreater(strategy["daily"][-1]["equity_fraction"], 0.0)

    def test_unknown_after_execution_is_reported_and_marks_result_partial(self):
        s = pd.DataFrame({
            "signal_at": ["2024-01-02 16:00:00", "2024-01-03 18:00:00"],
            "available_at": ["2024-01-02 15:00:00", None],
            "target_equity": [1.0, None],
        })
        result = evaluate(s=s)
        self.assertEqual(result["status"], "DATA_PARTIAL")
        strategy = result["strategies"]["rule"]
        self.assertEqual(strategy["unknown_signals"], [{"signal_at": "2024-01-03 18:00:00"}])
        self.assertEqual(len(strategy["trades"]), 1)
        self.assertAlmostEqual(strategy["daily"][-1]["equity_fraction"], 1.0)

    def test_python_date_without_time_does_not_claim_preopen_availability(self):
        s = pd.DataFrame({"signal_at": [date(2024, 1, 3)],
                          "available_at": [date(2024, 1, 3)], "target_equity": [1.0]})
        self.assertEqual(evaluate(s=s)["status"], "BLOCK")

    def test_date_promoted_to_pandas_midnight_does_not_claim_a_time(self):
        s = signals()
        s["signal_at"] = pd.to_datetime(["2024-01-03"])
        self.assertEqual(evaluate(s=s)["status"], "BLOCK")
        s = signals()
        s["available_at"] = pd.to_datetime(["2024-01-02"])
        self.assertEqual(evaluate(s=s)["status"], "BLOCK")

    def test_explicit_midnight_string_is_a_time(self):
        s = signals()
        s["signal_at"] = "2024-01-03 00:00:00"
        result = evaluate(s=s)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["strategies"]["rule"]["trades"][0]["date"], "2024-01-03")

    def test_unfilled_order_is_reported_when_no_later_session_is_tradable(self):
        p = prices()
        p.loc[1:, "tradable"] = False
        p.loc[1:, "actual_open"] = float("nan")
        p.loc[1:, "total_return_open"] = 100.0
        result = evaluate(p=p)
        self.assertEqual(result["strategies"]["rule"]["trades"], [])
        self.assertEqual(result["strategies"]["rule"]["unfilled_signal"]["target_equity"], 1.0)

    def test_missing_or_future_information_blocks_evaluation(self):
        s = signals()
        s.loc[0, "available_at"] = "2024-01-03 10:00:00"
        result = evaluate(s=s)
        self.assertEqual(result["status"], "BLOCK")
        self.assertNotIn("strategies", result)
        s.loc[0, "available_at"] = None
        self.assertEqual(evaluate(s=s)["status"], "BLOCK")
        e = evidence()
        e.pop("signal_vintage_evidence")
        self.assertEqual(evaluate(e=e)["status"], "BLOCK")

    def test_future_signal_and_missing_target_do_not_create_trades(self):
        s = pd.concat([signals(), pd.DataFrame({
            "signal_at": ["2024-01-03 18:00:00", "2024-01-06 18:00:00"],
            "available_at": ["2024-01-03 17:00:00", "2024-01-06 17:00:00"],
            "target_equity": [None, 0.0],
        })], ignore_index=True)
        normal = evaluate()["strategies"]["rule"]
        extended = evaluate(s=s)["strategies"]["rule"]
        self.assertEqual(normal["trades"], extended["trades"])
        self.assertEqual(normal["daily"], extended["daily"])

    def test_missing_price_or_calendar_is_not_zero_return(self):
        p = prices()
        p.loc[2, "total_return_open"] = float("nan")
        self.assertEqual(evaluate(p=p)["status"], "BLOCK")
        calendar = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
        self.assertEqual(evaluate(p=prices().iloc[:-1], calendar=calendar)["status"], "BLOCK")

    def test_benchmarks_share_period_and_cost_assumptions(self):
        result = evaluate()
        self.assertEqual(result["validation_status"], "not_validated")
        self.assertEqual(result["period"]["end_at"], "2024-01-05 09:30:00")
        self.assertEqual(set(result["strategies"]), {"rule", "buy_hold", "fixed_50"})
        for strategy in result["strategies"].values():
            self.assertEqual([row["date"] for row in strategy["daily"]], prices()["date"].tolist())
            self.assertIn("max_drawdown", strategy["metrics"])
            self.assertIn("turnover", strategy["metrics"])
            self.assertIn("average_equity", strategy["metrics"])
        self.assertAlmostEqual(result["strategies"]["buy_hold"]["daily"][-1]["nav"], 1.331 / 1.0005)
        self.assertAlmostEqual(result["strategies"]["fixed_50"]["daily"][0]["nav"], 1 / 1.00025)

    def test_protocol_cost_sensitivity_uses_the_same_inputs(self):
        normal = evaluate()
        sensitivity = evaluate_allocation(prices(), signals(), evidence(),
                                          trading_dates=prices()["date"], one_way_cost=0.001)
        self.assertEqual(sensitivity["assumptions"]["one_way_cost"], 0.001)
        self.assertEqual(normal["period"], sensitivity["period"])
        for name in ("rule", "buy_hold", "fixed_50"):
            self.assertLess(sensitivity["strategies"][name]["daily"][-1]["nav"],
                            normal["strategies"][name]["daily"][-1]["nav"])

    def test_no_in_period_signal_is_missing_not_a_zero_position_model(self):
        s = signals()
        s["target_equity"] = None
        result = evaluate(s=s)
        self.assertEqual(result["status"], "BLOCK")
        self.assertNotIn("strategies", result)

    def test_total_return_and_executable_price_need_explicit_verification(self):
        e = evidence()
        e["total_return_verified"] = False
        self.assertEqual(evaluate(e=e)["status"], "BLOCK")
        e = evidence()
        e["execution_prices_verified"] = False
        self.assertEqual(evaluate(e=e)["status"], "BLOCK")

    def test_execution_clock_is_only_applied_to_its_declared_market(self):
        e = evidence()
        e["market"] = "OTHER"
        self.assertEqual(evaluate(e=e)["status"], "BLOCK")

    def test_cli_without_inputs_reports_block_and_does_not_invent_returns(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "backtest_rules.py"
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 2)
        self.assertIn("BLOCK", result.stdout)
        self.assertNotIn("net_return", result.stdout)

    def test_cli_preserves_separate_runs_for_verified_fixture(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "backtest_rules.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prices().to_csv(root / "prices.csv", index=False)
            signals().to_csv(root / "signals.csv", index=False)
            pd.DataFrame({"date": prices()["date"]}).to_csv(root / "calendar.csv", index=False)
            (root / "evidence.json").write_text(json.dumps(evidence()), encoding="utf-8")
            args = [sys.executable, str(script), "--prices", str(root / "prices.csv"),
                    "--signals", str(root / "signals.csv"), "--calendar", str(root / "calendar.csv"),
                    "--evidence", str(root / "evidence.json"), "--output", str(root / "runs")]
            for _ in range(2):
                result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)
            runs = list((root / "runs").glob("*/evaluation.json"))
            self.assertEqual(len(runs), 2)
            stored = json.loads(runs[0].read_text(encoding="utf-8"))
            self.assertEqual(stored["status"], "OK")


if __name__ == "__main__":
    unittest.main()
