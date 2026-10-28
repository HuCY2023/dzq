import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.diagnosis import _through
from data_source.dp_cycle.gui_bundle import _clip_bundle_as_of


class AvailabilityTimeTests(unittest.TestCase):
    def test_same_day_later_publication_is_not_visible_at_midnight(self):
        frame = pd.DataFrame({"date": ["2026-10-27"], "融资余额_亿元": [100],
                              "available_at": ["2026-10-27 18:00:00"]})
        cutoff = pd.Timestamp("2026-10-27")
        self.assertTrue(_through(frame, cutoff).empty)
        self.assertTrue(_clip_bundle_as_of({"margin": frame}, cutoff)["margin"].empty)

    def test_future_unlock_also_requires_publication_time(self):
        frame = pd.DataFrame({"date": ["2026-11-01"], "amount_yi": [100],
                              "available_at": ["2026-10-27 18:00:00"]})
        self.assertTrue(_clip_bundle_as_of({"unlock_daily": frame}, "2026-10-27")["unlock_daily"].empty)


if __name__ == "__main__":
    unittest.main()
