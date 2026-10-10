"""Post-discovery precision constraints.

These checks run after the complete input has been visited.  They are kept
outside the fetcher so a result cannot become official merely because it was
processed before a duplicate domain was encountered.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from ..core.evidence import evidence
from ..core.orgnumber import extract_org_numbers, digits_only
from ..web.website import registered_domain


def _website_evidence(profile: dict[str, Any]) -> dict[str, Any]:
    record = (profile.get("evidence") or {}).get("website") or {}
    return record if isinstance(record, dict) else {}


def _evidence_text(record: dict[str, Any]) -> str:
    value = record.get("value") or {}
    parts = [value.get(key) for key in ("title", "description", "main_text_excerpt", "identity_text_excerpt")]
    parts.extend(value.get("structured_organisations") or [])
    parts.extend(value.get("structured_identifiers") or [])
    parts.extend(page.get(key) for page in value.get("pages") or [] for key in ("title", "main_text_excerpt", "identity_text_excerpt") if isinstance(page, dict))
    return " ".join(str(item or "") for item in parts)


def _published_domain(profile: dict[str, Any]) -> str:
    record = _website_evidence(profile)
    value = record.get("value") or {}
    return registered_domain(str(value.get("final_url") or record.get("source_url") or ""))


def _cache_domain(record: dict[str, Any]) -> str:
    claims = record.get("claims") or {}
    value = claims.get("official_website") or claims.get("official_website_g4") or {}
    evidence_record = (record.get("website") or {}).get("evidence") or {}
    return registered_domain(str(value.get("final_url") or evidence_record.get("source_url") or ""))


def _exactly_one_matching_number(profile: dict[str, Any]) -> bool:
    org = digits_only(profile.get("organisation_number"))
    numbers = extract_org_numbers(_evidence_text(_website_evidence(profile)))
    return bool(org and numbers == {org})


def _demote(profile: dict[str, Any], domain: str, reason: str) -> None:
    evidence_map = profile.setdefault("evidence", {})
    original = evidence_map.get("website")
    if original:
        evidence_map["website_related_only"] = original
        source_url = str(original.get("source_url") or ((original.get("value") or {}).get("final_url") or ""))
        evidence_map["website"] = evidence(
            "website",
            "not_found",
            "company_site",
            source_url,
            note=f"related_only: {reason}; registered domain {domain}",
        )
    for key in ("website_g4",):
        if evidence_map.get(key):
            evidence_map[f"{key}_related_only"] = evidence_map[key]
            evidence_map.pop(key, None)
    claims = profile.setdefault("claims", {})
    claims.pop("official_website", None)
    claims.pop("official_website_g4", None)
    profile.setdefault("discovery", {})["related_only"] = {
        "domain": domain,
        "reason": reason,
    }


def enforce_domain_uniqueness(
    profiles: list[dict[str, Any]],
    *,
    cache_records: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Demote duplicate official domains unless each page has one exact org number.

    The returned list is an audit trail.  Cache rows participate in the conflict
    set but are not mutated; a current-run row is conservatively demoted when
    its domain is already claimed by a different cached organisation.
    """
    owners: dict[str, set[str]] = defaultdict(set)
    for profile in profiles:
        domain = _published_domain(profile)
        if domain:
            owners[domain].add(str(profile.get("organisation_number") or ""))
    for record in cache_records:
        domain = _cache_domain(record)
        if domain:
            owners[domain].add(str(record.get("organisation_number") or ""))

    conflicts: list[dict[str, Any]] = []
    for profile in profiles:
        domain = _published_domain(profile)
        if not domain:
            continue
        owner_numbers = owners.get(domain, set())
        if len(owner_numbers) < 2 or _exactly_one_matching_number(profile):
            continue
        reason = "domain_published_for_multiple_organisation_numbers"
        _demote(profile, domain, reason)
        conflicts.append({
            "organisation_number": str(profile.get("organisation_number") or ""),
            "domain": domain,
            "owners": sorted(owner_numbers),
            "reason": reason,
        })
    return conflicts


__all__ = ["enforce_domain_uniqueness"]
