import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_source.dp_cycle import us_crowding as uc


def crowding_snapshot(**overrides):
    empty = pd.DataFrame()
    responses = {
        "fetch_vix": empty, "fetch_us_index": empty, "fetch_sox": empty,
        "fetch_m2": empty, "fetch_finra_margin_debt": empty, "fetch_gdp": empty,
        "fetch_shiller_cape": empty, "compute_vix_term_structure": {},
        "compute_credit_stress": {}, "compute_net_liquidity": {}, "fetch_yields": {},
    }
    responses.update(overrides)
    with patch.multiple(uc, **{name: Mock(return_value=value) for name, value in responses.items()}):
        return uc.compute_crowding_snapshot()


class USCrowdingTests(unittest.TestCase):
    def test_fred_csv_keeps_first_observation_and_marks_source(self):
        response = Mock(text=(
            "observation_date,BAMLH0A0HYM2\n"
            "2026-10-01,3.24\n2026-10-02,3.10\n2026-10-03,.\n"
            "2026-10-05,3.12\n2999-01-01,99\n"
        ))
        with patch.object(uc.requests, "get", return_value=response):
            result = uc._fetch_fred_csv("BAMLH0A0HYM2", start="2026-10-01")
        self.assertEqual(result["date"].dt.strftime("%Y-%m-%d").tolist(),
                         ["2026-10-01", "2026-10-02", "2026-10-05"])
        self.assertEqual(result["value"].tolist(), [3.24, 3.10, 3.12])
        self.assertIn("fred.stlouisfed.org", result.attrs["source"])
        self.assertTrue(result.attrs["retrieved_at"])
        self.assertIsNone(result.attrs["available_at"])

    def test_fred_csv_does_not_accept_html_response(self):
        response = Mock(text="<html>unavailable</html>")
        with patch.object(uc.requests, "get", return_value=response):
            result = uc._fetch_fred_csv("M2SL")
        self.assertTrue(result.empty)

    def test_sox_and_cape_success_record_fetch_time(self):
        sox_raw = pd.DataFrame({"日期": ["2026-10-23"], "最新值": [123.0]})
        cape_raw = pd.DataFrame({"Date": ["2026.09"], "CAPE": [30.0]})
        with patch.object(uc, "ensure_http"), patch.object(uc, "call_with_retry", return_value=sox_raw):
            sox = uc.fetch_sox()
        with patch.object(uc.requests, "get", return_value=Mock(content=b"sample")), \
             patch.object(uc.pd, "read_excel", return_value=cape_raw):
            cape = uc.fetch_shiller_cape()
        self.assertTrue(sox.attrs.get("retrieved_at"))
        self.assertTrue(cape.attrs.get("retrieved_at"))

    def test_cape_numeric_months_include_october(self):
        raw = pd.DataFrame({"Date": [2023.01, 2023.1, 2023.11], "CAPE": [20.0, 21.0, 22.0]})
        with patch.object(uc.requests, "get", return_value=Mock(content=b"sample")), \
             patch.object(uc.pd, "read_excel", return_value=raw):
            cape = uc.fetch_shiller_cape()
        self.assertEqual(cape["date"].dt.strftime("%Y-%m").tolist(), ["2023-01", "2023-10", "2023-11"])

    def test_old_cape_is_not_a_current_scored_observation(self):
        cape = pd.DataFrame({"date": [pd.Timestamp.now().normalize() - pd.DateOffset(years=2)], "cape": [36.0]})
        result = crowding_snapshot(fetch_shiller_cape=cape).to_dict()
        self.assertEqual(result["component_status"]["cape"]["status"], "stale")
        self.assertEqual((result["evaluable_count"], result["triggered_count"]), (0, 0))
        self.assertFalse(result["cape_extreme"])

    def test_margin_gdp_status_reflects_stale_gdp(self):
        latest_month = (pd.Timestamp.now() - pd.DateOffset(months=1)).to_period("M").start_time
        finra = pd.DataFrame({"date": [latest_month], "margin_debt_bn": [1200.0]})
        gdp = pd.DataFrame({"date": [pd.Timestamp("2020-01-01")], "value": [20000.0]})
        result = crowding_snapshot(fetch_finra_margin_debt=finra, fetch_gdp=gdp).to_dict()
        self.assertEqual(result["component_status"]["gdp"]["status"], "stale")
        self.assertEqual(result["component_status"]["margin_gdp"]["status"], "stale")

    def test_credit_percentiles_use_own_series_and_common_window(self):
        hy = pd.DataFrame({
            "date": pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]),
            "value": [2.0, 3.0, 4.0, 5.0],
        })
        bbb = pd.DataFrame({
            "date": pd.to_datetime(["2026-10-01", "2026-10-03", "2026-10-04", "2026-10-05"]),
            "value": [0.5, 1.0, 2.0, 10.0],
        })
        with patch.object(uc, "fetch_hy_spread", return_value=hy), \
             patch.object(uc, "fetch_ig_spread", return_value=bbb):
            result = uc.compute_credit_stress()
        self.assertEqual((result["hy"], result["ig"], result["spread"]), (5.0, 2.0, 3.0))
        self.assertEqual((result["hy_pct"], result["ig_pct"], result["spread_pct"]), (75.0, 66.7, 33.3))
        self.assertEqual(result["bbb"], result["ig"])
        self.assertEqual(result["bbb_pct"], result["ig_pct"])
        self.assertEqual(result["unit"], "percent")
        self.assertEqual(result["spread_unit"], "percentage_points")
        self.assertEqual(result["hy_history"], {"start_date": "2026-10-01", "end_date": "2026-10-04", "count": 4})
        self.assertEqual(result["ig_history"], {"start_date": "2026-10-01", "end_date": "2026-10-04", "count": 3})
        self.assertEqual(result["spread_history"], {"start_date": "2026-10-01", "end_date": "2026-10-04", "count": 3})

    def test_credit_threshold_uses_unrounded_percentile(self):
        dates = pd.date_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=2000)
        hy = pd.DataFrame({"date": dates, "value": [3.0] * 1599 + [5.0] * 400 + [4.0]})
        bbb = pd.DataFrame({"date": dates, "value": [1.0] * 2000})
        with patch.object(uc, "fetch_hy_spread", return_value=hy), \
             patch.object(uc, "fetch_ig_spread", return_value=bbb):
            credit = uc.compute_credit_stress()
        self.assertLess(credit.get("spread_pct_unrounded", credit["spread_pct"]), 80)
        snapshot = crowding_snapshot(compute_credit_stress=credit).to_dict()
        self.assertFalse(snapshot["credit_stress"])
        self.assertEqual(snapshot["triggered_count"], 0)

    def test_margin_m2_uses_only_same_month(self):
        finra = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-30", "2026-07-31", "2026-08-31"]),
            "margin_debt_bn": [1000.0, 1100.0, 1200.0],
        })
        m2 = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-01", "2026-08-01"]),
            "value": [20000.0, 22000.0],
        })
        result = uc.compute_margin_m2_series(finra, m2)
        self.assertEqual(result["date"].dt.strftime("%Y-%m").tolist(), ["2026-06", "2026-08"])
        self.assertAlmostEqual(result["margin_m2_pct"].iloc[-1], 1200 / 22000 * 100)

    def test_margin_m2_excludes_future_publication(self):
        finra = pd.DataFrame({"date": pd.to_datetime(["2026-08-01"]), "margin_debt_bn": [1200],
                              "available_at": ["2099-01-01T00:00:00Z"]})
        m2 = pd.DataFrame({"date": pd.to_datetime(["2026-08-01"]), "value": [20000]})
        self.assertTrue(uc.compute_margin_m2_series(finra, m2).empty)

    def test_no_or_partial_observations_never_report_normal_zero_score(self):
        empty = crowding_snapshot().to_dict()
        self.assertIsNone(empty["crowding_score"])
        self.assertEqual((empty["evaluable_count"], empty["triggered_count"]), (0, 0))
        self.assertIn("不足", empty["verdict"])
        self.assertEqual(empty["component_status"]["margin_m2"]["status"], "missing")
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=60)
        vix = pd.DataFrame({"date": dates, "value": range(60)})
        vix.attrs.update(source="FRED VIXCLS", retrieved_at="2026-10-27T10:00:00+08:00")
        cape_date = (pd.Timestamp.now() - pd.DateOffset(months=1)).to_period("M").start_time
        cape = pd.DataFrame({"date": [cape_date], "cape": [30.0]})
        partial = crowding_snapshot(fetch_vix=vix, fetch_shiller_cape=cape).to_dict()
        self.assertIsNone(partial["crowding_score"])
        self.assertEqual((partial["evaluable_count"], partial["triggered_count"]), (2, 1))
        self.assertEqual(partial["component_status"]["vix"]["observation_date"], str(dates[-1].date()))
        self.assertEqual(partial["component_status"]["vix"]["retrieved_at"], "2026-10-27T10:00:00+08:00")
        self.assertEqual(partial["component_status"]["cape"]["observation_date"], str(cape_date.date()))
        self.assertIsNone(partial["component_status"]["cape"]["available_at"])

    def test_complete_snapshot_uses_matched_month_and_five_factors(self):
        recent_month = (pd.Timestamp.now() - pd.DateOffset(months=1)).to_period("M").start_time
        prior_month = recent_month - pd.DateOffset(months=1)
        finra = pd.DataFrame({"date": [prior_month, recent_month],
                              "margin_debt_bn": [1200.0, 1600.0],
                              "retrieved_at": ["2026-10-27T10:00:00+08:00"] * 2})
        m2 = pd.DataFrame({"date": [prior_month], "value": [20000.0]})
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=61)
        vix = pd.DataFrame({"date": dates, "value": range(61)})
        sox = pd.DataFrame({"date": dates, "close": [100.0] * 60 + [150.0]})
        cape = pd.DataFrame({"date": [recent_month], "cape": [36.0]})
        credit = {"date": str(dates[-1].date()), "hy": 4, "ig": 1, "spread": 3, "spread_pct": 85}
        data = crowding_snapshot(fetch_finra_margin_debt=finra, fetch_m2=m2, fetch_vix=vix,
                                 fetch_sox=sox, fetch_shiller_cape=cape,
                                 compute_credit_stress=credit).to_dict()
        self.assertEqual(data["margin_debt_bn"], 1600.0)
        self.assertEqual(data.get("margin_m2_debt_bn"), 1200.0)
        self.assertEqual(data["margin_m2_pct"], 6.0)
        self.assertEqual(data["component_status"]["margin_m2"]["observation_date"], str(prior_month.date()))
        self.assertEqual((data["evaluable_count"], data["triggered_count"], data["crowding_score"]), (5, 5, 5))
        self.assertEqual(data["component_status"]["credit"]["observation_date"], str(dates[-1].date()))

    def test_sox_60_day_return_uses_60_intervals(self):
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=61)
        sox = pd.DataFrame({"date": dates, "close": [100.0] + [125.0] * 59 + [200.0]})
        self.assertAlmostEqual(crowding_snapshot(fetch_sox=sox).sox_60d_ret, 100.0)
        self.assertIsNone(crowding_snapshot(fetch_sox=sox.iloc[1:]).sox_60d_ret)

    def test_fed_13_week_change_uses_13_intervals(self):
        dates = pd.date_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=14, freq="7D")
        assets = pd.DataFrame({"date": dates, "value": [1000.0] + [2000.0] * 12 + [3000.0]})
        with patch.object(uc, "fetch_fed_assets", return_value=assets), \
             patch.object(uc, "fetch_on_rrp", return_value=pd.DataFrame()):
            result = uc.compute_net_liquidity()
        self.assertEqual(result["qt_13w_pct"], 200.0)
        with patch.object(uc, "fetch_fed_assets", return_value=assets.iloc[1:]), \
             patch.object(uc, "fetch_on_rrp", return_value=pd.DataFrame()):
            self.assertNotIn("qt_13w_pct", uc.compute_net_liquidity())

    def test_legacy_snapshot_cache_is_not_used_as_verified_input(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            payload = {"as_of": pd.Timestamp.now().strftime("%Y-%m-%d"), "crowding_score": 5, "margin_m2_pct": 6.5}
            cache.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(uc, "_snapshot_cache_path", return_value=cache):
                result = uc.load_cached_crowding_snapshot()
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8")), payload)
        self.assertIsNone(result)

    def test_current_cache_keeps_component_dates_and_is_marked_cached(self):
        payload = crowding_snapshot().to_dict()
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            with patch.object(uc, "_snapshot_cache_path", return_value=cache):
                uc.save_crowding_snapshot_cache(payload)
                result = uc.load_cached_crowding_snapshot()
        self.assertEqual(result.get("cache_state"), "cached")
        self.assertEqual(result["component_status"], payload["component_status"])

    def test_cached_observation_that_aged_out_stops_contributing(self):
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=60)
        vix = pd.DataFrame({"date": dates, "value": range(60)})
        payload = crowding_snapshot(fetch_vix=vix).to_dict()
        self.assertEqual((payload["evaluable_count"], payload["triggered_count"]), (1, 1))
        payload["component_status"]["vix"]["observation_date"] = "2000-01-01"
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            with patch.object(uc, "_snapshot_cache_path", return_value=cache):
                uc.save_crowding_snapshot_cache(payload)
                result = uc.load_cached_crowding_snapshot()
        self.assertEqual(result["component_status"]["vix"]["status"], "stale")
        self.assertEqual((result["evaluable_count"], result["triggered_count"]), (0, 0))
        self.assertFalse(result["vix_elevated"])

    def test_empty_refresh_preserves_cached_snapshot_and_reports_failure(self):
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=60)
        vix = pd.DataFrame({"date": dates, "value": range(60)})
        previous = crowding_snapshot(fetch_vix=vix).to_dict()
        empty = crowding_snapshot()
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            with patch.object(uc, "_snapshot_cache_path", return_value=cache):
                uc.save_crowding_snapshot_cache(previous)
                payload = json.loads(cache.read_text(encoding="utf-8"))
                payload["_cached_at"] = (pd.Timestamp.now() - pd.Timedelta(days=2)).isoformat(timespec="seconds")
                cache.write_text(json.dumps(payload), encoding="utf-8")
                original = cache.read_text(encoding="utf-8")
                with patch.object(uc, "compute_crowding_snapshot", return_value=empty):
                    result = uc.load_or_compute_crowding_snapshot(force_refresh=True)
                self.assertEqual(cache.read_text(encoding="utf-8"), original)
        self.assertTrue(result.get("refresh_failed"))
        self.assertEqual(result.get("cache_state"), "stale")
        self.assertTrue(result.get("from_stale_cache"))
        self.assertGreaterEqual(result.get("cache_age_hours", 0), 48)
        self.assertTrue(result.get("refresh_attempted_at"))
        self.assertEqual(result["vix_last"], previous["vix_last"])

    def test_empty_refresh_with_no_cache_reports_failure_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            with patch.object(uc, "_snapshot_cache_path", return_value=cache), \
                 patch.object(uc, "compute_crowding_snapshot", return_value=crowding_snapshot()):
                result = uc.load_or_compute_crowding_snapshot(force_refresh=True)
            self.assertFalse(cache.exists())
        self.assertTrue(result.get("refresh_failed"))
        self.assertIsNone(result["crowding_score"])

    def test_empty_refresh_does_not_overwrite_legacy_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            legacy = '{"crowding_score": 5}'
            cache.write_text(legacy, encoding="utf-8")
            with patch.object(uc, "_snapshot_cache_path", return_value=cache), \
                 patch.object(uc, "compute_crowding_snapshot", return_value=crowding_snapshot()):
                result = uc.load_or_compute_crowding_snapshot(force_refresh=True)
            self.assertEqual(cache.read_text(encoding="utf-8"), legacy)
        self.assertTrue(result["refresh_failed"])
        self.assertEqual(result["cache_state"], "unavailable")

    def test_cached_snapshot_direct_read_marks_age_based_staleness(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            payload = crowding_snapshot().to_dict()
            payload["_cached_at"] = (pd.Timestamp.now() - pd.Timedelta(days=2)).isoformat(timespec="seconds")
            cache.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(uc, "_snapshot_cache_path", return_value=cache):
                result = uc.load_cached_crowding_snapshot(max_age_hours=72)
        self.assertEqual(result["cache_state"], "stale")
        self.assertTrue(result.get("from_stale_cache"))

    def test_refresh_exception_marks_fresh_cache_as_failed_refresh(self):
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=60)
        previous = crowding_snapshot(fetch_vix=pd.DataFrame({"date": dates, "value": range(60)})).to_dict()
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            with patch.object(uc, "_snapshot_cache_path", return_value=cache):
                uc.save_crowding_snapshot_cache(previous)
                original = cache.read_text(encoding="utf-8")
                with patch.object(uc, "compute_crowding_snapshot", side_effect=RuntimeError("failure")):
                    result = uc.load_or_compute_crowding_snapshot(force_refresh=True)
                self.assertEqual(cache.read_text(encoding="utf-8"), original)
        self.assertTrue(result["refresh_failed"])
        self.assertIn("异常", result["refresh_error"])
        self.assertEqual(result["cache_state"], "cached")
        self.assertFalse(result["from_stale_cache"])

    def test_zero_evaluable_refresh_without_cache_is_partial_and_not_saved(self):
        vix = pd.DataFrame({"date": [pd.Timestamp.now().normalize() - pd.Timedelta(days=1)], "value": [20.0]})
        partial = crowding_snapshot(fetch_vix=vix)
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            with patch.object(uc, "_snapshot_cache_path", return_value=cache), \
                 patch.object(uc, "compute_crowding_snapshot", return_value=partial):
                result = uc.load_or_compute_crowding_snapshot(force_refresh=True)
            self.assertFalse(cache.exists())
        self.assertTrue(result.get("refresh_failed"))
        self.assertEqual(result.get("refresh_status"), "partial")
        self.assertEqual(result["vix_last"], 20.0)
        self.assertIsNone(result["crowding_score"])

    def test_zero_evaluable_refresh_preserves_old_scored_snapshot(self):
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=60)
        old_vix = pd.DataFrame({"date": dates, "value": range(60)})
        old_vix.attrs.update(source="FRED VIXCLS", retrieved_at="2026-10-27T10:00:00+08:00")
        previous = crowding_snapshot(fetch_vix=old_vix).to_dict()
        spx = pd.DataFrame({"date": [dates[-1]], "close": [5000.0]})
        cape = pd.DataFrame({"date": [pd.Timestamp.now().normalize() - pd.DateOffset(years=2)], "cape": [40.0]})
        for label, new, field in (
            ("spx", crowding_snapshot(fetch_us_index=spx), "spx_last"),
            ("stale_cape", crowding_snapshot(fetch_shiller_cape=cape), "shiller_cape"),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temp:
                cache = Path(temp) / "snapshot.json"
                with patch.object(uc, "_snapshot_cache_path", return_value=cache):
                    uc.save_crowding_snapshot_cache(previous)
                    original = cache.read_text(encoding="utf-8")
                    with patch.object(uc, "compute_crowding_snapshot", return_value=new):
                        result = uc.load_or_compute_crowding_snapshot(force_refresh=True)
                    self.assertEqual(cache.read_text(encoding="utf-8"), original)
                self.assertTrue(result.get("refresh_failed"))
                self.assertEqual(result.get("refresh_status"), "partial")
                self.assertEqual(result.get("refresh_evaluable_count"), 0)
                self.assertIn(field, result.get("refresh_observed_fields", []))
                self.assertEqual(result["vix_last"], previous["vix_last"])
                self.assertEqual(result["component_status"]["vix"], previous["component_status"]["vix"])
                self.assertIsNone(result[field])
                self.assertEqual(result["refresh_component_status"]["vix"]["status"], "missing")
                if field == "spx_last":
                    self.assertEqual(result["refresh_component_status"]["spx"]["observation_date"], str(dates[-1].date()))
                else:
                    self.assertEqual(result["refresh_component_status"]["cape"]["status"], "stale")

    def test_partial_refresh_with_scored_observation_is_saved_as_partial(self):
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=60)
        vix = pd.DataFrame({"date": dates, "value": range(60)})
        partial = crowding_snapshot(fetch_vix=vix)
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            with patch.object(uc, "_snapshot_cache_path", return_value=cache), \
                 patch.object(uc, "compute_crowding_snapshot", return_value=partial):
                result = uc.load_or_compute_crowding_snapshot(force_refresh=True)
            self.assertTrue(cache.exists())
        self.assertFalse(result["refresh_failed"])
        self.assertEqual(result.get("refresh_status"), "partial")
        self.assertEqual(result["evaluable_count"], 1)
        self.assertIsNone(result["crowding_score"])

    def test_partial_refresh_preserves_any_missing_still_valid_old_factor(self):
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=61)
        old_vix = pd.DataFrame({"date": dates, "value": range(61)})
        recent_month = (pd.Timestamp.now() - pd.DateOffset(months=1)).to_period("M").start_time
        cape = pd.DataFrame({"date": [recent_month], "cape": [36.0]})
        previous = crowding_snapshot(fetch_vix=old_vix, fetch_shiller_cape=cape).to_dict()
        new_vix = pd.DataFrame({"date": dates, "value": list(range(60)) + [999]})
        sox = pd.DataFrame({"date": dates, "close": [100.0] * 60 + [110.0]})
        for label, new in (
            ("one_factor", crowding_snapshot(fetch_vix=new_vix)),
            ("same_count_different_factors", crowding_snapshot(fetch_vix=new_vix, fetch_sox=sox)),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temp:
                cache = Path(temp) / "snapshot.json"
                with patch.object(uc, "_snapshot_cache_path", return_value=cache):
                    uc.save_crowding_snapshot_cache(previous)
                    original = cache.read_text(encoding="utf-8")
                    with patch.object(uc, "compute_crowding_snapshot", return_value=new):
                        result = uc.load_or_compute_crowding_snapshot(force_refresh=True)
                    self.assertEqual(cache.read_text(encoding="utf-8"), original)
                self.assertTrue(result.get("refresh_failed"))
                self.assertEqual(result.get("refresh_status"), "partial")
                self.assertEqual(result.get("refresh_evaluable_count"), new.evaluable_count)
                self.assertEqual(result.get("refresh_missing_prior_items"), ["cape"])
                self.assertIn("cape", result.get("refresh_error", ""))
                self.assertEqual(result["vix_last"], previous["vix_last"])
                self.assertEqual(result["component_status"]["cape"], previous["component_status"]["cape"])
                self.assertEqual(result["refresh_component_status"]["cape"]["status"], "missing")

    def test_partial_refresh_may_replace_cache_when_all_old_factors_are_present(self):
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=60)
        vix = pd.DataFrame({"date": dates, "value": range(60)})
        old = crowding_snapshot(fetch_vix=vix).to_dict()
        recent_month = (pd.Timestamp.now() - pd.DateOffset(months=1)).to_period("M").start_time
        cape = pd.DataFrame({"date": [recent_month], "cape": [36.0]})
        new = crowding_snapshot(fetch_vix=vix, fetch_shiller_cape=cape)
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            with patch.object(uc, "_snapshot_cache_path", return_value=cache):
                uc.save_crowding_snapshot_cache(old)
                with patch.object(uc, "compute_crowding_snapshot", return_value=new):
                    result = uc.load_or_compute_crowding_snapshot(force_refresh=True)
                saved = json.loads(cache.read_text(encoding="utf-8"))
        self.assertFalse(result["refresh_failed"])
        self.assertEqual(result["refresh_status"], "partial")
        self.assertEqual(saved["evaluable_count"], 2)

    def test_expired_old_factor_does_not_block_a_new_partial_snapshot(self):
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=60)
        previous = crowding_snapshot(fetch_vix=pd.DataFrame({"date": dates, "value": range(60)})).to_dict()
        previous["component_status"]["vix"]["observation_date"] = "2000-01-01"
        recent_month = (pd.Timestamp.now() - pd.DateOffset(months=1)).to_period("M").start_time
        new = crowding_snapshot(fetch_shiller_cape=pd.DataFrame({"date": [recent_month], "cape": [36.0]}))
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            with patch.object(uc, "_snapshot_cache_path", return_value=cache):
                uc.save_crowding_snapshot_cache(previous)
                with patch.object(uc, "compute_crowding_snapshot", return_value=new):
                    result = uc.load_or_compute_crowding_snapshot(force_refresh=True)
                saved = json.loads(cache.read_text(encoding="utf-8"))
        self.assertFalse(result["refresh_failed"])
        self.assertEqual(result["refresh_status"], "partial")
        self.assertIsNone(saved["vix_last"])
        self.assertEqual(saved["shiller_cape"], 36.0)

    def test_partial_refresh_cannot_replace_newer_cached_observation(self):
        latest = pd.Timestamp.now().normalize() - pd.Timedelta(days=1)
        old_dates = pd.date_range(end=latest, periods=60)
        new_dates = old_dates - pd.Timedelta(days=8)
        old_vix = pd.DataFrame({"date": old_dates, "value": range(60)})
        old_vix.attrs.update(source="FRED VIXCLS", retrieved_at="2026-10-27T10:00:00+08:00")
        new_vix = pd.DataFrame({"date": new_dates, "value": range(100, 160)})
        previous = crowding_snapshot(fetch_vix=old_vix).to_dict()
        new = crowding_snapshot(fetch_vix=new_vix)
        self.assertEqual((previous["evaluable_count"], new.evaluable_count), (1, 1))
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            with patch.object(uc, "_snapshot_cache_path", return_value=cache):
                uc.save_crowding_snapshot_cache(previous)
                original = cache.read_text(encoding="utf-8")
                with patch.object(uc, "compute_crowding_snapshot", return_value=new):
                    result = uc.load_or_compute_crowding_snapshot(force_refresh=True)
                self.assertEqual(cache.read_text(encoding="utf-8"), original)
        self.assertTrue(result["refresh_failed"])
        self.assertEqual(result["refresh_status"], "partial")
        self.assertEqual(result.get("refresh_regressed_prior_items"), ["vix"])
        self.assertIn("vix", result["refresh_error"])
        self.assertEqual(result["vix_last"], previous["vix_last"])
        self.assertEqual(result["component_status"]["vix"], previous["component_status"]["vix"])
        self.assertEqual(result["refresh_component_status"]["vix"]["observation_date"], str(new_dates[-1].date()))

    def test_equal_observation_date_may_update_value(self):
        dates = pd.date_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=60)
        old = crowding_snapshot(fetch_vix=pd.DataFrame({"date": dates, "value": range(60)})).to_dict()
        new = crowding_snapshot(fetch_vix=pd.DataFrame({"date": dates, "value": range(100, 160)}))
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            with patch.object(uc, "_snapshot_cache_path", return_value=cache):
                uc.save_crowding_snapshot_cache(old)
                with patch.object(uc, "compute_crowding_snapshot", return_value=new):
                    result = uc.load_or_compute_crowding_snapshot(force_refresh=True)
                saved = json.loads(cache.read_text(encoding="utf-8"))
        self.assertFalse(result["refresh_failed"])
        self.assertEqual(saved["vix_last"], 159.0)

    def test_zero_evaluable_refresh_does_not_promote_aged_cached_component(self):
        dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=1), periods=60)
        previous = crowding_snapshot(fetch_vix=pd.DataFrame({"date": dates, "value": range(60)})).to_dict()
        previous["component_status"]["vix"]["observation_date"] = "2000-01-01"
        spx = pd.DataFrame({"date": [dates[-1]], "close": [5000.0]})
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "snapshot.json"
            with patch.object(uc, "_snapshot_cache_path", return_value=cache):
                uc.save_crowding_snapshot_cache(previous)
                original = cache.read_text(encoding="utf-8")
                with patch.object(uc, "compute_crowding_snapshot", return_value=crowding_snapshot(fetch_us_index=spx)):
                    result = uc.load_or_compute_crowding_snapshot(force_refresh=True)
                self.assertEqual(cache.read_text(encoding="utf-8"), original)
        self.assertEqual(result["component_status"]["vix"]["status"], "stale")
        self.assertEqual(result["evaluable_count"], 0)
        self.assertFalse(result["vix_elevated"])
        self.assertEqual(result["refresh_status"], "partial")

    def test_finra_uses_official_monthly_excel_and_not_old_cache(self):
        source = pd.DataFrame({
            "Year-Month": ["2026-07", "2026-04", "2026-03"],
            "Debit Balances in Customers' Securities Margin Accounts": [1417225, 1304281, 1220922],
        })
        payload = io.BytesIO()
        source.to_excel(payload, index=False, sheet_name="Customer Margin Balances")
        response = Mock(content=payload.getvalue())
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "legacy.json"
            cache.write_text(json.dumps({"2026-03": 9999, "2026-08": 9999}), encoding="utf-8")
            with patch.object(uc, "_finra_cache_path", return_value=cache), \
                 patch.object(uc.requests, "get", return_value=response):
                result = uc.fetch_finra_margin_debt()
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["2026-03"], 9999)
        self.assertEqual(result["date"].dt.strftime("%Y-%m").tolist(), ["2026-03", "2026-04", "2026-07"])
        self.assertEqual(result["margin_debt_bn"].tolist(), [1220.922, 1304.281, 1417.225])
        self.assertTrue(result["source"].str.contains("finra.org").all())
        self.assertTrue(result["retrieved_at"].notna().all())
        self.assertTrue(result["available_at"].isna().all())

    def test_finra_network_failure_does_not_promote_unverified_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "legacy.json"
            cache.write_text('{"2026-03": 1500}', encoding="utf-8")
            with patch.object(uc, "_finra_cache_path", return_value=cache), \
                 patch.object(uc.requests, "get", side_effect=OSError("offline")):
                result = uc.fetch_finra_margin_debt()
            self.assertTrue(cache.is_file())
        self.assertTrue(result.empty)


if __name__ == "__main__":
    unittest.main()
