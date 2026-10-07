from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..web.website import registered_domain
from .external_footprint import connector_policy_entry, observation_id

CONNECTOR_ID = "google_places_api"
PLATFORM = "google_places"
ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join(("places.id", "places.displayName", "places.formattedAddress", "places.nationalPhoneNumber", "places.internationalPhoneNumber", "places.websiteUri", "places.rating", "places.userRatingCount", "places.businessStatus", "places.googleMapsUri"))


def _tokens(value: Any) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", str(value or "").casefold().replace("æ", "ae").replace("ø", "o").replace("å", "a")) if token not in {"as", "asa", "og", "the"} and len(token) > 1}


def normalize_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("47") and len(digits) > 8:
        digits = digits[2:]
    return digits[-8:]


def _registry(profile: dict[str, Any]) -> dict[str, Any]:
    return ((profile.get("evidence") or {}).get("registry_live") or (profile.get("evidence") or {}).get("registry") or {}).get("value") or {}


def _field(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
        value: Any = record
        for part in name.split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if value not in (None, ""):
            return value
    return None


def _registry_phone(profile: dict[str, Any]) -> str:
    raw = _registry(profile)
    return normalize_phone(_field(raw, "telefon", "mobil", "phone", "forretningsadresse.telefon"))


def _registry_address(profile: dict[str, Any]) -> tuple[str, str, str]:
    raw = _registry(profile)
    address = _field(raw, "forretningsadresse", "business_address") or {}
    if isinstance(address, str):
        street = address
        postcode = str(_field(raw, "forretningsadresse.postnummer", "postnummer") or "")
        city = str(_field(raw, "forretningsadresse.poststed", "poststed") or "")
    else:
        street = " ".join(str(item) for item in (address.get("adresse") or address.get("street") or []) if item) if isinstance(address.get("adresse") or address.get("street"), list) else str(address.get("adresse") or address.get("street") or "")
        postcode = str(address.get("postnummer") or address.get("postal_code") or "")
        city = str(address.get("poststed") or address.get("city") or "")
    return street, re.sub(r"\D", "", postcode), city


def identity_signals(profile: dict[str, Any], place: dict[str, Any]) -> dict[str, Any]:
    name = (place.get("displayName") or {}).get("text") if isinstance(place.get("displayName"), dict) else place.get("displayName")
    formatted = str(place.get("formattedAddress") or "")
    phone_match = bool(_registry_phone(profile) and _registry_phone(profile) == normalize_phone(place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber")))
    street, postcode, _city = _registry_address(profile)
    address_tokens = _tokens(formatted)
    street_tokens = _tokens(street)
    address_match = bool(street_tokens and street_tokens & address_tokens and (not postcode or postcode in re.sub(r"\D", "", formatted)))
    website = (profile.get("evidence", {}).get("website", {}).get("value") or {})
    verified_domain = registered_domain(website.get("final_url") or profile.get("website") or "")
    place_domain = registered_domain(place.get("websiteUri") or "")
    website_match = bool(verified_domain and place_domain and verified_domain == place_domain)
    legal_tokens = _tokens(profile.get("name")) | _tokens(website.get("title"))
    name_overlap = bool(legal_tokens & _tokens(name))
    signals = {"phone_match": phone_match, "address_match": address_match, "website_match": website_match}
    return {
        **signals, "matched_signals": [key for key, value in signals.items() if value],
        "name_overlap": name_overlap, "name": name, "address": formatted,
        "business_operational": str(place.get("businessStatus") or "").upper() in {"OPERATIONAL", "OPEN"},
        "website_contradiction": bool(verified_domain and place_domain and verified_domain != place_domain),
        "place_id": place.get("id"),
    }


def choose_place(profile: dict[str, Any], places: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    candidates = []
    for place in places:
        signals = identity_signals(profile, place)
        if signals["name_overlap"] and signals["business_operational"] and not signals["website_contradiction"] and len(signals["matched_signals"]) >= 2:
            candidates.append({"place": place, "signals": signals})
    if len(candidates) != 1:
        return None, candidates
    return candidates[0], candidates


def _keys() -> list[str]:
    values = []
    for name in ("GOOGLE_MAPS_API_KEYS", "GOOGLE_MAPS_API_KEY", "PLACES_API_KEYS", "PLACES_API_KEY", "GOOGLE_PLACES_API_KEY"):
        values.extend(part.strip() for part in os.environ.get(name, "").split(",") if part.strip())
    return list(dict.fromkeys(values))


def _query(profile: dict[str, Any]) -> str:
    name = re.sub(r"\b(AS|ASA|SA|ENK|NUF|DA|ANS)\b", "", str(profile.get("name") or "").strip(), flags=re.I).strip()
    return f"{name} {profile.get('municipality') or ''}".strip()


def _request(key: str, profile: dict[str, Any], *, timeout: float = 15.0, opener=urllib.request.urlopen) -> dict[str, Any]:
    body = json.dumps({"textQuery": _query(profile), "regionCode": "NO", "languageCode": "no", "maxResultCount": 3}).encode()
    request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={"Content-Type": "application/json", "X-Goog-FieldMask": FIELD_MASK, "X-Goog-Api-Key": key})
    with opener(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _observation(profile: dict[str, Any], place: dict[str, Any], signals: dict[str, Any], *, signal_type: str, now: datetime, raw: Any, policy_path: str, **extra: Any) -> dict[str, Any]:
    policy = connector_policy_entry(CONNECTOR_ID, platform=PLATFORM, acquisition_mode="official_api", path=policy_path)
    source = str(place.get("googleMapsUri") or f"https://www.google.com/maps/search/?api=1&query_place_id={place.get('id')}")
    return {
        "id": observation_id(CONNECTOR_ID, str(profile["organisation_number"]), source, signal_type),
        "organisation_number": str(profile["organisation_number"]), "platform": PLATFORM, "signal_type": signal_type,
        "source_url": source, "retrieved_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "content_sha256": hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "exact_entity": True, "identity_proof": [{"type": key, "matched": signals[key]} for key in signals.get("matched_signals", [])] + [{"type": "google_place_id", "value": place.get("id")}, {"type": "place_address", "value": place.get("formattedAddress")}],
        "acquisition_mode": "official_api", "rights_status": policy.get("rights_status", "review_required"), "connector_id": CONNECTOR_ID,
        "source_class": "google_places_api", "strategy": extra.pop("strategy", "places_identity_resolution"), **extra,
    }


def is_triaged(profile: dict[str, Any]) -> bool:
    if str(profile.get("stratum") or "") in {"S2", "S3", "S2|", "S3|"}:
        return True
    code = str(profile.get("industry_code") or profile.get("nace") or "")
    return profile.get("employees") in (None, "") and str(profile.get("legal_form") or "").upper() in {"AS", "ASA"} and code[:2] in {"64", "68", "00"} and not (_registry_phone(profile) or _registry_address(profile)[0])


def collect(profile: dict[str, Any], *, now: datetime, context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    started = time.monotonic()
    if is_triaged(profile):
        return {"status": "not_applicable", "observations": [], "operations": {"requests": 0, "third_party_cost_usd": 0.0, "latency_ms": [0]}, "note": "Triaged property/holding/unspecified entity without customer-facing identity signals."}
    max_calls = int(context.get("max_calls", 1000))
    used = int(context.get("calls_used", 0))
    if used >= max_calls:
        return {"status": "failed", "observations": [], "operations": {"requests": 0, "third_party_cost_usd": 0.0, "latency_ms": [0]}, "note": "budget_exhausted"}
    try:
        raw = context.get("response")
        if raw is None:
            key = context.get("api_key") or (_keys()[0] if _keys() else "")
            if not key:
                return {"status": "failed", "observations": [], "operations": {"requests": 0, "third_party_cost_usd": 0.0, "latency_ms": [0]}, "note": "No Google Places API key configured."}
            raw = _request(key, profile, timeout=float(context.get("timeout", 15)), opener=context.get("opener", urllib.request.urlopen))
        places = raw.get("places") or []
        selected, candidates = choose_place(profile, places)
        operations = {"requests": 1, "third_party_cost_usd": 0.0, "latency_ms": [round((time.monotonic() - started) * 1000)]}
        if not selected:
            return {"status": "not_available", "observations": [], "operations": operations, "note": "No unique operational place met two-of-three exact identity signals.", "candidates": [{"place_id": item["place"].get("id"), "matched_signals": item["signals"].get("matched_signals", [])} for item in candidates]}
        place, signals = selected["place"], selected["signals"]
        observations = [_observation(profile, place, signals, signal_type="place_summary", now=now, raw=raw, policy_path=str(context.get("policy_path", "config/connector-policy.json")), strategy="places_identity_resolution", metrics={"place_id": place.get("id"), "address": place.get("formattedAddress")})]
        if place.get("rating") is not None or place.get("userRatingCount") is not None:
            observations.append(_observation(profile, place, signals, signal_type="review_summary", now=now, raw=raw, policy_path=str(context.get("policy_path", "config/connector-policy.json")), strategy="places_rating_reviews", metrics={"rating": place.get("rating"), "review_count": place.get("userRatingCount")}, attribution="Google Maps attribution required when displayed."))
        index_path = context.get("index_path")
        if index_path:
            path = Path(index_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"organisation_number": profile.get("organisation_number"), "place_id": place.get("id"), "website_uri": place.get("websiteUri"), "phone": place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber"), "matched_signals": signals.get("matched_signals", []), "retrieved_at": observations[0]["retrieved_at"]}, ensure_ascii=False, separators=(",", ":")) + "\n")
        return {"status": "available", "observations": observations, "operations": operations, "note": None}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        return {"status": "failed", "observations": [], "operations": {"requests": 1, "third_party_cost_usd": 0.0, "latency_ms": [round((time.monotonic() - started) * 1000)]}, "note": f"Google Places failure: {type(exc).__name__}"}
