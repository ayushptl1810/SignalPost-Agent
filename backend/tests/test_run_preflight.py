from __future__ import annotations

import csv
import gzip
import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.registry.batch import validate_envelopes  # noqa: E402
from scripts.run import run_competition_batch as batch  # noqa: E402


class NetworkPreflightBatchTests(unittest.TestCase):
    ORGS = ["923609016", "971686294", "936204724"]

    def _bulk(self, path: Path) -> None:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=["organisasjonsnummer", "navn", "organisasjonsform.kode"])
        writer.writeheader()
        for index, org in enumerate(self.ORGS):
            writer.writerow({"organisasjonsnummer": org, "navn": f"Company {index} AS", "organisasjonsform.kode": "AS"})
        with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
            handle.write(output.getvalue())

    def test_failed_preflight_warns_and_emits_three_valid_envelopes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            organisations = root / "organisations.jsonl"
            organisations.write_text("".join(json.dumps({"organisation_number": org}) + "\n" for org in self.ORGS), encoding="utf-8")
            bulk = root / "snapshot.csv.gz"
            self._bulk(bulk)
            output = root / "envelopes.jsonl"
            profiles = root / "profiles.jsonl"
            report = root / "report.json"
            argv = [
                "run_competition_batch.py", "--organisations", str(organisations), "--bulk", str(bulk),
                "--output", str(output), "--profiles-output", str(profiles), "--report", str(report),
                "--modules", "registry", "--discovery", "off", "--run-budget-seconds", "0",
                "--checkpoint-every", "1",
            ]
            stderr = io.StringIO()
            with patch.object(sys, "argv", argv), patch.object(batch, "network_preflight", side_effect=batch.NetworkPreflightError("offline fixture")), redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    batch.main()
            self.assertEqual(raised.exception.code, 0)
            self.assertIn("warning", stderr.getvalue().casefold())
            envelopes = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(envelopes), 3)
            self.assertTrue(validate_envelopes(envelopes, 3)["passed"])
            run_report = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(run_report["network_preflight"], {"passed": False, "error": "offline fixture"})
            self.assertEqual(run_report["envelope_shape"], "starter")
            self.assertEqual(run_report["full_envelope_path"], str(root / "envelopes.full.jsonl"))
            self.assertNotIn("claims", envelopes[0])
            full = json.loads((root / "envelopes.full.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertIn("claims", full)
            self.assertTrue(run_report["discovery"]["g4_name_postcode"]["enabled"])


if __name__ == "__main__":
    unittest.main()
