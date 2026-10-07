#!/usr/bin/env python3
"""Apply explicit second-review corrections to an annotation JSONL artifact."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply manually audited annotation corrections.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--corrections", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = read_jsonl(Path(args.input))
    corrections = {str(row["organisation_number"]): row for row in read_jsonl(Path(args.corrections))}
    if len(corrections) != len(read_jsonl(Path(args.corrections))):
        raise ValueError("correction file contains duplicate organisation numbers")
    by_org = {str(row["organisation_number"]): row for row in rows}
    if not set(corrections) <= set(by_org):
        raise ValueError("correction file contains an organisation outside the input artifact")
    for org, correction in corrections.items():
        row = by_org[org]
        old = row.get("outcome")
        for key in ("outcome", "domain", "evidence_tier", "evidence", "confidence", "found_via", "labeler", "source_urls"):
            if key in correction:
                row[key] = correction[key]
        row.setdefault("audit", {})["manual_correction"] = {
            "previous_outcome": old,
            "corrected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "reason": correction.get("evidence"),
            "source_urls": correction.get("source_urls", []),
        }
        row["annotation_batch"] = "remaining_independent_review_manual_audit"
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "corrections": len(corrections), "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
