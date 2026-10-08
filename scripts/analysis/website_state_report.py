#!/usr/bin/env python3
"""Explain every non-complete website result in a batch profiles file."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def _reason(candidate: dict[str, Any], website: dict[str, Any]) -> str:
    candidate_url = str(candidate.get("requested_url") or website.get("source_url") or "")
    if candidate.get("block_reason"):
        reason = str(candidate["block_reason"])
        if "robot" in reason.casefold() and "url:" not in reason.casefold():
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(candidate_url)
            robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", "")) if parsed.netloc else candidate_url
            return f"{reason}; robots URL: {robots_url}"
        if "policy" not in reason.casefold() or "url" not in reason.casefold():
            return f"{reason}; policy URL: https://builderr.ai/docs/signalpost-evaluation-harness.md"
        return reason
    if candidate.get("failure_kind"):
        return str(candidate["failure_kind"])
    first_party = candidate.get("first_party") or {}
    signals = first_party.get("signals") or {}
    if candidate.get("related_only", {}).get("related_only"):
        return f"related_only:{candidate.get('related_only', {}).get('reason')}"
    for key in ("directory_marker", "listing_path_marker", "blocked_host", "contradicted"):
        if signals.get(key):
            return key
    note = website.get("note") or candidate.get("note")
    return str(note or candidate.get("website_status") or "no publishable candidate")


def build_report(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for profile in rows:
        website = (profile.get("evidence") or {}).get("website") or {}
        if website.get("status") == "available":
            continue
        candidates = (profile.get("discovery") or {}).get("candidates") or []
        driver = next((item for item in candidates if item.get("state") in {"blocked", "failed", "ambiguous"}), None)
        if driver is None and candidates:
            driver = candidates[-1]
        driver = driver or {"requested_url": website.get("source_url"), "state": (profile.get("discovery") or {}).get("state")}
        report.append({
            "organisation_number": profile.get("organisation_number"),
            "name": profile.get("name"),
            "module_status": website.get("status"),
            "discovery_state": (profile.get("discovery") or {}).get("state"),
            "candidate": driver.get("requested_url") or driver.get("domain"),
            "candidate_source": driver.get("source"),
            "candidate_state": driver.get("state"),
            "reason": _reason(driver, website),
        })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
    report = build_report(rows)
    text = "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in report)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
    print(json.dumps({"rows": len(report), "states": dict(Counter(item.get("candidate_state") for item in report))}, ensure_ascii=False), file=sys.stderr)
    print(text, end="")


if __name__ == "__main__":
    main()
