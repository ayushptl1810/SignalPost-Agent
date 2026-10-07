from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.analysis.score_discovery_run import (  # noqa: E402
    _append_evaluation_look,
    build_scorecard,
    classify_row,
    diff_verdicts,
    main as scorecard_main,
    score_annotations,
    summary_lines,
    wilson_lower,
)

STRONG = {"registry_email_domain_match": True, "address_match": True}


def verified_row(org: str, domain: str, signals: dict | None = None, score: float = 1.0) -> dict:
    return {
        "organisation_number": org,
        "website": "",
        "evidence": {
            "website_discovery": {"status": "available"},
            "website_discovered": {"value": {
                "identity_assessment": {"score": score, "publishable": True},
                "first_party_assessment": {"candidate_domain": domain, "signals": signals if signals is not None else STRONG},
            }},
        },
    }


def candidate_row(org: str, *candidates: dict) -> dict:
    return {
        "organisation_number": org,
        "website": "",
        "evidence": {
            "website_discovery": {"status": "not_found"},
            "website_discovered_candidates": list(candidates),
        },
    }


def empty_row(org: str) -> dict:
    return {"organisation_number": org, "website": "", "evidence": {"website_discovery": {"status": "not_found"}}}


def cached_row(org: str) -> dict:
    return {
        "organisation_number": org,
        "website": "",
        "evidence": {"website_discovery": {"status": "available", "value": {"skipped_negative_cache": True}}},
    }


def timed_out_row(org: str) -> dict:
    return {
        "organisation_number": org,
        "website": "",
        "evidence": {"website_discovery": {"status": "timed_out", "value": {"timed_out": True}}},
    }


def annotation(org: str, outcome: str, *, domain: str | None = None, stratum: str = "S1", split: str = "development", population_n: int = 100) -> dict:
    return {
        "organisation_number": org,
        "name": f"Company {org}",
        "outcome": outcome,
        "domain": domain,
        "stratum": stratum,
        "split": split,
        "population_n": population_n,
    }


REPORT = {"counts": {"provider_requests": 10, "provider_errors": 2, "crawl_requests": 30}, "crawl_latency_ms": {"p95": 900}}


class ScorecardTests(unittest.TestCase):
    def test_outcome_precedence(self) -> None:
        self.assertEqual(classify_row(verified_row("1", "a.no"))["outcome"], "verified")
        self.assertEqual(classify_row(empty_row("2"))["outcome"], "no_candidate")
        self.assertEqual(classify_row(candidate_row("3", {"identity_publishable": False}))["outcome"], "crawled_identity_failed")
        self.assertEqual(
            classify_row(candidate_row("4", {"identity_publishable": True, "first_party_status": "directory_or_registry", "registered_domain": "x.no"}))["outcome"],
            "identity_ok_directory_or_registry",
        )
        self.assertEqual(
            classify_row(candidate_row(
                "5",
                {"identity_publishable": True, "first_party_status": "directory_or_registry", "registered_domain": "x.no"},
                {"identity_publishable": True, "first_party_status": "insufficient_evidence", "registered_domain": "y.no"},
            ))["outcome"],
            "identity_ok_first_party_insufficient",
        )
        self.assertEqual(classify_row({"organisation_number": "6", "website": "https://a.no", "evidence": {}})["outcome"], "registry_site")

    def test_unqueried_and_registry_rows_do_not_count_as_queried(self) -> None:
        rows = [verified_row("1", "a.no"), empty_row("2"), {"organisation_number": "3", "website": "https://c.no", "evidence": {}}]
        card = build_scorecard(rows, REPORT)
        self.assertEqual(card["queried"], 2)
        self.assertEqual(card["coverage"]["yield_on_queried"], 0.5)

    def test_trust_proxy_penalises_weak_and_shared_domains(self) -> None:
        rows = [
            verified_row("1", "a.no"),
            verified_row("2", "b.no", signals={}),  # org match but no corroborator
            verified_row("3", "shared.no"),
            verified_row("4", "shared.no"),  # same domain claimed twice
        ]
        card = build_scorecard(rows, REPORT)
        self.assertEqual(card["trust"]["strong"], 1)
        self.assertEqual(card["trust"]["trust_proxy"], 0.25)
        self.assertEqual(card["trust"]["shared_domains"], {"shared.no": ["3", "4"]})
        self.assertIn("2", card["review_queue"])

    def test_suspected_directories_need_two_companies(self) -> None:
        directory = {"identity_publishable": True, "first_party_status": "insufficient_evidence", "registered_domain": "dir.no"}
        rows = [candidate_row("1", directory), candidate_row("2", directory), candidate_row("3", {**directory, "registered_domain": "solo.no"})]
        card = build_scorecard(rows, REPORT)
        self.assertEqual(card["suspected_directories"], [{"domain": "dir.no", "companies": 2}])

    def test_wilson_bound_is_conservative_for_small_samples(self) -> None:
        self.assertIsNone(wilson_lower(0, 0))
        self.assertLess(wilson_lower(1, 1), 0.3)
        self.assertGreater(wilson_lower(95, 100), 0.88)

    def test_labels_drive_precision_and_gate(self) -> None:
        rows = [verified_row(str(i), f"s{i}.no") for i in range(1, 6)]
        labels = {str(i): {"organisation_number": str(i), "exact": i != 5, "has_site": True} for i in range(1, 6)}
        card = build_scorecard(rows, REPORT, labels=labels, min_labels=5)
        self.assertEqual(card["labels"]["precision"], 0.8)
        self.assertEqual(card["labels"]["wrong_company"], 1)
        self.assertEqual(card["gate"]["status"], "FAIL")
        few = build_scorecard(rows, REPORT, labels=labels, min_labels=20)
        self.assertEqual(few["gate"]["status"], "INSUFFICIENT_LABELS")

    def test_trust_drop_fails_gate_and_diff_lists_flips(self) -> None:
        before = build_scorecard([verified_row("1", "a.no"), verified_row("2", "b.no")], REPORT)
        after = build_scorecard([verified_row("1", "a.no", signals={}), empty_row("2")], REPORT, previous=before)
        self.assertEqual(after["gate"]["status"], "FAIL")
        self.assertEqual(after["diff_vs_previous"], {"gained": [], "lost": ["2"], "changed_domain": []})
        self.assertEqual(diff_verdicts({"1": {"outcome": "verified", "domain": "n.no"}}, {"1": {"outcome": "verified", "domain": "o.no"}})["changed_domain"], ["1"])
        self.assertTrue(any(line.startswith("GATE FAIL") for line in summary_lines(after)))

    def test_provider_error_rate_surfaces(self) -> None:
        card = build_scorecard([empty_row("1")], REPORT)
        self.assertEqual(card["reliability"]["provider_error_rate"], 0.2)


class AnnotationScoreTests(unittest.TestCase):
    def test_truth_prediction_mapping_and_domain_normalisation(self) -> None:
        rows = [
            verified_row("1", "WWW.Example.NO"),
            verified_row("2", "wrong.no"),
            empty_row("3"),
            verified_row("4", "related.no"),
            empty_row("5"),
            verified_row("6", "hallucinated.no"),
            empty_row("7"),
            verified_row("8", "ignored.no"),
        ]
        labels = [
            annotation("1", "official_site", domain="example.no"),
            annotation("2", "official_site", domain="example.no"),
            annotation("3", "official_site", domain="missing.no"),
            annotation("4", "related_only", domain="related.no"),
            annotation("5", "related_only", domain="related.no"),
            annotation("6", "no_site_confirmed"),
            annotation("7", "no_site_confirmed"),
            annotation("8", "undetermined", domain="ignored.no"),
        ]
        result = score_annotations(rows, labels, min_published=1)
        self.assertEqual(result["counts"], {"tp": 1, "wrong_url": 2, "fn": 1, "related_as_official": 1, "tn": 2, "undetermined": 1})
        self.assertEqual(result["published_determined"], 4)
        self.assertEqual(result["published_precision"], 0.25)
        self.assertEqual(result["has_site_recall"], 0.5)
        self.assertEqual(result["abstention_precision"], 0.6667)
        self.assertEqual({item["error"] for item in result["errors"]}, {"wrong_url", "fn", "related_as_official"})
        self.assertEqual(result["undetermined_rate"], 0.125)

    def test_weighting_uses_selected_split_counts_not_sample_n(self) -> None:
        rows = [verified_row("a", "a.no"), empty_row("b"), verified_row("c", "c.no"), verified_row("d", "wrong.no")]
        labels = [
            annotation("a", "official_site", domain="a.no", stratum="S1", population_n=100),
            annotation("b", "official_site", domain="b.no", stratum="S1", population_n=100),
            annotation("c", "official_site", domain="c.no", stratum="S4", population_n=1000),
            annotation("d", "no_site_confirmed", stratum="S4", population_n=1000),
        ]
        result = score_annotations(rows, labels, min_published=1)
        self.assertEqual(result["published_precision"], 0.6667)
        self.assertEqual(result["weighted"]["stratum_weights"], {"S1": 50.0, "S4": 500.0})
        self.assertEqual(result["weighted"]["published_precision"], 0.5238)
        self.assertEqual(result["weighted"]["has_site_recall"], 0.9167)
        self.assertEqual(result["weighted"]["wrong_company_per_1000"], 454.5455)

    def test_guards_reject_negative_cache_and_missing_companies(self) -> None:
        labels = [annotation("1", "no_site_confirmed")]
        with self.assertRaisesRegex(ValueError, "negative-cache"):
            build_scorecard([cached_row("1")], REPORT, annotations=labels, min_published=1)
        with self.assertRaisesRegex(ValueError, "missing 1"):
            score_annotations([], labels, min_published=1)

    def test_timed_out_is_failure_not_correct_abstention(self) -> None:
        result = score_annotations([timed_out_row("1")], [annotation("1", "no_site_confirmed")], min_published=1)
        self.assertEqual(result["pipeline_failures"], 1)
        self.assertEqual(result["counts"]["timed_out"], 1)
        self.assertIsNone(result["abstention_precision"])
        self.assertEqual(result["gate"]["status"], "FAIL")

    def test_annotation_gate_states(self) -> None:
        row = verified_row("1", "good.no")
        official = [annotation("1", "official_site", domain="good.no")]
        self.assertEqual(score_annotations([row], official, min_published=2)["gate"]["status"], "INSUFFICIENT_PUBLISHED")
        bad_rows = [verified_row(str(i), f"wrong{i}.no") for i in range(40)]
        bad_labels = [annotation(str(i), "official_site", domain=f"right{i}.no") for i in range(40)]
        self.assertEqual(score_annotations(bad_rows, bad_labels, min_published=35)["gate"]["status"], "FAIL")
        good_rows = [verified_row(str(i), f"good{i}.no") for i in range(100)]
        good_labels = [annotation(str(i), "official_site", domain=f"good{i}.no") for i in range(100)]
        self.assertEqual(score_annotations(good_rows, good_labels, min_published=35)["gate"]["status"], "PASS")


class EvaluationLookTests(unittest.TestCase):
    def test_looks_append_and_validation_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.jsonl"
            profiles = root / "profiles.jsonl"
            looks = root / "looks.jsonl"
            manifest.write_text("manifest\n", encoding="utf-8")
            profiles.write_text("profiles\n", encoding="utf-8")
            card = {"annotations": {"published_precision": 1.0, "published_precision_wilson_lower": 0.9, "has_site_recall": 1.0}, "gate": {"status": "PASS"}}
            self.assertEqual(_append_evaluation_look(looks, split="held_out", manifest=manifest, profiles=profiles, card=card, forced=False), 1)
            self.assertEqual(_append_evaluation_look(looks, split="held_out", manifest=manifest, profiles=profiles, card=card, forced=False), 2)
            self.assertEqual(len(looks.read_text(encoding="utf-8").splitlines()), 2)
            self.assertEqual(_append_evaluation_look(looks, split="validation", manifest=manifest, profiles=profiles, card=card, forced=False), 1)
            with self.assertRaisesRegex(ValueError, "already been looked at"):
                _append_evaluation_look(looks, split="validation", manifest=manifest, profiles=profiles, card=card, forced=False)

    def test_validation_requires_final_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profiles = root / "profiles.jsonl"
            report = root / "report.json"
            annotations = root / "annotations.jsonl"
            output = root / "scorecard.json"
            profiles.write_text(json.dumps(empty_row("1")) + "\n", encoding="utf-8")
            report.write_text(json.dumps(REPORT), encoding="utf-8")
            annotations.write_text(json.dumps(annotation("1", "no_site_confirmed", split="validation")) + "\n", encoding="utf-8")
            argv = ["score_discovery_run.py", "--profiles", str(profiles), "--report", str(report), "--output", str(output), "--annotations", str(annotations), "--split", "validation"]
            with patch.object(sys, "argv", argv), self.assertRaises(SystemExit) as raised:
                scorecard_main()
            self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
