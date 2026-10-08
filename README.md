# Signalpost reference agent

This repository is a reproducible, evidence-first runner for the Signalpost
company-research challenge. The organisation number from the Brønnøysund
registry is the identity anchor. Website discovery publishes only after the
existing identity, first-party, different-organisation-number and public-URL
checks pass.

## Official-shaped run

Python 3.12.x and `uv` are required. The evaluator supplies the organisation
list; a local run uses the frozen Brreg bulk snapshot and the dated parent-keyed
NAV index committed under `data/`.

```bash
uv sync
uv run python scripts/run/run_competition_batch.py \
  --organisations <organisations.jsonl> \
  --bulk data/brreg-enheter.csv \
  --profiles-output out/profiles.jsonl \
  --output out/envelopes.jsonl \
  --report out/run-report.json \
  --run-id local-001 \
  --expected-count 100 \
  --discovery g4
```

Pasteable single-line command:

```bash
uv run python scripts/run/run_competition_batch.py --organisations <organisations.jsonl> --bulk data/brreg-enheter.csv --profiles-output out/profiles.jsonl --output out/envelopes.jsonl --report out/run-report.json --run-id local-001 --expected-count 100 --discovery g4
```

The command emits one terminal envelope per input, an ordered profiles JSONL,
and a report containing requests, bytes, latency percentiles, discovery runtime
and seconds per company. `--resume` reuses complete profile rows;
`--previous-profiles` and `--changes-output` enable material-change reporting.
For ad-hoc larger batches, use `--shard-index N --shard-count M`. The official
default is G4 live discovery; `--discovery off` retains the registry-site-only
path and `--discovery g3` selects the stricter older publication gate.

Discovery is bounded by `--company-timeout` (20 seconds by default),
`--run-budget-seconds` or `SIGNALPOST_RUN_BUDGET_SECONDS`, and the safe opener's
5-second connect / 12-second total fetch limits. CPU parsing uses up to
`min(8, os.cpu_count())` processes and `2 * processes` worker threads by
default; `SIGNALPOST_WORKERS` overrides the thread count. At 80% of a global
budget new discovery stops and remaining rows receive a `run budget exhausted`
website failure; the batch still emits every row. Network access is
rate-limited per host and follows robots and Retry-After. The complete shipped
NAV index is used by default, expired ads are removed at read time, and an
index older than 14 days is marked stale rather than discarded.

## Inputs, outputs and sources

Inputs are organisation numbers and a local Brreg bulk snapshot. Outputs are
JSONL profiles, terminal envelopes, a machine-readable report, and optional
material-change JSONL. The default command uses Brønnøysund open-data
bulk/entity/roles/accounts/sub-unit endpoints, DNS, company websites through the
safe opener, and the committed parent-keyed NAV public-feed index. NAV is
rebuilt with [`scripts/run/refresh_nav_index.sh`](scripts/run/refresh_nav_index.sh)
before a release; the official batch does not make a live NAV call. YouTube,
Places, News and search are not on the default path. Search is optional and off
by default; `--search-fill` is an explicit, capped Serper opt-in.

No model calls are required and no torch/transformers package is needed by the
default install. The default expected third-party cost is `$0`; the optional
Serper budget is the only declared paid/search cost. Public cache material may
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
[`docs/output-contract-states.md`](docs/output-contract-states.md) for the
honest discovery state mapping.

## Clean-machine check

Run the reproducibility check from a clean clone at a committed revision. The
100-row smoke requires paths to the evaluator-shaped organisation list and the
Brreg bulk snapshot because those datasets are intentionally not committed:

```bash
scripts/run/clean_machine_check.sh <commit> \
  --organisations /path/to/100-organisations.jsonl \
  --bulk /path/to/brreg-enheter.csv
```

The script clones the requested commit into a temporary directory, runs
`uv sync`, the full pytest suite, the live G4 smoke, and the envelope validator
inside an `env -i` environment. No parent `data/` or `out/` directory is used;
only the explicitly supplied input files are mounted. It prints `PASS` only
when every step succeeds. No output, data, `.env`, virtual environment, or
secret is copied into this repository.

## Code map

- `src/norway_company_agent/discovery/` — shared candidate generation, bounded
  fetch/gates, honest states and whole-batch domain constraints.
- `src/norway_company_agent/registry/` — Brreg ingestion and terminal envelopes.
- `src/norway_company_agent/web/` — safe website opener, identity and first-party gates.
- `src/norway_company_agent/external/` — NAV and other permitted source contracts.
- `scripts/run/` — official batch, cache and refresh runners.
- `scripts/analysis/` — reports, audits and gap tooling.

Run the tests with:

```bash
uv run --with pytest pytest -q
```

The full-universe precompute is deliberately not part of the official command.
About 10% of companies currently obtain a verified website in live discovery;
hiring covers only NAV-listed employers, and search is intentionally absent from
the default run. Live discovery is bounded, cache use is declared, and no
low-confidence absence is published as proof that a company has no website.
