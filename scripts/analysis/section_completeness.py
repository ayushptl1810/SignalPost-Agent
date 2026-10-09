#!/usr/bin/env python3
"""Report availability and fact counts for each envelope section."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _rows(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _facts(field: str, value: Any) -> list[Any]:
    if value in (None, "", [], {}):
        return []
    if field == "public_activity" and isinstance(value, dict):
        return list(value.get("items") or [])
    if field == "hiring" and isinstance(value, dict):
        return list(value.get("postings") or value.get("ads") or value.get("nav_ads") or [])
    if field == "company_brief" and isinstance(value, dict) and value.get("parts"):
        return list(value["parts"])
    if isinstance(value, list):
        return value
    return [value]


def completeness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    availability: dict[str, Counter[str]] = defaultdict(Counter)
    facts_per_company: dict[str, list[int]] = defaultdict(list)
    deficits: list[dict[str, Any]] = []
    for row in rows:
        profile = row.get("profile") or {}
        source = profile.get("evidence") or {}
        claims = {str(item.get("field")): item for item in row.get("claims") or []}
        for field, claim in claims.items():
            availability[field][str(claim.get("availability") or "missing")] += 1
            facts_per_company[field].append(len(_facts(field, claim.get("value"))))
        expected = {
            "accounts_all_years": len((((source.get("financials") or {}).get("value") or {}).get("records") or [])),
            "leadership": len((((source.get("roles") or {}).get("value") or {}).get("roles") or [])),
            "registered_workplaces": len((((source.get("locations") or {}).get("value") or {}).get("locations") or [])),
        }
        for field, expected_count in expected.items():
            if field == "accounts_all_years":
                actual = len(_facts("latest_annual_accounts", (claims.get("latest_annual_accounts") or {}).get("value"))) + len(_facts("accounts_history", (claims.get("accounts_history") or {}).get("value")))
            else:
                actual = len(_facts(field, (claims.get(field) or {}).get("value")))
            if actual < expected_count:
                deficits.append({"organisation_number": row.get("organisation_number"), "field": field, "expected_from_registry": expected_count, "emitted": actual})
    summary = {}
    for field in sorted(availability):
        counts = availability[field]
        values = facts_per_company[field]
        summary[field] = {"availability": dict(counts), "companies": len(values), "total_facts": sum(values), "facts_per_company": {"min": min(values) if values else 0, "max": max(values) if values else 0, "average": round(sum(values) / len(values), 3) if values else 0.0}}
    return {"rows": len(rows), "sections": summary, "registry_fact_deficits": deficits, "passed": not deficits}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelopes", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = completeness(_rows(args.envelopes))
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
