from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.registry.batch import terminal_envelope  # noqa: E402


MODULES = ["registry", "registry_live", "financials", "roles", "locations", "hiring", "public_activity", "company_brief"]


def _record(field: str, value: object, url: str, *, status: str = "available") -> dict:
    return {"field": field, "status": status, "source_url": url, "source_class": "official_test", "retrieved_at": "2026-10-11T00:00:00Z", "value": value, "content_sha256": f"hash-{field}"}


def _profile(records: list[dict]) -> dict:
    return {
        "organisation_number": "923609016",
        "name": "Signal Example AS",
        "employees": 12,
        "raw": {"antallAnsatte": "12"},
        "evidence": {item["field"]: item for item in records},
    }


def _base_records(revenues: tuple[float, float] | None) -> list[dict]:
    records = [
        _record("registry", {"organisasjonsnummer": "923609016", "navn": "Signal Example AS"}, "https://bulk.test/registry"),
        _record("registry_live", {"organisation_number": "923609016", "name": "Signal Example AS", "employees": 12}, "https://data.brreg.no/enhetsregisteret/api/enheter/923609016"),
        _record("roles", {"roles": [{"name": "A. Leader", "role": "Daglig leder", "last_changed": "2026-04-01", "inactive": False}]}, "https://registry.test/roles"),
        _record("locations", {"locations": [{"name": "New office", "registered_at": "2026-05-01"}]}, "https://registry.test/locations"),
        _record("hiring", {"postings": [{"title": "Engineer", "url": "https://signal.example/jobs/1"}], "nav_ads": []}, "https://signal.example/careers"),
        _record("public_activity", {"items": [{"title": "Company update", "date": "2026-09-01", "published_at": "2026-09-01", "url": "https://signal.example/news/update", "claim_span": "Company update"}]}, "https://signal.example/news/update"),
        _record("company_brief", {"summary": "A deterministic brief."}, "https://data.brreg.no/enhetsregisteret/api/enheter/923609016"),
    ]
    if revenues is not None:
        records.append(_record("financials", {"records": [{"period": {"tilDato": "2024-12-31"}, "revenue": revenues[0]}, {"period": {"tilDato": "2025-12-31"}, "revenue": revenues[1]}]}, "https://data.brreg.no/regnskapsregisteret/regnskap/923609016"))
    else:
        records.append(_record("financials", {"records": []}, "https://data.brreg.no/regnskapsregisteret/regnskap/923609016", status="not_found"))
    return records


def _brief(profile: dict) -> dict:
    envelope = terminal_envelope(profile, run_id="synthesis", modules=MODULES, started_at="2026-10-11T00:00:00Z", completed_at="2026-10-11T00:00:00Z")
    return next(claim["value"] for claim in envelope["claims"] if claim["field"] == "company_brief")


def test_growth_synthesis_has_sourced_signals_and_named_inference() -> None:
    brief = _brief(_profile(_base_records((100.0, 150.0))))
    kinds = {item["kind"] for item in brief["signals"]}
    assert {"revenue_change", "registered_employees", "hiring_signal", "latest_dated_news", "leadership_change", "new_sub_unit"} <= kinds
    assert brief["signals"][0]["evidence_ids"]
    assert all(item["signal_ids"] for item in brief["inference"])
    assert "signal_revenue_change" in {signal_id for item in brief["inference"] for signal_id in item["signal_ids"]}


def test_decline_is_a_sourced_signal_not_a_missing_value() -> None:
    brief = _brief(_profile(_base_records((150.0, 100.0))))
    revenue = next(item for item in brief["signals"] if item["kind"] == "revenue_change")
    assert revenue["value"]["change"] == -50.0
    assert revenue["value"]["change_percent"] == -33.33


def test_no_accounts_has_no_revenue_signal_and_keeps_unknown() -> None:
    brief = _brief(_profile(_base_records(None)))
    assert not any(item["kind"] == "revenue_change" for item in brief["signals"])
    assert "latest_annual_accounts" in brief["unknowns"]
