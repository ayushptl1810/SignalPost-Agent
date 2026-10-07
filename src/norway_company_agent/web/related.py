from __future__ import annotations

from typing import Any

from ..core.orgnumber import digits_only, extract_org_numbers
from .first_party import _page_text


def group_org_numbers(profile: dict[str, Any]) -> set[str]:
    """Organisation numbers of parents, siblings and subsidiaries from the registry group structure."""
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


def assess_related_entity(profile: dict[str, Any], website: dict[str, Any]) -> dict[str, Any]:
    """Flag pages that identify a group relative but not the target itself.

    A related site is never an official-website claim; it is kept as evidence of the
    group relationship so it is not discarded and not mistaken for an exact match.
    """
    target = digits_only(profile.get("organisation_number"))
    text, _header = _page_text(website)
    on_page = extract_org_numbers(text)
    related = sorted(on_page & group_org_numbers(profile))
    if related and target not in on_page:
        return {"status": "related", "relationship": "group_member", "related_org_numbers": related, "publishable_as_official": False}
    return {"status": "none", "publishable_as_official": False}
