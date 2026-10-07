#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - the dependency is declared for normal runs
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

from norway_company_agent.core.evidence import evidence, utc_now  # noqa: E402
from norway_company_agent.core.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.core.ledger import DiscoveryLedger  # noqa: E402
from norway_company_agent.external.nav_jobs import FEED_ORIGIN, load_index as load_nav_index  # noqa: E402
from norway_company_agent.web.candidates import registry_candidates  # noqa: E402
from norway_company_agent.web.classification import build_candidate_classifier  # noqa: E402
from norway_company_agent.web.constraints import enforce_domain_uniqueness  # noqa: E402
from norway_company_agent.web.discovery import (  # noqa: E402
    build_company_search_query,
    build_company_search_queries,
    choose_search_candidates,
    parse_serper_results,
)
from norway_company_agent.web.first_party import assess_first_party_ownership  # noqa: E402
from norway_company_agent.web.related import assess_related_entity  # noqa: E402
from norway_company_agent.web.website import fetch_website  # noqa: E402

SERPER_ENDPOINT = "https://google.serper.dev/search"
REGISTRY_ENDPOINT = "https://data.brreg.no/enhetsregisteret/api/enheter/{org}"
RETRYABLE_STATUSES = {0, 429, 500, 502, 503, 504}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def _serper_once(
    profile: dict[str, Any],
    api_key: str,
    *,
    timeout: float,
    count: int,
    query: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    search_query = query or build_company_search_query(profile)
    body = json.dumps({
        "q": search_query,
        "gl": "no",
        "hl": "no",
        "num": count,
    }).encode("utf-8")
    request = urllib.request.Request(
        SERPER_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-KEY": api_key,
            "User-Agent": "builderr-signalpost-poc/0.1 (+https://builderr.ai)",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
        elapsed_ms = int((time.monotonic() - started) * 1000)
        payload = json.loads(raw)
        return parse_serper_results(payload, query=search_query), {
            "status": status,
            "latency_ms": elapsed_ms,
            "bytes": len(raw),
            "query_sha256": hashlib.sha256(search_query.encode("utf-8")).hexdigest(),
        }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        return [], {
            "status": getattr(exc, "code", 0),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "bytes": 0,
            "query_sha256": hashlib.sha256(search_query.encode("utf-8")).hexdigest(),
            "error": type(exc).__name__,
        }


def serper_search(
    profile: dict[str, Any],
    api_key: str,
    *,
    timeout: float,
    count: int,
    query: str | None = None,
    retries: int = 2,
    backoff: float = 1.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One Serper query, retrying rate-limit, server and network errors with exponential backoff."""
    for attempt in range(retries + 1):
        results, operation = _serper_once(profile, api_key, timeout=timeout, count=count, query=query)
        operation["attempts"] = attempt + 1
        if not operation.get("error") or operation.get("status") not in RETRYABLE_STATUSES or attempt == retries:
            return results, operation
        time.sleep(backoff * (2 ** attempt))
    raise AssertionError("unreachable")  # pragma: no cover


def _candidate_classifier_input(candidate: dict[str, Any], website: dict[str, Any]) -> dict[str, Any]:
    value = website.get("value") or {}
    return {
        **candidate,
        "page_title": value.get("title"),
        "page_text": " ".join([
            str(value.get("description") or ""),
            str(value.get("main_text_excerpt") or ""),
            " ".join(str(page.get("main_text_excerpt") or "") for page in value.get("pages") or []),
        ])[:12000],
        "identity_text": str(value.get("identity_text_excerpt") or ""),
    }


def _candidate_summary(
    candidate: dict[str, Any],
    website: dict[str, Any],
    classifier: dict[str, Any],
    retrieval_classifier: dict[str, Any],
    first_party: dict[str, Any],
    related: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    return {
        "url": candidate.get("url"),
        "registered_domain": candidate.get("registered_domain"),
        "source": candidate.get("provider"),
        "rank": candidate.get("rank"),
        "candidate_score": candidate.get("score"),
        "website_status": website.get("status"),
        "final_url": website.get("source_url") or value.get("final_url"),
        "identity_status": identity.get("status"),
        "identity_score": identity.get("score"),
        "identity_publishable": identity.get("publishable", False),
        "first_party_status": first_party.get("status"),
        "first_party_publishable": first_party.get("publishable", False),
        "publishable": bool(identity.get("publishable") and first_party.get("publishable")),
        "related": related or {"status": "none"},
        "retrieval_classifier": retrieval_classifier,
        "page_classifier": classifier,
    }


def _exact_probability(assessment: dict[str, Any]) -> float:
    probabilities = assessment.get("probabilities") or {}
    return float(probabilities.get("exact_entity", 0.0))


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def crawl_candidates(
    row: dict[str, Any],
    candidates: list[dict[str, Any]],
    retrieval_assessments: dict[str, dict[str, Any]],
    *,
    classifier: Any,
    timeout: float,
    counts: Counter[str],
    crawl_latencies: list[int],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Fetch and gate candidates in order. Returns (verified website, summaries, first related website)."""
    summaries: list[dict[str, Any]] = []
    verified_website = None
    related_website = None
    for candidate in candidates:
        source = candidate.get("provider") or "unknown"
        website, web_ops = fetch_website(candidate["url"], timeout=timeout)
        crawl_latencies.extend(web_ops.get("latencies_ms", []))
        counts["independent_crawls"] += 1
        counts[f"crawls_{source}"] += 1
        counts["crawl_requests"] += web_ops.get("requests", 0)
        retrieval = retrieval_assessments.get(candidate["url"], {})
        classifier_assessment = classifier.classify(row, _candidate_classifier_input(candidate, website))
        website_value = website.get("value") or {}
        website["value"] = website_value
        website["source_type"] = "search_discovered_company_website" if source == "serper_api" else "registry_derived_company_website"
        gated = apply_website_identity_gate(row, website)
        website = gated["website"]
        first_party_assessment = assess_first_party_ownership(row, website)
        related = assess_related_entity(row, website)
        website_value = website.get("value") or {}
        website["value"] = website_value
        website_value["retrieval_classifier_assessment"] = retrieval
        website_value["classifier_assessment"] = classifier_assessment
        website_value["first_party_assessment"] = first_party_assessment
        summaries.append(_candidate_summary(candidate, website, classifier_assessment, retrieval, first_party_assessment, related))
        assessment = gated["assessment"]
        if assessment and assessment.get("publishable") and first_party_assessment.get("publishable") and website.get("status") == "available":
            verified_website = website
            counts["verified_sites"] += 1
            counts[f"verified_by_{source}"] += 1
            break
        if assessment and assessment.get("publishable") and not first_party_assessment.get("publishable"):
            counts["identity_only_quarantined"] += 1
        if related["status"] == "related":
            counts["related_entities"] += 1
            website_value["related_assessment"] = related
            related_website = related_website or website
        counts["quarantined_sites"] += 1
    return verified_website, summaries, related_website


def search_stage(
    row: dict[str, Any],
    api_key: str,
    args: argparse.Namespace,
    counts: Counter[str],
    provider_latencies: list[int],
    exclude_domains: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    queries = build_company_search_queries(row)
    all_results: list[dict[str, Any]] = []
    query_hashes: list[str] = []
    provider_errors = 0
    for search_query in queries:
        results, operation = serper_search(row, api_key, timeout=args.timeout, count=args.count, query=search_query)
        all_results.extend(results)
        provider_latencies.append(operation["latency_ms"])
        query_hashes.append(operation["query_sha256"])
        counts["provider_requests"] += operation.get("attempts", 1)
        if operation.get("error"):
            provider_errors += 1
            counts[f"provider_error_{operation.get('status')}"] += 1
    counts["provider_errors"] += provider_errors
    decision = choose_search_candidates(row, all_results, limit=args.max_candidates)
    selected = [candidate for candidate in decision["selected"] if candidate.get("registered_domain") not in exclude_domains]
    summary = {
        "provider": "serper_api",
        "query_sha256": query_hashes,
        "provider_requests": len(queries),
        "candidate_count": len(all_results),
        "selected_for_independent_crawl": len(selected),
        "retention_policy": "Search titles, snippets, ranks, query text, and raw response are not persisted.",
    }
    return selected, summary, bool(queries) and provider_errors == len(queries)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Registry-derived and Serper candidate discovery followed by independent exact-entity website verification."
    )
    parser.add_argument("--input", required=True, help="Profile JSONL, normally the competition-batch profile output")
    parser.add_argument("--output", required=True, help="Profile JSONL with discovery evidence")
    parser.add_argument("--report", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--count", type=int, default=10, choices=range(1, 21), metavar="1..20")
    parser.add_argument("--max-candidates", type=int, default=3, choices=range(1, 6), metavar="1..5")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--min-interval", type=float, default=0.1)
    parser.add_argument("--classifier", choices=("rules", "laya"), default="rules")
    parser.add_argument("--api-key-env", default="SERPER_API_KEY")
    parser.add_argument("--promote-verified", action="store_true")
    parser.add_argument("--no-search", action="store_true", help="Use registry-derived candidates only; no search API key needed")
    parser.add_argument("--no-name-domains", action="store_true", help="Do not guess .no domains from the legal name")
    parser.add_argument("--nav-index", help="NAV employer index JSONL from run_nav_jobs_connector.py")
    parser.add_argument("--ledger", help="Candidate ledger JSONL; enables the negative cache")
    parser.add_argument("--negative-ttl-days", type=float, default=30.0)
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit must be positive")
    load_dotenv(ROOT / ".env")
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key and not args.no_search:
        parser.error(f"Missing API key in environment variable {args.api_key_env} (or pass --no-search)")

    classifier = build_candidate_classifier(args.classifier)
    ledger = DiscoveryLedger(Path(args.ledger) if args.ledger else None)
    nav_index = load_nav_index(args.nav_index) if args.nav_index else {}
    rows = read_jsonl(Path(args.input))
    counts: Counter[str] = Counter()
    provider_latencies: list[int] = []
    crawl_latencies: list[int] = []
    started_at = utc_now()
    queried = 0

    for row in rows:
        if queried >= args.limit:
            break
        if row.get("website"):
            counts["registry_website_present_skipped"] += 1
            continue
        org = str(row.get("organisation_number"))
        now = datetime.now(timezone.utc)
        if ledger.should_skip(org, now, args.negative_ttl_days):
            counts["skipped_negative_cache"] += 1
            row.setdefault("evidence", {})["website_discovery"] = evidence(
                "website_discovery", "not_found", "discovery_ledger", REGISTRY_ENDPOINT.format(org=org),
                value={"skipped_negative_cache": True},
                note="Searched within the negative-cache window and nothing credible was found; not re-queried.",
            )
            continue
        queried += 1

        summaries: list[dict[str, Any]] = []
        verified_website = related_website = None
        search_summary: dict[str, Any] = {"provider": None}
        provider_failed = False

        nav_entry = nav_index.get(org)
        if nav_entry:
            counts["nav_employers_matched"] += 1
            row.setdefault("evidence", {})["nav_jobs"] = evidence(
                "nav_jobs", "available", "official_nav_job_feed", FEED_ORIGIN, value=nav_entry,
                note="Employer facts and active ads keyed by the exact organisation number in NAV's public vacancy feed.",
            )
        derived = registry_candidates(row, name_domains=not args.no_name_domains, nav_index=nav_index)
        counts["registry_derived_candidates"] += len(derived)
        if derived:
            verified_website, derived_summaries, related_website = crawl_candidates(
                row, derived, {}, classifier=classifier, timeout=args.timeout, counts=counts, crawl_latencies=crawl_latencies,
            )
            summaries.extend(derived_summaries)

        if verified_website:
            counts["search_skipped_registry_verified"] += 1
        elif not args.no_search:
            selected, search_summary, provider_failed = search_stage(
                row, api_key, args, counts, provider_latencies, {item["registered_domain"] for item in derived},
            )
            if selected:
                retrieval = {candidate["url"]: classifier.classify(row, candidate) for candidate in selected}
                selected.sort(
                    key=lambda candidate: (
                        _exact_probability(retrieval[candidate["url"]]),
                        float(candidate.get("score") or 0.0),
                        -int(candidate.get("rank") or 999),
                    ),
                    reverse=True,
                )
                verified_website, search_summaries, search_related = crawl_candidates(
                    row, selected, retrieval, classifier=classifier, timeout=args.timeout, counts=counts, crawl_latencies=crawl_latencies,
                )
                summaries.extend(search_summaries)
                related_website = related_website or search_related
            time.sleep(args.min_interval)

        if verified_website:
            outcome, status = "verified", "available"
        elif provider_failed and not summaries:
            outcome, status = "provider_failed", "failed"
            counts["provider_failed_companies"] += 1
        elif not summaries:
            outcome, status = "no_candidate", "not_found"
            counts["abstained_before_crawl"] += 1
        else:
            outcome, status = "crawled_no_verified", "not_found"

        row.setdefault("evidence", {})["website_discovery"] = evidence(
            "website_discovery",
            status,
            "registry_derived_and_search_then_independent_crawl",
            SERPER_ENDPOINT if not args.no_search else REGISTRY_ENDPOINT.format(org=org),
            value={**search_summary, "registry_derived_candidates": len(derived), "candidates": summaries},
            note="Candidates are leads only. Publication requires independently fetched exact-entity evidence plus first-party contact/domain corroboration.",
        )
        row["evidence"]["website_discovered_candidates"] = summaries
        if verified_website:
            row["evidence"]["website_discovered"] = verified_website
            if args.promote_verified:
                row["evidence"]["website"] = verified_website
                counts["promoted_sites"] += 1
        elif related_website:
            related_website["relationship"] = (related_website.get("value") or {}).get("related_assessment", {}).get("relationship")
            row["evidence"]["website_related"] = related_website
        ledger.record(org, outcome, summaries, now)

    conflicts = enforce_domain_uniqueness(rows)
    counts["domain_conflicts"] = len(conflicts)
    write_jsonl(Path(args.output), rows)
    ledger.save()
    completed_at = utc_now()
    report = {
        "generated_at": completed_at,
        "started_at": started_at,
        "provider": "Serper API" if not args.no_search else None,
        "provider_endpoint": SERPER_ENDPOINT if not args.no_search else None,
        "classifier": args.classifier,
        "input_profiles": len(rows),
        "queried_missing_website_profiles": queried,
        "counts": dict(counts),
        "domain_conflicts": conflicts,
        "provider_latency_ms": {"p50": percentile(provider_latencies, 0.5), "p95": percentile(provider_latencies, 0.95)},
        "crawl_latency_ms": {"p50": percentile(crawl_latencies, 0.5), "p95": percentile(crawl_latencies, 0.95)},
        "raw_search_results_persisted": False,
        "promote_verified_enabled": args.promote_verified,
        "search_enabled": not args.no_search,
        "name_domains_enabled": not args.no_name_domains,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
