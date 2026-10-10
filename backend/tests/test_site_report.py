from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.analysis.section_completeness import completeness  # noqa: E402
from scripts.demo.build_report import build_report  # noqa: E402


class SiteReportTests(unittest.TestCase):
    def test_report_is_self_contained_and_has_find_compare_verify_responsive_controls(self) -> None:
        row = {
            "organisation_number": "912345678",
            "profile": {"name": "Example AS", "municipality": "Oslo", "raw": {"naeringskode1.beskrivelse": "Programvare"}},
            "claims": [{"field": "company_brief", "availability": "available", "value": {"parts": [{"text": "Programvare"}]}, "evidence_ids": ["ev-1"]}],
            "evidence": [{"id": "ev-1", "source_url": "https://example.no", "retrieved_at": "2026-10-01T00:00:00Z", "content_sha256": "a" * 64, "claim_span": "Programvare"}],
        }
        report = build_report([row])
        self.assertIn('type="search"', report)
        self.assertIn("Compare this company", report)
        self.assertIn("retrieved_at", report)
        self.assertIn("@media (max-width:360px)", report)
        self.assertNotIn('<script src="http', report)
        self.assertNotIn('<link href="http', report)

    def test_section_completeness_flags_registry_fact_loss(self) -> None:
        row = {
            "organisation_number": "912345678",
            "profile": {"evidence": {"roles": {"value": {"roles": [{"name": "A"}, {"name": "B"}]}}, "locations": {"value": {"locations": [{"name": "L"}]}}}},
            "claims": [{"field": "leadership", "availability": "available", "value": [{"name": "A"}], "evidence_ids": []}, {"field": "registered_workplaces", "availability": "available", "value": [{"name": "L"}], "evidence_ids": []}],
        }
        result = completeness([row])
        self.assertFalse(result["passed"])
        self.assertEqual(result["registry_fact_deficits"][0]["field"], "leadership")


if __name__ == "__main__":
    unittest.main()
