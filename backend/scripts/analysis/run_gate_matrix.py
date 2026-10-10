#!/usr/bin/env python3
"""Run the development-only G0/G1/G2 gate comparison from recorded caches."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/run/run_search_discovery.py"
SCORER = ROOT / "scripts/analysis/score_discovery_run.py"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_matrix(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scorecards: dict[str, dict[str, Any]] = {}
    for gate in ("g0", "g1", "g2"):
        profiles = output_dir / f"discovery-{gate}.jsonl"
        report = output_dir / f"discovery-{gate}-report.json"
        scorecard = output_dir / f"scorecard-{gate}.json"
        common = [
            sys.executable, str(RUNNER), "--input", str(args.input), "--output", str(profiles), "--report", str(report),
            "--limit", str(args.limit), "--count", str(args.count), "--max-candidates", str(args.max_candidates),
            "--timeout", str(args.timeout), "--company-timeout", str(args.company_timeout), "--workers", str(args.workers),
            "--classifier", args.classifier, "--gate", gate, "--search-cache", str(args.search_cache),
            "--fetch-cache", str(args.fetch_cache), "--replay-only",
        ]
        if args.nav_index:
            common.extend(["--nav-index", str(args.nav_index)])
        subprocess.run(common, cwd=ROOT, check=True)
        subprocess.run([
            sys.executable, str(SCORER), "--profiles", str(profiles), "--report", str(report), "--output", str(scorecard),
            "--annotations", str(args.annotations), "--split", "development", "--min-published", str(args.min_published),
            "--fetch-cache", str(args.fetch_cache),
        ], cwd=ROOT, check=True)
        scorecards[gate] = json.loads(scorecard.read_text(encoding="utf-8"))

    baseline = (scorecards["g0"].get("annotations") or {}).get("verdicts") or {}
    flips: dict[str, list[dict[str, Any]]] = {}
    annotations = {str(row["organisation_number"]): row for row in read_jsonl(Path(args.annotations)) if row.get("split") == "development"}
    for gate in ("g1", "g2"):
        changed: list[dict[str, Any]] = []
        for org, current in ((scorecards[gate].get("annotations") or {}).get("verdicts") or {}).items():
            before = baseline.get(org) or {}
            if (before.get("outcome"), before.get("domain")) != (current.get("outcome"), current.get("domain")):
                changed.append({
                    "organisation_number": org,
                    "name": annotations.get(org, {}).get("name"),
                    "truth": annotations.get(org, {}).get("outcome"),
                    "g0": {"outcome": before.get("outcome"), "domain": before.get("domain")},
                    gate: {"outcome": current.get("outcome"), "domain": current.get("domain")},
                })
        flips[gate] = changed
    result = {
        "configs": {
            gate: {
                "scorecard": str(output_dir / f"scorecard-{gate}.json"),
                "annotations": (scorecards[gate].get("annotations") or {}),
                "gate": scorecards[gate].get("gate"),
            }
            for gate in ("g0", "g1", "g2")
        },
        "flips_vs_g0": flips,
    }
    result_path = output_dir / "gate-matrix.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--search-cache", required=True)
    parser.add_argument("--fetch-cache", required=True)
    parser.add_argument("--nav-index")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=240)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--company-timeout", type=float, default=60.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--classifier", choices=("rules", "laya"), default="rules")
    parser.add_argument("--min-published", type=int, default=35)
    args = parser.parse_args()
    print(json.dumps(run_matrix(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
