"""Deterministic facts extracted from a verified company website.

This module deliberately sits after website discovery.  It never chooses a
website and it never changes the identity or first-party decision that allowed
the website to be published.  Its parsers are also useful on captured HTML in
tests, which keeps the default batch path free of model or keyed-provider
calls.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import urllib.parse
from email.utils import parsedate_to_datetime
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from bs4 import BeautifulSoup

from .core.evidence import evidence, utc_now
from .web import website as safe_web


NORWEGIAN_MONTHS = {
    "januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "desember": 12,
}
MONTHS = {**NORWEGIAN_MONTHS, **{name: number for number, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"),
    1,
)}}

CAREER_TERMS = (
    "karriere", "career", "jobb", "jobs", "ledige-stillinger", "ledigestillinger",
    "stillinger", "stilling", "jobb-hos-oss", "job-hos-oss",
)
ACTIVITY_TERMS = (
    "news", "nyheter", "nyhet", "aktuelt", "blog", "press", "presse",
    "pressemeldinger", "investor", "om-oss/nyheter", "om_oss/nyheter",
)
NO_OPEN_ROLES = re.compile(
    r"(?:ingen|ingen\s+har)\s+(?:ledige\s+)?stillinger|ingen\s+ledige\s+stillinger|no\s+open\s+positions?|no\s+vacancies|we\s+are\s+not\s+hiring",
    re.I,
)
DATE_RE = re.compile(
    r"(?P<day>\d{1,2})[./-]\s?(?P<month>\d{1,2})[./-]\s?(?P<year>\d{4})"
    r"|(?P<day_name>\d{1,2})\.\s*(?P<month_name>[A-Za-zæøåÆØÅ]+)\s+(?P<year_name>\d{4})",
    re.I,
)
STOPWORDS = {"as", "asa", "og", "the", "a", "an", "of", "for", "også", "i", "på", "en", "et"}
STEM_SUFFIXES = ("heten", "heter", "ende", "ane", "ene", "er", "en", "et", "a", "e", "s")


def parse_date(value: Any) -> str | None:
    """Parse an explicit ISO, numeric, or Norwegian/English written date."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return None
    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b", text)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1)).isoformat()
        except ValueError:
            pass
    try:
        parsed_rfc = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        parsed_rfc = None
    if parsed_rfc is not None:
        return parsed_rfc.date().isoformat()
    match = DATE_RE.search(text)
    if not match:
        return None
    try:
        if match.group("day"):
            return date(int(match.group("year")), int(match.group("month")), int(match.group("day"))).isoformat()
        month = MONTHS.get(match.group("month_name").casefold())
        if month:
            return date(int(match.group("year_name")), month, int(match.group("day_name"))).isoformat()
    except (TypeError, ValueError):
        return None
    return None


def _jsonld_values(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _jsonld_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _jsonld_values(child)


def _jsonld_from_html(soup: BeautifulSoup) -> list[Any]:
    found: list[Any] = []
    for script in soup.select('script[type="application/ld+json"], script[type="application/json+ld"]'):
        try:
            found.append(json.loads(script.string or script.get_text()))
        except (TypeError, json.JSONDecodeError):
            continue
    return found


def _parse_soup(value: str | bytes, *, prefer_xml: bool = False) -> BeautifulSoup:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    xml_document = prefer_xml or bool(re.match(r"\s*(?:<\?xml|<(?:rss|feed|urlset|sitemapindex)\b)", text, re.I))
    return BeautifulSoup(value, "lxml-xml" if xml_document else "lxml")


def _clean_sentence(value: Any, *, limit: int = 300) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    if len(text) > limit:
        boundary = re.search(r"[.!?](?:\s|$)", text[:limit])
        text = text[: boundary.end() if boundary else limit].strip()
    return text[:limit]


def _same_site(base_url: str, candidate: str) -> bool:
    try:
        return bool(candidate) and safe_web.registered_domain(candidate) == safe_web.registered_domain(base_url)
    except (ValueError, TypeError):
        return False


def _claim_span(title: str, text: str, explicit: Any = None) -> str:
    return _clean_sentence(explicit or title or text, limit=500) or "Explicit structured or visible site item"


def _page_payload(page: dict[str, Any], *, base_url: str) -> tuple[list[Any], str, dict[str, str], list[dict[str, str]]]:
    json_ld = page.get("json_ld") or page.get("structured_json_ld") or page.get("structured_data") or []
    if isinstance(json_ld, dict):
        json_ld = [json_ld]
    text = " ".join(str(page.get(key) or "") for key in ("main_text_excerpt", "identity_text_excerpt", "visible_text", "text"))
    opengraph = page.get("opengraph") or page.get("meta") or {}
    if not isinstance(opengraph, dict):
        opengraph = {}
    links = page.get("links") or []
    normalized_links: list[dict[str, str]] = []
    for link in links:
        if isinstance(link, str):
            normalized_links.append({"url": urllib.parse.urljoin(base_url, link), "text": ""})
        elif isinstance(link, dict):
            url = urllib.parse.urljoin(base_url, str(link.get("url") or link.get("href") or ""))
            if url:
                normalized_links.append({"url": url, "text": " ".join(str(link.get("text") or "").split())})
    return list(json_ld), text, {str(k).casefold(): str(v) for k, v in opengraph.items()}, normalized_links


def _activity_from_jsonld(node: dict[str, Any], page_url: str, *, source_class: str = "company_owned") -> dict[str, Any] | None:
    kinds = node.get("@type") or node.get("type") or []
    kinds = {str(item).casefold() for item in (kinds if isinstance(kinds, list) else [kinds])}
    if not kinds & {"newsarticle", "blogposting", "article", "event"}:
        return None
    parsed = parse_date(node.get("datePublished") or node.get("dateCreated") or node.get("startDate"))
    if not parsed:
        return None
    raw_url = node.get("url") or node.get("mainEntityOfPage") or page_url
    if isinstance(raw_url, dict):
        raw_url = raw_url.get("@id") or raw_url.get("url")
    url = urllib.parse.urljoin(page_url, str(raw_url or page_url))
    if not _same_site(page_url, url):
        return None
    title = _clean_sentence(node.get("headline") or node.get("name") or node.get("title") or "Company activity")
    return {"title": title or "Company activity", "date": parsed, "url": url, "claim_span": _claim_span(title, "", node.get("description")), "source_class": source_class}


def _activity_from_visible(soup: BeautifulSoup, page_url: str, text: str, *, source_class: str = "company_owned") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    candidates = list(soup.select("article, li, [class*='news'], [class*='article'], [class*='press'], [class*='aktuelt']"))
    candidates.extend(soup.select("time"))
    for node in candidates[:300]:
        node_text = " ".join(node.get_text(" ", strip=True).split())
        date_node = node.select_one("time[datetime]") if hasattr(node, "select_one") else None
        parsed = parse_date(date_node.get("datetime") if date_node else node_text)
        if not parsed:
            continue
        anchor = node.select_one("a[href]") if hasattr(node, "select_one") else None
        url = urllib.parse.urljoin(page_url, str(anchor.get("href") if anchor else page_url))
        if not _same_site(page_url, url):
            continue
        heading = node.select_one("h1, h2, h3, h4") if hasattr(node, "select_one") else None
        title = _clean_sentence(heading.get_text(" ", strip=True) if heading else node_text)
        found.append({"title": title or "Company activity", "date": parsed, "url": url, "claim_span": _claim_span(title, node_text), "source_class": source_class})
    # A visible dated listing is allowed even when the page does not use article/li.
    if not found and _path_has_term(page_url, ACTIVITY_TERMS):
        parsed = parse_date(text)
        if parsed:
            found.append({"title": _clean_sentence(soup.title.get_text(" ", strip=True) if soup.title else "Company activity"), "date": parsed, "url": page_url, "claim_span": _claim_span("", text), "source_class": source_class})
    return found


def parse_activity_html(html: str | bytes, url: str, *, source_class: str = "company_owned") -> list[dict[str, Any]]:
    soup = _parse_soup(html)
    if soup.find("rss") or soup.find("feed"):
        return parse_activity_feed(html, url, source_class=source_class)
    json_ld = _jsonld_from_html(soup)
    found = [item for node in _jsonld_values(json_ld) if (item := _activity_from_jsonld(node, url, source_class=source_class))]
    for meta in soup.select('meta[property="article:published_time"], meta[name="datePublished"], meta[itemprop="datePublished"]'):
        parsed = parse_date(meta.get("content"))
        if parsed:
            found.append({"title": _clean_sentence(soup.title.get_text(" ", strip=True) if soup.title else "Company activity"), "date": parsed, "url": url, "claim_span": _claim_span("", soup.get_text(" ", strip=True)), "source_class": source_class})
    text = " ".join(soup.get_text(" ", strip=True).split())
    found.extend(_activity_from_visible(soup, url, text, source_class=source_class))
    return _dedupe_items(found, keys=("date", "url", "title"))


def parse_activity_feed(raw: str | bytes, url: str, *, source_class: str = "company_owned") -> list[dict[str, Any]]:
    soup = BeautifulSoup(raw, "xml")
    found: list[dict[str, Any]] = []
    for item in soup.find_all(["item", "entry"]):
        title = _clean_sentence(item.find("title").get_text(" ", strip=True) if item.find("title") else "Company activity")
        date_value = next((item.find(name).get_text(" ", strip=True) for name in ("pubDate", "published", "updated", "date") if item.find(name)), "")
        parsed = parse_date(date_value)
        if not parsed:
            continue
        link_node = item.find("link")
        link = str(link_node.get("href") if link_node and link_node.get("href") else link_node.get_text(" ", strip=True) if link_node else url)
        link = urllib.parse.urljoin(url, link)
        if not _same_site(url, link):
            continue
        description = item.find("description") or item.find("summary")
        found.append({"title": title, "date": parsed, "url": link, "claim_span": _claim_span(title, "", description.get_text(" ", strip=True) if description else ""), "source_class": source_class})
    return _dedupe_items(found, keys=("date", "url", "title"))


def parse_sitemap_activity(raw: str | bytes, url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(raw, "xml")
    found: list[dict[str, Any]] = []
    for node in soup.find_all("url"):
        loc = node.find("loc")
        lastmod = node.find("lastmod")
        target = loc.get_text(" ", strip=True) if loc else ""
        parsed = parse_date(lastmod.get_text(" ", strip=True) if lastmod else "")
        if not target or not parsed or not _same_site(url, target) or not _path_has_term(target, ACTIVITY_TERMS):
            continue
        title = _clean_sentence(urllib.parse.unquote(urllib.parse.urlparse(target).path.rstrip("/").rsplit("/", 1)[-1]).replace("-", " ")) or "Company activity"
        found.append({"title": title, "date": parsed, "url": target, "claim_span": f"sitemap lastmod {parsed}", "source_class": "company_owned"})
    return _dedupe_items(found, keys=("date", "url", "title"))


def _job_from_jsonld(node: dict[str, Any], page_url: str) -> dict[str, Any] | None:
    kinds = node.get("@type") or node.get("type") or []
    kinds = {str(item).casefold() for item in (kinds if isinstance(kinds, list) else [kinds])}
    if "jobposting" not in kinds:
        return None
    url = urllib.parse.urljoin(page_url, str(node.get("url") or page_url))
    if not _same_site(page_url, url):
        return None
    location = node.get("jobLocation") or node.get("job_location") or ""
    if isinstance(location, list):
        location = ", ".join(_location_text(item) for item in location)
    else:
        location = _location_text(location)
    title = _clean_sentence(node.get("title") or node.get("name") or "Open position")
    return {"title": title or "Open position", "location": location, "posted": parse_date(node.get("datePosted")), "closing": parse_date(node.get("validThrough")), "url": url, "claim_span": _claim_span(title, location, node.get("description")), "source_class": "company_owned"}


def _location_text(value: Any) -> str:
    if isinstance(value, dict):
        address = value.get("address") or value
        if isinstance(address, dict):
            return ", ".join(str(address.get(key) or "").strip() for key in ("streetAddress", "addressLocality", "addressRegion") if str(address.get(key) or "").strip())
    return " ".join(str(value or "").split())


def parse_jobs_html(html: str | bytes, url: str) -> tuple[list[dict[str, Any]], bool]:
    soup = _parse_soup(html)
    json_ld = _jsonld_from_html(soup)
    jobs = [item for node in _jsonld_values(json_ld) if (item := _job_from_jsonld(node, url))]
    for node in soup.select("article, li, [class*='job'], [class*='stilling'], [class*='career']")[:300]:
        text = " ".join(node.get_text(" ", strip=True).split())
        if not text or NO_OPEN_ROLES.search(text):
            continue
        heading = node.select_one("h1, h2, h3, h4, a")
        title = _clean_sentence(heading.get_text(" ", strip=True) if heading else text)
        if not title or len(title) < 3:
            continue
        href = heading.get("href") if heading and heading.name == "a" else None
        target = urllib.parse.urljoin(url, str(href or url))
        if not _same_site(url, target):
            continue
        jobs.append({"title": title, "location": "", "posted": parse_date(text), "closing": None, "url": target, "claim_span": _claim_span(title, text), "source_class": "company_owned"})
    return _dedupe_jobs(jobs), bool(json_ld or soup.select("article, li, [class*='job'], [class*='stilling'], [class*='career']"))


def _path_has_term(url: str, terms: Iterable[str]) -> bool:
    path = urllib.parse.urlparse(url).path.casefold()
    return any(term in path for term in terms)


def _dedupe_items(items: Iterable[dict[str, Any]], *, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    result = []
    for item in items:
        marker = tuple(str(item.get(key) or "") for key in keys)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def _dedupe_jobs(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        key = (re.sub(r"\W+", " ", str(item.get("title") or "").casefold()).strip(), re.sub(r"\W+", " ", str(item.get("location") or "").casefold()).strip())
        if not key[0]:
            continue
        by_key.setdefault(key, item)
    return list(by_key.values())


def _retrieved_date(value: Any) -> date:
    parsed = parse_date(value)
    return date.fromisoformat(parsed) if parsed else datetime.now(timezone.utc).date()


def _mark_old(items: list[dict[str, Any]], retrieved_at: Any) -> list[dict[str, Any]]:
    cutoff = _retrieved_date(retrieved_at) - timedelta(days=730)
    for item in items:
        item["old"] = bool(item.get("date") and date.fromisoformat(str(item["date"])) < cutoff)
    return items


def _stem_tokens(value: Any) -> set[str]:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold()
    tokens = {token for token in re.findall(r"[a-z0-9]+", text) if token not in STOPWORDS and len(token) > 2}
    result = set()
    for token in tokens:
        stem = token
        for suffix in STEM_SUFFIXES:
            if len(stem) > len(suffix) + 2 and stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        result.add(stem)
    return result


def _registry_fields(profile: dict[str, Any]) -> tuple[str, str, str]:
    raw = profile.get("raw") or {}
    industry = str(profile.get("industry_label") or raw.get("naeringskode1.beskrivelse") or raw.get("naeringskode1_beskrivelse") or raw.get("industry_label") or "").strip()
    municipality = str(profile.get("municipality") or raw.get("forretningsadresse.kommune") or raw.get("postadresse.kommune") or "").strip()
    name = str(profile.get("name") or raw.get("navn") or "").strip()
    return name, municipality, industry


def _site_pages(website: dict[str, Any]) -> list[dict[str, Any]]:
    value = website.get("value") or {}
    pages = list(value.get("pages") or [])
    if not pages and value:
        pages = [value]
    return [page for page in pages if isinstance(page, dict)]


def extract_company_brief(profile: dict[str, Any], website: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    name, municipality, industry = _registry_fields(profile)
    registry_text = f"Registered in {municipality or 'an unreported municipality'}; industry: {industry or 'not reported'}."
    parts = [{"text": registry_text, "source_class": "official_registry", "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv", "claim_span": registry_text, "kind": "registry"}]
    site_value = (website or {}).get("value") or {}
    site_url = str(site_value.get("final_url") or (website or {}).get("source_url") or "")
    site_texts: list[tuple[str, str]] = []
    for page in _site_pages(website or {}):
        json_ld = page.get("json_ld") or page.get("structured_json_ld") or page.get("structured_data") or []
        for node in _jsonld_values(json_ld):
            if node.get("description"):
                site_texts.append((_clean_sentence(node["description"]), "jsonld_description"))
        for key in ("description", "meta_description", "h1", "lead"):
            if page.get(key):
                site_texts.append((_clean_sentence(page[key]), key))
    for key in ("description", "h1", "lead"):
        if site_value.get(key):
            site_texts.append((_clean_sentence(site_value[key]), key))
    deduped = _dedupe_items(({"text": text, "kind": kind} for text, kind in site_texts if text), keys=("text",))
    for item in deduped[:4]:
        parts.append({"text": item["text"], "source_class": "company_owned", "source_url": site_url, "claim_span": item["text"], "kind": item["kind"]})
    if not site_url or (website or {}).get("status") != "available":
        return "available", {"summary": registry_text, "parts": parts, "registry_derived": True, "source_url": parts[0]["source_url"], "checked": True}
    site_text = " ".join(item["text"] for item in parts[1:])
    ambiguous = bool(industry and site_text and not (_stem_tokens(industry) & _stem_tokens(site_text)))
    summary = " ".join(item["text"] for item in parts)
    return ("ambiguous" if ambiguous else "available"), {"summary": summary, "parts": parts, "registry_derived": False, "checked": True, "site_disagrees_with_registry": ambiguous, "source_url": site_url}


def _nav_postings(profile: dict[str, Any]) -> list[dict[str, Any]]:
    record = (profile.get("evidence") or {}).get("nav_jobs") or {}
    value = record.get("value") or {}
    ads = value.get("ads") if isinstance(value, dict) else value
    result = []
    for ad in ads or []:
        if not isinstance(ad, dict):
            continue
        result.append({
            "title": str(ad.get("title") or "Open NAV position"),
            "location": _location_text(ad.get("location") or ad.get("workLocations") or ""),
            "posted": parse_date(ad.get("published") or ad.get("posted")),
            "closing": parse_date(ad.get("expires") or ad.get("closing")),
            "url": str(ad.get("link") or ad.get("source_url") or "https://arbeidsplassen.nav.no/stillinger"),
            "claim_span": _claim_span(str(ad.get("title") or "Open NAV position"), str(ad.get("location") or "")),
            "source_class": "nav_feed",
        })
    return _dedupe_jobs(result)


def _merge_postings(site: list[dict[str, Any]], nav: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    agreements: list[dict[str, Any]] = []
    for posting in [*site, *nav]:
        key = (re.sub(r"\W+", " ", str(posting.get("title") or "").casefold()).strip(), re.sub(r"\W+", " ", str(posting.get("location") or "").casefold()).strip())
        if key in merged:
            existing = merged[key]
            existing.setdefault("source_urls", [existing.get("url")])
            if posting.get("url") not in existing["source_urls"]:
                existing["source_urls"].append(posting.get("url"))
            existing.setdefault("source_classes", [existing.get("source_class")])
            if posting.get("source_class") not in existing["source_classes"]:
                existing["source_classes"].append(posting.get("source_class"))
            agreements.append({"title": existing.get("title"), "location": existing.get("location"), "sources": list(existing["source_classes"])})
        else:
            merged[key] = dict(posting)
    return list(merged.values()), agreements


def extract_site_facts(profile: dict[str, Any], *, extra_documents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build three evidence records from a published website and registry/NAV."""
    website = (profile.get("evidence") or {}).get("website") or {}
    website_available = website.get("status") == "available"
    retrieved_at = website.get("retrieved_at") or utc_now()
    value = website.get("value") or {}
    pages = _site_pages(website)
    documents = [*pages, *(extra_documents or [])]
    activity: list[dict[str, Any]] = []
    site_jobs: list[dict[str, Any]] = []
    career_found = False
    career_parseable = False
    no_open_roles = False
    parse_errors: list[str] = []
    base_url = str(value.get("final_url") or website.get("source_url") or "")
    for page in documents:
        page_url = str(page.get("url") or base_url)
        json_ld, text, opengraph, links = _page_payload(page, base_url=page_url or base_url)
        for node in _jsonld_values(json_ld):
            item = _activity_from_jsonld(node, page_url)
            if item:
                activity.append(item)
            job = _job_from_jsonld(node, page_url)
            if job:
                site_jobs.append(job)
        if opengraph.get("article:published_time"):
            parsed = parse_date(opengraph["article:published_time"])
            if parsed:
                activity.append({"title": _clean_sentence(page.get("title") or "Company activity"), "date": parsed, "url": page_url, "claim_span": _claim_span(str(page.get("title") or ""), text), "source_class": "company_owned"})
        if page.get("html") or page.get("raw"):
            raw = page.get("html") or page.get("raw")
            activity.extend(parse_activity_html(raw, page_url))
            jobs, parseable = parse_jobs_html(raw, page_url)
            site_jobs.extend(jobs)
            career_parseable = career_parseable or parseable
            if NO_OPEN_ROLES.search(str(raw)):
                no_open_roles = True
        for link in links:
            target = link.get("url") or ""
            if _path_has_term(target, CAREER_TERMS) or any(term in str(link.get("text") or "").casefold() for term in CAREER_TERMS):
                career_found = True
        if _path_has_term(page_url, CAREER_TERMS):
            career_found = True
            if NO_OPEN_ROLES.search(text):
                no_open_roles = True
    for document in extra_documents or []:
        if document.get("kind") == "feed":
            activity.extend(parse_activity_feed(document.get("raw") or document.get("html") or "", str(document.get("url") or base_url)))
        elif document.get("kind") == "sitemap":
            activity.extend(parse_sitemap_activity(document.get("raw") or document.get("html") or "", str(document.get("url") or base_url)))
        if _path_has_term(str(document.get("url") or ""), CAREER_TERMS):
            career_found = True
    activity = _dedupe_items(activity, keys=("date", "url", "title"))
    activity = sorted(activity, key=lambda item: (str(item.get("date") or ""), str(item.get("url") or "")), reverse=True)
    activity = _mark_old(activity, retrieved_at)[:10]
    nav_activity = []
    for posting in _nav_postings(profile):
        if posting.get("posted"):
            nav_activity.append({**posting, "date": posting["posted"], "source_class": "nav_feed", "claim_span": posting.get("claim_span") or posting.get("title")})
    activity = _mark_old(_dedupe_items([*activity, *nav_activity], keys=("date", "url", "title")), retrieved_at)
    activity = sorted(activity, key=lambda item: (str(item.get("date") or ""), str(item.get("url") or "")), reverse=True)[:10]
    nav_evidence = (profile.get("evidence") or {}).get("nav_jobs") or {}
    # A complete NAV index that was consulted for this organisation is a real check, even
    # when the company has no verified website to crawl.
    nav_checked = nav_evidence.get("status") in {"available", "not_found"}
    activity_status = "available" if activity else ("blocked" if website.get("status") == "blocked" else "not_available" if (website_available or nav_checked) else "not_checked")
    if parse_errors and not activity:
        activity_status = "failed"
    brief_status, brief_value = extract_company_brief(profile, website)
    nav_jobs = _nav_postings(profile)
    site_jobs = _dedupe_jobs(site_jobs)
    merged_jobs, agreements = _merge_postings(site_jobs, nav_jobs)
    if career_found:
        hiring_status = "available" if site_jobs or no_open_roles else "ambiguous" if not career_parseable else "not_available"
    else:
        hiring_status = "available" if nav_jobs else "not_available" if (website_available or nav_checked) else "not_checked"
    hiring_value = {
        "postings": merged_jobs,
        "company_site_postings": site_jobs,
        "nav_ads": nav_jobs,
        "careers_page_found": career_found,
        "no_open_roles": no_open_roles,
        "checked": bool(website_available or (profile.get("evidence") or {}).get("nav_jobs")),
        "sources_agree": agreements,
    }
    digest = hashlib.sha256(json.dumps({"activity": activity, "brief": brief_value, "hiring": hiring_value}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    source_url = base_url or str((profile.get("evidence") or {}).get("registry", {}).get("source_url") or "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv")
    return {
        "public_activity": evidence("public_activity", activity_status, "company_owned" if website_available else "nav_feed", source_url, value={"items": activity, "checked": bool(website_available), "max_items": 10}, note="Dated company-site and NAV activity; dates are parsed from source content", retrieved_at=retrieved_at, content_sha256=digest),
        "company_brief": evidence("company_brief", brief_status, "company_owned" if not brief_value.get("registry_derived") else "official_registry", source_url, value=brief_value, note="Deterministic registry and verified-site description parts", retrieved_at=retrieved_at, content_sha256=digest),
        "hiring": evidence("hiring", hiring_status, "company_owned" if site_jobs else "nav_feed", source_url if site_jobs else "https://arbeidsplassen.nav.no/stillinger", value=hiring_value, note="Company-owned careers postings merged with exact-organisation NAV ads", retrieved_at=retrieved_at, content_sha256=digest),
        "metrics": {"requests": 0, "bytes": 0, "latencies_ms": [], "activity_items": len(activity), "company_site_jobs": len(site_jobs), "merged_jobs": len(merged_jobs), "career_pages": int(career_found)},
    }


def _fetch_fact_document(url: str, *, base_url: str, timeout: float, request_policy: Any = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Fetch one same-domain document with the existing SSRF/robots-safe opener."""
    started = time.monotonic()
    if not _same_site(base_url, url):
        return None, {"requests": 0, "bytes": 0, "latencies_ms": [], "error": "cross_domain"}
    try:
        safe_web.assert_public_url(url)
        host = request_policy.host_key(url) if request_policy else None
        robots_cached = bool(request_policy and host in getattr(request_policy, "_robots", {}))
        allowed = safe_web._robots_allowed(url, timeout, request_policy)
        if not allowed:
            return None, {"requests": 1, "bytes": 0, "latencies_ms": [0], "blocked": True, "url": url}
        request = safe_web.urllib.request.Request(url, headers={"User-Agent": safe_web.USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml,text/xml,application/rss+xml,application/atom+xml"})
        with safe_web._open_response(request, timeout, request_policy) as response:
            raw = safe_web._read_response(response, 1_000_001, timeout)
            final_url = response.geturl()
            content_type = response.headers.get("content-type", "")
        if len(raw) > 1_000_000 or not _same_site(base_url, final_url):
            return None, {"requests": 1, "bytes": len(raw), "latencies_ms": [int((time.monotonic() - started) * 1000)], "error": "oversized_or_cross_domain", "url": url}
        kind = "sitemap" if "sitemap" in url.casefold() or raw.lstrip().startswith(b"<urlset") or raw.lstrip().startswith(b"<sitemapindex") else "feed" if "rss" in content_type.casefold() or "atom" in content_type.casefold() or raw.lstrip().startswith((b"<rss", b"<feed")) else "html"
        html = raw.decode("utf-8", errors="replace")
        page: dict[str, Any] = {"url": final_url, "content_sha256": hashlib.sha256(raw).hexdigest(), "kind": kind, "raw": raw if kind != "html" else None}
        if kind == "html":
            soup = BeautifulSoup(html, "lxml")
            page.update(parse_html_metadata(html, final_url, soup=soup))
            page["html"] = html
        return page, {"requests": 1 if robots_cached else 2, "bytes": len(raw), "latencies_ms": [int((time.monotonic() - started) * 1000)], "url": final_url}
    except safe_web.PublicURLPolicyError as exc:
        return None, {"requests": 0, "bytes": 0, "latencies_ms": [], "blocked": True, "error": str(exc), "url": url}
    except Exception as exc:  # noqa: BLE001 - source failure is contained in the module
        return None, {"requests": 1, "bytes": 0, "latencies_ms": [int((time.monotonic() - started) * 1000)], "error": f"{type(exc).__name__}: {exc}", "url": url}


def parse_html_metadata(html: str, url: str, *, soup: BeautifulSoup | None = None) -> dict[str, Any]:
    soup = soup or _parse_soup(html)
    json_ld = _jsonld_from_html(soup)
    description = soup.select_one('meta[name="description"], meta[property="og:description"]')
    h1 = soup.find("h1")
    paragraph = next((node for node in soup.find_all(["p", "div"]) if node.get_text(" ", strip=True)), None)
    links = [{"url": urllib.parse.urljoin(url, str(anchor.get("href") or "")), "text": " ".join(anchor.get_text(" ", strip=True).split())} for anchor in soup.select("a[href]")[:200]]
    alternate = []
    for link in soup.select('link[rel~="alternate"][href]'):
        link_type = str(link.get("type") or "").casefold()
        if "rss" in link_type or "atom" in link_type or "xml" in link_type or "feed" in str(link.get("href") or "").casefold():
            alternate.append(urllib.parse.urljoin(url, str(link.get("href"))))
    return {
        "title": soup.title.get_text(" ", strip=True)[:500] if soup.title else "",
        "description": str(description.get("content") or "").strip()[:2000] if description else "",
        "h1": _clean_sentence(h1.get_text(" ", strip=True)) if h1 else "",
        "lead": _clean_sentence(paragraph.get_text(" ", strip=True)) if paragraph else "",
        "json_ld": json_ld,
        "opengraph": {str(meta.get("property") or meta.get("name") or "").casefold(): str(meta.get("content") or "") for meta in soup.select("meta[property], meta[name]")},
        "links": links,
        "alternate_feeds": alternate,
        "visible_text": " ".join(soup.get_text(" ", strip=True).split())[:10000],
    }


def enrich_site_facts(
    profile: dict[str, Any],
    *,
    request_policy: Any = None,
    timeout: float = 8.0,
    max_extra_requests: int = 6,
    deadline: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use captured pages first, then fetch at most six safe same-domain documents."""
    website = (profile.get("evidence") or {}).get("website") or {}
    if website.get("status") != "available":
        facts = extract_site_facts(profile)
        return facts, facts["metrics"]
    value = website.get("value") or {}
    base_url = str(value.get("final_url") or website.get("source_url") or "")
    candidates: list[tuple[str, str]] = []
    for page in _site_pages(website):
        for link in page.get("links") or []:
            target = urllib.parse.urljoin(str(page.get("url") or base_url), str(link.get("url") or link.get("href") or ""))
            text = str(link.get("text") or "").casefold()
            if _path_has_term(target, CAREER_TERMS) or any(term in text for term in CAREER_TERMS):
                candidates.append((target, "career"))
            elif _path_has_term(target, ACTIVITY_TERMS):
                candidates.append((target, "activity"))
        for feed in page.get("alternate_feeds") or []:
            candidates.append((urllib.parse.urljoin(str(page.get("url") or base_url), str(feed)), "feed"))
    for path, kind in (("/karriere", "career"), ("/careers", "career"), ("/jobb", "career"), ("/jobs", "career"), ("/ledige-stillinger", "career"), ("/news", "activity"), ("/nyheter", "activity"), ("/aktuelt", "activity"), ("/blog", "activity"), ("/presse", "activity"), ("/sitemap.xml", "sitemap")):
        candidates.append((urllib.parse.urljoin(base_url, path), kind))
    seen = {str(page.get("url") or "") for page in _site_pages(website)}
    extras: list[dict[str, Any]] = []
    metrics = {"requests": 0, "bytes": 0, "latencies_ms": [], "extra_requests": 0, "extra_errors": []}
    for target, kind in candidates:
        if metrics["extra_requests"] >= max_extra_requests or target in seen or (deadline is not None and time.monotonic() >= deadline):
            continue
        if not _same_site(base_url, target):
            continue
        seen.add(target)
        document, operation = _fetch_fact_document(target, base_url=base_url, timeout=timeout, request_policy=request_policy)
        metrics["requests"] += int(operation.get("requests") or 0)
        metrics["bytes"] += int(operation.get("bytes") or 0)
        metrics["latencies_ms"].extend(operation.get("latencies_ms") or [])
        metrics["extra_requests"] += int(operation.get("requests") or 0)
        if document:
            document["kind"] = kind if document.get("kind") == "html" else document.get("kind")
            extras.append(document)
        elif operation.get("error") or operation.get("blocked"):
            metrics["extra_errors"].append({"url": target, "kind": kind, "error": operation.get("error") or "robots_blocked", "blocked": bool(operation.get("blocked"))})
    facts = extract_site_facts(profile, extra_documents=extras)
    facts["metrics"].update(metrics)
    return facts, metrics


def attach_site_facts(profile: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    records = profile.setdefault("evidence", {})
    for field in ("public_activity", "company_brief", "hiring"):
        if field in facts:
            records[field] = facts[field]
    metrics = profile.setdefault("run_metrics", {})
    site_metrics = facts.get("metrics") or {}
    metrics["site_fact_requests"] = int(site_metrics.get("requests") or 0)
    metrics["site_fact_bytes"] = int(site_metrics.get("bytes") or 0)
    metrics["site_fact_elapsed_ms"] = sum(int(value) for value in site_metrics.get("latencies_ms") or [])
    metrics["elapsed_ms"] = int(metrics.get("elapsed_ms") or 0) + metrics["site_fact_elapsed_ms"]
    metrics["site_fact_items"] = {key: site_metrics.get(key) for key in ("activity_items", "company_site_jobs", "merged_jobs", "career_pages")}
    metrics["requests"] = int(metrics.get("requests") or 0) + metrics["site_fact_requests"]
    metrics["bytes"] = int(metrics.get("bytes") or 0) + metrics["site_fact_bytes"]
    metrics["latencies_ms"] = list(metrics.get("latencies_ms") or []) + list(site_metrics.get("latencies_ms") or [])
    return profile


__all__ = [
    "ACTIVITY_TERMS", "CAREER_TERMS", "attach_site_facts", "enrich_site_facts",
    "extract_company_brief", "extract_site_facts", "parse_activity_feed",
    "parse_activity_html", "parse_date", "parse_jobs_html", "parse_sitemap_activity",
]
