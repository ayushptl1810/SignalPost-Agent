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
from zoneinfo import ZoneInfo

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
CAREER_HEADING_TERMS = (
    "karriere", "career", "jobb", "jobs", "job", "stilling", "stillinger", "vacancy", "vacancies",
    "hiring", "open position", "open positions", "join us", "join our team", "work with us", "ledige stillinger",
)
ACTIVITY_TERMS = (
    "news", "nyheter", "nyhet", "aktuelt", "blog", "press", "presse",
    "pressemelding", "pressemeldinger", "artikkel", "blogg", "investor", "om-oss/nyheter", "om_oss/nyheter",
)
NEWS_SEGMENTS = {
    "aktuelt", "nyheter", "nyhet", "nyheiter", "news", "blogg", "blog", "presse",
    "pressemeldinger", "siste-nytt", "artikler", "artikkel", "nyhetsarkiv", "innsikt",
}
NEWS_SKIP_SEGMENTS = {"side", "page", "kategori", "category", "tag", "author", "feed", "arkiv"}
ACTIVITY_BLOCKED_SLUGS = {
    "about", "about-us", "om-oss", "kontakt", "kontakt-oss", "personvern", "privacy",
    "hei-verden", "hello-world", "cookie", "cookies", "vilkar", "terms", "contact",
    # Catalogue pages in site-wide feeds are not news (S29 live: Ischebeck /produkter/...).
    "produkter", "produkt", "products", "product", "tjenester", "services", "shop", "butikk", "nettbutikk",
}
ACTIVITY_NAV_WORDS = {
    "about", "contact", "home", "hjem", "kontakt", "menu", "meny", "om", "oss", "search", "søk",
}
ACTIVITY_LISTING_SLUGS = {"news", "nyheter", "blog", "blogg", "aktuelt", "press", "presse", "artikkel", "pressemelding"}
ACTIVITY_PLACEHOLDER_TITLES = {"company activity", "news archive", "nyhetsarkiv"}
ABOUT_TERMS = ("om-oss", "om_oss", "om", "about", "selskapet", "bedriften", "hvem-er-vi", "hvem_er_vi")
NO_OPEN_ROLES = re.compile(
    r"(?:ingen|ingen\s+har)\s+(?:ledige\s+)?stillinger|ingen\s+ledige\s+stillinger|no\s+open\s+positions?|no\s+vacancies|we\s+are\s+not\s+hiring",
    re.I,
)


def _no_openings_span(value: str) -> str:
    text = " ".join(BeautifulSoup(value, "lxml").get_text(" ", strip=True).split())
    match = NO_OPEN_ROLES.search(text)
    return text[max(0, match.start() - 80):match.end() + 80].strip() if match else ""
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
JOB_GREETING_TITLE_RE = re.compile(
    r"^(?:god\s+jul|godt\s+nytt\s+år|godt\s+nytt\s+ar|julehilsen|sommerhilsen|påskehilsen|paskehilsen|hilsen)(?:\s+.+)?$",
    re.I,
)
JOB_LEADERSHIP_MESSAGE_RE = re.compile(r"\b(?:daglig\s+leder|styreleder|administrerende\s+direktør)\b", re.I)
JOB_VACANCY_CUE_RE = re.compile(r"\b(?:ledig|søker|søke|stilling(?:er)?|vacanc(?:y|ies)|hiring)\b", re.I)
JOB_EDITORIAL_PATH_TERMS = {"blog", "blogg", "news", "nyheter", "aktuelt", "artikkel"}
JOB_NAV_WORDS = {
    "about", "about us", "bil", "bli forhandler", "buy", "careers", "career", "company",
    "contact", "kontakt", "clinic", "klinikk", "du er her", "forhandler", "hjem", "home",
    "inn", "login", "logg inn", "menu", "meny", "norsk bokmål", "om oss", "oss", "products",
    "produkter", "services", "selskaper", "tenester", "tjenester", "våre selskaper", "våre tjenester",
}
RECOGNIZED_ATS_DOMAINS = {
    "recman.no", "webcruiter.com", "varbi.com", "teamtailor.com", "easycruit.com", "lever.co",
    "greenhouse.io", "greenhouse.com", "personio.com", "jobylon.com", "hr-manager.net", "reachmee.com", "reachmee.no",
}
DATE_RE = re.compile(
    r"(?P<day>\d{1,2})[./-]\s?(?P<month>\d{1,2})[./-]\s?(?P<year>\d{4})"
    r"|(?P<day_name>\d{1,2})\.\s*(?P<month_name>[A-Za-zæøåÆØÅ]+)\s+(?P<year_name>\d{4})",
    re.I,
)
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b")
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


def parse_datetime(value: Any) -> str | None:
    """Parse a source date while preserving an explicit time and offset.

    Date-only input intentionally remains date-only.  A naive timestamp is a
    Norwegian local time because the scored sources are Norwegian sites.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value.isoformat()
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            try:
                return date.fromisoformat(text).isoformat()
            except ValueError:
                return None
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            parsed = None
        if parsed is None:
            iso_text = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
            try:
                parsed = datetime.fromisoformat(iso_text)
            except ValueError:
                parsed_date = parse_date(text)
                return parsed_date
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Europe/Oslo"))
    return parsed.isoformat()


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


def _title_is_date_only(value: Any) -> bool:
    text = _clean_sentence(value)
    if not text:
        return False
    matches = [*ISO_DATE_RE.finditer(text), *DATE_RE.finditer(ISO_DATE_RE.sub("", text))]
    if not matches:
        return False
    remainder = ISO_DATE_RE.sub("", text)
    remainder = DATE_RE.sub("", remainder)
    remainder = re.sub(r"[\s,;:|()\[\]{}<>/\\–—-]+", "", remainder)
    return not remainder


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


def _job_title_is_greeting_or_leader_message(title: str) -> bool:
    normalized = _normalized_job_title(title)
    return normalized.endswith("fra daglig leder") or bool(JOB_GREETING_TITLE_RE.fullmatch(normalized))


def _job_title_is_non_vacancy_leadership_message(title: str) -> bool:
    normalized = _normalized_job_title(title)
    return bool(JOB_LEADERSHIP_MESSAGE_RE.search(normalized)) and not bool(JOB_VACANCY_CUE_RE.search(normalized))


def _job_title_is_visible_posting(title: str) -> bool:
    if _job_title_is_label_or_place(title) or _job_title_is_greeting_or_leader_message(title) or _job_title_is_non_vacancy_leadership_message(title):
        return False
    if len(_job_title_tokens(title)) > 12 or re.search(r"[.!?]\s*$", _clean_job_title(title)):
        return False
    if JOB_TITLE_NEGATION_RE.search(_normalized_job_title(title)):
        return False
    return bool(_job_title_tokens(title) & {word.casefold() for word in JOB_ROLE_WORDS})


def _job_title_is_ats_posting(title: str) -> bool:
    return (
        not _job_title_is_label_or_place(title)
        and not _job_title_is_greeting_or_leader_message(title)
        and not _job_title_is_non_vacancy_leadership_message(title)
        and not JOB_TITLE_NEGATION_RE.search(_normalized_job_title(title))
    )


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
            return bool(re.search(r"/(?:jobb|job)(?:/|$)", parsed.path.casefold()))
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
    if len(segments) == 1 and segments[0].startswith(("om-", "about-")):
        return False  # "Om RsV Gruppen AS" style about pages
    slug = segments[-1] if segments else ""
    return slug not in ACTIVITY_LISTING_SLUGS and slug not in ACTIVITY_PLACEHOLDER_TITLES


def _activity_is_nav_or_placeholder(title: str, claim_span: str) -> bool:
    if title.casefold().strip() in ACTIVITY_PLACEHOLDER_TITLES:
        return True
    tokens = [token.casefold() for token in re.findall(r"[\wÀ-ÿÆØÅæøå'-]+", claim_span, re.UNICODE)]
    return bool(tokens) and all(token in ACTIVITY_NAV_WORDS for token in tokens)


def _activity_fallback_title(soup: BeautifulSoup) -> str | None:
    for node in (soup.select_one("h1"), soup.title):
        candidate = _clean_sentence(node.get_text(" ", strip=True) if node else "")
        if (
            candidate
            and candidate.casefold().strip() not in ACTIVITY_LISTING_SLUGS
            and not _title_is_date_only(candidate)
            and not _activity_is_nav_or_placeholder(candidate, candidate)
        ):
            return candidate
    return None


def _activity_item(title: Any, parsed: str, url: str, claim: Any, *, source_class: str, extraction_method: str = "html_text_span", fallback_title: str | None = None, published_at: str | None = None) -> dict[str, Any] | None:
    clean_title = _clean_sentence(title, limit=120)
    if _title_is_date_only(clean_title):
        clean_title = _clean_sentence(fallback_title, limit=120)
    claim_span = _clean_claim_span(claim or clean_title)
    if not clean_title or _title_is_date_only(clean_title) or not parsed or not url or not _activity_url_allowed(url) or not claim_span:
        return None
    if _activity_is_nav_or_placeholder(clean_title, claim_span):
        return None
    published_at = published_at or parsed
    return {"title": clean_title, "date": parsed, "published_at": published_at, "effective_at": published_at, "url": url, "claim_span": claim_span, "source_class": source_class, "extraction_method": extraction_method}


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


def _activity_from_jsonld(node: dict[str, Any], page_url: str, *, source_class: str = "company_owned", fallback_title: str | None = None) -> dict[str, Any] | None:
    kinds = node.get("@type") or node.get("type") or []
    kinds = {str(item).casefold() for item in (kinds if isinstance(kinds, list) else [kinds])}
    if not kinds & {"newsarticle", "blogposting", "article"}:
        return None
    raw_published = node.get("datePublished") or node.get("dateCreated")
    published_at = parse_datetime(raw_published)
    parsed = parse_date(raw_published)
    if not parsed:
        return None
    raw_url = node.get("url") or node.get("mainEntityOfPage") or page_url
    if isinstance(raw_url, dict):
        raw_url = raw_url.get("@id") or raw_url.get("url")
    url = urllib.parse.urljoin(page_url, str(raw_url or page_url))
    if not _same_site(page_url, url):
        return None
    title = _clean_sentence(node.get("headline") or node.get("name") or node.get("title"))
    return _activity_item(title, parsed, url, node.get("description") or title, source_class=source_class, extraction_method="html_jsonld", fallback_title=fallback_title, published_at=published_at)


def _activity_from_visible(soup: BeautifulSoup, page_url: str, text: str, *, source_class: str = "company_owned", fallback_title: str | None = None) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    candidates = list(soup.select("article, li"))
    for node in candidates[:300]:
        node_text = " ".join(node.get_text(" ", strip=True).split())
        date_node = node.select_one("time[datetime]")
        raw_date = date_node.get("datetime") if date_node else ""
        published_at = parse_datetime(raw_date)
        parsed = parse_date(raw_date)
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
        item = _activity_item(title, parsed, url, claim, source_class=source_class, extraction_method="html_text_span", fallback_title=fallback_title, published_at=published_at)
        if item:
            found.append(item)
    return found


def _article_date_ladder(soup: BeautifulSoup, page_url: str) -> tuple[str | None, str | None, str]:
    """Return date, source timestamp and extraction method in the required order."""
    meta = soup.select_one('meta[property="article:published_time"], meta[name="article:published_time"]')
    if meta and meta.get("content"):
        raw = str(meta.get("content"))
        return parse_date(raw), parse_datetime(raw), "article_page:meta_published_time"
    for node in _jsonld_values(_jsonld_from_html(soup)):
        kinds = node.get("@type") or node.get("type") or [] if isinstance(node, dict) else []
        kinds = {str(item).casefold() for item in (kinds if isinstance(kinds, list) else [kinds])}
        if not kinds & {"article", "newsarticle", "blogposting"}:
            continue
        raw = node.get("datePublished") or node.get("dateCreated")
        if raw:
            return parse_date(raw), parse_datetime(raw), "article_page:jsonld_datePublished"
    for node in soup.select("article time[datetime], main time[datetime]"):
        raw = str(node.get("datetime") or "")
        if raw:
            return parse_date(raw), parse_datetime(raw), "article_page:time_datetime"
    prefix = " ".join(soup.get_text(" ", strip=True).split())[:1500]
    match = DATE_RE.search(prefix)
    if match:
        raw = match.group(0)
        return parse_date(raw), parse_datetime(raw), "article_page:norwegian_text_date"
    path_match = re.search(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)", urllib.parse.urlparse(page_url).path)
    if path_match:
        raw = f"{path_match.group(1)}-{int(path_match.group(2)):02d}-{int(path_match.group(3)):02d}"
        return parse_date(raw), parse_datetime(raw), "article_page:url_date"
    return None, None, ""


def parse_activity_html(html: str | bytes, url: str, *, source_class: str = "company_owned", soup: BeautifulSoup | None = None) -> list[dict[str, Any]]:
    soup = soup or _parse_soup(html)
    if soup.find("rss") or soup.find("feed"):
        return parse_activity_feed(html, url, source_class=source_class)
    fallback_title = _activity_fallback_title(soup)
    json_ld = _jsonld_from_html(soup)
    found = [item for node in _jsonld_values(json_ld) if (item := _activity_from_jsonld(node, url, source_class=source_class, fallback_title=fallback_title))]
    text = " ".join(soup.get_text(" ", strip=True).split())
    found.extend(_activity_from_visible(soup, url, text, source_class=source_class, fallback_title=fallback_title))
    ladder_date, ladder_published, ladder_method = _article_date_ladder(soup, url)
    if not found:
        parsed, published_at, extraction_method = ladder_date, ladder_published, ladder_method
        if published_at and parsed:
            page_title = _activity_fallback_title(soup)
            item = _activity_item(page_title, parsed, url, text, source_class=source_class, extraction_method=extraction_method or "html_text_span", published_at=published_at)
            if item:
                found.append(item)
    return _dedupe_items(found, keys=("date", "url", "title"))


def parse_activity_feed(raw: str | bytes, url: str, *, source_class: str = "company_owned") -> list[dict[str, Any]]:
    soup = BeautifulSoup(raw, "xml")
    found: list[dict[str, Any]] = []
    for item in soup.find_all(["item", "entry"]):
        title = _clean_sentence(item.find("title").get_text(" ", strip=True) if item.find("title") else "")
        date_names = ("pubDate", "published") if item.name == "item" else ("published", "updated")
        date_node = next((item.find(name) for name in date_names if item.find(name)), None)
        date_value = date_node.get_text(" ", strip=True) if date_node else ""
        published_at = parse_datetime(date_value)
        parsed = parse_date(date_value)
        if not parsed:
            continue
        link_node = item.find("link")
        link = str(link_node.get("href") if link_node and link_node.get("href") else link_node.get_text(" ", strip=True) if link_node else url)
        link = urllib.parse.urljoin(url, link)
        if not _same_site(url, link):
            continue
        description = item.find("description") or item.find("summary")
        activity = _activity_item(title, parsed, link, description.get_text(" ", strip=True) if description else title, source_class=source_class, extraction_method="feed_xml", published_at=published_at)
        if activity:
            found.append(activity)
    return _dedupe_items(found, keys=("date", "url", "title"))


def parse_activity_wp_json(raw: str | bytes, url: str, *, source_class: str = "company_owned") -> list[dict[str, Any]]:
    """Parse the public WordPress posts representation without treating it as a feed."""
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw))
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    found: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        if isinstance(title, dict):
            title = title.get("rendered")
        date_value = entry.get("date_gmt") if not str(entry.get("date") or "").strip().endswith(("Z", "+00:00")) else entry.get("date")
        date_value = date_value or entry.get("date")
        parsed = parse_date(date_value)
        published_at = parse_datetime(date_value)
        target = entry.get("link") or url
        if not parsed or not _same_site(url, str(target)):
            continue
        item = _activity_item(title, parsed, str(target), title, source_class=source_class, extraction_method="feed_xml", published_at=published_at)
        if item:
            found.append(item)
    return _dedupe_items(found, keys=("date", "url", "title"))


def _path_has_news_segment(url: str) -> bool:
    segments = [unescape(part).casefold() for part in urllib.parse.urlparse(url).path.split("/") if part]
    return any(segment in NEWS_SEGMENTS for segment in segments) and not any(segment in NEWS_SKIP_SEGMENTS for segment in segments)


def parse_sitemap_article_urls(raw: str | bytes, url: str, *, limit: int = 4) -> list[str]:
    """Return same-domain news URLs from a sitemap; no sitemap date/title is read."""
    soup = BeautifulSoup(raw, "xml")
    found: list[str] = []
    for node in soup.find_all("url"):
        loc = node.find("loc")
        target = loc.get_text(" ", strip=True) if loc else ""
        if not target or not _same_site(url, target) or not _path_has_news_segment(target):
            continue
        normalized = urllib.parse.urlunparse(urllib.parse.urlparse(target)._replace(fragment=""))
        if normalized not in found:
            found.append(normalized)
    return found[:max(0, limit)]


def parse_sitemap_career_urls(raw: str | bytes, url: str, *, limit: int = 8) -> list[str]:
    """Return career-shaped sitemap URLs; sitemap metadata is intentionally ignored."""
    soup = BeautifulSoup(raw, "xml")
    found: list[str] = []
    career_segments = {term.casefold() for term in CAREER_TERMS}
    for node in soup.find_all("url"):
        loc = node.find("loc")
        target = loc.get_text(" ", strip=True) if loc else ""
        if not target or not _same_site(url, target):
            continue
        segments = [unescape(part).casefold() for part in urllib.parse.urlparse(target).path.split("/") if part][:2]
        if not any(segment in career_segments or any(term in segment for term in career_segments) for segment in segments):
            continue
        normalized = urllib.parse.urlunparse(urllib.parse.urlparse(target)._replace(fragment=""))
        if normalized not in found:
            found.append(normalized)
    return found[:max(0, limit)]


def parse_sitemap_activity(raw: str | bytes, url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(raw, "xml")
    found: list[dict[str, Any]] = []
    for node in soup.find_all("url"):
        loc = node.find("loc")
        lastmod = node.find("lastmod")
        target = loc.get_text(" ", strip=True) if loc else ""
        raw_lastmod = lastmod.get_text(" ", strip=True) if lastmod else ""
        published_at = parse_datetime(raw_lastmod)
        parsed = parse_date(raw_lastmod)
        if not target or not parsed or not _same_site(url, target) or not _path_has_term(target, ACTIVITY_TERMS) or not _activity_url_allowed(target):
            continue
        title = _clean_sentence(urllib.parse.unquote(urllib.parse.urlparse(target).path.rstrip("/").rsplit("/", 1)[-1]).replace("-", " ")) or "Company activity"
        activity = _activity_item(title, parsed, target, f"sitemap lastmod {parsed}", source_class="company_owned", extraction_method="sitemap", published_at=published_at)
        if activity:
            found.append(activity)
    return _dedupe_items(found, keys=("date", "url", "title"))


def _job_from_jsonld(node: dict[str, Any], page_url: str, *, as_of: date | None = None, company_name: str = "", organisation_number: str = "") -> dict[str, Any] | None:
    kinds = node.get("@type") or node.get("type") or []
    kinds = {str(item).casefold() for item in (kinds if isinstance(kinds, list) else [kinds])}
    if "jobposting" not in kinds:
        return None
    if not _job_employer_matches(node, company_name=company_name, organisation_number=organisation_number):
        return None
    url = urllib.parse.urljoin(page_url, str(node.get("url") or page_url))
    if not _job_url_allowed(page_url, url):
        return None
    # A third-party JSON-LD posting is not evidence from the employer page. ATS
    # links are handled separately as links from the verified careers page.
    if not _same_site(page_url, url):
        return None
    location = node.get("jobLocation") or node.get("job_location") or ""
    if isinstance(location, list):
        location = ", ".join(_location_text(item) for item in location)
    else:
        location = _location_text(location)
    title = _clean_job_title(node.get("title") or node.get("name"))
    if not title or _job_title_is_non_vacancy_leadership_message(title) or _job_title_is_greeting_or_leader_message(title):
        return None
    posted = parse_date(node.get("datePosted"))
    closing = parse_date(node.get("validThrough"))
    reference = as_of or datetime.now(timezone.utc).date()
    if posted and date.fromisoformat(posted) > reference:
        closing = closing or posted
        posted = None
    return {"title": title, "location": location, "posted": posted, "effective_at": posted, "closing": closing, "url": url, "claim_span": _claim_span(title, location, node.get("description")), "source_class": "company_owned", "extraction_method": "html_jsonld"}


def _location_text(value: Any) -> str:
    if isinstance(value, dict):
        address = value.get("address") or value
        if isinstance(address, dict):
            return ", ".join(str(address.get(key) or "").strip() for key in ("streetAddress", "addressLocality", "addressRegion") if str(address.get(key) or "").strip())
    return " ".join(str(value or "").split())


APPLY_MARKERS = re.compile(r"(?:søknadsfrist|tiltredelse|stillingsprosent|apply\s+now)", re.I)
ATS_GENERIC_LABELS = {"les mer", "read more", "søk her", "søke her", "apply", "apply now", "view job", "se stilling"}


def _ats_platform(url: str) -> str:
    domain = safe_web.registered_domain(url)
    return {"teamtailor.com": "teamtailor", "recman.no": "recman", "jobylon.com": "jobylon", "easycruit.com": "easycruit", "reachmee.com": "reachmee", "reachmee.no": "reachmee", "finn.no": "finn"}.get(domain, domain)


def parse_jobs_html(html: str | bytes, url: str, *, soup: BeautifulSoup | None = None, as_of: date | None = None, company_name: str = "", organisation_number: str = "") -> tuple[list[dict[str, Any]], bool]:
    soup = soup or _parse_soup(html)
    json_ld = _jsonld_from_html(soup)
    jobs = [item for node in _jsonld_values(json_ld) if (item := _job_from_jsonld(node, url, as_of=as_of, company_name=company_name, organisation_number=organisation_number))]
    for anchor in soup.select("a[href]")[:300]:
        target = urllib.parse.urljoin(url, str(anchor.get("href") or ""))
        if not _is_recognized_ats_url(target):
            continue
        title = _clean_job_title(anchor.get_text(" ", strip=True))
        if _job_title_is_label_or_place(title) or title.casefold() in ATS_GENERIC_LABELS:
            previous = anchor.find_all_previous(["h2", "h3", "h4"], limit=1)
            title = _clean_job_title(previous[0].get_text(" ", strip=True) if previous else "")
        if title and not _is_navigation_job_title(title) and _job_title_is_ats_posting(title) and not _job_path_is_editorial(target):
            jobs.append({"title": title, "location": "", "posted": None, "effective_at": None, "closing": None, "url": target, "platform": _ats_platform(target), "claim_span": _claim_span(title, ""), "source_class": "company_owned", "extraction_method": "html_text_span"})
    # Generic article/li cards count only on a careers URL; elsewhere they are products or news
    # (S29 live: ArcticZymes /biomanufacturing "Production of viral vectors by host-cell").
    card_selector = "article, li, [class*='job'], [class*='stilling'], [class*='career']" if _job_page_is_career_url(url) else "[class*='job'], [class*='stilling'], [class*='career']"
    for node in soup.select(card_selector)[:300]:
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
        posted = parse_date(text)
        closing = None
        if posted and date.fromisoformat(posted) > (as_of or datetime.now(timezone.utc).date()):
            # A future date on a visible job card is the application deadline, not the posting date.
            posted, closing = None, posted
        jobs.append({"title": title, "location": "", "posted": posted, "effective_at": posted, "closing": closing, "url": target, "claim_span": _claim_span(title, text), "source_class": "company_owned", "extraction_method": "html_text_span"})
    if _job_page_is_career_url(url) and not NO_OPEN_ROLES.search(" ".join(soup.get_text(" ", strip=True).split())):
        visible = " ".join(soup.get_text(" ", strip=True).split())
        if APPLY_MARKERS.search(visible):
            heading = soup.select_one("h1") or soup.select_one("title")
            title = _clean_job_title(heading.get_text(" ", strip=True) if heading else "")
            if title and _job_title_is_visible_posting(title):
                posted = parse_date(visible)
                closing = None
                if posted and posted > (as_of or datetime.now(timezone.utc).date()).isoformat():
                    posted, closing = None, posted
                jobs.append({"title": title, "location": "", "posted": posted, "effective_at": posted, "closing": closing, "url": url, "claim_span": _claim_span(title, visible), "source_class": "company_owned", "extraction_method": "html_text_span"})
    selectors = soup.select("article, li, [class*='job'], [class*='stilling'], [class*='career']")
    return _dedupe_jobs(jobs), bool(json_ld or selectors or _job_page_is_career_url(url))


def _path_has_term(url: str, terms: Iterable[str]) -> bool:
    path = urllib.parse.urlparse(url).path.casefold()
    # Terms must start a word: "utstillinger" and "timebestilling" are not job pages.
    return any(re.search(r"(?<![a-z0-9æøå])" + re.escape(term), path) for term in terms)


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


def _comparable_datetime(value: Any) -> datetime | None:
    parsed = parse_datetime(value)
    if not parsed:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", parsed):
        return datetime.fromisoformat(parsed).replace(tzinfo=timezone.utc)
    result = datetime.fromisoformat(parsed)
    if result.tzinfo is None:
        result = result.replace(tzinfo=ZoneInfo("Europe/Oslo"))
    return result.astimezone(timezone.utc)


def _mark_old(items: list[dict[str, Any]], retrieved_at: Any = None, *, as_of: Any = None) -> list[dict[str, Any]]:
    reference = as_of if as_of is not None else datetime.now(timezone.utc).date()
    cutoff = _retrieved_date(reference) - timedelta(days=730)
    for item in items:
        item["old"] = bool(item.get("date") and date.fromisoformat(str(item["date"])) < cutoff)
    return items


def _career_status_code(document: dict[str, Any]) -> int | None:
    for key in ("status_code", "http_status", "response_status"):
        if document.get(key) is not None:
            try:
                return int(document[key])
            except (TypeError, ValueError):
                return None
    return None


def _career_page_candidate(document: dict[str, Any], *, base_url: str) -> dict[str, Any] | None:
    """Return provenance for a fetched, career-shaped page on the verified site/ATS."""
    final_url = str(document.get("final_url") or document.get("url") or "")
    status_code = _career_status_code(document)
    if status_code is not None and not 200 <= status_code < 300:
        return None
    if not _job_page_is_career_url(final_url) or not _job_url_allowed(base_url, final_url):
        return None
    raw = document.get("raw") or document.get("html") or ""
    content_hash = str(document.get("content_sha256") or "")
    if not content_hash and raw:
        raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
    title = _clean_sentence(document.get("h1") or document.get("title") or "")
    headings: list[str] = [title] if title else []
    if raw:
        soup = _parse_soup(raw)
        heading_nodes = [*soup.select("h1"), *(soup.select("h2")[:6])]
        headings = [_clean_sentence(node.get_text(" ", strip=True)) for node in heading_nodes]
        headings = [item for item in headings if item]
        if soup.title:
            headings.append(_clean_sentence(soup.title.get_text(" ", strip=True)))
        title = headings[0] if headings else title
    heading_text = " ".join(headings).casefold()
    if not any(term in heading_text for term in CAREER_HEADING_TERMS):
        return None
    return {
        "url": final_url,
        "claim_span": _claim_span(title, str(document.get("visible_text") or document.get("main_text_excerpt") or "")),
        "content_sha256": content_hash or None,
        "extraction_method": "careers_page_fetch",
    }


def _company_org_number(profile: dict[str, Any]) -> str:
    raw = profile.get("raw") or {}
    value = profile.get("organisation_number") or profile.get("organisationNumber") or raw.get("organisasjonsnummer") or raw.get("organisation_number") or ""
    return re.sub(r"\D", "", str(value))


def _company_name_matches(candidate: Any, company_name: str) -> bool:
    left = set(_stem_tokens(candidate))
    right = set(_stem_tokens(company_name))
    return bool(left and right and (left <= right or right <= left or len(left & right) >= max(1, min(2, len(right)))))


def _job_employer_matches(node: dict[str, Any], *, company_name: str = "", organisation_number: str = "") -> bool:
    employer = node.get("hiringOrganization") or node.get("hiring_organization")
    if not employer:
        return True
    if isinstance(employer, list):
        employer = employer[0] if employer else {}
    if not isinstance(employer, dict):
        return False
    employer_org = re.sub(r"\D", "", str(employer.get("identifier") or employer.get("orgnr") or employer.get("organisationNumber") or ""))
    if employer_org and organisation_number:
        return employer_org == organisation_number
    employer_name = str(employer.get("name") or "").strip()
    return not employer_name or not company_name or _company_name_matches(employer_name, company_name)


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
    parts = [{"text": registry_text, "source_class": "official_registry", "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv", "claim_span": registry_text, "kind": "registry", "extraction_method": "registry_json"}]
    site_value = (website or {}).get("value") or {}
    site_url = str(site_value.get("final_url") or (website or {}).get("source_url") or "")
    candidates = _brief_candidates(website or {}, base_url=site_url)
    if candidates and site_url and (website or {}).get("status") == "available":
        item = candidates[0]
        parts.insert(0, {"text": item["text"], "source_class": "company_owned", "source_url": item["source_url"] or site_url, "claim_span": item["text"], "kind": item["kind"], "extraction_method": "html_jsonld" if item["kind"] == "jsonld_description" else "html_text_span"})
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
        posted = parse_date(ad.get("published") or ad.get("posted"))
        result.append({
            "title": str(ad.get("title") or "Open NAV position"),
            "location": _location_text(ad.get("location") or ad.get("workLocations") or ""),
            "posted": posted,
            "effective_at": posted,
            "closing": parse_date(ad.get("expires") or ad.get("closing")),
            "url": str(ad.get("link") or ad.get("source_url") or "https://arbeidsplassen.nav.no/stillinger"),
            "claim_span": _claim_span(str(ad.get("title") or "Open NAV position"), str(ad.get("location") or "")),
            "source_class": "nav_feed",
            "extraction_method": str(ad.get("extraction_method") or "nav_live_api"),
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
    verified_career: dict[str, Any] | None = None
    no_open_roles = False
    no_open_roles_span = ""
    parse_errors: list[str] = []
    phase_timings_ms = {"activity": 0.0, "hiring": 0.0, "brief": 0.0, "parse": 0.0, "dns": 0.0, "fetch": 0.0}
    base_url = str(value.get("final_url") or website.get("source_url") or "")
    company_name = str(profile.get("name") or (profile.get("raw") or {}).get("navn") or "")
    organisation_number = _company_org_number(profile)
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
                item.setdefault("discovered_via", page_url)
                activity.append(item)
            phase_timings_ms["activity"] += (time.perf_counter() - activity_started) * 1000
            hiring_started = time.perf_counter()
            job = _job_from_jsonld(node, page_url, as_of=_retrieved_date(retrieved_at), company_name=company_name, organisation_number=organisation_number)
            if job:
                site_jobs.append(job)
                career_found = True
                career_url = career_url or page_url
            phase_timings_ms["hiring"] += (time.perf_counter() - hiring_started) * 1000
        activity_started = time.perf_counter()
        if raw:
            page_activity = parse_activity_html(raw, page_url, soup=soup)
            for item in page_activity:
                item.setdefault("discovered_via", page_url)
            activity.extend(page_activity)
        phase_timings_ms["activity"] += (time.perf_counter() - activity_started) * 1000
        hiring_started = time.perf_counter()
        if raw:
            jobs, parseable = parse_jobs_html(raw, page_url, soup=soup, as_of=_retrieved_date(retrieved_at), company_name=company_name, organisation_number=organisation_number)
            site_jobs.extend(jobs)
            if jobs:
                career_found = True
                career_url = career_url or page_url
            career_parseable = career_parseable or parseable
            if NO_OPEN_ROLES.search(str(raw)):
                no_open_roles = True
                no_open_roles_span = no_open_roles_span or _no_openings_span(str(raw))
        phase_timings_ms["hiring"] += (time.perf_counter() - hiring_started) * 1000
        for link in links:
            target = link.get("url") or ""
            if _path_has_term(target, CAREER_TERMS) or any(term in str(link.get("text") or "").casefold() for term in CAREER_TERMS):
                career_url = career_url or target
        candidate = _career_page_candidate(page, base_url=base_url)
        if candidate:
            career_found = True
            career_url = career_url or candidate["url"]
            verified_career = verified_career or candidate
            if NO_OPEN_ROLES.search(text):
                no_open_roles = True
                no_open_roles_span = no_open_roles_span or _no_openings_span(text)
    for document in extra_documents or []:
        if document.get("kind") == "feed":
            feed_url = str(document.get("url") or base_url)
            feed_items = parse_activity_feed(document.get("raw") or document.get("html") or "", feed_url)
            for item in feed_items:
                item.setdefault("discovered_via", feed_url)
            activity.extend(feed_items)
        if document.get("kind") == "wp_json":
            feed_url = str(document.get("url") or base_url)
            feed_items = parse_activity_wp_json(document.get("raw") or document.get("html") or "", feed_url)
            for item in feed_items:
                item.setdefault("discovered_via", feed_url)
            activity.extend(feed_items)
        # Sitemaps are not a news source: lastmod is not a publication date and slugs are not titles.
        if _path_has_term(str(document.get("url") or ""), CAREER_TERMS):
            career_url = career_url or str(document.get("url") or "")
        if verified_career is None and document.get("kind") == "career":
            verified_career = _career_page_candidate(document, base_url=base_url)
            if verified_career:
                career_found = True
    site_jobs = _dedupe_jobs(site_jobs)[:25]
    job_urls = {str(item.get("url") or "") for item in site_jobs}
    job_titles = {_normalized_job_title(item.get("title")) for item in site_jobs}
    activity = [
        item for item in activity
        if str(item.get("url") or "") not in job_urls
        and _normalized_job_title(item.get("title")) not in job_titles
    ]
    crawl_date = _retrieved_date(retrieved_at)
    retrieval_instant = _comparable_datetime(retrieved_at)
    activity = [
        item for item in activity
        if str(item.get("date") or "") != crawl_date.isoformat()
        and (retrieval_instant is None or _comparable_datetime(item.get("published_at") or item.get("date")) is None or _comparable_datetime(item.get("published_at") or item.get("date")) <= retrieval_instant)
    ]
    activity = _dedupe_items(activity, keys=("date", "url", "title"))
    activity = sorted(activity, key=lambda item: (str(item.get("date") or ""), str(item.get("url") or "")), reverse=True)
    activity = _mark_old(activity, as_of=datetime.now(timezone.utc).date())[:20]
    nav_evidence = (profile.get("evidence") or {}).get("nav_jobs") or {}
    nav_value = nav_evidence.get("value") if isinstance(nav_evidence.get("value"), dict) else {}
    # A complete NAV index that was consulted for this organisation is a real check, even
    # when the company has no verified website to crawl.
    nav_checked = nav_evidence.get("status") in {"available", "not_found", "not_available"} and nav_value.get("checked") is True
    recent_activity = [item for item in activity if not item.get("old")]
    activity_status = "not_available" if activity and not recent_activity else "available" if activity else ("blocked" if website.get("status") == "blocked" else "not_available" if (website_available or nav_checked) else "not_checked")
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
        hiring_status = "available" if site_jobs or verified_career else "not_available"
    else:
        hiring_status = "available" if nav_jobs else "not_available" if (website_available or nav_checked) else "not_checked"
    hiring_value = {
        "postings": merged_jobs,
        "company_site_postings": site_jobs,
        "nav_ads": nav_jobs,
        "careers_page_found": bool(career_found or career_url),
        "signal": "postings" if site_jobs else "nav_ads" if nav_jobs else "careers_page" if verified_career else None,
        "no_open_roles": no_open_roles,
        "explicit_no_openings": no_open_roles,
        "explicit_no_openings_span": no_open_roles_span or None,
        "checked_url": career_url or (base_url if site_jobs else ""),
        "checked": bool(website_available or nav_checked),
        "sources_agree": agreements,
    }
    if verified_career:
        hiring_value.update({"careers_page_url": verified_career["url"], "career_claim_span": verified_career["claim_span"]})
    activity_methods = {str(item.get("extraction_method") or "html_text_span") for item in activity}
    activity_method = next(iter(activity_methods)) if len(activity_methods) == 1 else "html_text_span"
    nav_methods = {str(item.get("extraction_method") or "nav_live_api") for item in nav_jobs}
    hiring_method = next(iter(nav_methods)) if nav_jobs and not site_jobs and len(nav_methods) == 1 else "nav_live_api" if nav_jobs and not site_jobs else verified_career["extraction_method"] if verified_career and not site_jobs and not nav_jobs else "html_text_span"
    activity_effective = sorted({str(item.get("published_at") or item.get("effective_at") or item.get("date")) for item in activity if item.get("published_at") or item.get("effective_at") or item.get("date")})
    hiring_effective = sorted({str(item.get("effective_at") or item.get("posted")) for item in merged_jobs if item.get("effective_at") or item.get("posted")})
    digest = hashlib.sha256(json.dumps({"activity": activity, "brief": brief_value, "hiring": hiring_value}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    source_url = base_url or str((profile.get("evidence") or {}).get("registry", {}).get("source_url") or "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv")
    hiring_content_sha = verified_career.get("content_sha256") if verified_career and not site_jobs and not nav_jobs else digest
    return {
        "public_activity": evidence("public_activity", activity_status, "company_owned" if website_available else "nav_feed", source_url, value={"items": activity, "checked": bool(website_available or nav_checked), "max_items": 20}, note=activity_note, retrieved_at=retrieved_at, content_sha256=digest, effective_at=activity_effective or None, extraction_method=activity_method),
        "company_brief": evidence("company_brief", brief_status, "company_owned" if not brief_value.get("registry_derived") else "official_registry", source_url, value=brief_value, note="Deterministic registry and verified-site description parts", retrieved_at=retrieved_at, content_sha256=digest, extraction_method="registry_json" if brief_value.get("registry_derived") else "html_text_span"),
        "hiring": evidence("hiring", hiring_status, "company_owned" if (site_jobs or career_found) else "nav_feed", verified_career["url"] if verified_career and not site_jobs and not nav_jobs else career_url or (source_url if site_jobs else "https://arbeidsplassen.nav.no/stillinger"), value=hiring_value, note="checked: careers page found, no postings" if career_found and not site_jobs else "Company-owned careers postings merged with exact-organisation NAV ads", retrieved_at=retrieved_at, content_sha256=hiring_content_sha or digest, effective_at=hiring_effective or None, extraction_method=hiring_method),
        "metrics": {"requests": 0, "bytes": 0, "latencies_ms": [], "activity_items": len(activity), "company_site_jobs": len(site_jobs), "merged_jobs": len(merged_jobs), "career_pages": int(career_found), "phase_timings_ms": {key: round(value, 3) for key, value in phase_timings_ms.items()}},
    }


def _fetch_fact_document(url: str, *, base_url: str, timeout: float, request_policy: Any = None, allow_ats: bool = False) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Fetch one same-domain document with the existing SSRF/robots-safe opener."""
    started = time.monotonic()
    timeout = min(6.0, max(0.1, float(timeout)))
    phase_timings_ms = {"dns": 0.0, "fetch": 0.0, "parse": 0.0, "activity": 0.0, "hiring": 0.0, "brief": 0.0}
    if not (_job_url_allowed(base_url, url) if allow_ats else _same_site(base_url, url)):
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
            status_code = int(response.getcode() or 200)
        phase_timings_ms["fetch"] = round((time.perf_counter() - fetch_started) * 1000, 3)
        if len(raw) > 1_000_000 or not ((_job_url_allowed(base_url, final_url)) if allow_ats else _same_site(base_url, final_url)):
            return None, {"requests": 1, "bytes": len(raw), "latencies_ms": [int((time.monotonic() - started) * 1000)], "error": "oversized_or_cross_domain", "url": url, "phase_timings_ms": phase_timings_ms}
        kind = "sitemap" if "sitemap" in url.casefold() or raw.lstrip().startswith(b"<urlset") or raw.lstrip().startswith(b"<sitemapindex") else "wp_json" if "wp-json" in url.casefold() or "application/json" in content_type.casefold() else "feed" if "rss" in content_type.casefold() or "atom" in content_type.casefold() or raw.lstrip().startswith((b"<rss", b"<feed")) else "html"
        html = raw.decode("utf-8", errors="replace")
        page: dict[str, Any] = {"url": final_url, "requested_url": url, "status_code": status_code, "content_sha256": hashlib.sha256(raw).hexdigest(), "kind": kind, "raw": raw if kind != "html" else None}
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
    generator = soup.select_one('meta[name="generator"]')
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
        "generator": str(generator.get("content") or "").strip()[:200] if generator else "",
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


def _activity_title_key(value: Any) -> str:
    return re.sub(r"\W+", " ", _clean_sentence(value, limit=180).casefold()).strip()


def _article_matches_activity(listing: dict[str, Any], article_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    listing_title = _activity_title_key(listing.get("title"))
    listing_date = parse_date(listing.get("published_at") or listing.get("date"))
    for article in article_items:
        if _activity_title_key(article.get("title")) != listing_title:
            continue
        article_date = parse_date(article.get("published_at") or article.get("date"))
        if listing_date and article_date == listing_date:
            return article
    return None


def _news_link_candidates(page: dict[str, Any], base_url: str) -> list[str]:
    result: list[str] = []
    for link in page.get("links") or []:
        target = urllib.parse.urljoin(str(page.get("url") or base_url), str(link.get("url") or link.get("href") or ""))
        if _same_site(base_url, target) and _path_has_news_segment(target) and target not in result:
            result.append(target)
    return result


def _platform_feed_candidates(page: dict[str, Any], base_url: str) -> list[str]:
    raw = (str(page.get("raw") or page.get("html") or "") + " " + str(page.get("generator") or "")).casefold()
    links = [str(item) for item in page.get("alternate_feeds") or []]
    if links:
        return [urllib.parse.urljoin(base_url, item) for item in links]
    platform = "wordpress" if "wp-content" in raw or "wordpress" in raw else "wix" if "wixstatic" in raw or "wix.com" in raw else "squarespace" if "squarespace" in raw else ""
    if platform == "wordpress":
        return [urllib.parse.urljoin(base_url, path) for path in ("/feed/", "/feed/?paged=2", "/wp-json/wp/v2/posts?per_page=20&_fields=link,title,date,date_gmt")]
    if platform == "wix":
        return [urllib.parse.urljoin(base_url, "/blog-feed.xml")]
    if platform == "squarespace":
        listing = str(page.get("url") or base_url)
        return [listing + ("&" if "?" in listing else "?") + "format=rss"]
    return [urllib.parse.urljoin(base_url, path) for path in ("/rss.xml", "/feed")]


def _pagination_candidates(page: dict[str, Any], base_url: str) -> list[str]:
    found: list[str] = []
    current = str(page.get("url") or base_url)
    for link in page.get("links") or []:
        target = urllib.parse.urljoin(current, str(link.get("url") or link.get("href") or ""))
        if re.search(r"(?:/page/2(?:/|$)|[?&](?:page|side|paged)=2(?:&|$))", target, re.I) and _same_site(base_url, target):
            if target not in found:
                found.append(target)
    return found[:4]


def enrich_site_facts(
    profile: dict[str, Any],
    *,
    request_policy: Any = None,
    timeout: float = 6.0,
    max_extra_requests: int = 8,
    max_news_requests: int = 6,
    max_article_requests: int = 6,
    total_budget_seconds: float = 12.0,
    deadline: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use captured pages, then fetch bounded listing, career and article documents."""
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
    feed_candidates: list[tuple[str, str, bool]] = []
    activity_candidates: list[tuple[str, str, bool]] = []
    career_candidates: list[tuple[str, str, bool]] = []
    about_candidates: list[tuple[str, str, bool]] = []
    for page in _site_pages(website):
        for link in page.get("links") or []:
            target = urllib.parse.urljoin(str(page.get("url") or base_url), str(link.get("url") or link.get("href") or ""))
            text = str(link.get("text") or "").casefold()
            target_path = urllib.parse.urlparse(target).path.casefold()
            if target_path.endswith((".xml", ".rss", ".atom")) or "feed" in target_path or text in {"rss", "atom", "feed", "nyhetsfeed"}:
                feed_candidates.append((target, "feed", False))
                continue
            is_career = _path_has_term(target, CAREER_TERMS) or any(term in text for term in CAREER_TERMS)
            is_activity = _path_has_term(target, ACTIVITY_TERMS)
            is_about = any(term in target_path.strip("/").split("/")[:2] for term in ABOUT_TERMS) or any(term in text for term in ABOUT_TERMS)
            if is_activity and not is_career:
                activity_candidates.append((target, "activity", False))
            elif is_career:
                career_candidates.append((target, "career", False))
            elif is_about:
                about_candidates.append((target, "about", False))
        for feed in page.get("alternate_feeds") or []:
            feed_candidates.append((urllib.parse.urljoin(str(page.get("url") or base_url), str(feed)), "feed", False))
    pages = _site_pages(website)
    # Order: linked careers pages, about pages (depth 2, only without a careers link), linked
    # news/feeds, one shared sitemap, then blind careers and news probes alternating so neither
    # family starves the other (S29 live: six news probes used the whole budget and Studix,
    # Narvik Golf and StrongPoint lost their careers pages).
    linked_news: list[tuple[str, str, bool]] = [*activity_candidates[:2], *feed_candidates[:2]]
    guessed_news: list[tuple[str, str, bool]] = []
    if not feed_candidates and not activity_candidates:
        for page in pages[:1]:
            guessed_news.extend((url, "feed", True) for url in _platform_feed_candidates(page, base_url))
    if not activity_candidates:
        for path in ("/aktuelt", "/nyheter", "/news", "/blogg", "/presse", "/en/news"):
            guessed_news.append((urllib.parse.urljoin(base_url, path), "activity", True))
    guessed_careers: list[tuple[str, str, bool]] = []
    if not career_candidates:
        for path in ("/karriere", "/jobb", "/ledige-stillinger", "/careers", "/jobs"):
            guessed_careers.append((urllib.parse.urljoin(base_url, path), "career", True))
    sitemap = [(urllib.parse.urljoin(base_url, "/sitemap.xml"), "sitemap", True)] if not career_candidates or not activity_candidates else []
    probes: list[tuple[str, str, bool]] = []
    for index in range(max(len(guessed_news), len(guessed_careers))):
        probes.extend(item[index] for item in (guessed_careers, guessed_news) if index < len(item))
    candidates = [*career_candidates[:2], *([] if career_candidates else about_candidates[:2]), *linked_news, *sitemap, *probes]
    seen = {str(page.get("url") or "") for page in pages}
    extras: list[dict[str, Any]] = []
    sitemap_article_urls: list[str] = []
    news_queue = list(candidates)
    processed_news_urls: set[str] = set()
    news_requests = 0
    career_requests = 0
    metrics = {"requests": 0, "bytes": 0, "latencies_ms": [], "extra_requests": 0, "news_requests": 0, "article_requests": 0, "extra_errors": [], "phase_timings_ms": {"activity": 0.0, "hiring": 0.0, "brief": 0.0, "parse": 0.0, "dns": 0.0, "fetch": 0.0}}
    while news_queue:
        target, kind, _guessed = news_queue.pop(0)
        if metrics["extra_requests"] >= max_extra_requests or target in seen or time.monotonic() >= local_deadline:
            continue
        is_news = kind in {"activity", "feed"}
        if is_news and news_requests >= max_news_requests:
            continue
        if not (_job_url_allowed(base_url, target) if kind == "career" else _same_site(base_url, target)):
            continue
        if kind == "career" and career_requests >= 8:
            continue
        seen.add(target)
        remaining = local_deadline - time.monotonic()
        if remaining <= 0:
            break
        request_timeout = min(6.0, max(0.1, float(timeout)), remaining)
        document, operation = _fetch_fact_document(target, base_url=base_url, timeout=request_timeout, request_policy=request_policy, allow_ats=kind == "career")
        metrics["requests"] += int(operation.get("requests") or 0)
        metrics["bytes"] += int(operation.get("bytes") or 0)
        metrics["latencies_ms"].extend(operation.get("latencies_ms") or [])
        metrics["extra_requests"] += 1
        if is_news:
            news_requests += 1
            metrics["news_requests"] = news_requests
        if kind == "career":
            career_requests += 1
            metrics["career_requests"] = career_requests
        for phase, elapsed in (operation.get("phase_timings_ms") or {}).items():
            metrics["phase_timings_ms"][phase] = metrics["phase_timings_ms"].get(phase, 0.0) + float(elapsed or 0.0)
        if document:
            document["discovery_kind"] = kind
            extras.append(document)
            if kind == "sitemap":
                sitemap_article_urls.extend(parse_sitemap_article_urls(document.get("raw") or document.get("html") or "", target, limit=4))
                if not career_candidates:
                    news_queue[0:0] = [(url, "career", True) for url in parse_sitemap_career_urls(document.get("raw") or document.get("html") or "", target, limit=8)]
            elif kind == "activity":
                raw = document.get("html") or document.get("raw") or ""
                listing_items = parse_activity_html(raw, target) if raw else []
                # Follow-ups from a real listing go before blind probes.
                follow_ups = []
                if not listing_items:
                    follow_ups.extend((link, "activity", False) for link in _news_link_candidates(document, base_url)[:1] if link not in processed_news_urls)
                follow_ups.extend((link, "activity", False) for link in _pagination_candidates(document, base_url)[:1] if link not in processed_news_urls)
                news_queue[0:0] = follow_ups
            elif kind == "about":
                metadata = parse_html_metadata(document.get("html") or document.get("raw") or "", target)
                discovered = []
                for link in metadata.get("links") or []:
                    link_url = str(link.get("url") or "")
                    if _path_has_term(link_url, CAREER_TERMS) or any(term in str(link.get("text") or "").casefold() for term in CAREER_TERMS):
                        discovered.append((link_url, "career", False))
                news_queue[0:0] = discovered[:2]
            processed_news_urls.add(target)
        elif operation.get("error") or operation.get("blocked"):
            metrics["extra_errors"].append({"url": target, "kind": kind, "error": operation.get("error") or "robots_blocked", "blocked": bool(operation.get("blocked"))})
    facts = extract_site_facts(profile, extra_documents=extras)
    # Listing/feed dates are valid evidence when the entry supplies its own date
    # and same-domain URL. Article upgrades improve provenance, but are optional.
    activity_items = ((facts.get("public_activity") or {}).get("value") or {}).get("items") or []
    article_candidates = [
        item for item in activity_items
        if isinstance(item, dict) and item.get("url") and _activity_url_allowed(str(item.get("url")))
    ]
    article_candidates = sorted(article_candidates, key=lambda item: (str(item.get("date") or ""), str(item.get("url") or "")), reverse=True)
    article_targets: list[tuple[dict[str, Any] | None, str]] = [(item, str(item.get("url") or "")) for item in article_candidates]
    existing_article_urls = {url for _item, url in article_targets}
    article_targets.extend((None, url) for url in sitemap_article_urls if url not in existing_article_urls)
    for listing, article_url in article_targets[:max_article_requests]:
        if metrics["extra_requests"] >= max_extra_requests or time.monotonic() >= local_deadline:
            break
        if not article_url or article_url in seen:
            continue
        seen.add(article_url)
        remaining = local_deadline - time.monotonic()
        if remaining <= 0:
            break
        document, operation = _fetch_fact_document(article_url, base_url=base_url, timeout=min(6.0, max(0.1, float(timeout)), remaining), request_policy=request_policy)
        metrics["requests"] += int(operation.get("requests") or 0)
        metrics["bytes"] += int(operation.get("bytes") or 0)
        metrics["latencies_ms"].extend(operation.get("latencies_ms") or [])
        metrics["extra_requests"] += 1
        metrics["article_requests"] = int(metrics.get("article_requests") or 0) + 1
        for phase, elapsed in (operation.get("phase_timings_ms") or {}).items():
            metrics["phase_timings_ms"][phase] = metrics["phase_timings_ms"].get(phase, 0.0) + float(elapsed or 0.0)
        if not document:
            if operation.get("error") or operation.get("blocked"):
                metrics["extra_errors"].append({"url": article_url, "kind": "article", "error": operation.get("error") or "robots_blocked", "blocked": bool(operation.get("blocked"))})
            continue
        article_raw = document.get("html") or document.get("raw") or ""
        article_items = parse_activity_html(article_raw, article_url) if article_raw else []
        matched = _article_matches_activity(listing, article_items) if listing else (article_items[0] if article_items else None)
        if matched:
            target_item = listing
            if target_item is None:
                target_item = matched
                target_item.setdefault("discovered_via", str(document.get("requested_url") or base_url))
                activity_items.append(target_item)
            target_item.update({
                "article_verified": True,
                "article_url": article_url,
                "article_content_sha256": document.get("content_sha256") or hashlib.sha256(str(article_raw).encode()).hexdigest(),
                "article_claim_span": matched.get("title") or listing.get("title"),
                "article_extraction_method": matched.get("extraction_method") or "html_text_span",
                "published_at": matched.get("published_at") or listing.get("published_at"),
                "effective_at": matched.get("published_at") or listing.get("effective_at"),
                "discovered_via": listing.get("discovered_via") or base_url,
            })
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
    "extract_company_brief", "extract_site_facts", "parse_activity_feed", "parse_activity_wp_json",
    "parse_activity_html", "parse_date", "parse_datetime", "parse_jobs_html", "parse_sitemap_activity", "parse_sitemap_article_urls", "parse_sitemap_career_urls",
]
