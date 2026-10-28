import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from matplotlib import pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_source.dp_cycle.gui_bundle import _clip_bundle_as_of
from data_source.dp_cycle.long_term_pdf import _method_page, build_long_term_pdf
from data_source.dp_cycle import report


class EtfFlowDeliveryTests(unittest.TestCase):
    def test_historical_clip_does_not_show_a_later_etf_observation(self):
        clipped = _clip_bundle_as_of({"etf_flow": {
            "status": "partial", "date": "2026-10-26", "flow_yi": 217.1622,
            "components": [{"code": "159919", "flow_yi": 18.2621}],
        }}, "2026-10-23")
        self.assertIsNone(clipped["etf_flow"]["date"])
        self.assertIsNone(clipped["etf_flow"]["flow_yi"])
        self.assertEqual(clipped["etf_flow"]["components"], [])

    def test_historical_clip_does_not_reuse_later_coverage_status(self):
        clipped = _clip_bundle_as_of({"etf_flow": {
            "status": "partial", "as_of": "2026-10-27", "date": "2026-10-26",
            "flow_yi": 217.1622, "components": [],
            "reason": "10 月 27 日覆盖未知",
        }}, "2026-10-26")
        self.assertIsNone(clipped["etf_flow"]["date"])
        self.assertEqual(clipped["etf_flow"]["as_of"], "2026-10-26")
        self.assertNotIn("10 月 27 日", clipped["etf_flow"]["reason"])

    def test_pdf_labels_flow_estimate_and_causal_financing_rank(self):
        bundle = {"meta": {"as_of": "2026-10-27"}, "etf_flow": {
            "date": "2026-10-26", "flow_yi": 217.1622,
            "coverage": {"comparable": 4, "expected": 4}, "source": {"events_recorded": False},
        }}
        with tempfile.TemporaryDirectory() as directory, patch(
            "data_source.dp_cycle.long_term_pdf._dashboard"
        ) as dashboard, patch("data_source.dp_cycle.long_term_pdf._cover"), patch(
            "data_source.dp_cycle.long_term_pdf._score_page"
        ), patch("data_source.dp_cycle.long_term_pdf._position_page"), patch(
            "data_source.dp_cycle.long_term_pdf._chart_page"
        ), patch("data_source.dp_cycle.long_term_pdf._method_page"):
            build_long_term_pdf(out_pdf=str(Path(directory) / "report.pdf"), meta={}, pngs={},
                                tables=bundle, diagnosis={"items": []})
        cards = dashboard.call_args.kwargs["cards"]
        etf = next(card for card in cards if card["title"] == "02 ETF 份额代理")
        margin = next(card for card in cards if card["title"] == "03 融资余额")
        self.assertIn("217.16", etf["value"])
        self.assertIn("估算", etf["value"])
        self.assertIn("2026-10-26", etf["sub"])
        self.assertIn("4/4", etf["sub"])
        self.assertIn("非实际现金流", etf["sub"])
        self.assertIn("事件表未留存", etf["sub"])
        self.assertNotIn("全样本分位", margin["sub"])
        self.assertIn("截至当期", margin["sub"])

    def test_method_page_explains_scissors_rule_for_each_version(self):
        for version, phrase in (("improved_v1", "剪刀差仅观察"), ("author_v1", "剪刀差可计分")):
            with self.subTest(version=version), patch("data_source.dp_cycle.long_term_pdf._page") as save:
                _method_page(None, as_of="2026-10-27", page=1, total=1, rule_version=version)
                figure = save.call_args.args[0]
                text = " ".join(item.get_text() for item in figure.axes[0].texts)
                plt.close(figure)
                self.assertIn(phrase, text)

    def test_financing_chart_recomputes_historical_percentile_from_visible_balances(self):
        frame = pd.DataFrame({"date": pd.date_range("2026-01-05", periods=3),
                              "融资余额_亿元": [3, 1, 2],
                              "分位_全样本": [42, 42, 42]})
        with patch.object(report, "_save", side_effect=lambda figure, path: figure):
            figure = report.plot_margin(frame, "unused.png")
        percentile = figure.axes[2].lines[0].get_ydata()
        plt.close(figure)
        self.assertAlmostEqual(percentile[0], 100)
        self.assertAlmostEqual(percentile[1], 50)
        self.assertAlmostEqual(percentile[2], 200 / 3)


if __name__ == "__main__":
    unittest.main()
