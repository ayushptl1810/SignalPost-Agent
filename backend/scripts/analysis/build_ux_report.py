#!/usr/bin/env python3
"""Deterministic UX checklist for the generated prototype."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


CHECKS = (
    ("profile_identity_and_sources", 1),
    ("claim_citations_and_retrieval", 1),
    ("external_platforms_and_freshness", 2),
    ("explicit_abstentions", 1),
    ("inspectable_screening", 1),
    ("saved_work_and_export", 1),
    ("score_and_gate_transparency", 1),
)


def evaluate_html(html: str, screenshot_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    checks = {
        "profile_identity_and_sources": bool("Company index" in html and "Source" in html and ("org" in html.casefold() or "Organisation" in html)),
        "claim_citations_and_retrieval": bool("Retrieved" in html and "source" in html.casefold() and ("content_sha256" in html or '"hash"' in html)),
        "external_platforms_and_freshness": bool("external-footprint" in html and "Signals by platform" in html and "Retrieved" in html and ("not a combined popularity score" in html or "not blended into a popularity score" in html)),
        "explicit_abstentions": bool("Missing is not zero" in html and ("No qualified external signals" in html or "not available" in html.casefold())),
        "inspectable_screening": bool("Search companies" in html and "Save view" in html and "filter" in html.casefold()),
        "saved_work_and_export": bool("Save view" in html and "Export results" in html and "localStorage" in html),
        "score_and_gate_transparency": bool("Evidence coverage" in html and "qualification" in html.casefold() and "gate" in html.casefold()),
    }
    results = {name: {"passed": checks[name], "points": weight if checks[name] else 0, "maximum": weight} for name, weight in CHECKS}
    score = sum(item["points"] for item in results.values())
    external = bool(checks["external_platforms_and_freshness"] and checks["explicit_abstentions"])
    if screenshot_manifest is not None:
        results["screenshot_manifest"] = {"passed": bool(screenshot_manifest), "points": 0, "maximum": 0}
    return {"score": score, "maximum": 8, "checks": results, "external_intelligence_presented": external, "qualification_passed": score >= 6 and external}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", "--prototype", dest="html", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--screenshot-manifest")
    args = parser.parse_args()
    html = Path(args.html).read_text(encoding="utf-8")
    manifest = json.loads(Path(args.screenshot_manifest).read_text(encoding="utf-8")) if args.screenshot_manifest else None
    report = evaluate_html(html, manifest)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
