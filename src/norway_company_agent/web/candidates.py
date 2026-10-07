from __future__ import annotations

import socket
from typing import Any, Callable

from ..core.identity import name_tokens
from ..core.orgnumber import digits_only
from .first_party import _domain_from_email, _host_is_blocked, _registry_email, _registry_raw
from .website import normalize_homepage, registered_domain

# Consumer and ISP mailbox domains say nothing about a company's own website.
FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.no", "outlook.com", "outlook.no", "live.com", "live.no",
    "msn.com", "yahoo.com", "yahoo.no", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com",
    "online.no", "getmail.no", "broadpark.no", "c2i.net", "start.no", "altibox.no", "lyse.net", "tele2.no",
    "telenor.net", "frisurf.no", "powertech.no", "bbnett.no", "chello.no", "combell.no", "sensewave.com", "gmx.com",
    "yandex.com", "mail.com",
}
# ponytail: sequential DNS check per generated name; thread pool if the universe run needs it.
NAME_DOMAIN_LIMIT = 2


def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False


def _candidate(url: str, provider: str, score: float, reason: str) -> dict[str, Any] | None:
    normalized = normalize_homepage(url)
    domain = registered_domain(normalized) if normalized else ""
    if not normalized or not domain or domain in FREEMAIL_DOMAINS or _host_is_blocked(domain):
        return None
    return {
        "url": normalized,
        "host": domain,
        "registered_domain": domain,
        "rank": 0,
        "score": score,
        "status": "accepted_for_crawl",
        "publishable_candidate": True,
        "provider": provider,
        "query": None,
        "reasons": [reason],
        "method": "registry_derived_candidate_v1",
    }


def registry_email_candidate(profile: dict[str, Any]) -> dict[str, Any] | None:
    domain = _domain_from_email(_registry_email(profile, _registry_raw(profile)))
    return _candidate(f"https://{domain}/", "registry_email_domain", 0.95, "registry email domain is not a mailbox provider") if domain else None


def nav_employer_candidates(profile: dict[str, Any], nav_index: dict[str, dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Homepage the employer registered with NAV for this exact organisation number."""
    entry = (nav_index or {}).get(digits_only(profile.get("organisation_number")))
    homepages = (entry or {}).get("homepages") or []
    found = (_candidate(homepage, "nav_employer_homepage", 0.95, "NAV job feed lists this homepage for the exact organisation number") for homepage in homepages)
    return [candidate for candidate in found if candidate]


def subunit_website_candidates(profile: dict[str, Any]) -> list[dict[str, Any]]:
    locations = (((profile.get("evidence") or {}).get("locations") or {}).get("value") or {}).get("locations") or []
    found = (_candidate(str(item.get("website") or ""), "registry_subunit_website", 0.9, "registered establishment lists this website") for item in locations if item.get("website"))
    return [candidate for candidate in found if candidate]


def name_domain_candidates(
    profile: dict[str, Any],
    *,
    resolves: Callable[[str], bool] = _resolves,
    limit: int = NAME_DOMAIN_LIMIT,
) -> list[dict[str, Any]]:
    """Guess .no domains from the legal name. Speculative: each must still pass the identity gate."""
    tokens = name_tokens(profile.get("name"))
    if not tokens:
        return []
    base_slugs = ("".join(tokens), "-".join(tokens))
    slugs = list(dict.fromkeys(
        slug
        for base in base_slugs
        for slug in (base, f"{base}-as")
        if 4 <= len(slug) <= 40
    ))
    candidates = []
    for slug in slugs:
        host = f"{slug}.no"
        if not resolves(host):
            continue
        candidate = _candidate(f"https://{host}/", "name_derived_domain", 0.5, "domain guessed from the legal name resolves in DNS")
        if candidate:
            candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates


def registry_candidates(
    profile: dict[str, Any],
    *,
    name_domains: bool = True,
    resolves: Callable[[str], bool] = _resolves,
    nav_index: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Deterministic candidates that need no search call, strongest first, deduplicated by registered domain."""
    ordered = [*nav_employer_candidates(profile, nav_index), registry_email_candidate(profile), *subunit_website_candidates(profile)]
    if name_domains:
        ordered.extend(name_domain_candidates(profile, resolves=resolves))
    seen: set[str] = set()
    result = []
    for candidate in ordered:
        if candidate and candidate["registered_domain"] not in seen:
            seen.add(candidate["registered_domain"])
            result.append(candidate)
    return result
