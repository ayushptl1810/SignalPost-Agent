"""Published envelope sections derived from the existing profile evidence."""
from __future__ import annotations

import hashlib
import json
import urllib.parse
from typing import Any

from ..core.evidence import evidence, infer_extraction_method, utc_now
from ..web.website import normalize_social_url

ALLOWED_AVAILABILITY = {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed", "not_checked"}
FALLBACK_EVIDENCE_URL = "https://builderr.ai/docs/signalpost-evaluation-harness.md"


def _account_sort_key(record: dict[str, Any]) -> tuple[str, int]:
    period = record.get("period") or {}
    until = (period.get("tilDato") or period.get("til_dato") or "") if isinstance(period, dict) else str(period)
    try:
        record_id = int(record.get("record_id") or 0)
    except (TypeError, ValueError):
        record_id = 0
    return (str(until), record_id)


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


def _stable(value: Any) -> Any:
    """Make any profile value JSON-serialisable with sorted keys (a None key or a set must not crash a run)."""
    if isinstance(value, dict):
        return {str(key): _stable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_stable(item) for item in value), key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
    return value


def _dumps_sorted(value: Any, **kwargs: Any) -> str:
    return json.dumps(_stable(value), ensure_ascii=False, sort_keys=True, default=str, **kwargs)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_dumps_sorted(value, separators=(",", ":")).encode()).hexdigest()


def _claim_span(record: dict[str, Any]) -> str:
    value = record.get("value")
    if isinstance(value, dict):
        for key in ("identity_text_excerpt", "main_text_excerpt", "title", "description", "claim_span"):
            if value.get(key):
                return str(value[key])[:1000]
    if value not in (None, "", [], {}):
        return _dumps_sorted(value)[:1000]
    return str(record.get("note") or f"source status: {record.get('status') or 'not_checked'}")[:1000]


def _evidence_id(org: str, field: str, record: dict[str, Any], index: int) -> str:
    seed = "|".join((org, field, str(record.get("source_url") or ""), str(record.get("retrieved_at") or ""), str(index)))
    return "ev-" + hashlib.sha256(seed.encode()).hexdigest()[:16]


def _effective_at(value: Any) -> Any:
    """Extract source-effective dates without confusing them with retrieval time."""
    dates: list[str] = []

    def add(candidate: Any) -> None:
        if candidate in (None, "", [], {}):
            return
        if isinstance(candidate, (list, tuple, set, frozenset)):
            for item in candidate:
                add(item)
            return
        text = str(candidate)
        if text not in dates:
            dates.append(text)

    if isinstance(value, dict):
        for item in value.get("items") or []:
            if isinstance(item, dict):
                add(item.get("effective_at") or item.get("date"))
        for item in [*(value.get("postings") or []), *(value.get("nav_ads") or [])]:
            if isinstance(item, dict):
                add(item.get("effective_at") or item.get("posted") or item.get("published") or item.get("date"))
        for item in value.get("records") or []:
            if isinstance(item, dict):
                period = item.get("period")
                if isinstance(period, dict):
                    add(period.get("tilDato") or period.get("til_dato") or period.get("to"))
                else:
                    add(period)
        period = value.get("period")
        if isinstance(period, dict):
            add(period.get("tilDato") or period.get("til_dato") or period.get("to"))
        elif period:
            add(period)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                add(item.get("effective_at") or item.get("date") or item.get("posted") or item.get("published"))
    if not dates:
        return None
    return dates[0] if len(dates) == 1 else dates


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
        source_url = str(record.get("source_url") or FALLBACK_EVIDENCE_URL)
        value = record.get("value")
        content_hash = record.get("content_sha256") or _canonical_hash(value)
        identifier = _evidence_id(org, field, record, index)
        evidence_ids[field] = identifier
        evidence_row = {
            "id": identifier,
            "source_url": source_url,
            "source_class": record.get("source_class") or record.get("source_type") or "unknown",
            "retrieved_at": record.get("retrieved_at") or completed_at,
            "content_sha256": content_hash,
            "claim_span": _claim_span(record),
            "extraction_method": record.get("extraction_method") or infer_extraction_method(record.get("source_type") or record.get("source_class"), field=field),
        }
        effective_at = record.get("effective_at") or _effective_at(record.get("value"))
        if effective_at:
            evidence_row["effective_at"] = effective_at
        if field in {"financials", "financial_history", "latest_annual_accounts", "accounts_history"}:
            value_period = record.get("as_of") or record.get("reporting_period")
            if not value_period:
                value = record.get("value") or {}
                if isinstance(value, dict) and value.get("records"):
                    value_period = [item.get("period") for item in value["records"] if isinstance(item, dict) and item.get("period")]
                elif isinstance(value, dict) and value.get("period"):
                    value_period = value.get("period")
            if value_period:
                evidence_row["reporting_period"] = value_period
        output_evidence.append(evidence_row)

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

    def repeated_evidence(
        field: str,
        *,
        source_url: str,
        source_class: str,
        retrieved_at: str,
        content_sha256: str,
        claim_span: str,
        extraction_method: str,
        effective_at: Any = None,
        discovered_via: str | None = None,
    ) -> str:
        index = len(output_evidence)
        record = {"source_url": source_url, "retrieved_at": retrieved_at, "content_sha256": content_sha256}
        identifier = _evidence_id(org, field, record, index)
        row = {
            "id": identifier,
            "source_url": source_url,
            "source_class": source_class,
            "retrieved_at": retrieved_at,
            "content_sha256": content_sha256,
            "claim_span": claim_span[:1000],
            "extraction_method": extraction_method,
        }
        if effective_at:
            row["effective_at"] = effective_at
        if discovered_via:
            row["discovered_via"] = discovered_via
        output_evidence.append(row)
        return identifier

    def repeated_claim(field: str, value: Any, evidence_id: str, **details: Any) -> dict[str, Any]:
        return {"field": field, "value": value, "availability": "available", "confidence": 1.0, "evidence_ids": [evidence_id], **details}

    def social_match_key(url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        return f"{(parsed.hostname or '').casefold().removeprefix('www.')}{parsed.path.casefold()}"

    def social_link_proof(website: dict[str, Any], social_url: str) -> tuple[str, str, str, str]:
        value = website.get("value") or {}
        target = normalize_social_url(social_url) or {"url": social_url}
        target_url = str(target.get("url") or social_url).casefold()
        containers = [value, *(value.get("pages") or [])]
        for container in containers:
            if not isinstance(container, dict):
                continue
            for link in container.get("links") or []:
                if not isinstance(link, dict):
                    continue
                href = str(link.get("url") or link.get("href") or "")
                normalized = normalize_social_url(href)
                if normalized and str(normalized.get("url") or "").casefold() == target_url:
                    return (str(container.get("url") or website.get("source_url") or FALLBACK_EVIDENCE_URL), str(link.get("text") or href or social_url), "html_link", str(container.get("content_sha256") or value.get("content_sha256") or website.get("content_sha256") or _canonical_hash(value)))

        def same_as(node: Any) -> bool:
            if isinstance(node, dict):
                values = node.get("sameAs")
                values = values if isinstance(values, list) else [values]
                for candidate in values:
                    normalized = normalize_social_url(str(candidate or ""))
                    if normalized and str(normalized.get("url") or "").casefold() == target_url:
                        return True
                return any(same_as(child) for child in node.values())
            if isinstance(node, list):
                return any(same_as(child) for child in node)
            return False

        if same_as(value.get("structured_organisations") or []) or same_as(value.get("json_ld") or []):
            return (str(value.get("final_url") or website.get("source_url") or FALLBACK_EVIDENCE_URL), social_url, "jsonld_sameas", str(value.get("content_sha256") or website.get("content_sha256") or _canonical_hash(value)))
        return (str(value.get("final_url") or website.get("source_url") or FALLBACK_EVIDENCE_URL), social_url, "html_link", str(value.get("content_sha256") or website.get("content_sha256") or _canonical_hash(value)))

    claims: list[dict[str, Any]] = []
    if not profile.get("_missing_registry") and not profile.get("input_error"):
        name, registry = source("registry")
        claims.append(claim("legal_identity", (registry or {}).get("value"), name, registry))

        website_name, website = source("website")
        website_value = (website or {}).get("value") or {}
        brand = {key: website_value.get(key) for key in ("title", "description") if website_value.get(key)}
        claims.append(claim("public_brand", brand, website_name, website, checked_empty=bool(website and not brand)))

        brief_name, brief = source("company_brief")
        if brief is None and registry is not None:
            raw = (registry.get("value") or {}) if isinstance(registry.get("value"), dict) else {}
            municipality = raw.get("forretningsadresse.kommune") or raw.get("postadresse.kommune") or profile.get("municipality") or "an unreported municipality"
            industry = raw.get("naeringskode1.beskrivelse") or raw.get("naeringskode1_beskrivelse") or raw.get("industry_label") or "not reported"
            registry_text = f"Registrert i {municipality}."
            if industry and str(industry).casefold() not in {"uoppgitt", "ikke oppgitt", "not reported"}:
                registry_text = f"{registry_text} Bransje: {industry}."
            brief = evidence(
                "company_brief",
                "available",
                "official_registry",
                registry.get("source_url") or FALLBACK_EVIDENCE_URL,
                value={"summary": registry_text, "parts": [{"text": registry_text, "source_class": "official_registry", "source_url": registry.get("source_url") or FALLBACK_EVIDENCE_URL, "claim_span": registry_text, "kind": "registry"}], "registry_derived": True, "checked": True},
                retrieved_at=registry.get("retrieved_at") or completed_at,
                content_sha256=registry.get("content_sha256") or _canonical_hash(registry.get("value")),
            )
            brief_name = "registry"
        claims.append(claim("company_brief", (brief or {}).get("value"), brief_name, brief))

        activity_name, activity = source("public_activity")
        if activity is not None:
            activity_value = (activity or {}).get("value") or {}
            claims.append(claim("public_activity", activity_value, activity_name, activity, checked_empty=activity.get("status") == "available" and not (activity_value.get("items") if isinstance(activity_value, dict) else activity_value)))
        else:
            claims.append({"field": "public_activity", "value": {"checked": False}, "availability": "not_checked", "confidence": 0.0, "evidence_ids": []})

        financial_name, financial = source("financials")
        financial_value = (financial or {}).get("value") or {}
        account_records = financial_value.get("records") if isinstance(financial_value, dict) else []
        # The accounts API lists filed years oldest first; "latest" must be the newest period.
        ordered_records = sorted(account_records or [], key=_account_sort_key, reverse=True)
        claims.append(claim("latest_annual_accounts", ordered_records[0] if ordered_records else None, financial_name, financial, checked_empty=bool(financial and not ordered_records)))

        history_name, history = source("financial_history")
        history_value = (history or {}).get("value") or {}
        if history:
            if isinstance(history_value, dict):
                years_or_pdfs = history_value.get("years") or history_value.get("pdfs")
            else:
                years_or_pdfs = history_value
            claims.append(claim("accounts_history", history_value, history_name, history, checked_empty=bool(history and not years_or_pdfs)))
        else:
            # The older filed years are already in the accounts records; report them as history
            # with the same source evidence instead of leaving the section unchecked.
            older = ordered_records[1:]
            claims.append(claim("accounts_history", older if older else None, financial_name, financial, checked_empty=bool(financial and not older)))

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

        hiring_name, hiring = source("hiring", "nav_jobs")
        nav = hiring
        nav_value = (nav or {}).get("value") if nav else (profile.get("claims") or {}).get("nav_jobs")
        hiring_value = nav_value.get("ads") if isinstance(nav_value, dict) and "ads" in nav_value else nav_value
        if isinstance(nav_value, dict) and "postings" in nav_value:
            hiring_value = nav_value
        claims.append(claim("hiring", hiring_value, hiring_name, hiring, checked_empty=bool(nav and not hiring_value)))

        # Repeated claim rows are additive: the legacy kit/profile fields above
        # remain unchanged, while each scored fact gets its own evidence pointer.
        if website:
            website_value = (website.get("value") or {}) if isinstance(website, dict) else {}
            seen_social: set[str] = set()
            for link in website_value.get("social_links") or []:
                if not isinstance(link, dict) or not link.get("url"):
                    continue
                canonical = (normalize_social_url(str(link["url"])) or {"url": str(link["url"])})["url"]
                if canonical in seen_social:
                    continue
                seen_social.add(canonical)
                source_url, claim_span, method, content_hash = social_link_proof(website, canonical)
                social_id = repeated_evidence("social_profile", source_url=source_url, source_class="company_owned", retrieved_at=str(website.get("retrieved_at") or completed_at), content_sha256=content_hash, claim_span=claim_span, extraction_method=method)
                claims.append(repeated_claim("social_profile", canonical, social_id, match_key=social_match_key(canonical)))

        hiring_source = hiring if isinstance(hiring, dict) else None
        if hiring_source:
            hiring_value_dict = hiring_source.get("value") if isinstance(hiring_source.get("value"), dict) else {}
            career_urls = []
            if hiring_value_dict.get("careers_page_url"):
                career_urls.append(str(hiring_value_dict["careers_page_url"]))
            for career_url in career_urls:
                career_id = repeated_evidence("hiring_signal", source_url=career_url, source_class="company_owned", retrieved_at=str(hiring_source.get("retrieved_at") or completed_at), content_sha256=str(hiring_source.get("content_sha256") or _canonical_hash(hiring_value_dict)), claim_span=str(hiring_value_dict.get("career_claim_span") or career_url), extraction_method=str(hiring_source.get("extraction_method") or "html_text_span"))
                claims.append(repeated_claim("hiring_signal", career_url, career_id, value_details={"kind": "careers_page"}))
            postings = list(hiring_value_dict.get("postings") or [])
            if not postings:
                postings = [*(hiring_value_dict.get("company_site_postings") or []), *(hiring_value_dict.get("nav_ads") or [])]
            if not postings and isinstance(hiring_value_dict.get("ads"), list):
                postings = list(hiring_value_dict.get("ads") or [])
            if not postings and isinstance((hiring_source.get("value") if hiring_source else None), list):
                postings = list(hiring_source.get("value") or [])
            seen_postings: set[str] = set()
            for posting in postings:
                if not isinstance(posting, dict) or not posting.get("url"):
                    continue
                posting_url = str(posting["url"])
                if posting_url in seen_postings:
                    continue
                seen_postings.add(posting_url)
                posting_id = repeated_evidence(
                    "hiring_signal",
                    source_url=posting_url,
                    source_class=str(posting.get("source_class") or hiring_source.get("source_class") or "company_owned"),
                    retrieved_at=str(hiring_source.get("retrieved_at") or completed_at),
                    content_sha256=str(posting.get("content_sha256") or hiring_source.get("content_sha256") or _canonical_hash(posting)),
                    claim_span=str(posting.get("claim_span") or posting.get("title") or posting_url),
                    extraction_method=str(posting.get("extraction_method") or hiring_source.get("extraction_method") or "html_text_span"),
                    effective_at=posting.get("effective_at") or posting.get("posted") or posting.get("published") or posting.get("date"),
                )
                claims.append(repeated_claim("hiring_signal", posting_url, posting_id, value_details={"kind": "posting", "title": posting.get("title") or ""}))

        activity_source = activity if isinstance(activity, dict) else None
        if activity_source:
            activity_value = activity_source.get("value") if isinstance(activity_source.get("value"), dict) else {}
            for item in activity_value.get("items") or []:
                if not isinstance(item, dict) or not item.get("url") or not (item.get("published_at") or item.get("date")):
                    continue
                published_at = str(item.get("published_at") or item.get("date"))
                article_url = str(item.get("article_url") if item.get("article_verified") and item.get("article_url") else item.get("url"))
                article_id = repeated_evidence(
                    "dated_news",
                    source_url=article_url,
                    source_class="company_owned",
                    retrieved_at=str(activity_source.get("retrieved_at") or completed_at),
                    content_sha256=str(item.get("article_content_sha256") or activity_source.get("content_sha256") or _canonical_hash(item)),
                    claim_span=str(item.get("article_claim_span") if item.get("article_verified") else item.get("claim_span") or item.get("title") or "")[:1000],
                    extraction_method=str(item.get("article_extraction_method") if item.get("article_verified") else item.get("extraction_method") or activity_source.get("extraction_method") or "html_text_span"),
                    effective_at=published_at,
                    discovered_via=str(item.get("discovered_via") or activity_source.get("source_url") or ""),
                )
                claims.append(repeated_claim("dated_news", f"{item.get('title')} ({published_at})", article_id, value_details={"title": item.get("title"), "published_at": published_at, "url": article_url}))

        refresh_record = {
            "status": "available",
            "source_url": "https://builderr.ai/docs/signalpost-evaluation-harness.md",
            "source_type": "runtime_metadata",
            "retrieved_at": completed_at or utc_now(),
            "value": {"run_id": run_id, "organisation_number": org, "run_metrics": profile.get("run_metrics") or {}, "recall_cache": profile.get("recall_cache")},
        }
        refresh_id = _evidence_id(org, "refresh_metadata", refresh_record, len(output_evidence))
        evidence_ids["refresh_metadata"] = refresh_id
        output_evidence.append({"id": refresh_id, "source_url": refresh_record["source_url"], "source_class": "runtime_metadata", "retrieved_at": completed_at, "content_sha256": _canonical_hash(refresh_record["value"]), "claim_span": _dumps_sorted(refresh_record["value"])[:1000], "extraction_method": "runtime_metadata"})
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
