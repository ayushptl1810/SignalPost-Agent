from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.run import run_competition_batch as batch  # noqa: E402


class RuntimeDefaultsTests(unittest.TestCase):
    def test_search_fill_is_default_on_with_explicit_off_controls(self) -> None:
        self.assertTrue(batch.resolve_search_fill_enabled(None, {}))
        self.assertTrue(batch.resolve_search_fill_enabled(True, {}))
        self.assertFalse(batch.resolve_search_fill_enabled(False, {}))
        self.assertFalse(batch.resolve_search_fill_enabled(None, {"SIGNALPOST_SEARCH_FILL": "0"}))
        self.assertFalse(batch.resolve_search_fill_enabled(True, {"SIGNALPOST_SEARCH_FILL": "0"}))

    def test_name_postcode_is_default_on_with_explicit_off_controls(self) -> None:
        self.assertTrue(batch.resolve_name_postcode_enabled(False, {}))
        self.assertFalse(batch.resolve_name_postcode_enabled(False, {"SIGNALPOST_G4_NAME_POSTCODE": "0"}))
        self.assertFalse(batch.resolve_name_postcode_enabled(True, {"SIGNALPOST_G4_NAME_POSTCODE": "1"}))

    def test_name_variants_are_opt_in(self) -> None:
        self.assertFalse(batch.resolve_name_variants_enabled(False, {}))
        self.assertTrue(batch.resolve_name_variants_enabled(True, {}))
        self.assertTrue(batch.resolve_name_variants_enabled(False, {"SIGNALPOST_G4_NAME_VARIANTS": "1"}))

    def test_registry_floor_is_default_on_with_explicit_off_controls(self) -> None:
        self.assertTrue(batch.resolve_registry_floor_enabled(False, {}))
        self.assertFalse(batch.resolve_registry_floor_enabled(False, {"SIGNALPOST_G4_REGISTRY_FLOOR": "0"}))
        self.assertFalse(batch.resolve_registry_floor_enabled(True, {"SIGNALPOST_G4_REGISTRY_FLOOR": "1"}))

    def test_run_budget_defaults_and_overrides_keep_the_source(self) -> None:
        self.assertEqual(batch.resolve_run_budget(None, None), (1500.0, "default"))
        self.assertEqual(batch.resolve_run_budget(None, "900"), (900.0, "env"))
        self.assertEqual(batch.resolve_run_budget(0.0, "900"), (None, "flag"))
        self.assertEqual(batch.resolve_run_budget(None, "-5"), (None, "env"))

    def test_network_workers_are_independent_of_cpu_count(self) -> None:
        with patch.object(batch.os, "cpu_count", return_value=2):
            self.assertEqual(batch.runtime_defaults(), {"cpu_count": 2, "workers": 16, "discovery_processes": 8})
        with patch.object(batch.os, "cpu_count", return_value=32):
            self.assertEqual(batch.runtime_defaults(), {"cpu_count": 32, "workers": 16, "discovery_processes": 8})


if __name__ == "__main__":
    unittest.main()
