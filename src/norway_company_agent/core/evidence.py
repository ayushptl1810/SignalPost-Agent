from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

EvidenceStatus = Literal[
    "available",
    "not_found",
    "not_applicable",
    "not_fetched",
    "source_error",
    "failed",
    "blocked",
]


def infer_extraction_method(source_type: str, *, field: str | None = None) -> str:
    """Map source labels to a small, stable extraction vocabulary."""
    normalized = " ".join((str(source_type or ""), str(field or ""))).casefold()
    if "nav" in normalized or "job_feed" in normalized:
        return "nav_live_api"
    if "sitemap" in normalized:
        return "sitemap"
    if any(token in normalized for token in ("feed", "rss", "atom", "xml")):
        return "feed_xml"
    if any(token in normalized for token in ("registry", "brreg", "annual_account", "roles", "subunit", "group_structure", "rule_interpretation")):
        return "registry_json"
    if any(token in normalized for token in ("runtime", "batch", "signalpost")):
        return "runtime_metadata"
    if "jsonld" in normalized or "json-ld" in normalized:
        return "html_jsonld"
    return "html_text_span"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Evidence:
    field: str
    status: EvidenceStatus
    source_type: str
    source_class: str
    source_url: str
    retrieved_at: str
    value: Any = None
    as_of: str | None = None
    note: str | None = None
    content_sha256: str | None = None
    source_row_key: str | None = None
    effective_at: Any = None
    extraction_method: str = "structured_record"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evidence(
    field: str,
    status: EvidenceStatus,
    source_type: str,
    source_url: str,
    *,
    value: Any = None,
    as_of: str | None = None,
    note: str | None = None,
    retrieved_at: str | None = None,
    content_sha256: str | None = None,
    source_row_key: str | None = None,
    effective_at: Any = None,
    extraction_method: str | None = None,
) -> dict[str, Any]:
    return Evidence(
        field=field,
        status=status,
        source_type=source_type,
        source_class=source_type,
        source_url=source_url,
        retrieved_at=retrieved_at or utc_now(),
        value=value,
        as_of=as_of,
        note=note,
        content_sha256=content_sha256,
        source_row_key=source_row_key,
        effective_at=effective_at,
        extraction_method=extraction_method or infer_extraction_method(source_type, field=field),
    ).to_dict()
