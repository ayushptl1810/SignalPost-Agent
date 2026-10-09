from __future__ import annotations

import socket
import sys
import unittest
from urllib.error import URLError
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.evidence import evidence  # noqa: E402
from norway_company_agent.web.candidates import name_domain_candidates  # noqa: E402
from norway_company_agent.web import website as website_module  # noqa: E402
from norway_company_agent.web.website import network_failure_class  # noqa: E402


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
if __name__ == "__main__":
    unittest.main()
