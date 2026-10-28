import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.national_team_etf import adjust_share_events


class ShenzhenEtfEventTests(unittest.TestCase):
    def test_share_conversion_uses_official_2019_factor(self):
        original = pd.DataFrame({"date": ["2019-01-10", "2019-01-11"],
                                 "code": ["159919", "159919"],
                                 "份额_亿份": [100, 111.0680861]})
        result = adjust_share_events(original)
        self.assertAlmostEqual(result["份额_调整后_亿份"].iloc[0], 111.0680861)
        self.assertAlmostEqual(result["份额_调整后_亿份"].iloc[1], 111.0680861)
        self.assertEqual(original["份额_亿份"].iloc[0], 100)


if __name__ == "__main__":
    unittest.main()
