import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from data_source.dp_cycle.diagnosis import diagnose_position

spec = importlib.util.spec_from_file_location("dzq_audit", ROOT / "scripts" / "audit_path.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditEntryTest(unittest.TestCase):
    def test_empty_bundle_is_reported_without_false_zero_or_crash(self):
        bundle = {"margin": pd.DataFrame(columns=["date", "融资余额_亿元"])}
        output = io.StringIO()
        with patch.object(audit, "load_dp_cycle_gui_bundle", return_value=bundle), contextlib.redirect_stdout(output):
            result = audit.main()
        self.assertEqual(result, 0)
        self.assertNotIn("FAIL: score sum", output.getvalue())

    def test_failed_path_check_returns_nonzero(self):
        bundle = {"margin": pd.DataFrame(columns=["date", "融资余额_亿元"])}
        diagnosis = diagnose_position(bundle, heavy=False)
        diagnosis["score"] = 5
        with patch.object(audit, "load_dp_cycle_gui_bundle", return_value=bundle), patch.object(
            audit, "diagnose_position", return_value=diagnosis
        ), contextlib.redirect_stdout(io.StringIO()):
            result = audit.main()
        self.assertEqual(result, 1)

    def test_observation_only_etf_does_not_require_exit(self):
        bundle = {"margin": pd.DataFrame(columns=["date", "融资余额_亿元"])}
        diagnosis = diagnose_position(bundle, heavy=False)
        next(i for i in diagnosis["items"] if i["id"] == "national_team").update(
            {"zone": "high", "trend": "out", "status": "info"}
        )
        with patch.object(audit, "load_dp_cycle_gui_bundle", return_value=bundle), patch.object(
            audit, "diagnose_position", return_value=diagnosis
        ), contextlib.redirect_stdout(io.StringIO()):
            result = audit.main()
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
