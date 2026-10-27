import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb
import pandas as pd

from data_source.dp_cycle import margin
from data_source.dp_cycle.ingest import ingest_margin
from data_source.dp_cycle.store import init_schema


def macro(rows):
    return pd.DataFrame(rows, columns=["日期", "融资余额", "融资融券余额"])


def detail(code_col, rows):
    return pd.DataFrame(rows, columns=[code_col, "融资余额"])


def immediate(fn, *args, **kwargs):
    return fn(*args, **{k: v for k, v in kwargs.items() if k not in ("retries", "delay")})


class MarginCompletenessTests(unittest.TestCase):
    def setUp(self):
        retry = patch("data_source.dp_cycle.margin.call_with_retry", side_effect=immediate)
        retry.start()
        self.addCleanup(retry.stop)

    def test_macro_keeps_each_side_and_marks_missing_dates_partial(self):
        sh = macro([
            ("2024-01-02", 0, 0), ("2024-01-03", 10, 11), ("2024-01-04", 12, 13),
        ])
        sz = macro([
            ("2024-01-02", 0, 0), ("2024-01-04", 20, 21), ("2024-01-05", 30, 31),
        ])
        with patch("akshare.macro_china_market_margin_sh", return_value=sh), patch(
            "akshare.macro_china_market_margin_sz", return_value=sz
        ):
            data, meta = margin.fetch_margin_daily(cross_check=False)

        self.assertIn("rz_sh", data.columns)
        rows = data.set_index("date")
        self.assertEqual(rows.loc[pd.Timestamp("2024-01-02"), "融资余额_亿元"], 0)
        self.assertEqual(rows.loc[pd.Timestamp("2024-01-04"), "融资余额_亿元"], 32)
        self.assertEqual(rows.loc[pd.Timestamp("2024-01-04"), "两融余额_亿元"], 34)
        sh_only = rows.loc[pd.Timestamp("2024-01-03")]
        sz_only = rows.loc[pd.Timestamp("2024-01-05")]
        self.assertEqual(sh_only["rz_sh"], 10)
        self.assertTrue(pd.isna(sh_only["rz_sz"]))
        self.assertTrue(pd.isna(sh_only["融资余额_亿元"]))
        self.assertTrue(pd.isna(sh_only["两融余额_亿元"]))
        self.assertEqual(sz_only["rz_sz"], 30)
        self.assertTrue(pd.isna(sz_only["融资余额_亿元"]))
        self.assertFalse(meta["ok"])
        self.assertTrue(meta["partial"])
        self.assertEqual(meta["last_date"], "2024-01-04")
        self.assertEqual(meta["latest_raw_date"], "2024-01-05")
        self.assertIsNone(meta["last_融资余额_亿元"])
        self.assertEqual(meta["primary"]["sources"]["sh"]["rows"], 3)
        self.assertEqual(meta["primary"]["sources"]["sz"]["rows"], 3)

    def test_macro_keeps_available_side_if_other_request_fails(self):
        sh = macro([("2024-01-02", 10, 11)])
        with patch("akshare.macro_china_market_margin_sh", return_value=sh), patch(
            "akshare.macro_china_market_margin_sz", side_effect=RuntimeError("offline")
        ):
            data, meta = margin.fetch_margin_daily(cross_check=False)
        self.assertEqual(len(data), 1)
        self.assertEqual(data.iloc[0]["rz_sh"], 10)
        self.assertTrue(pd.isna(data.iloc[0]["融资余额_亿元"]))
        self.assertTrue(meta["partial"])
        self.assertFalse(meta["ok"])
        self.assertIn("offline", meta["primary"]["sources"]["sz"]["error"])
        self.assertIsNone(meta["last_date"])

    def test_both_macro_requests_fail_with_both_errors(self):
        with patch("akshare.macro_china_market_margin_sh", side_effect=RuntimeError("sh offline")), patch(
            "akshare.macro_china_market_margin_sz", side_effect=RuntimeError("sz offline")
        ):
            data, meta = margin.fetch_margin_daily(cross_check=False)
        self.assertTrue(data.empty)
        self.assertFalse(meta["ok"])
        self.assertFalse(meta["partial"])
        self.assertIn("sh offline", meta["primary"]["sources"]["sh"]["error"])
        self.assertIn("sz offline", meta["primary"]["sources"]["sz"]["error"])

    def test_complete_macro_zero_is_valid(self):
        day = macro([("2024-01-02", 0, 0)])
        with patch("akshare.macro_china_market_margin_sh", return_value=day), patch(
            "akshare.macro_china_market_margin_sz", return_value=day
        ):
            data, meta = margin.fetch_margin_daily(cross_check=False)
        self.assertEqual(data.iloc[0]["融资余额_亿元"], 0)
        self.assertTrue(meta["ok"])
        self.assertIn("partial", meta)
        self.assertFalse(meta["partial"])
        self.assertEqual(meta["last_融资余额_亿元"], 0)

    def test_missing_margin_total_column_is_not_substituted(self):
        sh = pd.DataFrame({"日期": ["2024-01-02"], "融资余额": [10]})
        sz = macro([("2024-01-02", 20, 21)])
        with patch("akshare.macro_china_market_margin_sh", return_value=sh), patch(
            "akshare.macro_china_market_margin_sz", return_value=sz
        ):
            data, meta = margin.fetch_margin_daily(cross_check=False)
        self.assertEqual(data.iloc[0]["融资余额_亿元"], 30)
        self.assertTrue(pd.isna(data.iloc[0]["两融余额_亿元"]))
        self.assertTrue(meta["partial"])

    def test_board_fetch_keeps_available_side_and_error_in_batch(self):
        sh = detail("标的证券代码", [("600000", 100000000), ("688001", 0)])
        with patch("akshare.stock_margin_detail_sse", return_value=sh), patch(
            "akshare.stock_margin_detail_szse", side_effect=RuntimeError("sz offline")
        ):
            one = margin.fetch_margin_boards_one_day("2024-01-02")
            batch = margin.fetch_margin_boards_for_dates(["2024-01-02"], sleep_s=0)
        self.assertEqual(one["沪主板_亿元"], 1)
        self.assertEqual(one["科创板_亿元"], 0)
        for name in ("深主板_亿元", "创业板_亿元", "主板_亿元", "深市_亿元"):
            self.assertTrue(pd.isna(one[name]), name)
        self.assertFalse(one["ok"])
        self.assertTrue(one["partial"])
        self.assertEqual((one["_sse_n"], one["_szse_n"]), (2, 0))
        self.assertIn("sz offline", one["errors"]["szse"])
        self.assertEqual(len(batch), 1)
        self.assertTrue(batch.iloc[0]["partial"])
        self.assertIn("sz offline", batch.iloc[0]["errors"]["szse"])

    def test_only_shenzhen_board_does_not_invent_shanghai_values(self):
        sz = detail("证券代码", [("000001", 100000000), ("300001", 0)])
        row = margin.aggregate_margin_boards_from_details(None, sz, day=pd.Timestamp("2024-01-02"))
        self.assertTrue(pd.isna(row["沪主板_亿元"]))
        self.assertTrue(pd.isna(row["科创板_亿元"]))
        self.assertTrue(pd.isna(row["主板_亿元"]))
        self.assertEqual(row["深市_亿元"], 1)

    def test_incomplete_board_values_remain_missing_and_report_which_side(self):
        sh = detail("标的证券代码", [("600000", None), ("688001", 100000000)])
        sz = detail("证券代码", [("000001", 0), ("300001", 200000000)])
        with patch("akshare.stock_margin_detail_sse", return_value=sh), patch(
            "akshare.stock_margin_detail_szse", return_value=sz
        ):
            row = margin.fetch_margin_boards_one_day("2024-01-02")
        self.assertTrue(pd.isna(row["沪主板_亿元"]))
        self.assertEqual(row["科创板_亿元"], 1)
        self.assertTrue(row["partial"])
        self.assertIn("sse", row["errors"])

    def test_both_board_sides_and_real_zero_are_complete(self):
        sh = detail("标的证券代码", [("600000", 100000000), ("688001", 0)])
        sz = detail("证券代码", [("000001", 0), ("300001", 200000000)])
        with patch("akshare.stock_margin_detail_sse", return_value=sh), patch(
            "akshare.stock_margin_detail_szse", return_value=sz
        ):
            row = margin.fetch_margin_boards_one_day("2024-01-02")
        self.assertTrue(row["ok"])
        self.assertIn("partial", row)
        self.assertFalse(row["partial"])
        self.assertEqual((row["沪主板_亿元"], row["深主板_亿元"], row["科创板_亿元"],
                          row["创业板_亿元"], row["主板_亿元"], row["深市_亿元"]),
                         (1, 0, 0, 2, 1, 2))

    def test_ingest_retries_partial_sides_and_preserves_previously_fetched_values(self):
        day = macro([("2024-01-02", 10, 11)])
        sh = detail("标的证券代码", [("600000", 100000000), ("688001", 0)])
        sz = detail("证券代码", [("000001", 0), ("300001", 200000000)])
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        init_schema(con)
        with patch("akshare.macro_china_market_margin_sh", return_value=day), patch(
            "akshare.macro_china_market_margin_sz", return_value=day
        ), patch("akshare.stock_margin_detail_sse", side_effect=[sh, RuntimeError("sh offline")]) as sse, patch(
            "akshare.stock_margin_detail_szse", side_effect=[RuntimeError("sz offline"), sz]
        ) as szse, patch("data_source.dp_cycle.ingest._invalidate_load_cache"), patch(
            "data_source.dp_cycle.gui_bundle.invalidate_web_payload_cache"
        ):
            first = ingest_margin(con, cross_check=False)
            row = con.execute("SELECT sh_main_yi, sz_main_yi, chinext_yi, star_yi FROM margin_daily").fetchone()
            self.assertEqual(row, (1, None, None, 0))
            self.assertFalse(first["ok"])
            self.assertTrue(first["partial"])
            self.assertEqual(first["boards"]["partial"], 1)
            self.assertIn("sz offline", first["boards"]["details"][0]["errors"]["szse"])

            second = ingest_margin(con, cross_check=False)
            row = con.execute("SELECT sh_main_yi, sz_main_yi, chinext_yi, star_yi FROM margin_daily").fetchone()
            self.assertEqual(row, (1, 0, 2, 0))
            self.assertFalse(second["ok"])
            self.assertTrue(second["partial"])
            self.assertEqual(second["boards"]["planned"], 1)
            self.assertIn("sh offline", second["boards"]["details"][0]["errors"]["sse"])

            third = ingest_margin(con, cross_check=False)
            self.assertTrue(third["ok"])
            self.assertEqual(third["boards"]["planned"], 0)
            self.assertEqual((sse.call_count, szse.call_count), (2, 2))

    def test_board_fetch_failure_keeps_existing_partial_values_for_retry(self):
        day = macro([("2024-01-02", 10, 11)])
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        init_schema(con)
        con.execute("INSERT INTO margin_daily(date, sh_main_yi, star_yi) "
                    "VALUES ('2024-01-02', 1, 0)")
        with patch("akshare.macro_china_market_margin_sh", return_value=day), patch(
            "akshare.macro_china_market_margin_sz", return_value=day
        ), patch("akshare.stock_margin_detail_sse", side_effect=RuntimeError("sh offline")), patch(
            "akshare.stock_margin_detail_szse", side_effect=RuntimeError("sz offline")
        ), patch("data_source.dp_cycle.ingest._invalidate_load_cache"), patch(
            "data_source.dp_cycle.gui_bundle.invalidate_web_payload_cache"
        ):
            meta = ingest_margin(con, cross_check=False)
        self.assertEqual(con.execute("SELECT sh_main_yi, sz_main_yi, chinext_yi, star_yi "
                                     "FROM margin_daily").fetchone(), (1, None, None, 0))
        self.assertFalse(meta["ok"])
        self.assertTrue(meta["partial"])
        self.assertEqual((meta["boards"]["planned"], meta["boards"]["failed"]), (1, 1))
        self.assertIn("sh offline", meta["boards"]["details"][0]["errors"]["sse"])
        self.assertIn("sz offline", meta["boards"]["details"][0]["errors"]["szse"])

    def test_skipped_board_update_reports_remaining_partial_data(self):
        day = macro([("2024-01-02", 10, 11)])
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        init_schema(con)
        con.execute("INSERT INTO margin_daily(date, sh_main_yi, star_yi) VALUES ('2024-01-02', 1, 0)")
        with patch("akshare.macro_china_market_margin_sh", return_value=day), patch(
            "akshare.macro_china_market_margin_sz", return_value=day
        ), patch("data_source.dp_cycle.ingest._invalidate_load_cache"), patch(
            "data_source.dp_cycle.gui_bundle.invalidate_web_payload_cache"
        ):
            meta = ingest_margin(con, cross_check=False, board_max_days=0)
        self.assertIn("skipped", meta["boards"])
        self.assertTrue(meta["boards"]["skipped"])
        self.assertEqual(meta["boards"]["remaining_partial"], 1)
        self.assertTrue(meta["partial"])
        self.assertFalse(meta["ok"])

    def test_fully_failed_board_is_not_counted_as_cached_partial(self):
        day = macro([("2024-01-02", 10, 11)])
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        init_schema(con)
        with patch("akshare.macro_china_market_margin_sh", return_value=day), patch(
            "akshare.macro_china_market_margin_sz", return_value=day
        ), patch("akshare.stock_margin_detail_sse", side_effect=RuntimeError("sh offline")), patch(
            "akshare.stock_margin_detail_szse", side_effect=RuntimeError("sz offline")
        ), patch("data_source.dp_cycle.ingest._invalidate_load_cache"), patch(
            "data_source.dp_cycle.gui_bundle.invalidate_web_payload_cache"
        ):
            meta = ingest_margin(con, cross_check=False)
        self.assertEqual(meta["boards"]["failed"], 1)
        self.assertEqual(meta["boards"]["remaining_partial"], 0)

    def test_limited_board_fetch_reports_unplanned_missing_dates(self):
        dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
        day = macro([(d, 10, 11) for d in dates])
        sh = detail("标的证券代码", [("600000", 100000000), ("688001", 0)])
        sz = detail("证券代码", [("000001", 0), ("300001", 200000000)])
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        init_schema(con)
        with patch("akshare.macro_china_market_margin_sh", return_value=day), patch(
            "akshare.macro_china_market_margin_sz", return_value=day
        ), patch("akshare.stock_margin_detail_sse", return_value=sh), patch(
            "akshare.stock_margin_detail_szse", return_value=sz
        ), patch("data_source.dp_cycle.ingest._invalidate_load_cache"), patch(
            "data_source.dp_cycle.gui_bundle.invalidate_web_payload_cache"
        ):
            first = ingest_margin(con, cross_check=False, board_max_days=1)
            self.assertFalse(first["ok"])
            self.assertTrue(first["partial"])
            boards = first["boards"]
            self.assertEqual((boards["target"], boards["complete"], boards["remaining_partial"],
                              boards["remaining_missing"], boards["remaining_unplanned"]), (3, 1, 0, 2, 2))
            second = ingest_margin(con, cross_check=False, board_max_days=1)
            self.assertFalse(second["ok"])
            self.assertEqual((second["boards"]["complete"], second["boards"]["remaining_missing"],
                              second["boards"]["remaining_unplanned"]), (2, 1, 1))
            third = ingest_margin(con, cross_check=False, board_max_days=1)
            self.assertTrue(third["ok"])
            self.assertFalse(third["partial"])
            self.assertEqual((third["boards"]["complete"], third["boards"]["remaining_missing"],
                              third["boards"]["remaining_unplanned"]), (3, 0, 0))

    def test_board_coverage_counts_complete_partial_and_missing_separately(self):
        dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
        day = macro([(d, 10, 11) for d in dates])
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        init_schema(con)
        con.execute("INSERT INTO margin_daily(date, sh_main_yi, sz_main_yi, chinext_yi, star_yi) VALUES "
                    "('2024-01-02', 1, NULL, NULL, 0), ('2024-01-03', 1, 0, 2, 0)")
        with patch("akshare.macro_china_market_margin_sh", return_value=day), patch(
            "akshare.macro_china_market_margin_sz", return_value=day
        ), patch("data_source.dp_cycle.ingest._invalidate_load_cache"), patch(
            "data_source.dp_cycle.gui_bundle.invalidate_web_payload_cache"
        ):
            meta = ingest_margin(con, cross_check=False, board_max_days=0)
        boards = meta["boards"]
        self.assertEqual((boards["target"], boards["complete"], boards["remaining_partial"],
                          boards["remaining_missing"], boards["remaining_unplanned"]), (3, 1, 1, 1, 2))
        self.assertFalse(meta["ok"])
        self.assertTrue(meta["partial"])

    def test_ingest_series_metadata_uses_latest_complete_total(self):
        sh = macro([("2024-01-02", 10, 11), ("2024-01-03", 12, 13)])
        sz = macro([("2024-01-02", 20, 21)])
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        init_schema(con)
        with patch("akshare.macro_china_market_margin_sh", return_value=sh), patch(
            "akshare.macro_china_market_margin_sz", return_value=sz
        ), patch("data_source.dp_cycle.ingest._invalidate_load_cache"), patch(
            "data_source.dp_cycle.gui_bundle.invalidate_web_payload_cache"
        ):
            meta = ingest_margin(con, cross_check=False, board_max_days=0)
        self.assertEqual(con.execute("SELECT financing_yi, margin_total_yi FROM margin_daily ORDER BY date").fetchall(),
                         [(30, 32), (None, None)])
        last_date, note = con.execute("SELECT last_date, note FROM series_meta WHERE series = 'margin_daily'").fetchone()
        self.assertEqual(str(last_date), "2024-01-02")
        self.assertIn("partial", note)
        self.assertFalse(meta["ok"])
        self.assertEqual(meta["latest_raw_date"], "2024-01-03")

    def test_partial_macro_refresh_preserves_existing_totals_as_cached(self):
        sh = macro([("2024-01-02", 10, 11), ("2024-01-03", 12, 13)])
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        init_schema(con)
        con.execute("INSERT INTO margin_daily(date, financing_yi, margin_total_yi) "
                    "VALUES ('2024-01-02', 30, 32)")
        with patch("akshare.macro_china_market_margin_sh", return_value=sh), patch(
            "akshare.macro_china_market_margin_sz", side_effect=RuntimeError("sz offline")
        ), patch("data_source.dp_cycle.ingest._invalidate_load_cache"), patch(
            "data_source.dp_cycle.gui_bundle.invalidate_web_payload_cache"
        ):
            meta = ingest_margin(con, cross_check=False, board_max_days=0)
        self.assertEqual(con.execute("SELECT financing_yi, margin_total_yi FROM margin_daily ORDER BY date").fetchall(),
                         [(30, 32), (None, None)])
        self.assertFalse(meta["ok"])
        self.assertTrue(meta["partial"])
        self.assertEqual(meta["cached_totals"], 1)

    def test_cached_margin_total_and_latest_stored_date_are_reported(self):
        sh = macro([("2024-01-02", 10, None)])
        sz = macro([("2024-01-02", 20, 21)])
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        init_schema(con)
        con.execute("INSERT INTO margin_daily(date, financing_yi, margin_total_yi) VALUES "
                    "('2024-01-02', 29, 32), ('2024-01-03', 40, 42)")
        with patch("akshare.macro_china_market_margin_sh", return_value=sh), patch(
            "akshare.macro_china_market_margin_sz", return_value=sz
        ), patch("data_source.dp_cycle.ingest._invalidate_load_cache"), patch(
            "data_source.dp_cycle.gui_bundle.invalidate_web_payload_cache"
        ):
            meta = ingest_margin(con, cross_check=False, board_max_days=0)
        self.assertEqual(con.execute("SELECT financing_yi, margin_total_yi FROM margin_daily ORDER BY date").fetchall(),
                         [(30, 32), (40, 42)])
        self.assertEqual(meta["cached_totals"], 2)
        last = con.execute("SELECT last_date FROM series_meta WHERE series = 'margin_daily'").fetchone()[0]
        self.assertEqual(str(last), "2024-01-03")

    def test_short_partial_response_recomputes_percentile_with_full_cached_history(self):
        dates = pd.date_range("2024-01-01", periods=30)
        values = list(range(2, 31)) + [1]
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        init_schema(con)
        con.executemany("INSERT INTO margin_daily(date, financing_yi, margin_total_yi) VALUES (?, ?, ?)",
                        [(d.date(), value, value + 1) for d, value in zip(dates, values)])
        short = macro([(str(d.date()), 10, 11) for d in dates[-2:]])
        with patch("akshare.macro_china_market_margin_sh", return_value=short), patch(
            "akshare.macro_china_market_margin_sz", side_effect=RuntimeError("sz offline")
        ), patch("data_source.dp_cycle.ingest._invalidate_load_cache"), patch(
            "data_source.dp_cycle.gui_bundle.invalidate_web_payload_cache"
        ):
            meta = ingest_margin(con, cross_check=False, board_max_days=0)
        rows = con.execute("SELECT financing_yi, margin_total_yi, pct_all, pct_5y "
                           "FROM margin_daily ORDER BY date").fetchall()
        self.assertEqual(len(rows), 30)
        self.assertEqual([row[0] for row in rows], values)
        self.assertEqual([row[1] for row in rows], [value + 1 for value in values])
        self.assertAlmostEqual(rows[-1][2], 100 / 30)
        self.assertAlmostEqual(rows[-1][3], 100 / 30)
        self.assertEqual(meta["cached_totals"], 30)
        self.assertFalse(meta["ok"])

    def test_short_complete_response_updates_values_without_losing_history(self):
        dates = pd.date_range("2024-01-01", periods=6)
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        init_schema(con)
        con.executemany("INSERT INTO margin_daily(date, financing_yi, margin_total_yi) VALUES (?, ?, ?)",
                        [(d.date(), n, n + 1) for n, d in enumerate(dates[:5], 1)])
        sh = macro([(str(dates[3].date()), 3, 3), (str(dates[5].date()), 0, 0)])
        sz = macro([(str(dates[3].date()), 0, 0), (str(dates[5].date()), 10, 10)])
        with patch("akshare.macro_china_market_margin_sh", return_value=sh), patch(
            "akshare.macro_china_market_margin_sz", return_value=sz
        ), patch("data_source.dp_cycle.ingest._invalidate_load_cache"), patch(
            "data_source.dp_cycle.gui_bundle.invalidate_web_payload_cache"
        ):
            ingest_margin(con, cross_check=False, board_max_days=0)
        rows = con.execute("SELECT financing_yi, pct_all FROM margin_daily ORDER BY date").fetchall()
        self.assertEqual([row[0] for row in rows], [1, 2, 3, 3, 5, 10])
        self.assertAlmostEqual(rows[3][1], 100 * 3.5 / 4)
        self.assertEqual(rows[-1][1], 100)

    def test_each_missing_atomic_column_is_retried(self):
        day = macro([("2024-01-02", 10, 11)])
        sh = detail("标的证券代码", [("600000", 100000000), ("688001", 0)])
        sz = detail("证券代码", [("000001", 0), ("300001", 200000000)])
        for missing in range(4):
            with self.subTest(missing=missing):
                con = duckdb.connect(":memory:")
                try:
                    init_schema(con)
                    values = [1, 0, 2, 0]
                    values[missing] = None
                    con.execute("INSERT INTO margin_daily(date, sh_main_yi, sz_main_yi, chinext_yi, star_yi) "
                                "VALUES ('2024-01-02', ?, ?, ?, ?)", values)
                    with patch("akshare.macro_china_market_margin_sh", return_value=day), patch(
                        "akshare.macro_china_market_margin_sz", return_value=day
                    ), patch("akshare.stock_margin_detail_sse", return_value=sh) as sse, patch(
                        "akshare.stock_margin_detail_szse", return_value=sz
                    ) as szse, patch("data_source.dp_cycle.ingest._invalidate_load_cache"), patch(
                        "data_source.dp_cycle.gui_bundle.invalidate_web_payload_cache"
                    ):
                        meta = ingest_margin(con, cross_check=False)
                    self.assertEqual(meta["boards"]["planned"], 1)
                    self.assertEqual((sse.call_count, szse.call_count), (1, 1))
                    self.assertEqual(con.execute("SELECT sh_main_yi, sz_main_yi, chinext_yi, star_yi "
                                                 "FROM margin_daily").fetchone(), (1, 0, 2, 0))
                finally:
                    con.close()

    def test_rolling_percentile_excludes_missing_totals(self):
        frame = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=6),
                              "融资余额_亿元": [1, 2, None, 3, 4, 5]})
        enriched = margin.enrich_margin(frame)
        self.assertEqual(enriched.iloc[-1]["分位_近N年"], 100)


if __name__ == "__main__":
    unittest.main()
