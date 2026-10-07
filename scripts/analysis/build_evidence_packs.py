#!/usr/bin/env python3
"""Fetch one evidence pack per blind-audit row.

The pack is deliberately a compact, source-attributed capture. It never writes
the human verdict columns from the worksheet and treats blocked social pages as
``fetch_blocked`` rather than guessing from a login wall or search result.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.orgnumber import extract_org_numbers  # noqa: E402
from norway_company_agent.web.website import assert_public_url, registered_domain  # noqa: E402


SOCIAL_HOSTS = {"facebook.com", "instagram.com", "linkedin.com", "x.com", "twitter.com", "youtube.com", "tiktok.com"}
PHONE_RE = re.compile(r"(?<!\d)(?:\+?47[ .-]?)?(?:\d[ .-]?){8,11}(?!\d)")


class VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.text: list[str] = []
        self.links: list[str] = []
        self._in_title = False
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag.casefold() == "title":
            self._in_title = True
        if tag.casefold() in {"script", "style", "noscript", "svg"} or "hidden" in attrs_map or attrs_map.get("aria-hidden") == "true":
            self._hidden += 1
        if tag.casefold() == "a" and attrs_map.get("href"):
            self.links.append(str(attrs_map["href"]))

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self._in_title = False
        if tag.casefold() in {"script", "style", "noscript", "svg"}:
            self._hidden = max(0, self._hidden - 1)

    def handle_data(self, data: str) -> None:
        if self._hidden:
            return
        value = " ".join(data.split())
        if not value:
            return
        if self._in_title:
            self.title.append(value)
        self.text.append(value)


def _is_social(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").casefold().removeprefix("www.")
    return host in SOCIAL_HOSTS or any(host.endswith("." + value) for value in SOCIAL_HOSTS)


def _phone_values(text: str) -> list[str]:
    values = []
    for match in PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", match)
        if digits.startswith("47") and len(digits) > 8:
            digits = digits[2:]
        if len(digits) == 8 and digits not in values:
            values.append(match.strip())
    return values[:10]


def _address_values(text: str) -> list[str]:
    lines = [line.strip(" ,") for line in re.split(r"[\n|]+", text) if line.strip()]
    values = [line for line in lines if re.search(r"\b\d{4}\b", line) and len(line) < 180]
    return list(dict.fromkeys(values))[:10]


def _same_company_links(url: str, links: list[str], company_domain: str) -> list[str]:
    result = []
    for raw in links:
        absolute = urllib.parse.urljoin(url, raw)
        if company_domain and registered_domain(absolute) == company_domain and absolute not in result:
            result.append(absolute)
    return result[:50]


def _fetch_url(url: str, *, timeout: float = 15.0, max_bytes: int = 750_000) -> dict[str, Any]:
    if not url:
        return {"status": "missing_url", "requested_url": "", "final_url": ""}
    try:
        assert_public_url(url)
        request = urllib.request.Request(url, headers={"User-Agent": "builderr-signalpost-audit/1.0", "Accept": "text/html,application/xhtml+xml"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            final_url = response.geturl()
            content_type = str(response.headers.get("content-type") or "")
        if len(raw) > max_bytes:
            return {"status": "fetch_blocked" if _is_social(url) else "source_error", "requested_url": url, "final_url": final_url, "note": "response exceeds evidence byte limit"}
        if "html" not in content_type.casefold() and raw.lstrip()[:1] != b"<":
            return {"status": "fetch_blocked" if _is_social(url) else "source_error", "requested_url": url, "final_url": final_url, "content_type": content_type, "note": "unsupported content type"}
        parser = VisibleHTML()
        parser.feed(raw.decode("utf-8", errors="replace"))
        text = " ".join(parser.text)
        title = " ".join(parser.title)[:500]
        return {
            "status": "available", "requested_url": url, "final_url": final_url, "title": title,
            "visible_organisation_name": text[:300], "org_numbers": sorted(extract_org_numbers(text)),
            "address_found": _address_values(text), "phone_found": _phone_values(text),
            "links": parser.links, "excerpt": text[:1200], "content_sha256": hashlib.sha256(raw).hexdigest(),
        }
    except urllib.error.HTTPError as exc:
        status = "fetch_blocked" if _is_social(url) and exc.code in {401, 403, 406, 429} else "source_error"
        return {"status": status, "requested_url": url, "final_url": getattr(exc, "url", url), "http_status": exc.code, "note": str(exc.reason)}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        status = "fetch_blocked" if _is_social(url) else "source_error"
        return {"status": status, "requested_url": url, "final_url": url, "note": f"{type(exc).__name__}: {exc}"[:300]}


def _screenshot(url: str, path: Path, timeout_ms: int = 15_000) -> str | None:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        return None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.screenshot(path=str(path), full_page=True)
            browser.close()
        return str(path)
    except Exception:
        return None


def read_worksheet(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_pack(row: dict[str, str], output_root: str | Path, *, timeout: float = 15.0, screenshots: bool = True) -> dict[str, Any]:
    row_id = str(row.get("id") or "").strip()
    if not row_id:
        raise ValueError("worksheet row has no id")
    directory = Path(output_root) / row_id
    directory.mkdir(parents=True, exist_ok=True)
    urls = [row.get("source_url") or ""]
    declaring = row.get("found_on_url") or ""
    if declaring and declaring not in urls:
        urls.append(declaring)
    sources = []
    for index, url in enumerate(urls, start=1):
        source = _fetch_url(url, timeout=timeout)
        if screenshots and source.get("status") == "available":
            screenshot = _screenshot(str(source.get("final_url") or url), directory / f"screenshot-{index}.png")
            if screenshot:
                source["screenshot"] = screenshot
        company_domain = str(row.get("verified_site_domain") or "")
        source["links_back_to_company_domain"] = _same_company_links(str(source.get("final_url") or url), source.get("links") or [], company_domain)
        source.pop("links", None)
        sources.append(source)
        (directory / f"source-{index}.json").write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pack = {
        "id": row_id, "organisation_number": row.get("organisation_number", ""),
        "registry_facts": {key: row.get(key, "") for key in ("registry_name", "registry_address", "registry_phone", "registry_email_domain", "verified_site_domain")},
        "row_context": {key: row.get(key, "") for key in ("platform", "signal_type", "handle_text", "ad_title", "ad_published_at", "ad_expires_at", "place_name", "place_address", "place_phone", "evidence_excerpt")},
        "sources": sources,
        "status": "available" if any(item.get("status") == "available" for item in sources) else ("fetch_blocked" if any(item.get("status") == "fetch_blocked" for item in sources) else "source_error"),
        "human_verdicts": {"exact_entity": "", "metric_correct": "", "sentiment_correct": "", "notes": ""},
    }
    (directory / "evidence.json").write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pack


def build_packs(worksheet: str | Path, output_root: str | Path, *, timeout: float = 15.0, screenshots: bool = True) -> dict[str, Any]:
    rows = read_worksheet(worksheet)
    packs: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(build_pack, row, output_root, timeout=timeout, screenshots=screenshots) for row in rows]
        for future in as_completed(futures):
            packs.append(future.result())
    packs.sort(key=lambda item: str(item.get("id")))
    return {"rows": len(packs), "available": sum(item["status"] == "available" for item in packs), "fetch_blocked": sum(item["status"] == "fetch_blocked" for item in packs), "output_root": str(output_root)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build source evidence packs for every audit row.")
    parser.add_argument("--worksheet", required=True)
    parser.add_argument("--output-root", default=str(ROOT / "out/proxy/evidence"))
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--no-screenshots", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build_packs(args.worksheet, args.output_root, timeout=args.timeout, screenshots=not args.no_screenshots), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
