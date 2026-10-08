#!/usr/bin/env python3
"""Repair JSONL records containing raw Unicode line separators in string values."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def repair(path: str | Path) -> int:
    target = Path(path)
    text = target.read_text(encoding="utf-8").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    target.write_text("".join(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    args = parser.parse_args()
    print(json.dumps({"path": args.path, "rows": repair(args.path)}))


if __name__ == "__main__":
    main()
