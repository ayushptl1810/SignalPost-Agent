#!/usr/bin/env python3
"""Collect fresh, non-search evidence for the 180-company extension.

This collector is intentionally conservative.  It checks the registry-listed
homepage and a small, deterministic set of name-derived domains, while
recording that the alternative-provider search was unavailable when no valid
provider key can answer.  It never turns an unperformed check into a
``no_site_confirmed`` label and never calls the evaluation scorecard.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.text import fold_tokens  # noqa: E402
from norway_company_agent.web.website import fetch_website, registered_domain  # noqa: E402

from annotate_eval_evidence import load_registry, read_jsonl  # noqa: E402


GENERIC = {"as", "asa", "ans", "da", "enk", "iks", "sa", "nuf", "og", "the", "group", "gruppen", "norge", "norway"}


def slug_candidates(name: str) -> list[str]:
    tokens = [token for token in fold_tokens(name) if token not in GENERIC and len(token) > 1]
    if not tokens:
        return []
    compact = "".join(tokens)
    dashed = "-".join(tokens)
    values = [compact, dashed, f"{compact}as", f"{dashed}-as"]
    return list(dict.fromkeys(f"https://{value}.no/" for value in values if len(value) >= 7))[:2]


def _page(org: str, name: str, url: str, source: str, *, timeout: float) -> tuple[dict[str, Any], dict[str, Any]]:
    website, operations = fetch_website(url, timeout=timeout, max_bytes=750_000)
    return {
        "organisation_number": org,
        "name": name,
        "search_result": {"url": url, "title": source, "snippet": "", "rank": 0, "provider": source},
        "website": website,
        "operations": operations,
    }, operations


def collect_row(row: dict[str, Any], registry: dict[str, Any], *, timeout: float, registry_only: bool = False) -> dict[str, Any]:
    org = str(row["organisation_number"])
    registry_site = str(registry.get("website") or (registry.get("raw") or {}).get("hjemmeside") or "").strip()
    urls: list[tuple[str, str]] = []
    if registry_site:
        urls.append((registry_site, "registry_listed_site"))
    registry_domain = registered_domain(registry_site) if registry_site else None
    if not registry_only:
        for candidate in slug_candidates(str(row.get("name") or "")):
            if registered_domain(candidate) != registry_domain:
                urls.append((candidate, "name_derived_domain"))
    pages: list[dict[str, Any]] = []
    domain_checks: list[dict[str, Any]] = []
    for url, source in urls:
        try:
            page, operations = _page(org, str(row.get("name") or ""), url, source, timeout=timeout)
        except Exception as exc:  # pragma: no cover - defensive network boundary
            page = {
                "organisation_number": org,
                "name": row.get("name"),
                "search_result": {"url": url, "title": source, "rank": 0, "provider": source},
                "website": {"status": "source_error", "source_url": url, "error": type(exc).__name__},
                "operations": {"requests": 0},
            }
            operations = page["operations"]
        pages.append(page)
        website = page.get("website") or {}
        value = website.get("value") or {}
        domain_checks.append({
            "url": url,
            "source": source,
            "status": website.get("status"),
            "final_url": value.get("final_url") or website.get("source_url") or url,
            "publishable": False,
            "requests": operations.get("requests", 0),
        })
    return {
        "organisation_number": org,
        "name": row.get("name"),
        "split": row.get("split"),
        "stratum": row.get("stratum"),
        "queries": [],
        "provider": "none_available",
        "provider_attempted": False,
        "provider_error": "serpapi_and_linkup_bakeoff_keys_rejected_or_unavailable",
        "results": [],
        "domain_checks": domain_checks,
        "pages": pages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect conservative fresh extension evidence without scoring it.")
    parser.add_argument("--manifest", default="out/eval-sample/manifest-extension.jsonl")
    parser.add_argument("--registry", default="data/brreg-enheter.csv")
    parser.add_argument("--search-output", required=True)
    parser.add_argument("--pages-output", required=True)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--registry-only", action="store_true", help="Independent second pass: visit only registry-listed sites")
    parser.add_argument("--limit", type=int, help="Deterministic blind subset size")
    parser.add_argument("--seed", type=int, default=20261009)
    args = parser.parse_args()
    rows = read_jsonl(Path(args.manifest))
    if args.limit is not None:
        rows = sorted(rows, key=lambda item: hashlib.sha256(f"{args.seed}:{item['organisation_number']}".encode()).hexdigest())[:args.limit]
    targets = {str(row["organisation_number"]) for row in rows}
    registry = load_registry(Path(args.registry), targets)
    collected: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
        futures = {pool.submit(collect_row, row, registry[str(row["organisation_number"])], timeout=args.timeout, registry_only=args.registry_only): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            try:
                evidence = future.result()
            except Exception as exc:  # pragma: no cover - defensive registry/network boundary
                evidence = {
                    "organisation_number": str(row["organisation_number"]), "name": row.get("name"),
                    "split": row.get("split"), "stratum": row.get("stratum"), "queries": [],
                    "provider": "none_available", "provider_attempted": False,
                    "provider_error": type(exc).__name__, "results": [], "domain_checks": [], "pages": [],
                }
            collected.append({key: value for key, value in evidence.items() if key != "pages"})
            pages.extend(evidence.get("pages") or [])
    collected.sort(key=lambda item: str(item["organisation_number"]))
    pages.sort(key=lambda item: (str(item["organisation_number"]), str((item.get("search_result") or {}).get("url") or "")))
    for output, payload in ((Path(args.search_output), collected), (Path(args.pages_output), pages)):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in payload), encoding="utf-8")
    print(json.dumps({
        "companies": len(collected), "pages": len(pages),
        "registry_sites": sum(1 for row in collected if any(item.get("source") == "registry_listed_site" for item in row.get("domain_checks") or [])),
        "derived_checks": sum(sum(item.get("source") == "name_derived_domain" for item in row.get("domain_checks") or []) for row in collected),
        "provider_search": "not_run_after_bakeoff_auth_or_quota_failures",
        "search_output": args.search_output, "pages_output": args.pages_output,
    }, indent=2))


if __name__ == "__main__":
    main()
