from __future__ import annotations

import importlib
import pkgutil
from datetime import datetime
from typing import Any

PACKAGE = "norway_company_agent.external"
KNOWN_CONNECTORS = {"nav_jobs": "norway_company_agent.external.nav_jobs", "youtube_data_api": "norway_company_agent.external.youtube_api", "google_places_api": "norway_company_agent.external.google_places"}


def discover_connectors() -> tuple[dict[str, Any], dict[str, str]]:
    found: dict[str, Any] = {}
    errors: dict[str, str] = {}
    module_names = set(KNOWN_CONNECTORS.values())
    try:
        package = importlib.import_module(PACKAGE)
        module_names |= {info.name for info in pkgutil.iter_modules(package.__path__, package.__name__ + ".")}
    except Exception as exc:
        errors[PACKAGE] = type(exc).__name__
    for module_name in sorted(module_names):
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            errors[module_name] = type(exc).__name__
            continue
        connector_id = getattr(module, "CONNECTOR_ID", None)
        collect = getattr(module, "collect", None)
        if connector_id and callable(collect):
            found[str(connector_id)] = module
    return found, errors


def run_connectors(profile: dict[str, Any], now: datetime, budgets: dict[str, Any] | None = None) -> dict[str, Any]:
    budgets = budgets or {}
    modules, import_errors = discover_connectors()
    results: dict[str, dict[str, Any]] = {}
    all_observations: list[dict[str, Any]] = []
    for connector_id, module_name in KNOWN_CONNECTORS.items():
        if connector_id not in modules:
            results[connector_id] = {"status": "not_configured", "observations": [], "operations": {"requests": 0, "third_party_cost_usd": 0.0, "latency_ms": []}, "note": import_errors.get(module_name, "connector module is absent")}
            continue
        try:
            context = dict(budgets.get(connector_id, {})) if isinstance(budgets.get(connector_id), dict) else {}
            result = modules[connector_id].collect(profile, now=now, context=context)
            if not isinstance(result, dict):
                raise TypeError("connector collect() must return a dict")
        except Exception as exc:  # connector failures are data, not a batch crash
            result = {"status": "failed", "observations": [], "operations": {"requests": 0, "third_party_cost_usd": 0.0, "latency_ms": []}, "note": f"connector exception: {type(exc).__name__}"}
        results[connector_id] = result
        all_observations.extend(result.get("observations") or [])
    return {"observations": all_observations, "connectors": results, "import_errors": import_errors}
