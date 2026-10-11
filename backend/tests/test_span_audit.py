from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.analysis.span_audit import audit_spans  # noqa: E402
from norway_company_agent.core.http import FetchResult  # noqa: E402
from norway_company_agent.registry.official import _classified  # noqa: E402


def _rows() -> list[dict]:
    path = ROOT / "submission" / "smoke-100" / "envelopes.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_span_audit_runs_offline_on_committed_smoke_with_fake_source_fetcher() -> None:
    rows = _rows()
    source_fragments: dict[str, list[str]] = {}
    for row in rows:
        for item in row.get("evidence") or []:
            if item.get("source_url") and item.get("claim_span"):
                source_fragments.setdefault(str(item["source_url"]), []).append(str(item["claim_span"]))
            if item.get("source_url") and item.get("date_span"):
                # Dated news carries a second span (the feed or page date string).
                source_fragments.setdefault(str(item["source_url"]), []).append(str(item["date_span"]))

    def fake_fetch(url: str) -> str:
        return "<html><body>" + " ".join(source_fragments[url]) + "</body></html>"

    report = audit_spans(rows, sample_size=100, seed=7, fetcher=fake_fetch)
    assert report["sampled_claims"] > 0
    assert report["passed"] is True, report
    assert report["verbatim_percent"] == 100.0


def test_span_audit_reports_claim_and_date_failures() -> None:
    rows = [{
        "organisation_number": "923609016",
        "claims": [{"field": "dated_news", "availability": "available", "evidence_ids": ["ev-1"]}],
        "evidence": [{"id": "ev-1", "source_url": "https://example.no/news", "claim_span": "Missing title", "date_span": "2026-10-01"}],
    }]
    report = audit_spans(rows, sample_size=1, fetcher=lambda _url: "<html><time datetime='2025-01-01'>Old</time></html>")
    assert report["passed"] is False
    assert {item["kind"] for item in report["failures"]} == {"claim_span", "date_span"}


def test_official_evidence_keeps_a_fragment_from_the_hashed_raw_response() -> None:
    raw = '{"name":"Printed value","amount":288000.00}'
    result = FetchResult(
        "https://data.brreg.no/example",
        200,
        1,
        len(raw),
        {"name": "Printed value", "amount": 288000.0},
        content_sha256="a" * 64,
        retrieved_at="2026-10-11T00:00:00Z",
        raw_body=raw,
    )
    record = _classified("financials", "official_annual_accounts", result, value={"records": []})
    assert record["claim_span"] == raw
