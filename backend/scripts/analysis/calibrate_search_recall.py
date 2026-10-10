#!/usr/bin/env python3
"""Small, replayable Serper/SerpApi calibration against the free-source cache."""
from __future__ import annotations

import argparse
import gzip
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

from norway_company_agent.cache import CacheLookup  # noqa: E402
from norway_company_agent.core.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.search.providers import ProviderError, SerpApiSearchProvider, SerperSearchProvider  # noqa: E402
from norway_company_agent.web.first_party import assess_first_party_ownership  # noqa: E402
from norway_company_agent.web.website import HostRequestPolicy, fetch_website, registered_domain  # noqa: E402


def _read(path: str | Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _result_is_official(record: dict[str, Any], result: dict[str, Any], policy: HostRequestPolicy) -> tuple[bool, dict[str, Any]]:
    evidence, metrics = fetch_website(result.get("url"), timeout=15.0, request_policy=policy, max_secondary_pages=0)
    profile = {"organisation_number": record.get("organisation_number"), "name": record.get("name"), "website": record.get("website"), "raw": record.get("raw") or {}, "evidence": {}}
    gated = apply_website_identity_gate(profile, evidence)["website"]
    first_party = assess_first_party_ownership(profile, gated, istat_gate=True) if gated.get("status") == "available" else {"publishable": False}
    return bool(first_party.get("publishable")), {"metrics": metrics, "domain": registered_domain((gated.get("value") or {}).get("final_url") or result.get("url") or ""), "identity": gated.get("value", {}).get("identity_assessment"), "first_party": first_party}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", default="out/search-calibration/report.json")
    parser.add_argument("--raw-output", default="out/search-calibration/raw-results.jsonl")
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--serper-cap", type=int, default=450)
    parser.add_argument("--serpapi-cap", type=int, default=100)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    # Use the same deterministic ordering as the cache pilot without exposing key material.
    from scripts.run.build_universe_cache import read_universe, select_stratified
    records = select_stratified(read_universe(args.universe), args.limit)
    cache = CacheLookup(args.cache)
    raw_path = Path(args.raw_output)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_handle = raw_path.open("a", encoding="utf-8")
    serper_key = os.environ.get("SERPER_API_KEY", "").strip()
    serpapi_key = os.environ.get("SERPAPI_KEY", "").strip()
    serper = SerperSearchProvider(serper_key) if serper_key else None
    serpapi = SerpApiSearchProvider(serpapi_key) if serpapi_key else None
    free_missed: list[dict[str, Any]] = []
    search_found: list[dict[str, Any]] = []
    search_missed: list[dict[str, Any]] = []
    failures = Counter()
    policy = HostRequestPolicy(min_interval=1.0, max_inflight=1)
    serper_used = serpapi_used = 0
    for record in records:
        query = f"{record.get('name', '')} {record.get('municipality', '')}".strip()
        results: list[dict[str, Any]] = []
        if serper and serper_used < args.serper_cap:
            try:
                results, operation = serper.search(query, country="no", language="no", count=10, timeout=30.0)
                serper_used += 1
                raw_handle.write(json.dumps({"provider": "serper", "query": query, "results": results, "operation": {key: value for key, value in operation.items() if key not in {"api_key", "key"}}}, ensure_ascii=False) + "\n")
                raw_handle.flush()
            except ProviderError as exc:
                failures[f"serper:{type(exc).__name__}"] += 1
                break
        cached_site = ((cache.get(record.get("organisation_number")) or {}).get("website") or {}).get("evidence")
        free_domain = registered_domain((((cached_site or {}).get("value") or {}).get("final_url") or "")) if cached_site else ""
        found = False
        for result in results:
            try:
                official, detail = _result_is_official(record, result, policy)
            except Exception as exc:
                failures[type(exc).__name__] += 1
                continue
            if official:
                found = True
                search_found.append({"organisation_number": record.get("organisation_number"), "domain": detail.get("domain"), "free_domain": free_domain, "query": query})
                break
        if free_domain and not found:
            search_missed.append({"organisation_number": record.get("organisation_number"), "domain": free_domain, "query": query})
        elif found and free_domain == search_found[-1].get("domain"):
            pass
        elif found and not free_domain:
            free_missed.append(search_found[-1])
    # Highest-value misses are the first 100 free-source misses, and only after the Serper pass.
    serpapi_details = []
    for miss in free_missed[:args.serpapi_cap]:
        record = next((item for item in records if item.get("organisation_number") == miss.get("organisation_number")), None)
        if not record or not serpapi:
            continue
        query = f"{record.get('name', '')} {record.get('municipality', '')}".strip()
        try:
            results, operation = serpapi.search(query, country="no", language="no", count=10, timeout=60.0)
            serpapi_used += 1
            raw_handle.write(json.dumps({"provider": "serpapi", "query": query, "results": results, "operation": {key: value for key, value in operation.items() if key not in {"api_key", "key"}}}, ensure_ascii=False) + "\n")
            raw_handle.flush()
            serpapi_details.append({"organisation_number": record.get("organisation_number"), "results": len(results)})
        except ProviderError as exc:
            failures[f"serpapi:{type(exc).__name__}"] += 1
    raw_handle.close()
    report = {"sample_count": len(records), "serper_queries": serper_used, "serpapi_queries": serpapi_used, "caps": {"serper": args.serper_cap, "serpapi": args.serpapi_cap}, "keys_available": {"serper": bool(serper_key), "serpapi": bool(serpapi_key)}, "free_sources_found_sites_search_missed": len(search_missed), "search_found_sites_free_sources_missed": len(free_missed), "search_verified_overlap": len(search_found) - len(free_missed), "common_traits": {"search_missed_strata": Counter((cache.get(item.get("organisation_number")) or {}).get("stratum", "unknown") for item in search_missed), "free_missed_strata": Counter((cache.get(item.get("organisation_number")) or {}).get("stratum", "unknown") for item in free_missed)}, "provider_failures": dict(failures), "raw_results_path": str(raw_path), "note": "This is calibration only; search is not a runtime dependency."}
    report["common_traits"]["search_missed_strata"] = dict(report["common_traits"]["search_missed_strata"])
    report["common_traits"]["free_missed_strata"] = dict(report["common_traits"]["free_missed_strata"])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
