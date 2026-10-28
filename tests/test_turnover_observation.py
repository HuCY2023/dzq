import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from data_source.dp_cycle.turnover_observation import build_observation, load_latest_turnover_snapshot
from data_source.dp_cycle.gui_bundle import _clip_bundle_as_of, bundle_to_web_payload
from import_turnover_concentration import run_import


def fixture():
    amounts = [200_000_000, 100_000_000, 90_000_000, 60_000_000, 50_000_000]
    top = pd.DataFrame({"symbol": [f"sh60000{i}" for i in range(5)], "amount_yuan": amounts})
    summary = {"status": "DATA_PARTIAL", "generated_at": "2026-10-27T09:01:55",
               "as_of": "2026-10-26", "data_date": "2026-10-22",
               "latest": {"stocks": 100, "total": 10, "n1": 1, "n3": 3, "n5": 5,
                          "share1": 20, "share3": 39, "share5": 50, "abs5": 5}}
    return summary, top


class TurnoverObservationTests(unittest.TestCase):
    def test_current_cross_section_is_recalculated_and_labeled_partial(self):
        summary, top = fixture()
        row = build_observation(summary, top)
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["date"], "2026-10-22")
        self.assertEqual(row["source_generated_at"], "2026-10-27T09:01:55")
        self.assertEqual(row["stocks"], 100)
        self.assertEqual(row["top_counts"], {"1": 1, "3": 3, "5": 5})
        self.assertEqual(row["shares_pct"], {"1": 20.0, "3": 39.0, "5": 50.0})
        self.assertEqual(row["top5_amount_yi"], 5.0)
        self.assertIn("证券名录", row["note"])
        self.assertIn("不参与评分", row["note"])

    def test_mismatched_top5_cannot_be_published_as_a_valid_snapshot(self):
        summary, top = fixture()
        top.loc[0, "amount_yuan"] = 250_000_000
        with self.assertRaisesRegex(ValueError, "成交额"):
            build_observation(summary, top)

    def test_part_cannot_exceed_recorded_total(self):
        summary, top = fixture()
        summary["latest"].update(total=1, share1=200, share3=390, share5=500)
        with self.assertRaisesRegex(ValueError, "成交额"):
            build_observation(summary, top)

    def test_snapshot_is_saved_without_lookahead_and_keeps_original_sources(self):
        summary, top = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            source, root = Path(tmp, "source"), Path(tmp, "imports")
            source.mkdir()
            (source / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            top.to_csv(source / "latest-top5.csv", index=False)
            saved = run_import(source, root)
            self.assertTrue((Path(saved["snapshot"]) / "source_summary.json").is_file())
            self.assertTrue((Path(saved["snapshot"]) / "latest-top5.csv").is_file())
            current = load_latest_turnover_snapshot(root, as_of="2026-10-27")
            self.assertEqual(current["date"], "2026-10-22")
            self.assertEqual(current["status"], "partial")
            previous = load_latest_turnover_snapshot(root, as_of="2026-10-26")
            self.assertEqual(previous["status"], "unavailable")
            self.assertIsNone(previous["date"])

    def test_historical_clip_and_web_payload_keep_observation_separate(self):
        summary, top = fixture()
        row = build_observation(summary, top)
        clipped = _clip_bundle_as_of({"turnover_observation": row}, "2026-10-26")
        self.assertEqual(clipped["turnover_observation"]["status"], "unavailable")
        self.assertNotIn("stocks", clipped["turnover_observation"])
        payload = bundle_to_web_payload({"meta": {"as_of": "2026-10-27"},
                                         "turnover_observation": row}, heavy=False)
        self.assertEqual(payload["turnover_observation"], row)


if __name__ == "__main__":
    unittest.main()
