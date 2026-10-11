#!/usr/bin/env python3
"""Compare a Builderr reference export with SignalPost profiles."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path
from collections import Counter
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
        links = []
        if isinstance(external, dict):
            links.extend(external.get("handles") or [])
            links.extend((_website_value(row).get("social_links") or []))
    else:
        links = list((_website_value(row).get("social_links") or []))
        for claim in row.get("claims") or []:
            if isinstance(claim, dict) and claim.get("field") == "social_profile":
                links.append(claim.get("value"))
    result: set[str] = set()
    for link in links or []:
        if isinstance(link, dict) and link.get("url"):
            link = link["url"]
        elif isinstance(link, str) and link:
            link = link
        else:
            continue
        normalized = _url_key(str(link))
        if normalized:
            result.add(normalized)
    return result


def _url_key(value: Any) -> str:
    """Builderr URL key: lower-case, no scheme/www, no trailing slash."""
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urllib.parse.urlparse(text if "://" in text else "https://" + text)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    if not host:
        return ""
    path = parsed.path.rstrip("/").casefold()
    return host + path + (f"?{parsed.query.casefold()}" if parsed.query else "")


def _claim_values(row: dict[str, Any], field: str) -> list[Any]:
    values: list[Any] = []
    for claim in row.get("claims") or []:
        if isinstance(claim, dict) and claim.get("field") == field:
            values.append(claim.get("value"))
    return values


def _ours_urls(row: dict[str, Any], field: str) -> set[str]:
    values = _claim_values(row, field)
    if field == "hiring_signal":
        hiring = (row.get("evidence") or {}).get("hiring") or {}
        value = hiring.get("value") if isinstance(hiring, dict) else {}
        values.extend([value.get("careers_page_url")] if isinstance(value, dict) else [])
        values.extend(
            item.get("url")
            for item in (value.get("postings") or []) if isinstance(item, dict)
        ) if isinstance(value, dict) else None
    result: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("url") or value.get("source") or value.get("job_url")
        if value:
            key = _url_key(value)
            if key:
                result.add(key)
    return result


def _reference_hiring_urls(row: dict[str, Any]) -> set[str]:
    external = row.get("external") or {}
    result: set[str] = set()
    if not isinstance(external, dict):
        return result
    company_site = external.get("company_site") or {}
    if isinstance(company_site, dict):
        career = company_site.get("careers_page")
        if isinstance(career, dict):
            career = career.get("url")
        if career:
            result.add(_url_key(career))
        for job in company_site.get("jobs") or []:
            if isinstance(job, dict):
                key = _url_key(job.get("url") or job.get("source"))
                if key:
                    result.add(key)
    linkedin = external.get("linkedin") or {}
    if isinstance(linkedin, dict):
        for job in linkedin.get("jobs") or []:
            if isinstance(job, dict):
                key = _url_key(job.get("job_url") or job.get("source"))
                if key:
                    result.add(key)
    return {key for key in result if key}


def _news_keys(row: dict[str, Any], *, reference: bool = False) -> tuple[set[str], set[str]]:
    """Return all facts and the comparable company-site reference facts."""
    items: list[Any] = []
    comparable: list[Any] = []
    if reference:
        external = row.get("external") or {}
        if isinstance(external, dict):
            linkedin = external.get("linkedin") or {}
            if isinstance(linkedin, dict):
                items.extend(linkedin.get("posts") or [])
            company_site = external.get("company_site") or {}
            if isinstance(company_site, dict):
                comparable.extend(company_site.get("posts") or [])
                items.extend(company_site.get("posts") or [])
    else:
        items.extend(_claim_values(row, "dated_news"))
        activity = (row.get("evidence") or {}).get("public_activity") or {}
        value = activity.get("value") if isinstance(activity, dict) else {}
        if isinstance(value, dict):
            items.extend(value.get("items") or [])
    def key(item: Any) -> str:
        if isinstance(item, dict):
            title = item.get("title") or item.get("text") or item.get("headline") or ""
            published = item.get("published_at") or item.get("date_published") or item.get("date") or ""
            return f"{str(title).strip().casefold()} ({str(published).strip().casefold()})" if title and published else _url_key(item.get("url") or item.get("source"))
        return str(item or "").strip().casefold()
    all_keys = {key_value for key_value in (key(item) for item in items) if key_value}
    comparable_keys = {key_value for key_value in (key(item) for item in comparable) if key_value}
    return all_keys, comparable_keys


def _family_report(reference_values: dict[str, set[str]], ours_values: dict[str, set[str]], *, comparable_reference: dict[str, set[str]] | None = None) -> dict[str, Any]:
    def as_set(value: Any) -> set[str]:
        if isinstance(value, set):
            return value
        if isinstance(value, (list, tuple)):
            return {str(item) for item in value if item}
        return {str(value)} if value else set()

    reference_values = {org: as_set(value) for org, value in reference_values.items()}
    ours_values = {org: as_set(value) for org, value in ours_values.items()}
    if comparable_reference is not None:
        comparable_reference = {org: as_set(value) for org, value in comparable_reference.items()}
    comparable_reference = comparable_reference or reference_values
    ref_companies = {org for org, values in reference_values.items() if values}
    ours_companies = {org for org, values in ours_values.items() if values}
    comparable_companies = {org for org, values in comparable_reference.items() if values}
    matched_companies = {org for org in comparable_companies & ours_companies if comparable_reference.get(org, set()) & ours_values.get(org, set())}
    matched_facts = sum(len(comparable_reference.get(org, set()) & ours_values.get(org, set())) for org in comparable_companies)
    denominator = len(comparable_companies or ref_companies)
    fact_denominator = sum(len(values) for values in comparable_reference.values())
    company_recall = len(matched_companies) / denominator if denominator else 0.0
    fact_recall = matched_facts / fact_denominator if fact_denominator else 0.0
    return {
        "reference_companies": len(ref_companies),
        "ours_companies": len(ours_companies),
        "matched_companies": len(matched_companies),
        "reference_only": sorted(ref_companies - ours_companies),
        "ours_only": sorted(ours_companies - ref_companies),
        "facts_matched": matched_facts,
        "proxy": round(0.7 * company_recall + 0.3 * fact_recall, 4),
    }


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    first_party = candidate.get("first_party") or {}
    return {
        "source": candidate.get("source"),
        "state": candidate.get("state"),
        "failure_kind": candidate.get("failure_kind"),
        "first_party_status": first_party.get("status") if isinstance(first_party, dict) else None,
    }


def compare_rows(
    reference: Iterable[dict[str, Any]],
    profiles: Iterable[dict[str, Any]],
    *,
    compare_before: Iterable[dict[str, Any]] | None = None,
    compare_after: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
    matched_social = sum(bool(reference_social.get(org, set()) & ours_social.get(org, set())) for org in all_orgs)
    reference_only_social = sum(len(reference_social.get(org, set()) - ours_social.get(org, set())) for org in all_orgs)
    ours_only_social = sum(len(ours_social.get(org, set()) - reference_social.get(org, set())) for org in all_orgs)

    reference_hiring = {_org(row): _reference_hiring_urls(row) for row in reference_by_org.values()}
    ours_hiring = {_org(row): _ours_urls(row, "hiring_signal") for row in profiles_by_org.values()}
    reference_news_all: dict[str, set[str]] = {}
    reference_news_comparable: dict[str, set[str]] = {}
    ours_news: dict[str, set[str]] = {}
    for org, row in reference_by_org.items():
        all_news, comparable_news = _news_keys(row, reference=True)
        reference_news_all[org] = all_news
        reference_news_comparable[org] = comparable_news
    for org, row in profiles_by_org.items():
        ours_news[org] = _news_keys(row)[0]

    source_counts: Counter[str] = Counter()
    for row in profiles_by_org.values():
        discovery = row.get("discovery") or {}
        for raw_source, count in (discovery.get("source_yield") or {}).items():
            source = str(raw_source).removesuffix(":g4")
            source_counts[source] += int(count or 0)

    result = {
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
        "families": {
            "website": _family_report(reference_web, ours_web),
            "social_profile": _family_report(reference_social, ours_social),
            "hiring_signal": _family_report(reference_hiring, ours_hiring),
            "dated_news": _family_report(reference_news_all, ours_news, comparable_reference=reference_news_comparable),
        },
        "source_yield": dict(sorted(source_counts.items())),
    }
    if compare_before is not None or compare_after is not None:
        before_by_org = {_org(row): row for row in (compare_before or []) if _org(row)}
        after_by_org = {_org(row): row for row in (compare_after or []) if _org(row)}
        values = {
            "website": ({org: _domain(row) for org, row in before_by_org.items()}, {org: _domain(row) for org, row in after_by_org.items()}),
            "social_profile": ({org: _social_urls(row) for org, row in before_by_org.items()}, {org: _social_urls(row) for org, row in after_by_org.items()}),
            "hiring_signal": ({org: _ours_urls(row, "hiring_signal") for org, row in before_by_org.items()}, {org: _ours_urls(row, "hiring_signal") for org, row in after_by_org.items()}),
            "dated_news": ({org: _news_keys(row)[0] for org, row in before_by_org.items()}, {org: _news_keys(row)[0] for org, row in after_by_org.items()}),
        }
        result["compare"] = {
            family: {
                "gained_companies": sorted(org for org in set(after) - set(before) if after.get(org)),
                "lost_companies": sorted(org for org in set(before) - set(after) if before.get(org)),
            }
            for family, (before, after) in values.items()
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True)
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="Compare two profile/envelope runs for gained/lost companies")
    parser.add_argument("--output")
    args = parser.parse_args()
    compare_before = compare_after = None
    if args.compare:
        compare_before, compare_after = (_rows(args.compare[0]), _rows(args.compare[1]))
    report = compare_rows(_rows(args.reference), _rows(args.profiles), compare_before=compare_before, compare_after=compare_after)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
