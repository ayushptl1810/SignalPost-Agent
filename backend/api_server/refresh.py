from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Database


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RefreshManager:
    """Small in-process queue for the first live deployment.

    The database is the source of truth for job state. A durable queue can replace
    this executor later without changing the HTTP contract.
    """

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="signalpost-refresh")
        self.lock = threading.Lock()
        self.active: dict[str, Future[None]] = {}

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def submit(self, organisation_number: str) -> tuple[str, bool]:
        with self.lock:
            active = self.database.active_run_for(organisation_number)
            if active:
                return str(active["run_id"]), False
            run_id = f"live-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
            self.database.create_run(run_id, organisation_number)
            future = self.executor.submit(self._run, organisation_number, run_id)
            self.active[organisation_number] = future
            future.add_done_callback(lambda _future: self._finished(organisation_number))
            return run_id, True

    def _finished(self, organisation_number: str) -> None:
        with self.lock:
            self.active.pop(organisation_number, None)

    def _run(self, organisation_number: str, run_id: str) -> None:
        started_at = utc_iso()
        self.database.update_run(run_id, status="running", started_at=started_at)
        with tempfile.TemporaryDirectory(prefix="signalpost-refresh-") as temp_dir:
            directory = Path(temp_dir)
            input_path = directory / "organisations.jsonl"
            output_path = directory / "envelopes.jsonl"
            profiles_path = directory / "profiles.jsonl"
            report_path = directory / "run-report.json"
            input_path.write_text(json.dumps({"organisation_number": organisation_number}) + "\n", encoding="utf-8")
            command = [
                sys.executable,
                str(self.settings_path / "scripts/run/run_competition_batch.py"),
                "--organisations", str(input_path),
                "--bulk", str(self.settings.registry_snapshot),
                "--output", str(output_path),
                "--profiles-output", str(profiles_path),
                "--report", str(report_path),
                "--run-id", run_id,
            ]
            if self.settings.cache_path:
                command.extend(["--cache", str(self.settings.cache_path)])
            if self.settings.nav_index:
                command.extend(["--nav-index", str(self.settings.nav_index)])
            environment = os.environ.copy()
            environment.pop("DATABASE_URL", None)
            environment.pop("NEON_CONNECTION_STRING", None)
            try:
                result = subprocess.run(
                    command,
                    cwd=self.settings_path,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=self.settings.refresh_timeout_seconds,
                    check=False,
                )
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or "runner exited unsuccessfully")[-1200:]
                    self.database.update_run(run_id, status="failed", completed_at=utc_iso(), error={"kind": "runner_failed", "detail": detail})
                    return
                envelopes = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if not envelopes:
                    raise RuntimeError("runner produced no envelope")
                for envelope in envelopes:
                    self.database.upsert_envelope(envelope)
                self.database.update_run(run_id, status="completed", completed_at=utc_iso())
            except subprocess.TimeoutExpired:
                self.database.update_run(run_id, status="failed", completed_at=utc_iso(), error={"kind": "runner_timeout"})
            except Exception as exc:  # Preserve failure state without exposing internals to the caller.
                self.database.update_run(run_id, status="failed", completed_at=utc_iso(), error={"kind": type(exc).__name__})

    @property
    def settings_path(self) -> Path:
        return Path(__file__).resolve().parents[1]
