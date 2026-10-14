#!/usr/bin/env python3
"""Run a small, machine-readable self-check over published envelopes."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from norway_company_agent.registry.batch import validate_envelopes, read_organisation_inputs  # noqa: E402
from norway_company_agent.registry.contract import ALLOWED_AVAILABILITY, SCORED_EXTERNAL_FIELDS  # noqa: E402


REQUIRED_RUN_KEYS = ("run_id", "started_at", "completed_at", "terminal_status")
REQUIRED_OPERATION_KEYS = ("requests", "runtime_ms", "third_party_cost_usd")


def _json_type(value: Any, *, present: bool = True) -> str:
    if not present:
        return "missing"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _scored_value_type_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts = {field: Counter() for field in sorted(SCORED_EXTERNAL_FIELDS)}
    for row in rows:
        claims = row.get("claims") or []
        if not isinstance(claims, list):
            continue
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            field = str(claim.get("field") or "")
            if field not in SCORED_EXTERNAL_FIELDS:
                continue
            availability = str(claim.get("availability") or "missing")
            counts[field][f"{availability}|{_json_type(claim.get('value'), present='value' in claim)}"] += 1
    return {field: dict(sorted(values.items())) for field, values in counts.items()}


def _add_violation(violations: list[dict[str, Any]], **value: Any) -> None:
    if value not in violations:
        violations.append(value)


def check_contract(rows: list[dict[str, Any]], expected_count: int) -> dict[str, Any]:
    """Return the self-check report for already-loaded envelope rows."""
    violations: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            _add_violation(violations, row=index, kind="envelope_not_object")
            continue
        organisation_number = row.get("organisation_number")
        run = row.get("run")
        if not isinstance(run, dict):
            _add_violation(violations, organisation_number=organisation_number, kind="missing_required_key", path="run")
        else:
            for key in REQUIRED_RUN_KEYS:
                if key not in run:
                    _add_violation(violations, organisation_number=organisation_number, kind="missing_required_key", path=f"run.{key}")
        operations = row.get("operations")
        if not isinstance(operations, dict):
            _add_violation(violations, organisation_number=organisation_number, kind="missing_required_key", path="operations")
        else:
            for key in REQUIRED_OPERATION_KEYS:
                if key not in operations:
                    _add_violation(violations, organisation_number=organisation_number, kind="missing_required_key", path=f"operations.{key}")

        evidence_rows = row.get("evidence") or []
        evidence_by_id = {
            item.get("id"): item
            for item in evidence_rows
            if isinstance(item, dict) and item.get("id")
        } if isinstance(evidence_rows, list) else {}
        claims = row.get("claims") or []
        if not isinstance(claims, list):
            _add_violation(violations, organisation_number=organisation_number, kind="claims_not_array")
            continue
        for claim in claims:
            if not isinstance(claim, dict):
                _add_violation(violations, organisation_number=organisation_number, kind="claim_not_object")
                continue
            field = str(claim.get("field") or "")
            availability = claim.get("availability")
            if availability not in ALLOWED_AVAILABILITY:
                _add_violation(violations, organisation_number=organisation_number, field=field, kind="unknown_availability", availability=availability)
            if availability != "available":
                continue
            refs = claim.get("evidence_ids")
            if not isinstance(refs, list) or not refs:
                _add_violation(violations, organisation_number=organisation_number, field=field, kind="available_missing_evidence_refs")
            else:
                for ref in refs:
                    if ref not in evidence_by_id:
                        _add_violation(violations, organisation_number=organisation_number, field=field, evidence_id=ref, kind="available_dangling_evidence_id")
            if field in SCORED_EXTERNAL_FIELDS:
                value = claim.get("value")
                if not isinstance(value, str) or not value.strip():
                    _add_violation(violations, organisation_number=organisation_number, field=field, kind="scored_value_not_string")

    validation = validate_envelopes(rows, expected_count)
    for error in validation.get("contract_errors") or []:
        _add_violation(violations, source="validate_envelopes", **error)
    if len(rows) != expected_count:
        _add_violation(violations, kind="envelope_count_mismatch", expected_count=expected_count, actual_count=len(rows))

    return {
        "passed": not violations and validation.get("passed", False),
        "envelopes": len(rows),
        "expected_count": expected_count,
        "scored_value_type_counts": _scored_value_type_counts(rows),
        "violations": violations,
        "validator": validation,
    }


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append({"line": line_number, "kind": "invalid_json", "detail": str(exc)})
            continue
        rows.append(value)
    return rows, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Signalpost envelope contract conformance")
    parser.add_argument("--envelopes", required=True, help="Envelope JSONL to inspect")
    parser.add_argument("--organisations", help="Input organisation JSON/JSONL/text file used for the count check")
    parser.add_argument("--expected-count", type=int, help="Expected envelope count when no input file is available")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.organisations is None and args.expected_count is None:
        parser.error("one of --organisations or --expected-count is required")

    rows, load_errors = _load_jsonl(Path(args.envelopes))
    input_count = None
    input_error = None
    if args.organisations is not None:
        try:
            input_count = len(read_organisation_inputs(args.organisations, tolerant=True))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            input_error = {"kind": "organisations_read_error", "detail": str(exc)}
    expected_count = args.expected_count if args.expected_count is not None else input_count
    if expected_count is None:
        expected_count = 0
    report = check_contract(rows, expected_count)
    if load_errors:
        report["violations"].extend({"source": "envelopes", **error} for error in load_errors)
    if input_error:
        report["violations"].append(input_error)
    report["input_count"] = input_count
    report["passed"] = not report["violations"] and report["validator"].get("passed", False)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
    print(text, end="")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
