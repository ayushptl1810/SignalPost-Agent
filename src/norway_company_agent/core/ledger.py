from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Only outcomes that mean "we looked and found nothing" may be cached. A provider or
# network failure is not evidence that a company has no website, so it is never cached.
NEGATIVE_OUTCOMES = {"no_candidate", "crawled_no_verified"}


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _evidence_hash(summary: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(summary, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class DiscoveryLedger:
    """Per-organisation memory of every candidate seen, plus a negative cache.

    One JSONL line per organisation. It feeds refresh diffs (first_seen/last_seen,
    decision changes) and stops repeated search spend on entities already searched.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        if path and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self.entries[str(entry["organisation_number"])] = entry

    def should_skip(self, organisation_number: str, now: datetime, ttl_days: float) -> bool:
        entry = self.entries.get(str(organisation_number))
        if not entry or entry.get("last_outcome") not in NEGATIVE_OUTCOMES:
            return False
        return now - _parse(entry["last_checked"]) < timedelta(days=ttl_days)

    def record(self, organisation_number: str, outcome: str, candidates: list[dict[str, Any]], now: datetime) -> None:
        stamp = _iso(now)
        entry = self.entries.get(str(organisation_number)) or {"organisation_number": str(organisation_number), "candidates": []}
        known = {item["domain"]: item for item in entry["candidates"]}
        for summary in candidates:
            domain = summary.get("registered_domain")
            if not domain:
                continue
            previous = known.get(domain)
            known[domain] = {
                "domain": domain,
                "first_seen": previous["first_seen"] if previous else stamp,
                "last_seen": stamp,
                "decision": "published" if summary.get("publishable") else "quarantined",
                "evidence_sha256": _evidence_hash(summary),
            }
        entry.update({"last_checked": stamp, "last_outcome": outcome, "candidates": sorted(known.values(), key=lambda item: item["domain"])})
        self.entries[str(organisation_number)] = entry

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for key in sorted(self.entries):
                handle.write(json.dumps(self.entries[key], ensure_ascii=False, separators=(",", ":")) + "\n")
        temporary.replace(self.path)
