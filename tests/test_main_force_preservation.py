import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb
import pandas as pd

from data_source.dp_cycle.ingest import ingest_main_force
from data_source.dp_cycle.main_force_em import (
    fetch_em_market_fund_flow,
    load_em_fund_flow_cache,
    union_main_force_frames,
)
from data_source.dp_cycle.store import init_schema


class MainForcePreservationTests(unittest.TestCase):
    def setUp(self):
        ths = patch("data_source.dp_cycle.main_force_ths.upsert_today_from_ths",
                    side_effect=AssertionError("unexpected THS network fallback"))
        ths.start()
        self.addCleanup(ths.stop)

    def test_union_keeps_each_latest_non_null_value_and_its_sources(self):
        cached = pd.DataFrame([{
            "date": "2024-01-02", "净额_亿元": 10, "小单_亿元": 0,
            "中单_亿元": 2, "大单_亿元": 3, "超大单_亿元": 4,
            "source": "em_market_main",
        }])
        stored = pd.DataFrame([{
            "date": "2024-01-02", "净额_亿元": None, "中单_亿元": None,
            "流入_亿元": 8, "source": "ths_industry_sum",
        }])
        fresh = pd.DataFrame([{
            "date": "2024-01-02", "净额_亿元": 11, "中单_亿元": 0,
            "source": "em_market_main",
        }])

        row = union_main_force_frames(cached, stored, fresh).iloc[0]

        self.assertFalse(pd.isna(row["小单_亿元"]))
        self.assertEqual([row["净额_亿元"], row["小单_亿元"], row["中单_亿元"],
                          row["大单_亿元"], row["超大单_亿元"], row["流入_亿元"]],
                         [11, 0, 0, 3, 4, 8])
        self.assertEqual(row["source"], "mixed_or_unknown")

    def test_partial_lines_are_parsed_without_losing_other_fields(self):
        response = Mock()
        response.json.return_value = {"data": {"klines": [
            "2024-01-02,200000000,0,-,500000000,600000000",
            "2024-01-03,-,100000000,200000000,300000000,400000000",
        ]}}
        with tempfile.TemporaryDirectory() as tmp, patch(
            "output_paths.dp_cycle_store_dir", return_value=tmp,
        ), patch("requests.sessions.Session.get", return_value=response):
            daily, meta = fetch_em_market_fund_flow(retries=1)
        self.assertTrue(meta["ok"])
        self.assertEqual(len(daily), 2)
        self.assertEqual(daily.iloc[0]["小单_亿元"], 0)
        self.assertTrue(pd.isna(daily.iloc[0]["中单_亿元"]))
        self.assertTrue(pd.isna(daily.iloc[1]["净额_亿元"]))
        self.assertEqual(daily.iloc[1]["中单_亿元"], 2)
        self.assertIsNone(meta["last_净额_亿元"])

    def test_partial_fetch_keeps_existing_fields_in_raw_cache(self):
        response = Mock()
        response.json.return_value = {"data": {"klines": [
            "2024-01-02,200000000,0,-,500000000,600000000",
            "2024-01-03,100000000,100000000,200000000,300000000,400000000",
        ]}}
        with tempfile.TemporaryDirectory() as tmp, patch(
            "output_paths.dp_cycle_store_dir", return_value=tmp,
        ), patch("requests.sessions.Session.get", return_value=response):
            path = Path(tmp, "_em_fflow_raw.json")
            path.write_text(json.dumps({"data": {"klines": [
                "2024-01-02,100000000,100000000,200000000,300000000,400000000"
            ]}}), encoding="utf-8")
            daily, meta = fetch_em_market_fund_flow(retries=1)
            cached = load_em_fund_flow_cache(str(path))
        self.assertTrue(meta["ok"])
        self.assertEqual(len(cached), 2)
        row = cached.iloc[0]
        self.assertEqual([row["净额_亿元"], row["小单_亿元"], row["中单_亿元"],
                          row["大单_亿元"], row["超大单_亿元"]], [2, 0, 2, 5, 6])

    def test_partial_fetch_is_preserved_through_ingest_and_csv(self):
        response = Mock()
        response.json.return_value = {"data": {"klines": [
            "2024-01-02,200000000,0,-,500000000,600000000",
            "2024-01-03,100000000,100000000,200000000,300000000,400000000",
        ]}}
        with tempfile.TemporaryDirectory() as tmp, patch(
            "output_paths.dp_cycle_store_dir", return_value=tmp,
        ), patch("requests.sessions.Session.get", return_value=response):
            Path(tmp, "_em_fflow_raw.json").write_text(json.dumps({"data": {"klines": [
                "2024-01-02,100000000,100000000,200000000,300000000,400000000"
            ]}}), encoding="utf-8")
            con = duckdb.connect(":memory:")
            init_schema(con)
            con.execute("INSERT INTO main_force_daily(date, net_yi, source) "
                        "VALUES ('2024-01-02', 1, 'em_market_main')")
            meta = ingest_main_force(con)
            row = con.execute("SELECT net_yi, small_yi, mid_yi, large_yi, super_yi, source "
                              "FROM main_force_daily WHERE date = '2024-01-02'").fetchone()
            self.assertEqual(row, (2, 0, 2, 5, 6, "em_market_main"))
            self.assertEqual(meta["wrote"], 2)
            cached = load_em_fund_flow_cache(str(Path(tmp, "main_force_ths", "em_market_daily.csv")))
            self.assertEqual(cached.iloc[0]["中单_亿元"], 2)
            con.close()

    def test_csv_keeps_split_only_row_without_net(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "daily.csv")
            path.write_text("date,净额_亿元,小单_亿元,中单_亿元,source\n"
                            "2024-01-03,,0,2,em_market_main\n", encoding="utf-8")
            cached = load_em_fund_flow_cache(str(path))
            self.assertEqual(len(cached), 1)
            self.assertTrue(pd.isna(cached.iloc[0]["净额_亿元"]))
            self.assertEqual((cached.iloc[0]["小单_亿元"], cached.iloc[0]["中单_亿元"]), (0, 2))
            path.write_text("date,小单_亿元\n2024-01-03,0\n", encoding="utf-8")
            cached = load_em_fund_flow_cache(str(path))
            self.assertEqual(len(cached), 1)
            self.assertTrue(pd.isna(cached.iloc[0]["净额_亿元"]))
            self.assertEqual(cached.iloc[0]["小单_亿元"], 0)

    def test_split_only_new_date_survives_online_and_offline_ingest(self):
        response = Mock()
        response.json.return_value = {"data": {"klines": [
            "2024-01-02,100000000,100000000,200000000,300000000,400000000",
            "2024-01-03,-,0,200000000,300000000,400000000",
        ]}}
        with tempfile.TemporaryDirectory() as tmp, patch(
            "output_paths.dp_cycle_store_dir", return_value=tmp,
        ), patch("requests.sessions.Session.get", return_value=response):
            con = duckdb.connect(":memory:")
            init_schema(con)
            meta = ingest_main_force(con)
            rows = con.execute("SELECT net_yi, small_yi, mid_yi FROM main_force_daily "
                               "ORDER BY date").fetchall()
            self.assertEqual(rows, [(1, 1, 2), (None, 0, 2)])
            self.assertFalse(meta["ok"])
            self.assertTrue(meta["partial"])
            self.assertEqual(meta["missing_net_rows"], 1)
            self.assertEqual(meta.get("last_date"), "2024-01-02")
            self.assertEqual(meta.get("latest_raw_date"), "2024-01-03")
            self.assertEqual(meta.get("first_date"), "2024-01-02")
            self.assertIsNone(meta.get("last_净额_亿元"))
            self.assertEqual(str(con.execute("SELECT last_date FROM series_meta "
                                            "WHERE series = 'main_force_daily'").fetchone()[0]),
                             "2024-01-02")
            Path(tmp, "_em_fflow_raw.json").unlink()
            con2 = duckdb.connect(":memory:")
            init_schema(con2)
            with patch("data_source.dp_cycle.main_force_em.fetch_em_market_fund_flow",
                       return_value=(pd.DataFrame(), {"ok": False, "error": "offline"})):
                offline = ingest_main_force(con2)
            self.assertEqual(con2.execute("SELECT net_yi, small_yi, mid_yi FROM main_force_daily "
                                          "ORDER BY date").fetchall(), rows)
            self.assertFalse(offline["ok"])
            self.assertEqual(offline["missing_net_rows"], 1)
            self.assertIsNone(offline.get("latest_raw_date"))
            self.assertEqual(offline.get("last_date"), "2024-01-02")
            con2.close()
            con.close()

    def test_split_only_ingest_has_no_valid_net_date(self):
        response = Mock()
        response.json.return_value = {"data": {"klines": [
            "2024-01-03,-,0,200000000,-,-",
        ]}}
        with tempfile.TemporaryDirectory() as tmp, patch(
            "output_paths.dp_cycle_store_dir", return_value=tmp,
        ), patch("requests.sessions.Session.get", return_value=response):
            con = duckdb.connect(":memory:")
            init_schema(con)
            meta = ingest_main_force(con)
            self.assertEqual(con.execute("SELECT net_yi, small_yi, mid_yi FROM main_force_daily")
                             .fetchone(), (None, 0, 2))
            self.assertFalse(meta["ok"])
            self.assertTrue(meta["partial"])
            self.assertEqual(meta["missing_net_rows"], 1)
            self.assertIsNone(meta.get("last_date"))
            self.assertEqual(meta.get("latest_raw_date"), "2024-01-03")
            self.assertIsNone(con.execute("SELECT last_date FROM series_meta "
                                          "WHERE series = 'main_force_daily'").fetchone()[0])
            con.close()

    def test_gui_and_diagnosis_do_not_treat_split_only_row_as_valid_net(self):
        from datetime import date

        from data_source.dp_cycle.cross_check import check_main_force
        from data_source.dp_cycle.diagnosis import _availability, _current_week
        from data_source.dp_cycle.gui_bundle import load_dp_cycle_gui_bundle

        with tempfile.TemporaryDirectory() as tmp, patch("output_paths.dp_cycle_store_dir", return_value=tmp):
            con = duckdb.connect(str(Path(tmp, "dp_cycle.duckdb")))
            init_schema(con)
            con.execute("INSERT INTO main_force_daily(date, mid_yi, source) "
                        "VALUES ('2024-01-03', 2, 'em_market_main')")
            con.close()
            bundle = load_dp_cycle_gui_bundle(as_of=date(2024, 1, 3), refresh=False)
            daily = bundle["main_force_daily"]
            self.assertEqual(len(daily), 1)
            self.assertTrue(pd.isna(daily.iloc[0]["净额_亿元"]))
            self.assertTrue(bundle["main_force_weekly"].empty)
            self.assertFalse(bundle["meta"]["01_main_force"]["ok"])
            self.assertEqual(_availability(daily, ["净额_亿元"], pd.Timestamp("2024-01-03"), 10)["status"], "gap")
            self.assertIsNone(_current_week(daily, pd.Timestamp("2024-01-03"),
                                            completed_end=pd.Timestamp("2023-12-29"))["main_force"])
            try:
                quality = check_main_force(daily)
            except IndexError:
                self.fail("empty net values must be reported as unavailable")
            self.assertEqual(quality["rows"], 0)
            self.assertIsNot(quality["stable"], True)

    def test_gui_latest_split_only_date_is_marked_partial(self):
        from datetime import date

        from data_source.dp_cycle.diagnosis import _availability, _current_week
        from data_source.dp_cycle.gui_bundle import load_dp_cycle_gui_bundle

        with tempfile.TemporaryDirectory() as tmp, patch("output_paths.dp_cycle_store_dir", return_value=tmp):
            con = duckdb.connect(str(Path(tmp, "dp_cycle.duckdb")))
            init_schema(con)
            con.execute("INSERT INTO main_force_daily(date, net_yi, mid_yi, source) VALUES "
                        "('2024-01-02', 1, 2, 'em_market_main'), "
                        "('2024-01-03', NULL, 2, 'em_market_main')")
            con.close()
            bundle = load_dp_cycle_gui_bundle(as_of=date(2024, 1, 3), refresh=False)
            daily = bundle["main_force_daily"]
            self.assertFalse(bundle["meta"]["01_main_force"]["ok"])
            self.assertTrue(bundle["meta"]["01_main_force"]["partial"])
            self.assertEqual(_availability(daily, ["净额_亿元"], pd.Timestamp("2024-01-03"), 10)
                             ["observation_date"], "2024-01-02")
            week = _current_week(daily, pd.Timestamp("2024-01-03"), completed_end=pd.Timestamp("2023-12-29"))
            self.assertEqual(week["last_observation_date"], "2024-01-02")
            self.assertEqual(week["main_force"], 1)
            self.assertEqual(week["observed_trading_days"], 1)

    def test_legacy_mixed_source_is_not_treated_as_each_fields_source(self):
        previous = pd.DataFrame([{
            "date": "2024-01-02", "净额_亿元": 9, "小单_亿元": 1,
            "source": "em_market_main | ths_industry_sum",
        }])
        fresh = pd.DataFrame([{
            "date": "2024-01-02", "净额_亿元": 11, "source": "em_market_main",
        }])
        row = union_main_force_frames(previous, fresh).iloc[0]
        self.assertEqual([row["净额_亿元"], row["小单_亿元"]], [11, 1])
        self.assertEqual(row["source"], "mixed_or_unknown")

    def test_new_databases_initialize_after_connections_are_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(12):
                con = duckdb.connect(str(Path(tmp, f"db_{i}.duckdb")))
                init_schema(con)
                tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
                con.close()
                del con
                self.assertIn("main_force_daily", tables, f"database {i} was not initialized")

    def test_offline_ingest_preserves_all_split_values_and_csv_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            dates = pd.date_range("2024-01-01", periods=126)
            klines = [f"{d:%Y-%m-%d},100000000,0,200000000,300000000,400000000"
                      for d in dates]
            Path(tmp, "_em_fflow_raw.json").write_text(
                json.dumps({"data": {"klines": klines}}), encoding="utf-8")
            db_path = str(Path(tmp, "main_force.duckdb"))
            con = duckdb.connect(db_path)
            init_schema(con)
            con.executemany(
                "INSERT INTO main_force_daily(date, net_yi, source) VALUES (?, ?, ?)",
                [(d.date(), 1.0, "em_market_main") for d in dates],
            )
            con.execute("INSERT INTO main_force_daily(date, net_yi, inflow_yi, source) "
                        "VALUES ('2024-06-01', 5, 7, 'ths_industry_sum')")
            with patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
                "data_source.dp_cycle.main_force_em.fetch_em_market_fund_flow",
                return_value=(pd.DataFrame(), {"ok": False, "error": "offline"}),
            ):
                meta = ingest_main_force(con)
                result = con.execute(
                    "SELECT COUNT(*), COUNT(mid_yi), SUM(CASE WHEN small_yi = 0 THEN 1 ELSE 0 END) "
                    "FROM main_force_daily WHERE date < '2024-06-01'"
                ).fetchone()
                ths = con.execute("SELECT net_yi, inflow_yi, source FROM main_force_daily "
                                  "WHERE date = '2024-06-01'").fetchone()
                self.assertEqual(result, (126, 126, 126))
                self.assertEqual(ths, (5, 7, "ths_industry_sum"))
                self.assertEqual(meta["wrote"], 127)

                # Losing the raw JSON must not strip the saved CSV's splits or sources.
                Path(tmp, "_em_fflow_raw.json").unlink()
                con2 = duckdb.connect(":memory:")
                init_schema(con2)
                ingest_main_force(con2)
                self.assertEqual(con2.execute(
                    "SELECT COUNT(*), COUNT(mid_yi) FROM main_force_daily "
                    "WHERE date < '2024-06-01'"
                ).fetchone(), (126, 126))
                self.assertEqual(con2.execute("SELECT source FROM main_force_daily "
                                              "WHERE date = '2024-06-01'").fetchone()[0],
                                 "ths_industry_sum")
                con2.close()
            con.close()
            reopened = duckdb.connect(db_path, read_only=True)
            self.assertEqual(reopened.execute("SELECT COUNT(mid_yi) FROM main_force_daily "
                                               "WHERE date < '2024-06-01'").fetchone()[0], 126)
            reopened.close()

    def test_old_schema_and_mixed_sources_survive_offline_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "_em_fflow_raw.json").write_text(json.dumps({"data": {"klines": [
                "2024-01-02,100000000,0,200000000,300000000,400000000"
            ]}}), encoding="utf-8")
            con = duckdb.connect(":memory:")
            con.execute("CREATE TABLE main_force_daily (date DATE PRIMARY KEY, net_yi DOUBLE, "
                        "inflow_yi DOUBLE, outflow_yi DOUBLE, industry_n INTEGER, source VARCHAR)")
            con.execute("INSERT INTO main_force_daily VALUES "
                        "('2024-01-02', 9, 8, 7, 2, 'ths_industry_sum')")
            with patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
                "data_source.dp_cycle.main_force_em.fetch_em_market_fund_flow",
                return_value=(pd.DataFrame(), {"ok": False, "error": "offline"}),
            ):
                ingest_main_force(con)
            row = con.execute("SELECT net_yi, inflow_yi, outflow_yi, industry_n, "
                              "small_yi, mid_yi, large_yi, super_yi, source "
                              "FROM main_force_daily").fetchone()
            self.assertEqual(row[:8], (9, 8, 7, 2, 0, 2, 3, 4))
            self.assertEqual(row[8], "mixed_or_unknown")
            con.close()

    def test_persisted_mixed_source_stays_unattributed_after_next_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "_em_fflow_raw.json").write_text(json.dumps({"data": {"klines": [
                "2024-01-02,100000000,100000000,200000000,300000000,400000000"
            ]}}), encoding="utf-8")
            con = duckdb.connect(":memory:")
            init_schema(con)
            con.execute("INSERT INTO main_force_daily(date, net_yi, source) "
                        "VALUES ('2024-01-02', 9, 'ths_industry_sum')")
            with patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
                "data_source.dp_cycle.main_force_em.fetch_em_market_fund_flow",
                return_value=(pd.DataFrame(), {"ok": False, "error": "offline"}),
            ):
                ingest_main_force(con)
            fresh = pd.DataFrame([{"date": "2024-01-02", "净额_亿元": 11}])
            with patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
                "data_source.dp_cycle.main_force_em.fetch_em_market_fund_flow",
                return_value=(fresh, {"ok": True}),
            ):
                meta = ingest_main_force(con)
            row = con.execute("SELECT net_yi, small_yi, source FROM main_force_daily").fetchone()
            self.assertEqual(row, (11, 1, "mixed_or_unknown"))
            self.assertEqual(meta["source"], "mixed_or_unknown")
            con.close()

    def test_existing_database_split_values_take_priority_over_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "_em_fflow_raw.json").write_text(json.dumps({"data": {"klines": [
                "2024-01-02,100000000,100000000,200000000,300000000,400000000"
            ]}}), encoding="utf-8")
            con = duckdb.connect(":memory:")
            init_schema(con)
            con.execute("INSERT INTO main_force_daily(date, net_yi, small_yi, mid_yi, source) "
                        "VALUES ('2024-01-02', 9, 0, 7, 'em_market_main')")
            with patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
                "data_source.dp_cycle.main_force_em.fetch_em_market_fund_flow",
                return_value=(pd.DataFrame(), {"ok": False, "error": "offline"}),
            ):
                ingest_main_force(con)
            self.assertEqual(con.execute("SELECT net_yi, small_yi, mid_yi FROM main_force_daily")
                             .fetchone(), (9, 0, 7))
            con.close()

    def test_both_cache_files_contribute_unique_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "_em_fflow_raw.json").write_text(json.dumps({"data": {"klines": [
                "2024-01-02,100000000,0,200000000,300000000,400000000"
            ]}}), encoding="utf-8")
            csv_dir = Path(tmp, "main_force_ths")
            csv_dir.mkdir()
            Path(csv_dir, "em_market_daily.csv").write_text(
                "date,净额_亿元,source\n2024-01-03,9,ths_industry_sum\n", encoding="utf-8")
            con = duckdb.connect(":memory:")
            init_schema(con)
            with patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
                "data_source.dp_cycle.main_force_em.fetch_em_market_fund_flow",
                return_value=(pd.DataFrame(), {"ok": False, "error": "offline"}),
            ):
                ingest_main_force(con)
            self.assertEqual(con.execute("SELECT net_yi, source FROM main_force_daily ORDER BY date")
                             .fetchall(), [(1, "em_market_main"), (9, "ths_industry_sum")])
            con.close()

    def test_csv_reader_keeps_legacy_values_without_inventing_a_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_dir = Path(tmp, "main_force_ths")
            csv_dir.mkdir()
            path = Path(csv_dir, "em_market_daily.csv")
            path.write_text("date,净额_亿元,中单_亿元\n2024-01-02,1,0\n", encoding="utf-8")
            row = load_em_fund_flow_cache(str(path)).iloc[0]
            self.assertIn("中单_亿元", row.index)
            self.assertEqual(row["中单_亿元"], 0)
            self.assertTrue("source" not in row.index or pd.isna(row["source"]))
            con = duckdb.connect(":memory:")
            init_schema(con)
            with patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
                "data_source.dp_cycle.main_force_em.fetch_em_market_fund_flow",
                return_value=(pd.DataFrame(), {"ok": False, "error": "offline"}),
            ):
                meta = ingest_main_force(con)
            self.assertEqual(con.execute("SELECT mid_yi, source FROM main_force_daily").fetchone(),
                             (0, "unknown"))
            self.assertEqual(meta["source"], "unknown")
            con.close()

    def test_fresh_partial_response_updates_only_its_non_null_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "_em_fflow_raw.json").write_text(json.dumps({"data": {"klines": [
                "2024-01-02,100000000,100000000,200000000,300000000,400000000"
            ]}}), encoding="utf-8")
            con = duckdb.connect(":memory:")
            init_schema(con)
            con.execute("INSERT INTO main_force_daily(date, net_yi, inflow_yi, source) "
                        "VALUES ('2024-01-02', 9, 8, 'ths_industry_sum')")
            fresh = pd.DataFrame([{"date": "2024-01-02", "净额_亿元": 11,
                                   "小单_亿元": 0, "中单_亿元": None}])
            with patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
                "data_source.dp_cycle.main_force_em.fetch_em_market_fund_flow",
                return_value=(fresh, {"ok": True}),
            ):
                ingest_main_force(con)
            row = con.execute("SELECT net_yi, small_yi, mid_yi, inflow_yi, source "
                              "FROM main_force_daily").fetchone()
            self.assertEqual(row[:4], (11, 0, 2, 8))
            self.assertEqual(row[4], "mixed_or_unknown")
            con.close()

    def test_ths_fallback_remains_ths_when_recovered_from_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = duckdb.connect(":memory:")
            init_schema(con)
            ths = pd.DataFrame([{"date": "2024-01-02", "净额_亿元": 1, "流入_亿元": 2}])
            with patch("output_paths.dp_cycle_store_dir", return_value=tmp), patch(
                "data_source.dp_cycle.main_force_em.fetch_em_market_fund_flow",
                return_value=(pd.DataFrame(), {"ok": False, "error": "offline"}),
            ), patch("data_source.dp_cycle.main_force_ths.upsert_today_from_ths",
                     return_value=(ths, {"ok": True})):
                ingest_main_force(con)
                self.assertEqual(con.execute("SELECT source FROM main_force_daily").fetchone()[0],
                                 "ths_industry_sum")
                con2 = duckdb.connect(":memory:")
                init_schema(con2)
                meta = ingest_main_force(con2)
                self.assertEqual(con2.execute("SELECT source FROM main_force_daily").fetchone()[0],
                                 "ths_industry_sum")
                self.assertEqual(meta["source"], "ths_industry_sum")
                con2.close()
            con.close()


if __name__ == "__main__":
    unittest.main()
