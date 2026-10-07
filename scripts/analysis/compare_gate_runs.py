#!/usr/bin/env python3
"""Compare two development scorecards and list the annotation-level flips.

The scorecards already contain the adjudicated prediction map.  Keeping this
comparison as a small standalone command makes gate experiments reproducible
without rerunning a paid search provider.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _prediction(card: dict[str, Any], org: str) -> dict[str, Any]:
    annotations = card.get("annotations") or {}
    return (annotations.get("verdicts") or {}).get(org) or {}


def _metrics(card: dict[str, Any]) -> dict[str, Any]:
    annotations = card.get("annotations") or {}
    weighted = annotations.get("weighted") or {}
    return {
        "published_precision": annotations.get("published_precision"),
        "published_precision_wilson_lower": annotations.get("published_precision_wilson_lower"),
        "has_site_recall": annotations.get("has_site_recall"),
        "abstention_precision": annotations.get("abstention_precision"),
        "wrong_company_per_1000": weighted.get("wrong_company_per_1000"),
        "published_determined": annotations.get("published_determined"),
        "determined": annotations.get("determined"),
        "undetermined_rate": annotations.get("undetermined_rate"),
    }


def compare(baseline: dict[str, Any], candidate: dict[str, Any], annotations: list[dict[str, Any]]) -> dict[str, Any]:
    labels = {str(row["organisation_number"]): row for row in annotations}
    orgs = sorted(set(labels) | set((baseline.get("annotations") or {}).get("verdicts") or {}) | set((candidate.get("annotations") or {}).get("verdicts") or {}))
    flips: list[dict[str, Any]] = []
    for org in orgs:
        before = _prediction(baseline, org)
        after = _prediction(candidate, org)
        if bool(before.get("published")) == bool(after.get("published")) and (before.get("domain") or None) == (after.get("domain") or None):
            continue
        label = labels.get(org) or {}
        flips.append({
            "organisation_number": org,
            "name": label.get("name"),
            "truth": label.get("outcome"),
            "truth_domain": label.get("domain"),
            "before": {
                "published": bool(before.get("published")),
                "domain": before.get("domain"),
                "outcome": before.get("outcome"),
            },
            "after": {
                "published": bool(after.get("published")),
                "domain": after.get("domain"),
                "outcome": after.get("outcome"),
            },
        })
    return {
        "baseline": _metrics(baseline),
        "candidate": _metrics(candidate),
        "delta": {
            key: (
                round(candidate_value - baseline_value, 4)
                if isinstance(candidate_value, (int, float)) and isinstance(baseline_value, (int, float))
                else None
            )
            for key, baseline_value in _metrics(baseline).items()
            for candidate_value in [_metrics(candidate).get(key)]
        },
        "flips": flips,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two adjudicated development scorecards.")
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--annotations", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    annotations = [json.loads(line) for line in args.annotations.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = compare(_read(args.baseline), _read(args.candidate), annotations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"flips": len(result["flips"]), "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
