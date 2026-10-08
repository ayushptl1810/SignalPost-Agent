#!/usr/bin/env python3
"""Explain strong-looking, unpublished G4 candidates in a declared cache."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.cache.universe import iter_cache_records  # noqa: E402


def _conditions(candidate: dict) -> list[str]:
    g4 = candidate.get("g4") or {}
    first_party = candidate.get("first_party") or {}
    signals = {**(first_party.get("signals") or {}), **(g4.get("g4_signals") or {})}
    reasons: list[str] = []
    related = g4.get("related_only") or candidate.get("related_only") or {}
    if related.get("related_only"):
        reasons.append(f"related_only:{related.get('reason')}")
    if signals.get("contradicted") or signals.get("contradicting_organisation_numbers"):
        reasons.append("different_organisation_number_veto")
    if not signals.get("identity_verified"):
        reasons.append("identity_gate")
    for key, label in (
        ("blocked_host", "blocked_host"),
        ("directory_marker", "directory_marker"),
        ("listing_path_marker", "listing_path_marker"),
        ("group_related_only", "group_related_only"),
    ):
        if signals.get(key):
            reasons.append(label)
    if not signals.get("legal_name_all_tokens", signals.get("legal_name_match")):
        reasons.append("legal_name_tokens")
    if not signals.get("registered_street_and_postcode", signals.get("address_match")):
        reasons.append("registered_address")
    if not signals.get("registry_candidate_tie") and not signals.get("registry_website_match"):
        reasons.append("registry_tie")
    return reasons or ["first_party_gate"]


def build_report(cache_path: str | Path) -> list[dict]:
    rows: list[dict] = []
    for record in iter_cache_records(cache_path):
        states = record.get("states") or {}
        if states.get("official_website_g4") == "available":
            continue
        for candidate in record.get("candidates") or []:
            first_party = candidate.get("first_party") or {}
            g4 = candidate.get("g4") or {}
            signals = {**(first_party.get("signals") or {}), **(g4.get("g4_signals") or {})}
            strong = bool(
                (signals.get("identity_verified") and (signals.get("address_match") or signals.get("phone_match") or signals.get("registry_website_match")))
                or signals.get("legal_name_all_tokens")
                or signals.get("registry_candidate_tie")
            )
            if not strong:
                continue
            rows.append({
                "organisation_number": str(record.get("organisation_number")),
                "name": record.get("name"),
                "domain": candidate.get("domain"),
                "requested_url": candidate.get("requested_url"),
                "source": candidate.get("source"),
                "website_status": candidate.get("website_status"),
                "state": candidate.get("state"),
                "conditions_blocking_publication": _conditions(candidate),
                "first_party": first_party,
                "g4": g4,
            })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    rows = build_report(args.cache)
    text = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    print(json.dumps({"rows": len(rows), "cache": args.cache}, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    main()
