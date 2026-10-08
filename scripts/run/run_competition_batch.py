#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
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


def _attach_nav_evidence(profile: dict, nav_index: dict[str, dict], nav_metadata: dict | None) -> None:
    """Attach dated NAV absence/presence evidence without making a live call."""
    if not nav_metadata or not nav_index:
        return
    org = str(profile.get("organisation_number") or "")
    row = nav_index.get(org) or {}
    ads = row.get("ads") or []
    built_at = nav_metadata.get("built_at")
    value = {"ads": ads, "checked": True, "index_built_at": built_at, "stale": bool(nav_metadata.get("stale"))}
    status = "available" if ads else "not_found"
    note = "Active exact-organisation NAV ads from the shipped parent-keyed index." if ads else f"No active ads for this organisation in the complete NAV index built at {built_at}."
    profile.setdefault("evidence", {})["nav_jobs"] = evidence("nav_jobs", status, "nav_public_feed_index", "https://arbeidsplassen.nav.no/stillinger", value=value, note=note, retrieved_at=utc_now())
    if ads:
        profile.setdefault("claims", {})["nav_jobs"] = value


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
        if executor is not None:
            try:
                retry_budget = min(max(0.25, budget), 3.0)
                retry = discover_profile(profile, gate=gate, timeout=min(12.0, retry_budget), request_policy=request_policy, nav_index=nav_index, max_seconds=retry_budget)
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
    if discovery.get("error") or discovery.get("budget_exhausted"):
        profile.setdefault("errors", []).append({"module": "website", "kind": "budget_exhausted" if discovery.get("budget_exhausted") else discovery.get("error"), "detail": "run budget exhausted" if discovery.get("budget_exhausted") else "discovery worker failed"})
    return profile


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluator-owned Signalpost batch contract")
    cpu_count = max(1, os.cpu_count() or 1)
    default_processes = min(8, cpu_count)
    default_workers = int(os.environ.get("SIGNALPOST_WORKERS") or (2 * default_processes))
    budget_default = os.environ.get("SIGNALPOST_RUN_BUDGET_SECONDS")
    parser.add_argument("--organisations", required=True, help="JSON, JSONL, or text organisation-number list")
    parser.add_argument("--bulk", required=True, help="Frozen Brreg entity snapshot")
    parser.add_argument("--output", required=True, help="Terminal envelope JSONL")
    parser.add_argument("--profiles-output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-count", type=int, default=100)
    parser.add_argument("--workers", type=int, default=default_workers)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--modules", default="registry,accounting_obligation,registry_live,financials,roles,group,locations,website")
    parser.add_argument("--previous-profiles", help="Previous profile JSONL used for material-change detection")
    parser.add_argument("--changes-output", help="JSONL material-change output")
    parser.add_argument("--cache", help="Declared universe cache directory or JSONL.gz")
    parser.add_argument("--cache-timeout", type=float, default=8.0, help="Per-company cache re-verification budget")
    parser.add_argument("--discovery", choices=("off", "g3", "g4"), default="g4", help="Live registry/NAV/name candidate discovery gate")
    parser.add_argument("--company-timeout", type=float, default=20.0, help="Maximum live discovery time per company")
    parser.add_argument("--run-budget-seconds", type=float, default=float(budget_default) if budget_default else None, help="Optional wall-clock budget for live discovery")
    parser.add_argument("--discovery-processes", type=int, default=default_processes, help="CPU parsing process budget")
    parser.add_argument("--nav-index", help="Complete, fresh NAV employer index JSONL")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--search-fill", action="store_true", help="Opt in to one capped Serper query for cache misses")
    parser.add_argument("--search-budget", type=int, default=0, help="Maximum Serper queries for --search-fill (default: 0)")
    parser.add_argument("--search-cache", default="out/search-fill-cache.jsonl", help="Local replay cache for raw search results")
    args = parser.parse_args()
    if args.workers < 1 or args.discovery_processes < 1:
        raise SystemExit("--workers and --discovery-processes must be positive")
    if args.nav_index is None:
        shipped_nav = Path("data/nav-employer-index.jsonl")
        args.nav_index = str(shipped_nav) if shipped_nav.exists() else None

    try:
        preflight = network_preflight()
    except NetworkPreflightError as exc:
        raise SystemExit(str(exc)) from exc
    started_at = utc_now()
    organisation_inputs = read_organisation_inputs(args.organisations, tolerant=True)
    if args.shard_count < 1 or args.shard_index < 0 or args.shard_index >= args.shard_count:
        raise SystemExit("--shard-index must be within --shard-count")
    if args.shard_count > 1:
        organisation_inputs = organisation_inputs[args.shard_index::args.shard_count]
    effective_expected_count = len(organisation_inputs) if args.shard_count > 1 else args.expected_count
    orgs = [item["organisation_number"] for item in organisation_inputs if item.get("organisation_number") and not item.get("input_error")]
    if len(organisation_inputs) != effective_expected_count:
        raise SystemExit(f"Expected {effective_expected_count} input lines, received {len(organisation_inputs)}")
    profiles, registry_metadata = profiles_from_bulk(args.bulk, orgs, allow_missing=True)
    annotations = {item["organisation_number"]: item for item in organisation_inputs if item.get("organisation_number") and not item.get("input_error")}
    for profile in profiles:
        for key in ("evaluation_split", "sample_slice"):
            if key in annotations[profile["organisation_number"]]:
                profile[key] = annotations[profile["organisation_number"]][key]
    requested_modules = [item.strip() for item in args.modules.split(",") if item.strip()]
    fetch_modules = set(requested_modules) - {"registry", "accounting_obligation", "website"}
    operations = {"requests": 0, "bytes": 0, "latencies_ms": [], "third_party_cost_usd": 0.0}
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

    def enrich(profile: dict) -> tuple[dict, dict]:
        if profile.get("_missing_registry"):
            _missing_registry_profile(profile, requested_modules)
            _attach_nav_evidence(profile, nav_index, nav_metadata)
            profile["run_metrics"] = {"requests": 0, "bytes": 0, "latencies_ms": [], "failure_kind": "absent_from_registry_snapshot"}
            return profile, profile["run_metrics"]
        if budget_phase() == "hard":
            for module in fetch_modules:
                profile.setdefault("evidence", {})[module] = evidence(module, "failed", "signalpost_batch", "https://builderr.ai/docs/signalpost-evaluation-harness.md", note="run budget exhausted")
            if "website" in requested_modules:
                profile.setdefault("evidence", {})["website"] = evidence("website", "failed", "signalpost_batch", str(profile.get("website") or ""), note="run budget exhausted")
            _attach_nav_evidence(profile, nav_index, nav_metadata)
            profile["errors"] = [{"module": "batch", "kind": "run_budget_exhausted", "detail": "hard stop at 95% of global run budget"}]
            profile["run_metrics"] = {"requests": 0, "bytes": 0, "latencies_ms": [], "failure_kind": "budget_exhausted"}
            return profile, profile["run_metrics"]
        records, metrics, module_errors = _safe_official_modules(profile, fetch_modules)
        profile["evidence"].update(records)
        profile.setdefault("errors", []).extend(module_errors)
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
                        discovery, discovery_metric = discover_or_budget(profile)
                        _apply_discovery(profile, discovery, gate=args.discovery)
                        discovery_stats["companies"] += 1
                        if discovery_metric.get("timed_out"):
                            discovery_stats["timeouts"] += 1
                        discovery_seconds.append(float(discovery_metric.get("elapsed_seconds") or 0.0))
            elif "website" in requested_modules:
                cache_stats["reverify_unavailable"] += 1
                if args.discovery != "off":
                    discovery, discovery_metric = discover_or_budget(profile)
                    _apply_discovery(profile, discovery, gate=args.discovery)
                    discovery_stats["companies"] += 1
                    if discovery_metric.get("timed_out"):
                        discovery_stats["timeouts"] += 1
                    discovery_seconds.append(float(discovery_metric.get("elapsed_seconds") or 0.0))
        elif "website" in requested_modules:
            cache_stats["misses"] += 1
            if args.discovery != "off":
                discovery, discovery_metric = discover_or_budget(profile)
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
        metric["elapsed_ms"] = sum(metric["latencies_ms"])
        _attach_nav_evidence(profile, nav_index, nav_metadata)
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
    ordered_profiles = []
    for row in organisation_inputs:
        if row.get("input_error"):
            ordered_profiles.append(_input_error_profile(row, requested_modules))
        elif row["organisation_number"] in state:
            ordered_profiles.append(state[row["organisation_number"]])
        else:
            ordered_profiles.append(_contain_company_failure({"organisation_number": row["organisation_number"], "evidence": {}}, requested_modules, RuntimeError("company was not processed")))
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
    operations["cpu_count"] = max(1, os.cpu_count() or 1)
    operations["processes"] = args.discovery_processes
    operations["workers"] = args.workers
    operations["wall_time_seconds"] = round(time.monotonic() - run_started_clock, 3)
    operations["budget_phase_end"] = budget_phase()
    operations["budget_thresholds"] = {"soft_fraction": 0.80, "hard_fraction": 0.95}
    operations["third_party_cost_usd"] = round(float(operations.get("third_party_cost_usd") or 0.0), 6)
    operations["discovery"] = {
        "mode": args.discovery,
        "processes": args.discovery_processes,
        "companies": discovery_stats["companies"],
        "timeouts": discovery_stats["timeouts"],
        "total_runtime_seconds": round(time.monotonic() - discovery_started, 3),
        "seconds_per_company": round((sum(discovery_seconds) / len(discovery_seconds)), 3) if discovery_seconds else None,
        "run_budget_seconds": args.run_budget_seconds,
        "domain_conflicts": len(domain_conflicts),
        "nav": {
            "path": args.nav_index,
            "complete": bool(nav_metadata and nav_metadata.get("complete") and nav_index),
            "built_at": nav_metadata.get("built_at") if nav_metadata else None,
            "stale": bool(nav_metadata.get("stale")) if nav_metadata else False,
        },
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
