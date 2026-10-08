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


def _self_test(client: NavFeedClient, *, since: str | None, max_pages: int | None, max_details: int | None) -> dict[str, object]:
    """Use the live feed/index path, then prove exact-org ``collect`` matching."""
    index: dict[str, dict[str, object]] = {}
    stats: dict[str, object] = {}
    build_index(client, since_http_date=since, max_pages=max_pages, max_details=max_details, index=index, max_workers=4, stats=stats)
    if not index:
        return {"passed": False, "reason": "no_valid_active_ad_in_feed_slice", "feed_entries_seen": stats.get("feed_entries_seen", 0)}
    org, entry = next(iter(index.items()))
    profile = {"organisation_number": org, "name": entry.get("employer_name") or ""}
    result = collect(profile, now=datetime.now(timezone.utc), context={"index": index})
    return {
        "passed": result.get("status") == "available" and bool(result.get("observations")),
        "organisation_number": org,
        "collect_status": result.get("status"),
        "observations": len(result.get("observations") or []),
        "feed_entries_seen": stats.get("feed_entries_seen", 0),
        "detail_requests": stats.get("detail_requests", 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the NAV employer index.")
    parser.add_argument("--output", required=True, help="Index JSONL; merged into if it already exists")
    parser.add_argument("--report", required=True)
    parser.add_argument("--since-days", type=float, help="Incremental refresh window; omit for the complete active index")
    parser.add_argument("--since-http-date", help="Pass the feed's previous Last-Modified value for an incremental refresh")
    parser.add_argument("--max-pages", type=int, help="Testing cap; omit for every feed page")
    parser.add_argument("--max-details", type=int, help="Testing cap; omit for every active ad detail")
    parser.add_argument("--min-interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--progress", help="JSON progress file; completed detail URLs are resumed")
    parser.add_argument("--self-test", action="store_true", help="Use one live feed ad to prove exact-org collect matching")
    parser.add_argument("--universe", help="Universe JSONL(.gz), used only for in-universe employer counts")
    args = parser.parse_args()

    output = Path(args.output)
    index = load_index(output)
    before = len(index)
    since = args.since_http_date
    if since is None and args.since_days is not None:
        since = format_datetime(datetime.now(timezone.utc) - timedelta(days=args.since_days), usegmt=True)
    client = NavFeedClient(os.environ.get("NAV_FEED_TOKEN") or None, min_interval=args.min_interval, timeout=args.timeout)
    errors: list[str] = []
    started_at = utc_now()
    started_clock = time.monotonic()
    self_test = _self_test(client, since=since, max_pages=args.max_pages, max_details=args.max_details) if args.self_test else None
    stats: dict[str, object] = {}
    if not args.self_test:
        try:
            build_index(
                client,
                since_http_date=since,
                max_pages=args.max_pages,
                max_details=args.max_details,
                index=index,
                on_error=lambda url, exc: errors.append(f"{url}: {type(exc).__name__}"),
                max_workers=4,
                progress_path=args.progress or str(output) + ".progress.json",
                stats=stats,
            )
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            stats["complete"] = False
            stats["feed_error"] = type(exc).__name__
            errors.append(f"feed: {type(exc).__name__}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for org in sorted(index):
            handle.write(json.dumps(index[org], ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(output)
    meta = {"complete": bool(stats.get("complete")) and not args.self_test, "built_at": utc_now(), "since": since, "feed_entries_seen": stats.get("feed_entries_seen", 0), "detail_requests": stats.get("detail_requests", 0)}
    meta_path = output.with_suffix(output.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
        "ads": sum(len(entry.get("ads") or []) for entry in index.values()),
        "distinct_organisation_numbers": len(index),
        "distinct_organisation_numbers_in_universe": len(set(index) & universe_orgs) if universe_orgs else None,
        "employers_before": before,
        "employers_after": len(index),
        "employers_with_homepage": sum(1 for entry in index.values() if entry["homepages"]),
        "errors": errors[:20],
        "error_count": len(errors),
        "timeouts": stats.get("detail_timeouts", getattr(client, "timeouts", 0)),
        "detail_errors": stats.get("detail_errors", len(errors)),
        "circuit_breaker_tripped": stats.get("circuit_breaker_tripped", False),
        "progress_path": args.progress or str(output) + ".progress.json",
        "self_test": self_test,
        "complete_index": meta["complete"],
        "metadata_path": str(meta_path),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
