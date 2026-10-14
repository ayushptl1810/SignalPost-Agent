from __future__ import annotations

from typing import Any

from .envelopes import adapt_envelope


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, fallback: str = "Not available") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _claims(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(claim.get("field")): claim
        for claim in envelope.get("claims", [])
        if isinstance(claim, dict) and claim.get("field")
    }


def _sources(envelope: dict[str, Any], claim: dict[str, Any] | None) -> list[dict[str, Any]]:
    evidence = {
        str(item.get("id")): item
        for item in envelope.get("evidence", [])
        if isinstance(item, dict) and item.get("id")
    }
    ids = claim.get("evidence_ids", []) if claim else []
    selected = [evidence[evidence_id] for evidence_id in ids if evidence_id in evidence]
    return [
        {
            "id": item.get("id"),
            "url": item.get("source_url"),
            "source_class": item.get("source_class"),
            "retrieved_at": item.get("retrieved_at"),
        }
        for item in selected[:8]
    ]


def _answer_for_topic(topic: str, envelope: dict[str, Any], claims: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
    profile = _record(envelope.get("profile"))
    name = _text(profile.get("name"), str(envelope.get("organisation_number", "the company")))
    claim: dict[str, Any] | None = None

    if topic == "financials":
        claim = claims.get("latest_annual_accounts")
        value = _record(claim.get("value") if claim else None)
        if not claim or claim.get("availability") != "available":
            return f"The latest annual accounts are not available in the current public record for {name}.", claim
        period = _record(value.get("period"))
        end_date = _text(period.get("tilDato"), "the latest reported period")
        return (
            f"{name}'s latest reported accounts cover the period ending {end_date}. "
            f"Revenue: {_money(value.get('revenue'))}; annual result: {_money(value.get('annual_result'))}; "
            f"assets: {_money(value.get('assets'))}; debt: {_money(value.get('debt'))}.",
            claim,
        )

    if topic == "leadership":
        claim = claims.get("leadership")
        people = claim.get("value", []) if claim else []
        if not claim or claim.get("availability") != "available" or not isinstance(people, list) or not people:
            return f"Leadership is not available in the current public record for {name}.", claim
        leaders = [f"{_text(_record(person).get('name'))} ({_text(_record(person).get('role'))})" for person in people[:6]]
        return f"The public record lists these leadership roles for {name}: {', '.join(leaders)}.", claim

    if topic == "hiring":
        claim = claims.get("hiring")
        value = _record(claim.get("value") if claim else None)
        postings = [
            item
            for key in ("postings", "company_site_postings", "nav_ads")
            for item in value.get(key, [])
            if isinstance(item, dict)
        ]
        if not claim or claim.get("availability") != "available" or not postings:
            return f"No current hiring roles were found for {name} in the checked public sources.", claim
        roles = ", ".join(_text(item.get("title") or item.get("position") or item.get("job_title")) for item in postings[:5])
        return f"The checked public sources list {len(postings)} hiring signal(s) for {name}. Roles include: {roles}.", claim

    if topic == "location":
        claim = claims.get("registered_workplaces")
        places = claim.get("value", []) if claim else []
        if not claim or claim.get("availability") != "available" or not isinstance(places, list) or not places:
            return f"Registered workplaces are not available in the current public record for {name}.", claim
        addresses = []
        for place in places[:5]:
            address = _record(_record(place).get("address"))
            parts = [
                ", ".join(str(part) for part in address.get("adresse", []) if part),
                _text(address.get("poststed"), ""),
                _text(address.get("kommune"), ""),
            ]
            addresses.append(" · ".join(part for part in parts if part))
        return f"The registry lists {name} at: {'; '.join(address for address in addresses if address)}.", claim

    if topic == "activity":
        claim = claims.get("public_activity")
        items = _record(claim.get("value") if claim else None).get("items", [])
        if not claim or claim.get("availability") != "available" or not isinstance(items, list) or not items:
            return f"No dated public activity was found for {name} in the checked sources.", claim
        latest = _record(items[0])
        return f"The latest dated public activity found for {name} is {_text(latest.get('title') or latest.get('headline'))}: {_text(latest.get('detail') or latest.get('description'))}.", claim

    claim = claims.get("company_brief")
    value = _record(claim.get("value") if claim else None)
    if claim and claim.get("availability") == "available":
        return _text(value.get("summary"), f"The checked record identifies {name} but does not provide a fuller brief."), claim
    industry = _record(claims.get("legal_identity", {}).get("value")).get("naeringskode1.beskrivelse")
    municipality = _text(profile.get("municipality"), "an unavailable municipality")
    return f"{name} is registered in {municipality}. Industry: {_text(industry)}.", claims.get("legal_identity")


def _money(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "not available"
    return f"NOK {value:,.0f}"


def answer_question(question: str, envelope: dict[str, Any]) -> dict[str, Any]:
    envelope = adapt_envelope(envelope)
    normalized = question.lower()
    if any(word in normalized for word in ("unknown", "missing", "gap", "not available")):
        topic = "gaps"
        claims = _claims(envelope)
        unknown = envelope.get("unknown_areas", [])
        if isinstance(unknown, list) and unknown:
            answer = f"The current public record does not establish: {', '.join(str(area).replace('_', ' ') for area in unknown)}. These areas remain unavailable rather than inferred."
        else:
            answer = "No open evidence gaps were reported in the latest backend run."
        claim = None
    elif any(word in normalized for word in ("financial", "accounts", "revenue", "result", "assets", "debt")):
        topic = "financials"
        claims = _claims(envelope)
        answer, claim = _answer_for_topic(topic, envelope, claims)
    elif any(word in normalized for word in ("leader", "lead", "director", "who runs")):
        topic = "leadership"
        claims = _claims(envelope)
        answer, claim = _answer_for_topic(topic, envelope, claims)
    elif any(word in normalized for word in ("hire", "job", "role", "working here", "careers")):
        topic = "hiring"
        claims = _claims(envelope)
        answer, claim = _answer_for_topic(topic, envelope, claims)
    elif any(word in normalized for word in ("where", "office", "location", "workplace", "address")):
        topic = "location"
        claims = _claims(envelope)
        answer, claim = _answer_for_topic(topic, envelope, claims)
    elif any(word in normalized for word in ("change", "recent", "activity", "update", "news")):
        topic = "activity"
        claims = _claims(envelope)
        answer, claim = _answer_for_topic(topic, envelope, claims)
    else:
        topic = "company_brief"
        claims = _claims(envelope)
        answer, claim = _answer_for_topic(topic, envelope, claims)

    return {
        "answer": answer,
        "topic": topic,
        "sources": _sources(envelope, claim),
        "unknown_areas": envelope.get("unknown_areas", []),
        "run_id": envelope.get("run_id") or _record(envelope.get("run")).get("run_id"),
    }
