import sys
import unittest
from inspect import signature
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_source.dp_cycle.diagnosis import diagnose_position
from data_source.dp_cycle.national_team_etf import monthly_share_change


def etf_samples(last_day):
    dates = [pd.Timestamp(last_day) - pd.DateOffset(months=n) for n in range(11, -1, -1)]
    return pd.DataFrame({
        "date": dates,
        "份额_亿份": [200, 180, 160, 140, 130, 120, 110, 100, 90, 80, 50, 60],
    })


def core_samples(last_day):
    end = pd.Timestamp(last_day).normalize()
    days = pd.bdate_range(end=end, periods=40)
    last_friday = end - pd.Timedelta(days=(end.weekday() - 4) % 7)
    return {
        "weekly": pd.DataFrame({"date": [last_friday], "close": [100.0]}),
        "margin": pd.DataFrame({
            "date": days,
            "融资余额_亿元": range(1000, 1040),
            "分位_全样本": [50.0] * 40,
        }),
        "main_force_daily": pd.DataFrame({"date": days, "净额_亿元": [0.0] * 40}),
        "etf_monthly": etf_samples(end),
    }


def etf_history(last_day, drop_latest=None):
    monthly = etf_samples(last_day)
    rows = []
    for number, row in monthly.iterrows():
        for code in ("510300", "510310", "510330"):
            if number == len(monthly) - 1 and code == drop_latest:
                continue
            rows.append({"date": row["date"], "code": code, "份额_亿份": row["份额_亿份"] / 3})
    return pd.DataFrame(rows)


class DataAvailabilityTests(unittest.TestCase):
    def assert_no_position(self, diagnosis):
        self.assertEqual(diagnosis["decision"]["id"], "insufficient_data")
        self.assertIsNone(diagnosis["decision"]["equity_low"])
        self.assertIsNone(diagnosis["decision"]["equity_high"])
        self.assertIsNone(diagnosis["position"]["equity_low"])
        self.assertIsNone(diagnosis["position"]["equity_high"])
        self.assertIn("数据不足", diagnosis["decision"]["label"])

    def test_empty_bundle_has_no_score_or_position(self):
        diagnosis = diagnose_position({}, heavy=False)
        self.assertIsNone(diagnosis["score"])
        self.assert_no_position(diagnosis)

    def test_etf_proxy_alone_does_not_produce_a_buy_decision(self):
        diagnosis = diagnose_position({"etf_monthly": etf_samples(pd.Timestamp.today())}, heavy=False)
        etf = next(item for item in diagnosis["items"] if item["id"] == "national_team")
        self.assertEqual(etf["score"], 3)
        self.assert_no_position(diagnosis)

    def test_stale_key_data_does_not_produce_a_position(self):
        end = pd.Timestamp.today().normalize() - pd.Timedelta(days=27)
        diagnosis = diagnose_position(core_samples(end), heavy=False)
        self.assert_no_position(diagnosis)
        items = {item["id"]: item for item in diagnosis["items"]}
        self.assertEqual(items["margin"]["status"], "stale")
        self.assertIsNone(items["margin"]["score"])

    def test_analysis_date_exposes_stale_data_separately_from_report_date(self):
        self.assertIn("as_of", signature(diagnose_position).parameters)
        diagnosis = diagnose_position(core_samples("2026-09-30"), heavy=False, as_of="2026-10-27")
        self.assert_no_position(diagnosis)
        self.assertEqual(diagnosis["as_of"], "2026-10-27")
        self.assertEqual(diagnosis["data_availability"]["margin"]["observation_date"], "2026-09-30")
        self.assertEqual(diagnosis["data_availability"]["margin"]["status"], "stale")

    def test_future_week_label_is_not_a_current_observation(self):
        self.assertIn("as_of", signature(diagnose_position).parameters)
        bundle = core_samples("2026-10-26")
        bundle["weekly"] = pd.DataFrame({"date": ["2026-10-30"], "close": [100.0]})
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assert_no_position(diagnosis)
        self.assertNotEqual(diagnosis["data_availability"]["weekly"]["status"], "ok")
        self.assertIsNone(diagnosis["data_availability"]["weekly"]["observation_date"])

    def test_fresh_inputs_keep_zero_score_and_numeric_decision(self):
        self.assertIn("as_of", signature(diagnose_position).parameters)
        bundle = core_samples("2026-10-26")
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        margin = next(item for item in diagnosis["items"] if item["id"] == "margin")
        self.assertEqual(margin["score"], 0)
        self.assertEqual(margin["status"], "ok")
        self.assertIsInstance(diagnosis["decision"]["equity_low"], int)

    def test_future_daily_observations_are_not_used_at_analysis_date(self):
        self.assertIn("as_of", signature(diagnose_position).parameters)
        bundle = core_samples("2026-10-26")
        expected = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        future = {key: frame.copy() for key, frame in bundle.items()}
        for key in ("weekly", "margin", "main_force_daily", "etf_monthly"):
            row = future[key].iloc[[-1]].copy()
            row["date"] = pd.Timestamp("2026-11-06")
            for col in row.columns.difference(["date"]):
                row[col] = 100000
            future[key] = pd.concat([future[key], row], ignore_index=True)
        actual = diagnose_position(future, heavy=False, as_of="2026-10-27")
        self.assertEqual(actual["decision"], expected["decision"])
        self.assertEqual(actual["score"], expected["score"])
        self.assertEqual(actual["data_availability"], expected["data_availability"])

    def test_missing_margin_percentile_is_not_a_neutral_score(self):
        self.assertIn("as_of", signature(diagnose_position).parameters)
        bundle = core_samples("2026-10-26")
        bundle["margin"]["分位_全样本"] = float("nan")
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        margin = next(item for item in diagnosis["items"] if item["id"] == "margin")
        self.assertIsNone(margin["score"])
        self.assertEqual(margin["status"], "gap")
        self.assert_no_position(diagnosis)

    def test_margin_percentile_is_already_in_percent_units(self):
        bundle = core_samples("2026-10-26")
        bundle["margin"]["分位_全样本"] = 0.75
        result = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        item = next(i for i in result["items"] if i["id"] == "margin")
        self.assertEqual(item["pct"], 0.75)
        self.assertEqual(item["zone"], "low")

    def test_latest_missing_margin_balance_is_partial(self):
        bundle = core_samples("2026-10-26")
        missing = bundle["margin"].iloc[[-1]].copy()
        missing["date"] = pd.Timestamp("2026-10-27")
        missing["融资余额_亿元"] = float("nan")
        bundle["margin"] = pd.concat([bundle["margin"], missing], ignore_index=True)
        result = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        item = next(i for i in result["items"] if i["id"] == "margin")
        self.assertEqual(item["status"], "partial")
        self.assertIsNone(item["score"])
        self.assert_no_position(result)

    def test_current_week_is_observed_separately_and_not_scored_as_a_full_week(self):
        bundle = core_samples("2026-10-26")
        weekly = bundle["main_force_daily"]
        previous_week = weekly["date"].between("2026-10-19", "2026-10-23")
        weekly.loc[previous_week, "净额_亿元"] = -500.0
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        main_force = next(item for item in diagnosis["items"] if item["id"] == "main_force")
        self.assertEqual(main_force["score"], -2)
        self.assertIn("2026-10-23", main_force["note"])
        period = diagnosis["current_week"]
        self.assertEqual(period["period_end"], "2026-10-30")
        self.assertEqual(period["last_observation_date"], "2026-10-26")
        self.assertEqual(period["observed_trading_days"], 1)
        self.assertFalse(period["period_complete"])

    def test_stale_growth_value_cannot_change_current_score(self):
        bundle = core_samples("2026-10-26")
        bundle["growth_value"] = pd.DataFrame({"date": ["2026-09-30"], "偏离度": [20.0]})
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        growth = next(item for item in diagnosis["items"] if item["id"] == "divergence")
        self.assertEqual(growth["status"], "stale")
        self.assertIsNone(growth["score"])

    def test_stale_funding_is_not_described_as_flat(self):
        diagnosis = diagnose_position(core_samples("2026-09-30"), heavy=False, as_of="2026-10-27")
        self.assertIn("杠杆陈旧", diagnosis["resonance"]["text"])
        card = next(card for card in diagnosis["triple"] if card["id"] == "margin")
        self.assertEqual(card["score_text"], "陈旧")
        self.assertIsNone(card["zone"])

    def test_snapshot_created_after_analysis_date_is_not_used(self):
        bundle = core_samples("2026-10-26")
        bundle["us_crowding"] = {"as_of": "2026-11-02", "crowding_score": 5, "margin_m2_pct": 6.0}
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        item = next(item for item in diagnosis["items"] if item["id"] == "us_crowding")
        self.assertEqual(item["status"], "gap")
        flag = next(flag for flag in diagnosis["red_light"]["flags"] if flag["id"] == "global")
        self.assertIsNone(flag["on"])

    def test_rows_published_after_analysis_date_are_not_used(self):
        bundle = core_samples("2026-10-26")
        expected = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        future = bundle["margin"].iloc[[-1]].copy()
        future["date"] = pd.Timestamp("2026-10-27")
        future["available_at"] = pd.Timestamp("2026-10-28")
        future["融资余额_亿元"] = 10000
        future["分位_全样本"] = 0.99
        bundle["margin"]["available_at"] = bundle["margin"]["date"]
        bundle["margin"] = pd.concat([bundle["margin"], future], ignore_index=True)
        actual = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assertEqual(actual["decision"], expected["decision"])
        self.assertEqual(actual["score"], expected["score"])

    def test_later_etf_share_event_does_not_adjust_an_earlier_diagnosis(self):
        dates = pd.date_range("2023-09-30", "2024-09-30", freq="ME")
        hist = pd.DataFrame({
            "date": dates,
            "code": ["510310"] * len(dates),
            "份额_亿份": [100.0 + n for n in range(12)] + [56.0],
        })
        before = hist.loc[hist["date"] <= "2024-08-31"]
        expected = diagnose_position({"etf_monthly": monthly_share_change(before)}, heavy=False, as_of="2024-08-31")
        actual = diagnose_position({"etf_hist": hist, "etf_monthly": monthly_share_change(hist)}, heavy=False, as_of="2024-08-31")
        expected_etf = next(item for item in expected["items"] if item["id"] == "national_team")
        actual_etf = next(item for item in actual["items"] if item["id"] == "national_team")
        self.assertIn("份额 111 亿份", actual_etf["note"])
        self.assertEqual(actual_etf["proxy_delta"], expected_etf["proxy_delta"])
        self.assertEqual(actual_etf["score"], expected_etf["score"])

    def test_changing_etf_membership_only_blocks_author_position(self):
        bundle = core_samples("2026-10-26")
        bundle["etf_hist"] = etf_history("2026-10-26", drop_latest="510310")
        bundle["etf_monthly"] = monthly_share_change(bundle["etf_hist"])
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        author = diagnose_position(bundle, heavy=False, as_of="2026-10-27", rule_version="author_v1")
        self.assertIsNotNone(diagnosis["decision"]["equity_low"])
        self.assert_no_position(author)
        etf = next(item for item in diagnosis["items"] if item["id"] == "national_team")
        self.assertEqual(etf["status"], "partial")
        self.assertIsNone(etf["score"])
        coverage = diagnosis["data_availability"]["national_team"]["coverage"]
        self.assertEqual(coverage["previous_count"], 3)
        self.assertEqual(coverage["current_count"], 2)
        self.assertIn("159919", coverage["missing_codes"])
        october = next(row for row in diagnosis["funds_month"] if row["month"] == "2026-10")
        self.assertIsNone(october["national_team"])

    def test_same_three_etfs_reports_missing_fourth_without_claiming_complete(self):
        bundle = core_samples("2026-10-26")
        bundle["etf_hist"] = etf_history("2026-10-26")
        bundle["etf_monthly"] = monthly_share_change(bundle["etf_hist"])
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assertIn("coverage", diagnosis["data_availability"]["national_team"])
        coverage = diagnosis["data_availability"]["national_team"]["coverage"]
        self.assertEqual(coverage["current_count"], 3)
        self.assertIn("159919", coverage["missing_codes"])
        self.assertFalse(coverage["complete"])
        self.assertIn("159919", diagnosis["data_availability"]["national_team"]["reason"])

    def test_null_etf_snapshot_does_not_become_zero_shares(self):
        bundle = core_samples("2026-10-26")
        hist = etf_history("2026-10-26")
        invalid = hist.iloc[-3:].copy()
        invalid["date"] = pd.Timestamp("2026-10-27")
        invalid["份额_亿份"] = float("nan")
        bundle["etf_hist"] = pd.concat([hist, invalid], ignore_index=True)
        bundle["etf_monthly"] = monthly_share_change(bundle["etf_hist"])
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assertEqual(diagnosis["data_availability"]["national_team"]["observation_date"], "2026-10-26")

    def test_missing_main_force_days_in_ended_week_blocks_position(self):
        bundle = core_samples("2026-10-26")
        days = bundle["main_force_daily"]["date"]
        bundle["main_force_daily"] = bundle["main_force_daily"].loc[~days.between("2026-10-20", "2026-10-23")]
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assert_no_position(diagnosis)
        item = next(item for item in diagnosis["items"] if item["id"] == "main_force")
        self.assertEqual(item["status"], "partial")
        self.assertIsNone(item["score"])
        self.assertIn("融资", item["note"])

    def test_missing_reference_week_cannot_verify_main_force(self):
        bundle = core_samples("2026-10-26")
        dates = bundle["margin"]["date"]
        bundle["margin"] = bundle["margin"].loc[~dates.between("2026-10-19", "2026-10-23")]
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assert_no_position(diagnosis)
        self.assertEqual(diagnosis["data_availability"]["main_force"]["status"], "partial")

    def test_one_reference_day_is_not_enough_to_verify_week(self):
        bundle = core_samples("2026-10-26")
        dates = bundle["margin"]["date"]
        bundle["margin"] = bundle["margin"].loc[~dates.between("2026-10-20", "2026-10-23")]
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assertEqual(diagnosis["data_availability"]["main_force"]["status"], "partial")
        self.assert_no_position(diagnosis)

    def test_calendar_can_confirm_short_week_with_two_trading_days(self):
        bundle = core_samples("2026-10-26")
        for key in ("margin", "main_force_daily"):
            dates = bundle[key]["date"]
            bundle[key] = bundle[key].loc[~dates.between("2026-10-21", "2026-10-23")]
        bundle["trade_calendar"] = {
            "dates": ["2026-10-19", "2026-10-20"], "coverage_start": "2026-10-19",
            "coverage_end": "2026-10-23", "source": "测试交易日历",
        }
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assertEqual(diagnosis["data_availability"]["main_force"]["status"], "ok")
        self.assertIsInstance(diagnosis["decision"]["equity_low"], int)
        self.assertIn("测试交易日历", diagnosis["data_availability"]["main_force"]["reason"])

    def test_two_series_missing_same_weekdays_are_unverified_without_calendar(self):
        bundle = core_samples("2026-10-26")
        for key in ("margin", "main_force_daily"):
            dates = bundle[key]["date"]
            bundle[key] = bundle[key].loc[~dates.between("2026-10-21", "2026-10-23")]
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assertEqual(diagnosis["data_availability"]["main_force"]["status"], "partial")
        self.assert_no_position(diagnosis)

    def test_full_five_workdays_can_be_scored_without_calendar(self):
        diagnosis = diagnose_position(core_samples("2026-10-26"), heavy=False, as_of="2026-10-27")
        self.assertEqual(diagnosis["data_availability"]["main_force"]["status"], "ok")
        self.assertIsInstance(diagnosis["decision"]["equity_low"], int)

    def test_calendar_ending_before_week_cannot_confirm_short_week(self):
        bundle = core_samples("2026-10-26")
        for key in ("margin", "main_force_daily"):
            dates = bundle[key]["date"]
            bundle[key] = bundle[key].loc[~dates.between("2026-10-21", "2026-10-23")]
        bundle["trade_calendar"] = {
            "dates": ["2026-10-19", "2026-10-20"], "coverage_start": "2026-10-19",
            "coverage_end": "2026-10-20", "source": "覆盖不足的日历",
        }
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assertEqual(diagnosis["data_availability"]["main_force"]["status"], "partial")
        self.assert_no_position(diagnosis)

    def test_calendar_requires_both_margin_and_main_force_for_each_trading_day(self):
        bundle = core_samples("2026-10-26")
        bundle["margin"] = bundle["margin"].loc[bundle["margin"]["date"] != "2026-10-22"]
        bundle["trade_calendar"] = {
            "dates": ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"],
            "coverage_start": "2026-10-19", "coverage_end": "2026-10-23", "source": "测试交易日历",
        }
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assertEqual(diagnosis["data_availability"]["main_force"]["status"], "partial")
        self.assert_no_position(diagnosis)

    def test_current_holiday_week_is_complete_after_calendar_last_trading_day(self):
        bundle = core_samples("2026-10-27")
        dates = bundle["main_force_daily"]["date"]
        bundle["main_force_daily"].loc[dates.between("2026-10-26", "2026-10-27"), "净额_亿元"] = -1200.0
        bundle["weekly"] = pd.DataFrame({
            "date": ["2026-10-23", "2026-10-30"], "close": [100.0, 101.0],
            "available_at": ["2026-10-23", "2026-10-27"],
        })
        bundle["trade_calendar"] = {
            "dates": ["2026-10-26", "2026-10-27"], "coverage_start": "2026-10-26",
            "coverage_end": "2026-10-30", "source": "测试交易日历",
        }
        diagnosis = diagnose_position(bundle, heavy=True, as_of="2026-10-28")
        item = next(item for item in diagnosis["items"] if item["id"] == "main_force")
        self.assertEqual(item["score"], -2)
        self.assertEqual(item["status"], "ok")
        self.assertIn("2026-10-27", item["note"])
        self.assertEqual(diagnosis["data_availability"]["weekly"]["period_end"], "2026-10-30")
        self.assertIsNone(diagnosis["data_availability"]["weekly"]["observation_date"])
        self.assertEqual(diagnosis["current_week"]["last_observation_date"], "2026-10-27")
        self.assertTrue(diagnosis["current_week"]["period_complete"])
        self.assertTrue(all(row["time"] <= "2026-10-28" for row in diagnosis["score_curve"]))

    def test_completed_short_week_does_not_admit_weekly_bar_released_after_as_of(self):
        bundle = core_samples("2026-10-27")
        bundle["weekly"] = pd.DataFrame({
            "date": ["2026-10-23", "2026-10-30"], "close": [100.0, 101.0],
            "available_at": ["2026-10-23", "2026-10-29"],
        })
        bundle["trade_calendar"] = {
            "dates": ["2026-10-26", "2026-10-27"], "coverage_start": "2026-10-26",
            "coverage_end": "2026-10-30", "source": "测试交易日历",
        }
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-28")
        self.assertEqual(diagnosis["data_availability"]["weekly"]["period_end"], "2026-10-23")
        self.assertEqual(diagnosis["data_availability"]["weekly"]["status"], "partial")
        self.assert_no_position(diagnosis)

    def test_completed_short_week_future_bar_without_availability_cannot_set_position(self):
        bundle = core_samples("2026-10-27")
        bundle["weekly"] = pd.DataFrame({"date": ["2026-10-30"], "close": [101.0]})
        bundle["trade_calendar"] = {
            "dates": ["2026-10-26", "2026-10-27"], "coverage_start": "2026-10-26",
            "coverage_end": "2026-10-30", "source": "测试交易日历",
        }
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-28")
        self.assertIn(diagnosis["data_availability"]["weekly"]["status"], {"gap", "partial"})
        self.assertIn("周K", diagnosis["data_availability"]["weekly"]["reason"])
        self.assert_no_position(diagnosis)

    def test_completed_short_week_does_not_fall_back_to_previous_weekly_bar(self):
        bundle = core_samples("2026-10-27")
        bundle["weekly"] = pd.DataFrame({"date": ["2026-10-23"], "close": [100.0]})
        bundle["trade_calendar"] = {
            "dates": ["2026-10-26", "2026-10-27"], "coverage_start": "2026-10-26",
            "coverage_end": "2026-10-30", "source": "测试交易日历",
        }
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-28")
        self.assertEqual(diagnosis["data_availability"]["weekly"]["status"], "partial")
        self.assert_no_position(diagnosis)

    def test_completed_short_week_accepts_bar_known_on_analysis_date(self):
        bundle = core_samples("2026-10-27")
        bundle["weekly"] = pd.DataFrame({
            "date": ["2026-10-30"], "close": [101.0], "available_at": ["2026-10-28"],
        })
        bundle["trade_calendar"] = {
            "dates": ["2026-10-26", "2026-10-27"], "coverage_start": "2026-10-26",
            "coverage_end": "2026-10-30", "source": "测试交易日历",
        }
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-28")
        self.assertEqual(diagnosis["data_availability"]["weekly"]["status"], "ok")
        self.assertIsInstance(diagnosis["decision"]["equity_low"], int)

    def test_completed_short_week_does_not_assume_a_later_same_day_release(self):
        bundle = core_samples("2026-10-27")
        bundle["weekly"] = pd.DataFrame({
            "date": ["2026-10-30"], "close": [101.0], "available_at": ["2026-10-28 16:00:00"],
        })
        bundle["trade_calendar"] = {
            "dates": ["2026-10-26", "2026-10-27"], "coverage_start": "2026-10-26",
            "coverage_end": "2026-10-30", "source": "测试交易日历",
        }
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-28")
        self.assertIn(diagnosis["data_availability"]["weekly"]["status"], {"gap", "partial"})
        self.assert_no_position(diagnosis)

    def test_early_completed_week_requires_all_calendar_trading_days(self):
        bundle = core_samples("2026-10-27")
        bundle["margin"] = bundle["margin"].loc[bundle["margin"]["date"] != "2026-10-27"]
        bundle["trade_calendar"] = {
            "dates": ["2026-10-26", "2026-10-27"], "coverage_start": "2026-10-26",
            "coverage_end": "2026-10-30", "source": "测试交易日历",
        }
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-28")
        self.assertEqual(diagnosis["data_availability"]["main_force"]["status"], "partial")
        self.assert_no_position(diagnosis)

    def test_current_week_cannot_be_completed_early_without_calendar(self):
        bundle = core_samples("2026-10-27")
        dates = bundle["main_force_daily"]["date"]
        bundle["main_force_daily"].loc[dates.between("2026-10-26", "2026-10-27"), "净额_亿元"] = -1200.0
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-28")
        item = next(item for item in diagnosis["items"] if item["id"] == "main_force")
        self.assertEqual(item["score"], 0)
        self.assertFalse(diagnosis["current_week"]["period_complete"])

    def test_friday_date_alone_does_not_establish_that_trading_has_closed(self):
        bundle = core_samples("2026-10-23")
        dates = bundle["main_force_daily"]["date"]
        bundle["main_force_daily"].loc[dates.between("2026-10-19", "2026-10-23"), "净额_亿元"] = -1200.0
        bundle["weekly"] = pd.DataFrame({"date": ["2026-10-16", "2026-10-23"], "close": [100.0, 101.0]})
        bundle["trade_calendar"] = {
            "dates": ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"],
            "coverage_start": "2026-10-19", "coverage_end": "2026-10-23", "source": "测试交易日历",
        }
        friday = diagnose_position(bundle, heavy=False, as_of="2026-10-23")
        saturday = diagnose_position(bundle, heavy=False, as_of="2026-10-24")
        friday_force = next(item for item in friday["items"] if item["id"] == "main_force")
        saturday_force = next(item for item in saturday["items"] if item["id"] == "main_force")
        self.assertEqual(friday_force["score"], 0)
        self.assertIsNot(friday["current_week"]["period_complete"], True)
        self.assertEqual(saturday_force["score"], -2)
        self.assertTrue(saturday["current_week"]["period_complete"])

    def test_missing_intervening_etf_month_does_not_produce_a_monthly_score(self):
        bundle = core_samples("2026-10-26")
        etf = bundle["etf_monthly"]
        bundle["etf_monthly"] = etf.drop(etf.index[-2])
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        item = next(item for item in diagnosis["items"] if item["id"] == "national_team")
        self.assertEqual(item["status"], "gap")
        self.assertIsNone(item["score"])
        self.assertNotIn("月变动 +", item["note"])
        self.assertIsNotNone(diagnosis["decision"]["equity_low"])
        self.assert_no_position(diagnose_position(bundle, heavy=False, as_of="2026-10-27", rule_version="author_v1"))

    def test_unavailable_monthly_delta_is_not_recomputed_from_unverified_totals(self):
        bundle = core_samples("2026-10-26")
        bundle["etf_monthly"]["月变动_亿份"] = bundle["etf_monthly"]["份额_亿份"].diff()
        bundle["etf_monthly"].loc[bundle["etf_monthly"].index[-1], "月变动_亿份"] = float("nan")
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        etf = next(item for item in diagnosis["items"] if item["id"] == "national_team")
        self.assertIsNone(etf["score"])
        self.assertEqual(etf["status"], "gap")
        self.assertIsNotNone(diagnosis["decision"]["equity_low"])
        self.assert_no_position(diagnose_position(bundle, heavy=False, as_of="2026-10-27", rule_version="author_v1"))

    def test_latest_main_force_row_without_net_is_partial(self):
        bundle = core_samples("2026-10-26")
        partial = pd.DataFrame({"date": ["2026-10-27"], "净额_亿元": [float("nan")], "中单_亿元": [1.0]})
        bundle["main_force_daily"] = pd.concat([bundle["main_force_daily"], partial], ignore_index=True)
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assertEqual(diagnosis["data_availability"]["main_force"]["status"], "partial")
        self.assert_no_position(diagnosis)

    def test_month_table_does_not_label_a_gap_as_etf_monthly_change(self):
        bundle = core_samples("2026-10-26")
        etf = bundle["etf_monthly"].drop(bundle["etf_monthly"].index[-2]).copy()
        etf["月变动_亿份"] = etf["份额_亿份"].diff()
        bundle["etf_monthly"] = etf
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        october = next(row for row in diagnosis["funds_month"] if row["month"] == "2026-10")
        self.assertIsNone(october["national_team"])

    def test_unlock_with_no_current_or_next_month_is_unknown(self):
        dates = pd.date_range("2025-08-31", "2026-09-30", freq="ME")
        unlock = pd.DataFrame({"date": dates, "universe": "all", "amount_yi": 100.0, "available_at": "2025-01-01"})
        diagnosis = diagnose_position({"unlock_daily": unlock}, heavy=False, as_of="2026-10-27")
        item = next(item for item in diagnosis["items"] if item["id"] == "unlock")
        self.assertEqual(item["status"], "gap")
        self.assertIsNone(item["score"])

    def test_unlock_with_only_one_normal_month_is_unknown(self):
        dates = pd.date_range("2025-08-31", "2026-10-31", freq="ME")
        unlock = pd.DataFrame({"date": dates, "universe": "all", "amount_yi": 100.0, "available_at": "2025-01-01"})
        diagnosis = diagnose_position({"unlock_daily": unlock}, heavy=False, as_of="2026-10-27")
        item = next(item for item in diagnosis["items"] if item["id"] == "unlock")
        self.assertEqual(item["status"], "gap")
        self.assertIsNone(item["score"])

    def test_unlock_zero_baseline_cannot_trigger_peak(self):
        dates = pd.date_range("2025-08-31", "2026-11-30", freq="ME")
        unlock = pd.DataFrame({"date": dates, "universe": "all", "amount_yi": 0.0, "available_at": "2025-01-01"})
        diagnosis = diagnose_position({"unlock_daily": unlock}, heavy=False, as_of="2026-10-27")
        item = next(item for item in diagnosis["items"] if item["id"] == "unlock")
        self.assertEqual(item["status"], "gap")
        self.assertIsNone(item["score"])

    def test_unlock_one_known_high_month_reports_peak_and_missing_month(self):
        dates = pd.date_range("2025-08-31", "2026-10-31", freq="ME")
        unlock = pd.DataFrame({"date": dates, "universe": "all", "amount_yi": 100.0, "available_at": "2025-01-01"})
        unlock.loc[unlock["date"] == "2026-10-31", "amount_yi"] = 200.0
        diagnosis = diagnose_position({"unlock_daily": unlock}, heavy=False, as_of="2026-10-27")
        item = next(item for item in diagnosis["items"] if item["id"] == "unlock")
        self.assertEqual(item["score"], -1)
        self.assertIn("下月无汇总", item["note"])

    def test_history_curve_without_index_never_uses_future_week_labels(self):
        bundle = core_samples("2026-10-26")
        bundle.pop("weekly")
        diagnosis = diagnose_position(bundle, heavy=True, as_of="2026-10-27")
        self.assertTrue(diagnosis["score_curve"])
        self.assertTrue(all(row["time"] <= "2026-10-27" for row in diagnosis["score_curve"]))

    def test_empty_red_flags_are_unknown_not_off(self):
        red = diagnose_position({}, heavy=False, as_of="2026-10-27")["red_light"]
        self.assertTrue(all("status" in flag for flag in red["flags"]))
        self.assertTrue(all(flag["status"] == "unknown" and flag["on"] is None for flag in red["flags"]))
        self.assertEqual(red["known"], 0)
        self.assertIsNone(red["severe"])
        self.assertIn("未知", red["note"])

    def test_empty_external_snapshot_does_not_prove_global_risk_is_off(self):
        bundle = {"us_crowding": {"as_of": "2026-10-27", "crowding_score": 0}}
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        flag = next(flag for flag in diagnosis["red_light"]["flags"] if flag["id"] == "global")
        self.assertIn("status", flag)
        self.assertEqual(flag["status"], "unknown")
        self.assertIsNone(flag["on"])

    def test_global_risk_can_be_on_with_a_valid_margin_ratio(self):
        bundle = {"us_crowding": {"as_of": "2026-10-27", "margin_m2_pct": 5.5, "crowding_score": 0,
                                  "component_status": {"margin_m2": {"status": "available"}}}}
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        flag = next(flag for flag in diagnosis["red_light"]["flags"] if flag["id"] == "global")
        self.assertIn("status", flag)
        self.assertEqual(flag["status"], "on")
        self.assertIs(flag["on"], True)

    def test_score_note_describes_unscored_items_without_calling_them_all_missing(self):
        bundle = core_samples("2026-10-26")
        bundle["us_crowding"] = {"as_of": "2026-10-27", "margin_m2_pct": 5.5, "crowding_score": 0}
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assertIn("未参与评分", diagnosis["score_note"])
        self.assertIn("未参与评分", diagnosis["score_logic"])


if __name__ == "__main__":
    unittest.main()
