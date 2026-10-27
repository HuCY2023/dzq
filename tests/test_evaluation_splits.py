import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.evaluation import purged_time_split
from test_evaluation_baselines import inputs


class EvaluationSplitTests(unittest.TestCase):
    def test_future_labels_do_not_cross_any_partition_boundary(self):
        dates = pd.bdate_range("2024-01-02", periods=30)
        split = purged_time_split(dates, train_end=dates[9], validation_end=dates[19],
                                  test_end=dates[29], label_horizon=3)
        self.assertEqual(split["status"], "OK")
        self.assertIn("不代表当日已有信号", split["note"])
        self.assertEqual([len(split["partitions"][key]) for key in ("train", "validation", "test")], [7, 7, 7])
        for name, boundary in (("train", dates[9]), ("validation", dates[19]), ("test", dates[29])):
            for row in split["partitions"][name]:
                self.assertLessEqual(pd.Timestamp(row["label_end"]), boundary)
        self.assertEqual(split["excluded_count"], 9)

    def test_future_extension_does_not_change_a_fixed_split(self):
        dates = pd.bdate_range("2024-01-02", periods=35)
        options = {"train_end": dates[9], "validation_end": dates[19], "test_end": dates[29], "label_horizon": 3}
        first = purged_time_split(dates[:30], **options)
        second = purged_time_split(dates, **options)
        self.assertEqual(first, second)

    def test_short_partitions_are_partial_not_fabricated(self):
        dates = pd.bdate_range("2024-01-02", periods=9)
        split = purged_time_split(dates, train_end=dates[2], validation_end=dates[5],
                                  test_end=dates[8], label_horizon=3)
        self.assertEqual(split["status"], "DATA_PARTIAL")
        self.assertEqual([len(split["partitions"][key]) for key in ("train", "validation", "test")], [0, 0, 0])

    def test_split_input_must_be_ordered_and_nonoverlapping(self):
        dates = pd.bdate_range("2024-01-02", periods=10)
        with self.assertRaises(ValueError):
            purged_time_split(dates[::-1], train_end=dates[2], validation_end=dates[5],
                              test_end=dates[9], label_horizon=1)
        with self.assertRaises(ValueError):
            purged_time_split(dates, train_end=dates[5], validation_end=dates[2],
                              test_end=dates[9], label_horizon=1)

    def test_cli_can_save_an_explicit_predeclared_split(self):
        prices, signals, evidence, dates = inputs(30)
        script = Path(__file__).resolve().parents[1] / "scripts" / "backtest_rules.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prices.to_csv(root / "prices.csv", index=False)
            signals.to_csv(root / "signals.csv", index=False)
            pd.DataFrame({"date": dates}).to_csv(root / "calendar.csv", index=False)
            (root / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
            args = [sys.executable, str(script), "--prices", str(root / "prices.csv"),
                    "--signals", str(root / "signals.csv"), "--calendar", str(root / "calendar.csv"),
                    "--evidence", str(root / "evidence.json"), "--output", str(root / "runs"),
                    "--train-end", str(dates[9].date()), "--validation-end", str(dates[19].date()),
                    "--test-end", str(dates[29].date()), "--label-horizon", "3"]
            result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            record = next((root / "runs").glob("*/evaluation.json"))
            stored = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(stored["split"]["status"], "OK")
            self.assertEqual(stored["split"]["label_horizon"], 3)
            self.assertIn("尚未完成分区外评价", " ".join(stored["limitations"]))


if __name__ == "__main__":
    unittest.main()
