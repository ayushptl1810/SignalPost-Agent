from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.core.identity import assess_website_identity  # noqa: E402
from norway_company_agent.web.first_party import assess_first_party_ownership  # noqa: E402
from norway_company_agent.web.website import _identity_text_excerpt  # noqa: E402
from scripts.analysis.qc_annotations import disagreement_rows  # noqa: E402
from scripts.analysis.score_discovery_run import build_scorecard, score_annotations  # noqa: E402
from scripts.run.run_search_discovery import (  # noqa: E402
    FetchEvidenceCache,
    ProviderFailureBreaker,
    ProviderFatalError,
    ReplayCacheMiss,
    SearchResultCache,
    _serper_once,
    serper_search,
)


class ReplayAndGateTests(unittest.TestCase):
    def test_credit_failure_is_fatal_and_does_not_retry(self) -> None:
        calls = 0

        def fail(_request, timeout):
            nonlocal calls
            calls += 1
            raise urllib.error.HTTPError(
                "https://google.serper.dev/search", 400, "credits", {}, io.BytesIO(b'{"message":"Not enough credits"}'),
            )

        with patch("scripts.run.run_search_discovery.urllib.request.urlopen", fail):
            with self.assertRaisesRegex(ProviderFatalError, "credits_exhausted"):
                serper_search({"name": "Example AS", "organisation_number": "123 456 789"}, "secret", timeout=1, count=5)
        self.assertEqual(calls, 1)

    def test_provider_breaker_trips_on_tenth_consecutive_error(self) -> None:
        breaker = ProviderFailureBreaker(threshold=10)
        operation = {"error": "HTTPError", "status": 500}
        for _ in range(9):
            breaker.observe(operation)
        with self.assertRaisesRegex(ProviderFatalError, "provider_error_breaker"):
            breaker.observe(operation)

    def test_search_and_fetch_cache_round_trip_and_replay_miss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            search_path = root / "search.jsonl"
            fetch_path = root / "fetch.jsonl"
            search = SearchResultCache(search_path)
            results = [{"url": "https://example.no/", "title": "Example", "snippet": "", "position": 1}]
            search.put('"Example AS" 123', results, num=5)
            replay = SearchResultCache(search_path, replay_only=True)
            self.assertEqual(replay.require_or_none('"Example AS" 123', num=5)[0], results)
            with self.assertRaises(ReplayCacheMiss):
                replay.require_or_none('"Missing AS"', num=5)

            fetch = FetchEvidenceCache(fetch_path)
            website = {"status": "available", "value": {"redirect_chain": ["https://old.no/", "https://new.no/"]}}
            operations = {"requests": 1, "latencies_ms": [2]}
            fetch.put("https://old.no/", website, operations)
            fetched = FetchEvidenceCache(fetch_path, replay_only=True)
            self.assertEqual(fetched.require_or_none("https://old.no/")[0], website)
            with self.assertRaises(ReplayCacheMiss):
                fetched.require_or_none("https://missing.no/")

    def test_redirect_alias_is_a_match_but_unrelated_domain_is_not(self) -> None:
        row = {
            "organisation_number": "1",
            "evidence": {
                "website_discovery": {"status": "available"},
                "website_discovered": {"value": {
                    "identity_assessment": {"publishable": True, "score": 1.0},
                    "first_party_assessment": {"candidate_domain": "new.no", "signals": {}},
                    "redirect_chain": ["https://old.no/", "https://new.no/"],
                }},
            },
        }
        alias = [{"organisation_number": "1", "name": "Example", "outcome": "official_site", "domain": "old.no", "stratum": "S1", "split": "development", "population_n": 1}]
        result = score_annotations([row], alias, min_published=1)
        self.assertEqual(result["counts"], {"tp": 1})
        self.assertEqual(result["redirect_matches"], 1)
        wrong = [{**alias[0], "domain": "other.no"}]
        self.assertEqual(score_annotations([row], wrong, min_published=1)["counts"], {"wrong_url": 1})

    def test_registry_listed_sparse_single_name_is_publishable(self) -> None:
        profile = {
            "name": "FUSUS AS",
            "organisation_number": "999 999 999",
            "website": "https://fusus.no",
            "evidence": {"website": {"status": "available", "value": {
                "final_url": "https://fusus.no/",
                "title": "Fusus",
                "main_text_excerpt": "",
                "identity_text_excerpt": "Fusus AS, Espedalsvegen 448, 4110 Forsand",
            }}},
        }
        result = assess_website_identity(profile)
        self.assertTrue(result["publishable"])
        self.assertIn("registry-listed homepage", result["reasons"][0])

    def test_footer_and_explicit_identity_container_survive_extraction(self) -> None:
        from bs4 import BeautifulSoup

        long_body = " ".join(["main content"] * 900)
        soup = BeautifulSoup(
            f'<html><body><main>{long_body}</main><div class="contact-box">Smile Factory AS Org.nr 921 065 647 Østre Nesttunvegen 2</div></body></html>',
            "lxml",
        )
        extracted = _identity_text_excerpt(soup)
        self.assertIn("Smile Factory AS", extracted)
        self.assertIn("921 065 647", extracted)

    def test_jsonld_identifier_is_identity_evidence(self) -> None:
        profile = {
            "name": "Smile Factory AS",
            "organisation_number": "921 065 647",
            "evidence": {"website": {"status": "available", "value": {
                "final_url": "https://smilefactory.no/",
                "title": "Smile Factory",
                "main_text_excerpt": "",
                "identity_text_excerpt": "Smile Factory",
                "structured_identifiers": ["921 065 647"],
            }}},
        }
        result = assess_website_identity(profile)
        self.assertEqual(result["score"], 1.0)

    def test_contradicting_njord_number_vetoes_name_match(self) -> None:
        profile = {
            "name": "NJORD AS",
            "organisation_number": "985 337 691",
            "evidence": {"website": {"status": "available", "value": {
                "final_url": "https://seakayaknorway.com/",
                "title": "NJORD AS",
                "main_text_excerpt": "NJORD AS",
                "identity_text_excerpt": "NJORD AS Org.nr 930 863 491",
            }}},
        }
        result = assess_website_identity(profile)
        self.assertFalse(result["publishable"])
        self.assertEqual(result["contradicting_organisation_numbers"], ["930863491"])

    def test_group_number_is_flagged_related_but_not_contradiction(self) -> None:
        profile = {
            "name": "Target AS",
            "organisation_number": "985 337 691",
            "municipality": "OSLO",
            "evidence": {"group": {"value": {"organisasjonsnummer": "930 863 491"}}},
        }
        website = {
            "status": "available",
            "source_url": "https://group.no/",
            "value": {
                "final_url": "https://group.no/",
                "identity_assessment": {"publishable": True, "score": 1.0},
                "title": "Target AS",
                "main_text_excerpt": "Target AS Org.nr 930 863 491",
                "identity_text_excerpt": "Target AS Org.nr 930 863 491",
            },
        }
        identity = assess_website_identity({**profile, "evidence": {"group": profile["evidence"]["group"], "website": website}})
        self.assertFalse(identity["contradicted"])
        assessment = assess_first_party_ownership(profile, website, istat_gate=True)
        self.assertEqual(assessment["status"], "related_entity")
        self.assertFalse(assessment["publishable"])

    def test_provider_fatal_scorecard_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider-fatal"):
            build_scorecard([], {"provider_fatal": "credits_exhausted"})

    def test_disagreement_export_contains_errors_and_priority_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            annotations = [
                {"organisation_number": "1", "name": "Example", "outcome": "official_site", "domain": "right.no", "source_urls": ["https://source"], "split": "development"},
                {"organisation_number": "986801235", "name": "Niprox", "outcome": "no_site_confirmed", "domain": None, "source_urls": [], "split": "development"},
            ]
            annotation_path = root / "annotations.jsonl"
            annotation_path.write_text("".join(json.dumps(row) + "\n" for row in annotations), encoding="utf-8")
            scorecard_path = root / "scorecard.json"
            scorecard_path.write_text(json.dumps({
                "profiles_path": str(root / "profiles.jsonl"),
                "annotations": {"errors": [{"organisation_number": "1", "error": "wrong_url"}]},
                "verdicts": {"1": {"outcome": "verified", "domain": "wrong.no", "source": "search"}},
            }), encoding="utf-8")
            (root / "profiles.jsonl").write_text(json.dumps({"organisation_number": "1", "evidence": {"website_discovered": {"source_url": "https://wrong.no/", "value": {"first_party_assessment": {"candidate_domain": "wrong.no"}}}}}) + "\n", encoding="utf-8")
            rows = disagreement_rows(annotation_path, scorecard_path)
            self.assertEqual([row["organisation_number"] for row in rows], ["986801235", "1"])
            self.assertEqual(rows[0]["reviewer_verdict"], "")
            self.assertEqual(rows[1]["pipeline_domain"], "wrong.no")


if __name__ == "__main__":
    unittest.main()
