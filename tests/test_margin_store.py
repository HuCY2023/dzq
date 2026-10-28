import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle import store


class MarginStoreTests(unittest.TestCase):
    def test_board_totals_require_each_component_and_keep_real_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp, "test.duckdb"))
            con = duckdb.connect(db)
            store.init_schema(con)
            con.execute("""
                INSERT INTO margin_daily (date, sh_main_yi, sz_main_yi, chinext_yi)
                VALUES ('2026-10-01', 20, NULL, 3),
                       ('2026-10-02', NULL, 0, 3),
                       ('2026-10-03', 20, 0, 3)
            """)
            con.close()
            store._invalidate_load_cache()
            with patch.object(store, "db_path", return_value=db):
                rows = store.load_from_db(start="2026-01-01")["margin"]
            self.assertTrue(pd.isna(rows.iloc[0]["主板_亿元"]))
            self.assertTrue(pd.isna(rows.iloc[0]["深市_亿元"]))
            self.assertTrue(pd.isna(rows.iloc[1]["主板_亿元"]))
            self.assertEqual(rows.iloc[1]["深市_亿元"], 3)
            self.assertEqual(rows.iloc[2]["主板_亿元"], 20)
            self.assertEqual(rows.iloc[2]["深市_亿元"], 3)


if __name__ == "__main__":
    unittest.main()
