import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle import gui_bundle


class PayloadCacheTest(unittest.TestCase):
    def test_expired_previous_day_cache_is_not_used(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            gui_bundle, "_payload_cache_path", return_value=Path(directory) / "payload.json"
        ), patch.object(gui_bundle, "_db_mtime_ns", return_value=1):
            gui_bundle.save_web_payload_cache("sh000001", {"diagnosis": {"heavy": True}})
            path = Path(directory) / "payload.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["_cache"]["saved_at"] = str(date.today() - timedelta(days=1)) + "T23:50:00"
            path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertIsNone(gui_bundle.load_cached_web_payload("sh000001"))

    def test_new_external_snapshot_invalidates_cached_diagnosis(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            gui_bundle, "_payload_cache_path", return_value=Path(directory) / "payload.json"
        ), patch.object(gui_bundle, "_db_mtime_ns", return_value=1), patch.object(
            gui_bundle, "_us_snapshot_mtime_ns", create=True, return_value=2
        ) as mtime:
            gui_bundle.save_web_payload_cache("sh000001", {"diagnosis": {"heavy": True}})
            self.assertIsNotNone(gui_bundle.load_cached_web_payload("sh000001"))
            mtime.return_value = 3
            self.assertIsNone(gui_bundle.load_cached_web_payload("sh000001"))


if __name__ == "__main__":
    unittest.main()
