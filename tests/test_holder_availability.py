import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle import store
from data_source.dp_cycle.holder_reduce import fetch_holder_reduces, visible_holder_reductions
from data_source.dp_cycle.ingest import ingest_holder_reduce


class HolderAvailabilityTests(unittest.TestCase):
    @staticmethod
    def raw_row(code, end, notice):
        return {"SECURITY_CODE": code, "HOLDER_NAME": "A", "END_DATE": end,
                "NOTICE_DATE": notice, "CHANGE_NUM": -100, "CLOSE_PRICE": 10}

    def test_source_records_missing_notice_without_claiming_it_was_known(self):
        response = Mock()
        response.json.return_value = {"result": {"pages": 1, "count": 3, "data": [
            self.raw_row("600001", "2026-10-20", "2026-10-20"),
            self.raw_row("600002", "2026-10-20", "2026-10-21"),
            self.raw_row("600003", "2026-10-20", None),
        ]}}
        with patch("data_source.dp_cycle.holder_reduce.requests.Session.get", return_value=response):
            rows, meta = fetch_holder_reduces()
        self.assertEqual(rows["notice_known"].tolist(), [True, True, False])
        self.assertEqual(rows.iloc[2]["notice_date"], pd.Timestamp("2026-10-20"))
        self.assertEqual(meta["notice_unknown"], 1)
        self.assertTrue(meta["partial"])
        self.assertFalse(meta["ok"])

    def test_known_dates_are_not_available_before_next_day_and_unknown_is_not_visible(self):
        frame = pd.DataFrame({
            "date": ["2026-10-20"] * 5 + ["2026-10-22"],
            "notice_date": ["2026-10-21", "2026-10-23", "2026-10-20", "2026-10-20", "2026-10-21", "2026-10-21"],
            "notice_known": [True, True, False, pd.NA, pd.NA, True],
            "code": ["A", "B", "C", "D", "E", "F"],
        })
        visible, meta = visible_holder_reductions(frame, as_of="2026-10-22")
        self.assertEqual(set(visible["code"]), {"A", "E"})
        self.assertEqual(meta["notice_unknown"], 2)
        self.assertEqual(meta["not_yet_available"], 1)
        self.assertEqual(meta["future_event"], 1)
        self.assertEqual(meta["status"], "partial")
        self.assertEqual(visible.loc[visible["code"] == "A", "available_at"].iloc[0], pd.Timestamp("2026-10-22"))

    def test_observed_same_day_notice_becomes_usable_the_next_day(self):
        frame = pd.DataFrame({"date": ["2026-10-20"], "notice_date": ["2026-10-20"],
                              "notice_known": [True], "amount_yi": [10]})
        before, _ = visible_holder_reductions(frame, as_of="2026-10-20")
        after, meta = visible_holder_reductions(frame, as_of="2026-10-21")
        self.assertTrue(before.empty)
        self.assertEqual(len(after), 1)
        self.assertEqual(meta["status"], "ok")

    def test_mixed_timezones_use_taipei_date_and_midnight_for_availability(self):
        frame = pd.DataFrame({
            "date": ["2026-10-20", "2026-10-20T20:30:00Z", "2026-10-20",
                     "2026-10-20", "2026-10-20", "2026-10-20T16:30:00Z"],
            "notice_date": ["2026-10-21", "2026-10-21T15:30:00Z", "2026-10-21T16:30:00Z",
                            "2026-10-21 23:30:00", "2026-10-20T16:30:00Z", "2026-10-20T17:00:00Z"],
            "notice_known": [True, True, True, True, pd.NA, pd.NA],
            "code": ["A", "B", "C", "D", "E", "F"],
        })
        before, _ = visible_holder_reductions(frame, as_of="2026-10-21T15:59:00Z")
        after, meta = visible_holder_reductions(frame, as_of="2026-10-21T16:00:00Z")
        local, _ = visible_holder_reductions(frame, as_of="2026-10-22 00:00:00")
        next_day, _ = visible_holder_reductions(frame, as_of="2026-10-22T16:00:00Z")
        self.assertTrue(before.empty)
        self.assertEqual(set(after["code"]), {"A", "B", "D", "E"})
        self.assertEqual(set(local["code"]), set(after["code"]))
        self.assertEqual(set(next_day["code"]), {"A", "B", "C", "D", "E"})
        self.assertTrue((after["available_at"] == pd.Timestamp("2026-10-22")).all())
        self.assertEqual(meta["notice_unknown"], 1)
        self.assertEqual(meta["not_yet_available"], 1)
        self.assertEqual(meta["status"], "partial")

    def test_aware_datetime_columns_compare_with_naive_local_cutoff(self):
        frame = pd.DataFrame({"date": pd.to_datetime(["2026-10-20T16:30:00Z"], utc=True),
                              "notice_date": pd.to_datetime(["2026-10-21T15:30:00Z"], utc=True),
                              "notice_known": [True]})
        before, _ = visible_holder_reductions(frame, as_of="2026-10-21 23:59:00")
        after, _ = visible_holder_reductions(frame, as_of="2026-10-22 00:00:00")
        self.assertTrue(before.empty)
        self.assertEqual(len(after), 1)

    def test_new_flags_are_persisted_in_in_memory_database(self):
        con = duckdb.connect(":memory:")
        store.init_schema(con)
        raw = pd.DataFrame({"code": ["600001", "600002"], "holder": ["A", "B"],
                            "end_date": pd.to_datetime(["2026-10-20", "2026-10-20"]),
                            "notice_date": pd.to_datetime(["2026-10-20", "2026-10-20"]),
                            "notice_known": [True, False], "change_wan": [-100, -20],
                            "close_price": [10, 10], "amount_yi": [0.1, 0.02]})
        with patch("data_source.dp_cycle.holder_reduce.fetch_holder_reduces", return_value=(raw, {"ok": False, "partial": True})):
            meta = ingest_holder_reduce(con)
        self.assertEqual(meta["wrote"], 2)
        self.assertEqual(con.execute("SELECT notice_known FROM holder_reduce ORDER BY code").fetchall(), [(True,), (False,)])
        con.close()

    def test_unknown_refresh_does_not_overwrite_known_notice_at_same_key(self):
        con = duckdb.connect(":memory:")
        store.init_schema(con)
        con.execute("""INSERT INTO holder_reduce (code, holder, end_date, notice_date, notice_known, amount_yi)
                       VALUES ('600001', 'A', '2026-10-20', '2026-10-20', true, 0.1)""")
        raw = pd.DataFrame({"code": ["600001"], "holder": ["A"],
                            "end_date": pd.to_datetime(["2026-10-20"]),
                            "notice_date": pd.to_datetime(["2026-10-20"]),
                            "notice_known": [False], "change_wan": [-200],
                            "close_price": [10], "amount_yi": [0.2]})
        with patch("data_source.dp_cycle.holder_reduce.fetch_holder_reduces", return_value=(raw, {"ok": False, "partial": True})):
            meta = ingest_holder_reduce(con)
        self.assertEqual(meta["wrote"], 0)
        self.assertEqual(meta["preserved_known_rows"], 1)
        self.assertEqual(con.execute("SELECT notice_known, amount_yi FROM holder_reduce").fetchone(), (True, 0.1))
        con.close()

    def test_partial_refresh_preserves_complete_amount_with_its_original_inputs(self):
        con = duckdb.connect(":memory:")
        store.init_schema(con)
        con.execute("""INSERT INTO holder_reduce
                       (code, holder, end_date, notice_date, notice_known, change_wan, close_price, amount_yi)
                       VALUES ('600001', 'A', '2026-10-20', '2026-10-20', true, -100, 10, 0.1),
                              ('600002', 'B', '2026-10-20', '2026-10-20', true, -150, 15, 0.225),
                              ('600003', 'C', '2026-10-20', '2026-10-20', true, -300, 30, 0.9),
                              ('600004', 'D', '2026-10-20', '2026-10-20', true, -50, null, null)""")
        raw = pd.DataFrame({"code": ["600001", "600002", "600003", "600004"], "holder": ["A", "B", "C", "D"],
                            "end_date": pd.to_datetime(["2026-10-20"] * 4),
                            "notice_date": pd.to_datetime(["2026-10-20"] * 4),
                            "notice_known": [True] * 4, "change_wan": [-200, 0, -400, -70],
                            "close_price": [None, 0, 20, None], "amount_yi": [None, 0, 0.8, None]})
        with patch("data_source.dp_cycle.holder_reduce.fetch_holder_reduces", return_value=(raw, {"ok": True})):
            meta = ingest_holder_reduce(con)
        rows = con.execute("SELECT change_wan, close_price, amount_yi FROM holder_reduce ORDER BY code").fetchall()
        self.assertEqual(rows, [(-100, 10, 0.1), (0, 0, 0), (-400, 20, 0.8), (-70, None, None)])
        self.assertFalse(meta["ok"])
        self.assertTrue(meta["partial"])
        con.close()

    def test_legacy_database_is_read_only_and_infers_only_distinct_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp, "legacy.duckdb"))
            con = duckdb.connect(db)
            store.init_schema(con)
            con.execute("ALTER TABLE holder_reduce DROP COLUMN notice_known")
            con.execute("""INSERT INTO holder_reduce (code, holder, end_date, notice_date)
                           VALUES ('600001', 'A', '2026-10-20', '2026-10-20'),
                                  ('600002', 'B', '2026-10-20', '2026-10-21')""")
            con.close()
            store._invalidate_load_cache()
            with patch.object(store, "db_path", return_value=db):
                rows = store.load_from_db(start="2026-01-01")["holder_reduce"]
            self.assertEqual(len(rows), 2)
            self.assertTrue(pd.isna(rows.iloc[0]["notice_known"]))
            self.assertTrue(rows.iloc[1]["notice_known"])
            con = duckdb.connect(db, read_only=True)
            self.assertNotIn("notice_known", [r[1] for r in con.execute("PRAGMA table_info('holder_reduce')").fetchall()])
            con.close()

    def test_migration_preserves_old_record_and_keeps_verification_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp, "legacy.duckdb"))
            con = duckdb.connect(db)
            store.init_schema(con)
            con.execute("ALTER TABLE holder_reduce DROP COLUMN notice_known")
            con.execute("""INSERT INTO holder_reduce (code, holder, end_date, notice_date)
                           VALUES ('600001', 'A', '2026-10-20', '2026-10-20')""")
            con.close()
            con = duckdb.connect(db)
            store.init_schema(con)
            self.assertEqual(con.execute("SELECT code, notice_known FROM holder_reduce").fetchall(), [("600001", None)])
            con.close()

    def test_future_appended_records_do_not_change_visible_set(self):
        earlier = pd.DataFrame({"date": ["2026-10-20"], "notice_date": ["2026-10-21"],
                                "notice_known": [True], "code": ["A"]})
        later = pd.concat([earlier, pd.DataFrame({"date": ["2026-10-24"], "notice_date": ["2026-10-25"],
                                                 "notice_known": [True], "code": ["B"]})], ignore_index=True)
        a, _ = visible_holder_reductions(earlier, as_of="2026-10-22")
        b, _ = visible_holder_reductions(later, as_of="2026-10-22")
        self.assertEqual(a["code"].tolist(), b["code"].tolist())


if __name__ == "__main__":
    unittest.main()
