from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..core.orgnumber import digits_only

CACHE_SCHEMA_VERSION = "signalpost-universe-cache-v1"
FIELD_FAMILIES = ("official_website", "social_profiles", "contact", "nav_jobs")
STATES = {"available", "not_available", "ambiguous", "blocked", "failed", "not_checked"}


def iter_cache_records(path: str | Path) -> Iterator[dict[str, Any]]:
    root = Path(path)
    files = sorted(root.glob("*.jsonl.gz")) if root.is_dir() else [root]
    for file in files:
        opener = gzip.open if file.suffix == ".gz" else open
        with opener(file, "rt", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    record = json.loads(line)
                    if isinstance(record, dict) and record.get("organisation_number"):
                        yield record


class CacheLookup:
    """Load the append-only cache into an exact-org lookup."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.records: dict[str, dict[str, Any]] = {}
        for record in iter_cache_records(self.path):
            self.records[digits_only(record.get("organisation_number"))] = record
        self.manifest = {}
        manifest = self.path / "manifest.json" if self.path.is_dir() else self.path.with_name("manifest.json")
        if manifest.exists():
            try:
                self.manifest = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.manifest = {}

    def get(self, organisation_number: str | int | None) -> dict[str, Any] | None:
        return self.records.get(digits_only(organisation_number))

    def __len__(self) -> int:
        return len(self.records)


def _state(record: dict[str, Any], family: str) -> str:
    value = (record.get("states") or {}).get(family)
    return value if value in STATES else "not_checked"


def merge_cache_profile(profile: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Attach cache claims without converting unavailable data to false facts."""
    claims = record.get("claims") or {}
    profile["recall_cache"] = {
        "schema": record.get("cache_schema", CACHE_SCHEMA_VERSION),
        "cache_built_at": record.get("built_at"),
        "source_retrieval_times": record.get("source_retrieval_times") or {},
        "states": {family: _state(record, family) for family in FIELD_FAMILIES},
        "candidate_domains": record.get("candidate_domains") or [],
        "reverification": profile.get("recall_cache", {}).get("reverification"),
    }
    profile.setdefault("claims", {})
    for family, value in claims.items():
        if _state(record, family) == "available":
            profile["claims"][family] = value
    profile.setdefault("evidence", {})
    website = record.get("website")
    if isinstance(website, dict) and website.get("evidence"):
        profile["evidence"]["website"] = website["evidence"]
    if record.get("nav_jobs"):
        profile["external"] = {"nav_jobs": record["nav_jobs"]}
    return profile


def cache_metadata(path: str | Path) -> dict[str, Any]:
    lookup = CacheLookup(path)
    return {"path": str(path), "records": len(lookup), **lookup.manifest}
