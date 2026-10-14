from __future__ import annotations

import json
import re
import urllib.parse
from collections import Counter
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
CHAIN_PAGE_PATH_MARKERS = (
    "/butikker", "/finn-forhandler", "/avdelinger", "/medlemmer",
)
CHAIN_DOMAIN_EXAMPLES = frozenset({"rorkjop.no"})
PARKING_MARKERS = (
    "domain is for sale", "domain for sale", "this domain is for sale", "kjøp dette domenet",
    "buy this domain", "domain parking", "parked domain", "parked", "sedo.com", "dan.com", "afternic.com",
    "bodis.com", "parkingcrew", "one.com", "web hosting by", "dette domenet er til salgs",
)
THIRD_PARTY_MARKERS = ("fansite", "fan site", "fanside", "supporter", "uoffisiell", "unofficial", "ikke offisiell", "wiki", "katalog", "bedriftsoversikt", "firmaliste")
FREE_MAIL_DOMAINS = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "proton.me"}
SPORTS_LEGAL_TOKENS = {"bil", "idrettslag", "bedriftsidrettslag"}
LEGAL_FORM_SUFFIXES = frozenset({
    "as", "asa", "ans", "da", "enk", "iks", "sa", "sam", "sti", "stiftelsen",
    "nuf", "ab", "b", "v", "ba", "ks", "kf", "limited", "ltd", "inc", "plc",
})
GENERIC_DECLARED_NAME_TOKENS = {
    "as", "asa", "ans", "da", "norge", "norway", "group", "gruppen", "company", "official",
    "site", "website", "service", "drift", "holding", "the", "and", "og",
}

# These are deliberately conservative, deterministic examples of umbrella,
# franchise and housing/co-operative sites.  A related site can be useful
# evidence, but it must never become the exact entity's official website.
RELATED_ONLY_DOMAIN_EXAMPLES = frozenset({
    "bbl.no", "ringbo.no", "helgelandbbl.no", "privatmegleren.no",
    "krausnaimer.no", "trysilposten.no", "ragde.no", "rvsas.no",
})
# Matched only against the domain and the page title/header, never the whole page body: ordinary
# company sites say "konsern", "franchise" or "samarbeidspartnere" in running text.
RELATED_ONLY_TEXT_MARKERS = (
    "franchisetaker", "boligbyggelag", "borettslag", "våre selskaper", "vare selskaper",
    "our companies", "group sites", "group companies",
)
HOUSING_COOP_MARKERS = frozenset({"sameie", "sameiet", "borettslag", "boligbyggelag", "boligsameie", "bbl"})


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


def shared_registry_phone_counts(profiles: list[dict[str, Any]]) -> dict[str, int]:
    """Count registry phone reuse once for a bulk run, before website gates execute."""
    counts: Counter[str] = Counter()
    for profile in profiles:
        counts.update(_registry_phones(profile, _registry_raw(profile)))
    return dict(counts)


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


def _page_third_party_marker(website: dict[str, Any]) -> bool:
    value = website.get("value") or {}
    fields = [value.get("title"), value.get("h1"), value.get("description"), value.get("meta_description"), value.get("og_description")]
    for page in value.get("pages") or []:
        if isinstance(page, dict):
            fields.extend([page.get("title"), page.get("h1"), page.get("description"), page.get("meta_description"), page.get("og_description")])
    lowered = " ".join(str(item or "") for item in fields).casefold()
    return any(marker in lowered for marker in THIRD_PARTY_MARKERS)


def _delegated_contact(website: dict[str, Any], domain: str, registry_domains: set[str] | None = None) -> bool:
    # Own = the site's domain, any domain the registry lists for this company (DIPS: dips.com
    # site, registry email post@dips.no), or the same name on another TLD.
    # A domain the registry declares is never "delegated": its contact address may be a short
    # brand domain (aplacetostaystavanger.com -> hello@apts.no). The veto targets guessed domains.
    if domain and domain in (registry_domains or set()):
        return False
    text, _header = _page_text(website)
    emails = {match.casefold() for match in EMAIL_PATTERN.findall(text)}
    domains = {_domain_from_email(item) for item in emails}
    own_domains = {item for item in {domain, *(registry_domains or set())} if item}

    def label(item: str) -> str:
        return item.split(".")[0].replace("-", "")  # trondelag-betong.no ~ trondelagbetong.no

    own_labels = {label(item) for item in own_domains}

    def is_own(item: str) -> bool:
        return any(item == own or item.endswith("." + own) for own in own_domains) or label(item) in own_labels

    own = any(item and is_own(item) for item in domains)
    external = any(item and not is_own(item) and item not in FREE_MAIL_DOMAINS for item in domains)
    return bool(domains and not own and external)


REGION_TOKENS = {"midt", "nord", "sor", "vest", "ost", "norge", "norway", "nordland", "trondelag", "vestlandet", "ostlandet", "sorlandet"}


def _place_qualifier_missing_from_domain(profile: dict[str, Any], domain: str, place: Any, municipality: Any) -> bool:
    """A legal name with a place qualifier the domain lacks is a branch on a wider brand/group site.

    S29 live: HUSBY OPTIKK LEVANGER AS -> husbyoptikk.no (three-store chain run by sibling
    HUSBY OPTIKK AS); POLARIS MEDIA MIDT-NORGE AS -> polarismedia.no (group site).
    """
    label = (domain or "").split(".")[0].replace("-", "")
    places = set(_tokens(place)) | set(_tokens(municipality)) | REGION_TOKENS
    return any(token in places and token not in label for token in _tokens(profile.get("name")))


def _sports_name_only(profile: dict[str, Any]) -> bool:
    name = " ".join(_tokens(profile.get("name")))
    return bool(set(name.split()) & SPORTS_LEGAL_TOKENS)


def _name_postcode_domain_matches_legal_name(profile: dict[str, Any], domain: str) -> bool:
    """Match only the legal-name slug, allowing separators but no extra words."""
    name_tokens = list(_tokens(profile.get("name")))
    while name_tokens and name_tokens[-1] in LEGAL_FORM_SUFFIXES:
        name_tokens.pop()
    # Keep this aligned with the bounded name-domain generator while also
    # accepting the fully hyphenated spelling of a short connector (for
    # example, both musikkinnlandet.no and musikk-i-innlandet.no).
    expected = {"".join(name_tokens), "".join(token for token in name_tokens if len(token) > 1)}
    label = str(domain or "").split(".", 1)[0].casefold()
    if not any(expected) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", label):
        return False
    return label.replace("-", "") in expected


def _registered_person_match(profile: dict[str, Any], text: str) -> bool:
    raw = _registry_raw(profile)
    values = [profile.get("roles"), profile.get("officers"), profile.get("leadership"), raw.get("roller"), raw.get("roles")]
    names: list[str] = []
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if str(key).casefold() in {"name", "navn", "full_name", "person"} and isinstance(child, str):
                    names.append(child)
                else:
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    for value in values:
        walk(value)
    page_tokens = set(_tokens(text))
    return any(set(_tokens(name)) >= {token for token in _tokens(name) if len(token) > 2} and set(_tokens(name)) <= page_tokens for name in names if len(_tokens(name)) >= 2)


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


def detect_related_only_site(profile: dict[str, Any], website: dict[str, Any]) -> dict[str, Any]:
    """Identify umbrella, group, franchise and co-operative sites.

    This is a veto for G4 only; it does not alter the existing identity or G3
    assessment.  The returned reason is intentionally auditable and stable.
    """
    value = website.get("value") or {}
    final_url = value.get("final_url") or website.get("source_url") or ""
    domain = registered_domain(final_url) if final_url else ""
    target = digits_only(profile.get("organisation_number"))
    text, header = _page_text(website)
    lowered = f"{domain} {header} {text}".casefold()
    if domain in RELATED_ONLY_DOMAIN_EXAMPLES:
        return {"related_only": True, "reason": "known_group_franchise_or_cooperative_domain", "domain": domain}
    header_text = f"{domain} {header}".casefold()
    if any(marker in header_text for marker in RELATED_ONLY_TEXT_MARKERS):
        return {"related_only": True, "reason": "group_franchise_or_cooperative_marker", "domain": domain}
    identity = value.get("identity_assessment") or {}
    if identity.get("group_or_brand"):
        return {"related_only": True, "reason": "identity_gate_group_or_brand", "domain": domain}
    parsed_url = urllib.parse.urlparse(final_url)
    if domain in CHAIN_DOMAIN_EXAMPLES or any(marker in parsed_url.path.casefold() for marker in CHAIN_PAGE_PATH_MARKERS):
        return {"related_only": True, "reason": "chain_or_member_directory_page", "domain": domain}
    domain_tokens = set(_tokens(domain.split(".", 1)[0])) - {"as", "asa", "ans", "da", "vvs", "bygg", "gruppen", "group"}
    legal_tokens = set(_tokens(profile.get("name"))) - {"as", "asa", "ans", "da", "og"}
    directory_language = ("forhandler", "butikker", "medlemmer", "avdelinger", "franchise", "locations")
    domain_label = domain.split(".", 1)[0].replace("-", "")
    legal_joined = "".join(sorted(legal_tokens, key=len, reverse=True)[:1])
    # A concatenated domain such as "husbyoptikk" shares no whole token with "husby optikk levanger"
    # but plainly contains its distinctive words; only a real name mismatch counts.
    name_in_domain = any(token in domain_label for token in legal_tokens if len(token) >= 4) or (legal_joined and legal_joined in domain_label)
    directory_hits = sum(1 for word in directory_language if word in lowered)
    if domain_tokens and legal_tokens and not domain_tokens & legal_tokens and not name_in_domain and directory_hits >= 2:
        return {"related_only": True, "reason": "registry_name_domain_mismatch_on_directory_page", "domain": domain}
    page_numbers = extract_org_numbers(text)
    if len(page_numbers) > 1 and target not in page_numbers:
        return {"related_only": True, "reason": "several_other_organisation_numbers", "domain": domain}
    return {"related_only": False, "reason": None, "domain": domain}


def _g4_address_match(text: str, street: str, postcode: str) -> bool:
    """Require the registered postcode and street, without the G3 place fallback."""
    if not postcode or not street:
        return False
    compact = re.sub(r"\D", "", text)
    postcode_digits = re.sub(r"\D", "", postcode)
    if not postcode_digits or postcode_digits not in compact:
        return False
    street_tokens = set(_tokens(street))
    page_tokens = set(_tokens(text))
    return bool(street_tokens and len(street_tokens & page_tokens) >= max(1, min(2, len(street_tokens))))


def _g4_evidence_span(text: str, *, name_match: bool, address_match: bool, registry_tie: bool, registry_exact_identity: bool = False) -> str:
    if not text:
        return ""
    markers = []
    if name_match:
        markers.append("legal name")
    if address_match:
        markers.append("registered address and postcode")
    if registry_tie:
        markers.append("registry candidate tie")
    if registry_exact_identity:
        markers.append("exact registry identity")
    prefix = "G4 " + ", ".join(markers) + ": " if markers else "G4 evidence: "
    return (prefix + " ".join(text.split()))[:1200]


def _registry_declared_website(profile: dict[str, Any]) -> str:
    raw = _registry_raw(profile)
    return normalize_homepage(_first(profile.get("website"), raw.get("hjemmeside"), raw.get("Hjemmeside")))


def _registry_declared_signals(profile: dict[str, Any], website: dict[str, Any], *, text: str, signals: dict[str, Any]) -> dict[str, Any]:
    declared_url = _registry_declared_website(profile)
    value = website.get("value") or {}
    final_url = str(value.get("final_url") or website.get("source_url") or "")
    declared_domain = registered_domain(declared_url) if declared_url else ""
    final_domain = registered_domain(final_url) if final_url else ""
    status_code = value.get("status_code", website.get("status_code"))
    fetched_2xx = status_code is None or 200 <= int(status_code) < 300
    page_numbers = extract_org_numbers(text)
    organisation_number = digits_only(profile.get("organisation_number"))
    _street, postcode, _place, _municipality = _registry_address(profile, _registry_raw(profile))
    postcode_match = bool(postcode and re.search(rf"(?<!\d){re.escape(re.sub(r'\D', '', postcode))}(?!\d)", re.sub(r"\s+", " ", text)))
    legal_tokens = {token for token in _tokens(profile.get("name")) if token not in {"as", "asa", "ans", "da", "og", "and"}}
    distinctive_tokens = legal_tokens - GENERIC_DECLARED_NAME_TOKENS
    page_tokens = set(_tokens(text))
    distinctive_name = bool(distinctive_tokens & page_tokens)
    ordered_name = [token for token in _tokens(profile.get("name")) if token not in {"as", "asa", "ans", "da"}]
    full_name_phrase = bool(ordered_name and " ".join(ordered_name) in " ".join(_tokens(text)))
    all_name_tokens = bool(legal_tokens and legal_tokens <= page_tokens)
    declared_label = (declared_domain.split(".", 1)[0] if declared_domain else "").replace("-", "")
    declared_label_match = bool(declared_label and declared_label in "".join(page_tokens))
    org_corroboration = bool(
        signals.get("legal_org_number_match")
        or signals.get("structured_organisation_number_match")
        or (organisation_number and organisation_number in page_numbers)
    )
    cross_domain_allowed = bool(org_corroboration or all_name_tokens or declared_label_match)
    visible_length = len(" ".join(text.split()))
    parking = any(marker in text.casefold() for marker in PARKING_MARKERS) or ("coming soon" in text.casefold() and visible_length < 200)
    other_numbers = len(page_numbers - {organisation_number} - _group_numbers(profile))
    corroborated = bool(org_corroboration or distinctive_name or signals.get("registry_email_domain_match") or signals.get("phone_match") or signals.get("registered_person_match") or postcode_match)
    same_declared_site = bool(declared_domain and final_domain == declared_domain)
    final_url_is_allowed = same_declared_site or (bool(final_domain) and cross_domain_allowed)
    return {
        "declared_url": declared_url,
        "declared_domain": declared_domain,
        "final_domain": final_domain,
        "fetched_2xx": fetched_2xx,
        "same_declared_site": same_declared_site,
        "cross_domain_allowed": cross_domain_allowed,
        "parking": parking,
        "other_page_organisation_numbers": other_numbers,
        "distinctive_name_token": distinctive_name,
        "org_number_on_page": org_corroboration,
        "full_name_phrase": full_name_phrase,
        "corroborated": corroborated,
        "publishable": bool(
            declared_domain
            and final_url_is_allowed
            and fetched_2xx
            # The registry declaration replaces the name-identity check; corroboration below is required instead.
            and not parking
            and not signals.get("contradicted")
            and not signals.get("blocked_host")
            and not signals.get("directory_marker")
            and not signals.get("listing_path_marker")
            and not signals.get("third_party_marker")
            and not signals.get("delegated_contact")
            and not signals.get("three_or_more_org_numbers")
            and not signals.get("sports_name_only")
            and other_numbers < 3
            and corroborated
        ),
    }


def _registry_declared_floor_signals(
    profile: dict[str, Any],
    website: dict[str, Any],
    *,
    text: str,
    signals: dict[str, Any],
    related: dict[str, Any],
    enabled: bool,
) -> dict[str, Any]:
    """Apply the conservative registry-declared URL floor for weak identity pages."""
    declared_url = _registry_declared_website(profile)
    value = website.get("value") or {}
    requested_url = str(value.get("requested_url") or website.get("source_url") or "")
    final_url = str(value.get("final_url") or website.get("source_url") or "")
    declared_domain = registered_domain(declared_url) if declared_url else ""
    requested_domain = registered_domain(requested_url) if requested_url else ""
    final_domain = registered_domain(final_url) if final_url else ""
    status_code = value.get("status_code", website.get("status_code"))
    try:
        fetched_2xx = status_code is None or 200 <= int(status_code) < 300
    except (TypeError, ValueError):
        fetched_2xx = False
    page_numbers = extract_org_numbers(text)
    organisation_number = digits_only(profile.get("organisation_number"))
    other_numbers = page_numbers - {organisation_number}
    title = str(value.get("title") or "")
    final_label = final_domain.split(".", 1)[0] if final_domain else ""
    legal_tokens = set(_tokens(profile.get("name"))) - LEGAL_FORM_SUFFIXES
    distinctive_tokens = legal_tokens - GENERIC_DECLARED_NAME_TOKENS
    label_tokens = set(_tokens(final_label))
    title_tokens = set(_tokens(title))
    name_or_title_match = bool(distinctive_tokens & (label_tokens | title_tokens))
    registry_email_match = bool(signals.get("registry_email_domain_match") and final_domain)
    _street, postcode, place, municipality = _registry_address(profile, _registry_raw(profile))
    postcode_match = bool(postcode and re.search(rf"(?<!\d){re.escape(re.sub(r'\D', '', postcode))}(?!\d)", re.sub(r"\s+", " ", text)))
    contact_match = bool(registry_email_match or postcode_match or signals.get("phone_match") or signals.get("address_match") or _address_match(text, _street, postcode, place, municipality))
    support_match = bool(name_or_title_match or contact_match or (organisation_number and organisation_number in page_numbers))
    branch_site = _place_qualifier_missing_from_domain(profile, final_domain, place, municipality)
    header = " ".join(str(part or "") for part in (value.get("title"), value.get("description"))).casefold()
    housing_text = " ".join((str(profile.get("name") or ""), final_domain, title, header)).casefold()
    housing_coop_marker = any(marker in housing_text for marker in HOUSING_COOP_MARKERS)
    legal_form = str(profile.get("legal_form") or _registry_raw(profile).get("organisasjonsform.kode") or "").upper()
    managed_entity = legal_form in {"ESEK", "BRL", "SAM"}
    parsed_final_url = urllib.parse.urlparse(final_url)
    header_lower = f"{title} {value.get('description') or ''}".casefold()
    page_directory_marker = any(marker in header_lower or marker in final_domain for marker in DIRECTORY_MARKERS)
    page_listing_marker = any(marker in parsed_final_url.path.casefold() for marker in LISTING_PATH_MARKERS)
    page_blocked_host = _host_is_blocked((parsed_final_url.hostname or "").casefold().removeprefix("www."))
    page_third_party_marker = _page_third_party_marker(website)
    page_parking_marker = any(marker in text.casefold() for marker in PARKING_MARKERS)
    vetoes = {
        "blocked_host": bool(signals.get("blocked_host") or page_blocked_host),
        "directory_marker": bool(signals.get("directory_marker") or page_directory_marker),
        "listing_path_marker": bool(signals.get("listing_path_marker") or page_listing_marker),
        "third_party_marker": bool(signals.get("third_party_marker") or page_third_party_marker),
        "contradicted": bool(signals.get("contradicted")),
        "other_page_organisation_numbers": bool(other_numbers),
        "group_related_only": bool(signals.get("group_related_only")),
        "related_only": bool(related.get("related_only")),
        "parking_marker": bool(signals.get("parking_marker") or page_parking_marker),
        "delegated_contact": bool(signals.get("delegated_contact")),
        "sports_name_only": bool(signals.get("sports_name_only")),
        "branch_site": bool(branch_site),
        "housing_coop_marker": housing_coop_marker,
        "managed_entity": managed_entity,
    }
    requested_from_declared_host = bool(declared_domain and requested_domain == declared_domain)
    publishable = bool(
        enabled
        and website.get("status") == "available"
        and declared_domain
        and final_domain
        and requested_from_declared_host
        and fetched_2xx
        and support_match
        and not any(vetoes.values())
    )
    return {
        "declared_url": declared_url,
        "declared_domain": declared_domain,
        "requested_domain": requested_domain,
        "final_domain": final_domain,
        "requested_from_declared_host": requested_from_declared_host,
        "fetched_2xx": fetched_2xx,
        "name_or_title_match": name_or_title_match,
        "registry_email_domain_match": registry_email_match,
        "postcode_match": postcode_match,
        "phone_match": bool(signals.get("phone_match")),
        "address_match": bool(signals.get("address_match") or _address_match(text, _street, postcode, place, municipality)),
        "support_match": support_match,
        "page_organisation_numbers": sorted(page_numbers),
        "vetoes": vetoes,
        **vetoes,
        "publishable": publishable,
    }


def _identity_evidence_text(website: dict[str, Any]) -> str:
    """Mirror the identity gate's name-bearing fields for G4 token checks."""
    value = website.get("value") or {}
    parts: list[str] = [
        value.get("title"), value.get("description"), value.get("identity_text_excerpt"),
        value.get("final_url"), value.get("main_text_excerpt"),
    ]
    opengraph = value.get("opengraph") or {}
    if isinstance(opengraph, dict):
        parts.extend(opengraph.get(key) for key in ("og:site_name", "site_name", "og:title"))

    def add_names(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if key in {"name", "legalName", "alternateName"} and isinstance(child, str):
                    parts.append(child)
                else:
                    add_names(child)
        elif isinstance(node, list):
            for child in node:
                add_names(child)

    add_names(value.get("structured_organisations") or [])
    add_names(value.get("json_ld") or value.get("structured_json_ld") or [])
    for page in value.get("pages") or []:
        if isinstance(page, dict):
            parts.extend([page.get("title"), page.get("main_text_excerpt"), page.get("identity_text_excerpt")])
            page_og = page.get("opengraph") or {}
            if isinstance(page_og, dict):
                parts.extend(page_og.get(key) for key in ("og:site_name", "site_name", "og:title"))
    return " ".join(str(part or "") for part in parts)


def assess_g4_ownership(
    profile: dict[str, Any],
    website: dict[str, Any],
    *,
    candidate_source: str | None = None,
    g3_assessment: dict[str, Any] | None = None,
    allow_name_postcode: bool = True,
    allow_registry_floor: bool = True,
) -> dict[str, Any]:
    """Apply the additive G4 gate while retaining every G3 safety veto."""
    base = g3_assessment or assess_first_party_ownership(profile, website, istat_gate=True)
    text, _header = _page_text(website)
    identity_text = _identity_evidence_text(website)
    g4_text = f"{text} {identity_text}"
    raw = _registry_raw(profile)
    street, postcode, _place, _municipality = _registry_address(profile, raw)
    name_tokens = set(_tokens(profile.get("name"))) - {"as", "asa", "ans", "da", "og", "and"}
    page_tokens = set(_tokens(g4_text))
    name_match = bool(name_tokens and name_tokens <= page_tokens)
    address_match = _g4_address_match(text, street, postcode)
    postcode_match = bool(postcode and re.search(rf"(?<!\d){re.escape(re.sub(r'\D', '', postcode))}(?!\d)", re.sub(r"\s+", " ", text)))
    related = detect_related_only_site(profile, website)
    signals = base.get("signals") or {}
    identity = (website.get("value") or {}).get("identity_assessment") or {}
    own_number = digits_only(profile.get("organisation_number"))
    own_org_evidence = bool(signals.get("legal_org_number_match") or signals.get("structured_organisation_number_match") or (identity.get("score") == 1.0 and own_number in {digits_only(n) for n in identity.get("page_organisation_numbers") or []}))
    registry_tie = candidate_source in {"registry_website", "registry_email_domain"} and bool(signals.get("address_match") or signals.get("phone_match") or address_match)
    registry_declared = _registry_declared_signals(profile, website, text=g4_text, signals=signals) if candidate_source == "registry_website" else {"publishable": False}
    registry_declared_floor = _registry_declared_floor_signals(profile, website, text=g4_text, signals=signals, related=related, enabled=allow_registry_floor) if candidate_source == "registry_website" else {"publishable": False, "reason": "not_registry_candidate"}
    safe = bool(
        signals.get("identity_verified")
        and not signals.get("blocked_host")
        and not signals.get("directory_marker")
        and not signals.get("listing_path_marker")
        and not signals.get("contradicted")
        and not signals.get("group_related_only")
        and not related.get("related_only")
        and not signals.get("third_party_marker")
        and not signals.get("three_or_more_org_numbers")
        and not signals.get("parking_marker")
        and (own_org_evidence or not signals.get("delegated_contact"))
    )
    identity_score = float(identity.get("score") or 0.0)
    registry_exact_identity = bool(
        candidate_source in {"registry_website", "registry_email_domain"}
        and base.get("status") == "insufficient_evidence"
        and safe
        and identity_score >= 0.95
        and (signals.get("legal_org_number_match") or name_match)
    )
    exact_org_number = bool(
        safe
        and identity_score >= 0.95
        and (
            signals.get("legal_org_number_match")
            or signals.get("structured_organisation_number_match")
            # The identity gate already found this company's own number in its evidence (score 1.0).
            or (identity_score >= 1.0 and own_number in {digits_only(n) for n in identity.get("page_organisation_numbers") or []})
        )
        and signals.get("other_page_organisation_numbers", 0) < 3
    )
    final_domain = registered_domain(str((website.get("value") or {}).get("final_url") or website.get("source_url") or ""))
    name_postcode_domain_match = _name_postcode_domain_matches_legal_name(profile, final_domain)
    would_publish_name_postcode = bool(
        candidate_source == "name_derived_no"
        and final_domain.endswith(".no")
        and safe
        and name_match
        and postcode_match
        and (own_org_evidence or not signals.get("sports_name_only"))
    )
    branch_site = _place_qualifier_missing_from_domain(profile, final_domain, _place, _municipality)
    would_publish_name_postcode = would_publish_name_postcode and not branch_site
    name_postcode_guard = bool(own_org_evidence or (postcode_match and name_match and name_postcode_domain_match))
    if exact_org_number:
        rule = "g4_exact_org_number"
    elif related.get("related_only"):
        rule = None
    elif base.get("publishable"):
        rule = "g3"
    elif safe and name_match and address_match and not branch_site and (own_org_evidence or not signals.get("sports_name_only")):
        rule = "g4_name_address"
    elif safe and registry_tie:
        rule = "g4_registry_tie"
    elif registry_exact_identity:
        rule = "g4_registry_exact_identity"
    elif allow_name_postcode and would_publish_name_postcode and name_postcode_guard:
        rule = "g4_name_derived_postcode"
    else:
        rule = None
    # This is the sole G4 exception to brand/chain related-only demotion.  All
    # identity, directory, contradiction, parking, org-number and URL checks
    # above remain mandatory for the registry-declared candidate.
    # A cooperative/management-company page (e.g. a housing manager declared by a sameie) is another
    # organisation's site: the shared email or phone is not enough, the page must name this company.
    cooperative_page = related.get("reason") in {"group_franchise_or_cooperative_marker", "known_group_franchise_or_cooperative_domain"}
    # A group/brand page may be a parent's site: shared email, phone or postcode is not enough there either.
    brand_page = related.get("reason") == "identity_gate_group_or_brand"
    names_this_company = bool(registry_declared.get("org_number_on_page") or (
        registry_declared.get("full_name_phrase") if cooperative_page else registry_declared.get("distinctive_name_token")
    ))
    # Co-ownerships and housing cooperatives rarely own their site; the declared one is usually the manager's
    # (KOBBL, BATE). Only their own organisation number on the page ties it to them.
    legal_form = str(profile.get("legal_form") or _registry_raw(profile).get("organisasjonsform.kode") or "").upper()
    managed_entity = legal_form in {"ESEK", "BRL", "SAM"}
    if managed_entity and not registry_declared.get("org_number_on_page"):
        names_this_company = False
        brand_page = True
    if registry_declared.get("publishable") and (names_this_company or not (cooperative_page or brand_page)) and (own_org_evidence or not signals.get("sports_name_only")):
        rule = "g4_registry_declared"
    if registry_declared_floor.get("publishable") and rule is None:
        rule = "g4_registry_declared_floor"
    result = {
        **base,
        "publishable": bool(rule),
        "gate": "g4",
        "rule": rule,
        "evidence_span": _g4_evidence_span(g4_text, name_match=name_match, address_match=address_match, registry_tie=registry_tie, registry_exact_identity=registry_exact_identity or exact_org_number),
        "g4_signals": {"legal_name_all_tokens": name_match, "registered_street_and_postcode": address_match, "registered_postcode": postcode_match, "registry_candidate_tie": registry_tie, "registry_exact_identity": registry_exact_identity, "exact_org_number": exact_org_number, "registry_declared": registry_declared, "registry_declared_floor": registry_declared_floor, "related_only": bool(related.get("related_only")), "registered_person_match": _registered_person_match(profile, g4_text), "name_postcode_domain_match": name_postcode_domain_match, "name_postcode_guard": name_postcode_guard},
        "would_publish_name_postcode": would_publish_name_postcode,
        "would_publish_registry_floor": bool(registry_declared_floor.get("publishable")),
        "related_only": related,
    }
    if rule == "g4_registry_declared":
        result["published_url"] = registry_declared.get("declared_url") or (website.get("value") or {}).get("final_url")
    elif rule == "g4_registry_declared_floor":
        result["published_url"] = (website.get("value") or {}).get("final_url") or website.get("source_url")
    if not result["publishable"] and related.get("related_only"):
        result["status"] = "related_entity"
        result["reasons"] = [str(related.get("reason"))]
    elif result["publishable"]:
        result["status"] = "first_party"
        result["reasons"] = [f"G4 rule {rule} passed with the existing identity and SSRF-safe fetch gates"]
    return result


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

    shared_phone_count = int(profile.get("shared_phone_count") or profile.get("shared_registry_phone_count") or raw.get("shared_phone_count") or 0)
    sports_name_only = _sports_name_only(profile)
    signals = {
        "blocked_host": _host_is_blocked(host),
        "directory_marker": any(marker in header.casefold() or marker in host for marker in DIRECTORY_MARKERS),
        "listing_path_marker": any(marker in parsed_url.path.casefold() for marker in LISTING_PATH_MARKERS),
        "identity_verified": identity.get("publishable") is True,
        "registry_website_match": bool(registry_domain and registry_domain == domain),
        "registry_email_domain_match": bool(registry_email_domain and registry_email_domain == domain),
        "page_email_domain_match": bool(domain and domain in page_email_domains),
        "phone_match": bool(registry_phones and shared_phone_count < 5 and any(phone in compact_page_digits for phone in registry_phones)),
        "shared_registry_phone": bool(shared_phone_count >= 5 and registry_phones),
        "address_match": _address_match(text, street, postcode, place, municipality),
        "structured_organisation_number_match": bool(organisation_number and organisation_number in extract_org_numbers(structured_text)),
        "legal_org_number_match": legal_org_number_match,
        "other_page_organisation_numbers": len(page_org_numbers - {organisation_number}),
        "legal_name_municipality_imprint": _legal_name_municipality_imprint(profile, text),
        "legal_name_match": legal_name_match,
        "group_or_brand": bool(identity.get("group_or_brand")),
        "contradicting_organisation_numbers": contradicting_org_numbers,
        "allowed_group_organisation_numbers": related_org_numbers,
        "contradicted": bool(contradicting_org_numbers),
        "group_related_only": bool(related_org_numbers and organisation_number not in page_org_numbers),
        "third_party_marker": _page_third_party_marker(website),
        "delegated_contact": _delegated_contact(website, domain, {registry_domain, registry_email_domain}),
        "three_or_more_org_numbers": len(page_org_numbers) >= 3,
        "parking_marker": any(marker in text.casefold() for marker in PARKING_MARKERS) or ("coming soon" in text.casefold() and len(" ".join(text.split())) < 200),
        "sports_name_only": sports_name_only,
        "registered_person_match": _registered_person_match(profile, text),
    }
    medium_evidence_count = sum(bool(signals[name]) for name in ("registry_email_domain_match", "phone_match", "address_match"))
    blocked = signals["blocked_host"] or signals["directory_marker"] or signals["listing_path_marker"] or signals["third_party_marker"] or signals["parking_marker"] or signals["three_or_more_org_numbers"]
    group_or_brand = signals["group_or_brand"]
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
        and not group_or_brand
        and not signals["group_related_only"]
        and (signals["legal_org_number_match"] or not signals["delegated_contact"])
        and (signals["legal_org_number_match"] or not signals["sports_name_only"])
        and not signals["delegated_contact"]
        and not signals["sports_name_only"]
    )
    imprint_publishable = bool(
        relax_imprint_gate
        and signals["identity_verified"]
        and float(identity.get("score") or 0.0) >= 0.95
        and not blocked
        and signals["legal_name_municipality_imprint"]
        and signals["other_page_organisation_numbers"] < 3
        and not signals["contradicted"]
        and not group_or_brand
        and not signals["group_related_only"]
        and (signals["legal_org_number_match"] or not signals["delegated_contact"])
    )
    istat_publishable = bool(
        istat_gate
        and signals["identity_verified"]
        and not blocked
        and not signals["contradicted"]
        and not group_or_brand
        and not signals["group_related_only"]
        and signals["legal_name_match"]
        and (signals["legal_org_number_match"] or not signals["delegated_contact"])
        and (signals["legal_org_number_match"] or not signals["sports_name_only"])
        and ((signals["legal_org_number_match"] or signals["structured_organisation_number_match"]) or medium_evidence_count >= 2)
    )
    publishable = bool(
        signals["identity_verified"]
        and not blocked
        and not signals["contradicted"]
        and not group_or_brand
        and not signals["group_related_only"]
        and (signals["legal_org_number_match"] or not signals["delegated_contact"])
        and (signals["legal_org_number_match"] or not signals["sports_name_only"])
        and ((signals["registry_website_match"] and (signals["legal_org_number_match"] or signals["structured_organisation_number_match"] or signals["legal_name_match"])) or (contact_match and corroboration) or (signals["phone_match"] and signals["address_match"]) or relaxed_publishable or imprint_publishable or istat_publishable)
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
    elif group_or_brand:
        status = "related_entity"
        reasons = ["site title or visible brand/group evidence does not establish the registered legal entity"]
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
