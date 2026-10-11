"""Fail-open, candidate-only website sources used by the batch runner.

These helpers deliberately return URLs and counters only.  The shared discovery
engine remains responsible for DNS, SSRF, robots, identity, first-party and
domain-uniqueness decisions.
"""
from __future__ import annotations

from html.parser import HTMLParser
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Any, Callable, Iterable

from ..core.orgnumber import digits_only
from ..web.website import SAFE_OPENER

WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
WIKIDATA_TIMEOUT_SECONDS = 20.0
WIKIDATA_WALL_CAP_SECONDS = 30.0
WIKIDATA_CHUNK_SIZE = 200
USER_AGENT = "SignalPost-Agent/1.0 (public company research; contact via Builderr)"


def _response_body(response: Any) -> bytes:
    body = response.read()
    return body if isinstance(body, bytes) else str(body).encode("utf-8")


def _org_variants(org: str) -> list[str]:
    value = digits_only(org)
    if not value:
        return []
    return [value, f"{value[:3]} {value[3:6]} {value[6:]}" if len(value) == 9 else value]


def _sparql_query(orgs: Iterable[str]) -> str:
    values = []
    for org in orgs:
        values.extend(json.dumps(item) for item in _org_variants(org))
    return (
        "SELECT ?orgnr ?item ?website WHERE { "
        f"VALUES ?orgnr {{{' '.join(dict.fromkeys(values))}}} "
        "?item wdt:P2333 ?orgnr . OPTIONAL { ?item wdt:P856 ?website } "
        "}"
    )


def _parse_wikidata_response(body: bytes | str, requested_orgs: Iterable[str]) -> dict[str, list[dict[str, str]]]:
    payload = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
    bindings = (((payload.get("results") or {}).get("bindings")) if isinstance(payload, dict) else None) or []
    requested = {digits_only(org) for org in requested_orgs if digits_only(org)}
    items_by_org: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        org = digits_only(((binding.get("orgnr") or {}).get("value")))
        item = str(((binding.get("item") or {}).get("value")) or "").strip()
        website = str(((binding.get("website") or {}).get("value")) or "").strip()
        if org not in requested or not item:
            continue
        items_by_org[org][item].add(website)
    candidates: dict[str, list[dict[str, str]]] = {}
    for org, item_map in items_by_org.items():
        # A duplicate Wikidata item or two competing items is not identity proof.
        if len(item_map) != 1:
            continue
        urls = sorted(url for url in next(iter(item_map.values())) if url.startswith(("https://", "http://")))
        if urls:
            candidates[org] = [{"url": url, "source": "wikidata_p856"} for url in urls]
    return candidates


def fetch_wikidata_candidates(
    records: Iterable[dict[str, Any]],
    *,
    opener: Callable[..., Any] = SAFE_OPENER.open,
    endpoint: str = WIKIDATA_ENDPOINT,
    timeout: float = WIKIDATA_TIMEOUT_SECONDS,
    wall_cap: float = WIKIDATA_WALL_CAP_SECONDS,
    chunk_size: int = WIKIDATA_CHUNK_SIZE,
) -> tuple[dict[str, list[dict[str, str]]], dict[str, Any]]:
    """Fetch P2333/P856 in bounded POST chunks; all failures are fail-open."""
    rows = [row for row in records if row.get("organisation_number") and not row.get("input_error")]
    result: dict[str, list[dict[str, str]]] = {}
    stats: dict[str, Any] = {"enabled": True, "requested": len(rows), "chunks": 0, "candidates": 0, "error": None}
    started = time.monotonic()
    try:
        for offset in range(0, len(rows), max(1, int(chunk_size))):
            if time.monotonic() - started >= wall_cap:
                stats["error"] = "wall_cap"
                break
            chunk = rows[offset : offset + max(1, int(chunk_size))]
            query = _sparql_query(str(row["organisation_number"]) for row in chunk)
            request = urllib.request.Request(
                endpoint,
                data=urllib.parse.urlencode({"query": query, "format": "json"}).encode("utf-8"),
                method="POST",
                headers={"Accept": "application/sparql-results+json", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT},
            )
            with opener(request, timeout=min(timeout, max(0.1, wall_cap - (time.monotonic() - started)))) as response:
                parsed = _parse_wikidata_response(_response_body(response), (str(row["organisation_number"]) for row in chunk))
            for org, candidates in parsed.items():
                result.setdefault(org, []).extend(candidates)
            stats["chunks"] += 1
    except Exception as exc:  # noqa: BLE001 - optional public source must fail open
        result = {}
        stats["error"] = type(exc).__name__
    stats["candidates"] = sum(len(items) for items in result.values())
    stats["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result, stats


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._script = False
        self._chunks: list[str] = []
        self.documents: list[Any] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        attributes = {key.casefold(): value or "" for key, value in attrs}
        self._script = "ld+json" in attributes.get("type", "").casefold()
        self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._script:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or not self._script:
            return
        try:
            self.documents.append(json.loads("".join(self._chunks)))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        self._script = False
        self._chunks = []


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def parse_fagfolkguiden_jsonld(body: bytes | str) -> list[str]:
    parser = _JsonLdParser()
    parser.feed(body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body)
    urls: list[str] = []
    for document in parser.documents:
        for node in _walk_json(document):
            for key in ("sameAs", "url"):
                values = node.get(key)
                values = values if isinstance(values, list) else [values]
                for value in values:
                    if isinstance(value, str) and value.startswith(("https://", "http://")) and "fagfolkguiden.no" not in value.casefold():
                        urls.append(value.strip())
    return list(dict.fromkeys(urls))


def fagfolkguiden_slug(name: Any) -> str:
    raw = str(name or "").translate(str.maketrans({"Æ": "Ae", "æ": "ae", "Ø": "O", "ø": "o", "Å": "A", "å": "a"}))
    text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii").casefold()
    text = re.sub(r"\b(?:as|asa|enk|da|ans|ks|nuf|sa)\b", " ", text)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def fetch_fagfolkguiden_candidate(
    profile: dict[str, Any],
    *,
    opener: Callable[..., Any] = SAFE_OPENER.open,
    timeout: float = 20.0,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    org = digits_only(profile.get("organisation_number"))
    slug = fagfolkguiden_slug(profile.get("name"))
    if not org or not slug:
        return [], {"requested": False, "candidates": 0, "error": "missing_slug_or_org"}
    url = f"https://www.fagfolkguiden.no/bedrift/{slug}-{org}"
    try:
        request = urllib.request.Request(url, headers={"Accept": "text/html", "User-Agent": USER_AGENT})
        with opener(request, timeout=timeout) as response:
            urls = parse_fagfolkguiden_jsonld(_response_body(response))
        return [{"url": item, "source": "fagfolkguiden_sameas"} for item in urls], {"requested": True, "candidates": len(urls), "url": url}
    except Exception as exc:  # noqa: BLE001 - optional public source must fail open
        return [], {"requested": True, "candidates": 0, "url": url, "error": type(exc).__name__}
