#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external.google_places import CONNECTOR_ID, collect  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect official Google Places identity and rating observations.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--index", default="out/places-index.jsonl")
    parser.add_argument("--policy", default="config/connector-policy.json")
    parser.add_argument("--max-calls", type=int, default=1000)
    args = parser.parse_args()
    profiles = [json.loads(line) for line in Path(args.profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
    now = datetime.now(timezone.utc)
    results = []
    calls = 0
    for row in profiles:
        result = collect(row, now=now, context={"policy_path": args.policy, "index_path": args.index, "max_calls": args.max_calls, "calls_used": calls})
        calls += int(result.get("operations", {}).get("requests", 0))
        results.append(result)
    observations = [item for result in results for item in result.get("observations", [])]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in observations), encoding="utf-8")
    report = {"connector_id": CONNECTOR_ID, "companies": len(profiles), "searched": sum(result.get("operations", {}).get("requests", 0) > 0 for result in results), "accepted_places": sum(result.get("status") == "available" for result in results), "observations": len(observations), "calls_used": calls, "abstentions": [result.get("note") for result in results if result.get("status") == "not_available"]}
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
