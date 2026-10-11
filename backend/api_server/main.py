from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from norway_company_agent.core.orgnumber import is_valid_org_number

from .config import Settings
from .db import Database
from .answers import answer_question
from .refresh import RefreshManager


class AnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


def _org_or_422(value: str) -> str:
    normalized = "".join(character for character in value if character.isdigit())
    if not is_valid_org_number(normalized):
        raise HTTPException(status_code=422, detail={"code": "invalid_organisation_number", "message": "Use a valid nine-digit Norwegian organisation number."})
    return normalized


def _database(request: Request) -> Database:
    return request.app.state.database


def _manager(request: Request) -> RefreshManager:
    return request.app.state.refresh_manager


def _require_admin(request: Request, token: str | None) -> None:
    configured = request.app.state.settings.admin_token
    if not configured:
        raise HTTPException(status_code=503, detail={"code": "refresh_not_configured", "message": "Refresh requires SIGNALPOST_ADMIN_TOKEN to be configured."})
    if not token or not secrets.compare_digest(token, configured):
        raise HTTPException(status_code=401, detail={"code": "invalid_admin_token", "message": "A valid X-Admin-Token is required."})


@asynccontextmanager
async def lifespan(application: FastAPI):
    settings = Settings.from_env()
    database = Database(settings.database_url, max_size=settings.database_pool_size)
    database.open()
    application.state.settings = settings
    application.state.database = database
    application.state.refresh_manager = RefreshManager(database, settings)
    try:
        yield
    finally:
        application.state.refresh_manager.close()
        database.close()


app = FastAPI(title="SignalPost API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(Settings.from_env().cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Admin-Token"],
)


@app.get("/api/v1/health")
def health(request: Request) -> dict[str, Any]:
    try:
        _database(request).health()
        return {"status": "ok", "database": "reachable"}
    except Exception:
        return JSONResponse({"status": "degraded", "database": "unavailable"}, status_code=503)


@app.get("/api/v1/companies")
def list_companies(
    request: Request,
    q: str | None = Query(default=None, max_length=120),
    coverage: str = Query(default="all", pattern="^(all|complete|review)$"),
    sort: str = Query(default="complete", pattern="^(complete|name|recent)$"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> dict[str, Any]:
    rows, total = _database(request).list_companies(query=q.strip() if q else None, coverage=coverage, sort=sort, limit=limit, offset=offset)
    data = [{
        "organisation_number": row["organisation_number"],
        "name": row["name"],
        "legal_name": row["legal_name"],
        "municipality": row["municipality"],
        "industry_label": row["industry_label"],
        "coverage_score": row["coverage_score"],
        "latest_run_id": row["latest_run_id"],
        "latest_checked": row["latest_checked"],
        "envelope": row["latest_envelope"],
    } for row in rows]
    return {"data": data, "meta": {"total": total, "limit": limit, "offset": offset, "has_next": offset + len(data) < total}}


@app.get("/api/v1/companies/{organisation_number}/evidence")
def company_evidence(request: Request, organisation_number: str) -> dict[str, Any]:
    org = _org_or_422(organisation_number)
    snapshot = _database(request).get_snapshot(org)
    if not snapshot:
        raise HTTPException(status_code=404, detail={"code": "company_not_found", "message": "No profile has been imported for this organisation number."})
    envelope = snapshot["envelope"]
    return {"data": {"organisation_number": org, "run_id": snapshot["run_id"], "claims": envelope.get("claims", []), "evidence": envelope.get("evidence", []), "unknown_areas": envelope.get("unknown_areas", []), "errors": envelope.get("errors", [])}}


@app.get("/api/v1/companies/{organisation_number}/changes")
def company_changes(request: Request, organisation_number: str) -> dict[str, Any]:
    org = _org_or_422(organisation_number)
    snapshot = _database(request).get_snapshot(org)
    if not snapshot:
        raise HTTPException(status_code=404, detail={"code": "company_not_found", "message": "No profile has been imported for this organisation number."})
    envelope = snapshot["envelope"]
    return {"data": {"organisation_number": org, "run_id": snapshot["run_id"], "changes": envelope.get("changes", [])}}


@app.post("/api/v1/companies/{organisation_number}/answers")
def company_answer(request: Request, organisation_number: str, payload: AnswerRequest) -> dict[str, Any]:
    org = _org_or_422(organisation_number)
    snapshot = _database(request).get_snapshot(org)
    if not snapshot:
        raise HTTPException(status_code=404, detail={"code": "company_not_found", "message": "No profile has been imported for this organisation number."})
    return {"data": {"organisation_number": org, **answer_question(payload.question, snapshot["envelope"])}}


@app.get("/api/v1/companies/{organisation_number}")
def company_profile(request: Request, organisation_number: str) -> dict[str, Any]:
    org = _org_or_422(organisation_number)
    snapshot = _database(request).get_snapshot(org)
    if not snapshot:
        raise HTTPException(status_code=404, detail={"code": "company_not_found", "message": "No profile has been imported for this organisation number."})
    return {"data": {"organisation_number": org, "run_id": snapshot["run_id"], "envelope": snapshot["envelope"]}}


@app.post("/api/v1/companies/{organisation_number}/refresh", status_code=202)
def refresh_company(request: Request, organisation_number: str, x_admin_token: str | None = Header(default=None)) -> JSONResponse:
    _require_admin(request, x_admin_token)
    org = _org_or_422(organisation_number)
    run_id, created = _manager(request).submit(org)
    return JSONResponse({"data": {"run_id": run_id, "organisation_number": org, "status": "queued" if created else "running"}}, status_code=202 if created else 200)


@app.get("/api/v1/runs/{run_id}")
def run_status(request: Request, run_id: str) -> dict[str, Any]:
    if len(run_id) > 100 or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in run_id):
        raise HTTPException(status_code=422, detail={"code": "invalid_run_id", "message": "Invalid run identifier."})
    row = _database(request).get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": "Run not found."})
    return {"data": {"run_id": row["run_id"], "organisation_number": row["organisation_number"], "status": row["status"], "requested_at": row["requested_at"], "started_at": row["started_at"], "completed_at": row["completed_at"], "error": row["error"]}}
