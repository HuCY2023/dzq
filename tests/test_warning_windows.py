import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.evaluation import evaluate_warning_windows
from test_evaluation_baselines import inputs


class WarningWindowTests(unittest.TestCase):
    def setUp(self):
        self.prices, self.signals, self.evidence, self.dates = inputs(12)
        values = [100, 100, 110, 105, 80, 90, 100, 100, 100, 100, 100, 100]
        self.prices["actual_open"] = values
        self.prices["total_return_open"] = values
        self.evidence["warning_vintage_evidence"] = "synthetic warning history"

    def warnings(self, positions=(0, 1, 5, 9)):
        return pd.DataFrame({"signal_at": [str(self.dates[i].date()) + " 16:00:00" for i in positions],
                             "available_at": [str(self.dates[i].date()) + " 15:00:00" for i in positions],
                             "warning": [True] * len(positions)})

    def evaluate(self, warnings=None, **kwargs):
        return evaluate_warning_windows(self.prices, self.warnings() if warnings is None else warnings,
                                        self.evidence, trading_dates=self.dates, horizons=(3,), **kwargs)

    def test_overlapping_warnings_are_not_counted_as_independent_windows(self):
        result = self.evaluate()
        self.assertEqual(result["status"], "DATA_PARTIAL")
        window = result["horizons"]["3"]
        self.assertEqual(window["warning_count"], 4)
        self.assertEqual(window["overlapping_count"], 1)
        self.assertEqual(window["incomplete_count"], 1)
        self.assertEqual(len(window["events"]), 2)
        first = window["events"][0]
        self.assertEqual(first["entry_date"], str(self.dates[1].date()))
        self.assertEqual(first["end_date"], str(self.dates[4].date()))
        self.assertAlmostEqual(first["forward_return"], -0.2)
        self.assertAlmostEqual(first["max_drawdown"], 80 / 110 - 1)
        self.assertIn("不保证统计独立", result["note"])

    def test_appending_future_prices_does_not_change_completed_windows(self):
        first = self.evaluate(warnings=self.warnings((0, 1)))
        more, _, _, extra_dates = inputs(16)
        more.loc[:11, ["actual_open", "total_return_open"]] = self.prices[["actual_open", "total_return_open"]].values
        later = evaluate_warning_windows(more, self.warnings((0, 1)), self.evidence,
                                         trading_dates=extra_dates, horizons=(3,))
        self.assertEqual(first["horizons"]["3"]["events"], later["horizons"]["3"]["events"])

    def test_missing_warning_vintage_or_future_information_blocks(self):
        missing = dict(self.evidence)
        missing.pop("warning_vintage_evidence")
        self.assertEqual(evaluate_warning_windows(self.prices, self.warnings(), missing,
                                                  trading_dates=self.dates, horizons=(3,))["status"], "BLOCK")
        warning = self.warnings((0,))
        warning.loc[0, "available_at"] = str(self.dates[1].date()) + " 10:00:00"
        self.assertEqual(self.evaluate(warnings=warning)["status"], "BLOCK")

    def test_first_day_preopen_warning_can_use_the_first_open(self):
        warning = self.warnings((0,))
        warning.loc[0, "signal_at"] = str(self.dates[0].date()) + " 08:00:00"
        warning.loc[0, "available_at"] = str(self.dates[0].date()) + " 07:59:00"
        result = self.evaluate(warnings=warning)
        event = result["horizons"]["3"]["events"][0]
        self.assertEqual(event["entry_date"], str(self.dates[0].date()))
        self.assertEqual(event["end_date"], str(self.dates[3].date()))

    def test_missing_horizon_endpoint_is_not_a_zero_return(self):
        self.prices.loc[4, "tradable"] = False
        self.prices.loc[4, "actual_open"] = float("nan")
        result = self.evaluate(warnings=self.warnings((0,)))
        self.assertEqual(result["status"], "DATA_PARTIAL")
        self.assertEqual(result["horizons"]["3"]["events"], [])
        self.assertEqual(result["horizons"]["3"]["incomplete_count"], 1)

    def test_unknown_warning_is_reported_separately(self):
        warning = self.warnings((0, 5))
        warning = warning.astype({"warning": object, "available_at": object})
        warning.at[1, "warning"] = None
        warning.at[1, "available_at"] = None
        result = self.evaluate(warnings=warning)
        self.assertEqual(result["status"], "DATA_PARTIAL")
        self.assertEqual(result["unknown_count"], 1)
        self.assertEqual(result["horizons"]["3"]["warning_count"], 1)
        self.assertEqual(len(result["horizons"]["3"]["events"]), 1)

    def test_cli_saves_warning_windows_when_evidence_is_available(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "backtest_rules.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.prices.to_csv(root / "prices.csv", index=False)
            self.signals.to_csv(root / "signals.csv", index=False)
            self.warnings((0,)).to_csv(root / "warnings.csv", index=False)
            pd.DataFrame({"date": self.dates}).to_csv(root / "calendar.csv", index=False)
            (root / "evidence.json").write_text(json.dumps(self.evidence), encoding="utf-8")
            args = [sys.executable, str(script), "--prices", str(root / "prices.csv"),
                    "--signals", str(root / "signals.csv"), "--warnings", str(root / "warnings.csv"),
                    "--calendar", str(root / "calendar.csv"), "--evidence", str(root / "evidence.json"),
                    "--output", str(root / "runs")]
            completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(completed.returncode, 2, completed.stderr)
            stored = json.loads(next((root / "runs").glob("*/evaluation.json")).read_text(encoding="utf-8"))
            self.assertEqual(stored["warning_windows"]["status"], "DATA_PARTIAL")
            self.assertEqual(stored["validation_status"], "not_validated")


if __name__ == "__main__":
    unittest.main()
