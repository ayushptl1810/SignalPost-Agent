from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any

from ..core.orgnumber import digits_only, extract_org_numbers
from ..core.text import fold_tokens
from .website import normalize_homepage, registered_domain, site_root


DATA_BLOCKLIST_PATH = Path(__file__).resolve().parents[3] / "data" / "blocklist-domains.txt"
SOCIAL_DISCOVERY_HOSTS = {
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "tiktok.com",
}
BLOCKED_DISCOVERY_PATH_MARKERS = (
    "/company/", "/foretak/", "/bedrift/", "/bedrifter/", "/selskap/", "/firma/", "/opplysning/",
    "/medlemsbedrift/", "/detail/", "/profil/", "/produkter/", "/tannlege/", "/lege/",
)
GENERIC_NAME_TOKENS = {"as", "asa", "ans", "da", "enk", "sa", "nuf", "company", "norge", "norway", "gruppen", "group"}


def load_blocked_discovery_hosts(path: Path = DATA_BLOCKLIST_PATH) -> set[str]:
    hosts = set(SOCIAL_DISCOVERY_HOSTS)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.split("#", 1)[0].strip().casefold().removeprefix("www.")
            if value:
                hosts.add(value)
    return hosts


BLOCKED_DISCOVERY_HOSTS = load_blocked_discovery_hosts()


def build_company_search_query(profile: dict[str, Any]) -> str:
    name = " ".join(str(profile.get("name") or "").split())
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    municipality = " ".join(str(profile.get("municipality") or "").split())
    if not name or not org:
        raise ValueError("Company discovery requires a legal name and organisation number")
    location = f" {municipality}" if municipality else ""
    return f'"{name}" {org}{location}'


def build_company_search_queries(profile: dict[str, Any], *, include_identifier_fallback: bool = True) -> list[str]:
    """Build the local query and an optional exact-identifier fallback."""
    name = " ".join(str(profile.get("name") or "").split())
    municipality = " ".join(str(profile.get("municipality") or "").split())
    local = f'"{name}" {municipality}'.strip()
    exact = build_company_search_query(profile)
    queries = (local, exact) if include_identifier_fallback else (local,)
    return list(dict.fromkeys(query for query in queries if query))


def parse_brave_web_results(payload: dict[str, Any], *, query: str) -> list[dict[str, Any]]:
    results = (payload.get("web") or {}).get("results") or []
    parsed = []
    for rank, result in enumerate(results, start=1):
        if not isinstance(result, dict) or not result.get("url"):
            continue
        parsed.append({
            "url": result.get("url"),
            "title": result.get("title") or "",
            "snippet": result.get("description") or "",
            "rank": rank,
            "provider": "brave_search_api",
            "query": query,
        })
    return parsed


def parse_serper_results(payload: dict[str, Any], *, query: str) -> list[dict[str, Any]]:
    """Convert Serper's organic results into the provider-neutral candidate shape."""
    results = payload.get("organic") or []
    parsed = []
    for rank, result in enumerate(results, start=1):
        if not isinstance(result, dict) or not result.get("link"):
            continue
        parsed.append({
            "url": result.get("link"),
            "title": result.get("title") or "",
            "snippet": result.get("snippet") or "",
            "rank": result.get("position") or rank,
            "provider": "serper_api",
            "query": query,
        })
    return parsed


def _tokens(value: Any) -> list[str]:
    return [token for token in fold_tokens(value) if len(token) > 1]


def _company_slug_variants(profile: dict[str, Any]) -> list[str]:
    tokens = [token for token in _tokens(profile.get("name")) if token not in GENERIC_NAME_TOKENS]
    variants = {
        "".join(tokens),
        "-".join(tokens),
        "".join([*tokens, "as"]),
        "-".join([*tokens, "as"]),
    }
    return sorted((variant for variant in variants if len(variant) >= 8), key=len, reverse=True)


def listing_path_reason(profile: dict[str, Any], url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    decoded_path = urllib.parse.unquote(parsed.path).casefold()
    compact_path = re.sub(r"[^a-z0-9]", "", decoded_path)
    for marker in BLOCKED_DISCOVERY_PATH_MARKERS:
        if marker in f"/{decoded_path.lstrip('/')}":
            return "directory or listing path is not a company website candidate"
    org = digits_only(profile.get("organisation_number"))
    if org and org in compact_path:
        return "path contains the organisation number"
    if org and org in re.sub(r"\D", "", parsed.query):
        return "query contains the organisation number"
    for slug in _company_slug_variants(profile):
        if slug in compact_path:
            return "path contains a company listing slug"
    return None


def score_search_candidate(profile: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    matched_url = normalize_homepage(result.get("url"))
    normalized = site_root(matched_url)
    if not normalized or not matched_url:
        return {"status": "rejected", "score": 0.0, "publishable_candidate": False, "reasons": ["invalid HTTP(S) candidate URL"]}
    path_reason = listing_path_reason(profile, matched_url)
    if path_reason:
        return {"status": "rejected", "score": 0.0, "publishable_candidate": False, "url": normalized, "matched_url": matched_url, "host": urllib.parse.urlparse(normalized).hostname or "", "reasons": [path_reason]}
    parsed = urllib.parse.urlparse(normalized)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    if any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_DISCOVERY_HOSTS):
        return {"status": "rejected", "score": 0.0, "publishable_candidate": False, "url": normalized, "host": host, "reasons": ["directory, aggregator, or social host is not a company website candidate"]}
    if any(marker in parsed.path.casefold() for marker in BLOCKED_DISCOVERY_PATH_MARKERS):
        return {"status": "rejected", "score": 0.0, "publishable_candidate": False, "url": normalized, "host": host, "reasons": ["directory or listing path is not a company website candidate"]}

    name_tokens = [token for token in _tokens(profile.get("name")) if token not in GENERIC_NAME_TOKENS]
    title_tokens = _tokens(result.get("title"))
    snippet_tokens = _tokens(result.get("snippet"))
    evidence_tokens = set(title_tokens + snippet_tokens + _tokens(host))
    host_compact = "".join(_tokens(host))
    name_compact = "".join(name_tokens)
    org = digits_only(profile.get("organisation_number"))
    evidence_org_numbers = extract_org_numbers(f"{result.get('title', '')} {result.get('snippet', '')}")
    municipality_tokens = set(_tokens(profile.get("municipality")))

    org_match = bool(org and org in evidence_org_numbers)
    all_name_tokens = bool(name_tokens and set(name_tokens).issubset(evidence_tokens))
    all_name_tokens_in_title = bool(name_tokens and set(name_tokens).issubset(set(title_tokens)))
    name_in_host = bool(name_compact and name_compact in host_compact)
    municipality_match = bool(municipality_tokens and municipality_tokens <= set(snippet_tokens))
    score = 0.0
    reasons = []
    if org_match:
        score += 0.75
        reasons.append("exact organisation number appears in result evidence")
    if all_name_tokens_in_title:
        score += 0.45
        reasons.append("all distinctive legal-name tokens appear in the result title")
    elif all_name_tokens:
        score += 0.25
        reasons.append("all distinctive legal-name tokens appear across result evidence")
    if name_in_host:
        score += 0.3
        reasons.append("normalized legal name appears in candidate hostname")
    if municipality_match:
        score += 0.1
        reasons.append("registry municipality appears in result snippet")
    score = min(score, 1.0)
    # This only decides whether the URL is worth fetching. It does not publish
    # the site. Page-level identity verification remains mandatory, so a group
    # or directory result can be fetched and rejected after its content is read.
    identifier_candidate = org_match and (all_name_tokens or all_name_tokens_in_title)
    strong_name_candidate = all_name_tokens_in_title and (municipality_match or name_in_host)
    publishable_candidate = identifier_candidate or strong_name_candidate
    return {
        "status": "accepted_for_crawl" if publishable_candidate else "review" if score >= 0.6 else "rejected",
        "score": score,
        "publishable_candidate": publishable_candidate,
        "url": normalized,
        "crawl_url": normalized,
        "matched_url": matched_url,
        "host": host,
        "registered_domain": registered_domain(normalized),
        "rank": result.get("rank"),
        "provider": result.get("provider"),
        "query": result.get("query"),
        "reasons": reasons or ["insufficient exact-entity evidence"],
        "method": "deterministic_search_candidate_identity_v1",
    }


def choose_search_candidate(profile: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    decision = choose_search_candidates(profile, results, limit=1)
    return {
        "selected": decision["selected"][0] if decision["selected"] else None,
        "candidates": decision["candidates"],
        "abstained": decision["abstained"],
        "policy": decision["policy"],
    }


def choose_search_candidates(profile: dict[str, Any], results: list[dict[str, Any]], *, limit: int = 3) -> dict[str, Any]:
    assessed = [score_search_candidate(profile, result) for result in results]
    assessed.sort(key=lambda item: (-item.get("score", 0.0), item.get("rank") or 10_000, item.get("url") or ""))
    accepted = []
    seen_domains = set()
    for item in assessed:
        domain = item.get("registered_domain")
        if not item.get("publishable_candidate") or not domain or domain in seen_domains:
            continue
        seen_domains.add(domain)
        accepted.append(item)
        if len(accepted) >= limit:
            break
    return {
        "selected": accepted,
        "candidates": assessed,
        "abstained": not accepted,
        "policy": "A search result is only a crawl candidate. Publication still requires independently fetched exact-entity page evidence.",
    }
