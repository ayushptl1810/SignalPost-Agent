from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.discovery.engine import candidate_domains  # noqa: E402
from norway_company_agent.discovery.external_candidates import (  # noqa: E402
    fagfolkguiden_slug,
    fetch_wikidata_candidates,
    parse_fagfolkguiden_jsonld,
)


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _wikidata_body() -> bytes:
    return json.dumps({
        "results": {"bindings": [
            {"orgnr": {"value": "923 609 016"}, "item": {"value": "http://www.wikidata.org/entity/Q1"}, "website": {"value": "https://example.no/"}},
            {"orgnr": {"value": "923609024"}, "item": {"value": "http://www.wikidata.org/entity/Q2"}, "website": {"value": "https://one.example/"}},
            {"orgnr": {"value": "923609024"}, "item": {"value": "http://www.wikidata.org/entity/Q3"}, "website": {"value": "https://two.example/"}},
        ]}
    }).encode("utf-8")


def test_wikidata_accepts_plain_and_spaced_org_numbers_but_rejects_duplicate_items() -> None:
    calls: list[object] = []

    def opener(request: object, **_kwargs: object) -> _Response:
        calls.append(request)
        return _Response(_wikidata_body())

    candidates, stats = fetch_wikidata_candidates(
        [{"organisation_number": "923609016"}, {"organisation_number": "923609024"}],
        opener=opener,
        chunk_size=200,
    )
    assert candidates == {"923609016": [{"url": "https://example.no/", "source": "wikidata_p856"}]}
    assert stats["chunks"] == 1
    assert len(calls) == 1


def test_wikidata_failure_is_fail_open() -> None:
    def opener(*_args: object, **_kwargs: object) -> _Response:
        raise TimeoutError("offline")

    candidates, stats = fetch_wikidata_candidates([{"organisation_number": "923609016"}], opener=opener)
    assert candidates == {}
    assert stats["error"] == "TimeoutError"


def test_fagfolkguiden_jsonld_and_slug_are_deterministic() -> None:
    html = '<script type="application/ld+json">{"@type":"LocalBusiness","url":"https://fagfolkguiden.no/bedrift/x","sameAs":["https://right.example.no/","https://www.fagfolkguiden.no/ignore"]}</script>'
    assert parse_fagfolkguiden_jsonld(html) == ["https://right.example.no/"]
    assert fagfolkguiden_slug("Ærlig VVS & Bygg AS") == "aerlig-vvs-bygg"


def test_subunit_email_and_website_domains_are_candidates() -> None:
    record = {
        "organisation_number": "923609016",
        "name": "Example AS",
        "raw": {},
        "evidence": {
                "locations": {"value": {"locations": [{"website": "https://branch.example.no/", "email": "hello@branch-mail.no"}]}}
        },
    }
    sources = {item["source"] for item in candidate_domains(record)}
    domains = {item["domain"] for item in candidate_domains(record)}
    assert "subunit_website_or_email_domain" in sources
    assert {"example.no", "branch.example.no", "branch-mail.no"}.intersection(domains)
