from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.core.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.web.first_party import assess_first_party_ownership  # noqa: E402
from scripts.analysis.build_observation_audit import merge_audit  # noqa: E402
from scripts.analysis.evaluate_external_footprint import _is_human_label  # noqa: E402
from scripts.analysis.annotate_eval_evidence import annotate_one  # noqa: E402
from scripts.analysis.review_audit_packs import review_pack  # noqa: E402
from scripts.analysis.score_discovery_run import _certification_truth  # noqa: E402
from scripts.transform.build_verified_observations import guard_social_links  # noqa: E402


def site(title: str, text: str, url: str) -> dict:
    return {"status": "available", "source_url": url, "value": {"final_url": url, "title": title, "main_text_excerpt": text, "pages": [], "structured_organisations": [], "structured_identifiers": []}}


class S10AuditCorpusTests(unittest.TestCase):
    def test_registry_group_cases_are_not_publishable(self) -> None:
        creo = {"name": "CREONORDIC PROSJEKT AS", "organisation_number": "898467872", "website": "https://creonordic.no/", "raw": {"hjemmeside": "creonordic.no"}}
        creo_result = apply_website_identity_gate(creo, site("CreoNordic - Leverandør", "Vi i CreoNordic-gruppen org.nr. 998 467 888", "https://creonordic.no/"))
        self.assertFalse(creo_result["assessment"]["publishable"])
        self.assertFalse(assess_first_party_ownership(creo, creo_result["website"])["publishable"])

        akademiet = {"name": "NORGES REALFAGSGYMNAS SANDVIKA AS", "organisation_number": "998060257", "website": "https://akademiet.no/", "raw": {"hjemmeside": "akademiet.no"}}
        akademiet_result = apply_website_identity_gate(akademiet, site("Akademiet | Videregående | Grunnskole", "Akademiet-gruppen driver våre skoler. Norges Realfagsgymnas Sandvika.", "https://akademiet.no/"))
        self.assertTrue(akademiet_result["assessment"]["group_or_brand"])
        self.assertFalse(akademiet_result["assessment"]["publishable"])
        self.assertFalse(assess_first_party_ownership(akademiet, akademiet_result["website"])["publishable"])

    def test_ambiguous_platform_withholds_other_weak_handles(self) -> None:
        profile = {"name": "NORGES REALFAGSGYMNAS SANDVIKA AS"}
        links = [
            {"platform": "facebook", "url": "https://facebook.com/akademiet"},
            {"platform": "facebook", "url": "https://facebook.com/akademietutveksling"},
            {"platform": "youtube", "url": "https://youtube.com/@akademiet_no"},
        ]
        survivors, suppressed = guard_social_links(profile, links)
        self.assertEqual(survivors, [])
        self.assertEqual({item["reason"] for item in suppressed}, {"ambiguous_handle"})

    def test_clean_company_keeps_independent_single_handles(self) -> None:
        profile = {"name": "NORSK FISKEEKSPORT AS"}
        links = [
            {"platform": "facebook", "url": "https://facebook.com/norskfiskeeksport"},
            {"platform": "youtube", "url": "https://youtube.com/@norskfiskeeksport"},
        ]
        survivors, suppressed = guard_social_links(profile, links)
        self.assertEqual(len(survivors), 2)
        self.assertEqual(suppressed, [])

    def test_human_predicate_requires_browser_export_marker(self) -> None:
        self.assertFalse(_is_human_label({"labeler": "owner"}))
        self.assertFalse(_is_human_label({"labeler": "owner", "export_source": "manual.jsonl", "export_session_id": "x"}))
        self.assertTrue(_is_human_label({"labeler": "owner", "export_source": "audit-review.html", "export_session_id": "x"}))

    def test_assistant_no_requires_notes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            incoming = root / "labels.jsonl"
            output = root / "merged.jsonl"
            incoming.write_text(json.dumps({"id": "no", "exact_entity": False, "metric_correct": True, "labeler": "codex_review"}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "requires notes"):
                merge_audit(incoming, output, labeler="codex_review")

    def test_missing_alternative_provider_cannot_become_no_site(self) -> None:
        result = annotate_one(
            {"organisation_number": "123456785", "name": "EXAMPLE AS", "split": "held_out", "stratum": "S4"},
            {"raw": {}, "name": "EXAMPLE AS", "organisation_number": "123456785"},
            {"provider": "none_available", "provider_attempted": False, "provider_error": "unavailable", "queries": [], "results": [], "domain_checks": []},
            [],
            {},
        )
        self.assertEqual(result["outcome"], "undetermined")
        self.assertEqual(result["negative_checks"]["alternative_provider_search"]["status"], "not_run")

    def test_low_confidence_no_search_negative_is_not_certification_truth(self) -> None:
        row = {
            "outcome": "no_site_confirmed",
            "confidence": "low",
            "negative_checks": {"search": {"status": "not_run"}},
        }
        self.assertEqual(_certification_truth(row), "undetermined")
        row["confidence"] = "medium"
        self.assertEqual(_certification_truth(row), "no_site_confirmed")

    def test_audit_pack_review_does_not_promote_blocked_social(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "evidence.json"
            path.write_text(json.dumps({
                "id": "social",
                "organisation_number": "123456785",
                "registry_facts": {"registry_name": "EXAMPLE AS"},
                "row_context": {"platform": "youtube", "signal_type": "profile_handle"},
                "sources": [{"status": "fetch_blocked", "requested_url": "https://youtube.com/@example"}],
            }), encoding="utf-8")
            label = review_pack(path)
        self.assertEqual(label["label"], "unresolved")
        self.assertEqual(label["labeler"], "codex_review")


if __name__ == "__main__":
    unittest.main()
