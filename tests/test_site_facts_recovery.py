from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.evidence import evidence  # noqa: E402
from norway_company_agent.site_facts import parse_html_metadata  # noqa: E402
from scripts.run.run_competition_batch import (  # noqa: E402
    _apply_discovery,
    _enrich_and_attach_site_facts,
)


FIXTURE = json.loads((ROOT / "tests/fixtures/site_facts_recovery/recovery.json").read_text(encoding="utf-8"))
BRIEF_FIXTURES = json.loads((ROOT / "tests/fixtures/site_facts_recovery/briefs.json").read_text(encoding="utf-8"))


class SiteFactsRecoveryTests(unittest.TestCase):
    def test_second_chance_recovery_attaches_site_facts_after_discovery_succeeds(self) -> None:
        url = FIXTURE["website_url"]
        metadata = parse_html_metadata(FIXTURE["website_html"], url)
        profile = {
            "organisation_number": FIXTURE["organisation_number"],
            "name": FIXTURE["name"],
            "raw": {"navn": FIXTURE["name"]},
            "evidence": {},
            "claims": {},
        }
        failed = {"states": {"official_website": "failed", "official_website_g4": "failed"}, "candidates": [{"source": "registry_website", "state": "failed"}]}
        _apply_discovery(profile, failed, gate="g4")
        self.assertEqual(profile["evidence"]["website"]["status"], "failed")

        website = evidence(
            "website", "available", "company_owned", url,
            value={"final_url": url, "pages": [{"url": url, "html": FIXTURE["website_html"], **metadata}]},
            retrieved_at="2026-10-01T12:00:00Z", content_sha256="a" * 64,
        )
        recovered = {
            "states": {"official_website": "available", "official_website_g4": "available"},
            "website": {"evidence": website},
            "candidates": [{"source": "registry_website", "state": "available"}],
        }
        _apply_discovery(profile, recovered, gate="g4")

        facts = {
            "public_activity": evidence("public_activity", "available", "company_owned", url, value=FIXTURE["activity"], retrieved_at="2026-10-01T12:00:00Z", content_sha256="b" * 64),
            "company_brief": evidence("company_brief", "available", "company_owned", url, value=FIXTURE["brief"], retrieved_at="2026-10-01T12:00:00Z", content_sha256="b" * 64),
            "hiring": evidence("hiring", "not_available", "company_owned", url, value=FIXTURE["hiring"], retrieved_at="2026-10-01T12:00:00Z", content_sha256="b" * 64),
            "metrics": {"requests": 1, "bytes": 100, "latencies_ms": [1], "phase_timings_ms": {"activity": 1.0, "hiring": 1.0, "brief": 1.0, "parse": 1.0, "dns": 0.0, "fetch": 1.0}},
        }
        with patch("scripts.run.run_competition_batch.enrich_site_facts", return_value=(facts, facts["metrics"])):
            _enrich_and_attach_site_facts(
                profile,
                request_policy=None,
                timeout=6.0,
                total_budget_seconds=12.0,
                deadline=None,
                requested_modules={"website"},
            )

        self.assertEqual(profile["evidence"]["website"]["status"], "available")
        self.assertEqual(profile["evidence"]["public_activity"]["status"], "available")
        self.assertEqual(profile["evidence"]["company_brief"]["status"], "available")
        self.assertEqual(profile["evidence"]["hiring"]["status"], "not_available")

    def test_named_industry_descriptions_are_available_not_ambiguous(self) -> None:
        from norway_company_agent.site_facts import extract_company_brief

        for fixture in BRIEF_FIXTURES:
            with self.subTest(name=fixture["name"]):
                url = "https://brief.example.no/"
                html = f"<html><head><meta name='description' content='{fixture['description']}'></head><body></body></html>"
                metadata = parse_html_metadata(html, url)
                website = evidence(
                    "website", "available", "company_owned", url,
                    value={"final_url": url, "pages": [{"url": url, "html": html, **metadata}]},
                    retrieved_at="2026-10-01T12:00:00Z", content_sha256="c" * 64,
                )
                profile = {
                    "name": fixture["name"],
                    "municipality": "Oslo",
                    "industry_label": fixture["industry"],
                    "raw": {"navn": fixture["name"], "forretningsadresse.kommune": "Oslo", "naeringskode1.beskrivelse": fixture["industry"]},
                    "evidence": {"website": website},
                }
                status, value = extract_company_brief(profile, website)
                self.assertEqual(status, "available")
                self.assertFalse(value.get("site_disagrees_with_registry"), value)
                self.assertIn(fixture["description"].split()[0], value["summary"])


if __name__ == "__main__":
    unittest.main()
