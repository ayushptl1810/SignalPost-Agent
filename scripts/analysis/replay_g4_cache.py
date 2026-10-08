#!/usr/bin/env python3
"""Replay G4 eligibility from a recorded cache without fetching pages.

S12's random2000 fixture predates the v2 evidence-span fields.  For that
fixture this tool replays only the boolean signals recorded by the old G3
assessment and marks the result as a conservative signal replay; a v2 cache
with page evidence should be audited with ``export_g4_audit.py``.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.cache.universe import iter_cache_records  # noqa: E402

RELATED_DOMAINS = {"bbl.no", "ringbo.no", "helgelandbbl.no", "privatmegleren.no", "krausnaimer.no", "trysilposten.no", "ragde.no", "rvsas.no"}


def replay(cache: str | Path) -> dict:
    records = list(iter_cache_records(cache))
    g3 = sum(1 for record in records if (record.get("states") or {}).get("official_website") == "available")
    added: dict[str, dict] = {}
    rules = Counter()
    for record in records:
        org = str(record.get("organisation_number"))
        if (record.get("states") or {}).get("official_website") == "available":
            continue
        for candidate in record.get("candidates") or []:
            assessment = candidate.get("first_party") or {}
            signals = assessment.get("signals") or {}
            domain = str(candidate.get("domain") or "")
            if any(signals.get(key) for key in ("blocked_host", "directory_marker", "listing_path_marker", "contradicted", "group_related_only")) or domain in RELATED_DOMAINS:
                continue
            rule = None
            if signals.get("identity_verified") and signals.get("legal_name_match") and signals.get("address_match"):
                rule = "g4_name_address"
            elif candidate.get("source") in {"registry_website", "registry_email_domain"} and signals.get("identity_verified") and (signals.get("address_match") or signals.get("phone_match")):
                rule = "g4_registry_tie"
            if rule and org not in added:
                added[org] = {"organisation_number": org, "name": record.get("name"), "domain": domain, "rule": rule, "evidence_span": "replayed from recorded v1 gate signals; raw page span unavailable"}
                rules[rule] += 1
    return {"cache": str(cache), "records": len(records), "g3_available": g3, "g4_added": len(added), "g4_added_by_rule": dict(rules), "g3_reproduced": g3 == 173 if len(records) == 2000 else None, "mode": "recorded_signal_replay", "audit_warning": "random2000 is a v1 cache without raw G4 page spans; planner must audit v2 G4 evidence before promotion"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay G4 signals from an existing cache without network access.")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = replay(args.cache)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
