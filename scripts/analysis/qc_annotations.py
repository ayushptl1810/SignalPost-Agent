#!/usr/bin/env python3
"""Export and merge blind human QC worksheets for evaluation annotations."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


QC_CATEGORIES = ("site", "related", "no_site", "undetermined")
VERDICTS = {"official_site", "related_only", "no_site", "no_site_confirmed", "undetermined"}
PRIORITY_ORGANISATIONS = (
    "986801235",  # Niprox Technology: annotation says no site; pipeline found niprox.no.
    "929312457",  # Oslo Søppeltaxi: annotation says .as; pipeline found .no.
    "934970845",  # Geminor NO: related-only versus exact-entity rule conflict.
    "978664407",  # Taubane Teknikk: low-confidence undetermined row.
    "916329717",  # Strand Unikorn: low-confidence undetermined row.
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _rank(seed: int, organisation_number: str) -> str:
    return hashlib.sha256(f"{seed}|{organisation_number}".encode()).hexdigest()


def _category(outcome: str) -> str:
    return {
        "official_site": "site",
        "related_only": "related",
        "no_site_confirmed": "no_site",
        "no_site": "no_site",
        "undetermined": "undetermined",
    }[outcome]


def _load_registry(path: Path, targets: set[str]) -> dict[str, dict[str, str]]:
    """Load only target registry rows without materialising the whole export."""
    opener = gzip.open if path.read_bytes()[:2] == b"\x1f\x8b" else open
    found: dict[str, dict[str, str]] = {}
    with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
        fields = next(csv.reader([next(handle)]))
        for line in handle:
            organisation_number = line.split(",", 1)[0].strip('"')
            if organisation_number in targets:
                values = next(csv.reader([line]))
                found[organisation_number] = dict(zip(fields, values))
    return found


def choose_qc_rows(rows: list[dict[str, Any]], *, seed: int = 20261009) -> list[dict[str, Any]]:
    """Select a deterministic, blindable QC subset."""
    selected: dict[str, dict[str, Any]] = {}
    by_org = {str(row["organisation_number"]): row for row in rows}
    priority = [by_org[org] for org in PRIORITY_ORGANISATIONS if org in by_org]
    selected.update({str(row["organisation_number"]): row for row in priority})

    no_site = [row for row in rows if row.get("outcome") == "no_site_confirmed"]
    low_confidence = [row for row in no_site if row.get("confidence") == "low"]
    for row in low_confidence:
        selected[str(row["organisation_number"])] = row

    quotas = {"S1": 13, "S4": 13, "S5": 14}
    for stratum, quota in quotas.items():
        candidates = sorted(
            (row for row in no_site if row.get("stratum") == stratum and str(row["organisation_number"]) not in selected),
            key=lambda row: _rank(seed, str(row["organisation_number"])),
        )
        for row in candidates[: max(0, quota - sum(1 for item in selected.values() if item.get("stratum") == stratum))]:
            selected[str(row["organisation_number"])] = row

    for row in rows:
        if row.get("outcome") == "official_site" and row.get("split") in {"held_out", "validation"}:
            selected[str(row["organisation_number"])] = row

    uncertain = sorted(
        (row for row in rows if row.get("outcome") == "undetermined" and row.get("stratum") in {"S5", "S6"}),
        key=lambda row: _rank(seed, str(row["organisation_number"])),
    )
    for row in uncertain[:30]:
        selected[str(row["organisation_number"])] = row
    priority_orgs = {str(row["organisation_number"]) for row in priority}
    remainder = sorted(
        (row for org, row in selected.items() if org not in priority_orgs),
        key=lambda row: _rank(seed, str(row["organisation_number"])),
    )
    return [*priority, *remainder]


def _registry_value(registry: dict[str, str], *keys: str) -> str:
    for key in keys:
        if registry.get(key):
            return registry[key]
    return ""


def worksheet_rows(annotation_rows: list[dict[str, Any]], registry: dict[str, dict[str, str]], *, seed: int = 20261009) -> list[dict[str, Any]]:
    rows = []
    for row in choose_qc_rows(annotation_rows, seed=seed):
        org = str(row["organisation_number"])
        raw = registry.get(org, {})
        rows.append({
            "organisation_number": org,
            "name": row.get("name", ""),
            "municipality": row.get("municipality") or _registry_value(raw, "forretningsadresse.kommune", "postadresse.kommune"),
            "registry_address": _registry_value(raw, "forretningsadresse.adresse", "postadresse.adresse"),
            "registry_email": raw.get("epostadresse", ""),
            "registry_phone": _registry_value(raw, "telefon", "mobil"),
            "source_urls": " | ".join(row.get("source_urls") or []),
            "split": row.get("split", ""),
            "stratum": row.get("stratum", ""),
            "reviewer_verdict": "",
            "reviewer_domain": "",
            "reviewer_notes": "",
            # Deliberately after the blank reviewer fields: this is not used by
            # the human-facing decision columns, but remains auditable.
            "outcome_v1": row.get("outcome", ""),
            "domain_v1": row.get("domain") or "",
            "confidence_v1": row.get("confidence", ""),
        })
    return rows


def export_worksheet(annotations_path: Path, registry_path: Path, csv_path: Path, jsonl_path: Path, *, seed: int) -> int:
    annotations = read_jsonl(annotations_path)
    targets = {str(row["organisation_number"]) for row in choose_qc_rows(annotations, seed=seed)}
    registry = _load_registry(registry_path, targets) if registry_path.exists() else {}
    rows = worksheet_rows(annotations, registry, seed=seed)
    if not rows:
        raise ValueError("QC selection is empty")
    fields = list(rows[0])
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    jsonl_path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    return len(rows)


def _profile_evidence(row: dict[str, Any] | None) -> tuple[str, str, str]:
    if not row:
        return "", "", ""
    evidence = row.get("evidence") or {}
    published = evidence.get("website_discovered") or evidence.get("website") or {}
    value = published.get("value") or {}
    urls: list[str] = []
    for item in (published.get("source_url"), value.get("requested_url"), value.get("final_url"), *(value.get("redirect_chain") or [])):
        if item and str(item) not in urls:
            urls.append(str(item))
    for candidate in evidence.get("website_discovered_candidates") or []:
        for item in (candidate.get("url"), candidate.get("matched_url"), candidate.get("final_url")):
            if item and str(item) not in urls:
                urls.append(str(item))
    first_party = value.get("first_party_assessment") or {}
    return str(first_party.get("candidate_domain") or value.get("registered_domain") or ""), " | ".join(urls), str((value.get("identity_assessment") or {}).get("score") or "")


def disagreement_rows(annotations_path: Path, scorecard_path: Path, *, profiles_path: Path | None = None) -> list[dict[str, Any]]:
    """Create an adjudication sheet without silently deciding which side is right."""
    annotations = {str(row["organisation_number"]): row for row in read_jsonl(annotations_path)}
    card = json.loads(scorecard_path.read_text(encoding="utf-8"))
    errors = {
        str(row["organisation_number"]): row
        for row in (card.get("annotations") or {}).get("errors") or []
        if row.get("error") in {"wrong_url", "related_as_official", "fn"}
    }
    targets = set(errors) | {org for org in PRIORITY_ORGANISATIONS if org in annotations}
    profile_file = profiles_path
    if profile_file is None and card.get("profiles_path"):
        profile_file = Path(str(card["profiles_path"]))
    if profile_file is None:
        inferred = scorecard_path.with_name(scorecard_path.name.replace("scorecard", "discovery").replace(".json", ".jsonl"))
        if inferred.exists():
            profile_file = inferred
    pipeline = {}
    if profile_file and profile_file.exists():
        pipeline = {str(row["organisation_number"]): row for row in read_jsonl(profile_file)}
    predictions = (card.get("verdicts") or {})
    rows: list[dict[str, Any]] = []
    for org in sorted(targets, key=lambda item: (PRIORITY_ORGANISATIONS.index(item) if item in PRIORITY_ORGANISATIONS else len(PRIORITY_ORGANISATIONS), item)):
        annotation = annotations[org]
        prediction = predictions.get(org) or {}
        domain, evidence_urls, identity_score = _profile_evidence(pipeline.get(org))
        rows.append({
            "organisation_number": org,
            "name": annotation.get("name", ""),
            "annotation_outcome": annotation.get("outcome", ""),
            "annotation_domain": annotation.get("domain") or "",
            "annotation_source_urls": " | ".join(annotation.get("source_urls") or []),
            "pipeline_outcome": prediction.get("outcome", ""),
            "pipeline_domain": prediction.get("domain") or domain,
            "pipeline_source": prediction.get("source", ""),
            "pipeline_identity_score": identity_score,
            "pipeline_evidence_urls": evidence_urls,
            "disagreement_type": (errors.get(org) or {}).get("error", "priority_review"),
            "reviewer_verdict": "",
            "reviewer_domain": "",
            "reviewer_notes": "",
        })
    return rows


def export_disagreements(annotations_path: Path, scorecard_path: Path, csv_path: Path, jsonl_path: Path, *, profiles_path: Path | None = None) -> int:
    rows = disagreement_rows(annotations_path, scorecard_path, profiles_path=profiles_path)
    if not rows:
        raise ValueError("disagreement selection is empty")
    fields = list(rows[0])
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    jsonl_path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    return len(rows)


def cohen_kappa(first: Iterable[str], second: Iterable[str]) -> float | None:
    first_list, second_list = list(first), list(second)
    if len(first_list) != len(second_list) or not first_list:
        return None
    n = len(first_list)
    observed = sum(a == b for a, b in zip(first_list, second_list)) / n
    a_counts, b_counts = Counter(first_list), Counter(second_list)
    expected = sum(a_counts[category] * b_counts[category] for category in QC_CATEGORIES) / (n * n)
    if expected == 1:
        return 1.0
    return round((observed - expected) / (1 - expected), 4)


def merge_worksheet(annotations_path: Path, worksheet_path: Path, output_path: Path, *, qc_by: str) -> dict[str, Any]:
    original = {str(row["organisation_number"]): row for row in read_jsonl(annotations_path)}
    with worksheet_path.open(encoding="utf-8", newline="") as handle:
        reviewed = {str(row["organisation_number"]): row for row in csv.DictReader(handle)}
    if not set(reviewed) <= set(original):
        raise ValueError("worksheet contains organisations outside the annotation set")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    merged: list[dict[str, Any]] = []
    old_categories: list[str] = []
    new_categories: list[str] = []
    changed_by_old: Counter[str] = Counter()
    for org, row in original.items():
        result = dict(row)
        result["outcome_v1"] = row.get("outcome")
        result["domain_v1"] = row.get("domain")
        worksheet = reviewed.get(org)
        verdict = (worksheet or {}).get("reviewer_verdict", "").strip()
        if not verdict:
            result.update({"qc_status": "not_reviewed", "qc_by": None, "qc_at": None})
        else:
            if verdict not in VERDICTS:
                raise ValueError(f"invalid reviewer verdict for {org}: {verdict}")
            normalized = "no_site_confirmed" if verdict == "no_site" else verdict
            reviewer_domain = (worksheet or {}).get("reviewer_domain", "").strip() or None
            if normalized in {"official_site", "related_only"} and not reviewer_domain:
                raise ValueError(f"{normalized} reviewer verdict requires a domain for {org}")
            result["outcome"] = normalized
            result["domain"] = reviewer_domain if normalized in {"official_site", "related_only"} else None
            result.update({
                "qc_status": "confirmed" if normalized == row.get("outcome") and result["domain"] == row.get("domain") else "changed",
                "qc_by": qc_by,
                "qc_at": now,
                "qc_notes": (worksheet or {}).get("reviewer_notes", ""),
            })
            old_categories.append(_category(str(row.get("outcome"))))
            new_categories.append(_category(normalized))
            if result["qc_status"] == "changed":
                changed_by_old[str(row.get("outcome"))] += 1
        merged.append(result)
    reviewed_count = len(old_categories)
    agreement = round(sum(a == b for a, b in zip(old_categories, new_categories)) / reviewed_count, 4) if reviewed_count else None
    report = {
        "rows": len(merged),
        "reviewed": reviewed_count,
        "agreement_rate": agreement,
        "cohen_kappa": cohen_kappa(old_categories, new_categories),
        "changed_by_original_outcome": dict(changed_by_old),
        "qc_by": qc_by,
        "qc_at": now,
        "output": str(output_path),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in merged), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Blind QC worksheet tools for evaluation annotations.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--annotations", default="out/eval-sample/annotations-v1.jsonl")
    export.add_argument("--registry", default="data/brreg-enheter.csv")
    export.add_argument("--csv-output")
    export.add_argument("--jsonl-output")
    export.add_argument("--seed", type=int, default=20261009)
    export.add_argument("--disagreements", action="store_true")
    export.add_argument("--scorecard", help="Scorecard JSON for disagreement export")
    export.add_argument("--profiles", help="Optional discovery JSONL; otherwise use scorecard profiles_path")
    merge = subparsers.add_parser("merge")
    merge.add_argument("--annotations", default="out/eval-sample/annotations-v1.jsonl")
    merge.add_argument("--worksheet", default="out/eval-sample/qc-worksheet.csv")
    merge.add_argument("--output", default="out/eval-sample/annotations-v2.jsonl")
    merge.add_argument("--qc-by", required=True)
    args = parser.parse_args()
    if args.command == "export":
        if args.disagreements:
            if not args.scorecard:
                parser.error("--disagreements requires --scorecard")
            csv_output = args.csv_output or "out/eval-sample/qc-disagreements.csv"
            jsonl_output = args.jsonl_output or "out/eval-sample/qc-disagreements.jsonl"
            count = export_disagreements(Path(args.annotations), Path(args.scorecard), Path(csv_output), Path(jsonl_output), profiles_path=Path(args.profiles) if args.profiles else None)
        else:
            csv_output = args.csv_output or "out/eval-sample/qc-worksheet.csv"
            jsonl_output = args.jsonl_output or "out/eval-sample/qc-worksheet.jsonl"
            count = export_worksheet(Path(args.annotations), Path(args.registry), Path(csv_output), Path(jsonl_output), seed=args.seed)
        print(json.dumps({"rows": count, "csv": csv_output, "jsonl": jsonl_output}, indent=2))
    else:
        print(json.dumps(merge_worksheet(Path(args.annotations), Path(args.worksheet), Path(args.output), qc_by=args.qc_by), indent=2))


if __name__ == "__main__":
    main()
