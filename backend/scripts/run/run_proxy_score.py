#!/usr/bin/env python3
"""Run the local competition proxy without hiding missing evidence."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.analysis.build_observation_audit import export_audit, read_jsonl  # noqa: E402


def _read(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _default_profile_path() -> Path:
    candidates = [
        ROOT / "out/eval-sample/development-profiles-v2.jsonl",
        ROOT / "out/eval-sample/development-profiles.jsonl",
        ROOT / "out/eval-sample/development-input.jsonl",
    ]
    return next((path for path in candidates if path.exists()), candidates[-1])


def _run_refresh_fixture(output: Path) -> Path:
    fixture = ROOT / "tests/fixtures/refresh-snapshots.json"
    if not fixture.exists():
        _write(output, {})
        return output
    command = [sys.executable, str(ROOT / "scripts/run/run_refresh_replay.py"), "--manifest", str(fixture), "--output", str(output)]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if completed.returncode != 0 and not output.exists():
        _write(output, {"warning": "refresh replay failed", "stderr": completed.stderr[-1000:]})
    return output


def _run_score(args: argparse.Namespace, profiles: Path, reports: dict[str, Path], output: Path) -> dict[str, Any]:
    command = [
        sys.executable, str(ROOT / "scripts/analysis/score_competition_v3.py"),
        "--profiles", str(profiles), "--external-report", str(reports["external"]),
        "--batch-report", str(reports["batch"]), "--resume-report", str(reports["resume"]),
        "--refresh-report", str(reports["refresh"]), "--research-report", str(reports["research"]),
        "--ux-report", str(reports["ux"]), "--output", str(output), "--target", str(args.target),
    ]
    if reports.get("sentiment"):
        command.extend(["--sentiment-report", str(reports["sentiment"])])
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if not output.exists():
        raise RuntimeError(f"proxy scorer did not write output: {completed.stderr[-2000:]}")
    result = _read(output)
    if completed.returncode != 0:
        result.setdefault("warnings", []).append("one or more qualification gates failed")
    return result


def _print_table(result: dict[str, Any]) -> None:
    categories = result.get("category_scores", {})
    maxima = {
        "external_footprint_intelligence": 55, "official_company_foundation": 15,
        "research_agent": 10, "daily_extensibility_refresh": 12, "product_ux_design": 8,
    }
    gates = result.get("qualification_gates", {})
    print("category\tpoints\tmaximum\tgate")
    for name, maximum in maxima.items():
        gate = "PASS" if all(gates.values()) else "GATED"
        print(f"{name}\t{categories.get(name, 0)}\t{maximum}\t{gate}")
    print("gate\tstatus\tmissing input")
    for name, passed in gates.items():
        print(f"{name}\t{'PASS' if passed else 'FAIL'}\t{result.get('gate_details', {}).get(name) or ''}")
    print(f"raw_score\t{result.get('raw_score', 0)}\t100\tawardable={result.get('awardable_score', 0)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the development-slice competition proxy.")
    parser.add_argument("--profiles", type=Path, default=_default_profile_path())
    parser.add_argument("--observations", type=Path, default=ROOT / "out/proxy/observations.jsonl")
    parser.add_argument("--labels", type=Path, default=ROOT / "out/proxy/observation-labels.jsonl")
    parser.add_argument("--batch-report", type=Path)
    parser.add_argument("--resume-report", type=Path)
    parser.add_argument("--research-report", type=Path)
    parser.add_argument("--ux-report", type=Path)
    parser.add_argument("--sentiment-report", type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT / "out/proxy")
    parser.add_argument("--as-of")
    parser.add_argument("--target", type=float, default=80)
    parser.add_argument("--simulate-approved", action="store_true", help="Also write a WHAT-IF score without changing policy.json")
    args = parser.parse_args()
    root = args.output_root
    root.mkdir(parents=True, exist_ok=True)
    profiles = args.profiles
    observations = args.observations
    if not observations.exists():
        observations.write_text("", encoding="utf-8")
    profile_rows = read_jsonl(profiles) if profiles.exists() else []
    observation_rows = read_jsonl(observations)
    audit = root / "observation-audit.csv"
    export_audit(
        observation_rows, profile_rows, audit, minimum=100,
        labels_path=args.labels,
        annotation_path=ROOT / "out/eval-sample/annotations-v2-adjudicated.jsonl",
        drafts_output=root / "observation-audit-drafts.jsonl",
        instructions_output=root / "audit-instructions.md",
    )
    if not args.labels.exists():
        args.labels.write_text("", encoding="utf-8")

    stamp = (args.as_of or datetime.now(timezone.utc).date().isoformat())
    dated = root / stamp
    dated.mkdir(parents=True, exist_ok=True)
    reports = {
        "external": root / "external-report.json",
        "batch": args.batch_report or root / "batch-report.json",
        "resume": args.resume_report or root / "resume-report.json",
        "refresh": root / "refresh-report.json",
        "research": args.research_report or root / "research-report.json",
        "ux": args.ux_report or root / "ux-report.json",
    }
    for key in ("batch", "resume", "research", "ux"):
        if not reports[key].exists():
            _write(reports[key], {"warning": f"missing input: {key} report; run the corresponding handoff"})
    _run_refresh_fixture(reports["refresh"])
    command = [
        sys.executable, str(ROOT / "scripts/analysis/evaluate_external_footprint.py"),
        "--profiles", str(profiles), "--observations", str(observations), "--labels", str(args.labels),
        "--output", str(reports["external"]), "--policy", str(ROOT / "config/connector-policy.json"),
    ]
    if args.as_of:
        command.extend(["--as-of", args.as_of])
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if completed.returncode != 0 and not reports["external"].exists():
        _write(reports["external"], {"warning": "external evaluation failed", "stderr": completed.stderr[-1000:]})
    output = dated / "score.json"
    result = _run_score(args, profiles, reports, output)
    simulated_result = None
    if args.simulate_approved:
        simulated_external = dated / "external-report-simulated.json"
        simulated_command = list(command)
        simulated_command[simulated_command.index("--output") + 1] = str(simulated_external)
        simulated_command.append("--simulate-approved")
        simulated_completed = subprocess.run(simulated_command, cwd=ROOT, text=True, capture_output=True)
        if not simulated_external.exists():
            _write(simulated_external, {"warning": "simulated external evaluation failed", "stderr": simulated_completed.stderr[-1000:]})
        simulated_reports = {**reports, "external": simulated_external}
        simulated_result = _run_score(args, profiles, simulated_reports, dated / "score-simulated.json")
    history = root / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True).stdout.strip()
    history.open("a", encoding="utf-8").write(json.dumps({
        "date": stamp, "git_commit": commit, "raw_score": result.get("raw_score"),
        "awardable_score": result.get("awardable_score"), "category_scores": result.get("category_scores"),
        "gate_map": result.get("qualification_gates"),
    }, ensure_ascii=False, separators=(",", ":")) + "\n")
    _print_table(result)
    if simulated_result:
        print("WHAT-IF: NOT AWARDABLE")
        _print_table(simulated_result)


if __name__ == "__main__":
    main()
