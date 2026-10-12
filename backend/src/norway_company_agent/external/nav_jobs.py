from __future__ import annotations

import json
import hashlib
import copy
import re
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from bs4 import BeautifulSoup

from ..core.orgnumber import digits_only, is_valid_org_number
from .external_footprint import connector_policy_entry, observation_id
from ..web.website import SAFE_OPENER, assert_public_url

CONNECTOR_ID = "nav_jobs"
PLATFORM = "job_board"

FEED_ORIGIN = "https://pam-stilling-feed.nav.no"
FEED_SOURCE_URL = f"{FEED_ORIGIN}/api/v1/feed"
USER_AGENT = "builderr-signalpost-poc/0.1 (+https://builderr.ai)"
LIVE_DETAIL_TIMEOUT = 6.0
MAX_LIVE_ADS_PER_COMPANY = 5
LIVE_REJECTION_REASONS = ("inactive", "expired", "organisation_mismatch", "payload_missing", "payload_mismatch")
# The experiment token is published for anyone; a private token is requested from NAV.
PUBLIC_TOKEN_URL = f"{FEED_ORIGIN}/api/publicToken"


def load_index(path: Any) -> dict[str, dict[str, Any]]:
    """Read the employer index JSONL written by the NAV connector (empty if missing)."""
    from pathlib import Path

    file = Path(path)
    if not file.exists():
        return {}
    now = datetime.now(timezone.utc)
    rows = (json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip())
    loaded: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("organisation_number"):
            continue
        row["ads"] = [ad for ad in row.get("ads") or [] if not _ad_expired(ad, now)]
        loaded[str(row["organisation_number"])] = row
    return loaded


def _ad_expired(ad: dict[str, Any], now: datetime) -> bool:
    expires = str(ad.get("expires") or ad.get("expires_at") or "")
    if not expires:
        return False
    try:
        parsed = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return parsed <= now
    except ValueError:
        return False


def parse_token(text: str) -> str:
    """The public-token endpoint returns a sentence followed by the JWT on its own line."""
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _rsc_public_ad(text: str) -> dict[str, Any] | None:
    employer_match = re.search(
        r'"employer"\s*:\s*\{\s*"orgnr"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*"name"\s*:\s*"((?:\\.|[^"\\])*)"',
        text,
    )
    if not employer_match:
        return None

    def value(name: str, region: str, *, last: bool = False) -> str | None:
        matches = list(re.finditer(rf'"{name}"\s*:\s*"((?:\\.|[^"\\])*)"', region))
        if not matches:
            return None
        raw = matches[-1 if last else 0].group(1)
        try:
            return json.loads('"' + raw + '"')
        except json.JSONDecodeError:
            return raw.replace('\\"', '"')

    before = text[:employer_match.start()]
    after = text[employer_match.end():employer_match.end() + 1800]
    return {
        "uuid": value("uuid", before, last=True),
        "title": value("title", before, last=True) or value("jobTitle", before, last=True),
        "status": value("status", after),
        "published": value("published", before, last=True),
        "expires": value("expires", after),
        "employer": {"orgnr": employer_match.group(1), "name": employer_match.group(2)},
    }


def _next_payloads(html: str | bytes) -> Iterator[Any]:
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        raw = script.string or script.get_text()
        if not raw.strip():
            continue
        if script.get("id") == "__NEXT_DATA__" or script.get("type") in {"application/json", "application/json+ld"}:
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                pass
        for match in re.finditer(r"__next_f\.push\((.+)\)", raw, re.S):
            try:
                envelope = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            yield envelope
            if isinstance(envelope, list):
                for part in envelope:
                    if not isinstance(part, str):
                        continue
                    yield part
                    try:
                        yield json.loads(part)
                    except json.JSONDecodeError:
                        pass
                    rsc_ad = _rsc_public_ad(part)
                    if rsc_ad:
                        yield rsc_ad


def _public_ad_node(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        employer = value.get("employer")
        if isinstance(employer, dict) and employer.get("orgnr"):
            return value
        for child in value.values():
            found = _public_ad_node(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _public_ad_node(child)
            if found:
                return found
    return None


def _page_ad_uuid(page_url: str | None) -> str | None:
    if not page_url:
        return None
    path = urllib.parse.urlparse(str(page_url)).path
    match = re.search(r"/stillinger/stilling/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:/|$)", path)
    return match.group(1).lower() if match else None


def _balanced_json_object(text: str, start: int) -> str | None:
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _rsc_ad_data(value: str) -> Iterator[dict[str, Any]]:
    """Yield exact adData objects from a decoded RSC chunk, not nearby ads."""
    for match in re.finditer(r'"adData"\s*:\s*', value):
        object_start = match.end()
        while object_start < len(value) and value[object_start].isspace():
            object_start += 1
        object_text = _balanced_json_object(value, object_start)
        if not object_text:
            continue
        try:
            node = json.loads(object_text)
        except json.JSONDecodeError:
            continue
        if isinstance(node, dict):
            yield node


def _ad_data_nodes(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, str):
        yield from _rsc_ad_data(value)
    elif isinstance(value, dict):
        ad_data = value.get("adData")
        if isinstance(ad_data, dict):
            yield ad_data
        for child in value.values():
            yield from _ad_data_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _ad_data_nodes(child)


def _normalise_public_ad(node: dict[str, Any]) -> dict[str, Any]:
    employer = node.get("employer") or {}
    application = node.get("application") or {}
    return {
        "uuid": node.get("id") or node.get("uuid"),
        "title": node.get("title") or node.get("jobTitle") or node.get("name"),
        "status": node.get("status"),
        "published": node.get("published") or node.get("datePosted"),
        "expires": node.get("expires") or node.get("validThrough"),
        "employer": {"orgnr": employer.get("orgnr"), "name": employer.get("name"), "homepage": employer.get("homepage")},
        "location": node.get("location") or node.get("locationList") or node.get("workLocations") or node.get("work_locations"),
        "applicationUrl": node.get("applicationUrl") or node.get("application_url") or application.get("applicationUrl"),
        "source": node.get("source"),
        "_extracted_payload": node,
    }


def _find_public_ad_payload(html: str | bytes, page_url: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    page_uuid = _page_ad_uuid(page_url)
    ad_data_nodes: list[dict[str, Any]] = []
    for payload in _next_payloads(html):
        ad_data_nodes.extend(_ad_data_nodes(payload))
    if ad_data_nodes:
        if page_uuid:
            for node in ad_data_nodes:
                node_uuid = str(node.get("id") or node.get("uuid") or "").lower()
                if node_uuid == page_uuid:
                    return node, None
            return None, "payload_mismatch"
        return ad_data_nodes[0], None
    if page_uuid:
        return None, "payload_missing"
    # Keep the S24 synthetic fixture contract for callers that did not provide a real page URL.
    for payload in _next_payloads(html):
        node = _public_ad_node(payload)
        if node:
            return node, None
    return None, "payload_missing"


def parse_public_page(html: str | bytes, page_url: str | None = None) -> dict[str, Any] | None:
    """Extract the main NAV ad, identified strictly by the page URL UUID."""
    node, _reason = _find_public_ad_payload(html, page_url)
    return _normalise_public_ad(node) if node else None


def _fetch_public_page(url: str, timeout: float = LIVE_DETAIL_TIMEOUT) -> str:
    assert_public_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    with SAFE_OPENER.open(request, timeout=min(LIVE_DETAIL_TIMEOUT, max(0.1, float(timeout)))) as response:
        body = response.read(20_000_000 + 1)
    if len(body) > 20_000_000:
        raise OSError("NAV public page exceeded 20 MB limit")
    return body.decode("utf-8", errors="replace")


def _parse_nav_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def verify_public_page(
    url: str,
    *,
    organisation_number: str,
    sub_unit_organisation_number: str | None = None,
    now: datetime | None = None,
    timeout: float = LIVE_DETAIL_TIMEOUT,
    fetcher: Callable[[str, float], str | bytes] | None = None,
) -> dict[str, Any]:
    """Fetch and verify one public NAV page without requiring a feed token."""
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        html = fetcher(url, timeout) if fetcher else _fetch_public_page(url, timeout)
        extracted, payload_reason = _find_public_ad_payload(html, url)
        extracted = _normalise_public_ad(extracted) if extracted else None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "fetch_failed", "error": type(exc).__name__, "retrieved_at": retrieved_at}
    if not extracted:
        return {"ok": False, "reason": payload_reason or "payload_missing", "retrieved_at": retrieved_at}
    status = str(extracted.get("status") or "").upper()
    if status != "ACTIVE":
        return {"ok": False, "reason": "inactive", "retrieved_at": retrieved_at}
    expires = _parse_nav_timestamp(extracted.get("expires"))
    if expires and expires <= (now or datetime.now(timezone.utc)):
        return {"ok": False, "reason": "expired", "retrieved_at": retrieved_at}
    title = str(extracted.get("title") or "").strip()
    if not title:
        return {"ok": False, "reason": "payload_missing", "retrieved_at": retrieved_at}
    actual_org = digits_only((extracted.get("employer") or {}).get("orgnr"))
    allowed_orgs = {digits_only(organisation_number)}
    if sub_unit_organisation_number:
        allowed_orgs.add(digits_only(sub_unit_organisation_number))
    if actual_org not in allowed_orgs or not is_valid_org_number(actual_org):
        return {"ok": False, "reason": "organisation_mismatch", "retrieved_at": retrieved_at}
    payload = extracted.get("_extracted_payload") or extracted
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()
    employer = extracted.get("employer") or {}
    ad = {
        "uuid": extracted.get("uuid"),
        "title": title,
        "published": extracted.get("published"),
        "expires": extracted.get("expires"),
        "link": url,
        "location": extracted.get("location"),
        "application_url": extracted.get("applicationUrl"),
        "source": extracted.get("source") or "NAV public page",
        "source_url": url,
    }
    return {
        "ok": True,
        "parsed": {
            "organisation_number": actual_org,
            "employer_organisation_number": actual_org,
            "employer_name": employer.get("name"),
            "homepage": (employer.get("homepage") or "").strip() or None,
            "contact_email_domains": [],
            "ad": ad,
            "extraction_method": "nav_public_page",
        },
        "retrieved_at": retrieved_at,
        "content_sha256": payload_hash,
    }


def parse_feed_page(page: dict[str, Any], *, include_inactive: bool = False) -> tuple[list[dict[str, Any]], str | None]:
    items = []
    for item in page.get("items") or []:
        entry = item.get("_feed_entry") or {}
        if item.get("url") and (include_inactive or entry.get("status") == "ACTIVE"):
            modified = entry.get("sistEndret") or entry.get("date_modified") or item.get("sistEndret") or item.get("date_modified")
            parsed = {
                "uuid": entry.get("uuid") or item.get("id"),
                "url": item["url"],
                "status": entry.get("status") or item.get("status"),
            }
            if modified:
                parsed["sistEndret"] = modified
            if entry.get("date_modified") or item.get("date_modified"):
                parsed["date_modified"] = entry.get("date_modified") or item.get("date_modified")
            items.append(parsed)
    return items, page.get("next_url")


def _entry_time(entry: dict[str, Any]) -> str:
    return str(entry.get("sistEndret") or entry.get("date_modified") or "")


def dedupe_feed_entries(entries: Iterator[dict[str, Any]] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the newest change for each ad UUID, including inactive changes."""
    latest: dict[str, dict[str, Any]] = {}
    for entry in entries:
        uuid = str(entry.get("uuid") or "")
        if not uuid:
            continue
        previous = latest.get(uuid)
        if previous is None or _entry_time(entry) >= _entry_time(previous):
            latest[uuid] = entry
    return sorted(latest.values(), key=lambda item: (str(item.get("sistEndret") or ""), str(item.get("uuid") or "")))


def parse_ad(detail: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the employer facts from one active ad. Inactive or masked ads return None."""
    ad = detail.get("ad_content")
    if detail.get("status") != "ACTIVE" or not isinstance(ad, dict):
        return None
    employer = ad.get("employer") or {}
    org = digits_only(employer.get("orgnr"))
    if not is_valid_org_number(org):
        return None
    emails = [str(contact.get("email") or "") for contact in ad.get("contactList") or []]
    return {
        "organisation_number": org,
        "employer_name": employer.get("name"),
        "homepage": (employer.get("homepage") or "").strip() or None,
        "contact_email_domains": sorted({email.rsplit("@", 1)[1].casefold() for email in emails if "@" in email}),
        "ad": {
            "uuid": ad.get("uuid"),
            "title": ad.get("title"),
            "published": ad.get("published"),
            "expires": ad.get("expires"),
            "link": ad.get("link"),
            "location": ad.get("location") or ad.get("workLocations") or ad.get("work_locations"),
            "application_url": (ad.get("applicationUrl") or "").strip() or None,
            "source": ad.get("source"),
            "source_url": f"https://arbeidsplassen.nav.no/stillinger/stilling/{ad.get('uuid')}" if ad.get("uuid") else None,
            "sistEndret": ad.get("sistEndret") or detail.get("sistEndret"),
        },
    }


def _api_rejection_reason(
    detail: Any,
    *,
    now: datetime,
    organisation_number: str,
    sub_unit_organisation_number: str | None = None,
) -> str:
    if not isinstance(detail, dict):
        return "payload_missing"
    ad = detail.get("ad_content")
    if not isinstance(ad, dict):
        return "payload_missing"
    if str(detail.get("status") or "").upper() != "ACTIVE":
        return "inactive"
    expiry = _parse_nav_timestamp(ad.get("expires"))
    if expiry and expiry <= now:
        return "expired"
    employer = ad.get("employer") or {}
    actual_org = digits_only(employer.get("orgnr"))
    allowed_orgs = {digits_only(organisation_number)}
    if sub_unit_organisation_number:
        allowed_orgs.add(digits_only(sub_unit_organisation_number))
    if actual_org not in allowed_orgs or not is_valid_org_number(actual_org):
        return "organisation_mismatch"
    return "payload_missing" if not str(ad.get("title") or "").strip() or not ad.get("uuid") else ""


def _fold(value: Any) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", str(value or "").casefold()) if token not in {"as", "asa", "og", "the"} and len(token) > 1}


def employer_name_matches(business_name: str, profile: dict[str, Any]) -> bool:
    target = _fold(profile.get("name"))
    aliases = set()
    website = (profile.get("evidence", {}).get("website", {}).get("value") or {})
    aliases |= _fold(website.get("title"))
    candidate = _fold(business_name)
    return bool(candidate and target and (target <= candidate or candidate <= target or aliases & candidate))


def canonical_job_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    ad = parsed.get("ad") or {}
    return {
        "uuid": ad.get("uuid"), "title": ad.get("title"), "published": ad.get("published"),
        "expires": ad.get("expires"), "link": ad.get("link"), "application_url": ad.get("application_url"),
        "source": ad.get("source"), "location": ad.get("location"), "employer_name": parsed.get("employer_name"),
        "source_url": ad.get("source_url"),
        "organisation_number": parsed.get("organisation_number"),
        "employer_organisation_number": parsed.get("employer_organisation_number") or parsed.get("organisation_number"),
    }


def build_job_observation(
    profile: dict[str, Any],
    parsed: dict[str, Any],
    *,
    now: datetime | None = None,
    policy_path: str | Path = "config/connector-policy.json",
    live_retrieved_at: str | None = None,
    live_content_sha256: str | None = None,
    index_built_at: str | None = None,
    feed_source_url: str = FEED_SOURCE_URL,
    extraction_method: str = "nav_live_api",
) -> dict[str, Any] | None:
    """Convert an exact-org NAV ad to a privacy-safe observation."""
    org = digits_only(profile.get("organisation_number"))
    if not parsed or parsed.get("organisation_number") != org:
        return None
    ad = parsed.get("ad") or {}
    expires = str(ad.get("expires") or "")
    if expires:
        try:
            expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            expiry = expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)
            if expiry <= (now or datetime.now(timezone.utc)):
                return None
        except ValueError:
            pass
    link = str(ad.get("link") or "")
    source_url = str(ad.get("source_url") or (f"https://arbeidsplassen.nav.no/stillinger/stilling/{ad.get('uuid')}" if ad.get("uuid") else ""))
    if not link or not source_url:
        return None
    policy = connector_policy_entry(CONNECTOR_ID, platform=PLATFORM, acquisition_mode="official_api", path=policy_path)
    payload = canonical_job_payload(parsed)
    retrieved_at = live_retrieved_at or (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    return {
        "id": observation_id(CONNECTOR_ID, org, link, "job_posting"),
        "organisation_number": org,
        "platform": PLATFORM,
        "signal_type": "job_posting",
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "content_sha256": live_content_sha256 or hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
        "live_verified": bool(live_retrieved_at and live_content_sha256),
        "live_retrieved_at": live_retrieved_at,
        "live_content_sha256": live_content_sha256,
        "extraction_method": extraction_method,
        "index_built_at": index_built_at,
        "feed_source_url": feed_source_url,
        "exact_entity": True,
        "identity_proof": [
            {"type": "nav_feed_employer_orgnr", "organisation_number": org, "employer_name": parsed.get("employer_name")},
            {"type": "registry_name_similarity", "score": round(len(_fold(parsed.get("employer_name")) & _fold(profile.get("name"))) / max(1, len(_fold(profile.get("name")))), 3)},
        ],
        "acquisition_mode": "official_api",
        "rights_status": policy.get("rights_status", "review_required"),
        "connector_id": CONNECTOR_ID,
        "source_class": "official_job_feed",
        "strategy": "jobs_feed_discovery",
        "metrics": {
            "published": ad.get("published"), "expires": ad.get("expires"),
            "extent": ad.get("extent"), "occupation_categories": ad.get("occupationCategories") or ad.get("occupation_categories"),
        },
        "index_window_days": 90,
    }


def _index_complete(index_meta: dict[str, Any] | None, explicit: Any, *, now: datetime) -> bool:
    if not (explicit or (index_meta or {}).get("complete")):
        return False
    built_at = (index_meta or {}).get("built_at")
    if not built_at:
        return True
    try:
        datetime.fromisoformat(str(built_at).replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _align_nav_parsed(profile: dict[str, Any], parsed: dict[str, Any], indexed_ad: dict[str, Any]) -> dict[str, Any] | None:
    """Accept a parent or indexed sub-unit employer, publishing under the parent."""
    parent_org = digits_only(profile.get("organisation_number"))
    employer_org = digits_only(parsed.get("organisation_number"))
    allowed = {parent_org}
    sub_unit = digits_only(indexed_ad.get("sub_unit_organisation_number"))
    if sub_unit:
        allowed.add(sub_unit)
    if employer_org not in allowed:
        return None
    aligned = dict(parsed)
    aligned["employer_organisation_number"] = employer_org
    aligned["organisation_number"] = parent_org
    return aligned


def collect(profile: dict[str, Any], *, now: datetime, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Target the NAV feed at one company and return the shared connector contract."""
    context = context or {}
    started = time.monotonic()
    rejection_reasons = {reason: 0 for reason in LIVE_REJECTION_REASONS}
    operations = {"requests": 0, "third_party_cost_usd": 0.0, "latency_ms": [], "detail_timeouts": 0, "detail_errors": 0, "circuit_breaker_tripped": False, "public_page_attempts": 0, "public_page_failures": 0, "public_page_rejections": 0, "live_rejection_reasons": rejection_reasons}
    policy_path = context.get("policy_path", "config/connector-policy.json")
    shared_client = context.get("client")
    shared_requests_before = int(getattr(shared_client, "requests", 0)) if shared_client is not None else 0
    parsed_ads: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    live_ads: list[dict[str, Any]] = []
    live_failures = 0
    live_rejections = 0
    live_unverified = 0

    def record_rejection(reason: str | None) -> None:
        if reason in rejection_reasons:
            rejection_reasons[reason] += 1

    try:
        if context.get("ads") is not None:
            parsed_ads = [item for item in context.get("ads") or [] if isinstance(item, dict)]
        elif "index" in context:
            indexed_ads = list((context.get("index") or {}).get(digits_only(profile.get("organisation_number")), {}).get("ads", []))
            client = context.get("client")
            max_ads = max(0, int(context.get("max_ads", MAX_LIVE_ADS_PER_COMPANY)))
            live_unverified = max(0, len(indexed_ads) - max_ads)
            public_page_fetcher = context.get("public_page_fetcher")
            for indexed_ad in indexed_ads[:max_ads]:
                detail_url = str(indexed_ad.get("link") or indexed_ad.get("source_url") or "")
                if not detail_url:
                    live_failures += 1
                    continue

                def publish(parsed: dict[str, Any], retrieved_at: str, content_sha256: str, extraction_method: str) -> bool:
                    aligned = _align_nav_parsed(profile, parsed, indexed_ad)
                    if not aligned:
                        return False
                    observation = build_job_observation(
                        profile,
                        aligned,
                        now=now,
                        policy_path=context.get("policy_path", "config/connector-policy.json"),
                        live_retrieved_at=retrieved_at,
                        live_content_sha256=content_sha256,
                        index_built_at=(context.get("index_meta") or {}).get("built_at"),
                        extraction_method=extraction_method,
                    )
                    if not observation:
                        return False
                    ad = dict(aligned["ad"])
                    ad.update({
                        "live_verified": True,
                        "live_retrieved_at": retrieved_at,
                        "live_content_sha256": content_sha256,
                        "extraction_method": extraction_method,
                        "index_built_at": (context.get("index_meta") or {}).get("built_at"),
                        "feed_source_url": FEED_SOURCE_URL,
                    })
                    live_ads.append(ad)
                    parsed_ads.append(aligned)
                    observations.append(observation)
                    return True

                api_failed = client is None
                if client is not None:
                    try:
                        detail = client.get_json(detail_url)
                        live_retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                        live_content_sha256 = hashlib.sha256(json.dumps(detail, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()
                        parsed = parse_ad(detail)
                        if parsed is None:
                            record_rejection(_api_rejection_reason(
                                detail,
                                now=now,
                                organisation_number=str(profile.get("organisation_number") or ""),
                                sub_unit_organisation_number=str(indexed_ad.get("sub_unit_organisation_number") or "") or None,
                            ))
                            live_rejections += 1
                            continue
                        if publish(parsed, live_retrieved_at, live_content_sha256, "nav_live_api"):
                            continue
                        record_rejection(_api_rejection_reason(
                            detail,
                            now=now,
                            organisation_number=str(profile.get("organisation_number") or ""),
                            sub_unit_organisation_number=str(indexed_ad.get("sub_unit_organisation_number") or "") or None,
                        ))
                        live_rejections += 1
                        continue
                    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
                        api_failed = True
                if not api_failed:
                    continue
                operations["public_page_attempts"] += 1
                verification = verify_public_page(
                    detail_url,
                    organisation_number=str(profile.get("organisation_number") or ""),
                    sub_unit_organisation_number=str(indexed_ad.get("sub_unit_organisation_number") or "") or None,
                    now=now,
                    timeout=LIVE_DETAIL_TIMEOUT,
                    fetcher=public_page_fetcher,
                )
                if not verification.get("ok"):
                    reason = verification.get("reason")
                    record_rejection(reason)
                    if reason in {"fetch_failed", "payload_missing"}:
                        live_failures += 1
                        operations["public_page_failures"] += 1
                    else:
                        live_rejections += 1
                        operations["public_page_rejections"] += 1
                    continue
                if not publish(verification["parsed"], verification["retrieved_at"], verification["content_sha256"], "nav_public_page"):
                    live_rejections += 1
        else:
            client = context.get("client") or NavFeedClient(token=context.get("token"), min_interval=float(context.get("min_interval", 1.0)))
            cache_path = Path(context.get("cache_path", "out/nav-details-cache.json"))
            cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
            entries = [entry for entry in iter_active_entries(client, since_http_date=context.get("since_http_date"), max_pages=context.get("max_pages")) if employer_name_matches(entry.get("business_name", ""), profile)]
            operations["requests"] = getattr(client, "requests", 0)
            consecutive_failures = 0
            for entry in entries[:MAX_LIVE_ADS_PER_COMPANY]:
                uuid = str(entry.get("uuid") or "")
                detail = None
                try:
                    live_payload = client.get_json(entry["url"])
                    live_retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    live_content_sha256 = hashlib.sha256(json.dumps(live_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()
                    detail = parse_ad(live_payload)
                    consecutive_failures = 0
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as exc:
                    operations["detail_errors"] += 1
                    consecutive_failures += 1
                    if isinstance(exc, TimeoutError) or "timed out" in str(exc).casefold():
                        operations["detail_timeouts"] += 1
                    detail = None
                    if consecutive_failures >= 5:
                        operations["circuit_breaker_tripped"] = True
                        break
                if detail:
                    detail["_live_retrieved_at"] = live_retrieved_at
                    detail["_live_content_sha256"] = live_content_sha256
                    safe_detail = dict(detail)
                    safe_detail.pop("contact_email_domains", None)
                    cache[uuid] = {"orgnr": detail.get("organisation_number"), "sistEndret": entry.get("sistEndret"), "parsed": safe_detail}
                if detail:
                    parsed_ads.append(detail)
            if context.get("cache_path"):
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            operations["requests"] = getattr(client, "requests", operations["requests"])
            operations["detail_timeouts"] = max(operations["detail_timeouts"], getattr(client, "timeouts", 0))
        if "index" not in context:
            observations = [build_job_observation(profile, item, now=now, policy_path=policy_path, live_retrieved_at=item.get("_live_retrieved_at"), live_content_sha256=item.get("_live_content_sha256"), index_built_at=(context.get("index_meta") or {}).get("built_at")) for item in parsed_ads]
            observations = [item for item in observations if item]
            live_ads = []
            for item in parsed_ads:
                if not item.get("ad"):
                    continue
                ad = dict(item["ad"])
                ad.update({
                    "live_verified": bool(item.get("_live_retrieved_at") and item.get("_live_content_sha256")),
                    "live_retrieved_at": item.get("_live_retrieved_at"),
                    "live_content_sha256": item.get("_live_content_sha256"),
                    "index_built_at": (context.get("index_meta") or {}).get("built_at"),
                    "feed_source_url": FEED_SOURCE_URL,
                })
                live_ads.append(ad)
        operations["latency_ms"] = [round((time.monotonic() - started) * 1000)]
        complete = _index_complete(context.get("index_meta"), context.get("index_complete"), now=now)
        built_at = (context.get("index_meta") or {}).get("built_at")
        stale = False
        if built_at:
            try:
                parsed_built_at = datetime.fromisoformat(str(built_at).replace("Z", "+00:00"))
                parsed_built_at = parsed_built_at if parsed_built_at.tzinfo else parsed_built_at.replace(tzinfo=timezone.utc)
                stale = now - parsed_built_at > timedelta(days=14)
            except ValueError:
                stale = True
        operations["index_built_at"] = built_at
        operations["index_stale"] = stale
        operations["live_verified_ads"] = len(live_ads)
        operations["live_verification_failures"] = live_failures
        operations["live_rejections"] = live_rejections
        operations["live_unverified_ads"] = live_unverified
        operations["live_rejection_reasons"] = dict(rejection_reasons)
        if shared_client is not None:
            operations["requests"] = max(0, int(getattr(shared_client, "requests", 0)) - shared_requests_before)
        status = "not_checked" if live_failures or live_unverified else "available" if observations else "not_available" if complete or context.get("ads") is not None else "failed"
        note = "Active exact-org NAV jobs were live-verified against NAV detail responses." if observations and status == "available" else "NAV live verification failed; no unverified ad was published." if live_failures or live_unverified else f"No active exact-organisation NAV ad in the complete index built at {built_at}." if complete else "NAV index was not complete; absence is unchecked."
        return {
            "status": status,
            "observations": observations,
            "ads": live_ads,
            "operations": operations,
            "note": note,
        }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError) as exc:
        operations["latency_ms"] = [round((time.monotonic() - started) * 1000)]
        return {"status": "failed", "observations": [], "operations": operations, "note": f"NAV feed failure: {type(exc).__name__}"}


def merge_ad(index: dict[str, dict[str, Any]], parsed: dict[str, Any]) -> None:
    org = parsed["organisation_number"]
    entry = index.setdefault(org, {"organisation_number": org, "employer_name": parsed["employer_name"], "homepages": [], "contact_email_domains": [], "ads": []})
    if parsed["homepage"] and parsed["homepage"] not in entry["homepages"]:
        entry["homepages"].append(parsed["homepage"])
    entry["contact_email_domains"] = sorted(set(entry["contact_email_domains"]) | set(parsed["contact_email_domains"]))
    incoming = parsed["ad"]
    existing = next((ad for ad in entry["ads"] if ad.get("uuid") == incoming.get("uuid")), None)
    if existing is None:
        entry["ads"].append(incoming)
    elif str(incoming.get("sistEndret") or "") >= str(existing.get("sistEndret") or ""):
        entry["ads"] = [incoming if ad is existing else ad for ad in entry["ads"]]


class NavFeedClient:
    def __init__(self, token: str | None = None, *, timeout: float = 20.0, min_interval: float = 0.125, retries: int = 2, backoff: float = 0.5) -> None:
        self.token = token
        self.timeout = timeout
        self.min_interval = min_interval
        self.retries = retries
        self.backoff = backoff
        self.requests = 0
        self.timeouts = 0
        self._pace_lock = threading.Condition()
        self._token_lock = threading.Lock()
        self._token_failure: Exception | None = None
        self._last_started = 0.0

    def _get(self, url: str, headers: dict[str, str] | None = None, *, retries: int | None = None) -> str:
        absolute = urllib.parse.urljoin(FEED_ORIGIN, url)
        assert_public_url(absolute)
        request = urllib.request.Request(absolute, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
        last_error: Exception | None = None
        attempts = self.retries if retries is None else max(0, int(retries))
        for attempt in range(attempts + 1):
            self.requests += 1
            try:
                with self._pace_lock:
                    wait = self.min_interval - (time.monotonic() - self._last_started)
                    if wait > 0:
                        self._pace_lock.wait(wait)
                    self._last_started = time.monotonic()
                alarm_active = threading.current_thread() is threading.main_thread()
                previous_handler = previous_timer = None
                if alarm_active:
                    previous_handler = signal.getsignal(signal.SIGALRM)
                    def deadline(_signum: int, _frame: Any) -> None:
                        raise TimeoutError("NAV request deadline exceeded")
                    signal.signal(signal.SIGALRM, deadline)
                    previous_timer = signal.setitimer(signal.ITIMER_REAL, self.timeout)
                try:
                    with SAFE_OPENER.open(request, timeout=self.timeout) as response:
                        socket_obj = getattr(getattr(response, "fp", None), "raw", None)
                        socket_obj = getattr(socket_obj, "_sock", None)
                        if socket_obj is not None:
                            socket_obj.settimeout(self.timeout)
                        chunks: list[bytes] = []
                        total = 0
                        while True:
                            chunk = response.read(min(64 * 1024, 20_000_000 - total))
                            if not chunk:
                                break
                            chunks.append(chunk)
                            total += len(chunk)
                            if total >= 20_000_000:
                                raise OSError("NAV response exceeded 20 MB limit")
                        body = b"".join(chunks).decode("utf-8")
                finally:
                    if alarm_active and previous_handler is not None and previous_timer is not None:
                        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
                        signal.signal(signal.SIGALRM, previous_handler)
                return body
            except urllib.error.HTTPError as exc:
                last_error = exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if attempt >= self.retries:
                    raise
                try:
                    delay = max(0.0, float(retry_after)) if retry_after else self.backoff * (2 ** attempt)
                except ValueError:
                    delay = self.backoff * (2 ** attempt)
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if isinstance(exc, TimeoutError) or "timed out" in str(exc).casefold():
                    self.timeouts += 1
                if attempt >= self.retries:
                    raise
                time.sleep(self.backoff * (2 ** attempt))
        raise last_error or RuntimeError("NAV request failed")

    def ensure_token(self) -> str:
        if not self.token:
            with self._token_lock:
                if not self.token:
                    if self._token_failure is not None:
                        raise self._token_failure
                    try:
                        token = parse_token(self._get(PUBLIC_TOKEN_URL, retries=0))
                        if not token:
                            raise ValueError("NAV public token response was empty")
                        self.token = token
                    except Exception as exc:  # noqa: BLE001 - circuit-break the dead public endpoint for this run
                        self._token_failure = exc
                        raise
        return self.token

    def get_json(self, path: str, headers: dict[str, str] | None = None) -> Any:
        target = path if str(path).startswith(("http://", "https://")) else FEED_ORIGIN + str(path)
        return json.loads(self._get(target, {"Authorization": f"Bearer {self.ensure_token()}", **(headers or {})}))


def iter_feed_entries(client: Any, *, since_http_date: str | None, max_pages: int | None = None) -> Iterator[dict[str, Any]]:
    path = "/api/v1/feed"
    headers = {"If-Modified-Since": since_http_date} if since_http_date else None
    pages = 0
    while max_pages is None or pages < max_pages:
        pages += 1
        items, next_url = parse_feed_page(client.get_json(path, headers), include_inactive=True)
        yield from items
        if not next_url or next_url == path:
            return
        path, headers = next_url, None


def iter_active_entries(client: Any, *, since_http_date: str | None, max_pages: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield active entries for callers that do not need change-log semantics."""
    yield from (item for item in iter_feed_entries(client, since_http_date=since_http_date, max_pages=max_pages) if item.get("status") == "ACTIVE")


class NavFeedBuildError(RuntimeError):
    """A feed build stopped before a complete index could be written."""


class NavFeedPageError(NavFeedBuildError):
    """A feed page could not be read after the configured attempts."""

    def __init__(self, path: str, cause: BaseException, attempts: int) -> None:
        self.path = path
        self.cause = cause
        self.attempts = attempts
        super().__init__(f"NAV feed page {path!r} failed after {attempts} attempt(s); resume cursor is {path!r}: {type(cause).__name__}: {cause}")


class NavBuildInterrupted(KeyboardInterrupt):
    """Raised by the builder's SIGINT/SIGTERM handlers after progress is staged."""


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
    temporary.replace(path)


def _write_index_atomic(path: Path, index: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for org in sorted(index):
            handle.write(json.dumps(index[org], ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
    temporary.replace(path)


def _feed_page_id(page: dict[str, Any]) -> str | None:
    for key in ("id", "page_id", "pageId", "feed_id", "feedId"):
        value = page.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _load_json_object(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _valid_feed_progress(value: dict[str, Any]) -> bool:
    """Accept only the cursor-based format; older detail-URL progress is ignored."""
    if not isinstance(value, dict) or "cursor" not in value:
        return False
    source = value.get("feed_source")
    if source and source != FEED_SOURCE_URL:
        return False
    return isinstance(value.get("entries", {}), dict)


def _latest_entry_map(entries: dict[str, Any]) -> dict[str, dict[str, Any]]:
    compact: dict[str, dict[str, Any]] = {}
    for uuid, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("uuid") or uuid)
        if not key or not entry.get("url"):
            continue
        compact[key] = {
            "uuid": key,
            "status": entry.get("status"),
            "url": entry.get("url"),
            **({"sistEndret": entry["sistEndret"]} if entry.get("sistEndret") else {}),
            **({"date_modified": entry["date_modified"]} if entry.get("date_modified") else {}),
        }
    return compact


def _merge_latest_entries(latest: dict[str, dict[str, Any]], entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        uuid = str(entry.get("uuid") or "")
        if not uuid:
            continue
        previous = latest.get(uuid)
        if previous is None or _entry_time(entry) >= _entry_time(previous):
            latest[uuid] = {
                "uuid": uuid,
                "status": entry.get("status"),
                "url": entry.get("url"),
                **({"sistEndret": entry["sistEndret"]} if entry.get("sistEndret") else {}),
                **({"date_modified": entry["date_modified"]} if entry.get("date_modified") else {}),
            }


def build_index(
    client: Any,
    *,
    since_http_date: str | None = None,
    max_pages: int | None = None,
    max_details: int | None = None,
    index: dict[str, dict[str, Any]] | None = None,
    on_error: Callable[[str, Exception], None] | None = None,
    max_workers: int = 4,
    progress_path: str | Path | None = None,
    index_path: str | Path | None = None,
    flush_every: int = 500,
    progress_every: int = 25,
    page_attempts: int = 6,
    page_backoff: float = 1.0,
    page_jitter: float = 0.25,
    max_age_days: float | None = None,
    stats: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch a de-duplicated active-ad index with cursor-based progress.

    Feed pages are reduced as they arrive.  A failed or interrupted build only
    updates the progress file; ``index_path`` is replaced after a complete run.
    """
    input_index = index if index is not None else {}
    working_index = copy.deepcopy(input_index)
    progress_file = Path(progress_path) if progress_path else None
    loaded_progress = _load_json_object(progress_file)
    valid_progress = _valid_feed_progress(loaded_progress)
    resuming = valid_progress and loaded_progress.get("phase") != "complete" and (
        loaded_progress.get("cursor") is not None or loaded_progress.get("phase") == "details"
    )
    progress: dict[str, Any] = dict(loaded_progress) if valid_progress else {}
    latest: dict[str, dict[str, Any]] = _latest_entry_map(progress.get("entries") or {}) if resuming else {}
    path = str(progress.get("cursor")) if resuming and progress.get("cursor") else "/api/v1/feed"
    pages_fetched = int(progress.get("pages_fetched") or 0) if resuming else 0
    first_page_id = str(progress.get("first_page_id")) if progress.get("first_page_id") else None
    last_page_id = str(progress.get("last_page_id")) if progress.get("last_page_id") else None
    feed_entries_seen = int(progress.get("feed_entries_seen") or 0) if resuming else 0
    run_stats = stats if stats is not None else {}
    run_stats.setdefault("detail_requests", 0)
    run_stats.setdefault("detail_timeouts", 0)
    run_stats.setdefault("detail_errors", 0)
    run_stats.setdefault("circuit_breaker_tripped", False)
    run_stats.setdefault("resumed_pages", pages_fetched if resuming else 0)
    run_stats.setdefault("flushes", 0)

    def save_progress() -> None:
        if not progress_file:
            return
        progress.update({
            "version": 2,
            "feed_source": FEED_SOURCE_URL,
            "cursor": path,
            "entries": latest,
            "pages_fetched": pages_fetched,
            "first_page_id": first_page_id,
            "last_page_id": last_page_id,
            "feed_entries_seen": feed_entries_seen,
        })
        _atomic_write_json(progress_file, progress)

    previous_handlers: dict[int, Any] = {}
    if threading.current_thread() is threading.main_thread():
        def stop_handler(signum: int, _frame: Any) -> None:
            raise NavBuildInterrupted(f"NAV feed build interrupted by signal {signum}")

        for signal_number in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signal_number] = signal.getsignal(signal_number)
            signal.signal(signal_number, stop_handler)

    progress["phase"] = "feed" if path else "details"
    progress["complete"] = False
    save_progress()
    feed_exhausted = not bool(path)
    hit_detail_cap = False
    try:
        while path and (max_pages is None or pages_fetched - (int(progress.get("resumed_pages") or 0)) < max_pages):
            current_path = path
            progress["cursor"] = current_path
            # Step 2 adds retries around this single-page call.  Keeping the call
            # here, rather than materialising an iterator, makes the cursor durable.
            page = client.get_json(current_path, None)
            if not isinstance(page, dict):
                raise NavFeedBuildError(f"NAV feed page {current_path!r} was not a JSON object")
            items, next_url = parse_feed_page(page, include_inactive=True)
            _merge_latest_entries(latest, items)
            page_id = _feed_page_id(page)
            if first_page_id is None:
                first_page_id = page_id
            last_page_id = page_id
            pages_fetched += 1
            feed_entries_seen += len(items)
            path = str(next_url) if next_url and str(next_url) != current_path else None
            progress["cursor"] = path
            if progress_every > 0 and pages_fetched % progress_every == 0:
                save_progress()
            if not path:
                feed_exhausted = True

        progress["phase"] = "details"
        progress["cursor"] = None
        save_progress()

        unique_entries = sorted(latest.values(), key=lambda item: (_entry_time(item), str(item.get("uuid") or "")))
        run_stats["unique_feed_uuids"] = len(unique_entries)
        run_stats["inactive_unique_uuids"] = sum(1 for entry in unique_entries if entry.get("status") != "ACTIVE")
        entries: list[dict[str, Any]] = []
        active_entries: list[dict[str, Any]] = []
        for entry in unique_entries:
            if entry.get("status") != "ACTIVE":
                uuid = str(entry.get("uuid") or "")
                for employer in working_index.values():
                    employer["ads"] = [ad for ad in employer.get("ads") or [] if str(ad.get("uuid")) != uuid]
            else:
                active_entries.append(entry)
        if max_details is not None and len(active_entries) > max_details:
            hit_detail_cap = True
            active_entries = active_entries[:max_details]
        entries.extend(active_entries)
        run_stats["detail_entries_submitted"] = len(entries)
        with ThreadPoolExecutor(max_workers=max(1, min(16, max_workers))) as pool:
            futures = {pool.submit(client.get_json, entry["url"]): entry for entry in entries}
            for future in as_completed(futures):
                entry = futures[future]
                url = str(entry.get("url") or "")
                run_stats["detail_requests"] += 1
                try:
                    parsed = parse_ad(future.result())
                    if parsed:
                        merge_ad(working_index, parsed)
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError) as exc:
                    run_stats["detail_errors"] += 1
                    if isinstance(exc, TimeoutError) or "timed out" in str(exc).casefold():
                        run_stats["detail_timeouts"] += 1
                    progress.setdefault("errors", []).append({"url": url, "kind": type(exc).__name__})
                    if on_error:
                        on_error(url, exc)
                processed = int(run_stats.get("details_processed") or 0) + 1
                run_stats["details_processed"] = processed
                progress["details_processed"] = processed
                if flush_every > 0 and processed % flush_every == 0:
                    save_progress()

        run_stats["feed_entries_seen"] = feed_entries_seen
        run_stats["pages_fetched"] = pages_fetched
        run_stats["feed_cursor"] = {"next_url": path, "id": last_page_id, "first_page_id": first_page_id}
        run_stats["complete"] = feed_exhausted and not hit_detail_cap and not bool(run_stats.get("circuit_breaker_tripped"))
        run_stats["distinct_employers"] = len(working_index)
        run_stats["distinct_ads"] = sum(len(item.get("ads") or []) for item in working_index.values())
        if run_stats["complete"]:
            if index_path:
                _write_index_atomic(Path(index_path), working_index)
                run_stats["flushes"] += 1
            progress["phase"] = "complete"
            progress["complete"] = True
            progress["cursor"] = None
        save_progress()
    finally:
        run_stats.setdefault("complete", False)
        run_stats["feed_entries_seen"] = feed_entries_seen
        run_stats["pages_fetched"] = pages_fetched
        for signal_number, handler in previous_handlers.items():
            signal.signal(signal_number, handler)
        # This is intentionally after handler restoration: a signal received while
        # writing progress cannot leave the process with our temporary handlers.
        save_progress()

    if run_stats.get("complete") and index is not None:
        index.clear()
        index.update(working_index)
    return working_index
