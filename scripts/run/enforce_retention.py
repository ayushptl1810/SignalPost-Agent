#!/usr/bin/env python3
"""Apply connector-specific retention without deleting human labels."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

PLATFORM_CONNECTORS = {
    "company_site": "company_site", "job_board": "nav_jobs", "youtube": "youtube_data_api",
    "google_places": "google_places_api", "news": "google_news_rss",
}
PROTECTED_PARTS = {"observation-labels.jsonl", "labels.jsonl", "human-labels.jsonl"}


def _read_policy(path: str | Path) -> dict[str, dict[str, Any]]:
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = body.get("connectors", []) if isinstance(body, dict) else body
    return {str(row.get("connector_id")): dict(row) for row in entries if isinstance(row, dict) and row.get("connector_id")}


def _date(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _connector(row: dict[str, Any]) -> str:
    return str(row.get("connector_id") or row.get("connector") or PLATFORM_CONNECTORS.get(str(row.get("platform") or ""), ""))


def _nav_expired(row: dict[str, Any], now: datetime) -> bool:
    if str(row.get("platform") or "") != "job_board" or str(row.get("signal_type") or "") != "job_posting":
        return False
    if row.get("active") is False or str(row.get("status") or "").casefold() in {"inactive", "closed", "expired", "removed"}:
        return True
    for key in ("expires_at", "expiration_date", "expiry_date"):
        expiry = _date(row.get(key))
        if expiry and expiry <= now:
            return True
    return False


def _expired(row: dict[str, Any], policies: dict[str, dict[str, Any]], now: datetime) -> bool:
    if _nav_expired(row, now):
        return True
    connector = _connector(row)
    days = policies.get(connector, {}).get("retention_days")
    retrieved = _date(row.get("retrieved_at"))
    return bool(days and retrieved and retrieved + timedelta(days=int(days)) <= now)


def _protected(path: Path) -> bool:
    return path.name in PROTECTED_PARTS or any(part.casefold() in {"human", "labels", "label"} for part in path.parts)


def _filter_jsonl(path: Path, policies: dict[str, dict[str, Any]], now: datetime, report: dict[str, Any], *, dry_run: bool) -> None:
    if not path.exists() or _protected(path):
        report["protected_files"].append(str(path)) if path.exists() else None
        return
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError):
        return
    keep = [row for row in rows if not _expired(row, policies, now)]
    removed = len(rows) - len(keep)
    if not removed:
        return
    report["removed_rows"] += removed
    report["changed_files"].append(str(path))
    if not dry_run:
        path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in keep), encoding="utf-8")


def _filter_evidence(root: Path, policies: dict[str, dict[str, Any]], now: datetime, report: dict[str, Any], *, dry_run: bool) -> None:
    evidence_root = root / "proxy" / "evidence"
    if not evidence_root.exists():
        return
    for pack_path in sorted(evidence_root.glob("*/evidence.json")):
        if _protected(pack_path):
            continue
        try:
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        context = pack.get("row_context") or {}
        row = {**context, "platform": context.get("platform"), "retrieved_at": next((source.get("retrieved_at") for source in pack.get("sources", []) if source.get("retrieved_at")), None)}
        if not _expired(row, policies, now):
            continue
        report["removed_evidence_packs"].append(str(pack_path.parent))
        if not dry_run:
            shutil.rmtree(pack_path.parent)


def enforce_retention(
    root: str | Path = "out",
    *,
    policy_path: str | Path = ROOT / "config/connector-policy.json",
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    if root_path.name == "daily":
        root_path = root_path.parent
    policies = _read_policy(policy_path)
    current = now or datetime.now(timezone.utc)
    report: dict[str, Any] = {"root": str(root_path), "as_of": current.isoformat().replace("+00:00", "Z"), "dry_run": dry_run, "retention_days": {key: value.get("retention_days") for key, value in policies.items()}, "removed_rows": 0, "changed_files": [], "removed_evidence_packs": [], "protected_files": []}
    for path in (root_path / "proxy" / "observations.jsonl",):
        _filter_jsonl(path, policies, current, report, dry_run=dry_run)
    for path in sorted(root_path.glob("daily/*/observations.jsonl")):
        _filter_jsonl(path, policies, current, report, dry_run=dry_run)
    _filter_evidence(root_path, policies, current, report, dry_run=dry_run)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Enforce connector retention policies.")
    parser.add_argument("--root", default="out")
    parser.add_argument("--policy", default=str(ROOT / "config/connector-policy.json"))
    parser.add_argument("--as-of")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    now = _date(args.as_of) if args.as_of else None
    print(json.dumps(enforce_retention(args.root, policy_path=args.policy, now=now, dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
