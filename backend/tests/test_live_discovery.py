from __future__ import annotations

import time
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.discovery import candidate_domains, enforce_domain_uniqueness, process_record  # noqa: E402
from norway_company_agent.web.first_party import detect_related_only_site  # noqa: E402
from scripts.run import run_competition_batch as batch  # noqa: E402
from scripts.run.build_universe_cache import process_record as builder_process_record  # noqa: E402


def _profile(org: str, name: str, website: str = "https://example.no/") -> dict:
    return {
        "organisation_number": org,
        "name": name,
        "website": website,
        "municipality": "Oslo",
        "raw": {"hjemmeside": website},
        "evidence": {},
    }


def _site(url: str, text: str, *, status: str = "available") -> dict:
    return {
        "field": "website",
        "status": status,
        "source_url": url,
        "value": {"final_url": url, "title": text, "main_text_excerpt": text, "pages": []},
    }


class LiveDiscoveryTests(unittest.TestCase):
    def test_tier_timeouts_keep_registry_at_fifteen_and_name_at_six(self) -> None:
        from norway_company_agent.discovery.engine import NAME_DERIVED_TIMEOUT, NAME_DERIVED_TOTAL, REGISTRY_TIER_TIMEOUT, REGISTRY_TIER_TOTAL

        self.assertEqual((NAME_DERIVED_TIMEOUT, NAME_DERIVED_TOTAL), (10.0, 9.0))
        self.assertEqual((REGISTRY_TIER_TIMEOUT, REGISTRY_TIER_TOTAL), (15.0, 25.0))
        observed: list[tuple[str, float]] = []

        def fake_fetch(url: str, *, timeout: float, **_kwargs: object):
            observed.append((url, timeout))
            return {"status": "failed", "source_url": url}, {"failure_kind": "timeout"}

        registry = _profile("923609016", "Registry AS", "https://registry.example.no/")
        registry_candidate = next(item for item in candidate_domains(registry) if item["source"] == "registry_website")
        registry_host = registry_candidate["domain"]
        process_record(registry, timeout=30.0, resolved_domains={registry_host: {"resolved": True}}, fetcher=fake_fetch)
        self.assertTrue(any(timeout == 15.0 for _url, timeout in observed))

        observed.clear()
        name_record = _profile("923609024", "Name Derived AS", website="")
        name_candidate = next(item for item in candidate_domains(name_record) if item["source"].startswith("name_derived"))
        name_host = name_candidate["domain"]
        process_record(name_record, timeout=30.0, resolved_domains={name_host: {"resolved": True}}, fetcher=fake_fetch)
        self.assertTrue(any(timeout == 10.0 for _url, timeout in observed))

    def test_registry_and_name_tiers_start_together_and_fast_name_publishes(self) -> None:
        record = _profile("923609016", "Fast Name AS", website="https://slow-registry.no/")
        record["raw"] = {
            "hjemmeside": "https://slow-registry.no/",
            "epostadresse": "post@unrelated.example",
            "forretningsadresse.adresse": "Main Street 1",
            "forretningsadresse.postnummer": "0123",
            "forretningsadresse.poststed": "Oslo",
        }
        record["website"] = "https://slow-registry.no/"
        started = time.monotonic()
        calls: list[tuple[str, float]] = []

        def fake_fetch(url: str, *, timeout: float, **_kwargs: object):
            calls.append((url, time.monotonic() - started))
            if "slow-registry.no" in url:
                time.sleep(0.12)
                return _site(url, "Fast Name AS Main Street 1 0123 Oslo"), {"requests": 1, "bytes": 10}
            return _site(url, "Fast Name AS Main Street 1 0123 Oslo"), {"requests": 1, "bytes": 10}

        candidates = candidate_domains(record)
        resolved = {item["domain"]: {"resolved": True} for item in candidates}
        result = process_record(record, gate="g4", fetcher=fake_fetch, resolved_domains=resolved, timeout=30.0, max_seconds=1.0)

        assert result["states"]["official_website"] == "available"
        assert result["website"]["evidence"]["value"]["final_url"] == "https://slow-registry.no/"
        assert len(calls) >= 2
        assert max(at for _url, at in calls[:2]) < 0.2

    def test_hanging_registry_email_does_not_block_fast_name_candidate(self) -> None:
        record = _profile("923609016", "Fast Name AS", website="")
        record["raw"] = {
            "epostadresse": "post@slow-registry-email.no",
            "forretningsadresse.adresse": "Main Street 1",
            "forretningsadresse.postnummer": "0123",
            "forretningsadresse.poststed": "Oslo",
        }

        def fake_fetch(url: str, **_kwargs: object):
            if "slow-registry-email.no" in url:
                time.sleep(0.4)
                return {"status": "failed", "source_url": url}, {"failure_kind": "timeout"}
            return _site(url, "Fast Name AS Main Street 1 0123 Oslo"), {"requests": 1, "bytes": 10}

        candidates = candidate_domains(record)
        resolved = {item["domain"]: {"resolved": True} for item in candidates}
        started = time.monotonic()
        result = process_record(record, gate="g4", fetcher=fake_fetch, resolved_domains=resolved, timeout=30.0, max_seconds=0.2)

        assert time.monotonic() - started < 0.35
        assert result["states"]["official_website_g4"] == "available"
        assert result["website_g4"]["evidence"]["value"]["final_url"].endswith(".no/")

    def test_all_dead_candidates_complete_without_hanging(self) -> None:
        record = _profile("923609016", "Dead AS", website="https://dead.example.no/")

        def dead_fetch(url: str, **_kwargs: object):
            return {"status": "failed", "source_url": url}, {"failure_kind": "connect"}

        started = time.monotonic()
        result = process_record(record, fetcher=dead_fetch, resolved_domains={item["domain"]: {"resolved": True} for item in candidate_domains(record)}, timeout=30.0, max_seconds=0.5)

        assert time.monotonic() - started < 0.3
        assert result["states"]["official_website"] in {"failed", "not_available"}

    def test_cache_builder_uses_the_shared_engine(self):
        self.assertIs(builder_process_record, process_record)

    def test_rorkjop_member_page_is_related_only(self):
        profile = _profile("923609016", "Mathisen VVS AS", "https://rorkjop.no/finn-forhandler/mathisen")
        site = _site(
            "https://rorkjop.no/finn-forhandler/mathisen",
            "Finn forhandler Mathisen VVS AS Org.nr 923 609 016. Andre butikker: Rør AS Org.nr 999 999 999.",
        )
        decision = detect_related_only_site(profile, site)
        self.assertTrue(decision["related_only"])
        self.assertEqual(decision["reason"], "chain_or_member_directory_page")

    def test_duplicate_domain_is_demoted_without_an_exact_number(self):
        profiles = []
        for org, name in (("923609016", "Alpha AS"), ("923609024", "Beta AS")):
            item = _profile(org, name, "https://shared.no/")
            item["evidence"]["website"] = _site("https://shared.no/", f"{name} contact")
            item["claims"] = {"official_website": {"final_url": "https://shared.no/"}}
            profiles.append(item)
        conflicts = enforce_domain_uniqueness(profiles)
        self.assertEqual(len(conflicts), 2)
        self.assertNotIn("official_website", profiles[0]["claims"])
        self.assertEqual(profiles[0]["discovery"]["related_only"]["domain"], "shared.no")

    def test_duplicate_domain_is_allowed_when_page_has_one_matching_number(self):
        profiles = []
        for org, name in (("923609016", "Alpha AS"), ("923609024", "Beta AS")):
            item = _profile(org, name, "https://shared.no/")
            item["evidence"]["website"] = _site("https://shared.no/", f"{name} Org.nr {org}")
            item["claims"] = {"official_website": {"final_url": "https://shared.no/"}}
            profiles.append(item)
        self.assertEqual(enforce_domain_uniqueness(profiles), [])
        self.assertIn("official_website", profiles[0]["claims"])

    def test_shared_engine_reports_honest_candidate_states(self):
        record = _profile("923609016", "Unknown AS", "https://unknown.example/")
        failed = process_record(record, fetcher=lambda *_args, **_kwargs: ({"status": "failed", "source_url": "https://unknown.example/"}, {"failure_kind": "connect"}))
        # Only guessed domains failed to load: that says nothing about the company.
        self.assertEqual(failed["states"]["official_website"], "not_available")

        blocked = process_record(record, fetcher=lambda *_args, **_kwargs: ({"status": "blocked", "source_url": "https://unknown.example/"}, {}))
        self.assertEqual(blocked["states"]["official_website"], "blocked")

    def test_slow_company_is_contained_without_waiting_for_the_host(self):
        original = batch.discover_profile

        def slow(*_args, **_kwargs):
            time.sleep(0.15)
            return {"states": {"official_website": "available"}, "claims": {}}

        batch.discover_profile = slow
        try:
            started = time.monotonic()
            result, metric = batch._run_discovery_bounded(
                _profile("923609016", "Slow AS"),
                gate="g4",
                timeout=0.01,
                run_deadline=None,
                request_policy=batch.HostRequestPolicy(),
                nav_index={},
            )
            self.assertLess(time.monotonic() - started, 0.1)
            self.assertTrue(metric["timed_out"])
            self.assertEqual(result["states"]["official_website"], "failed")
        finally:
            batch.discover_profile = original


if __name__ == "__main__":
    unittest.main()


class FinalStateTests(unittest.TestCase):
    def test_registry_linked_failure_makes_the_module_failed_but_guessed_failures_do_not(self):
        from norway_company_agent.discovery.engine import _final_state

        guessed = [{"source": "name_derived_no", "state": "failed"}, {"source": "name_derived_no", "state": "not_available"}]
        self.assertEqual(_final_state(guessed, published=False), "not_available")
        linked = [{"source": "registry_website", "state": "failed"}, {"source": "name_derived_no", "state": "not_available"}]
        self.assertEqual(_final_state(linked, published=False), "failed")
