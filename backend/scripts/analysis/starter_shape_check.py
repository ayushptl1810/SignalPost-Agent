#!/usr/bin/env python3
"""Check that an envelope JSONL file has the starter kit's exact key shape."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from norway_company_agent.registry.batch import (
    STARTER_MODULES,
)


def _diff(path: str, actual: Iterable[str], expected: Iterable[str]) -> dict[str, Any] | None:
    actual_set = set(actual)
    expected_set = set(expected)
    extra = sorted(actual_set - expected_set)
    missing = sorted(expected_set - actual_set)
    if not extra and not missing:
        return None
    return {"path": path, "extra": extra, "missing": missing}


def check_rows(rows: list[dict[str, Any]], shape: dict[str, Any]) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        prefix = f"rows[{row_index}]"
        if not isinstance(row, dict):
            differences.append({"path": prefix, "extra": [type(row).__name__], "missing": ["object"]})
            continue
        for path, actual, expected in (
            (prefix, row.keys(), shape["envelope"]),
            (f"{prefix}.modules", (row.get("modules") or {}).keys(), shape["modules"]),
            (f"{prefix}.profile", (row.get("profile") or {}).keys(), shape["profile"]),
        ):
            difference = _diff(path, actual, expected)
            if difference:
                differences.append(difference)
        modules = row.get("modules") if isinstance(row.get("modules"), dict) else {}
        for module in STARTER_MODULES:
            value = modules.get(module)
            if not isinstance(value, dict):
                differences.append({"path": f"{prefix}.modules.{module}", "extra": [], "missing": list(shape["module"])})
            else:
                difference = _diff(f"{prefix}.modules.{module}", value.keys(), shape["module"])
                if difference:
                    differences.append(difference)
        profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
        profile_evidence = profile.get("evidence") if isinstance(profile.get("evidence"), dict) else {}
        difference = _diff(f"{prefix}.profile.evidence", profile_evidence.keys(), shape["modules"])
        if difference:
            differences.append(difference)
        for module in STARTER_MODULES:
            value = profile_evidence.get(module)
            if not isinstance(value, dict):
                differences.append({"path": f"{prefix}.profile.evidence.{module}", "extra": [], "missing": list(shape["evidence"])})
                continue
            difference = _diff(f"{prefix}.profile.evidence.{module}", value.keys(), shape["evidence"])
            if difference:
                differences.append(difference)
            if module == "website":
                website = value.get("value") if isinstance(value.get("value"), dict) else {}
                difference = _diff(f"{prefix}.profile.evidence.website.value", website.keys(), shape["website_value"])
                if difference:
                    differences.append(difference)
                pages = website.get("pages") if isinstance(website.get("pages"), list) else []
                for page_index, page in enumerate(pages):
                    if not isinstance(page, dict):
                        differences.append({"path": f"{prefix}.profile.evidence.website.value.pages[{page_index}]", "extra": [type(page).__name__], "missing": list(shape["website_page"])})
                        continue
                    difference = _diff(f"{prefix}.profile.evidence.website.value.pages[{page_index}]", page.keys(), shape["website_page"])
                    if difference:
                        differences.append(difference)
    return differences


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("envelopes", type=Path)
    parser.add_argument("--shape", type=Path, default=Path(__file__).resolve().parents[2] / "tests/fixtures/starter_shape.json")
    args = parser.parse_args()
    differences = check_rows(_read_jsonl(args.envelopes), json.loads(args.shape.read_text(encoding="utf-8")))
    if differences:
        print(json.dumps(differences, ensure_ascii=False, indent=2))
        return 1
    print(f"starter shape passed: {args.envelopes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
