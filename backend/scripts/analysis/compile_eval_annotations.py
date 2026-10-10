#!/usr/bin/env python3
"""Compile the frozen 400-company evaluation annotation set.

The eight pre-existing pilot labels are retained as development-only labels.
The 52 previously undetermined pilot rows are replaced by the independent
adjudications.  The other 340 rows come only from the fresh Google/page review
artifacts, so held-out and validation do not inherit the old pilot labels.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_OUTCOMES = {"official_site", "related_only", "no_site_confirmed", "undetermined"}
SPLITS = {"development", "held_out", "validation"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def enrich(label: dict[str, Any], manifest_row: dict[str, Any], *, batch: str, independence: str) -> dict[str, Any]:
    effective_batch = batch
    if batch == "remaining_independent_review" and (label.get("audit") or {}).get("manual_correction"):
        effective_batch = "remaining_independent_review_manual_audit"
    result = {
        **manifest_row,
        **label,
        "organisation_number": str(manifest_row["organisation_number"]),
        "name": manifest_row["name"],
        "stratum": manifest_row["stratum"],
        "split": manifest_row["split"],
        "annotation_batch": effective_batch,
        "annotation_independence": independence,
    }
    result.setdefault("source_urls", [])
    if not result["source_urls"]:
        # The original eight development labels predate the URL-audit field.
        # Preserve their label/evidence verbatim while adding the registry page
        # as the minimum reproducible provenance.
        result["source_urls"] = [
            f"https://virksomhet.brreg.no/nb/oppslag/enheter/{manifest_row['organisation_number']}"
        ]
        if result.get("domain"):
            result["source_urls"].append(f"https://{result['domain']}/")
    result.setdefault("attempted", True)
    return result


def validate(rows: list[dict[str, Any]], manifest: list[dict[str, Any]], existing: dict[str, Any]) -> dict[str, Any]:
    manifest_by_org = {str(row["organisation_number"]): row for row in manifest}
    ids = [str(row["organisation_number"]) for row in rows]
    if len(rows) != len(manifest_by_org) or len(set(ids)) != len(ids) or set(ids) != set(manifest_by_org):
        raise ValueError("compiled annotations do not cover the manifest exactly once")
    if any(row.get("outcome") not in VALID_OUTCOMES for row in rows):
        raise ValueError("compiled annotations contain an invalid outcome")
    if any(row.get("split") not in SPLITS for row in rows):
        raise ValueError("compiled annotations contain an invalid split")
    split_counts = Counter(row["split"] for row in rows)
    if split_counts != Counter({"development": 240, "held_out": 80, "validation": 80}):
        raise ValueError(f"unexpected split counts: {split_counts}")
    official_old = {
        org for org, row in existing.items()
        if row.get("outcome") == "official_site"
    }
    if any(
        str(row["organisation_number"]) in official_old and row["split"] != "development"
        for row in rows
    ):
        raise ValueError("an existing official_site label escaped the development split")
    old_sources = {
        row["organisation_number"]: row.get("annotation_batch")
        for row in rows
        if row["organisation_number"] in official_old
    }
    if any(batch != "pilot_existing_development" for batch in old_sources.values()):
        raise ValueError("existing pilot labels were not preserved as development-only")
    return {
        "rows": len(rows),
        "split_counts": dict(sorted(split_counts.items())),
        "outcomes": dict(sorted(Counter(row["outcome"] for row in rows).items())),
        "outcomes_by_split": {
            split: dict(sorted(Counter(row["outcome"] for row in rows if row["split"] == split).items()))
            for split in sorted(SPLITS)
        },
        "confidence": dict(sorted(Counter(row.get("confidence", "missing") for row in rows).items())),
        "annotation_batches": dict(sorted(Counter(row["annotation_batch"] for row in rows).items())),
        "existing_official_site_rows_development_only": sorted(official_old),
        "compiled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def compile_extension(base_path: Path, extension_path: Path, output_path: Path) -> dict[str, Any]:
    """Append the independently annotated 180-row extension without rewriting v2."""
    base = read_jsonl(base_path)
    extension = read_jsonl(extension_path)
    base_orgs = {str(row["organisation_number"]) for row in base}
    if base_orgs & {str(row["organisation_number"]) for row in extension}:
        raise ValueError("extension annotations overlap the existing adjudicated corpus")
    for row in extension:
        outcome = row.get("outcome")
        if outcome not in VALID_OUTCOMES:
            raise ValueError(f"invalid extension outcome for {row.get('organisation_number')}")
        if outcome != "official_site" and not row.get("negative_checks"):
            raise ValueError(f"non-positive extension row lacks mandatory negative_checks: {row.get('organisation_number')}")
        row.setdefault("annotation_batch", "extension_v1")
        row.setdefault("annotation_independence", "independent_search_and_page_review")
    combined = base + extension
    if len(combined) != 580 or len({str(row["organisation_number"]) for row in combined}) != len(combined):
        raise ValueError(f"expected 580 unique rows, got {len(combined)}")
    write_jsonl(output_path, combined)
    split_counts = Counter(str(row.get("split")) for row in extension)
    outcome_counts = Counter(str(row.get("outcome")) for row in extension)
    complete_negative = sum(
        row.get("outcome") != "official_site" and all((row.get("negative_checks", {}).get(name) or {}).get("status") in {"pass", "fail", "not_run"} for name in (
            "registry_website_and_email_domain", "nav_employer_index", "name_derived_domains", "alternative_provider_search", "address_and_phone_page_check"
        )) for row in extension
    )
    return {"rows": len(combined), "extension_rows": len(extension), "extension_split_counts": dict(split_counts), "extension_outcomes": dict(outcome_counts), "negative_checks_complete": complete_negative, "output": str(output_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile and validate the 400-row evaluation annotations.")
    parser.add_argument("--manifest", default="out/eval-sample/manifest.jsonl")
    parser.add_argument("--existing", default="out/eval-sample/pilot-annotations.jsonl")
    parser.add_argument("--pilot-adjudications", default="out/eval-sample/pilot-manual-adjudications.jsonl")
    parser.add_argument("--remaining", default="out/eval-sample/remaining-annotations.jsonl")
    parser.add_argument("--output", default="out/eval-sample/annotations-v1.jsonl")
    parser.add_argument("--report", default="out/eval-sample/annotation-report-v1.json")
    parser.add_argument("--extension-base", help="Existing adjudicated annotation JSONL")
    parser.add_argument("--extension-annotations", help="180-row extension annotation JSONL")
    parser.add_argument("--extension-output", help="Combined 580-row output")
    args = parser.parse_args()
    if args.extension_base or args.extension_annotations or args.extension_output:
        if not (args.extension_base and args.extension_annotations and args.extension_output):
            parser.error("extension mode requires --extension-base, --extension-annotations and --extension-output")
        print(json.dumps(compile_extension(Path(args.extension_base), Path(args.extension_annotations), Path(args.extension_output)), ensure_ascii=False, indent=2))
        return

    manifest = read_jsonl(Path(args.manifest))
    manifest_by_org = {str(row["organisation_number"]): row for row in manifest}
    existing_rows = read_jsonl(Path(args.existing))
    existing = {str(row["organisation_number"]): row for row in existing_rows}
    pilot = {str(row["organisation_number"]): row for row in read_jsonl(Path(args.pilot_adjudications))}
    remaining_rows = read_jsonl(Path(args.remaining))
    remaining = {str(row["organisation_number"]): row for row in remaining_rows}

    pilot_undetermined = {
        org for org, row in existing.items() if row.get("outcome") == "undetermined"
    }
    if set(pilot) != pilot_undetermined:
        raise ValueError(f"pilot adjudication coverage mismatch: expected {len(pilot_undetermined)}, got {len(pilot)}")
    if set(existing) - set(manifest_by_org) or set(remaining) - set(manifest_by_org):
        raise ValueError("annotation artifact contains an organisation outside the frozen manifest")

    compiled: list[dict[str, Any]] = []
    for manifest_row in manifest:
        org = str(manifest_row["organisation_number"])
        if org in pilot:
            compiled.append(enrich(pilot[org], manifest_row, batch="pilot_adjudication", independence="fresh_search_and_page_review"))
        elif org in existing:
            compiled.append(enrich(existing[org], manifest_row, batch="pilot_existing_development", independence="preexisting_development_label"))
        elif org in remaining:
            compiled.append(enrich(remaining[org], manifest_row, batch="remaining_independent_review", independence="fresh_search_and_page_review"))
        else:
            raise ValueError(f"no annotation for {org}")

    report = validate(compiled, manifest, existing)
    output = Path(args.output)
    write_jsonl(output, compiled)
    for split in sorted(SPLITS):
        write_jsonl(output.with_name(f"{output.stem}-{split}{output.suffix}"), [row for row in compiled if row["split"] == split])
    report["output"] = str(output)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
