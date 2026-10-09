from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.external.nav_jobs import NavFeedClient, build_index, collect, load_index, merge_ad, parse_ad, parse_feed_page, parse_public_page, parse_token, verify_public_page  # noqa: E402
from norway_company_agent.web.candidates import nav_employer_candidates, registry_candidates  # noqa: E402
from scripts.analysis.review_candidates import build_queue, export_hard_negatives, run_review  # noqa: E402

ORG = "923609016"


def feed_item(uuid: str, status: str = "ACTIVE") -> dict:
    return {"id": uuid, "url": f"/api/v1/feedentry/{uuid}", "_feed_entry": {"uuid": uuid, "status": status, "businessName": "Norsk Fiskeeksport AS", "municipal": "NOTODDEN"}}


def ad_detail(uuid: str = "ad-1", org: str = ORG, homepage: str = "https://www.fiskeeksport.no/", status: str = "ACTIVE") -> dict:
    return {"uuid": uuid, "status": status, "ad_content": {
        "uuid": uuid, "title": "Fiskearbeider", "published": "2026-10-02T17:33:19+02:00", "expires": "2026-10-18T00:00:00+02:00",
        "link": f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}", "applicationUrl": "", "source": "Stillingsregistrering",
        "contactList": [{"name": "A", "email": "Jobb@Fiskeeksport.no"}, {"name": "B", "email": None}],
        "employer": {"name": "Norsk Fiskeeksport AS", "orgnr": org, "homepage": homepage},
    }}


class NavParsingTests(unittest.TestCase):
    def test_token_is_the_last_line_of_the_response(self) -> None:
        self.assertEqual(parse_token("Current public token for Nav Job Vacancy Feed:\neyJhbGciOi.abc.def\n"), "eyJhbGciOi.abc.def")

    def test_feed_page_keeps_only_active_entries(self) -> None:
        items, next_url = parse_feed_page({"items": [feed_item("a"), feed_item("b", "INACTIVE")], "next_url": "/api/v1/feed/next"})
        self.assertEqual([item["uuid"] for item in items], ["a"])
        self.assertEqual(next_url, "/api/v1/feed/next")

    def test_active_ad_yields_employer_facts_keyed_by_org_number(self) -> None:
        parsed = parse_ad(ad_detail())
        self.assertEqual(parsed["organisation_number"], ORG)
        self.assertEqual(parsed["homepage"], "https://www.fiskeeksport.no/")
        self.assertEqual(parsed["contact_email_domains"], ["fiskeeksport.no"])
        self.assertIsNone(parsed["ad"]["application_url"])

    def test_masked_inactive_or_invalid_org_ads_are_ignored(self) -> None:
        self.assertIsNone(parse_ad({"uuid": "x", "status": "INACTIVE"}))
        self.assertIsNone(parse_ad(ad_detail(status="INACTIVE")))
        self.assertIsNone(parse_ad(ad_detail(org="923609017")))  # fails the checksum

    def test_merge_deduplicates_ads_and_homepages(self) -> None:
        index: dict = {}
        for _ in range(2):
            merge_ad(index, parse_ad(ad_detail()))
        merge_ad(index, parse_ad(ad_detail("ad-2", homepage="https://fiskeeksport.no/")))
        entry = index[ORG]
        self.assertEqual(len(entry["ads"]), 2)
        self.assertEqual(len(entry["homepages"]), 2)


class FakeClient:
    def __init__(self, pages: dict, details: dict) -> None:
        self.pages, self.details, self.calls = pages, details, []

    def get_json(self, path: str, headers=None):
        self.calls.append(path)
        return self.pages[path] if path in self.pages else self.details[path]


class NavIndexTests(unittest.TestCase):
    def test_build_index_follows_pages_and_respects_the_detail_cap(self) -> None:
        pages = {
            "/api/v1/feed": {"items": [feed_item("a"), feed_item("b")], "next_url": "/api/v1/feed/2"},
            "/api/v1/feed/2": {"items": [feed_item("c")], "next_url": None},
        }
        details = {f"/api/v1/feedentry/{uuid}": ad_detail(uuid) for uuid in "abc"}
        client = FakeClient(pages, details)
        index = build_index(client, since_http_date=None, max_pages=5, max_details=2)
        self.assertEqual(len(index[ORG]["ads"]), 2)
        self.assertNotIn("/api/v1/feedentry/c", client.calls)

    def test_index_round_trips_through_load_index(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "index.jsonl"
            self.assertEqual(load_index(path), {})
            index: dict = {}
            merge_ad(index, parse_ad(ad_detail()))
            path.write_text("".join(json.dumps(entry) + "\n" for entry in index.values()), encoding="utf-8")
            self.assertEqual(load_index(path)[ORG]["homepages"], ["https://www.fiskeeksport.no/"])


class LiveNavVerificationTests(unittest.TestCase):
    META = {"complete": True, "built_at": "2026-10-09T12:00:00Z"}

    class DetailClient:
        def __init__(self, responses: dict[str, object]) -> None:
            self.responses = responses
            self.calls: list[str] = []

        def get_json(self, path: str):
            self.calls.append(path)
            response = self.responses[path]
            if isinstance(response, BaseException):
                raise response
            return response

    def _profile(self) -> dict[str, object]:
        return {"organisation_number": ORG, "name": "Norsk Fiskeeksport AS"}

    def _index(self, *ads: dict) -> dict[str, dict]:
        return {ORG: {"organisation_number": ORG, "employer_name": "Norsk Fiskeeksport AS", "ads": list(ads)}}

    def _ad(self, uuid: str = "ad-1") -> dict:
        return {
            "uuid": uuid,
            "link": f"https://pam-stilling-feed.nav.no/api/v1/feedentry/{uuid}",
            "title": "Indexed title",
            "published": "2026-10-01T00:00:00Z",
            "expires": "2026-10-18T00:00:00Z",
        }

    def test_live_active_matched_ad_is_published_with_refresh_metadata(self) -> None:
        detail = ad_detail()
        client = self.DetailClient({"https://pam-stilling-feed.nav.no/api/v1/feedentry/ad-1": detail})
        result = collect(
            self._profile(), now=datetime(2026, 10, 10, tzinfo=timezone.utc),
            context={"index": self._index(self._ad()), "index_meta": self.META, "index_complete": True, "client": client},
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(len(client.calls), 1)
        ad = result["ads"][0]
        self.assertTrue(ad["live_verified"])
        self.assertEqual(ad["index_built_at"], self.META["built_at"])
        self.assertEqual(ad["feed_source_url"], "https://pam-stilling-feed.nav.no/api/v1/feed")
        self.assertTrue(ad["live_retrieved_at"])
        self.assertEqual(len(ad["live_content_sha256"]), 64)

    def test_expired_removed_and_mismatched_ads_are_not_published(self) -> None:
        expired = ad_detail("ad-expired")
        expired["ad_content"]["expires"] = "2026-10-01T00:00:00Z"
        removed = ad_detail("ad-removed", status="REMOVED")
        mismatch = ad_detail("ad-mismatch", org="923609025")
        responses = {
            f"https://pam-stilling-feed.nav.no/api/v1/feedentry/{uuid}": detail
            for uuid, detail in (("ad-expired", expired), ("ad-removed", removed), ("ad-mismatch", mismatch))
        }
        result = collect(
            self._profile(), now=datetime(2026, 10, 10, tzinfo=timezone.utc),
            context={"index": self._index(self._ad("ad-expired"), self._ad("ad-removed"), self._ad("ad-mismatch")), "index_meta": self.META, "index_complete": True, "client": self.DetailClient(responses)},
        )
        self.assertEqual(result["status"], "not_available")
        self.assertEqual(result["observations"], [])

    def test_detail_failure_is_not_checked_and_publishes_nothing(self) -> None:
        client = self.DetailClient({"https://pam-stilling-feed.nav.no/api/v1/feedentry/ad-1": TimeoutError("NAV timeout")})
        result = collect(
            self._profile(), now=datetime(2026, 10, 10, tzinfo=timezone.utc),
            context={"index": self._index(self._ad()), "index_meta": self.META, "index_complete": True, "client": client},
        )
        self.assertEqual(result["status"], "not_checked")
        self.assertEqual(result["observations"], [])
        self.assertIn("live verification", result["note"])

    def test_index_without_a_live_client_never_publishes(self) -> None:
        result = collect(
            self._profile(), now=datetime(2026, 10, 10, tzinfo=timezone.utc),
            context={"index": self._index(self._ad()), "index_meta": self.META, "index_complete": True},
        )
        self.assertEqual(result["status"], "not_checked")
        self.assertEqual(result["observations"], [])

    def test_live_verification_fetches_at_most_five_ads_per_company(self) -> None:
        ads = [self._ad(f"ad-{index}") for index in range(6)]
        responses = {
            f"https://pam-stilling-feed.nav.no/api/v1/feedentry/ad-{index}": ad_detail(f"ad-{index}")
            for index in range(6)
        }
        client = self.DetailClient(responses)
        result = collect(
            self._profile(), now=datetime(2026, 10, 10, tzinfo=timezone.utc),
            context={"index": self._index(*ads), "index_meta": self.META, "index_complete": True, "client": client},
        )
        self.assertEqual(len(client.calls), 5)
        self.assertEqual(len(result["observations"]), 5)


class NavPublicPageFixtureTests(unittest.TestCase):
    ACTIVE_HTML = (ROOT / "tests/fixtures/nav_jobs/nav_page_active.html").read_text(encoding="utf-8")
    EXPIRED_HTML = (ROOT / "tests/fixtures/nav_jobs/nav_page_expired.html").read_text(encoding="utf-8")
    NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)

    def _verify(self, html: str, *, org: str = ORG) -> dict:
        return verify_public_page(
            "https://arbeidsplassen.nav.no/stillinger/stilling/nav-fixture-active",
            organisation_number=ORG,
            sub_unit_organisation_number=None,
            now=self.NOW,
            fetcher=lambda _url, _timeout: html,
        )

    def test_active_matching_page_publishes_parsed_payload_metadata(self) -> None:
        parsed = parse_public_page(self.ACTIVE_HTML)
        self.assertEqual(parsed["employer"]["orgnr"], ORG)
        self.assertEqual(parsed["status"], "ACTIVE")
        result = self._verify(self.ACTIVE_HTML)
        self.assertTrue(result["ok"])
        self.assertEqual(result["parsed"]["ad"]["title"], "Fiskearbeider")
        self.assertEqual(result["parsed"]["extraction_method"], "nav_public_page")
        self.assertTrue(result["retrieved_at"])
        self.assertEqual(len(result["content_sha256"]), 64)

    def test_expired_page_does_not_publish(self) -> None:
        result = self._verify(self.EXPIRED_HTML)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "expired")

    def test_mismatched_employer_does_not_publish(self) -> None:
        mismatch = self.ACTIVE_HTML.replace("923609016", "923609025")
        result = self._verify(mismatch)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "organisation_mismatch")


class RealNavPublicPageFixtureTests(unittest.TestCase):
    IGLO_URL = "https://arbeidsplassen.nav.no/stillinger/stilling/c5a984e5-c30d-4794-adca-05e72e6ecf2e"
    FAVORIT_URL = "https://arbeidsplassen.nav.no/stillinger/stilling/4c38bf0e-c23d-4e4f-ba76-1d7932d95c58"
    IGLO_HTML = (ROOT / "tests/fixtures/nav_jobs/real_page_iglo_active_2026-10-09.html").read_text(encoding="utf-8")
    FAVORIT_HTML = (ROOT / "tests/fixtures/nav_jobs/real_page_favorit_active_2026-10-09.html").read_text(encoding="utf-8")
    NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)

    def _verify(self, html: str, url: str, organisation_number: str, sub_unit: str | None = None) -> dict:
        return verify_public_page(
            url,
            organisation_number=organisation_number,
            sub_unit_organisation_number=sub_unit,
            now=self.NOW,
            fetcher=lambda _url, _timeout: html,
        )

    def test_real_iglo_page_is_active_and_matches_parent_or_sub_unit(self) -> None:
        parsed = parse_public_page(self.IGLO_HTML, self.IGLO_URL)
        self.assertEqual(parsed["uuid"], "c5a984e5-c30d-4794-adca-05e72e6ecf2e")
        self.assertEqual(parsed["status"], "ACTIVE")
        self.assertEqual(parsed["employer"]["orgnr"], "936226507")
        result = self._verify(self.IGLO_HTML, self.IGLO_URL, "936204724", "936226507")
        self.assertTrue(result["ok"])
        self.assertEqual(result["parsed"]["ad"]["title"], "Tømrer til varierteoppgaver")
        self.assertEqual(result["parsed"]["extraction_method"], "nav_public_page")

    def test_real_favorit_page_is_active_and_matches_sub_unit(self) -> None:
        parsed = parse_public_page(self.FAVORIT_HTML, self.FAVORIT_URL)
        self.assertEqual(parsed["uuid"], "4c38bf0e-c23d-4e4f-ba76-1d7932d95c58")
        self.assertEqual(parsed["status"], "ACTIVE")
        self.assertEqual(parsed["employer"]["orgnr"], "971686294")
        result = self._verify(self.FAVORIT_HTML, self.FAVORIT_URL, "936204724", "971686294")
        self.assertTrue(result["ok"])
        self.assertEqual(result["parsed"]["ad"]["title"], "Butikkmedarbeider")

    def test_real_page_rejections_do_not_use_similar_ads(self) -> None:
        closed = self.IGLO_HTML.replace(r'\"status\":\"ACTIVE\"', r'\"status\":\"CLOSED\"', 1)
        expired = self.IGLO_HTML.replace("2026-10-17T22:00:00.000Z", "2020-10-17T22:00:00.000Z", 1)
        mismatch = self.IGLO_HTML.replace(r'\"orgnr\":\"936226507\"', r'\"orgnr\":\"936226508\"', 1)
        for html, reason in ((closed, "inactive"), (expired, "expired"), (mismatch, "organisation_mismatch")):
            with self.subTest(reason=reason):
                result = self._verify(html, self.IGLO_URL, "936204724", "936226507")
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], reason)

    def test_real_page_uuid_mismatch_is_rejected(self) -> None:
        other_url = self.IGLO_URL.replace("c5a984e5-c30d-4794-adca-05e72e6ecf2e", "4c38bf0e-c23d-4e4f-ba76-1d7932d95c58")
        result = self._verify(self.IGLO_HTML, other_url, "936204724", "936226507")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "payload_mismatch")

    def test_collect_reports_rejection_reason_counts(self) -> None:
        closed = self.IGLO_HTML.replace(r'\"status\":\"ACTIVE\"', r'\"status\":\"CLOSED\"', 1)
        expired = self.IGLO_HTML.replace("2026-10-17T22:00:00.000Z", "2020-10-17T22:00:00.000Z", 1)
        mismatch = self.IGLO_HTML.replace(r'\"orgnr\":\"936226507\"', r'\"orgnr\":\"936226508\"', 1)
        page_by_url = {
            self.IGLO_URL + "?case=closed": closed,
            self.IGLO_URL + "?case=expired": expired,
            self.IGLO_URL + "?case=mismatch": mismatch,
            self.IGLO_URL + "?case=missing": "<html><body>no payload</body></html>",
            self.FAVORIT_URL: self.IGLO_HTML,
        }
        ads = [{"uuid": str(index), "link": url} for index, url in enumerate(page_by_url)]
        index = {"936204724": {"organisation_number": "936204724", "ads": ads}}
        result = collect(
            {"organisation_number": "936204724", "name": "Iglo Entreprenør As"},
            now=self.NOW,
            context={
                "index": index,
                "index_meta": {"complete": True, "built_at": "2026-10-09T12:00:00Z"},
                "index_complete": True,
                "client": NavPublicFallbackTests.FailingDetailClient(),
                "public_page_fetcher": lambda url, _timeout: page_by_url[url],
            },
        )
        self.assertEqual(result["operations"]["live_rejection_reasons"], {
            "inactive": 1,
            "expired": 1,
            "organisation_mismatch": 1,
            "payload_missing": 1,
            "payload_mismatch": 1,
        })

    def test_next_data_ad_data_is_selected_by_uuid(self) -> None:
        payload = {
            "props": {"pageProps": {"adData": {
                "id": "11111111-1111-4111-8111-111111111111",
                "status": "ACTIVE",
                "title": "Developer",
                "published": "2026-10-01T00:00:00Z",
                "expires": "2099-10-01T00:00:00Z",
                "employer": {"orgnr": ORG, "name": "Norsk Fiskeeksport AS"},
            }}}
        }
        html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"
        parsed = parse_public_page(html, "https://arbeidsplassen.nav.no/stillinger/stilling/11111111-1111-4111-8111-111111111111")
        self.assertEqual(parsed["title"], "Developer")


class NavPublicFallbackTests(unittest.TestCase):
    META = {"complete": True, "built_at": "2026-10-09T12:00:00Z"}
    NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
    ACTIVE_HTML = (ROOT / "tests/fixtures/nav_jobs/nav_page_active.html").read_text(encoding="utf-8")

    class FailingDetailClient:
        def __init__(self, error: BaseException = TimeoutError("NAV token timeout")) -> None:
            self.error = error
            self.calls = 0
            self.requests = 0

        def get_json(self, _path: str):
            self.calls += 1
            raise self.error

    def _profile(self) -> dict[str, object]:
        return {"organisation_number": ORG, "name": "Norsk Fiskeeksport AS"}

    def _index(self) -> dict[str, dict]:
        page_url = "https://arbeidsplassen.nav.no/stillinger/stilling/nav-fixture-active"
        return {ORG: {"organisation_number": ORG, "ads": [{"uuid": "nav-fixture-active", "link": page_url, "title": "Indexed title", "published": "2026-10-02T00:00:00Z", "expires": "2099-10-18T00:00:00Z"}]}}

    def test_token_or_detail_timeout_falls_back_to_public_page(self) -> None:
        client = self.FailingDetailClient()
        result = collect(
            self._profile(),
            now=self.NOW,
            context={
                "index": self._index(),
                "index_meta": self.META,
                "index_complete": True,
                "client": client,
                "public_page_fetcher": lambda _url, _timeout: self.ACTIVE_HTML,
            },
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["observations"][0]["extraction_method"], "nav_public_page")
        self.assertTrue(result["ads"][0]["live_verified"])
        self.assertEqual(client.calls, 1)

    def test_api_and_public_page_failure_gives_not_checked(self) -> None:
        client = self.FailingDetailClient()
        result = collect(
            self._profile(),
            now=self.NOW,
            context={
                "index": self._index(),
                "index_meta": self.META,
                "index_complete": True,
                "client": client,
                "public_page_fetcher": lambda _url, _timeout: (_ for _ in ()).throw(TimeoutError("page timeout")),
            },
        )
        self.assertEqual(result["status"], "not_checked")
        self.assertEqual(result["observations"], [])
        self.assertIn("live verification", result["note"])

    def test_public_page_accepts_indexed_sub_unit_match(self) -> None:
        client = self.FailingDetailClient()
        sub_unit_html = self.ACTIVE_HTML.replace("923609016", "971686294")
        index = self._index()
        index[ORG]["ads"][0]["sub_unit_organisation_number"] = "971686294"
        result = collect(
            self._profile(),
            now=self.NOW,
            context={
                "index": index,
                "index_meta": self.META,
                "index_complete": True,
                "client": client,
                "public_page_fetcher": lambda _url, _timeout: sub_unit_html,
            },
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["observations"][0]["organisation_number"], ORG)

    def test_expired_and_mismatched_public_pages_publish_nothing(self) -> None:
        expired_html = (ROOT / "tests/fixtures/nav_jobs/nav_page_expired.html").read_text(encoding="utf-8")
        for html in (expired_html, self.ACTIVE_HTML.replace("923609016", "923609025")):
            with self.subTest(page=html[:40]):
                result = collect(
                    self._profile(),
                    now=self.NOW,
                    context={
                        "index": self._index(),
                        "index_meta": self.META,
                        "index_complete": True,
                        "client": self.FailingDetailClient(),
                        "public_page_fetcher": lambda _url, _timeout, page=html: page,
                    },
                )
                self.assertEqual(result["status"], "not_available")
                self.assertEqual(result["observations"], [])

    def test_public_token_endpoint_is_attempted_once_after_failure(self) -> None:
        client = NavFeedClient(token=None, timeout=6.0, retries=0)
        with patch.object(client, "_get", side_effect=TimeoutError("token timeout")) as get:
            with self.assertRaises(TimeoutError):
                client.ensure_token()
            with self.assertRaises(TimeoutError):
                client.ensure_token()
        self.assertEqual(get.call_count, 1)


class NavCandidateTests(unittest.TestCase):
    PROFILE = {"organisation_number": "923 609 016", "name": "Norsk Fiskeeksport AS", "evidence": {"registry": {"value": {"epostadresse": "post@other.no"}}}}

    def test_nav_homepage_is_the_first_candidate_for_the_exact_org_number(self) -> None:
        index: dict = {}
        merge_ad(index, parse_ad(ad_detail()))
        found = registry_candidates(self.PROFILE, name_domains=False, nav_index=index)
        self.assertEqual([c["provider"] for c in found], ["nav_employer_homepage", "registry_email_domain"])
        self.assertEqual(found[0]["registered_domain"], "fiskeeksport.no")

    def test_other_organisations_and_missing_homepages_yield_nothing(self) -> None:
        index: dict = {}
        merge_ad(index, parse_ad(ad_detail(homepage="")))
        self.assertEqual(nav_employer_candidates(self.PROFILE, index), [])
        self.assertEqual(nav_employer_candidates({**self.PROFILE, "organisation_number": "982942942"}, index), [])


def scorecard() -> dict:
    return {"review_queue": ["2"], "verdicts": {
        "1": {"outcome": "verified", "domain": "a.no", "signals": {"address_match": True}},
        "2": {"outcome": "no_candidate", "domain": None, "signals": {}},
        "3": {"outcome": "crawled_identity_failed", "domain": None, "signals": {}},
    }}


ROWS = [{"organisation_number": org, "name": f"Co {org}", "municipality": "OSLO", "evidence": {}} for org in "123"]


class ReviewTests(unittest.TestCase):
    def test_queue_has_flagged_and_published_companies_but_skips_labeled_ones(self) -> None:
        self.assertEqual(build_queue(scorecard(), set()), ["2", "1"])
        self.assertEqual(build_queue(scorecard(), {"1"}, also_unverified=5), ["2", "3"])

    def test_answers_become_labels_that_the_scorecard_understands(self) -> None:
        answers = iter(["h", "y"])  # queue order: "2" (unpublished) then "1" (published)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "labels.jsonl"
            written = run_review(ROWS, scorecard(), path, ask=lambda _prompt: next(answers), say=lambda _line: None)
            labels = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(written, 2)
        by_org = {label["organisation_number"]: label for label in labels}
        self.assertEqual((by_org["1"]["exact"], by_org["1"]["has_site"]), (True, True))
        self.assertEqual(by_org["2"]["has_site"], True)
        self.assertNotIn("exact", by_org["2"])

    def test_invalid_answers_reprompt_skip_does_not_label_and_quit_stops(self) -> None:
        answers = iter(["zzz", "s", "q"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "labels.jsonl"
            written = run_review(ROWS, scorecard(), path, ask=lambda _prompt: next(answers), say=lambda _line: None)
            self.assertEqual(written, 0)
            self.assertFalse(path.exists())

    def test_wrong_company_labels_export_as_hard_negatives(self) -> None:
        labels = [
            {"organisation_number": "1", "name": "A", "domain": "a.no", "exact": False},
            {"organisation_number": "2", "name": "B", "domain": "b.no", "exact": True},
            {"organisation_number": "3", "name": "C", "domain": None, "has_site": True},
        ]
        self.assertEqual([item["organisation_number"] for item in export_hard_negatives(labels)], ["1"])


if __name__ == "__main__":
    unittest.main()
