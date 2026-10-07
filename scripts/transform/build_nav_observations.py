#!/usr/bin/env python3
"""Build privacy-safe NAV observations from a connector result or profile JSONL."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external.nav_jobs import build_job_observation  # noqa: E402


def build(profiles: list[dict], *, now: datetime | None = None, policy_path: str | Path = "config/connector-policy.json") -> list[dict]:
    current = now or datetime.now(timezone.utc)
    rows = []
    for profile in profiles:
        source = profile.get("external", {}).get("nav_jobs") or profile.get("nav_jobs") or {}
        ads = source.get("ads") if isinstance(source, dict) else source
        for parsed in ads or []:
            item = build_job_observation(profile, parsed, now=current, policy_path=policy_path)
            if item:
                rows.append(item)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", "--input", dest="profiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--policy", default="config/connector-policy.json")
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
    observations = build(rows, policy_path=args.policy)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in observations), encoding="utf-8")
    print(json.dumps({"observations": len(observations), "companies": len({row["organisation_number"] for row in observations})}, indent=2))


if __name__ == "__main__":
    main()
