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
