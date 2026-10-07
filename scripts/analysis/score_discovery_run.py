#!/usr/bin/env python3
"""Score one discovery run and compare it with the previous run.

Works without labels (funnel, yield, trust proxy, cost). Labels add measured
precision with a Wilson lower bound. Outcomes are derived only from fields the
discovery runner already writes, so the pipeline does not change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.web.website import registered_domain  # noqa: E402

# Outcomes from best to worst. A company gets the best outcome any of its candidates reached.
OUTCOMES = (
    "verified",
    "identity_ok_first_party_insufficient",
    "identity_ok_directory_or_registry",
    "related_entity",
    "crawled_identity_failed",
    "no_candidate",
    "search_skipped_triage",
    "provider_failed",
    "timed_out",
    "registry_site",
    "negative_cache",
    "not_queried",
)
LOSS_STAGES = ("identity_ok_first_party_insufficient", "identity_ok_directory_or_registry", "related_entity", "crawled_identity_failed", "no_candidate")
NOT_QUERIED = ("registry_site", "negative_cache", "not_queried", "search_skipped_triage")
ANNOTATION_OUTCOMES = {"official_site", "related_only", "no_site_confirmed", "undetermined"}
ANNOTATION_SPLITS = {"development", "held_out", "validation"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _normalise_domain(value: Any) -> str | None:
    value = str(value or "").strip().casefold()
    if not value:
        return None
    if "://" not in value:
        value = "https://" + value
    domain = registered_domain(value)
    return domain.casefold() if domain else None


def wilson_lower(successes: int, total: int, z: float = 1.96) -> float | None:
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return round((centre - margin) / denominator, 4)


def _published_prediction(row: dict[str, Any]) -> dict[str, Any]:
    """Map a discovery profile to the externally published-site prediction."""
    verdict = classify_row(row)
    if verdict["outcome"] == "verified":
        domain = _normalise_domain(verdict.get("domain"))
        website = (row.get("evidence") or {}).get("website_discovered") or {}
        value = website.get("value") or {}
        return {
            "published": bool(domain),
            "domain": domain,
            "outcome": verdict["outcome"],
            "redirect_chain": [_normalise_domain(item) for item in value.get("redirect_chain") or [] if _normalise_domain(item)],
        }
    if verdict["outcome"] == "registry_site" and verdict.get("registry_site_verified"):
        evidence = row.get("evidence") or {}
        website = evidence.get("website") or {}
        value = website.get("value") or {}
        domain = next(
            (_normalise_domain(candidate) for candidate in (
                value.get("final_url"), value.get("requested_url"), website.get("source_url"), row.get("website"),
            ) if _normalise_domain(candidate)),
            None,
        )
        return {
            "published": bool(domain),
            "domain": domain,
            "outcome": verdict["outcome"],
            "redirect_chain": [_normalise_domain(item) for item in value.get("redirect_chain") or [] if _normalise_domain(item)],
        }
    return {"published": False, "domain": None, "outcome": verdict["outcome"], "redirect_chain": []}


def load_fetch_redirects(path: Path | None) -> dict[str, list[list[str]]]:
    """Read redirect chains from the optional persistent fetch cache."""
    if not path or not path.exists():
        return {}
    result: dict[str, list[list[str]]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            website = record.get("website") or {}
            value = website.get("value") or {}
            chain = [_normalise_domain(item) for item in value.get("redirect_chain") or [] if _normalise_domain(item)]
            if not chain:
                continue
            key = _normalise_domain(record.get("url") or record.get("key"))
            if key:
                result[key].append(chain)
    return dict(result)


def _domain_match(prediction: dict[str, Any], truth_domain: str | None, fetch_redirects: dict[str, list[list[str]]]) -> tuple[bool, str | None]:
    published_domain = prediction.get("domain")
    if not published_domain or not truth_domain:
        return False, None
    if published_domain == truth_domain:
        return True, "registered_domain"
    if truth_domain in (prediction.get("redirect_chain") or []):
        return True, "redirect"
    for chain in fetch_redirects.get(truth_domain, []):
        if published_domain in chain:
            return True, "redirect"
    return False, None


def _classification_kind(prediction: dict[str, Any], truth: str | None, truth_domain: str | None, fetch_redirects: dict[str, list[list[str]]]) -> str:
    if truth == "undetermined":
        return "undetermined"
    if prediction.get("outcome") in {"provider_failed", "timed_out"}:
        return "search_error" if prediction.get("outcome") == "provider_failed" else "timed_out"
    published = bool(prediction.get("published"))
    same = published and _domain_match(prediction, truth_domain, fetch_redirects)[0]
    if truth == "official_site":
        return "tp" if same else "wrong_url" if published else "fn"
    if truth == "related_only":
        return "tn" if not published else "related_as_official" if same else "wrong_url"
    if truth == "no_site_confirmed":
        return "wrong_url" if published else "tn"
    return "undetermined"


def _annotation_group(stratum: str | None) -> str:
    if stratum in {"S1", "S2", "S3"}:
        return "small_as"
    if stratum in {"S4", "S5", "S6"}:
        return "sized_as"
    return "non_as"


def _weighted_ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _annotation_weights(rows: list[dict[str, Any]]) -> dict[str, float]:
    counts = Counter(str(row.get("stratum") or "missing") for row in rows)
    return {
        stratum: float(next(row.get("population_n") or 0 for row in rows if str(row.get("stratum") or "missing") == stratum)) / count
        for stratum, count in counts.items()
    }


def score_annotations(
    pipeline_rows: list[dict[str, Any]],
    annotation_rows: list[dict[str, Any]],
    *,
    split: str = "development",
    min_published: int = 35,
    precision_threshold: float = 0.90,
    fetch_redirects: dict[str, list[list[str]]] | None = None,
) -> dict[str, Any]:
    """Score publication predictions against independent company annotations."""
    if split not in ANNOTATION_SPLITS | {"all"}:
        raise ValueError(f"invalid evaluation split: {split}")
    labels = [row for row in annotation_rows if split == "all" or row.get("split") == split]
    if not labels:
        raise ValueError(f"no annotation rows for split {split}")
    label_by_org = {str(row["organisation_number"]): row for row in labels}
    if len(label_by_org) != len(labels):
        raise ValueError("annotations contain duplicate organisation numbers")
    pipeline_by_org = {str(row.get("organisation_number")): row for row in pipeline_rows}
    if len(pipeline_by_org) != len(pipeline_rows):
        raise ValueError("pipeline output contains duplicate organisation numbers")
    missing = sorted(set(label_by_org) - set(pipeline_by_org))
    if missing:
        raise ValueError(f"pipeline output is missing {len(missing)} annotated companies: {missing[:5]}")
    fetch_redirects = fetch_redirects or {}
    predictions = {org: _published_prediction(pipeline_by_org[org]) for org in label_by_org}
    counts: Counter[str] = Counter()
    errors: list[dict[str, Any]] = []
    groups: dict[str, Counter[str]] = defaultdict(Counter)
    determined: list[dict[str, Any]] = []
    for org, label in label_by_org.items():
        truth = label.get("outcome")
        group = _annotation_group(label.get("stratum"))
        if truth == "undetermined":
            counts["undetermined"] += 1
            groups[group]["undetermined"] += 1
            continue
        if truth not in ANNOTATION_OUTCOMES:
            raise ValueError(f"invalid annotation outcome for {org}: {truth}")
        determined.append(label)
        prediction = predictions[org]
        if prediction["outcome"] in {"provider_failed", "timed_out"}:
            failure_kind = "timed_out" if prediction["outcome"] == "timed_out" else "search_error"
            counts[failure_kind] += 1
            groups[group][failure_kind] += 1
            errors.append({
                "organisation_number": org,
                "name": label.get("name"),
                "truth": truth,
                "published_domain": None,
                "truth_domain": _normalise_domain(label.get("domain")),
                "error": failure_kind,
                "stratum": label.get("stratum"),
                "split": label.get("split"),
            })
            continue
        published = prediction["published"]
        truth_domain = _normalise_domain(label.get("domain"))
        domain_matches, matched_via = _domain_match(prediction, truth_domain, fetch_redirects)
        if truth == "official_site":
            kind = "tp" if published and domain_matches else "wrong_url" if published else "fn"
        elif truth == "related_only":
            kind = "tn" if not published else "related_as_official" if domain_matches else "wrong_url"
        else:  # no_site_confirmed
            kind = "wrong_url" if published else "tn"
        counts[kind] += 1
        groups[group][kind] += 1
        if kind in {"wrong_url", "related_as_official", "fn"}:
            errors.append({
                "organisation_number": org,
                "name": label.get("name"),
                "truth": truth,
                "published_domain": prediction["domain"],
                "truth_domain": truth_domain,
                "error": kind,
                "stratum": label.get("stratum"),
                "split": label.get("split"),
                **({"matched_via": matched_via} if matched_via == "redirect" else {}),
            })

    weights = _annotation_weights(labels)
    weighted: Counter[str] = Counter()
    for label in determined:
        org = str(label["organisation_number"])
        weight = weights[str(label.get("stratum") or "missing")]
        truth = label["outcome"]
        prediction = predictions[org]
        if prediction["outcome"] in {"provider_failed", "timed_out"}:
            weighted["timed_out" if prediction["outcome"] == "timed_out" else "search_error"] += weight
            continue
        same = prediction["published"] and _domain_match(prediction, _normalise_domain(label.get("domain")), fetch_redirects)[0]
        if truth == "official_site":
            kind = "tp" if same else "wrong_url" if prediction["published"] else "fn"
        elif truth == "related_only":
            kind = "tn" if not prediction["published"] else "related_as_official" if same else "wrong_url"
        else:
            kind = "wrong_url" if prediction["published"] else "tn"
        weighted[kind] += weight

    published = counts["tp"] + counts["wrong_url"] + counts["related_as_official"]
    precision = _ratio(counts["tp"], published)
    lower = wilson_lower(counts["tp"], published)
    recall = _ratio(counts["tp"], counts["tp"] + counts["fn"])
    abstention = _ratio(counts["tn"], counts["tn"] + counts["fn"])
    weighted_published = weighted["tp"] + weighted["wrong_url"] + weighted["related_as_official"]
    weighted_determined = sum(weighted.values())
    by_group: dict[str, Any] = {}
    for group in ("small_as", "sized_as", "non_as"):
        total = sum(1 for row in labels if _annotation_group(row.get("stratum")) == group)
        published_group = groups[group]["tp"] + groups[group]["wrong_url"] + groups[group]["related_as_official"]
        by_group[group] = {
            "rows": total,
            "undetermined": groups[group]["undetermined"],
            "undetermined_rate": _ratio(groups[group]["undetermined"], total),
            "tp": groups[group]["tp"],
            "wrong_url": groups[group]["wrong_url"],
            "related_as_official": groups[group]["related_as_official"],
            "search_error": groups[group]["search_error"],
            "timed_out": groups[group]["timed_out"],
            "tn": groups[group]["tn"],
            "fn": groups[group]["fn"],
            "published_precision": _ratio(groups[group]["tp"], published_group),
        }
    undetermined_rate = _ratio(counts["undetermined"], len(labels))
    pipeline_failures = counts["search_error"] + counts["timed_out"]
    qc_changed_rows = sum(1 for label in labels if label.get("qc_status") == "changed")
    qc_headline_moved_rows = 0
    for label in labels:
        if label.get("qc_status") != "changed" or not label.get("outcome_v1"):
            continue
        org = str(label["organisation_number"])
        old_kind = _classification_kind(predictions[org], label.get("outcome_v1"), _normalise_domain(label.get("domain_v1")), fetch_redirects)
        new_kind = _classification_kind(predictions[org], label.get("outcome"), _normalise_domain(label.get("domain")), fetch_redirects)
        qc_headline_moved_rows += old_kind != new_kind
    if pipeline_failures:
        gate, reasons = "FAIL", [f"{pipeline_failures} pipeline failures were not scored as abstentions"]
    elif published < min_published:
        gate, reasons = "INSUFFICIENT_PUBLISHED", [f"fewer than {min_published} determined published companies ({published})"]
    elif precision is None or precision < 0.95 or lower is None or lower < precision_threshold:
        gate, reasons = "FAIL", [f"precision {precision or 0:.2f} or Wilson lower bound {lower or 0:.2f} is below target"]
    else:
        gate, reasons = "PASS", []
    return {
        "split": split,
        "rows": len(labels),
        "determined": len(determined),
        "undetermined": counts["undetermined"],
        "undetermined_rate": undetermined_rate,
        "pipeline_failures": pipeline_failures,
        "published_determined": published,
        "counts": dict(counts),
        "published_precision": precision,
        "published_precision_wilson_lower": lower,
        "has_site_recall": recall,
        "abstention_precision": abstention,
        "weighted": {
            "stratum_weights": weights,
            "determined_population_estimate": round(weighted_determined, 4),
            "published_precision": _weighted_ratio(weighted["tp"], weighted_published),
            "has_site_recall": _weighted_ratio(weighted["tp"], weighted["tp"] + weighted["fn"]),
            "wrong_company_per_1000": round((weighted["wrong_url"] + weighted["related_as_official"]) / weighted_determined * 1000, 4) if weighted_determined else None,
        },
        "by_group": by_group,
        "errors": sorted(errors, key=lambda row: (row["error"], row["organisation_number"])),
        "redirect_matches": sum(
            1 for org, prediction in predictions.items()
            if _domain_match(prediction, _normalise_domain(label_by_org[org].get("domain")), fetch_redirects)[1] == "redirect"
        ),
        "qc_changed_rows": qc_changed_rows,
        "qc_headline_moved_rows": qc_headline_moved_rows,
        "gate": {"status": gate, "reasons": reasons, "min_published": min_published, "precision_threshold": precision_threshold},
        "verdicts": {
            org: {
                **predictions[org],
                "truth": label_by_org[org].get("outcome"),
                "matched_via": _domain_match(predictions[org], _normalise_domain(label_by_org[org].get("domain")), fetch_redirects)[1],
            }
            for org in label_by_org
        },
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def classify_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return {outcome, domain, signals} for one profile row."""
    evidence = row.get("evidence") or {}
    if row.get("website"):
        site = (evidence.get("website") or {}).get("value") or {}
        publishable = (site.get("identity_assessment") or {}).get("publishable") is True
        return {"outcome": "registry_site", "domain": None, "registry_site_verified": publishable, "signals": {}}
    discovery = evidence.get("website_discovery")
    if not discovery:
        return {"outcome": "not_queried", "domain": None, "signals": {}}
    if (discovery.get("value") or {}).get("skipped_negative_cache"):
        return {"outcome": "negative_cache", "domain": None, "signals": {}}
    if (discovery.get("value") or {}).get("search_skipped") == "triage":
        return {"outcome": "search_skipped_triage", "domain": None, "signals": {}}
    if discovery.get("status") == "timed_out" or (discovery.get("value") or {}).get("timed_out"):
        return {"outcome": "timed_out", "domain": None, "signals": {}}
    verified = evidence.get("website_discovered") or {}
    if verified:
        value = verified.get("value") or {}
        first_party = value.get("first_party_assessment") or {}
        identity = value.get("identity_assessment") or {}
        return {
            "outcome": "verified",
            "domain": first_party.get("candidate_domain"),
            "source": "registry_derived" if verified.get("source_type") == "registry_derived_company_website" else "search",
            "signals": {**(first_party.get("signals") or {}), "identity_score": identity.get("score")},
        }
    candidates = evidence.get("website_discovered_candidates") or (discovery.get("value") or {}).get("candidates") or []
    if not candidates:
        return {"outcome": "provider_failed" if discovery.get("status") == "failed" else "no_candidate", "domain": None, "signals": {}}
    identity_ok = [c for c in candidates if c.get("identity_publishable")]
    insufficient = [c for c in identity_ok if c.get("first_party_status") == "insufficient_evidence"]
    if insufficient:
        return {"outcome": "identity_ok_first_party_insufficient", "domain": insufficient[0].get("registered_domain"), "signals": {}}
    if identity_ok:
        return {"outcome": "identity_ok_directory_or_registry", "domain": identity_ok[0].get("registered_domain"), "signals": {}}
    related = [c for c in candidates if (c.get("related") or {}).get("status") == "related"]
    if related:
        return {"outcome": "related_entity", "domain": related[0].get("registered_domain"), "signals": {}}
    return {"outcome": "crawled_identity_failed", "domain": None, "signals": {}}


def trust_assessment(verdicts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    verified = {org: v for org, v in verdicts.items() if v["outcome"] == "verified"}
    domain_owners: dict[str, list[str]] = defaultdict(list)
    for org, verdict in verified.items():
        if verdict.get("domain"):
            domain_owners[verdict["domain"]].append(org)
    shared = {domain: orgs for domain, orgs in domain_owners.items() if len(orgs) > 1}
    strong = 0
    org_backed = 0
    weak: list[str] = []
    for org, verdict in verified.items():
        signals = verdict.get("signals") or {}
        identity_score = signals.get("identity_score") or 0
        org_backed += identity_score >= 1.0
        org_match = identity_score >= 0.95  # exact organisation number (1.0) or full legal-name match (0.95)
        contact = bool(signals.get("registry_email_domain_match") or signals.get("phone_match") or signals.get("legal_org_number_match"))
        corroborated = contact or bool(signals.get("address_match")) or bool(signals.get("registry_website_match"))
        unique = verdict.get("domain") not in shared
        if org_match and corroborated and unique:
            strong += 1
        else:
            weak.append(org)
    return {
        "verified": len(verified),
        "strong": strong,
        "trust_proxy": _ratio(strong, len(verified)),
        "org_number_backed": _ratio(org_backed, len(verified)),
        "weak_orgs": sorted(weak),
        "shared_domains": {domain: sorted(orgs) for domain, orgs in shared.items()},
    }


def suspected_directories(rows: list[dict[str, Any]], *, minimum_orgs: int = 2) -> list[dict[str, Any]]:
    """Domains that pass identity for several different companies are almost never company sites."""
    owners: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        for candidate in (row.get("evidence") or {}).get("website_discovered_candidates") or []:
            if candidate.get("identity_publishable") and candidate.get("registered_domain"):
                owners[candidate["registered_domain"]].add(str(row.get("organisation_number")))
    ranked = sorted(((domain, len(orgs)) for domain, orgs in owners.items() if len(orgs) >= minimum_orgs), key=lambda item: -item[1])
    return [{"domain": domain, "companies": count} for domain, count in ranked]


def label_metrics(verdicts: dict[str, dict[str, Any]], labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    published = {org for org, v in verdicts.items() if v["outcome"] == "verified"}
    labeled_published = [org for org in published if org in labels and "exact" in labels[org]]
    correct = sum(1 for org in labeled_published if labels[org]["exact"])
    has_site = [org for org, label in labels.items() if label.get("has_site")]
    found = sum(1 for org in has_site if org in published and labels[org].get("exact", True))
    return {
        "labeled_total": len(labels),
        "labeled_published": len(labeled_published),
        "wrong_company": len(labeled_published) - correct,
        "precision": _ratio(correct, len(labeled_published)),
        "precision_wilson_lower": wilson_lower(correct, len(labeled_published)),
        "labeled_has_site": len(has_site),
        "recall_on_labeled": _ratio(found, len(has_site)),
    }


def diff_verdicts(current: dict[str, dict[str, Any]], previous: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    gained, lost, changed = [], [], []
    for org, verdict in current.items():
        before = previous.get(org)
        if before is None:
            continue
        was, now = before.get("outcome") == "verified", verdict["outcome"] == "verified"
        if now and not was:
            gained.append(org)
        elif was and not now:
            lost.append(org)
        elif now and was and before.get("domain") != verdict.get("domain"):
            changed.append(org)
    return {"gained": sorted(gained), "lost": sorted(lost), "changed_domain": sorted(changed)}


def build_scorecard(
    rows: list[dict[str, Any]],
    report: dict[str, Any],
    *,
    labels: dict[str, dict[str, Any]] | None = None,
    annotations: list[dict[str, Any]] | None = None,
    evaluation_split: str = "development",
    min_published: int = 35,
    previous: dict[str, Any] | None = None,
    precision_threshold: float = 0.90,
    trust_drop_threshold: float = 0.05,
    min_labels: int = 20,
    fetch_redirects: dict[str, list[list[str]]] | None = None,
) -> dict[str, Any]:
    if report.get("provider_fatal"):
        raise ValueError(f"evaluation refuses provider-fatal run: {report['provider_fatal']}")
    if annotations is not None:
        cached = [
            str(row.get("organisation_number"))
            for row in rows
            if classify_row(row)["outcome"] == "negative_cache"
        ]
        if cached:
            raise ValueError(f"evaluation refuses negative-cache rows: {cached[:5]}")
    verdicts = {str(row.get("organisation_number")): classify_row(row) for row in rows}
    outcome_counts = Counter(v["outcome"] for v in verdicts.values())
    queried = sum(outcome_counts[o] for o in OUTCOMES if o not in NOT_QUERIED)
    reached = queried - outcome_counts["provider_failed"]
    verified = outcome_counts["verified"]
    verified_by_source = Counter(v.get("source", "unknown") for v in verdicts.values() if v["outcome"] == "verified")
    registry_verified = sum(1 for v in verdicts.values() if v.get("registry_site_verified"))
    registry_site_fetch_failures = sum(
        1
        for row in rows
        if row.get("website")
        and ((row.get("evidence") or {}).get("website") or {}).get("status") == "failed"
    )
    counts = report.get("counts") or {}
    provider_requests = counts.get("provider_requests", 0)
    requests_total = provider_requests + counts.get("crawl_requests", 0)
    trust = trust_assessment(verdicts)
    funnel_losses = {stage: outcome_counts[stage] for stage in LOSS_STAGES}
    biggest_loss = max(funnel_losses, key=funnel_losses.get) if any(funnel_losses.values()) else None
    label_block = label_metrics(verdicts, labels) if labels else None
    annotation_block = score_annotations(
        rows, annotations, split=evaluation_split, min_published=min_published,
        precision_threshold=precision_threshold, fetch_redirects=fetch_redirects,
    ) if annotations is not None else None

    gate, gate_reasons = "PASS", []
    lower = label_block["precision_wilson_lower"] if label_block else None
    if annotation_block is not None:
        gate = annotation_block["gate"]["status"]
        gate_reasons.extend(annotation_block["gate"]["reasons"])
    elif label_block and label_block["labeled_published"] >= min_labels:
        if lower is not None and lower < precision_threshold:
            gate = "FAIL"
            gate_reasons.append(f"labeled precision lower bound {lower:.2f} < {precision_threshold:.2f}")
    else:
        gate = "INSUFFICIENT_LABELS"
        gate_reasons.append(f"fewer than {min_labels} labeled published sites; precision unmeasured")
    previous_trust = ((previous or {}).get("trust") or {}).get("trust_proxy")
    if trust["trust_proxy"] is not None and previous_trust is not None and previous_trust - trust["trust_proxy"] > trust_drop_threshold:
        gate = "FAIL"
        gate_reasons.append(f"trust proxy dropped {previous_trust:.2f} -> {trust['trust_proxy']:.2f}")

    card: dict[str, Any] = {
        "companies": len(rows),
        "queried": queried,
        "outcomes": dict(outcome_counts),
        "coverage": {
            "discovered_verified": verified,
            "registry_site_verified": registry_verified,
            "overall_coverage": _ratio(verified + registry_verified, len(rows)),
            "yield_on_queried": _ratio(verified, queried),
            "yield_excluding_provider_failures": _ratio(verified, reached),
            "verified_by_source": dict(verified_by_source),
        },
        "trust": trust,
        "funnel": {"losses": funnel_losses, "biggest_loss_stage": biggest_loss},
        "suspected_directories": suspected_directories(rows),
        "reliability": {
            "provider_requests": provider_requests,
            "provider_errors": counts.get("provider_errors", 0),
            "provider_error_rate": _ratio(counts.get("provider_errors", 0), provider_requests),
            "registry_site_fetch_failures": registry_site_fetch_failures,
            "registry_site_fetch_failure_rate": _ratio(registry_site_fetch_failures, sum(bool(row.get("website")) for row in rows)),
        },
        "cost": {
            "requests_total": requests_total,
            "requests_per_verified": round(requests_total / verified, 1) if verified else None,
            "crawl_latency_p95_ms": (report.get("crawl_latency_ms") or {}).get("p95"),
        },
        "labels": label_block,
        "annotations": annotation_block,
        "gate": {"status": gate, "reasons": gate_reasons},
        "verdicts": verdicts,
    }
    review = set(trust["weak_orgs"])
    if previous:
        card["diff_vs_previous"] = diff_verdicts(verdicts, previous.get("verdicts") or {})
        for orgs in card["diff_vs_previous"].values():
            review.update(orgs)
    card["review_queue"] = sorted(review)
    return card


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def summary_lines(card: dict[str, Any]) -> list[str]:
    cov, trust, rel, cost, labels = card["coverage"], card["trust"], card["reliability"], card["cost"], card["labels"]
    annotations = card.get("annotations")
    if annotations:
        weighted = annotations["weighted"]
        lines = [
            f"PRECISION {_pct(annotations['published_precision'])} (LB {_pct(annotations['published_precision_wilson_lower'])}, n={annotations['published_determined']}) | "
            f"RECALL {_pct(annotations['has_site_recall'])} | ABSTAIN {_pct(annotations['abstention_precision'])} | "
            f"WRONG/1000 {weighted['wrong_company_per_1000'] if weighted['wrong_company_per_1000'] is not None else 'n/a'} | "
            f"UNDETERMINED {_pct(annotations['undetermined_rate'])} | GATE {annotations['gate']['status']}"
        ]
        if rel.get("registry_site_fetch_failure_rate") is not None and rel["registry_site_fetch_failure_rate"] > 0.05:
            lines.append(
                f"REGISTRY SITE FETCH FAILURES {rel['registry_site_fetch_failures']} "
                f"({_pct(rel['registry_site_fetch_failure_rate'])}); do not interpret these rows as no-site outcomes"
            )
        if card.get("diff_vs_previous"):
            diff = card["diff_vs_previous"]
            lines.append(f"VS PREVIOUS +{len(diff['gained'])} gained, -{len(diff['lost'])} lost, ~{len(diff['changed_domain'])} changed domain")
        return lines
    precision = "n/a (no labels)" if not labels or labels["precision"] is None else (
        f"{_pct(labels['precision'])} (lower {_pct(labels['precision_wilson_lower'])}, n={labels['labeled_published']})"
    )
    lines = [
        f"COVERAGE {_pct(cov['overall_coverage'])} overall | YIELD {_pct(cov['yield_on_queried'])} "
        f"({cov['discovered_verified']}/{card['queried']} queried) | TRUST {_pct(trust['trust_proxy'])} (proxy; {_pct(trust['org_number_backed'])} org-number-backed) | PRECISION {precision}",
        f"COST {cost['requests_per_verified'] or 'n/a'} req/verified | PROVIDER ERRORS {_pct(rel['provider_error_rate'])} "
        f"({rel['provider_errors']}/{rel['provider_requests']})",
        f"BIGGEST LOSS {card['funnel']['biggest_loss_stage']} {card['funnel']['losses']}",
    ]
    if rel.get("registry_site_fetch_failures", 0) > 0:
        lines.append(
            f"REGISTRY SITE FETCH FAILURES {rel['registry_site_fetch_failures']} "
            f"({_pct(rel.get('registry_site_fetch_failure_rate'))}); do not interpret these rows as no-site outcomes"
        )
    if cov["verified_by_source"]:
        lines.append("VERIFIED BY SOURCE " + ", ".join(f"{name} {count}" for name, count in sorted(cov["verified_by_source"].items())))
    if card["outcomes"].get("negative_cache"):
        lines.append(f"NEGATIVE CACHE {card['outcomes']['negative_cache']} companies skipped (searched recently, nothing found); yield above covers only the {card['queried']} re-queried")
    if card["outcomes"].get("provider_failed"):
        lines.append(f"PROVIDER OUTAGE {card['outcomes']['provider_failed']} companies not searched; yield excluding them {_pct(cov['yield_excluding_provider_failures'])}")
    if card["suspected_directories"]:
        lines.append("SUSPECTED DIRECTORIES " + ", ".join(f"{d['domain']}({d['companies']})" for d in card["suspected_directories"][:6]))
    diff = card.get("diff_vs_previous")
    if diff:
        lines.append(f"VS PREVIOUS +{len(diff['gained'])} gained, -{len(diff['lost'])} lost, ~{len(diff['changed_domain'])} changed domain")
    lines.append(f"GATE {card['gate']['status']}" + (" - " + "; ".join(card["gate"]["reasons"]) if card["gate"]["reasons"] else ""))
    return lines


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _append_evaluation_look(
    path: Path,
    *,
    split: str,
    manifest: Path,
    profiles: Path,
    card: dict[str, Any],
    forced: bool,
) -> int:
    previous: list[dict[str, Any]] = []
    if path.exists():
        previous = read_jsonl(path)
    if split in {"validation", "all"} and not forced and any(item.get("split") in {"validation", "all"} for item in previous):
        raise ValueError("validation has already been looked at; use --force-validation to record another look")
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "split": split,
        "manifest_path": str(manifest),
        "manifest_sha256": _file_sha256(manifest),
        "pipeline_output_path": str(profiles),
        "pipeline_output_sha256": _file_sha256(profiles),
        "git_commit": _git_commit(),
        "forced_validation": bool(forced and split in {"validation", "all"}),
        "look_count_for_split": sum(1 for item in previous if item.get("split") == split) + 1,
        "headline": {
            "published_precision": (card.get("annotations") or {}).get("published_precision"),
            "wilson_lower": (card.get("annotations") or {}).get("published_precision_wilson_lower"),
            "recall": (card.get("annotations") or {}).get("has_site_recall"),
            "gate": card.get("gate", {}).get("status"),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
    return entry["look_count_for_split"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a discovery run.")
    parser.add_argument("--profiles", required=True, help="Discovery output JSONL")
    parser.add_argument("--report", required=True, help="Discovery run report JSON")
    parser.add_argument("--output", required=True, help="Scorecard JSON to write")
    parser.add_argument("--labels", help="JSONL: organisation_number, exact (bool), has_site (bool)")
    parser.add_argument("--annotations", help="Evaluation annotations JSONL with official_site/no_site/related_only/undetermined outcomes")
    parser.add_argument("--split", choices=("development", "held_out", "validation", "all"), default="development")
    parser.add_argument("--min-published", type=int, default=35)
    parser.add_argument("--previous", help="Previous scorecard JSON for run-to-run diff")
    parser.add_argument("--precision-threshold", type=float, default=0.90)
    parser.add_argument("--trust-drop-threshold", type=float, default=0.05)
    parser.add_argument("--min-labels", type=int, default=20)
    parser.add_argument("--manifest", default="out/eval-sample/manifest.jsonl")
    parser.add_argument("--looks-path", default="out/eval-sample/looks.jsonl")
    parser.add_argument("--final", action="store_true", help="Allow validation/all evaluation and record the look")
    parser.add_argument("--force-validation", action="store_true", help="Override the one-look validation guard and log the override")
    parser.add_argument("--fetch-cache", help="Optional website-evidence cache used for redirect alias matching")
    args = parser.parse_args()

    rows = read_jsonl(Path(args.profiles))
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    if args.labels and args.annotations:
        parser.error("--labels and --annotations are mutually exclusive")
    annotations = read_jsonl(Path(args.annotations)) if args.annotations else None
    if annotations and args.split in {"validation", "all"} and not args.final:
        parser.error("--split validation/all requires --final")
    if annotations and report.get("complete") is False:
        parser.error("evaluation refuses incomplete pipeline output; resume the discovery run first")
    labels = {str(item["organisation_number"]): item for item in read_jsonl(Path(args.labels))} if args.labels else None
    previous = json.loads(Path(args.previous).read_text(encoding="utf-8")) if args.previous else None
    try:
        card = build_scorecard(
            rows, report, labels=labels, annotations=annotations, evaluation_split=args.split, min_published=args.min_published, previous=previous,
            precision_threshold=args.precision_threshold, trust_drop_threshold=args.trust_drop_threshold, min_labels=args.min_labels,
            fetch_redirects=load_fetch_redirects(Path(args.fetch_cache) if args.fetch_cache else None),
        )
    except ValueError as exc:
        parser.error(str(exc))
    if annotations and args.split in {"held_out", "validation", "all"}:
        try:
            count = _append_evaluation_look(
                Path(args.looks_path), split=args.split, manifest=Path(args.manifest), profiles=Path(args.profiles), card=card,
                forced=args.force_validation,
            )
            card.setdefault("evaluation_look", {})["look_count"] = count
        except ValueError as exc:
            parser.error(str(exc))
    card["profiles_path"] = str(Path(args.profiles))
    card["report_path"] = str(Path(args.report))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n".join(summary_lines(card)))


if __name__ == "__main__":
    main()
