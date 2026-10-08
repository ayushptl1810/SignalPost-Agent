from __future__ import annotations

import csv
import gzip
import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.external.nav_jobs import load_index  # noqa: E402
from norway_company_agent.registry.batch import profiles_from_bulk, read_organisation_inputs, terminal_envelope, validate_envelopes  # noqa: E402
from scripts.analysis.envelope_audit import audit  # noqa: E402
from scripts.analysis.website_state_report import build_report  # noqa: E402
from scripts.run.run_competition_batch import _fresh_nav_index, _input_error_profile  # noqa: E402


class SubmissionHardeningTests(unittest.TestCase):
    def test_tolerant_input_keeps_empty_malformed_duplicate_and_non_numeric_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "organisations.jsonl"
            path.write_text('923609016\n"not-an-org"\noops\n\n{"organisation_number":"923609016"}\n', encoding="utf-8")
            rows = read_organisation_inputs(path, tolerant=True)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0], {"organisation_number": "923609016"})
        self.assertEqual(rows[1]["input_error"]["kind"], "invalid_organisation_number")
        self.assertEqual(rows[2]["input_error"]["kind"], "malformed_json")
        self.assertEqual(rows[3]["input_error"]["kind"], "empty_line")
        self.assertEqual(rows[4]["input_error"]["kind"], "duplicate_input")

    def test_absent_registry_row_and_input_error_have_terminal_module_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bulk = Path(directory) / "bulk.csv.gz"
            with gzip.open(bulk, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["organisasjonsnummer", "navn", "organisasjonsform.kode"])
                writer.writeheader()
                writer.writerow({"organisasjonsnummer": "923609016", "navn": "Present AS", "organisasjonsform.kode": "AS"})
            profiles, _ = profiles_from_bulk(bulk, ["923609016", "999999999"], allow_missing=True)
        self.assertTrue(profiles[1]["_missing_registry"])
        envelope = terminal_envelope(
            _input_error_profile({"organisation_number": "", "input_error": {"kind": "malformed_json"}}, ["registry", "website"]),
            run_id="test",
            modules=["registry", "website"],
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T00:00:01Z",
        )
        self.assertEqual(envelope["state"], "submission_error")
        self.assertEqual({item["state"] for item in envelope["modules"].values()}, {"source_error"})
        self.assertTrue(validate_envelopes([envelope], 1)["passed"])

    def test_financial_claim_is_byte_identical_and_auditable(self) -> None:
        record = {"period": "2025", "currency": "NOK", "revenue": 12345}
        profile = {
            "organisation_number": "923609016",
            "evidence": {
                "registry": {"status": "available", "source_url": "https://registry.test", "source_class": "official", "retrieved_at": "2026-01-01T00:00:00Z", "value": {"organisation_number": "923609016"}},
                "financials": {"status": "available", "source_url": "https://accounts.test", "source_class": "official", "retrieved_at": "2026-01-01T00:00:00Z", "value": {"records": [record]}},
            },
        }
        envelope = terminal_envelope(profile, run_id="audit", modules=["registry", "financials"], started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:01Z")
        self.assertTrue(validate_envelopes([envelope], 1)["passed"])
        result = audit([envelope])
        self.assertTrue(result["passed"], result)
        self.assertFalse(result["financial_value_mismatches"])

    def test_nav_expiry_and_fourteen_day_staleness_are_explicit(self) -> None:
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "nav.jsonl"
            index.write_text(
                json.dumps({"organisation_number": "923609016", "ads": [{"uuid": "expired", "expires": (now - timedelta(days=1)).isoformat()}, {"uuid": "live", "expires": (now + timedelta(days=1)).isoformat()}]}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual([item["uuid"] for item in load_index(index)["923609016"]["ads"]], ["live"])
            meta = Path(str(index) + ".meta.json")
            meta.write_text(json.dumps({"complete": True, "built_at": (now - timedelta(days=15)).isoformat()}), encoding="utf-8")
            loaded, metadata = _fresh_nav_index(str(index))
        self.assertIn("923609016", loaded)
        self.assertTrue(metadata["stale"])

    def test_blocked_report_names_the_policy_url(self) -> None:
        rows = [{
            "organisation_number": "923609016",
            "name": "Example AS",
            "evidence": {"website": {"status": "blocked", "source_url": "https://example.no/"}},
            "discovery": {"state": "blocked", "candidates": [{"requested_url": "https://example.no/", "state": "blocked", "block_reason": "robots.txt disallows this user agent"}]},
        }]
        result = build_report(rows)
        self.assertIn("robots URL: https://example.no/robots.txt", result[0]["reason"])

    def test_data_defaults_are_tracked(self) -> None:
        tracked = set(subprocess.check_output(["git", "ls-files", "--cached"], cwd=ROOT, text=True).splitlines())
        pattern = re.compile(r"(?:Path\([\"']|--nav-index\s+)data/([A-Za-z0-9_.-]+)")
        literals: list[str] = []
        for directory in (ROOT / "src", ROOT / "scripts" / "run"):
            for path in directory.rglob("*"):
                if path.suffix not in {".py", ".sh"}:
                    continue
                for match in pattern.finditer(path.read_text(encoding="utf-8")):
                    literals.append("data/" + match.group(1))
        self.assertTrue(literals)
        self.assertTrue(set(literals).issubset(tracked), sorted(set(literals) - tracked))

    def test_keyed_search_is_explicit_opt_in(self) -> None:
        source = (ROOT / "scripts" / "run" / "run_competition_batch.py").read_text(encoding="utf-8")
        self.assertRegex(source, r"if args\.search_fill and args\.search_budget > 0")
        self.assertIn('os.environ.get("SERPER_API_KEY", "")', source)
        self.assertIn('default_off', source)


if __name__ == "__main__":
    unittest.main()
