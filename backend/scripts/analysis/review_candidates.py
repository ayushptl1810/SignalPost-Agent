#!/usr/bin/env python3
"""Label discovery results from the terminal so the scorecard can measure real precision.

Reads a scorecard and its profile rows, asks one question per company, and appends
labels.jsonl. The labels feed score_discovery_run.py --labels, and wrong-company
labels can be exported as hard-negative fixtures.

Published site:   y = exact company, n = wrong company, 0 = company has no official site
Unpublished:      h = company has an official site (we missed it), 0 = no official site
Both:             s = skip, q = quit
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

PUBLISHED_ANSWERS = {"y": {"exact": True, "has_site": True}, "n": {"exact": False}, "0": {"exact": False, "has_site": False}}
UNPUBLISHED_ANSWERS = {"h": {"has_site": True}, "0": {"has_site": False}}
UNVERIFIED_WITH_CANDIDATES = ("identity_ok_first_party_insufficient", "identity_ok_directory_or_registry", "related_entity", "crawled_identity_failed")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_queue(scorecard: dict[str, Any], labeled: set[str], *, also_unverified: int = 0) -> list[str]:
    verdicts = scorecard["verdicts"]
    queue = list(scorecard.get("review_queue") or [])
    queue += [org for org, verdict in verdicts.items() if verdict["outcome"] == "verified"]
    extra = [org for org, verdict in verdicts.items() if verdict["outcome"] in UNVERIFIED_WITH_CANDIDATES]
    queue += extra[:also_unverified]
    return [org for org in dict.fromkeys(queue) if org not in labeled]


def describe(row: dict[str, Any], verdict: dict[str, Any]) -> list[str]:
    lines = [f"{row.get('name')}  [{row.get('organisation_number')}]  {row.get('municipality') or ''}  outcome={verdict['outcome']}"]
    if verdict["outcome"] == "verified":
        signals = {key: value for key, value in (verdict.get("signals") or {}).items() if value}
        lines.append(f"  published: https://{verdict.get('domain')}/   signals: {', '.join(sorted(signals)) or 'none'}")
    for candidate in (row.get("evidence") or {}).get("website_discovered_candidates") or []:
        lines.append(f"  candidate: {candidate.get('registered_domain')} source={candidate.get('source')} identity={candidate.get('identity_status')} first_party={candidate.get('first_party_status')} related={(candidate.get('related') or {}).get('status')}")
    return lines


def run_review(
    rows: list[dict[str, Any]],
    scorecard: dict[str, Any],
    labels_path: Path,
    *,
    also_unverified: int = 0,
    limit: int | None = None,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    by_org = {str(row.get("organisation_number")): row for row in rows}
    labeled = {str(label["organisation_number"]) for label in read_jsonl(labels_path)}
    written = 0
    for org in build_queue(scorecard, labeled, also_unverified=also_unverified):
        if limit is not None and written >= limit:
            break
        row, verdict = by_org.get(org), scorecard["verdicts"].get(org)
        if not row or not verdict:
            continue
        published = verdict["outcome"] == "verified"
        answers = PUBLISHED_ANSWERS if published else UNPUBLISHED_ANSWERS
        for line in describe(row, verdict):
            say(line)
        while True:
            reply = ask(f"  [{'/'.join(answers)}/s/q] > ").strip().lower()
            if reply in answers or reply in {"s", "q"}:
                break
            say("  unrecognised answer")
        if reply == "q":
            break
        if reply == "s":
            continue
        label = {"organisation_number": org, "name": row.get("name"), "domain": verdict.get("domain"), **answers[reply], "labeled_at": now().isoformat().replace("+00:00", "Z")}
        labels_path.parent.mkdir(parents=True, exist_ok=True)
        with labels_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(label, ensure_ascii=False) + "\n")
        written += 1
    return written


def export_hard_negatives(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Published sites a human marked as the wrong company, ready to use as regression fixtures."""
    return [
        {"organisation_number": label["organisation_number"], "name": label.get("name"), "domain": label.get("domain"), "reason": "labeled_wrong_company"}
        for label in labels
        if label.get("exact") is False and label.get("domain")
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Label discovery results.")
    parser.add_argument("--scorecard", required=True)
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--labels", required=True, help="labels.jsonl; appended to")
    parser.add_argument("--also-unverified", type=int, default=0, help="Also review N unpublished companies that had candidates")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--export-hard-negatives", help="Write wrong-company labels to this JSONL and exit")
    args = parser.parse_args()

    labels_path = Path(args.labels)
    if args.export_hard_negatives:
        negatives = export_hard_negatives(read_jsonl(labels_path))
        Path(args.export_hard_negatives).write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in negatives), encoding="utf-8")
        print(f"wrote {len(negatives)} hard negatives")
        return
    scorecard = json.loads(Path(args.scorecard).read_text(encoding="utf-8"))
    written = run_review(read_jsonl(Path(args.profiles)), scorecard, labels_path, also_unverified=args.also_unverified, limit=args.limit)
    print(f"{written} labels written to {labels_path}")


if __name__ == "__main__":
    main()
