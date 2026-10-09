from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.analysis.local_scorecard import _promote, score  # noqa: E402


def envelope(org: str, claims: list[dict]) -> dict:
    evidence = []
    for index, claim in enumerate(claims):
        evidence_id = f"ev-{org}-{index}"
        claim["evidence_ids"] = [evidence_id]
        evidence.append({"id": evidence_id, "source_url": f"https://example.no/{org}/{index}", "retrieved_at": "2026-10-01T00:00:00Z", "content_sha256": "a" * 64, "claim_span": "source span"})
    return {"organisation_number": org, "claims": claims, "evidence": evidence, "changes": [], "unknown_areas": []}


class LocalScorecardTests(unittest.TestCase):
    def test_sparse_pool_is_not_measured(self) -> None:
        pool = [envelope("1", [{"field": "hiring", "availability": "available", "value": [{"title": "A"}]}])]
        candidate = [envelope("1", [{"field": "hiring", "availability": "available", "value": [{"title": "A"}]}])]
        result = score(candidate, pool)
        self.assertEqual(result["recall"]["status"], "not_measured")
        self.assertIsNone(result["recall"]["average_recall"])

    def test_wrong_company_label_blocks_promotion(self) -> None:
        result = {
            "precision_evidence": {"wrong_company_publications": 1, "evidence_completeness": 1.0, "ratio": 1.0},
            "recall": {"types": {}},
            "runtime": {"wall_time_seconds": 1},
        }
        decision = _promote(result, result)
        self.assertEqual(decision["decision"], "DO NOT PROMOTE")
        self.assertFalse(decision["reasons"][0]["passed"])

    def test_recall_uses_company_and_claim_components(self) -> None:
        pool = []
        candidate = []
        for number in range(1, 16):
            claims = [
                {"field": "hiring", "availability": "available", "value": [{"title": f"Job {number}"}]},
                {"field": "leadership", "availability": "available", "value": [{"name": f"Person {number}"}]},
                {"field": "public_activity", "availability": "available", "value": {"items": [{"title": f"News {number}", "date": "2026-01-01", "url": f"https://example.no/news/{number}"}]}},
            ]
            pool.append(envelope(str(number), claims))
            candidate.append(envelope(str(number), claims[:2]))
        result = score(candidate, pool)
        self.assertEqual(result["recall"]["status"], "measured")
        self.assertEqual(result["recall"]["types"]["hiring"]["recall"], 1.0)
        self.assertEqual(result["recall"]["types"]["dated_public_activity"]["company_recall"], 0.0)


if __name__ == "__main__":
    unittest.main()
