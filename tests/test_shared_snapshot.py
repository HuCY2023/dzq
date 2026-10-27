import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class SharedSnapshotTests(unittest.TestCase):
    def test_report_passes_same_inputs_and_diagnosis_to_pdf(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "build_web_report.py"
        spec = importlib.util.spec_from_file_location("build_shared_snapshot", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        bundle = {"meta": {"weekly": {"ok": True}}}
        diagnosis = {"score": None, "items": []}
        with patch("sys.argv", ["build_web_report", "--pdf"]), patch(
            "data_source.dp_cycle.gui_bundle.load_dp_cycle_gui_bundle", return_value=bundle
        ), patch("data_source.dp_cycle.gui_bundle.bundle_to_web_payload", return_value={"diagnosis": diagnosis}), patch(
            "data_source.dp_cycle.gui_bundle.write_web_snapshot", return_value="snapshot"
        ), patch("data_source.dp_cycle.pipeline.run_dp_cycle_pipeline", return_value={"pdf": "report.pdf"}) as pipeline:
            self.assertEqual(script.main(), 0)
            self.assertIs(pipeline.call_args.kwargs["bundle"], bundle)
            self.assertIs(pipeline.call_args.kwargs["diagnosis"], diagnosis)

    def test_pipeline_does_not_reload_a_given_snapshot(self):
        from data_source.dp_cycle import pipeline

        diagnosis = {"items": [], "score": None,
                     "data_availability": {"national_team": {"coverage": {"complete": False}}}}
        bundle = {"meta": {"as_of": "2026-10-27"}}
        with tempfile.TemporaryDirectory() as tmp:
            research = Path(tmp, "research", "DP")
            research.mkdir(parents=True)
            (research / "大周期五项指标_长期跟踪.pdf").write_bytes(b"previous")
            with patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
                "output_paths.project_root", return_value=tmp
            ), patch.object(pipeline, "load_dp_cycle_gui_bundle") as load, patch.object(
                pipeline, "build_long_term_pdf", return_value=str(Path(tmp, "report.pdf"))
            ) as pdf, patch.object(pipeline.report, "write_pdf"), patch.object(pipeline.report, "write_meta"), patch.multiple(
                pipeline.report, plot_main_force=lambda *a: None, plot_national_team=lambda *a: None,
                plot_margin=lambda *a: None, plot_growth_value=lambda *a: None, plot_high_low=lambda *a: None,
            ):
                pipeline.run_dp_cycle_pipeline(out_dir=tmp, bundle=bundle, diagnosis=diagnosis)
                load.assert_not_called()
                self.assertIs(pdf.call_args.kwargs["tables"], bundle)
                self.assertIs(pdf.call_args.kwargs["diagnosis"], diagnosis)
                self.assertTrue(pdf.call_args.kwargs["meta"]["indicators"]["02_national_team"]["partial"])


if __name__ == "__main__":
    unittest.main()
