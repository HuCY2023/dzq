import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from scripts import import_etf_flow


CODES = ("510300", "510310", "510330", "159919")
ORIGIN = "akshare.tool_trade_date_hist_sina"


def rows(day="2024-06-04", previous_day="2024-06-03"):
    return [{"date": day, "code": code, "previous_date": previous_day,
             "shares": "100000000.0", "previous_shares": "100000000.0",
             "split_factor": "1", "nav": "2.0000", "status": "ok",
             "shares_source": "original-share-source", "split_source": "original-split-source"}
            for code in CODES]


class ImportEtfFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input_path = self.root / "components.csv"
        self.calendar_path = self.root / "trade_calendar.csv"
        self.reference_path = self.root / "index-crosscheck.csv"
        self.output_root = self.root / "output" / "user" / "dp_cycle"
        self.write_input(rows())
        self.write_calendar(["2024-05-31", "2024-06-03", "2024-06-04", "2024-06-05"])
        self.write_reference(["2024-05-31", "2024-06-03", "2024-06-04", "2024-06-05"])

    def write_input(self, values):
        pd.DataFrame(values).to_csv(self.input_path, index=False)

    def write_calendar(self, dates, origin=ORIGIN):
        pd.DataFrame({"date": dates, "origin": [origin] * len(dates)}).to_csv(self.calendar_path, index=False)

    def write_reference(self, dates):
        pd.DataFrame({"date": dates, "primary_close": 4000, "tencent": 4000}).to_csv(self.reference_path, index=False)

    def run_import(self, as_of="2024-06-04"):
        return import_etf_flow.run_import(self.input_path, self.calendar_path, self.output_root, as_of=as_of)

    def test_complete_input_preserves_source_and_exports_traceable_values(self):
        before = self.input_path.read_bytes()
        result = self.run_import()
        output = Path(result["output_dir"])
        raw = pd.read_csv(output / "source_components.csv", dtype=str, keep_default_na=False)
        computed = pd.read_csv(output / "components.csv", dtype={"code": str})
        daily = pd.read_csv(output / "daily.csv")
        saved = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(self.input_path.read_bytes(), before)
        self.assertEqual(set(raw["code"]), set(CODES))
        self.assertEqual(raw.iloc[0]["nav"], "2.0000")
        self.assertEqual(raw.iloc[0]["shares_source"], "original-share-source")
        self.assertEqual(raw.iloc[0]["split_source"], "original-split-source")
        self.assertEqual(raw.iloc[0]["status"], "ok")
        self.assertEqual(set(computed["source_status"]), {"ok"})
        self.assertEqual(daily.iloc[0]["comparable_count"], 4)
        self.assertEqual(daily.iloc[0]["flow_yi"], 0)
        self.assertEqual(saved["data_start"], "2024-06-04")
        self.assertEqual(saved["data_end"], "2024-06-04")
        self.assertEqual(saved["complete_days"], 1)
        self.assertEqual(saved["partial_days"], 0)
        self.assertTrue(saved["calendar"]["coverage_verified"])
        self.assertEqual(saved["calendar"]["source"], ORIGIN)
        self.assertEqual(saved["calendar"]["coverage_start"], "2024-05-31")
        self.assertEqual(saved["calendar"]["coverage_end"], "2024-06-04")
        self.assertEqual(saved["calendar"]["crosscheck_path"], str(self.reference_path))
        self.assertIn("历史可交易回测", " ".join(saved["limitations"]))

    def test_cutoff_filters_future_rows_and_non_basket_funds(self):
        extra = rows("2024-06-05", "2024-06-04")
        outside = rows()[0].copy()
        outside["code"] = "510500"
        self.write_input(rows() + extra + [outside])
        result = self.run_import()
        raw = pd.read_csv(Path(result["output_dir"]) / "source_components.csv", dtype=str)
        self.assertEqual(len(raw), 4)
        self.assertEqual(set(raw["date"]), {"2024-06-04"})
        self.assertEqual(result["data_end"], "2024-06-04")
        self.assertEqual(result["future_input_rows"], 4)

    def test_future_as_of_is_rejected(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "未来"):
            self.run_import(tomorrow)
        self.assertFalse(self.output_root.exists())

    def test_missing_fund_remains_partial_without_total(self):
        self.write_input(rows()[:-1])
        result = self.run_import()
        daily = pd.read_csv(Path(result["output_dir"]) / "daily.csv")
        self.assertEqual(result["complete_days"], 0)
        self.assertEqual(result["partial_days"], 1)
        self.assertEqual(daily.iloc[0]["comparable_count"], 3)
        self.assertTrue(pd.isna(daily.iloc[0]["flow_yi"]))

    def test_missing_nav_remains_partial_and_source_row_is_retained(self):
        source = rows()
        source[0]["nav"] = ""
        self.write_input(source)
        result = self.run_import()
        output = Path(result["output_dir"])
        raw = pd.read_csv(output / "source_components.csv", dtype=str, keep_default_na=False)
        items = pd.read_csv(output / "components.csv", dtype={"code": str}).set_index("code")
        self.assertEqual(raw.iloc[0]["nav"], "")
        self.assertEqual(items.loc["510300", "status"], "missing_nav")
        self.assertEqual(result["partial_days"], 1)

    def test_missing_whole_trading_day_is_counted_as_partial(self):
        self.write_input(rows("2024-06-03", "2024-05-31") + rows("2024-06-05", "2024-06-04"))
        result = self.run_import(as_of="2024-06-05")
        daily = pd.read_csv(Path(result["output_dir"]) / "daily.csv").set_index("date")
        self.assertEqual(result["complete_days"], 2)
        self.assertEqual(result["partial_days"], 1)
        self.assertEqual(daily.loc["2024-06-04", "observed_count"], 0)
        self.assertTrue(pd.isna(daily.loc["2024-06-04", "flow_yi"]))

    def test_missing_verified_tail_is_partial_and_may_be_unpublished(self):
        result = self.run_import(as_of="2024-06-05")
        output = Path(result["output_dir"])
        daily = pd.read_csv(output / "daily.csv").set_index("date")
        items = pd.read_csv(output / "components.csv", dtype={"code": str})
        tail = items.loc[items["date"] == "2024-06-05"]
        self.assertIn("2024-06-05", daily.index)
        self.assertEqual(result["data_end"], "2024-06-04")
        self.assertEqual(result["as_of"], "2024-06-05")
        self.assertEqual(result["pending_tail_days"], 1)
        self.assertEqual(result["partial_days"], 1)
        self.assertEqual(daily.loc["2024-06-05", "status"], "partial")
        self.assertEqual(daily.loc["2024-06-05", "observed_count"], 0)
        self.assertTrue(pd.isna(daily.loc["2024-06-05", "flow_yi"]))
        self.assertEqual(len(tail), 4)
        self.assertEqual(set(tail["status"]), {"missing_fund"})
        self.assertIn("尚未发布", " ".join(result["limitations"]))

    def test_uncertified_calendar_does_not_invent_tail_dates(self):
        self.reference_path.unlink()
        result = self.run_import(as_of="2024-06-05")
        daily = pd.read_csv(Path(result["output_dir"]) / "daily.csv")
        self.assertEqual(list(daily["date"]), ["2024-06-04"])
        self.assertIsNone(result["pending_tail_days"])

    def test_snapshot_contains_actual_calendar_sources_and_absolute_paths(self):
        original_cwd = Path.cwd()
        calendar_bytes, reference_bytes = self.calendar_path.read_bytes(), self.reference_path.read_bytes()
        try:
            os.chdir(self.root)
            result = import_etf_flow.run_import(Path("components.csv"), Path("trade_calendar.csv"), Path("out"), as_of="2024-06-04")
        finally:
            os.chdir(original_cwd)
        calendar = result["calendar"]
        self.assertEqual(result["input"], str(self.input_path.resolve()))
        self.assertEqual(calendar["path"], str(self.calendar_path.resolve()))
        self.assertEqual(calendar["crosscheck_path"], str(self.reference_path.resolve()))
        self.assertEqual(Path(calendar["snapshot_path"]).read_bytes(), calendar_bytes)
        self.assertEqual(Path(calendar["crosscheck_snapshot_path"]).read_bytes(), reference_bytes)

    def test_snapshot_records_inputs_used_even_if_sources_change_during_import(self):
        calendar_bytes, reference_bytes = self.calendar_path.read_bytes(), self.reference_path.read_bytes()
        calculate = import_etf_flow.estimate_core_etf_flows

        def alter_sources(observations, *, trading_dates, share_events):
            self.calendar_path.write_text("changed", encoding="utf-8")
            self.reference_path.write_text("changed", encoding="utf-8")
            return calculate(observations, trading_dates=trading_dates, share_events=share_events)

        with patch.object(import_etf_flow, "estimate_core_etf_flows", side_effect=alter_sources):
            result = self.run_import()
        self.assertEqual(Path(result["calendar"]["snapshot_path"]).read_bytes(), calendar_bytes)
        self.assertEqual(Path(result["calendar"]["crosscheck_snapshot_path"]).read_bytes(), reference_bytes)

    def test_import_saves_the_event_table_actually_used(self):
        event_path = self.root / "events.csv"
        original = (import_etf_flow.ROOT / "research" / "DP" / "data" / "etf_share_events.csv").read_bytes()
        event_path.write_bytes(original)
        calculate = import_etf_flow.estimate_core_etf_flows

        def alter_events(observations, *, trading_dates, share_events):
            event_path.write_text("changed", encoding="utf-8")
            return calculate(observations, trading_dates=trading_dates, share_events=share_events)

        with patch.object(import_etf_flow, "EVENTS_PATH", event_path), patch.object(
            import_etf_flow, "estimate_core_etf_flows", side_effect=alter_events
        ):
            result = self.run_import()
        self.assertEqual(Path(result["share_events"]["snapshot_path"]).read_bytes(), original)
        self.assertEqual(Path(result["share_events"]["source"]), event_path)

    def test_missing_or_unrecorded_calendar_origin_is_not_certified(self):
        self.write_calendar(["2024-05-31", "2024-06-03", "2024-06-04"], origin="")
        result = self.run_import()
        items = pd.read_csv(Path(result["output_dir"]) / "components.csv")
        self.assertFalse(result["calendar"]["coverage_verified"])
        self.assertEqual(set(items["status"]), {"calendar_unavailable"})
        self.assertEqual(result["partial_days"], 1)

    def test_sparse_calendar_missing_observed_predecessor_is_not_certified(self):
        self.write_calendar(["2024-05-31", "2024-06-04", "2024-06-05"])
        result = self.run_import()
        items = pd.read_csv(Path(result["output_dir"]) / "components.csv")
        self.assertFalse(result["calendar"]["coverage_verified"])
        self.assertEqual(set(items["status"]), {"calendar_unavailable"})
        self.assertEqual(result["partial_days"], 1)

    def test_sparse_calendar_cannot_be_confirmed_just_by_matching_sparse_input(self):
        self.write_input(rows("2024-06-04", "2024-05-31"))
        self.write_calendar(["2024-05-31", "2024-06-04"])
        result = self.run_import()
        items = pd.read_csv(Path(result["output_dir"]) / "components.csv")
        self.assertFalse(result["calendar"]["coverage_verified"])
        self.assertEqual(set(items["status"]), {"calendar_unavailable"})

    def test_missing_independent_crosscheck_keeps_data_partial(self):
        self.reference_path.unlink()
        result = self.run_import()
        self.assertFalse(result["calendar"]["coverage_verified"])
        self.assertEqual(result["partial_days"], 1)
        self.assertTrue((Path(result["output_dir"]) / "source_components.csv").exists())

    def test_days_before_crosscheck_coverage_are_not_comparable(self):
        self.write_reference(["2024-06-04", "2024-06-05"])
        result = self.run_import()
        items = pd.read_csv(Path(result["output_dir"]) / "components.csv")
        self.assertTrue(result["calendar"]["coverage_verified"])
        self.assertEqual(result["calendar"]["coverage_start"], "2024-06-04")
        self.assertEqual(set(items["status"]), {"calendar_unavailable"})

    def test_later_incomplete_crosscheck_does_not_affect_cutoff(self):
        reference = pd.read_csv(self.reference_path)
        reference.loc[reference["date"] == "2024-06-05", "tencent"] = None
        reference.to_csv(self.reference_path, index=False)
        result = self.run_import()
        self.assertTrue(result["calendar"]["coverage_verified"])
        self.assertEqual(result["complete_days"], 1)

    def test_later_calendar_origin_does_not_affect_cutoff(self):
        calendar = pd.read_csv(self.calendar_path)
        calendar.loc[calendar["date"] == "2024-06-05", "origin"] = ""
        calendar.to_csv(self.calendar_path, index=False)
        result = self.run_import()
        self.assertTrue(result["calendar"]["coverage_verified"])
        self.assertEqual(result["complete_days"], 1)

    def test_new_import_does_not_replace_an_earlier_snapshot(self):
        first = self.run_import()
        first_path = Path(first["output_dir"]) / "source_components.csv"
        content = first_path.read_bytes()
        second = self.run_import()
        self.assertNotEqual(first["output_dir"], second["output_dir"])
        self.assertEqual(first_path.read_bytes(), content)

    def test_cli_requires_input_and_prints_dates_and_limitations(self):
        with patch.object(import_etf_flow, "CALENDAR_PATH", self.calendar_path), \
             patch.object(import_etf_flow, "OUTPUT_ROOT", self.output_root), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            code = import_etf_flow.main(["--input", str(self.input_path), "--as-of", "2024-06-04"])
        self.assertEqual(code, 0)
        text = output.getvalue()
        for phrase in ("2024-06-04", "完整", "部分", "历史可交易回测"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
