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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ..core.orgnumber import digits_only, is_valid_org_number
from .external_footprint import connector_policy_entry, observation_id
from ..web.website import SAFE_OPENER, assert_public_url

CONNECTOR_ID = "nav_jobs"
PLATFORM = "job_board"

FEED_ORIGIN = "https://pam-stilling-feed.nav.no"
USER_AGENT = "builderr-signalpost-poc/0.1 (+https://builderr.ai)"
# The experiment token is published for anyone; a private token is requested from NAV.
PUBLIC_TOKEN_URL = f"{FEED_ORIGIN}/api/publicToken"


def load_index(path: Any) -> dict[str, dict[str, Any]]:
    """Read the employer index JSONL written by the NAV connector (empty if missing)."""
    from pathlib import Path

    file = Path(path)
    if not file.exists():
        return {}
    rows = (json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip())
    return {row["organisation_number"]: row for row in rows}


def parse_token(text: str) -> str:
    """The public-token endpoint returns a sentence followed by the JWT on its own line."""
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    return lines[-1] if lines else ""


def parse_feed_page(page: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    items = []
    for item in page.get("items") or []:
        entry = item.get("_feed_entry") or {}
        if item.get("url") and entry.get("status") == "ACTIVE":
            items.append({
                "uuid": entry.get("uuid") or item.get("id"),
                "url": item["url"],
                "business_name": entry.get("businessName"),
                "municipal": entry.get("municipal"),
                "sistEndret": entry.get("sistEndret") or item.get("sistEndret"),
            })
    return items, page.get("next_url")


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
        "organisation_number": parsed.get("organisation_number"),
    }


def build_job_observation(
    profile: dict[str, Any],
    parsed: dict[str, Any],
    *,
    now: datetime | None = None,
    policy_path: str | Path = "config/connector-policy.json",
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
    if not link:
        return None
    policy = connector_policy_entry(CONNECTOR_ID, platform=PLATFORM, acquisition_mode="official_api", path=policy_path)
    payload = canonical_job_payload(parsed)
    return {
        "id": observation_id(CONNECTOR_ID, org, link, "job_posting"),
        "organisation_number": org,
        "platform": PLATFORM,
        "signal_type": "job_posting",
        "source_url": link,
        "retrieved_at": (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"),
        "content_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
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


def collect(profile: dict[str, Any], *, now: datetime, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Target the NAV feed at one company and return the shared connector contract."""
    context = context or {}
    started = time.monotonic()
    operations = {"requests": 0, "third_party_cost_usd": 0.0, "latency_ms": [], "detail_timeouts": 0, "detail_errors": 0, "circuit_breaker_tripped": False}
    policy_path = context.get("policy_path", "config/connector-policy.json")
    parsed_ads: list[dict[str, Any]] = []
    try:
        if context.get("ads") is not None:
            parsed_ads = [item for item in context.get("ads") or [] if isinstance(item, dict)]
        elif "index" in context:
            parsed_ads = []
            for entry in (context.get("index") or {}).get(digits_only(profile.get("organisation_number")), {}).get("ads", []):
                parsed_ads.append({"organisation_number": digits_only(profile.get("organisation_number")), "employer_name": profile.get("name"), "ad": entry})
        else:
            client = context.get("client") or NavFeedClient(token=context.get("token"), min_interval=float(context.get("min_interval", 1.0)))
            cache_path = Path(context.get("cache_path", "out/nav-details-cache.json"))
            cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
            entries = [entry for entry in iter_active_entries(client, since_http_date=context.get("since_http_date"), max_pages=context.get("max_pages")) if employer_name_matches(entry.get("business_name", ""), profile)]
            operations["requests"] = getattr(client, "requests", 0)
            consecutive_failures = 0
            for entry in entries:
                uuid = str(entry.get("uuid") or "")
                cached = cache.get(uuid)
                detail = None
                if cached and cached.get("sistEndret") == entry.get("sistEndret") and cached.get("parsed"):
                    detail = cached["parsed"]
                else:
                    try:
                        detail = parse_ad(client.get_json(entry["url"]))
                        consecutive_failures = 0
                    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError) as exc:
                        operations["detail_errors"] += 1
                        consecutive_failures += 1
                        if isinstance(exc, TimeoutError) or "timed out" in str(exc).casefold():
                            operations["detail_timeouts"] += 1
                        detail = None
                        if consecutive_failures >= 5:
                            operations["circuit_breaker_tripped"] = True
                            break
                    if detail:
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
        observations = [build_job_observation(profile, item, now=now, policy_path=policy_path) for item in parsed_ads]
        observations = [item for item in observations if item]
        operations["latency_ms"] = [round((time.monotonic() - started) * 1000)]
        complete = bool(context.get("index_complete") or (context.get("index_meta") or {}).get("complete"))
        status = "available" if observations else "not_available" if complete or context.get("ads") is not None else "failed"
        return {
            "status": status,
            "observations": observations,
            "operations": operations,
            "note": "Active exact-org jobs only; contact data and free-text descriptions are removed." if observations else "No active exact-organisation NAV ad in the complete index." if complete else "NAV index was not complete; absence is unchecked.",
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
    if all(ad["uuid"] != parsed["ad"]["uuid"] for ad in entry["ads"]):
        entry["ads"].append(parsed["ad"])


class NavFeedClient:
    def __init__(self, token: str | None = None, *, timeout: float = 20.0, min_interval: float = 1.0, retries: int = 2, backoff: float = 0.5) -> None:
        self.token = token
        self.timeout = timeout
        self.min_interval = min_interval
        self.retries = retries
        self.backoff = backoff
        self.requests = 0
        self.timeouts = 0
        self._pace_lock = threading.Condition()
        self._last_started = 0.0

    def _get(self, url: str, headers: dict[str, str] | None = None) -> str:
        absolute = urllib.parse.urljoin(FEED_ORIGIN, url)
        assert_public_url(absolute)
        request = urllib.request.Request(absolute, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
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
            self.token = parse_token(self._get(PUBLIC_TOKEN_URL))
        return self.token

    def get_json(self, path: str, headers: dict[str, str] | None = None) -> Any:
        target = path if str(path).startswith(("http://", "https://")) else FEED_ORIGIN + str(path)
        return json.loads(self._get(target, {"Authorization": f"Bearer {self.ensure_token()}", **(headers or {})}))


def iter_active_entries(client: Any, *, since_http_date: str | None, max_pages: int | None = None) -> Iterator[dict[str, Any]]:
    path = "/api/v1/feed"
    headers = {"If-Modified-Since": since_http_date} if since_http_date else None
    pages = 0
    while max_pages is None or pages < max_pages:
        pages += 1
        items, next_url = parse_feed_page(client.get_json(path, headers))
        yield from items
        if not next_url or next_url == path:
            return
        path, headers = next_url, None


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
    stats: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch active ads (newest window first) and fold their employer facts into an organisation-number index."""
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

    def save_progress() -> None:
        if not progress_file:
            return
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text(json.dumps(progress, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    entries: list[dict[str, Any]] = []
    for entry in iter_active_entries(client, since_http_date=since_http_date, max_pages=max_pages):
        if max_details is not None and fetched >= max_details:
            break
        fetched += 1
        if entry.get("url") not in completed:
            entries.append(entry)
    consecutive_failures = 0
    with ThreadPoolExecutor(max_workers=max(1, min(4, max_workers))) as pool:
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
                    break
            save_progress()
    run_stats["feed_entries_seen"] = fetched
    run_stats["detail_entries_submitted"] = len(entries)
    save_progress()
    run_stats["complete"] = not bool(run_stats.get("circuit_breaker_tripped")) and max_pages is None and max_details is None
    run_stats["distinct_employers"] = len(index)
    run_stats["distinct_ads"] = sum(len(item.get("ads") or []) for item in index.values())
    return index
