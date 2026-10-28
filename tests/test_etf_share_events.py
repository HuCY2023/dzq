from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_source.dp_cycle.national_team_etf import monthly_share_change


class ShareEventTests(unittest.TestCase):
    @staticmethod
    def observed_shares():
        # Actual month-end observations, in 亿份, from the local ETF table.
        monthly = [
            ("2024-07-31", 752.854877, 1016.763074, 343.847498),
            ("2024-08-30", 852.619877, 1172.663074, 376.985498),
            ("2024-09-30", 969.223877, 651.921440, 398.117498),
        ]
        rows = [
            (day, code, shares, shares * 1e8)
            for day, *values in monthly
            for code, shares in zip(("510300", "510310", "510330"), values)
        ]
        return pd.DataFrame(rows, columns=["date", "code", "份额_亿份", "基金份额"])

    def test_september_event_corrects_month_change_and_percentile_history(self):
        hist = self.observed_shares()
        original = hist.copy(deep=True)
        result = monthly_share_change(hist)

        raw = hist.groupby("date")["份额_亿份"].sum()
        self.assertAlmostEqual(raw.iloc[-1] - raw.iloc[-2], -383.005634, places=6)
        expected = [
            752.854877 + 1016.763074 * 0.49977589 + 343.847498,
            852.619877 + 1172.663074 * 0.49977589 + 376.985498,
            969.223877 + 651.921440 + 398.117498,
        ]
        for actual, value in zip(result["份额_亿份"], expected):
            self.assertAlmostEqual(actual, value, places=6)
        self.assertAlmostEqual(result["月变动_亿份"].iloc[-1], 203.588709, places=6)
        self.assertEqual((result["份额_亿份"] <= result["份额_亿份"].iloc[-1]).sum(), 3)
        self.assertEqual((raw <= raw.iloc[-1]).sum(), 1)
        assert_frame_equal(hist, original)

    def test_old_history_keeps_units_when_event_has_not_occurred(self):
        hist = self.observed_shares()
        hist = hist[hist["date"] < "2024-09-20"]
        result = monthly_share_change(hist)
        raw = hist.groupby("date")["份额_亿份"].sum()
        self.assertEqual(result["份额_亿份"].tolist(), raw.tolist())

    def test_event_day_is_in_new_units_but_previous_day_is_not(self):
        hist = pd.DataFrame(
            [("2024-08-30", "510310", 100.0), ("2024-09-19", "510310", 110.0),
             ("2024-09-20", "510310", 55.0)],
            columns=["date", "code", "份额_亿份"],
        )
        before = monthly_share_change(hist.iloc[:2])
        after = monthly_share_change(hist)
        self.assertEqual(before["份额_亿份"].tolist(), [100.0, 110.0])
        self.assertAlmostEqual(after["份额_亿份"].iloc[0], 100.0 * 0.49977589)
        self.assertEqual(after["份额_亿份"].iloc[1], 55.0)

    def test_unaffected_funds_and_legacy_input_without_codes_keep_values(self):
        hist = self.observed_shares()
        hist = hist[hist["code"] != "510310"]
        expected = hist.groupby("date")["份额_亿份"].sum().tolist()
        self.assertEqual(monthly_share_change(hist)["份额_亿份"].tolist(), expected)
        self.assertEqual(monthly_share_change(hist.drop(columns="code"))["份额_亿份"].tolist(), expected)

    def test_integer_codes_from_csv_are_adjusted(self):
        hist = self.observed_shares()
        hist["code"] = hist["code"].astype(int)
        result = monthly_share_change(hist)
        self.assertAlmostEqual(result["月变动_亿份"].iloc[-1], 203.588709, places=6)

    def test_missing_month_is_not_shown_as_a_monthly_flow(self):
        hist = self.observed_shares()
        hist = hist.loc[~hist["date"].astype(str).str.startswith("2024-08")]
        result = monthly_share_change(hist)
        self.assertTrue(pd.isna(result["月变动_亿份"].iloc[-1]))

    def test_changed_fund_membership_is_not_shown_as_a_monthly_flow(self):
        hist = self.observed_shares()
        hist = hist.loc[~((hist["date"] == "2024-09-30") & (hist["code"] == "510310"))]
        result = monthly_share_change(hist)
        self.assertTrue(pd.isna(result["月变动_亿份"].iloc[-1]))

    def sample_bundle(self, hist):
        from data_source.dp_cycle import gui_bundle

        days = pd.to_datetime(["2024-08-02", "2024-08-30", "2024-10-04"])
        weekly = pd.DataFrame({"date": days, "open": 1, "high": 1, "low": 1, "close": 1})
        daily = hist.groupby("date", as_index=False)["份额_亿份"].sum()
        tables = {"weekly": weekly, "etf_hist": hist, "etf_daily": daily}
        with patch.object(gui_bundle, "load_from_db", return_value=tables), \
             patch.object(gui_bundle, "table_counts", return_value={}), \
             patch("data_source.dp_cycle.us_crowding.load_cached_crowding_snapshot", return_value=None):
            return gui_bundle.load_dp_cycle_gui_bundle(refresh=False)

    def test_web_etf_chart_matches_monthly_units_and_preserves_raw_history(self):
        from data_source.dp_cycle.gui_bundle import bundle_to_web_payload

        hist = self.observed_shares()
        original = hist.copy(deep=True)
        bundle = self.sample_bundle(hist)
        web = bundle_to_web_payload(bundle, heavy=False)
        points = {row["time"]: row["value"] for row in web["etf"]}
        monthly = bundle["etf_monthly"]
        self.assertAlmostEqual(points["2024-08-30"], monthly["份额_亿份"].iloc[-2], places=6)
        self.assertAlmostEqual(points["2024-10-04"], monthly["份额_亿份"].iloc[-1], places=6)
        self.assertIn("份额事件折算", web["coverage_note"]["etf"])
        self.assertIn("非资金净流入", web["coverage_note"]["etf"])
        assert_frame_equal(hist, original)
        assert_frame_equal(bundle["etf_hist"], original)

    def test_web_legacy_total_does_not_claim_event_adjustment(self):
        from data_source.dp_cycle.gui_bundle import bundle_to_web_payload

        weekly = pd.DataFrame({"date": pd.to_datetime(["2024-08-30"]),
                               "open": 1, "high": 1, "low": 1, "close": 1})
        daily = pd.DataFrame({"date": pd.to_datetime(["2024-08-30"]), "份额_亿份": [100.0]})
        web = bundle_to_web_payload({"weekly": weekly, "etf_daily": daily}, heavy=False)
        self.assertIn("无法逐基金折算", web["coverage_note"]["etf"])
        self.assertIn("无法逐基金折算", web["subtitle"])
        self.assertNotIn("按已发生份额事件折算", web["coverage_note"]["etf"])

    def test_pdf_chart_uses_adjusted_fund_lines_and_raw_daily_csv(self):
        import output_paths
        from data_source.dp_cycle import pipeline, report

        hist = self.observed_shares()
        original = hist.copy(deep=True)
        bundle = self.sample_bundle(hist)
        observed = {}
        save = report._save

        def capture(fig, path):
            if Path(path).name == "02_national_team.png":
                observed["lines"] = {line.get_label(): list(line.get_ydata()) for line in fig.axes[0].lines}
                observed["bars"] = [bar.get_height() for bar in fig.axes[1].containers[0].patches]
                observed["title"] = fig._suptitle.get_text()
            return save(fig, path)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report"
            store = Path(tmp) / "store"
            store.mkdir()
            with patch.object(pipeline, "load_dp_cycle_gui_bundle", return_value=bundle), \
                 patch.object(output_paths, "dp_cycle_store_dir", return_value=str(store)), \
                 patch.object(output_paths, "project_root", return_value=tmp), \
                 patch.object(report, "_save", side_effect=capture):
                result = pipeline.run_dp_cycle_pipeline(out_dir=str(out), refresh_db=False)
                self.assertTrue(Path(result["pdf"]).is_file())
                raw_csv = pd.read_csv(out / "02_etf_shares_daily.csv", dtype={"code": str})

        self.assertAlmostEqual(observed["lines"]["510310"][1], 1172.663074 * 0.49977589, places=6)
        self.assertAlmostEqual(observed["lines"]["510310"][2], 651.921440, places=6)
        self.assertEqual(observed["lines"]["510300"], [752.854877, 852.619877, 969.223877])
        self.assertEqual(observed["lines"]["510330"], [343.847498, 376.985498, 398.117498])
        self.assertAlmostEqual(observed["bars"][-1], 203.588709, places=6)
        self.assertIn("份额事件折算", observed["title"])
        self.assertIn("非资金净流入", observed["title"])
        assert_frame_equal(raw_csv, original)
        assert_frame_equal(hist, original)


if __name__ == "__main__":
    unittest.main()
