# SignalPost live API

The live service is a small FastAPI layer over the existing SignalPost batch
runner. Neon/Postgres stores the latest company index, every run status, and
the complete source-attributed envelope for each snapshot. The runner remains
the authority for research and identity safety; the API does not invent missing
facts.

## Local setup

1. Create a Neon Postgres project and copy its pooled connection string into
   `backend/.env` as `NEON_CONNECTION_STRING` (or `DATABASE_URL`). Do not commit
   that file.
2. Copy `.env.example` to `.env` and set a long random
   `SIGNALPOST_ADMIN_TOKEN` if refreshes should be enabled.
3. From `backend/`, import the checked smoke artifact:

```bash
uv run python -m api_server.import_artifact submission/smoke-100/envelopes.jsonl
uv run uvicorn api_server.main:app --host 127.0.0.1 --port 8000
```

The frontend can use this service by setting `frontend/.env.local`:

```bash
SIGNALPOST_API_URL=http://127.0.0.1:8000
```

Restart Next.js after changing that variable. When it is unset, the UI uses the
checked JSONL adapter so a local preview still has explicit data provenance.

## HTTP contract

All responses use a small `{data, meta}` envelope where applicable. Company
identifiers are normalized and checksum-validated as Norwegian nine-digit
organisation numbers.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/v1/health` | Database reachability for deployment health checks |
| GET | `/api/v1/companies?q=&coverage=&sort=&limit=&offset=` | Search and paginate indexed companies |
| GET | `/api/v1/companies/{org}` | Latest complete envelope for one company |
| GET | `/api/v1/companies/{org}/evidence` | Claims, evidence, unknown areas, and errors |
| GET | `/api/v1/companies/{org}/changes` | Detected changes from the latest snapshot |
| POST | `/api/v1/companies/{org}/answers` | Grounded answer from that envelope only |
| POST | `/api/v1/companies/{org}/refresh` | Queue a fresh batch run; requires `X-Admin-Token` |
| GET | `/api/v1/runs/{run_id}` | Poll queued, running, completed, or failed status |

Ask request body:

```json
{"question":"What are the latest financials?"}
```

The answer endpoint returns a topic, answer text, source URLs, run ID, and
unknown areas. It is intentionally deterministic in this first deployment;
adding a model later should preserve the same evidence and abstention contract.

## Deployment notes

- Run the API as a separate service from Next.js. Next's server route proxies
  list and Ask requests so the database connection string and admin token never reach the
  browser.
- Use the Neon pooled URL for web traffic and keep the pool small; the default
  is five connections.
- Put the refresh endpoint behind a scheduler or an authenticated operator
  action. It launches the existing one-company batch runner and persists the
  resulting envelope only after the run completes.
- Configure `SIGNALPOST_CORS_ORIGINS` to the actual frontend origin in
  production. The default only allows local development origins.
- Logs intentionally retain only a short runner failure detail and error kind;
  credentials and full source payloads are not emitted by the API.
