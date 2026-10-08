from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from ..core.evidence import evidence, utc_now
from .contract import ALLOWED_AVAILABILITY, build_contract_sections
from .official import accounting_obligation_assessment
from .sampling import iter_bulk


TERMINAL_STATES = {
    "complete",
    "not_applicable",
    "not_found",
    "blocked_policy",
    "blocked_robots",
    "source_error",
    "budget_exhausted",
    "submission_error",
}


def read_organisation_inputs(path: str | Path, *, tolerant: bool = False) -> list[dict[str, Any]]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    values: list[Any]
    if source.suffix == ".json":
        try:
            body = json.loads(text)
            if isinstance(body, list):
                values = body
            elif isinstance(body, dict):
                values = body.get("organisation_numbers", [])
            else:
                raise TypeError("JSON organisation input must be a list or object")
        except (json.JSONDecodeError, TypeError) as exc:
            if not tolerant:
                raise
            values = [{"_input_error": {"kind": "malformed_json", "detail": str(exc)}}]
    elif source.suffix == ".jsonl":
        values = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                if tolerant:
                    values.append({"_input_error": {"kind": "empty_line", "line": line_number}})
                continue
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError as exc:
                if not tolerant:
                    raise
                values.append({"_input_error": {"kind": "malformed_json", "line": line_number, "detail": str(exc)}})
    else:
        values = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if line.strip() or tolerant:
                values.append({"value": line.strip(), "_line": line_number} if tolerant else line.strip())
    records = []
    seen: set[str] = set()
    for value in values:
        metadata = value if isinstance(value, dict) else {}
        if metadata.get("_input_error"):
            if not tolerant:
                raise ValueError(str(metadata["_input_error"]))
            records.append({"organisation_number": "", "input_error": metadata["_input_error"]})
            continue
        raw_org = value.get("organisation_number") if isinstance(value, dict) and "value" not in value else (value.get("value") if isinstance(value, dict) else value)
        raw_org = str(raw_org or "").strip()
        normalized_org = "".join(character for character in raw_org if character.isdigit())
        formatting_only = bool(raw_org) and all(character.isdigit() or character.isspace() or character in {"-", "."} for character in raw_org)
        org = normalized_org if formatting_only and len(normalized_org) == 9 else ""
        if not org:
            if not tolerant:
                raise ValueError(f"Invalid Norwegian organisation number: {value!r}")
            records.append({"organisation_number": "", "input_error": {"kind": "invalid_organisation_number", "value": raw_org}})
            continue
        record = {"organisation_number": org}
        if org in seen:
            if not tolerant:
                raise ValueError(f"Duplicate organisation number: {org}")
            record["input_error"] = {"kind": "duplicate_input", "organisation_number": org}
        seen.add(org)
        if isinstance(value, dict):
            for key in ("evaluation_split", "sample_slice"):
                if value.get(key) is not None:
                    record[key] = value[key]
        records.append(record)
    orgs = [record["organisation_number"] for record in records if record.get("organisation_number") and not record.get("input_error")]
    if not tolerant and len(orgs) != len(set(orgs)):
        raise ValueError("Organisation-number input contains duplicates")
    return records


def read_organisation_numbers(path: str | Path) -> list[str]:
    return [record["organisation_number"] for record in read_organisation_inputs(path)]


def profiles_from_bulk(path: str | Path, organisation_numbers: Iterable[str], *, allow_missing: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requested = list(organisation_numbers)
    wanted = set(requested)
    snapshot_sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    retrieved_at = utc_now()
    found: dict[str, dict[str, Any]] = {}
    scanned = 0
    for profile in iter_bulk(path):
        scanned += 1
        org = profile["organisation_number"]
        if org not in wanted:
            continue
        raw = profile.get("raw", {})
        profile["raw"] = raw
        profile["evidence"] = {
            "registry": evidence(
                "registry",
                "available",
                "official_registry_bulk",
                "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv",
                value=raw,
                retrieved_at=retrieved_at,
                content_sha256=snapshot_sha256,
                source_row_key=org,
            ),
            "accounting_obligation": accounting_obligation_assessment(profile),
        }
        found[org] = profile
        if len(found) == len(wanted):
            break
    missing = [org for org in requested if org not in found]
    if missing and not allow_missing:
        raise ValueError(f"Organisation numbers absent from registry snapshot: {missing[:10]}")
    for org in missing:
        found[org] = {
            "organisation_number": org,
            "name": None,
            "raw": {},
            "_missing_registry": True,
            "evidence": {
                "registry": evidence("registry", "not_found", "official_registry_bulk", "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv", note="absent from registry snapshot", source_row_key=org),
                "accounting_obligation": evidence("accounting_obligation", "not_found", "official_registry_bulk", "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv", note="absent from registry snapshot", source_row_key=org),
            },
        }
    return [found[org] for org in requested], {
        "registry_snapshot_sha256": snapshot_sha256,
        "registry_rows_scanned": scanned,
        "requested": len(requested),
        "selected": len(found),
    }


def evidence_terminal_state(record: dict[str, Any] | None) -> str:
    if not record:
        return "submission_error"
    status = record.get("status")
    if status == "available":
        return "complete"
    if status == "not_applicable":
        return "not_applicable"
    if status == "not_found":
        return "not_found"
    if status == "blocked":
        note = str(record.get("note") or "").casefold()
        return "blocked_robots" if "robot" in note else "blocked_policy"
    if status in {"source_error", "failed"}:
        # A dead or unresolvable source (for example a registry-listed site whose host no
        # longer resolves) is a source problem, not a failed submission for the company.
        return "source_error"
    return "submission_error"


def terminal_envelope(
    profile: dict[str, Any],
    *,
    run_id: str,
    modules: Iterable[str],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    module_states = {}
    for module in modules:
        record = profile.get("evidence", {}).get(module)
        module_states[module] = {
            "state": evidence_terminal_state(record),
            "retry_count": int((record or {}).get("retry_count") or 0),
            "final_timestamp": (record or {}).get("retrieved_at") or completed_at,
        }
    if profile.get("input_error"):
        entity_state = "submission_error"
    elif profile.get("_missing_registry"):
        entity_state = "source_error"
    else:
        entity_state = "submission_error" if any(item["state"] == "submission_error" for item in module_states.values()) else "complete"
    contract = build_contract_sections(profile, run_id=run_id, started_at=started_at, completed_at=completed_at)
    return {
        "run_id": run_id,
        "organisation_number": profile["organisation_number"],
        "state": entity_state,
        "started_at": started_at,
        "completed_at": completed_at,
        "modules": module_states,
        "profile": profile,
        **contract,
    }


def validate_envelopes(envelopes: list[dict[str, Any]], expected_count: int) -> dict[str, Any]:
    orgs = [item.get("organisation_number") for item in envelopes]
    counted_orgs = [item.get("organisation_number") for item in envelopes if not (item.get("profile") or {}).get("input_error")]
    invalid_states = [
        {"organisation_number": item.get("organisation_number"), "state": state.get("state")}
        for item in envelopes
        for state in item.get("modules", {}).values()
        if state.get("state") not in TERMINAL_STATES
    ]
    contract_errors: list[dict[str, Any]] = []
    for envelope in envelopes:
        evidence_rows = envelope.get("evidence") or []
        evidence_by_id = {row.get("id"): row for row in evidence_rows if isinstance(row, dict)}
        if not {"run", "claims", "evidence", "errors", "operations"}.issubset(envelope):
            contract_errors.append({"organisation_number": envelope.get("organisation_number"), "kind": "missing_contract_blocks"})
            continue
        for claim in envelope.get("claims") or []:
            refs = claim.get("evidence_ids") or []
            if claim.get("availability") not in ALLOWED_AVAILABILITY:
                contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": claim.get("field"), "kind": "invalid_availability"})
            if any(ref not in evidence_by_id for ref in refs):
                contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": claim.get("field"), "kind": "unknown_evidence_id"})
            if claim.get("availability") == "available" and not refs:
                contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": claim.get("field"), "kind": "available_without_evidence"})
        for row in evidence_rows:
            missing_fields = [key for key in ("id", "source_url", "source_class", "retrieved_at", "content_sha256", "claim_span") if not row.get(key)]
            if missing_fields:
                contract_errors.append({"organisation_number": envelope.get("organisation_number"), "kind": "incomplete_evidence", "fields": missing_fields})
    checks = {
        "exact_expected_count": len(envelopes) == expected_count,
        "unique_organisation_numbers": len(counted_orgs) == len(set(counted_orgs)),
        "all_entity_states_terminal": all(item.get("state") in TERMINAL_STATES for item in envelopes),
        "all_module_states_terminal": not invalid_states,
        "zero_silent_drops": len(envelopes) == expected_count and len(counted_orgs) == len(set(counted_orgs)),
        "contract_blocks_valid": not contract_errors,
    }
    return {"passed": all(checks.values()), "checks": checks, "invalid_states": invalid_states, "contract_errors": contract_errors}


def profile_complete_for_modules(profile: dict[str, Any], modules: Iterable[str]) -> bool:
    records = profile.get("evidence", {})
    return all(module in records and records[module].get("status") != "not_fetched" for module in modules)
