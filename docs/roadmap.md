# Points roadmap and parallel work plan

**Date:** 2026-10-07 · **Owner of this file:** planning (Claude). Codex sessions append results to their own handoff, never here.

## 1. How points are really computed (read `scripts/analysis/score_competition_v3.py`)

The proxy scorer is **gate-first**. If any of seven qualification gates fails, `awardable_score` is 0 no matter the raw score, and every external category is worth 0 unless the external report passes qualification.

| Gate | What must be true | Producer today |
|---|---|---|
| `external_audit_at_least_100` | at least 100 human-labelled external observations | **missing** (no observation audit tooling) |
| `zero_wrong_company_external_publications` | labelled published observations all have `exact_entity` true | evaluator exists; no labels |
| `external_claims_supported` | no published observation fails `validate_observation` | evaluator exists |
| `external_connector_policy` | external report `connector_policy_passed` true | **missing** (field never written) |
| `official_identity_complete` | every profile has `registry_live.organisation_number` equal to its own | needs the full official-module run |
| `terminal_batch_contract` | batch report validation passed and one envelope per input | exists; rerun with network |
| `refresh_replay` | refresh report: precision and recall at least 0.95, evidence complete, idempotent | script exists; fixture only |

Points once gates pass (external report must also have `qualification_passed`: at least 100 audited, zero wrong entity, precision at least 0.995, metric correctness at least 0.98):

- External 55: entity attribution **10 flat**; breadth 10 x share of companies with 2+ platforms; jobs 7 x share with job/workforce signals; reviews 8 x share with review/rating/place signals; buzz 7 x share with posts/mentions/profile metrics; sentiment 10 x share (separate gate); freshness 3 x `fresh_coverage` (**field never written**).
- Foundation 15: identity 4, annual accounts 4 x availability, roles+locations 4, website terminal state 3.
- Research agent 10 (capped at 5 unless `external_footprint_qa_passed`, **never written**). UX 8 (capped at 4 unless `external_intelligence_presented`, **never written**; no UX report producer exists).
- Extensibility 12: terminal daily batch 4, deterministic resume 2, measured refresh diffs 3, p95 latency under 10 s 1, connector rights and rate policy 2.

**Consequence.** The fastest route to a non-zero awardable score is to close the gates with the data we already have (verified company sites and their social handles), not to add connectors first. Connector coverage then adds points in proportion to the share of companies that have the signal. In this local proxy the denominator is all companies in the batch, so a signal that exists for 5% of companies is worth about 5% of its points; the hidden scorer reportedly measures hit rate against evaluator gold, which can be higher. Do not over-fit to the proxy; use it to find which gates and inputs are missing.

## 2. Work packages (one Codex session each; all can start now except where noted)

| ID | Spec | Unlocks | Depends on | Size |
|---|---|---|---|---|
| S1 | `handoff-search-providers.md` | free, rotating search; fewer queries | none | medium |
| S2 | `handoff-proxy-score.md` | **all gates, baseline score, observation audit, policy** | none (**start first**) | large |
| S3 | `handoff-nav-jobs.md` | jobs signal (7 pts), exact by org number | S2 policy file (soft) | medium |
| S4 | `handoff-youtube-api.md` | handles, profile metrics, posts (buzz 7 pts) | owner YouTube key | medium |
| S5 | `handoff-google-places.md` | ratings and place identity (8 pts), website candidates | owner Google key | medium |
| S6 | `handoff-daily-refresh.md` | extensibility 12 pts, material-change output, freshness | S3-S5 modules exposing `collect` (soft) | large |
| S7 | `handoff-research-ux.md` | research agent 10, UX 8 | S2 report formats | medium |
| S8 | `handoff-extension-annotation.md` | certifies website discovery on held-out and validation | S1 (soft) | labour |

**Status 2026-10-07:** S1-S7 are built and tested as code (232 tests) and their specs are archived in `out/docs-archive/`; **none was run live**. The open specs are **S8** (`handoff-extension-annotation.md`, 180 companies still unlabelled) and the new **S9** (`handoff-live-run.md`: first live measurements, audit worksheet fixes, handle-ownership guard, real and what-if proxy scores). Treat the table above as the history of who owned which files.

News mentions and sentiment are deliberately not specced yet: sentiment needs about 300 labelled snippets and has the heaviest gate; decide after S2 shows the gate status.

## 3. File ownership (avoid merge conflicts)

| Session | May edit | Must not edit |
|---|---|---|
| S1 | `scripts/run/run_search_discovery.py`, `src/norway_company_agent/web/discovery.py`, new `src/norway_company_agent/search/`, `tests/test_search_*` | anything under `external/`, `scripts/analysis/` |
| S2 | `scripts/analysis/evaluate_external_footprint.py`, `scripts/analysis/score_competition_v3.py` (additive only), `src/.../external/external_footprint.py` (additive only), new `scripts/run/run_proxy_score.py`, `scripts/analysis/build_observation_audit.py`, `config/connector-policy.json`, `scripts/transform/build_verified_observations.py`, `scripts/transform/normalize_social_links.py` | discovery code, connectors |
| S3 | `src/.../external/nav_jobs.py`, `scripts/connectors/run_nav_jobs_connector.py`, new `scripts/transform/build_nav_observations.py`, `tests/test_nav_*` | policy file (propose entries in your report) |
| S4 | new `src/.../external/youtube_api.py`, new `scripts/connectors/run_youtube_api_connector.py`, `tests/test_youtube_api.py` | the old `run_youtube_search_connector.py` |
| S5 | new `src/.../external/google_places.py`, new `scripts/connectors/run_google_places_connector.py`, `tests/test_google_places.py` | discovery candidate generation |
| S6 | `scripts/run/run_competition_batch.py`, `src/.../core/refresh.py`, `src/.../core/snapshots.py`, new `scripts/run/run_daily_refresh.py`, new `src/.../external/registry.py`, `tests/test_daily_refresh.py`, `tests/fixtures/refresh-snapshots.json` | connector modules |
| S7 | `src/.../research/*`, `scripts/demo/*`, `scripts/analysis/evaluate_research_agent.py`, new `scripts/analysis/build_ux_report.py`, research fixtures | scoring scripts |
| S8 | `out/eval-sample/*` data, `scripts/analysis/annotate_eval_evidence.py`, `scripts/analysis/compile_eval_annotations.py` | pipeline code |

Shared rules: add **no new third-party dependencies** (use the standard library `urllib`); do not edit `BRAIN.md` or `docs/roadmap.md`; new tests go in new files; each session works on its own git branch or worktree (`git worktree add ../wt-<id> -b feat/<id>`) and reports the branch name; the full suite must pass before reporting; never open or score the held-out or validation splits; never log or store API keys.

## 4. Shared contracts

**Observation record** (what every connector emits; see `external_footprint.py::validate_observation`): `id` (deterministic: first 24 hex of sha256 of connector, org number, source URL and signal), `organisation_number`, `platform` (one of `PLATFORMS`), `signal_type` (one of `SIGNAL_TYPES`), `source_url`, `retrieved_at` (ISO-8601 UTC with `Z`), `content_sha256` (64 hex of the raw capture), `exact_entity` true, `identity_proof` (non-empty list of typed proofs), `acquisition_mode` (`official_api`, `licensed_api`, `company_authorized_export`, `permitted_public_page`), `rights_status` (**copied from `config/connector-policy.json`, never hard-coded; `approved` only when the owner has approved**), `source_class`, `strategy` (a name from `external_control.STRATEGIES`), plus `evidence_span` for review, post and mention signals, and `metrics` where relevant.

**Connector contract** (dict based, no shared import needed): each connector module exposes `CONNECTOR_ID: str` and
`collect(profile: dict, *, now: datetime, context: dict | None = None) -> dict` returning
`{"status": "available|not_available|blocked|failed|not_applicable", "observations": [...], "operations": {"requests": int, "third_party_cost_usd": float, "latency_ms": [int]}, "note": str | None}`.
`not_applicable` means we checked and the company cannot have the signal; `not_available` means checked, nothing found; `blocked` and `failed` are never converted into negatives. S6 discovers connectors by this contract.

**Development slice:** the 240 development companies of `out/eval-sample/manifest.jsonl`. Held-out and validation stay sealed.

**Reporting:** append a `## Results (date)` section to your own handoff: branch, files changed, tests passing, measured numbers, deviations, open questions. Include exact commands you ran.

## 5. Owner actions (cannot be done by Codex)

1. API keys in `.env` (comma-separated lists allowed): search providers (S1), `YOUTUBE_API_KEYS` (S4), `GOOGLE_MAPS_API_KEYS` (S5). Google Places needs a billing account even within free caps.
2. Rights decisions recorded in `config/connector-policy.json` after reading each provider's terms: NAV feed (S3), YouTube API Services terms (S4), Google Maps Platform terms (S5), and the company-site crawl policy. A connector earns no points until its entry is `approved` with `approved_by` and `approved_on`.
3. Human labelling: the observation audit worksheet from S2 (at least 100 observations), the QC worksheet for website labels, and a spot-check of the 180 extension labels from S8.
4. Decide the wrong-company ceiling and whether the medium-evidence website path may publish.

## 6. Environment variable names (every session must accept all of these)

Keys live in `.env` (git-ignored). Each provider accepts a comma-separated plural name and the singular and alias names below; the plural form is canonical for new keys. Never print or log a value; reports may show only a short hash.

| Provider | Names accepted (first found wins; merge all found) |
|---|---|
| Serper | `SERPER_API_KEYS`, `SERPER_API_KEY` |
| SerpApi | `SERPAPI_API_KEYS`, `SERPAPI_API_KEY`, `SERPAPI_KEY` |
| Tavily | `TAVILY_API_KEYS`, `TAVILY_API_KEY` |
| Linkup | `LINKUP_API_KEYS`, `LINKUP_API_KEY` |
| Brave | `BRAVE_API_KEYS`, `BRAVE_API_KEY` |
| Google Places (New) | `GOOGLE_MAPS_API_KEYS`, `GOOGLE_MAPS_API_KEY`, `PLACES_API_KEYS`, `PLACES_API_KEY`, `GOOGLE_PLACES_API_KEY` |
| YouTube Data API v3 | `YOUTUBE_API_KEYS`, `YOUTUBE_API_KEY`, `YOUTUBE_DATA_API_KEYS`, `YOUTUBE_DATA_API_KEY` |

The owner's `.env` currently contains `SERPER_API_KEY`, `SERPAPI_KEY`, `LINKUP_API_KEY` and `PLACES_API_KEY`. Session S1 also updates `.env.example` with the canonical names and no values; a missing key means that provider is skipped and reported as `not_configured`, never an error.
