from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.search.optional import OptionalProviderMember, OptionalSearchPool  # noqa: E402
from scripts.run import run_competition_batch as batch  # noqa: E402


RUN_AT = "2026-10-15T00:00:00Z"


def _write_inputs(root: Path, rows: list[tuple[str, str]]) -> tuple[Path, Path]:
    organisations = root / "organisations.jsonl"
    organisations.write_text("".join(json.dumps({"organisation_number": org}) + "\n" for org, _name in rows), encoding="utf-8")
    bulk = root / "snapshot.csv"
    bulk.write_text(
        "organisasjonsnummer,navn,organisasjonsform.kode,forretningsadresse.kommune,forretningsadresse.postnummer,forretningsadresse.poststed,hjemmeside\n"
        + "".join(f"{org},{name},AS,Lillehammer,2609,Lillehammer,\n" for org, name in rows),
        encoding="utf-8",
    )
    return organisations, bulk


def _not_found(url: str | None) -> tuple[dict, dict]:
    return {
        "status": "not_found",
        "source_url": str(url or ""),
        "source_class": "registry_linked_company_website",
        "retrieved_at": RUN_AT,
        "value": {},
    }, {"requests": 0, "bytes": 0, "latencies_ms": []}


def _no_site_facts(profile: dict, **_kwargs: object) -> tuple[dict, dict, dict]:
    return profile, {"requests": 0, "bytes": 0, "latencies_ms": []}, {}


def _run_batch(
    root: Path,
    rows: list[tuple[str, str]],
    *,
    extra_args: tuple[str, ...] = (),
    fetch_website,
    pool: OptionalSearchPool | None = None,
    environment: dict[str, str] | None = None,
) -> tuple[Path, Path, dict, list[dict]]:
    organisations, bulk = _write_inputs(root, rows)
    output = root / "envelopes.jsonl"
    profiles = root / "profiles.jsonl"
    report = root / "report.json"
    argv = [
        "run_competition_batch.py",
        "--organisations", str(organisations),
        "--bulk", str(bulk),
        "--output", str(output),
        "--profiles-output", str(profiles),
        "--report", str(report),
        "--run-id", "s36-test",
        "--modules", "registry,website",
        "--discovery", "off",
        "--workers", "1",
        "--discovery-processes", "1",
        "--checkpoint-every", str(len(rows)),
        *extra_args,
    ]
    patches = [
        patch.object(sys, "argv", argv),
        patch.dict(os.environ, environment or {}, clear=True),
        patch.object(batch, "utc_now", return_value=RUN_AT),
        patch("norway_company_agent.registry.batch.utc_now", return_value=RUN_AT),
        patch.object(batch, "network_preflight", return_value={"passed": True}),
        patch.object(batch, "fetch_website", side_effect=fetch_website),
        patch.object(batch, "_enrich_and_attach_site_facts", side_effect=_no_site_facts),
    ]
    if pool is not None:
        patches.append(patch.object(batch.OptionalSearchPool, "from_environment", return_value=pool))
    active = []
    try:
        for item in patches:
            active.append(item.start())
        try:
            batch.main()
        except SystemExit as exc:
            assert exc.code == 0
    finally:
        for item in reversed(patches):
            item.stop()
    envelopes = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    return output, report, json.loads(report.read_text(encoding="utf-8")), envelopes


def test_no_key_default_keeps_starter_envelopes_byte_identical_to_explicit_off(tmp_path: Path) -> None:
    rows = [("926790137", "Musikk i Innlandet AS")]

    def fetch(url: str | None, **_kwargs: object):
        return _not_found(url)

    default_root = tmp_path / "default"
    off_root = tmp_path / "off"
    default_root.mkdir()
    off_root.mkdir()
    default_output, _default_report_path, default_report, _default_envelopes = _run_batch(default_root, rows, fetch_website=fetch)
    off_output, _off_report_path, off_report, _off_envelopes = _run_batch(off_root, rows, extra_args=("--no-search-fill",), fetch_website=fetch)

    def without_clock_fields(path: Path) -> dict:
        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

        def scrub(value):
            if isinstance(value, dict):
                return {
                    key: "<clock>" if key in {"final_timestamp", "retrieved_at"} else scrub(child)
                    for key, child in value.items()
                }
            if isinstance(value, list):
                return [scrub(child) for child in value]
            return value

        return scrub(row)

    assert without_clock_fields(default_output) == without_clock_fields(off_output)
    assert default_report["search"]["enabled"] is True
    assert default_report["search"]["default_off"] is False
    assert default_report["search"]["configured_members"] == 0
    assert default_report["search"]["skipped_no_key"] == 1
    assert default_report["search"]["stats"]["skipped_no_key"] == 1
    assert off_report["search"]["enabled"] is False
    assert off_report["search"]["default_off"] is True


class _FakeSearchAdapter:
    def __init__(self, *, sleep_seconds: float = 0.0, result: dict | None = None) -> None:
        self.sleep_seconds = sleep_seconds
        self.result = result
        self.calls = 0

    def search(self, _query: str, **_kwargs: object):
        self.calls += 1
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        return ([self.result] if self.result else []), {"bytes": 10, "latency_ms": 1}


def test_keyed_candidate_is_published_in_starter_profile_and_search_url_is_not_evidence(tmp_path: Path) -> None:
    site_url = "https://musikkinnlandet.no/"
    adapter = _FakeSearchAdapter(
        result={
            "url": site_url,
            "title": "Musikk i Innlandet",
            "snippet": "Musikk i Innlandet 926 790 137",
            "source_url": "https://search.invalid/result/1",
            "position": 1,
        }
    )
    pool = OptionalSearchPool([OptionalProviderMember("serper", "fake-key", adapter)], max_queries=3)
    fetched = {
        "status": "available",
        "source_url": site_url,
        "retrieved_at": RUN_AT,
        "value": {
            "final_url": site_url,
            "title": "Musikk i Innlandet",
            "main_text_excerpt": "Musikk i Innlandet Org.nr 926 790 137 2609 Lillehammer",
            "identity_text_excerpt": "Musikk i Innlandet Org.nr 926 790 137",
            "pages": [],
        },
    }

    def fetch(url: str | None, **_kwargs: object):
        return (fetched, {"requests": 1, "bytes": 10, "latencies_ms": [1]}) if url else _not_found(url)

    _output, _report_path, report, envelopes = _run_batch(
        tmp_path,
        [("926790137", "Musikk i Innlandet AS")],
        fetch_website=fetch,
        pool=pool,
        environment={"SERPER_API_KEY": "fake-key"},
    )

    website = envelopes[0]["profile"]["evidence"]["website"]
    assert website["status"] == "available"
    assert website["source_url"] == site_url
    assert "search.invalid" not in json.dumps(envelopes[0])
    assert adapter.calls == 1
    assert report["search"]["enabled"] is True
    assert report["search"]["configured_members"] == 1
    assert "fake-key" not in json.dumps(report)


def test_explicit_search_off_blocks_fake_provider_and_budget_expiry_keeps_terminal_starter_envelopes(tmp_path: Path) -> None:
    adapter = _FakeSearchAdapter(sleep_seconds=0.15)
    pool = OptionalSearchPool([OptionalProviderMember("serper", "fake-key", adapter)], max_queries=10)

    def fetch(url: str | None, **_kwargs: object):
        return _not_found(url)

    output, _report_path, report, envelopes = _run_batch(
        tmp_path,
        [("926790137", "Musikk i Innlandet AS"), ("926790138", "Another Innlandet AS")],
        extra_args=("--no-search-fill", "--run-budget-seconds", "0.5"),
        fetch_website=fetch,
        pool=pool,
        environment={"SERPER_API_KEY": "fake-key"},
    )

    assert adapter.calls == 0
    assert report["search"]["enabled"] is False
    assert len(envelopes) == 2
    assert batch.validate_envelopes(envelopes, 2)["passed"]
    assert all(envelope["state"] == "complete" for envelope in envelopes)
    assert all("claims" not in envelope and "evidence" not in envelope for envelope in envelopes)

    # A second run with search enabled and a sleeping fake provider exercises the
    # terminalization path after the bounded search window expires.
    budget_root = tmp_path / "budget"
    budget_root.mkdir()
    budget_adapter = _FakeSearchAdapter(sleep_seconds=0.15)
    budget_pool = OptionalSearchPool([OptionalProviderMember("serper", "fake-key", budget_adapter)], max_queries=10)
    _output, _report_path, budget_report, budget_envelopes = _run_batch(
        budget_root,
        [("926790137", "Musikk i Innlandet AS"), ("926790138", "Another Innlandet AS")],
        extra_args=("--run-budget-seconds", "0.5"),
        fetch_website=fetch,
        pool=budget_pool,
        environment={"SERPER_API_KEY": "fake-key"},
    )
    assert budget_adapter.calls <= 1
    assert budget_report["search"]["stats"].get("budget_exhausted", 0) >= 1
    assert batch.validate_envelopes(budget_envelopes, 2)["passed"]
    assert output.exists()
