from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..core.evidence import evidence, utc_now
from .contract import ALLOWED_AVAILABILITY, build_contract_sections
from .official import accounting_obligation_assessment
from .sampling import iter_bulk
from ..site_facts import parse_date, parse_datetime


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


def _value_has_reporting_period(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("period") or value.get("reporting_period"):
            return True
        records = value.get("records")
        if isinstance(records, list):
            return any(isinstance(item, dict) and (item.get("period") or item.get("reporting_period")) for item in records)
    if isinstance(value, list):
        return any(_value_has_reporting_period(item) for item in value)
    return False


def _comparable_datetime(value: Any) -> datetime | None:
    parsed = parse_datetime(value)
    if not parsed:
        return None
    try:
        if len(parsed) == 10:
            return datetime.fromisoformat(parsed).replace(tzinfo=timezone.utc)
        result = datetime.fromisoformat(parsed)
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except ValueError:
        return None


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
    try:
        contract = build_contract_sections(profile, run_id=run_id, started_at=started_at, completed_at=completed_at)
    except Exception as exc:  # noqa: BLE001 - one odd profile must never lose the batch
        contract = {
            "run": {"run_id": run_id, "started_at": started_at, "completed_at": completed_at, "terminal_status": "source_error"},
            "claims": [],
            "evidence": [],
            "changes": [],
            "errors": [{"module": "contract", "kind": type(exc).__name__, "detail": str(exc)[:300]}],
            "operations": {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0.0},
        }
        entity_state = "source_error"
    contract["unknown_areas"] = [
        str(item.get("field"))
        for item in contract.get("claims") or []
        if item.get("availability") not in {"available", "not_applicable"}
    ]
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
            field = claim.get("field")
            refs = claim.get("evidence_ids") or []
            if claim.get("availability") not in ALLOWED_AVAILABILITY:
                contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": claim.get("field"), "kind": "invalid_availability"})
            if any(ref not in evidence_by_id for ref in refs):
                contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": claim.get("field"), "kind": "unknown_evidence_id"})
            if claim.get("availability") == "available" and not refs:
                contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": claim.get("field"), "kind": "available_without_evidence"})
            if claim.get("availability") == "available" and claim.get("field") in {"latest_annual_accounts", "accounts_history"} and _value_has_reporting_period(claim.get("value")):
                for ref in refs:
                    if ref in evidence_by_id and not evidence_by_id[ref].get("reporting_period"):
                        contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": claim.get("field"), "kind": "missing_reporting_period", "evidence_id": ref})
            if claim.get("field") == "dated_news" and claim.get("availability") == "available":
                details = claim.get("value_details") or {}
                published_at = details.get("published_at")
                if not details.get("url") or not details.get("title") or not published_at:
                    contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": "dated_news", "kind": "incomplete_value_details"})
                retrieved_instants = [_comparable_datetime((evidence_by_id.get(ref) or {}).get("retrieved_at")) for ref in refs]
                retrieved_instants = [item for item in retrieved_instants if item is not None]
                source_instant = _comparable_datetime(published_at)
                if source_instant and retrieved_instants and source_instant > max(retrieved_instants):
                    contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": "dated_news", "kind": "dated_news_after_retrieval", "value": published_at})
            if claim.get("field") in {"public_activity", "hiring"}:
                value = claim.get("value")
                if claim.get("field") == "public_activity":
                    dated_items = (value or {}).get("items", []) if isinstance(value, dict) else []
                elif isinstance(value, dict):
                    dated_items = [*(value.get("postings") or []), *(value.get("nav_ads") or [])]
                else:
                    dated_items = value if isinstance(value, list) else []
                if field == "hiring" and claim.get("availability") == "available" and isinstance(value, dict):
                    has_postings = bool(value.get("postings") or value.get("nav_ads"))
                    has_verified_career = bool(value.get("careers_page_url"))
                    if not has_postings and not has_verified_career:
                        contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": field, "kind": "available_hiring_without_postings_or_verified_careers_page"})
                retrieved_dates = []
                for ref in refs:
                    retrieved = (evidence_by_id.get(ref) or {}).get("retrieved_at")
                    if retrieved:
                        try:
                            retrieved_dates.append(datetime.fromisoformat(str(retrieved).replace("Z", "+00:00")).astimezone(timezone.utc).date())
                        except ValueError:
                            pass
                for item in dated_items:
                    if not isinstance(item, dict):
                        contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": claim.get("field"), "kind": "dated_item_not_object"})
                        continue
                    date_value = item.get("published_at") or item.get("date") or item.get("posted") or item.get("published")
                    parsed_date = parse_datetime(date_value) if date_value else None
                    if date_value and not parsed_date:
                        contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": claim.get("field"), "kind": "invalid_item_date", "value": date_value})
                    retrieved_instants = [_comparable_datetime((evidence_by_id.get(ref) or {}).get("retrieved_at")) for ref in refs]
                    retrieved_instants = [item for item in retrieved_instants if item is not None]
                    source_instant = _comparable_datetime(date_value)
                    if source_instant and retrieved_instants and source_instant > max(retrieved_instants):
                        contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": claim.get("field"), "kind": "activity_date_after_retrieval", "value": date_value})
                    if not (item.get("url") or item.get("source_url") or item.get("link")):
                        contract_errors.append({"organisation_number": envelope.get("organisation_number"), "field": claim.get("field"), "kind": "dated_item_without_source_url"})
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
