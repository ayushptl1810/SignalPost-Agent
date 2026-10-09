from __future__ import annotations

import json
import hashlib
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


def parse_public_page(html: str | bytes) -> dict[str, Any] | None:
    """Extract the public NAV ad record embedded in a Next.js page."""
    for payload in _next_payloads(html):
        node = _public_ad_node(payload)
        if not node:
            continue
        employer = node.get("employer") or {}
        return {
            "uuid": node.get("uuid") or node.get("id"),
            "title": node.get("title") or node.get("jobTitle") or node.get("name"),
            "status": node.get("status"),
            "published": node.get("published") or node.get("datePosted"),
            "expires": node.get("expires") or node.get("validThrough"),
            "employer": {"orgnr": employer.get("orgnr"), "name": employer.get("name"), "homepage": employer.get("homepage")},
            "location": node.get("location") or node.get("workLocations") or node.get("work_locations"),
            "applicationUrl": node.get("applicationUrl") or node.get("application_url"),
            "source": node.get("source"),
            "_extracted_payload": node,
        }
    return None


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
        extracted = parse_public_page(html)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "fetch_failed", "error": type(exc).__name__, "retrieved_at": retrieved_at}
    if not extracted:
        return {"ok": False, "reason": "payload_missing", "retrieved_at": retrieved_at}
    status = str(extracted.get("status") or "").upper()
    if status != "ACTIVE":
        return {"ok": False, "reason": "inactive", "retrieved_at": retrieved_at}
    expires = _parse_nav_timestamp(extracted.get("expires"))
    if expires and expires <= (now or datetime.now(timezone.utc)):
        return {"ok": False, "reason": "expired", "retrieved_at": retrieved_at}
    title = str(extracted.get("title") or "").strip()
    if not title:
        return {"ok": False, "reason": "missing_title", "retrieved_at": retrieved_at}
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
            items.append({
                "uuid": entry.get("uuid") or item.get("id"),
                "url": item["url"],
                "status": entry.get("status") or item.get("status"),
                "business_name": entry.get("businessName"),
                "municipal": entry.get("municipal"),
                "sistEndret": entry.get("sistEndret") or item.get("sistEndret"),
            })
    return items, page.get("next_url")


def _entry_time(entry: dict[str, Any]) -> str:
    return str(entry.get("sistEndret") or "")


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
    operations = {"requests": 0, "third_party_cost_usd": 0.0, "latency_ms": [], "detail_timeouts": 0, "detail_errors": 0, "circuit_breaker_tripped": False, "public_page_attempts": 0, "public_page_failures": 0, "public_page_rejections": 0}
    policy_path = context.get("policy_path", "config/connector-policy.json")
    shared_client = context.get("client")
    shared_requests_before = int(getattr(shared_client, "requests", 0)) if shared_client is not None else 0
    parsed_ads: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    live_ads: list[dict[str, Any]] = []
    live_failures = 0
    live_rejections = 0
    live_unverified = 0
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
                            live_rejections += 1
                            continue
                        if publish(parsed, live_retrieved_at, live_content_sha256, "nav_live_api"):
                            continue
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


def build_index(
    client: Any,
    *,
    since_http_date: str | None,
    max_pages: int | None = None,
    max_details: int | None = None,
    index: dict[str, dict[str, Any]] | None = None,
    on_error: Callable[[str, Exception], None] | None = None,
    max_workers: int = 4,
    progress_path: str | Path | None = None,
    index_path: str | Path | None = None,
    flush_every: int = 500,
    stats: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch a de-duplicated active-ad index with crash-safe progress flushes."""
    index = index if index is not None else {}
    fetched = 0
    progress_file = Path(progress_path) if progress_path else None
    progress: dict[str, Any] = {}
    if progress_file and progress_file.exists():
        try:
            progress = json.loads(progress_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            progress = {}
    completed = set(progress.get("completed_urls") or [])
    run_stats = stats if stats is not None else {}
    run_stats.setdefault("detail_requests", 0)
    run_stats.setdefault("detail_timeouts", 0)
    run_stats.setdefault("detail_errors", 0)
    run_stats.setdefault("circuit_breaker_tripped", False)
    run_stats.setdefault("resumed_details", len(completed))
    run_stats.setdefault("flushes", 0)

    def save_progress() -> None:
        if not progress_file:
            return
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text(json.dumps(progress, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    def flush_index() -> None:
        if not index_path:
            return
        target = Path(index_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for org in sorted(index):
                handle.write(json.dumps(index[org], ensure_ascii=False, separators=(",", ":")) + "\n")
        temporary.replace(target)
        run_stats["flushes"] += 1

    all_entries = list(iter_feed_entries(client, since_http_date=since_http_date, max_pages=max_pages))
    fetched = len(all_entries)
    unique_entries = dedupe_feed_entries(all_entries)
    run_stats["unique_feed_uuids"] = len(unique_entries)
    run_stats["inactive_unique_uuids"] = sum(1 for entry in unique_entries if entry.get("status") != "ACTIVE")
    entries: list[dict[str, Any]] = []
    for entry in unique_entries:
        if entry.get("status") != "ACTIVE":
            # An inactive newest change retracts any previously indexed copy.
            uuid = str(entry.get("uuid") or "")
            for employer in index.values():
                employer["ads"] = [ad for ad in employer.get("ads") or [] if str(ad.get("uuid")) != uuid]
            continue
        if max_details is not None and len(entries) >= max_details:
            break
        if entry.get("url") not in completed:
            entries.append(entry)
    consecutive_failures = 0
    with ThreadPoolExecutor(max_workers=max(1, min(16, max_workers))) as pool:
        futures = {pool.submit(client.get_json, entry["url"]): entry for entry in entries}
        for future in as_completed(futures):
            entry = futures[future]
            url = str(entry.get("url") or "")
            run_stats["detail_requests"] += 1
            try:
                parsed = parse_ad(future.result())
                consecutive_failures = 0
                if parsed:
                    merge_ad(index, parsed)
                progress.setdefault("completed_urls", []).append(url)
                completed.add(url)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError) as exc:
                consecutive_failures += 1
                run_stats["detail_errors"] += 1
                if isinstance(exc, TimeoutError) or "timed out" in str(exc).casefold():
                    run_stats["detail_timeouts"] += 1
                progress.setdefault("errors", []).append({"url": url, "kind": type(exc).__name__})
                if on_error:
                    on_error(url, exc)
                if consecutive_failures >= 5:
                    run_stats["circuit_breaker_tripped"] = True
                    progress["circuit_breaker_tripped"] = True
                    save_progress()
                    flush_index()
                    break
            processed = int(run_stats.get("details_processed") or 0) + 1
            run_stats["details_processed"] = processed
            if flush_every > 0 and processed % flush_every == 0:
                save_progress()
                flush_index()
    run_stats["feed_entries_seen"] = fetched
    run_stats["detail_entries_submitted"] = len(entries)
    save_progress()
    flush_index()
    run_stats["complete"] = not bool(run_stats.get("circuit_breaker_tripped")) and max_pages is None and max_details is None
    run_stats["distinct_employers"] = len(index)
    run_stats["distinct_ads"] = sum(len(item.get("ads") or []) for item in index.values())
    return index
