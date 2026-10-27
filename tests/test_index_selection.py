import sys
import tempfile
import unittest
import importlib.util
import contextlib
import io
from pathlib import Path
from unittest.mock import patch

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle import store
from data_source.dp_cycle import gui_bundle
from data_source.dp_cycle.gui_bundle import load_dp_cycle_gui_bundle


class IndexSelectionTests(unittest.TestCase):
    def test_missing_requested_index_is_not_replaced_by_another(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp, "test.duckdb"))
            con = duckdb.connect(db)
            store.init_schema(con)
            con.execute("INSERT INTO index_weekly (date, symbol, close) VALUES ('2026-10-23', 'sh000001', 4000)")
            con.close()
            store._invalidate_load_cache()
            with patch.object(store, "db_path", return_value=db), patch("output_paths.dp_cycle_store_dir", return_value=tmp):
                result = load_dp_cycle_gui_bundle(index_symbol="sh000300", refresh=False)
        self.assertTrue(result["weekly"].empty)
        self.assertEqual(result["meta"]["index_symbol"], "sh000300")
        self.assertIn("sh000300", result["meta"]["weekly"]["error"])

    def test_build_does_not_overwrite_snapshot_for_missing_index(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "build_web_report.py"
        spec = importlib.util.spec_from_file_location("build_web_report", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        with patch("sys.argv", ["build_web_report", "--index", "sh000300"]), patch(
            "data_source.dp_cycle.gui_bundle.load_dp_cycle_gui_bundle",
            return_value={"meta": {"weekly": {"ok": False, "error": "缺少 sh000300 周线"}}},
        ), patch("data_source.dp_cycle.gui_bundle.write_web_snapshot") as write:
            self.assertNotEqual(script.main(), 0)
            write.assert_not_called()

    def test_partial_refresh_is_reported_after_output_is_saved(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "build_web_report.py"
        spec = importlib.util.spec_from_file_location("build_web_report_refresh", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        bundle = {"meta": {"weekly": {"ok": True}, "ingest": {"ok": False, "margin": {"ok": False}}}}
        with tempfile.TemporaryDirectory() as tmp, patch("sys.argv", ["build_web_report", "--refresh"]), patch(
            "output_paths.dp_cycle_dir", return_value=str(Path(tmp, "dated"))
        ), patch("output_paths.dp_cycle_store_dir", return_value=str(Path(tmp, "store"))), patch(
            "data_source.dp_cycle.gui_bundle.load_dp_cycle_gui_bundle", return_value=bundle
        ), patch("data_source.dp_cycle.gui_bundle.bundle_to_web_payload", return_value={}), patch(
            "data_source.dp_cycle.gui_bundle.write_web_snapshot", wraps=gui_bundle.write_web_snapshot
        ) as write:
            self.assertNotEqual(script.main(), 0)
            write.assert_called_once()

    def test_calendar_and_us_refresh_failures_report_partial_after_saving(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "build_web_report.py"
        spec = importlib.util.spec_from_file_location("build_web_report_status", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        cases = (({"refresh_status": "failed"}, {"refresh_failed": False}, "交易日历"),
                 ({"refresh_status": "updated"}, {"refresh_failed": True}, "美股拥挤度"),
                 ({"refresh_status": "updated"}, {"ok": False, "error": "无本地快照"}, "美股拥挤度"),
                 ({"refresh_status": "updated"}, {"ok": False, "error": "计算异常"}, "美股拥挤度"),
                 ({"refresh_status": "updated"}, {"ok": False, "partial": True, "refresh_failed": False}, "美股拥挤度"))
        for calendar, crowding, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                bundle = {"meta": {"weekly": {"ok": True}, "ingest": {"ok": True},
                                   "trade_calendar": calendar, "us_crowding": crowding}}
                errors = io.StringIO()
                with patch("sys.argv", ["build_web_report", "--refresh"]), patch(
                    "output_paths.dp_cycle_dir", return_value=str(Path(tmp, "dated"))
                ), patch("output_paths.dp_cycle_store_dir", return_value=str(Path(tmp, "store"))), patch(
                    "data_source.dp_cycle.gui_bundle.load_dp_cycle_gui_bundle", return_value=bundle
                ), patch("data_source.dp_cycle.gui_bundle.bundle_to_web_payload", return_value={}), contextlib.redirect_stderr(errors):
                    self.assertEqual(script.main(), 2)
                self.assertIn(expected, errors.getvalue())
                self.assertTrue((Path(tmp, "store") / "web_snapshot" / "index.html").is_file())


if __name__ == "__main__":
    unittest.main()
