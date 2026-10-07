from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.external.external_footprint import policy_is_approved  # noqa: E402
from scripts.analysis.build_audit_review import build_review_page  # noqa: E402
from scripts.analysis.build_evidence_packs import build_pack  # noqa: E402
from scripts.analysis.build_observation_audit import export_audit, merge_audit  # noqa: E402
from scripts.analysis.evaluate_external_footprint import policy_check  # noqa: E402
from scripts.run.enforce_retention import enforce_retention  # noqa: E402
from scripts.transform.build_verified_observations import guard_social_links  # noqa: E402


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class LiveRunAuditTests(unittest.TestCase):
    def test_address_fields_incremental_export_and_objective_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profiles = root / "profiles.jsonl"
            observations = root / "observations.jsonl"
            labels = root / "labels.jsonl"
            worksheet = root / "audit.csv"
            drafts = root / "drafts.jsonl"
            write_jsonl(profiles, [{
                "organisation_number": "928710130", "name": "VEMUNDVIK BRYGGE AS", "website": "https://vemundvik.no",
                "evidence": {"registry_live": {"value": {"forretningsadresse.adresse": "Gåsnesvegen 7", "forretningsadresse.postnummer": "7810", "forretningsadresse.poststed": "NAMSOS", "telefon": "12345678", "epostadresse": "info@example.no"}}},
            }])
            rows = [
                {"id": "site", "organisation_number": "928710130", "platform": "company_site", "signal_type": "company_profile", "source_url": "https://vemundvik.no", "exact_entity": True},
                {"id": "social", "organisation_number": "928710130", "platform": "facebook", "signal_type": "profile_handle", "source_url": "https://facebook.com/example", "website_source_url": "https://vemundvik.no"},
            ]
            write_jsonl(observations, rows)
            write_jsonl(labels, [{"id": "site", "exact_entity": True, "metric_correct": True, "labeler": "owner"}])
            annotation = root / "annotations.jsonl"
            write_jsonl(annotation, [{"organisation_number": "928710130", "outcome": "official_site", "domain": "vemundvik.no"}])
            result = export_audit(rows, [json.loads(profiles.read_text())], worksheet, labels_path=labels, annotation_path=annotation, drafts_output=drafts)
            self.assertEqual(result["excluded_labelled"], 1)
            with worksheet.open(newline="", encoding="utf-8") as handle:
                exported = list(csv.DictReader(handle))
            self.assertEqual([row["id"] for row in exported], ["social"])
            self.assertEqual(exported[0]["registry_address"], "Gåsnesvegen 7, 7810, NAMSOS")
            self.assertEqual(exported[0]["assistant_draft_exact_entity"], "")

    def test_merge_accumulates_jsonl_labels_and_owner_format(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            incoming = root / "labels.jsonl"
            output = root / "all-labels.jsonl"
            write_jsonl(output, [{"id": "old", "exact_entity": True, "metric_correct": True, "labeler": "owner"}])
            write_jsonl(incoming, [{"id": "new", "exact_entity": True, "metric_correct": True, "labeler": "owner"}])
            result = merge_audit(incoming, output, labeler="owner", labeled_at="2026-10-08T00:00:00Z")
            self.assertEqual(result["labels"], 2)
            self.assertEqual({row["id"] for row in [json.loads(line) for line in output.read_text().splitlines()]}, {"old", "new"})

    def test_akademiet_handles_are_quarantined(self) -> None:
        profile = {"name": "NORGES REALFAGSGYMNAS SANDVIKA AS"}
        links = [
            {"platform": "facebook", "url": "https://facebook.com/privatistognettstudier"},
            {"platform": "facebook", "url": "https://facebook.com/akademietutveksling"},
        ]
        survivors, suppressed = guard_social_links(profile, links)
        self.assertEqual(survivors, [])
        self.assertEqual({item["reason"] for item in suppressed}, {"ambiguous_handle"})

    def test_evidence_pack_does_not_write_human_verdicts_and_marks_blocked_source(self) -> None:
        row = {"id": "row", "organisation_number": "1", "registry_name": "Example AS", "registry_address": "Street 1", "platform": "facebook", "signal_type": "profile_handle", "source_url": "https://facebook.com/example", "found_on_url": "https://example.no", "verified_site_domain": "example.no"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def fake_fetch(url: str, **_kwargs):
                return {"status": "fetch_blocked", "requested_url": url, "final_url": url, "excerpt": "blocked"} if "facebook" in url else {"status": "available", "requested_url": url, "final_url": url, "title": "Example", "links": [], "excerpt": "Example AS"}
            with patch("scripts.analysis.build_evidence_packs._fetch_url", side_effect=fake_fetch):
                pack = build_pack(row, root, screenshots=False)
            self.assertEqual(pack["sources"][0]["status"], "fetch_blocked")
            self.assertEqual(pack["human_verdicts"]["exact_entity"], "")

    def test_review_page_is_offline_and_exports_owner_labels(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            worksheet = root / "audit.csv"
            worksheet.write_text("id,organisation_number,registry_name,registry_address,platform,signal_type,source_url,exact_entity,metric_correct\nrow,1,Example AS,Street,company_site,company_profile,https://example.no,,,\n", encoding="utf-8")
            (root / "row").mkdir()
            (root / "row" / "evidence.json").write_text(json.dumps({"status": "available", "sources": []}), encoding="utf-8")
            output = root / "review.html"
            build_review_page(worksheet, root, output)
            text = output.read_text(encoding="utf-8")
            self.assertIn("localStorage", text)
            self.assertIn("Export labels", text)
            self.assertIn("labeler:'owner'", text)
            self.assertIn("Unsure", text)


class RetentionTests(unittest.TestCase):
    def test_retention_removes_expired_connector_data_but_protects_labels(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "proxy").mkdir(parents=True)
            write_jsonl(root / "proxy" / "observations.jsonl", [
                {"id": "old-site", "platform": "company_site", "retrieved_at": "2026-08-01T00:00:00Z"},
                {"id": "old-youtube", "platform": "youtube", "retrieved_at": "2026-08-01T00:00:00Z"},
                {"id": "inactive-nav", "platform": "job_board", "signal_type": "job_posting", "active": False, "retrieved_at": "2026-10-07T00:00:00Z"},
            ])
            labels = root / "proxy" / "observation-labels.jsonl"
            labels.write_text('{"id":"old-site","exact_entity":true}\n', encoding="utf-8")
            report = enforce_retention(root, now=datetime(2026, 10, 8, tzinfo=timezone.utc))
            remaining = [json.loads(line) for line in (root / "proxy" / "observations.jsonl").read_text().splitlines() if line]
            self.assertEqual(remaining, [])
            self.assertTrue(labels.exists())
            self.assertEqual(report["removed_rows"], 3)

    def test_policy_approval_is_not_changed_by_simulation(self) -> None:
        item = {"id": "x", "connector_id": "google_places_api", "platform": "google_places", "acquisition_mode": "official_api", "rights_status": "review_required"}
        self.assertFalse(policy_is_approved(item))
        policy = json.loads((ROOT / "config/connector-policy.json").read_text(encoding="utf-8"))
        places = next(row for row in policy["connectors"] if row["connector_id"] == "google_places_api")
        self.assertEqual(places["rights_status"], "review_required")

    def test_simulated_policy_check_is_in_memory_only(self) -> None:
        item = {"id": "x", "connector_id": "google_places_api", "platform": "google_places", "acquisition_mode": "official_api", "rights_status": "review_required"}
        real, _ = policy_check([item], ROOT / "config/connector-policy.json")
        simulated, _ = policy_check([item], ROOT / "config/connector-policy.json", simulate_approved=True)
        self.assertFalse(real)
        self.assertTrue(simulated)
        self.assertEqual(
            json.loads((ROOT / "config/connector-policy.json").read_text(encoding="utf-8"))["connectors"][3]["rights_status"],
            "review_required",
        )

    def test_empty_batch_report_is_loudly_missing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profiles = root / "profiles.jsonl"
            profiles.write_text('{"organisation_number":"1","evidence":{}}\n', encoding="utf-8")
            for name, body in {
                "external.json": {"coverage": {}, "fresh_coverage": 0, "published_audited": 0},
                "batch.json": {}, "resume.json": {}, "refresh.json": {"qualification_passed": True, "evidence_complete": True, "idempotent_rerun": True},
                "research.json": {}, "ux.json": {"score": 0},
            }.items():
                (root / name).write_text(json.dumps(body), encoding="utf-8")
            output = root / "score.json"
            command = [sys.executable, str(ROOT / "scripts/analysis/score_competition_v3.py"), "--profiles", str(profiles), "--external-report", str(root / "external.json"), "--batch-report", str(root / "batch.json"), "--resume-report", str(root / "resume.json"), "--refresh-report", str(root / "refresh.json"), "--research-report", str(root / "research.json"), "--ux-report", str(root / "ux.json"), "--output", str(output)]
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(report["qualification_gates"]["terminal_batch_contract"])
            self.assertIn("missing required fields", report["gate_details"]["terminal_batch_contract"])


if __name__ == "__main__":
    unittest.main()
