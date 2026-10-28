import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from data_source.dp_cycle import etf_flow
from scripts import import_etf_flow


CODES = ("510300", "510310", "510330", "159919")


class EtfFlowSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.import_root = self.root / "imports"

    def snapshot(self, name, days, *, snapshot_as_of="2024-06-05", mtime=100):
        path = self.import_root / name
        path.mkdir(parents=True)
        components, daily = [], []
        for day, statuses in days:
            for index, (code, status) in enumerate(zip(CODES, statuses)):
                observed = status != "missing_fund"
                components.append({"date": day, "code": code, "name": f"fund-{code}",
                                   "observed": observed, "status": status, "source_status": "ok" if observed else None,
                                   "shares": 100_000_000 if observed else None,
                                   "previous_shares": 100_000_000 if observed else None,
                                   "split_factor": 1 if observed else None, "nav": 2 if observed else None,
                                   "flow_yi": index + 1 if status == "ok" else None,
                                   "shares_source": f"source-{code}" if observed else None})
            observed_count = sum(status != "missing_fund" for status in statuses)
            comparable_count = sum(status == "ok" for status in statuses)
            complete = comparable_count == 4
            daily.append({"date": day, "observed_count": observed_count, "comparable_count": comparable_count,
                          "expected_count": 4, "complete": complete, "status": "ok" if complete else "partial",
                          "flow_yi": 10 if complete else None})
        pd.DataFrame(components).to_csv(path / "components.csv", index=False)
        pd.DataFrame(daily).to_csv(path / "daily.csv", index=False)
        meta = {"as_of": snapshot_as_of, "input": str(self.root / "input.csv"),
                "calendar": {"source": "akshare.tool_trade_date_hist_sina", "coverage_verified": True,
                             "coverage_start": "2024-06-03", "coverage_end": snapshot_as_of},
                "limitations": ["输入净值同日假设"]}
        (path / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        os.utime(path / "metadata.json", (mtime, mtime))
        return path

    def load(self, as_of="2024-06-05"):
        return etf_flow.load_latest_etf_flow_snapshot(self.import_root, as_of=as_of)

    def test_no_snapshots_returns_unavailable_without_creating_directory(self):
        self.assertTrue(callable(getattr(etf_flow, "load_latest_etf_flow_snapshot", None)))
        result = self.load(as_of="2024-06-04")
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["flow_yi"])
        self.assertFalse(self.import_root.exists())

    def test_old_snapshot_does_not_claim_its_event_version_was_saved(self):
        path = self.snapshot("old", [("2024-06-04", ["ok"] * 4)])
        (path / "etf_share_events.csv").write_text("added later", encoding="utf-8")
        result = self.load(as_of="2024-06-04")
        self.assertFalse(result["source"]["events_recorded"])
        self.assertIn("事件表版本不可追溯", " ".join(result["limitations"]))

    def test_latest_snapshot_is_selected_by_metadata_time_not_directory_name(self):
        self.snapshot("zzz-old", [("2024-06-03", ["ok"] * 4)], mtime=100)
        newest = self.snapshot("aaa-new", [("2024-06-04", ["ok"] * 4)], mtime=200)
        result = self.load(as_of="2024-06-04")
        self.assertEqual(result["source"]["snapshot"], str(newest.resolve()))
        self.assertEqual(result["date"], "2024-06-04")
        self.assertEqual(result["latest_observation_date"], "2024-06-04")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["flow_yi"], 10)
        self.assertEqual([item["code"] for item in result["components"]], list(CODES))
        self.assertEqual(result["coverage"]["comparable"], 4)
        self.assertIn("metadata.json", result["snapshot_selection"])
        self.assertIn("历史可交易回测", " ".join(result["limitations"]))
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_equal_metadata_times_use_path_as_deterministic_tiebreaker(self):
        self.snapshot("aaa", [("2024-06-04", ["ok"] * 4)], mtime=100)
        newest = self.snapshot("zzz", [("2024-06-04", ["ok"] * 4)], mtime=100)
        self.assertEqual(self.load(as_of="2024-06-04")["source"]["snapshot"], str(newest.resolve()))

    def test_partial_tail_never_reuses_previous_complete_day(self):
        self.snapshot("one", [("2024-06-04", ["ok"] * 4), ("2024-06-05", ["missing_fund"] * 4)])
        result = self.load()
        self.assertEqual(result["date"], "2024-06-05")
        self.assertEqual(result["latest_observation_date"], "2024-06-04")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["observed"], 0)
        self.assertEqual(result["coverage"]["comparable"], 0)
        self.assertIsNone(result["flow_yi"])
        self.assertEqual({item["status"] for item in result["components"]}, {"missing_fund"})
        self.assertTrue(all(item["flow_yi"] is None for item in result["components"]))
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_older_snapshot_with_known_missing_tail_does_not_fall_back(self):
        self.snapshot("one", [("2024-06-04", ["ok"] * 4)], snapshot_as_of="2024-06-05")
        result = self.load()
        self.assertEqual(result["date"], "2024-06-05")
        self.assertEqual(result["latest_observation_date"], "2024-06-04")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["observed"], 0)
        self.assertIsNone(result["flow_yi"])

    def test_latest_partial_actual_day_retains_observed_funds_without_total(self):
        self.snapshot("one", [("2024-06-04", ["ok"] * 4),
                              ("2024-06-05", ["ok", "ok", "missing_nav", "missing_fund"])])
        result = self.load()
        self.assertEqual(result["date"], "2024-06-05")
        self.assertEqual(result["latest_observation_date"], "2024-06-05")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["observed"], 3)
        self.assertEqual(result["coverage"]["comparable"], 2)
        self.assertIsNone(result["flow_yi"])
        by_code = {item["code"]: item for item in result["components"]}
        self.assertEqual(by_code["510300"]["flow_yi"], 1)
        self.assertEqual(by_code["510330"]["status"], "missing_nav")
        self.assertIsNone(by_code["159919"]["shares"])

    def test_as_of_filters_later_observations_and_does_not_imply_backtest(self):
        self.snapshot("one", [("2024-06-04", ["ok"] * 4), ("2024-06-05", ["missing_fund"] * 4)])
        result = self.load(as_of="2024-06-04")
        self.assertEqual(result["date"], "2024-06-04")
        self.assertEqual(result["latest_observation_date"], "2024-06-04")
        self.assertEqual(result["flow_yi"], 10)
        self.assertEqual(result["snapshot_as_of"], "2024-06-05")
        self.assertIn("历史版本", " ".join(result["limitations"]))

    def test_no_data_by_cutoff_is_unavailable(self):
        self.snapshot("one", [("2024-06-04", ["ok"] * 4)])
        result = self.load(as_of="2024-06-03")
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["date"])
        self.assertIsNone(result["latest_observation_date"])

    def test_later_as_of_marks_snapshot_coverage_unknown(self):
        self.snapshot("one", [("2024-06-04", ["ok"] * 4)], snapshot_as_of="2024-06-04")
        result = self.load(as_of="2024-06-05")
        self.assertEqual(result["date"], "2024-06-04")
        self.assertEqual(result["flow_yi"], 10)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["coverage"]["snapshot_covers_as_of"])
        self.assertEqual(result["snapshot_as_of"], "2024-06-04")

    def test_snapshot_as_of_does_not_prove_calendar_coverage_through_it(self):
        path = self.snapshot("one", [("2024-06-04", ["ok"] * 4)], snapshot_as_of="2024-06-05")
        metadata_path = path / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["calendar"]["coverage_end"] = "2024-06-04"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        result = self.load(as_of="2024-06-05")
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["coverage"]["calendar_covers_as_of"])
        self.assertFalse(result["coverage"]["snapshot_covers_as_of"])
        self.assertEqual(result["date"], "2024-06-04")
        self.assertEqual(result["latest_observation_date"], "2024-06-04")
        self.assertIn("日历", result["reason"])
        self.assertIn("2024-06-04", result["reason"])

    def test_calendar_proves_non_trading_cutoff_when_coverage_extends_beyond_it(self):
        path = self.snapshot("one", [("2024-06-05", ["ok"] * 4)], snapshot_as_of="2024-06-07")
        pd.DataFrame({"date": ["2024-06-03", "2024-06-04", "2024-06-05", "2024-06-07"]}).to_csv(
            path / "trade_calendar_source.csv", index=False)
        result = self.load(as_of="2024-06-06")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["coverage"]["calendar_covers_as_of"])
        self.assertTrue(result["coverage"]["snapshot_covers_as_of"])
        self.assertEqual(result["date"], "2024-06-05")

    def test_covered_interval_without_calendar_copy_cannot_prove_non_trading_cutoff(self):
        self.snapshot("one", [("2024-06-05", ["ok"] * 4)], snapshot_as_of="2024-06-07")
        result = self.load(as_of="2024-06-06")
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["coverage"]["calendar_covers_as_of"])
        self.assertFalse(result["coverage"]["snapshot_covers_as_of"])

    def test_broken_newest_snapshot_does_not_silently_fall_back(self):
        self.snapshot("old", [("2024-06-04", ["ok"] * 4)], mtime=100)
        broken = self.snapshot("new", [("2024-06-05", ["ok"] * 4)], mtime=200)
        (broken / "daily.csv").unlink()
        result = self.load()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["source"]["snapshot"], str(broken.resolve()))
        self.assertIsNone(result["flow_yi"])

    def test_incomplete_component_rows_never_become_complete(self):
        path = self.snapshot("one", [("2024-06-04", ["ok"] * 4)])
        components = pd.read_csv(path / "components.csv", dtype={"code": str})
        components = components[components["code"] != "159919"]
        components.to_csv(path / "components.csv", index=False)
        result = self.load(as_of="2024-06-04")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["comparable"], 3)
        self.assertIsNone(result["flow_yi"])
        self.assertEqual(result["components"][-1]["status"], "snapshot_component_missing")

    def test_zero_flow_is_retained_as_a_real_zero(self):
        path = self.snapshot("one", [("2024-06-04", ["ok"] * 4)])
        daily = pd.read_csv(path / "daily.csv")
        daily["flow_yi"] = 0
        daily.to_csv(path / "daily.csv", index=False)
        components = pd.read_csv(path / "components.csv", dtype={"code": str})
        components["flow_yi"] = 0
        components.to_csv(path / "components.csv", index=False)
        result = self.load(as_of="2024-06-04")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["flow_yi"], 0)
        self.assertTrue(all(item["flow_yi"] == 0 for item in result["components"]))

    def test_imported_snapshot_with_partial_tail_can_be_read_without_changes(self):
        input_path = self.root / "components.csv"
        calendar_path = self.root / "trade_calendar.csv"
        reference_path = self.root / "index-crosscheck.csv"
        source = [{"date": "2024-06-04", "code": code, "previous_date": "2024-06-03",
                   "shares": 100_000_000, "previous_shares": 100_000_000,
                   "split_factor": 1, "nav": 2, "status": "ok"} for code in CODES]
        pd.DataFrame(source).to_csv(input_path, index=False)
        days = ["2024-05-31", "2024-06-03", "2024-06-04", "2024-06-05"]
        pd.DataFrame({"date": days, "origin": "akshare.tool_trade_date_hist_sina"}).to_csv(calendar_path, index=False)
        pd.DataFrame({"date": days, "primary_close": 4000, "tencent": 4000}).to_csv(reference_path, index=False)
        snapshot = import_etf_flow.run_import(input_path, calendar_path, self.import_root, as_of="2024-06-05")
        before = (Path(snapshot["output_dir"]) / "metadata.json").read_bytes()
        result = self.load()
        self.assertEqual(result["date"], "2024-06-05")
        self.assertEqual(result["latest_observation_date"], "2024-06-04")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["comparable"], 0)
        self.assertEqual((Path(snapshot["output_dir"]) / "metadata.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
