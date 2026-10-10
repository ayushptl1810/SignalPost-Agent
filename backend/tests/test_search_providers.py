from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from argparse import Namespace
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.search.pool import ProviderPool, ProviderMember, ProviderUsageStore  # noqa: E402
from norway_company_agent.search.providers import (  # noqa: E402
    BraveSearchProvider,
    LinkupSearchProvider,
    ProviderFatalError,
    ProviderTransientError,
    SerpApiSearchProvider,
    SerperSearchProvider,
    TavilySearchProvider,
    parse_brave_payload,
    parse_linkup_payload,
    parse_serpapi_payload,
    parse_serper_payload,
    parse_tavily_payload,
)
from norway_company_agent.web.candidates import should_skip_search_triage  # noqa: E402
from scripts.run.run_search_discovery import SearchResultCache, search_stage  # noqa: E402
from scripts.analysis.score_discovery_run import classify_row  # noqa: E402


class SearchProviderParsingTests(unittest.TestCase):
    def test_serper_parser_drops_sponsored_results(self):
        results = parse_serper_payload({"organic": [
            {"position": 1, "link": "https://one.no", "title": "One", "snippet": ""},
            {"position": 2, "link": "https://ad.no", "title": "Ad", "snippet": "", "sponsored": True},
        ]}, query="q", provider="serper")
        self.assertEqual([item["url"] for item in results], ["https://one.no"])
        self.assertEqual(results[0]["provider"], "serper")

    def test_serpapi_parser_uses_organic_results_only(self):
        results = parse_serpapi_payload({
            "ads": [{"link": "https://ad.no", "title": "Ad"}],
            "organic_results": [{"position": 1, "link": "https://one.no", "title": "One", "snippet": "S"}],
        }, query="q", provider="serpapi")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["position"], 1)

    def test_tavily_linkup_and_brave_parsers_normalize_results(self):
        tavily = parse_tavily_payload({"results": [{"url": "https://t.no", "title": "T", "content": "C"}]}, query="q", provider="tavily")
        linkup = parse_linkup_payload({"results": [{"url": "https://l.no", "name": "L", "content": "C"}]}, query="q", provider="linkup")
        brave = parse_brave_payload({"web": {"results": [{"url": "https://b.no", "title": "B", "description": "C"}]}}, query="q", provider="brave")
        self.assertEqual([item["url"] for item in [*tavily, *linkup, *brave]], ["https://t.no", "https://l.no", "https://b.no"])
        self.assertEqual([item["position"] for item in [*tavily, *linkup, *brave]], [1, 1, 1])

    def test_provider_adapters_build_provider_specific_requests(self):
        captured = []

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"organic": [{"link": "https://one.no", "title": "One"}]}).encode()

        def opener(request, timeout):
            captured.append(request)
            return Response()

        SerperSearchProvider("secret", opener=opener).search("q", country="no", language="no", count=3)
        self.assertEqual(captured[-1].method, "POST")
        self.assertNotIn("secret", captured[-1].full_url)
        self.assertEqual(json.loads(captured[-1].data)["num"], 3)

    def test_http_error_keeps_redacted_body_and_quota_headers(self):
        def opener(_request, *, timeout):
            raise urllib.error.HTTPError(
                "https://api.example.test", 429, "Too Many", {"X-RateLimit-Remaining": "0"},
                __import__("io").BytesIO(b'{"message":"secret-key quota exceeded"}'),
            )

        with self.assertRaises(ProviderFatalError) as context:
            SerperSearchProvider("secret-key", opener=opener).search("q", country="no", language="no", count=1)
        error = context.exception
        self.assertEqual(error.reason, "quota_exhausted")
        self.assertNotIn("secret-key", error.body_excerpt)
        self.assertEqual(error.headers["x-ratelimit-remaining"], "0")

    def test_fatal_and_transient_errors_have_shared_types(self):
        self.assertTrue(issubclass(ProviderFatalError, RuntimeError))
        self.assertTrue(issubclass(ProviderTransientError, RuntimeError))


class SearchProviderPoolTests(unittest.TestCase):
    def member(self, name: str, key: str, allowance: int = 10):
        class Fake:
            storage_allowed = True

            def __init__(self, provider_name):
                self.name = provider_name
                self.calls = 0

            def search(self, query, *, country, language, count, timeout):
                self.calls += 1
                return [{"url": f"https://{self.name}.no", "title": self.name, "snippet": "", "position": 1, "provider": self.name, "query": query}], {"status": 200, "latency_ms": 1}

        fake = Fake(name)
        return ProviderMember(provider=name, key=key, adapter=fake, allowance=allowance, reset_policy="monthly")

    def test_weighted_rotation_prefers_member_with_more_remaining_fraction(self):
        first = self.member("one", "key-one", allowance=10)
        second = self.member("two", "key-two", allowance=10)
        first.used = 9
        pool = ProviderPool([first, second], rotation="weighted", usage_store=ProviderUsageStore(None))
        result, operation = pool.search("q", country="no", language="no", count=1, timeout=1)
        self.assertEqual(result[0]["provider"], "two")
        self.assertEqual(operation["key_id"], second.key_id)

    def test_fatal_member_is_exhausted_and_query_moves_to_next(self):
        class Fatal:
            storage_allowed = True
            name = "one"

            def search(self, *_args, **_kwargs):
                raise ProviderFatalError("quota_exhausted")

        first = ProviderMember(provider="one", key="secret-one", adapter=Fatal(), allowance=10, reset_policy="monthly")
        second = self.member("two", "secret-two")
        with tempfile.TemporaryDirectory() as folder:
            store = ProviderUsageStore(Path(folder) / "provider-usage.json")
            pool = ProviderPool([first, second], rotation="priority", usage_store=store)
            _results, operation = pool.search("q", country="no", language="no", count=1, timeout=1)
            saved = json.loads((Path(folder) / "provider-usage.json").read_text())
        self.assertEqual(operation["provider"], "two")
        self.assertEqual(saved["members"][0]["status"], "disabled")
        self.assertNotIn("secret-one", json.dumps(saved))

    def test_all_exhausted_is_shared_fatal(self):
        class Fatal:
            storage_allowed = True
            name = "one"

            def search(self, *_args, **_kwargs):
                raise ProviderFatalError("credits_exhausted")

        members = [ProviderMember(provider="one", key="a", adapter=Fatal(), allowance=1, reset_policy="one_time"), ProviderMember(provider="two", key="b", adapter=Fatal(), allowance=1, reset_policy="one_time")]
        pool = ProviderPool(members, rotation="priority", usage_store=ProviderUsageStore(None))
        with self.assertRaisesRegex(ProviderFatalError, "all_exhausted"):
            pool.search("q", country="no", language="no", count=1, timeout=1)

    def test_quota_failure_is_one_attempt_and_permanent_for_pinned_run(self):
        class Quota:
            storage_allowed = True
            name = "quota"

            def __init__(self):
                self.calls = 0

            def search(self, *_args, **_kwargs):
                self.calls += 1
                raise ProviderFatalError("quota_exhausted", status=429)

        adapter = Quota()
        member = ProviderMember(provider="one", key="key", adapter=adapter, allowance=10, reset_policy="monthly")
        pool = ProviderPool([member], rotation="priority", provider_pin="one", usage_store=ProviderUsageStore(None))
        with self.assertRaisesRegex(ProviderFatalError, "all_exhausted"):
            pool.search("q", country="no", language="no", count=1, timeout=1)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(member.status, "disabled")
        self.assertIsNone(member.exhausted_until)

    def test_pinned_pool_uses_only_requested_provider(self):
        pool = ProviderPool([self.member("one", "a"), self.member("two", "b")], rotation="weighted", provider_pin="two", usage_store=ProviderUsageStore(None))
        _results, operation = pool.search("q", country="no", language="no", count=1, timeout=1)
        self.assertEqual(operation["provider"], "two")
        self.assertEqual(pool.report()["rotation"], "off")

    def test_transient_failure_falls_through_without_exhausting_member(self):
        class Transient:
            storage_allowed = True
            name = "one"

            def search(self, *_args, **_kwargs):
                raise ProviderTransientError("timeout")

        first = ProviderMember(provider="one", key="a", adapter=Transient(), allowance=10)
        second = self.member("two", "b")
        pool = ProviderPool([first, second], rotation="priority", usage_store=ProviderUsageStore(None), sleep=lambda _seconds: None)
        _results, operation = pool.search("q", country="no", language="no", count=1, timeout=1)
        self.assertEqual(operation["provider"], "two")
        self.assertEqual(first.status, "active")

    def test_key_id_is_hashed_and_reset_date_reactivates_member(self):
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        member = self.member("one", "super-secret", allowance=1)
        member.status = "exhausted"
        member.exhausted_until = "2026-11-01T00:00:00Z"
        pool = ProviderPool([member], rotation="priority", usage_store=ProviderUsageStore(None), now=lambda: now)
        self.assertEqual(pool.available_members()[0].key_id, member.key_id)
        self.assertNotIn("super-secret", member.key_id)


class SearchTriageTests(unittest.TestCase):
    def test_s2_without_registry_signals_is_skipped(self):
        row = {"legal_form": "AS", "employees": None, "industry_code": "68.209", "website": "", "evidence": {"registry": {"value": {}}}}
        self.assertTrue(should_skip_search_triage(row, {}))

    def test_s3_with_registry_email_or_nav_homepage_is_not_skipped(self):
        row = {"organisation_number": "123", "legal_form": "AS", "employees": None, "industry_code": "64.200", "website": "", "evidence": {"registry": {"value": {"epostadresse": "info@company.no"}}}}
        self.assertFalse(should_skip_search_triage(row, {}))
        row["evidence"]["registry"]["value"]["epostadresse"] = ""
        self.assertFalse(should_skip_search_triage(row, {"123": {"homepages": ["https://company.no"]}}))


class SearchRunnerQueryTests(unittest.TestCase):
    def test_default_uses_one_local_query_and_stops_after_candidate(self):
        calls = []

        class FakePool:
            def search(self, query, **_kwargs):
                calls.append(query)
                return ([{"url": "https://company.no", "title": "Norsk Fiskeeksport AS", "snippet": "NOTODDEN", "position": 1, "provider": "fake"}], {"status": 200, "latency_ms": 1, "provider": "fake", "query_sha256": "x"})

        args = Namespace(count=10, max_candidates=3, timeout=2.0, two_queries=False, provider=None, replay_only=False, cache_urls_only=False)
        selected, _summary, failed, _timed_out = search_stage(
            {"name": "Norsk Fiskeeksport AS", "organisation_number": "923609016", "municipality": "NOTODDEN"},
            "", args, Counter(), [], set(), provider_pool=FakePool(),
        )
        self.assertFalse(failed)
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("NOTODDEN", calls[0])


class SearchCachePolicyTests(unittest.TestCase):
    def test_brave_style_provider_is_not_cached_and_urls_only_omits_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "search.jsonl"
            cache = SearchResultCache(path)
            self.assertFalse(cache.put("q", [{"url": "https://b.no", "title": "Title", "snippet": "Text", "position": 1}], num=3, provider="brave", storage_allowed=False))
            self.assertFalse(path.exists())
            self.assertTrue(cache.put("q", [{"url": "https://b.no", "title": "Title", "snippet": "Text", "position": 1}], num=3, provider="serper", urls_only=True))
            stored = json.loads(path.read_text().splitlines()[0])
            self.assertEqual(stored["results"], [{"url": "https://b.no", "position": 1, "provider": None}])

    def test_triage_skip_is_a_distinct_non_provider_outcome(self):
        row = {"organisation_number": "1", "evidence": {"website_discovery": {"status": "not_found", "value": {"search_skipped": "triage"}}}}
        self.assertEqual(classify_row(row)["outcome"], "search_skipped_triage")


if __name__ == "__main__":
    unittest.main()
