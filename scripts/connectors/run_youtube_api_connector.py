#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external.youtube_api import CONNECTOR_ID, collect  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect official YouTube observations from verified-site links.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--policy", default="config/connector-policy.json")
    args = parser.parse_args()
    profiles = [json.loads(line) for line in Path(args.profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
    now = datetime.now(timezone.utc)
    results = [collect(row, now=now, context={"policy_path": args.policy}) for row in profiles]
    observations = [item for result in results for item in result.get("observations", [])]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in observations), encoding="utf-8")
    report = {"connector_id": CONNECTOR_ID, "companies": len(profiles), "observations": len(observations), "statuses": {status: sum(result.get("status") == status for result in results) for status in {result.get("status") for result in results}}, "units": sum(result.get("operations", {}).get("units", 0) for result in results)}
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
