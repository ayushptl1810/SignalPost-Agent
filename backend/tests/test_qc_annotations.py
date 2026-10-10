from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.analysis.qc_annotations import PRIORITY_ORGANISATIONS, choose_qc_rows, cohen_kappa, merge_worksheet, worksheet_rows  # noqa: E402


def label(org: str, outcome: str, *, split: str = "development", stratum: str = "S1", domain: str | None = None) -> dict:
    return {
        "organisation_number": org,
        "name": f"Company {org}",
        "municipality": "OSLO",
        "outcome": outcome,
        "domain": domain,
        "split": split,
        "stratum": stratum,
        "source_urls": [f"https://source{org}.no"],
        "confidence": "high",
    }


class QcTests(unittest.TestCase):
    def test_priority_rows_are_first_and_deterministic(self) -> None:
        rows = [
            label(org, "undetermined" if org in {"978664407", "916329717"} else "no_site_confirmed")
            for org in ["1", *PRIORITY_ORGANISATIONS]
        ]
        first = choose_qc_rows(rows)
        second = choose_qc_rows(rows)
        self.assertEqual([row["organisation_number"] for row in first[:5]], list(PRIORITY_ORGANISATIONS))
        self.assertEqual(first, second)

    def test_export_rows_are_deterministic_and_reviewer_blind(self) -> None:
        rows = [label(str(i), "no_site_confirmed", stratum=stratum) for i, stratum in enumerate(["S1"] * 14 + ["S4"] * 14 + ["S5"] * 14)]
        rows.extend(label(str(100 + i), "official_site", split="held_out", domain="official.no") for i in range(2))
        rows.extend(label(str(200 + i), "undetermined", stratum="S5") for i in range(3))
        first = worksheet_rows(rows, {}, seed=7)
        second = worksheet_rows(rows, {}, seed=7)
        self.assertEqual(first, second)
        self.assertTrue(first)
        self.assertTrue(all(row["reviewer_verdict"] == "" and row["reviewer_domain"] == "" for row in first))
        self.assertTrue(all("outcome_v1" in row for row in first))
        self.assertTrue(any(row["outcome_v1"] == "official_site" for row in first))

    def test_merge_preserves_v1_and_marks_changes(self) -> None:
        annotations = [label("1", "official_site", domain="one.no"), label("2", "no_site_confirmed")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            annotation_path = root / "annotations.jsonl"
            worksheet_path = root / "worksheet.csv"
            output_path = root / "annotations-v2.jsonl"
            annotation_path.write_text("".join(json.dumps(row) + "\n" for row in annotations), encoding="utf-8")
            with worksheet_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["organisation_number", "reviewer_verdict", "reviewer_domain", "reviewer_notes"])
                writer.writeheader()
                writer.writerow({"organisation_number": "1", "reviewer_verdict": "official_site", "reviewer_domain": "one.no", "reviewer_notes": "confirmed"})
                writer.writerow({"organisation_number": "2", "reviewer_verdict": "official_site", "reviewer_domain": "two.no", "reviewer_notes": "found"})
            report = merge_worksheet(annotation_path, worksheet_path, output_path, qc_by="tester")
            merged = {row["organisation_number"]: row for row in (json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines())}
            self.assertEqual(merged["1"]["outcome_v1"], "official_site")
            self.assertEqual(merged["1"]["qc_status"], "confirmed")
            self.assertEqual(merged["2"]["outcome_v1"], "no_site_confirmed")
            self.assertEqual(merged["2"]["outcome"], "official_site")
            self.assertEqual(merged["2"]["qc_status"], "changed")
            self.assertEqual(report["changed_by_original_outcome"], {"no_site_confirmed": 1})
            self.assertEqual(report["agreement_rate"], 0.5)

    def test_kappa_hand_calculation(self) -> None:
        self.assertEqual(cohen_kappa(["site", "site", "no_site"], ["site", "related", "no_site"]), 0.5)


if __name__ == "__main__":
    unittest.main()
