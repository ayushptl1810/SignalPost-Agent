from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS companies (
        organisation_number TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        legal_name TEXT,
        municipality TEXT,
        industry_label TEXT,
        coverage_score INTEGER NOT NULL DEFAULT 0,
        latest_run_id TEXT,
        latest_checked TIMESTAMPTZ,
        latest_envelope JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        organisation_number TEXT,
        status TEXT NOT NULL,
        requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        error JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_snapshots (
        organisation_number TEXT NOT NULL REFERENCES companies(organisation_number) ON DELETE CASCADE,
        run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        state TEXT NOT NULL,
        completed_at TIMESTAMPTZ,
        envelope JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (organisation_number, run_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS companies_name_idx ON companies (lower(name))",
    "CREATE INDEX IF NOT EXISTS companies_municipality_idx ON companies (lower(municipality))",
    "CREATE INDEX IF NOT EXISTS companies_updated_at_idx ON companies (updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS snapshots_org_completed_idx ON company_snapshots (organisation_number, completed_at DESC)",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _profile_value(envelope: dict[str, Any], key: str) -> Any:
    profile = envelope.get("profile")
    return profile.get(key) if isinstance(profile, dict) else None


def _coverage_score(envelope: dict[str, Any]) -> int:
    claims = envelope.get("claims")
    if not isinstance(claims, list):
        return 0
    fields = {"legal_identity", "latest_annual_accounts", "leadership", "company_profiles", "hiring"}
    return sum(1 for claim in claims if isinstance(claim, dict) and claim.get("field") in fields and claim.get("availability") == "available")


def _timestamp(value: Any) -> Any:
    return value if isinstance(value, str) and value else None


class Database:
    def __init__(self, database_url: str, *, max_size: int = 5) -> None:
        if not database_url:
            raise RuntimeError("DATABASE_URL or NEON_CONNECTION_STRING is required for the live API")
        self.pool = ConnectionPool(database_url, min_size=1, max_size=max_size, kwargs={"row_factory": dict_row}, open=False)

    def open(self) -> None:
        self.pool.open(wait=True)
        with self.pool.connection() as connection:
            for statement in SCHEMA:
                connection.execute(statement)
            connection.commit()

    def close(self) -> None:
        self.pool.close()

    def health(self) -> bool:
        with self.pool.connection() as connection:
            connection.execute("SELECT 1")
            return True

    def list_companies(self, *, query: str | None, coverage: str, sort: str, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        conditions = ["TRUE"]
        params: list[Any] = []
        if query:
            pattern = f"%{query}%"
            conditions.append("(organisation_number ILIKE %s OR name ILIKE %s OR municipality ILIKE %s OR industry_label ILIKE %s)")
            params.extend([pattern, pattern, pattern, pattern])
        if coverage == "complete":
            conditions.append("coverage_score = 5")
        elif coverage == "review":
            conditions.append("coverage_score < 3")
        order_by = {
            "name": "lower(name) ASC, organisation_number ASC",
            "recent": "updated_at DESC, lower(name) ASC",
            "complete": "coverage_score DESC, lower(name) ASC",
        }[sort]
        where = " AND ".join(conditions)
        with self.pool.connection() as connection:
            total = connection.execute(f"SELECT count(*) AS count FROM companies WHERE {where}", params).fetchone()["count"]
            rows = connection.execute(
                f"SELECT organisation_number, name, legal_name, municipality, industry_label, coverage_score, latest_run_id, latest_checked, latest_envelope FROM companies WHERE {where} ORDER BY {order_by} LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
        return list(rows), int(total)

    def get_company(self, organisation_number: str) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            return connection.execute("SELECT * FROM companies WHERE organisation_number = %s", [organisation_number]).fetchone()

    def get_snapshot(self, organisation_number: str, run_id: str | None = None) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            if run_id:
                return connection.execute("SELECT * FROM company_snapshots WHERE organisation_number = %s AND run_id = %s", [organisation_number, run_id]).fetchone()
            return connection.execute("SELECT * FROM company_snapshots WHERE organisation_number = %s ORDER BY completed_at DESC NULLS LAST, created_at DESC LIMIT 1", [organisation_number]).fetchone()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            return connection.execute("SELECT * FROM runs WHERE run_id = %s", [run_id]).fetchone()

    def active_run_for(self, organisation_number: str) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            return connection.execute("SELECT * FROM runs WHERE organisation_number = %s AND status IN ('queued', 'running') ORDER BY requested_at DESC LIMIT 1", [organisation_number]).fetchone()

    def create_run(self, run_id: str, organisation_number: str) -> None:
        with self.pool.connection() as connection:
            connection.execute("INSERT INTO runs (run_id, organisation_number, status) VALUES (%s, %s, 'queued')", [run_id, organisation_number])
            connection.commit()

    def update_run(self, run_id: str, *, status: str, started_at: Any = None, completed_at: Any = None, error: dict[str, Any] | None = None) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                "UPDATE runs SET status = %s, started_at = COALESCE(%s::timestamptz, started_at), completed_at = COALESCE(%s::timestamptz, completed_at), error = COALESCE(%s::jsonb, error) WHERE run_id = %s",
                [status, _timestamp(started_at), _timestamp(completed_at), Jsonb(error) if error is not None else None, run_id],
            )
            connection.commit()

    def upsert_envelope(self, envelope: dict[str, Any]) -> None:
        organisation_number = str(envelope.get("organisation_number") or "")
        if not organisation_number:
            raise ValueError("Envelope is missing organisation_number")
        run = envelope.get("run") if isinstance(envelope.get("run"), dict) else {}
        run_id = str(envelope.get("run_id") or run.get("run_id") or "unknown-run")
        completed_at = envelope.get("completed_at") or run.get("completed_at")
        profile = envelope.get("profile") if isinstance(envelope.get("profile"), dict) else {}
        name = str(profile.get("name") or organisation_number)
        status = "completed" if envelope.get("state") in {"complete", "completed"} else str(envelope.get("state") or "completed")
        error = {"errors": envelope.get("errors", [])} if envelope.get("errors") else None
        with self.pool.connection() as connection:
            connection.execute(
                "INSERT INTO runs (run_id, organisation_number, status, started_at, completed_at, error) VALUES (%s, %s, %s, %s::timestamptz, %s::timestamptz, %s::jsonb) ON CONFLICT (run_id) DO UPDATE SET status = EXCLUDED.status, completed_at = COALESCE(EXCLUDED.completed_at, runs.completed_at), error = COALESCE(EXCLUDED.error, runs.error)",
                [run_id, organisation_number, status, _timestamp(envelope.get("started_at") or run.get("started_at")), _timestamp(completed_at), Jsonb(error) if error is not None else None],
            )
            connection.execute(
                "INSERT INTO companies (organisation_number, name, legal_name, municipality, industry_label, coverage_score, latest_run_id, latest_checked, latest_envelope) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::timestamptz, %s::jsonb) ON CONFLICT (organisation_number) DO UPDATE SET name = EXCLUDED.name, legal_name = EXCLUDED.legal_name, municipality = EXCLUDED.municipality, industry_label = EXCLUDED.industry_label, coverage_score = EXCLUDED.coverage_score, latest_run_id = EXCLUDED.latest_run_id, latest_checked = EXCLUDED.latest_checked, latest_envelope = EXCLUDED.latest_envelope, updated_at = now()",
                [organisation_number, name, profile.get("legal_name") or name, profile.get("municipality"), profile.get("industry_label"), _coverage_score(envelope), run_id, _timestamp(completed_at), Jsonb(envelope)],
            )
            connection.execute(
                "INSERT INTO company_snapshots (organisation_number, run_id, state, completed_at, envelope) VALUES (%s, %s, %s, %s::timestamptz, %s::jsonb) ON CONFLICT (organisation_number, run_id) DO UPDATE SET state = EXCLUDED.state, completed_at = EXCLUDED.completed_at, envelope = EXCLUDED.envelope",
                [organisation_number, run_id, str(envelope.get("state") or "completed"), _timestamp(completed_at), Jsonb(envelope)],
            )
            connection.commit()
