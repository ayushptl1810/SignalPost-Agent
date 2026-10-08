#!/usr/bin/env python3
"""Convert the pre-existing assistant audit into explicitly non-human drafts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _draft_note(row: dict[str, Any], evidence_root: Path) -> str:
    pack_path = evidence_root / str(row.get("id")) / "evidence.json"
    if not pack_path.exists():
        return "Codex review draft; no evidence pack was available at provenance repair time."
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    details = []
    for source in pack.get("sources") or []:
        url = source.get("final_url") or source.get("requested_url") or ""
        status = source.get("status") or "unknown"
        title = source.get("title") or source.get("visible_organisation_name") or source.get("note") or ""
        details.append(f"{status} {url} ({' '.join(str(title).split())[:180]})")
    checked = "; ".join(details) or "no source rows"
    verdict = row.get("exact_entity")
    decision = "not exact entity" if verdict is False else "exact-entity draft" if verdict is True else "unresolved draft"
    return f"Codex review draft ({decision}); checked evidence source(s): {checked}. This is not a human label."


def repair(input_path: str | Path, output_path: str | Path, evidence_root: str | Path) -> dict[str, Any]:
    rows = _read_jsonl(Path(input_path))
    repaired = []
    for row in rows:
        item = dict(row)
        item["labeler"] = "codex_review"
        item["notes"] = str(item.get("notes") or "").strip() or _draft_note(item, Path(evidence_root))
        item.pop("export_source", None)
        item.pop("export_session_id", None)
        repaired.append(item)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in repaired), encoding="utf-8")
    return {
        "rows": len(repaired),
        "labeler": "codex_review",
        "no_rows_with_notes": sum(row.get("exact_entity") is False and bool(row.get("notes")) for row in repaired),
        "output": str(output),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="out/proxy/observation-labels.jsonl")
    parser.add_argument("--output")
    parser.add_argument("--evidence-root", default="out/proxy/evidence")
    args = parser.parse_args()
    output = args.output or args.input
    print(json.dumps(repair(args.input, output, args.evidence_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
