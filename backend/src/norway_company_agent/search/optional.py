"""Ephemeral, key-gated search pool for candidate websites.

No usage state or result payload is persisted here.  Provider adapters remain
plain ``urllib`` implementations from ``search.providers``.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from .providers import make_provider

PROVIDER_ENV = {
    "brave": ("BRAVE_API_KEY", "BRAVE_API_KEYS", "BRAVE_SEARCH_API_KEY", "BRAVE_SEARCH_API_KEYS"),
    "serper": ("SERPER_API_KEY", "SERPER_API_KEYS"),
    "tavily": ("TAVILY_API_KEY", "TAVILY_API_KEYS"),
    "serpapi": ("SERPAPI_API_KEY", "SERPAPI_API_KEYS"),
}
GENERIC_PROVIDER_KEY_ENV = "SIGNALPOST_SEARCH_API_KEY"
GENERIC_PROVIDER_ENV = "SIGNALPOST_SEARCH_PROVIDER"
SUPPORTED_PROVIDERS = frozenset(PROVIDER_ENV)
PROVIDER_COST_PER_QUERY = {"brave": 0.005}


def _key_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _env_keys(names: tuple[str, ...], environ: dict[str, str] | None = None) -> list[str]:
    source = os.environ if environ is None else environ
    result: list[str] = []
    for name in names:
        for value in re.split(r"[\s,]+", str(source.get(name) or "")):
            value = value.strip()
            if value and value not in result:
                result.append(value)
    return result


@dataclass
class OptionalProviderMember:
    provider: str
    key: str
    adapter: Any
    disabled: bool = False
    attempts: int = 0

    @property
    def key_id(self) -> str:
        return _key_id(self.key)


class OptionalSearchPool:
    def __init__(self, members: list[OptionalProviderMember], *, max_queries: int = 1000) -> None:
        self.members = members
        self.max_queries = max(0, int(max_queries))
        self.queries = 0
        self.failures: dict[str, int] = {}
        self.circuits: set[str] = set()
        self.by_provider: dict[str, int] = {}
        self.errors: list[dict[str, Any]] = []

    @classmethod
    def from_environment(cls, *, max_queries: int = 1000, environ: dict[str, str] | None = None, factory: Callable[[str, str], Any] = make_provider) -> "OptionalSearchPool":
        members: list[OptionalProviderMember] = []
        source = os.environ if environ is None else environ
        seen: set[tuple[str, str]] = set()

        def add_member(provider: str, key: str) -> None:
            identity = (provider, key)
            if identity in seen:
                return
            seen.add(identity)
            try:
                adapter = factory(provider, key)
            except Exception:
                # A malformed optional provider configuration cannot affect the batch.
                return
            members.append(OptionalProviderMember(provider, key, adapter))

        for provider, names in PROVIDER_ENV.items():
            for key in _env_keys(names, environ):
                add_member(provider, key)
        generic_provider = str(source.get(GENERIC_PROVIDER_ENV) or "").strip().casefold()
        if generic_provider in SUPPORTED_PROVIDERS:
            for key in _env_keys((GENERIC_PROVIDER_KEY_ENV,), environ):
                add_member(generic_provider, key)
        return cls(members, max_queries=max_queries)

    def search(self, query: str, *, timeout: float = 15.0, count: int = 3) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        attempted: set[str] = set()
        operation: dict[str, Any] = {"search_queries": 0, "provider_attempts": 0, "cost_estimate_usd": 0.0, "query": query}
        while self.queries < self.max_queries:
            members = [member for member in self.members if not member.disabled and member.provider not in self.circuits and member.key_id not in attempted]
            if not members:
                break
            member = members[0]
            attempted.add(member.key_id)
            self.queries += 1
            member.attempts += 1
            operation["provider_attempts"] += 1
            self.by_provider[member.provider] = self.by_provider.get(member.provider, 0) + 1
            try:
                results, provider_operation = member.adapter.search(query, country="no", language="no", count=count, timeout=timeout)
                self.failures[member.provider] = 0
                operation.update({"provider": member.provider, "key_id": member.key_id, "search_queries": 1, "bytes": int(provider_operation.get("bytes") or 0), "latency_ms": int(provider_operation.get("latency_ms") or 0)})
                operation["cost_estimate_usd"] = PROVIDER_COST_PER_QUERY.get(member.provider, 0.0)
                return results, operation
            except Exception as exc:  # noqa: BLE001 - optional provider failures fail open
                status = getattr(exc, "status", None)
                self.failures[member.provider] = self.failures.get(member.provider, 0) + 1
                if status in {403, 429}:
                    member.disabled = True
                if self.failures[member.provider] >= 3:
                    self.circuits.add(member.provider)
                self.errors.append({"provider": member.provider, "key_id": member.key_id, "error": type(exc).__name__, "status": status})
                operation["last_error"] = type(exc).__name__
        operation["error"] = "search_unavailable" if self.members else "no_keys"
        return [], operation

    def report(self) -> dict[str, Any]:
        return {
            "configured_members": len(self.members),
            "queries": self.queries,
            "provider_attempts": dict(self.by_provider),
            "circuits_open": sorted(self.circuits),
            "errors": list(self.errors),
            "members": [{"provider": member.provider, "key_id": member.key_id, "attempts": member.attempts, "disabled": member.disabled} for member in self.members],
        }


__all__ = ["OptionalProviderMember", "OptionalSearchPool", "PROVIDER_ENV"]
