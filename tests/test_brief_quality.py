from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.core.evidence import evidence  # noqa: E402
from norway_company_agent.site_facts import (  # noqa: E402
    extract_company_brief,
    extract_site_facts,
    enrich_site_facts,
    parse_html_metadata,
)
from scripts.analysis.local_scorecard import _format_text, score  # noqa: E402
from scripts.run.run_competition_batch import _run_discovery_bounded, _second_chance_retry_orgs  # noqa: E402
from norway_company_agent.web.website import HostRequestPolicy  # noqa: E402


FIXTURES = json.loads((ROOT / "tests/fixtures/brief_quality/snippets.json").read_text(encoding="utf-8"))
FURNITURE = {"hjem", "home", "om", "oss", "kontakt", "contact", "meny", "menu", "login", "logg", "inn", "cookies", "personvern", "powered", "copyright", "parked", "construction"}


def _profile(sample: dict, html: str | None = None) -> tuple[dict, dict]:
    url = "https://example.no/"
    html = html or sample["html"]
    metadata = parse_html_metadata(html, url)
    page = {"url": url, "html": html, **metadata}
    website = evidence(
        "website",
        "available",
        "company_owned",
        url,
        value={"final_url": url, "pages": [page], **metadata},
        retrieved_at="2026-10-01T12:00:00Z",
        content_sha256="a" * 64,
    )
    profile = {
        "organisation_number": "912345678",
        "name": sample["name"],
        "municipality": sample["municipality"],
        "industry_label": sample["industry"],
        "raw": {
            "navn": sample["name"],
            "forretningsadresse.kommune": sample["municipality"],
            "naeringskode1.beskrivelse": sample["industry"],
        },
        "evidence": {"website": website},
    }
    return profile, website


class BriefQualityTests(unittest.TestCase):
    def test_named_noisy_examples_produce_clean_site_or_registry_only_briefs(self) -> None:
        self.assertEqual(len(FIXTURES), 20)
        for sample in FIXTURES:
            with self.subTest(sample=sample["name"]):
                profile, website = _profile(sample)
                status, value = extract_company_brief(profile, website)
                self.assertIn(status, {"available", "ambiguous"})
                self.assertTrue(value["parts"])
                self.assertTrue(value["parts"][-1]["text"].startswith("Registrert i "))
                self.assertNotIn("Registered in ", value["summary"])
                site_parts = value["parts"][:-1]
                if sample["expected_site"]:
                    self.assertTrue(site_parts, value)
                    self.assertFalse(value.get("registry_derived"), value)
                    self.assertLessEqual(len(site_parts), 2)
                    site_text = " ".join(part["text"] for part in site_parts)
                    self.assertNotIn("�", site_text)
                    tokens = set(site_text.casefold().replace(".", " ").split())
                    self.assertFalse(tokens & FURNITURE, site_text)
                else:
                    self.assertTrue(value.get("registry_derived"), value)
                    self.assertEqual(site_parts, [], value)

    def test_registry_text_omits_uoppgitt_industry(self) -> None:
        sample = {**FIXTURES[2], "industry": "Uoppgitt"}
        profile, website = _profile(sample, "<html><head></head><body><main><p>" + "A genuinely useful company description with enough words for review." + "</p></main></body></html>")
        _status, value = extract_company_brief(profile, website)
        self.assertEqual(value["parts"][-1]["text"], "Registrert i OSLO.")

    def test_site_description_precedence_and_evidence_spans(self) -> None:
        sample = {"name": "Precedence AS", "municipality": "OSLO", "industry": "Programmering"}
        html = """
        <html><head>
          <meta name="description" content="Hjem Om oss Kontakt Meny">
          <meta property="og:description" content="We design durable software systems for Norwegian businesses and public services.">
          <script type="application/ld+json">{"@type":"Organization","description":"We design durable software systems for Norwegian businesses and public services."}</script>
        </head><body><main><p>Shorter fallback text that should not win over structured description.</p></main></body></html>
        """
        profile, website = _profile(sample, html)
        _status, value = extract_company_brief(profile, website)
        self.assertEqual(value["parts"][0]["kind"], "jsonld_description")
        self.assertTrue(value["parts"][0]["claim_span"])
        self.assertTrue(value["parts"][1]["claim_span"])

    def test_site_fact_metrics_include_phase_timings_and_reuse_soup(self) -> None:
        sample = FIXTURES[0]
        profile, _website = _profile(sample)
        facts = extract_site_facts(profile)
        timings = facts["metrics"]["phase_timings_ms"]
        for phase in ("activity", "hiring", "brief", "parse"):
            self.assertIn(phase, timings)
            self.assertGreaterEqual(timings[phase], 0)

    def test_enrichment_defaults_to_four_requests_and_six_second_fetches(self) -> None:
        sample = FIXTURES[0]
        profile, website = _profile(sample)
        website["value"]["pages"][0]["links"] = [{"url": f"/jobs/{i}", "text": "jobb"} for i in range(8)]
        calls = []

        def fake_fetch(url: str, **kwargs):
            calls.append((url, kwargs["timeout"]))
            return None, {"requests": 1, "bytes": 0, "latencies_ms": [0], "error": "fixture"}

        with patch("norway_company_agent.site_facts._fetch_fact_document", side_effect=fake_fetch):
            _facts, metrics = enrich_site_facts(profile)
        self.assertLessEqual(len(calls), 4)
        self.assertTrue(all(timeout <= 6.0 for _url, timeout in calls))
        self.assertIn("phase_timings_ms", metrics)

    def test_second_chance_only_retries_resolved_registry_linked_timeouts(self) -> None:
        state = {
            "1": {"discovery": {"state": "failed", "candidates": [{"source": "registry_website", "state": "failed", "failure_kind": "timeout"}]}},
            "2": {"discovery": {"state": "failed", "candidates": [{"source": "name_derived_no", "state": "failed", "failure_kind": "timeout"}]}},
            "3": {"discovery": {"state": "ambiguous", "candidates": [{"source": "registry_website", "state": "ambiguous"}]}},
        }
        self.assertEqual(_second_chance_retry_orgs(state), ["1"])

    def test_second_chance_fetcher_recovers_after_one_timeout(self) -> None:
        calls = []

        def flaky_discovery(*_args, **_kwargs):
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                return {"states": {"official_website": "failed"}, "candidates": [{"source": "registry_website", "state": "failed", "failure_kind": "timeout"}]}
            return {"states": {"official_website": "available"}, "candidates": [{"source": "registry_website", "state": "available"}]}

        profile = {"organisation_number": "1", "raw": {"navn": "Example AS"}}
        with patch("scripts.run.run_competition_batch.discover_profile", side_effect=flaky_discovery):
            first, _ = _run_discovery_bounded(profile, gate="g4", timeout=1.0, run_deadline=None, request_policy=HostRequestPolicy(), nav_index={}, executor=None)
            self.assertEqual(_second_chance_retry_orgs({"1": {"discovery": {"state": "failed", "candidates": first["candidates"]}}}), ["1"])
            second, _ = _run_discovery_bounded(profile, gate="g4", timeout=25.0, run_deadline=None, request_policy=HostRequestPolicy(), nav_index={}, executor=None)
        self.assertEqual(len(calls), 2)
        self.assertEqual(second["states"]["official_website"], "available")

    def test_scorecard_marks_self_pool_and_prints_absolute_counts(self) -> None:
        row = {
            "organisation_number": "1",
            "claims": [{"field": "leadership", "availability": "available", "value": [{"name": "A"}], "evidence_ids": ["e1"]}],
            "evidence": [{"id": "e1", "source_url": "https://example.no", "retrieved_at": "2026-10-01T00:00:00Z", "content_sha256": "a" * 64, "claim_span": "A"}],
            "changes": [],
            "unknown_areas": [],
        }
        result = score([row], [row])
        self.assertEqual(result["recall"]["comparability"], "self_pool_not_comparable")
        self.assertEqual(result["synthesis"]["comparability"], "self_pool_not_comparable")
        self.assertEqual(result["categories"]["recall_and_coverage"]["comparability"], "self_pool_not_comparable")
        self.assertEqual(result["categories"]["synthesis"]["comparability"], "self_pool_not_comparable")
        leadership = result["recall"]["types"]["leadership"]
        self.assertEqual(leadership["companies_covered"], 1)
        self.assertEqual(leadership["facts_covered"], 1)
        self.assertEqual(result["total_note"], "not an estimate of the official score")
        text = _format_text(result)
        self.assertIn("Recall comparability: self_pool_not_comparable", text)
        self.assertIn("Synthesis comparability: self_pool_not_comparable", text)
        self.assertIn("companies 1/1", text)
        self.assertIn("facts 1/1", text)


if __name__ == "__main__":
    unittest.main()
