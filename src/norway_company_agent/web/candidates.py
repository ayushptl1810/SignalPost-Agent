from __future__ import annotations

import socket
import re
import unicodedata
from typing import Any, Callable

from ..core.identity import LEGAL_AND_GENERIC
from ..core.text import ascii_fold, fold_tokens
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
NAME_DOMAIN_GENERIC = {"norge", "norsk", "gruppen", "group", "holding", "company", "bygg", "byggservice", "transport", "invest", "service", "consult", "regnskap", "eiendom"}


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
    include_com: bool = False,
) -> list[dict[str, Any]]:
    """Guess bounded DNS-first name variants; every hit still passes all gates."""
    slugs = name_domain_variants(profile.get("name"), include_com=include_com)
    candidates = []
    lookups = 0
    for slug in slugs:
        if lookups >= lookup_limit:
            break
        host = slug if slug.endswith(".com") else f"{slug}.no"
        lookups += 1
        if not resolves(host):
            continue
        provider = "name_derived_distinctive_com" if host.endswith(".com") else "name_derived_domain"
        candidate = _candidate(f"https://{host}/", provider, 0.5, "domain guessed from the legal name resolves in DNS")
        if candidate:
            candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates


def _raw_name_tokens(value: Any) -> list[str]:
    return [token for token in re.findall(r"[^\W_]+", str(value or "").casefold(), flags=re.UNICODE) if token]


def _token_forms(token: str) -> list[str]:
    """Return the common Norwegian spelling forms used in domain names."""
    direct = token.translate(str.maketrans({"æ": "ae", "ø": "o", "å": "a"}))
    aa_or_a = token.replace("å", "aa").replace("Å", "Aa").translate(str.maketrans({"æ": "ae", "ø": "o"}))
    ae_or_a = token.replace("æ", "a").replace("Æ", "A").translate(str.maketrans({"ø": "o", "å": "a"}))
    oe = token.translate(str.maketrans({"æ": "ae", "ø": "oe", "å": "a"}))
    values = [direct, aa_or_a, ae_or_a, oe]
    return list(dict.fromkeys(re.sub(r"[^a-z0-9]", "", ascii_fold(item)) for item in values if item))


def name_domain_variants(name: Any, *, include_com: bool = False) -> list[str]:
    """Generate deterministic joined, hyphenated and Norwegian name variants.

    The returned values are hostnames and are deliberately small.  Callers must
    resolve them before fetching; spelling variants are candidates, not proof.
    """
    raw_tokens = _raw_name_tokens(name)
    if not raw_tokens:
        return []
    token_forms = [_token_forms(token) for token in raw_tokens]
    canonical = [forms[0] for forms in token_forms]
    legal = [token for token in canonical if token not in NAME_DOMAIN_STOPWORDS and len(token) > 1]
    if not legal:
        return []
    distinctive = [token for token in legal if token not in {"og", "and"}]
    trimmed = list(distinctive)
    while len(trimmed) > 1 and trimmed[-1] in NAME_DOMAIN_GENERIC:
        trimmed.pop()
    slugs: list[str] = []

    def add_forms(tokens: list[str]) -> None:
        if not tokens:
            return
        joined = "".join(tokens)
        hyphenated = "-".join(tokens)
        for base in (joined, hyphenated):
            if 4 <= len(base) <= 40:
                slugs.extend((base, f"{base}-as"))

    # Keeping the connector first preserves the high-value "...og..." form.
    add_forms(legal)
    add_forms(distinctive)
    add_forms(trimmed)
    if len(distinctive) >= 2:
        add_forms(distinctive[:2])
    if distinctive and len(distinctive[0]) >= 4:
        slugs.extend((distinctive[0], f"{distinctive[0]}-as"))

    # Add non-canonical Norwegian spellings without multiplying every compound.
    legal_positions = [index for index, token in enumerate(canonical) if token in legal]
    distinctive_positions = [index for index in legal_positions if canonical[index] not in {"og", "and"}]
    for index, forms in enumerate(token_forms):
        if index not in legal_positions:
            continue
        for alternative in forms[1:]:
            altered = [canonical[position] for position in legal_positions]
            altered[legal_positions.index(index)] = alternative
            add_forms(altered)
            if index in distinctive_positions:
                altered_distinctive = [canonical[position] for position in distinctive_positions]
                altered_distinctive[distinctive_positions.index(index)] = alternative
                add_forms(altered_distinctive)
    unique = list(dict.fromkeys(slugs))
    if include_com:
        distinctive_enough = bool(distinctive and (len(distinctive) == 1 or len("".join(distinctive)) >= 9))
        if distinctive_enough:
            unique.extend(f"{slug}.com" for slug in unique if not slug.endswith(".com"))
    return list(dict.fromkeys(unique))


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
        ordered.extend(name_domain_candidates(profile, resolves=resolves, include_com=True))
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
