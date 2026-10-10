#!/usr/bin/env python3
"""Collect independent Google-backed search evidence for evaluation annotation.

This is an annotation aid, not the production discovery output. It deliberately
keeps the search evidence and the annotation label separate so development labels
cannot silently become held-out or validation truth.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from scripts.run.run_search_discovery import serper_search  # noqa: E402


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_targets(manifest_path: Path, registry_path: Path, annotations_path: Path, mode: str) -> list[dict[str, Any]]:
    manifest = {str(row["organisation_number"]): row for row in read_jsonl(manifest_path)}
    registry = {str(row["organisation_number"]): row for row in json.loads(registry_path.read_text(encoding="utf-8"))}
    annotations = {str(row["organisation_number"]): row for row in read_jsonl(annotations_path)} if annotations_path.exists() else {}
    targets = []
    for org, row in manifest.items():
        annotation = annotations.get(org)
        if mode == "pilot_undetermined" and not (annotation and annotation.get("outcome") == "undetermined"):
            continue
        if mode == "remaining_unannotated" and org in annotations:
            continue
        registry_row = registry.get(org, {})
        targets.append({
            **row,
            "email": registry_row.get("email", ""),
            "phone": registry_row.get("phone", ""),
            "street": registry_row.get("street", ""),
            "zip": registry_row.get("zip", ""),
            "place": registry_row.get("place", ""),
            "regsite": registry_row.get("regsite", ""),
            "registry_annotation": annotation,
        })
    return targets


def research_one(row: dict[str, Any], api_key: str, *, count: int, timeout: float) -> dict[str, Any]:
    profile = {
        "name": row["name"],
        "organisation_number": row["organisation_number"],
        "municipality": row.get("municipality") or row.get("place") or "",
    }
    queries = [
        f'"{row["name"]}" {profile["municipality"]}'.strip(),
        f'"{row["name"]}" {row["organisation_number"]}'.strip(),
    ]
    results: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    for query in queries:
        found, operation = serper_search(profile, api_key, timeout=timeout, count=count, query=query)
        results.extend(found)
        operations.append({key: value for key, value in operation.items() if key != "query_sha256"})
    return {
        "organisation_number": row["organisation_number"],
        "name": row["name"],
        "split": row.get("split"),
        "stratum": row.get("stratum"),
        "registry": {
            key: row.get(key)
            for key in ("municipality", "email", "phone", "street", "zip", "place", "regsite")
            if row.get(key)
        },
        "queries": queries,
        "results": results,
        "operations": operations,
        "searched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "provider": "serper_api_google_search",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect independent Google-backed annotation evidence.")
    parser.add_argument("--manifest", default="out/eval-sample/manifest.jsonl")
    parser.add_argument("--registry", default="out/eval-sample/pilot-registry.json")
    parser.add_argument("--annotations", default="out/eval-sample/pilot-annotations.jsonl")
    parser.add_argument("--mode", choices=("pilot_undetermined", "remaining_unannotated"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=8, choices=range(1, 21), metavar="1..20")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--workers", type=int, default=3, choices=range(1, 9), metavar="1..8")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("SERPER_API_KEY", "").strip()
    if not api_key:
        parser.error("SERPER_API_KEY is required")
    targets = load_targets(Path(args.manifest), Path(args.registry), Path(args.annotations), args.mode)
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(research_one, target, api_key, count=args.count, timeout=args.timeout): target
            for target in targets
        }
        for future in as_completed(futures):
            target = futures[future]
            try:
                records.append(future.result())
            except Exception as exc:  # preserve a failed target for later retry rather than losing it
                records.append({
                    "organisation_number": target["organisation_number"],
                    "name": target["name"],
                    "split": target.get("split"),
                    "stratum": target.get("stratum"),
                    "queries": [],
                    "results": [],
                    "operations": [],
                    "provider_error": type(exc).__name__,
                    "provider_error_detail": str(exc)[:240],
                    "searched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "provider": "serper_api_google_search",
                })
    records.sort(key=lambda row: str(row["organisation_number"]))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in records), encoding="utf-8")
    print(json.dumps({"mode": args.mode, "targets": len(targets), "records": len(records), "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
