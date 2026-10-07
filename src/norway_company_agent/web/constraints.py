from __future__ import annotations

from collections import defaultdict
from typing import Any

PROMOTED_SOURCE_TYPES = {"search_discovered_company_website", "registry_derived_company_website"}


def _first_party(row: dict[str, Any]) -> dict[str, Any]:
    site = (row.get("evidence") or {}).get("website_discovered") or {}
    return (site.get("value") or {}).get("first_party_assessment") or {}


def enforce_domain_uniqueness(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Downgrade every exact-site claimant when a domain is shared by entities.

    Shared sites belong to groups, housing associations, managers or directories. A
    registry website field is useful corroboration, but it cannot establish that one
    domain is the official site of multiple legal entities. Every claimant is therefore
    moved to related evidence and marked ambiguous. Returns {domain: [org numbers]}.
    """
    claimants: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        domain = _first_party(row).get("candidate_domain")
        if domain:
            claimants[domain].append(row)
    conflicts: dict[str, list[str]] = {}
    for domain, group in claimants.items():
        if len(group) < 2:
            continue
        conflicts[domain] = sorted(str(row.get("organisation_number")) for row in group)
        for row in group:
            evidence = row["evidence"]
            site = evidence.pop("website_discovered")
            site["relationship"] = "shared_domain"
            evidence["website_related"] = site
            promoted = evidence.get("website") or {}
            if promoted.get("source_type") in PROMOTED_SOURCE_TYPES and promoted.get("source_url") == site.get("source_url"):
                evidence.pop("website")
            discovery = evidence.get("website_discovery")
            if discovery:
                discovery["status"] = "ambiguous"
                discovery["note"] = f"{domain} was verified for {len(group)} organisations; not published as an exact official website."
    return conflicts
