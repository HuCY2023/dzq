import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("snapshot_report", ROOT / "scripts" / "build_web_report.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


class ReportSnapshotsTests(unittest.TestCase):
    def _run(self, root, *, score, fail_pdf=False, flow_source=None, change_events=False, change_disclosures=False):
        out = Path(root, "dated")
        store = Path(root, "store")
        events = Path(root, "etf_share_events.csv")
        if not events.exists():
            events.write_text("code,event_date,ratio\n510300,2024-01-01,2\n", encoding="utf-8")
        disclosures = Path(root, "etf_holder_disclosures.csv")
        if not disclosures.exists():
            disclosures.write_text("code,period_end\n159919,2026-06-30\n", encoding="utf-8")
        bundle = {"meta": {"weekly": {"ok": True}, "as_of": "2026-10-27",
                           "trade_calendar": {"origin": "test"}},
                  "weekly": pd.DataFrame({"date": ["2026-10-23"], "close": [100]}),
                  "trade_calendar": {"dates": ["2026-10-23"], "source": "test"},
                  "etf_holder_disclosures": {"as_of": "2026-10-27", "status": "partial"}}
        if flow_source is not None:
            bundle["etf_flow"] = {"source": {"snapshot": str(flow_source)}}
        diagnosis = {"as_of": "2026-10-27", "rule_version": "improved_v1", "score": score,
                     "data_availability": {"margin": {"observation_date": "2026-10-26", "status": "ok"}}}
        payload = {"diagnosis": diagnosis, "coverage_span": {"margin": {"last": "2026-10-26"}},
                   "coverage": {"margin": 4}}

        def fake_pdf(*, out_dir, **kwargs):
            if fail_pdf:
                raise RuntimeError("simulated PDF failure")
            pdf = Path(out_dir, "report.pdf")
            pdf.write_bytes(f"pdf {score}".encode())
            return {"pdf": str(pdf)}

        def fake_payload(*args, **kwargs):
            if change_events:
                events.write_text("changed during report", encoding="utf-8")
            if change_disclosures:
                disclosures.write_text("changed during report", encoding="utf-8")
            return payload

        with patch.object(script, "SHARE_EVENTS_PATH", events), patch.object(script, "HOLDER_DISCLOSURES_PATH", disclosures), patch("sys.argv", ["build_web_report", "--pdf"]), patch(
            "output_paths.dp_cycle_dir", return_value=str(out)
        ), patch("output_paths.dp_cycle_store_dir", return_value=str(store)), patch(
            "data_source.dp_cycle.gui_bundle.load_dp_cycle_gui_bundle", return_value=bundle
        ), patch("data_source.dp_cycle.gui_bundle.bundle_to_web_payload", side_effect=fake_payload), patch(
            "data_source.dp_cycle.pipeline.run_dp_cycle_pipeline", side_effect=fake_pdf
        ):
            return script.main()

    def test_two_runs_are_preserved_with_shared_diagnosis_and_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(tmp, score=1), 0)
            self.assertEqual(self._run(tmp, score=2), 0)
            runs = sorted(Path(tmp, "dated", "runs").iterdir())
            self.assertEqual(len(runs), 2)
            self.assertTrue(all((p / "web_snapshot" / "index.html").is_file() for p in runs))
            manifests = [json.loads((p / "manifest.json").read_text(encoding="utf-8")) for p in runs]
            self.assertEqual([m["diagnosis"]["score"] for m in manifests], [1, 2])
            self.assertEqual(manifests[-1]["rule_version"], "improved_v1")
            self.assertEqual(manifests[-1]["data_availability"]["margin"]["observation_date"], "2026-10-26")
            self.assertTrue((runs[0] / "inputs" / "weekly.csv").is_file())
            self.assertTrue((runs[0] / "inputs" / "trade_calendar.json").is_file())
            archived_disclosures = runs[0] / "inputs" / "etf_holder_disclosures.csv"
            self.assertEqual(archived_disclosures.read_bytes(), Path(tmp, "etf_holder_disclosures.csv").read_bytes())
            self.assertEqual(json.loads((runs[0] / "inputs" / "etf_holder_disclosures.json").read_text(encoding="utf-8"))["status"], "partial")
            archived_events = runs[0] / "inputs" / "etf_share_events.csv"
            self.assertEqual(archived_events.read_bytes(), Path(tmp, "etf_share_events.csv").read_bytes())
            self.assertEqual(manifests[0]["artifacts"]["etf_share_events"], str(archived_events))
            Path(tmp, "etf_share_events.csv").write_text("changed later", encoding="utf-8")
            self.assertIn("510300", archived_events.read_text(encoding="utf-8"))
            self.assertIn('"score": 2', Path(tmp, "store", "web_snapshot", "index.html").read_text(encoding="utf-8"))
            for run in runs:
                self.assertTrue((Path(tmp, "store", "web_snapshot", "versions", run.name) / "app.js").is_file())
            previous = runs[1] / "previous_entries" / "web_snapshot"
            self.assertIn('"score": 1', (previous / "index.html").read_text(encoding="utf-8"))
            for name in ("app.js", "styles.css", "lightweight-charts.js"):
                self.assertTrue((previous / "versions" / runs[0].name / name).is_file())
            self.assertEqual((runs[1] / "previous_entries" / "latest-大周期五项指标_长期跟踪.pdf").read_bytes(), b"pdf 1")

    def test_report_and_import_event_tables_are_archived_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            imported = Path(tmp, "imported")
            imported.mkdir()
            (imported / "metadata.json").write_text("{}", encoding="utf-8")
            (imported / "etf_share_events.csv").write_text("import-time events", encoding="utf-8")
            self.assertEqual(self._run(tmp, score=1, flow_source=imported), 0)
            run = next((Path(tmp, "dated") / "runs").iterdir())
            self.assertIn("510300", (run / "inputs" / "etf_share_events.csv").read_text(encoding="utf-8"))
            archived = run / "inputs" / "etf_flow" / "etf_share_events.csv"
            self.assertEqual(archived.read_text(encoding="utf-8"), "import-time events")
            (imported / "etf_share_events.csv").write_text("new events", encoding="utf-8")
            self.assertEqual(archived.read_text(encoding="utf-8"), "import-time events")

    def test_older_import_without_event_table_is_still_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            imported = Path(tmp, "older-import")
            imported.mkdir()
            (imported / "metadata.json").write_text("{}", encoding="utf-8")
            self.assertEqual(self._run(tmp, score=1, flow_source=imported), 0)
            run = next((Path(tmp, "dated") / "runs").iterdir())
            self.assertTrue((run / "inputs" / "etf_flow" / "metadata.json").is_file())
            self.assertFalse((run / "inputs" / "etf_flow" / "etf_share_events.csv").exists())

    def test_event_table_change_during_report_preserves_existing_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(tmp, score=1), 0)
            html = Path(tmp, "store", "web_snapshot", "index.html")
            pdf = Path(tmp, "store", "大周期五项指标_长期跟踪.pdf")
            before = html.read_bytes(), pdf.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "ETF 份额事件表在报告生成期间发生变化"):
                self._run(tmp, score=2, change_events=True)
            self.assertEqual((html.read_bytes(), pdf.read_bytes()), before)

    def test_disclosure_table_change_during_report_preserves_existing_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(tmp, score=1), 0)
            html = Path(tmp, "store", "web_snapshot", "index.html")
            pdf = Path(tmp, "store", "大周期五项指标_长期跟踪.pdf")
            before = html.read_bytes(), pdf.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "持有人披露表在报告生成期间发生变化"):
                self._run(tmp, score=2, change_disclosures=True)
            self.assertEqual((html.read_bytes(), pdf.read_bytes()), before)

    def test_previous_legacy_report_and_pdfs_remain_openable(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp, "store", "web_snapshot")
            web.mkdir(parents=True)
            old_html = '<link href="styles.css"><script src="lightweight-charts.js"></script><script src="app.js"></script>'
            (web / "index.html").write_text(old_html, encoding="utf-8")
            for name in ("app.js", "styles.css", "lightweight-charts.js"):
                (web / name).write_text("old " + name, encoding="utf-8")
            dated = Path(tmp, "dated")
            dated.mkdir()
            (dated / "大周期五项指标_长期跟踪.pdf").write_bytes(b"dated old")
            (web.parent / "大周期五项指标_长期跟踪.pdf").write_bytes(b"latest old")

            self.assertEqual(self._run(tmp, score=1), 0)
            run = next((dated / "runs").iterdir())
            previous = run / "previous_entries"
            self.assertEqual((previous / "web_snapshot" / "index.html").read_text(encoding="utf-8"), old_html)
            for name in ("app.js", "styles.css", "lightweight-charts.js"):
                self.assertEqual((previous / "web_snapshot" / name).read_text(encoding="utf-8"), "old " + name)
            self.assertEqual((previous / "dated-大周期五项指标_长期跟踪.pdf").read_bytes(), b"dated old")
            self.assertEqual((previous / "latest-大周期五项指标_长期跟踪.pdf").read_bytes(), b"latest old")

    def test_pdf_failure_leaves_previous_stable_entries_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(tmp, score=1), 0)
            html = Path(tmp, "store", "web_snapshot", "index.html")
            pdf = Path(tmp, "store", "大周期五项指标_长期跟踪.pdf")
            before = (html.read_bytes(), pdf.read_bytes())
            with self.assertRaisesRegex(RuntimeError, "simulated PDF failure"):
                self._run(tmp, score=2, fail_pdf=True)
            self.assertEqual((html.read_bytes(), pdf.read_bytes()), before)

    def test_late_html_replace_failure_restores_previous_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(tmp, score=1), 0)
            html = Path(tmp, "store", "web_snapshot", "index.html")
            pdf = Path(tmp, "store", "大周期五项指标_长期跟踪.pdf")
            dated_pdf = Path(tmp, "dated", "大周期五项指标_长期跟踪.pdf")
            before = (html.read_bytes(), pdf.read_bytes(), dated_pdf.read_bytes())
            replace = os.replace

            def blocked(source, target):
                if Path(target) == html:
                    raise OSError("simulated locked HTML")
                return replace(source, target)

            with patch.object(script.os, "replace", side_effect=blocked), self.assertRaisesRegex(OSError, "locked HTML"):
                self._run(tmp, score=2)
            self.assertEqual((html.read_bytes(), pdf.read_bytes(), dated_pdf.read_bytes()), before)

    def test_missing_web_asset_does_not_replace_existing_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(tmp, score=1), 0)
            html = Path(tmp, "store", "web_snapshot", "index.html")
            before = html.read_bytes()
            archive = Path(tmp, "broken")
            archive.mkdir()
            (archive / "index.html").write_text("broken", encoding="utf-8")
            run = Path(tmp, "broken-run")
            run.mkdir()
            with self.assertRaisesRegex(RuntimeError, "缺少运行资源"):
                script._publish(run, archive, Path(tmp, "store"), Path(tmp, "dated"), None)
            self.assertEqual(html.read_bytes(), before)

    def test_rule_version_changes_invalidate_payload_cache(self):
        from data_source.dp_cycle import gui_bundle

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            gui_bundle, "_payload_cache_path", return_value=Path(tmp, "payload.json")
        ), patch.object(gui_bundle, "_db_mtime_ns", return_value=1):
            gui_bundle.save_web_payload_cache("sh000001", {"diagnosis": {"rule_version": "improved_v1"}})
            self.assertIsNotNone(gui_bundle.load_cached_web_payload("sh000001", rule_version="improved_v1"))
            self.assertIsNone(gui_bundle.load_cached_web_payload("sh000001", rule_version="author_v1"))

    def test_calendar_or_etf_event_change_invalidates_cache(self):
        from data_source.dp_cycle import gui_bundle

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            gui_bundle, "_payload_cache_path", return_value=Path(tmp, "payload.json")
        ), patch.object(gui_bundle, "_db_mtime_ns", return_value=1), patch.object(
            gui_bundle, "_other_dependency_mtimes", return_value={"trade_calendar": 1, "etf_events": 1}
        ) as versions:
            gui_bundle.save_web_payload_cache("sh000001", {"diagnosis": {"rule_version": "improved_v1"}})
            self.assertIsNotNone(gui_bundle.load_cached_web_payload("sh000001"))
            versions.return_value = {"trade_calendar": 2, "etf_events": 1}
            self.assertIsNone(gui_bundle.load_cached_web_payload("sh000001"))

    def test_selected_etf_files_individually_invalidate_real_cache(self):
        from data_source.dp_cycle import gui_bundle

        with tempfile.TemporaryDirectory() as tmp, patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch.object(
            gui_bundle, "_db_mtime_ns", return_value=1
        ), patch.object(gui_bundle, "_us_snapshot_mtime_ns", return_value=1):
            imports = Path(tmp, "etf_flow_imports")
            old, selected = imports / "old", imports / "selected"
            old.mkdir(parents=True)
            selected.mkdir()
            names = ("metadata.json", "daily.csv", "components.csv", "trade_calendar_source.csv", "etf_share_events.csv")
            for name in names:
                (selected / name).write_text("fixture", encoding="utf-8")
                os.utime(selected / name, ns=(1_700_000_000_000_000_000,) * 2)
            (old / "metadata.json").write_text("fixture", encoding="utf-8")
            os.utime(old / "metadata.json", ns=(1_600_000_000_000_000_000,) * 2)
            # Metadata is the newest within the imports, but all fixture times are
            # earlier than the directory time. A single max timestamp misses changes.
            os.utime(selected / "metadata.json", ns=(1_700_000_100_000_000_000,) * 2)

            dependencies = gui_bundle._other_dependency_mtimes()
            for name in names:
                self.assertEqual(dependencies[f"etf_flow/selected/{name}"], (selected / name).stat().st_mtime_ns)
                gui_bundle.save_web_payload_cache("sh000001", {"diagnosis": {"rule_version": "improved_v1"}})
                self.assertIsNotNone(gui_bundle.load_cached_web_payload("sh000001"))
                timestamp = (selected / name).stat().st_mtime_ns + 1_000_000_000
                os.utime(selected / name, ns=(timestamp, timestamp))
                self.assertIsNone(gui_bundle.load_cached_web_payload("sh000001"), name)

    def test_newer_etf_snapshot_changes_cache_selection(self):
        from data_source.dp_cycle import gui_bundle

        with tempfile.TemporaryDirectory() as tmp, patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch.object(
            gui_bundle, "_db_mtime_ns", return_value=1
        ), patch.object(gui_bundle, "_us_snapshot_mtime_ns", return_value=1):
            imports = Path(tmp, "etf_flow_imports")
            first, newer = imports / "first", imports / "newer"
            first.mkdir(parents=True)
            newer.mkdir()
            (first / "metadata.json").write_text("{}", encoding="utf-8")
            os.utime(first / "metadata.json", ns=(1_700_000_000_000_000_000,) * 2)
            gui_bundle.save_web_payload_cache("sh000001", {"diagnosis": {"rule_version": "improved_v1"}})
            self.assertIsNotNone(gui_bundle.load_cached_web_payload("sh000001"))
            (newer / "metadata.json").write_text("{}", encoding="utf-8")
            os.utime(newer / "metadata.json", ns=(1_700_000_100_000_000_000,) * 2)
            self.assertIsNone(gui_bundle.load_cached_web_payload("sh000001"))

    def test_calendar_copy_added_to_selected_snapshot_invalidates_cache(self):
        from data_source.dp_cycle import gui_bundle

        with tempfile.TemporaryDirectory() as tmp, patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch.object(
            gui_bundle, "_db_mtime_ns", return_value=1
        ), patch.object(gui_bundle, "_us_snapshot_mtime_ns", return_value=1):
            selected = Path(tmp, "etf_flow_imports", "selected")
            selected.mkdir(parents=True)
            (selected / "metadata.json").write_text("{}", encoding="utf-8")
            gui_bundle.save_web_payload_cache("sh000001", {"diagnosis": {"rule_version": "improved_v1"}})
            self.assertIsNotNone(gui_bundle.load_cached_web_payload("sh000001"))
            (selected / "trade_calendar_source.csv").write_text("date\n2024-06-04\n", encoding="utf-8")
            self.assertIsNone(gui_bundle.load_cached_web_payload("sh000001"))


if __name__ == "__main__":
    unittest.main()
