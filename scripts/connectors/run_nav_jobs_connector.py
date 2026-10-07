#!/usr/bin/env python3
"""Build or refresh an organisation-number employer index from NAV's public job-vacancy feed.

The index maps organisation number -> registered employer homepage, contact email
domains and active ads. Discovery uses the homepage as a high-priority candidate that
still has to pass the identity gate; the ads are job evidence keyed by exact org number.
Set NAV_FEED_TOKEN to use a private token; otherwise the published experiment token is used.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.evidence import utc_now  # noqa: E402
from norway_company_agent.external.nav_jobs import NavFeedClient, build_index, load_index  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the NAV employer index.")
    parser.add_argument("--output", required=True, help="Index JSONL; merged into if it already exists")
    parser.add_argument("--report", required=True)
    parser.add_argument("--since-days", type=float, default=2.0, help="Only ads modified in this window")
    parser.add_argument("--max-pages", type=int, default=3, help="Feed pages of up to 1000 entries each")
    parser.add_argument("--max-details", type=int, default=500, help="Ad detail requests; one request per ad")
    parser.add_argument("--min-interval", type=float, default=0.1)
    args = parser.parse_args()

    output = Path(args.output)
    index = load_index(output)
    before = len(index)
    since = format_datetime(datetime.now(timezone.utc) - timedelta(days=args.since_days), usegmt=True)
    client = NavFeedClient(os.environ.get("NAV_FEED_TOKEN") or None, min_interval=args.min_interval)
    errors: list[str] = []
    started_at = utc_now()
    build_index(
        client,
        since_http_date=since,
        max_pages=args.max_pages,
        max_details=args.max_details,
        index=index,
        on_error=lambda url, exc: errors.append(f"{url}: {type(exc).__name__}"),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for org in sorted(index):
            handle.write(json.dumps(index[org], ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(output)
    report = {
        "started_at": started_at,
        "completed_at": utc_now(),
        "source": "NAV pam-stilling-feed",
        "token": "private" if os.environ.get("NAV_FEED_TOKEN") else "public_experiment",
        "since": since,
        "requests": client.requests,
        "employers_before": before,
        "employers_after": len(index),
        "employers_with_homepage": sum(1 for entry in index.values() if entry["homepages"]),
        "errors": errors[:20],
        "error_count": len(errors),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
