#!/usr/bin/env python3
"""Finish extension labels using only cached non-search evidence.

The alternative-provider search is intentionally absent here.  A negative can
be promoted only when all four non-search checks are clean; it remains a low-
confidence diagnostic label and the scorer treats it as undetermined.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

from annotate_eval_evidence import annotate_one, load_registry, read_jsonl  # noqa: E402


NON_SEARCH_CHECKS = (
    "registry_website_and_email_domain",
    "nav_employer_index",
    "name_derived_domains",
    "address_and_phone_page_check",
)


def _reason(checks: dict[str, Any], pages: list[dict[str, Any]]) -> str:
    for name in NON_SEARCH_CHECKS:
        item = checks.get(name) or {}
        if item.get("status") == "fail":
            return {
                "registry_website_and_email_domain": "registry_site_candidate",
                "nav_employer_index": "nav_homepage_candidate",
                "name_derived_domains": "name_derived_candidate",
                "address_and_phone_page_check": "ambiguous_page",
            }[name]
        if item.get("status") == "not_run":
            return {
                "registry_website_and_email_domain": "registry_only_no_page",
                "nav_employer_index": "nav_check_not_run",
                "name_derived_domains": "name_derived_not_run",
                "address_and_phone_page_check": "registry_only_no_page",
            }[name]
    for page in pages:
        status = str((page.get("website") or {}).get("status") or "")
        note = str((page.get("website") or {}).get("note") or "").casefold()
        if status == "blocked" or "robots" in note:
            return "fetch_blocked"
        if status in {"failed", "source_error"}:
            return "dns_fail" if "resolv" in note or "dns" in note else "fetch_error"
    return "search_not_run"


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def finish(
    manifest_path: Path,
    registry_path: Path,
    previous_path: Path,
    search_path: Path,
    pages_path: Path,
    nav_path: Path,
    output_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    manifest = read_jsonl(manifest_path)
    previous = {str(row["organisation_number"]): row for row in read_jsonl(previous_path)}
    search_rows = {str(row["organisation_number"]): row for row in read_jsonl(search_path)}
    pages_by_org: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in read_jsonl(pages_path):
        pages_by_org[str(page["organisation_number"])].append(page)
    targets = {str(row["organisation_number"]) for row in manifest}
    registry = load_registry(registry_path, targets)
    nav_index = {str(row.get("organisation_number")): row for row in read_jsonl(nav_path)} if nav_path.exists() else {}
    output: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    check_status: Counter[str] = Counter()
    changed_by_check: Counter[str] = Counter()
    for manifest_row in manifest:
        org = str(manifest_row["organisation_number"])
        old = dict(previous[org])
        fresh = annotate_one(manifest_row, registry[org], search_rows[org], pages_by_org.get(org, []), nav_index)
        result = dict(old)
        result["annotation_batch"] = "extension_v2"
        result["annotation_independence"] = "fresh_non_search_checks"
        result["negative_checks"] = fresh.get("negative_checks") or old.get("negative_checks") or {}
        checks = result["negative_checks"]
        for name in NON_SEARCH_CHECKS:
            check_status[f"{name}:{(checks.get(name) or {}).get('status', 'not_run')}"] += 1
        if old.get("outcome") == "undetermined":
            result.update({key: value for key, value in fresh.items() if key not in {"annotation_batch", "annotation_independence"}})
            result["annotation_batch"] = "extension_v2"
            result["annotation_independence"] = "fresh_non_search_checks"
            result["negative_checks"] = fresh.get("negative_checks") or {}
            checks = result["negative_checks"]
            all_non_search_pass = all((checks.get(name) or {}).get("status") == "pass" for name in NON_SEARCH_CHECKS)
            # A fetched first-party-looking page is a live candidate, even if
            # its identity gate did not pass.  It must be opened and judged;
            # four clean cached checks cannot turn it into a negative.
            reviewable_pages = [
                item for item in (fresh.get("audit", {}).get("page_reviews") or [])
                if item.get("website_status") == "available" and not item.get("third_party")
            ]
            if fresh.get("outcome") in {"official_site", "related_only"}:
                pass
            elif all_non_search_pass and not reviewable_pages:
                result.update({
                    "outcome": "no_site_confirmed",
                    "domain": None,
                    "evidence_tier": "none_search_not_run",
                    "confidence": "low",
                    "evidence": "Four cached non-search checks were cleanly negative; alternative-provider search was not run, so this diagnostic negative is excluded from certification.",
                    "found_via": "cached_registry_nav_name_domain_and_contact_checks",
                    "reason_code": "four_non_search_checks_clean_search_not_run",
                })
                result["negative_checks"]["search"] = {"status": "not_run", "reason": "provider_unavailable"}
                result["negative_checks"].setdefault("alternative_provider_search", {"status": "not_run"})
                changed_by_check.update(name for name in NON_SEARCH_CHECKS)
            else:
                result["outcome"] = "undetermined"
                result["domain"] = None
                result["confidence"] = "low"
                result["reason_code"] = "non_directory_candidate" if reviewable_pages else _reason(checks, pages_by_org.get(org, []))
                result["evidence"] = f"Non-search checks did not establish an exact site ({result['reason_code']}); no-site publication is withheld."
                result["found_via"] = "cached_registry_nav_name_domain_and_contact_checks"
        if result.get("outcome") != old.get("outcome") or result.get("domain") != old.get("domain"):
            result["previous_outcome"] = old.get("outcome")
            result["previous_domain"] = old.get("domain")
            changed.append({"organisation_number": org, "from": old.get("outcome"), "to": result.get("outcome"), "reason_code": result.get("reason_code")})
        output.append(result)
    _write(output_path, output)
    report = {
        "rows": len(output),
        "outcomes": dict(Counter(str(row.get("outcome")) for row in output)),
        "outcomes_by_split": {
            split: dict(Counter(str(row.get("outcome")) for row in output if row.get("split") == split))
            for split in ("held_out", "validation")
        },
        "outcomes_by_stratum": {
            stratum: dict(Counter(str(row.get("outcome")) for row in output if row.get("stratum") == stratum))
            for stratum in ("S4", "S5", "S6")
        },
        "confident_determined_by_split": {
            split: sum(row.get("outcome") in {"official_site", "related_only"} for row in output if row.get("split") == split)
            for split in ("held_out", "validation")
        },
        "low_confidence_negative_rows": sum(row.get("outcome") == "no_site_confirmed" and row.get("confidence") == "low" for row in output),
        "changed_rows": len(changed),
        "changed": changed,
        "check_status_counts": dict(sorted(check_status.items())),
        "changed_by_clean_non_search_check": {name: changed_by_check[name] for name in NON_SEARCH_CHECKS},
        "search": {"status": "not_run", "reason": "SerpApi and Linkup diagnosis did not provide an approved alternative-provider search for this run"},
        "output": str(output_path),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Finish extension annotations using cached non-search checks.")
    parser.add_argument("--manifest", default="out/eval-sample/manifest-extension.jsonl", type=Path)
    parser.add_argument("--registry", default="data/brreg-enheter.csv", type=Path)
    parser.add_argument("--previous", default="out/eval-sample/annotations-ext-v1.jsonl", type=Path)
    parser.add_argument("--search", default="out/eval-sample/extension-search-v2.jsonl", type=Path)
    parser.add_argument("--pages", default="out/eval-sample/extension-pages-v2.jsonl", type=Path)
    parser.add_argument("--nav-index", default="out/nav-employer-index.jsonl", type=Path)
    parser.add_argument("--output", default="out/eval-sample/annotations-ext-v2.jsonl", type=Path)
    parser.add_argument("--report", default="out/eval-sample/extension-finish-report.json", type=Path)
    args = parser.parse_args()
    print(json.dumps(finish(args.manifest, args.registry, args.previous, args.search, args.pages, args.nav_index, args.output, args.report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
