#!/usr/bin/env python3
"""Measure local cache coverage on a fixed, non-certification sample."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.cache import CacheLookup  # noqa: E402
from norway_company_agent.cache.universe import FIELD_FAMILIES  # noqa: E402
from norway_company_agent.registry.sampling import financial_filer_stratum  # noqa: E402


def read_universe(path: str | Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def fixed_sample(records: list[dict[str, Any]], count: int = 1000, seed: int = 20261008) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in records:
        groups.setdefault(financial_filer_stratum(item), []).append(item)
    selected: list[dict[str, Any]] = []
    total = len(records) or 1
    for key, values in groups.items():
        quota = min(len(values), count * len(values) // total)
        ranked = sorted(values, key=lambda item: hashlib.sha256(f"{seed}:{item.get('organisation_number')}".encode()).hexdigest())
        selected.extend(ranked[:quota])
    remaining = count - len(selected)
    chosen = {item.get("organisation_number") for item in selected}
    rest = sorted((item for item in records if item.get("organisation_number") not in chosen), key=lambda item: hashlib.sha256(f"{seed}:rest:{item.get('organisation_number')}".encode()).hexdigest())
    return selected + rest[:remaining]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", default="out/recall-report.json")
    parser.add_argument("--audit-output", default="out/published-claims-audit-100.jsonl")
    parser.add_argument("--count", type=int, default=1000)
    args = parser.parse_args()
    records = fixed_sample(read_universe(args.universe), args.count)
    lookup = CacheLookup(args.cache)
    report: dict[str, Any] = {"seed": 20261008, "sample_count": len(records), "cache_path": args.cache, "cache_manifest": lookup.manifest, "field_families": {}}
    for family in FIELD_FAMILIES:
        states = Counter()
        company_available = 0
        claim_count = 0
        for item in records:
            cached = lookup.get(item.get("organisation_number"))
            state = (cached or {}).get("states", {}).get(family, "not_checked")
            states[state] += 1
            if state == "available":
                company_available += 1
                value = (cached or {}).get("claims", {}).get(family)
                claim_count += len(value) if isinstance(value, list) else 1 if value else 0
        report["field_families"][family] = {"company_coverage": company_available / len(records) if records else 0.0, "companies_available": company_available, "claim_count": claim_count, "state_counts": dict(states), "lost_to_abstention": sum(states[state] for state in ("ambiguous", "blocked", "failed"))}
    published = [record for record in lookup.records.values() if (record.get("states") or {}).get("official_website") == "available"]
    published.sort(key=lambda item: hashlib.sha256(f"20261008:audit:{item.get('organisation_number')}".encode()).hexdigest())
    audit_path = Path(args.audit_output)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("w", encoding="utf-8") as handle:
        for record in published[:100]:
            website = ((record.get("website") or {}).get("evidence") or {})
            value = website.get("value") or {}
            handle.write(json.dumps({"organisation_number": record.get("organisation_number"), "name": record.get("name"), "final_url": value.get("final_url") or website.get("source_url"), "retrieved_at": website.get("retrieved_at"), "content_sha256": value.get("content_sha256") or website.get("content_sha256"), "evidence_snippet": (value.get("identity_text_excerpt") or value.get("main_text_excerpt") or "")[:500]}, ensure_ascii=False) + "\n")
    report["published_website_audit_sample"] = {"path": str(audit_path), "rows": min(100, len(published)), "target": 100}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
