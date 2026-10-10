#!/usr/bin/env python3
"""Make exactly one redacted request per configured provider key."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

from norway_company_agent.search.pool import ProviderPool  # noqa: E402
from norway_company_agent.search.providers import ProviderError, ProviderFatalError, make_provider  # noqa: E402


def _key_suffix(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[-6:]


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose configured search keys with one request each.")
    parser.add_argument("--provider-config", default=str(ROOT / "data/search-providers.json"), type=Path)
    parser.add_argument("--output", default="out/provider-diagnosis.json", type=Path)
    parser.add_argument("--timeout", default=15.0, type=float)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    config = ProviderPool.load_config(args.provider_config)
    rows: list[dict[str, Any]] = []
    query = '"SignalPost provider diagnostic" Norway'
    for provider, settings in config.items():
        env_name = str(settings.get("env") or f"{provider.upper()}_API_KEYS")
        raw = os.environ.get(env_name, "") or os.environ.get({
            "serper": "SERPER_API_KEY", "serpapi": "SERPAPI_KEY", "linkup": "LINKUP_API_KEY",
            "tavily": "TAVILY_API_KEY", "brave": "BRAVE_API_KEY",
        }.get(provider, ""), "")
        keys = [value.strip() for value in raw.split(",") if value.strip()]
        if not keys:
            rows.append({"provider": provider, "configured": False, "env": env_name, "classification": "no_key_configured"})
            continue
        key = keys[0]
        result: dict[str, Any] = {
            "provider": provider, "configured": True, "env": env_name,
            "key_id_suffix": _key_suffix(key), "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
            "request_count": 1,
        }
        try:
            adapter = make_provider(provider, key)
            _results, operation = adapter.search(query, country="no", language="no", count=1, timeout=args.timeout)
            result.update({"classification": "success", "http_status": operation.get("status"), "response_headers": {}, "error_code": None, "error_body": ""})
        except ProviderError as exc:
            reason = getattr(exc, "reason", type(exc).__name__)
            if reason in {"provider_auth_or_permission", "provider_http_error"}:
                classification = "invalid_or_disabled_key_or_permission"
            elif reason in {"credits_exhausted", "quota_exhausted"}:
                classification = "quota_or_credits_exhausted"
            elif reason == "provider_transient_error":
                classification = "transient_or_rate_limited"
            else:
                classification = "provider_error"
            result.update({
                "classification": classification, "http_status": getattr(exc, "status", None),
                "error_code": reason, "error_body": getattr(exc, "body_excerpt", ""),
                "response_headers": getattr(exc, "headers", {}),
            })
        except Exception as exc:  # pragma: no cover - defensive boundary
            result.update({"classification": "client_error", "http_status": None, "error_code": type(exc).__name__, "error_body": str(exc)[:500], "response_headers": {}})
        rows.append(result)
    payload = {
        "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "one_request_per_configured_provider": True,
        "parallel_requests": False,
        "rows": rows,
        "secrets_redacted": True,
        "next_action": {
            "serpapi": "check account verification, quota and API-key status in the SerpApi dashboard",
            "linkup": "check account verification, plan/credit state and API-key status in the Linkup dashboard",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
