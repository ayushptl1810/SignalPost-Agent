#!/usr/bin/env python3
"""Draw the stratified evaluation sample described in docs/evaluation-sampling-plan.md.

Deterministic: companies are ranked inside each stratum by sha256(seed | organisation number),
the first n_h are taken, and the same rank order assigns the development / held-out /
validation split (60 / 20 / 20) and the pilot flag. The same inputs always give the same manifest.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.registry.sampling import financial_filer_eligible, normalize_row  # noqa: E402

ALLOCATION = {"S1": 100, "S2": 40, "S3": 40, "S4": 80, "S5": 60, "S6": 30, "S7": 50}
SPLIT_SHARES = (("development", 0.6), ("held_out", 0.2), ("validation", 0.2))
# The pilot is the first ranked companies of these strata; they fall inside the development split.
PILOT = {"S1": 30, "S4": 30}
AS_FORMS = {"AS", "ASA"}
EXTENSION_ALLOCATION = {"S4": 90, "S5": 60, "S6": 30}


def assign_stratum(record: dict[str, Any]) -> str:
    if record["legal_form"] not in AS_FORMS:
        return "S7"
    employees = record["employees"]
    if employees is None:
        activity = str(record["industry_code"])[:2]
        return "S2" if activity == "68" else "S3" if activity in {"64", "00"} else "S1"
    return "S4" if employees < 20 else "S5" if employees < 100 else "S6"


def _rank_key(seed: int, organisation_number: str) -> str:
    return hashlib.sha256(f"{seed}|{organisation_number}".encode("utf-8")).hexdigest()


def split_for_rank(rank: int, size: int) -> str:
    boundary = 0.0
    for name, share in SPLIT_SHARES:
        boundary += share * size
        if rank < round(boundary):
            return name
    return SPLIT_SHARES[-1][0]


def draw_sample(records: list[dict[str, Any]], *, seed: int, allocation: dict[str, int] = ALLOCATION) -> list[dict[str, Any]]:
    by_stratum: dict[str, list[dict[str, Any]]] = {name: [] for name in allocation}
    for record in records:
        by_stratum.setdefault(assign_stratum(record), []).append(record)
    manifest: list[dict[str, Any]] = []
    for stratum, wanted in allocation.items():
        population = sorted(by_stratum.get(stratum, []), key=lambda r: _rank_key(seed, r["organisation_number"]))
        chosen = population[: min(wanted, len(population))]
        for rank, record in enumerate(chosen):
            manifest.append({
                "organisation_number": record["organisation_number"],
                "name": record["name"],
                "legal_form": record["legal_form"],
                "municipality": record["municipality"],
                "industry_code": record["industry_code"],
                "employees": record["employees"],
                "registry_website": record["website"] or None,
                "stratum": stratum,
                "population_n": len(population),
                "sample_n": len(chosen),
                "weight": round(len(population) / len(chosen), 4),
                "split": split_for_rank(rank, len(chosen)),
                "pilot": rank < PILOT.get(stratum, 0),
            })
    return manifest


def draw_extension(
    records: list[dict[str, Any]],
    base_manifest: list[dict[str, Any]],
    *,
    seed: int,
    allocation: dict[str, int] = EXTENSION_ALLOCATION,
) -> list[dict[str, Any]]:
    """Draw extra S4/S5/S6 rows without touching the frozen base manifest."""
    existing = {str(row["organisation_number"]) for row in base_manifest}
    by_stratum: dict[str, list[dict[str, Any]]] = {key: [] for key in allocation}
    populations: Counter[str] = Counter()
    for record in records:
        stratum = assign_stratum(record)
        if stratum in allocation:
            populations[stratum] += 1
            if record["organisation_number"] not in existing:
                by_stratum[stratum].append(record)
    extension: list[dict[str, Any]] = []
    for stratum, wanted in allocation.items():
        chosen = sorted(by_stratum[stratum], key=lambda row: _rank_key(seed, row["organisation_number"]))[:wanted]
        for rank, record in enumerate(chosen):
            extension.append({
                "organisation_number": record["organisation_number"],
                "name": record["name"],
                "legal_form": record["legal_form"],
                "municipality": record["municipality"],
                "industry_code": record["industry_code"],
                "employees": record["employees"],
                "registry_website": record["website"] or None,
                "stratum": stratum,
                "population_n": populations[stratum],
                "sample_n": len([item for item in base_manifest if item["stratum"] == stratum]) + len(chosen),
                "weight": round(populations[stratum] / (len([item for item in base_manifest if item["stratum"] == stratum]) + len(chosen)), 4),
                "split": "held_out" if rank % 2 == 0 else "validation",
                "pilot": False,
                "source": "extension",
            })
    return extension


def combine_extension_manifest(base_manifest: list[dict[str, Any]], extension: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return base + extension with recalculated weights for S4/S5/S6."""
    combined = [dict(row, source=row.get("source", "original")) for row in base_manifest] + [dict(row) for row in extension]
    totals = Counter(row["stratum"] for row in combined)
    populations = {row["stratum"]: row.get("population_n") for row in combined}
    for row in combined:
        if row["stratum"] in EXTENSION_ALLOCATION:
            row["sample_n"] = totals[row["stratum"]]
            row["weight"] = round(float(populations[row["stratum"]]) / totals[row["stratum"]], 4)
    return combined


def load_eligible(path: Path, latest_year: str) -> list[dict[str, Any]]:
    opener = gzip.open if path.read_bytes()[:2] == b"\x1f\x8b" else open
    records = []
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            record = normalize_row(row)
            if financial_filer_eligible(record, latest_year, active_only=True):
                records.append(record)
    return records


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Draw the stratified evaluation sample.")
    parser.add_argument("--bulk", default="data/brreg-enheter.csv")
    parser.add_argument("--latest-year", default="2025")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", help="Manifest JSONL for the default sample")
    parser.add_argument("--summary", help="Summary JSON with counts and hashes")
    parser.add_argument("--extension", action="store_true", help="Draw the held-out/validation extension sample")
    parser.add_argument("--base-manifest", default="out/eval-sample/manifest.jsonl")
    parser.add_argument("--extension-output", default="out/eval-sample/manifest-extension.jsonl")
    parser.add_argument("--combined-output", default="out/eval-sample/manifest-v2.jsonl")
    parser.add_argument("--extension-summary", default="out/eval-sample/manifest-extension-summary.json")
    parser.add_argument("--s4-extra", type=int, default=90)
    parser.add_argument("--s5-extra", type=int, default=60)
    parser.add_argument("--s6-extra", type=int, default=30)
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else (20261008 if args.extension else 20261007)

    bulk = Path(args.bulk)
    records = load_eligible(bulk, args.latest_year)
    if args.extension:
        base_path = Path(args.base_manifest)
        base_manifest = [json.loads(line) for line in base_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        allocation = {"S4": args.s4_extra, "S5": args.s5_extra, "S6": args.s6_extra}
        extension = draw_extension(records, base_manifest, seed=seed, allocation=allocation)
        combined = combine_extension_manifest(base_manifest, extension)
        extension_lines = [json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in extension]
        combined_lines = [json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in combined]
        extension_output = Path(args.extension_output)
        combined_output = Path(args.combined_output)
        extension_output.parent.mkdir(parents=True, exist_ok=True)
        combined_output.parent.mkdir(parents=True, exist_ok=True)
        extension_output.write_text("\n".join(extension_lines) + ("\n" if extension_lines else ""), encoding="utf-8")
        combined_output.write_text("\n".join(combined_lines) + ("\n" if combined_lines else ""), encoding="utf-8")
        summary = {
            "seed": seed,
            "latest_year": args.latest_year,
            "bulk_sha256": file_sha256(bulk),
            "base_manifest": str(base_path),
            "base_manifest_sha256": file_sha256(base_path),
            "extension_output": str(extension_output),
            "extension_sha256": hashlib.sha256("\n".join(extension_lines).encode("utf-8")).hexdigest(),
            "combined_output": str(combined_output),
            "combined_sha256": hashlib.sha256("\n".join(combined_lines).encode("utf-8")).hexdigest(),
            "extension_selected": len(extension),
            "by_stratum": dict(Counter(item["stratum"] for item in extension)),
            "by_split": dict(Counter(item["split"] for item in extension)),
            "allocation": allocation,
        }
        summary_path = Path(args.extension_summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    if not args.output or not args.summary:
        parser.error("--output and --summary are required unless --extension is used")
    manifest = draw_sample(records, seed=seed)
    lines = [json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in manifest]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary = {
        "seed": seed,
        "latest_year": args.latest_year,
        "bulk_sha256": file_sha256(bulk),
        "eligible_rows": len(records),
        "selected": len(manifest),
        "manifest_sha256": hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(),
        "by_stratum": {name: sum(1 for item in manifest if item["stratum"] == name) for name in ALLOCATION},
        "by_split": dict(Counter(item["split"] for item in manifest)),
        "pilot": sum(1 for item in manifest if item["pilot"]),
        "allocation": ALLOCATION,
    }
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
