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
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.evidence import utc_now  # noqa: E402
from norway_company_agent.external.nav_jobs import NavFeedClient, build_index, collect, load_index  # noqa: E402

CONNECTOR_ID = "nav_jobs"


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _self_test(client: NavFeedClient, *, since: str | None, max_pages: int | None, max_details: int | None) -> dict[str, object]:
    """Use the live feed/index path, then prove exact-org ``collect`` matching."""
    index: dict[str, dict[str, object]] = {}
    stats: dict[str, object] = {}
    build_index(
        client,
        since_http_date=since,
        dated_request=since is not None,
        max_pages=max_pages,
        max_details=max_details,
        index=index,
        max_workers=8,
        stats=stats,
    )
    if not index:
        return {"passed": False, "reason": "no_valid_active_ad_in_feed_slice", "feed_entries_seen": stats.get("feed_entries_seen", 0)}
    org, entry = next(iter(index.items()))
    profile = {"organisation_number": org, "name": entry.get("employer_name") or ""}
    result = collect(profile, now=datetime.now(timezone.utc), context={"index": index, "client": client, "index_complete": True})
    return {
        "passed": result.get("status") == "available" and bool(result.get("observations")),
        "organisation_number": org,
        "collect_status": result.get("status"),
        "observations": len(result.get("observations") or []),
        "feed_entries_seen": stats.get("feed_entries_seen", 0),
        "detail_requests": stats.get("detail_requests", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the NAV employer index.")
    parser.add_argument("--output", required=True, help="Index JSONL")
    parser.add_argument("--report", required=True)
    parser.add_argument("--since-days", type=float, help="Incremental refresh window; omit for the complete active index")
    parser.add_argument("--since-http-date", help="Pass the feed's previous Last-Modified value for an incremental refresh")
    parser.add_argument("--resume-from-meta", action="store_true", help="Resume from feed_cursor in the existing index metadata")
    parser.add_argument("--max-pages", type=int, help="Testing cap; omit for every feed page")
    parser.add_argument("--max-details", type=int, help="Testing cap; omit for every active ad detail")
    parser.add_argument("--max-age-days", type=float, default=200.0, help="Drop feed changes older than this while reducing pages")
    parser.add_argument("--progress-every", type=int, default=25, help="Persist feed progress every N pages")
    parser.add_argument("--page-attempts", type=int, default=6, help="Attempts per feed page")
    parser.add_argument("--page-backoff", type=float, default=1.0, help="Initial retry backoff in seconds")
    parser.add_argument("--page-jitter", type=float, default=0.25, help="Maximum random retry jitter in seconds")
    parser.add_argument("--page-failure-limit", type=int, default=10, help="Consecutive page-failure circuit-breaker limit")
    parser.add_argument("--min-interval", type=float, default=0.125)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--progress", help="JSON progress file; feed cursor and UUID reduction are resumed")
    parser.add_argument("--self-test", action="store_true", help="Use one live feed ad to prove exact-org collect matching")
    parser.add_argument("--universe", help="Universe JSONL(.gz), used only for in-universe employer counts")
    args = parser.parse_args()

    output = Path(args.output)
    meta_path = output.with_suffix(output.suffix + ".meta.json")
    existing_index = load_index(output)
    incremental_base = bool(args.resume_from_meta or args.since_days is not None or args.since_http_date is not None)
    index = existing_index if incremental_base else {}
    before = len(existing_index)
    since = args.since_http_date
    if since is None and args.since_days is not None:
        since = format_datetime(datetime.now(timezone.utc) - timedelta(days=args.since_days), usegmt=True)
    client = NavFeedClient(os.environ.get("NAV_FEED_TOKEN") or None, min_interval=args.min_interval, timeout=args.timeout)
    errors: list[str] = []
    started_at = utc_now()
    started_clock = time.monotonic()
    self_test = None
    stats: dict[str, object] = {}
    result_index: dict[str, dict[str, object]] = existing_index
    exit_code = 0
    try:
        if args.self_test:
            self_test = _self_test(client, since=since, max_pages=args.max_pages, max_details=args.max_details)
            stats["complete"] = False
        else:
            result_index = build_index(
                client,
                since_http_date=since,
                dated_request=since is not None,
                max_pages=args.max_pages,
                max_details=args.max_details,
                index=index,
                on_error=lambda url, exc: errors.append(f"{url}: {type(exc).__name__}"),
                max_workers=8,
                progress_path=args.progress or str(output) + ".progress.json",
                index_path=output,
                max_age_days=args.max_age_days,
                progress_every=args.progress_every,
                page_attempts=args.page_attempts,
                page_backoff=args.page_backoff,
                page_jitter=args.page_jitter,
                page_failure_limit=args.page_failure_limit,
                resume_from_meta=args.resume_from_meta,
                meta_path=meta_path,
                stats=stats,
            )
    except (Exception, KeyboardInterrupt) as exc:
        stats["complete"] = False
        stats["feed_error"] = type(exc).__name__
        errors.append(f"feed: {exc}")
        exit_code = 1

    complete = bool(stats.get("complete")) and not args.self_test and exit_code == 0
    report_index = result_index if complete else existing_index
    if complete:
        meta = {
            "complete": True,
            "built_at": utc_now(),
            "since": since,
            "refresh_mode": stats.get("refresh_mode", "full"),
            "pages_fetched": stats.get("pages_fetched", 0),
            "feed_cursor": stats.get("feed_cursor"),
            "first_page_id": (stats.get("feed_cursor") or {}).get("first_page_id") if isinstance(stats.get("feed_cursor"), dict) else None,
            "feed_entries_seen": stats.get("feed_entries_seen", 0),
            "unique_feed_uuids": stats.get("unique_feed_uuids", 0),
            "inactive_unique_uuids": stats.get("inactive_unique_uuids", 0),
            "detail_requests": stats.get("detail_requests", 0),
            "details_processed": stats.get("details_processed", 0),
            "flushes": stats.get("flushes", 0),
        }
        _atomic_json(meta_path, meta)
    else:
        exit_code = 1 if not args.self_test else exit_code

    universe_orgs: set[str] = set()
    if args.universe:
        import gzip
        with gzip.open(args.universe, "rt", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    universe_orgs.add(str(json.loads(line).get("organisation_number")))
    report = {
        "started_at": started_at,
        "completed_at": utc_now(),
        "source": "NAV pam-stilling-feed",
        "token": "private" if os.environ.get("NAV_FEED_TOKEN") else "public_experiment",
        "since": since,
        "requests": client.requests,
        "wall_time_seconds": round(time.monotonic() - started_clock, 2),
        "ads": sum(len(entry.get("ads") or []) for entry in report_index.values()),
        "unique_feed_uuids": stats.get("unique_feed_uuids", 0),
        "inactive_unique_uuids": stats.get("inactive_unique_uuids", 0),
        "distinct_organisation_numbers": len(report_index),
        "distinct_organisation_numbers_in_universe": len(set(report_index) & universe_orgs) if universe_orgs else None,
        "employers_before": before,
        "employers_after": len(report_index),
        "employers_with_homepage": sum(1 for entry in report_index.values() if entry.get("homepages")),
        "errors": errors[:20],
        "messages": stats.get("messages", []),
        "error_count": len(errors),
        "timeouts": stats.get("detail_timeouts", getattr(client, "timeouts", 0)),
        "detail_errors": stats.get("detail_errors", len(errors)),
        "page_failures": stats.get("page_failures", 0),
        "circuit_breaker_tripped": stats.get("circuit_breaker_tripped", False),
        "refresh_mode": stats.get("refresh_mode", "full"),
        "pages_fetched": stats.get("pages_fetched", 0),
        "feed_cursor": stats.get("feed_cursor"),
        "progress_path": args.progress or str(output) + ".progress.json",
        "self_test": self_test,
        "complete_index": complete,
        "metadata_path": str(meta_path),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
