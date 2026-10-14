from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.search.optional import PROVIDER_ENV, OptionalProviderMember, OptionalSearchPool  # noqa: E402
from norway_company_agent.search.providers import ProviderTransientError  # noqa: E402
from norway_company_agent.web.discovery import choose_search_candidates  # noqa: E402
from scripts.run.run_competition_batch import run_optional_search_for_profile  # noqa: E402


class _Fail429:
    def search(self, *_args: object, **_kwargs: object):
        raise ProviderTransientError("rate limited", status=429)


class _Fail500:
    def search(self, *_args: object, **_kwargs: object):
        raise ProviderTransientError("down", status=500)


class _Success:
    def search(self, query: str, **_kwargs: object):
        return [{"url": "https://right.example.no/", "title": "Right Example AS", "snippet": "Right Example AS", "position": 1}], {"bytes": 10, "latency_ms": 1}


def test_absent_keys_create_zero_call_pool() -> None:
    pool = OptionalSearchPool.from_environment(environ={"BRAVE_API_KEY": "", "SERPER_API_KEY": ""}, max_queries=10)
    results, operation = pool.search('"Example AS" 923609016')
    assert results == []
    assert operation["error"] == "no_keys"
    assert pool.queries == 0


def test_plural_key_pools_split_on_commas_and_whitespace_without_logging_values() -> None:
    environment = {
        "SERPER_API_KEYS": "serper-one, serper-two\tserper-three",
        "SERPAPI_API_KEYS": "serpapi-one serpapi-two",
        "BRAVE_API_KEYS": "brave-one",
        "BRAVE_SEARCH_API_KEYS": "brave-search-one",
        "TAVILY_API_KEYS": "tavily-one",
    }

    pool = OptionalSearchPool.from_environment(environ=environment, factory=lambda _provider, _key: object())

    assert [(member.provider, member.key) for member in pool.members] == [
        ("brave", "brave-one"),
        ("brave", "brave-search-one"),
        ("serper", "serper-one"),
        ("serper", "serper-two"),
        ("serper", "serper-three"),
        ("tavily", "tavily-one"),
        ("serpapi", "serpapi-one"),
        ("serpapi", "serpapi-two"),
    ]
    report = pool.report()
    assert all(key not in json.dumps(report) for key in {
        "brave-one", "brave-search-one", "serper-one", "serper-two", "serper-three", "tavily-one", "serpapi-one", "serpapi-two",
    })
    assert "SERPER_API_KEYS" in PROVIDER_ENV["serper"]
    assert "BRAVE_SEARCH_API_KEYS" in PROVIDER_ENV["brave"]


def test_generic_search_key_uses_the_selected_provider_only() -> None:
    pool = OptionalSearchPool.from_environment(
        environ={"SIGNALPOST_SEARCH_API_KEY": "generic-key", "SIGNALPOST_SEARCH_PROVIDER": "brave"},
        factory=lambda provider, key: {"provider": provider, "key": key},
    )

    assert [(member.provider, member.key) for member in pool.members] == [("brave", "generic-key")]
    assert pool.report()["members"][0]["key_id"] != "generic-key"
    assert "generic-key" not in json.dumps(pool.report())


def test_429_rotates_to_next_key_without_exposing_key() -> None:
    pool = OptionalSearchPool([OptionalProviderMember("serper", "secret-one", _Fail429()), OptionalProviderMember("serper", "secret-two", _Success())], max_queries=3)
    results, operation = pool.search('"Example AS" 923609016')
    assert results and operation["provider"] == "serper"
    assert pool.members[0].disabled is True
    assert all("secret" not in str(pool.report()) for _ in [0])


def test_three_provider_failures_open_circuit() -> None:
    pool = OptionalSearchPool([OptionalProviderMember("serper", "key", _Fail500())], max_queries=10)
    for _ in range(3):
        pool.search("Example")
    assert pool.circuits == {"serper"}
    assert pool.search("Example")[1]["error"] == "search_unavailable"


def test_directory_is_prefiltered_and_provider_failure_does_not_mutate_profile() -> None:
    profile = {"organisation_number": "923609016", "name": "Example AS", "municipality": "Oslo", "evidence": {}, "claims": {}}
    directory = choose_search_candidates(profile, [{"url": "https://proff.no/company/example", "title": "Example AS", "snippet": "Example AS"}])
    assert directory["selected"] == []

    class BrokenPool:
        def search(self, *_args: object, **_kwargs: object):
            return [], {"error": "search_unavailable", "provider_attempts": 1, "search_queries": 0}

    before = repr(profile)
    result, _operation = run_optional_search_for_profile(profile, pool=BrokenPool())
    assert result is None
    assert repr(profile) == before


def test_accepted_search_candidate_still_needs_the_identity_gate() -> None:
    profile = {
        "organisation_number": "923609016",
        "name": "Example AS",
        "municipality": "Oslo",
        "raw": {"organisasjonsnummer": "923609016", "navn": "Example AS"},
        "evidence": {},
        "claims": {},
    }

    class GoodPool:
        def search(self, *_args: object, **_kwargs: object):
            return [{"url": "https://example.no/", "title": "Example AS", "snippet": "Example AS 923 609 016"}], {"provider_attempts": 1, "search_queries": 1, "provider": "serper"}

    fetched = {"status": "available", "source_url": "https://example.no/", "value": {"final_url": "https://example.no/", "title": "Example AS", "main_text_excerpt": "Example AS Org.nr 923 609 016", "identity_text_excerpt": "Example AS Org.nr 923 609 016", "pages": []}}
    with patch("scripts.run.run_competition_batch.fetch_website", return_value=(fetched, {"requests": 1, "bytes": 10, "latencies_ms": [1]})):
        result, operation = run_optional_search_for_profile(profile, pool=GoodPool())
    assert result is not None
    assert operation["published"] == 1
    assert profile["claims"] == {}


def test_musikk_search_candidate_publishes_fetched_site_not_search_result_url() -> None:
    profile = {
        "organisation_number": "926790137",
        "name": "Musikk i Innlandet AS",
        "municipality": "Lillehammer",
        "postal_code": "2609",
        "raw": {
            "organisasjonsnummer": "926790137",
            "navn": "Musikk i Innlandet AS",
            "forretningsadresse.postnummer": "2609",
            "forretningsadresse.poststed": "Lillehammer",
        },
        "evidence": {},
        "claims": {},
    }
    site_url = "https://musikkinnlandet.no/"
    fetched = {
        "status": "available",
        "source_url": site_url,
        "value": {
            "final_url": site_url,
            "title": "Musikk i Innlandet",
            "main_text_excerpt": "Musikk i Innlandet Org.nr 926 790 137 2609 Lillehammer",
            "identity_text_excerpt": "Musikk i Innlandet Org.nr 926 790 137",
            "pages": [],
        },
    }

    class GoodPool:
        def search(self, *_args: object, **_kwargs: object):
            return [
                {
                    "url": site_url,
                    "title": "Musikk i Innlandet",
                    "snippet": "Musikk i Innlandet 926 790 137",
                    "source_url": "https://search.invalid/result/1",
                    "position": 1,
                }
            ], {"provider_attempts": 1, "search_queries": 1, "provider": "fake"}

    with patch("scripts.run.run_competition_batch.fetch_website", return_value=(fetched, {"requests": 1, "bytes": 10, "latencies_ms": [1]})):
        result, operation = run_optional_search_for_profile(profile, pool=GoodPool())

    assert result is not None
    assert result["evidence"]["source_url"] == site_url
    assert "search.invalid" not in json.dumps(result["evidence"])
    assert operation["published"] == 1


def test_search_page_that_fails_identity_gate_is_not_published() -> None:
    profile = {
        "organisation_number": "926790137",
        "name": "Musikk i Innlandet AS",
        "municipality": "Lillehammer",
        "raw": {"forretningsadresse.postnummer": "2609", "forretningsadresse.poststed": "Lillehammer"},
        "evidence": {},
        "claims": {},
    }
    wrong_page = {
        "status": "available",
        "source_url": "https://musikkinnlandet.no/",
        "value": {
            "final_url": "https://musikkinnlandet.no/",
            "title": "Another Organisation",
            "main_text_excerpt": "Another Organisation Org.nr 999 999 999 2609 Lillehammer",
            "identity_text_excerpt": "Another Organisation Org.nr 999 999 999",
            "pages": [],
        },
    }

    class CandidatePool:
        def search(self, *_args: object, **_kwargs: object):
            return [{"url": "https://musikkinnlandet.no/", "title": "Musikk i Innlandet", "snippet": "Musikk i Innlandet", "position": 1}], {"provider_attempts": 1, "search_queries": 1}

    before = repr(profile)
    with patch("scripts.run.run_competition_batch.fetch_website", return_value=(wrong_page, {"requests": 1, "bytes": 10, "latencies_ms": [1]})):
        result, _operation = run_optional_search_for_profile(profile, pool=CandidatePool())

    assert result is None
    assert repr(profile) == before
