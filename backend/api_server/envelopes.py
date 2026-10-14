from __future__ import annotations

from copy import deepcopy
from typing import Any

from norway_company_agent.registry.contract import build_contract_sections


def adapt_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Adapt a starter envelope to the API's internal rich read shape.

    Full envelopes are returned unchanged.  Starter rows are copied before the
    existing contract builder derives claims and source references, so importing
    or answering questions never mutates the evaluator-facing row.
    """
    if not isinstance(envelope, dict):
        raise TypeError("Envelope must be an object")
    if {"run", "claims", "evidence"}.issubset(envelope):
        return envelope

    profile = deepcopy(envelope.get("profile") if isinstance(envelope.get("profile"), dict) else {})
    run_id = str(envelope.get("run_id") or "unknown-run")
    started_at = str(envelope.get("started_at") or "")
    completed_at = str(envelope.get("completed_at") or "")
    contract = build_contract_sections(
        profile,
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
    )
    adapted = deepcopy(envelope)
    adapted.update(contract)
    adapted["changes"] = []
    adapted["unknown_areas"] = [
        str(item.get("field"))
        for item in adapted.get("claims") or []
        if item.get("availability") not in {"available", "not_applicable"}
    ]
    return adapted
