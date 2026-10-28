import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib import pyplot as plt
from core.plotting import setup_matplotlib_chinese
from data_source.dp_cycle.long_term_pdf import _position_page, _score_page, build_long_term_pdf
from data_source.dp_cycle.diagnosis import diagnose_position
from data_source.dp_cycle.national_team_etf import monthly_share_change
from test_data_availability import core_samples, etf_history

setup_matplotlib_chinese()

class PdfMissingDataTest(unittest.TestCase):
    def test_no_score_does_not_break_position_page(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.pdf"
            with PdfPages(output) as pdf:
                _position_page(pdf, as_of="2026-10-27", diagnosis={
                    "state": "数据不足", "score": None, "position": {}
                }, page=1, total=1)
            self.assertGreater(output.stat().st_size, 0)

    def test_info_item_is_not_shown_as_missing(self):
        with patch("data_source.dp_cycle.long_term_pdf._page") as save_page:
            _score_page(None, as_of="2026-10-27", diagnosis={}, items=[{
                "label": "美股拥挤度", "status": "info", "score": None,
            }], page=1, total=1)
            figure = save_page.call_args.args[0]
            texts = [text.get_text() for text in figure.axes[0].texts]
            plt.close(figure)
            self.assertIn("观察", texts)

    def test_missing_red_light_is_not_shown_as_off(self):
        with patch("data_source.dp_cycle.long_term_pdf._page") as save_page:
            _score_page(None, as_of="2026-10-27", diagnosis={"red_light": {"flags": [
                {"label": "外盘", "on": None, "status": "unknown"}
            ]}}, items=[], page=1, total=1)
            figure = save_page.call_args.args[0]
            texts = [text.get_text() for text in figure.axes[0].texts]
            plt.close(figure)
            self.assertIn("红灯：外盘(未知)", texts)

    def test_stale_and_partial_items_keep_their_status(self):
        with patch("data_source.dp_cycle.long_term_pdf._page") as save_page:
            _score_page(None, as_of="2026-10-27", diagnosis={}, items=[
                {"label": "旧融资", "status": "stale", "score": None},
                {"label": "部分ETF", "status": "partial", "score": None},
            ], page=1, total=1)
            figure = save_page.call_args.args[0]
            texts = [text.get_text() for text in figure.axes[0].texts]
            plt.close(figure)
            self.assertIn("陈旧", texts)
            self.assertIn("部分覆盖", texts)

    def test_pdf_receives_etf_membership_needed_for_same_decision_as_web(self):
        bundle = core_samples("2026-10-26")
        bundle["etf_hist"] = etf_history("2026-10-26", drop_latest="510310")
        bundle["etf_monthly"] = monthly_share_change(bundle["etf_hist"])
        expected = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        captured = []

        def capture(tables, **kwargs):
            result = diagnose_position(tables, heavy=False, as_of="2026-10-27")
            captured.append(result)
            return result

        with tempfile.TemporaryDirectory() as directory, patch(
            "data_source.dp_cycle.diagnosis.diagnose_position", side_effect=capture
        ):
            build_long_term_pdf(out_pdf=str(Path(directory) / "report.pdf"),
                                meta={}, pngs={}, tables=bundle)
        self.assertIsNotNone(expected["decision"]["equity_low"])
        author = diagnose_position(bundle, heavy=False, as_of="2026-10-27", rule_version="author_v1")
        self.assertEqual(author["decision"]["id"], "insufficient_data")
        self.assertEqual(captured[0]["decision"]["id"], expected["decision"]["id"])

    def test_pdf_does_not_mark_partial_data_as_fully_ready(self):
        indicators = {name: {"ok": True} for name in
                      ("01_main_force", "02_national_team", "03_margin", "04_growth_value", "05_high_low")}
        indicators["01_main_force"]["partial"] = True
        with tempfile.TemporaryDirectory() as directory, patch(
            "data_source.dp_cycle.long_term_pdf._dashboard"
        ) as dashboard, patch("data_source.dp_cycle.long_term_pdf._cover"), patch(
            "data_source.dp_cycle.long_term_pdf._score_page"
        ), patch("data_source.dp_cycle.long_term_pdf._position_page"), patch(
            "data_source.dp_cycle.long_term_pdf._chart_page"
        ), patch("data_source.dp_cycle.long_term_pdf._method_page"):
            build_long_term_pdf(out_pdf=str(Path(directory) / "report.pdf"),
                                meta={"indicators": indicators}, pngs={}, tables={})
        status = next(card for card in dashboard.call_args.kwargs["cards"] if card["title"] == "数据状态")
        self.assertNotEqual(status["value"], "五项就绪")


if __name__ == "__main__":
    unittest.main()
