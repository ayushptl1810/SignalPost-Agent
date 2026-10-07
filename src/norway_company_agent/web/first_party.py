from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from ..core.orgnumber import digits_only, extract_org_numbers
from ..core.text import fold_tokens
from .discovery import BLOCKED_DISCOVERY_HOSTS
from .website import normalize_homepage, registered_domain


KNOWN_NON_FIRST_PARTY_HOSTS = set(BLOCKED_DISCOVERY_HOSTS) | {
    "arkitektkontorer.no",
    "brreg.no",
    "dibk.no",
    "kartverket.no",
    "nav.no",
    "skatteetaten.no",
}
EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
POSTCODE_PATTERN = re.compile(r"(?<!\d)(\d{4})(?!\d)")
DIRECTORY_MARKERS = (
    "business directory",
    "company directory",
    "company profile",
    "bedriftsprofil",
    "bedriftsregister",
    "firmaliste",
    "company listings",
    "find a company",
    "arkitektkontorer",
)
LISTING_PATH_MARKERS = (
    "/company/", "/foretak/", "/bedrift/", "/bedrifter/", "/selskap/", "/firma/", "/opplysning/", "/medlemsbedrift/",
    "/detail/", "/profil/", "/produkter/", "/tannlege/", "/lege/",
)


_tokens = fold_tokens


def _first(*values: Any) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _registry_raw(profile: dict[str, Any]) -> dict[str, Any]:
    raw = profile.get("raw")
    if isinstance(raw, dict):
        return raw
    registry = (profile.get("evidence") or {}).get("registry") or {}
    value = registry.get("value")
    return value if isinstance(value, dict) else {}


def _registry_email(profile: dict[str, Any], raw: dict[str, Any]) -> str:
    return _first(
        profile.get("email"),
        profile.get("email_address"),
        profile.get("epostadresse"),
        raw.get("epostadresse"),
        raw.get("Epostadresse"),
    ).casefold()


def _registry_phones(profile: dict[str, Any], raw: dict[str, Any]) -> set[str]:
    values = [
        profile.get("phone"), profile.get("telephone"), profile.get("telefon"),
        profile.get("mobile"), profile.get("mobil"),
        raw.get("telefon"), raw.get("Telefon"), raw.get("mobil"), raw.get("Mobil"),
    ]
    normalized: set[str] = set()
    for value in values:
        digits = re.sub(r"\D", "", str(value or ""))
        if digits.startswith("47") and len(digits) == 10:
            digits = digits[2:]
        if len(digits) == 8:
            normalized.add(digits)
    return normalized


def _registry_address(profile: dict[str, Any], raw: dict[str, Any]) -> tuple[str, str, str, str]:
    business = profile.get("business_address") if isinstance(profile.get("business_address"), dict) else {}
    postal = profile.get("postal_address") if isinstance(profile.get("postal_address"), dict) else {}
    street = _first(
        profile.get("address"),
        business.get("adresse"),
        raw.get("forretningsadresse.adresse"),
        raw.get("Forretningsadresse.adresse"),
        postal.get("adresse"),
        raw.get("postadresse.adresse"),
    )
    postcode = _first(
        profile.get("postal_code"),
        business.get("postnummer"),
        raw.get("forretningsadresse.postnummer"),
        raw.get("Forretningsadresse.postnummer"),
        postal.get("postnummer"),
        raw.get("postadresse.postnummer"),
    )
    place = _first(
        profile.get("city"),
        profile.get("municipality"),
        business.get("poststed"),
        business.get("kommune"),
        raw.get("forretningsadresse.poststed"),
        raw.get("forretningsadresse.kommune"),
        postal.get("poststed"),
        raw.get("postadresse.poststed"),
    )
    municipality = _first(profile.get("municipality"), business.get("kommune"), raw.get("forretningsadresse.kommune"))
    return street, postcode, place, municipality


def _page_text(website: dict[str, Any]) -> tuple[str, str]:
    value = website.get("value") or {}
    parts = [value.get("title"), value.get("description"), value.get("main_text_excerpt"), value.get("identity_text_excerpt")]
    for page in value.get("pages") or []:
        parts.extend([page.get("title"), page.get("main_text_excerpt"), page.get("identity_text_excerpt")])
    structured = value.get("structured_organisations") or []
    parts.append(json.dumps(structured, ensure_ascii=False))
    text = " ".join(str(part or "") for part in parts)
    header = " ".join(str(part or "") for part in parts[:2])
    return text, header


def _domain_from_email(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().casefold() if "@" in email else ""


def _host_is_blocked(host: str) -> bool:
    return any(host == blocked or host.endswith("." + blocked) for blocked in KNOWN_NON_FIRST_PARTY_HOSTS)


def _address_match(text: str, street: str, postcode: str, place: str, municipality: str) -> bool:
    normalized_text = " ".join(_tokens(text))
    text_tokens = set(_tokens(text))
    street_tokens = set(_tokens(street))
    postcode_match = bool(postcode and re.search(rf"(?<!\d){re.escape(re.sub(r'\D', '', postcode))}(?!\d)", re.sub(r"\s+", " ", text)))
    street_overlap = len(street_tokens & text_tokens)
    place_tokens = set(_tokens(place or municipality))
    place_match = bool(place_tokens & text_tokens)
    return (postcode_match and street_overlap >= 1) or (place_match and street_overlap >= 2 and bool(normalized_text))


def _legal_name_municipality_imprint(profile: dict[str, Any], text: str) -> bool:
    name_tokens = {token for token in _tokens(profile.get("name")) if token not in {"as", "asa", "ans", "da", "og", "and"}}
    municipality = _first(profile.get("municipality"), _registry_address(profile, _registry_raw(profile))[3])
    page_tokens = set(_tokens(text))
    municipality_tokens = set(_tokens(municipality))
    return bool(name_tokens and name_tokens <= page_tokens and municipality_tokens & page_tokens)


def _group_numbers(profile: dict[str, Any]) -> set[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("organisasjonsnummer", "parentOrganisasjonsnummer"):
                number = digits_only(node.get(key))
                if number:
                    found.add(number)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(((profile.get("evidence") or {}).get("group") or {}).get("value"))
    found.discard(digits_only(profile.get("organisation_number")))
    return found


def _legal_org_number_match(text: str, organisation_number: str) -> bool:
    if not organisation_number:
        return False
    pattern = re.compile(
        r"(?:org(?:anisasjonsnummer)?\.?\s*(?:nr|no|number|nummer)?|organisasjonsnummer)"
        r"\s*[:#.-]?\s*(\d{3}[ .\u00a0]?\d{3}[ .\u00a0]?\d{3})",
        re.I,
    )
    return any(digits_only(match.group(1)) == organisation_number for match in pattern.finditer(text))


def assess_first_party_ownership(
    profile: dict[str, Any],
    website: dict[str, Any],
    *,
    relax_address_gate: bool = False,
    relax_imprint_gate: bool = False,
    istat_gate: bool = False,
) -> dict[str, Any]:
    """Decide whether an exact-entity page also provides first-party ownership evidence.

    This deliberately returns only booleans and counts for contact comparisons. Raw
    email addresses, phone numbers, and street values are never copied into the
    assessment or persisted as discovery evidence.
    """
    value = website.get("value") or {}
    final_url = value.get("final_url") or website.get("source_url") or ""
    domain = registered_domain(final_url) if final_url else ""
    parsed_url = urllib.parse.urlparse(final_url)
    host = (parsed_url.hostname or "").casefold().removeprefix("www.")
    raw = _registry_raw(profile)
    registry_email = _registry_email(profile, raw)
    registry_email_domain = _domain_from_email(registry_email)
    registry_phones = _registry_phones(profile, raw)
    street, postcode, place, municipality = _registry_address(profile, raw)
    text, header = _page_text(website)
    compact_page_digits = re.sub(r"\D", "", text)
    page_emails = {match.casefold() for match in EMAIL_PATTERN.findall(text)}
    page_email_domains = {_domain_from_email(email) for email in page_emails}
    registry_website = _first(profile.get("website"), raw.get("hjemmeside"), raw.get("Hjemmeside"))
    registry_domain = registered_domain(normalize_homepage(registry_website) or "") if registry_website else ""
    structured_text = json.dumps(
        [*(value.get("structured_organisations") or []), *(value.get("structured_identifiers") or [])],
        ensure_ascii=False,
    )
    organisation_number = digits_only(profile.get("organisation_number"))
    identity = value.get("identity_assessment") or {}
    page_org_numbers = extract_org_numbers(text)
    legal_org_number_match = _legal_org_number_match(text, organisation_number)
    group_numbers = _group_numbers(profile)
    contradicting_org_numbers = sorted(page_org_numbers - {organisation_number} - group_numbers)
    related_org_numbers = sorted(page_org_numbers & group_numbers)
    legal_name_tokens = {token for token in _tokens(profile.get("name")) if token not in {"as", "asa", "ans", "da", "og", "and"}}
    page_tokens = set(_tokens(text))
    legal_name_match = bool(legal_name_tokens and legal_name_tokens <= page_tokens)

    signals = {
        "blocked_host": _host_is_blocked(host),
        "directory_marker": any(marker in header.casefold() or marker in host for marker in DIRECTORY_MARKERS),
        "listing_path_marker": any(marker in parsed_url.path.casefold() for marker in LISTING_PATH_MARKERS),
        "identity_verified": identity.get("publishable") is True,
        "registry_website_match": bool(registry_domain and registry_domain == domain),
        "registry_email_domain_match": bool(registry_email_domain and registry_email_domain == domain),
        "page_email_domain_match": bool(domain and domain in page_email_domains),
        "phone_match": bool(registry_phones and any(phone in compact_page_digits for phone in registry_phones)),
        "address_match": _address_match(text, street, postcode, place, municipality),
        "structured_organisation_number_match": bool(organisation_number and organisation_number in extract_org_numbers(structured_text)),
        "legal_org_number_match": legal_org_number_match,
        "other_page_organisation_numbers": len(page_org_numbers - {organisation_number}),
        "legal_name_municipality_imprint": _legal_name_municipality_imprint(profile, text),
        "legal_name_match": legal_name_match,
        "contradicting_organisation_numbers": contradicting_org_numbers,
        "allowed_group_organisation_numbers": related_org_numbers,
        "contradicted": bool(contradicting_org_numbers),
        "group_related_only": bool(related_org_numbers and organisation_number not in page_org_numbers),
    }
    medium_evidence_count = sum(bool(signals[name]) for name in ("registry_email_domain_match", "phone_match", "address_match"))
    blocked = signals["blocked_host"] or signals["directory_marker"] or signals["listing_path_marker"]
    contact_match = signals["registry_email_domain_match"] or signals["phone_match"] or signals["legal_org_number_match"]
    corroboration = signals["address_match"] or signals["registry_website_match"] or signals["structured_organisation_number_match"]
    relaxed_contact = contact_match and signals["other_page_organisation_numbers"] < 3
    relaxed_publishable = bool(
        relax_address_gate
        and signals["identity_verified"]
        and float(identity.get("score") or 0.0) >= 0.95
        and not blocked
        and relaxed_contact
        and not signals["contradicted"]
        and not signals["group_related_only"]
    )
    imprint_publishable = bool(
        relax_imprint_gate
        and signals["identity_verified"]
        and float(identity.get("score") or 0.0) >= 0.95
        and not blocked
        and signals["legal_name_municipality_imprint"]
        and signals["other_page_organisation_numbers"] < 3
        and not signals["contradicted"]
        and not signals["group_related_only"]
    )
    istat_publishable = bool(
        istat_gate
        and signals["identity_verified"]
        and not blocked
        and not signals["contradicted"]
        and not signals["group_related_only"]
        and signals["legal_name_match"]
        and ((signals["legal_org_number_match"] or signals["structured_organisation_number_match"]) or medium_evidence_count >= 2)
    )
    publishable = bool(
        signals["identity_verified"]
        and not blocked
        and not signals["contradicted"]
        and not signals["group_related_only"]
        and (signals["registry_website_match"] or (contact_match and corroboration) or (signals["phone_match"] and signals["address_match"]) or relaxed_publishable or imprint_publishable or istat_publishable)
    )

    if blocked:
        status = "directory_or_registry"
        reasons = ["candidate host or page markers identify a non-first-party source"]
    elif signals["contradicted"]:
        status = "contradicted"
        reasons = [f"page contains a different organisation number: {', '.join(contradicting_org_numbers)}"]
    elif signals["group_related_only"]:
        status = "related_entity"
        reasons = [f"page identifies a registry group member: {', '.join(related_org_numbers)}"]
    elif not signals["identity_verified"]:
        status = "insufficient_evidence"
        reasons = ["exact-entity identity gate did not pass"]
    elif publishable:
        status = "first_party"
        reasons = ["exact-entity evidence is corroborated by domain ownership/contact and address or structured evidence"]
    else:
        status = "insufficient_evidence"
        reasons = ["candidate lacks enough first-party contact and corroborating address/domain evidence"]
    return {
        "status": status,
        "publishable": publishable,
        "candidate_domain": domain,
        "signals": signals,
        "page_email_count": len(page_emails),
        "registry_phone_count": len(registry_phones),
        "method": "istat_strong_weak_gate_v1" if istat_gate else "first_party_contact_address_gate_v3" if relax_imprint_gate else "first_party_contact_address_gate_v2",
        "relax_address_gate": relax_address_gate,
        "relax_imprint_gate": relax_imprint_gate,
        "istat_gate": istat_gate,
        "reasons": reasons,
    }
