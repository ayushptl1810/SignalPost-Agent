from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.evidence import evidence  # noqa: E402
from norway_company_agent.registry.batch import terminal_envelope, validate_envelopes  # noqa: E402
from norway_company_agent.registry.contract import build_contract_sections  # noqa: E402
from scripts.analysis.envelope_audit import audit  # noqa: E402


def _profile(*, website_status: str = "available", url: str = "https://musikkinnlandet.no/") -> tuple[dict, dict]:
    website_value = {
        "requested_url": url,
        "final_url": url,
        "registered_domain": "musikkinnlandet.no",
        "title": "Musikk i Innlandet",
        "description": "Musikk i Innlandet",
        "main_text_excerpt": "Musikk i Innlandet Org.nr 926 790 137",
        "identity_text_excerpt": "Musikk i Innlandet",
        "social_links": [{"platform": "linkedin", "url": "https://linkedin.com/company/musikk-i-innlandet"}],
        "pages": [{"url": url, "claim_span": "Musikk i Innlandet"}],
        "content_sha256": "a" * 64,
    }
    return (
        {
            "organisation_number": "926790137",
            "name": "Musikk i Innlandet",
            "evidence": {
                "registry": evidence("registry", "available", "official_registry", "https://registry.test/926790137", value={"name": "Musikk i Innlandet"}),
                "website": evidence("website", website_status, "company_owned", url, value=website_value, content_sha256="a" * 64, retrieved_at="2026-10-14T00:00:00Z"),
            },
        },
        website_value,
    )


def test_available_official_website_is_a_url_and_preserves_full_details() -> None:
    profile, old_value = _profile()

    contract = build_contract_sections(
        profile,
        run_id="s34-test",
        started_at="2026-10-14T00:00:00Z",
        completed_at="2026-10-14T00:01:00Z",
    )
    claim = next(item for item in contract["claims"] if item["field"] == "official_website")
    evidence_row = next(item for item in contract["evidence"] if item["id"] == claim["evidence_ids"][0])

    assert claim["availability"] == "available"
    assert claim["value"] == "https://musikkinnlandet.no/"
    assert claim["value"] == evidence_row["source_url"]
    assert claim["value_details"] == old_value
    assert claim["value_details"]["social_links"] == old_value["social_links"]


def test_non_available_official_website_keeps_the_existing_small_value() -> None:
    profile, _old_value = _profile(website_status="failed")
    contract = build_contract_sections(
        profile,
        run_id="s34-test",
        started_at="2026-10-14T00:00:00Z",
        completed_at="2026-10-14T00:01:00Z",
    )
    claim = next(item for item in contract["claims"] if item["field"] == "official_website")

    assert claim["availability"] == "failed"
    assert isinstance(claim["value"], dict)
    assert "value_details" not in claim


def _envelope() -> dict:
    profile, _old_value = _profile()
    return terminal_envelope(
        profile,
        run_id="s34-test",
        modules=[],
        started_at="2026-10-14T00:00:00Z",
        completed_at="2026-10-14T00:01:00Z",
    )


def test_validator_rejects_wrong_scored_value_types_and_wrong_website_domain() -> None:
    for field in ("official_website", "social_profile", "hiring_signal", "dated_news"):
        envelope = _envelope()
        evidence_id = envelope["claims"][0]["evidence_ids"][0]
        envelope["claims"].append({"field": field, "value": {"old": True}, "availability": "available", "confidence": 1.0, "evidence_ids": [evidence_id]})
        result = validate_envelopes([envelope], 1)
        assert not result["passed"]
        assert any(error["kind"] == "scored_value_not_string" and error["field"] == field for error in result["contract_errors"])

    envelope = _envelope()
    website = next(item for item in envelope["claims"] if item["field"] == "official_website")
    website["value"] = "https://other.example/"
    result = validate_envelopes([envelope], 1)
    assert not result["passed"]
    assert any(error["kind"] == "official_website_domain_mismatch" for error in result["contract_errors"])


def test_envelope_audit_rejects_wrong_scored_value_types() -> None:
    envelope = _envelope()
    website = next(item for item in envelope["claims"] if item["field"] == "official_website")
    website["value"] = {"final_url": "https://musikkinnlandet.no/"}

    report = audit([envelope])

    assert not report["passed"]
    assert any(error["kind"] == "scored_value_not_string" for error in report["scored_value_errors"])
