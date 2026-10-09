from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.evidence import evidence  # noqa: E402
from norway_company_agent.site_facts import parse_html_metadata, parse_jobs_html, extract_site_facts  # noqa: E402


FIXTURES = json.loads((ROOT / "tests/fixtures/activity_hiring_precision/hiring.json").read_text(encoding="utf-8"))


def profile_for(html: str, url: str = "https://example.no/karriere") -> dict:
    page = {"url": url, "html": html, **parse_html_metadata(html, url)}
    website = evidence(
        "website", "available", "company_owned", url,
        value={"final_url": url, "pages": [page], **parse_html_metadata(html, url)},
        retrieved_at="2026-10-01T12:00:00Z", content_sha256="a" * 64,
    )
    return {"organisation_number": "912345678", "name": "Example AS", "municipality": "Oslo", "industry_label": "Programvareutvikling", "evidence": {"website": website}}


class HiringPrecisionTests(unittest.TestCase):
    def test_real_junk_titles_are_rejected_and_genuine_titles_survive(self) -> None:
        for fixture in FIXTURES:
            with self.subTest(name=fixture["name"]):
                jobs, _parseable = parse_jobs_html(fixture["html"], fixture["url"])
                titles = [job["title"] for job in jobs]
                self.assertEqual(titles, fixture["expected_titles"])
                self.assertTrue(all(len(title) <= 120 for title in titles))
                self.assertTrue(all(str(job["url"]).startswith("https://") for job in jobs))

    def test_empty_careers_page_is_checked_not_available_with_career_url(self) -> None:
        profile = profile_for("<html><body><main><h1>Karriere</h1><p>Ingen ledige stillinger akkurat nå.</p></main></body></html>")
        facts = extract_site_facts(profile)
        hiring = facts["hiring"]
        self.assertEqual(hiring["status"], "not_available")
        self.assertEqual(hiring["value"]["postings"], [])
        self.assertIn("careers page found, no postings", hiring["note"])
        self.assertEqual(hiring["source_url"], "https://example.no/karriere")


if __name__ == "__main__":
    unittest.main()
