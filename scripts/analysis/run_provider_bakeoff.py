#!/usr/bin/env python3
"""Compare search-provider candidate recall on the development annotations.

This measures retrieval only.  It never changes the website publication gate.
Provider responses are kept in memory; a provider marked as non-storable is
therefore safe to evaluate without creating a result cache.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

from norway_company_agent.search.pool import ProviderPool  # noqa: E402
from norway_company_agent.web.discovery import BLOCKED_DISCOVERY_HOSTS, build_company_search_queries  # noqa: E402
from norway_company_agent.web.website import registered_domain  # noqa: E402


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def select_bakeoff_rows(profiles: list[dict[str, Any]], annotations: list[dict[str, Any]], *, no_site_limit: int = 20) -> list[dict[str, Any]]:
    labels = {str(row["organisation_number"]): row for row in annotations if row.get("split") == "development"}
    official = [row for row in annotations if row.get("split") == "development" and row.get("outcome") == "official_site"]
    no_site = [row for row in annotations if row.get("split") == "development" and row.get("outcome") == "no_site_confirmed"][:no_site_limit]
    selected = {str(row["organisation_number"]) for row in [*official, *no_site]}
    return [profile for profile in profiles if str(profile.get("organisation_number")) in selected and str(profile.get("organisation_number")) in labels]


def _directory(result: dict[str, Any]) -> bool:
    domain = registered_domain(result.get("url") or "") or ""
    host = domain.casefold().removeprefix("www.")
    path = str(result.get("url") or "").casefold()
    return any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_DISCOVERY_HOSTS) or any(marker in path for marker in ("/company/", "/foretak/", "/bedrift/", "/firma/", "/profil/"))


def score_results(rows: list[dict[str, Any]], annotations: list[dict[str, Any]], results_by_org: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    labels = {str(row["organisation_number"]): row for row in annotations if row.get("split") == "development"}
    metrics: dict[str, Any] = {}
    for cutoff in (3, 5, 10):
        eligible = 0
        hits = 0
        for row in rows:
            label = labels.get(str(row.get("organisation_number"))) or {}
            if label.get("outcome") != "official_site" or not label.get("domain"):
                continue
            eligible += 1
            domains = {registered_domain(item.get("url") or "") for item in results_by_org.get(str(row.get("organisation_number")), [])[:cutoff]}
            if str(label["domain"]).casefold() in domains:
                hits += 1
        metrics[f"recall_at_{cutoff}"] = round(hits / eligible, 4) if eligible else None
    top_five = [item for values in results_by_org.values() for item in values[:5]]
    metrics["directory_share_top5"] = round(sum(_directory(item) for item in top_five) / len(top_five), 4) if top_five else None
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the development retrieval bake-off.")
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--annotations", required=True, type=Path)
    parser.add_argument("--baseline-run", type=Path, help="Stored Serper discovery output for the no-new-credit baseline")
    parser.add_argument("--provider", required=True, help="One pinned provider name")
    parser.add_argument("--provider-config", default=str(ROOT / "data" / "search-providers.json"))
    parser.add_argument("--provider-usage", default="out/provider-usage.json")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--no-site-limit", type=int, default=20)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")

    profiles = read_jsonl(args.profiles)
    annotations = read_jsonl(args.annotations)
    rows = select_bakeoff_rows(profiles, annotations, no_site_limit=args.no_site_limit)
    config = ProviderPool.load_config(Path(args.provider_config))
    pool = ProviderPool.from_environment(config=config, usage_path=Path(args.provider_usage), rotation="priority", provider_pin=args.provider, legacy_serper_key=os.environ.get("SERPER_API_KEY"))
    results_by_org: dict[str, list[dict[str, Any]]] = {}
    latencies: list[int] = []
    errors: Counter[str] = Counter()
    started = time.monotonic()
    for row in rows:
        query = build_company_search_queries(row, include_identifier_fallback=False)[0]
        try:
            results, operation = pool.search(query, country="no", language="no", count=args.count, timeout=args.timeout)
            results_by_org[str(row["organisation_number"])] = results
            latencies.append(int(operation.get("latency_ms") or 0))
        except Exception as exc:
            errors[type(exc).__name__] += 1
            results_by_org[str(row["organisation_number"])] = []
    output = {
        "provider": args.provider,
        "rotation": "off",
        "companies": len(rows),
        "official_site_companies": sum(1 for row in rows if next((a for a in annotations if str(a.get("organisation_number")) == str(row.get("organisation_number")) and a.get("split") == "development"), {}).get("outcome") == "official_site"),
        "queries_used": pool.report().get("queries_used"),
        "latency_ms": {"p50": sorted(latencies)[len(latencies) // 2] if latencies else None, "wall_seconds": round(time.monotonic() - started, 3)},
        "quota_or_provider_errors": dict(errors),
        "metrics": score_results(rows, annotations, results_by_org),
        "provider_pool": pool.report(),
        "cache_written": False,
        "note": "Retrieval metrics only; publication gate is not evaluated here.",
    }
    if args.baseline_run:
        baseline_rows = read_jsonl(args.baseline_run)
        baseline_results = {
            str(row.get("organisation_number")): [
                {"url": item.get("final_url") or item.get("url"), "position": item.get("rank") or 999, "title": "", "snippet": ""}
                for item in (row.get("evidence") or {}).get("website_discovered_candidates") or []
                if item.get("source") in {"serper", "serper_api"}
            ]
            for row in baseline_rows
        }
        output["serper_stored_baseline"] = score_results(rows, annotations, baseline_results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
