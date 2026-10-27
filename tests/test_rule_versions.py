import json
import sys
import unittest
from inspect import signature
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.diagnosis import _national_team_item, diagnose_position, score_history_weekly
from data_source.dp_cycle.allocation import monthly_regimes
from test_data_availability import core_samples


def bundle_with_etf_regime(regime):
    bundle = core_samples("2026-10-26")
    bundle["etf_monthly"]["份额_亿份"] = (
        [100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 210, 195]
        if regime == "high_out"
        else [200, 180, 160, 140, 130, 120, 110, 100, 90, 80, 50, 60]
    )
    return bundle


class RuleVersionTests(unittest.TestCase):
    def test_default_high_etf_outflow_does_not_force_exit(self):
        diagnosis = diagnose_position(bundle_with_etf_regime("high_out"), heavy=False, as_of="2026-10-27")
        self.assertEqual(diagnosis["decision"]["id"], "hold")
        self.assertEqual(diagnosis["score"], 0)

    def test_default_low_etf_inflow_does_not_force_bottom_fishing(self):
        diagnosis = diagnose_position(bundle_with_etf_regime("low_in"), heavy=False, as_of="2026-10-27")
        self.assertEqual(diagnosis["decision"]["id"], "hold")
        self.assertEqual(diagnosis["score"], 0)

    def test_default_is_named_and_does_not_claim_institutional_actions(self):
        diagnosis = diagnose_position(bundle_with_etf_regime("low_in"), heavy=True, as_of="2026-10-27")
        rendered = json.dumps(diagnosis, ensure_ascii=False)
        for term in ("国家队", "郭嘉队", "国嘉队", "汇金", "托举", "最高优先"):
            self.assertNotIn(term, rendered)
        self.assertEqual(diagnosis["rule_version"], "improved_v1")
        item = next(item for item in diagnosis["items"] if item["id"] == "national_team")
        self.assertEqual(item["status"], "info")
        self.assertIn("代理", item["label"])

    def test_author_research_version_retains_original_high_and_low_rules(self):
        self.assertIn("rule_version", signature(diagnose_position).parameters)
        for regime, expected in (("high_out", "exit"), ("low_in", "bottom")):
            with self.subTest(regime=regime):
                diagnosis = diagnose_position(bundle_with_etf_regime(regime), heavy=False,
                                              as_of="2026-10-27", rule_version="author_v1")
                self.assertEqual(diagnosis["decision"]["id"], expected)
                self.assertEqual(diagnosis["rule_version"], "author_v1")
                self.assertIn("未验证", diagnosis["rule_validation"])

    def test_improved_version_does_not_convert_etf_signal_into_identity_with_other_evidence(self):
        bundle = bundle_with_etf_regime("high_out")
        bundle["margin"]["分位_全样本"] = 90.0
        bundle["margin"]["融资余额_亿元"] = range(1000, 5000, 100)
        diagnosis = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        self.assertIn(diagnosis["decision"]["id"], {"exit", "reduce"})
        self.assertIn("融资", diagnosis["state"])
        self.assertNotIn("郭嘉队", json.dumps(diagnosis, ensure_ascii=False))

    def test_score_curve_uses_selected_version(self):
        self.assertIn("rule_version", signature(score_history_weekly).parameters)
        bundle = bundle_with_etf_regime("high_out")
        bundle["weekly"] = pd.DataFrame({"date": ["2026-10-30"], "close": [100.0]})
        improved = score_history_weekly(bundle, rule_version="improved_v1")
        author = score_history_weekly(bundle, rule_version="author_v1")
        self.assertEqual(improved[-1]["value"], 0)
        self.assertEqual(author[-1]["value"], -4)

    def test_both_versions_keep_the_no_data_position_gate(self):
        self.assertIn("rule_version", signature(diagnose_position).parameters)
        for version in ("improved_v1", "author_v1"):
            with self.subTest(version=version):
                diagnosis = diagnose_position({}, heavy=False, as_of="2026-10-27", rule_version=version)
                self.assertEqual(diagnosis["decision"]["id"], "insufficient_data")
                self.assertIsNone(diagnosis["decision"]["equity_low"])

    def test_author_history_does_not_recompute_a_missing_monthly_delta(self):
        bundle = bundle_with_etf_regime("high_out")
        etf = bundle["etf_monthly"]
        etf["月变动_亿份"] = etf["份额_亿份"].diff()
        etf.loc[etf.index[-1], "月变动_亿份"] = float("nan")
        bundle["weekly"] = pd.DataFrame({"date": ["2026-10-30"], "close": [100.0]})
        curve = score_history_weekly(bundle, rule_version="author_v1")
        self.assertEqual(curve[-1]["value"], 0)
        self.assertIn("rule_version", signature(monthly_regimes).parameters)
        periods = monthly_regimes(bundle, rule_version="author_v1")
        self.assertEqual(periods[-1]["phase_id"], "unclear")

    def test_legacy_etf_history_cannot_infer_delta_across_missing_month(self):
        bundle = bundle_with_etf_regime("high_out")
        etf = bundle["etf_monthly"]
        bundle["etf_monthly"] = etf.drop(etf.index[-2])
        bundle["weekly"] = pd.DataFrame({"date": ["2026-10-30"], "close": [100.0]})
        curve = score_history_weekly(bundle, rule_version="author_v1")
        self.assertEqual(curve[-1]["value"], 0)
        self.assertIn("rule_version", signature(monthly_regimes).parameters)
        periods = monthly_regimes(bundle, rule_version="author_v1")
        self.assertEqual(periods[-1]["phase_id"], "unclear")

    def test_old_etf_item_call_remains_usable_with_default_version(self):
        self.assertIsNot(signature(_national_team_item).parameters["rule_version"].default, signature(_national_team_item).empty)
        item = _national_team_item(bundle_with_etf_regime("low_in")["etf_monthly"])
        self.assertIn("代理", item["label"])
        self.assertNotIn("国家队", json.dumps(item, ensure_ascii=False))
        self.assertNotIn("最高优先", json.dumps(item, ensure_ascii=False))

    def test_unknown_rule_version_does_not_silently_select_another_version(self):
        for function in (diagnose_position, score_history_weekly, monthly_regimes):
            with self.subTest(function=function.__name__):
                with self.assertRaises(ValueError):
                    function({}, rule_version="not_a_rule")

    def test_missing_etf_is_only_observation_gap_in_improved_version(self):
        bundle = bundle_with_etf_regime("low_in")
        bundle.pop("etf_monthly")
        improved = diagnose_position(bundle, heavy=False, as_of="2026-10-27")
        author = diagnose_position(bundle, heavy=False, as_of="2026-10-27", rule_version="author_v1")
        self.assertEqual(improved["decision"]["id"], "hold")
        self.assertEqual(improved["data_availability"]["national_team"]["status"], "gap")
        self.assertTrue(any("ETF" in gap and "缺" in gap for gap in improved["gaps"]))
        self.assertEqual(author["decision"]["id"], "insufficient_data")


if __name__ == "__main__":
    unittest.main()
