#!/usr/bin/env python3
"""Build strict external observations from verified first-party profiles."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external.external_footprint import (  # noqa: E402
    connector_policy_entry,
    content_hash,
    observation_id,
)
from norway_company_agent.core.identity import assess_social_identity  # noqa: E402
from norway_company_agent.web.website import normalize_social_url, structured_social_links  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _verified_website(profile: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    record = profile.get("evidence", {}).get("website") or {}
    value = record.get("value") or {}
    identity = value.get("identity_assessment") or {}
    first_party = value.get("first_party_assessment") or record.get("first_party_assessment") or {}
    if record.get("status") != "available" or not identity.get("publishable"):
        return None
    # Older discovery reports did not persist the first-party block. If one is
    # present, an explicit failed first-party gate wins.
    if first_party and not first_party.get("publishable"):
        return None
    return record, value


def _policy_fields(connector: str, platform: str, acquisition_mode: str, policy_path: str | Path) -> dict[str, Any]:
    entry = connector_policy_entry(connector, platform=platform, acquisition_mode=acquisition_mode, path=policy_path)
    return {"connector_id": connector, "rights_status": entry.get("rights_status", "review_required"), "policy_entry": entry}


def _base_observation(
    *,
    profile: dict[str, Any],
    platform: str,
    signal_type: str,
    source_url: str,
    retrieved_at: str,
    raw_value: Any,
    identity_proof: list[dict[str, Any]],
    acquisition_mode: str,
    source_class: str,
    strategy: str,
    connector: str,
    policy_path: str | Path,
    **extra: Any,
) -> dict[str, Any]:
    org = str(profile["organisation_number"])
    policy = _policy_fields(connector, platform, acquisition_mode, policy_path)
    return {
        "id": observation_id(connector, org, source_url, signal_type),
        "organisation_number": org,
        "platform": platform,
        "signal_type": signal_type,
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "content_sha256": content_hash(raw_value),
        "exact_entity": True,
        "identity_proof": identity_proof,
        "acquisition_mode": acquisition_mode,
        "rights_status": policy["rights_status"],
        "connector_id": connector,
        "source_class": source_class,
        "strategy": strategy,
        **extra,
    }


def _legacy_seed_build(config: list[dict[str, Any]], profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compatibility for the original reviewed-news seed fixture."""
    names = {str(row["organisation_number"]): str(row.get("name") or "") for row in profiles}
    output = []
    for seed in config:
        seed = dict(seed)
        org = str(seed["organisation_number"])
        if org not in names:
            raise ValueError(f"unknown organisation number: {org}")
        digest = str(seed.get("content_sha256") or "")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"invalid content hash for {org}")
        proof = str(seed.pop("proof"))
        mode = seed.get("acquisition_mode") or "rights_review_experiment"
        output.append({
            **seed,
            "id": "verified-observation-" + hashlib.sha256(f"{org}|{seed['source_url']}|{seed['signal_type']}".encode()).hexdigest()[:24],
            "retrieved_at": "2026-08-23T05:28:01Z",
            "exact_entity": True,
            "identity_proof": [{"type": "manual_exact_entity_review", "legal_name": names[org], "basis": proof}],
            "acquisition_mode": mode,
            "rights_status": seed.get("rights_status") or "review_required",
            "source_class": seed.get("source_class") or "public_news",
            "strategy": seed.get("strategy") or "independent_news_discovery",
        })
    return output


def guard_social_links(profile: dict[str, Any], links: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Keep only unambiguous first-party social profiles.

    A single declared profile is allowed. When a site declares several profiles
    for one platform, a profile must have strong legal-name similarity; otherwise
    every candidate is quarantined as ambiguous. This avoids treating a group or
    programme link as the legal entity's handle merely because it was found on a
    first-party page.
    """
    by_platform: dict[str, list[dict[str, str]]] = {}
    for link in links:
        by_platform.setdefault(str(link.get("platform")), []).append(link)
    survivors: list[dict[str, str]] = []
    suppressed: list[dict[str, Any]] = []
    for platform, candidates in sorted(by_platform.items()):
        assessments = [(link, assess_social_identity(profile, link)) for link in candidates]
        strong = [item for item in assessments if item[1].get("identity_score", 0) >= 0.9 and item[1].get("matched_tokens")]
        if len(candidates) == 1:
            survivors.append(candidates[0])
            continue
        if len(strong) == 1:
            survivors.append(strong[0][0])
            for link, _assessment in assessments:
                if link != strong[0][0]:
                    suppressed.append({"platform": platform, "url": link.get("url"), "reason": "ambiguous_handle", "selected": strong[0][0].get("url")})
            continue
        for link, _assessment in assessments:
            suppressed.append({"platform": platform, "url": link.get("url"), "reason": "ambiguous_handle", "candidates": len(candidates)})
    return sorted(survivors, key=lambda item: (item.get("platform", ""), item.get("url", ""))), suppressed


def build_with_report(
    profiles: list[dict[str, Any]],
    *,
    policy_path: str | Path = "config/connector-policy.json",
    retrieved_at: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    captured_at = retrieved_at or _now()
    result: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for profile in profiles:
        verified = _verified_website(profile)
        if not verified:
            continue
        record, value = verified
        source_url = str(value.get("final_url") or record.get("source_url") or "")
        if not source_url:
            continue
        assessment = value.get("identity_assessment") or {}
        first_party = value.get("first_party_assessment") or record.get("first_party_assessment") or {}
        proof = [
            {"type": "website_identity_assessment", "assessment": assessment},
            {"type": "verified_first_party_page", "assessment": first_party or {"publishable": True}},
        ]
        result.append(_base_observation(
            profile=profile, platform="company_site", signal_type="company_profile", source_url=source_url,
            retrieved_at=str(record.get("retrieved_at") or captured_at), raw_value=value,
            identity_proof=proof, acquisition_mode="permitted_public_page", source_class="verified_company_site",
            strategy="company_site_identity", connector="company_site", policy_path=policy_path,
            evidence_span=str(value.get("identity_text_excerpt") or value.get("main_text_excerpt") or value.get("title") or profile.get("name") or "")[:500],
        ))
        links = list(value.get("discovered_social_links") or value.get("social_links") or [])
        links.extend(structured_social_links(value.get("structured_organisations") or []))
        normalized: dict[tuple[str, str], dict[str, str]] = {}
        for link in links:
            item = normalize_social_url(str(link.get("url") or "")) if isinstance(link, dict) else None
            if item:
                normalized[(item["platform"], item["url"])] = item
        owned, blocked = guard_social_links(profile, list(normalized.values()))
        for item in blocked:
            suppressed.append({"organisation_number": profile.get("organisation_number"), **item})
        for link in owned:
            handle_proof = [
                {"type": "linked_from_verified_first_party_page", "source_url": source_url},
                {"type": "website_identity_assessment", "assessment": assessment},
                {"type": "handle_ownership_guard", "status": "unambiguous"},
            ]
            result.append(_base_observation(
                profile=profile, platform=link["platform"], signal_type="profile_handle", source_url=link["url"],
                retrieved_at=str(record.get("retrieved_at") or captured_at), raw_value={"website": source_url, "link": link},
                identity_proof=handle_proof, acquisition_mode="permitted_public_page", source_class="company_declared_social_link",
                strategy="verified_handle_extraction", connector="company_site", policy_path=policy_path,
                evidence_span=f"Declared {link['platform']} profile linked from {source_url}", website_source_url=source_url,
                handle_text=link["url"].rstrip("/").rsplit("/", 1)[-1],
                handle_match_signal=next((item for item in value.get("social_link_assessments", []) if item.get("url") == link["url"]), None),
            ))
    return result, {
        "handle_candidates": sum(1 for row in result if row.get("signal_type") == "profile_handle") + len(suppressed),
        "handle_survivors": sum(1 for row in result if row.get("signal_type") == "profile_handle"),
        "suppressed": suppressed,
    }


def build(
    profiles_or_config: list[dict[str, Any]],
    profiles: list[dict[str, Any]] | None = None,
    *,
    policy_path: str | Path = "config/connector-policy.json",
    retrieved_at: str | None = None,
) -> list[dict[str, Any]]:
    """Build company-site and social-link observations.

    ``build(seed_rows, profiles)`` remains supported for the original fixture;
    production calls use ``build(profiles, policy_path=...)``.
    """
    if profiles is not None and profiles_or_config and "proof" in profiles_or_config[0]:
        return _legacy_seed_build(profiles_or_config, profiles)
    return build_with_report(profiles_or_config, policy_path=policy_path, retrieved_at=retrieved_at)[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build strict observations from verified first-party company sites.")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--config", default="config/connector-policy.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--retrieved-at")
    args = parser.parse_args()
    profiles = [json.loads(line) for line in Path(args.profiles).read_text(encoding="utf-8").splitlines() if line.strip()]
    rows, guard_report = build_with_report(profiles, policy_path=args.config, retrieved_at=args.retrieved_at)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"observations": len(rows), "companies": len({row["organisation_number"] for row in rows}), "handle_guard": guard_report}, indent=2))


if __name__ == "__main__":
    main()
