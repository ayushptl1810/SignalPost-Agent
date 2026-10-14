from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.evidence import evidence  # noqa: E402
from norway_company_agent.discovery.engine import _final_state  # noqa: E402
from norway_company_agent.registry.batch import terminal_envelope, validate_envelopes  # noqa: E402
from norway_company_agent.registry.contract import build_contract_sections  # noqa: E402
from scripts.run import run_competition_batch as batch  # noqa: E402
from scripts.analysis import contract_check  # noqa: E402
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


def test_envelope_audit_rejects_non_http_official_website_urls() -> None:
    envelope = _envelope()
    website = next(item for item in envelope["claims"] if item["field"] == "official_website")
    website["value"] = "ftp://musikkinnlandet.no/"

    report = audit([envelope])

    assert not report["passed"]
    assert any(error["kind"] == "official_website_invalid_url" for error in report["scored_value_errors"])


def _failed_discovery(*, candidate: dict | None = None, error: str | None = None) -> dict:
    discovery = {
        "states": {"official_website": "failed", "official_website_g4": "failed"},
        "candidates": [candidate] if candidate else [],
    }
    if error:
        discovery["error"] = error
    return discovery


def _candidate(source: str = "name_derived_no", **extra: object) -> dict:
    return {
        "source": source,
        "requested_url": "https://musikkinnlandet.no/",
        "website_status": "failed",
        "state": "failed",
        **extra,
    }


def test_failed_name_only_discovery_is_emitted_as_not_available() -> None:
    profile = {"organisation_number": "926790137", "website": ""}
    fixtures = [
        _failed_discovery(),
        _failed_discovery(candidate=_candidate(failure_kind="resolution", failure_class="permanent")),
        _failed_discovery(candidate=_candidate(failure_kind="timeout", homepage_timed_out=True)),
        _failed_discovery(candidate=_candidate(website_status="not_found", status_code=404)),
        _failed_discovery(candidate=_candidate(first_party={"status": "insufficient_evidence"})),
    ]

    for discovery in fixtures:
        website, summary = batch._discovery_evidence(profile, discovery, gate="g4")
        assert website["status"] == "not_found"
        assert summary["state"] == "not_available"
        assert "failed candidate demoted to not_available" in (website["note"] or "") or not discovery["candidates"]


def test_strong_source_failure_and_module_exception_remain_failed() -> None:
    profile = {"organisation_number": "926790137", "website": ""}
    strong = _failed_discovery(candidate=_candidate("registry_website", failure_kind="timeout", failure_class="transient"))
    website, summary = batch._discovery_evidence(profile, strong, gate="g4")
    assert website["status"] == "failed"
    assert summary["state"] == "failed"

    exception = _failed_discovery(error="RuntimeError")
    website, summary = batch._discovery_evidence(profile, exception, gate="g4")
    assert website["status"] == "failed"
    assert summary["state"] == "failed"


def test_final_state_keeps_strong_failures_and_demotes_name_failures() -> None:
    assert _final_state([_candidate(failure_kind="connect")], published=False) == "not_available"
    assert _final_state([_candidate("registry_website", failure_kind="connect", failure_class="transient")], published=False) == "failed"


def test_failed_to_not_available_reasons_are_aggregated_for_the_run_report() -> None:
    target = {"failed_to_not_available_by_reason": batch.Counter()}
    batch._merge_discovery_metrics(target, {"metrics": {"failed_to_not_available_by_reason": {"nxdomain": 2, "timeout": 1}}})

    assert dict(target["failed_to_not_available_by_reason"]) == {"nxdomain": 2, "timeout": 1}


def test_tiny_budget_emits_twenty_registry_complete_and_external_not_checked_envelopes() -> None:
    organisations = [str(900000000 + index) for index in range(1, 21)]
    with TemporaryDirectory() as directory:
        root = Path(directory)
        input_path = root / "organisations.jsonl"
        input_path.write_text("".join(json.dumps({"organisation_number": org}) + "\n" for org in organisations), encoding="utf-8")
        bulk_path = root / "snapshot.csv"
        bulk_path.write_text(
            "organisasjonsnummer,navn,organisasjonsform.kode\n"
            + "".join(f"{org},Budget Company {index} AS,AS\n" for index, org in enumerate(organisations, 1)),
            encoding="utf-8",
        )
        output_path = root / "envelopes.jsonl"
        profiles_path = root / "profiles.jsonl"
        report_path = root / "report.json"
        argv = [
            "run_competition_batch.py",
            "--organisations", str(input_path),
            "--bulk", str(bulk_path),
            "--output", str(output_path),
            "--profiles-output", str(profiles_path),
            "--report", str(report_path),
            "--modules", "registry,website",
            "--discovery", "g4",
            "--workers", "1",
            "--discovery-processes", "1",
            "--checkpoint-every", "20",
            "--second-chance-cap-seconds", "0.1",
        ]

        def slow_budgeted_discovery(*_args: object, **_kwargs: object) -> tuple[dict, dict]:
            time.sleep(4.8)
            return {
                "states": {"official_website": "not_checked", "official_website_g4": "not_checked"},
                "candidates": [],
                "budget_exhausted": True,
            }, {"timed_out": False, "budget_exhausted": True, "elapsed_seconds": 4.8}

        with patch.object(sys, "argv", argv), patch.dict(os.environ, {"SIGNALPOST_RUN_BUDGET_SECONDS": "5"}), patch.object(batch, "network_preflight", return_value={"passed": True}), patch.object(batch, "fetch_wikidata_candidates", return_value=({}, {"chunks": 0, "candidates": 0})), patch.object(batch, "_run_discovery_bounded", side_effect=slow_budgeted_discovery):
            try:
                batch.main()
            except SystemExit as exc:
                assert exc.code == 0

        envelopes = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(envelopes) == 20
        assert batch.validate_envelopes(envelopes, 20)["passed"]
        for envelope in envelopes:
            assert envelope["run"]["terminal_status"] == "completed"
            registry_claim = next(item for item in envelope["claims"] if item["field"] == "legal_identity")
            website_claim = next(item for item in envelope["claims"] if item["field"] == "official_website")
            assert registry_claim["availability"] == "available"
            assert website_claim["availability"] == "not_checked"
            assert envelope["profile"]["evidence"]["website"]["status"] == "not_fetched"


def test_contract_check_passes_fresh_fixture_and_rejects_old_object_shaped_submission() -> None:
    fresh = contract_check.check_contract([_envelope()], 1)
    assert fresh["passed"], fresh
    assert fresh["scored_value_type_counts"]["official_website"]["available|string"] == 1

    old_path = ROOT / "submission" / "smoke-100" / "envelopes.jsonl"
    old_rows, load_errors = contract_check._load_jsonl(old_path)
    assert not load_errors
    old = contract_check.check_contract(old_rows, 100)
    assert not old["passed"]
    assert old["scored_value_type_counts"]["official_website"]["available|object"] == 8
    assert any(error.get("kind") == "scored_value_not_string" and error.get("field") == "official_website" for error in old["violations"])


def test_contract_check_catches_missing_keys_unknown_availability_and_dangling_refs() -> None:
    envelope = _envelope()
    del envelope["run"]["terminal_status"]
    claim = envelope["claims"][0]
    claim["availability"] = "unknown"
    claim["evidence_ids"] = ["missing-evidence"]
    envelope["claims"].append({"field": "company_profiles", "value": "", "availability": "available", "confidence": 1.0, "evidence_ids": ["missing-evidence"]})
    report = contract_check.check_contract([envelope], 1)
    kinds = {error.get("kind") for error in report["violations"]}
    assert {"missing_required_key", "unknown_availability", "available_dangling_evidence_id"}.issubset(kinds)
