#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import concurrent.futures
import hashlib
import json
import os
import socket
import sys
import threading
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
from norway_company_agent.web.candidates import registry_candidates, should_skip_search_triage  # noqa: E402
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
from norway_company_agent.web.website import HostRequestPolicy, NetworkPreflightError, ResolutionFailureBreaker, _wall_timeout, fetch_website, network_preflight, normalize_homepage, registered_domain  # noqa: E402
from norway_company_agent.search.pool import ProviderPool  # noqa: E402
from norway_company_agent.search.providers import ProviderFatalError as PoolProviderFatalError  # noqa: E402

SERPER_ENDPOINT = "https://google.serper.dev/search"
REGISTRY_ENDPOINT = "https://data.brreg.no/enhetsregisteret/api/enheter/{org}"
RETRYABLE_STATUSES = {0, 429, 500, 502, 503, 504}


class ProviderFatalError(RuntimeError):
    """A provider failure that makes continuing the run misleading or wasteful."""

    def __init__(self, reason: str, *, operation: dict[str, Any] | None = None) -> None:
        self.reason = reason
        self.operation = operation or {}
        super().__init__(reason)


class ReplayCacheMiss(RuntimeError):
    """Raised when replay-only mode would otherwise make a network request."""


def _search_cache_key(query: str, *, gl: str = "no", hl: str = "no", num: int = 10) -> str:
    canonical = json.dumps(
        {"query": query, "gl": gl, "hl": hl, "num": num},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SearchResultCache:
    """Small append-only JSONL cache for deterministic Serper replay."""

    def __init__(self, path: Path | None, *, replay_only: bool = False) -> None:
        self.path = path
        self.replay_only = replay_only
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self.providers: set[str] = set()
        self.hits = 0
        self.misses = 0
        if path and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("key"):
                    self._records[str(record["key"])] = record
                    if record.get("provider"):
                        self.providers.add(str(record["provider"]))

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def get(self, query: str, *, num: int, provider: str | None = None, replay_any: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        if not self.path:
            return None
        key = _search_cache_key(query, num=num)
        with self._lock:
            record = self._records.get(key)
            if record is None:
                self.misses += 1
                return None
            if provider and not replay_any and str(record.get("provider") or "").casefold() not in {provider.casefold(), f"{provider.casefold()}_api"}:
                self.misses += 1
                return None
            self.hits += 1
            operation = {
                "status": 200,
                "latency_ms": 0,
                "bytes": 0,
                "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                "attempts": int(record.get("attempts") or 1),
                "cache_hit": True,
                "provider": record.get("provider"),
                "key_id": record.get("key_id"),
                "storage_allowed": record.get("storage_allowed", True),
            }
            return copy.deepcopy(record.get("results") or []), operation

    def put(
        self,
        query: str,
        results: list[dict[str, Any]],
        *,
        num: int,
        attempts: int = 1,
        provider: str = "serper_api",
        key_id: str | None = None,
        storage_allowed: bool = True,
        urls_only: bool = False,
    ) -> bool:
        if not self.path or not storage_allowed:
            return False
        stored_results = copy.deepcopy(results)
        if urls_only:
            stored_results = [{"url": item.get("url"), "position": item.get("position"), "provider": item.get("provider")} for item in stored_results if item.get("url")]
        record = {
            "key": _search_cache_key(query, num=num),
            "query": query,
            "gl": "no",
            "hl": "no",
            "num": num,
            "results": stored_results,
            "fetched_at": utc_now(),
            "provider": provider,
            "key_id": key_id,
            "storage_allowed": storage_allowed,
            "urls_only": urls_only,
            "attempts": attempts,
        }
        with self._lock:
            if record["key"] in self._records:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._records[record["key"]] = record
            self.providers.add(provider)
        return True

    def require_or_none(self, query: str, *, num: int, provider: str | None = None, replay_any: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        cached = self.get(query, num=num, provider=provider, replay_any=replay_any)
        if cached is None and self.replay_only:
            raise ReplayCacheMiss(f"search cache miss for query {query!r}")
        return cached


class FetchEvidenceCache:
    """Persistent website-evidence cache. The runner never writes it unless requested."""

    def __init__(self, path: Path | None, *, replay_only: bool = False) -> None:
        self.path = path
        self.replay_only = replay_only
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0
        if path and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("key"):
                    self._records[str(record["key"])] = record

    @property
    def enabled(self) -> bool:
        return self.path is not None

    @staticmethod
    def key(url: str) -> str:
        return normalize_homepage(url) or url.casefold()

    def get(self, url: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if not self.path:
            return None
        key = self.key(url)
        with self._lock:
            record = self._records.get(key)
            if record is None:
                self.misses += 1
                return None
            self.hits += 1
            return copy.deepcopy(record.get("website") or {}), copy.deepcopy(record.get("operations") or {})

    def contains(self, url: str) -> bool:
        if not self.path:
            return False
        with self._lock:
            return self.key(url) in self._records

    def put(self, url: str, website: dict[str, Any], operations: dict[str, Any], *, provider: str = "website_fetch") -> None:
        if not self.path:
            return
        key = self.key(url)
        record = {
            "key": key,
            "url": key,
            "website": copy.deepcopy(website),
            "operations": copy.deepcopy(operations),
            "fetched_at": utc_now(),
            "provider": provider,
        }
        with self._lock:
            if key in self._records:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._records[key] = record

    def require_or_none(self, url: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        cached = self.get(url)
        if cached is None and self.replay_only:
            raise ReplayCacheMiss(f"fetch cache miss for URL {url!r}")
        return cached


class ProviderFailureBreaker:
    """Stop after a run-wide streak of provider errors instead of burning credits."""

    def __init__(self, *, threshold: int = 10) -> None:
        self.threshold = threshold
        self.consecutive = 0
        self.total_errors = 0
        self._lock = threading.Lock()

    def observe(self, operation: dict[str, Any]) -> None:
        with self._lock:
            if operation.get("error"):
                self.consecutive += 1
                self.total_errors += 1
                if self.consecutive >= self.threshold:
                    raise ProviderFatalError("provider_error_breaker", operation=operation)
            else:
                self.consecutive = 0


class WebsiteFetchCache:
    """Fetch one registered domain once, sharing the result across workers."""

    def __init__(
        self,
        *,
        failure_breaker: ResolutionFailureBreaker | None = None,
        persistent_cache: FetchEvidenceCache | None = None,
    ) -> None:
        self._condition = threading.Condition()
        self._values: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self._inflight: set[str] = set()
        self.hits = 0
        self.misses = 0
        self.failure_breaker = failure_breaker
        self.persistent_cache = persistent_cache

    def fetch(
        self,
        url: str,
        *,
        timeout: float,
        request_policy: HostRequestPolicy | None = None,
        deadline: float | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        key = registered_domain(url) or url.casefold()
        with self._condition:
            while key in self._inflight:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("company budget expired while waiting for a cached domain")
                self._condition.wait(0.05 if remaining is None else min(0.05, remaining))
            if key in self._values:
                self.hits += 1
                website, operations = self._values[key]
                return copy.deepcopy(website), copy.deepcopy(operations), True
            self._inflight.add(key)
            self.misses += 1
        try:
            persistent = self.persistent_cache.require_or_none(url) if self.persistent_cache else None
            if persistent is not None:
                website, operations = persistent
            else:
                website, operations = fetch_website(url, timeout=timeout, request_policy=request_policy)
                if self.persistent_cache:
                    self.persistent_cache.put(url, website, operations)
            if self.failure_breaker:
                self.failure_breaker.observe(operations)
            with self._condition:
                self._values[key] = (copy.deepcopy(website), copy.deepcopy(operations))
                self._inflight.discard(key)
                self._condition.notify_all()
            return website, operations, False
        except BaseException:
            with self._condition:
                self._inflight.discard(key)
                self._condition.notify_all()
            raise

    def contains(self, url: str) -> bool:
        """Return whether a persistent replay record exists without counting a hit."""
        return bool(self.persistent_cache and self.persistent_cache.contains(url))

    def host_resolves_from_cache(self, host: str) -> bool:
        return self.contains(f"https://{host}/") or self.contains(f"http://{host}/")


def timed_out_result(row: dict[str, Any], *, reason: str = "company time budget exceeded") -> dict[str, Any]:
    result = copy.deepcopy(row)
    result.setdefault("evidence", {})["website_discovery"] = evidence(
        "website_discovery",
        "timed_out",
        "discovery_runner",
        SERPER_ENDPOINT,
        value={"timed_out": True},
        note=reason,
    )
    result["evidence"]["website_discovered_candidates"] = []
    return {"row": result, "outcome": "timed_out", "summaries": [], "counts": Counter({"timed_out_companies": 1})}


def run_worker_pool(
    rows: list[dict[str, Any]],
    worker: Any,
    *,
    workers: int = 8,
    company_timeout: float = 60.0,
    existing: dict[str, Any] | None = None,
    on_complete: Any | None = None,
) -> list[Any]:
    """Run company jobs concurrently, returning results in input order.

    Timed-out futures are deliberately detached from the coordinator so one
    stalled host cannot stop unrelated companies from completing. The network
    layer has its own request timeouts; this is the final per-company guard.
    """
    if workers < 1 or company_timeout <= 0:
        raise ValueError("workers and company_timeout must be positive")
    results = dict(existing or {})
    pending_rows = [row for row in rows if str(row.get("organisation_number")) not in results]
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    futures: dict[concurrent.futures.Future[Any], dict[str, Any]] = {}
    actual_starts: dict[str, float] = {}
    start_lock = threading.Lock()

    def invoke(row: dict[str, Any]) -> Any:
        org = str(row.get("organisation_number"))
        with start_lock:
            actual_starts[org] = time.monotonic()
        return worker(row)

    try:
        for row in pending_rows:
            future = executor.submit(invoke, row)
            futures[future] = row
        active = set(futures)
        while active:
            done, _ = concurrent.futures.wait(active, timeout=0.05, return_when=concurrent.futures.FIRST_COMPLETED)
            now = time.monotonic()
            for future in done:
                active.discard(future)
                row = futures[future]
                started = actual_starts.get(str(row.get("organisation_number")))
                if started is not None and now - started > company_timeout:
                    result = timed_out_result(row)
                else:
                    try:
                        result = future.result()
                    except (NetworkPreflightError, ProviderFatalError, ReplayCacheMiss):
                        raise
                    except Exception as exc:  # a single company must not abort the batch
                        result = search_error_result(row, type(exc).__name__)
                org = str(row.get("organisation_number"))
                results[org] = result
                if on_complete:
                    on_complete(org, result)
            for future in list(active):
                row = futures[future]
                started = actual_starts.get(str(row.get("organisation_number")))
                if started is not None and now - started > company_timeout:
                    active.discard(future)
                    org = str(row.get("organisation_number"))
                    result = timed_out_result(row)
                    results[org] = result
                    if on_complete:
                        on_complete(org, result)
        return [results[str(row.get("organisation_number"))] for row in rows if str(row.get("organisation_number")) in results]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _open_provider(request: urllib.request.Request, timeout: float) -> Any:
    """Keep a provider connection from bypassing urllib's request timeout."""
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    finally:
        socket.setdefaulttimeout(previous)


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
        with _wall_timeout(timeout):
            with _open_provider(request, timeout) as response:
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
    except urllib.error.HTTPError as exc:
        try:
            body_text = exc.read().decode("utf-8", errors="replace")[:1000]
        except Exception:
            body_text = ""
        lowered = body_text.casefold()
        fatal_reason = None
        if "not enough credits" in lowered or "credit" in lowered and exc.code in {400, 402}:
            fatal_reason = "credits_exhausted"
        elif exc.code in {401, 402, 403}:
            fatal_reason = "provider_auth_or_permission"
        return [], {
            "status": getattr(exc, "code", 0),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "bytes": 0,
            "query_sha256": hashlib.sha256(search_query.encode("utf-8")).hexdigest(),
            "error": type(exc).__name__,
            **({"fatal_reason": fatal_reason} if fatal_reason else {}),
        }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
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
    deadline: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One Serper query, retrying rate-limit, server and network errors with exponential backoff."""
    for attempt in range(retries + 1):
        remaining = timeout if deadline is None else deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("company budget expired before search")
        results, operation = _serper_once(profile, api_key, timeout=min(timeout, remaining), count=count, query=query)
        operation["attempts"] = attempt + 1
        if operation.get("fatal_reason"):
            raise ProviderFatalError(str(operation["fatal_reason"]), operation=operation)
        if not operation.get("error") or operation.get("status") not in RETRYABLE_STATUSES or attempt == retries:
            return results, operation
        sleep_for = backoff * (2 ** attempt)
        if deadline is not None:
            sleep_for = min(sleep_for, max(0.0, deadline - time.monotonic()))
        time.sleep(sleep_for)
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
        "matched_url": candidate.get("matched_url"),
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
    request_policy: HostRequestPolicy | None = None,
    website_cache: WebsiteFetchCache | None = None,
    deadline: float | None = None,
    relax_address_gate: bool = False,
    relax_imprint_gate: bool = False,
    istat_gate: bool = False,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None, bool]:
    """Fetch and gate candidates in order. Returns (verified website, summaries, first related website)."""
    summaries: list[dict[str, Any]] = []
    verified_website = None
    related_website = None
    timed_out = False
    for candidate in candidates:
        if deadline is not None and deadline <= time.monotonic():
            timed_out = True
            break
        source = candidate.get("provider") or "unknown"
        try:
            fetch_timeout = timeout if deadline is None else min(timeout, max(0.01, deadline - time.monotonic()))
            if website_cache:
                website, web_ops, cached = website_cache.fetch(
                    candidate.get("crawl_url") or candidate["url"], timeout=fetch_timeout, request_policy=request_policy, deadline=deadline,
                )
                counts["cached_candidate_fetches" if cached else "independent_crawls"] += 1
            else:
                website, web_ops = fetch_website(candidate.get("crawl_url") or candidate["url"], timeout=fetch_timeout, request_policy=request_policy)
                counts["independent_crawls"] += 1
        except TimeoutError:
            timed_out = True
            break
        crawl_latencies.extend(web_ops.get("latencies_ms", []))
        counts[f"crawls_{source}"] += 1
        counts["crawl_requests"] += web_ops.get("requests", 0)
        retrieval = retrieval_assessments.get(candidate["url"], {})
        classifier_assessment = classifier.classify(row, _candidate_classifier_input(candidate, website))
        website_value = website.get("value") or {}
        website["value"] = website_value
        search_sources = {"serper", "serper_api", "serpapi", "tavily", "linkup", "brave", "brave_search_api"}
        website["source_type"] = "search_discovered_company_website" if source in search_sources else "registry_derived_company_website"
        gated = apply_website_identity_gate(row, website)
        website = gated["website"]
        first_party_assessment = assess_first_party_ownership(
            row, website, relax_address_gate=relax_address_gate, relax_imprint_gate=relax_imprint_gate, istat_gate=istat_gate,
        )
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
    return verified_website, summaries, related_website, timed_out


def search_stage(
    row: dict[str, Any],
    api_key: str,
    args: argparse.Namespace,
    counts: Counter[str],
    provider_latencies: list[int],
    exclude_domains: set[str],
    deadline: float | None = None,
    search_cache: SearchResultCache | None = None,
    provider_breaker: ProviderFailureBreaker | None = None,
    provider_pool: ProviderPool | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], bool, bool]:
    queries = build_company_search_queries(row, include_identifier_fallback=True)
    if not getattr(args, "two_queries", False):
        queries = queries[:1]
    all_results: list[dict[str, Any]] = []
    query_hashes: list[str] = []
    provider_records: list[dict[str, Any]] = []
    provider_errors = 0
    timed_out = False
    request_count = 0
    for search_query in queries:
        try:
            cached = search_cache.require_or_none(
                search_query,
                num=args.count,
                provider=getattr(args, "provider", None),
                replay_any=getattr(args, "replay_only", False),
            ) if search_cache else None
            if cached is not None:
                results, operation = cached
            else:
                if provider_pool:
                    try:
                        results, operation = provider_pool.search(
                            search_query, country="no", language="no", count=args.count,
                            timeout=min(args.timeout, max(0.01, deadline - time.monotonic())) if deadline else args.timeout,
                        )
                    except PoolProviderFatalError as exc:
                        raise ProviderFatalError(exc.reason) from exc
                else:
                    results, operation = serper_search(
                        row, api_key, timeout=args.timeout, count=args.count, query=search_query, deadline=deadline,
                    )
                if search_cache:
                    search_cache.put(
                        search_query,
                        results,
                        num=args.count,
                        attempts=int(operation.get("attempts") or 1),
                        provider=str(operation.get("provider") or "serper_api"),
                        key_id=operation.get("key_id"),
                        storage_allowed=bool(operation.get("storage_allowed", True)),
                        urls_only=bool(getattr(args, "cache_urls_only", False)),
                    )
        except TimeoutError:
            timed_out = True
            break
        all_results.extend(results)
        provider_latencies.append(operation.get("latency_ms", 0))
        query_hashes.append(operation["query_sha256"])
        provider_records.append({"provider": operation.get("provider"), "key_id": operation.get("key_id"), "cache_hit": bool(operation.get("cache_hit"))})
        attempts = operation.get("attempts", 1)
        request_count += attempts
        if not operation.get("cache_hit"):
            counts["provider_requests"] += attempts
            counts["queries_used"] += attempts
        if operation.get("error"):
            provider_errors += 1
            counts[f"provider_error_{operation.get('status')}"] += 1
            if provider_breaker:
                provider_breaker.observe(operation)
        decision = choose_search_candidates(row, all_results, limit=args.max_candidates)
        if [candidate for candidate in decision["selected"] if candidate.get("registered_domain") not in exclude_domains]:
            break
    counts["provider_errors"] += provider_errors
    decision = choose_search_candidates(row, all_results, limit=args.max_candidates)
    selected = [candidate for candidate in decision["selected"] if candidate.get("registered_domain") not in exclude_domains]
    summary = {
        "provider": (getattr(args, "provider", None) or "serper_api"),
        "query_sha256": query_hashes,
        "provider_records": provider_records,
        "provider_requests": request_count,
        "queries_used": sum(1 for query in query_hashes),
        "queries_saved_by_triage": 0,
        "candidate_count": len(all_results),
        "selected_for_independent_crawl": len(selected),
        "retention_policy": "Search titles, snippets, ranks and query text are retained only when --search-cache is explicitly enabled; raw provider responses are not persisted.",
    }
    return selected, summary, bool(queries) and provider_errors == len(queries), timed_out


def discover_company(
    original_row: dict[str, Any],
    *,
    args: argparse.Namespace,
    api_key: str,
    classifier: Any,
    nav_index: dict[str, Any],
    request_policy: HostRequestPolicy,
    website_cache: WebsiteFetchCache,
    search_cache: SearchResultCache | None = None,
    provider_breaker: ProviderFailureBreaker | None = None,
    provider_pool: ProviderPool | None = None,
) -> dict[str, Any]:
    """Discover one company and return isolated metrics for the coordinator."""
    row = copy.deepcopy(original_row)
    counts: Counter[str] = Counter()
    provider_latencies: list[int] = []
    crawl_latencies: list[int] = []
    started = datetime.now(timezone.utc)
    deadline = time.monotonic() + args.company_timeout
    org = str(row.get("organisation_number"))
    if row.get("website"):
        if not args.refresh_registry_websites:
            counts["registry_website_present_skipped"] += 1
            return {"row": row, "outcome": "registry_site", "summaries": [], "counts": counts, "provider_latencies": [], "crawl_latencies": []}

    summaries: list[dict[str, Any]] = []
    verified_website = related_website = None
    search_summary: dict[str, Any] = {"provider": None}
    provider_failed = False
    timed_out = False
    nav_entry = nav_index.get(org)
    if nav_entry:
        counts["nav_employers_matched"] += 1
        row.setdefault("evidence", {})["nav_jobs"] = evidence(
            "nav_jobs", "available", "official_nav_job_feed", FEED_ORIGIN, value=nav_entry,
            note="Employer facts and active ads keyed by the exact organisation number in NAV's public vacancy feed.",
        )
    candidate_options: dict[str, Any] = {
        "name_domains": not args.no_name_domains,
        "nav_index": nav_index,
    }
    if args.replay_only:
        candidate_options["resolves"] = website_cache.host_resolves_from_cache
    derived = registry_candidates(row, **candidate_options)
    if args.replay_only:
        derived = [
            candidate for candidate in derived
            if website_cache.contains(candidate.get("crawl_url") or candidate.get("url") or "")
        ]
    if row.get("website") and args.refresh_registry_websites:
        registry_url = normalize_homepage(str(row["website"]))
        registry_candidate = {
            "url": registry_url or str(row["website"]),
            "crawl_url": registry_url or str(row["website"]),
            "registered_domain": registered_domain(registry_url or str(row["website"])),
            "provider": "registry_derived",
            "rank": 0,
            "score": 1.0,
        }
        verified, refreshed_summaries, related, refresh_timed_out = crawl_candidates(
            row, [registry_candidate], {}, classifier=classifier, timeout=args.timeout, counts=counts,
            crawl_latencies=crawl_latencies, request_policy=request_policy, website_cache=website_cache,
            deadline=deadline, relax_address_gate=args.gate in {"g1", "g2"},
            relax_imprint_gate=args.gate == "g2", istat_gate=args.gate == "g3",
        )
        summaries.extend(refreshed_summaries)
        if verified:
            row.setdefault("evidence", {})["website"] = verified
        elif related:
            row.setdefault("evidence", {})["website_related"] = related
        else:
            # A failed refresh must not preserve an old, pre-veto identity
            # assessment. Re-evaluate the evidence already in the profile so a
            # cached/live page with a contradicting number is still unpublished.
            existing_website = (row.get("evidence") or {}).get("website") or {}
            if existing_website.get("status") == "available":
                gated_existing = apply_website_identity_gate(row, existing_website)
                existing_value = gated_existing["website"].get("value") or {}
                existing_value["first_party_assessment"] = assess_first_party_ownership(
                    row, gated_existing["website"], relax_address_gate=args.gate in {"g1", "g2"},
                    relax_imprint_gate=args.gate == "g2", istat_gate=args.gate == "g3",
                )
                gated_existing["website"]["value"] = existing_value
                row.setdefault("evidence", {})["website"] = gated_existing["website"]
        timed_out = refresh_timed_out
        if verified:
            counts["registry_sites_refreshed"] += 1
        elif not refresh_timed_out:
            counts["registry_sites_refresh_failed"] += 1
        # A registry-listed URL is a complete discovery observation even when
        # its refreshed fetch is unavailable; scoring will inspect its status.
        if refresh_timed_out:
            status, outcome = "timed_out", "timed_out"
        else:
            status, outcome = "available", "registry_site"
        row.setdefault("evidence", {})["website_discovery"] = evidence(
            "website_discovery", status, "registry_website_refresh", REGISTRY_ENDPOINT.format(org=org),
            value={"registry_derived_candidates": 1, "candidates": summaries},
            note="Registry-listed website re-fetched through the same identity and first-party gates.",
        )
        row["evidence"]["website_discovered_candidates"] = summaries
        return {"row": row, "outcome": outcome, "summaries": summaries, "counts": counts, "provider_latencies": [], "crawl_latencies": crawl_latencies}
    counts["registry_derived_candidates"] += len(derived)
    if derived:
        verified_website, derived_summaries, related_website, timed_out = crawl_candidates(
            row, derived, {}, classifier=classifier, timeout=args.timeout, counts=counts, crawl_latencies=crawl_latencies,
            request_policy=request_policy, website_cache=website_cache, deadline=deadline,
            relax_address_gate=args.gate in {"g1", "g2"}, relax_imprint_gate=args.gate == "g2", istat_gate=args.gate == "g3",
        )
        summaries.extend(derived_summaries)

    if verified_website:
        counts["search_skipped_registry_verified"] += 1
    elif not timed_out and not args.no_search and should_skip_search_triage(row, nav_index):
        counts["search_skipped_triage"] += 1
        counts["queries_saved_by_triage"] += 2 if getattr(args, "two_queries", False) else 1
        row.setdefault("evidence", {})["website_discovery"] = evidence(
            "website_discovery", "not_found", "search_triage", REGISTRY_ENDPOINT.format(org=org),
            value={"search_skipped": "triage", "triage_reason": "S2/S3 profile without registry/NAV website signal"},
            note="Paid search was skipped for a low-yield property, holding, or unspecified-activity profile.",
        )
        row["evidence"]["website_discovered_candidates"] = summaries
        return {"row": row, "outcome": "search_skipped_triage", "summaries": summaries, "counts": counts, "provider_latencies": provider_latencies, "crawl_latencies": crawl_latencies}
    elif not timed_out and not args.no_search:
        selected, search_summary, provider_failed, timed_out = search_stage(
            row, api_key, args, counts, provider_latencies, {item["registered_domain"] for item in derived}, deadline=deadline,
            search_cache=search_cache, provider_breaker=provider_breaker, provider_pool=provider_pool,
        )
        if selected and not timed_out:
            retrieval = {candidate["url"]: classifier.classify(row, candidate) for candidate in selected}
            selected.sort(
                key=lambda candidate: (
                    _exact_probability(retrieval[candidate["url"]]),
                    float(candidate.get("score") or 0.0),
                    -int(candidate.get("rank") or 999),
                ),
                reverse=True,
            )
            verified_website, search_summaries, search_related, crawl_timed_out = crawl_candidates(
                row, selected, retrieval, classifier=classifier, timeout=args.timeout, counts=counts, crawl_latencies=crawl_latencies,
                request_policy=request_policy, website_cache=website_cache, deadline=deadline,
                relax_address_gate=args.gate in {"g1", "g2"}, relax_imprint_gate=args.gate == "g2", istat_gate=args.gate == "g3",
            )
            timed_out = timed_out or crawl_timed_out
            summaries.extend(search_summaries)
            related_website = related_website or search_related
        if not timed_out:
            remaining = max(0.0, min(args.min_interval, deadline - time.monotonic()))
            time.sleep(remaining)

    if timed_out or time.monotonic() >= deadline:
        status, outcome = "timed_out", "timed_out"
        counts["timed_out_companies"] += 1
    elif verified_website:
        status, outcome = "available", "verified"
    elif provider_failed and not summaries:
        status, outcome = "failed", "search_error"
        counts["provider_failed_companies"] += 1
        counts["search_error_companies"] += 1
    elif not summaries:
        status, outcome = "not_found", "no_candidate"
        counts["abstained_before_crawl"] += 1
    else:
        status, outcome = "not_found", "crawled_no_verified"

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
    return {
        "row": row,
        "outcome": outcome,
        "summaries": summaries,
        "counts": counts,
        "provider_latencies": provider_latencies,
        "crawl_latencies": crawl_latencies,
        "started": started.isoformat(),
    }


def search_error_result(row: dict[str, Any], error: str) -> dict[str, Any]:
    result = copy.deepcopy(row)
    result.setdefault("evidence", {})["website_discovery"] = evidence(
        "website_discovery",
        "failed",
        "discovery_runner",
        SERPER_ENDPOINT,
        value={"search_error": True, "error": error},
        note="The discovery worker raised an exception; this is not a correct abstention.",
    )
    result["evidence"]["website_discovered_candidates"] = []
    return {"row": result, "outcome": "search_error", "summaries": [], "counts": Counter({"search_error_companies": 1}), "error": error}


def _row_has_discovery(row: dict[str, Any]) -> bool:
    return bool(row.get("website") or (row.get("evidence") or {}).get("website_discovery"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Registry-derived and provider-neutral search discovery followed by independent exact-entity website verification."
    )
    parser.add_argument("--input", required=True, help="Profile JSONL, normally the competition-batch profile output")
    parser.add_argument("--output", required=True, help="Profile JSONL with discovery evidence")
    parser.add_argument("--report", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--count", type=int, default=10, choices=range(1, 21), metavar="1..20")
    parser.add_argument("--max-candidates", type=int, default=3, choices=range(1, 6), metavar="1..5")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--company-timeout", type=float, default=60.0, help="Maximum seconds allowed for one company")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent company workers")
    parser.add_argument("--min-interval", type=float, default=0.1)
    parser.add_argument("--classifier", choices=("rules", "laya"), default="rules")
    parser.add_argument("--api-key-env", default="SERPER_API_KEY")
    parser.add_argument("--provider", help="Pin one provider for reproducible evaluation: serper, serpapi, tavily, linkup, or brave")
    parser.add_argument("--rotation", choices=("weighted", "priority", "round_robin"), default="weighted")
    parser.add_argument("--provider-config", default=str(ROOT / "data" / "search-providers.json"))
    parser.add_argument("--provider-usage", default="out/provider-usage.json")
    parser.add_argument("--max-queries", type=int)
    parser.add_argument("--max-queries-per-provider", type=int)
    parser.add_argument("--two-queries", action="store_true", help="Allow the organisation-number fallback query after the local query returns no crawl candidate")
    parser.add_argument("--promote-verified", action="store_true")
    parser.add_argument("--no-search", action="store_true", help="Use registry-derived candidates only; no search API key needed")
    parser.add_argument("--no-name-domains", action="store_true", help="Do not guess .no domains from the legal name")
    parser.add_argument("--refresh-registry-websites", action="store_true", help="Re-fetch registry-listed websites through the current gate")
    parser.add_argument("--relax-address-gate", action="store_true", help="Apply the measured exact-identity/Istat contact rule when publishing")
    parser.add_argument("--gate", choices=("g0", "g1", "g2", "g3"), default="g0", help="Gate variant: g0 current, g1 relaxed address, g2 imprint plus municipality, g3 Istat strong/weak evidence")
    parser.add_argument("--search-cache", help="JSONL cache for normalized search results; enables paid-result recording")
    parser.add_argument("--cache-urls-only", action="store_true", help="Cache result URLs and positions only; omit titles and snippets")
    parser.add_argument("--fetch-cache", help="JSONL cache for website evidence; enables deterministic crawl replay")
    parser.add_argument("--replay-only", action="store_true", help="Refuse every search/fetch cache miss instead of using the network")
    parser.add_argument("--nav-index", help="NAV employer index JSONL from run_nav_jobs_connector.py")
    parser.add_argument("--ledger", help="Candidate ledger JSONL; enables the negative cache")
    parser.add_argument("--negative-ttl-days", type=float, default=30.0)
    parser.add_argument("--resume", action="store_true", help="Resume from completed rows already in --output")
    args = parser.parse_args()

    if args.limit < 1 or args.workers < 1 or args.timeout <= 0 or args.company_timeout <= 0:
        parser.error("limit, workers, timeout, and company-timeout must be positive")
    load_dotenv(ROOT / ".env")
    api_key = os.environ.get(args.api_key_env, "").strip()
    if args.relax_address_gate and args.gate == "g0":
        args.gate = "g1"
    if args.replay_only and not args.no_search and not args.search_cache:
        parser.error("--replay-only requires --search-cache when search is enabled")
    if args.replay_only and not args.fetch_cache:
        parser.error("--replay-only requires --fetch-cache")
    if args.replay_only:
        preflight = {"mode": "replay_only", "checked_at": time.time()}
    else:
        try:
            preflight = network_preflight()
        except NetworkPreflightError as exc:
            parser.error(str(exc))

    search_cache = SearchResultCache(Path(args.search_cache) if args.search_cache else None, replay_only=args.replay_only)
    fetch_cache = FetchEvidenceCache(Path(args.fetch_cache) if args.fetch_cache else None, replay_only=args.replay_only)
    if args.search_cache or args.fetch_cache or args.replay_only:
        print(json.dumps({
            "cache_mode": "replay_only" if args.replay_only else "record_on_miss",
            "search_cache": args.search_cache,
            "fetch_cache": args.fetch_cache,
        }, ensure_ascii=False))

    classifier = build_candidate_classifier(args.classifier)
    ledger = DiscoveryLedger(Path(args.ledger) if args.ledger else None)
    nav_index = load_nav_index(args.nav_index) if args.nav_index else {}
    provider_pool: ProviderPool | None = None
    if not args.no_search and not args.replay_only:
        try:
            provider_config = ProviderPool.load_config(Path(args.provider_config))
            provider_pool = ProviderPool.from_environment(
                config=provider_config,
                usage_path=Path(args.provider_usage) if args.provider_usage else None,
                rotation=args.rotation,
                provider_pin=args.provider,
                legacy_serper_key=api_key,
                max_queries=args.max_queries,
                max_queries_per_provider=args.max_queries_per_provider,
            )
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    rows = read_jsonl(Path(args.input))
    input_orgs = [str(row.get("organisation_number")) for row in rows]
    if len(input_orgs) != len(set(input_orgs)):
        parser.error("input contains duplicate organisation numbers")
    output_path = Path(args.output)
    existing: dict[str, dict[str, Any]] = {}
    if args.resume and output_path.exists():
        prior = read_jsonl(output_path)
        prior_orgs = [str(row.get("organisation_number")) for row in prior]
        if len(prior_orgs) != len(set(prior_orgs)) or not set(prior_orgs) <= set(input_orgs):
            parser.error("resume output contains duplicate or out-of-input organisation numbers")
        existing = {str(row["organisation_number"]): row for row in prior if _row_has_discovery(row)}
    counts: Counter[str] = Counter()
    provider_latencies: list[int] = []
    crawl_latencies: list[int] = []
    started_at = utc_now()
    state: dict[str, dict[str, Any]] = dict(existing)
    counts["resumed_companies"] = len(existing)
    selected: list[dict[str, Any]] = []
    query_slots = 0
    estimated_queries = 0
    for original in rows:
        org = str(original.get("organisation_number"))
        if org in state:
            continue
        if original.get("website") and not args.refresh_registry_websites:
            state[org] = copy.deepcopy(original)
            counts["registry_website_present_skipped"] += 1
            continue
        now = datetime.now(timezone.utc)
        if ledger.should_skip(org, now, args.negative_ttl_days):
            cached = copy.deepcopy(original)
            cached.setdefault("evidence", {})["website_discovery"] = evidence(
                "website_discovery", "not_found", "discovery_ledger", REGISTRY_ENDPOINT.format(org=org),
                value={"skipped_negative_cache": True},
                note="Searched within the negative-cache window and nothing credible was found; not re-queried.",
            )
            cached["evidence"]["website_discovered_candidates"] = []
            state[org] = cached
            counts["skipped_negative_cache"] += 1
            continue
        if query_slots >= args.limit:
            counts["not_queried_due_limit"] += 1
            continue
        selected.append(original)
        query_slots += 1
        if not should_skip_search_triage(original, nav_index):
            estimated_queries += 2 if args.two_queries else 1

    request_policy = HostRequestPolicy(min_interval=1.0, max_inflight=2)
    failure_breaker = ResolutionFailureBreaker()
    website_cache = WebsiteFetchCache(failure_breaker=failure_breaker, persistent_cache=fetch_cache if fetch_cache.enabled else None)
    provider_breaker = ProviderFailureBreaker()

    def checkpoint(org: str, result: dict[str, Any]) -> None:
        state[org] = result["row"]
        counts.update(result.get("counts") or {})
        provider_latencies.extend(result.get("provider_latencies") or [])
        crawl_latencies.extend(result.get("crawl_latencies") or [])
        if args.ledger:
            ledger.record(org, result.get("outcome", "search_error"), result.get("summaries") or [], datetime.now(timezone.utc))
        write_jsonl(output_path, [state[item] for item in input_orgs if item in state])

    def worker(row: dict[str, Any]) -> dict[str, Any]:
        return discover_company(
            row, args=args, api_key=api_key, classifier=classifier, nav_index=nav_index,
            request_policy=request_policy, website_cache=website_cache,
            search_cache=search_cache if search_cache.enabled else None,
            provider_breaker=provider_breaker,
            provider_pool=provider_pool,
        )

    # run_worker_pool catches worker errors itself; the callback writes every
    # completed row, preserving input order in each atomic checkpoint.
    provider_fatal: str | None = None
    provider_fatal_detail: str | None = None
    try:
        run_worker_pool(
            selected,
            worker,
            workers=args.workers,
            company_timeout=args.company_timeout,
            existing={},
            on_complete=checkpoint,
        )
    except ProviderFatalError as exc:
        provider_fatal = exc.reason
        provider_fatal_detail = str(exc)
    except ReplayCacheMiss as exc:
        provider_fatal = "replay_cache_miss"
        provider_fatal_detail = str(exc)

    final_rows = [state.get(org, copy.deepcopy(row)) for org, row in zip(input_orgs, rows)]
    conflicts = enforce_domain_uniqueness(final_rows)
    counts["domain_conflicts"] = len(conflicts)
    failure_counts = Counter()
    for row in final_rows:
        status = ((row.get("evidence") or {}).get("website_discovery") or {}).get("status")
        if status == "timed_out":
            failure_counts["timed_out_companies"] += 1
        elif status == "failed":
            failure_counts["search_error_companies"] += 1
    for name, value in failure_counts.items():
        counts[name] = value
    write_jsonl(output_path, final_rows)
    ledger.save()
    completed_at = utc_now()
    complete = all(_row_has_discovery(row) for row in final_rows)
    report = {
        "generated_at": completed_at,
        "started_at": started_at,
        "provider": (args.provider or "rotating_pool") if not args.no_search else None,
        "provider_endpoint": SERPER_ENDPOINT if (not args.no_search and not provider_pool) else None,
        "provider_selection": {
            "provider_pin": args.provider,
            "rotation": "off" if args.provider else args.rotation,
            "estimated_queries_after_triage": estimated_queries,
            "two_queries": args.two_queries,
        },
        "classifier": args.classifier,
        "input_profiles": len(rows),
        "completed_profiles": sum(_row_has_discovery(row) for row in final_rows),
        "pending_profiles": sum(not _row_has_discovery(row) for row in final_rows),
        "complete": complete,
        "provider_fatal": provider_fatal,
        "provider_fatal_detail": provider_fatal_detail,
        "queried_missing_website_profiles": sum(
            not row.get("website") and bool((row.get("evidence") or {}).get("website_discovery")) for row in final_rows
        ),
        "counts": dict(counts),
        "queries_used": (provider_pool.report().get("queries_used") if provider_pool else counts.get("queries_used", 0)),
        "queries_saved_by_triage": counts.get("queries_saved_by_triage", 0),
        "failures": dict(failure_counts),
        "workers": args.workers,
        "company_timeout_seconds": args.company_timeout,
        "resumed": args.resume,
        "website_cache": {"hits": website_cache.hits, "misses": website_cache.misses},
        "search_cache": {"enabled": search_cache.enabled, "hits": search_cache.hits, "misses": search_cache.misses},
        "provider_pool": provider_pool.report() if provider_pool else None,
        "fetch_cache": {"enabled": fetch_cache.enabled, "hits": fetch_cache.hits, "misses": fetch_cache.misses},
        "domain_conflicts": conflicts,
        "provider_latency_ms": {"p50": percentile(provider_latencies, 0.5), "p95": percentile(provider_latencies, 0.95)},
        "crawl_latency_ms": {"p50": percentile(crawl_latencies, 0.5), "p95": percentile(crawl_latencies, 0.95)},
        "raw_search_results_persisted": bool(search_cache.enabled and not args.cache_urls_only),
        "cached_search_providers": sorted(search_cache.providers),
        "cache_urls_only": args.cache_urls_only,
        "promote_verified_enabled": args.promote_verified,
        "search_enabled": not args.no_search,
        "name_domains_enabled": not args.no_name_domains,
        "relax_address_gate": args.relax_address_gate,
        "gate": args.gate,
        "network_preflight": preflight,
        "website_failure_counts": {
            "total": failure_breaker.total_fetches,
            **dict(failure_breaker.failure_counts),
        },
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if provider_fatal:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
