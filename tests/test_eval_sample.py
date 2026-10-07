from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.run.make_eval_batch import make_batch  # noqa: E402
from scripts.run.select_eval_sample import (  # noqa: E402
    assign_stratum,
    combine_extension_manifest,
    draw_extension,
    draw_sample,
    split_for_rank,
)


def record(org: int, *, form: str = "AS", employees: int | None = None, industry: str = "47.110", website: str = "") -> dict:
    return {
        "organisation_number": str(org), "name": f"Co {org}", "legal_form": form, "municipality": "OSLO",
        "industry_code": industry, "employees": employees, "website": website,
    }


def population() -> list[dict]:
    rows, org = [], 100_000_000
    spec = [("AS", None, "47.110", 300), ("AS", None, "68.209", 200), ("AS", None, "64.200", 150), ("AS", None, "00.000", 50),
            ("AS", 8, "47.110", 150), ("ASA", 40, "47.110", 90), ("AS", 250, "47.110", 40), ("BRL", None, "68.320", 120)]
    for form, employees, industry, count in spec:
        for _ in range(count):
            rows.append(record(org, form=form, employees=employees, industry=industry))
            org += 1
    return rows


class StratumTests(unittest.TestCase):
    def test_strata_follow_the_plan(self) -> None:
        self.assertEqual(assign_stratum(record(1)), "S1")
        self.assertEqual(assign_stratum(record(1, industry="68.209")), "S2")
        self.assertEqual(assign_stratum(record(1, industry="64.200")), "S3")
        self.assertEqual(assign_stratum(record(1, industry="00.000")), "S3")
        self.assertEqual(assign_stratum(record(1, employees=8)), "S4")
        self.assertEqual(assign_stratum(record(1, form="ASA", employees=40)), "S5")
        self.assertEqual(assign_stratum(record(1, employees=250)), "S6")
        self.assertEqual(assign_stratum(record(1, form="BRL")), "S7")


class DrawTests(unittest.TestCase):
    def test_draw_is_deterministic_and_seed_sensitive(self) -> None:
        first = draw_sample(population(), seed=1)
        self.assertEqual(first, draw_sample(population(), seed=1))
        self.assertNotEqual([item["organisation_number"] for item in first], [item["organisation_number"] for item in draw_sample(population(), seed=2)])

    def test_allocation_and_weights(self) -> None:
        manifest = draw_sample(population(), seed=1)
        counts = Counter(item["stratum"] for item in manifest)
        self.assertEqual(counts, Counter({"S1": 100, "S2": 40, "S3": 40, "S4": 80, "S5": 60, "S6": 30, "S7": 50}))
        s1 = next(item for item in manifest if item["stratum"] == "S1")
        self.assertEqual(s1["population_n"], 300)
        self.assertEqual(s1["weight"], 3.0)
        self.assertAlmostEqual(sum(item["weight"] for item in manifest if item["stratum"] == "S3"), 200, delta=0.1)

    def test_every_stratum_is_split_60_20_20_and_pilot_is_development_only(self) -> None:
        manifest = draw_sample(population(), seed=1)
        for stratum in ("S1", "S4", "S7"):
            splits = Counter(item["split"] for item in manifest if item["stratum"] == stratum)
            size = sum(splits.values())
            self.assertEqual(splits["development"], round(0.6 * size))
            self.assertEqual(splits["held_out"] + splits["validation"], size - round(0.6 * size))
        pilot = [item for item in manifest if item["pilot"]]
        self.assertEqual(Counter(item["stratum"] for item in pilot), Counter({"S1": 30, "S4": 30}))
        self.assertTrue(all(item["split"] == "development" for item in pilot))

    def test_small_strata_are_taken_whole_without_error(self) -> None:
        rows = [record(i) for i in range(5)]
        manifest = draw_sample(rows, seed=1)
        s1 = [item for item in manifest if item["stratum"] == "S1"]
        self.assertEqual(len(s1), 5)
        self.assertEqual(s1[0]["weight"], 1.0)

    def test_split_boundaries(self) -> None:
        self.assertEqual([split_for_rank(rank, 10) for rank in range(10)], ["development"] * 6 + ["held_out"] * 2 + ["validation"] * 2)

    def test_extension_is_deterministic_balanced_and_disjoint(self) -> None:
        base = draw_sample(population(), seed=1)
        allocation = {"S4": 4, "S5": 6, "S6": 4}
        extension = draw_extension(population(), base, seed=2, allocation=allocation)
        self.assertEqual(extension, draw_extension(population(), base, seed=2, allocation=allocation))
        self.assertEqual(len(extension), sum(allocation.values()))
        self.assertEqual(set(item["organisation_number"] for item in extension) & set(item["organisation_number"] for item in base), set())
        self.assertEqual(Counter(item["split"] for item in extension), Counter({"held_out": 7, "validation": 7}))
        self.assertTrue(all(item["source"] == "extension" for item in extension))

    def test_combined_extension_reweights_only_extended_strata(self) -> None:
        base = draw_sample(population(), seed=1)
        extension = draw_extension(population(), base, seed=2, allocation={"S4": 4, "S5": 6, "S6": 4})
        combined = combine_extension_manifest(base, extension)
        for stratum in ("S4", "S5", "S6"):
            rows = [item for item in combined if item["stratum"] == stratum]
            self.assertAlmostEqual(sum(item["weight"] for item in rows), rows[0]["population_n"], delta=0.1)
        self.assertEqual(
            [item for item in combined if item["stratum"] == "S1"],
            [dict(item, source="original") for item in base if item["stratum"] == "S1"],
        )

    def test_make_eval_batch_preserves_split_metadata(self) -> None:
        manifest = [
            {"organisation_number": "1", "split": "development", "pilot": True},
            {"organisation_number": "2", "split": "held_out", "pilot": False, "source": "extension"},
        ]
        self.assertEqual(
            make_batch(manifest, split="development"),
            [{"organisation_number": "1", "evaluation_split": "development", "sample_slice": "pilot"}],
        )
        self.assertEqual(
            make_batch(manifest, split="all"),
            [
                {"organisation_number": "1", "evaluation_split": "development", "sample_slice": "pilot"},
                {"organisation_number": "2", "evaluation_split": "held_out", "sample_slice": "extension"},
            ],
        )
        self.assertEqual(make_batch(manifest, split="all", offset=1, limit=1)[0]["organisation_number"], "2")


if __name__ == "__main__":
    unittest.main()
