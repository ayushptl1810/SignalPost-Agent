"""Deterministic facts extracted from a verified company website.

This module deliberately sits after website discovery.  It never chooses a
website and it never changes the identity or first-party decision that allowed
the website to be published.  Its parsers are also useful on captured HTML in
tests, which keeps the default batch path free of model or keyed-provider
calls.
"""
from __future__ import annotations

import hashlib
from html import unescape
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
    "pressemelding", "pressemeldinger", "artikkel", "blogg", "investor", "om-oss/nyheter", "om_oss/nyheter",
)
ACTIVITY_BLOCKED_SLUGS = {
    "about", "about-us", "om-oss", "kontakt", "kontakt-oss", "personvern", "privacy",
    "hei-verden", "hello-world", "cookie", "cookies", "vilkar", "terms", "contact",
}
ACTIVITY_NAV_WORDS = {
    "about", "contact", "home", "hjem", "kontakt", "menu", "meny", "om", "oss", "search", "søk",
}
ACTIVITY_LISTING_SLUGS = {"news", "nyheter", "blog", "blogg", "aktuelt", "press", "presse", "artikkel", "pressemelding"}
ACTIVITY_PLACEHOLDER_TITLES = {"company activity", "news archive", "nyhetsarkiv"}
NO_OPEN_ROLES = re.compile(
    r"(?:ingen|ingen\s+har)\s+(?:ledige\s+)?stillinger|ingen\s+ledige\s+stillinger|no\s+open\s+positions?|no\s+vacancies|we\s+are\s+not\s+hiring",
    re.I,
)
JOB_TITLE_CUES = (
    "søker", "søke", "ledig", "stilling", "stillinger", "rekrutter", "vi trenger",
    "tømrer", "rørlegger", "sykepleier", "pedagog", "ingeniør", "konsulent", "leder",
    "assistent", "montør", "revisor", "manager", "engineer", "developer", "technician",
    "technicians", "marketing", "butikkmedarbeider", "eiendomsforvalter", "housekeeping",
    "production", "operator", "advisor", "analyst", "designer", "coordinator", "specialist",
    "consultant", "director", "nurse", "vaktmester",
)
JOB_ROLE_WORDS = frozenset({
    "accountant", "accountants", "advokat", "arkitekt", "architect", "analyst", "analytiker", "assistent", "assistant",
    "barnehagelærer", "barnehagelaerer", "bioingeniør", "bioingenior", "butikkmedarbeider", "controller", "coordinator",
    "dataingeniør", "dataingenior", "designer", "developer", "direktør", "direktor", "director", "driftsleder",
    "driftsoperatør", "driftsoperator", "driver", "elektriker", "engineer", "ingeniør", "ingenior", "eiendomsforvalter",
    "fagarbeider", "fysioterapeut", "gartner", "grafisk", "helsefagarbeider", "helsesekretær", "helsesekretaer", "housekeeping",
    "ingeniør", "ingenior", "installatør", "installator", "journalist", "jurist", "kokk", "koordinator", "konsulent", "consultant",
    "kundebehandler", "kunderådgiver", "kunderaadgiver", "lagerarbeider", "lagermedarbeider", "leder", "leader", "lege", "logistikkmedarbeider",
    "lokfører", "lokforer", "lønnskonsulent", "lonnskonsulent", "lærer", "laerer", "manager", "markedsfører", "markedsforer",
    "mekaniker", "miljøterapeut", "miljoterapeut", "montør", "montor", "nurse", "operatør", "operator", "optiker", "pedagog",
    "produksjonsleder", "produksjonsmedarbeider", "produksjonstekniker", "production", "psykolog", "prosjektleder", "prosjektkoordinator",
    "rådgiver", "radgiver", "receptionist", "resepsjonist", "renholder", "revisor", "reviewer", "rørlegger", "rorlegger", "salgsingeniør",
    "salgsingenior", "selger", "servicetekniker", "sjåfør", "sjafor", "snekker", "socialarbeider", "sosialarbeider", "specialist",
    "spesialpedagog", "sykepleier", "systemutvikler", "taktekker", "tannlege", "tannpleier", "teacher", "teamleder", "technician",
    "technicians", "tekniker", "terapeut", "testingeniør", "testingenior", "tolk", "trener", "tømrer", "tomrer", "utvikler",
    "vaktmester", "vasker", "vekter", "vernepleier", "veterinær", "veterinaer", "webdesigner", "økonom", "okonom", "økonomisjef", "okonomisjef",
})
JOB_TITLE_LABEL_PREFIXES = (
    "ledige stillinger", "careers", "karriere", "jobs", "join us", "jobb hos oss", "open positions", "stillinger",
)
JOB_TITLE_NEGATION_RE = re.compile(r"\b(?:ikke|not)\b|\bno\s+longer\b", re.I)
JOB_EDITORIAL_PATH_TERMS = {"blog", "blogg", "news", "nyheter", "aktuelt", "artikkel"}
JOB_NAV_WORDS = {
    "about", "about us", "bil", "bli forhandler", "buy", "careers", "career", "company",
    "contact", "kontakt", "clinic", "klinikk", "du er her", "forhandler", "hjem", "home",
    "inn", "login", "logg inn", "menu", "meny", "norsk bokmål", "om oss", "oss", "products",
    "produkter", "services", "selskaper", "tenester", "tjenester", "våre selskaper", "våre tjenester",
}
RECOGNIZED_ATS_DOMAINS = {
    "recman.no", "webcruiter.com", "varbi.com", "teamtailor.com", "easycruit.com", "lever.co",
    "greenhouse.io", "greenhouse.com", "personio.com", "jobylon.com", "hr-manager.net",
}
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


def _clean_job_title(value: Any) -> str:
    soup = BeautifulSoup(str(value or ""), "lxml")
    text = unescape(" ".join(soup.get_text(" ", strip=True).split()))
    return text[:120].strip() if len(text) <= 120 else ""


def _normalized_job_title(value: Any) -> str:
    return re.sub(r"\s+", " ", _clean_job_title(value).casefold()).strip(" .:-")


def _job_title_tokens(value: Any) -> set[str]:
    normalized = _normalized_job_title(value).translate(str.maketrans({"æ": "ae", "ø": "o", "å": "a", "Æ": "AE", "Ø": "O", "Å": "A"}))
    normalized = unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode()
    return set(re.findall(r"[a-z0-9]+", normalized))


def _is_navigation_job_title(title: str) -> bool:
    return _normalized_job_title(title) in JOB_NAV_WORDS


def _has_job_title_cue(title: str) -> bool:
    normalized = _normalized_job_title(title)
    return any(re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", normalized, re.I) for cue in JOB_TITLE_CUES)


def _job_title_is_label_or_place(title: str) -> bool:
    normalized = _normalized_job_title(title)
    if any(normalized == label or any(normalized.startswith(label + separator) for separator in (" ", ":", "-")) for label in JOB_TITLE_LABEL_PREFIXES):
        return True
    if re.fullmatch(r"careers\s+in\s+[a-zÀ-ÿ]+", normalized, re.I):
        return True
    tokens = _job_title_tokens(title)
    return len(tokens) == 1 and not (tokens & {word.casefold() for word in JOB_ROLE_WORDS})


def _job_title_is_visible_posting(title: str) -> bool:
    if _job_title_is_label_or_place(title):
        return False
    if len(_job_title_tokens(title)) > 12 or re.search(r"[.!?]\s*$", _clean_job_title(title)):
        return False
    if JOB_TITLE_NEGATION_RE.search(_normalized_job_title(title)):
        return False
    return bool(_job_title_tokens(title) & {word.casefold() for word in JOB_ROLE_WORDS})


def _job_title_is_ats_posting(title: str) -> bool:
    return not _job_title_is_label_or_place(title) and not JOB_TITLE_NEGATION_RE.search(_normalized_job_title(title))


def _job_path_is_editorial(url: str) -> bool:
    try:
        segments = [unescape(segment).casefold() for segment in urllib.parse.urlparse(url).path.split("/") if segment]
    except (TypeError, ValueError):
        return False
    return any(segment in JOB_EDITORIAL_PATH_TERMS for segment in segments)


def _is_recognized_ats_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            return False
        domain = safe_web.registered_domain(url)
        if domain == "finn.no":
            return bool(re.search(r"/(?:jobb)(?:/|$)", parsed.path.casefold()))
        return domain in RECOGNIZED_ATS_DOMAINS
    except (TypeError, ValueError):
        return False


def _job_url_allowed(page_url: str, candidate: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(candidate)
        if parsed.scheme != "https":
            return False
    except (TypeError, ValueError):
        return False
    return _same_site(page_url, candidate) or _is_recognized_ats_url(candidate)


def _job_page_is_career_url(url: str) -> bool:
    return _path_has_term(url, CAREER_TERMS)


def _same_site(base_url: str, candidate: str) -> bool:
    try:
        return bool(candidate) and safe_web.registered_domain(candidate) == safe_web.registered_domain(base_url)
    except (ValueError, TypeError):
        return False


def _claim_span(title: str, text: str, explicit: Any = None) -> str:
    return _clean_sentence(explicit or title or text, limit=500) or "Explicit structured or visible site item"


def _clean_claim_span(value: Any) -> str:
    soup = BeautifulSoup(str(value or ""), "lxml")
    for node in soup.select("script, style, noscript"):
        node.decompose()
    text = unescape(" ".join(soup.get_text(" ", strip=True).split()))
    return text[:200].strip()


def _activity_url_allowed(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.fragment.casefold().startswith("comment-"):
        return False
    segments = [unescape(segment).casefold() for segment in parsed.path.split("/") if segment]
    if any(segment in ACTIVITY_BLOCKED_SLUGS for segment in segments):
        return False
    slug = segments[-1] if segments else ""
    return slug not in ACTIVITY_LISTING_SLUGS and slug not in ACTIVITY_PLACEHOLDER_TITLES


def _activity_is_nav_or_placeholder(title: str, claim_span: str) -> bool:
    if title.casefold().strip() in ACTIVITY_PLACEHOLDER_TITLES:
        return True
    tokens = [token.casefold() for token in re.findall(r"[\wÀ-ÿÆØÅæøå'-]+", claim_span, re.UNICODE)]
    return bool(tokens) and all(token in ACTIVITY_NAV_WORDS for token in tokens)


def _activity_item(title: Any, parsed: str, url: str, claim: Any, *, source_class: str) -> dict[str, Any] | None:
    clean_title = _clean_sentence(title, limit=120)
    claim_span = _clean_claim_span(claim or clean_title)
    if not clean_title or not parsed or not url or not _activity_url_allowed(url) or not claim_span:
        return None
    if _activity_is_nav_or_placeholder(clean_title, claim_span):
        return None
    return {"title": clean_title, "date": parsed, "url": url, "claim_span": claim_span, "source_class": source_class}


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
    if not kinds & {"newsarticle", "blogposting", "article"}:
        return None
    parsed = parse_date(node.get("datePublished"))
    if not parsed:
        return None
    raw_url = node.get("url") or node.get("mainEntityOfPage") or page_url
    if isinstance(raw_url, dict):
        raw_url = raw_url.get("@id") or raw_url.get("url")
    url = urllib.parse.urljoin(page_url, str(raw_url or page_url))
    if not _same_site(page_url, url):
        return None
    title = _clean_sentence(node.get("headline") or node.get("name") or node.get("title"))
    return _activity_item(title, parsed, url, node.get("description") or title, source_class=source_class)


def _activity_from_visible(soup: BeautifulSoup, page_url: str, text: str, *, source_class: str = "company_owned") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    candidates = list(soup.select("article, li"))
    for node in candidates[:300]:
        node_text = " ".join(node.get_text(" ", strip=True).split())
        date_node = node.select_one("time[datetime]")
        parsed = parse_date(date_node.get("datetime") if date_node else "")
        if not parsed:
            continue
        anchor = node.select_one("a[href]") if hasattr(node, "select_one") else None
        url = urllib.parse.urljoin(page_url, str(anchor.get("href") if anchor else page_url))
        if not _same_site(page_url, url):
            continue
        heading = node.select_one("h1, h2, h3, h4") if hasattr(node, "select_one") else None
        title = _clean_sentence(heading.get_text(" ", strip=True) if heading else node_text)
        paragraph = node.select_one("p")
        claim = paragraph.get_text(" ", strip=True) if paragraph else title
        item = _activity_item(title, parsed, url, claim, source_class=source_class)
        if item:
            found.append(item)
    return found


def parse_activity_html(html: str | bytes, url: str, *, source_class: str = "company_owned", soup: BeautifulSoup | None = None) -> list[dict[str, Any]]:
    soup = soup or _parse_soup(html)
    if soup.find("rss") or soup.find("feed"):
        return parse_activity_feed(html, url, source_class=source_class)
    json_ld = _jsonld_from_html(soup)
    found = [item for node in _jsonld_values(json_ld) if (item := _activity_from_jsonld(node, url, source_class=source_class))]
    text = " ".join(soup.get_text(" ", strip=True).split())
    found.extend(_activity_from_visible(soup, url, text, source_class=source_class))
    return _dedupe_items(found, keys=("date", "url", "title"))


def parse_activity_feed(raw: str | bytes, url: str, *, source_class: str = "company_owned") -> list[dict[str, Any]]:
    soup = BeautifulSoup(raw, "xml")
    found: list[dict[str, Any]] = []
    for item in soup.find_all(["item", "entry"]):
        title = _clean_sentence(item.find("title").get_text(" ", strip=True) if item.find("title") else "")
        date_value = next((item.find(name).get_text(" ", strip=True) for name in ("pubDate", "published") if item.find(name)), "")
        parsed = parse_date(date_value)
        if not parsed:
            continue
        link_node = item.find("link")
        link = str(link_node.get("href") if link_node and link_node.get("href") else link_node.get_text(" ", strip=True) if link_node else url)
        link = urllib.parse.urljoin(url, link)
        if not _same_site(url, link):
            continue
        description = item.find("description") or item.find("summary")
        activity = _activity_item(title, parsed, link, description.get_text(" ", strip=True) if description else title, source_class=source_class)
        if activity:
            found.append(activity)
    return _dedupe_items(found, keys=("date", "url", "title"))


def parse_sitemap_activity(raw: str | bytes, url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(raw, "xml")
    found: list[dict[str, Any]] = []
    for node in soup.find_all("url"):
        loc = node.find("loc")
        lastmod = node.find("lastmod")
        target = loc.get_text(" ", strip=True) if loc else ""
        parsed = parse_date(lastmod.get_text(" ", strip=True) if lastmod else "")
        if not target or not parsed or not _same_site(url, target) or not _path_has_term(target, ACTIVITY_TERMS) or not _activity_url_allowed(target):
            continue
        title = _clean_sentence(urllib.parse.unquote(urllib.parse.urlparse(target).path.rstrip("/").rsplit("/", 1)[-1]).replace("-", " ")) or "Company activity"
        activity = _activity_item(title, parsed, target, f"sitemap lastmod {parsed}", source_class="company_owned")
        if activity:
            found.append(activity)
    return _dedupe_items(found, keys=("date", "url", "title"))


def _job_from_jsonld(node: dict[str, Any], page_url: str) -> dict[str, Any] | None:
    kinds = node.get("@type") or node.get("type") or []
    kinds = {str(item).casefold() for item in (kinds if isinstance(kinds, list) else [kinds])}
    if "jobposting" not in kinds:
        return None
    url = urllib.parse.urljoin(page_url, str(node.get("url") or page_url))
    if not _job_url_allowed(page_url, url):
        return None
    location = node.get("jobLocation") or node.get("job_location") or ""
    if isinstance(location, list):
        location = ", ".join(_location_text(item) for item in location)
    else:
        location = _location_text(location)
    title = _clean_job_title(node.get("title") or node.get("name"))
    if not title:
        return None
    return {"title": title, "location": location, "posted": parse_date(node.get("datePosted")), "closing": parse_date(node.get("validThrough")), "url": url, "claim_span": _claim_span(title, location, node.get("description")), "source_class": "company_owned"}


def _location_text(value: Any) -> str:
    if isinstance(value, dict):
        address = value.get("address") or value
        if isinstance(address, dict):
            return ", ".join(str(address.get(key) or "").strip() for key in ("streetAddress", "addressLocality", "addressRegion") if str(address.get(key) or "").strip())
    return " ".join(str(value or "").split())


def parse_jobs_html(html: str | bytes, url: str, *, soup: BeautifulSoup | None = None) -> tuple[list[dict[str, Any]], bool]:
    soup = soup or _parse_soup(html)
    json_ld = _jsonld_from_html(soup)
    jobs = [item for node in _jsonld_values(json_ld) if (item := _job_from_jsonld(node, url))]
    for anchor in soup.select("a[href]")[:300]:
        target = urllib.parse.urljoin(url, str(anchor.get("href") or ""))
        if not _is_recognized_ats_url(target):
            continue
        title = _clean_job_title(anchor.get_text(" ", strip=True))
        if title and not _is_navigation_job_title(title) and _job_title_is_ats_posting(title) and not _job_path_is_editorial(target):
            jobs.append({"title": title, "location": "", "posted": None, "closing": None, "url": target, "claim_span": _claim_span(title, ""), "source_class": "company_owned"})
    for node in soup.select("article, li, [class*='job'], [class*='stilling'], [class*='career']")[:300]:
        if node.find_parent(["nav", "header", "footer"]):
            continue
        text = " ".join(node.get_text(" ", strip=True).split())
        if not text or NO_OPEN_ROLES.search(text):
            continue
        heading = node.select_one("h1, h2, h3, h4") or node.select_one("a[href]")
        title = _clean_job_title(heading.get_text(" ", strip=True) if heading else text)
        if not title or len(title) < 3 or not _has_job_title_cue(title) or not _job_title_is_visible_posting(title) or _is_navigation_job_title(title):
            continue
        link = node.select_one("a[href]")
        href = link.get("href") if link else None
        target = urllib.parse.urljoin(url, str(href or url))
        if not _job_url_allowed(url, target) or _job_path_is_editorial(target):
            continue
        jobs.append({"title": title, "location": "", "posted": parse_date(text), "closing": None, "url": target, "claim_span": _claim_span(title, text), "source_class": "company_owned"})
    selectors = soup.select("article, li, [class*='job'], [class*='stilling'], [class*='career']")
    return _dedupe_jobs(jobs), bool(json_ld or selectors or _job_page_is_career_url(url))


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


def _mark_old(items: list[dict[str, Any]], retrieved_at: Any = None, *, as_of: Any = None) -> list[dict[str, Any]]:
    reference = as_of if as_of is not None else datetime.now(timezone.utc).date()
    cutoff = _retrieved_date(reference) - timedelta(days=730)
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


_BRIEF_NAV_WORDS = {
    "about", "blog", "close", "contact", "cookies", "content", "copyright", "home",
    "hjem", "inn", "login", "logg", "menu", "meny", "newsletter", "open", "oss",
    "personvern", "privacy", "search", "skip", "søk", "top", "om", "kontakt", "powered", "by", "nyhetsbrev",
}
_BRIEF_TEMPLATE_RE = re.compile(
    r"(?:domain\s+is\s+(?:parked|for\s+sale)|is\s+parked|parked\b|under\s+construction|coming\s+soon|"
    r"just\s+another\s+wordpress|default\s+wordpress|no\s+active\s+website|currently\s+does\s+not\s+have\s+an\s+active\s+website|"
    r"her\s+kommer\s+en\s+ny\s+side|viser\s+vi\s+til\s+v[aå]r\s+facebook|contactinfo)",
    re.I,
)
_BRIEF_CTA_RE = re.compile(r"^\s*(?:slik\b|kontakt\s+oss\b|contact\s+us\b|les\s+mer\b|read\s+more\b|learn\s+more\b|bestill\b|klikk\b)", re.I)
_BRIEF_PHONE_RE = re.compile(r"(?<!\d)(?:\+?47[\s.-]*)?(?:\d[\s.-]*){8,}(?!\d)")
_BRIEF_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_BRIEF_ADDRESS_RE = re.compile(
    r"\b(?:[A-ZÆØÅ][\wÆØÅæøå-]*\s+){0,3}(?:gata|gate|veien|vei|storgata|street|road)\s+\d{1,4}\b",
    re.I,
)
_BRIEF_POSTCODE_RE = re.compile(r"\b\d{4}\s+[A-ZÆØÅ][\wÆØÅæøå-]*\b")
_BRIEF_SEMANTIC_GROUPS = (
    {"production", "produksjon", "produserer", "produce", "manufacturing", "manufacture", "maskin", "machines"},
    {"analysis", "analyse", "analyser", "overvaking", "miljo", "environment", "sediment"},
    {"consulting", "consulting", "konsulent", "strategy", "management", "transformation"},
    {"radiator", "motorvogn", "vehicle", "repair", "utskifting"},
    {"instrument", "instrumenter", "measurement", "måle", "counting", "systems"},
    {"hotel", "hotell", "badehotell", "restaurant", "spa"},
    {"property", "eiendom", "eiendomsforvaltning", "boliger", "skog", "forvalter", "leier"},
    {"software", "programmering", "technology", "webrtc", "ai", "digital", "systems"},
    {"training", "trening", "treningssentre", "fitness"},
    {"power", "kraft", "vannkraft", "electricity", "strøm"},
    {"pipe", "rør", "ror", "vvs", "rørlegger", "rorlegger", "rørlegging", "rorlegging"},
    {"print", "reklame", "reklametrykk", "trykk", "trykksaker", "folie", "folier", "skilt", "grafisk", "printing"},
    {"building", "bygg", "byggmester", "entreprenør", "entreprenor", "tømrer", "tomrer", "oppføring", "oppforing", "bygning", "bygninger"},
    {"creative", "artist", "speaker", "musikk", "music", "utøvende", "utovende", "kunstner", "kunstnerisk"},
    {"accounting", "accounting", "accountancy", "regnskap", "revisjon", "bookkeeping", "bokføring", "bokforing"},
    {"seafood", "fisk", "fish", "seafood", "sjømat", "sjomat", "mat", "food", "products"},
    {"hair", "frisør", "frisor", "frisørvirksomhet", "beauty", "skjønnhet", "skjonnhet", "salon", "salong"},
)

_BRIEF_RESIDUAL_INDUSTRY_TERMS = {
    "annen", "uoppgitt", "ikke", "oppgitt", "not", "reported", "holding", "management",
    "hovedkontor", "konsern",
}


def _brief_clean_candidate(value: Any) -> str:
    soup = BeautifulSoup(str(value or ""), "lxml")
    for node in soup.select("script, style, noscript"):
        node.decompose()
    text = unescape(soup.get_text(" ", strip=True)).replace("\ufffd", "").replace("�", "")
    text = unicodedata.normalize("NFKC", text)
    text = " ".join(text.split()).strip()
    if not text:
        return ""
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip()]
    text = " ".join(sentences[:2]) if sentences else text
    if len(text) > 280:
        boundary = re.search(r"[.!?](?:\s|$)", text[:281])
        if boundary is None:
            return ""
        text = text[:boundary.end()].strip()
    return text[:280].strip()


def _brief_candidate_is_clean(text: str) -> bool:
    words = re.findall(r"[\wÀ-ÿÆØÅæøå'-]+", text, re.UNICODE)
    if len(words) < 8 or len(text) < 50:
        return False
    if _BRIEF_TEMPLATE_RE.search(text) or _BRIEF_PHONE_RE.search(text) or _BRIEF_EMAIL_RE.search(text) or _BRIEF_ADDRESS_RE.search(text) or _BRIEF_POSTCODE_RE.search(text):
        return False
    if _BRIEF_CTA_RE.search(text):
        return False
    letters = [char for char in text if char.isalpha()]
    if len(letters) >= 8 and sum(char.isupper() for char in letters) / len(letters) > 0.88:
        return False
    tokens = [token.casefold() for token in re.findall(r"[\wÀ-ÿÆØÅæøå'-]+", text, re.UNICODE)]
    if tokens and sum(token in _BRIEF_NAV_WORDS for token in tokens) / len(tokens) > 0.25:
        return False
    normalized_sentences = [re.sub(r"\W+", " ", sentence.casefold()).strip() for sentence in re.split(r"(?<=[.!?])\s+", text)]
    if len(normalized_sentences) != len(set(normalized_sentences)):
        return False
    return True


def _brief_page_metadata(page: dict[str, Any], *, base_url: str) -> dict[str, Any]:
    if page.get("json_ld") or page.get("meta_description") or page.get("first_paragraph") or page.get("largest_text_block"):
        return page
    raw = page.get("html") or page.get("raw")
    if not raw:
        return page
    soup = page.get("_soup") if isinstance(page.get("_soup"), BeautifulSoup) else _parse_soup(raw)
    metadata = parse_html_metadata(raw, str(page.get("url") or base_url), soup=soup)
    return {**page, **metadata, "_soup": soup}


def _brief_page_order(pages: list[dict[str, Any]], *, base_url: str) -> list[dict[str, Any]]:
    def priority(page: dict[str, Any]) -> tuple[int, str]:
        path = urllib.parse.urlparse(str(page.get("url") or base_url)).path.rstrip("/").casefold()
        if path in {"", "/"}:
            return (0, str(page.get("url") or ""))
        if path.rsplit("/", 1)[-1] in {"kontakt", "kontakt-oss", "om-oss", "about", "about-us"}:
            return (2, path)
        return (1, path)
    return sorted(pages, key=priority)


def _brief_semantic_tokens(value: Any) -> set[str]:
    tokens = _stem_tokens(value)
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold()
    for group in _BRIEF_SEMANTIC_GROUPS:
        if any(term in normalized for term in group):
            tokens.add(f"group:{next(iter(sorted(group)))}")
    return tokens


def _brief_is_residual_industry(industry: str) -> bool:
    """Return true for registry labels that are too generic to contradict a site."""
    normalized = unicodedata.normalize("NFKD", str(industry or "")).encode("ascii", "ignore").decode().casefold()
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    return not normalized.strip() or bool(tokens & _BRIEF_RESIDUAL_INDUSTRY_TERMS)


def _brief_shared_stem(industry: str, text: str) -> bool:
    industry_tokens = {token for token in _stem_tokens(industry) if len(token) >= 5}
    text_tokens = {token for token in _stem_tokens(text) if len(token) >= 5}
    return any(left[:5] == right[:5] for left in industry_tokens for right in text_tokens)


def _brief_industry_matches(industry: str, text: str) -> bool:
    if _brief_is_residual_industry(industry):
        return True
    industry_tokens = _brief_semantic_tokens(industry)
    text_tokens = _brief_semantic_tokens(text)
    group_tokens = {token for token in industry_tokens if token.startswith("group:")}
    text_group_tokens = {token for token in text_tokens if token.startswith("group:")}
    return bool(group_tokens & text_group_tokens) or _brief_shared_stem(industry, text)


def _brief_is_clearly_different(industry: str, text: str) -> bool:
    """Be conservative: only known, disjoint industry families can be ambiguous."""
    if _brief_is_residual_industry(industry) or _brief_industry_matches(industry, text):
        return False
    industry_groups = {token for token in _brief_semantic_tokens(industry) if token.startswith("group:")}
    text_groups = {token for token in _brief_semantic_tokens(text) if token.startswith("group:")}
    return bool(industry_groups and text_groups and industry_groups.isdisjoint(text_groups))


def _brief_candidates(website: dict[str, Any], *, base_url: str) -> list[dict[str, str]]:
    value = website.get("value") or {}
    pages = _brief_page_order([_brief_page_metadata(page, base_url=base_url) for page in _site_pages(website)], base_url=base_url)
    if isinstance(value, dict) and value and value not in pages:
        pages.append(_brief_page_metadata(value, base_url=base_url))
    pages = _brief_page_order(pages, base_url=base_url)
    candidates: list[dict[str, str]] = []

    def add(text: Any, kind: str, page: dict[str, Any]) -> None:
        cleaned = _brief_clean_candidate(text)
        if cleaned and _brief_candidate_is_clean(cleaned):
            candidates.append({"text": cleaned, "kind": kind, "source_url": str(page.get("url") or base_url)})

    for page in pages:
        json_ld = page.get("json_ld") or page.get("structured_json_ld") or page.get("structured_data") or []
        for node in _jsonld_values(json_ld):
            kinds = node.get("@type") or node.get("type") or []
            kinds = {str(item).casefold().rsplit("#", 1)[-1] for item in (kinds if isinstance(kinds, list) else [kinds])}
            if kinds & {"organization", "localbusiness", "website"}:
                add(node.get("description"), "jsonld_description", page)
    for page in pages:
        path = urllib.parse.urlparse(str(page.get("url") or base_url)).path.rstrip("/").casefold()
        if path in {"", "/"}:
            og = page.get("og_description")
            if not og and isinstance(page.get("opengraph"), dict):
                og = page["opengraph"].get("og:description") or page["opengraph"].get("description")
            add(og, "og_description", page)
    for page in pages:
        meta = page.get("meta_description")
        if not meta and not page.get("og_description"):
            meta = page.get("description")
        add(meta, "meta_description", page)
    for page in pages:
        og = page.get("og_description")
        if not og and isinstance(page.get("opengraph"), dict):
            og = page["opengraph"].get("og:description") or page["opengraph"].get("description")
        add(og, "og_description", page)
    for page in pages:
        add(page.get("first_paragraph") or page.get("lead"), "first_paragraph", page)
        add(page.get("largest_text_block"), "largest_text_block", page)
    seen: set[str] = set()
    result = []
    for candidate in candidates:
        if candidate["text"] in seen:
            continue
        seen.add(candidate["text"])
        result.append(candidate)
    return result


def _brief_has_clear_contradiction(website: dict[str, Any], industry: str, *, base_url: str) -> bool:
    """Detect a substantive but unusable site description that contradicts the registry."""
    if _brief_is_residual_industry(industry):
        return False
    value = website.get("value") or {}
    pages = _brief_page_order([_brief_page_metadata(page, base_url=base_url) for page in _site_pages(website)], base_url=base_url)
    if isinstance(value, dict) and value and value not in pages:
        pages.append(_brief_page_metadata(value, base_url=base_url))
    for page in pages:
        raw_values = [page.get(key) for key in ("meta_description", "og_description", "description", "first_paragraph", "largest_text_block", "lead")]
        for raw in raw_values:
            text = _brief_clean_candidate(raw)
            if len(text) < 30 or len(re.findall(r"[\wÀ-ÿÆØÅæøå'-]+", text, re.UNICODE)) < 5:
                continue
            if _BRIEF_TEMPLATE_RE.search(text) or _BRIEF_CTA_RE.search(text) or _BRIEF_PHONE_RE.search(text) or _BRIEF_EMAIL_RE.search(text) or _BRIEF_ADDRESS_RE.search(text) or _BRIEF_POSTCODE_RE.search(text):
                continue
            tokens = [token.casefold() for token in re.findall(r"[\wÀ-ÿÆØÅæøå'-]+", text, re.UNICODE)]
            if tokens and sum(token in _BRIEF_NAV_WORDS for token in tokens) / len(tokens) > 0.25:
                continue
            if _brief_is_clearly_different(industry, text):
                return True
    return False


def extract_company_brief(profile: dict[str, Any], website: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    name, municipality, industry = _registry_fields(profile)
    registry_text = f"Registrert i {municipality or 'ukjent kommune'}."
    if industry and industry.casefold() not in {"uoppgitt", "ikke oppgitt", "not reported"}:
        registry_text = f"{registry_text} Bransje: {industry}."
    parts = [{"text": registry_text, "source_class": "official_registry", "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv", "claim_span": registry_text, "kind": "registry"}]
    site_value = (website or {}).get("value") or {}
    site_url = str(site_value.get("final_url") or (website or {}).get("source_url") or "")
    candidates = _brief_candidates(website or {}, base_url=site_url)
    if candidates and site_url and (website or {}).get("status") == "available":
        item = candidates[0]
        parts.insert(0, {"text": item["text"], "source_class": "company_owned", "source_url": item["source_url"] or site_url, "claim_span": item["text"], "kind": item["kind"]})
    else:
        contradiction = bool(site_url and (website or {}).get("status") == "available" and _brief_has_clear_contradiction(website or {}, industry, base_url=site_url))
        if contradiction:
            return "ambiguous", {"summary": registry_text, "parts": parts, "registry_derived": True, "checked": True, "site_disagrees_with_registry": True, "source_url": site_url}
        return "available", {"summary": registry_text, "parts": parts, "registry_derived": True, "source_url": parts[0]["source_url"], "checked": True}
    site_text = parts[0]["text"]
    ambiguous = _brief_is_clearly_different(industry, site_text)
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
    career_url = ""
    career_parseable = False
    no_open_roles = False
    parse_errors: list[str] = []
    phase_timings_ms = {"activity": 0.0, "hiring": 0.0, "brief": 0.0, "parse": 0.0, "dns": 0.0, "fetch": 0.0}
    base_url = str(value.get("final_url") or website.get("source_url") or "")
    for page in documents:
        page_url = str(page.get("url") or base_url)
        json_ld, text, opengraph, links = _page_payload(page, base_url=page_url or base_url)
        raw = page.get("html") or page.get("raw")
        soup = page.get("_soup") if isinstance(page.get("_soup"), BeautifulSoup) else None
        if raw and soup is None:
            parse_started = time.perf_counter()
            soup = _parse_soup(raw)
            phase_timings_ms["parse"] += (time.perf_counter() - parse_started) * 1000
        for node in _jsonld_values(json_ld):
            activity_started = time.perf_counter()
            item = _activity_from_jsonld(node, page_url)
            if item:
                activity.append(item)
            phase_timings_ms["activity"] += (time.perf_counter() - activity_started) * 1000
            hiring_started = time.perf_counter()
            job = _job_from_jsonld(node, page_url)
            if job:
                site_jobs.append(job)
                career_found = True
                career_url = career_url or page_url
            phase_timings_ms["hiring"] += (time.perf_counter() - hiring_started) * 1000
        activity_started = time.perf_counter()
        if raw:
            activity.extend(parse_activity_html(raw, page_url, soup=soup))
        phase_timings_ms["activity"] += (time.perf_counter() - activity_started) * 1000
        hiring_started = time.perf_counter()
        if raw:
            jobs, parseable = parse_jobs_html(raw, page_url, soup=soup)
            site_jobs.extend(jobs)
            if jobs:
                career_found = True
                career_url = career_url or page_url
            career_parseable = career_parseable or parseable
            if NO_OPEN_ROLES.search(str(raw)):
                no_open_roles = True
        phase_timings_ms["hiring"] += (time.perf_counter() - hiring_started) * 1000
        for link in links:
            target = link.get("url") or ""
            if _path_has_term(target, CAREER_TERMS) or any(term in str(link.get("text") or "").casefold() for term in CAREER_TERMS):
                career_found = True
                career_url = career_url or target
        if _path_has_term(page_url, CAREER_TERMS):
            career_found = True
            career_url = career_url or page_url
            if NO_OPEN_ROLES.search(text):
                no_open_roles = True
    for document in extra_documents or []:
        if document.get("kind") == "feed":
            activity.extend(parse_activity_feed(document.get("raw") or document.get("html") or "", str(document.get("url") or base_url)))
        elif document.get("kind") == "sitemap":
            activity.extend(parse_sitemap_activity(document.get("raw") or document.get("html") or "", str(document.get("url") or base_url)))
        if _path_has_term(str(document.get("url") or ""), CAREER_TERMS):
            career_found = True
            career_url = career_url or str(document.get("url") or "")
    site_jobs = _dedupe_jobs(site_jobs)
    job_urls = {str(item.get("url") or "") for item in site_jobs}
    job_titles = {_normalized_job_title(item.get("title")) for item in site_jobs}
    activity = [
        item for item in activity
        if str(item.get("url") or "") not in job_urls
        and _normalized_job_title(item.get("title")) not in job_titles
    ]
    crawl_date = _retrieved_date(retrieved_at)
    activity = [item for item in activity if str(item.get("date") or "") != crawl_date.isoformat()]
    activity = _dedupe_items(activity, keys=("date", "url", "title"))
    activity = sorted(activity, key=lambda item: (str(item.get("date") or ""), str(item.get("url") or "")), reverse=True)
    activity = _mark_old(activity, as_of=datetime.now(timezone.utc).date())[:10]
    nav_evidence = (profile.get("evidence") or {}).get("nav_jobs") or {}
    # A complete NAV index that was consulted for this organisation is a real check, even
    # when the company has no verified website to crawl.
    nav_checked = nav_evidence.get("status") in {"available", "not_found"}
    recent_activity = [item for item in activity if not item.get("old")]
    activity_status = "available" if recent_activity else ("blocked" if website.get("status") == "blocked" else "not_available" if (website_available or nav_checked) else "not_checked")
    activity_note = "Dated company-site and NAV activity; dates are parsed from source content"
    if activity and not recent_activity:
        activity_note = "No public activity in the last 24 months; older dated items are retained"
    if parse_errors and not activity:
        activity_status = "failed"
    brief_started = time.perf_counter()
    brief_status, brief_value = extract_company_brief(profile, website)
    phase_timings_ms["brief"] += (time.perf_counter() - brief_started) * 1000
    nav_jobs = _nav_postings(profile)
    merged_jobs, agreements = _merge_postings(site_jobs, nav_jobs)
    if career_found:
        hiring_status = "available" if site_jobs else "not_available"
    else:
        hiring_status = "available" if nav_jobs else "not_available" if (website_available or nav_checked) else "not_checked"
    hiring_value = {
        "postings": merged_jobs,
        "company_site_postings": site_jobs,
        "nav_ads": nav_jobs,
        "careers_page_found": career_found,
        "no_open_roles": no_open_roles,
        "checked_url": career_url or (base_url if site_jobs else ""),
        "checked": bool(website_available or (profile.get("evidence") or {}).get("nav_jobs")),
        "sources_agree": agreements,
    }
    digest = hashlib.sha256(json.dumps({"activity": activity, "brief": brief_value, "hiring": hiring_value}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    source_url = base_url or str((profile.get("evidence") or {}).get("registry", {}).get("source_url") or "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv")
    return {
        "public_activity": evidence("public_activity", activity_status, "company_owned" if website_available else "nav_feed", source_url, value={"items": activity, "checked": bool(website_available), "max_items": 10}, note=activity_note, retrieved_at=retrieved_at, content_sha256=digest),
        "company_brief": evidence("company_brief", brief_status, "company_owned" if not brief_value.get("registry_derived") else "official_registry", source_url, value=brief_value, note="Deterministic registry and verified-site description parts", retrieved_at=retrieved_at, content_sha256=digest),
        "hiring": evidence("hiring", hiring_status, "company_owned" if (site_jobs or career_found) else "nav_feed", career_url or (source_url if site_jobs else "https://arbeidsplassen.nav.no/stillinger"), value=hiring_value, note="checked: careers page found, no postings" if career_found and not site_jobs else "Company-owned careers postings merged with exact-organisation NAV ads", retrieved_at=retrieved_at, content_sha256=digest),
        "metrics": {"requests": 0, "bytes": 0, "latencies_ms": [], "activity_items": len(activity), "company_site_jobs": len(site_jobs), "merged_jobs": len(merged_jobs), "career_pages": int(career_found), "phase_timings_ms": {key: round(value, 3) for key, value in phase_timings_ms.items()}},
    }


def _fetch_fact_document(url: str, *, base_url: str, timeout: float, request_policy: Any = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Fetch one same-domain document with the existing SSRF/robots-safe opener."""
    started = time.monotonic()
    timeout = min(6.0, max(0.1, float(timeout)))
    phase_timings_ms = {"dns": 0.0, "fetch": 0.0, "parse": 0.0, "activity": 0.0, "hiring": 0.0, "brief": 0.0}
    if not _same_site(base_url, url):
        return None, {"requests": 0, "bytes": 0, "latencies_ms": [], "error": "cross_domain", "phase_timings_ms": phase_timings_ms}
    try:
        dns_started = time.perf_counter()
        safe_web.assert_public_url(url)
        host = request_policy.host_key(url) if request_policy else None
        robots_cached = bool(request_policy and host in getattr(request_policy, "_robots", {}))
        allowed = safe_web._robots_allowed(url, timeout, request_policy)
        phase_timings_ms["dns"] = round((time.perf_counter() - dns_started) * 1000, 3)
        if not allowed:
            return None, {"requests": 1, "bytes": 0, "latencies_ms": [0], "blocked": True, "url": url, "phase_timings_ms": phase_timings_ms}
        request = safe_web.urllib.request.Request(url, headers={"User-Agent": safe_web.USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml,text/xml,application/rss+xml,application/atom+xml"})
        fetch_started = time.perf_counter()
        with safe_web._open_response(request, timeout, request_policy) as response:
            raw = safe_web._read_response(response, 1_000_001, timeout)
            final_url = response.geturl()
            content_type = response.headers.get("content-type", "")
        phase_timings_ms["fetch"] = round((time.perf_counter() - fetch_started) * 1000, 3)
        if len(raw) > 1_000_000 or not _same_site(base_url, final_url):
            return None, {"requests": 1, "bytes": len(raw), "latencies_ms": [int((time.monotonic() - started) * 1000)], "error": "oversized_or_cross_domain", "url": url, "phase_timings_ms": phase_timings_ms}
        kind = "sitemap" if "sitemap" in url.casefold() or raw.lstrip().startswith(b"<urlset") or raw.lstrip().startswith(b"<sitemapindex") else "feed" if "rss" in content_type.casefold() or "atom" in content_type.casefold() or raw.lstrip().startswith((b"<rss", b"<feed")) else "html"
        html = raw.decode("utf-8", errors="replace")
        page: dict[str, Any] = {"url": final_url, "content_sha256": hashlib.sha256(raw).hexdigest(), "kind": kind, "raw": raw if kind != "html" else None}
        if kind == "html":
            parse_started = time.perf_counter()
            soup = BeautifulSoup(html, "lxml")
            page.update(parse_html_metadata(html, final_url, soup=soup))
            page["html"] = html
            page["_soup"] = soup
            phase_timings_ms["parse"] = round((time.perf_counter() - parse_started) * 1000, 3)
        return page, {"requests": 1 if robots_cached else 2, "bytes": len(raw), "latencies_ms": [int((time.monotonic() - started) * 1000)], "url": final_url, "phase_timings_ms": phase_timings_ms}
    except safe_web.PublicURLPolicyError as exc:
        return None, {"requests": 0, "bytes": 0, "latencies_ms": [], "blocked": True, "error": str(exc), "url": url, "phase_timings_ms": phase_timings_ms}
    except Exception as exc:  # noqa: BLE001 - source failure is contained in the module
        return None, {"requests": 1, "bytes": 0, "latencies_ms": [int((time.monotonic() - started) * 1000)], "error": f"{type(exc).__name__}: {exc}", "url": url, "phase_timings_ms": phase_timings_ms}


def parse_html_metadata(html: str, url: str, *, soup: BeautifulSoup | None = None) -> dict[str, Any]:
    soup = soup or _parse_soup(html)
    json_ld = _jsonld_from_html(soup)
    meta_description = soup.select_one('meta[name="description"]')
    og_description = soup.select_one('meta[property="og:description"]')
    h1 = soup.find("h1")
    paragraph_nodes = soup.select("main p, article p") or soup.find_all("p")
    paragraph = next((node for node in paragraph_nodes if node.get_text(" ", strip=True)), None)
    block_nodes = soup.select("main p, main div, article p, article div") or soup.find_all(["p", "div"])
    largest = max(block_nodes, key=lambda node: len(node.get_text(" ", strip=True)), default=None)
    links = [{"url": urllib.parse.urljoin(url, str(anchor.get("href") or "")), "text": " ".join(anchor.get_text(" ", strip=True).split())} for anchor in soup.select("a[href]")[:200]]
    alternate = []
    for link in soup.select('link[rel~="alternate"][href]'):
        link_type = str(link.get("type") or "").casefold()
        if "rss" in link_type or "atom" in link_type or "xml" in link_type or "feed" in str(link.get("href") or "").casefold():
            alternate.append(urllib.parse.urljoin(url, str(link.get("href"))))
    return {
        "title": soup.title.get_text(" ", strip=True)[:500] if soup.title else "",
        "description": str((meta_description or og_description).get("content") or "").strip()[:2000] if (meta_description or og_description) else "",
        "meta_description": str(meta_description.get("content") or "").strip()[:2000] if meta_description else "",
        "og_description": str(og_description.get("content") or "").strip()[:2000] if og_description else "",
        "h1": _clean_sentence(h1.get_text(" ", strip=True)) if h1 else "",
        "lead": _clean_sentence(paragraph.get_text(" ", strip=True)) if paragraph else "",
        "first_paragraph": _clean_sentence(paragraph.get_text(" ", strip=True), limit=500) if paragraph else "",
        "largest_text_block": _clean_sentence(largest.get_text(" ", strip=True), limit=500) if largest else "",
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
    timeout: float = 6.0,
    max_extra_requests: int = 4,
    total_budget_seconds: float = 12.0,
    deadline: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use captured pages first, then fetch at most four safe same-domain documents."""
    website = (profile.get("evidence") or {}).get("website") or {}
    if website.get("status") != "available":
        facts = extract_site_facts(profile)
        return facts, facts["metrics"]
    value = website.get("value") or {}
    base_url = str(value.get("final_url") or website.get("source_url") or "")
    now = time.monotonic()
    local_deadline = now + max(0.0, float(total_budget_seconds))
    if deadline is not None:
        local_deadline = min(local_deadline, deadline)
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
    metrics = {"requests": 0, "bytes": 0, "latencies_ms": [], "extra_requests": 0, "extra_errors": [], "phase_timings_ms": {"activity": 0.0, "hiring": 0.0, "brief": 0.0, "parse": 0.0, "dns": 0.0, "fetch": 0.0}}
    for target, kind in candidates:
        if metrics["extra_requests"] >= max_extra_requests or target in seen or time.monotonic() >= local_deadline:
            continue
        if not _same_site(base_url, target):
            continue
        seen.add(target)
        remaining = local_deadline - time.monotonic()
        if remaining <= 0:
            break
        request_timeout = min(6.0, max(0.1, float(timeout)), remaining)
        document, operation = _fetch_fact_document(target, base_url=base_url, timeout=request_timeout, request_policy=request_policy)
        metrics["requests"] += int(operation.get("requests") or 0)
        metrics["bytes"] += int(operation.get("bytes") or 0)
        metrics["latencies_ms"].extend(operation.get("latencies_ms") or [])
        metrics["extra_requests"] += 1
        for phase, elapsed in (operation.get("phase_timings_ms") or {}).items():
            metrics["phase_timings_ms"][phase] = metrics["phase_timings_ms"].get(phase, 0.0) + float(elapsed or 0.0)
        if document:
            document["kind"] = kind if document.get("kind") == "html" else document.get("kind")
            extras.append(document)
        elif operation.get("error") or operation.get("blocked"):
            metrics["extra_errors"].append({"url": target, "kind": kind, "error": operation.get("error") or "robots_blocked", "blocked": bool(operation.get("blocked"))})
    facts = extract_site_facts(profile, extra_documents=extras)
    extraction_phases = facts["metrics"].get("phase_timings_ms") or {}
    for phase, elapsed in extraction_phases.items():
        metrics["phase_timings_ms"][phase] = round(metrics["phase_timings_ms"].get(phase, 0.0) + float(elapsed or 0.0), 3)
    facts["metrics"].update(metrics)
    facts["metrics"]["phase_timings_ms"] = metrics["phase_timings_ms"]
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
    current_phases = metrics.setdefault("phase_timings_ms", {})
    for phase, elapsed in (site_metrics.get("phase_timings_ms") or {}).items():
        current_phases[phase] = round(float(current_phases.get(phase) or 0.0) + float(elapsed or 0.0), 3)
    metrics["requests"] = int(metrics.get("requests") or 0) + metrics["site_fact_requests"]
    metrics["bytes"] = int(metrics.get("bytes") or 0) + metrics["site_fact_bytes"]
    metrics["latencies_ms"] = list(metrics.get("latencies_ms") or []) + list(site_metrics.get("latencies_ms") or [])
    return profile


__all__ = [
    "ACTIVITY_TERMS", "CAREER_TERMS", "attach_site_facts", "enrich_site_facts",
    "extract_company_brief", "extract_site_facts", "parse_activity_feed",
    "parse_activity_html", "parse_date", "parse_jobs_html", "parse_sitemap_activity",
]
