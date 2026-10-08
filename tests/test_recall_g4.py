from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.external.nav_jobs import build_index, dedupe_feed_entries, collect  # noqa: E402
from norway_company_agent.web.candidates import name_domain_candidates, name_domain_variants  # noqa: E402
from norway_company_agent.web.first_party import assess_g4_ownership, detect_related_only_site  # noqa: E402
from scripts.run.build_universe_cache import process_record  # noqa: E402


ORG = "923609016"


def profile(**overrides: object) -> dict:
    value = {
        "organisation_number": ORG,
        "name": "Example Drift og Service AS",
        "website": "https://example.no/",
        "phone": "22 33 44 55",
        "raw": {
            "forretningsadresse.adresse": "Main Street 1",
            "forretningsadresse.postnummer": "0123",
            "forretningsadresse.poststed": "Oslo",
            "epostadresse": "post@example.no",
        },
    }
    value.update(overrides)
    return value


def site(domain: str = "example.no", text: str = "Example Drift og Service AS Main Street 1 0123 Oslo") -> dict:
    return {"status": "available", "source_url": f"https://{domain}/", "value": {"final_url": f"https://{domain}/", "registered_domain": domain, "title": text, "main_text_excerpt": text, "identity_assessment": {"publishable": True, "score": 1.0}, "pages": []}}


class G4Tests(unittest.TestCase):
    def test_name_address_rule_is_additive_and_records_span(self) -> None:
        result = assess_g4_ownership(profile(website="", raw={"forretningsadresse.adresse": "Main Street 1", "forretningsadresse.postnummer": "0123", "forretningsadresse.poststed": "Oslo", "epostadresse": "post@other.no"}), site(), candidate_source="name_derived_no")
        self.assertTrue(result["publishable"])
        self.assertEqual(result["rule"], "g4_name_address")
        self.assertIn("registered address", result["evidence_span"])

    def test_registry_tie_accepts_matching_phone(self) -> None:
        result = assess_g4_ownership(profile(website=""), site(text="22 33 44 55 Org.nr 923 609 016"), candidate_source="registry_email_domain")
        self.assertTrue(result["publishable"])
        self.assertEqual(result["rule"], "g4_registry_tie")

    def test_contradicting_org_number_still_vetoes_g4(self) -> None:
        result = assess_g4_ownership(profile(website=""), site(text="Example Drift og Service AS Main Street 1 0123 Oslo Org.nr 999 999 999"), candidate_source="name_derived_no")
        self.assertFalse(result["publishable"])
        self.assertTrue(result["signals"]["contradicted"])

    def test_related_examples_are_never_official(self) -> None:
        result = detect_related_only_site(profile(), site("privatmegleren.no"))
        self.assertTrue(result["related_only"])
        decision = assess_g4_ownership(profile(website=""), site("privatmegleren.no"), candidate_source="name_derived_no")
        self.assertFalse(decision["publishable"])


class VariantAndStateTests(unittest.TestCase):
    def test_search_only_name_variants_are_generated(self) -> None:
        self.assertIn("erik-hoel", name_domain_variants("Erik Hoel AS"))
        self.assertIn("pixlo-dvnor", name_domain_variants("Pixlo DVNor AS"))
        self.assertIn("jpbyggogbetong", name_domain_variants("JP Bygg og Betong AS"))
        self.assertIn("jpbyggbetong", name_domain_variants("JP Bygg og Betong AS"))
        self.assertIn("hypro", name_domain_variants("Hypro AS"))
        self.assertNotIn("hypro.com", name_domain_variants("Hypro AS", include_com=False))

    def test_variant_lookup_remains_dns_first(self) -> None:
        found = name_domain_candidates(profile(name="Erik Hoel AS"), resolves=lambda host: host == "erik-hoel.no")
        self.assertEqual(found[0]["registered_domain"], "erik-hoel.no")

    def test_all_resolution_failures_are_not_available(self) -> None:
        calls: list[str] = []

        def no_fetch(url: str, **_kwargs: object):
            calls.append(url)
            raise AssertionError("unresolved candidates must not be fetched")

        result = process_record(profile(website="https://missing.invalid/"), fetcher=no_fetch, resolved_domains={})
        self.assertEqual(result["states"]["official_website"], "not_available")
        self.assertEqual(calls, [])


class NavG4Tests(unittest.TestCase):
    def test_feed_dedupe_keeps_latest_and_inactive_retraction(self) -> None:
        entries = [
            {"uuid": "a", "url": "/a", "status": "ACTIVE", "sistEndret": "2026-10-01T00:00:00Z"},
            {"uuid": "a", "url": "/a2", "status": "INACTIVE", "sistEndret": "2026-10-02T00:00:00Z"},
            {"uuid": "b", "url": "/b", "status": "ACTIVE", "sistEndret": "2026-10-01T00:00:00Z"},
        ]
        latest = dedupe_feed_entries(entries)
        self.assertEqual({item["uuid"]: item["status"] for item in latest}, {"a": "INACTIVE", "b": "ACTIVE"})

    def test_stale_complete_index_is_not_a_zero(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        result = collect(profile(), now=datetime.now(timezone.utc), context={"index": {}, "index_meta": {"complete": True, "built_at": old}})
        self.assertEqual(result["status"], "not_available")


if __name__ == "__main__":
    unittest.main()
