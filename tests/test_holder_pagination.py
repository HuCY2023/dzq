import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.holder_reduce import fetch_holder_reduces


class HolderPaginationTests(unittest.TestCase):
    def test_failed_later_page_keeps_rows_but_is_not_complete(self):
        first = Mock()
        first.json.return_value = {"result": {"pages": 2, "count": 2, "data": [{
            "SECURITY_CODE": "600000", "HOLDER_NAME": "A", "END_DATE": "2026-10-20",
            "NOTICE_DATE": "2026-10-21", "CHANGE_NUM": -100, "CLOSE_PRICE": 10,
        }]}}
        with patch("data_source.dp_cycle.holder_reduce.requests.Session.get", side_effect=[first, OSError("offline")]):
            rows, meta = fetch_holder_reduces(page_size=1)
        self.assertEqual(len(rows), 1)
        self.assertTrue(meta["partial"])
        self.assertFalse(meta["ok"])

    def test_short_page_count_is_partial_even_if_requests_succeed(self):
        first = Mock()
        first.json.return_value = {"result": {"pages": 1, "count": 2, "data": [{
            "SECURITY_CODE": "600000", "HOLDER_NAME": "A", "END_DATE": "2026-10-20",
            "NOTICE_DATE": "2026-10-21", "CHANGE_NUM": -100, "CLOSE_PRICE": 10,
        }]}}
        with patch("data_source.dp_cycle.holder_reduce.requests.Session.get", return_value=first):
            rows, meta = fetch_holder_reduces()
        self.assertEqual(len(rows), 1)
        self.assertTrue(meta["partial"])
        self.assertFalse(meta["ok"])

    def test_duplicate_pages_and_missing_price_are_partial(self):
        row = {"SECURITY_CODE": "600000", "HOLDER_NAME": "A", "END_DATE": "2026-10-20",
               "NOTICE_DATE": "2026-10-21", "CHANGE_NUM": -100, "CLOSE_PRICE": 10}
        response = Mock()
        response.json.return_value = {"result": {"pages": 2, "count": 2, "data": [row]}}
        with patch("data_source.dp_cycle.holder_reduce.requests.Session.get", return_value=response):
            rows, meta = fetch_holder_reduces(page_size=1)
        self.assertEqual(len(rows), 1)
        self.assertFalse(meta["ok"])
        self.assertTrue(meta["partial"])
        row["CLOSE_PRICE"] = None
        response.json.return_value = {"result": {"pages": 1, "count": 1, "data": [row]}}
        with patch("data_source.dp_cycle.holder_reduce.requests.Session.get", return_value=response):
            rows, meta = fetch_holder_reduces()
        self.assertEqual(len(rows), 1)
        self.assertFalse(meta["ok"])
        self.assertTrue(meta["partial"])


if __name__ == "__main__":
    unittest.main()
