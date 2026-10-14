from __future__ import annotations

import contextlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.core.evidence import evidence  # noqa: E402
from norway_company_agent.discovery import candidate_domains, process_record  # noqa: E402
from norway_company_agent.web import website as website_module  # noqa: E402
from scripts.run import run_competition_batch as batch  # noqa: E402


def _profile(name: str = "Example Drift og Service AS") -> dict:
    return {
        "organisation_number": "923609016",
        "name": name,
        "website": "",
        "raw": {
            "forretningsadresse.postnummer": "0123",
            "forretningsadresse.poststed": "Oslo",
        },
        "evidence": {},
    }


def _site(url: str, text: str, *, pages: list[dict] | None = None) -> dict:
    return evidence(
        "website",
        "available",
        "registry_linked_company_website",
        url,
        value={
            "final_url": url,
            "title": text,
            "main_text_excerpt": text,
            "identity_text_excerpt": text,
            "pages": list(pages or []),
        },
    )


class LeanFetchTests(unittest.TestCase):
    def test_homepage_only_skips_sitemap_and_secondary_requests(self) -> None:
        html = b"<html><head><title>Example Drift</title></head><body>Example Drift og Service</body></html>"

        class Response:
            headers = {"content-type": "text/html"}

            def geturl(self) -> str:
                return "https://example.no/"

        @contextlib.contextmanager
        def open_response(*_args, **_kwargs):
            yield Response()

        with (
            patch.object(website_module, "assert_public_url"),
            patch.object(website_module, "_robots_policy", return_value=(True, [])),
            patch.object(website_module, "_open_response", open_response),
            patch.object(website_module, "_read_response", return_value=html),
            patch.object(website_module, "_discover_sitemap_pages") as sitemap,
        ):
            result, metrics = website_module._fetch_website_once("https://example.no/", max_secondary_pages=0)

        sitemap.assert_not_called()
        self.assertEqual(metrics["requests"], 2)
        self.assertEqual(result["value"]["fetch_mode"], "homepage_only")
        self.assertEqual(len(result["value"]["pages"]), 1)

    def test_full_fetch_still_discovers_secondary_pages(self) -> None:
        html = b"<html><head><title>Example Drift</title></head><body><a href='/kontakt'>Kontakt</a> Example Drift</body></html>"

        class Response:
            headers = {"content-type": "text/html"}

            def geturl(self) -> str:
                return "https://example.no/"

        @contextlib.contextmanager
        def open_response(*_args, **_kwargs):
            yield Response()

        secondary = {"url": "https://example.no/kontakt", "title": "Kontakt", "main_text_excerpt": "Contact"}
        with (
            patch.object(website_module, "assert_public_url"),
            patch.object(website_module, "_robots_policy", return_value=(True, [])),
            patch.object(website_module, "_open_response", open_response),
            patch.object(website_module, "_read_response", return_value=html),
            patch.object(website_module, "_discover_sitemap_pages", return_value=(["https://example.no/kontakt"], 1, 12, [1], [])),
            patch.object(website_module, "_fetch_secondary_page", return_value=(secondary, [], 1, 12, 1, None)),
        ):
            result, metrics = website_module._fetch_website_once("https://example.no/", max_secondary_pages=1)

        self.assertNotEqual(result["value"].get("fetch_mode"), "homepage_only")
        self.assertEqual(len(result["value"]["pages"]), 2)
        self.assertGreaterEqual(metrics["requests"], 4)


class HomepageEnrichmentTests(unittest.TestCase):
    def test_publishable_name_candidate_is_enriched_and_counters_are_reported(self) -> None:
        record = _profile()
        candidate = next(item for item in candidate_domains(record) if item["source"] == "name_derived_no")
        calls: list[int] = []

        def fetch(url: str, **kwargs: object):
            calls.append(int(kwargs.get("max_secondary_pages") or 0))
            if calls[-1] == 0:
                return _site(url, "Example Drift og Service AS 0123 Oslo"), {"requests": 2, "bytes": 10, "fetch_mode": "homepage_only"}
            return _site(url, "Example Drift og Service AS 0123 Oslo", pages=[{"url": url + "kontakt", "main_text_excerpt": "Kontakt"}]), {"requests": 2, "bytes": 20}

        result = process_record(
            record,
            gate="g4",
            fetcher=fetch,
            resolved_domains={candidate["domain"]: {"resolved": True}},
        )

        self.assertEqual(calls, [0, 4])
        self.assertEqual(result["states"]["official_website_g4"], "available")
        self.assertEqual(len(result["website_g4"]["evidence"]["value"]["pages"]), 1)
        self.assertEqual(result["metrics"]["name_derived_homepage_only"], 1)
        self.assertEqual(result["metrics"]["enrichment_attempted"], 1)
        self.assertEqual(result["metrics"]["enrichment_failed"], 0)
        self.assertEqual(result["metrics"]["mean_requests_per_candidate_by_source"]["name_derived_no"], 4.0)

    def test_unpublishable_name_candidate_is_not_enriched(self) -> None:
        record = _profile()
        candidate = next(item for item in candidate_domains(record) if item["source"] == "name_derived_no")
        calls: list[int] = []

        def fetch(url: str, **kwargs: object):
            calls.append(int(kwargs.get("max_secondary_pages") or 0))
            return _site(url, "Unrelated homepage"), {"requests": 2, "bytes": 10, "fetch_mode": "homepage_only"}

        result = process_record(record, gate="g4", fetcher=fetch, resolved_domains={candidate["domain"]: {"resolved": True}})

        self.assertEqual(calls, [0])
        self.assertEqual(result["metrics"]["enrichment_attempted"], 0)
        self.assertEqual(result["states"]["official_website_g4"], "not_available")

    def test_enrichment_failure_keeps_publication_and_records_audit_error(self) -> None:
        record = _profile()
        candidate = next(item for item in candidate_domains(record) if item["source"] == "name_derived_no")
        calls: list[int] = []

        def fetch(url: str, **kwargs: object):
            calls.append(int(kwargs.get("max_secondary_pages") or 0))
            if calls[-1] == 0:
                return _site(url, "Example Drift og Service AS 0123 Oslo"), {"requests": 2, "bytes": 10, "fetch_mode": "homepage_only"}
            return {"status": "failed", "source_url": url, "value": {}}, {"requests": 1, "failure_kind": "timeout"}

        result = process_record(record, gate="g4", fetcher=fetch, resolved_domains={candidate["domain"]: {"resolved": True}})

        self.assertEqual(calls, [0, 4])
        self.assertEqual(result["states"]["official_website_g4"], "available")
        self.assertEqual(result["metrics"]["enrichment_failed"], 1)
        self.assertTrue(any(item.get("error") == "enrichment_failed" for item in result["website_g4"]["evidence"]["value"]["crawl_errors"]))


class RobotsAndSecondChanceTests(unittest.TestCase):
    def test_robots_timeout_allows_homepage_but_a_200_disallow_blocks(self) -> None:
        with patch.object(website_module, "assert_public_url"):
            with patch.object(website_module, "_open_response", side_effect=TimeoutError("robots timed out")):
                self.assertEqual(website_module._robots_policy("https://example.no/", 3.0), (True, []))
        self.assertEqual(website_module._robots_policy.__name__, "_robots_policy")

        record = _site("https://example.no/", "Example")
        with patch.object(website_module, "assert_public_url"), patch.object(website_module, "_robots_policy", return_value=(False, [])):
            blocked, metrics = website_module._fetch_website_once("https://example.no/")
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(metrics["requests"], 1)

    def test_re_resolved_name_timeout_precedes_dead_name_guess(self) -> None:
        state = {
            "live": {"discovery": {"state": "failed", "candidates": [{
                "source": "name_derived_no", "state": "failed", "failure_kind": "timeout", "homepage_timed_out": True,
                "requested_url": "https://live-again.no/",
            }]}},
            "dead": {"discovery": {"state": "failed", "candidates": [{
                "source": "name_derived_no", "state": "failed", "failure_kind": "timeout", "homepage_timed_out": True,
                "requested_url": "https://dead-guess.no/",
            }]}},
        }
        refreshed = {"live": {"live-again.no": {"resolved": True}}, "dead": {}}
        self.assertEqual(batch._second_chance_retry_orgs(state, refreshed), ["live", "dead"])


class AuditAndVariantTests(unittest.TestCase):
    def test_related_demoted_audit_is_observational(self) -> None:
        discovery = {
            "organisation_number": "1",
            "name": "Stord Reinhald AS",
            "candidates": [{
                "source": "name_derived_no",
                "domain": "stordreinhald.no",
                "identity": {"status": "exact", "score": 0.95, "contradicted": False, "page_organisation_numbers": []},
                "first_party": {"status": "related_entity", "signals": {"contradicted": False, "other_page_organisation_numbers": 0}},
                "g4": {"evidence_span": "group page", "related_only": {"related_only": True}},
            }],
        }
        rows = batch._related_demoted_audit_rows(discovery)
        self.assertEqual(rows[0]["domain"], "stordreinhald.no")
        self.assertEqual(rows[0]["group_or_brand_tokens"], [])
        self.assertEqual(rows[0]["evidence_span"], "group page")

    def test_name_variants_are_off_by_default_and_capped_when_enabled(self) -> None:
        record = _profile("The P.E. Gaarud AS")
        self.assertFalse(any(item["source"] == "name_variant" for item in candidate_domains(record)))
        variants = [item for item in candidate_domains(record, include_name_variants=True) if item["source"] == "name_variant"]
        self.assertLessEqual(len(variants), 2)
        self.assertTrue(any("pe-gaarud" in item["domain"] for item in variants))


if __name__ == "__main__":
    unittest.main()
