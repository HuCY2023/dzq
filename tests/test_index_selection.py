import sys
import tempfile
import unittest
import importlib.util
from pathlib import Path
from unittest.mock import patch

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle import store
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
        with patch("sys.argv", ["build_web_report", "--refresh"]), patch(
            "data_source.dp_cycle.gui_bundle.load_dp_cycle_gui_bundle", return_value=bundle
        ), patch("data_source.dp_cycle.gui_bundle.bundle_to_web_payload", return_value={}), patch(
            "data_source.dp_cycle.gui_bundle.write_web_snapshot", return_value="snapshot"
        ) as write:
            self.assertNotEqual(script.main(), 0)
            write.assert_called_once()


if __name__ == "__main__":
    unittest.main()
