from __future__ import annotations

import re
import socket
import time
from collections import Counter
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from ..cache.universe import CACHE_SCHEMA_VERSION
from ..core.identity import apply_website_identity_gate
from ..registry.sampling import stratum
from ..web.candidates import name_domain_variants
from ..web.first_party import assess_first_party_ownership, assess_g4_ownership, detect_related_only_site
from ..web.website import HostRequestPolicy, fetch_website, normalize_homepage, registered_domain

FREE_EMAIL_DOMAINS = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "proton.me", "online.no"}
HOST_EMAIL_DOMAINS = {"brreg.no", "nav.no", "altinn.no", "regnskap-norge.no"}


def _email_domain(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return text.rsplit("@", 1)[-1] if "@" in text else ""


def candidate_domains(
    record: dict[str, Any],
    *,
    nav_index: dict[str, dict[str, Any]] | None = None,
    subunit_domains: dict[str, list[str]] | None = None,
) -> list[dict[str, str]]:
    """Return registry/NAV/name candidates in strongest-first order."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(value: Any, source: str, related_only: bool = False) -> None:
        normalized = normalize_homepage(str(value or ""))
        if not normalized:
            return
        domain = registered_domain(normalized)
        if not domain or domain in seen:
            return
        seen.add(domain)
        found.append({"url": normalized, "domain": domain, "source": source, "related_only": str(related_only).lower()})

    raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
    add(record.get("website") or raw.get("hjemmeside") or raw.get("Hjemmeside"), "registry_website")
    domain = _email_domain(record.get("email") or raw.get("epostadresse") or raw.get("Epostadresse"))
    if domain and domain not in FREE_EMAIL_DOMAINS and domain not in HOST_EMAIL_DOMAINS:
        add("https://" + domain, "registry_email_domain")
    for item in (subunit_domains or {}).get(str(record.get("organisation_number")), []):
        add(item, "subunit_website_or_email_domain")
    nav = (nav_index or {}).get(str(record.get("organisation_number")), {})
    for homepage in nav.get("homepages") or []:
        add(homepage, "nav_employer_homepage")
    for email_domain in nav.get("contact_email_domains") or []:
        add("https://" + str(email_domain), "nav_contact_email_domain")
    for variant in name_domain_variants(record.get("name"), include_com=True):
        if variant.endswith(".com"):
            add("https://" + variant, "name_derived_distinctive_com")
        else:
            add("https://" + variant + ".no", "name_derived_no")
    return found


def _contact_fields(profile: dict[str, Any], website: dict[str, Any]) -> dict[str, Any]:
    value = website.get("value") or {}
    text = " ".join(str(value.get(key) or "") for key in ("title", "description", "main_text_excerpt", "identity_text_excerpt"))
    raw = profile.get("raw") or {}
    registry_email = str(profile.get("email") or raw.get("epostadresse") or raw.get("Epostadresse") or "").casefold()
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text, flags=re.I)))
    phones = re.findall(r"(?<!\d)(?:\+47[ .]?)?\d{2}(?:[ .]?\d{2}){3}(?!\d)", text)
    registry_digits = re.sub(r"\D", "", str(profile.get("phone") or raw.get("telefon") or raw.get("Telefon") or ""))
    email_match = registry_email and any(item.casefold() == registry_email for item in emails)
    phone_match = registry_digits and any(re.sub(r"\D", "", item).endswith(registry_digits[-8:]) for item in phones)
    return {"email": registry_email if email_match else None, "phone": next((item for item in phones if phone_match), None), "address": None, "matched_registry": bool(email_match or phone_match)}


def _resolve_domain(domain: str) -> bool:
    try:
        return bool(socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM))
    except OSError:
        return False


def usable_candidate_url(candidate: dict[str, Any], resolved: dict[str, bool] | None) -> str | None:
    """Pick the host form that actually resolves (as listed, apex, then www); None if none does."""
    url = str(candidate.get("url") or "")
    if resolved is None:
        return url
    domain = str(candidate.get("domain") or "")
    host = (urlparse(url).hostname or "").casefold()
    if host and resolved.get(host, False):
        return url
    if domain and resolved.get(domain, False):
        return "https://" + domain + "/"
    if domain and resolved.get("www." + domain, False):
        return "https://www." + domain + "/"
    return None


def pre_resolve_domains(
    records: Iterable[dict[str, Any]],
    *,
    nav_index: dict[str, dict[str, Any]] | None = None,
    subunit_domains: dict[str, list[str]] | None = None,
    workers: int = 256,
) -> dict[str, bool]:
    domains: set[str] = set()
    for record in records:
        for candidate in candidate_domains(record, nav_index=nav_index, subunit_domains=subunit_domains):
            domain = candidate.get("domain")
            if not domain:
                continue
            domains.add(domain)
            domains.add("www." + domain)
            host = (urlparse(candidate.get("url") or "").hostname or "").casefold()
            if host:
                domains.add(host)
    result: dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_resolve_domain, domain): domain for domain in sorted(domains)}
        for future in as_completed(futures):
            domain = futures[future]
            try:
                result[domain] = bool(future.result())
            except OSError:
                result[domain] = False
    return result


def _final_state(candidate_results: list[dict[str, Any]], *, published: bool) -> str:
    if published:
        return "available"
    states = [str(item.get("state") or "") for item in candidate_results]
    if not states or all(state == "not_available" for state in states):
        return "not_available"
    if any(state == "ambiguous" for state in states):
        return "ambiguous"
    if any(state == "blocked" for state in states) and all(state in {"blocked", "not_available"} for state in states):
        return "blocked"
    # A guessed (name-derived) domain that resolves but will not load says nothing about the
    # company; only a registry- or NAV-linked candidate that failed makes the whole module `failed`.
    promising_failed = any(
        str(item.get("state") or "") == "failed" and not str(item.get("source") or "").startswith("name_derived")
        for item in candidate_results
    )
    if promising_failed:
        return "failed"
    if any(state == "failed" for state in states):
        return "not_available"
    return "ambiguous"


NAME_DERIVED_TIMEOUT = 6.0
NAME_DERIVED_TOTAL = 9.0
REGISTRY_TIER_TOTAL = 18.0


def _call_fetcher(fetcher: Callable[..., tuple[dict[str, Any], dict[str, Any]]], url: str, *, timeout: float, request_policy: HostRequestPolicy | None) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        return fetcher(url, timeout=timeout, request_policy=request_policy, max_secondary_pages=1)
    except TypeError:
        return fetcher(url, timeout=timeout, request_policy=request_policy)


def fetch_with_http_fallback(fetcher: Callable[..., tuple[dict[str, Any], dict[str, Any]]], url: str, *, timeout: float, request_policy: HostRequestPolicy | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch over https and, when the host resolves but https fails, retry plain http once.

    Many small Norwegian company sites still answer only on http (the starter pipeline
    normalised registry-listed sites that way); without this fallback they were lost.
    """
    evidence, metrics = _call_fetcher(fetcher, url, timeout=timeout, request_policy=request_policy)
    if url.startswith("https://") and evidence.get("status") in {"failed", "source_error"} and metrics.get("failure_kind") != "resolution":
        fallback_evidence, fallback_metrics = _call_fetcher(fetcher, "http://" + url[len("https://"):], timeout=timeout, request_policy=request_policy)
        merged = {key: (int(metrics.get(key) or 0) + int(fallback_metrics.get(key) or 0)) if key in {"requests", "bytes"} else fallback_metrics.get(key, metrics.get(key)) for key in set(metrics) | set(fallback_metrics)}
        if fallback_evidence.get("status") not in {"failed", "source_error"}:
            return fallback_evidence, merged
        return evidence, merged
    return evidence, metrics


def process_record(
    record: dict[str, Any],
    *,
    nav_index: dict[str, dict[str, Any]] | None = None,
    subunit_domains: dict[str, list[str]] | None = None,
    request_policy: HostRequestPolicy | None = None,
    timeout: float = 12.0,
    fetcher: Callable[..., tuple[dict[str, Any], dict[str, Any]]] = fetch_website,
    gate: str = "g3",
    resolved_domains: dict[str, bool] | None = None,
    max_seconds: float | None = None,
) -> dict[str, Any]:
    if gate not in {"g3", "g4"}:
        raise ValueError("gate must be g3 or g4")
    started = time.monotonic()
    candidates = candidate_domains(record, nav_index=nav_index, subunit_domains=subunit_domains)
    for item in candidates:
        usable = usable_candidate_url(item, resolved_domains)
        if usable:
            item["url"] = usable
    candidate_results: list[dict[str, Any]] = []
    website_record = None
    gate_record = None
    first_party = None
    website_g4_record = None
    g4_gate_record = None
    requests = bytes_received = 0
    source_times: dict[str, str] = {}
    source_yield: Counter[str] = Counter()
    deadline_hit = False
    prefetched: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for candidate in candidates:
        if candidate.get("related_only") == "true":
            continue
        if max_seconds is not None and time.monotonic() - started > max_seconds:
            # The worker enforces its own budget from the moment it starts, so queue and
            # process start-up time in the parent can never be mistaken for a slow host.
            deadline_hit = True
            break
        chosen_url = usable_candidate_url(candidate, resolved_domains)
        if chosen_url is None:
            candidate_results.append({"source": candidate["source"], "domain": candidate["domain"], "requested_url": candidate["url"], "website_status": "failed", "state": "not_available", "failure_kind": "resolution", "identity": None, "first_party": {"publishable": False, "status": "not_checked"}})
            continue
        if candidate["url"] not in prefetched:
            # Fetch the whole tier (registry-linked candidates, then name-derived guesses)
            # concurrently: a parked or hanging guessed domain must not cost one full
            # timeout per candidate in sequence.
            tier_name_derived = str(candidate["source"]).startswith("name_derived")
            group = [
                item for item in candidates
                if str(item["source"]).startswith("name_derived") == tier_name_derived
                and item.get("related_only") != "true"
                and item["url"] not in prefetched
                and usable_candidate_url(item, resolved_domains) is not None
            ]
            tier_timeout = min(timeout, NAME_DERIVED_TIMEOUT) if tier_name_derived else timeout
            tier_total = NAME_DERIVED_TOTAL if tier_name_derived else REGISTRY_TIER_TOTAL
            pool = ThreadPoolExecutor(max_workers=max(1, min(len(group), 6)))
            try:
                futures = {item["url"]: pool.submit(fetch_with_http_fallback, fetcher, item["url"], timeout=tier_timeout, request_policy=request_policy) for item in group}
                tier_deadline = time.monotonic() + tier_total
                for url, future in futures.items():
                    try:
                        prefetched[url] = future.result(timeout=max(0.01, tier_deadline - time.monotonic()))
                    except FutureTimeout:
                        # A fetch is several requests; bound the whole candidate, not each request.
                        prefetched[url] = ({"status": "failed", "note": "candidate exceeded its total fetch budget"}, {"requests": 0, "bytes": 0, "failure_kind": "timeout"})
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        evidence, metrics = prefetched[candidate["url"]]
        requests += int(metrics.get("requests") or 0)
        bytes_received += int(metrics.get("bytes") or 0)
        source_times[candidate["source"]] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        profile = {"organisation_number": record.get("organisation_number"), "name": record.get("name"), "website": record.get("website"), "raw": record.get("raw") or {}, "evidence": {}}
        identity = apply_website_identity_gate(profile, evidence)["website"]
        assessment = assess_first_party_ownership({**profile, "evidence": {"website": identity}}, identity, istat_gate=True)
        related = detect_related_only_site({**profile, "evidence": {"website": identity}}, identity)
        if related.get("related_only"):
            assessment = {
                **assessment,
                "publishable": False,
                "status": "related_entity",
                "related_only": related,
                "reasons": [str(related.get("reason"))],
            }
        g4_assessment = assess_g4_ownership({**profile, "evidence": {"website": identity}}, identity, candidate_source=candidate["source"], g3_assessment=assessment) if gate == "g4" else {"publishable": False, "rule": None}
        candidate_results.append({"source": candidate["source"], "domain": candidate["domain"], "requested_url": candidate["url"], "website_status": identity.get("status"), "block_reason": identity.get("note") if identity.get("status") == "blocked" else None, "failure_kind": metrics.get("failure_kind"), "identity": (identity.get("value") or {}).get("identity_assessment"), "first_party": assessment, "g4": g4_assessment, "related_only": related})
        if identity.get("status") == "blocked":
            state = "blocked"
        elif identity.get("status") in {"failed", "source_error"}:
            state = "not_available" if metrics.get("failure_kind") == "resolution" else "failed"
        elif identity.get("status") in {"not_found", "not_available"}:
            state = "not_available"
        elif assessment.get("publishable"):
            state = "available"
            source_yield[candidate["source"]] += 1
            website_record, gate_record, first_party = identity, identity.get("value", {}).get("identity_assessment"), assessment
            website_g4_record, g4_gate_record = identity, g4_assessment
            candidate_results[-1]["state"] = state
            break
        elif g4_assessment.get("publishable"):
            state = "available"
            website_g4_record, g4_gate_record = identity, g4_assessment
            source_yield[f"{candidate['source']}:g4"] += 1
            candidate_results[-1]["gate"] = "g4"
            candidate_results[-1]["state"] = state
            break
        else:
            identity_assessment = (identity.get("value") or {}).get("identity_assessment") or {}
            entity_evidence = bool(
                float(identity_assessment.get("score") or 0.0) >= 0.65
                or (assessment.get("signals") or {}).get("address_match")
                or (assessment.get("signals") or {}).get("phone_match")
                or (assessment.get("signals") or {}).get("registry_email_domain_match")
                or (assessment.get("signals") or {}).get("registry_website_match")
            )
            state = "ambiguous" if entity_evidence else "not_available"
        candidate_results[-1]["state"] = state
    org = str(record.get("organisation_number"))
    website_state = _final_state(candidate_results, published=bool(website_record))
    website_g4_state = "not_checked" if gate != "g4" else _final_state(candidate_results, published=bool(website_g4_record))
    if deadline_hit:
        if not website_record:
            website_state = "failed"
        if gate == "g4" and not website_g4_record:
            website_g4_state = "failed"
    website_value = (website_record or {}).get("value") or {}
    website_g4_value = (website_g4_record or {}).get("value") or {}
    social_links = website_value.get("social_links") or []
    social_state = "available" if social_links else ("blocked" if website_record else "not_checked")
    contact = _contact_fields(record, website_record or {}) if website_record else {}
    contact_state = "available" if contact.get("matched_registry") else ("not_available" if website_record else "not_checked")
    nav = (nav_index or {}).get(org)
    nav_complete = bool((nav_index or {}).get("_meta", {}).get("complete"))
    nav_state = "available" if nav and nav.get("ads") else "not_available" if nav_complete else "not_checked"
    claims = {"official_website": website_value if website_state == "available" else {}, "official_website_g4": website_g4_value if website_g4_state == "available" else {}, "social_profiles": social_links if social_state == "available" else [], "contact": contact if contact_state == "available" else {}, "nav_jobs": (nav or {}).get("ads", []) if nav_state == "available" else []}
    try:
        record_stratum = stratum(record)
    except (KeyError, TypeError):
        record_stratum = "unknown"
    return {"cache_schema": CACHE_SCHEMA_VERSION, "organisation_number": org, "name": record.get("name"), "municipality": record.get("municipality"), "employees": record.get("employees"), "stratum": record_stratum, "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "candidate_domains": candidates, "candidates": candidate_results, "website": {"evidence": website_record, "identity_gate": gate_record, "first_party_gate": first_party} if website_record else {}, "website_g4": {"evidence": website_g4_record, "identity_gate": (website_g4_record or {}).get("value", {}).get("identity_assessment"), "first_party_gate": g4_gate_record} if website_g4_record else {}, "claims": claims, "states": {"official_website": website_state, "official_website_g4": website_g4_state, "social_profiles": social_state, "contact": contact_state, "nav_jobs": nav_state}, "nav_jobs": nav or {}, "source_retrieval_times": source_times, "metrics": {"requests": requests, "bytes": bytes_received, "elapsed_ms": int((time.monotonic() - started) * 1000)}, "source_yield": dict(source_yield)}


def discover_profile(profile: dict[str, Any], *, gate: str = "g4", timeout: float = 12.0, request_policy: HostRequestPolicy | None = None, nav_index: dict[str, dict[str, Any]] | None = None, resolved_domains: dict[str, bool] | None = None, max_seconds: float | None = None) -> dict[str, Any]:
    """Discover a profile using the same engine as the cache builder."""
    if resolved_domains is None:
        # Keep DNS resolution ahead of HTTP in the official path too.  The
        # cache builder supplies a whole-batch map; a live batch worker resolves
        # its own bounded candidate set in the process that owns the fetch.
        resolved_domains = pre_resolve_domains([profile], nav_index=nav_index, workers=16)
    return process_record(profile, nav_index=nav_index, request_policy=request_policy, timeout=timeout, gate=gate, resolved_domains=resolved_domains, max_seconds=max_seconds)
