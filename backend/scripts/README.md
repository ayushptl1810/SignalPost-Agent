# Scripts map

Use the folders below instead of searching one large flat scripts directory.

| Folder | Purpose | Examples |
| --- | --- | --- |
| `run/` | Main workflows | `run_competition_batch.py`, `run_search_discovery.py`, `run_brave_discovery.py`, `run_scrapy_websites.py` |
| `connectors/` | Optional source connectors | LinkedIn experiments, Google News, YouTube, annual reports, reviews |
| `analysis/` | Scoring and evaluation | completeness, external footprint, research-agent evaluation |
| `transform/` | Normalize or build evidence | identity gates, site activity/news, social links |
| `demo/` | Local demos and prototypes | `ask_agent.py`, `build_prototype.py` |

The competition path is:

1. `run/run_competition_batch.py` — registry baseline and terminal envelopes.
2. `run/run_search_discovery.py` — Serper candidate discovery, sitemap-aware contact/legal crawl, first-party verification, and classifier annotation.
3. `run/run_scrapy_websites.py` — permitted website crawling.
4. `transform/` — identity gates and publishable evidence.
5. `analysis/` — audit and scoring.

The discovery runner reads provider keys from comma-separated `*_API_KEYS` variables
(`SERPER_API_KEYS`, `SERPAPI_API_KEYS`, `TAVILY_API_KEYS`, `LINKUP_API_KEYS`, and
`BRAVE_API_KEYS`). `SERPER_API_KEY` remains a backwards-compatible single-key
fallback. Provider metadata and default allowances live in
`data/search-providers.json`; usage state is masked and written to the ignored
`out/provider-usage.json`.

Search uses one `"legal name" municipality` query by default. `--two-queries`
enables the organisation-number fallback only when the first query produces no
crawl candidate. Low-yield S2/S3 profiles without a registry website, non-mailbox
email domain, or NAV homepage are recorded as `search_skipped: triage`.

For evaluation, pin a provider so retrieval differences do not confound the gate:

```bash
uv run python scripts/run/run_search_discovery.py ... \
  --provider tavily --rotation priority --provider-usage out/provider-usage.json
```

Production-style runs may use the default weighted rotation. `--max-queries` and
`--max-queries-per-provider` are hard guards. Use `--cache-urls-only` when local
storage terms for titles/snippets are not confirmed; Brave is marked non-storable
and is never written to the search cache.

The runner does not persist raw search responses by default. It compares
registry contact/address fields locally and does not put those values into search
queries. Use the
deterministic `rules` classifier by default; install the optional `classifier` extra
to try the local Laya typed-decision model.

## Evaluating a discovery round

```bash
# 1. (optional, free) refresh the NAV employer index: org number -> registered homepage + active ads
uv run python scripts/connectors/run_nav_jobs_connector.py --output out/nav-employer-index.jsonl --report out/nav-report.json

# 2. run discovery. Registry/NAV/name-derived candidates run first; search only runs if they fail.
#    --no-search needs no API key. --ledger enables the negative cache (default 30 days).
uv run python scripts/run/run_search_discovery.py --input out/smoke-profiles.jsonl --output out/run-N.jsonl \
  --report out/run-N-report.json --limit 100 --ledger out/ledger.jsonl --nav-index out/nav-employer-index.jsonl

# 3. score it and compare with the previous round
python3 scripts/analysis/score_discovery_run.py --profiles out/run-N.jsonl --report out/run-N-report.json \
  --output out/scorecard-N.json --previous out/scorecard-M.json [--labels out/labels.jsonl]

# 4. label what the scorecard flags, then rescore with --labels to get measured precision
python3 scripts/analysis/review_candidates.py --scorecard out/scorecard-N.json --profiles out/run-N.jsonl \
  --labels out/labels.jsonl --also-unverified 10
```

For the frozen evaluation set, do not pass `--ledger`: a negative-cache skip is not a
valid evaluation observation. The complete development run is:

```bash
# Build the evaluator-owned registry profiles for the manifest split.
uv run python scripts/run/make_eval_batch.py --manifest out/eval-sample/manifest.jsonl \
  --split development --output out/eval-sample/development-input.jsonl
uv run python scripts/run/run_competition_batch.py \
  --organisations out/eval-sample/development-input.jsonl --bulk data/brreg-enheter.csv \
  --output out/eval-sample/development-envelopes.jsonl \
  --profiles-output out/eval-sample/development-profiles.jsonl \
  --report out/eval-sample/development-batch-report.json --run-id eval-development-20261007 \
  --expected-count 240 --modules registry,website
uv run python scripts/run/run_search_discovery.py \
  --input out/eval-sample/development-profiles.jsonl \
  --output out/eval-sample/development-discovery.jsonl \
  --report out/eval-sample/development-discovery-report.json --limit 240 \
  --count 10 --max-candidates 3 --classifier rules --nav-index out/nav-employer-index.jsonl \
  --workers 8 --company-timeout 60 --resume
uv run python scripts/analysis/score_discovery_run.py \
  --profiles out/eval-sample/development-discovery.jsonl \
  --report out/eval-sample/development-discovery-report.json \
  --annotations out/eval-sample/annotations-v1.jsonl --split development \
  --manifest out/eval-sample/manifest.jsonl \
  --output out/eval-sample/development-scorecard.json
```

The annotation-aware scorecard reports precision, its Wilson lower bound, recall,
abstention precision, weighted wrong companies per 1,000, stratum groups, errors,
and a gate. Held-out scoring must use `--final`; validation is one-look guarded:

The discovery runner checkpoints each completed company atomically in input order.
`--resume` skips rows already containing discovery evidence after an interruption.
`--workers` controls company concurrency (default 8), while every host is limited
to two in-flight requests and one request start per second. A company exceeding
`--company-timeout` is recorded as `timed_out`; it is not counted as a correct
abstention. Evaluation runs should omit `--ledger` so negative-cache skips cannot
silently become observations.

```bash
uv run python scripts/analysis/score_discovery_run.py ... --split held_out
uv run python scripts/analysis/score_discovery_run.py ... --split validation --final
```

The extension and blind QC artifacts are created with:

```bash
uv run python scripts/run/select_eval_sample.py --extension
uv run python scripts/analysis/qc_annotations.py export
```

After human review, merge a copied/completed worksheet with
`qc_annotations.py merge --qc-by NAME`; this writes `annotations-v2.jsonl` and
never overwrites v1.

The scorecard prints COVERAGE (verified sites over all companies), YIELD (over those queried),
TRUST (share of verified sites with a strong identity match, a contact/address corroborator and a
unique domain), PRECISION (only with labels, as a Wilson lower bound), cost, provider error rate, the
biggest funnel loss, suspected directory domains, and a PASS / FAIL / INSUFFICIENT_LABELS gate
(labeled precision lower bound under 0.90, or a trust drop above 5 points). Only a labeled precision
bound can actually prove accuracy; TRUST is a proxy that says "look here".

## Paid recording and offline gate replay

After credits are available, record the development run once with both caches:

```bash
uv run python scripts/run/run_search_discovery.py \
  --input out/eval-sample/development-profiles-v2.jsonl \
  --output out/eval-sample/development-discovery-recorded.jsonl \
  --report out/eval-sample/development-discovery-recorded-report.json \
  --limit 240 --workers 8 --company-timeout 60 --classifier rules \
  --nav-index out/nav-employer-index.jsonl \
  --search-cache out/eval-sample/development-search-cache.jsonl \
  --fetch-cache out/eval-sample/development-fetch-cache.jsonl
```

The search cache stores normalized result fields and the fetch cache stores the
website evidence needed for replay. Both are local artifacts under `out/` and
are git-ignored. A replay never contacts Serper or a website:

```bash
uv run python scripts/analysis/run_gate_matrix.py \
  --input out/eval-sample/development-profiles-v2.jsonl \
  --annotations out/eval-sample/annotations-v1.jsonl \
  --search-cache out/eval-sample/development-search-cache.jsonl \
  --fetch-cache out/eval-sample/development-fetch-cache.jsonl \
  --nav-index out/nav-employer-index.jsonl \
  --output-dir out/eval-sample/gate-matrix
```

This writes G0 (current), G1 (relaxed address), and G2 (exact imprint plus
municipality) scorecards and lists every flip against G0. The Istat rule is
available as G3 on the discovery runner. For a no-search replay of a recorded
fetch cache, use `--no-search --replay-only --gate g3`; it does not contact
Serper or websites. Compare two adjudicated scorecards with:

```bash
uv run python scripts/analysis/compare_gate_runs.py \
  --baseline out/eval-sample/development-scorecard-nosearch-g0-replay.json \
  --candidate out/eval-sample/development-scorecard-nosearch-g3-replay.json \
  --annotations out/eval-sample/annotations-v2-adjudicated.jsonl \
  --output out/eval-sample/development-g0-g3-comparison.json
```

A provider-fatal or incomplete run is refused by the scorer.

Once provider keys are available, the retrieval-only development bake-off is:

```bash
uv run python scripts/analysis/run_provider_bakeoff.py \
  --profiles out/eval-sample/development-profiles-v2.jsonl \
  --annotations out/eval-sample/annotations-v2-adjudicated.jsonl \
  --baseline-run out/eval-sample/development-discovery-v2.jsonl \
  --provider tavily \
  --output out/eval-sample/bakeoff-tavily.json
```

Run it separately for each pinned provider. The output reports recall at 3, 5
and 10, top-five directory share, latency, quota errors and provider usage; it
does not alter the publication gate.

Serper's [public terms](https://serper.dev/terms) prohibit mirroring the
service as-is and restrict use of returned data by third parties, but do not
explicitly answer whether a private, temporary evaluation cache is permitted.
Treat local caching as pending written confirmation from Serper; do not publish
the cache or include it in a submission.
