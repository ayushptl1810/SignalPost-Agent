from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.analysis.reference_compare import compare_rows  # noqa: E402


def test_compare_rows_reports_registered_domain_and_social_misses() -> None:
    reference = [
        {
            "org": "1",
            "web": {"status": "available", "value": {"final_url": "https://www.alpha.no/"}},
            "external": {"handles": [{"url": "https://Facebook.com/alpha"}]},
        },
        {
            "org": "2",
            "web": {"status": "available", "value": {"final_url": "https://beta.no/"}},
            "external": {"handles": [{"url": "https://instagram.com/beta"}]},
        },
        {
            "org": "3",
            "web": {"status": "available", "value": {"final_url": "https://gamma.no/"}},
            "external": {"handles": []},
        },
    ]
    profiles = [
        {
            "organisation_number": "1",
            "website": {"evidence": {"value": {"final_url": "https://alpha.no/path"}}},
            "discovery": {"state": "available", "candidates": []},
        },
        {
            "organisation_number": "2",
            "evidence": {"website": {"value": {"final_url": "https://other.no/", "social_links": [{"url": "https://instagram.com/beta"}]}}},
            "discovery": {
                "state": "ambiguous",
                "candidates": [{"source": "name_derived_no", "state": "ambiguous", "failure_kind": "resolution", "first_party": {"status": "insufficient_evidence"}}],
            },
        },
        {
            "organisation_number": "3",
            "discovery": {"state": "not_available", "candidates": []},
        },
    ]

    result = compare_rows(reference, profiles)

    assert result["websites"] == {"reference": 3, "ours": 2, "matched": 1, "reference_only": 2, "ours_only": 1}
    assert result["social"] == {"reference": 2, "ours": 1, "matched": 1, "reference_only": 1, "ours_only": 0}
    assert result["website_misses"] == [
        {"organisation_number": "2", "reference_domain": "beta.no", "ours_domain": "other.no", "discovery_state": "ambiguous", "candidates": [{"source": "name_derived_no", "state": "ambiguous", "failure_kind": "resolution", "first_party_status": "insufficient_evidence"}]},
        {"organisation_number": "3", "reference_domain": "gamma.no", "ours_domain": None, "discovery_state": "not_available", "candidates": []},
    ]
