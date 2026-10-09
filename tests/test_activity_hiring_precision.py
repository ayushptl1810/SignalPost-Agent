from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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


if __name__ == "__main__":
    unittest.main()
