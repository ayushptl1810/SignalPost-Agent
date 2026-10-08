#!/usr/bin/env python3
"""Re-key a NAV employer index from sub-unit to parent organisation numbers.

NAV's feed carries the *sub-unit* (underenhet) organisation number of the workplace
that advertises, while the competition universe and the registry anchor use the legal
entity (enhet). This joins the public Brreg sub-unit bulk file (`overordnetEnhet`) onto
the index and merges ads, homepages and contact e-mail domains per parent.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path


def load_parent_map(path: str | Path, wanted: set[str]) -> dict[str, str]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    mapping: dict[str, str] = {}
    for unit in data:
        org = str(unit.get("organisasjonsnummer") or "")
        parent = str(unit.get("overordnetEnhet") or "")
        if org in wanted and parent:
            mapping[org] = parent
    return mapping


def rekey(index: dict[str, dict], parent_of: dict[str, str], enheter: set[str] | None = None) -> tuple[dict[str, dict], dict[str, int]]:
    merged: dict[str, dict] = {}
    stats = {"input": len(index), "mapped_to_parent": 0, "already_parent": 0, "unmapped": 0}
    for org, entry in index.items():
        parent = parent_of.get(org)
        if parent:
            stats["mapped_to_parent"] += 1
        elif enheter is not None and org in enheter:
            parent = org
            stats["already_parent"] += 1
        else:
            stats["unmapped"] += 1
            continue
        target = merged.setdefault(parent, {"organisation_number": parent, "employer_name": entry.get("employer_name"), "homepages": [], "contact_email_domains": [], "ads": [], "sub_units": []})
        target["sub_units"] = sorted(set(target["sub_units"]) | {org})
        for key in ("homepages", "contact_email_domains"):
            target[key] = sorted(set(target[key]) | set(entry.get(key) or []))
        seen = {ad.get("uuid") for ad in target["ads"]}
        for ad in entry.get("ads") or []:
            if ad.get("uuid") not in seen:
                target["ads"].append({**ad, "sub_unit_organisation_number": org})
                seen.add(ad.get("uuid"))
    return merged, stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True)
    parser.add_argument("--underenheter", required=True, help="Brreg sub-unit bulk JSON (gzip)")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    index = {}
    for line in Path(args.index).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            index[str(row["organisation_number"])] = row
    parent_of = load_parent_map(args.underenheter, set(index))
    merged, stats = rekey(index, parent_of)
    out = Path(args.output)
    out.write_text("".join(json.dumps(merged[k], ensure_ascii=False, separators=(",", ":")) + "\n" for k in sorted(merged)), encoding="utf-8")
    meta_src = Path(args.index + ".meta.json")
    meta = json.loads(meta_src.read_text(encoding="utf-8")) if meta_src.exists() else {}
    meta.update({"rekeyed_to_parent": True, "parents": len(merged), **stats})
    Path(str(out) + ".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps({"parents": len(merged), **stats}))


if __name__ == "__main__":
    main()
