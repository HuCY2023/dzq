import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.diagnosis import _industry_flow_item, diagnose_position, score_history_weekly
from data_source.dp_cycle.industry_flow import fetch_industry_flows


TECH = {
    "半导体": "BK1036", "消费电子": "BK1037", "计算机设备": "BK0735",
    "软件开发": "BK0737", "通信设备": "BK0448", "光学光电子": "BK1038",
    "元件": "BK0459", "互联网服务": "BK0447",
}
BANK = {"银行Ⅱ": "BK0475"}


def observations(days, tech_names=None, include_bank=True):
    selected = TECH if tech_names is None else {name: TECH[name] for name in tech_names}
    selected = {**selected, **(BANK if include_bank else {})}
    return pd.DataFrame([
        {"date": day, "bk_code": code, "name": name, "net_yi": 0 if name in BANK else -3}
        for day in days for name, code in selected.items()
    ])


class IndustryCoverageTests(unittest.TestCase):
    def setUp(self):
        self.days = pd.bdate_range("2026-09-21", "2026-09-25")
        self.end = pd.Timestamp("2026-09-25")

    def test_partial_basket_is_not_scored_as_tech_total(self):
        raw = observations(self.days, ["通信设备", "光学光电子"])
        item = _industry_flow_item(raw, period_end=self.end)
        self.assertEqual(item["status"], "partial")
        self.assertIsNone(item["score"])
        self.assertEqual(item["coverage"]["observed_tech_count"], 2)
        self.assertIn("2/8", item["note"])
        self.assertNotIn("科技合计", item["note"])

    def test_full_basket_and_bank_each_cover_every_day_before_scoring(self):
        raw = observations(self.days)
        item = _industry_flow_item(raw, period_end=self.end)
        self.assertEqual(item["status"], "ok")
        self.assertEqual(item["score"], -1)
        self.assertEqual(item["coverage"]["complete_tech_count"], 8)
        raw = raw.loc[~((raw.bk_code == "BK0735") & (raw.date == self.days[-1]))]
        partial = _industry_flow_item(raw, period_end=self.end)
        self.assertEqual(partial["status"], "partial")
        self.assertIsNone(partial["score"])
        self.assertEqual(partial["coverage"]["complete_tech_count"], 7)

    def test_same_name_with_different_code_does_not_count(self):
        raw = observations(self.days)
        raw.loc[raw.name == "半导体", "bk_code"] = "BK-OTHER"
        item = _industry_flow_item(raw, period_end=self.end)
        self.assertEqual(item["status"], "partial")
        self.assertEqual(item["coverage"]["observed_tech_count"], 7)

    def test_holiday_week_requires_calendar_or_five_weekdays(self):
        raw = observations(self.days[:2])
        self.assertEqual(_industry_flow_item(raw, period_end=self.end)["status"], "partial")
        calendar = {"coverage_start": "2026-09-21", "coverage_end": "2026-09-25",
                    "dates": ["2026-09-21", "2026-09-22"]}
        item = _industry_flow_item(raw, period_end=self.end, calendar=calendar)
        self.assertEqual(item["status"], "ok")

    def test_historical_scoring_never_reuses_a_prior_complete_week(self):
        first = observations(self.days)
        second = observations(pd.bdate_range("2026-09-28", "2026-10-02"), ["通信设备", "光学光电子"])
        weekly = pd.DataFrame({"date": [self.end, pd.Timestamp("2026-10-02")], "close": [1, 1]})
        curve = score_history_weekly({"weekly": weekly, "industry_flow_daily": pd.concat([first, second])}, as_of="2026-10-02")
        self.assertEqual(curve, [{"time": "2026-09-25", "value": -1}])

    def test_diagnosis_reports_scored_week_and_current_week_separately(self):
        previous = observations(self.days, ["通信设备", "光学光电子"])
        current = observations([pd.Timestamp("2026-09-28")], ["通信设备"])
        result = diagnose_position({"industry_flow_daily": pd.concat([previous, current])}, as_of="2026-09-29", heavy=False)
        item = next(i for i in result["items"] if i["id"] == "industry_flow")
        self.assertEqual(item["status"], "partial")
        self.assertIsNone(item["score"])
        self.assertEqual(item["coverage"]["observed_tech_count"], 2)
        self.assertEqual(item["current_week_coverage"]["observed_tech_count"], 1)
        self.assertIn("本周最新观测 2026-09-28", item["note"])
        self.assertNotIn("本周截至 2026-09-29", item["note"])
        self.assertEqual(item["current_week_coverage"]["last_observation_date"], "2026-09-28")

    def test_unrelated_current_board_cannot_supply_target_observation_date(self):
        previous = observations(self.days, ["通信设备"])
        other = pd.DataFrame([{"date": "2026-09-29", "bk_code": "BK4444", "name": "其他", "net_yi": 1}])
        result = diagnose_position({"industry_flow_daily": pd.concat([previous, other])}, as_of="2026-09-29", heavy=False)
        item = next(i for i in result["items"] if i["id"] == "industry_flow")
        self.assertIsNone(item["current_week_coverage"]["last_observation_date"])
        self.assertIn("本周尚未取得目标板块有效观测", item["note"])

    def test_bank_is_not_inferred_from_another_level(self):
        raw = observations(self.days, include_bank=False)
        other_bank = pd.DataFrame([{"date": day, "bk_code": "BK1283", "name": "银行", "net_yi": 0} for day in self.days])
        item = _industry_flow_item(pd.concat([raw, other_bank]), period_end=self.end)
        self.assertEqual(item["status"], "partial")
        self.assertFalse(item["coverage"]["bank_observed"])


class IndustryPaginationTests(unittest.TestCase):
    @staticmethod
    def response(data):
        response = Mock()
        response.json.return_value = {"data": data}
        return response

    def test_all_directory_pages_are_read_and_missing_targets_are_partial(self):
        pages = {
            1: {"total": 3, "diff": [{"f12": "BK1036", "f14": "半导体"}]},
            2: {"total": 3, "diff": [{"f12": "BK0475", "f14": "银行Ⅱ"}]},
            3: {"total": 3, "diff": [{"f12": "BK4444", "f14": "其他"}]},
        }
        session = Mock()
        def get(url, *, params, **kwargs):
            if "clist" in url:
                return self.response(pages[int(params["pn"])])
            return self.response({"klines": ["2026-09-25,-100000000,0,0"]})
        session.get.side_effect = get
        with patch("data_source.dp_cycle.industry_flow.requests.Session", return_value=session), patch(
            "data_source.dp_cycle.industry_flow._PAGE_SIZE", 1, create=True
        ), patch("data_source.dp_cycle.industry_flow.time.sleep"):
            rows, meta = fetch_industry_flows()
        self.assertEqual(meta["directory_pages"], 3)
        self.assertEqual(meta["directory_total"], 3)
        self.assertEqual(set(rows["bk_code"]), {"BK1036", "BK0475"})
        self.assertFalse(meta["ok"])
        self.assertTrue(meta["partial"])
        self.assertIn("互联网服务", meta["missing_targets"])

    def test_directory_page_failure_keeps_observed_subset_but_is_partial(self):
        session = Mock()
        def get(url, *, params, **kwargs):
            if "clist" in url:
                if int(params["pn"]) == 2:
                    raise OSError("page failed")
                return self.response({"total": 2, "diff": [{"f12": "BK1036", "f14": "半导体"}]})
            return self.response({"klines": ["2026-09-25,-100000000,0,0"]})
        session.get.side_effect = get
        with patch("data_source.dp_cycle.industry_flow.requests.Session", return_value=session), patch(
            "data_source.dp_cycle.industry_flow._PAGE_SIZE", 1, create=True
        ), patch("data_source.dp_cycle.industry_flow.time.sleep"):
            rows, meta = fetch_industry_flows()
        self.assertEqual(len(rows), 1)
        self.assertFalse(meta["ok"])
        self.assertTrue(meta["partial"])
        self.assertIn("page failed", meta["error"])

    def test_one_board_failure_is_not_reported_as_a_complete_update(self):
        targets = {**TECH, **BANK}
        session = Mock()
        def get(url, *, params, **kwargs):
            if "clist" in url:
                return self.response({"total": len(targets), "diff": [
                    {"f12": code, "f14": name} for name, code in targets.items()
                ]})
            if params["secid"].endswith("BK1036"):
                raise OSError("board unavailable")
            return self.response({"klines": ["2026-09-25,-100000000,0,0"]})
        session.get.side_effect = get
        with patch("data_source.dp_cycle.industry_flow.requests.Session", return_value=session), patch(
            "data_source.dp_cycle.industry_flow.time.sleep"
        ):
            rows, meta = fetch_industry_flows()
        self.assertEqual(len(rows), 8)
        self.assertFalse(meta["ok"])
        self.assertTrue(meta["partial"])
        self.assertEqual(meta["failed_targets"], ["半导体"])

    def test_lagging_board_is_partial_even_if_all_requests_succeed(self):
        targets = {**TECH, **BANK}
        session = Mock()
        def get(url, *, params, **kwargs):
            if "clist" in url:
                return self.response({"total": len(targets), "diff": [
                    {"f12": code, "f14": name} for name, code in targets.items()
                ]})
            day = "2026-09-24" if params["secid"].endswith("BK1036") else "2026-09-25"
            return self.response({"klines": [f"{day},-100000000,0,0"]})
        session.get.side_effect = get
        with patch("data_source.dp_cycle.industry_flow.requests.Session", return_value=session), patch(
            "data_source.dp_cycle.industry_flow.time.sleep"
        ):
            rows, meta = fetch_industry_flows()
        self.assertEqual(len(rows), 9)
        self.assertFalse(meta["ok"])
        self.assertTrue(meta["partial"])
        self.assertEqual(meta["latest_by_target"]["半导体"], "2026-09-24")
        self.assertEqual(meta["lagging_targets"], ["半导体"])


if __name__ == "__main__":
    unittest.main()
