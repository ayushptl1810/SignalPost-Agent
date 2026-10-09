from __future__ import annotations

import socket
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.error import URLError
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.core.evidence import evidence  # noqa: E402
from norway_company_agent.discovery import process_record  # noqa: E402
from norway_company_agent.web.candidates import name_domain_candidates  # noqa: E402
from norway_company_agent.web import website as website_module  # noqa: E402
from norway_company_agent.web.website import network_failure_class  # noqa: E402
from scripts.run import run_competition_batch as batch  # noqa: E402


def profile(name: str = "Radiatorservice AS") -> dict:
    return {"organisation_number": "912345678", "name": name, "evidence": {}}


class FailureClassificationTests(unittest.TestCase):
    def test_dns_and_network_errors_have_permanent_or_transient_classes(self) -> None:
        permanent = socket.gaierror(getattr(socket, "EAI_NONAME", -2), "Name or service not known")
        transient_dns = socket.gaierror(getattr(socket, "EAI_AGAIN", -3), "Temporary failure in name resolution")
        servfail = socket.gaierror(getattr(socket, "EAI_FAIL", -4), "SERVFAIL")
        cases = [
            (permanent, "permanent"),
            (transient_dns, "transient"),
            (servfail, "transient"),
            (URLError("NXDOMAIN"), "permanent"),
            (URLError("SERVFAIL"), "transient"),
            (TimeoutError("timed out"), "transient"),
            (ConnectionResetError("connection reset by peer"), "transient"),
        ]
        for error, expected in cases:
            with self.subTest(error=repr(error)):
                self.assertEqual(network_failure_class(error), expected)

    def test_transient_name_resolution_is_retained_as_a_candidate(self) -> None:
        def fake_resolver(host: str) -> dict[str, object]:
            if host == "radiatorservice.no":
                return {"resolved": False, "failure_class": "transient"}
            return {"resolved": False, "failure_class": "permanent"}

        found = name_domain_candidates(profile(), resolves=fake_resolver, limit=1)
        self.assertEqual([item["registered_domain"] for item in found], ["radiatorservice.no"])
        self.assertEqual(found[0]["failure_class"], "transient")


class InCandidateRetryTests(unittest.TestCase):
    def _failed(self) -> tuple[dict, dict]:
        return (
            evidence("website", "failed", "registry_linked_company_website", "https://radiatorservice.no/", note="temporary DNS"),
            {"requests": 1, "bytes": 0, "latencies_ms": [1], "failure_kind": "resolution", "failure_class": "transient"},
        )

    def _available(self) -> tuple[dict, dict]:
        return (
            evidence("website", "available", "registry_linked_company_website", "https://radiatorservice.no/", value={"final_url": "https://radiatorservice.no/"}),
            {"requests": 1, "bytes": 10, "latencies_ms": [1]},
        )

    def test_transient_failure_is_retried_and_success_is_returned(self) -> None:
        with patch.object(website_module, "_fetch_website_once", side_effect=[self._failed(), self._available()]) as fetch, patch.object(website_module.time, "sleep"):
            result, metrics = website_module.fetch_website("https://radiatorservice.no/", timeout=1.0)
        self.assertEqual(result["status"], "available")
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(metrics["retry_count"], 1)

    def test_permanent_failure_is_not_retried(self) -> None:
        permanent = (
            evidence("website", "failed", "registry_linked_company_website", "https://missing.example/", note="NXDOMAIN"),
            {"requests": 1, "bytes": 0, "latencies_ms": [1], "failure_kind": "resolution", "failure_class": "permanent"},
        )
        with patch.object(website_module, "_fetch_website_once", return_value=permanent) as fetch, patch.object(website_module.time, "sleep"):
            result, metrics = website_module.fetch_website("https://missing.example/", timeout=1.0)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(metrics["retry_count"], 0)

    def test_successful_candidate_has_no_retry_overhead(self) -> None:
        with patch.object(website_module, "_fetch_website_once", return_value=self._available()) as fetch, patch.object(website_module.time, "sleep") as sleep:
            result, metrics = website_module.fetch_website("https://radiatorservice.no/", timeout=1.0)
        self.assertEqual(result["status"], "available")
        self.assertEqual(fetch.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(metrics["retry_count"], 0)


class SecondChanceFixtureTests(unittest.TestCase):
    def test_transient_fetch_then_success_publishes_with_fake_fetcher(self) -> None:
        record = {
            "organisation_number": "912345678",
            "name": "Radiatorservice AS",
            "website": "https://radiatorservice.no/",
            "raw": {"hjemmeside": "https://radiatorservice.no/"},
        }
        calls = 0

        def flaky_fetch(url: str, **_kwargs: object) -> tuple[dict, dict]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return evidence("website", "failed", "registry_linked_company_website", url, note="temporary DNS"), {
                    "requests": 1, "bytes": 0, "latencies_ms": [1], "failure_kind": "resolution", "failure_class": "transient",
                }
            text = "Radiatorservice AS Organisasjonsnummer 912345678 Main Street 1 0123 Oslo"
            return evidence("website", "available", "registry_linked_company_website", url, value={
                "final_url": url, "title": "Radiatorservice AS", "main_text_excerpt": text, "identity_text_excerpt": text,
                "pages": [],
            }), {"requests": 1, "bytes": 100, "latencies_ms": [1]}

        first = process_record(record, gate="g4", fetcher=flaky_fetch, resolved_domains={"radiatorservice.no": {"resolved": True}})
        self.assertEqual(first["states"]["official_website"], "failed")
        second = process_record(record, gate="g4", fetcher=flaky_fetch, resolved_domains={"radiatorservice.no": {"resolved": True}})
        self.assertEqual(second["states"]["official_website"], "available")
        self.assertEqual(second["website"]["evidence"]["status"], "available")
        self.assertEqual(calls, 2)

    def test_reresolver_failure_once_then_success_is_available_for_retry(self) -> None:
        state = {"1": {"discovery": {"state": "not_available", "candidates": [
            {"source": "name_derived_no", "state": "not_available", "failure_kind": "resolution", "failure_class": "permanent", "requested_url": "https://radiatorservice.no/"},
        ]}}}
        responses = iter([
            {"resolved": False, "failure_kind": "resolution", "failure_class": "permanent"},
            {"resolved": True, "addresses": ["203.0.113.7"], "failure_kind": None, "failure_class": None},
        ])
        with patch.object(batch, "resolve_host", side_effect=lambda _host, **_kwargs: next(responses)):
            first = batch._reresolve_second_chance_name_hosts(state, ["1"], workers=8, timeout=0.5)
            second = batch._reresolve_second_chance_name_hosts(state, ["1"], workers=8, timeout=0.5)
        self.assertFalse(first["1"]["radiatorservice.no"]["resolved"])
        self.assertTrue(second["1"]["radiatorservice.no"]["resolved"])

    def test_timeout_without_class_then_success_publishes_on_second_discovery(self) -> None:
        record = {
            "organisation_number": "912345678",
            "name": "Radiatorservice AS",
            "website": "https://radiatorservice.no/",
            "raw": {"hjemmeside": "https://radiatorservice.no/"},
        }
        calls = 0

        def flaky_timeout(url: str, **_kwargs: object) -> tuple[dict, dict]:
            nonlocal calls
            calls += 1
            if calls <= 2:
                return evidence("website", "failed", "registry_linked_company_website", url, note="fetch timed out"), {
                    "requests": 1, "bytes": 0, "latencies_ms": [1], "failure_kind": "timeout",
                }
            text = "Radiatorservice AS Organisasjonsnummer 912345678 Main Street 1 0123 Oslo"
            return evidence("website", "available", "registry_linked_company_website", url, value={
                "final_url": url, "title": "Radiatorservice AS", "main_text_excerpt": text, "identity_text_excerpt": text,
                "pages": [],
            }), {"requests": 1, "bytes": 100, "latencies_ms": [1]}

        first = process_record(record, gate="g4", fetcher=flaky_timeout, resolved_domains={"radiatorservice.no": {"resolved": True}})
        self.assertEqual(first["states"]["official_website"], "failed")
        self.assertEqual(first["candidates"][0]["failure_kind"], "timeout")
        second = process_record(record, gate="g4", fetcher=flaky_timeout, resolved_domains={"radiatorservice.no": {"resolved": True}})
        self.assertEqual(second["states"]["official_website"], "available")

    def test_permanent_nxdomain_is_skipped_without_a_fetch(self) -> None:
        record = {"organisation_number": "912345678", "name": "Radiatorservice AS", "website": ""}
        calls = []

        def should_not_fetch(*_args: object, **_kwargs: object):
            calls.append(True)
            raise AssertionError("permanent NXDOMAIN must not be fetched")

        result = process_record(
            record,
            fetcher=should_not_fetch,
            resolved_domains={"radiatorservice.no": {"resolved": False, "failure_kind": "resolution", "failure_class": "permanent"}},
        )
        self.assertEqual(result["states"]["official_website"], "not_available")
        self.assertEqual(calls, [])


class DNSConcurrencyTests(unittest.TestCase):
    def test_dns_slots_are_process_wide_and_default_to_32(self) -> None:
        self.assertEqual(website_module.MAX_CONCURRENT_DNS_LOOKUPS, 32)
        active = 0
        peak = 0
        lock = threading.Lock()

        def lookup() -> None:
            nonlocal active, peak
            with website_module._dns_slot(timeout=1.0):
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.01)
                with lock:
                    active -= 1

        with patch.object(website_module, "GLOBAL_DNS_SEMAPHORE", threading.BoundedSemaphore(2)):
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _item: lookup(), range(8)))
        self.assertLessEqual(peak, 2)

if __name__ == "__main__":
    unittest.main()
