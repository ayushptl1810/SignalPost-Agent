from __future__ import annotations

import csv
import io
import json
import os
import signal
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from norway_company_agent.registry.batch import validate_envelopes  # noqa: E402
from norway_company_agent.core.evidence import evidence  # noqa: E402
from scripts.run import run_competition_batch as batch  # noqa: E402


def _profile(org: str) -> dict:
    return {
        "organisation_number": org,
        "name": f"Company {org} AS",
        "evidence": {
            "registry": {"status": "available", "source_url": "https://registry.test", "retrieved_at": "2026-10-10T00:00:00Z", "value": {"organisation_number": org}},
            "website": evidence("website", "available", "html", "https://company.test", value={"final_url": "https://company.test"}),
        },
    }


class ProvisionalOutputTests(unittest.TestCase):
    def test_snapshot_contains_finished_and_run_incomplete_fallback_rows(self) -> None:
        inputs = [{"organisation_number": "1"}, {"organisation_number": "2"}, {"organisation_number": "3"}]
        profiles = {str(index): _profile(str(index)) for index in range(1, 4)}
        ordered, envelopes, validation = batch.build_output_snapshot(
            inputs,
            {"1": profiles["1"]},
            profiles,
            ["registry", "website"],
            run_id="checkpoint",
            started_at="2026-10-10T00:00:00Z",
            completed_at="2026-10-10T00:00:01Z",
        )
        self.assertEqual(len(ordered), 3)
        self.assertEqual(len(envelopes), 3)
        self.assertTrue(validation["passed"], validation)
        self.assertEqual(envelopes[0]["modules"]["website"]["state"], "complete")
        self.assertEqual(envelopes[1]["modules"]["website"]["state"], "submission_error")
        self.assertEqual(envelopes[2]["profile"]["errors"][-1]["kind"], "run_incomplete")

    def test_atomic_snapshot_is_validator_clean(self) -> None:
        with TemporaryDirectory() as directory:
            target = Path(directory) / "envelopes.jsonl"
            batch.write_jsonl(target, [{"organisation_number": "1", "state": "complete"}])
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["organisation_number"], "1")
            self.assertFalse(target.with_suffix(target.suffix + ".tmp").exists())

    def test_sigterm_keeps_finished_and_fallback_envelopes(self) -> None:
        organisations = ["923609016", "971686294"]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "organisations.jsonl"
            input_path.write_text("".join(json.dumps({"organisation_number": org}) + "\n" for org in organisations), encoding="utf-8")
            bulk = root / "snapshot.csv"
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=["organisasjonsnummer", "navn", "organisasjonsform.kode"])
            writer.writeheader()
            for index, org in enumerate(organisations):
                writer.writerow({"organisasjonsnummer": org, "navn": f"Company {index} AS", "organisasjonsform.kode": "AS"})
            bulk.write_text(output.getvalue(), encoding="utf-8")
            envelopes_path = root / "envelopes.jsonl"
            profiles_path = root / "profiles.jsonl"
            report_path = root / "report.json"
            argv = [
                "run_competition_batch.py", "--organisations", str(input_path), "--bulk", str(bulk),
                "--output", str(envelopes_path), "--profiles-output", str(profiles_path), "--report", str(report_path),
                "--modules", "registry,roles", "--discovery", "off", "--run-budget-seconds", "0", "--workers", "1",
                "--checkpoint-every", "1",
            ]

            def slow_official(*_args, **_kwargs):
                time.sleep(1.0)
                return {}, []

            def stop_process() -> None:
                time.sleep(0.15)
                os.kill(os.getpid(), signal.SIGTERM)

            stopper = threading.Thread(target=stop_process)
            with patch.object(sys, "argv", argv), patch.object(batch, "network_preflight", side_effect=batch.NetworkPreflightError("offline fixture")), patch.object(batch, "fetch_official_modules", side_effect=slow_official):
                stopper.start()
                with self.assertRaises(SystemExit) as raised:
                    batch.main()
                stopper.join(timeout=2.0)
            self.assertEqual(raised.exception.code, 0)
            envelopes = [json.loads(line) for line in envelopes_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(envelopes), len(organisations))
            self.assertTrue(validate_envelopes(envelopes, len(organisations))["passed"])
            run_report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(run_report["aborted"]["signal"], "SIGTERM")
            self.assertEqual(run_report["aborted"]["fallback_profiles"], len(organisations))

    def test_post_discovery_exception_still_writes_the_batch(self) -> None:
        organisation = "923609016"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "organisations.jsonl"
            input_path.write_text(json.dumps({"organisation_number": organisation}) + "\n", encoding="utf-8")
            bulk = root / "snapshot.csv"
            bulk.write_text("organisasjonsnummer,navn,organisasjonsform.kode\n923609016,Company AS,AS\n", encoding="utf-8")
            previous = root / "previous.jsonl"
            previous.write_text(json.dumps(_profile(organisation)) + "\n", encoding="utf-8")
            output = root / "envelopes.jsonl"
            profiles = root / "profiles.jsonl"
            report = root / "report.json"
            argv = [
                "run_competition_batch.py", "--organisations", str(input_path), "--bulk", str(bulk),
                "--output", str(output), "--profiles-output", str(profiles), "--report", str(report),
                "--modules", "registry", "--discovery", "off", "--run-budget-seconds", "0",
                "--previous-profiles", str(previous),
            ]
            with patch.object(sys, "argv", argv), patch.object(batch, "network_preflight", side_effect=batch.NetworkPreflightError("offline fixture")), patch.object(batch, "diff_datasets", side_effect=RuntimeError("injected diff crash")):
                with self.assertRaises(SystemExit) as raised:
                    batch.main()
            self.assertEqual(raised.exception.code, 0)
            envelopes = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(validate_envelopes(envelopes, 1)["passed"])
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["aborted"]["exception"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
