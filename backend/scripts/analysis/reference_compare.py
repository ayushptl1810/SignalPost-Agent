#!/usr/bin/env python3
"""Compare a Builderr reference export with SignalPost profiles."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from norway_company_agent.web.website import registered_domain  # noqa: E402


def _rows(path: str | Path) -> list[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    if not text.strip():
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [value]
    raise ValueError(f"expected JSON object/list or JSONL: {path}")


def _org(row: dict[str, Any]) -> str:
    value = row.get("org") or row.get("organisation_number") or row.get("organisationNumber")
    if isinstance(value, dict):
        value = value.get("organisation_number") or value.get("number") or value.get("org")
    return str(value or "")


def _website_value(row: dict[str, Any]) -> dict[str, Any]:
    reference = row.get("web")
    if isinstance(reference, dict):
        value = reference.get("value")
        if isinstance(value, dict):
            return value
    evidence = row.get("evidence")
    if isinstance(evidence, dict):
        record = evidence.get("website")
        if isinstance(record, dict) and isinstance(record.get("value"), dict):
            return record["value"]
    website = row.get("website")
    if isinstance(website, dict):
        record = website.get("evidence") if isinstance(website.get("evidence"), dict) else website
        if isinstance(record, dict) and isinstance(record.get("value"), dict):
            return record["value"]
    return {}


def _domain(row: dict[str, Any], *, reference: bool = False) -> str | None:
    record = row.get("web") if reference else (row.get("evidence") or {}).get("website")
    if not isinstance(record, dict) and not reference:
        website = row.get("website")
        record = website.get("evidence") if isinstance(website, dict) else None
    if isinstance(record, dict) and record.get("status") not in {None, "available"}:
        return None
    url = _website_value(row).get("final_url")
    if not url:
        return None
    domain = registered_domain(str(url))
    return domain or None


def _social_urls(row: dict[str, Any], *, reference: bool = False) -> set[str]:
    if reference:
        external = row.get("external") or {}
        links = external.get("handles") if isinstance(external, dict) else []
    else:
        links = (_website_value(row).get("social_links") or [])
    result: set[str] = set()
    for link in links or []:
        if isinstance(link, dict) and link.get("url"):
            result.add(str(link["url"]).casefold())
        elif isinstance(link, str) and link:
            result.add(link.casefold())
    return result


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    first_party = candidate.get("first_party") or {}
    return {
        "source": candidate.get("source"),
        "state": candidate.get("state"),
        "failure_kind": candidate.get("failure_kind"),
        "first_party_status": first_party.get("status") if isinstance(first_party, dict) else None,
    }


def compare_rows(reference: Iterable[dict[str, Any]], profiles: Iterable[dict[str, Any]]) -> dict[str, Any]:
    reference_by_org = {_org(row): row for row in reference if _org(row)}
    profiles_by_org = {_org(row): row for row in profiles if _org(row)}
    all_orgs = sorted(set(reference_by_org) | set(profiles_by_org))

    reference_web = {_org(row): _domain(row, reference=True) for row in reference_by_org.values()}
    ours_web = {_org(row): _domain(row) for row in profiles_by_org.values()}
    matched_web = sum(bool(reference_web.get(org) and reference_web.get(org) == ours_web.get(org)) for org in all_orgs)
    reference_only_web = sum(bool(reference_web.get(org)) and reference_web.get(org) != ours_web.get(org) for org in all_orgs)
    ours_only_web = sum(bool(ours_web.get(org)) and ours_web.get(org) != reference_web.get(org) for org in all_orgs)
    website_misses = []
    for org in all_orgs:
        expected = reference_web.get(org)
        actual = ours_web.get(org)
        if expected and expected != actual:
            profile = profiles_by_org.get(org, {})
            discovery = profile.get("discovery") or {}
            website_misses.append({
                "organisation_number": org,
                "reference_domain": expected,
                "ours_domain": actual,
                "discovery_state": discovery.get("state"),
                "candidates": [_candidate_summary(item) for item in discovery.get("candidates") or [] if isinstance(item, dict)],
            })

    reference_social = {_org(row): _social_urls(row, reference=True) for row in reference_by_org.values()}
    ours_social = {_org(row): _social_urls(row) for row in profiles_by_org.values()}
    matched_social = sum(bool(reference_social.get(org) & ours_social.get(org, set())) for org in all_orgs)
    reference_only_social = sum(len(reference_social.get(org, set()) - ours_social.get(org, set())) for org in all_orgs)
    ours_only_social = sum(len(ours_social.get(org, set()) - reference_social.get(org, set())) for org in all_orgs)

    return {
        "websites": {
            "reference": sum(bool(value) for value in reference_web.values()),
            "ours": sum(bool(value) for value in ours_web.values()),
            "matched": matched_web,
            "reference_only": reference_only_web,
            "ours_only": ours_only_web,
        },
        "social": {
            "reference": sum(len(value) for value in reference_social.values()),
            "ours": sum(len(value) for value in ours_social.values()),
            "matched": matched_social,
            "reference_only": reference_only_social,
            "ours_only": ours_only_social,
        },
        "website_misses": website_misses,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True)
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = compare_rows(_rows(args.reference), _rows(args.profiles))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
