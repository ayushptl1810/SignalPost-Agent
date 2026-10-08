#!/usr/bin/env python3
"""Export G4-only website publications for human review.

The export is an audit aid, not an answer key.  It never changes cache records
and includes the deterministic rule and evidence span used by the builder.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.cache.universe import iter_cache_records  # noqa: E402


def export_g4_audit(cache: str | Path, output: str | Path) -> dict[str, int | str]:
    rows = []
    for record in iter_cache_records(cache):
        g4 = record.get("website_g4") or {}
        assessment = g4.get("first_party_gate") or {}
        if not g4.get("evidence") or assessment.get("rule") not in {"g4_name_address", "g4_registry_tie"}:
            continue
        evidence = g4.get("evidence") or {}
        value = evidence.get("value") or {}
        rows.append({
            "organisation_number": record.get("organisation_number"),
            "name": record.get("name"),
            "municipality": record.get("municipality"),
            "domain": value.get("registered_domain"),
            "final_url": value.get("final_url") or evidence.get("source_url"),
            "source": next((item.get("source") for item in record.get("candidates") or [] if item.get("domain") == value.get("registered_domain") and item.get("g4", {}).get("rule") == assessment.get("rule")), None),
            "rule": assessment.get("rule"),
            "evidence_span": assessment.get("evidence_span", ""),
            "retrieved_at": evidence.get("retrieved_at"),
            "content_sha256": value.get("content_sha256") or evidence.get("content_sha256"),
        })
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    return {"rows": len(rows), "output": str(destination)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export G4-only cache publications for audit.")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(export_g4_audit(args.cache, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
