from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.core.evidence import evidence  # noqa: E402
from norway_company_agent.registry.batch import (  # noqa: E402
    STARTER_EVIDENCE_KEYS,
    STARTER_ENVELOPE_KEYS,
    STARTER_MODULE_KEYS,
    STARTER_MODULES,
    STARTER_PROFILE_KEYS,
    STARTER_WEBSITE_PAGE_KEYS,
    STARTER_WEBSITE_VALUE_KEYS,
    terminal_envelope,
    to_starter_envelope,
    validate_envelopes,
)
from scripts.analysis.starter_shape_check import check_rows  # noqa: E402
from scripts.analysis import contract_check  # noqa: E402
from scripts.run import run_competition_batch as batch  # noqa: E402
from api_server.envelopes import adapt_envelope  # noqa: E402


def _profile() -> dict:
    website_url = "https://musikkinnlandet.no/"
    website_value = {
        "requested_url": website_url,
        "final_url": website_url,
        "registered_domain": "musikkinnlandet.no",
        "title": "Musikk i Innlandet",
        "description": "A music organisation",
        "main_text_excerpt": "Musikk i Innlandet Org.nr 926 790 137",
        "identity_text_excerpt": "Musikk i Innlandet",
        "social_links": [{"platform": "linkedin", "url": "https://linkedin.example/musikk"}],
        "structured_organisations": [{"name": "Musikk i Innlandet"}],
        "content_sha256": "b" * 64,
        "extraction_state": "complete",
        "pages": [{
            "url": website_url,
            "title": "Musikk i Innlandet",
            "main_text_excerpt": "Musikk i Innlandet",
            "identity_text_excerpt": "Musikk i Innlandet",
            "content_sha256": "c" * 64,
            "claim_span": "must be removed",
        }],
        "crawl_errors": [],
        "identity_assessment": {"publishable": True},
        "discovered_social_links": [{"platform": "linkedin", "url": "https://linkedin.example/musikk"}],
        "social_link_assessments": [{"url": "https://linkedin.example/musikk", "status": "accepted"}],
        "json_ld": [{"@type": "Organisation"}],
        "visible_text": "must be removed",
    }
    profile = {
        "organisation_number": "926790137",
        "name": "Musikk i Innlandet",
        "legal_form": "AS",
        "employees": 12,
        "bankrupt": False,
        "liquidating": False,
        "municipality": "Hamar",
        "municipality_number": "3403",
        "industry_code": "90.011",
        "industry_label": "Performing arts",
        "website": website_url,
        "latest_submitted_accounts": {"year": 2025},
        "run_metrics": {"requests": 2, "bytes": 1234},
        "raw": {"extra": True},
        "claims": {"extra": True},
        "errors": [{"kind": "extra"}],
        "evidence": {},
    }
    for module in STARTER_MODULES:
        profile["evidence"][module] = evidence(
            module,
            "available",
            "company_owned" if module == "website" else "official_registry",
            website_url if module == "website" else "https://registry.example/926790137",
            value=website_value if module == "website" else {"module": module},
            retrieved_at="2026-10-14T00:00:00Z",
            content_sha256="a" * 64,
            source_row_key="926790137",
            effective_at="2026-01-01",
        )
    profile["evidence"]["website_g4"] = evidence(
        "website_g4", "available", "company_owned", website_url, value={"extra": True}, retrieved_at="2026-10-14T00:00:00Z"
    )
    return profile


def _full_envelope() -> dict:
    return terminal_envelope(
        _profile(),
        run_id="s35-test",
        modules=STARTER_MODULES,
        started_at="2026-10-14T00:00:00Z",
        completed_at="2026-10-14T00:01:00Z",
    )


def test_starter_projection_is_exact_and_does_not_mutate_rich_envelope() -> None:
    rich = _full_envelope()
    before = deepcopy(rich)

    starter = to_starter_envelope(rich)

    assert set(starter) == set(STARTER_ENVELOPE_KEYS)
    assert set(starter["modules"]) == set(STARTER_MODULES)
    assert all(set(value) == set(STARTER_MODULE_KEYS) for value in starter["modules"].values())
    assert set(starter["profile"]) == set(STARTER_PROFILE_KEYS)
    assert set(starter["profile"]["evidence"]) == set(STARTER_MODULES)
    assert all(set(value) == set(STARTER_EVIDENCE_KEYS) for value in starter["profile"]["evidence"].values())

    website = starter["profile"]["evidence"]["website"]["value"]
    assert set(website) == set(STARTER_WEBSITE_VALUE_KEYS)
    assert all(set(page) == set(STARTER_WEBSITE_PAGE_KEYS) for page in website["pages"])
    assert "claim_span" not in website["pages"][0]
    assert "website_g4" not in starter["profile"]["evidence"]
    assert rich == before


def test_starter_projection_is_accepted_by_the_envelope_validator() -> None:
    starter = to_starter_envelope(_full_envelope())

    result = validate_envelopes([starter], 1)

    assert result["passed"], result
    assert result["contract_errors"] == []


def test_shape_checker_uses_the_committed_key_set_fixture() -> None:
    starter = to_starter_envelope(_full_envelope())
    fixture = json.loads((ROOT / "tests/fixtures/starter_shape.json").read_text(encoding="utf-8"))

    assert check_rows([starter], fixture) == []

    extra = deepcopy(starter)
    extra["unexpected"] = True
    assert check_rows([extra], fixture)


def test_output_selector_defaults_to_starter_and_writes_a_full_sidecar(tmp_path: Path) -> None:
    rich = _full_envelope()
    output = tmp_path / "envelopes.jsonl"

    sidecar = batch.write_envelope_outputs(output, [rich], batch.resolve_envelope_shape(None, {}))

    assert sidecar == tmp_path / "envelopes.full.jsonl"
    primary = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    full = json.loads(sidecar.read_text(encoding="utf-8").splitlines()[0])
    assert set(primary) == set(STARTER_ENVELOPE_KEYS)
    assert "claims" in full and "evidence" in full

    full_output = tmp_path / "full.jsonl"
    assert batch.write_envelope_outputs(full_output, [rich], "full") is None
    assert "claims" in json.loads(full_output.read_text(encoding="utf-8").splitlines()[0])


def test_output_shape_flag_and_environment_override() -> None:
    assert batch.resolve_envelope_shape(None, {}) == "starter"
    assert batch.resolve_envelope_shape("full", {}) == "full"
    assert batch.resolve_envelope_shape(None, {"SIGNALPOST_ENVELOPE_SHAPE": "full"}) == "full"


def test_api_adapter_accepts_starter_rows_without_mutating_them() -> None:
    starter = to_starter_envelope(_full_envelope())
    before = deepcopy(starter)

    adapted = adapt_envelope(starter)

    assert {"run", "claims", "evidence", "operations", "unknown_areas"}.issubset(adapted)
    assert any(claim["field"] == "legal_identity" for claim in adapted["claims"])
    assert starter == before


def test_full_contract_checker_is_not_applied_to_starter_rows() -> None:
    starter = to_starter_envelope(_full_envelope())

    report = contract_check.check_contract([starter], 1)

    assert not report["passed"]
    assert any(item["kind"] == "shape_not_full" for item in report["violations"])
