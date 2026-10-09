from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.evidence import evidence  # noqa: E402
from norway_company_agent.site_facts import (  # noqa: E402
    _mark_old,
    extract_site_facts,
    parse_activity_feed,
    parse_activity_html,
    parse_html_metadata,
    parse_jobs_html,
    parse_sitemap_activity,
)
from scripts.analysis.envelope_audit import detect_junk  # noqa: E402


FIXTURES = json.loads((ROOT / "tests/fixtures/activity_hiring_precision/hiring.json").read_text(encoding="utf-8"))
BRIEF_FIXTURES = json.loads((ROOT / "tests/fixtures/activity_hiring_precision/briefs.json").read_text(encoding="utf-8"))


def profile_for(html: str, url: str = "https://example.no/karriere") -> dict:
    page = {"url": url, "html": html, **parse_html_metadata(html, url)}
    website = evidence(
        "website", "available", "company_owned", url,
        value={"final_url": url, "pages": [page], **parse_html_metadata(html, url)},
        retrieved_at="2026-10-01T12:00:00Z", content_sha256="a" * 64,
    )
    return {"organisation_number": "912345678", "name": "Example AS", "municipality": "Oslo", "industry_label": "Programvareutvikling", "evidence": {"website": website}}


def profile_with_pages(name: str, pages: list[dict[str, str]]) -> dict:
    prepared = []
    for page in pages:
        prepared.append({"url": page["url"], "html": page["html"], **parse_html_metadata(page["html"], page["url"])})
    root_url = pages[0]["url"]
    website = evidence(
        "website", "available", "company_owned", root_url,
        value={"final_url": "https://" + root_url.split("//", 1)[1].split("/", 1)[0] + "/", "pages": prepared},
        retrieved_at="2026-10-01T12:00:00Z", content_sha256="b" * 64,
    )
    return {"organisation_number": "912345678", "name": name, "municipality": "Oslo", "industry_label": "Uoppgitt", "evidence": {"website": website}}


class HiringPrecisionTests(unittest.TestCase):
    def test_real_junk_titles_are_rejected_and_genuine_titles_survive(self) -> None:
        for fixture in FIXTURES:
            with self.subTest(name=fixture["name"]):
                jobs, _parseable = parse_jobs_html(fixture["html"], fixture["url"])
                titles = [job["title"] for job in jobs]
                self.assertEqual(titles, fixture["expected_titles"])
                self.assertTrue(all(len(title) <= 120 for title in titles))
                self.assertTrue(all(str(job["url"]).startswith("https://") for job in jobs))

    def test_gramart_greetings_and_leader_messages_are_not_jobs(self) -> None:
        html = """
        <html><body><main><ul>
          <li><h2><a href="/blog/julehilsen">Julehilsen fra daglig leder</a></h2></li>
          <li><h2><a href="/blog/sommerhilsen">Sommerhilsen fra daglig leder</a></h2></li>
          <li><h2><a href="/blog/hilsen">Hilsen</a></h2></li>
          <li><h2><a href="/blog/god-jul">God jul</a></h2></li>
          <li><h2><a href="/stillinger/advokat">Ledig stilling som advokat</a></h2></li>
        </ul></main></body></html>
        """
        jobs, _parseable = parse_jobs_html(html, "https://gramart.no/karriere")
        self.assertEqual([job["title"] for job in jobs], ["Ledig stilling som advokat"])

    def test_empty_careers_page_is_checked_not_available_with_career_url(self) -> None:
        profile = profile_for("<html><body><main><h1>Karriere</h1><p>Ingen ledige stillinger akkurat nå.</p></main></body></html>")
        facts = extract_site_facts(profile)
        hiring = facts["hiring"]
        self.assertEqual(hiring["status"], "not_available")
        self.assertEqual(hiring["value"]["postings"], [])
        self.assertIn("careers page found, no postings", hiring["note"])
        self.assertEqual(hiring["source_url"], "https://example.no/karriere")

    def test_structured_homepage_job_is_available_without_career_path(self) -> None:
        html = "<script type='application/ld+json'>{\"@type\":\"JobPosting\",\"title\":\"Senior Engineer\",\"url\":\"/jobs/senior-engineer\",\"datePosted\":\"2026-09-01\"}</script>"
        facts = extract_site_facts(profile_for(html, "https://structured.no/"))
        self.assertEqual(facts["hiring"]["status"], "available")
        self.assertEqual(facts["hiring"]["value"]["company_site_postings"][0]["title"], "Senior Engineer")
        self.assertEqual(facts["hiring"]["source_url"], "https://structured.no/")

    def test_activity_requires_item_publication_date_and_cleans_claim_span(self) -> None:
        html = """
        <html><head><title>Nyheter</title>
        <meta property="article:published_time" content="2026-09-01">
        </head><body>
        <time datetime="2026-08-01">page date</time>
        <article><h2><a href="/news/quarterly-release">Quarterly release</a></h2><p><strong>We opened</strong> a new office for customers.</p><time datetime="2026-08-20">20. august 2026</time></article>
        <article><h2><a href="/news/undated">Undated page furniture</a></h2><p>Published 19. august 2026 in the page copy.</p></article>
        <article><h2><a href="/news/navigation">Navigation only</a></h2><p>Home Contact Menu</p><time datetime="2026-08-21">21. august 2026</time></article>
        </body></html>
        """
        items = parse_activity_html(html, "https://example.no/nyheter")
        self.assertEqual([item["title"] for item in items], ["Quarterly release"])
        self.assertEqual(items[0]["date"], "2026-08-20")
        self.assertEqual(items[0]["claim_span"], "We opened a new office for customers.")
        self.assertNotIn("<", items[0]["claim_span"])
        self.assertLessEqual(len(items[0]["claim_span"]), 200)

    def test_activity_item_dated_on_crawl_date_is_rejected_at_extraction(self) -> None:
        html = """
        <html><body>
          <article><h2><a href="/news/today">Listing refreshed</a></h2><p>Current catalogue listing.</p><time datetime="2026-10-01">1. oktober 2026</time></article>
          <article><h2><a href="/news/announcement">Customer announcement</a></h2><p>Customers joined our service.</p><time datetime="2026-09-30">30. september 2026</time></article>
        </body></html>
        """
        facts = extract_site_facts(profile_for(html, "https://example.no/nyheter"))
        items = facts["public_activity"]["value"]["items"]
        self.assertEqual([item["date"] for item in items], ["2026-09-30"])
        self.assertNotIn("Listing refreshed", [item["title"] for item in items])

    def test_activity_jsonld_types_slugs_feed_and_sitemap_are_precise(self) -> None:
        html = """
        <script type="application/ld+json">{"@type":"NewsArticle","headline":"Launch","datePublished":"2026-08-20","url":"/news/launch","description":"<p>Launch announcement</p>"}</script>
        <script type="application/ld+json">{"@type":"WebPage","name":"About","datePublished":"2026-08-21","url":"/om-oss"}</script>
        <script type="application/ld+json">{"@type":"Article","headline":"Hei verden","datePublished":"2026-08-22","url":"/blog/hei-verden"}</script>
        <script type="application/ld+json">{"@type":"ContactPage","name":"Contact","datePublished":"2026-08-23","url":"/kontakt"}</script>
        """
        items = parse_activity_html(html, "https://example.no/")
        self.assertEqual([(item["title"], item["date"]) for item in items], [("Launch", "2026-08-20")])
        self.assertEqual(items[0]["claim_span"], "Launch announcement")

        feed = """
        <rss><channel>
          <item><title>Real announcement</title><pubDate>20. august 2026</pubDate><link>/news/real</link><description><![CDATA[<p>Customers joined us.</p>]]></description></item>
          <item><title>Updated listing</title><updated>21. august 2026</updated><link>/news/updated</link></item>
          <item><title>Hei verden</title><published>22. august 2026</published><link>/blog/hei-verden</link></item>
        </channel></rss>
        """
        feed_items = parse_activity_feed(feed, "https://example.no/feed.xml")
        self.assertEqual([item["title"] for item in feed_items], ["Real announcement"])
        self.assertEqual(feed_items[0]["claim_span"], "Customers joined us.")

        sitemap = """
        <urlset>
          <url><loc>https://example.no/nyheter/real</loc><lastmod>2026-08-20</lastmod></url>
          <url><loc>https://example.no/om-oss</loc><lastmod>2026-08-21</lastmod></url>
          <url><loc>https://example.no/blog/hei-verden</loc><lastmod>2026-08-22</lastmod></url>
        </urlset>
        """
        sitemap_items = parse_sitemap_activity(sitemap, "https://example.no/sitemap.xml")
        self.assertEqual([item["url"] for item in sitemap_items], ["https://example.no/nyheter/real"])

    def test_old_only_activity_is_not_available_and_nav_jobs_are_not_activity(self) -> None:
        profile = profile_for("<html><body><main><h1>Nyheter</h1></main></body></html>", "https://example.no/nyheter")
        profile["evidence"]["nav_jobs"] = {"status": "available", "value": {"ads": [{"title": "Accountant", "published": "2026-09-01", "link": "https://arbeidsplassen.nav.no/stillinger/1"}]}}
        facts = extract_site_facts(profile)
        self.assertEqual(facts["public_activity"]["status"], "not_available")
        self.assertEqual(facts["public_activity"]["value"]["items"], [])

        items = [{"date": "2025-01-01"}, {"date": "2023-01-01"}]
        self.assertEqual([item["old"] for item in _mark_old(items, "2020-01-01", as_of="2026-10-09")], [False, True])

        old_profile = profile_for("<html><body><article><h2><a href='/news/old'>Old announcement</a></h2><time datetime='2020-01-01'>1. januar 2020</time></article></body></html>", "https://example.no/news")
        old_facts = extract_site_facts(old_profile)
        self.assertEqual(old_facts["public_activity"]["status"], "not_available")
        self.assertIn("last 24 months", old_facts["public_activity"]["note"])

    def test_named_brief_defects_are_cleaned_and_legal_page_name_is_kept(self) -> None:
        for fixture in BRIEF_FIXTURES:
            with self.subTest(name=fixture["name"]):
                profile = profile_with_pages(fixture["name"], fixture["pages"])
                facts = extract_site_facts(profile)
                brief = facts["company_brief"]
                self.assertEqual(brief["status"], "available")
                self.assertIn(fixture["expected_contains"], brief["value"]["summary"])
                self.assertNotIn("<p>", brief["value"]["summary"])
                self.assertNotRegex(brief["value"]["summary"], r"^(?:Slik|Kontakt oss|Les mer|Contact us)\b")
                if fixture["expected_kind"] == "registry":
                    self.assertTrue(brief["value"]["registry_derived"])
                else:
                    self.assertFalse(brief["value"]["registry_derived"])

    def test_root_description_precedes_clean_contact_description(self) -> None:
        profile = profile_with_pages("Root preference", [
            {"url": "https://root-preference.no/kontakt", "html": "<html><head><meta name='description' content='Root Preference provides clean accounting services for local companies.'></head></html>"},
            {"url": "https://root-preference.no/", "html": "<html><head><meta property='og:description' content='Root Preference builds Norwegian software for accounting teams and auditors.'></head></html>"},
        ])
        facts = extract_site_facts(profile)
        self.assertIn("builds Norwegian software", facts["company_brief"]["value"]["summary"])

    def test_junk_detector_reports_all_five_defect_classes_and_clean_fixture_is_zero(self) -> None:
        evidence_row = {"id": "activity-evidence", "retrieved_at": "2026-10-01T12:00:00Z"}
        clean = {
            "organisation_number": "1",
            "evidence": [evidence_row],
            "claims": [
                {"field": "hiring", "value": {"postings": [{"title": "Senior Engineer", "source_class": "company_owned"}]}, "evidence_ids": ["activity-evidence"]},
                {"field": "public_activity", "value": {"items": [{"date": "2026-09-30", "claim_span": "A dated customer announcement.", "url": "https://example.no/news/announcement"}]}, "evidence_ids": ["activity-evidence"]},
                {"field": "company_brief", "value": {"summary": "Example AS designs industrial equipment.", "parts": [{"source_class": "company_owned", "text": "Example AS designs industrial equipment."}]}, "evidence_ids": ["activity-evidence"]},
            ],
        }
        self.assertEqual(detect_junk([clean], as_of="2026-10-09"), {
            "rows": 1,
            "hiring_nav_titles": 0,
            "activity_markup_claim_spans": 0,
            "activity_crawl_date_items": 0,
            "activity_old_items_informational": 0,
            "brief_cta_prefixes": 0,
            "total": 0,
            "passed": True,
        })

        noisy = {
            "organisation_number": "2",
            "evidence": [evidence_row],
            "claims": [
                {"field": "hiring", "value": {"postings": [{"title": "Kontakt"}]}, "evidence_ids": ["activity-evidence"]},
                {"field": "public_activity", "value": {"items": [
                    {"date": "2026-10-01", "claim_span": "<p>Markup claim</p>", "url": "https://example.no/news/one"},
                    {"date": "2023-01-01", "claim_span": "Old claim", "url": "https://example.no/news/two"},
                ]}, "evidence_ids": ["activity-evidence"]},
                {"field": "company_brief", "value": {"summary": "Slik kjøper du medlemskap. Les mer.", "parts": [{"source_class": "company_owned", "text": "Slik kjøper du medlemskap. Les mer."}]}, "evidence_ids": ["activity-evidence"]},
            ],
        }
        report = detect_junk([noisy], as_of="2026-10-09")
        self.assertEqual(report["hiring_nav_titles"], 1)
        self.assertEqual(report["activity_markup_claim_spans"], 1)
        self.assertEqual(report["activity_crawl_date_items"], 1)
        self.assertEqual(report["activity_old_items_informational"], 1)
        self.assertEqual(report["brief_cta_prefixes"], 1)
        self.assertEqual(report["total"], 5)
        self.assertFalse(report["passed"])

        informational_only = {
            "organisation_number": "3",
            "evidence": [evidence_row],
            "claims": [{
                "field": "public_activity",
                "value": {"items": [{"date": "2023-01-01", "claim_span": "Old claim", "url": "https://example.no/news/old"}]},
                "evidence_ids": ["activity-evidence"],
            }],
        }
        informational_report = detect_junk([informational_only], as_of="2026-10-09")
        self.assertEqual(informational_report["activity_old_items_informational"], 1)
        self.assertEqual(informational_report["total"], 1)
        self.assertTrue(informational_report["passed"])


if __name__ == "__main__":
    unittest.main()
