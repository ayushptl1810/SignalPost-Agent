from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.web.classification import (  # noqa: E402
    DECISION_LABELS,
    LayaCandidateClassifier,
    RuleBasedCandidateClassifier,
    build_candidate_classifier,
)
from norway_company_agent.web.discovery import (  # noqa: E402
    build_company_search_queries,
    choose_search_candidates,
    parse_serper_results,
    score_search_candidate,
)
from norway_company_agent.web.first_party import assess_first_party_ownership  # noqa: E402
from norway_company_agent.web.website import _discover_sitemap_pages, _read_response, _wall_timeout, parse_sitemap_locations, priority_sitemap_links  # noqa: E402
from scripts.run.run_search_discovery import serper_search  # noqa: E402


PROFILE = {
    "name": "Norsk Fiskeeksport AS",
    "organisation_number": "923 609 016",
    "municipality": "NOTODDEN",
}


def website_fixture(url: str, text: str, *, pages: list[dict[str, str]] | None = None) -> dict:
    return {
        "status": "available",
        "source_url": url,
        "value": {
            "final_url": url,
            "title": "Norsk Fiskeeksport AS",
            "main_text_excerpt": text,
            "pages": pages or [],
            "structured_organisations": [],
            "identity_assessment": {"status": "exact", "publishable": True},
        },
    }


class SearchArchitectureTests(unittest.TestCase):
    def test_query_variants_keep_exact_identity_and_add_bounded_fallback(self):
        queries = build_company_search_queries(PROFILE)

        self.assertEqual(len(queries), 2)
        self.assertIn('"Norsk Fiskeeksport AS"', queries[0])
        self.assertIn("NOTODDEN", queries[0])
        self.assertIn('"Norsk Fiskeeksport AS" 923609016', queries[1])

    def test_serper_parser_normalizes_organic_results(self):
        results = parse_serper_results(
            {
                "organic": [
                    {
                        "position": 1,
                        "title": "Norsk Fiskeeksport AS",
                        "link": "https://example-group.no/company",
                        "snippet": "923609016 in Notodden",
                    },
                    {"title": "Missing URL"},
                ]
            },
            query='"Norsk Fiskeeksport AS" 923609016',
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider"], "serper_api")
        self.assertEqual(results[0]["rank"], 1)
        self.assertEqual(results[0]["url"], "https://example-group.no/company")

    def test_candidate_selection_does_not_require_legal_name_in_hostname(self):
        decision = choose_search_candidates(
            PROFILE,
            [
                {
                    "url": "https://example-group.no/companies",
                    "title": "Norsk Fiskeeksport AS",
                    "snippet": "923609016, seafood exporter in Notodden",
                    "rank": 1,
                    "provider": "serper_api",
                }
            ],
            limit=3,
        )

        self.assertFalse(decision["abstained"])
        self.assertEqual(len(decision["selected"]), 1)
        self.assertEqual(decision["selected"][0]["status"], "accepted_for_crawl")

    def test_candidate_selection_deduplicates_registered_domains(self):
        decision = choose_search_candidates(
            PROFILE,
            [
                {
                    "url": "https://www.example-group.no/one",
                    "title": "Norsk Fiskeeksport AS",
                    "snippet": "923609016",
                    "rank": 1,
                },
                {
                    "url": "http://example-group.no/two",
                    "title": "Norsk Fiskeeksport AS",
                    "snippet": "923609016",
                    "rank": 2,
                },
            ],
            limit=3,
        )

        self.assertEqual(len(decision["selected"]), 1)
        self.assertEqual(decision["selected"][0]["registered_domain"], "example-group.no")

    def test_serper_request_keeps_secret_out_of_url(self):
        captured: dict[str, object] = {}

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"organic": [{
                    "position": 1,
                    "link": "https://example-group.no",
                    "title": "Norsk Fiskeeksport AS",
                    "snippet": "923609016",
                }]}).encode()

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode())
            captured["timeout"] = timeout
            return Response()

        with patch("scripts.run.run_search_discovery.urllib.request.urlopen", fake_open):
            results, operation = serper_search(PROFILE, "secret-test-key", timeout=3.0, count=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(operation["status"], 200)
        self.assertNotIn("secret-test-key", str(captured["url"]))
        headers = {str(key).casefold(): value for key, value in captured["headers"].items()}
        self.assertEqual(headers["x-api-key"], "secret-test-key")
        self.assertEqual(captured["body"]["gl"], "no")
        self.assertEqual(captured["body"]["hl"], "no")
        self.assertEqual(captured["body"]["num"], 5)
        self.assertEqual(captured["timeout"], 3.0)

    def test_rule_classifier_returns_closed_set_decision(self):
        classifier = RuleBasedCandidateClassifier()
        decision = classifier.classify(
            PROFILE,
            {
                "url": "https://example-group.no",
                "title": "Norsk Fiskeeksport AS",
                "snippet": "923609016",
            },
        )

        self.assertEqual(decision["choice"], "exact_entity")
        self.assertEqual(set(decision["probabilities"]), {
            "exact_entity",
            "related_entity",
            "wrong_entity",
            "insufficient_evidence",
        })
        self.assertEqual(decision["classifier"], "rules_v1")

    def test_rule_classifier_does_not_leak_target_identity_into_candidate_evidence(self):
        decision = RuleBasedCandidateClassifier().classify(
            PROFILE,
            {
                "url": "https://unrelated.no",
                "title": "Unrelated company",
                "snippet": "A separate business with no matching identifiers.",
            },
        )

        self.assertEqual(decision["choice"], "insufficient_evidence")

    def test_laya_adapter_maps_typed_decision_to_closed_set(self):
        class FakeRouter:
            def __init__(self):
                self.calls = []

            def predict(self, state, questions, model):
                self.calls.append((state, questions, model))
                return {
                    "answers": {
                        "relationship": {
                            "choice": "exact_entity",
                            "confidence": 0.91,
                            "probabilities": {"exact_entity": 0.91, "wrong_entity": 0.09},
                        }
                    },
                    "routing": {"model": model},
                }

        router = FakeRouter()
        decision = LayaCandidateClassifier(router=router).classify(PROFILE, {
            "url": "https://example.no",
            "title": "Norsk Fiskeeksport AS",
            "snippet": "923609016",
        })

        self.assertEqual(decision["choice"], "exact_entity")
        self.assertEqual(decision["model"], "multilingual")
        self.assertEqual(set(decision["probabilities"]), set(DECISION_LABELS))
        self.assertEqual(router.calls[0][2], "multilingual")

    def test_first_party_verifier_accepts_contact_and_address_match(self):
        profile = {
            **PROFILE,
            "raw": {
                "epostadresse": "post@norskfiske.no",
                "telefon": "+47 22 33 44 55",
                "forretningsadresse.adresse": "Havneveien 12",
                "forretningsadresse.postnummer": "0123",
                "forretningsadresse.poststed": "NOTODDEN",
            },
        }
        website = website_fixture(
            "https://norskfiske.no/",
            "Norsk Fiskeeksport AS. Havneveien 12, 0123 NOTODDEN. +47 22 33 44 55.",
            pages=[{
                "title": "Kontakt",
                "main_text_excerpt": "post@norskfiske.no",
            }],
        )

        assessment = assess_first_party_ownership(profile, website)

        self.assertTrue(assessment["publishable"])
        self.assertEqual(assessment["status"], "first_party")
        self.assertTrue(assessment["signals"]["registry_email_domain_match"])
        self.assertTrue(assessment["signals"]["phone_match"])
        self.assertTrue(assessment["signals"]["address_match"])
        self.assertNotIn("post@norskfiske.no", json.dumps(assessment))
        self.assertNotIn("Havneveien", json.dumps(assessment))

    def test_first_party_verifier_rejects_known_registry_or_directory_hosts(self):
        profile = {
            **PROFILE,
            "raw": {
                "epostadresse": "post@norskfiske.no",
                "telefon": "+47 22 33 44 55",
                "forretningsadresse.adresse": "Havneveien 12",
                "forretningsadresse.postnummer": "0123",
                "forretningsadresse.poststed": "NOTODDEN",
            },
        }
        website = website_fixture(
            "https://sgregister.dibk.no/enterprises/923609016",
            "Norsk Fiskeeksport AS 923609016 Havneveien 12 0123 NOTODDEN +47 22 33 44 55 post@norskfiske.no",
        )

        assessment = assess_first_party_ownership(profile, website)

        self.assertFalse(assessment["publishable"])
        self.assertEqual(assessment["status"], "directory_or_registry")
        self.assertTrue(assessment["signals"]["blocked_host"])

    def test_directory_hosts_and_listing_paths_are_rejected_before_first_party_matching(self):
        profile = {
            **PROFILE,
            "raw": {
                "epostadresse": "post@norskfiske.no",
                "telefon": "+47 22 33 44 55",
                "forretningsadresse.adresse": "Havneveien 12",
                "forretningsadresse.postnummer": "0123",
                "forretningsadresse.poststed": "NOTODDEN",
            },
        }
        website = website_fixture(
            "https://opplysning.byndle.no/selskap/norsk-fiskeeksport-as/923609016",
            "Norsk Fiskeeksport AS. Havneveien 12, 0123 NOTODDEN. +47 22 33 44 55.",
        )

        assessment = assess_first_party_ownership(profile, website)

        self.assertFalse(assessment["publishable"])
        self.assertEqual(assessment["status"], "directory_or_registry")
        self.assertTrue(assessment["signals"]["blocked_host"])
        self.assertTrue(assessment["signals"]["listing_path_marker"])

    def test_directory_listing_path_is_rejected_before_crawling(self):
        decision = score_search_candidate(
            PROFILE,
            {
                "url": "https://example-group.no/bedrifter/norsk-fiskeeksport-as/923609016",
                "title": "Norsk Fiskeeksport AS",
                "snippet": "923609016, NOTODDEN",
                "rank": 1,
            },
        )

        self.assertFalse(decision["publishable_candidate"])
        self.assertEqual(decision["status"], "rejected")
        self.assertIn("listing path", decision["reasons"][0])

    def test_observed_directory_urls_are_rejected_before_crawling(self):
        urls = [
            "https://haku.vainu.com/company/noni-network-norway-as/NO989247980/bedriftsinformasjon",
            "https://foretaksinfo.no/foretak/981598636/voss-storhusholdningsservice-as",
            "https://tanntrad.no/tannlege/tannlegene-bommen-as",
            "https://northdata.de/Bryne%20Sentrum%20Utvikling%20AS,%20Oslo/BR%20931471058",
            "https://aktie.no/produkter/prospekt/liste/nannestad/68-eiendomsvirksomhet/123",
            "https://courierslist.com/detail/norway/trondheim/kjeldsberg-transporttjenester-as",
        ]
        for url in urls:
            with self.subTest(url=url):
                decision = score_search_candidate(
                    {**PROFILE, "name": "Noni Network Norway AS", "organisation_number": "989247980"},
                    {"url": url, "title": "Noni Network Norway AS", "snippet": "989247980"},
                )
                self.assertFalse(decision["publishable_candidate"])
                self.assertEqual(decision["status"], "rejected")

    def test_known_directory_variant_and_membership_listing_are_rejected(self):
        known_directory = score_search_candidate(
            PROFILE,
            {
                "url": "https://proffi.no/en/company/norsk-fiskeeksport-as-923609016",
                "title": "Norsk Fiskeeksport AS",
                "snippet": "923609016, NOTODDEN",
            },
        )
        membership_listing = score_search_candidate(
            PROFILE,
            {
                "url": "https://example-group.no/medlemsbedrift/norsk-fiskeeksport-as",
                "title": "Norsk Fiskeeksport AS",
                "snippet": "923609016, NOTODDEN",
            },
        )

        self.assertEqual(known_directory["status"], "rejected")
        self.assertEqual(membership_listing["status"], "rejected")

    def test_first_party_verifier_does_not_accept_address_alone(self):
        profile = {
            **PROFILE,
            "raw": {
                "forretningsadresse.adresse": "Havneveien 12",
                "forretningsadresse.postnummer": "0123",
                "forretningsadresse.poststed": "NOTODDEN",
            },
        }
        website = website_fixture(
            "https://plausible-company.no/",
            "Norsk Fiskeeksport AS. Havneveien 12, 0123 NOTODDEN.",
        )

        assessment = assess_first_party_ownership(profile, website)

        self.assertFalse(assessment["publishable"])
        self.assertEqual(assessment["status"], "insufficient_evidence")

    def test_first_party_does_not_use_page_email_domain_as_ownership(self):
        profile = {
            **PROFILE,
            "raw": {
                "epostadresse": "post@registry-company.no",
                "forretningsadresse.adresse": "Havneveien 12",
                "forretningsadresse.postnummer": "0123",
                "forretningsadresse.poststed": "NOTODDEN",
            },
        }
        assessment = assess_first_party_ownership(profile, website_fixture(
            "https://directory-example.no/",
            "Norsk Fiskeeksport AS Havneveien 12 0123 NOTODDEN info@directory-example.no",
        ))
        self.assertTrue(assessment["signals"]["page_email_domain_match"])
        self.assertFalse(assessment["publishable"])

    def test_relaxed_gate_accepts_exact_identity_with_registry_email(self):
        profile = {**PROFILE, "raw": {"epostadresse": "post@example-group.no"}}
        website = website_fixture("https://example-group.no/", "Norsk Fiskeeksport AS")
        website["value"]["identity_assessment"] = {"status": "exact", "score": 0.95, "publishable": True}
        assessment = assess_first_party_ownership(profile, website, relax_address_gate=True)
        self.assertTrue(assessment["publishable"])
        self.assertFalse(assessment["signals"]["address_match"])

    def test_sitemap_parser_extracts_same_domain_locations_only(self):
        locations = parse_sitemap_locations(
            b'''<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.no/kontakt</loc></url>
              <url><loc>https://example.no/personvern</loc></url>
              <url><loc>https://other.no/contact</loc></url>
            </urlset>''',
            "https://example.no/",
        )

        self.assertEqual(locations, ["https://example.no/kontakt", "https://example.no/personvern"])

    def test_sitemap_priority_prefers_contact_and_legal_pages(self):
        selected = priority_sitemap_links(
            "https://example.no/",
            [
                "https://example.no/blog/nyhet-1",
                "https://example.no/kontakt",
                "https://example.no/personvern",
                "https://example.no/om-oss",
            ],
            limit=3,
        )

        self.assertEqual(selected, [
            "https://example.no/kontakt",
            "https://example.no/om-oss",
            "https://example.no/personvern",
        ])

    def test_sitemap_discovery_fetches_bounded_same_domain_document(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _max_bytes):
                return b'''<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url><loc>https://example.no/kontakt</loc></url>
                  <url><loc>https://example.no/blog/post</loc></url>
                </urlset>'''

            def geturl(self):
                return "https://example.no/sitemap.xml"

        with patch("norway_company_agent.web.website.assert_public_url"), patch(
            "norway_company_agent.web.website.SAFE_OPENER.open", return_value=Response()
        ):
            pages, requests, _bytes, _latencies, errors = _discover_sitemap_pages(
                "https://example.no/",
                [],
                timeout=1.0,
                max_bytes=10000,
            )

        self.assertEqual(pages, ["https://example.no/kontakt"])
        self.assertEqual(requests, 1)
        self.assertEqual(errors, [])

    def test_response_reader_chunks_and_caps_body(self):
        class Response:
            def __init__(self):
                self.body = b"abcdefghi"

            def read(self, size):
                self.assert_size = size
                chunk, self.body = self.body[:size], self.body[size:]
                return chunk

        response = Response()
        self.assertEqual(_read_response(response, 7, 1.0), b"abcdefg")
        self.assertEqual(response.assert_size, 7)

    def test_wall_timeout_interrupts_blocking_operation(self):
        with self.assertRaises(TimeoutError):
            with _wall_timeout(0.01):
                time.sleep(0.1)

    def test_classifier_factory_defaults_to_rules_and_rejects_unknown_backend(self):
        self.assertIsInstance(build_candidate_classifier("rules"), RuleBasedCandidateClassifier)
        with self.assertRaises(ValueError):
            build_candidate_classifier("unknown")


if __name__ == "__main__":
    unittest.main()
