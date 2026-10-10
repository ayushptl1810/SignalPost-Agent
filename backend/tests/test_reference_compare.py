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


def test_compare_rows_reports_all_four_families_and_source_yield() -> None:
    reference = [
        {
            "org": "1",
            "web": {"status": "available", "value": {"final_url": "https://www.alpha.no/"}},
            "external": {
                "handles": [{"url": "https://www.linkedin.com/company/alpha/"}],
                "company_site": {
                    "jobs": [{"url": "https://alpha.no/karriere", "title": "Jobb"}],
                    "posts": [{"source": "https://alpha.no/nyheter/a", "text": "Alpha news", "date_published": "2025-09-22T20:00:00+02:00"}],
                },
            },
        },
        {"org": "2", "web": {"status": "available", "value": {"final_url": "https://beta.no/"}}, "external": {"handles": []}},
        {"org": "3", "web": {"status": "available", "value": {"final_url": "https://gamma.no/"}}, "external": {"handles": []}},
    ]
    profiles = [
        {
            "organisation_number": "1",
            "evidence": {
                "website": {"status": "available", "value": {"final_url": "https://alpha.no/", "social_links": [{"url": "https://linkedin.com/company/alpha"}]}},
                "hiring": {"status": "available", "value": {"careers_page_url": "https://alpha.no/karriere"}},
                "public_activity": {"status": "available", "value": {"items": [{"title": "Alpha news", "published_at": "2025-09-22T20:00:00+02:00", "url": "https://alpha.no/nyheter/a"}]}},
            },
            "discovery": {"source_yield": {"registry_website": 1}},
        },
        {
            "organisation_number": "2",
            "claims": {"official_website": {"final_url": "https://beta.no/"}},
            "evidence": {"website": {"status": "available", "value": {"final_url": "https://beta.no/", "social_links": [{"url": "https://x.com/beta"}]}}, "hiring": {"status": "available", "value": {"postings": [{"url": "https://beta.no/jobs/one", "title": "Engineer"}]}}, "public_activity": {"status": "available", "value": {"items": [{"title": "Beta old", "published_at": "2023-01-01", "url": "https://beta.no/news/old"}]}}},
            "discovery": {"source_yield": {"name_derived_no": 1}},
        },
        {
            "organisation_number": "3",
            "evidence": {"website": {"status": "not_available", "value": {}}},
            "discovery": {"source_yield": {"registry_email_domain:g4": 1}},
        },
    ]

    result = compare_rows(reference, profiles)

    assert set(result["families"]) == {"website", "social_profile", "hiring_signal", "dated_news"}
    assert result["families"]["website"] == {
        "reference_companies": 3,
        "ours_companies": 2,
        "matched_companies": 2,
        "reference_only": ["3"],
        "ours_only": [],
        "facts_matched": 2,
        "proxy": 0.6667,
    }
    assert result["families"]["social_profile"]["reference_companies"] == 1
    assert result["families"]["social_profile"]["ours_companies"] == 2
    assert result["families"]["social_profile"]["matched_companies"] == 1
    assert result["families"]["hiring_signal"]["facts_matched"] == 1
    assert result["families"]["dated_news"]["facts_matched"] == 1
    assert result["source_yield"] == {"name_derived_no": 1, "registry_email_domain": 1, "registry_website": 1}


def test_compare_mode_reports_gains_and_losses() -> None:
    before = [{"organisation_number": "1", "evidence": {"website": {"status": "available", "value": {"final_url": "https://alpha.no/"}}}}]
    after = [{"organisation_number": "2", "evidence": {"website": {"status": "available", "value": {"final_url": "https://beta.no/"}}}}]

    result = compare_rows(before, after, compare_before=before, compare_after=after)

    assert result["compare"] == {"website": {"gained_companies": ["2"], "lost_companies": ["1"]}, "social_profile": {"gained_companies": [], "lost_companies": []}, "hiring_signal": {"gained_companies": [], "lost_companies": []}, "dated_news": {"gained_companies": [], "lost_companies": []}}
