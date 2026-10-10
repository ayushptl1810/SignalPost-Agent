#!/usr/bin/env python3
"""Local approximation of Builderr's published SignalPost scorecard.

The official verified pool is hidden.  This tool therefore reports a clearly
labelled local approximation from supplied envelopes and hand-audited facts; it
does not turn a local result into an official qualification claim.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


TYPE_FIELDS: dict[str, tuple[str, ...]] = {
    "company_brief": ("company_brief", "public_brand"),
    "latest_financials": ("latest_annual_accounts", "accounts_history"),
    "leadership": ("leadership",),
    "registered_workplaces": ("registered_workplaces",),
    "hiring": ("hiring",),
    "dated_public_activity": ("public_activity",),
    "official_website_and_profiles": ("official_website", "company_profiles"),
}
FIELD_TO_TYPE = {field: type_name for type_name, fields in TYPE_FIELDS.items() for field in fields}
FINANCIAL_FIELDS = {"latest_annual_accounts", "accounts_history"}
REQUIRED_EVIDENCE = ("source_url", "retrieved_at", "content_sha256", "claim_span")


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalised_text(value: Any) -> str:
    return re.sub(r"\W+", " ", str(value or "").casefold()).strip()


def fact_items(field: str, value: Any) -> list[Any]:
    """Expand a claim into individual recall facts without inventing values."""
    if value in (None, "", [], {}):
        return []
    if field == "public_activity" and isinstance(value, dict):
        return list(value.get("items") or [])
    if field == "hiring" and isinstance(value, dict):
        if "postings" in value:
            return list(value.get("postings") or [])
        return list(value.get("ads") or value.get("nav_ads") or [])
    if field == "company_brief" and isinstance(value, dict) and value.get("parts"):
        return list(value["parts"])
    if field == "company_profiles" and isinstance(value, dict) and value.get("profiles"):
        return list(value["profiles"])
    if isinstance(value, list):
        return value
    return [value]


def available_claims(row: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for claim in row.get("claims") or []:
        if claim.get("field") in FIELD_TO_TYPE and claim.get("availability") == "available":
            yield claim


def _pool_from_rows(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, dict[str, set[str]]], dict[str, set[str]]]:
    facts: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    companies: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        org = str(row.get("organisation_number") or "")
        if not org:
            continue
        for claim in available_claims(row):
            type_name = FIELD_TO_TYPE[claim["field"]]
            items = fact_items(claim["field"], claim.get("value"))
            if not items:
                items = [claim.get("value")]
            companies[type_name].add(org)
            for item in items:
                facts[type_name][org].add(_json_key(item))
    return facts, companies


def _merge_extra_pool(rows: Iterable[dict[str, Any]], facts: dict[str, dict[str, set[str]]], companies: dict[str, set[str]]) -> None:
    for row in rows:
        org = str(row.get("organisation_number") or "")
        field = str(row.get("field") or "")
        if not org or field not in FIELD_TO_TYPE:
            continue
        type_name = FIELD_TO_TYPE[field]
        value = row.get("value")
        if row.get("availability", "available") != "available":
            continue
        items = fact_items(field, value) or [value]
        companies[type_name].add(org)
        for item in items:
            facts[type_name][org].add(_json_key(item))


def _candidate_fact_sets(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, dict[str, set[str]]], dict[str, set[str]]]:
    return _pool_from_rows(rows)


def _local_recall(candidate_rows: list[dict[str, Any]], pool_rows: list[dict[str, Any]], extra_pool_rows: list[dict[str, Any]]) -> dict[str, Any]:
    pool_facts, pool_companies = _pool_from_rows(pool_rows)
    _merge_extra_pool(extra_pool_rows, pool_facts, pool_companies)
    candidate_facts, candidate_companies = _candidate_fact_sets(candidate_rows)
    positive_company_field_opportunities = sum(len(orgs) for orgs in pool_companies.values())
    field_families = sum(bool(orgs) for orgs in pool_companies.values())
    sparse_pool = positive_company_field_opportunities < 15 and field_families < 3
    by_type: dict[str, Any] = {}
    measured: list[float] = []
    for type_name in TYPE_FIELDS:
        companies = pool_companies.get(type_name, set())
        pool_count = sum(len(items) for items in pool_facts.get(type_name, {}).values())
        candidate_items = candidate_facts.get(type_name, {})
        company_hits = len(companies & candidate_companies.get(type_name, set()))
        claim_hits = sum(len(items & candidate_items.get(org, set())) for org, items in pool_facts.get(type_name, {}).items())
        if sparse_pool or not companies:
            by_type[type_name] = {
                "status": "not_measured",
                "reason": "local pool has fewer than 15 positive company-field opportunities across fewer than three field families" if sparse_pool else "no positive pool opportunities",
                "companies_covered": company_hits,
                "companies_in_pool": len(companies),
                "facts_covered": claim_hits,
                "facts_in_pool": pool_count,
            }
            continue
        company_recall = company_hits / len(companies)
        claim_recall = claim_hits / pool_count if pool_count else 0.0
        score = 0.7 * company_recall + 0.3 * claim_recall
        measured.append(score)
        by_type[type_name] = {
            "status": "measured",
            "company_recall": round(company_recall, 6),
            "claim_recall": round(claim_recall, 6),
            "recall": round(score, 6),
            "pool_companies": len(companies),
            "pool_facts": pool_count,
            "company_hits": company_hits,
            "claim_hits": claim_hits,
            "companies_covered": company_hits,
            "companies_in_pool": len(companies),
            "facts_covered": claim_hits,
            "facts_in_pool": pool_count,
        }
    return {
        "approximation": "The real cumulative verified pool is hidden and larger; these are local pool results only.",
        "mapping": TYPE_FIELDS,
        "positive_company_field_opportunities": positive_company_field_opportunities,
        "field_families": field_families,
        "types": by_type,
        "average_recall": round(sum(measured) / len(measured), 6) if measured else None,
        "measured_types": len(measured),
        "status": "not_measured" if sparse_pool or not measured else "measured",
        "comparability": "self_pool_not_comparable",
    }


def _claim_evidence_complete(row: dict[str, Any], claim: dict[str, Any]) -> bool:
    by_id = {item.get("id"): item for item in row.get("evidence") or []}
    refs = claim.get("evidence_ids") or []
    if not refs:
        return False
    for ref in refs:
        evidence_row = by_id.get(ref, {})
        if not all(evidence_row.get(key) for key in REQUIRED_EVIDENCE):
            return False
        if claim.get("field") in FINANCIAL_FIELDS and _financial_value_has_period(claim.get("value")) and not evidence_row.get("reporting_period"):
            return False
    return True


def _financial_value_has_period(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("period") or value.get("reporting_period"):
            return True
        records = value.get("records")
        if isinstance(records, list):
            return any(isinstance(item, dict) and (item.get("period") or item.get("reporting_period")) for item in records)
    if isinstance(value, list):
        return any(_financial_value_has_period(item) for item in value)
    return False


def _financial_expected(row: dict[str, Any], field: str) -> Any:
    profile_evidence = (row.get("profile") or {}).get("evidence") or {}
    if field == "accounts_history" and profile_evidence.get("financial_history"):
        return (profile_evidence["financial_history"].get("value") or {})
    records = ((profile_evidence.get("financials") or {}).get("value") or {}).get("records") or []
    records = sorted(records, key=lambda item: str((item.get("period") or {}).get("tilDato") or ""), reverse=True)
    if field == "latest_annual_accounts":
        return records[0] if records else None
    return records[1:] or None


def _label_map(labels: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(str(item.get("organisation_number") or ""), str(item.get("field") or "")): item for item in labels if item.get("organisation_number") and item.get("field")}


def _precision_evidence(candidate_rows: list[dict[str, Any]], labels: list[dict[str, Any]]) -> dict[str, Any]:
    label_by_key = _label_map(labels)
    available = []
    evidence_good = 0
    financial_mismatches = []
    field_labels: dict[str, list[str]] = defaultdict(list)
    for row in candidate_rows:
        org = str(row.get("organisation_number") or "")
        for claim in available_claims(row):
            available.append((row, claim))
            evidence_good += int(_claim_evidence_complete(row, claim))
            if claim.get("field") in FINANCIAL_FIELDS:
                expected = _financial_expected(row, claim["field"])
                if expected is not None and _json_key(expected) != _json_key(claim.get("value")):
                    financial_mismatches.append({"organisation_number": org, "field": claim["field"]})
            label = label_by_key.get((org, str(claim.get("field"))))
            if label:
                field_labels[str(claim.get("field"))].append(str(label.get("outcome") or ""))
    evidence_completeness = evidence_good / len(available) if available else 1.0
    labelled_total = sum(len(items) for items in field_labels.values())
    labelled_correct = sum(item == "correct" for items in field_labels.values() for item in items)
    labelled_precision = labelled_correct / labelled_total if labelled_total else None
    financial_fidelity = 1.0 if not financial_mismatches else 0.0
    precision_evidence_ratio = min(
        evidence_completeness,
        *(item for item in (labelled_precision, financial_fidelity) if item is not None),
    )
    wrong_company = sum(1 for label in labels if label.get("outcome") == "wrong_entity")
    return {
        "approximation": "The local 30-point category uses the minimum of evidence completeness and available precision/fidelity diagnostics; Builderr's hidden adjudication is authoritative.",
        "available_claims": len(available),
        "evidence_complete_claims": evidence_good,
        "evidence_completeness": round(evidence_completeness, 6),
        "labelled_precision": round(labelled_precision, 6) if labelled_precision is not None else None,
        "per_field_precision": {field: round(sum(value == "correct" for value in outcomes) / len(outcomes), 6) for field, outcomes in sorted(field_labels.items())},
        "financial_fidelity": round(financial_fidelity, 6),
        "financial_mismatches": financial_mismatches,
        "wrong_company_publications": wrong_company,
        "ratio": round(precision_evidence_ratio, 6),
    }


def _synthesis(candidate_rows: list[dict[str, Any]]) -> dict[str, Any]:
    passed = 0
    details = []
    for row in candidate_rows:
        claims = {str(claim.get("field")): claim for claim in row.get("claims") or []}
        brief = claims.get("company_brief")
        has_brief_source = bool(brief and brief.get("availability") in {"available", "ambiguous"} and brief.get("evidence_ids"))
        has_changes = "changes" in row or bool(row.get("no_change_since_previous_run"))
        unknown = row.get("unknown_areas")
        if unknown is None:
            unknown = [field for field, claim in claims.items() if claim.get("availability") not in {"available", "not_applicable"}]
        has_sources = all(claim.get("availability") != "available" or claim.get("evidence_ids") for claim in claims.values())
        ok = bool(has_brief_source and has_changes and isinstance(unknown, list) and has_sources)
        passed += int(ok)
        details.append({"organisation_number": row.get("organisation_number"), "passed": ok, "brief_source": has_brief_source, "changes_block": has_changes, "unknown_areas": isinstance(unknown, list), "claim_sources": has_sources})
    ratio = passed / len(candidate_rows) if candidate_rows else 0.0
    return {"approximation": "Static contract-shape approximation of Builderr's synthesis sentence.", "profiles": len(candidate_rows), "passed_profiles": passed, "ratio": round(ratio, 6), "details": details, "comparability": "self_pool_not_comparable"}


def _find_report(path: str | Path) -> Path | None:
    candidate = Path(path)
    for name in ("run-report.json", "report.json"):
        found = candidate.parent / name
        if found.exists():
            return found
    return None


def _run_metrics(path: str | Path) -> dict[str, Any]:
    report = _find_report(path)
    if report:
        try:
            body = json.loads(report.read_text(encoding="utf-8"))
            operations = body.get("operations") or {}
            wall_time = operations.get("wall_time_seconds") or (operations.get("discovery") or {}).get("total_runtime_seconds") or (body.get("discovery") or {}).get("total_runtime_seconds")
            return {"path": str(report), "wall_time_seconds": wall_time, "requests": operations.get("requests"), "bytes": operations.get("bytes"), "third_party_cost_usd": operations.get("third_party_cost_usd", 0.0)}
        except (OSError, json.JSONDecodeError):
            pass
    rows = read_jsonl(path)
    operations = [row.get("operations") or {} for row in rows]
    return {"path": None, "wall_time_seconds": None, "requests": sum(int(item.get("requests") or 0) for item in operations), "bytes": sum(int(item.get("bytes") or 0) for item in operations), "third_party_cost_usd": sum(float(item.get("third_party_cost_usd") or 0.0) for item in operations)}


def _ux(report_path: str | Path) -> dict[str, Any]:
    path = Path(report_path)
    if not path.exists():
        return {"status": "not_measured", "ratio": None, "checks": {"report_exists": False}}
    html = path.read_text(encoding="utf-8")
    checks = {
        "report_exists": True,
        "find_search": bool(re.search(r"<input[^>]+(?:search|query)|data-search|id=[\"']search", html, re.I)),
        "compare_multi_select": bool(re.search(r"compare|checkbox|multi-select|selectedCompanies", html, re.I)),
        "verify_source_link": bool(re.search(r"source_url|source link|href=\"https?://", html, re.I)),
        "verify_retrieval_date": bool(re.search(r"retrieved_at|retrieval date", html, re.I)),
        "verify_state": bool(re.search(r"availability|state", html, re.I)),
        "responsive_360": bool(re.search(r"(?:max-width\s*:\s*360px|width\s*:\s*360px|@media)", html, re.I)),
        "no_external_assets": not bool(re.search(r"<(?:script|link|img)[^>]+(?:src|href)=\"https?://", html, re.I)),
    }
    passed = sum(bool(value) for value in checks.values())
    return {"status": "measured", "passed": passed, "checks": checks, "ratio": round(passed / len(checks), 6)}


def _promote(candidate: dict[str, Any], baseline: dict[str, Any] | None, *, minimum_gain: float = 0.02, max_regression: float = 0.01, max_precision_drop: float = 0.01, max_runtime_factor: float = 2.0) -> dict[str, Any]:
    reasons: list[dict[str, Any]] = []
    precision = candidate["precision_evidence"]
    wrong_ok = precision["wrong_company_publications"] == 0
    reasons.append({"rule": "zero wrong-company publications", "passed": wrong_ok, "detail": precision["wrong_company_publications"]})
    evidence_ok = precision["evidence_completeness"] >= 1.0
    reasons.append({"rule": "100% evidence completeness", "passed": evidence_ok, "detail": precision["evidence_completeness"]})
    if baseline is None:
        reasons.append({"rule": "baseline comparison available", "passed": False, "detail": "no baseline supplied"})
        return {"decision": "DO NOT PROMOTE", "reasons": reasons, "declared_minimum_coverage_gain": minimum_gain, "declared_max_type_regression": max_regression, "declared_max_precision_drop": max_precision_drop, "declared_max_runtime_factor": max_runtime_factor}
    cand_types = candidate["recall"]["types"]
    base_types = baseline["recall"]["types"]
    gains = {name: (cand_types.get(name, {}).get("recall") - base_types.get(name, {}).get("recall")) for name in TYPE_FIELDS if cand_types.get(name, {}).get("status") == "measured" and base_types.get(name, {}).get("status") == "measured"}
    gain_ok = any(value >= minimum_gain for value in gains.values())
    regressions = {name: value for name, value in gains.items() if value < -max_regression}
    precision_drop = (baseline["precision_evidence"].get("ratio") or 0.0) - (precision.get("ratio") or 0.0)
    precision_ok = precision_drop <= max_precision_drop
    candidate_runtime = candidate["runtime"].get("wall_time_seconds")
    baseline_runtime = baseline["runtime"].get("wall_time_seconds")
    runtime_ok = bool(candidate_runtime is not None and baseline_runtime not in (None, 0) and candidate_runtime <= max_runtime_factor * baseline_runtime)
    reasons.extend([
        {"rule": f"coverage gain at least {minimum_gain:.2%} in one information type", "passed": gain_ok, "detail": gains},
        {"rule": f"no information type worse by more than {max_regression:.2%}", "passed": not regressions, "detail": regressions},
        {"rule": f"no meaningful precision drop (>{max_precision_drop:.2%})", "passed": precision_ok, "detail": precision_drop},
        {"rule": f"wall time at most {max_runtime_factor:.1f}x baseline", "passed": runtime_ok, "detail": {"candidate": candidate_runtime, "baseline": baseline_runtime}},
    ])
    decision = "PROMOTE" if all(item["passed"] for item in reasons) else "DO NOT PROMOTE"
    return {"decision": decision, "reasons": reasons, "coverage_gains": gains, "declared_minimum_coverage_gain": minimum_gain, "declared_max_type_regression": max_regression, "declared_max_precision_drop": max_precision_drop, "declared_max_runtime_factor": max_runtime_factor}


def score(candidate_rows: list[dict[str, Any]], pool_rows: list[dict[str, Any]], *, extra_pool_rows: list[dict[str, Any]] | None = None, labels: list[dict[str, Any]] | None = None, report_path: str | Path | None = None, baseline_rows: list[dict[str, Any]] | None = None, baseline_report_path: str | Path | None = None, candidate_path: str | Path | None = None, baseline_path: str | Path | None = None) -> dict[str, Any]:
    extra_pool_rows = extra_pool_rows or []
    labels = labels or []
    recall = _local_recall(candidate_rows, pool_rows, extra_pool_rows)
    precision_evidence = _precision_evidence(candidate_rows, labels)
    synthesis = _synthesis(candidate_rows)
    ux = _ux(report_path or "submission/smoke-100/report.html")
    runtime = _run_metrics(candidate_path or "submission/smoke-100/envelopes.jsonl")
    categories = {
        "recall_and_coverage": {"weight": 50, "ratio": recall["average_recall"], "points": round(50 * recall["average_recall"], 6) if recall["average_recall"] is not None else None, "comparability": recall["comparability"]},
        "precision_and_evidence": {"weight": 30, "ratio": precision_evidence["ratio"], "points": round(30 * precision_evidence["ratio"], 6)},
        "synthesis": {"weight": 12, "ratio": synthesis["ratio"], "points": round(12 * synthesis["ratio"], 6), "comparability": synthesis["comparability"]},
        "ux": {"weight": 8, "ratio": ux.get("ratio"), "points": round(8 * ux["ratio"], 6) if ux.get("ratio") is not None else None},
    }
    scored_points = [item["points"] for item in categories.values() if item["points"] is not None]
    total = round(sum(scored_points), 6) if len(scored_points) == len(categories) else None
    result = {
        "approximation": "Local approximation only: Builderr's cumulative verified pool and adjudication are hidden.",
        "categories": categories,
        "recall": recall,
        "precision_evidence": precision_evidence,
        "synthesis": synthesis,
        "ux": ux,
        "total": total,
        "total_note": "not an estimate of the official score",
        "qualifies_locally": bool(total is not None and total >= 65 and precision_evidence["wrong_company_publications"] == 0),
        "runtime": runtime,
        "baseline_runtime": _run_metrics(baseline_report_path) if baseline_report_path else None,
        "tiebreaks": {
            "wrong_company_publications": precision_evidence["wrong_company_publications"],
            "weighted_company_recall": recall["average_recall"],
            "declared_third_party_cost_usd": runtime.get("third_party_cost_usd", 0.0),
            "order": ["fewer wrong-company publications", "higher weighted company recall", "lower declared third-party cost"],
        },
    }
    baseline_result = None
    if baseline_rows is not None:
        baseline_result = score(baseline_rows, pool_rows, extra_pool_rows=extra_pool_rows, labels=labels, report_path=report_path, baseline_report_path=baseline_report_path, candidate_path=baseline_path or "submission/smoke-100/envelopes.jsonl")
    result["promotion"] = _promote(result, baseline_result)
    return result


def _format_text(result: dict[str, Any]) -> str:
    lines = [
        "LOCAL SCORECARD (approximation; the official verified pool is hidden)",
        "Category                         Weight  Ratio       Points",
    ]
    for name, category in result["categories"].items():
        ratio = "n/m" if category["ratio"] is None else f"{category['ratio']:.2%}"
        points = "n/m" if category["points"] is None else f"{category['points']:.2f}"
        lines.append(f"{name:<32} {category['weight']:>6}  {ratio:>8}  {points:>8}")
    lines.append(f"Total (not an estimate of the official score) {'':>2}  {result['total'] if result['total'] is not None else 'not measured'}")
    lines.append(f"Recall comparability: {result['recall']['comparability']}")
    lines.append(f"Synthesis comparability: {result['synthesis']['comparability']}")
    lines.append("Per-type recall (70% company recall + 30% claim recall):")
    for name, item in result["recall"]["types"].items():
        if item.get("status") != "measured":
            lines.append(f"  {name}: NOT MEASURED — {item.get('reason')}; companies {item.get('companies_covered', 0)}/{item.get('companies_in_pool', 0)}, facts {item.get('facts_covered', 0)}/{item.get('facts_in_pool', 0)}")
        else:
            lines.append(f"  {name}: company {item['company_recall']:.2%} ({item['companies_covered']}/{item['companies_in_pool']}), claims {item['claim_recall']:.2%} ({item['facts_covered']}/{item['facts_in_pool']}), combined {item['recall']:.2%}")
    lines.append(f"Wrong-company publications: {result['precision_evidence']['wrong_company_publications']}")
    lines.append(f"Runtime: {json.dumps(result['runtime'], ensure_ascii=False, sort_keys=True)}")
    lines.append(f"PROMOTION: {result['promotion']['decision']}")
    for reason in result["promotion"]["reasons"]:
        lines.append(f"  {'PASS' if reason['passed'] else 'FAIL'} — {reason['rule']}: {json.dumps(reason['detail'], ensure_ascii=False, sort_keys=True)}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--baseline")
    parser.add_argument("--pool", action="append", required=True)
    parser.add_argument("--extra-verified-facts", action="append", default=[])
    parser.add_argument("--audit-labels")
    parser.add_argument("--report")
    parser.add_argument("--baseline-report")
    parser.add_argument("--output", help="Write JSON result to this path")
    args = parser.parse_args()
    candidate = read_jsonl(args.candidate)
    baseline = read_jsonl(args.baseline) if args.baseline else None
    pool = [row for path in args.pool for row in read_jsonl(path)]
    extra = [row for path in args.extra_verified_facts for row in read_jsonl(path)]
    labels = read_jsonl(args.audit_labels) if args.audit_labels else []
    result = score(candidate, pool, extra_pool_rows=extra, labels=labels, report_path=args.report or "submission/smoke-100/report.html", baseline_rows=baseline, baseline_report_path=args.baseline_report, candidate_path=args.candidate, baseline_path=args.baseline)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(_format_text(result))
    print("JSON:")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
