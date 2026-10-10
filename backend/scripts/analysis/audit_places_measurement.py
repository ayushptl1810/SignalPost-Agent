#!/usr/bin/env python3
"""Mark the historical Places measurement as unverified without making calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def audit(path: str | Path, report_path: str | Path) -> dict[str, object]:
    source = Path(path)
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()] if source.exists() else []
    for row in rows:
        row["measurement_only"] = True
        row["verification_status"] = "unverified_pending_stage2_audit"
        row["unverified_reason"] = "Historical run accepted a place while recording zero stage-2 identity calls; retained for measurement only."
    if source.exists():
        source.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    report = {
        "calls_made": 0,
        "rows_marked_unverified": len(rows),
        "distinct_organisations": len({str(row.get("organisation_number")) for row in rows}),
        "explanation": "The stored acceptance path required two identity signals, but the historical runner reported zero stage-2 calls. No new Places calls were made; all stored rows are measurement-only until a stage-2 trace is available.",
        "source": str(source),
    }
    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="out/proxy/places-live-observations.jsonl")
    parser.add_argument("--report", default="out/proxy/places-audit-report.json")
    args = parser.parse_args()
    print(json.dumps(audit(args.input, args.report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
