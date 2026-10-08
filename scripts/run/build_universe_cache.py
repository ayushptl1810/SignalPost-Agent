#!/usr/bin/env python3
"""Build a resumable, gate-checked public-universe recall cache.

The command intentionally supports a bounded pilot.  It never turns a failed
or unvisited candidate into a negative claim and never bypasses the existing
safe opener or publication gates.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
import socket
import tempfile
import time
import urllib.parse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.cache.universe import CACHE_SCHEMA_VERSION, FIELD_FAMILIES, iter_cache_records  # noqa: E402
from norway_company_agent.core.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.discovery import candidate_domains, pre_resolve_domains, process_record  # noqa: E402
from norway_company_agent.registry.sampling import financial_filer_stratum, iter_bulk, normalize_row, stratum  # noqa: E402
from norway_company_agent.web.candidates import registry_email_candidate  # noqa: E402
from norway_company_agent.web.candidates import name_domain_variants  # noqa: E402
from norway_company_agent.web.first_party import assess_first_party_ownership, assess_g4_ownership  # noqa: E402
from norway_company_agent.web.website import HostRequestPolicy, fetch_website, normalize_homepage, registered_domain  # noqa: E402

SEED = 20261008
FREE_EMAIL_DOMAINS = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "proton.me", "online.no"}
HOST_EMAIL_DOMAINS = {"brreg.no", "nav.no", "altinn.no", "regnskap-norge.no"}


def read_universe(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict) and row.get("organisation_number"):
                    records.append(row)
    return records


def enrich_from_bulk(records: list[dict[str, Any]], bulk: str | Path) -> int:
    """Attach registry address, phone and email to the frozen universe rows.

    The official universe file carries only name, size and registry website; the
    address, phone and email signals that the gates and the registry-email candidate
    need come from the Brreg bulk snapshot, joined by organisation number.
    """
    wanted = {str(record["organisation_number"]) for record in records}
    found: dict[str, dict[str, Any]] = {}
    for item in iter_bulk(bulk):
        org = str(item.get("organisation_number") or "")
        if org in wanted:
            found[org] = item
    attached = 0
    for record in records:
        item = found.get(str(record["organisation_number"]))
        if not item:
            continue
        for key, value in item.items():
            if key == "raw" or record.get(key) in (None, ""):
                record[key] = value
        attached += 1
    return attached


def materialize_universe_from_bulk(bulk: str | Path, output: str | Path, *, latest_year: str = "2025") -> int:
    """Create the declared JSONL input from the local compressed Brreg snapshot.

    This is a reproducible fallback for workspaces where the frozen JSONL was
    not checked in.  The source hash and resulting row count are reported by
    the pilot; no source rows are silently invented.
    """
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with gzip.open(destination, "wt", encoding="utf-8") as target:
        for record in iter_bulk(bulk):
            if str(record.get("latest_submitted_accounts") or "") != str(latest_year) or record.get("bankrupt") or record.get("liquidating"):
                continue
            target.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def select_stratified(records: list[dict[str, Any]], limit: int | None, *, seed: int = SEED) -> list[dict[str, Any]]:
    eligible = [item for item in records if str(item.get("latest_submitted_accounts") or "2025") == "2025" and not item.get("bankrupt") and not item.get("liquidating")]
    if not limit or limit >= len(eligible):
        return sorted(eligible, key=lambda item: (-(item.get("employees") or 0), item.get("organisation_number", "")))
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in eligible:
        groups[financial_filer_stratum(item)] .append(item)
    total = len(eligible)
    quotas = {key: min(len(items), limit * len(items) // total) for key, items in groups.items()}
    left = limit - sum(quotas.values())
    fractional = sorted(groups, key=lambda key: (-(limit * len(groups[key]) / total - quotas[key]), key))
    for key in fractional[:left]:
        quotas[key] += 1
    selected: list[dict[str, Any]] = []
    for key, items in groups.items():
        ranked = sorted(items, key=lambda item: hashlib.sha256(f"{seed}:{item['organisation_number']}".encode()).hexdigest())
        selected.extend(ranked[:quotas[key]])
    return sorted(selected, key=lambda item: (-(item.get("employees") or 0), item.get("organisation_number", "")))


def _write_shard(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def build(
    records: list[dict[str, Any]],
    output: str | Path,
    *,
    limit: int | None = None,
    resume: bool = False,
    workers: int = 64,
    dns_workers: int = 256,
    timeout: float = 12.0,
    nav_index: dict[str, dict[str, Any]] | None = None,
    fetcher: Callable[..., tuple[dict[str, Any], dict[str, Any]]] = fetch_website,
    report_path: str | Path | None = None,
    gate: str = "g3",
    shard_size: int | None = None,
    shard_index: int | None = None,
) -> dict[str, Any]:
    selected_all = select_stratified(records, limit)
    selected = selected_all
    if shard_size is not None:
        if shard_size < 1 or (shard_index is not None and shard_index < 0):
            raise ValueError("shard_size must be positive and shard_index must be non-negative")
        index = shard_index or 0
        selected = selected_all[index * shard_size:(index + 1) * shard_size]
    elif shard_index is not None:
        raise ValueError("shard_index requires shard_size")
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    completed = {item.get("organisation_number") for item in iter_cache_records(output_path)} if resume else set()
    pending = [item for item in selected if item.get("organisation_number") not in completed]
    policy = HostRequestPolicy(min_interval=1.0, max_inflight=1)
    resolved_domains = None
    # Injected fixture fetchers are intentionally allowed to run without DNS;
    # the real builder always performs its DNS-first pass before HTTP.
    if fetcher is fetch_website:
        resolved_domains = pre_resolve_domains(pending, nav_index=nav_index, workers=dns_workers)
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    pending_shard: list[dict[str, Any]] = []
    shard_prefix = f"{(shard_index or 0):06d}-" if shard_index is not None else ""
    next_shard = len(list(output_path.glob(f"{shard_prefix}*.jsonl.gz")))

    def checkpoint() -> None:
        nonlocal next_shard
        if not pending_shard:
            return
        ordered = sorted(pending_shard, key=lambda item: (-(item.get("employees") or 0), item.get("organisation_number", "")))
        filename = f"{shard_prefix}{next_shard:06d}.jsonl.gz"
        _write_shard(output_path / filename, ordered)
        next_shard += 1
        pending_shard.clear()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(process_record, record, nav_index=nav_index, request_policy=policy, timeout=timeout, fetcher=fetcher, gate=gate, resolved_domains=resolved_domains): record for record in pending}
        for future in as_completed(futures):
            source = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"cache_schema": CACHE_SCHEMA_VERSION, "organisation_number": source.get("organisation_number"), "name": source.get("name"), "municipality": source.get("municipality"), "employees": source.get("employees"), "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "candidate_domains": [], "candidates": [], "website": {}, "website_g4": {}, "claims": {}, "states": {"official_website": "failed", "official_website_g4": "failed" if gate == "g4" else "not_checked", "social_profiles": "not_checked", "contact": "not_checked", "nav_jobs": "not_checked"}, "source_retrieval_times": {}, "metrics": {"requests": 0, "bytes": 0, "elapsed_ms": 0, "error": type(exc).__name__}, "source_yield": {}}
            results.append(result)
            pending_shard.append(result)
            if len(pending_shard) >= 250:
                checkpoint()
    results.sort(key=lambda item: (-(next((r.get("employees") or 0 for r in selected if r.get("organisation_number") == item.get("organisation_number")), 0)), item.get("organisation_number", "")))
    checkpoint()
    all_records = list(iter_cache_records(output_path))
    measured_records = results if results else all_records
    source_yield: Counter[str] = Counter()
    states: Counter[str] = Counter()
    for item in measured_records:
        source_yield.update(item.get("source_yield") or {})
        states.update((item.get("states") or {}).values())
    elapsed = time.monotonic() - started
    requests = sum(int(item.get("metrics", {}).get("requests") or 0) for item in measured_records)
    bytes_received = sum(int(item.get("metrics", {}).get("bytes") or 0) for item in measured_records)
    full_count = len(records)
    rate = len(measured_records) / elapsed if elapsed else 0.0
    projection = {"companies": full_count, "hours": round(elapsed / 3600 * full_count / max(1, len(measured_records)), 2), "requests": round(requests * full_count / max(1, len(measured_records))), "disk_bytes": round(sum(len(json.dumps(item, ensure_ascii=False)) for item in measured_records) * full_count / max(1, len(measured_records)))}
    report = {"schema": CACHE_SCHEMA_VERSION, "selected": len(selected), "processed_this_run": len(results), "records_measured": len(measured_records), "resumed": len(completed), "elapsed_seconds": round(elapsed, 2), "average_seconds_per_company": round(elapsed / max(1, len(measured_records)), 4), "achieved_rate_companies_per_second": round(rate, 4), "measurement_mode": "offline_injected_fetcher" if fetcher is not fetch_website else "network_builder", "requests": requests, "bytes": bytes_received, "source_yield": dict(source_yield), "state_counts": dict(states), "failure_or_blocked": sum(1 for item in measured_records for state in (item.get("states") or {}).values() if state in {"failed", "blocked"}), "full_run_projection": projection, "cache_path": str(output_path), "gate": gate, "dns_workers": dns_workers, "fetch_workers": workers, "shard_size": shard_size, "shard_index": shard_index}
    (output_path / "manifest.json").write_text(json.dumps({"cache_schema": CACHE_SCHEMA_VERSION, "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "universe_count": full_count, "selected": len(selected), "records": len(all_records), "pilot": bool(limit and limit < full_count), "full_run_projection": projection, "gate": gate}, indent=2) + "\n", encoding="utf-8")
    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="data/signalpost-company-universe-2025.official.jsonl.gz")
    parser.add_argument("--bulk", help="Materialize --universe from a compressed Brreg CSV if the JSONL is absent")
    parser.add_argument("--output", default="cache/universe")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--dns-workers", type=int, default=256)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--gate", choices=("g3", "g4"), default="g3")
    parser.add_argument("--shard-size", type=int)
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--nav-index")
    parser.add_argument("--report", default="out/recall-pilot-report.json")
    parser.add_argument("--materialize-only", action="store_true")
    args = parser.parse_args()
    if not Path(args.universe).exists():
        if not args.bulk:
            raise SystemExit(f"Universe input is missing: {args.universe}; pass --bulk to materialize it")
        count = materialize_universe_from_bulk(args.bulk, args.universe)
        print(json.dumps({"materialized_universe": args.universe, "records": count}))
        if args.materialize_only:
            return
    records = read_universe(args.universe)
    bulk_path = args.bulk or "data/brreg-enheter.csv"
    if Path(bulk_path).exists():
        attached = enrich_from_bulk(records, bulk_path)
        print(json.dumps({"registry_enrichment": {"bulk": bulk_path, "attached": attached, "universe_rows": len(records)}}))
    nav = {}
    if args.nav_index and Path(args.nav_index).exists():
        meta_path = Path(args.nav_index).with_suffix(Path(args.nav_index).suffix + ".meta.json")
        if meta_path.exists():
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            if metadata.get("complete"):
                from norway_company_agent.external.nav_jobs import load_index
                nav = load_index(args.nav_index)
                nav["_meta"] = metadata
    report = build(records, args.output, limit=args.limit, resume=args.resume, workers=args.workers, dns_workers=args.dns_workers, timeout=args.timeout, nav_index=nav, report_path=args.report, gate=args.gate, shard_size=args.shard_size, shard_index=args.shard_index)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
