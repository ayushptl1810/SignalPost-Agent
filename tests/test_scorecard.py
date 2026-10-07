from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.analysis.score_discovery_run import (  # noqa: E402
    build_scorecard,
    classify_row,
    diff_verdicts,
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


if __name__ == "__main__":
    unittest.main()
