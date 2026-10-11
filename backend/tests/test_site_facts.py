from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

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
    parse_sitemap_career_urls,
    parse_sitemap_article_urls,
    parse_sitemap_activity,
)
from norway_company_agent.core.evidence import evidence  # noqa: E402
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
    def test_careers_link_on_about_page_requires_careers_heading(self) -> None:
        root = "https://example.no/"
        homepage = "<html><body><a href='/om-oss'>Om oss</a></body></html>"
        about = "<html><body><h1>Om oss</h1><a href='/karriere'>Karriere</a></body></html>"
        career = "<html><body><h1>Karriere</h1><p>Vi har for tiden ingen ledige stillinger.</p></body></html>"
        profile = profile_with_html(homepage, url=root)
        profile["evidence"]["website"]["value"]["pages"] = [{"url": root, "html": homepage, **parse_html_metadata(homepage, root)}]

        def fake_fetch(url: str, **_kwargs: object):
            raw = about if url.endswith("/om-oss") else career
            return {"kind": "html", "url": url, "final_url": url, "status_code": 200, "raw": raw, **parse_html_metadata(raw, url)}, {"requests": 1, "bytes": len(raw), "latencies_ms": [1], "phase_timings_ms": {}}

        with patch("norway_company_agent.site_facts._fetch_fact_document", side_effect=fake_fetch):
            facts, _metrics = enrich_site_facts(profile, max_extra_requests=6)
        self.assertEqual(facts["hiring"]["status"], "available")
        self.assertEqual(facts["hiring"]["value"]["careers_page_url"], "https://example.no/karriere")
        self.assertTrue(facts["hiring"]["value"]["explicit_no_openings"])
        self.assertIn("for tiden ingen ledige stillinger", facts["hiring"]["value"]["explicit_no_openings_span"])

    def test_career_sitemap_returns_urls_but_not_dates(self) -> None:
        sitemap = "<urlset><url><loc>https://example.no/careers/jobs</loc><lastmod>2099-01-01</lastmod></url><url><loc>https://example.no/products/catalog/jobs</loc></url></urlset>"
        self.assertEqual(parse_sitemap_career_urls(sitemap, "https://example.no/sitemap.xml"), ["https://example.no/careers/jobs"])

    def test_jobposting_employer_and_ats_links_are_checked(self) -> None:
        html = """<html><body><h1>Karriere</h1><h2>Senior developer</h2>
        <a href='https://careers.teamtailor.com/jobs/42'>Søk her</a>
        <script type='application/ld+json'>{"@type":"JobPosting","title":"Client accountant","url":"https://example.no/jobs/client","hiringOrganization":{"name":"Other Recruiter"}}</script>
        </body></html>"""
        jobs, parseable = parse_jobs_html(html, "https://example.no/karriere", company_name="Example AS")
        self.assertTrue(parseable)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["platform"], "teamtailor")
        self.assertEqual(jobs[0]["title"], "Senior developer")

    def test_career_apply_page_is_a_posting_and_future_date_is_closing(self) -> None:
        html = "<html><body><h1>Ledig stilling som advokat</h1><p>Søknadsfrist: 31. desember 2099. Apply now.</p></body></html>"
        jobs, _parseable = parse_jobs_html(html, "https://example.no/karriere/advokat", as_of=date(2026, 10, 10))
        self.assertEqual(len(jobs), 1)
        self.assertIsNone(jobs[0]["posted"])
        self.assertEqual(jobs[0]["closing"], "2099-12-31")
    def test_news_discovery_probes_wordpress_feed_page_two_and_wp_json(self) -> None:
        root = "https://example.no/"
        html = "<html><head><meta name='generator' content='WordPress 6.0'></head><body><h1>Example</h1></body></html>"
        profile = profile_with_html(html, url=root)
        requested: list[str] = []

        def fake_fetch(url: str, **_kwargs: object) -> tuple[dict, dict]:
            requested.append(url)
            return {"kind": "html", "url": url, "final_url": url, "status_code": 200, "raw": "<html><body><h1>News</h1></body></html>", **parse_html_metadata("<html><body><h1>News</h1></body></html>", url)}, {"requests": 1, "bytes": 10, "latencies_ms": [1], "phase_timings_ms": {}}

        with patch("norway_company_agent.site_facts._fetch_fact_document", side_effect=fake_fetch):
            enrich_site_facts(profile, max_extra_requests=8, max_news_requests=6)
        self.assertIn("https://example.no/feed/", requested)
        self.assertIn("https://example.no/feed/?paged=2", requested)
        self.assertIn("https://example.no/wp-json/wp/v2/posts?per_page=20&_fields=link,title,date,date_gmt", requested)

    def test_news_discovery_follows_archive_and_page_two(self) -> None:
        root = "https://example.no/"
        html = "<html><body><a href='/nyheter'>Nyheter</a></body></html>"
        profile = profile_with_html(html, url=root)
        profile["evidence"]["website"]["value"]["pages"][0]["links"] = [{"url": "/nyheter", "text": "Nyheter"}]
        requested: list[str] = []
        listing = "<html><body><h1>Nyheter</h1><a href='/nyhetsarkiv'>Arkiv</a><a href='/nyheter/page/2'>Neste</a></body></html>"

        def fake_fetch(url: str, **_kwargs: object) -> tuple[dict, dict]:
            requested.append(url)
            raw = listing if url.endswith("/nyheter") else "<html><body><h1>Archive</h1></body></html>"
            return {"kind": "html", "url": url, "final_url": url, "status_code": 200, "raw": raw, **parse_html_metadata(raw, url)}, {"requests": 1, "bytes": len(raw), "latencies_ms": [1], "phase_timings_ms": {}}

        with patch("norway_company_agent.site_facts._fetch_fact_document", side_effect=fake_fetch):
            enrich_site_facts(profile, max_extra_requests=6, max_news_requests=6)
        self.assertEqual(requested[0], "https://example.no/nyheter")
        self.assertIn("https://example.no/nyhetsarkiv", requested)
        self.assertIn("https://example.no/nyheter/page/2", requested)

    def test_spam_titles_and_privacy_pages_are_not_news(self) -> None:
        # S29 live: hacked homerentals.no feed; norgestinn.no privacy page.
        from norway_company_agent import site_facts

        for title in ("De beste casino senza cruks voor VIP-ervaringen", "Тонкости ставок на 1win", "S Mostbet 50 free spins offer", "Pin Up Jackpot Slotları Bələdçi"):
            self.assertTrue(site_facts.ACTIVITY_SPAM.search(title), title)
        for title in ("Kjøper tilbake Monto AS", "Slottet får nytt tak", "Fra fotball til fagmann"):
            self.assertFalse(site_facts.ACTIVITY_SPAM.search(title), title)
        self.assertFalse(site_facts._activity_url_allowed("https://www.norgestinn.no/personvernerklaering/"))

    def test_generic_cards_on_non_careers_pages_are_not_jobs(self) -> None:
        # S29 live: ArcticZymes /biomanufacturing product card read as a posting.
        from norway_company_agent.site_facts import parse_jobs_html

        card = "<html><body><ul><li><h3>Production Engineer</h3><a href='/x'>Read</a></li></ul></body></html>"
        self.assertEqual(parse_jobs_html(card, "https://example.no/biomanufacturing")[0], [])
        self.assertTrue(parse_jobs_html(card, "https://example.no/karriere")[0])

    def test_catalogue_and_about_pages_are_not_news(self) -> None:
        # S29 live: Ischebeck's site-wide feed lists /produkter/ pages; RsV Gruppen's
        # "Om RsV Gruppen AS" page carries a published_time meta tag.
        from norway_company_agent import site_facts

        self.assertFalse(site_facts._activity_url_allowed("https://www.ischebeck.no/produkter/forskaling/forankringstilbehor/"))
        self.assertFalse(site_facts._activity_url_allowed("https://rsvgruppen.no/om-rsv-gruppen-as/"))
        self.assertTrue(site_facts._activity_url_allowed("https://www.ischebeck.no/prosjekter/bygg/kaffefabrikken-i-vestby/"))
        self.assertTrue(site_facts._activity_url_allowed("https://rsvgruppen.no/rsv-gruppen-feirer-25-ar/"))

    def test_blind_career_probe_is_not_starved_by_news_probes(self) -> None:
        # S29 live regression (Studix, studix.no/karriere): with no careers or news links,
        # six news probes used the whole budget and the careers page was never fetched.
        root = "https://example.no/"
        profile = profile_with_html("<html><body><h1>Example</h1></body></html>", url=root)
        requested: list[str] = []
        careers = "<html><head><title>Example</title></head><body><h1>ABOUT US</h1><h1>EXAMPLE IS HIRING</h1></body></html>"

        def fake_fetch(url: str, **_kwargs: object) -> tuple[dict, dict]:
            requested.append(url)
            raw = careers if url.endswith("/karriere") else "<html><body><h1>Side ikke funnet</h1></body></html>"
            return {"kind": "html", "url": url, "final_url": url, "status_code": 200, "raw": raw, **parse_html_metadata(raw, url)}, {"requests": 1, "bytes": len(raw), "latencies_ms": [1], "phase_timings_ms": {}}

        with patch("norway_company_agent.site_facts._fetch_fact_document", side_effect=fake_fetch):
            facts, _metrics = enrich_site_facts(profile, max_extra_requests=8, max_news_requests=6)
        self.assertIn("https://example.no/karriere", requested)
        self.assertEqual(facts["hiring"]["status"], "available")
        self.assertEqual(facts["hiring"]["value"]["careers_page_url"], "https://example.no/karriere")

    def test_sitemap_is_only_an_article_url_source(self) -> None:
        sitemap = "<urlset><url><loc>https://example.no/nyheter/one</loc><lastmod>2099-01-01</lastmod></url><url><loc>https://example.no/about/one</loc><lastmod>2025-01-01</lastmod></url></urlset>"
        self.assertEqual(parse_sitemap_article_urls(sitemap, "https://example.no/sitemap.xml"), ["https://example.no/nyheter/one"])

    def test_article_date_ladder_uses_url_date_and_rejects_future_in_extraction(self) -> None:
        article = "<html><head><title>URL dated announcement</title></head><body><h1>URL dated announcement</h1><p>Good news.</p></body></html>"
        items = parse_activity_html(article, "https://example.no/2025/09/22/url-dated-announcement")
        self.assertEqual(items[0]["date"], "2025-09-22")
        profile = profile_with_html("<html><body><h1>Example</h1></body></html>")
        future = "<html><body><article><h2>Future item</h2><time datetime='2099-01-01'>1. januar 2099</time></article></body></html>"
        profile["evidence"]["website"]["value"]["pages"] = [{"url": "https://example.no/news", "html": future, **parse_html_metadata(future, "https://example.no/news")}]
        facts = extract_site_facts(profile)
        self.assertEqual(facts["public_activity"]["value"]["items"], [])

    def test_old_feed_item_is_retained_as_a_dated_news_claim(self) -> None:
        feed = "<rss><channel><item><title>Long ago announcement</title><pubDate>Sun, 01 Jan 2023 12:00:00 +0100</pubDate><link>https://example.no/news/old</link></item></channel></rss>"
        profile = profile_with_html("<html><body><h1>Example</h1></body></html>")
        facts = extract_site_facts(profile, extra_documents=[{"kind": "feed", "url": "https://example.no/feed.xml", "raw": feed}])
        profile["evidence"].update(facts)
        envelope = terminal_envelope(profile, run_id="old-news", modules=["website"], started_at="2026-10-01T12:00:00Z", completed_at="2026-10-01T12:00:00Z")
        news = [claim for claim in envelope["claims"] if claim["field"] == "dated_news"]
        self.assertEqual(len(news), 1)
        self.assertIn("2023-01-01T12:00:00+01:00", news[0]["value"])
        self.assertTrue(facts["public_activity"]["value"]["items"][0]["old"])

    def test_fourteen_publishable_feed_items_become_fourteen_claims(self) -> None:
        items = "".join(
            f"<item><title>Announcement {index}</title><pubDate>Mon, {index:02d} Jan 2025 12:00:00 +0100</pubDate><link>https://example.no/news/{index}</link></item>"
            for index in range(1, 15)
        )
        feed = f"<rss><channel>{items}</channel></rss>"
        profile = profile_with_html("<html><body><h1>Example</h1></body></html>")
        facts = extract_site_facts(profile, extra_documents=[{"kind": "feed", "url": "https://example.no/feed.xml", "raw": feed}])
        profile["evidence"].update(facts)
        envelope = terminal_envelope(profile, run_id="fourteen-news", modules=["website"], started_at="2026-10-01T12:00:00Z", completed_at="2026-10-01T12:00:00Z")
        self.assertEqual(len([claim for claim in envelope["claims"] if claim["field"] == "dated_news"]), 14)

    def test_teaser_without_date_is_not_published_but_failed_upgrade_keeps_feed_claim(self) -> None:
        teaser = "<html><body><section><h2><a href='/news/teaser'>Teaser title</a></h2></section></body></html>"
        self.assertEqual(parse_activity_html(teaser, "https://example.no/news"), [])
        feed = "<rss><channel><item><title>Feed title</title><pubDate>Mon, 22 Sep 2025 20:00:00 +0200</pubDate><link>https://example.no/news/feed-title</link></item></channel></rss>"
        profile = profile_with_html("<html><body><h1>Example</h1></body></html>")

        def failed_fetch(*_args: object, **_kwargs: object) -> tuple[None, dict]:
            return None, {"requests": 1, "bytes": 0, "latencies_ms": [1], "error": "fixture failure"}

        profile["evidence"]["website"]["value"]["pages"][0]["links"] = [{"url": "/feed.xml", "text": "RSS"}]
        feed_document = {"kind": "feed", "url": "https://example.no/feed.xml", "raw": feed}
        feed_operation = {"requests": 1, "bytes": len(feed), "latencies_ms": [1], "phase_timings_ms": {}}
        with patch("norway_company_agent.site_facts._fetch_fact_document", side_effect=[(feed_document, feed_operation), failed_fetch()]):
            # The feed item remains valid even when its article upgrade fails.
            facts, _metrics = enrich_site_facts(profile, max_extra_requests=2)
        self.assertTrue(facts["public_activity"]["value"]["items"])
        profile["evidence"].update(facts)
        envelope = terminal_envelope(profile, run_id="failed-upgrade", modules=["website"], started_at="2026-10-01T12:00:00Z", completed_at="2026-10-01T12:00:00Z")
        self.assertEqual(len([claim for claim in envelope["claims"] if claim["field"] == "dated_news"]), 1)

    def test_feed_published_at_keeps_verbatim_iso_offset(self) -> None:
        feed = "<rss><channel><item><title>Builderr example</title><pubDate>2025-09-22T20:00:00+02:00</pubDate><link>https://example.no/news/example</link></item></channel></rss>"
        item = parse_activity_feed(feed, "https://example.no/feed.xml")[0]
        self.assertEqual(item["published_at"], "2025-09-22T20:00:00+02:00")

    def test_article_pages_are_verified_against_listing_title_and_date(self) -> None:
        root = "https://example.no/"
        root_html = "<html><body><a href='/news'>News</a><a href='/careers'>Careers</a></body></html>"
        profile = profile_with_html(root_html, url=root)
        profile["evidence"]["website"]["value"]["pages"] = [{"url": root, "html": root_html, **parse_html_metadata(root_html, root)}]
        listing_url = "https://example.no/news"
        article_url = "https://example.no/news/launch"
        listing_html = "<html><body><article><h2><a href='/news/launch'>Launch announced</a></h2><time datetime='2026-09-20T10:00:00+02:00'>20. september 2026</time></article></body></html>"
        article_html = "<html><body><article><h1>Launch announced</h1><time datetime='2026-09-20T10:00:00+02:00'>20. september 2026</time><p>We launched a new service.</p></article></body></html>"

        def fake_fetch(url: str, **_kwargs: object):
            if url == listing_url:
                return {"url": url, "kind": "html", "html": listing_html, "content_sha256": "b" * 64, **parse_html_metadata(listing_html, url)}, {"requests": 1, "bytes": len(listing_html), "latencies_ms": [1], "phase_timings_ms": {}}
            if url == "https://example.no/careers":
                career = "<html><body><h1>Careers</h1></body></html>"
                return {"url": url, "kind": "html", "html": career, "content_sha256": "c" * 64, **parse_html_metadata(career, url)}, {"requests": 1, "bytes": len(career), "latencies_ms": [1], "phase_timings_ms": {}}
            if url == article_url:
                return {"url": url, "kind": "html", "html": article_html, "content_sha256": "d" * 64, **parse_html_metadata(article_html, url)}, {"requests": 1, "bytes": len(article_html), "latencies_ms": [1], "phase_timings_ms": {}}
            return None, {"requests": 1, "bytes": 0, "latencies_ms": [1], "phase_timings_ms": {}, "error": "fixture_miss"}

        with patch("norway_company_agent.site_facts._fetch_fact_document", side_effect=fake_fetch):
            facts, metrics = enrich_site_facts(profile, max_extra_requests=3)

        items = facts["public_activity"]["value"]["items"]
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["article_verified"])
        self.assertEqual(items[0]["article_url"], article_url)
        self.assertEqual(items[0]["article_content_sha256"], "d" * 64)
        self.assertEqual(items[0]["discovered_via"], listing_url)
        self.assertEqual(metrics["extra_requests"], 3)

    def test_repeated_claims_have_independent_evidence(self) -> None:
        from norway_company_agent.registry.batch import terminal_envelope, validate_envelopes

        url = "https://example.no/"
        social = "https://facebook.com/example"
        website = evidence(
            "website", "available", "company_owned", url,
            value={"final_url": url, "title": "Example", "social_links": [{"platform": "facebook", "url": social}], "links": [{"url": social, "text": "Facebook"}], "pages": []},
            retrieved_at="2026-10-01T12:00:00Z", content_sha256="a" * 64,
        )
        activity = evidence(
            "public_activity", "available", "company_owned", url,
            value={"items": [{"title": "Launch announced", "published_at": "2026-09-20T10:00:00+02:00", "date": "2026-09-20", "url": "https://example.no/news/launch", "article_verified": True, "article_url": "https://example.no/news/launch", "article_content_sha256": "b" * 64, "discovered_via": "https://example.no/news", "claim_span": "Launch announced"}]},
            retrieved_at="2026-10-01T12:00:00Z", content_sha256="b" * 64,
        )
        hiring = evidence(
            "hiring", "available", "company_owned", url,
            value={"careers_page_url": "https://example.no/careers", "career_claim_span": "Careers", "postings": [{"title": "Developer", "url": "https://example.no/jobs/developer", "claim_span": "Developer", "extraction_method": "html_jsonld"}]},
            retrieved_at="2026-10-01T12:00:00Z", content_sha256="c" * 64,
        )
        profile = {"organisation_number": ORG, "name": "Example AS", "evidence": {"website": website, "public_activity": activity, "hiring": hiring}}
        envelope = terminal_envelope(profile, run_id="repeated", modules=["website"], started_at="2026-10-01T12:00:00Z", completed_at="2026-10-01T12:00:00Z")
        repeated = [claim for claim in envelope["claims"] if claim["field"] in {"social_profile", "hiring_signal", "dated_news"}]
        self.assertEqual({claim["field"] for claim in repeated}, {"social_profile", "hiring_signal", "dated_news"})
        self.assertEqual(len(repeated), 4)
        evidence_by_id = {item["id"]: item for item in envelope["evidence"]}
        for claim in repeated:
            self.assertEqual(len(claim["evidence_ids"]), 1)
            row = evidence_by_id[claim["evidence_ids"][0]]
            self.assertTrue(row["extraction_method"])
            self.assertTrue(row["source_url"])
        social_claim = next(claim for claim in repeated if claim["field"] == "social_profile")
        self.assertEqual(social_claim["value"], social)
        self.assertEqual(social_claim["match_key"], "facebook.com/example")
        news_claim = next(claim for claim in repeated if claim["field"] == "dated_news")
        self.assertEqual(news_claim["value_details"]["url"], "https://example.no/news/launch")
        self.assertTrue(validate_envelopes([envelope], 1)["passed"])
        self.assertEqual(envelope["external"]["handles"], [{"platform": "facebook", "url": social, "rightsStatus": "approved"}])
        self.assertEqual(envelope["external"]["company_site"]["posts"][0]["text"], "Launch announced")
        self.assertTrue(audit([envelope])["passed"])
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

        self.assertEqual(requested[:3], ["https://example.no/karriere", "https://example.no/aktuelt", "https://example.no/feed.xml"])
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
