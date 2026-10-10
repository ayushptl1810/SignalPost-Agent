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
from norway_company_agent.discovery.constraints import enforce_domain_uniqueness  # noqa: E402
from scripts.run.build_universe_cache import process_record  # noqa: E402
from scripts.run.build_universe_cache import build  # noqa: E402


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
    def _declared_brand_page(self, *, legal_name: str, domain: str, title: str, text: str | None = None, declared_url: str | None = None, identity: dict | None = None) -> tuple[dict, dict]:
        candidate_profile = profile(name=legal_name, website=declared_url or f"https://{domain}/", raw={"hjemmeside": declared_url or f"https://{domain}/", "epostadresse": f"post@{domain}"})
        page = site(domain, text or title)
        page["value"]["title"] = title
        page["value"]["requested_url"] = declared_url or f"https://{domain}/"
        page["value"]["status_code"] = 200
        page["value"]["identity_assessment"] = identity or {"publishable": True, "score": 1.0, "group_or_brand": True}
        return candidate_profile, page

    def test_registry_declared_brand_and_member_path_publish(self) -> None:
        work_profile, work_page = self._declared_brand_page(legal_name="Work System Norge AS", domain="worksystem.no", title="Work System", text="Work System official company site")
        work = assess_g4_ownership(work_profile, work_page, candidate_source="registry_website")
        self.assertTrue(work["publishable"])
        self.assertEqual(work["rule"], "g4_registry_declared")

        kitchn_profile, kitchn_page = self._declared_brand_page(legal_name="Kitchn Norge AS", domain="kitchn.no", title="Kitchn", text="Kitchn official company site")
        kitchn = assess_g4_ownership(kitchn_profile, kitchn_page, candidate_source="registry_website")
        self.assertTrue(kitchn["publishable"])
        self.assertEqual(kitchn["rule"], "g4_registry_declared")

        path_profile, path_page = self._declared_brand_page(legal_name="Husfliden Holmestrand AS", domain="norskflid.no", title="Husfliden Holmestrand", text="Husfliden Holmestrand norskflid.no/holmestrand", declared_url="https://norskflid.no/holmestrand")
        path = assess_g4_ownership(path_profile, path_page, candidate_source="registry_website")
        self.assertTrue(path["publishable"])
        self.assertEqual(path["rule"], "g4_registry_declared")
        self.assertEqual(path["published_url"], "https://norskflid.no/holmestrand")

    def test_registry_declared_keeps_hard_vetoes_and_source_scope(self) -> None:
        parked_profile, parked_page = self._declared_brand_page(legal_name="Parked AS", domain="parked.no", title="Domain is for sale", text="This domain is for sale. Kjøp dette domenet.")
        self.assertFalse(assess_g4_ownership(parked_profile, parked_page, candidate_source="registry_website")["publishable"])

        mismatch_profile, mismatch_page = self._declared_brand_page(legal_name="Mismatch AS", domain="mismatch.no", title="Mismatch", text="Mismatch AS Org.nr 999 999 999")
        mismatch = assess_g4_ownership(mismatch_profile, mismatch_page, candidate_source="registry_website")
        self.assertFalse(mismatch["publishable"])
        self.assertTrue(mismatch["signals"]["contradicted"])

        cross_profile, cross_page = self._declared_brand_page(legal_name="Cross Brand AS", domain="declared.no", title="Unrelated", text="Unrelated company", declared_url="https://declared.no/")
        cross_page["value"]["final_url"] = "https://unrelated.no/"
        cross = assess_g4_ownership(cross_profile, cross_page, candidate_source="registry_website")
        self.assertFalse(cross["publishable"])

        name_profile, name_page = self._declared_brand_page(legal_name="Work System Norge AS", domain="worksystem.no", title="Work System", text="Work System official company site")
        name = assess_g4_ownership(name_profile, name_page, candidate_source="name_derived_no")
        self.assertFalse(name["publishable"])
        self.assertNotEqual(name["rule"], "g4_registry_declared")

    def test_shared_domain_is_allowed_when_every_owner_uses_registry_declared_rule(self) -> None:
        profiles = []
        for org, name in (("923609016", "Vestbo Sør AS"), ("923609024", "Vestbo Nord AS")):
            item = profile(organisation_number=org, name=name, website="https://vestbo.no/", raw={"hjemmeside": "https://vestbo.no/", "epostadresse": "post@vestbo.no"})
            item["evidence"] = {"website": site("vestbo.no", name)}
            item["evidence"]["website_g4"] = {"value": {"final_url": "https://vestbo.no/"}, "first_party_gate": {"rule": "g4_registry_declared"}}
            item["claims"] = {"official_website_g4": {"final_url": "https://vestbo.no/"}}
            profiles.append(item)
        self.assertEqual(enforce_domain_uniqueness(profiles), [])

    def test_cache_report_exports_name_postcode_audit_when_rule_is_off(self) -> None:
        record = profile(
            website="",
            employees=4,
            raw={"forretningsadresse.adresse": "Main Street 1", "forretningsadresse.postnummer": "0123", "forretningsadresse.poststed": "Oslo"},
        )

        def fetch(url: str, **_kwargs: object):
            text = "Example Drift og Service AS 0123 Oslo " * 20
            return site(url.removeprefix("https://").rstrip("/"), text), {"requests": 1, "bytes": 100, "latencies_ms": [1]}

        with tempfile.TemporaryDirectory() as folder:
            off = build([record], Path(folder) / "off", workers=1, fetcher=fetch, gate="g4", allow_name_postcode=False)
            self.assertGreater(off["would_publish_name_postcode"], 0)
            self.assertEqual(off["name_postcode_audit"][0]["organisation_number"], ORG)
            on = build([record], Path(folder) / "on", workers=1, fetcher=fetch, gate="g4", allow_name_postcode=True)
            self.assertEqual(on["state_counts"].get("available"), 1)

    def test_name_derived_postcode_rule_is_reported_off_and_publishes_when_enabled(self) -> None:
        candidate_profile = profile(
            website="",
            raw={"forretningsadresse.adresse": "Main Street 1", "forretningsadresse.postnummer": "0123", "forretningsadresse.poststed": "Oslo"},
        )
        page = site(text="Example Drift og Service AS 0123 Oslo")
        off = assess_g4_ownership(candidate_profile, page, candidate_source="name_derived_no")
        self.assertFalse(off["publishable"])
        self.assertTrue(off["would_publish_name_postcode"])
        enabled = assess_g4_ownership(candidate_profile, page, candidate_source="name_derived_no", allow_name_postcode=True)
        self.assertTrue(enabled["publishable"])
        self.assertEqual(enabled["rule"], "g4_name_derived_postcode")

    def test_name_derived_postcode_needs_name_and_has_different_org_veto(self) -> None:
        candidate_profile = profile(
            website="",
            raw={"forretningsadresse.adresse": "Main Street 1", "forretningsadresse.postnummer": "0123", "forretningsadresse.poststed": "Oslo"},
        )
        no_name = assess_g4_ownership(candidate_profile, site(text="0123 Oslo"), candidate_source="name_derived_no")
        self.assertFalse(no_name["would_publish_name_postcode"])
        different_org = assess_g4_ownership(candidate_profile, site(text="Example Drift og Service AS 0123 Oslo Org.nr 999 999 999"), candidate_source="name_derived_no")
        self.assertFalse(different_org["would_publish_name_postcode"])
        self.assertFalse(different_org["publishable"])

    def test_registry_exact_identity_rule_publishes_without_contact_or_address(self) -> None:
        result = assess_g4_ownership(
            profile(website="", raw={"epostadresse": "post@example.no"}),
            site(text="Example Drift og Service AS"),
            candidate_source="registry_email_domain",
        )
        self.assertTrue(result["publishable"])
        self.assertEqual(result["rule"], "g4_registry_exact_identity")
        self.assertIn("exact registry identity", result["evidence_span"])

    def test_exact_org_number_rule_applies_to_name_derived_candidates(self) -> None:
        result = assess_g4_ownership(
            profile(website="", raw={"epostadresse": "post@unrelated.no"}),
            site(text="Example Drift og Service AS Org.nr 923 609 016"),
            candidate_source="name_derived_no",
        )
        self.assertTrue(result["publishable"])
        self.assertEqual(result["rule"], "g4_exact_org_number")

    def test_atea_title_is_used_for_g4_name_tokens(self) -> None:
        candidate_profile = profile(name="ATEA ASA", website="https://atea.no/", raw={"hjemmeside": "https://atea.no/"})
        page = site("atea.no", "")
        page["value"].update({"title": "Atea", "main_text_excerpt": "", "identity_text_excerpt": "", "opengraph": {"og:site_name": "Atea"}})
        result = assess_g4_ownership(candidate_profile, page, candidate_source="registry_website")
        self.assertTrue(result["g4_signals"]["legal_name_all_tokens"])

    def test_registry_exact_identity_keeps_different_org_and_brand_vetoes(self) -> None:
        different_org = assess_g4_ownership(
            profile(website="", raw={"epostadresse": "post@example.no"}),
            site(text="Example Drift og Service AS Org.nr 999 999 999"),
            candidate_source="registry_email_domain",
        )
        self.assertFalse(different_org["publishable"])
        self.assertTrue(different_org["signals"]["contradicted"])

        brand = site(text="Example Group")
        brand["value"]["identity_assessment"] = {"publishable": True, "score": 1.0, "group_or_brand": True}
        branded = assess_g4_ownership(
            profile(website="", raw={"epostadresse": "post@example.no"}),
            brand,
            candidate_source="registry_email_domain",
        )
        self.assertFalse(branded["publishable"])

    def test_name_derived_candidate_is_not_published_by_registry_exact_rule(self) -> None:
        result = assess_g4_ownership(
            profile(website="", raw={"epostadresse": "post@example.no"}),
            site(text="Example Drift og Service AS"),
            candidate_source="name_derived_no",
        )
        self.assertFalse(result["publishable"])
        self.assertNotEqual(result["rule"], "g4_registry_exact_identity")

    def test_name_address_rule_is_additive_and_records_span(self) -> None:
        result = assess_g4_ownership(profile(website="", raw={"forretningsadresse.adresse": "Main Street 1", "forretningsadresse.postnummer": "0123", "forretningsadresse.poststed": "Oslo", "epostadresse": "post@other.no"}), site(), candidate_source="name_derived_no")
        self.assertTrue(result["publishable"])
        self.assertEqual(result["rule"], "g4_name_address")
        self.assertIn("registered address", result["evidence_span"])

    def test_registry_tie_accepts_matching_phone(self) -> None:
        result = assess_g4_ownership(profile(website=""), site(text="22 33 44 55 Org.nr 923 609 016"), candidate_source="registry_email_domain")
        self.assertTrue(result["publishable"])
        self.assertEqual(result["rule"], "g4_exact_org_number")

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
