#!/usr/bin/env python3
"""Optional Serper gap-fill over an existing universe cache.

This command is never part of the default batch path.  It stores only the
provider results and the G4 decision metadata, never an API key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.cache import CacheLookup  # noqa: E402
from norway_company_agent.search.providers import ProviderError, SerperSearchProvider  # noqa: E402
from norway_company_agent.web.first_party import assess_g4_ownership  # noqa: E402
from norway_company_agent.core.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.web.website import HostRequestPolicy, fetch_website  # noqa: E402


def _read_rows(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[str(row.get("organisation_number"))] = row
    return rows


def _write_rows(path: Path, rows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(rows[key], ensure_ascii=False, separators=(",", ":")) + "\n" for key in sorted(rows)), encoding="utf-8")
    temporary.replace(path)


def build_search_cache(cache: str | Path, output: str | Path, *, budget: int, api_key: str | None, rate: float = 5.0, timeout: float = 30.0) -> dict:
    records = CacheLookup(cache).records
    existing = _read_rows(Path(output))
    provider = SerperSearchProvider(api_key) if api_key else None
    policy = HostRequestPolicy(min_interval=1.0, max_inflight=1)
    used = 0
    hits = 0
    errors = 0
    skipped = 0
    last_started = 0.0
    for org, record in sorted(records.items()):
        if record.get("states", {}).get("official_website") == "available":
            skipped += 1
            continue
        if org in existing:
            continue
        if used >= max(0, budget) or provider is None:
            break
        wait = (1.0 / max(rate, 0.01)) - (time.monotonic() - last_started)
        if wait > 0:
            time.sleep(wait)
        query = f'"{record.get("name") or ""}" {record.get("municipality") or ""}'.strip()
        last_started = time.monotonic()
        used += 1
        row = {"organisation_number": org, "query": query, "results": [], "g4": [], "operation": {"provider": "serper", "queries": 1}}
        try:
            results, operation = provider.search(query, country="no", language="no", count=3, timeout=timeout)
            row["results"] = results
            row["operation"].update({key: value for key, value in operation.items() if key not in {"api_key", "key"}})
            profile = {"organisation_number": org, "name": record.get("name"), "municipality": record.get("municipality"), "website": ((record.get("website") or {}).get("evidence") or {}).get("source_url"), "raw": record.get("raw") or {}, "evidence": {}}
            for result in results[:3]:
                evidence, _metrics = fetch_website(result.get("url"), timeout=timeout, request_policy=policy, max_secondary_pages=2)
                gated = apply_website_identity_gate(profile, evidence)["website"]
                decision = assess_g4_ownership(profile, gated, candidate_source="search_fill")
                row["g4"].append({"url": result.get("url"), "rule": decision.get("rule"), "publishable": bool(decision.get("publishable")), "evidence_span": decision.get("evidence_span", "")})
                if decision.get("publishable"):
                    hits += 1
                    break
        except ProviderError as exc:
            errors += 1
            row["operation"]["error"] = type(exc).__name__
        existing[org] = row
        _write_rows(Path(output), existing)
    return {"cache": str(cache), "output": str(output), "candidate_records": len(records), "queries": used, "budget": max(0, budget), "g4_hits": hits, "provider_errors": errors, "skipped_published": skipped, "resumed": len(existing) - used}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an optional, capped Serper search cache.")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--rate", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    report = build_search_cache(args.cache, args.output, budget=args.budget, api_key=os.environ.get("SERPER_API_KEY"), rate=args.rate, timeout=args.timeout)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
