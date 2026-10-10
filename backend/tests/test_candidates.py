from __future__ import annotations

import socket
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.core.ledger import DiscoveryLedger  # noqa: E402
from norway_company_agent.web.candidates import name_domain_candidates, registry_candidates, registry_email_candidate, subunit_website_candidates  # noqa: E402
from norway_company_agent.web.constraints import enforce_domain_uniqueness  # noqa: E402
from norway_company_agent.web.related import assess_related_entity, group_org_numbers  # noqa: E402
from norway_company_agent.web import website as website_module  # noqa: E402
from scripts.analysis.score_discovery_run import classify_row  # noqa: E402
from scripts.run import run_search_discovery as runner  # noqa: E402

TARGET = "923609016"
PARENT = "982942942"


def profile(email: str = "post@fiskeeksport.no", name: str = "Norsk Fiskeeksport AS", **evidence) -> dict:
    return {
        "organisation_number": TARGET,
        "name": name,
        "evidence": {"registry": {"value": {"epostadresse": email}}, **evidence},
    }


class CandidateTests(unittest.TestCase):
    def test_company_email_domain_becomes_a_candidate(self) -> None:
        candidate = registry_email_candidate(profile())
        self.assertEqual(candidate["registered_domain"], "fiskeeksport.no")
        self.assertEqual(candidate["provider"], "registry_email_domain")

    def test_mailbox_providers_and_missing_email_yield_nothing(self) -> None:
        for email in ("jon@gmail.com", "jon@online.no", ""):
            self.assertIsNone(registry_email_candidate(profile(email)))

    def test_subunit_websites_are_candidates_and_directories_are_not(self) -> None:
        locations = {"locations": {"value": {"locations": [{"website": "www.avdeling.no"}, {"website": "https://proff.no/x"}, {"website": ""}]}}}
        found = subunit_website_candidates(profile(**locations))
        self.assertEqual([c["registered_domain"] for c in found], ["avdeling.no"])

    def test_name_domains_require_dns_resolution_and_respect_the_limit(self) -> None:
        resolvable = {"norskfiskeeksport.no", "norsk-fiskeeksport.no"}
        found = name_domain_candidates(profile(), resolves=lambda host: host in resolvable, limit=1)
        self.assertEqual([c["registered_domain"] for c in found], ["norskfiskeeksport.no"])
        self.assertEqual(name_domain_candidates(profile(), resolves=lambda host: False), [])

    def test_name_domains_try_as_suffix_variant(self) -> None:
        found = name_domain_candidates(
            profile(),
            resolves=lambda host: host == "norskfiskeeksport-as.no",
            limit=3,
        )
        self.assertEqual([c["registered_domain"] for c in found], ["norskfiskeeksport-as.no"])

    def test_name_domains_keep_connectors_and_trim_generic_tails(self) -> None:
        found = name_domain_candidates(
            profile(name="Bokstav Og Bilde AS"),
            resolves=lambda host: host == "bokstavogbilde.no",
        )
        self.assertEqual([candidate["registered_domain"] for candidate in found], ["bokstavogbilde.no"])
        found = name_domain_candidates(
            profile(name="Kristiansen Og Stensrud AS"),
            resolves=lambda host: host == "kristiansenogstensrud.no",
        )
        self.assertEqual([candidate["registered_domain"] for candidate in found], ["kristiansenogstensrud.no"])
        found = name_domain_candidates(
            profile(name="Kencha Byggservice AS"),
            resolves=lambda host: host == "kencha.no",
        )
        self.assertEqual([candidate["registered_domain"] for candidate in found], ["kencha.no"])

    def test_candidates_are_deduplicated_by_domain_strongest_first(self) -> None:
        found = registry_candidates(profile("post@norskfiskeeksport.no"), resolves=lambda host: host == "norskfiskeeksport.no")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["provider"], "registry_email_domain")
        self.assertEqual(registry_candidates(profile(""), name_domains=False), [])


def discovered(domain: str, *, registry_match: bool = False) -> dict:
    return {
        "source_url": f"https://{domain}/",
        "source_type": "registry_derived_company_website",
        "value": {"first_party_assessment": {"candidate_domain": domain, "signals": {"registry_website_match": registry_match}}},
    }


class ConstraintTests(unittest.TestCase):
    def test_every_shared_domain_claimant_is_downgraded(self) -> None:
        rows = [
            {"organisation_number": "1", "evidence": {"website_discovery": {"status": "available"}, "website_discovered": discovered("shared.no", registry_match=True)}},
            {"organisation_number": "2", "evidence": {"website_discovery": {"status": "available"}, "website_discovered": discovered("shared.no")}},
            {"organisation_number": "3", "evidence": {"website_discovery": {"status": "available"}, "website_discovered": discovered("own.no")}},
        ]
        conflicts = enforce_domain_uniqueness(rows)
        self.assertEqual(conflicts, {"shared.no": ["1", "2"]})
        self.assertNotIn("website_discovered", rows[1]["evidence"])
        self.assertNotIn("website_discovered", rows[0]["evidence"])
        self.assertEqual(rows[0]["evidence"]["website_related"]["relationship"], "shared_domain")
        self.assertEqual(rows[1]["evidence"]["website_related"]["relationship"], "shared_domain")
        self.assertEqual(rows[0]["evidence"]["website_discovery"]["status"], "ambiguous")
        self.assertEqual(rows[1]["evidence"]["website_discovery"]["status"], "ambiguous")
        self.assertIn("website_discovered", rows[2]["evidence"])

    def test_promoted_website_is_withdrawn_with_the_downgrade(self) -> None:
        promoted = discovered("shared.no")
        rows = [
            {"organisation_number": "1", "evidence": {"website": promoted, "website_discovered": promoted}},
            {"organisation_number": "2", "evidence": {"website_discovered": discovered("shared.no")}},
        ]
        enforce_domain_uniqueness(rows)
        self.assertNotIn("website", rows[0]["evidence"])


def group_profile() -> dict:
    return profile(group={"value": {"organisasjonsnummer": "935095190", "children": [
        {"organisasjonsnummer": TARGET, "parentOrganisasjonsnummer": "935095190"},
        {"organisasjonsnummer": PARENT, "parentOrganisasjonsnummer": "935095190"},
    ]}})


class RelatedTests(unittest.TestCase):
    def test_group_numbers_exclude_the_target(self) -> None:
        self.assertEqual(group_org_numbers(group_profile()), {"935095190", PARENT})

    def test_page_naming_only_a_group_relative_is_related_not_exact(self) -> None:
        site = {"value": {"main_text_excerpt": "Konsernet. Org.nr 982 942 942"}}
        result = assess_related_entity(group_profile(), site)
        self.assertEqual(result["status"], "related")
        self.assertEqual(result["related_org_numbers"], [PARENT])
        self.assertFalse(result["publishable_as_official"])

    def test_page_naming_the_target_is_not_flagged_related(self) -> None:
        site = {"value": {"main_text_excerpt": "Org.nr 923 609 016 og 982 942 942"}}
        self.assertEqual(assess_related_entity(group_profile(), site)["status"], "none")


class LedgerTests(unittest.TestCase):
    NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

    def test_negative_outcomes_skip_within_ttl_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            ledger = DiscoveryLedger(Path(folder) / "ledger.jsonl")
            ledger.record(TARGET, "no_candidate", [], self.NOW)
            self.assertTrue(ledger.should_skip(TARGET, self.NOW + timedelta(days=10), 30))
            self.assertFalse(ledger.should_skip(TARGET, self.NOW + timedelta(days=31), 30))
            self.assertFalse(ledger.should_skip("111111111", self.NOW, 30))

    def test_failures_and_verified_results_are_never_negatively_cached(self) -> None:
        ledger = DiscoveryLedger(None)
        for outcome in ("provider_failed", "verified"):
            ledger.record(TARGET, outcome, [], self.NOW)
            self.assertFalse(ledger.should_skip(TARGET, self.NOW, 30), outcome)

    def test_first_seen_is_preserved_and_file_round_trips(self) -> None:
        summary = {"registered_domain": "x.no", "publishable": False, "url": "https://x.no/"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.jsonl"
            ledger = DiscoveryLedger(path)
            ledger.record(TARGET, "crawled_no_verified", [summary], self.NOW)
            ledger.record(TARGET, "crawled_no_verified", [{**summary, "publishable": True}], self.NOW + timedelta(days=3))
            ledger.save()
            entry = DiscoveryLedger(path).entries[TARGET]["candidates"][0]
            self.assertEqual(entry["first_seen"], "2026-10-07T12:00:00Z")
            self.assertEqual(entry["last_seen"], "2026-10-10T12:00:00Z")
            self.assertEqual(entry["decision"], "published")


class RunnerTests(unittest.TestCase):
    def test_serper_retries_transient_errors_then_succeeds(self) -> None:
        responses = [([], {"status": 429, "error": "HTTPError", "latency_ms": 1}), ([], {"status": 0, "error": "URLError", "latency_ms": 1}), (["ok"], {"status": 200, "latency_ms": 1})]
        with patch.object(runner, "_serper_once", side_effect=responses) as once, patch.object(runner.time, "sleep"):
            results, operation = runner.serper_search({}, "k", timeout=1, count=1, query="q")
        self.assertEqual(results, ["ok"])
        self.assertEqual(operation["attempts"], 3)
        self.assertEqual(once.call_count, 3)

    def test_serper_does_not_retry_client_errors(self) -> None:
        with patch.object(runner, "_serper_once", return_value=([], {"status": 401, "error": "HTTPError", "latency_ms": 1})) as once, patch.object(runner.time, "sleep"):
            _results, operation = runner.serper_search({}, "k", timeout=1, count=1, query="q")
        self.assertEqual(once.call_count, 1)
        self.assertEqual(operation["attempts"], 1)


class PeerAddressTests(unittest.TestCase):
    def test_connection_to_a_non_public_peer_is_rejected_even_without_the_name_check(self) -> None:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        threading.Thread(target=server.accept, daemon=True).start()
        port = server.getsockname()[1]
        try:
            with self.assertRaises(ValueError):
                website_module.SAFE_OPENER.open(urllib.request.Request(f"http://127.0.0.1:{port}/"), timeout=3)
        finally:
            server.close()


class ScorecardOutcomeTests(unittest.TestCase):
    def test_provider_failure_negative_cache_and_related_have_their_own_outcomes(self) -> None:
        failed = {"organisation_number": "1", "website": "", "evidence": {"website_discovery": {"status": "failed"}}}
        cached = {"organisation_number": "2", "website": "", "evidence": {"website_discovery": {"status": "not_found", "value": {"skipped_negative_cache": True}}}}
        related = {"organisation_number": "3", "website": "", "evidence": {"website_discovery": {"status": "not_found"}, "website_discovered_candidates": [
            {"identity_publishable": False, "related": {"status": "related"}, "registered_domain": "group.no"}]}}
        self.assertEqual(classify_row(failed)["outcome"], "provider_failed")
        self.assertEqual(classify_row(cached)["outcome"], "negative_cache")
        self.assertEqual(classify_row(related)["outcome"], "related_entity")


if __name__ == "__main__":
    unittest.main()
