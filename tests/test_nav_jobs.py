from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.external.nav_jobs import build_index, load_index, merge_ad, parse_ad, parse_feed_page, parse_token  # noqa: E402
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
