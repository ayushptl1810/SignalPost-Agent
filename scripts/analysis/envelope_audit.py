#!/usr/bin/env python3
"""Audit contract claims, evidence references and financial value fidelity."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from norway_company_agent.site_facts import parse_date  # noqa: E402


REQUIRED_EVIDENCE = ("source_url", "source_class", "retrieved_at", "content_sha256", "claim_span")


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


def _same_bytes(left: Any, right: Any) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(right, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    availability: dict[str, Counter[str]] = defaultdict(Counter)
    missing_required: list[dict[str, Any]] = []
    financial_mismatches: list[dict[str, Any]] = []
    dated_item_errors: list[dict[str, Any]] = []
    for row in rows:
        evidence_by_id = {item.get("id"): item for item in row.get("evidence") or []}
        profile_evidence = (row.get("profile") or {}).get("evidence") or {}
        for claim in row.get("claims") or []:
            field = str(claim.get("field") or "")
            state = str(claim.get("availability") or "missing")
            availability[field][state] += 1
            if state == "available":
                refs = claim.get("evidence_ids") or []
                for ref in refs:
                    evidence_row = evidence_by_id.get(ref) or {}
                    missing = [key for key in REQUIRED_EVIDENCE if not evidence_row.get(key)]
                    if missing:
                        missing_required.append({"organisation_number": row.get("organisation_number"), "field": field, "evidence_id": ref, "missing": missing})
                    if field in {"latest_annual_accounts", "accounts_history"} and _value_has_reporting_period(claim.get("value")) and not evidence_row.get("reporting_period"):
                        missing_required.append({"organisation_number": row.get("organisation_number"), "field": field, "evidence_id": ref, "missing": ["reporting_period"]})
                if not refs:
                    missing_required.append({"organisation_number": row.get("organisation_number"), "field": field, "missing": ["evidence_ids"]})
            if field in {"latest_annual_accounts", "accounts_history"} and state == "available":
                history_source = profile_evidence.get("financial_history")
                if field == "accounts_history" and history_source:
                    source = history_source
                    expected = (source.get("value") or {})
                else:
                    source = profile_evidence.get("financials") or {}
                    records = sorted((source.get("value") or {}).get("records") or [], key=_record_key, reverse=True)
                    expected = (records[0] if records else None) if field == "latest_annual_accounts" else (records[1:] or None)
                if not _same_bytes(claim.get("value"), expected):
                    financial_mismatches.append({"organisation_number": row.get("organisation_number"), "field": field, "claim_value": claim.get("value"), "source_value": expected})
            if field in {"public_activity", "hiring"}:
                value = claim.get("value")
                if field == "public_activity":
                    items = (value or {}).get("items", []) if isinstance(value, dict) else []
                elif isinstance(value, dict):
                    items = [*(value.get("postings") or []), *(value.get("nav_ads") or [])]
                else:
                    items = value if isinstance(value, list) else []
                retrieved = []
                for ref in claim.get("evidence_ids") or []:
                    try:
                        timestamp = datetime.fromisoformat(str((evidence_by_id.get(ref) or {}).get("retrieved_at")).replace("Z", "+00:00"))
                        retrieved.append(timestamp.astimezone(timezone.utc).date())
                    except (TypeError, ValueError):
                        pass
                for item in items:
                    if not isinstance(item, dict):
                        dated_item_errors.append({"organisation_number": row.get("organisation_number"), "field": field, "kind": "dated_item_not_object"})
                        continue
                    date_value = item.get("date") or item.get("posted") or item.get("published")
                    parsed = parse_date(date_value) if date_value else None
                    if date_value and not parsed:
                        dated_item_errors.append({"organisation_number": row.get("organisation_number"), "field": field, "kind": "invalid_date", "value": date_value})
                    if parsed and retrieved and datetime.fromisoformat(parsed).date() > max(retrieved):
                        dated_item_errors.append({"organisation_number": row.get("organisation_number"), "field": field, "kind": "date_after_retrieval", "value": date_value})
                    if not (item.get("url") or item.get("source_url") or item.get("link")):
                        dated_item_errors.append({"organisation_number": row.get("organisation_number"), "field": field, "kind": "missing_source_url"})
    return {
        "rows": len(rows),
        "availability_counts": {field: dict(counts) for field, counts in sorted(availability.items())},
        "available_claims_missing_required_evidence": missing_required,
        "financial_value_mismatches": financial_mismatches,
        "dated_item_errors": dated_item_errors,
        "passed": not missing_required and not financial_mismatches and not dated_item_errors,
    }


def _record_key(record: dict) -> tuple[str, int]:
    period = record.get("period") or {}
    until = period.get("tilDato") if isinstance(period, dict) else str(period)
    try:
        record_id = int(record.get("record_id") or 0)
    except (TypeError, ValueError):
        record_id = 0
    return (str(until or ""), record_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--envelopes", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.envelopes).read_text(encoding="utf-8").splitlines() if line.strip()]
    report = audit(rows)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
    print(text, end="")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
