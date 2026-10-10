from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.registry.batch import terminal_envelope, validate_envelopes  # noqa: E402
from norway_company_agent.core.refresh import diff_profile  # noqa: E402
from norway_company_agent.site_facts import (  # noqa: E402
    _fetch_fact_document,
    extract_site_facts,
    enrich_site_facts,
    parse_activity_feed,
    parse_activity_html,
    parse_date,
    parse_datetime,
    parse_html_metadata,
    parse_jobs_html,
    parse_sitemap_activity,
)
from norway_company_agent.web.website import HostRequestPolicy  # noqa: E402
from scripts.analysis.envelope_audit import audit  # noqa: E402


ORG = "912345678"


def profile_with_html(html: str, *, url: str = "https://example.no/", industry: str = "Programvareutvikling") -> dict:
    from norway_company_agent.core.evidence import evidence
    from norway_company_agent.site_facts import parse_html_metadata

    page = {"url": url, "content_sha256": "a" * 64, **parse_html_metadata(html, url)}
    website = evidence("website", "available", "company_owned", url, value={"final_url": url, "pages": [page], **parse_html_metadata(html, url)}, content_sha256="a" * 64, retrieved_at="2026-10-01T12:00:00Z")
    return {
        "organisation_number": ORG,
        "name": "Example AS",
        "municipality": "Oslo",
        "industry_label": industry,
        "raw": {"naeringskode1.beskrivelse": industry, "forretningsadresse.kommune": "Oslo"},
        "evidence": {"website": website},
    }


class SiteFactsTests(unittest.TestCase):
    def test_extra_fetch_order_prefers_feed_activity_and_career_before_guesses(self) -> None:
        root = "https://example.no/"
        html = """<html><body>
          <a href="/feed.xml">RSS</a><a href="/aktuelt">Aktuelt</a><a href="/karriere">Karriere</a>
        </body></html>"""
        metadata = parse_html_metadata(html, root)
        profile = profile_with_html(html, url=root)
        profile["evidence"]["website"]["value"]["pages"] = [{"url": root, "html": html, **metadata}]
        requested: list[str] = []

        def fake_fetch(url: str, **_kwargs: object) -> tuple[dict, dict]:
            requested.append(url)
            return ({"kind": "html", "url": url, "final_url": url, "status_code": 200, "raw": "<html><body><h1>Fetched</h1></body></html>"}, {"requests": 1, "bytes": 10, "latencies_ms": [1], "phase_timings_ms": {}})

        with patch("norway_company_agent.site_facts._fetch_fact_document", side_effect=fake_fetch):
            enrich_site_facts(profile, max_extra_requests=6)

        self.assertEqual(requested[:3], ["https://example.no/feed.xml", "https://example.no/aktuelt", "https://example.no/karriere"])
        self.assertEqual(requested[3:], [])

    def test_date_formats_and_undated_rejection(self) -> None:
        self.assertEqual(parse_date("12. mars 2026"), "2026-03-12")
        self.assertEqual(parse_date("12.03.2026"), "2026-03-12")
        self.assertEqual(parse_date("Thu, 12 Mar 2026 12:00:00 GMT"), "2026-03-12")
        self.assertIsNone(parse_date("recently"))

    def test_parse_datetime_preserves_offsets_and_uses_oslo_for_naive_times(self) -> None:
        self.assertEqual(parse_datetime("2025-09-22T20:00:00+02:00"), "2025-09-22T20:00:00+02:00")
        self.assertEqual(parse_datetime("2025-09-22T18:00:00Z"), "2025-09-22T18:00:00+00:00")
        self.assertEqual(parse_datetime("Mon, 22 Sep 2025 20:00:00 +0200"), "2025-09-22T20:00:00+02:00")
        self.assertEqual(parse_datetime("2025-09-22T20:00"), "2025-09-22T20:00:00+02:00")
        self.assertEqual(parse_datetime("2025-01-22T20:00"), "2025-01-22T20:00:00+01:00")
        self.assertEqual(parse_datetime("2025-09-22"), "2025-09-22")

    def test_activity_datetime_sources_are_kept_on_items(self) -> None:
        html = """
        <html><head><meta property="article:published_time" content="2025-09-22T20:00:00+02:00">
        <script type="application/ld+json">{"@type":"NewsArticle","headline":"JSON-LD release","datePublished":"2025-09-22T18:00:00Z","url":"/news/jsonld"}</script>
        </head><body><article><h2>Visible release</h2><time datetime="2025-09-22T20:00">20 September</time></article></body></html>
        """
        items = parse_activity_html(html, "https://example.no/news/release")
        by_title = {item["title"]: item for item in items}
        self.assertEqual(by_title["JSON-LD release"]["published_at"], "2025-09-22T18:00:00+00:00")
        self.assertEqual(by_title["Visible release"]["published_at"], "2025-09-22T20:00:00+02:00")
        self.assertEqual(by_title["JSON-LD release"]["effective_at"], by_title["JSON-LD release"]["published_at"])

    def test_feed_meta_and_sitemap_datetime_sources(self) -> None:
        rss = "<rss><channel><item><title>RSS news</title><pubDate>Mon, 22 Sep 2025 20:00:00 +0200</pubDate><link>https://example.no/news/rss</link></item></channel></rss>"
        atom = "<feed><entry><title>Atom news</title><updated>2025-09-22T18:00:00Z</updated><link href='https://example.no/news/atom'/></entry></feed>"
        sitemap = "<urlset><url><loc>https://example.no/blog/a</loc><lastmod>2025-09-22T20:00:00+02:00</lastmod></url></urlset>"
        self.assertEqual(parse_activity_feed(rss, "https://example.no/feed.xml")[0]["published_at"], "2025-09-22T20:00:00+02:00")
        self.assertEqual(parse_activity_feed(atom, "https://example.no/atom.xml")[0]["published_at"], "2025-09-22T18:00:00+00:00")
        self.assertEqual(parse_sitemap_activity(sitemap, "https://example.no/sitemap.xml")[0]["published_at"], "2025-09-22T20:00:00+02:00")

    def test_future_published_at_is_rejected_by_the_envelope_validator(self) -> None:
        profile = profile_with_html("<html><body><h1>Example</h1></body></html>")
        facts = extract_site_facts(profile)
        profile["evidence"].update(facts)
        envelope = terminal_envelope(profile, run_id="future", modules=["website"], started_at="2026-10-01T12:00:00Z", completed_at="2026-10-01T12:00:00Z")
        activity = next(claim for claim in envelope["claims"] if claim["field"] == "public_activity")
        activity["availability"] = "available"
        activity["value"] = {"items": [{"date": "2026-09-30", "published_at": "2026-10-01T13:00:00+00:00", "title": "future", "url": "https://example.no/news/future"}]}
        result = validate_envelopes([envelope], 1)
        self.assertFalse(result["passed"])
        self.assertTrue(any(item["kind"] == "activity_date_after_retrieval" for item in result["contract_errors"]))

    def test_jsonld_opengraph_visible_and_old_activity(self) -> None:
        html = """
        <html><head><title>Example news</title>
        <meta property="article:published_time" content="2026-09-01T10:00:00Z">
        <script type="application/ld+json">{"@type":"NewsArticle","headline":"Launch","datePublished":"2026-08-20","url":"/news/launch"}</script>
        </head><body><article><h2><a href="/news/old-item">Old item</a></h2><time datetime="2020-01-01">1. januar 2020</time></article></body></html>
        """
        items = parse_activity_html(html, "https://example.no/news")
        self.assertEqual({item["date"] for item in items}, {"2026-08-20", "2020-01-01"})
        self.assertTrue(any(item.get("old") is not False for item in items))

    def test_rss_and_sitemap_lastmod(self) -> None:
        rss = "<rss><channel><item><title>News</title><pubDate>12. mars 2026</pubDate><link>https://example.no/news/a</link></item></channel></rss>"
        sitemap = "<urlset><url><loc>https://example.no/blog/a</loc><lastmod>2026-03-12</lastmod></url><url><loc>https://other.no/blog/b</loc><lastmod>2026-03-13</lastmod></url></urlset>"
        self.assertEqual(parse_activity_feed(rss, "https://example.no/feed.xml")[0]["date"], "2026-03-12")
        self.assertEqual(len(parse_sitemap_activity(sitemap, "https://example.no/sitemap.xml")), 1)

    def test_careers_jsonld_no_open_roles_and_aggregator_rejection(self) -> None:
        jobs_html = """
        <html><body><h1>Karriere</h1><p>Vi har ingen ledige stillinger akkurat nå.</p>
        <script type="application/ld+json">{"@type":"JobPosting","title":"Developer","datePosted":"2026-09-01","url":"https://example.no/jobs/developer","jobLocation":{"address":{"addressLocality":"Oslo"}}}</script>
        <script type="application/ld+json">{"@type":"JobPosting","title":"Aggregator","datePosted":"2026-09-01","url":"https://finn.no/job/1"}</script>
        </body></html>
        """
        jobs, parseable = parse_jobs_html(jobs_html, "https://example.no/karriere")
        self.assertTrue(parseable)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["location"], "Oslo")
        self.assertEqual(len(parse_jobs_html("<html><body><h1>Jobb</h1><p>Ingen ledige stillinger</p></body></html>", "https://example.no/jobb")[0]), 0)

    def test_extract_merges_nav_and_marks_brief_disagreement(self) -> None:
        profile = profile_with_html("<html><head><meta name='description' content='We sell industrial fish products.'></head><body><h1>What we do</h1></body></html>", industry="Accounting")
        profile["evidence"]["nav_jobs"] = {"status": "available", "value": {"ads": [{"title": "Accountant", "location": "Oslo", "published": "2026-09-01", "link": "https://arbeidsplassen.nav.no/stillinger/1"}]}}
        facts = extract_site_facts(profile)
        self.assertEqual(facts["company_brief"]["status"], "ambiguous")
        self.assertEqual(facts["public_activity"]["status"], "not_available")
        self.assertEqual(facts["hiring"]["value"]["nav_ads"][0]["source_class"], "nav_feed")

    def test_extra_fetch_respects_robots_block(self) -> None:
        with patch("norway_company_agent.site_facts.safe_web.assert_public_url"), patch("norway_company_agent.site_facts.safe_web._robots_allowed", return_value=False):
            document, metrics = _fetch_fact_document("https://example.no/jobs", base_url="https://example.no/", timeout=1)
        self.assertIsNone(document)
        self.assertTrue(metrics["blocked"])

    def test_contract_validator_rejects_future_activity_and_missing_url(self) -> None:
        profile = profile_with_html("<html><body><h1>Example</h1></body></html>")
        facts = extract_site_facts(profile)
        profile["evidence"].update(facts)
        envelope = terminal_envelope(profile, run_id="test", modules=["website"], started_at="2026-10-01T12:00:00Z", completed_at="2026-10-01T12:00:00Z")
        activity = next(claim for claim in envelope["claims"] if claim["field"] == "public_activity")
        activity["availability"] = "available"
        activity["value"] = {"items": [{"date": "2026-10-02", "title": "future"}]}
        result = validate_envelopes([envelope], 1)
        self.assertFalse(result["passed"])
        self.assertTrue(any(item["kind"] == "dated_item_without_source_url" for item in result["contract_errors"]))

    def test_site_fact_changes_are_material_and_source_attributed(self) -> None:
        previous = {"organisation_number": ORG, "evidence": {"public_activity": {"status": "not_available", "source_url": "https://example.no", "retrieved_at": "2026-10-01T00:00:00Z", "content_sha256": "a" * 64, "value": {"items": []}}}}
        current = {"organisation_number": ORG, "evidence": {"public_activity": {"status": "available", "source_url": "https://example.no/news", "retrieved_at": "2026-10-02T00:00:00Z", "content_sha256": "b" * 64, "value": {"items": [{"title": "Launch", "date": "2026-10-01", "url": "https://example.no/news/launch"}]}}}}
        changes = diff_profile(previous, current)
        self.assertEqual([item["field"] for item in changes], ["public_activity.items"])

    def test_financial_evidence_requires_reporting_period(self) -> None:
        from norway_company_agent.core.evidence import evidence

        profile = {
            "organisation_number": ORG,
            "evidence": {
                "financials": evidence(
                    "financials",
                    "available",
                    "official_annual_accounts",
                    "https://data.brreg.no/regnskapsregisteret/regnskap/" + ORG,
                    value={"records": [{"period": {"tilDato": "2025-12-31"}, "revenue": 1}]},
                    retrieved_at="2026-10-01T12:00:00Z",
                    content_sha256="a" * 64,
                )
            },
        }
        envelope = terminal_envelope(profile, run_id="test", modules=[], started_at="2026-10-01T12:00:00Z", completed_at="2026-10-01T12:00:00Z")
        financial_evidence = next(row for row in envelope["evidence"] if row["source_class"] == "official_annual_accounts")
        financial_evidence.pop("reporting_period")
        validation = validate_envelopes([envelope], 1)
        self.assertFalse(validation["passed"])
        self.assertTrue(any(item["kind"] == "missing_reporting_period" for item in validation["contract_errors"]))
        self.assertFalse(audit([envelope])["passed"])


if __name__ == "__main__":
    unittest.main()
