#!/usr/bin/env python3
"""Attach frozen external observations to profiles for the research evaluator."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    observations = defaultdict(list)
    for line in Path(args.observations).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            observations[str(row.get("organisation_number"))].append(row)
    rows = []
    for line in Path(args.profiles).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row["external"] = {"observations": observations.get(str(row.get("organisation_number")), [])}
        rows.append(row)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"profiles": len(rows), "profiles_with_observations": sum(bool(row["external"]["observations"]) for row in rows), "observations": sum(len(row["external"]["observations"]) for row in rows)}))


if __name__ == "__main__":
    main()
