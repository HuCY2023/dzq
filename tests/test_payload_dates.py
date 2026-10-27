import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.gui_bundle import bundle_to_web_payload, load_dp_cycle_gui_bundle


class PayloadDatesTest(unittest.TestCase):
    def test_source_coverage_ends_at_observation_not_future_friday_label(self):
        bundle = {
            "weekly": pd.DataFrame({"date": ["2026-10-23", "2026-10-30"],
                                    "open": [1, 1], "high": [1, 1],
                                    "low": [1, 1], "close": [1, 1]}),
            "main_force_daily": pd.DataFrame({"date": ["2026-10-23", "2026-10-26"],
                                             "净额_亿元": [1, 2]}),
            "margin": pd.DataFrame({"date": ["2026-10-23", "2026-10-26"],
                                    "融资余额_亿元": [1, 2]}),
            "meta": {"as_of": "2026-10-27"},
        }
        payload = bundle_to_web_payload(bundle, heavy=False)
        self.assertEqual(payload["coverage_span"]["mf_week"]["last"], "2026-10-26")
        self.assertEqual(payload["coverage_span"]["mf_month"]["last"], "2026-10-26")
        self.assertEqual(payload["coverage_span"]["margin"]["last"], "2026-10-26")
        self.assertIn("周五", payload["coverage_note"]["weekly"])
        self.assertIn("2026-10-30", payload["axis_tail"])
        self.assertEqual(payload["weekly"][-1]["time"], "2026-10-23")
        self.assertEqual(payload["margin"][-1]["time"], "2026-10-30")
        self.assertEqual(payload["margin"][-1]["value"], 2)

    def test_historical_payload_does_not_draw_data_observed_after_analysis_date(self):
        bundle = {
            "weekly": pd.DataFrame({"date": ["2026-09-11", "2026-10-30"],
                                    "open": [1, 9], "high": [1, 9],
                                    "low": [1, 9], "close": [1, 9]}),
            "main_force_daily": pd.DataFrame({"date": ["2026-09-11", "2026-10-26"],
                                             "净额_亿元": [1, 900]}),
            "margin": pd.DataFrame({"date": ["2026-09-11", "2026-10-26"],
                                    "融资余额_亿元": [1, 900]}),
            "meta": {"as_of": "2026-09-15"},
        }
        payload = bundle_to_web_payload(bundle, heavy=False)
        self.assertEqual(payload["weekly"][-1]["close"], 1)
        self.assertEqual(payload["coverage_span"]["mf_week"]["last"], "2026-09-11")
        self.assertEqual(payload["coverage_span"]["margin"]["last"], "2026-09-11")
        self.assertEqual(len(bundle["margin"]), 2)

    def test_cached_trading_calendar_is_available_to_diagnosis(self):
        weekly = pd.DataFrame({"date": ["2026-10-23"], "open": [1.0], "high": [1.0],
                               "low": [1.0], "close": [1.0]})
        calendar = pd.DataFrame({"date": pd.to_datetime(["2026-10-19", "2026-10-23"])})
        meta = {"ok": True, "source": "cache", "status": "cached", "refresh_status": "not_requested",
                "start_date": "2026-10-19", "end_date": "2026-10-23"}
        with patch("data_source.dp_cycle.gui_bundle.load_from_db", return_value={"weekly": weekly}), patch(
            "data_source.dp_cycle.gui_bundle.table_counts", return_value={}
        ), patch("data_source.dp_cycle.trade_calendar.load_trade_calendar", return_value=(calendar, meta)), patch(
            "data_source.dp_cycle.us_crowding.load_cached_crowding_snapshot", return_value=None
        ):
            bundle = load_dp_cycle_gui_bundle(refresh=False)
        self.assertEqual(bundle["trade_calendar"]["coverage_start"], "2026-10-19")
        self.assertEqual(bundle["trade_calendar"]["coverage_end"], "2026-10-23")
        self.assertEqual(bundle["trade_calendar"]["dates"], ["2026-10-19", "2026-10-23"])

    def test_completed_holiday_week_can_use_a_published_weekly_bar(self):
        bundle = {
            "weekly": pd.DataFrame({"date": ["2026-10-23", "2026-10-30"],
                                    "available_at": ["2026-10-23", "2026-10-27"],
                                    "open": [1.0, 2.0], "high": [1.0, 2.0],
                                    "low": [1.0, 2.0], "close": [1.0, 2.0]}),
            "trade_calendar": {"dates": ["2026-10-26", "2026-10-27"],
                               "coverage_start": "2026-10-26", "coverage_end": "2026-10-30",
                               "source": "test calendar"},
            "meta": {"as_of": "2026-10-28"},
        }
        payload = bundle_to_web_payload(bundle, heavy=False)
        self.assertEqual(payload["weekly"][-1]["time"], "2026-10-30")
        self.assertEqual(payload["weekly"][-1]["close"], 2.0)

        bundle["weekly"].loc[1, "available_at"] = "2026-10-28 14:00:00"
        bundle["weekly"]["available_at"] = pd.to_datetime(bundle["weekly"]["available_at"], format="mixed")
        later = bundle_to_web_payload(bundle, heavy=False)
        self.assertEqual(later["weekly"][-1]["time"], "2026-10-23")


if __name__ == "__main__":
    unittest.main()
