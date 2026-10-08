from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.cache import CacheLookup, merge_cache_profile  # noqa: E402
from norway_company_agent.external.nav_jobs import collect  # noqa: E402
from scripts.run.build_universe_cache import build, process_record  # noqa: E402


ORG = "923609016"


def fake_fetch(url, **_kwargs):
    value = {
        "requested_url": url, "final_url": url, "title": "Norsk Fiskeeksport AS",
        "description": "", "main_text_excerpt": "Norsk Fiskeeksport AS Organisasjonsnummer 923609016",
        "identity_text_excerpt": "Norsk Fiskeeksport AS Organisasjonsnummer 923609016",
        "social_links": [{"platform": "youtube", "url": "https://youtube.com/@fiskeeksport"}],
        "structured_organisations": [], "structured_identifiers": [], "content_sha256": "a" * 64,
        "pages": [],
    }
    return {"kind": "evidence", "field": "website", "status": "available", "source_url": url, "retrieved_at": "2026-10-08T00:00:00Z", "content_sha256": "a" * 64, "value": value}, {"requests": 2, "bytes": 100, "latencies_ms": [1]}


def record(org=ORG, employees=10, website="https://fiskeeksport.no/"):
    return {"organisation_number": org, "name": "Norsk Fiskeeksport AS", "employees": employees, "legal_form": "AS", "municipality": "Notodden", "website": website, "latest_submitted_accounts": "2025", "bankrupt": False, "liquidating": False, "raw": {"hjemmeside": website, "epostadresse": "jobb@fiskeeksport.no", "forretningsadresse.adresse": "Havnevegen 1", "forretningsadresse.postnummer": "3674", "forretningsadresse.poststed": "Notodden"}}


class RecallCacheTests(unittest.TestCase):
    def test_gate_pass_and_gate_failure_are_distinct(self):
        published = process_record(record(), fetcher=fake_fetch)
        self.assertEqual(published["states"]["official_website"], "available")
        failed = process_record(record(website="https://other.example/"), fetcher=lambda *_args, **_kwargs: ({"status": "failed", "source_url": "https://other.example/"}, {"requests": 1, "bytes": 0}))
        self.assertIn(failed["states"]["official_website"], {"failed", "not_available"})

    def test_builder_orders_by_employees_and_resume_is_append_only(self):
        records = [record("923609016", 1), record("923609025", 100), record("923609034", 10)]
        with tempfile.TemporaryDirectory() as folder:
            first = build(records, Path(folder) / "cache", workers=2, fetcher=fake_fetch)
            self.assertEqual(first["processed_this_run"], 3)
            shard = next((Path(folder) / "cache").glob("*.jsonl.gz"))
            rows = CacheLookup(Path(folder) / "cache").records
            self.assertEqual(len(rows), 3)
            second = build(records, Path(folder) / "cache", resume=True, workers=2, fetcher=fake_fetch)
            self.assertEqual(second["processed_this_run"], 0)
            self.assertEqual(len(list(CacheLookup(Path(folder) / "cache").records)), 3)

    def test_checked_empty_nav_is_not_available_but_unchecked_is_failed(self):
        profile = {"organisation_number": ORG, "name": "Norsk Fiskeeksport AS"}
        checked = collect(profile, now=datetime(2026, 10, 8, tzinfo=timezone.utc), context={"index": {}, "index_complete": True})
        unchecked = collect(profile, now=datetime(2026, 10, 8, tzinfo=timezone.utc), context={"index": {}, "index_complete": False})
        self.assertEqual(checked["status"], "not_available")
        self.assertEqual(unchecked["status"], "failed")

    def test_cache_merge_keeps_state_and_claims(self):
        profile = {"organisation_number": ORG, "evidence": {}}
        merged = merge_cache_profile(profile, {"organisation_number": ORG, "states": {"official_website": "available"}, "claims": {"official_website": {"final_url": "https://example.no"}}, "built_at": "2026-10-08T00:00:00Z"})
        self.assertEqual(merged["claims"]["official_website"]["final_url"], "https://example.no")
        self.assertEqual(merged["recall_cache"]["states"]["official_website"], "available")


if __name__ == "__main__":
    unittest.main()
