from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .providers import ProviderFatalError, ProviderTransientError, SearchProvider, make_provider


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _key_id(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _next_reset(now: datetime, policy: str) -> str | None:
    if policy in {"one_time", "none", "paid"}:
        return None
    if policy == "daily":
        target = now.astimezone(timezone.utc).date() + timedelta(days=1)
        return f"{target.isoformat()}T00:00:00Z"
    if policy in {"monthly", "calendar_month"}:
        month = now.month + 1
        year = now.year
        if month == 13:
            month, year = 1, year + 1
        return f"{year:04d}-{month:02d}-01T00:00:00Z"
    return None


@dataclass
class ProviderMember:
    provider: str
    key: str = field(repr=False)
    adapter: SearchProvider | Any = field(repr=False)
    allowance: int | None = None
    reset_policy: str = "paid"
    used: int = 0
    status: str = "active"
    exhausted_until: str | None = None
    consecutive_failures: int = 0

    @property
    def key_id(self) -> str:
        return _key_id(self.key)

    @property
    def storage_allowed(self) -> bool:
        return bool(getattr(self.adapter, "storage_allowed", True))


class ProviderUsageStore:
    def __init__(self, path: Path | None) -> None:
        self.path = path

    def load(self) -> dict[tuple[str, str], dict[str, Any]]:
        if not self.path or not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return {(str(item.get("provider")), str(item.get("key_id"))): item for item in payload.get("members", [])}

    def save(self, members: list[ProviderMember]) -> None:
        if not self.path:
            return
        payload = {
            "generated_at": _utc_now().isoformat().replace("+00:00", "Z"),
            "members": [{
                "provider": member.provider,
                "key_id": member.key_id,
                "used": member.used,
                "allowance": member.allowance,
                "reset_policy": member.reset_policy,
                "status": member.status,
                "exhausted_until": member.exhausted_until,
            } for member in members],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, prefix=f".{self.path.name}.", delete=False) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(self.path)


class ProviderPool:
    def __init__(
        self,
        members: list[ProviderMember],
        *,
        rotation: str = "weighted",
        provider_pin: str | None = None,
        usage_store: ProviderUsageStore | None = None,
        now: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
        max_queries: int | None = None,
        max_queries_per_provider: int | None = None,
    ) -> None:
        if rotation not in {"weighted", "priority", "round_robin"}:
            raise ValueError("rotation must be weighted, priority, or round_robin")
        self.members = members
        self.rotation = "off" if provider_pin else rotation
        self.provider_pin = provider_pin.casefold().removesuffix("_api") if provider_pin else None
        self.usage_store = usage_store or ProviderUsageStore(None)
        self.now = now
        self.sleep = sleep
        self.max_queries = max_queries
        self.max_queries_per_provider = max_queries_per_provider
        self._round_robin_index = 0
        self._queries_used = 0
        self._queries_by_provider: dict[str, int] = {}
        self._queries_by_key: dict[str, int] = {}
        self._apply_saved_state()

    def _apply_saved_state(self) -> None:
        saved = self.usage_store.load()
        current = self.now()
        for member in self.members:
            state = saved.get((member.provider, member.key_id))
            if not state:
                continue
            member.used = int(state.get("used") or 0)
            member.status = str(state.get("status") or "active")
            member.exhausted_until = state.get("exhausted_until")
            if member.status == "exhausted" and member.exhausted_until and (_parse_time(member.exhausted_until) or current) <= current:
                member.status, member.used, member.exhausted_until = "active", 0, None

    def _save(self) -> None:
        self.usage_store.save(self.members)

    def _budget_available(self, member: ProviderMember) -> bool:
        if self.max_queries is not None and self._queries_used >= self.max_queries:
            return False
        if self.max_queries_per_provider is not None and self._queries_by_provider.get(member.provider, 0) >= self.max_queries_per_provider:
            return False
        return True

    def _available(self, member: ProviderMember) -> bool:
        current = self.now()
        if member.status == "exhausted" and member.exhausted_until and (_parse_time(member.exhausted_until) or current) <= current:
            member.status, member.used, member.exhausted_until = "active", 0, None
        if member.status != "active" or not self._budget_available(member):
            return False
        return member.allowance is None or member.used < member.allowance

    def available_members(self) -> list[ProviderMember]:
        return [member for member in self.members if (not self.provider_pin or member.provider == self.provider_pin) and self._available(member)]

    def _choose(self, members: list[ProviderMember]) -> ProviderMember:
        if self.rotation in {"priority", "off"}:
            return members[0]
        if self.rotation == "round_robin":
            member = members[self._round_robin_index % len(members)]
            self._round_robin_index += 1
            return member
        def remaining_fraction(member: ProviderMember) -> float:
            if member.allowance is None or member.allowance <= 0:
                return 1.0
            return max(0.0, (member.allowance - member.used) / member.allowance)
        return max(members, key=lambda member: (remaining_fraction(member), -member.used, member.provider, member.key_id))

    def _record_attempt(self, member: ProviderMember) -> None:
        member.used += 1
        self._queries_used += 1
        self._queries_by_provider[member.provider] = self._queries_by_provider.get(member.provider, 0) + 1
        self._queries_by_key[member.key_id] = self._queries_by_key.get(member.key_id, 0) + 1
        if member.allowance is not None and member.used >= member.allowance:
            member.status = "exhausted"
            member.exhausted_until = _next_reset(self.now(), member.reset_policy)

    def search(self, query: str, *, country: str, language: str, count: int, timeout: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        attempted: set[str] = set()
        while True:
            members = [member for member in self.available_members() if member.key_id not in attempted]
            if not members:
                reason = "max_queries_exceeded" if self.max_queries is not None and self._queries_used >= self.max_queries else "all_exhausted"
                raise ProviderFatalError(reason)
            member = self._choose(members)
            attempted.add(member.key_id)
            last_transient: ProviderTransientError | None = None
            for attempt in range(3):
                try:
                    results, operation = member.adapter.search(query, country=country, language=language, count=count, timeout=timeout)
                    self._record_attempt(member)
                    member.consecutive_failures = 0
                    self._save()
                    return results, {**operation, "provider": member.provider, "key_id": member.key_id, "storage_allowed": member.storage_allowed, "attempts": attempt + 1}
                except ProviderFatalError as exc:
                    self._record_attempt(member)
                    member.status = "disabled" if exc.disable else "exhausted"
                    member.exhausted_until = None if exc.disable else _next_reset(self.now(), member.reset_policy)
                    self._save()
                    break
                except ProviderTransientError as exc:
                    self._record_attempt(member)
                    member.consecutive_failures += 1
                    last_transient = exc
                    self._save()
                    if attempt < 2:
                        self.sleep(2 ** attempt)
                        continue
                    if member.consecutive_failures >= 5:
                        member.status = "disabled"
                        self._save()
                    break
            if last_transient and not any(self._available(candidate) for candidate in self.members if candidate.key_id != member.key_id):
                raise ProviderFatalError("all_exhausted")

    def report(self) -> dict[str, Any]:
        return {
            "rotation": self.rotation,
            "provider_pin": self.provider_pin,
            "queries_used": self._queries_used,
            "queries_by_provider": dict(self._queries_by_provider),
            "queries_by_key": dict(self._queries_by_key),
            "members": [{
                "provider": member.provider,
                "key_id": member.key_id,
                "used": member.used,
                "allowance": member.allowance,
                "reset_policy": member.reset_policy,
                "status": member.status,
                "exhausted_until": member.exhausted_until,
            } for member in self.members],
        }

    @classmethod
    def load_config(cls, path: Path) -> dict[str, dict[str, Any]]:
        return json.loads(path.read_text(encoding="utf-8"))

    @classmethod
    def from_environment(
        cls,
        *,
        config: dict[str, dict[str, Any]],
        usage_path: Path | None,
        rotation: str = "weighted",
        provider_pin: str | None = None,
        legacy_serper_key: str | None = None,
        **kwargs: Any,
    ) -> "ProviderPool":
        members: list[ProviderMember] = []
        for name, settings in config.items():
            env_name = str(settings.get("env") or f"{name.upper()}_API_KEYS")
            raw = os.environ.get(env_name, "")
            if not raw:
                legacy_names = {
                    "serper": "SERPER_API_KEY",
                    "serpapi": "SERPAPI_KEY",
                    "tavily": "TAVILY_API_KEY",
                    "linkup": "LINKUP_API_KEY",
                    "brave": "BRAVE_API_KEY",
                }
                raw = legacy_serper_key if name == "serper" and legacy_serper_key else os.environ.get(legacy_names.get(name, ""), "")
            for key in [item.strip() for item in raw.split(",") if item.strip()]:
                members.append(ProviderMember(
                    provider=name,
                    key=key,
                    adapter=make_provider(name, key),
                    allowance=settings.get("allowance"),
                    reset_policy=str(settings.get("reset_policy") or "paid"),
                ))
        if provider_pin and not any(member.provider == provider_pin.casefold().removesuffix("_api") for member in members):
            raise ValueError(f"no API key configured for pinned provider {provider_pin}")
        if not members:
            raise ValueError("no search provider API keys configured")
        return cls(members, rotation=rotation, provider_pin=provider_pin, usage_store=ProviderUsageStore(usage_path), **kwargs)
