import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_source.dp_cycle.holder_disclosures import load_holder_disclosures
from data_source.dp_cycle.gui_bundle import _clip_bundle_as_of, bundle_to_web_payload


def item(summary, code="159919"):
    return next(row for row in summary["items"] if row["code"] == code)


class HolderDisclosuresTests(unittest.TestCase):
    def test_date_only_report_is_not_visible_until_the_following_day(self):
        for as_of in ("2025-08-28", "2025-08-29"):
            with self.subTest(as_of=as_of):
                self.assertEqual(item(load_holder_disclosures(as_of=as_of))["status"], "not_verified")
        disclosed = item(load_holder_disclosures(as_of="2025-08-30"))
        self.assertEqual(disclosed["status"], "named_disclosure")
        self.assertEqual(disclosed["period_end"], "2025-06-30")
        self.assertEqual(disclosed["published_date"], "2025-08-29")
        self.assertEqual(disclosed["direct_shares"], 35498494985)

    def test_later_report_without_names_does_not_reuse_old_holding_as_current(self):
        summary = load_holder_disclosures(as_of="2026-10-27")
        disclosed = item(summary)
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["coverage"]["verified_reports"], 4)
        self.assertEqual(summary["coverage"]["named_latest_report"], 0)
        self.assertEqual(summary["coverage"]["expected"], 4)
        self.assertEqual(disclosed["period_end"], "2026-06-30")
        self.assertEqual(disclosed["published_date"], "2026-08-31")
        self.assertEqual(disclosed["status"], "not_disclosed")
        self.assertIsNone(disclosed["direct_shares"])
        self.assertEqual(disclosed["reported_total_shares"], 6939216676)
        self.assertEqual(disclosed["last_named"]["period_end"], "2025-12-31")
        self.assertEqual(disclosed["last_named"]["direct_shares"], 35498494985)
        self.assertTrue(all(item(summary, code)["status"] == "not_disclosed" for code in ("510300", "510310", "510330")))
        self.assertEqual(item(summary, "510300")["last_named"]["direct_shares"], 73513073833)
        self.assertEqual(item(summary, "510310")["last_named"]["direct_shares"], 55894085452)
        self.assertEqual(item(summary, "510330")["last_named"]["direct_shares"], 42028022295)
        self.assertTrue(all(row["direct_shares"] is None for row in summary["items"]))

    def test_current_disclosure_report_is_only_visible_after_its_publication_date(self):
        before = item(load_holder_disclosures(as_of="2026-08-31"))
        after = item(load_holder_disclosures(as_of="2026-09-01"))
        self.assertEqual(before["period_end"], "2025-12-31")
        self.assertEqual(before["status"], "named_disclosure")
        self.assertEqual(after["period_end"], "2026-06-30")
        self.assertEqual(after["status"], "not_disclosed")

    def test_reports_from_other_funds_respect_each_publication_date(self):
        before = load_holder_disclosures(as_of="2026-08-29")
        after = load_holder_disclosures(as_of="2026-08-30")
        self.assertEqual(item(before, "510300")["status"], "named_disclosure")
        self.assertEqual(item(after, "510300")["status"], "not_disclosed")
        self.assertEqual(item(after, "510310")["status"], "named_disclosure")
        self.assertEqual(item(load_holder_disclosures(as_of="2026-09-01"), "510310")["status"], "not_disclosed")

    def test_payload_keeps_disclosures_separate_from_estimated_flows(self):
        disclosures = load_holder_disclosures(as_of="2026-10-27")
        payload = bundle_to_web_payload({"meta": {"as_of": "2026-10-27"},
                                        "etf_holder_disclosures": disclosures}, heavy=False)
        self.assertEqual(payload["etf_holder_disclosures"], disclosures)
        self.assertIsNone(payload.get("etf_flow"))

    def test_historical_clip_does_not_expose_later_disclosure(self):
        disclosures = load_holder_disclosures(as_of="2026-10-27")
        clipped = _clip_bundle_as_of({"etf_holder_disclosures": disclosures}, "2025-08-29")
        self.assertEqual(item(clipped["etf_holder_disclosures"])["status"], "not_verified")


if __name__ == "__main__":
    unittest.main()
