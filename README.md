# Signalpost reference agent

This repository is a reproducible, evidence-first runner for the Signalpost
company-research challenge. The organisation number from the Brønnøysund
registry is the identity anchor. Website discovery publishes only after the
existing identity, first-party, different-organisation-number and public-URL
checks pass.

**Layout:** the agent lives in [`backend/`](backend/). Run every command below
from that directory (`cd backend`). The repository root is reserved for project
notes and a separate UI.

## Starter-shaped run

Python 3.12.x and `uv` are required. The evaluator supplies the organisation
list; a local run uses the frozen Brreg bulk snapshot and the dated parent-keyed
NAV index committed under `backend/data/`.

```bash
cd backend
uv sync
uv run python scripts/run/run_competition_batch.py \
  --organisations <organisations.jsonl> \
  --bulk data/brreg-enheter.csv \
  --profiles-output out/profiles.jsonl \
  --output out/envelopes.jsonl \
  --report out/run-report.json \
  --run-id local-001 \
  --expected-count 100 \
  --discovery g4 \
  --envelope-shape starter
```

Minimal pasteable command (the evaluator supplies the batch; every other option has a default: outputs go to `out/`, the run id is a timestamp, the expected count is whatever the batch file contains, and the Brreg registry snapshot is downloaded from Brreg open data when `--bulk` / `SIGNALPOST_REGISTRY_SNAPSHOT` is not provided):

```bash
cd backend && uv sync && uv run python scripts/run/run_competition_batch.py --organisations <organisations.jsonl>
```

Pasteable single-line command with every option spelled out:

```bash
cd backend && uv run python scripts/run/run_competition_batch.py --organisations <organisations.jsonl> --bulk data/brreg-enheter.csv --profiles-output out/profiles.jsonl --output out/envelopes.jsonl --report out/run-report.json --run-id local-001 --expected-count 100 --discovery g4
```

The command emits one starter-shaped terminal envelope per input, an ordered
profiles JSONL, and a report containing requests, bytes, latency percentiles,
discovery runtime and seconds per company. In default `starter` mode the rich
envelopes are also written to `out/envelopes.full.jsonl`; the primary file
stays evaluator-minimal. `--envelope-shape full` (or
`SIGNALPOST_ENVELOPE_SHAPE=full`) keeps the full contract on the primary path
and does not create a sidecar. `--resume` reuses complete profile rows;
`--previous-profiles` and `--changes-output` enable material-change reporting.
For ad-hoc larger batches, use `--shard-index N --shard-count M`. The official
default is G4 live discovery; `--discovery off` retains the registry-site-only
path and `--discovery g3` selects the stricter older publication gate.
The audited name-derived `.no` plus registered-postcode G4 rule is enabled by
default. Pass `--no-g4-name-postcode`, or set
`SIGNALPOST_G4_NAME_POSTCODE=0`, to disable it. The rule still requires the
existing identity, first-party, SSRF, robots, directory, related-site, branch,
sports-name, organisation-number and domain-uniqueness safeguards; without the
company's own organisation number, the candidate's `.no` label must be the
exact slug of its legal name with the legal-form suffix removed (hyphens are
allowed, extra domain tokens are not).

The primary starter envelope has exactly seven top-level keys and the eight
starter module/profile/evidence key sets documented in
[`backend/OUTPUT_CONTRACT.md`](backend/OUTPUT_CONTRACT.md). The API and
frontend accept either shape; the full sidecar is an internal read source, not
the evaluator submission.

Discovery is bounded by `--company-timeout` (30 seconds by default), a global
run budget (1,500 seconds by default; `--run-budget-seconds` or
`SIGNALPOST_RUN_BUDGET_SECONDS`, zero disables it), and the safe opener's
connect/fetch limits. Discovery uses 8 worker processes and 16 network threads
by default, independent of the CPU count (`SIGNALPOST_DISCOVERY_PROCESSES`,
`SIGNALPOST_WORKERS`). Envelopes are written at checkpoints and on SIGTERM/SIGINT,
so every input always has a valid row. At 80% of the global
budget new discovery stops and remaining rows receive a `run budget exhausted`
website failure; the batch still emits every row. Network access is
rate-limited per host and follows robots and Retry-After. The complete shipped
NAV index is used by default, expired ads are removed at read time, and an
index older than 14 days is marked stale rather than discarded.

The committed 100-company artifact includes a self-contained report at
[`backend/submission/smoke-100/report.html`](backend/submission/smoke-100/report.html). It has
search, sorting, keyboard-accessible company selection, side-by-side compare,
explicit unavailable areas, and source/retrieval/state details for each fact.
Regenerate it from any envelope file with:

```bash
uv run python scripts/demo/build_report.py \
  --envelopes submission/smoke-100/envelopes.jsonl \
  --output submission/smoke-100/report.html
```

## Inputs, outputs and sources

Inputs are organisation numbers and a local Brreg bulk snapshot. Outputs are
JSONL profiles, terminal envelopes, a machine-readable report, and optional
material-change JSONL. The default command uses Brønnøysund open-data
bulk/entity/roles/accounts/sub-unit endpoints, DNS, company websites through the
safe opener, and the committed parent-keyed NAV public-feed index. NAV is
rebuilt with [`backend/scripts/run/refresh_nav_index.sh`](backend/scripts/run/refresh_nav_index.sh)
before a release; the official batch does not make a live NAV call. YouTube,
Places and News are not on the default path. Candidate-only search-fill is
enabled by default when a provider key is available; without a key it is off in
effect and makes no provider call. Use `--no-search-fill` or
`SIGNALPOST_SEARCH_FILL=0` to disable it; `--search-fill` is the explicit
on-switch.

Available scored envelope claims for `official_website`, `social_profile`,
`hiring_signal`, and `dated_news` use a non-empty string `value`. Website
details remain in `value_details`; internal profiles and caches keep their
existing website object shape.

No model calls are required and no torch/transformers package is needed by the
default install. The default expected third-party cost is `$0`; search remains
capped by `SIGNALPOST_SEARCH_MAX_QUERIES` (default 1,000). Configure optional
search with comma- or whitespace-separated pools in `SERPER_API_KEY` /
`SERPER_API_KEYS`, `SERPAPI_API_KEY` / `SERPAPI_API_KEYS`, `BRAVE_API_KEY` /
`BRAVE_API_KEYS`, `BRAVE_SEARCH_API_KEY` / `BRAVE_SEARCH_API_KEYS`, or
`TAVILY_API_KEY` / `TAVILY_API_KEYS`. A single generic key can use
`SIGNALPOST_SEARCH_API_KEY` together with
`SIGNALPOST_SEARCH_PROVIDER=brave|serper|serpapi|tavily`. Search results are
candidate URLs only: the fetched company page must pass the existing identity,
first-party, robots, SSRF, directory and domain-uniqueness gates, and the search
result URL is never used as evidence. Public cache material may
be used when its source, retrieval time and staleness are declared. Secrets
belong in the environment or an ignored `.env` file, never in git. Brønnøysund
data is used under NLOD; the shipped NAV material is public-feed data under NAV's
feed terms; company pages are fetched under their published robots policy.

Every outbound URL goes through the safe opener: only public HTTP(S) targets are
allowed, redirects are checked for SSRF, robots policy is honoured, a clear
Signalpost User-Agent is sent, Retry-After is respected, and each host is
limited to one request start per second. Directory, group, brand, franchise and
related-only pages are evidence
but cannot become an exact-company claim. See
[`backend/docs/output-contract-states.md`](backend/docs/output-contract-states.md) for the
honest discovery state mapping.

## Clean-machine check

Run the reproducibility check from a clean clone at a committed revision. The
100-row smoke requires paths to the evaluator-shaped organisation list and the
Brreg bulk snapshot because those datasets are intentionally not committed:

```bash
cd backend
scripts/run/clean_machine_check.sh <commit> \
  --organisations /path/to/100-organisations.jsonl \
  --bulk /path/to/brreg-enheter.csv
```

The script clones the requested commit into a temporary directory, runs
`uv sync`, the full pytest suite, the live G4 smoke, the starter shape checker,
the full-envelope contract self-check, and the envelope validator inside an
`env -i` environment. No parent `data/` or `out/` directory is used;
only the explicitly supplied input files are mounted. It prints `PASS` only
when every step succeeds. No output, data, `.env`, virtual environment, or
secret is copied into this repository.

## Code map

Paths below are relative to `backend/`.

- `src/norway_company_agent/discovery/` — shared candidate generation, bounded
  fetch/gates, honest states and whole-batch domain constraints.
- `src/norway_company_agent/registry/` — Brreg ingestion and terminal envelopes.
- `src/norway_company_agent/web/` — safe website opener, identity and first-party gates.
- `src/norway_company_agent/external/` — NAV and other permitted source contracts.
- `scripts/run/` — official batch, cache and refresh runners.
- `scripts/analysis/` — reports, audits and gap tooling.

The default path also performs deterministic site-fact extraction after a
verified website is published: company brief, dated company-owned activity,
and company-site hiring merged with the exact-organisation NAV index. It uses
the existing safe opener, adds at most six bounded same-domain requests per
company (six seconds per fetch and twelve seconds total), and makes no model or
keyed-provider call. Website fetches share a process-wide semaphore capped at
48 concurrent requests by default (`SIGNALPOST_MAX_CONCURRENT_FETCHES`). DNS
lookups use a separate process-wide semaphore capped at 32 concurrent lookups
by default (`SIGNALPOST_MAX_CONCURRENT_DNS_LOOKUPS`); this does not change the
48-fetch cap. Reports retain discovery/activity/hiring/brief/DNS/fetch/parse
timings. The local Builderr-shaped
approximation is run with:

```bash
uv run python scripts/analysis/local_scorecard.py \
  --candidate submission/smoke-100/envelopes.jsonl \
  --pool submission/smoke-100/envelopes.jsonl
```

Run the tests with:

```bash
uv run --with pytest pytest -q
```

The full-universe precompute is deliberately not part of the official command.
About 10% of companies currently obtain a verified website in live discovery;
hiring covers only NAV-listed employers, and search is intentionally absent from
the default run. Live discovery is bounded, cache use is declared, and no
low-confidence absence is published as proof that a company has no website.
