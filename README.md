# Signalpost reference agent

This repository is a reproducible, evidence-first runner for the Signalpost
company-research challenge. The organisation number from the Brønnøysund
registry is the identity anchor. Website discovery publishes only after the
existing identity, first-party, different-organisation-number and public-URL
checks pass.

## Official-shaped run

Python 3.12+ and `uv` are required. The evaluator supplies the organisation
list; a local run uses the frozen Brreg bulk snapshot.

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

The command emits one terminal envelope per input, an ordered profiles JSONL,
and a report containing requests, bytes, latency percentiles, discovery runtime
and seconds per company. `--resume` reuses complete profile rows;
`--previous-profiles` and `--changes-output` enable material-change reporting.
For ad-hoc larger batches, use `--shard-index N --shard-count M`. The official
default is G4 live discovery; `--discovery off` retains the registry-site-only
path and `--discovery g3` selects the stricter older publication gate.

Discovery is bounded by `--company-timeout` (20 seconds by default),
`--run-budget-seconds`, and the safe opener's 5-second connect / 12-second total
fetch limits. CPU parsing uses a process pool; network access is rate-limited
per host and follows robots and Retry-After. A complete, fresh NAV index can be
passed with `--nav-index`; a missing or stale index is `not_checked`, not zero.

## Inputs, outputs and sources

Inputs are organisation numbers and a local Brreg bulk snapshot. Outputs are
JSONL profiles, terminal envelopes, a machine-readable report, and optional
material-change JSONL. Declared data sources are Brønnøysund open data, the NAV
public job feed, and YouTube Data API v3 when a key is present. Search is
optional and off by default; `--search-fill` is capped and uses Serper only when
an environment key and explicit budget are provided. Google Places and News
remain measurement/review-only connectors unless policy is changed by the owner.

No model calls are required. The default expected cost is $0 per 100 companies;
the optional Serper budget is the only declared third-party search cost. Public
cache material may be used when its source and retrieval times are declared.
Secrets belong in the environment or an ignored `.env` file, never in git.

Every outbound URL goes through the safe opener: only public HTTP(S) targets are
allowed, redirects are checked for SSRF, robots policy is honoured, the clear
Signalpost User-Agent is sent, and each host is limited to one request start per
second. Directory, group, brand, franchise and related-only pages are evidence
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
`uv sync`, the full pytest suite, the live G4 smoke, and the envelope validator.
It prints `PASS` only when every step succeeds. No output, data, `.env`, virtual
environment, or secret is copied into this repository.

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
Live discovery is bounded, cache use is declared, and no low-confidence absence
is published as proof that a company has no website.
