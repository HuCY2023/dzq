import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.allocation import monthly_regimes
from data_source.dp_cycle.diagnosis import _margin_item, diagnose_position, score_history_weekly
from data_source.dp_cycle.gui_bundle import _clip_bundle_as_of, bundle_to_web_payload
from data_source.dp_cycle.margin import causal_margin_frame, enrich_margin, margin_signal_at
from test_data_availability import core_samples


def financing_sample():
    dates = pd.bdate_range(end="2026-10-30", periods=45)
    values = [1000.0] * len(dates)
    values[-22] = 5000.0
    values[-21] = 100.0
    return pd.DataFrame({"date": dates, "融资余额_亿元": values, "分位_全样本": 50.0})


class CausalMarginTests(unittest.TestCase):
    def test_web_payload_uses_local_date_for_timezone_aware_cutoff(self):
        bundle = core_samples("2026-10-26")
        bundle["weekly"] = bundle["weekly"].assign(open=100.0, high=105.0, low=95.0)
        bundle["meta"] = {"as_of": "2026-10-27"}
        normal = bundle_to_web_payload(bundle, heavy=False)
        bundle["meta"] = {"as_of": "2026-10-26T16:00:00Z"}
        aware = bundle_to_web_payload(bundle, heavy=False)
        self.assertEqual(aware["diagnosis"]["as_of"], "2026-10-27")
        for key in ("weekly", "margin", "axis_tail"):
            with self.subTest(key=key):
                self.assertEqual(aware[key], normal[key])

    def test_mixed_timezones_use_local_time_and_obey_exact_cutoffs(self):
        margin = financing_sample()
        margin["available_at"] = margin["date"].dt.strftime("%Y-%m-%d")
        prior = margin.iloc[[-21]].copy()
        versions = []
        for released, balance in [
            ("2026-10-07 09:00:00", 500.0),
            ("2026-10-07T12:00:00+08:00", 5000.0),
            ("2026-10-07T05:00:00Z", 8000.0),
            ("2026-10-30T16:00:00Z", 9500.0),
        ]:
            row = prior.copy()
            row["available_at"] = released
            row["融资余额_亿元"] = balance
            versions.append(row)
        versions.reverse()
        margin = pd.concat([*versions, margin], ignore_index=True)
        prior_day = prior["date"].iloc[0]

        for cutoff, expected in [
            ("2026-10-07 08:59:00", 100.0),
            ("2026-10-07 09:00:00", 500.0),
            ("2026-10-07T03:59:59Z", 500.0),
            ("2026-10-07T04:00:00Z", 5000.0),
            ("2026-10-07T05:00:00Z", 8000.0),
            ("2026-10-29T16:00:00Z", 8000.0),
            ("2026-10-31", 9500.0),
        ]:
            with self.subTest(cutoff=cutoff):
                visible = causal_margin_frame(margin, as_of=cutoff)
                self.assertEqual(visible.loc[visible["date"] == prior_day, "融资余额_亿元"].iloc[0], expected)

        bundle = {"margin": margin, "weekly": pd.DataFrame({"date": ["2026-10-30"], "close": [1.0]})}
        cutoff = "2026-10-29T16:00:00Z"  # 台北时间 10 月 30 日零时
        clipped = _clip_bundle_as_of(bundle, cutoff)
        item = next(i for i in diagnose_position(bundle, heavy=False, as_of=cutoff)["items"] if i["id"] == "margin")
        self.assertEqual(margin_signal_at(clipped["margin"])["delta"], -7000.0)
        self.assertEqual((item["delta_20"], item["score"]), (-7000.0, -1))
        self.assertEqual(score_history_weekly(bundle, as_of=cutoff)[-1]["value"], item["score"])
        month = monthly_regimes(bundle, as_of=cutoff)[-1]
        self.assertEqual((month["margin_delta_20"], month["leverage"]), (-7000.0, -1))

    def test_explicit_missing_with_timezone_is_partial_in_each_view(self):
        margin = financing_sample()
        margin["available_at"] = margin["date"].dt.strftime("%Y-%m-%d")
        margin.loc[margin.index[-1], ["融资余额_亿元", "available_at"]] = [float("nan"), "2026-10-30T00:00:00+08:00"]
        bundle = {"margin": margin, "weekly": pd.DataFrame({"date": ["2026-10-30"], "close": [1.0]})}
        cutoff = "2026-10-30"
        clipped = _clip_bundle_as_of(bundle, cutoff)
        item = next(i for i in diagnose_position(bundle, heavy=False, as_of=cutoff)["items"] if i["id"] == "margin")
        self.assertEqual(margin_signal_at(clipped["margin"])["status"], "partial")
        self.assertEqual(item["status"], "partial")
        self.assertFalse(score_history_weekly(bundle, as_of=cutoff))
        self.assertEqual(monthly_regimes(bundle, as_of=cutoff)[-1]["margin_status"], "partial")

    def test_reversed_versions_select_latest_publication_before_cutoff(self):
        margin = financing_sample()
        margin["available_at"] = margin["date"]
        prior_day = margin["date"].iloc[-21]
        updated = margin.iloc[[-21]].copy()
        updated["融资余额_亿元"] = 5000.0
        updated["available_at"] = pd.Timestamp("2026-10-07")
        future = updated.copy()
        future["融资余额_亿元"] = 8000.0
        future["available_at"] = pd.Timestamp("2026-11-02")
        reversed_versions = pd.concat([future, updated, margin], ignore_index=True)

        for cutoff, expected in [("2026-10-06", 100.0), ("2026-10-30", 5000.0), ("2026-11-03", 8000.0)]:
            with self.subTest(cutoff=cutoff):
                visible = causal_margin_frame(reversed_versions, as_of=cutoff)
                value = visible.loc[visible["date"] == prior_day, "融资余额_亿元"].iloc[0]
                self.assertEqual(value, expected)

    def test_reversed_versions_match_current_weekly_and_monthly_signals(self):
        margin = financing_sample()
        margin["available_at"] = margin["date"]
        updated = margin.iloc[[-21]].copy()
        updated["融资余额_亿元"] = 5000.0
        updated["available_at"] = pd.Timestamp("2026-10-07")
        future = updated.copy()
        future["融资余额_亿元"] = 8000.0
        future["available_at"] = pd.Timestamp("2026-11-02")
        margin = pd.concat([future, updated, margin], ignore_index=True)
        bundle = {"margin": margin, "weekly": pd.DataFrame({"date": ["2026-10-30"], "close": [1.0]})}

        item = next(i for i in diagnose_position(bundle, heavy=False, as_of="2026-10-30")["items"]
                    if i["id"] == "margin")
        self.assertEqual((item["delta_20"], item["score"]), (-4000.0, -1))
        self.assertEqual(score_history_weekly(bundle, as_of="2026-10-30")[-1]["value"], item["score"])
        month = monthly_regimes(bundle, as_of="2026-10-30")[-1]
        self.assertEqual((month["margin_delta_20"], month["leverage"]), (item["delta_20"], item["score"]))
        self.assertEqual(month["margin_pct"], item["pct"])

    def test_mixed_publication_date_formats_remain_visible_in_all_views(self):
        margin = financing_sample()
        margin["available_at"] = margin["date"].dt.strftime("%Y-%m-%d")
        updated = margin.iloc[[-21]].copy()
        updated["融资余额_亿元"] = 5000.0
        updated["available_at"] = "2026-10-07 12:00:00"
        margin = pd.concat([margin, updated], ignore_index=True)
        bundle = {"margin": margin, "weekly": pd.DataFrame({"date": ["2026-10-30"], "close": [1.0]})}
        clipped = _clip_bundle_as_of(bundle, "2026-10-30")
        item = next(i for i in diagnose_position(bundle, heavy=False, as_of="2026-10-30")["items"]
                    if i["id"] == "margin")
        curve = score_history_weekly(bundle, as_of="2026-10-30")
        month = monthly_regimes(bundle, as_of="2026-10-30")[-1]
        values = {
            "single": (margin_signal_at(margin, as_of="2026-10-30").get("delta"), -4000.0),
            "clipped": (margin_signal_at(clipped["margin"]).get("delta"), -4000.0),
            "diagnosis": (item.get("delta_20"), -4000.0),
            "weekly": (curve[-1]["value"] if curve else None, -1),
            "monthly": (month["margin_delta_20"], -4000.0),
        }
        for view, (actual, expected) in values.items():
            with self.subTest(view=view):
                self.assertEqual(actual, expected)

    def test_duplicate_without_publication_time_keeps_last_row_behavior(self):
        margin = financing_sample()
        updated = margin.iloc[[-21]].copy()
        updated["融资余额_亿元"] = 5000.0
        signal = margin_signal_at(pd.concat([updated, margin], ignore_index=True), as_of="2026-10-30")
        self.assertEqual(signal["delta"], 900.0)

    def test_enrichment_does_not_rewrite_earlier_percentiles_when_future_is_added(self):
        base = financing_sample()
        future = pd.DataFrame({"date": ["2026-11-02", "2026-11-03"], "融资余额_亿元": [5.0, 9000.0]})
        previous = enrich_margin(base)["分位_全样本"].tolist()
        extended = enrich_margin(pd.concat([base, future], ignore_index=True))["分位_全样本"].iloc[:len(base)].tolist()
        self.assertEqual(previous, extended)

    def test_one_percentile_contract_and_twenty_trading_intervals(self):
        margin = financing_sample()
        item = _margin_item(margin)
        self.assertEqual(item["score"], 1)
        self.assertEqual(item["trend"], "up")
        bundle = {"margin": margin, "weekly": pd.DataFrame({"date": ["2026-10-30"], "close": [1.0]})}
        curve = score_history_weekly(bundle, as_of="2026-10-30")
        self.assertEqual(curve[-1]["value"], item["score"])
        regimes = monthly_regimes(bundle)
        self.assertEqual(regimes[-1]["end"], "2026-10-30")
        self.assertEqual(regimes[-1]["leverage"], item["score"])

    def test_latest_null_balance_is_not_replaced_by_yesterday(self):
        margin = financing_sample()
        margin.loc[margin.index[-1], "融资余额_亿元"] = float("nan")
        item = _margin_item(margin)
        self.assertIsNone(item["score"])
        bundle = {"margin": margin, "weekly": pd.DataFrame({"date": ["2026-10-30"], "close": [1.0]})}
        self.assertFalse(score_history_weekly(bundle, as_of="2026-10-30"))
        last = monthly_regimes(bundle)[-1]
        self.assertEqual(last["phase_id"], "unclear")
        self.assertIsNone(last["leverage"])

    def test_unpublished_late_observation_does_not_change_historical_week(self):
        margin = financing_sample()
        margin["available_at"] = margin["date"]
        before = pd.Timestamp("2026-10-23")
        base = margin.loc[margin["date"] <= before].copy()
        late = base.iloc[[-1]].copy()
        late["date"] = "2026-10-23"
        late["available_at"] = "2026-10-28 18:00:00"
        late["融资余额_亿元"] = 100000.0
        bundle = {"weekly": pd.DataFrame({"date": [before], "close": [1.0]})}
        baseline = score_history_weekly({**bundle, "margin": base}, as_of="2026-10-23")
        after = score_history_weekly({**bundle, "margin": pd.concat([base, late], ignore_index=True)}, as_of="2026-10-23")
        self.assertEqual(after, baseline)

    def test_appending_future_balances_does_not_change_prior_week_scores(self):
        margin = financing_sample()
        anchors = pd.date_range("2026-10-02", "2026-10-30", freq="W-FRI")
        bundle = {"margin": margin, "weekly": pd.DataFrame({"date": anchors, "close": 1.0})}
        future = pd.DataFrame({"date": pd.bdate_range("2026-11-02", periods=10),
                               "融资余额_亿元": 100000.0, "分位_全样本": 99.0})
        base = score_history_weekly(bundle)
        extended = score_history_weekly({**bundle, "margin": pd.concat([margin, future], ignore_index=True)})
        self.assertEqual(base, extended)

    def test_low_percentile_is_not_scaled_up(self):
        dates = pd.bdate_range(end="2026-10-30", periods=200)
        margin = pd.DataFrame({"date": dates, "融资余额_亿元": [100.0] * 199 + [1.0], "分位_全样本": 75.0})
        item = _margin_item(margin)
        self.assertEqual(item["pct"], 0.5)
        self.assertEqual(item["zone"], "low")

    def test_missing_twentieth_interval_cannot_be_replaced_with_an_earlier_value(self):
        margin = financing_sample()
        margin.loc[margin.index[-21], "融资余额_亿元"] = float("nan")
        item = _margin_item(margin)
        self.assertIsNone(item["score"])
        bundle = {"margin": margin, "weekly": pd.DataFrame({"date": ["2026-10-30"], "close": [1.0]})}
        self.assertFalse(score_history_weekly(bundle, as_of="2026-10-30"))
        self.assertIsNone(monthly_regimes(bundle)[-1]["leverage"])

    def test_calendar_preserves_twenty_trading_intervals_when_a_date_is_absent(self):
        margin = financing_sample()
        dates = margin["date"].tolist()
        calendar = {"dates": [d.strftime("%Y-%m-%d") for d in dates],
                    "coverage_start": dates[0].strftime("%Y-%m-%d"),
                    "coverage_end": dates[-1].strftime("%Y-%m-%d")}
        margin = margin.drop(margin.index[-10])
        item = _margin_item(margin, trade_calendar=calendar)
        self.assertEqual(item["delta_20"], 900.0)
        self.assertEqual(item["score"], 1)
        bundle = {"margin": margin, "trade_calendar": calendar,
                  "weekly": pd.DataFrame({"date": ["2026-10-30"], "close": [1.0]})}
        self.assertEqual(score_history_weekly(bundle, as_of="2026-10-30")[-1]["value"], 1)
        self.assertEqual(monthly_regimes(bundle)[-1]["leverage"], 1)

    def test_explicit_missing_latest_value_is_not_dropped_by_unknown_publication_time(self):
        margin = financing_sample()
        margin["available_at"] = margin["date"]
        margin.loc[margin.index[-1], ["融资余额_亿元", "available_at"]] = [float("nan"), pd.NaT]
        bundle = {"margin": margin, "weekly": pd.DataFrame({"date": ["2026-10-30"], "close": [1.0]})}
        self.assertFalse(score_history_weekly(bundle, as_of="2026-10-30"))
        self.assertIsNone(monthly_regimes(bundle)[-1]["leverage"])

    def test_later_missing_revision_does_not_change_an_earlier_signal(self):
        margin = financing_sample()
        margin["available_at"] = margin["date"]
        bundle = {"weekly": pd.DataFrame({"date": ["2026-10-30"], "close": [1.0]})}
        before = score_history_weekly({**bundle, "margin": margin}, as_of="2026-10-30")
        later = margin.iloc[[-1]].copy()
        later["融资余额_亿元"] = float("nan")
        later["available_at"] = "2026-11-02"
        revised = pd.concat([margin, later], ignore_index=True)
        after = score_history_weekly({**bundle, "margin": revised}, as_of="2026-10-30")
        self.assertEqual(after, before)
        self.assertEqual(monthly_regimes({**bundle, "margin": revised}), monthly_regimes({**bundle, "margin": margin}))

    def test_unknown_publication_time_does_not_hide_explicit_missing_current_balance(self):
        bundle = core_samples("2026-10-26")
        margin = bundle["margin"].copy()
        margin["available_at"] = margin["date"]
        missing = margin.iloc[[-1]].copy()
        missing["date"] = "2026-10-27"
        missing["融资余额_亿元"] = float("nan")
        missing["available_at"] = pd.NaT
        bundle["margin"] = pd.concat([margin, missing], ignore_index=True)
        late = margin.iloc[[-1]].copy()
        late["date"] = "2026-10-27"
        late["available_at"] = "2026-10-27 18:00:00"
        late["融资余额_亿元"] = 100000.0
        bundle["margin"] = pd.concat([bundle["margin"], late], ignore_index=True)
        clipped = _clip_bundle_as_of(bundle, "2026-10-27")
        last = clipped["margin"].loc[pd.to_datetime(clipped["margin"]["date"]) == "2026-10-27"]
        self.assertEqual(len(last), 1)
        self.assertTrue(last["融资余额_亿元"].isna().all())
        result = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        item = next(i for i in result["items"] if i["id"] == "margin")
        self.assertEqual(item["status"], "partial")
        self.assertIsNone(item["score"])
        self.assertEqual(result["decision"]["id"], "insufficient_data")
        self.assertEqual(margin_signal_at(bundle["margin"], as_of="2026-10-27")["status"], "partial")
        self.assertEqual(monthly_regimes(bundle, as_of="2026-10-27")[-1]["margin_status"], "partial")

    def test_future_outside_calendar_cannot_change_earlier_trading_interval(self):
        margin = financing_sample()
        dates = margin["date"].tolist()
        calendar = {"dates": [d.strftime("%Y-%m-%d") for d in dates],
                    "coverage_start": dates[0].strftime("%Y-%m-%d"),
                    "coverage_end": dates[-1].strftime("%Y-%m-%d")}
        margin = margin.drop(margin.index[-10])
        future = pd.DataFrame({"date": ["2026-11-02"], "融资余额_亿元": [100000.0]})
        extended = pd.concat([margin, future], ignore_index=True)
        current = margin_signal_at(margin, trade_calendar=calendar)
        past = margin_signal_at(extended, as_of="2026-10-30", trade_calendar=calendar)
        self.assertEqual((current["delta"], current["score"]), (900.0, 1))
        self.assertEqual((past["delta"], past["score"]), (900.0, 1))
        bundle = {"margin": margin, "trade_calendar": calendar,
                  "weekly": pd.DataFrame({"date": ["2026-10-30"], "close": [1.0]})}
        expected = score_history_weekly(bundle, as_of="2026-10-30")
        self.assertEqual(expected, score_history_weekly({**bundle, "margin": extended}, as_of="2026-10-30"))
        self.assertEqual(monthly_regimes(bundle, as_of="2026-10-30"),
                         monthly_regimes({**bundle, "margin": extended}, as_of="2026-10-30"))
        prior_months = monthly_regimes(bundle)
        extended_months = monthly_regimes({**bundle, "margin": extended})
        self.assertEqual(extended_months[:len(prior_months)], prior_months)
        self.assertEqual(prior_months[-1]["margin_status"], "ok")
        self.assertNotEqual(prior_months[-1]["stage"], "数据不足")


if __name__ == "__main__":
    unittest.main()
