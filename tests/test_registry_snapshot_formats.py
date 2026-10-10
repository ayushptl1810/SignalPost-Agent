from __future__ import annotations

import csv
import gzip
import io
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.registry.sampling import iter_bulk  # noqa: E402


class RegistrySnapshotTests(unittest.TestCase):
    FIELDNAMES = ["organisasjonsnummer", "navn", "organisasjonsform.kode"]
    ROWS = [
        {"organisasjonsnummer": "923609016", "navn": "Alpha AS", "organisasjonsform.kode": "AS"},
        {"organisasjonsnummer": "971686294", "navn": "Beta AS", "organisasjonsform.kode": "AS"},
        {"organisasjonsnummer": "936204724", "navn": "Gamma AS", "organisasjonsform.kode": "AS"},
    ]

    def _csv_bytes(self) -> bytes:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=self.FIELDNAMES)
        writer.writeheader()
        writer.writerows(self.ROWS)
        return output.getvalue().encode("utf-8")

    def test_plain_and_gzip_snapshots_have_identical_rows(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plain = root / "snapshot.csv"
            compressed = root / "snapshot.csv.gz"
            plain.write_bytes(self._csv_bytes())
            compressed.write_bytes(gzip.compress(self._csv_bytes()))
            self.assertEqual(list(iter_bulk(plain)), list(iter_bulk(compressed)))


if __name__ == "__main__":
    unittest.main()
