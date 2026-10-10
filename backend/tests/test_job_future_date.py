from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.site_facts import parse_jobs_html  # noqa: E402


def test_future_date_on_job_card_is_a_deadline_not_a_posting_date() -> None:
    deadline = (datetime.now(timezone.utc).date() + timedelta(days=21)).isoformat()
    html = f'<ul><li class="job"><h3><a href="/ledige-stillinger/it-konsulent/">IT-konsulent – Sandnes</a></h3><p>Søknadsfrist {deadline}</p></li></ul>'
    jobs, _ = parse_jobs_html(html, "https://example.no/ledige-stillinger/")
    assert jobs and jobs[0]["posted"] is None and jobs[0]["closing"] == deadline


def test_career_terms_must_start_a_word_in_the_path() -> None:
    from norway_company_agent.site_facts import _job_page_is_career_url

    for url in ("https://justismuseet.no/utstillinger/", "https://eurodent.no/timebestilling", "https://skaathunbilverksted.no/timebestilling/"):
        assert not _job_page_is_career_url(url), url
    for url in ("https://x.no/ledige-stillinger/", "https://x.no/om-oss/stilling-ledig", "https://x.no/karriere/", "https://x.no/en/career/", "https://x.no/jobbe-hos-oss"):
        assert _job_page_is_career_url(url), url
