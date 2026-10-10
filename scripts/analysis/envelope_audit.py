#!/usr/bin/env python3
"""Audit contract claims, evidence references and financial value fidelity."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from norway_company_agent.site_facts import JOB_NAV_WORDS, parse_date  # noqa: E402


REQUIRED_EVIDENCE = ("source_url", "source_class", "retrieved_at", "content_sha256", "claim_span")


def _value_has_reporting_period(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("period") or value.get("reporting_period"):
            return True
        records = value.get("records")
        if isinstance(records, list):
            return any(isinstance(item, dict) and (item.get("period") or item.get("reporting_period")) for item in records)
    if isinstance(value, list):
        return any(_value_has_reporting_period(item) for item in value)
    return False


def _same_bytes(left: Any, right: Any) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(right, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_JUNK_CTA_RE = re.compile(r"^\s*(?:slik\b|kontakt\s+oss\b|contact\s+us\b|les\s+mer\b|read\s+more\b|learn\s+more\b|bestill\b|klikk\b)", re.I)


def _junk_date(value: Any) -> date | None:
    parsed = parse_date(value)
    try:
        return date.fromisoformat(parsed) if parsed else None
    except ValueError:
        return None


def _claim_retrieval_dates(claim: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> list[date]:
    dates = []
    for ref in claim.get("evidence_ids") or []:
        retrieved = _junk_date((evidence_by_id.get(ref) or {}).get("retrieved_at"))
        if retrieved:
            dates.append(retrieved)
    return dates


def _normalized_junk_title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip(" .:-")


def detect_junk(rows: list[dict[str, Any]], *, as_of: Any = None) -> dict[str, Any]:
    """Count known site-fact furniture without changing the contract audit gate."""
    reference = _junk_date(as_of) or datetime.now(timezone.utc).date()
    counts = {
        "rows": len(rows),
        "hiring_nav_titles": 0,
        "activity_markup_claim_spans": 0,
        "activity_crawl_date_items": 0,
        "activity_old_items_informational": 0,
        "brief_cta_prefixes": 0,
    }
    for row in rows:
        evidence_by_id = {item.get("id"): item for item in row.get("evidence") or []}
        claims = {str(claim.get("field") or ""): claim for claim in row.get("claims") or []}
        hiring = claims.get("hiring") or {}
        hiring_value = hiring.get("value") or {}
        postings = hiring_value.get("postings") if isinstance(hiring_value, dict) else hiring_value
        for posting in postings or []:
            if isinstance(posting, dict) and _normalized_junk_title(posting.get("title")) in JOB_NAV_WORDS:
                counts["hiring_nav_titles"] += 1

        activity = claims.get("public_activity") or {}
        activity_value = activity.get("value") or {}
        items = activity_value.get("items", []) if isinstance(activity_value, dict) else []
        retrieval_dates = _claim_retrieval_dates(activity, evidence_by_id)
        crawl_date = max(retrieval_dates) if retrieval_dates else reference
        cutoff = crawl_date - timedelta(days=730)
        for item in items:
            if not isinstance(item, dict):
                continue
            claim_span = str(item.get("claim_span") or "")
            if "<" in claim_span or ">" in claim_span:
                counts["activity_markup_claim_spans"] += 1
            item_date = _junk_date(item.get("date"))
            if not item_date:
                continue
            if item_date == crawl_date:
                counts["activity_crawl_date_items"] += 1
            if item_date < cutoff:
                counts["activity_old_items_informational"] += 1

        brief = claims.get("company_brief") or {}
        brief_value = brief.get("value") or {}
        brief_texts = []
        if isinstance(brief_value, dict):
            brief_texts.extend(
                str(part.get("text") or "")
                for part in brief_value.get("parts") or []
                if isinstance(part, dict) and part.get("source_class") == "company_owned"
            )
            if not brief_texts:
                brief_texts.append(str(brief_value.get("summary") or ""))
        elif brief_value:
            brief_texts.append(str(brief_value))
        if any(_JUNK_CTA_RE.search(text) for text in brief_texts):
            counts["brief_cta_prefixes"] += 1
    actionable_keys = {
        "hiring_nav_titles",
        "activity_markup_claim_spans",
        "activity_crawl_date_items",
        "brief_cta_prefixes",
    }
    counts["total"] = sum(counts[key] for key in counts if key not in {"rows"})
    counts["passed"] = sum(counts[key] for key in actionable_keys) == 0
    return counts


def audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    availability: dict[str, Counter[str]] = defaultdict(Counter)
    missing_required: list[dict[str, Any]] = []
    financial_mismatches: list[dict[str, Any]] = []
    dated_item_errors: list[dict[str, Any]] = []
    for row in rows:
        evidence_by_id = {item.get("id"): item for item in row.get("evidence") or []}
        profile_evidence = (row.get("profile") or {}).get("evidence") or {}
        for claim in row.get("claims") or []:
            field = str(claim.get("field") or "")
            state = str(claim.get("availability") or "missing")
            availability[field][state] += 1
            if state == "available":
                refs = claim.get("evidence_ids") or []
                for ref in refs:
                    evidence_row = evidence_by_id.get(ref) or {}
                    missing = [key for key in REQUIRED_EVIDENCE if not evidence_row.get(key)]
                    if missing:
                        missing_required.append({"organisation_number": row.get("organisation_number"), "field": field, "evidence_id": ref, "missing": missing})
                    if field in {"latest_annual_accounts", "accounts_history"} and _value_has_reporting_period(claim.get("value")) and not evidence_row.get("reporting_period"):
                        missing_required.append({"organisation_number": row.get("organisation_number"), "field": field, "evidence_id": ref, "missing": ["reporting_period"]})
                if not refs:
                    missing_required.append({"organisation_number": row.get("organisation_number"), "field": field, "missing": ["evidence_ids"]})
            if field in {"latest_annual_accounts", "accounts_history"} and state == "available":
                history_source = profile_evidence.get("financial_history")
                if field == "accounts_history" and history_source:
                    source = history_source
                    expected = (source.get("value") or {})
                else:
                    source = profile_evidence.get("financials") or {}
                    records = sorted((source.get("value") or {}).get("records") or [], key=_record_key, reverse=True)
                    expected = (records[0] if records else None) if field == "latest_annual_accounts" else (records[1:] or None)
                if not _same_bytes(claim.get("value"), expected):
                    financial_mismatches.append({"organisation_number": row.get("organisation_number"), "field": field, "claim_value": claim.get("value"), "source_value": expected})
            if field in {"public_activity", "hiring"}:
                value = claim.get("value")
                if field == "public_activity":
                    items = (value or {}).get("items", []) if isinstance(value, dict) else []
                elif isinstance(value, dict):
                    items = [*(value.get("postings") or []), *(value.get("nav_ads") or [])]
                else:
                    items = value if isinstance(value, list) else []
                if field == "hiring" and claim.get("availability") == "available" and isinstance(value, dict):
                    has_postings = bool(value.get("postings") or value.get("nav_ads"))
                    has_verified_career = bool(value.get("careers_page_url"))
                    if not has_postings and not has_verified_career:
                        dated_item_errors.append({"organisation_number": row.get("organisation_number"), "field": field, "kind": "available_hiring_without_postings_or_verified_careers_page"})
                retrieved = []
                for ref in claim.get("evidence_ids") or []:
                    try:
                        timestamp = datetime.fromisoformat(str((evidence_by_id.get(ref) or {}).get("retrieved_at")).replace("Z", "+00:00"))
                        retrieved.append(timestamp.astimezone(timezone.utc).date())
                    except (TypeError, ValueError):
                        pass
                for item in items:
                    if not isinstance(item, dict):
                        dated_item_errors.append({"organisation_number": row.get("organisation_number"), "field": field, "kind": "dated_item_not_object"})
                        continue
                    date_value = item.get("date") or item.get("posted") or item.get("published")
                    parsed = parse_date(date_value) if date_value else None
                    if date_value and not parsed:
                        dated_item_errors.append({"organisation_number": row.get("organisation_number"), "field": field, "kind": "invalid_date", "value": date_value})
                    if parsed and retrieved and datetime.fromisoformat(parsed).date() > max(retrieved):
                        dated_item_errors.append({"organisation_number": row.get("organisation_number"), "field": field, "kind": "date_after_retrieval", "value": date_value})
                    if not (item.get("url") or item.get("source_url") or item.get("link")):
                        dated_item_errors.append({"organisation_number": row.get("organisation_number"), "field": field, "kind": "missing_source_url"})
    return {
        "rows": len(rows),
        "availability_counts": {field: dict(counts) for field, counts in sorted(availability.items())},
        "available_claims_missing_required_evidence": missing_required,
        "financial_value_mismatches": financial_mismatches,
        "dated_item_errors": dated_item_errors,
        "junk_detector": detect_junk(rows),
        "passed": not missing_required and not financial_mismatches and not dated_item_errors,
    }


def _record_key(record: dict) -> tuple[str, int]:
    period = record.get("period") or {}
    until = period.get("tilDato") if isinstance(period, dict) else str(period)
    try:
        record_id = int(record.get("record_id") or 0)
    except (TypeError, ValueError):
        record_id = 0
    return (str(until or ""), record_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--envelopes", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.envelopes).read_text(encoding="utf-8").splitlines() if line.strip()]
    report = audit(rows)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
    print(text, end="")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
