import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.liquidity_monitor import _fetch_fred


class FredCsvTests(unittest.TestCase):
    def test_public_csv_header_and_missing_values(self):
        response = Mock()
        response.text = "observation_date,DGS10\n2026-10-01,4.12\n2026-10-02,\n2026-10-05,4.18\n"
        with patch("data_source.dp_cycle.liquidity_monitor.requests.get", return_value=response):
            result = _fetch_fred("DGS10")
        self.assertEqual(result["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-10-01", "2026-10-05"])
        self.assertEqual(result["value"].tolist(), [4.12, 4.18])


if __name__ == "__main__":
    unittest.main()
