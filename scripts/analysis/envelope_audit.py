#!/usr/bin/env python3
"""Audit contract claims, evidence references and financial value fidelity."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REQUIRED_EVIDENCE = ("source_url", "source_class", "retrieved_at", "content_sha256", "claim_span")


def _same_bytes(left: Any, right: Any) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(right, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    availability: dict[str, Counter[str]] = defaultdict(Counter)
    missing_required: list[dict[str, Any]] = []
    financial_mismatches: list[dict[str, Any]] = []
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
                if not refs:
                    missing_required.append({"organisation_number": row.get("organisation_number"), "field": field, "missing": ["evidence_ids"]})
            if field in {"latest_annual_accounts", "accounts_history"} and state == "available":
                source = profile_evidence.get("financials" if field == "latest_annual_accounts" else "financial_history") or {}
                source_value = source.get("value") or {}
                expected = (source_value.get("records") or [None])[0] if field == "latest_annual_accounts" else source_value
                if not _same_bytes(claim.get("value"), expected):
                    financial_mismatches.append({"organisation_number": row.get("organisation_number"), "field": field, "claim_value": claim.get("value"), "source_value": expected})
    return {
        "rows": len(rows),
        "availability_counts": {field: dict(counts) for field, counts in sorted(availability.items())},
        "available_claims_missing_required_evidence": missing_required,
        "financial_value_mismatches": financial_mismatches,
        "passed": not missing_required and not financial_mismatches,
    }


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
