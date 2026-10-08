from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


USER_AGENT = "builderr-signalpost-poc/0.1 (+https://builderr.ai)"


class ProviderError(RuntimeError):
    """Base class for provider failures without carrying a secret key."""


class ProviderFatalError(ProviderError):
    def __init__(self, reason: str, *, status: int | None = None, disable: bool = False, body_excerpt: str = "", headers: dict[str, str] | None = None) -> None:
        self.reason = reason
        self.status = status
        self.disable = disable
        self.body_excerpt = body_excerpt
        self.headers = headers or {}
        super().__init__(reason)


class ProviderTransientError(ProviderError):
    def __init__(self, reason: str, *, status: int | None = None, body_excerpt: str = "", headers: dict[str, str] | None = None) -> None:
        self.reason = reason
        self.status = status
        self.body_excerpt = body_excerpt
        self.headers = headers or {}
        super().__init__(reason)


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _is_sponsored(item: dict[str, Any]) -> bool:
    if item.get("sponsored") is True or item.get("is_sponsored") is True or item.get("advertisement") is True:
        return True
    if isinstance(item.get("ad"), dict) or item.get("ad") is True:
        return True
    kind = str(item.get("type") or item.get("result_type") or "").casefold()
    return "sponsor" in kind or kind in {"ad", "advertisement"}


def _result(url: Any, title: Any, snippet: Any, position: Any, *, provider: str, query: str) -> dict[str, Any] | None:
    url_text = str(url or "").strip()
    if not url_text:
        return None
    return {
        "url": url_text,
        "title": str(title or ""),
        "snippet": str(snippet or ""),
        "position": int(position or 1),
        "provider": provider,
        "query": query,
    }


def _normalise(items: list[dict[str, Any]], *, query: str, provider: str, url_key: str, title_key: str, snippet_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for rank, item in enumerate(items, start=1):
        if not isinstance(item, dict) or _is_sponsored(item):
            continue
        snippet = next((item.get(key) for key in snippet_keys if item.get(key) is not None), "")
        parsed = _result(item.get(url_key), item.get(title_key), snippet, item.get("position") or rank, provider=provider, query=query)
        if parsed:
            results.append(parsed)
    return results


def parse_serper_payload(payload: dict[str, Any], *, query: str, provider: str = "serper") -> list[dict[str, Any]]:
    return _normalise(payload.get("organic") or [], query=query, provider=provider, url_key="link", title_key="title", snippet_keys=("snippet", "description"))


def parse_serpapi_payload(payload: dict[str, Any], *, query: str, provider: str = "serpapi") -> list[dict[str, Any]]:
    return _normalise(payload.get("organic_results") or [], query=query, provider=provider, url_key="link", title_key="title", snippet_keys=("snippet", "snippet_highlighted_words"))


def parse_tavily_payload(payload: dict[str, Any], *, query: str, provider: str = "tavily") -> list[dict[str, Any]]:
    return _normalise(payload.get("results") or [], query=query, provider=provider, url_key="url", title_key="title", snippet_keys=("content", "snippet"))


def parse_linkup_payload(payload: dict[str, Any], *, query: str, provider: str = "linkup") -> list[dict[str, Any]]:
    items = payload.get("results") or payload.get("sources") or payload.get("data") or []
    return _normalise(items, query=query, provider=provider, url_key="url", title_key="name", snippet_keys=("content", "snippet", "description", "title"))


def parse_brave_payload(payload: dict[str, Any], *, query: str, provider: str = "brave") -> list[dict[str, Any]]:
    return _normalise((payload.get("web") or {}).get("results") or [], query=query, provider=provider, url_key="url", title_key="title", snippet_keys=("description", "snippet"))


def _safe_response_headers(headers: Any) -> dict[str, str]:
    """Keep only quota/rate metadata; never persist cookies or auth headers."""
    allowed = ("remaining", "quota", "rate", "credit", "limit", "retry-after")
    return {
        str(key).casefold(): str(value)[:200]
        for key, value in (headers.items() if hasattr(headers, "items") else [])
        if any(marker in str(key).casefold() for marker in allowed)
    }


def _redact_body(body: str, api_key: str = "") -> str:
    excerpt = str(body or "")[:2000]
    return excerpt.replace(api_key, "[REDACTED]") if api_key else excerpt


def _error_from_http(status: int, body: str, *, headers: Any = None, api_key: str = "") -> ProviderError:
    lowered = body.casefold()
    quota = any(marker in lowered for marker in ("quota", "credit", "limit exceeded", "too many requests", "rate limit"))
    safe_body = _redact_body(body, api_key)
    safe_headers = _safe_response_headers(headers)
    if status in {401, 403}:
        return ProviderFatalError("provider_auth_or_permission", status=status, disable=True, body_excerpt=safe_body, headers=safe_headers)
    if status == 402 or (status == 400 and quota):
        return ProviderFatalError("credits_exhausted", status=status, body_excerpt=safe_body, headers=safe_headers)
    if status == 429 and quota:
        return ProviderFatalError("quota_exhausted", status=status, body_excerpt=safe_body, headers=safe_headers)
    if status == 429 or status >= 500 or status == 0:
        return ProviderTransientError("provider_transient_error", status=status, body_excerpt=safe_body, headers=safe_headers)
    return ProviderFatalError("provider_http_error", status=status, body_excerpt=safe_body, headers=safe_headers)


@dataclass
class SearchProvider:
    name: str
    api_key: str
    endpoint: str
    storage_allowed: bool = True
    opener: Callable[..., Any] = urllib.request.urlopen

    def _request(self, request: urllib.request.Request, *, timeout: float) -> tuple[dict[str, Any], dict[str, Any]]:
        started = time.monotonic()
        try:
            with self.opener(request, timeout=timeout) as response:
                raw = response.read()
                status = int(getattr(response, "status", 200))
            payload = json.loads(raw)
            return payload, {"status": status, "latency_ms": int((time.monotonic() - started) * 1000), "bytes": len(raw), "query_sha256": ""}
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")[:1000]
            except Exception:
                body = ""
            raise _error_from_http(int(exc.code), body, headers=exc.headers, api_key=self.api_key) from None
        except (urllib.error.URLError, TimeoutError, TimeoutError) as exc:
            raise ProviderTransientError("provider_transient_error", status=getattr(exc, "code", 0)) from None
        except json.JSONDecodeError:
            raise ProviderTransientError("provider_invalid_json") from None

    def search(self, query: str, *, country: str, language: str, count: int, timeout: float = 15.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        raise NotImplementedError

    def _operation(self, query: str, operation: dict[str, Any]) -> dict[str, Any]:
        return {**operation, "provider": self.name, "query_sha256": _query_hash(query)}


class SerperSearchProvider(SearchProvider):
    def __init__(self, api_key: str, *, endpoint: str = "https://google.serper.dev/search", opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        super().__init__("serper", api_key, endpoint, True, opener)

    def search(self, query: str, *, country: str, language: str, count: int, timeout: float = 15.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        body = json.dumps({"q": query, "gl": country, "hl": language, "num": count}).encode()
        request = urllib.request.Request(self.endpoint, data=body, method="POST", headers={"Accept": "application/json", "Content-Type": "application/json", "X-API-KEY": self.api_key, "User-Agent": USER_AGENT})
        payload, operation = self._request(request, timeout=timeout)
        return parse_serper_payload(payload, query=query), self._operation(query, operation)


class SerpApiSearchProvider(SearchProvider):
    def __init__(self, api_key: str, *, endpoint: str = "https://serpapi.com/search.json", opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        super().__init__("serpapi", api_key, endpoint, True, opener)

    def search(self, query: str, *, country: str, language: str, count: int, timeout: float = 15.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        params = urllib.parse.urlencode({"engine": "google", "q": query, "gl": country, "hl": language, "num": count, "api_key": self.api_key})
        request = urllib.request.Request(f"{self.endpoint}?{params}", headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        payload, operation = self._request(request, timeout=timeout)
        return parse_serpapi_payload(payload, query=query), self._operation(query, operation)


class TavilySearchProvider(SearchProvider):
    def __init__(self, api_key: str, *, endpoint: str = "https://api.tavily.com/search", opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        super().__init__("tavily", api_key, endpoint, True, opener)

    def search(self, query: str, *, country: str, language: str, count: int, timeout: float = 15.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        body = json.dumps({"api_key": self.api_key, "query": query, "search_depth": "basic", "topic": "general", "max_results": count, "country": "Norway", "include_answer": False}).encode()
        request = urllib.request.Request(self.endpoint, data=body, method="POST", headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": USER_AGENT})
        payload, operation = self._request(request, timeout=timeout)
        return parse_tavily_payload(payload, query=query), self._operation(query, operation)


class LinkupSearchProvider(SearchProvider):
    def __init__(self, api_key: str, *, endpoint: str = "https://api.linkup.so/v1/search", opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        super().__init__("linkup", api_key, endpoint, True, opener)

    def search(self, query: str, *, country: str, language: str, count: int, timeout: float = 15.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        body = json.dumps({"q": query, "depth": "standard", "outputType": "searchResults", "includeImages": False}).encode()
        request = urllib.request.Request(self.endpoint, data=body, method="POST", headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}", "User-Agent": USER_AGENT})
        payload, operation = self._request(request, timeout=timeout)
        return parse_linkup_payload(payload, query=query), self._operation(query, operation)


class BraveSearchProvider(SearchProvider):
    def __init__(self, api_key: str, *, endpoint: str = "https://api.search.brave.com/res/v1/web/search", opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        super().__init__("brave", api_key, endpoint, False, opener)

    def search(self, query: str, *, country: str, language: str, count: int, timeout: float = 15.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        params = urllib.parse.urlencode({"q": query, "count": count, "country": country, "search_lang": language, "safesearch": "moderate", "spellcheck": "0"})
        request = urllib.request.Request(f"{self.endpoint}?{params}", headers={"Accept": "application/json", "Accept-Encoding": "identity", "Cache-Control": "no-cache", "User-Agent": USER_AGENT, "X-Subscription-Token": self.api_key})
        payload, operation = self._request(request, timeout=timeout)
        return parse_brave_payload(payload, query=query), self._operation(query, operation)


ADAPTERS = {
    "serper": SerperSearchProvider,
    "serpapi": SerpApiSearchProvider,
    "tavily": TavilySearchProvider,
    "linkup": LinkupSearchProvider,
    "brave": BraveSearchProvider,
}


def make_provider(name: str, api_key: str, *, opener: Callable[..., Any] = urllib.request.urlopen) -> SearchProvider:
    canonical = name.casefold().removesuffix("_api")
    try:
        return ADAPTERS[canonical](api_key, opener=opener)
    except KeyError as exc:
        raise ValueError(f"unknown search provider: {name}") from exc
