from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.run import run_competition_batch as batch  # noqa: E402


class RunRSSReportTests(unittest.TestCase):
    def test_max_rss_reports_self_children_and_total_in_mb(self) -> None:
        usage = [SimpleNamespace(ru_maxrss=2 * 1024 * 1024), SimpleNamespace(ru_maxrss=1024 * 1024)]
        with patch.object(batch.sys, "platform", "darwin"), patch.object(batch.resource, "getrusage", side_effect=usage):
            result = batch.max_rss_mb()
        self.assertEqual(result, {"max_rss_mb": 3.0, "self_mb": 2.0, "children_mb": 1.0})


if __name__ == "__main__":
    unittest.main()
