from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.web.website import HostRequestPolicy  # noqa: E402
from scripts.run.run_search_discovery import WebsiteFetchCache, run_worker_pool  # noqa: E402


class WorkerPoolTests(unittest.TestCase):
    def test_order_is_preserved_and_resume_skips_completed_rows(self) -> None:
        rows = [{"organisation_number": str(index)} for index in range(6)]
        calls: list[str] = []
        existing = {"2": {"row": {"organisation_number": "2"}, "resumed": True}}

        def worker(row):
            calls.append(row["organisation_number"])
            time.sleep((5 - int(row["organisation_number"])) * 0.005)
            return {"row": row, "completed": True}

        results = run_worker_pool(rows, worker, workers=3, company_timeout=1, existing=existing)
        self.assertEqual([result["row"]["organisation_number"] for result in results], [str(index) for index in range(6)])
        self.assertNotIn("2", calls)
        self.assertTrue(results[2]["resumed"])

    def test_hung_worker_times_out_without_stalling_other_workers(self) -> None:
        rows = [{"organisation_number": str(index)} for index in range(5)]

        def worker(row):
            if row["organisation_number"] == "0":
                time.sleep(0.4)
            return {"row": row, "completed": True}

        started = time.monotonic()
        results = run_worker_pool(rows, worker, workers=3, company_timeout=0.03)
        elapsed = time.monotonic() - started
        by_org = {result["row"]["organisation_number"]: result for result in results}
        self.assertLess(elapsed, 0.25)
        self.assertEqual(by_org["0"]["outcome"], "timed_out")
        self.assertTrue(all(by_org[str(index)].get("completed") for index in range(1, 5)))


class HostPolicyTests(unittest.TestCase):
    def test_per_host_inflight_limit_and_interval(self) -> None:
        policy = HostRequestPolicy(min_interval=0.01, max_inflight=2)
        active = 0
        maximum = 0
        lock = threading.Lock()
        starts: list[float] = []

        def request() -> None:
            nonlocal active, maximum
            with policy.request("https://shared.example.no"):
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                    starts.append(time.monotonic())
                time.sleep(0.02)
                with lock:
                    active -= 1

        threads = [threading.Thread(target=request) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertLessEqual(maximum, 2)
        self.assertGreaterEqual(min(b - a for a, b in zip(starts, starts[1:])), 0.009)

    def test_robots_are_cached_per_host(self) -> None:
        policy = HostRequestPolicy()
        with patch("norway_company_agent.web.website._robots_policy", return_value=(True, ["https://example.no/sitemap.xml"])) as robots:
            self.assertEqual(policy.robots("https://example.no", 1), (True, ["https://example.no/sitemap.xml"]))
            self.assertEqual(policy.robots("https://example.no/contact", 1), (True, ["https://example.no/sitemap.xml"]))
        self.assertEqual(robots.call_count, 1)

    def test_candidate_domain_is_fetched_once(self) -> None:
        cache = WebsiteFetchCache()
        website = {"status": "available", "value": {"final_url": "https://example.no/"}}
        operations = {"requests": 3, "latencies_ms": [1]}
        with patch("scripts.run.run_search_discovery.fetch_website", return_value=(website, operations)) as fetch:
            first = cache.fetch("https://www.example.no", timeout=1)
            second = cache.fetch("https://example.no/contact", timeout=1)
        self.assertFalse(first[2])
        self.assertTrue(second[2])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(cache.misses, 1)
        self.assertEqual(cache.hits, 1)


if __name__ == "__main__":
    unittest.main()
