#!/usr/bin/env python3
"""Build exact-org NAV observations from an already collected employer index."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from norway_company_agent.external.nav_jobs import collect, load_index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--policy", default="config/connector-policy.json")
    args = parser.parse_args()
    profiles = [json.loads(line) for line in Path(args.profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
    index = load_index(args.index)
    now = datetime.now(timezone.utc)
    results = [collect(row, now=now, context={"index": index, "policy_path": args.policy}) for row in profiles]
    observations = [item for result in results for item in result.get("observations") or []]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(item, ensure_ascii=True, separators=(",", ":")) + "\n" for item in observations), encoding="utf-8")
    by_stratum = Counter(str(row.get("stratum") or "unknown") for row, result in zip(profiles, results) if result.get("observations"))
    report = {
        "companies": len(profiles),
        "matched_employers": sum(bool(result.get("observations")) for result in results),
        "observations": len(observations),
        "coverage": sum(bool(result.get("observations")) for result in results) / len(profiles) if profiles else 0,
        "by_stratum": dict(by_stratum),
        "index_employers": len(index),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
