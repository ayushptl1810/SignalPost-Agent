#!/usr/bin/env python3
"""Export and merge a blind human audit of external observations."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.web.website import registered_domain  # noqa: E402


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _key(item: dict[str, Any]) -> str:
    return hashlib.sha256(str(item.get("id", "")).encode()).hexdigest()


def _excerpt(item: dict[str, Any]) -> str:
    value = item.get("evidence_span") or item.get("notes") or ""
    return " ".join(str(value).split())[:500]


def _profile_index(profiles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for row in profiles:
        org = str(row.get("organisation_number") or "")
        registry = (row.get("evidence", {}).get("registry_live") or row.get("evidence", {}).get("registry") or {}).get("value") or {}
        address = row.get("business_address") or row.get("address") or registry.get("forretningsadresse") or registry.get("postadresse") or {}
        if not address and any(key in registry for key in ("forretningsadresse.adresse", "forretningsadresse.postnummer", "forretningsadresse.poststed")):
            address = {
                "adresse": registry.get("forretningsadresse.adresse", ""),
                "postnummer": registry.get("forretningsadresse.postnummer", ""),
                "poststed": registry.get("forretningsadresse.poststed", ""),
            }
        if isinstance(address, dict):
            street = address.get("adresse") or address.get("street") or ""
            if isinstance(street, list):
                street = ", ".join(str(item) for item in street if item)
            address_text = ", ".join(
                value for value in (str(street).strip(), str(address.get("postnummer") or address.get("postal_code") or "").strip(), str(address.get("poststed") or address.get("city") or "").strip()) if value
            )
        else:
            address_text = str(address or "")
        phone = registry.get("telefon") or registry.get("mobil") or registry.get("phone") or row.get("phone") or ""
        email = registry.get("epostadresse") or registry.get("email") or row.get("email") or ""
        site = row.get("website") or registry.get("hjemmeside") or ""
        result[org] = {
            "registry_name": row.get("name") or registry.get("name") or registry.get("navn") or "",
            "registry_address": address_text,
            "registry_phone": phone,
            "registry_email_domain": str(email).split("@", 1)[1].casefold() if "@" in str(email) else str(email),
            "verified_site_domain": registered_domain(str(site)) if site else "",
            "organisation_number": org,
        }
    return result


def stratified_sample(observations: list[dict[str, Any]], minimum: int = 100) -> list[dict[str, Any]]:
    ordered = sorted(observations, key=_key)
    if len(ordered) <= minimum:
        return ordered
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in ordered:
        groups.setdefault((str(row.get("platform") or ""), str(row.get("signal_type") or "")), []).append(row)
    group_count = len(groups)
    quotas = {key: max(1, minimum // group_count) for key in groups}
    remainder = minimum - sum(quotas.values())
    for key in sorted(groups):
        if remainder <= 0:
            break
        quotas[key] += 1
        remainder -= 1
    chosen: list[dict[str, Any]] = []
    for key in sorted(groups):
        chosen.extend(groups[key][:quotas[key]])
    if len(chosen) < minimum:
        selected = {str(item.get("id")) for item in chosen}
        chosen.extend(item for item in ordered if str(item.get("id")) not in selected)
        chosen = chosen[:minimum]
    return sorted(chosen[:minimum], key=_key)


AUDIT_FIELDS = [
    "id", "organisation_number", "registry_name", "registry_address", "registry_phone", "registry_email_domain",
    "verified_site_domain", "connector_id", "platform", "signal_type", "source_url", "found_on_url", "handle_text",
    "ad_title", "ad_published_at", "ad_expires_at", "place_name", "place_address", "place_phone",
    "evidence_excerpt", "assistant_draft_exact_entity", "exact_entity", "metric_correct", "sentiment_correct", "notes",
]


def _annotation_domains(path: str | Path | None) -> dict[str, str]:
    if not path or not Path(path).exists():
        return {}
    result: dict[str, str] = {}
    for row in read_jsonl(path):
        domain = row.get("domain")
        if domain and row.get("outcome") == "official_site":
            result[str(row.get("organisation_number"))] = str(domain).casefold().removeprefix("www.")
    return result


def _draft_exact_entity(item: dict[str, Any], profile: dict[str, Any], official_domains: dict[str, str]) -> tuple[str, str]:
    """Return an objective draft, never a social-handle guess."""
    signal = str(item.get("signal_type") or "")
    org = str(item.get("organisation_number") or "")
    if signal == "company_profile":
        observed = registered_domain(str(item.get("source_url") or ""))
        expected = official_domains.get(org) or str(profile.get("verified_site_domain") or "")
        if observed and expected:
            return ("yes" if observed == expected else "no", "source domain compared with adjudicated official domain")
    if signal == "job_posting" and item.get("organisation_number") and item.get("exact_entity") is True:
        return "yes", "NAV observation is keyed by the exact organisation number"
    return "", ""


def _observation_display(item: dict[str, Any]) -> dict[str, Any]:
    metrics = item.get("metrics") or {}
    return {
        "found_on_url": item.get("website_source_url") or next((proof.get("source_url") for proof in item.get("identity_proof", []) if proof.get("source_url")), "") if item.get("signal_type") == "profile_handle" else "",
        "handle_text": str(item.get("handle") or item.get("channel_id") or (str(item.get("source_url") or "").rstrip("/").rsplit("/", 1)[-1] if item.get("signal_type") == "profile_handle" else "")),
        "ad_title": item.get("title") or item.get("ad_title") or "",
        "ad_published_at": item.get("published_at") or "",
        "ad_expires_at": item.get("expires_at") or item.get("expiration_date") or "",
        "place_name": item.get("name") or metrics.get("name") or "",
        "place_address": item.get("address") or metrics.get("address") or "",
        "place_phone": item.get("phone") or metrics.get("phone") or "",
    }


def export_audit(
    observations: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    output: str | Path,
    *,
    minimum: int = 100,
    labels_path: str | Path | None = None,
    annotation_path: str | Path | None = None,
    drafts_output: str | Path | None = None,
    instructions_output: str | Path | None = None,
) -> dict[str, Any]:
    by_org = _profile_index(profiles)
    official_domains = _annotation_domains(annotation_path)
    existing = set()
    if labels_path and Path(labels_path).exists():
        existing = {str(row.get("id")) for row in read_jsonl(labels_path) if row.get("id")}
    pending = [item for item in observations if str(item.get("id")) not in existing]
    rows = []
    drafts = []
    for item in stratified_sample(pending, minimum):
        profile = by_org.get(str(item.get("organisation_number")), {})
        draft, draft_reason = _draft_exact_entity(item, profile, official_domains)
        display = _observation_display(item)
        if draft:
            drafts.append({"id": item.get("id", ""), "assistant_draft_exact_entity": draft, "reason": draft_reason})
        rows.append({
            "id": item.get("id", ""),
            "organisation_number": item.get("organisation_number", ""),
            "registry_name": profile.get("registry_name", ""),
            "registry_address": profile.get("registry_address", ""),
            "registry_phone": profile.get("registry_phone", ""),
            "registry_email_domain": profile.get("registry_email_domain", ""),
            "verified_site_domain": profile.get("verified_site_domain", ""),
            "connector_id": item.get("connector_id", ""),
            "platform": item.get("platform", ""),
            "signal_type": item.get("signal_type", ""),
            "source_url": item.get("source_url", ""),
            **display,
            "evidence_excerpt": _excerpt(item),
            "assistant_draft_exact_entity": draft,
            "exact_entity": "",
            "metric_correct": "",
            "sentiment_correct": "",
            "notes": "",
        })
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    if drafts_output:
        draft_path = Path(drafts_output)
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in drafts), encoding="utf-8")
    if instructions_output:
        write_audit_instructions(instructions_output)
    return {"rows": len(rows), "output": str(output), "platforms": sorted({row["platform"] for row in rows}), "blind": True, "excluded_labelled": len(existing), "drafts": len(drafts)}


def write_audit_instructions(output: str | Path) -> None:
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text("""# Observation audit instructions

Label each row independently. `exact_entity` asks whether the profile, site, ad or place belongs to this exact legal entity—not a parent, group, partner, product brand, franchise or person. `metric_correct` asks whether the displayed number matches the live source within normal drift. Use `unsure` in the review page when evidence is ambiguous; do not guess. The assistant draft is a separate, objective hint and is never a human verdict.

- Company-site rows: check the visible legal identity and domain; about 30–60 seconds.
- Social-handle rows: open both `source_url` and `found_on_url`; check ownership, not merely a link; about 45–90 seconds.
- NAV rows: verify the organisation number, title and active dates; about 30–45 seconds.
- Places rows: check name, address and phone against the registry; about 45–90 seconds.

Leave human verdict fields blank in the worksheet until reviewed. The merge command accepts only `yes`/`no` for exact entity and `yes`/`no`/`not_applicable` for metrics; unresolved rows remain unmerged.
""", encoding="utf-8")


def _answer(value: str, allowed: set[str], field: str, row_id: str) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized not in allowed:
        raise ValueError(f"{field} for {row_id} must be one of {sorted(allowed)}")
    return normalized


def _read_label_input(input_path: str | Path) -> list[dict[str, Any]]:
    path = Path(input_path)
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    if path.suffix.casefold() == ".jsonl" or text.lstrip().startswith("{"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def merge_audit(input_csv: str | Path, output_jsonl: str | Path, *, labeler: str, labeled_at: str | None = None) -> dict[str, Any]:
    if labeler not in {"owner", "codex_review"}:
        raise ValueError("labeler must be owner or codex_review")
    timestamp = labeled_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    existing = {str(row.get("id")): row for row in _read_label_input(output_jsonl)} if Path(output_jsonl).exists() else {}
    for existing_row in existing.values():
        if existing_row.get("labeler") == "owner" and (
            existing_row.get("export_source") != "audit-review.html"
            or not str(existing_row.get("export_session_id") or "").strip()
        ):
            raise ValueError(f"existing owner label {existing_row.get('id')} lacks an audit-review export marker")
    labels = list(existing.values())
    added = 0
    for row in _read_label_input(input_csv):
            row_id = str(row.get("id") or "")
            if not row_id:
                raise ValueError("audit row is missing id")
            exact_value = row.get("exact_entity", "")
            metric_value = row.get("metric_correct", "")
            if isinstance(exact_value, bool):
                exact_value = "yes" if exact_value else "no"
            if isinstance(metric_value, bool):
                metric_value = "yes" if metric_value else "no"
            exact = _answer(str(exact_value), {"yes", "no"}, "exact_entity", row_id)
            metric = _answer(str(metric_value), {"yes", "no", "not_applicable"}, "metric_correct", row_id)
            sentiment_raw = str(row.get("sentiment_correct") or "").strip().casefold()
            if sentiment_raw not in {"", "yes", "no"}:
                raise ValueError(f"sentiment_correct for {row_id} must be yes/no or blank")
            row_labeler = str(row.get("labeler") or labeler).strip()
            if row_labeler != labeler:
                raise ValueError(f"labeler mismatch for {row_id}: expected {labeler}, got {row_labeler}")
            notes = str(row.get("notes") or "").strip()
            if labeler == "codex_review" and exact == "no" and not notes:
                raise ValueError(f"assistant no label {row_id} requires notes describing the checked URL and finding")
            if labeler == "owner":
                if row.get("export_source") != "audit-review.html":
                    raise ValueError(f"owner label {row_id} must come from audit-review.html")
                if not str(row.get("export_session_id") or "").strip():
                    raise ValueError(f"owner label {row_id} is missing export_session_id")
            label = {
                "id": row_id,
                "exact_entity": exact == "yes",
                "metric_correct": metric == "yes" or metric == "not_applicable",
                "sentiment_correct": None if not sentiment_raw else sentiment_raw == "yes",
                "labeler": labeler,
                "labeled_at": timestamp,
                "notes": notes,
            }
            if labeler == "owner":
                label.update({
                    "export_source": "audit-review.html",
                    "export_session_id": str(row.get("export_session_id")).strip(),
                })
            labels.append(label)
            added += 1
            existing[row_id] = labels[-1]
    labels = list(existing.values())
    output = Path(output_jsonl)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in labels), encoding="utf-8")
    return {"labels": len(labels), "added": added, "output": str(output), "refused_unsure_or_blank": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the blind observation audit worksheet.")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--observations", required=True)
    export.add_argument("--profiles", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--minimum", type=int, default=100)
    export.add_argument("--labels")
    export.add_argument("--annotation-path", default=str(ROOT / "out/eval-sample/annotations-v2-adjudicated.jsonl"))
    export.add_argument("--drafts-output")
    export.add_argument("--instructions-output", default=str(ROOT / "out/proxy/audit-instructions.md"))
    merge = sub.add_parser("merge")
    merge.add_argument("--input", required=True)
    merge.add_argument("--output", required=True)
    merge.add_argument("--labeler", required=True)
    merge.add_argument("--labeled-at")
    args = parser.parse_args()
    result = export_audit(
        read_jsonl(args.observations), read_jsonl(args.profiles), args.output, minimum=args.minimum,
        labels_path=args.labels, annotation_path=args.annotation_path, drafts_output=args.drafts_output,
        instructions_output=args.instructions_output,
    ) if args.command == "export" else merge_audit(args.input, args.output, labeler=args.labeler, labeled_at=args.labeled_at)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
