#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.registry.batch import profile_complete_for_modules, profiles_from_bulk, read_organisation_inputs, terminal_envelope, validate_envelopes  # noqa: E402
from norway_company_agent.core.evidence import evidence, utc_now  # noqa: E402
from norway_company_agent.core.refresh import diff_datasets  # noqa: E402
from norway_company_agent.cache import CacheLookup, merge_cache_profile  # noqa: E402
from norway_company_agent.core.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.discovery import discover_profile, enforce_domain_uniqueness  # noqa: E402
from norway_company_agent.external.nav_jobs import load_index  # noqa: E402
from norway_company_agent.registry.official import fetch_official_modules  # noqa: E402
from norway_company_agent.web.first_party import assess_first_party_ownership  # noqa: E402
from norway_company_agent.web.first_party import assess_g4_ownership  # noqa: E402
from norway_company_agent.search.providers import ProviderError, SerperSearchProvider  # noqa: E402
from norway_company_agent.web.website import HostRequestPolicy, NetworkPreflightError, ResolutionFailureBreaker, fetch_website, network_preflight, registered_domain  # noqa: E402


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
    temporary.replace(path)


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
    temporary.replace(path)


def _fresh_nav_index(path: str | None) -> tuple[dict[str, dict], dict | None]:
    """Load NAV only when its connector declared a complete recent snapshot."""
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
    if not metadata.get("complete") or age_seconds < 0 or age_seconds > 7 * 86400:
        return {}, metadata
    index = load_index(index_path)
    index["_meta"] = metadata
    return index, metadata


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
    source_url = str(next((item.get("requested_url") for item in candidates if item.get("requested_url")), profile.get("website") or ""))
    status = {"not_available": "not_found", "ambiguous": "not_found", "blocked": "blocked", "failed": "failed"}.get(state, "not_found")
    note = f"live discovery state={state}; no publishable official website"
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
        )
    else:
        future = worker_pool.submit(_discover_process, profile, gate, min(12.0, budget), nav_index, budget)
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
        return {
            "states": {"official_website": "failed", "official_website_g4": "failed" if gate == "g4" else "not_checked"},
            "candidates": [],
            "error": type(exc).__name__,
        }, {"timed_out": False, "state": "failed", "error": type(exc).__name__, "elapsed_seconds": round(time.monotonic() - started, 3)}
    finally:
        if local_executor:
            worker_pool.shutdown(wait=False, cancel_futures=True)


def _discover_process(profile: dict, gate: str, timeout: float, nav_index: dict[str, dict], max_seconds: float | None = None) -> dict:
    """Process-pool entry point; each process gets its own bounded host policy."""
    policy = HostRequestPolicy(min_interval=1.0, max_inflight=2)
    return discover_profile(profile, gate=gate, timeout=timeout, request_policy=policy, nav_index=nav_index, max_seconds=max_seconds)


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
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluator-owned Signalpost batch contract")
    parser.add_argument("--organisations", required=True, help="JSON, JSONL, or text organisation-number list")
    parser.add_argument("--bulk", required=True, help="Frozen Brreg entity snapshot")
    parser.add_argument("--output", required=True, help="Terminal envelope JSONL")
    parser.add_argument("--profiles-output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-count", type=int, default=100)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--modules", default="registry,accounting_obligation,registry_live,financials,roles,group,locations,website")
    parser.add_argument("--previous-profiles", help="Previous profile JSONL used for material-change detection")
    parser.add_argument("--changes-output", help="JSONL material-change output")
    parser.add_argument("--cache", help="Declared universe cache directory or JSONL.gz")
    parser.add_argument("--cache-timeout", type=float, default=8.0, help="Per-company cache re-verification budget")
    parser.add_argument("--discovery", choices=("off", "g3", "g4"), default="g4", help="Live registry/NAV/name candidate discovery gate")
    parser.add_argument("--company-timeout", type=float, default=20.0, help="Maximum live discovery time per company")
    parser.add_argument("--run-budget-seconds", type=float, help="Optional wall-clock budget for live discovery")
    parser.add_argument("--discovery-processes", type=int, default=min(8, os.cpu_count() or 1), help="Reported CPU parsing process budget")
    parser.add_argument("--nav-index", help="Complete, fresh NAV employer index JSONL")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--search-fill", action="store_true", help="Opt in to one capped Serper query for cache misses")
    parser.add_argument("--search-budget", type=int, default=0, help="Maximum Serper queries for --search-fill (default: 0)")
    parser.add_argument("--search-cache", default="out/search-fill-cache.jsonl", help="Local replay cache for raw search results")
    args = parser.parse_args()

    try:
        preflight = network_preflight()
    except NetworkPreflightError as exc:
        raise SystemExit(str(exc)) from exc
    started_at = utc_now()
    organisation_inputs = read_organisation_inputs(args.organisations)
    if args.shard_count < 1 or args.shard_index < 0 or args.shard_index >= args.shard_count:
        raise SystemExit("--shard-index must be within --shard-count")
    if args.shard_count > 1:
        organisation_inputs = organisation_inputs[args.shard_index::args.shard_count]
    effective_expected_count = len(organisation_inputs) if args.shard_count > 1 else args.expected_count
    orgs = [item["organisation_number"] for item in organisation_inputs]
    if len(orgs) != effective_expected_count:
        raise SystemExit(f"Expected {effective_expected_count} organisations, received {len(orgs)}")
    profiles, registry_metadata = profiles_from_bulk(args.bulk, orgs)
    annotations = {item["organisation_number"]: item for item in organisation_inputs}
    for profile in profiles:
        for key in ("evaluation_split", "sample_slice"):
            if key in annotations[profile["organisation_number"]]:
                profile[key] = annotations[profile["organisation_number"]][key]
    requested_modules = [item.strip() for item in args.modules.split(",") if item.strip()]
    fetch_modules = set(requested_modules) - {"registry", "accounting_obligation", "website"}
    operations = {"requests": 0, "bytes": 0, "latencies_ms": []}
    failure_breaker = ResolutionFailureBreaker()
    failure_counts: Counter[str] = Counter()
    cache_lookup = CacheLookup(args.cache) if args.cache else None
    nav_index, nav_metadata = _fresh_nav_index(args.nav_index)
    request_policy = HostRequestPolicy(min_interval=1.0, max_inflight=2)
    cache_stats: Counter[str] = Counter()
    material_cache_changes: list[dict] = []
    discovery_started = time.monotonic()
    discovery_deadline = discovery_started + args.run_budget_seconds if args.run_budget_seconds else None
    discovery_stats: Counter[str] = Counter()
    discovery_seconds = []
    discovery_executor = ProcessPoolExecutor(max_workers=max(1, args.discovery_processes)) if args.discovery != "off" else None

    def enrich(profile: dict) -> tuple[dict, dict]:
        records, metrics = fetch_official_modules(profile["organisation_number"], fetch_modules)
        profile["evidence"].update(records)
        website_metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
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
                        discovery, discovery_metric = _run_discovery_bounded(profile, gate=args.discovery, timeout=args.company_timeout, run_deadline=discovery_deadline, request_policy=request_policy, nav_index=nav_index, executor=discovery_executor)
                        _apply_discovery(profile, discovery, gate=args.discovery)
                        discovery_stats["companies"] += 1
                        if discovery_metric.get("timed_out"):
                            discovery_stats["timeouts"] += 1
                        discovery_seconds.append(float(discovery_metric.get("elapsed_seconds") or 0.0))
            elif "website" in requested_modules:
                cache_stats["reverify_unavailable"] += 1
                if args.discovery != "off":
                    discovery, discovery_metric = _run_discovery_bounded(profile, gate=args.discovery, timeout=args.company_timeout, run_deadline=discovery_deadline, request_policy=request_policy, nav_index=nav_index, executor=discovery_executor)
                    _apply_discovery(profile, discovery, gate=args.discovery)
                    discovery_stats["companies"] += 1
                    if discovery_metric.get("timed_out"):
                        discovery_stats["timeouts"] += 1
                    discovery_seconds.append(float(discovery_metric.get("elapsed_seconds") or 0.0))
        elif "website" in requested_modules:
            cache_stats["misses"] += 1
            if args.discovery != "off":
                discovery, discovery_metric = _run_discovery_bounded(profile, gate=args.discovery, timeout=args.company_timeout, run_deadline=discovery_deadline, request_policy=request_policy, nav_index=nav_index, executor=discovery_executor)
                _apply_discovery(profile, discovery, gate=args.discovery)
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
        }
        profile["run_metrics"] = metric
        return profile, metric

    state: dict[str, dict] = {}
    resumed_profiles = 0
    profiles_output = Path(args.profiles_output)
    if args.resume and profiles_output.exists():
        prior = [json.loads(line) for line in profiles_output.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not set(item["organisation_number"] for item in prior).issubset(set(orgs)):
            raise SystemExit("Resume profile membership is not a subset of this batch")
        state = {
            item["organisation_number"]: item
            for item in prior
            if profile_complete_for_modules(item, requested_modules)
        }
        resumed_profiles = len(state)
    pending_profiles = [profile for profile in profiles if profile["organisation_number"] not in state]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(enrich, profile): profile["organisation_number"] for profile in pending_profiles}
        for index, future in enumerate(as_completed(futures), 1):
            profile, metric = future.result()
            state[profile["organisation_number"]] = profile
            operations["requests"] += metric["requests"]
            operations["bytes"] += metric["bytes"]
            operations["latencies_ms"].extend(metric["latencies_ms"])
            if metric.get("failure_kind"):
                failure_counts[metric["failure_kind"]] += 1
            if index % args.checkpoint_every == 0 or index == len(pending_profiles):
                checkpoint = [state[org] for org in orgs if org in state]
                write_jsonl(profiles_output, checkpoint)

    if discovery_executor is not None:
        discovery_executor.shutdown(wait=True, cancel_futures=False)

    search_stats: Counter[str] = Counter()
    search_cache_rows = _read_search_fill_cache(Path(args.search_cache)) if args.search_fill else {}
    search_cache_dirty = False
    search_provider = None
    if args.search_fill and args.search_budget > 0 and os.environ.get("SERPER_API_KEY", "").strip():
        search_provider = SerperSearchProvider(os.environ["SERPER_API_KEY"].strip())
    # Search is deliberately sequential and opt-in: it has a hard query budget,
    # and the default path above remains byte-for-byte free of provider calls.
    if args.search_fill:
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
    ordered_profiles = [state[org] for org in orgs]
    domain_conflicts = enforce_domain_uniqueness(
        ordered_profiles,
        cache_records=(cache_lookup.records.values() if cache_lookup else ()),
    ) if args.discovery != "off" else []
    for profile in ordered_profiles:
        discovery = profile.get("discovery") or {}
        metric = profile.get("run_metrics") or {}
        if discovery.get("metrics"):
            metric["discovery_requests"] = int((discovery.get("metrics") or {}).get("requests") or 0)
            metric["discovery_bytes"] = int((discovery.get("metrics") or {}).get("bytes") or 0)
            profile["run_metrics"] = metric
    previous_profiles: list[dict] = []
    first_run = not args.previous_profiles or not Path(args.previous_profiles).exists()
    if not first_run:
        previous_profiles = [json.loads(line) for line in Path(args.previous_profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
        if {item.get("organisation_number") for item in previous_profiles} != set(orgs):
            raise SystemExit("Previous profiles must contain exactly the current batch membership")
    changes = [] if first_run else diff_datasets(previous_profiles, ordered_profiles)
    changes.extend(material_cache_changes)
    envelopes = [
        terminal_envelope(profile, run_id=args.run_id, modules=requested_modules, started_at=started_at, completed_at=completed_at)
        for profile in ordered_profiles
    ]
    changes_by_org: dict[str, list[dict]] = {org: [] for org in orgs}
    for change in changes:
        changes_by_org.setdefault(change["organisation_number"], []).append(change)
    for envelope in envelopes:
        envelope["changes"] = changes_by_org.get(envelope["organisation_number"], [])
    validation = validate_envelopes(envelopes, effective_expected_count)
    write_jsonl(profiles_output, ordered_profiles)
    write_jsonl(Path(args.output), envelopes)
    if args.changes_output:
        write_jsonl(Path(args.changes_output), changes)
    latencies = sorted(operations.pop("latencies_ms"))
    operations["p50_ms"] = latencies[len(latencies) // 2] if latencies else None
    operations["p95_ms"] = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None
    operations["website_failure_counts"] = dict(failure_counts)
    operations["website_failure_rate"] = round(sum(failure_counts.values()) / effective_expected_count, 4) if effective_expected_count else 0.0
    operations["discovery"] = {
        "mode": args.discovery,
        "processes": args.discovery_processes,
        "companies": discovery_stats["companies"],
        "timeouts": discovery_stats["timeouts"],
        "total_runtime_seconds": round(time.monotonic() - discovery_started, 3),
        "seconds_per_company": round((sum(discovery_seconds) / len(discovery_seconds)), 3) if discovery_seconds else None,
        "run_budget_seconds": args.run_budget_seconds,
        "domain_conflicts": len(domain_conflicts),
        "nav": {"path": args.nav_index, "fresh_complete": bool(nav_metadata and nav_metadata.get("complete") and nav_index)},
    }
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
