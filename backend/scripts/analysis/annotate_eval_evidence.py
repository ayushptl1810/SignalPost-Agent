#!/usr/bin/env python3
"""Turn fresh search/page evidence into auditable evaluation annotations.

This is intentionally separate from the production discovery runner.  It uses
fresh Google-backed evidence, applies the same identity/first-party gates, and
records the evidence and uncertainty instead of silently treating every search
hit as a company-owned website.

The output is an annotation draft for the evaluation set.  A human can review
the rows with low confidence or ``undetermined`` outcome without re-running the
searches; source URLs and gate decisions are retained in each row.
"""
from __future__ import annotations

import argparse
import csv
import copy
import gzip
import json
import re
import sys
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.registry.sampling import normalize_row  # noqa: E402
from norway_company_agent.web.discovery import (  # noqa: E402
    BLOCKED_DISCOVERY_HOSTS,
    score_search_candidate,
)
from norway_company_agent.web.first_party import assess_first_party_ownership  # noqa: E402
from norway_company_agent.web.related import assess_related_entity  # noqa: E402
from norway_company_agent.web.website import registered_domain  # noqa: E402


OUTCOMES = {"official_site", "related_only", "no_site_confirmed", "undetermined"}
SOCIAL_DOMAINS = {
    "facebook.com", "instagram.com", "linkedin.com", "no.linkedin.com",
    "youtube.com", "x.com", "twitter.com", "tiktok.com",
}

# Google returns many pages that contain exact registry data but cannot be the
# company's own site.  Keep this list conservative: an unknown domain remains
# a review candidate rather than being silently discarded.
THIRD_PARTY_DOMAINS = {
    "orgi.no", "helsesmart.no", "bobilavisen.no", "regnskapsklinikken.no",
    "arendalnaeringsforening.no", "hyundai.com", "aktie.no", "kokstad.info",
    "nbbo.no", "elektrikerplassen.no", "kontakte.no", "firmaportalen.no",
    "cylex.no", "tannlegeplassen.no", "tannlege.nu", "legelisten.no",
    "vilbli.no", "utdanning.no", "opplaeringskontoret.no", "duodji.no",
    "kommunikasjon.ntb.no", "ntb.no", "finansavisen.no", "bobilavisen.no",
    "brreg.no", "forvalt.no", "regnskapstall.no", "norgelei.no", "kredittsjekk.no",
    "listings.no", "lokalebedrifter.no", "merinfo.no", "bolig.ai", "trafikkskoleplassen.no",
    "fagfolkguiden.no", "foretaksinfo.no", "aksjegrafen.com", "1890.no", "falio.no",
    "catalystone.com", "creditsafe.com", "wikipedia.org", "anbudstorget.no",
    "folkebladet.no", "framtidinord.no", "ringsaker-blad.no", "tracxn.com", "plastforum.no",
    "medlem.lastebil.no", "dnb.com", "dibk.no", "norskebransjemagasinet.no",
    "opencorpdata.com", "sameieplassen.no", "barnehagelisten.no", "samskipnaden.no",
    "bygg.no", "norgebiz.com", "biztrac.no", "dekkpartner.no", "nokiantyres.no",
    "rorleggerplassen.no", "nittedalnf.no", "snokrystallen.no", "graveplassen.no",
    "rosa.no",
}
THIRD_PARTY_DOMAINS |= set(BLOCKED_DISCOVERY_HOSTS)
THIRD_PARTY_PATH_MARKERS = (
    "/bedrift/", "/bedrifter/", "/selskap/", "/opplysning/", "/profil/",
    "/medlemmer/", "/forhandlere/", "/regnskapsforer/", "/klinikk/",
    "/prospekt/",
)
RELATED_MARKERS = (
    "group", "gruppen", "konsern", "parent", "subsidiary", "datterselskap",
    "franchise", "forhandler", "dealer", "member", "medlem", "brand",
    "merkevare", "eier", "owned by", "part of", "tilknyttet",
)
RELATED_DOMAINS = {
    "nbbo.no", "samskipnaden.no", "ncc.com", "pharmaq.com", "ascom.com",
    "salmongroup.no", "sameieplassen.no", "concordix.com", "flowfirma.no",
    "norgebiz.com", "biztrac.no", "hyundai.com", "dekkpartner.no", "nokiantyres.no",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    # Iterate physical newline records instead of ``str.splitlines``.  Website
    # text can contain Unicode line-separator characters (for example U+0085)
    # which are valid inside a JSON string but would make splitlines cut a row.
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_registry(path: Path, targets: set[str]) -> dict[str, dict[str, Any]]:
    """Read only target rows from the large compressed Brreg export.

    Parsing every field through ``csv.DictReader`` for 1.4m rows is needlessly
    expensive.  The organisation number is the first CSV field, so we cheaply
    screen lines before parsing the handful of rows in the evaluation sample.
    """
    records: dict[str, dict[str, Any]] = {}
    opener = gzip.open if path.read_bytes()[:2] == b"\x1f\x8b" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
        fields = next(csv.reader([next(handle)]))
        for line in handle:
            organisation_number = line.split(",", 1)[0].strip('"')
            if organisation_number in targets:
                values = next(csv.reader([line]))
                records[organisation_number] = normalize_row(dict(zip(fields, values)))
    missing = targets - set(records)
    if missing:
        raise ValueError(f"registry export is missing {len(missing)} target rows")
    return records


def host_and_domain(url: str) -> tuple[str, str, str]:
    parsed = urllib.parse.urlparse(url or "")
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    domain = registered_domain(url or "")
    return host, domain, parsed.path.casefold()


def is_third_party(url: str) -> bool:
    host, domain, path = host_and_domain(url)
    if host in SOCIAL_DOMAINS or domain in SOCIAL_DOMAINS:
        return True
    if any(host == item or host.endswith("." + item) or domain == item for item in THIRD_PARTY_DOMAINS):
        return True
    return any(marker in path for marker in THIRD_PARTY_PATH_MARKERS)


def profile_for(row: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    return {
        **registry,
        "name": row["name"],
        "organisation_number": row["organisation_number"],
        "municipality": row.get("municipality") or registry.get("municipality"),
        "evidence": {"registry": {"value": registry.get("raw", {})}},
    }


def page_review(row: dict[str, Any], registry: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    profile = profile_for(row, registry)
    website = copy.deepcopy(page.get("website") or {})
    gated = apply_website_identity_gate(profile, website)["website"]
    first_party = assess_first_party_ownership(profile, gated)
    related = assess_related_entity(profile, gated)
    value = gated.get("value") or {}
    identity = value.get("identity_assessment") or {}
    requested = page.get("search_result", {}).get("url") or gated.get("source_url") or ""
    final_url = value.get("final_url") or gated.get("source_url") or requested
    return {
        "url": requested,
        "final_url": final_url,
        "domain": registered_domain(final_url or requested),
        "third_party": is_third_party(final_url or requested),
        "website_status": gated.get("status"),
        "identity_status": identity.get("status"),
        "identity_score": identity.get("score"),
        "first_party_status": first_party.get("status"),
        "first_party_publishable": bool(first_party.get("publishable")),
        "first_party_signals": first_party.get("signals", {}),
        "related_status": related.get("status"),
        "title": value.get("title") or page.get("search_result", {}).get("title") or "",
        "text": " ".join([
            str(value.get("title") or ""),
            str(value.get("description") or ""),
            str(value.get("main_text_excerpt") or ""),
            str(value.get("identity_text_excerpt") or ""),
        ]),
    }


def search_review(row: dict[str, Any], search: dict[str, Any]) -> dict[str, Any]:
    profile = {"name": row["name"], "organisation_number": row["organisation_number"], "municipality": row.get("municipality")}
    results = search.get("results") or []
    assessed = []
    blocked = social = 0
    for result in results:
        url = result.get("url") or ""
        host, domain, path = host_and_domain(url)
        social_hit = host in SOCIAL_DOMAINS or domain in SOCIAL_DOMAINS
        blocked_hit = is_third_party(url)
        social += int(social_hit)
        blocked += int(blocked_hit)
        candidate = score_search_candidate(profile, result)
        assessed.append({
            "url": url,
            "domain": domain,
            "rank": result.get("rank"),
            "score": candidate.get("score"),
            "candidate_status": candidate.get("status"),
            "third_party": blocked_hit,
            "social": social_hit,
        })
    return {
        "result_count": len(results),
        "blocked_or_third_party_count": blocked,
        "social_count": social,
        "candidate_domains": sorted({item["domain"] for item in assessed if item["domain"] and not item["third_party"]}),
        "assessed": assessed,
    }


def source_urls(search: dict[str, Any], reviews: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for review in reviews:
        if review.get("url"):
            urls.append(review["url"])
    for result in search.get("results") or []:
        url = result.get("url") or ""
        if url:
            urls.append(url)
    # Stable and bounded: enough for audit, without embedding the entire SERP.
    return list(dict.fromkeys(urls))[:12]


def negative_checks(row: dict[str, Any], registry: dict[str, Any], search: dict[str, Any], pages: list[dict[str, Any]], nav_index: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record the mandatory independent negative checks without asserting a label.

    A ``pass`` means that the check supports a negative website conclusion.  A
    ``fail`` means that the check found a candidate which needs adjudication;
    it is deliberately not treated as a negative result.  This distinction is
    important for the extension corpus: a missing search provider or an
    unchecked name-derived domain must not silently become ``no_site``.
    """
    raw = registry.get("raw", {}) if isinstance(registry, dict) else {}
    registry_site = raw.get("hjemmeside") or raw.get("Hjemmeside") or registry.get("website")
    email = raw.get("epostadresse") or raw.get("Epostadresse") or registry.get("email") or ""
    email_domain = str(email).rsplit("@", 1)[-1].casefold() if "@" in str(email) else ""
    nav_entry = (nav_index or {}).get(str(row.get("organisation_number"))) or {}
    page_urls = [str(item.get("website", {}).get("source_url") or item.get("search_result", {}).get("url") or "") for item in pages]
    page_reviews = [page_review(row, registry, item) for item in pages]
    available_pages = [item for item in page_reviews if item.get("website_status") == "available"]
    acceptable_pages = [item for item in available_pages if item.get("first_party_publishable") and not item.get("third_party")]
    registry_domain = registered_domain(str(registry_site or ""))
    registry_pages = [item for item in available_pages if registry_domain and item.get("domain") == registry_domain]
    domain_checks = search.get("domain_checks") or []
    domain_check_items = domain_checks if all(isinstance(item, dict) for item in domain_checks) else [{"url": item} for item in domain_checks]
    derived_checked = [item for item in domain_check_items if item.get("status") in {"available", "not_found", "source_error", "failed", "blocked"}]
    derived_hits = [item for item in derived_checked if item.get("status") == "available" and item.get("publishable")]
    provider = str(search.get("provider") or "")
    queries = search.get("queries") or []
    provider_attempted = bool(search.get("provider_attempted"))
    provider_ok = provider_attempted and not search.get("provider_error") and len(queries) >= 2
    address_phone_matches = [
        item for item in available_pages
        if any((item.get("first_party_signals") or {}).get(key) for key in ("address_match", "phone_match"))
    ]
    search_check = {
        "status": "pass" if provider_ok and not (search.get("results") or []) else "fail" if provider_attempted else "not_run",
        "provider": provider, "query_count": len(queries), "provider_attempted": provider_attempted,
        "provider_error": search.get("provider_error"), "result_count": len(search.get("results") or []),
    }
    return {
        "registry_website_and_email_domain": {
            "status": "fail" if acceptable_pages or registry_pages else "pass",
            "website": registry_site, "email_domain": email_domain,
            "registry_domain": registry_domain, "registry_pages_checked": len(registry_pages),
            "finding": "publishable_page" if acceptable_pages else "registry_site_not_verified_as_first_party" if registry_site else "no_registry_site",
        },
        "nav_employer_index": {
            "status": "fail" if nav_entry else "pass", "homepage": (nav_entry.get("homepages") or [None])[0],
            "finding": "employer_homepage" if nav_entry else "no_exact_org_match",
        },
        "name_derived_domains": {
            "status": "fail" if derived_hits else "pass" if derived_checked else "not_run",
            "checked_urls": [item.get("url") for item in domain_check_items] or page_urls,
            "checks": domain_check_items,
        },
        "alternative_provider_search": search_check,
        "search": {"status": search_check["status"], "provider": provider, "query_count": len(queries), "provider_error": search.get("provider_error")},
        "address_and_phone_page_check": {
            "status": "fail" if address_phone_matches else "pass" if available_pages else "not_run",
            "pages_checked": len(page_urls), "matching_pages": len(address_phone_matches),
        },
    }


def annotate_one(row: dict[str, Any], registry: dict[str, Any], search: dict[str, Any], pages: list[dict[str, Any]], nav_index: dict[str, Any] | None = None) -> dict[str, Any]:
    search_audit = search_review(row, search)
    reviews = [page_review(row, registry, page) for page in pages]
    official = [item for item in reviews if item["first_party_publishable"] and not item["third_party"]]
    official.sort(key=lambda item: (
        not bool((item["first_party_signals"] or {}).get("registry_website_match")),
        not bool((item["first_party_signals"] or {}).get("structured_organisation_number_match")),
        -float(item.get("identity_score") or 0.0),
        item.get("domain") or "",
    ))
    accessible_non_first_party = [item for item in reviews if item["website_status"] == "available" and not item["third_party"]]
    relation_text = " ".join(item.get("text", "") for item in reviews).casefold()
    related_candidate = any(
        item["website_status"] == "available"
        and item["third_party"]
        and (
            item.get("domain") in RELATED_DOMAINS
            or (
                not any(marker in host_and_domain(item.get("final_url") or item.get("url") or "")[2] for marker in THIRD_PARTY_PATH_MARKERS)
                and any(marker in (item.get("text") or "").casefold() for marker in RELATED_MARKERS)
            )
        )
        for item in reviews
    )

    checks = negative_checks(row, registry, search, pages, nav_index)
    if official:
        chosen = official[0]
        signals = chosen["first_party_signals"]
        strong = bool(signals.get("structured_organisation_number_match"))
        medium = bool(signals.get("registry_website_match") or signals.get("registry_email_domain_match") or signals.get("phone_match") or signals.get("page_email_domain_match"))
        weak = bool(signals.get("address_match"))
        if strong and weak:
            tier, confidence = "strong+weak", "high"
        elif strong or (medium and weak):
            tier, confidence = "strong" if strong else "medium+weak", "high" if strong else "medium_high"
        else:
            tier, confidence = "weak", "medium"
        outcome, domain = "official_site", chosen["domain"]
        evidence = "Independent search found an accessible dedicated site; the exact-entity identity gate and first-party ownership gate passed."
        found_via = "independent_google_search_and_page_check"
    elif related_candidate:
        outcome, domain = "related_only", next((item["domain"] for item in reviews if item["third_party"] and item["domain"]), None)
        tier, confidence = "related", "medium"
        evidence = "Search/page evidence found a brand, group, marketplace, directory or association page related to the entity, but no publishable company-owned site."
        found_via = "independent_google_search_and_page_check"
    else:
        non_third_party_search = [item for item in search_audit["assessed"] if not item["third_party"]]
        failed_access = [item for item in reviews if item["website_status"] in {"source_error", "blocked"} and not item["third_party"]]
        if accessible_non_first_party or failed_access or non_third_party_search:
            outcome, domain = "undetermined", None
            tier, confidence = "insufficient_or_conflicting", "low"
            evidence = "A non-directory candidate or inaccessible candidate remains, but independent page evidence did not establish exact first-party ownership."
            found_via = "independent_google_search_and_page_check"
        else:
            outcome, domain = "no_site_confirmed", None
            tier = "none_social_only" if search_audit["social_count"] else "none"
            confidence = "medium" if search_audit["result_count"] and search_audit["blocked_or_third_party_count"] >= search_audit["result_count"] - 1 else "low"
            evidence = "The fixed name/place and name/organisation-number searches returned only registry, directory, social or other non-company pages; no dedicated site was verified."
            found_via = "independent_google_search_and_page_check"

    audit_urls = source_urls(search, reviews)
    if not audit_urls:
        audit_urls = [f"https://virksomhet.brreg.no/nb/oppslag/enheter/{row['organisation_number']}"]

    result = {
        "organisation_number": row["organisation_number"],
        "name": row["name"],
        "stratum": row.get("stratum"),
        "split": row.get("split"),
        "outcome": outcome,
        "domain": domain,
        "evidence_tier": tier,
        "evidence": evidence,
        "found_via": found_via,
        "confidence": confidence,
        "attempted": True,
        "labeler": "codex_independent_web_review",
        "annotated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_urls": audit_urls,
        "audit": {
            "provider": search.get("provider"),
            "queries": search.get("queries", []),
            "search": {key: value for key, value in search_audit.items() if key != "assessed"},
            "page_reviews": [
                {key: value for key, value in item.items() if key != "text"}
                for item in reviews
            ],
        },
    }
    if outcome == "no_site_confirmed" and not all(item.get("status") == "pass" for item in checks.values()):
        outcome = "undetermined"
        result["outcome"] = outcome
        result["domain"] = None
        result["evidence_tier"] = "insufficient_negative_checks"
        result["confidence"] = "low"
        result["evidence"] = "The direct checks did not establish an exact site, but at least one mandatory negative check was unavailable or found a candidate; no-site publication is withheld."
    if outcome not in {"official_site"}:
        result["negative_checks"] = checks
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Annotate fresh independent evaluation evidence.")
    parser.add_argument("--manifest", default="out/eval-sample/manifest.jsonl")
    parser.add_argument("--registry", default="data/brreg-enheter.csv")
    parser.add_argument("--search", required=True)
    parser.add_argument("--pages", required=True)
    parser.add_argument("--nav-index", default="out/nav-employer-index.jsonl")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    manifest = read_jsonl(Path(args.manifest))
    all_rows = {str(row["organisation_number"]): row for row in manifest}
    search_rows = {str(row["organisation_number"]): row for row in read_jsonl(Path(args.search))}
    target_orgs = set(search_rows)
    missing_from_manifest = target_orgs - set(all_rows)
    if missing_from_manifest:
        raise ValueError(f"search evidence has {len(missing_from_manifest)} organisations absent from manifest")
    rows = {org: all_rows[org] for org in target_orgs}
    registry = load_registry(Path(args.registry), target_orgs)
    page_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in read_jsonl(Path(args.pages)):
        page_rows[str(page["organisation_number"])].append(page)
    nav_index: dict[str, Any] = {}
    nav_path = Path(args.nav_index)
    if nav_path.exists():
        nav_index = {str(item.get("organisation_number")): item for item in read_jsonl(nav_path)}
    annotations = [annotate_one(rows[org], registry[org], search_rows[org], page_rows.get(org, []), nav_index) for org in sorted(target_orgs)]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in annotations), encoding="utf-8")
    print(json.dumps({
        "targets": len(annotations),
        "outcomes": dict(Counter(row["outcome"] for row in annotations)),
        "confidence": dict(Counter(row["confidence"] for row in annotations)),
        "splits": dict(Counter(row["split"] for row in annotations)),
        "page_evidence_companies": sum(bool(page_rows.get(org)) for org in target_orgs),
        "output": str(output),
    }, indent=2))


if __name__ == "__main__":
    main()
