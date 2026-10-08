"""Published envelope sections derived from the existing profile evidence."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..core.evidence import utc_now

ALLOWED_AVAILABILITY = {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed", "not_checked"}


def _availability(record: dict[str, Any] | None) -> str:
    status = str((record or {}).get("status") or "not_checked")
    if status == "available":
        return "available"
    if status in {"not_found", "not_available"}:
        return "not_available"
    if status == "blocked":
        return "blocked"
    if status in {"failed", "source_error"}:
        return "failed"
    if status == "not_applicable":
        return "not_applicable"
    if status in {"ambiguous", "not_fetched"}:
        return "ambiguous" if status == "ambiguous" else "not_checked"
    return "not_checked"


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _claim_span(record: dict[str, Any]) -> str:
    value = record.get("value")
    if isinstance(value, dict):
        for key in ("identity_text_excerpt", "main_text_excerpt", "title", "description", "claim_span"):
            if value.get(key):
                return str(value[key])[:1000]
    if value not in (None, "", [], {}):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)[:1000]
    return str(record.get("note") or f"source status: {record.get('status') or 'not_checked'}")[:1000]


def _evidence_id(org: str, field: str, record: dict[str, Any], index: int) -> str:
    seed = "|".join((org, field, str(record.get("source_url") or ""), str(record.get("retrieved_at") or ""), str(index)))
    return "ev-" + hashlib.sha256(seed.encode()).hexdigest()[:16]


def build_contract_sections(profile: dict[str, Any], *, run_id: str, started_at: str, completed_at: str) -> dict[str, Any]:
    """Build contract fields without changing the profile or inventing values."""
    org = str(profile.get("organisation_number") or "")
    records = profile.get("evidence") or {}
    output_evidence: list[dict[str, Any]] = []
    evidence_ids: dict[str, str] = {}
    for index, field in enumerate(sorted(records)):
        record = records.get(field)
        if not isinstance(record, dict):
            continue
        source_url = str(record.get("source_url") or "")
        value = record.get("value")
        content_hash = record.get("content_sha256") or _canonical_hash(value)
        identifier = _evidence_id(org, field, record, index)
        evidence_ids[field] = identifier
        output_evidence.append({
            "id": identifier,
            "source_url": source_url,
            "source_class": record.get("source_class") or record.get("source_type") or "unknown",
            "retrieved_at": record.get("retrieved_at") or completed_at,
            "content_sha256": content_hash,
            "claim_span": _claim_span(record),
        })

    def source(*names: str) -> tuple[str, dict[str, Any] | None]:
        for name in names:
            if isinstance(records.get(name), dict):
                return name, records[name]
        return names[0], None

    def claim(field: str, value: Any, record_name: str, record: dict[str, Any] | None, *, checked_empty: bool = False) -> dict[str, Any]:
        availability = _availability(record)
        if availability == "available" and (value in (None, "", [], {}) or checked_empty):
            availability = "not_available"
            value = {"checked": True} if checked_empty or value in (None, "", [], {}) else value
        refs = [evidence_ids[record_name]] if record_name in evidence_ids else []
        return {"field": field, "value": value, "availability": availability, "confidence": 1.0 if availability == "available" else 0.0, "evidence_ids": refs}

    claims: list[dict[str, Any]] = []
    if not profile.get("_missing_registry") and not profile.get("input_error"):
        name, registry = source("registry")
        claims.append(claim("legal_identity", (registry or {}).get("value"), name, registry))

        website_name, website = source("website")
        website_value = (website or {}).get("value") or {}
        brand = {key: website_value.get(key) for key in ("title", "description") if website_value.get(key)}
        claims.append(claim("public_brand", brand, website_name, website, checked_empty=bool(website and not brand)))

        financial_name, financial = source("financials")
        financial_value = (financial or {}).get("value") or {}
        account_records = financial_value.get("records") if isinstance(financial_value, dict) else []
        claims.append(claim("latest_annual_accounts", account_records[0] if account_records else None, financial_name, financial, checked_empty=bool(financial and not account_records)))

        history_name, history = source("financial_history")
        history_value = (history or {}).get("value") or {}
        if isinstance(history_value, dict):
            years_or_pdfs = history_value.get("years") or history_value.get("pdfs")
        else:
            years_or_pdfs = history_value
        claims.append(claim("accounts_history", history_value, history_name, history, checked_empty=bool(history and not years_or_pdfs)))

        roles_name, roles = source("roles")
        roles_value = (roles or {}).get("value") or {}
        claims.append(claim("leadership", roles_value.get("roles", roles_value), roles_name, roles, checked_empty=bool(roles and not roles_value.get("roles"))))

        locations_name, locations = source("locations")
        locations_value = (locations or {}).get("value") or {}
        claims.append(claim("registered_workplaces", locations_value.get("locations", locations_value), locations_name, locations, checked_empty=bool(locations and not locations_value.get("locations"))))

        group_name, group = source("group")
        claims.append(claim("group_links", (group or {}).get("value"), group_name, group, checked_empty=bool(group and not (group or {}).get("value"))))

        official_value = (profile.get("claims") or {}).get("official_website") or website_value
        claims.append(claim("official_website", official_value, website_name, website, checked_empty=bool(website and not official_value)))

        profiles_value = website_value.get("social_links") or []
        claims.append(claim("company_profiles", profiles_value, website_name, website, checked_empty=bool(website and not profiles_value)))

        nav_name, nav = source("nav_jobs")
        nav_value = (nav or {}).get("value") if nav else (profile.get("claims") or {}).get("nav_jobs")
        hiring_value = nav_value.get("ads") if isinstance(nav_value, dict) and "ads" in nav_value else nav_value
        claims.append(claim("hiring", hiring_value, nav_name, nav, checked_empty=bool(nav and not hiring_value)))

        refresh_record = {
            "status": "available",
            "source_url": "https://builderr.ai/docs/signalpost-evaluation-harness.md",
            "source_type": "runtime_metadata",
            "retrieved_at": completed_at or utc_now(),
            "value": {"run_id": run_id, "organisation_number": org, "run_metrics": profile.get("run_metrics") or {}, "recall_cache": profile.get("recall_cache")},
        }
        refresh_id = _evidence_id(org, "refresh_metadata", refresh_record, len(output_evidence))
        evidence_ids["refresh_metadata"] = refresh_id
        output_evidence.append({"id": refresh_id, "source_url": refresh_record["source_url"], "source_class": "runtime_metadata", "retrieved_at": completed_at, "content_sha256": _canonical_hash(refresh_record["value"]), "claim_span": json.dumps(refresh_record["value"], ensure_ascii=False, sort_keys=True)[:1000]})
        claims.append(claim("refresh_metadata", refresh_record["value"], "refresh_metadata", refresh_record))

    errors = list(profile.get("errors") or [])
    for field, record in records.items():
        if isinstance(record, dict) and record.get("status") in {"failed", "source_error"}:
            errors.append({"module": field, "kind": record.get("note") or record.get("status")})
    metrics = profile.get("run_metrics") or {}
    operations = {
        "requests": int(metrics.get("requests") or 0),
        "bytes": int(metrics.get("bytes") or 0),
        "runtime_ms": int(metrics.get("elapsed_ms") or 0),
        "third_party_cost_usd": 0.0,
    }
    terminal_status = "completed" if profile.get("_missing_registry") is not True and not profile.get("input_error") else "source_error"
    return {
        "run": {"run_id": run_id, "started_at": started_at, "completed_at": completed_at, "terminal_status": terminal_status},
        "claims": claims,
        "evidence": output_evidence,
        "errors": errors,
        "operations": operations,
    }


__all__ = ["ALLOWED_AVAILABILITY", "build_contract_sections"]
