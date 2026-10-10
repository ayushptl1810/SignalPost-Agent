#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
import resource
import signal
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.registry.batch import profile_complete_for_modules, profiles_from_bulk, read_organisation_inputs, terminal_envelope, validate_envelopes  # noqa: E402
from norway_company_agent.core.evidence import evidence, utc_now  # noqa: E402
from norway_company_agent.core.refresh import diff_datasets  # noqa: E402
from norway_company_agent.cache import CacheLookup, merge_cache_profile  # noqa: E402
from norway_company_agent.core.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.discovery import discover_profile, enforce_domain_uniqueness  # noqa: E402
from norway_company_agent.external.nav_jobs import FEED_SOURCE_URL, LIVE_DETAIL_TIMEOUT, MAX_LIVE_ADS_PER_COMPANY, NavFeedClient, collect as collect_nav_jobs, load_index  # noqa: E402
from norway_company_agent.registry.official import fetch_official_modules  # noqa: E402
from norway_company_agent.web.first_party import assess_first_party_ownership  # noqa: E402
from norway_company_agent.web.first_party import assess_g4_ownership  # noqa: E402
from norway_company_agent.site_facts import attach_site_facts, enrich_site_facts  # noqa: E402
from norway_company_agent.search.providers import ProviderError, SerperSearchProvider  # noqa: E402
from norway_company_agent.web.website import HostRequestPolicy, NetworkPreflightError, ResolutionFailureBreaker, fetch_website, network_failure_class, network_preflight, registered_domain, resolve_host  # noqa: E402

PHASE_NAMES = ("discovery", "activity", "hiring", "brief", "dns", "fetch", "parse")
DEFAULT_RUN_BUDGET_SECONDS = 1500.0
SECOND_CHANCE_FAILURE_KINDS = frozenset({"timeout", "connect", "resolution"})
SECOND_CHANCE_STRONG_SOURCES = frozenset({
    "registry_website",
    "registry_email_domain",
    "nav_employer_homepage",
    "subunit_website_or_email_domain",
})
SECOND_CHANCE_NAME_LIMIT = 3


def resolve_run_budget(flag_value: float | None, env_value: str | None) -> tuple[float | None, str]:
    """Resolve the explicit flag, environment override, or safe default; zero or negative disables it."""
    if flag_value is not None:
        value, source = float(flag_value), "flag"
    elif env_value is not None and str(env_value).strip():
        value, source = float(env_value), "env"
    else:
        value, source = DEFAULT_RUN_BUDGET_SECONDS, "default"
    return (value if value > 0 else None), source


def runtime_defaults() -> dict[str, int]:
    """Return process and network-worker defaults without coupling workers to CPUs."""
    cpu_count = max(1, os.cpu_count() or 1)
    return {
        "cpu_count": cpu_count,
        "workers": int(os.environ.get("SIGNALPOST_WORKERS") or 16),
        "discovery_processes": min(8, max(2, cpu_count)),
    }


def max_rss_mb() -> dict[str, float]:
    """Return peak resident memory for this process and its children."""
    self_rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    children_rss = float(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    self_mb = self_rss / divisor
    children_mb = children_rss / divisor
    return {
        "max_rss_mb": round(self_mb + children_mb, 3),
        "self_mb": round(self_mb, 3),
        "children_mb": round(children_mb, 3),
    }


def _add_phase_timings(target: dict, source: dict | None) -> None:
    timings = target.setdefault("phase_timings_ms", {phase: 0.0 for phase in PHASE_NAMES})
    for phase in PHASE_NAMES:
        timings[phase] = round(float(timings.get(phase) or 0.0) + float((source or {}).get(phase) or 0.0), 3)


def _run_second_chance_with_cap(
    orgs: list[str],
    retry: Callable[[str], tuple],
    *,
    cap_seconds: float,
    run_deadline: float | None,
    workers: int = 4,
    phase_deadline: float | None = None,
) -> tuple[list[tuple], dict[str, Any]]:
    """Run at most four retries at once without waiting past the phase wall cap."""
    started = time.monotonic()
    cap_deadline = phase_deadline if phase_deadline is not None else started + max(0.0, float(cap_seconds))
    next_index = 0
    active: dict[Any, str] = {}
    completed: list[tuple] = []
    cap_hit = False
    pool = ThreadPoolExecutor(max_workers=max(1, workers))

    def collect_done() -> None:
        for future in list(active):
            if not future.done():
                continue
            org = active.pop(future)
            try:
                completed.append(future.result())
            except Exception as exc:  # noqa: BLE001 - one retry must not erase completed recoveries
                completed.append((org, {"states": {}}, {"error": type(exc).__name__}))

    try:
        while True:
            while next_index < len(orgs) and len(active) < max(1, workers):
                now = time.monotonic()
                if now >= cap_deadline or (run_deadline is not None and now >= run_deadline):
                    cap_hit = now >= cap_deadline
                    break
                org = orgs[next_index]
                next_index += 1
                active[pool.submit(retry, org)] = org
            if not active:
                if next_index < len(orgs) and time.monotonic() >= cap_deadline:
                    cap_hit = True
                break
            remaining = cap_deadline - time.monotonic()
            if run_deadline is not None:
                remaining = min(remaining, run_deadline - time.monotonic())
            if remaining <= 0:
                cap_hit = cap_hit or time.monotonic() >= cap_deadline
                collect_done()
                break
            done, _pending = wait(list(active), timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                cap_hit = cap_hit or time.monotonic() >= cap_deadline
                collect_done()
                break
            for future in done:
                org = active.pop(future)
                try:
                    completed.append(future.result())
                except Exception as exc:  # noqa: BLE001 - one retry must not erase completed recoveries
                    completed.append((org, {"states": {}}, {"error": type(exc).__name__}))
    finally:
        pool.shutdown(wait=not active and not cap_hit, cancel_futures=bool(active or cap_hit))
    return completed, {
        "attempted": next_index,
        "skipped": max(0, len(orgs) - next_index),
        "cap_hit": cap_hit,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def _failed_name_candidates(item: dict) -> list[dict]:
    candidates: list[dict] = []
    for candidate in (item.get("discovery") or {}).get("candidates") or []:
        if not isinstance(candidate, dict) or candidate.get("state") not in {"failed", "not_available"}:
            continue
        if not str(candidate.get("source") or "").startswith("name_derived"):
            continue
        candidates.append(candidate)
        if len(candidates) >= SECOND_CHANCE_NAME_LIMIT:
            break
    return candidates


def _second_chance_retry_candidates(item: dict, refreshed_name_hosts: dict[str, dict] | None = None) -> list[dict]:
    """Return candidates in strong, timeout/connect, then re-resolved order."""
    refreshed_name_hosts = refreshed_name_hosts or {}
    strong: list[dict] = []
    timeout_or_connect: list[dict] = []
    re_resolved: list[dict] = []
    discovery = item.get("discovery") or {}
    for candidate in discovery.get("candidates") or []:
        if not isinstance(candidate, dict) or candidate.get("state") not in {"failed", "not_available"}:
            continue
        source = str(candidate.get("source") or "")
        name_derived = source.startswith("name_derived")
        if source in SECOND_CHANCE_STRONG_SOURCES:
            strong.append(candidate)
        elif candidate.get("failure_kind") in {"timeout", "connect"}:
            timeout_or_connect.append(candidate)
        elif name_derived and (refreshed_name_hosts.get(_candidate_host(candidate)) or {}).get("resolved"):
            re_resolved.append(candidate)
    selected: list[dict] = []
    name_count = 0
    seen: set[tuple[str, str]] = set()
    for candidate in [*strong, *timeout_or_connect, *re_resolved]:
        host = _candidate_host(candidate)
        marker = (str(candidate.get("source") or ""), host or str(id(candidate)))
        if marker in seen:
            continue
        if str(candidate.get("source") or "").startswith("name_derived"):
            if name_count >= SECOND_CHANCE_NAME_LIMIT:
                continue
            name_count += 1
        seen.add(marker)
        selected.append(candidate)
    return selected


def _second_chance_retry_orgs(state: dict[str, dict], refreshed_name_hosts: dict[str, dict[str, dict]] | None = None) -> list[str]:
    """Select unpublished profiles by failure kind, retaining strong sources."""
    buckets = {"strong_source": [], "timeout_connect": [], "reresolved_host": []}
    for org, item in state.items():
        discovery = item.get("discovery") or {}
        website = (item.get("evidence") or {}).get("website") or {}
        if discovery.get("state") == "available" or website.get("status") == "available":
            continue
        refreshed = (refreshed_name_hosts or {}).get(org) or {}
        candidates = _second_chance_retry_candidates(item, refreshed)
        if not candidates:
            continue
        if any(str(candidate.get("source") or "") in SECOND_CHANCE_STRONG_SOURCES for candidate in candidates):
            buckets["strong_source"].append(org)
        elif any(candidate.get("failure_kind") in {"timeout", "connect"} for candidate in candidates):
            buckets["timeout_connect"].append(org)
        else:
            buckets["reresolved_host"].append(org)
    return [org for reason in ("strong_source", "timeout_connect", "reresolved_host") for org in buckets[reason]]


def _second_chance_selection_reason_counts(
    state: dict[str, dict],
    retry_orgs: list[str],
    refreshed_name_hosts: dict[str, dict[str, dict]] | None = None,
) -> dict[str, int]:
    counts = {reason: 0 for reason in ("strong_source", "timeout_connect", "reresolved_host")}
    for org in retry_orgs:
        candidates = _second_chance_retry_candidates(state[org], (refreshed_name_hosts or {}).get(org) or {})
        if any(str(candidate.get("source") or "") in SECOND_CHANCE_STRONG_SOURCES for candidate in candidates):
            counts["strong_source"] += 1
        elif any(candidate.get("failure_kind") in {"timeout", "connect"} for candidate in candidates):
            counts["timeout_connect"] += 1
        elif candidates:
            counts["reresolved_host"] += 1
    return counts


def _second_chance_unpublished_orgs(state: dict[str, dict]) -> list[str]:
    return [
        org for org, item in state.items()
        if (item.get("discovery") or {}).get("state") != "available"
        and ((item.get("evidence") or {}).get("website") or {}).get("status") != "available"
    ]


def _candidate_host(candidate: dict) -> str:
    return (urllib.parse.urlparse(str(candidate.get("requested_url") or candidate.get("url") or "")).hostname or "").casefold().rstrip(".")


def _reresolve_second_chance_name_hosts(
    state: dict[str, dict],
    retry_orgs: list[str],
    *,
    workers: int = 8,
    timeout: float = 1.0,
    deadline: float | None = None,
) -> dict[str, dict[str, dict]]:
    """Re-resolve selected name-derived hosts once before the low-concurrency retry."""
    hosts_by_org: dict[str, set[str]] = {}
    hosts: set[str] = set()
    for org in retry_orgs:
        selected = hosts_by_org.setdefault(org, set())
        for candidate in _failed_name_candidates(state[org]):
            host = _candidate_host(candidate)
            if host:
                selected.add(host)
                hosts.add(host)
    resolved_by_host: dict[str, dict] = {}
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    deadline_hit = False
    try:
        futures = {host: pool.submit(resolve_host, host, timeout=timeout) for host in sorted(hosts)}
        for host, future in futures.items():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                deadline_hit = True
                break
            try:
                resolved_by_host[host] = future.result(timeout=remaining)
            except TimeoutError:
                deadline_hit = True
                break
            except OSError as exc:
                resolved_by_host[host] = {
                    "resolved": False,
                    "failure_kind": "resolution",
                    "failure_class": network_failure_class(exc) or "permanent",
                    "error": str(exc),
                }
    finally:
        pool.shutdown(wait=not deadline_hit, cancel_futures=deadline_hit)
    return {
        org: {host: resolved_by_host[host] for host in sorted(org_hosts) if host in resolved_by_host}
        for org, org_hosts in hosts_by_org.items()
    }


def _second_chance_resolution_overrides(item: dict, refreshed: dict[str, dict]) -> dict[str, dict]:
    """Force the retry to honor the bounded name-host refresh and its permanent skips."""
    overrides: dict[str, dict] = {}
    for candidate in _failed_name_candidates(item):
        host = _candidate_host(candidate)
        if not host:
            continue
        overrides[host] = refreshed.get(host) or {
            "resolved": False,
            "failure_kind": "resolution",
            "failure_class": "permanent",
        }
    return overrides


def _second_chance_failure_kind_counts(
    state: dict[str, dict],
    retry_orgs: list[str],
    refreshed_name_hosts: dict[str, dict[str, dict]] | None = None,
) -> dict[str, int]:
    counts = {kind: 0 for kind in sorted(SECOND_CHANCE_FAILURE_KINDS)}
    for org in retry_orgs:
        for candidate in _second_chance_retry_candidates(state[org], (refreshed_name_hosts or {}).get(org) or {}):
            kind = candidate.get("failure_kind")
            if kind in counts:
                counts[kind] += 1
    return counts


def _discovery_published(discovery: dict, *, gate: str) -> bool:
    """Return true only when the retry produced publishable website evidence."""
    containers = [discovery.get("website") or {}]
    if gate == "g4":
        containers.append(discovery.get("website_g4") or {})
    return any((container.get("evidence") or {}).get("status") == "available" for container in containers)


def _read_search_fill_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("organisation_number"):
            rows[str(row["organisation_number"])] = row
    return rows


def _write_search_fill_cache(path: Path, rows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(rows[key], ensure_ascii=False, separators=(",", ":")) + "\n" for key in sorted(rows)), encoding="utf-8")
    os.replace(temporary, path)


def _directory_search_result(url: str) -> bool:
    domain = registered_domain(url)
    return domain in {"proff.no", "1881.no", "gulesider.no", "purehelp.no", "brreg.no", "facebook.com", "linkedin.com"}


def run_search_fill(
    profile: dict,
    *,
    provider: SerperSearchProvider,
    cached_row: dict | None = None,
    search_cache_row: dict | None = None,
    timeout: float = 30.0,
    request_policy: HostRequestPolicy | None = None,
) -> tuple[dict | None, dict]:
    """Search one cache miss and route at most three results through G4."""
    name = str(profile.get("name") or "").strip()
    municipality = str(profile.get("municipality") or "").strip()
    query = f'"{name}" {municipality}'.strip()
    operation = {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0, "search_queries": 0, "query": query}
    if search_cache_row is not None:
        results = search_cache_row.get("results") or []
    else:
        try:
            results, provider_operation = provider.search(query, country="no", language="no", count=3, timeout=timeout)
            operation.update({"bytes": int(provider_operation.get("bytes") or 0), "latencies_ms": [int(provider_operation.get("latency_ms") or 0)], "search_queries": 1, "third_party_cost_usd": 0.001, "provider": "serper"})
        except ProviderError as exc:
            operation.update({"search_queries": 1, "third_party_cost_usd": 0.001, "provider": "serper", "error": type(exc).__name__})
            return None, operation
    operation["raw_results"] = results
    for result in results[:3]:
        url = str(result.get("url") or "")
        if not url or _directory_search_result(url):
            continue
        evidence, metrics = fetch_website(url, timeout=timeout, request_policy=request_policy, max_secondary_pages=2)
        operation["requests"] += int(metrics.get("requests") or 0)
        operation["bytes"] += int(metrics.get("bytes") or 0)
        operation["latencies_ms"].extend(metrics.get("latencies_ms") or [])
        gated = apply_website_identity_gate(profile, evidence)["website"]
        g4 = assess_g4_ownership(profile, gated, candidate_source="search_fill")
        if g4.get("publishable"):
            return {"evidence": gated, "identity_gate": gated.get("value", {}).get("identity_assessment"), "first_party_gate": g4}, operation
    return None, operation


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            # Escape U+2028/U+2029 as well as ordinary non-ASCII so a JSON
            # string can never be split by text tools treating line separators
            # as JSONL boundaries.
            handle.write(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def _fresh_nav_index(path: str | None) -> tuple[dict[str, dict], dict | None]:
    """Load a complete NAV snapshot, retaining an explicit >14-day stale flag."""
    if not path:
        return {}, None
    index_path = Path(path)
    meta_path = index_path.with_suffix(index_path.suffix + ".meta.json")
    if not index_path.exists() or not meta_path.exists():
        return {}, None
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        built_at = datetime.fromisoformat(str(metadata.get("built_at", "")).replace("Z", "+00:00"))
        if built_at.tzinfo is None:
            built_at = built_at.replace(tzinfo=timezone.utc)
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return {}, None
    age_seconds = (datetime.now(timezone.utc) - built_at).total_seconds()
    if not metadata.get("complete") or age_seconds < 0:
        return {}, metadata
    metadata = {**metadata, "stale": age_seconds > 14 * 86400}
    index = load_index(index_path)
    index["_meta"] = metadata
    return index, metadata


def _nav_slice(index: dict[str, dict], organisation_number: str) -> dict[str, dict]:
    row = index.get(str(organisation_number)) or {}
    metadata = index.get("_meta") or {}
    return {str(organisation_number): row, "_meta": metadata} if row or metadata else {}


def _attach_nav_evidence(profile: dict, nav_index: dict[str, dict], nav_metadata: dict | None, *, client: NavFeedClient | None = None) -> dict:
    """Attach NAV evidence only after each indexed ad is live-verified."""
    if not nav_metadata or not nav_index:
        return {}
    result = collect_nav_jobs(
        profile,
        now=datetime.now(timezone.utc),
        context={
            "index": nav_index,
            "index_meta": nav_metadata,
            "index_complete": True,
            "client": client,
            "max_ads": MAX_LIVE_ADS_PER_COMPANY,
        },
    )
    ads = result.get("ads") or []
    built_at = nav_metadata.get("built_at")
    live_hashes = sorted(str(ad.get("live_content_sha256")) for ad in ads if ad.get("live_content_sha256"))
    content_hash = live_hashes[0] if len(live_hashes) == 1 else hashlib.sha256(json.dumps(live_hashes, separators=(",", ":")).encode()).hexdigest() if live_hashes else None
    retrieved_at = max((str(ad.get("live_retrieved_at")) for ad in ads if ad.get("live_retrieved_at")), default=utc_now())
    value = {
        "ads": ads,
        "checked": result.get("status") in {"available", "not_available"},
        "index_built_at": built_at,
        "feed_source_url": FEED_SOURCE_URL,
        "live_verified": bool(ads) and all(ad.get("live_verified") for ad in ads),
        "live_retrieved_at": retrieved_at if ads else None,
        "stale": bool(nav_metadata.get("stale")),
        "live_verification_failures": int((result.get("operations") or {}).get("live_verification_failures") or 0),
        "live_rejection_reasons": dict((result.get("operations") or {}).get("live_rejection_reasons") or {}),
    }
    profile.setdefault("evidence", {})["nav_jobs"] = evidence("nav_jobs", result.get("status", "not_checked"), "nav_public_feed_index", FEED_SOURCE_URL, value=value, note=result.get("note"), retrieved_at=retrieved_at, content_sha256=content_hash)
    if result.get("status") == "available" and ads:
        profile.setdefault("claims", {})["nav_jobs"] = value
    return result.get("operations") or {}


def _terminal_website_evidence(profile: dict, *, status: str, note: str, source_url: str = "") -> dict:
    safe_status = status if status in {"not_found", "failed", "blocked", "source_error"} else "failed"
    return evidence("website", safe_status, "company_site", source_url or str(profile.get("website") or ""), note=note)


def _discovery_evidence(profile: dict, discovery: dict, *, gate: str) -> tuple[dict, dict]:
    """Translate cache-shaped discovery output into the batch evidence contract."""
    states = discovery.get("states") or {}
    selected = None
    if gate == "g4" and states.get("official_website_g4") == "available":
        selected = ((discovery.get("website_g4") or {}).get("evidence") or {})
    if not selected and states.get("official_website") == "available":
        selected = ((discovery.get("website") or {}).get("evidence") or {})
    if selected:
        return selected, {"state": "available", "candidate_count": len(discovery.get("candidates") or [])}
    state = states.get("official_website_g4") if gate == "g4" else states.get("official_website")
    state = state or "not_available"
    candidates = discovery.get("candidates") or []
    source_url = str(next((item.get("requested_url") for item in candidates if item.get("block_reason") and item.get("requested_url")), next((item.get("requested_url") for item in candidates if item.get("requested_url")), profile.get("website") or "")))
    status = {"not_available": "not_found", "ambiguous": "not_found", "blocked": "blocked", "failed": "failed"}.get(state, "not_found")
    driver_note = next((str(item.get("block_reason")) for item in candidates if item.get("block_reason")), "")
    if state == "blocked" and driver_note:
        if "robot" in driver_note.casefold():
            parsed = urllib.parse.urlparse(source_url)
            policy_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", "")) if parsed.netloc else source_url
            driver_note = f"{driver_note}; robots URL: {policy_url}"
        elif "policy" not in driver_note.casefold() or "url" not in driver_note.casefold():
            driver_note = f"{driver_note}; policy URL: https://builderr.ai/docs/signalpost-evaluation-harness.md"
    note = "run budget exhausted" if discovery.get("budget_exhausted") else driver_note if state == "blocked" and driver_note else f"live discovery state={state}; no publishable official website"
    if state == "ambiguous":
        note += "; candidate carried entity evidence but did not pass the first-party gate"
    return _terminal_website_evidence(profile, status=status, note=note, source_url=source_url), {
        "state": state,
        "candidate_count": len(candidates),
    }


def _run_discovery_bounded(
    profile: dict,
    *,
    gate: str,
    timeout: float,
    run_deadline: float | None,
    request_policy: HostRequestPolicy,
    nav_index: dict[str, dict],
    executor: ProcessPoolExecutor | None = None,
    resolution_overrides: dict[str, dict] | None = None,
) -> tuple[dict, dict]:
    """Contain one company without making a slow host hold up the batch."""
    # The batch profile moves the registry row into evidence; discovery needs the registry
    # address, phone and e-mail (gates and the registry-e-mail candidate) on the record itself.
    if not profile.get("raw"):
        profile = {**profile, "raw": ((profile.get("evidence") or {}).get("registry") or {}).get("value") or {}}
    budget = timeout
    if run_deadline is not None:
        budget = min(budget, max(0.01, run_deadline - time.monotonic()))
    started = time.monotonic()
    local_executor = executor is None
    worker_pool = executor or ThreadPoolExecutor(max_workers=1)
    if executor is None:
        future = worker_pool.submit(
            discover_profile,
            profile,
            gate=gate,
            timeout=min(12.0, budget),
            request_policy=request_policy,
            nav_index=nav_index,
            max_seconds=budget,
            resolution_overrides=resolution_overrides,
        )
    else:
        future = worker_pool.submit(_discover_process, profile, gate, min(12.0, budget), nav_index, budget, resolution_overrides)
    try:
        # The worker enforces its own budget once it starts; the parent only guards against a hung
        # worker, with a grace period for queueing and process start-up.
        discovery = future.result(timeout=budget + (30.0 if executor is not None else 0.0))
        return discovery, {"timed_out": False, "elapsed_seconds": round(time.monotonic() - started, 3)}
    except TimeoutError:
        return {
            "states": {"official_website": "failed", "official_website_g4": "failed" if gate == "g4" else "not_checked"},
            "candidates": [],
        }, {"timed_out": True, "state": "failed", "elapsed_seconds": round(time.monotonic() - started, 3)}
    except Exception as exc:  # a website failure must not erase other modules
        if executor is not None:
            try:
                retry_budget = min(max(0.25, budget), 3.0)
                retry = discover_profile(profile, gate=gate, timeout=min(12.0, retry_budget), request_policy=request_policy, nav_index=nav_index, max_seconds=retry_budget, resolution_overrides=resolution_overrides)
                return retry, {"timed_out": False, "retried_in_parent": True, "elapsed_seconds": round(time.monotonic() - started, 3)}
            except Exception as retry_exc:  # noqa: BLE001 - convert a dead worker to website failure
                exc = retry_exc
        return {
            "states": {"official_website": "failed", "official_website_g4": "failed" if gate == "g4" else "not_checked"},
            "candidates": [],
            "error": type(exc).__name__,
        }, {"timed_out": False, "state": "failed", "error": type(exc).__name__, "retried_in_parent": executor is not None, "elapsed_seconds": round(time.monotonic() - started, 3)}
    finally:
        if local_executor:
            worker_pool.shutdown(wait=False, cancel_futures=True)


def _discover_process(profile: dict, gate: str, timeout: float, nav_index: dict[str, dict], max_seconds: float | None = None, resolution_overrides: dict[str, dict] | None = None) -> dict:
    """Process-pool entry point; each process gets its own bounded host policy."""
    policy = HostRequestPolicy(min_interval=1.0, max_inflight=2)
    return discover_profile(profile, gate=gate, timeout=timeout, request_policy=policy, nav_index=nav_index, max_seconds=max_seconds, resolution_overrides=resolution_overrides)


def _apply_discovery(profile: dict, discovery: dict, *, gate: str) -> dict:
    website, summary = _discovery_evidence(profile, discovery, gate=gate)
    profile.setdefault("evidence", {})["website"] = website
    profile.setdefault("claims", {}).update({
        key: value for key, value in (discovery.get("claims") or {}).items()
        if key in {"official_website", "official_website_g4", "social_profiles", "contact", "nav_jobs"} and value
    })
    if (discovery.get("website_g4") or {}).get("evidence"):
        profile["evidence"]["website_g4"] = (discovery.get("website_g4") or {}).get("evidence")
    profile["discovery"] = {
        "gate": gate,
        "state": summary["state"],
        "candidate_count": summary["candidate_count"],
        "candidates": discovery.get("candidates") or [],
        "metrics": discovery.get("metrics") or {},
        "source_yield": discovery.get("source_yield") or {},
    }
    if discovery.get("error") or discovery.get("budget_exhausted"):
        profile.setdefault("errors", []).append({"module": "website", "kind": "budget_exhausted" if discovery.get("budget_exhausted") else discovery.get("error"), "detail": "run budget exhausted" if discovery.get("budget_exhausted") else "discovery worker failed"})
    return profile


def _enrich_and_attach_site_facts(
    profile: dict,
    *,
    request_policy: HostRequestPolicy | None,
    timeout: float,
    total_budget_seconds: float,
    deadline: float | None,
    requested_modules: set[str] | list[str],
) -> tuple[dict, dict, dict]:
    """Fetch and attach bounded site facts for an already-selected website."""
    fact_metrics: dict[str, Any] = {}
    try:
        facts, fact_metrics = enrich_site_facts(
            profile,
            request_policy=request_policy,
            timeout=timeout,
            max_extra_requests=4,
            total_budget_seconds=total_budget_seconds,
            deadline=deadline,
        )
        attach_site_facts(profile, facts)
        extra_errors = (fact_metrics or {}).get("extra_errors") or []
        if extra_errors:
            profile.setdefault("errors", []).append({
                "module": "site_facts",
                "kind": "source_error",
                "detail": f"{len(extra_errors)} bounded site-fact source(s) unavailable",
            })
    except Exception as exc:  # noqa: BLE001 - fact extraction cannot erase registry/site output
        profile.setdefault("errors", []).append({"module": "site_facts", "kind": "source_error", "detail": f"{type(exc).__name__}: {exc}"})
        if "website" in requested_modules:
            for field in ("public_activity", "company_brief", "hiring"):
                profile.setdefault("evidence", {}).setdefault(
                    field,
                    evidence(field, "source_error", "company_site", str((profile.get("evidence", {}).get("website") or {}).get("source_url") or "https://builderr.ai/docs/signalpost-evaluation-harness.md"), note=f"site-facts extractor failed: {type(exc).__name__}"),
                )
    return profile, profile.get("run_metrics") or {}, fact_metrics


def _safe_official_modules(profile: dict, modules: set[str]) -> tuple[dict[str, dict], list, list[dict]]:
    """Fetch each official module independently so one fault cannot erase a row."""
    records: dict[str, dict] = {}
    metrics: list = []
    errors: list[dict] = []
    for module in sorted(modules):
        try:
            module_records, module_metrics = fetch_official_modules(profile["organisation_number"], {module})
            records.update(module_records)
            metrics.extend(module_metrics)
        except Exception as exc:  # noqa: BLE001 - a module failure is data, not a batch failure
            note = f"{type(exc).__name__}: {exc}"
            records[module] = evidence(module, "source_error", "official_registry", "https://data.brreg.no/enhetsregisteret/api", note=note)
            errors.append({"module": module, "kind": type(exc).__name__, "detail": str(exc)})
        if module not in records:
            note = "official module returned no record"
            records[module] = evidence(module, "source_error", "official_registry", "https://data.brreg.no/enhetsregisteret/api", note=note)
            errors.append({"module": module, "kind": "missing_module_result", "detail": note})
    return records, metrics, errors


def _input_error_profile(row: dict, requested_modules: list[str]) -> dict:
    error = row.get("input_error") or {"kind": "invalid_input"}
    org = str(row.get("organisation_number") or "")
    note = f"input row rejected: {error.get('kind', 'invalid_input')}"
    module_evidence = {
        module: evidence(module, "source_error", "signalpost_batch", "https://builderr.ai/docs/signalpost-evaluation-harness.md", note=note)
        for module in requested_modules
    }
    return {"organisation_number": org, "input_error": error, "evidence": module_evidence, "errors": [{"module": "input", **error}], "claims": {}, "requested_modules": requested_modules, "run_metrics": {}}


def _missing_registry_profile(profile: dict, requested_modules: list[str]) -> dict:
    note = "absent from registry snapshot"
    source = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"
    for module in requested_modules:
        if module not in profile.setdefault("evidence", {}):
            profile["evidence"][module] = evidence(module, "not_found", "official_registry_bulk", source, note=note, source_row_key=str(profile.get("organisation_number") or ""))
    profile.setdefault("errors", []).append({"module": "registry", "kind": "absent_from_registry_snapshot", "detail": note})
    return profile


def _contain_company_failure(profile: dict, requested_modules: list[str], exc: BaseException) -> dict:
    note = f"{type(exc).__name__}: {exc}"
    profile.setdefault("errors", []).append({"module": "company", "kind": type(exc).__name__, "detail": str(exc)})
    for module in requested_modules:
        if module not in profile.setdefault("evidence", {}):
            profile["evidence"][module] = evidence(module, "source_error", "signalpost_batch", "https://builderr.ai/docs/signalpost-evaluation-harness.md", note=note)
    profile["run_metrics"] = {"requests": 0, "bytes": 0, "latencies_ms": [], "failure_kind": type(exc).__name__}
    return profile


class RunInterrupted(Exception):
    """Internal control flow for a graceful SIGTERM/SIGINT stop."""


def _run_incomplete_profile(profile: dict, requested_modules: list[str]) -> dict:
    """Build the validator-clean fallback for work not reached by this run."""
    base = {
        "organisation_number": str(profile.get("organisation_number") or ""),
        "name": profile.get("name"),
        "evidence": dict(profile.get("evidence") or {}),
        "claims": {},
        "errors": list(profile.get("errors") or []),
        "requested_modules": list(requested_modules),
    }
    for key in ("raw", "_missing_registry", "evaluation_split", "sample_slice"):
        if key in profile:
            base[key] = profile[key]
    note = "run not finished"
    source_url = "https://builderr.ai/docs/signalpost-evaluation-harness.md"
    for module in requested_modules:
        if module in {"registry", "accounting_obligation"} and module in base["evidence"]:
            continue
        base["evidence"][module] = evidence(module, "failed", "signalpost_batch", source_url, note=note)
    base["errors"].append({"module": "batch", "kind": "run_incomplete", "detail": note})
    base["run_metrics"] = {
        "requests": 0,
        "bytes": 0,
        "latencies_ms": [],
        "failure_kind": "run_incomplete",
    }
    return base


def build_output_snapshot(
    organisation_inputs: list[dict],
    state: dict[str, dict],
    profiles_by_org: dict[str, dict],
    requested_modules: list[str],
    *,
    run_id: str,
    started_at: str,
    completed_at: str,
    changes_by_org: dict[str, list[dict]] | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Build a complete, validator-checked snapshot without doing network work."""
    ordered_profiles: list[dict] = []
    for row in organisation_inputs:
        if row.get("input_error"):
            ordered_profiles.append(_input_error_profile(row, requested_modules))
        else:
            org = str(row["organisation_number"])
            ordered_profiles.append(
                state[org]
                if org in state
                else _run_incomplete_profile(profiles_by_org.get(org) or {"organisation_number": org}, requested_modules)
            )
    envelopes = [
        terminal_envelope(profile, run_id=run_id, modules=requested_modules, started_at=started_at, completed_at=completed_at)
        for profile in ordered_profiles
    ]
    for envelope in envelopes:
        envelope["changes"] = (changes_by_org or {}).get(envelope["organisation_number"], [])
    return ordered_profiles, envelopes, validate_envelopes(envelopes, len(organisation_inputs))


def previous_profiles_for_batch(previous: list[dict], orgs: list[str]) -> tuple[list[dict], dict[str, int]]:
    """Keep refresh comparisons on the exact current/previous membership intersection."""
    current_orgs = {str(org) for org in orgs}
    previous_orgs = {str(row.get("organisation_number")) for row in previous if row.get("organisation_number")}
    selected = [row for row in previous if str(row.get("organisation_number") or "") in current_orgs]
    return selected, {
        "current": len(current_orgs),
        "previous": len(previous_orgs),
        "intersection": len(current_orgs & previous_orgs),
    }


def resumed_profile_state(prior: list[dict], orgs: list[str], requested_modules: list[str]) -> dict[str, dict]:
    """Resume only complete rows belonging to the current batch."""
    current_orgs = {str(org) for org in orgs}
    return {
        str(item["organisation_number"]): item
        for item in prior
        if str(item.get("organisation_number") or "") in current_orgs
        and profile_complete_for_modules(item, requested_modules)
        and not (
            "website" in requested_modules
            and (item.get("evidence", {}).get("website") or {}).get("status") == "available"
            and not {"company_brief", "public_activity", "hiring"}.issubset(item.get("evidence", {}))
        )
    }


BRREG_BULK_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"


def ensure_registry_snapshot(path: str) -> None:
    """Download the public Brreg bulk file when the evaluator did not supply a snapshot."""
    target = Path(path)
    if target.exists() and target.stat().st_size > 0:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"registry snapshot {target} not found; downloading {BRREG_BULK_URL}", file=sys.stderr)
    request = urllib.request.Request(BRREG_BULK_URL, headers={"User-Agent": "signalpost-agent/1.0"})
    temporary = target.with_name(target.name + ".part")
    with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as handle:  # noqa: S310 - fixed public https URL
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            handle.write(chunk)
    temporary.replace(target)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluator-owned Signalpost batch contract")
    budget_default = os.environ.get("SIGNALPOST_RUN_BUDGET_SECONDS")
    defaults = runtime_defaults()
    parser.add_argument("--organisations", default=os.environ.get("SIGNALPOST_ORGANISATIONS"), help="JSON, JSONL, or text organisation-number list (or SIGNALPOST_ORGANISATIONS)")
    parser.add_argument("--bulk", default=os.environ.get("SIGNALPOST_REGISTRY_SNAPSHOT", "data/brreg-enheter.csv"), help="Frozen Brreg entity snapshot (or SIGNALPOST_REGISTRY_SNAPSHOT); downloaded from Brreg open data when the file is absent")
    parser.add_argument("--output", default="out/envelopes.jsonl", help="Terminal envelope JSONL")
    parser.add_argument("--profiles-output", default="out/profiles.jsonl")
    parser.add_argument("--report", default="out/run-report.json")
    parser.add_argument("--run-id", default=None, help="Run identifier (default: run-<UTC timestamp>)")
    parser.add_argument("--expected-count", type=int, default=None, help="Optional check of the input line count; the default is whatever the batch file contains")
    parser.add_argument("--workers", type=int, default=defaults["workers"])
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--modules", default="registry,accounting_obligation,registry_live,financials,roles,group,locations,website")
    parser.add_argument("--previous-profiles", help="Previous profile JSONL used for material-change detection")
    parser.add_argument("--changes-output", help="JSONL material-change output")
    parser.add_argument("--cache", help="Declared universe cache directory or JSONL.gz")
    parser.add_argument("--cache-timeout", type=float, default=8.0, help="Per-company cache re-verification budget")
    parser.add_argument("--discovery", choices=("off", "g3", "g4"), default="g4", help="Live registry/NAV/name candidate discovery gate")
    parser.add_argument("--company-timeout", type=float, default=20.0, help="Maximum live discovery time per company")
    parser.add_argument("--second-chance-cap-seconds", type=float, default=90.0, help="Minimum wall cap for the whole second-chance phase; effective cap is max(this value, 10%% of first-phase wall time)")
    parser.add_argument("--run-budget-seconds", type=float, default=None, help="Wall-clock budget for live discovery; zero or negative disables the budget")
    parser.add_argument("--discovery-processes", type=int, default=defaults["discovery_processes"], help="CPU parsing process budget")
    parser.add_argument("--nav-index", help="Complete, fresh NAV employer index JSONL")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--search-fill", action="store_true", help="Opt in to one capped Serper query for cache misses")
    parser.add_argument("--search-budget", type=int, default=0, help="Maximum Serper queries for --search-fill (default: 0)")
    parser.add_argument("--search-cache", default="out/search-fill-cache.jsonl", help="Local replay cache for raw search results")
    args = parser.parse_args()
    args.run_budget_seconds, args.run_budget_source = resolve_run_budget(args.run_budget_seconds, budget_default)
    if not args.organisations:
        raise SystemExit("--organisations (or SIGNALPOST_ORGANISATIONS) is required")
    if not args.run_id:
        args.run_id = "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ensure_registry_snapshot(args.bulk)
    if args.workers < 1 or args.discovery_processes < 1 or args.second_chance_cap_seconds <= 0:
        raise SystemExit("--workers, --discovery-processes, and --second-chance-cap-seconds must be positive")
    if args.nav_index is None:
        shipped_nav = Path("data/nav-employer-index.jsonl")
        args.nav_index = str(shipped_nav) if shipped_nav.exists() else None

    try:
        preflight = {"passed": True, **network_preflight()}
    except NetworkPreflightError as exc:
        print(f"warning: {exc}", file=sys.stderr)
        preflight = {"passed": False, "error": str(exc)}
    started_at = utc_now()
    organisation_inputs = read_organisation_inputs(args.organisations, tolerant=True)
    if args.shard_count < 1 or args.shard_index < 0 or args.shard_index >= args.shard_count:
        raise SystemExit("--shard-index must be within --shard-count")
    if args.shard_count > 1:
        organisation_inputs = organisation_inputs[args.shard_index::args.shard_count]
    effective_expected_count = len(organisation_inputs) if args.shard_count > 1 or args.expected_count is None else args.expected_count
    orgs = [item["organisation_number"] for item in organisation_inputs if item.get("organisation_number") and not item.get("input_error")]
    if len(organisation_inputs) != effective_expected_count:
        # Never abort the batch over a count hint: every input still gets one envelope.
        print(f"warning: --expected-count {effective_expected_count} but the batch file has {len(organisation_inputs)} input lines; processing all of them", file=sys.stderr)
        effective_expected_count = len(organisation_inputs)
    profiles, registry_metadata = profiles_from_bulk(args.bulk, orgs, allow_missing=True)
    annotations = {item["organisation_number"]: item for item in organisation_inputs if item.get("organisation_number") and not item.get("input_error")}
    for profile in profiles:
        for key in ("evaluation_split", "sample_slice"):
            if key in annotations[profile["organisation_number"]]:
                profile[key] = annotations[profile["organisation_number"]][key]
    requested_modules = [item.strip() for item in args.modules.split(",") if item.strip()]
    fetch_modules = set(requested_modules) - {"registry", "accounting_obligation", "website"}
    operations = {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0, "phase_timings_ms": {phase: 0.0 for phase in PHASE_NAMES}, "nav_live_rejection_reasons": {reason: 0 for reason in ("inactive", "expired", "organisation_mismatch", "payload_missing", "payload_mismatch")}, "run_budget_seconds": args.run_budget_seconds, "run_budget_source": args.run_budget_source}
    failure_breaker = ResolutionFailureBreaker()
    failure_counts: Counter[str] = Counter()
    cache_lookup = CacheLookup(args.cache) if args.cache else None
    nav_index, nav_metadata = _fresh_nav_index(args.nav_index)
    nav_live_client = NavFeedClient(token=os.environ.get("NAV_FEED_TOKEN") or None, timeout=LIVE_DETAIL_TIMEOUT)
    request_policy = HostRequestPolicy(min_interval=1.0, max_inflight=2)
    cache_stats: Counter[str] = Counter()
    material_cache_changes: list[dict] = []
    discovery_started = time.monotonic()
    discovery_deadline = discovery_started + args.run_budget_seconds if args.run_budget_seconds else None
    discovery_stats: Counter[str] = Counter()
    discovery_seconds = []
    run_started_clock = time.monotonic()
    discovery_executor = ProcessPoolExecutor(max_workers=max(1, args.discovery_processes)) if args.discovery != "off" else None

    def budget_phase() -> str:
        if not args.run_budget_seconds:
            return "none"
        fraction = (time.monotonic() - run_started_clock) / args.run_budget_seconds
        return "hard" if fraction >= 0.95 else "soft" if fraction >= 0.80 else "open"

    def discover_or_budget(profile: dict) -> tuple[dict, dict]:
        phase = budget_phase()
        if phase in {"soft", "hard"}:
            return {"states": {"official_website": "failed", "official_website_g4": "failed" if args.discovery == "g4" else "not_checked"}, "candidates": [], "budget_exhausted": True}, {"timed_out": False, "state": "failed", "budget_exhausted": True, "elapsed_seconds": 0.0}
        return _run_discovery_bounded(profile, gate=args.discovery, timeout=args.company_timeout, run_deadline=discovery_deadline, request_policy=request_policy, nav_index=_nav_slice(nav_index, str(profile.get("organisation_number") or "")), executor=discovery_executor)

    def maybe_enrich_site_facts(profile: dict, metric: dict) -> tuple[dict, dict, dict]:
        profile["run_metrics"] = metric
        remaining_company_budget = max(0.0, args.company_timeout - (float(metric.get("elapsed_ms") or 0.0) / 1000.0))
        if budget_phase() in {"open", "none"} and remaining_company_budget >= 0.40 * args.company_timeout:
            return _enrich_and_attach_site_facts(
                profile,
                request_policy=request_policy,
                timeout=min(6.0, remaining_company_budget),
                total_budget_seconds=min(12.0, remaining_company_budget),
                deadline=discovery_deadline,
                requested_modules=requested_modules,
            )
        if "website" in requested_modules:
            profile.setdefault("errors", []).append({"module": "site_facts", "kind": "budget_skipped", "detail": "less than 40% of the company budget remained or the global budget was soft"})
        return profile, profile.get("run_metrics") or metric, {}

    def enrich(profile: dict) -> tuple[dict, dict]:
        if profile.get("_missing_registry"):
            _missing_registry_profile(profile, requested_modules)
            _attach_nav_evidence(profile, nav_index, nav_metadata, client=nav_live_client)
            profile["run_metrics"] = {"requests": 0, "bytes": 0, "latencies_ms": [], "failure_kind": "absent_from_registry_snapshot"}
            return profile, profile["run_metrics"]
        if budget_phase() == "hard":
            for module in fetch_modules:
                profile.setdefault("evidence", {})[module] = evidence(module, "failed", "signalpost_batch", "https://builderr.ai/docs/signalpost-evaluation-harness.md", note="run budget exhausted")
            if "website" in requested_modules:
                profile.setdefault("evidence", {})["website"] = evidence("website", "failed", "signalpost_batch", str(profile.get("website") or ""), note="run budget exhausted")
            _attach_nav_evidence(profile, nav_index, nav_metadata, client=nav_live_client)
            profile["errors"] = [{"module": "batch", "kind": "run_budget_exhausted", "detail": "hard stop at 95% of global run budget"}]
            profile["run_metrics"] = {"requests": 0, "bytes": 0, "latencies_ms": [], "failure_kind": "budget_exhausted"}
            return profile, profile["run_metrics"]
        records, metrics, module_errors = _safe_official_modules(profile, fetch_modules)
        profile["evidence"].update(records)
        profile.setdefault("errors", []).extend(module_errors)
        website_metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
        discovery_elapsed_ms = 0.0
        cached = cache_lookup.get(profile["organisation_number"]) if cache_lookup else None
        if cached:
            cache_stats["hits"] += 1
            merge_cache_profile(profile, cached)
            cached_container = (cached.get("website_g4") or {}) if args.discovery == "g4" else (cached.get("website") or {})
            cached_evidence = (cached_container.get("evidence") or {})
            if not cached_evidence:
                cached_evidence = ((cached.get("website") or {}).get("evidence") or {})
            cached_value = cached_evidence.get("value") or {}
            cached_url = cached_value.get("final_url") or cached_evidence.get("source_url")
            if "website" in requested_modules and cached_url:
                reverified, website_metrics = fetch_website(cached_url, timeout=args.cache_timeout, request_policy=request_policy, max_secondary_pages=0)
                failure_breaker.observe(website_metrics)
                gated = apply_website_identity_gate(profile, reverified)["website"]
                first_party = assess_first_party_ownership(profile, gated, istat_gate=True) if gated.get("status") == "available" else {"publishable": False}
                g4 = assess_g4_ownership(profile, gated, candidate_source="cache_reverification", g3_assessment=first_party) if args.discovery == "g4" else first_party
                accepted = g4 if args.discovery == "g4" else first_party
                if accepted.get("publishable"):
                    cache_stats["reverified"] += 1
                    old_hash = cached_value.get("content_sha256") or cached_evidence.get("content_sha256")
                    new_hash = (gated.get("value") or {}).get("content_sha256") or gated.get("content_sha256")
                    if old_hash and new_hash and old_hash != new_hash:
                        material = {"organisation_number": profile["organisation_number"], "field": "official_website", "previous_content_sha256": old_hash, "current_content_sha256": new_hash, "retrieved_at": gated.get("retrieved_at")}
                        material_cache_changes.append(material)
                        cache_stats["material_changes"] += 1
                    profile["evidence"]["website"] = gated
                    profile.setdefault("claims", {})["official_website" if args.discovery != "g4" else "official_website_g4"] = gated.get("value") or {}
                    profile["discovery"] = {"gate": args.discovery, "state": "available", "candidate_count": 1, "source": "declared_cache"}
                else:
                    cache_stats["reverify_failed"] += 1
                    if args.discovery != "off":
                        discovery, discovery_metric = discover_or_budget(profile)
                        _apply_discovery(profile, discovery, gate=args.discovery)
                        discovery_elapsed_ms = float(discovery_metric.get("elapsed_seconds") or 0.0) * 1000
                        discovery_stats["companies"] += 1
                        if discovery_metric.get("timed_out"):
                            discovery_stats["timeouts"] += 1
                        discovery_seconds.append(float(discovery_metric.get("elapsed_seconds") or 0.0))
            elif "website" in requested_modules:
                cache_stats["reverify_unavailable"] += 1
                if args.discovery != "off":
                    discovery, discovery_metric = discover_or_budget(profile)
                    _apply_discovery(profile, discovery, gate=args.discovery)
                    discovery_elapsed_ms = float(discovery_metric.get("elapsed_seconds") or 0.0) * 1000
                    discovery_stats["companies"] += 1
                    if discovery_metric.get("timed_out"):
                        discovery_stats["timeouts"] += 1
                    discovery_seconds.append(float(discovery_metric.get("elapsed_seconds") or 0.0))
        elif "website" in requested_modules:
            cache_stats["misses"] += 1
            if args.discovery != "off":
                discovery, discovery_metric = discover_or_budget(profile)
                _apply_discovery(profile, discovery, gate=args.discovery)
                discovery_elapsed_ms = float(discovery_metric.get("elapsed_seconds") or 0.0) * 1000
                discovery_stats["companies"] += 1
                if discovery_metric.get("timed_out"):
                    discovery_stats["timeouts"] += 1
                discovery_seconds.append(float(discovery_metric.get("elapsed_seconds") or 0.0))
                website_metrics = {
                    "requests": int((discovery.get("metrics") or {}).get("requests") or 0),
                    "bytes": int((discovery.get("metrics") or {}).get("bytes") or 0),
                    "latencies_ms": [],
                    "failure_kind": "timeout" if discovery_metric.get("timed_out") else None,
                }
            else:
                website_record, website_metrics = fetch_website(profile.get("website"), request_policy=request_policy)
                failure_breaker.observe(website_metrics)
                profile["evidence"]["website"] = apply_website_identity_gate(profile, website_record)["website"]
        metric = {
            "requests": len(metrics) + website_metrics["requests"],
            "bytes": sum(item.bytes_received for item in metrics) + website_metrics["bytes"],
            "latencies_ms": [item.elapsed_ms for item in metrics] + website_metrics["latencies_ms"],
            "failure_kind": website_metrics.get("failure_kind"),
            "phase_timings_ms": {phase: 0.0 for phase in PHASE_NAMES},
        }
        metric["phase_timings_ms"]["discovery"] = round(discovery_elapsed_ms, 3)
        metric["elapsed_ms"] = sum(metric["latencies_ms"])
        nav_operations = _attach_nav_evidence(profile, nav_index, nav_metadata, client=nav_live_client)
        metric["requests"] += int(nav_operations.get("requests") or 0)
        metric["nav_live_verified_ads"] = int(nav_operations.get("live_verified_ads") or 0)
        metric["nav_live_verification_failures"] = int(nav_operations.get("live_verification_failures") or 0)
        metric["nav_live_rejection_reasons"] = dict(nav_operations.get("live_rejection_reasons") or {})
        profile, metric, _fact_metrics = maybe_enrich_site_facts(profile, metric)
        metric = profile.get("run_metrics") or metric
        return profile, metric

    state: dict[str, dict] = {}
    resumed_profiles = 0
    profiles_output = Path(args.profiles_output)
    profiles_by_org = {str(profile["organisation_number"]): deepcopy(profile) for profile in profiles}
    if args.resume and profiles_output.exists():
        prior = [json.loads(line) for line in profiles_output.read_text(encoding="utf-8").splitlines() if line.strip()]
        state = resumed_profile_state(prior, orgs, requested_modules)
        resumed_profiles = len(state)
    pending_profiles = [profile for profile in profiles if profile["organisation_number"] not in state]
    abort_state: dict[str, str | None] = {"signal": None, "at": None, "exception": None}
    stop_requested = False
    futures: dict[Any, str] = {}
    worker_pool: ThreadPoolExecutor | None = None
    previous_signal_handlers: dict[int, Any] = {}

    def request_abort(signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        if abort_state["signal"] is None:
            abort_state["signal"] = signal.Signals(signum).name
            abort_state["at"] = utc_now()
        stop_requested = True
        for future in futures:
            future.cancel()
        if discovery_executor is not None:
            discovery_executor.shutdown(wait=False, cancel_futures=True)
        raise RunInterrupted()

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_signal_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_abort)

    initial_profiles, initial_envelopes, initial_validation = build_output_snapshot(
        organisation_inputs,
        state,
        profiles_by_org,
        requested_modules,
        run_id=args.run_id,
        started_at=started_at,
        completed_at=started_at,
    )
    write_jsonl(profiles_output, initial_profiles)
    write_jsonl(Path(args.output), initial_envelopes)
    if not initial_validation["passed"]:
        print(f"warning: initial checkpoint validation failed: {initial_validation}", file=sys.stderr, flush=True)

    last_checkpoint_at = 0.0
    try:
        worker_pool = ThreadPoolExecutor(max_workers=args.workers)
        futures = {worker_pool.submit(enrich, profile): profile["organisation_number"] for profile in pending_profiles}
        for index, future in enumerate(as_completed(futures), 1):
            if stop_requested:
                raise RunInterrupted()
            org = futures[future]
            try:
                profile, metric = future.result()
            except Exception as exc:  # noqa: BLE001 - preserve every input row
                original = next(item for item in profiles if item["organisation_number"] == org)
                profile = _contain_company_failure(original, requested_modules, exc)
                metric = profile["run_metrics"]
            state[profile["organisation_number"]] = profile
            operations["requests"] += metric["requests"]
            operations["bytes"] += metric["bytes"]
            operations["latencies_ms"].extend(metric["latencies_ms"])
            _add_phase_timings(operations, metric.get("phase_timings_ms"))
            for reason, count in (metric.get("nav_live_rejection_reasons") or {}).items():
                if reason in operations["nav_live_rejection_reasons"]:
                    operations["nav_live_rejection_reasons"][reason] += int(count or 0)
            if metric.get("failure_kind"):
                failure_counts[metric["failure_kind"]] += 1
            if index % args.checkpoint_every == 0 or index == len(pending_profiles):
                now = time.monotonic()
                if not last_checkpoint_at or now - last_checkpoint_at >= 60.0 or index == len(pending_profiles):
                    checkpoint_at = utc_now()
                    checkpoint, checkpoint_envelopes, checkpoint_validation = build_output_snapshot(
                        organisation_inputs,
                        state,
                        profiles_by_org,
                        requested_modules,
                        run_id=args.run_id,
                        started_at=started_at,
                        completed_at=checkpoint_at,
                    )
                    write_jsonl(profiles_output, checkpoint)
                    write_jsonl(Path(args.output), checkpoint_envelopes)
                    last_checkpoint_at = now
                    if not checkpoint_validation["passed"]:
                        print(f"warning: checkpoint validation failed: {checkpoint_validation}", file=sys.stderr, flush=True)
                print(f"progress {index}/{len(pending_profiles)} profiles", flush=True)
    except RunInterrupted:
        pass
    except Exception as exc:  # noqa: BLE001 - finalization below preserves the batch contract
        abort_state["exception"] = type(exc).__name__
        abort_state["at"] = abort_state["at"] or utc_now()
    finally:
        if worker_pool is not None:
            worker_pool.shutdown(wait=not stop_requested, cancel_futures=stop_requested)
        if discovery_executor is not None:
            # A timed-out or dead worker must not hold the parent past its global deadline.
            discovery_executor.shutdown(wait=False, cancel_futures=True)
        for signum, previous_handler in previous_signal_handlers.items():
            signal.signal(signum, previous_handler)
    first_phase_wall_seconds = max(0.0, time.monotonic() - run_started_clock)

    # Second chance: any unpublished candidate with a transient failure is retried once,
    # slowly and with little concurrency, instead of being lost for the whole run.
    second_chance = {
        "attempted": 0,
        "recovered": 0,
        "recovered_published": 0,
        "failure_kind_counts": {kind: 0 for kind in sorted(SECOND_CHANCE_FAILURE_KINDS)},
        "selected_by_reason": {reason: 0 for reason in ("strong_source", "timeout_connect", "reresolved_host")},
        "cap_hit": False,
        "skipped": 0,
        "elapsed_seconds": 0.0,
    }
    second_chance_started = time.monotonic()
    second_chance_cap_seconds = max(float(args.second_chance_cap_seconds), 0.10 * first_phase_wall_seconds)
    second_chance_phase_deadline = second_chance_started + second_chance_cap_seconds
    if not stop_requested and abort_state["exception"] is None and args.discovery != "off" and not (discovery_deadline is not None and time.monotonic() >= discovery_deadline):
        unpublished_orgs = _second_chance_unpublished_orgs(state)
        reresolve_deadline = second_chance_phase_deadline
        if discovery_deadline is not None:
            reresolve_deadline = min(reresolve_deadline, discovery_deadline)
        try:
            refreshed_name_hosts = _reresolve_second_chance_name_hosts(
                state,
                unpublished_orgs,
                workers=8,
                timeout=1.0,
                deadline=reresolve_deadline,
            )
        except RunInterrupted:
            stop_requested = True
            refreshed_name_hosts = {}
        except Exception as exc:  # noqa: BLE001 - preserve completed profiles for finalization
            abort_state["exception"] = abort_state["exception"] or type(exc).__name__
            abort_state["at"] = abort_state["at"] or utc_now()
            refreshed_name_hosts = {}
        if time.monotonic() >= second_chance_phase_deadline:
            second_chance["cap_hit"] = True
        if stop_requested or abort_state["exception"]:
            retry_orgs = []
        else:
            try:
                retry_orgs = _second_chance_retry_orgs(state, refreshed_name_hosts)
            except Exception as exc:  # noqa: BLE001 - preserve final envelopes
                abort_state["exception"] = abort_state["exception"] or type(exc).__name__
                abort_state["at"] = abort_state["at"] or utc_now()
                retry_orgs = []
        if retry_orgs:
            second_chance["selected_by_reason"] = _second_chance_selection_reason_counts(state, retry_orgs, refreshed_name_hosts)
            second_chance["failure_kind_counts"] = _second_chance_failure_kind_counts(state, retry_orgs, refreshed_name_hosts)
            retry_policy = HostRequestPolicy(min_interval=1.0, max_inflight=2)

            def _retry(org: str) -> tuple[str, dict, dict]:
                item = state[org]
                overrides = _second_chance_resolution_overrides(item, refreshed_name_hosts.get(org) or {})
                discovery, metric = _run_discovery_bounded(item, gate=args.discovery, timeout=25.0, run_deadline=discovery_deadline, request_policy=retry_policy, nav_index=nav_index, executor=None, resolution_overrides=overrides)
                return org, discovery, metric

            try:
                results, retry_report = _run_second_chance_with_cap(
                    retry_orgs,
                    _retry,
                    cap_seconds=second_chance_cap_seconds,
                    run_deadline=discovery_deadline,
                    workers=4,
                    phase_deadline=second_chance_phase_deadline,
                )
            except RunInterrupted:
                stop_requested = True
                results, retry_report = [], {"attempted": 0, "skipped": len(retry_orgs), "cap_hit": False}
            except Exception as exc:  # noqa: BLE001 - retain any earlier recoveries
                abort_state["exception"] = abort_state["exception"] or type(exc).__name__
                abort_state["at"] = abort_state["at"] or utc_now()
                results, retry_report = [], {"attempted": 0, "skipped": len(retry_orgs), "cap_hit": False}
            second_chance["attempted"] = retry_report["attempted"]
            second_chance["skipped"] = retry_report["skipped"]
            second_chance["cap_hit"] = retry_report["cap_hit"]
            for org, discovery, metric in results:
                discovery_seconds.append(float(metric.get("elapsed_seconds") or 0.0))
                _apply_discovery(state[org], discovery, gate=args.discovery)
                if _discovery_published(discovery, gate=args.discovery):
                    recovered_profile, _recovery_metric, fact_metrics = maybe_enrich_site_facts(state[org], state[org].get("run_metrics") or {})
                    state[org] = recovered_profile
                    if fact_metrics:
                        operations["requests"] += int(fact_metrics.get("requests") or 0)
                        operations["bytes"] += int(fact_metrics.get("bytes") or 0)
                        operations["latencies_ms"].extend(fact_metrics.get("latencies_ms") or [])
                        _add_phase_timings(operations, fact_metrics.get("phase_timings_ms"))
                    second_chance["recovered"] += 1
                    second_chance["recovered_published"] += 1
    second_chance["elapsed_seconds"] = round(time.monotonic() - second_chance_started, 3)

    search_stats: Counter[str] = Counter()
    search_cache_rows = _read_search_fill_cache(Path(args.search_cache)) if args.search_fill else {}
    search_cache_dirty = False
    search_provider = None
    if args.search_fill and args.search_budget > 0 and os.environ.get("SERPER_API_KEY", "").strip():
        search_provider = SerperSearchProvider(os.environ["SERPER_API_KEY"].strip())
    # Search is deliberately sequential and opt-in: it has a hard query budget,
    # and the default path above remains byte-for-byte free of provider calls.
    if not stop_requested and abort_state["exception"] is None and args.search_fill:
        for org in orgs:
            if search_stats["queries"] >= max(0, args.search_budget):
                break
            profile = state[org]
            cache_claims = profile.get("claims") or {}
            if cache_claims.get("official_website") or profile.get("evidence", {}).get("website", {}).get("status") == "available":
                search_stats["skipped_published"] += 1
                continue
            cached_search = search_cache_rows.get(org)
            if cached_search is None and search_provider is None:
                search_stats["skipped_no_key_or_budget"] += 1
                break
            result, search_operation = run_search_fill(
                profile,
                provider=search_provider,
                search_cache_row=cached_search,
                timeout=args.cache_timeout,
                request_policy=request_policy,
            ) if search_provider or cached_search is not None else (None, {"search_queries": 0})
            if cached_search is None:
                search_stats["queries"] += int(search_operation.get("search_queries") or 0)
                raw_results = search_operation.pop("raw_results", [])
                search_cache_rows[org] = {"organisation_number": org, "query": search_operation.get("query"), "results": raw_results, "operation": {key: value for key, value in search_operation.items() if key not in {"query", "raw_results"}}}
                search_cache_dirty = True
            else:
                search_stats["replayed"] += 1
            operations["requests"] += int(search_operation.get("requests") or 0)
            operations["bytes"] += int(search_operation.get("bytes") or 0)
            operations["latencies_ms"].extend(search_operation.get("latencies_ms") or [])
            operations["third_party_cost_usd"] = operations.get("third_party_cost_usd", 0.0) + float(search_operation.get("third_party_cost_usd") or 0.0)
            if result:
                profile["evidence"]["website_g4"] = result["evidence"]
                profile.setdefault("claims", {})["official_website_g4"] = result["evidence"].get("value") or {}
                search_stats["g4_hits"] += 1
            else:
                search_stats["misses"] += 1
    if search_cache_dirty:
        _write_search_fill_cache(Path(args.search_cache), search_cache_rows)

    completed_at = utc_now()
    ordered_profiles, _preview_envelopes, _preview_validation = build_output_snapshot(
        organisation_inputs,
        state,
        profiles_by_org,
        requested_modules,
        run_id=args.run_id,
        started_at=started_at,
        completed_at=completed_at,
    )
    try:
        domain_conflicts = enforce_domain_uniqueness(
            ordered_profiles,
            cache_records=(cache_lookup.records.values() if cache_lookup else ()),
        ) if args.discovery != "off" else []
    except Exception as exc:  # noqa: BLE001 - preserve final envelopes after a post-discovery crash
        abort_state["exception"] = abort_state["exception"] or type(exc).__name__
        abort_state["at"] = abort_state["at"] or utc_now()
        domain_conflicts = []
    for profile in ordered_profiles:
        discovery = profile.get("discovery") or {}
        metric = profile.get("run_metrics") or {}
        if discovery.get("metrics"):
            metric["discovery_requests"] = int((discovery.get("metrics") or {}).get("requests") or 0)
            metric["discovery_bytes"] = int((discovery.get("metrics") or {}).get("bytes") or 0)
            profile["run_metrics"] = metric
    previous_profiles: list[dict] = []
    previous_profiles_overlap = {"current": len(set(orgs)), "previous": 0, "intersection": 0}
    first_run = not args.previous_profiles or not Path(args.previous_profiles).exists()
    if not first_run:
        previous_rows = [json.loads(line) for line in Path(args.previous_profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
        previous_profiles, previous_profiles_overlap = previous_profiles_for_batch(previous_rows, orgs)
    current_profile_orgs = {str(item.get("organisation_number") or "") for item in previous_profiles}
    current_profiles_for_diff = [row for row in ordered_profiles if str(row.get("organisation_number") or "") in current_profile_orgs]
    try:
        changes = [] if first_run else diff_datasets(previous_profiles, current_profiles_for_diff)
    except Exception as exc:  # noqa: BLE001 - final envelopes must survive a diff failure
        abort_state["exception"] = abort_state["exception"] or type(exc).__name__
        abort_state["at"] = abort_state["at"] or utc_now()
        changes = []
    changes.extend(material_cache_changes)
    changes_by_org: dict[str, list[dict]] = {org: [] for org in orgs}
    for change in changes:
        changes_by_org.setdefault(change["organisation_number"], []).append(change)
    ordered_profiles, envelopes, validation = build_output_snapshot(
        organisation_inputs,
        state,
        profiles_by_org,
        requested_modules,
        run_id=args.run_id,
        started_at=started_at,
        completed_at=completed_at,
        changes_by_org=changes_by_org,
    )
    write_jsonl(profiles_output, ordered_profiles)
    write_jsonl(Path(args.output), envelopes)
    if args.changes_output:
        write_jsonl(Path(args.changes_output), changes)
    latencies = sorted(operations.pop("latencies_ms"))
    operations["p50_ms"] = latencies[len(latencies) // 2] if latencies else None
    operations["p95_ms"] = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None
    operations["website_failure_counts"] = dict(failure_counts)
    operations["website_failure_rate"] = round(sum(failure_counts.values()) / effective_expected_count, 4) if effective_expected_count else 0.0
    operations["cpu_count"] = max(1, os.cpu_count() or 1)
    operations["processes"] = args.discovery_processes
    operations["discovery_processes"] = args.discovery_processes
    operations["workers"] = args.workers
    operations["wall_time_seconds"] = round(time.monotonic() - run_started_clock, 3)
    operations["phase_timings_ms"]["discovery"] = round(sum(discovery_seconds) * 1000, 3)
    operations["phase_timings_ms_average_per_company"] = {
        phase: round(float(operations["phase_timings_ms"].get(phase) or 0.0) / effective_expected_count, 3)
        for phase in PHASE_NAMES
    } if effective_expected_count else {phase: 0.0 for phase in PHASE_NAMES}
    operations["budget_phase_end"] = budget_phase()
    operations["budget_thresholds"] = {"soft_fraction": 0.80, "hard_fraction": 0.95}
    operations["third_party_cost_usd"] = round(float(operations.get("third_party_cost_usd") or 0.0), 6)
    rss = max_rss_mb()
    operations["max_rss_mb"] = rss["max_rss_mb"]
    operations["max_rss_self_mb"] = rss["self_mb"]
    operations["max_rss_children_mb"] = rss["children_mb"]
    operations["max_rss"] = {"self_mb": rss["self_mb"], "children_mb": rss["children_mb"]}
    operations["discovery"] = {
        "mode": args.discovery,
        "second_chance": second_chance, "processes": args.discovery_processes,
        "workers": args.workers, "discovery_processes": args.discovery_processes,
        "companies": discovery_stats["companies"],
        "timeouts": discovery_stats["timeouts"],
        "total_runtime_seconds": round(time.monotonic() - discovery_started, 3),
        "seconds_per_company": round((sum(discovery_seconds) / len(discovery_seconds)), 3) if discovery_seconds else None,
        "run_budget_seconds": args.run_budget_seconds,
        "run_budget_source": args.run_budget_source,
        "domain_conflicts": len(domain_conflicts),
        "nav": {
            "path": args.nav_index,
            "complete": bool(nav_metadata and nav_metadata.get("complete") and nav_index),
            "built_at": nav_metadata.get("built_at") if nav_metadata else None,
            "stale": bool(nav_metadata.get("stale")) if nav_metadata else False,
        },
    }
    fallback_profiles = sum(
        1
        for row in organisation_inputs
        if not row.get("input_error") and str(row.get("organisation_number") or "") not in state
    )
    report = {
        "run_id": args.run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "expected_count": effective_expected_count,
        "emitted_envelopes": len(envelopes),
        "resumed_profiles": resumed_profiles,
        "profiles_fetched_this_run": len(pending_profiles),
        "modules": requested_modules,
        "registry": registry_metadata,
        "network_preflight": preflight,
        "operations": operations,
        "validation": validation,
        "first_run": first_run,
        "changes": len(changes),
        "previous_profiles": args.previous_profiles,
        "previous_profiles_overlap": previous_profiles_overlap,
        "aborted": {
            "signal": abort_state["signal"],
            "at": abort_state["at"],
            "exception": abort_state["exception"],
            "finished_profiles": len(state),
            "fallback_profiles": fallback_profiles,
        },
        "cache": {"path": args.cache, "build": cache_lookup.manifest if cache_lookup else None, "stats": dict(cache_stats), "material_changes": len(material_cache_changes), "source_retrieval_times": sorted({key for item in (cache_lookup.records.values() if cache_lookup else []) for key in (item.get("source_retrieval_times") or {})})},
        "search_fill": {"enabled": bool(args.search_fill), "budget": max(0, args.search_budget), "stats": dict(search_stats), "cache_path": args.search_cache if args.search_fill else None, "default_off": not args.search_fill},
        "discovery": operations["discovery"],
        "registry_live_failures": [
            {"organisation_number": profile.get("organisation_number"), "status": (profile.get("evidence", {}).get("registry_live") or {}).get("status"), "note": (profile.get("evidence", {}).get("registry_live") or {}).get("note")}
            for profile in ordered_profiles
            if (profile.get("evidence", {}).get("registry_live") or {}).get("status") != "available"
            or str(((profile.get("evidence", {}).get("registry_live") or {}).get("value") or {}).get("organisation_number") or "") != str(profile.get("organisation_number"))
        ],
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if validation["passed"] else 1)


if __name__ == "__main__":
    main()
