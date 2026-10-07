from __future__ import annotations

import socket
from typing import Any, Callable

from ..core.identity import LEGAL_AND_GENERIC
from ..core.text import fold_tokens
from ..core.orgnumber import digits_only
from .first_party import _domain_from_email, _host_is_blocked, _registry_email, _registry_raw
from .website import normalize_homepage, registered_domain, site_root

# Consumer and ISP mailbox domains say nothing about a company's own website.
FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.no", "outlook.com", "outlook.no", "live.com", "live.no",
    "msn.com", "yahoo.com", "yahoo.no", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com",
    "online.no", "getmail.no", "broadpark.no", "c2i.net", "start.no", "altibox.no", "lyse.net", "tele2.no",
    "telenor.net", "frisurf.no", "powertech.no", "bbnett.no", "chello.no", "combell.no", "sensewave.com", "gmx.com",
    "yandex.com", "mail.com",
}
# ponytail: sequential DNS check per generated name; thread pool if the universe run needs it.
NAME_DOMAIN_LIMIT = 6
NAME_DOMAIN_LOOKUP_LIMIT = 6
NAME_DOMAIN_STOPWORDS = set(LEGAL_AND_GENERIC) - {"og", "and"}


def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False


def _candidate(url: str, provider: str, score: float, reason: str) -> dict[str, Any] | None:
    matched_url = normalize_homepage(url)
    normalized = site_root(matched_url)
    domain = registered_domain(normalized) if normalized else ""
    if not normalized or not domain or domain in FREEMAIL_DOMAINS or _host_is_blocked(domain):
        return None
    return {
        "url": normalized,
        "crawl_url": normalized,
        "matched_url": matched_url,
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
    lookup_limit: int = NAME_DOMAIN_LOOKUP_LIMIT,
) -> list[dict[str, Any]]:
    """Guess bounded .no variants. Speculative: each still passes all gates."""
    all_tokens = [token for token in fold_tokens(profile.get("name")) if token not in NAME_DOMAIN_STOPWORDS and len(token) > 1]
    connector_tokens = {"og", "and"}
    tokens = all_tokens
    if not tokens:
        return []
    distinctive = [token for token in all_tokens if token not in connector_tokens]
    trimmed = list(distinctive)
    generic_tail = {"bygg", "byggservice", "transport", "holding", "invest", "service", "consult", "regnskap", "eiendom"}
    while len(trimmed) > 1 and trimmed[-1] in generic_tail:
        trimmed.pop()
    base_slugs = [
        "".join(tokens),
        "-".join(tokens),
        "".join(distinctive),
        "-".join(distinctive),
        "".join(trimmed),
        "-".join(trimmed),
    ]
    if distinctive and len(distinctive[0]) >= 5:
        base_slugs.extend([distinctive[0], f"{distinctive[0]}-as"])
    slugs = list(dict.fromkeys(
        slug
        for base in base_slugs
        for slug in (base, f"{base}-as")
        if 4 <= len(slug) <= 40
    ))
    candidates = []
    lookups = 0
    for slug in slugs:
        if lookups >= lookup_limit:
            break
        host = f"{slug}.no"
        lookups += 1
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


def should_skip_search_triage(profile: dict[str, Any], nav_index: dict[str, dict[str, Any]] | None) -> bool:
    """Gate only the paid search fallback for low-yield S2/S3 profiles."""
    raw = _registry_raw(profile)
    legal_form = str(profile.get("legal_form") or raw.get("organisasjonsform.kode") or "").casefold()
    employees = profile.get("employees")
    if employees not in (None, ""):
        return False
    activity = str(profile.get("industry_code") or raw.get("naeringskode1.kode") or "").split(".", 1)[0]
    explicit_stratum = str(profile.get("stratum") or "")
    low_value = explicit_stratum in {"S2", "S3"} or (legal_form in {"as", "asa"} and activity in {"68", "64", "00"})
    if not low_value:
        return False
    registry_website = profile.get("website") or raw.get("hjemmeside") or raw.get("Hjemmeside")
    if str(registry_website or "").strip() or registry_email_candidate(profile):
        return False
    org = digits_only(profile.get("organisation_number"))
    return not bool(((nav_index or {}).get(org) or {}).get("homepages"))
