#!/usr/bin/env python3
"""Turn an evaluation manifest into the organisation-input JSONL for the batch runner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_manifest(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def make_batch(manifest: list[dict[str, Any]], *, split: str, offset: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
    if split not in {"development", "held_out", "validation", "all"}:
        raise ValueError(f"invalid split: {split}")
    if offset < 0 or (limit is not None and limit < 1):
        raise ValueError("offset must be non-negative and limit must be positive")
    selected = [row for row in manifest if split == "all" or row.get("split") == split][offset:]
    if limit is not None:
        selected = selected[:limit]
    organisations = [str(row.get("organisation_number")) for row in selected]
    if len(organisations) != len(set(organisations)):
        raise ValueError("manifest contains duplicate organisation numbers")
    return [
        {
            "organisation_number": org,
            "evaluation_split": row.get("split"),
            "sample_slice": "pilot" if row.get("pilot") else row.get("source", "evaluation"),
        }
        for org, row in zip(organisations, selected)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a batch-runner input from an evaluation manifest.")
    parser.add_argument("--manifest", default="out/eval-sample/manifest.jsonl")
    parser.add_argument("--split", choices=("development", "held_out", "validation", "all"), default="development")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = make_batch(load_manifest(Path(args.manifest)), split=args.split, offset=args.offset, limit=args.limit)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"manifest": args.manifest, "split": args.split, "selected": len(rows), "output": args.output}, indent=2))


if __name__ == "__main__":
    main()
