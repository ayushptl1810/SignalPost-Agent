from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.run import run_competition_batch as batch  # noqa: E402


def _complete(org: str) -> dict:
    return {"organisation_number": org, "evidence": {"registry": {"status": "available"}}}


class TolerantMembershipTests(unittest.TestCase):
    def test_previous_profiles_are_diffed_only_on_the_intersection(self) -> None:
        selected, overlap = batch.previous_profiles_for_batch([_complete("1"), _complete("2"), _complete("9")], ["1", "2", "3"])
        self.assertEqual([row["organisation_number"] for row in selected], ["1", "2"])
        self.assertEqual(overlap, {"current": 3, "previous": 3, "intersection": 2})

    def test_resume_ignores_prior_rows_outside_the_current_batch(self) -> None:
        state = batch.resumed_profile_state([_complete("1"), _complete("9")], ["1", "2"], ["registry"])
        self.assertEqual(sorted(state), ["1"])


if __name__ == "__main__":
    unittest.main()
