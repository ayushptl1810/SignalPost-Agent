#!/usr/bin/env python3
"""Independently review the blind audit evidence packs.

This reviewer deliberately reads only ``evidence.json`` files.  It does not
load the worksheet, earlier assistant labels, or the review-page export.  A
blocked social source is unresolved even when its ``found_on_url`` is an
available company page: the available page proves the declaration, not that
the blocked handle is owned by the exact legal entity.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


LEGAL_TOKENS = {
    "as", "sa", "asa", "stiftelsen", "forening", "foreningen", "sameie",
    "nuf", "da", "ks", "ik", "begrenset", "limited", "the",
}
PLATFORMS = {"facebook", "instagram", "linkedin", "youtube", "tiktok", "x"}
GENERIC_PAGE_MARKERS = {
    "linkedin login", "sign in | linkedin", "tiktok", "youtube",
    "facebook", "instagram", "x.com",
}


def _normalise(value: Any) -> str:
    text = str(value or "").translate(str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "å": "a", "Å": "A"}))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.casefold().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _compact(value: Any) -> str:
    return _normalise(value).replace(" ", "")


def _core_tokens(registry_name: str) -> list[str]:
    return [token for token in _normalise(registry_name).split() if token not in LEGAL_TOKENS and len(token) > 2]


def _name_matches(registry_name: str, evidence_text: str) -> bool:
    tokens = _core_tokens(registry_name)
    if not tokens:
        return False
    normalised = _normalise(evidence_text)
    compact = normalised.replace(" ", "")
    phrase = "".join(tokens)
    if phrase and phrase in compact:
        return True
    if len(tokens) == 1:
        return tokens[0] in normalised.split() or tokens[0] in compact
    return all(token in normalised.split() for token in tokens)


def _source_text(source: dict[str, Any]) -> str:
    return " ".join(
        str(source.get(key) or "")
        for key in ("title", "visible_organisation_name", "excerpt")
    )


def _is_platform_url(url: str, platform: str) -> bool:
    host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
    if platform == "x":
        return host in {"x.com", "twitter.com"}
    return host == f"{platform}.com" or host.endswith(f".{platform}.com")


def _pack_path(evidence_path: Path) -> str:
    return evidence_path.as_posix()


def review_pack(path: Path) -> dict[str, Any]:
    pack = json.loads(path.read_text(encoding="utf-8"))
    context = pack.get("row_context") or {}
    platform = str(context.get("platform") or "")
    target_org = str(pack.get("organisation_number") or "")
    registry_name = str((pack.get("registry_facts") or {}).get("registry_name") or "")
    sources = pack.get("sources") or []
    available = [source for source in sources if source.get("status") == "available"]

    exact_sources = [source for source in available if target_org and target_org in {str(item) for item in (source.get("org_numbers") or [])}]
    conflicting_sources = [
        source for source in available
        if source.get("org_numbers") and target_org not in {str(item) for item in source.get("org_numbers")}
    ]
    direct_sources = available
    if platform in PLATFORMS:
        direct_sources = [
            source for source in sources
            if _is_platform_url(str(source.get("requested_url") or ""), platform)
        ]
        direct_available = [source for source in direct_sources if source.get("status") == "available"]
        if not direct_available:
            return _label(path, pack, "unresolved", "The platform source is blocked or unavailable; the linked company page cannot prove handle ownership.", direct_sources)
        direct_sources = direct_available

    direct_exact = [source for source in direct_sources if target_org and target_org in {str(item) for item in (source.get("org_numbers") or [])}]
    if direct_exact or (platform not in PLATFORMS and exact_sources):
        return _label(path, pack, "correct", "The fetched source states the target organisation number.", direct_exact or exact_sources)

    if conflicting_sources and platform not in PLATFORMS:
        return _label(path, pack, "wrong_entity", "The fetched page exposes organisation number(s) but not the target number.", conflicting_sources)

    evidence_text = " ".join(_source_text(source) for source in direct_sources)
    if _name_matches(registry_name, evidence_text):
        # A one-token or exact legal-name match is enough for a direct company
        # page.  Multi-token social names must also be visible on the platform
        # page itself; a found_on company URL is not substituted for it.
        if platform in PLATFORMS and any(marker in _normalise(evidence_text) for marker in GENERIC_PAGE_MARKERS):
            return _label(path, pack, "unresolved", "The platform response is a generic login or shell page; identity is not independently visible.", direct_sources)
        return _label(path, pack, "correct", "The fetched source visibly names the target organisation.", direct_sources)

    return _label(path, pack, "unresolved", "The available evidence does not establish the exact legal entity or a metric contradiction.", direct_sources or available)


def _label(path: Path, pack: dict[str, Any], verdict: str, note: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    quotes = []
    for source in sources[:2]:
        quote = source.get("excerpt") or source.get("visible_organisation_name") or source.get("title") or source.get("final_url")
        if quote:
            quotes.append(str(quote)[:500])
    return {
        "id": pack.get("id"),
        "organisation_number": str(pack.get("organisation_number") or ""),
        "platform": (pack.get("row_context") or {}).get("platform"),
        "signal_type": (pack.get("row_context") or {}).get("signal_type"),
        "label": verdict,
        "verdict": verdict,
        "labeler": "codex_review",
        "evidence_quote": " | ".join(quotes),
        "pack_path": _pack_path(path),
        "source_urls": [str(source.get("final_url") or source.get("requested_url") or "") for source in sources if source.get("final_url") or source.get("requested_url")],
        "notes": note,
    }


def review(evidence_root: Path, output: Path, report_path: Path) -> dict[str, Any]:
    paths = sorted(evidence_root.glob("*/evidence.json"))
    labels = [review_pack(path) for path in paths]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(label, ensure_ascii=False, separators=(",", ":")) + "\n" for label in labels), encoding="utf-8")
    by_platform: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for label in labels:
        grouped[str(label.get("platform") or "unknown")].append(label)
    for platform, rows in sorted(grouped.items()):
        determinate = [row for row in rows if row["label"] in {"correct", "wrong_entity", "wrong_content"}]
        correct = sum(row["label"] == "correct" for row in determinate)
        by_platform[platform] = {
            "rows": len(rows),
            "correct": correct,
            "wrong_entity": sum(row["label"] == "wrong_entity" for row in rows),
            "wrong_content": sum(row["label"] == "wrong_content" for row in rows),
            "unresolved": sum(row["label"] == "unresolved" for row in rows),
            "precision": round(correct / len(determinate), 4) if determinate else None,
            "determinate": len(determinate),
        }
    report = {
        "rows": len(labels),
        "labels": dict(Counter(label["label"] for label in labels)),
        "wrong_entity_rows": [label["id"] for label in labels if label["label"] == "wrong_entity"],
        "per_connector": by_platform,
        "unresolved_reason": "Blocked social sources remain unresolved; no earlier worksheet labels were read.",
        "output": str(output),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Review audit evidence packs without prior labels.")
    parser.add_argument("--evidence-root", type=Path, default=Path("out/audit-corpus/evidence"))
    parser.add_argument("--output", type=Path, default=Path("out/audit-corpus/review-codex-v2.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("out/audit-corpus/review-codex-v2-report.json"))
    args = parser.parse_args()
    print(json.dumps(review(args.evidence_root, args.output, args.report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
