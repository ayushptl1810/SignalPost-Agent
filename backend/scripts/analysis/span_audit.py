#!/usr/bin/env python3
"""Audit whether published evidence spans occur in their cited source pages."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from norway_company_agent.web.website import SAFE_OPENER  # noqa: E402


class _VisiblePage(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.attributes: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "noscript", "template"}:
            self._hidden += 1
        self.attributes.extend(str(value) for _key, value in attrs if value)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript", "template"} and self._hidden:
            self._hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.text.append(data)


def _normalise(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def source_strings(body: bytes | str) -> tuple[str, str, list[str]]:
    raw = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    parser = _VisiblePage()
    try:
        parser.feed(raw)
    except Exception:  # noqa: BLE001 - malformed HTML is still auditable as raw text
        pass
    return raw, _normalise(" ".join(parser.text)), [_normalise(item) for item in parser.attributes if _normalise(item)]


def span_is_verbatim(span: Any, body: bytes | str) -> bool:
    candidate = _normalise(span)
    if not candidate:
        return False
    raw, visible, attributes = source_strings(body)
    return str(span) in raw or candidate in visible or any(candidate in attribute for attribute in attributes)


def _jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _published_claims(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        evidence = {str(item.get("id")): item for item in row.get("evidence") or [] if isinstance(item, dict)}
        for claim in row.get("claims") or []:
            if not isinstance(claim, dict) or claim.get("availability") != "available":
                continue
            field = str(claim.get("field") or "")
            if field == "refresh_metadata":
                # Runtime bookkeeping cites the harness documentation, not a page
                # containing the run-specific JSON fragment.
                continue
            for reference in claim.get("evidence_ids") or []:
                item = evidence.get(str(reference))
                if not item or not item.get("source_url"):
                    continue
                result.append({
                    "organisation_number": row.get("organisation_number"),
                    "field": field,
                    "evidence_id": reference,
                    "source_url": item.get("source_url"),
                    "claim_span": item.get("claim_span"),
                    "date_span": item.get("date_span") if field == "dated_news" else None,
                })
    return result


def audit_spans(
    rows: Iterable[dict[str, Any]],
    *,
    sample_size: int = 100,
    seed: int = 20261011,
    fetcher: Callable[[str], bytes | str] | None = None,
) -> dict[str, Any]:
    """Sample published claims and verify claim/date spans against fetched pages."""
    candidates = _published_claims(rows)
    randomiser = random.Random(seed)
    sample = randomiser.sample(candidates, min(max(0, int(sample_size)), len(candidates)))
    fetch = fetcher or _safe_fetch
    body_cache: dict[str, bytes | str] = {}
    fetch_error_urls: set[str] = set()
    fields: dict[str, Counter[str]] = defaultdict(Counter)
    failures: list[dict[str, Any]] = []
    fetch_failures: list[dict[str, Any]] = []
    for item in sample:
        url = str(item["source_url"])
        if url not in body_cache:
            try:
                body_cache[url] = fetch(url)
            except Exception as exc:  # noqa: BLE001 - report the source failure, never fail the run
                fetch_failures.append({"url": url, "error": type(exc).__name__})
                fetch_error_urls.add(url)
                body_cache[url] = ""
        if url in fetch_error_urls:
            continue
        body = body_cache[url]
        for kind in ("claim_span", "date_span"):
            span = item.get(kind)
            if kind == "date_span" and not span:
                continue
            fields[kind]["sampled"] += 1
            if span_is_verbatim(span, body):
                fields[kind]["verbatim"] += 1
            else:
                failures.append({**item, "kind": kind, "span": span})
    by_field = {
        field: {
            "sampled": counts["sampled"],
            "verbatim": counts["verbatim"],
            "percent": round(100.0 * counts["verbatim"] / counts["sampled"], 2) if counts["sampled"] else None,
        }
        for field, counts in sorted(fields.items())
    }
    checked = sum(item["sampled"] for item in by_field.values())
    passed = sum(item["verbatim"] for item in by_field.values())
    return {
        "seed": seed,
        "requested_sample_size": int(sample_size),
        "published_claims": len(candidates),
        "sampled_claims": len(sample),
        "fetched_sources": len(body_cache),
        "by_field": by_field,
        "verbatim_percent": round(100.0 * passed / checked, 2) if checked else None,
        "failures": failures,
        "fetch_failures": fetch_failures,
        # A dead or blocked public page is reported separately; it is not a
        # false span on a page we successfully fetched.
        "audited_claims": checked,
        "passed": not failures,
    }


def _safe_fetch(url: str) -> bytes:
    request = __import__("urllib.request", fromlist=["Request"]).Request(url, headers={"User-Agent": "SignalPost-span-audit/1.0"})
    with SAFE_OPENER.open(request, timeout=20.0) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit verbatim evidence spans in SignalPost envelopes")
    parser.add_argument("--envelopes", required=True)
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20261011)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = audit_spans(_jsonl(args.envelopes), sample_size=args.sample_size, seed=args.seed)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
