from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator

from ..core.orgnumber import digits_only, is_valid_org_number

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
            "application_url": (ad.get("applicationUrl") or "").strip() or None,
            "source": ad.get("source"),
        },
    }


def merge_ad(index: dict[str, dict[str, Any]], parsed: dict[str, Any]) -> None:
    org = parsed["organisation_number"]
    entry = index.setdefault(org, {"organisation_number": org, "employer_name": parsed["employer_name"], "homepages": [], "contact_email_domains": [], "ads": []})
    if parsed["homepage"] and parsed["homepage"] not in entry["homepages"]:
        entry["homepages"].append(parsed["homepage"])
    entry["contact_email_domains"] = sorted(set(entry["contact_email_domains"]) | set(parsed["contact_email_domains"]))
    if all(ad["uuid"] != parsed["ad"]["uuid"] for ad in entry["ads"]):
        entry["ads"].append(parsed["ad"])


class NavFeedClient:
    def __init__(self, token: str | None = None, *, timeout: float = 30.0, min_interval: float = 0.1) -> None:
        self.token = token
        self.timeout = timeout
        self.min_interval = min_interval
        self.requests = 0

    def _get(self, url: str, headers: dict[str, str] | None = None) -> str:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
        self.requests += 1
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8")
        time.sleep(self.min_interval)
        return body

    def ensure_token(self) -> str:
        if not self.token:
            self.token = parse_token(self._get(PUBLIC_TOKEN_URL))
        return self.token

    def get_json(self, path: str, headers: dict[str, str] | None = None) -> Any:
        return json.loads(self._get(FEED_ORIGIN + path, {"Authorization": f"Bearer {self.ensure_token()}", **(headers or {})}))


def iter_active_entries(client: Any, *, since_http_date: str | None, max_pages: int) -> Iterator[dict[str, Any]]:
    path = "/api/v1/feed"
    headers = {"If-Modified-Since": since_http_date} if since_http_date else None
    for _ in range(max_pages):
        items, next_url = parse_feed_page(client.get_json(path, headers))
        yield from items
        if not next_url or next_url == path:
            return
        path, headers = next_url, None


def build_index(
    client: Any,
    *,
    since_http_date: str | None,
    max_pages: int,
    max_details: int,
    index: dict[str, dict[str, Any]] | None = None,
    on_error: Callable[[str, Exception], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch active ads (newest window first) and fold their employer facts into an organisation-number index."""
    index = index if index is not None else {}
    fetched = 0
    for entry in iter_active_entries(client, since_http_date=since_http_date, max_pages=max_pages):
        if fetched >= max_details:
            break
        fetched += 1
        try:
            parsed = parse_ad(client.get_json(entry["url"]))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if on_error:
                on_error(entry["url"], exc)
            continue
        if parsed:
            merge_ad(index, parsed)
    return index
