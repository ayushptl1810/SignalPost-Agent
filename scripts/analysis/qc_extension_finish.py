#!/usr/bin/env python3
"""Compare and adjudicate the independent extension QC pass.

The second pass is deliberately registry-only.  Disagreements are resolved
from the primary pass's cached page reviews, never from a search scorecard.
The CSV is a small, blank-reviewer worksheet for the changed/contested rows.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def category(row: dict[str, Any]) -> str:
    return {
        "official_site": "site",
        "related_only": "related",
        "no_site_confirmed": "no_site",
        "undetermined": "undetermined",
    }.get(str(row.get("outcome")), "undetermined")


def kappa(first: list[str], second: list[str]) -> float | None:
    if not first or len(first) != len(second):
        return None
    observed = sum(left == right for left, right in zip(first, second)) / len(first)
    left_counts = Counter(first)
    right_counts = Counter(second)
    expected = sum(left_counts[item] * right_counts[item] for item in ("site", "related", "no_site", "undetermined")) / len(first) ** 2
    return 1.0 if expected == 1 else round((observed - expected) / (1 - expected), 4)


def adjudicate(primary: dict[str, Any], independent: dict[str, Any]) -> tuple[str, str]:
    if category(primary) == category(independent):
        return str(primary.get("outcome")), "The two independent passes agree."
    pages = primary.get("audit", {}).get("page_reviews") or []
    publishable = [page for page in pages if page.get("website_status") == "available" and page.get("first_party_publishable") and not page.get("third_party")]
    if publishable and category(primary) == "site":
        return "official_site", "Adjudicated from the primary pass's available first-party page review; the registry-only second pass did not fetch that derived domain."
    return "undetermined", "No independently cached page evidence settled the disagreement."


def run(primary_path: Path, independent_path: Path, worksheet_path: Path, report_path: Path) -> dict[str, Any]:
    primary = {str(row["organisation_number"]): row for row in read_jsonl(primary_path)}
    independent = {str(row["organisation_number"]): row for row in read_jsonl(independent_path)}
    common = sorted(set(primary) & set(independent))
    first = [category(primary[org]) for org in common]
    second = [category(independent[org]) for org in common]
    disagreements = []
    worksheet = []
    for org in common:
        if category(primary[org]) == category(independent[org]):
            continue
        outcome, note = adjudicate(primary[org], independent[org])
        row = primary[org]
        disagreements.append({
            "organisation_number": org,
            "name": row.get("name", ""),
            "primary_outcome": row.get("outcome"),
            "independent_outcome": independent[org].get("outcome"),
            "adjudicated_outcome": outcome,
            "domain": row.get("domain") or "",
            "source_urls": row.get("source_urls") or [],
            "note": note,
        })
        worksheet.append({
            "organisation_number": org,
            "name": row.get("name", ""),
            "primary_outcome": row.get("outcome", ""),
            "independent_outcome": independent[org].get("outcome", ""),
            "adjudicated_outcome": outcome,
            "domain": row.get("domain") or "",
            "source_urls": " | ".join(row.get("source_urls") or []),
            "reviewer_verdict": "",
            "reviewer_domain": "",
            "reviewer_notes": "",
            "adjudication_note": note,
        })
    if len(worksheet) > 40:
        raise ValueError("extension QC worksheet exceeds the 40-row cap")
    worksheet_path.parent.mkdir(parents=True, exist_ok=True)
    with worksheet_path.open("w", encoding="utf-8", newline="") as handle:
        fields = list(worksheet[0]) if worksheet else ["organisation_number", "name", "reviewer_verdict"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(worksheet)
    report = {
        "rows_compared": len(common),
        "agreement": round(sum(left == right for left, right in zip(first, second)) / len(common), 4) if common else None,
        "cohen_kappa": kappa(first, second),
        "primary_categories": dict(Counter(first)),
        "independent_categories": dict(Counter(second)),
        "disagreements": disagreements,
        "qc_worksheet_rows": len(worksheet),
        "worksheet": str(worksheet_path),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare and adjudicate the extension QC pass.")
    parser.add_argument("--primary", type=Path, default=Path("out/eval-sample/annotations-ext-v2.jsonl"))
    parser.add_argument("--independent", type=Path, default=Path("out/eval-sample/annotations-ext-v3-qc.jsonl"))
    parser.add_argument("--worksheet", type=Path, default=Path("out/eval-sample/qc-extension-finish-worksheet.csv"))
    parser.add_argument("--report", type=Path, default=Path("out/eval-sample/qc-extension-finish-report.json"))
    args = parser.parse_args()
    print(json.dumps(run(args.primary, args.independent, args.worksheet, args.report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
