#!/usr/bin/env python3
"""Draw the non-evaluation observation-audit corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from select_eval_sample import assign_stratum, file_sha256, load_eligible, _rank_key


ALLOCATION = {"S4": 100, "S5": 80, "S6": 60, "S7": 60}


def draw(records: list[dict[str, Any]], excluded: set[str], *, seed: int, allocation: dict[str, int] = ALLOCATION) -> list[dict[str, Any]]:
    by_stratum: dict[str, list[dict[str, Any]]] = {key: [] for key in allocation}
    for record in records:
        org = str(record["organisation_number"])
        stratum = assign_stratum(record)
        if org not in excluded and stratum in by_stratum:
            by_stratum[stratum].append(record)
    output: list[dict[str, Any]] = []
    for stratum, wanted in allocation.items():
        candidates = sorted(by_stratum[stratum], key=lambda row: _rank_key(seed, str(row["organisation_number"])))[:wanted]
        for record in candidates:
            output.append({
                "organisation_number": record["organisation_number"],
                "name": record["name"],
                "legal_form": record["legal_form"],
                "municipality": record["municipality"],
                "industry_code": record["industry_code"],
                "employees": record["employees"],
                "registry_website": record["website"] or None,
                "stratum": stratum,
                "purpose": "observation_audit",
                "seed": seed,
                "source": "audit_corpus",
            })
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bulk", default="data/brreg-enheter.csv")
    parser.add_argument("--exclude", default="out/eval-sample/manifest-v2.jsonl")
    parser.add_argument("--output", default="out/audit-corpus/manifest.jsonl")
    parser.add_argument("--summary", default="out/audit-corpus/manifest-summary.json")
    parser.add_argument("--seed", type=int, default=20261009)
    args = parser.parse_args()
    bulk = Path(args.bulk)
    excluded = {str(json.loads(line)["organisation_number"]) for line in Path(args.exclude).read_text(encoding="utf-8").splitlines() if line.strip()}
    records = load_eligible(bulk, "2025")
    rows = draw(records, excluded, seed=args.seed)
    assert not ({str(row["organisation_number"]) for row in rows} & excluded)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows]
    destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    summary = {
        "seed": args.seed,
        "purpose": "observation_audit",
        "selected": len(rows),
        "by_stratum": dict(Counter(row["stratum"] for row in rows)),
        "excluded_evaluation_rows": len(excluded),
        "overlap": sorted({str(row["organisation_number"]) for row in rows} & excluded),
        "bulk_sha256": file_sha256(bulk),
        "exclude_manifest": args.exclude,
        "output": str(destination),
        "manifest_sha256": hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(),
    }
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
