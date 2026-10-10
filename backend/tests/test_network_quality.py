from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.web.website import (  # noqa: E402
    NetworkPreflightError,
    ResolutionFailureBreaker,
    fetch_website,
    network_preflight,
)


class NetworkQualityTests(unittest.TestCase):
    def test_dns_failure_is_failed_not_blocked(self) -> None:
        with patch("norway_company_agent.web.website.socket.getaddrinfo", side_effect=socket.gaierror("no DNS")):
            record, metrics = fetch_website("https://missing.example", timeout=1)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(metrics["failure_kind"], "resolution")

    def test_preflight_aborts_when_known_host_cannot_resolve(self) -> None:
        with patch("norway_company_agent.web.website.socket.getaddrinfo", side_effect=socket.gaierror("no DNS")):
            with self.assertRaises(NetworkPreflightError):
                network_preflight(("data.brreg.no", "example.com"))

    def test_resolution_breaker_fires_above_twenty_percent(self) -> None:
        breaker = ResolutionFailureBreaker(threshold=0.20, minimum_samples=5)
        for _ in range(3):
            breaker.observe({})
        breaker.observe({"failure_kind": "resolution"})
        with self.assertRaises(NetworkPreflightError):
            breaker.observe({"failure_kind": "resolution"})


if __name__ == "__main__":
    unittest.main()
