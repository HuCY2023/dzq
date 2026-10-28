import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.diagnosis import _us_crowding_item, diagnose_position


class ExternalDiagnosisTests(unittest.TestCase):
    def test_stale_values_do_not_trigger_global_red_light(self):
        snap = {"as_of": "2026-10-27", "schema_version": 2, "margin_m2_pct": 6.2,
                "component_status": {"margin_m2": {"status": "stale"}}}
        result = diagnose_position({"us_crowding": snap}, heavy=False, as_of="2026-10-27")
        flag = next(f for f in result["red_light"]["flags"] if f["id"] == "global")
        self.assertIsNone(flag["on"])
        self.assertNotIn("6.2%", next(i for i in result["items"] if i["id"] == "us_crowding")["note"])

    def test_current_components_report_correct_denominator_and_units(self):
        snap = {"schema_version": 2, "margin_m2_pct": 6.2, "vix_pct": 70,
                "sox_60d_ret": 31, "shiller_cape": 36, "hy_ig_spread": 2.5,
                "hy_ig_pct": 90, "crowding_score": 5, "verdict": "高风险",
                "component_status": {key: {"status": "available"} for key in
                                     ["margin_m2", "vix", "sox", "cape", "credit"]}}
        item = _us_crowding_item(snap)
        self.assertIn("5/5", item["note"])
        self.assertIn("百分点", item["note"])
        self.assertNotIn("/6", item["note"])

    def test_failed_refresh_is_visible_and_unverified_term_structure_hidden(self):
        snap = {"schema_version": 2, "margin_m2_pct": 5.1, "vix_vxv_ratio": 1.2,
                "refresh_failed": True, "refresh_error": "外盘刷新无结果", "cache_age_hours": 5,
                "component_status": {"margin_m2": {"status": "available", "observation_date": "2026-08-01"}}}
        item = _us_crowding_item(snap)
        self.assertIn("更新失败", item["note"])
        self.assertIn("2026-08", item["note"])
        self.assertIn("5", item["note"])
        self.assertIn("外盘刷新无结果", item["note"])
        self.assertNotIn("VIX/VXV", item["note"])

    def test_empty_failed_refresh_displays_reason_without_claiming_a_cache(self):
        item = _us_crowding_item({"refresh_failed": True, "refresh_error": "来源无结果",
                                  "cache_state": "unavailable", "component_status": {}})
        self.assertIn("来源无结果", item["note"])
        self.assertNotIn("沿用缓存", item["note"])


if __name__ == "__main__":
    unittest.main()
