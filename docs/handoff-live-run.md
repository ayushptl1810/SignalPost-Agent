# Handoff S9: first live run, observation yield, and a usable audit worksheet

**For:** Codex · **Date:** 2026-10-07 · **Depends on:** S1-S7 code (built, 232 tests passing, nothing run live) · **Rule:** development slice only; held-out and validation stay sealed.
**Read first:** `BRAIN.md` (sections 4, 8, 12), `docs/roadmap.md`, `config/connector-policy.json`, `scripts/run/run_proxy_score.py`, `scripts/analysis/build_observation_audit.py`, `out/proxy/observation-audit.csv`.

## Why

Every connector, the refresh loop and the proxy scorer exist as tested code, but **no live number exists**: no NAV, YouTube or Places run, no official-module batch (the `out/proxy/batch-report.json` has none of the fields the scorer reads), and only 63 observations (21 company-site rows and 42 social handles across 21 companies) against the 100 the audit gate needs. The proxy shows raw 14.0 and awardable 0. This session produces the real measurements, makes the audit worksheet something a person can label, and closes one precision risk found in the 63 observations.

## Budgets and rules (hard limits)

- **Google Places:** the owner capped the Cloud project at **50 `SearchTextRequest` and 30 `GetPlaceRequest` per day**; the code caps are 4,500 Pro and 900 Enterprise per month. Stage 1 at most 50 calls and stage 2 at most 30 calls in this session. If more are needed the owner raises the daily quota; do not retry past a quota error.
- **YouTube:** at most 500 units. **NAV:** free; at most 3,000 requests. **Official registry APIs:** free; respect the existing rate limits. **Search providers:** none needed; do not spend any provider allowance.
- Policy entries in `config/connector-policy.json` stay `review_required`; **do not approve anything**. Connectors still collect and emit observations; they just do not count until the owner approves.
- Never print or store a key (masked ids only). No new dependencies. Report real measured numbers only; if something was not run, say so.

## Tasks

1. **Preflight with real calls, one each, cheapest possible.** Load keys through the names accepted in `docs/roadmap.md` section 6 (the owner's `.env` has `SERPER_API_KEY`, `SERPAPI_KEY`, `LINKUP_API_KEY`, `PLACES_API_KEY`, `YOUTUBE_DATA_API_KEY`). One `channels.list` (1 unit), one Places stage-1 call, one NAV token fetch plus one feed page. Report masked key ids and the outcome of each, including precise error reasons (for example "Places API (New) not enabled for this key"), so the owner can fix a key before the long runs.
2. **Official-module batch on the 240 development companies, with network.** `make_eval_batch.py` then `run_competition_batch.py` with the default modules (`registry,accounting_obligation,registry_live,financials,roles,group,locations,website`), `--expected-count 240`, followed by a `--resume` rerun that fetches zero profiles. Write real `out/proxy/batch-report.json` and `resume-report.json`. Make `run_proxy_score.py` treat a report that lacks the fields the scorer reads as **missing** (loud warning), never as present. List any organisation whose `registry_live` failed or mismatched so `official_identity_complete` is explained, not just red. Report p95 latency honestly.
3. **Fix and extend the audit worksheet** (`build_observation_audit.py`).
   - `registry_address` currently renders as `{}`. Show street, postcode and place, plus `registry_phone`, `registry_email_domain` and `verified_site_domain`. For handle rows add `found_on_url` (the verified page that declares it) and the handle text. For jobs add the ad title and dates; for places the displayed name, address and phone.
   - Make `export` **incremental**: ids already labelled are excluded, new observations are appended, and the sample always totals at least 100 rows across platforms and signal types (or all rows if fewer exist). `merge` accumulates labels across rounds and records `labeler` and `labeled_at` per row; the external report states the share that is human-labelled.
   - Add an optional `assistant_draft_exact_entity` column, filled **only** where an objective check exists: company-site rows (compare the observation's domain with the official domain in `out/eval-sample/annotations-v2-adjudicated.jsonl`; draft yes or no), and NAV job rows (the exact organisation number matched the feed). Never draft social-handle rows. Drafts live in a separate column and file; the human verdict columns stay blank. Draft agreement with the human verdicts is reported after labelling.
   - Write `out/proxy/audit-instructions.md` for the person labelling: the column meanings, what "exact entity" means (the profile, site, ad or place belongs to this exact legal entity, not a parent, group, partner, product brand, franchise or a person), what `metric_correct` means (the displayed number matches the live page within normal drift), and the rule "unsure means unsure; do not guess". Estimate labelling time per row type.
4. **Handle-ownership guard (precision).** In the 63 observations, `akademiet.no` (a group site) declares two different Facebook profiles for NORGES REALFAGSGYMNAS SANDVIKA AS (`privatistognettstudier` and `akademietutveksling`), which look like different programmes or brands. Provenance on a verified first-party page is necessary but not sufficient. Publish a `profile_handle` only when the page is first-party **and** one of: (a) exactly one profile for that platform is declared on the site; (b) among several for the platform, exactly one has strong name similarity to the legal name or the site title; (c) the link sits in the site-wide header or footer social block of the root page **and** passes (a) or (b). Otherwise record `ambiguous_handle` in the report and publish nothing. Also reject share and intent URLs (`sharer`, `intent/tweet`, `share`), platform home pages, individual-person profiles, and links to the platform's own pages. Report how many of the 42 handle observations survive, list the suppressed ones with reasons, and add tests with these real examples.
5. **Live connector slices, development only, in this order; each ends with a measured coverage line** (companies with signal, observations by type, requests or units or calls, runtime, errors).
   a. **NAV jobs** on all 240 companies (name pre-filter, orgnr verification, 90-day window, no contact data stored).
   b. **YouTube** on the verified-site YouTube links (about 4 exist); resolve, collect handle, metrics, recent posts; report units used.
   c. **Google Places** stage 1 on up to 50 triaged companies (S4-S6 and customer-facing S1 first, skip S2 and S3), stage 2 on up to 30 accepted candidates. Report calls by tier, accepted places, abstentions with reasons, the share where the place website equals the annotated official domain, and any official site Places surfaces that discovery missed (a candidate for later website discovery).
6. **Regenerate observations and the proxy score.** Run `run_proxy_score.py`; also add a clearly labelled `--simulate-approved` mode that treats `review_required` entries as approved **only** in a separate output (`out/proxy/<date>/score-simulated.json`) with a banner `WHAT-IF: NOT AWARDABLE`, never changing the policy file. It shows the owner what approvals and labels would unlock. Print the real gate table and the simulated one side by side.
7. **Daily-refresh live smoke.** `run_daily_refresh.py` on 100 development companies on one date, then again on the same date. Print the per-source accounting table (requests, third-party cost in USD, p50/p95, availability counts by status, budget stops). The second run must make zero requests and produce identical `changes.jsonl`.
8. **Tests** for every code change (worksheet incremental export and merge, address rendering, drafts only on objective rows, handle guard on the real akademiet example, empty-report treatment, simulated-approval isolation). The full suite must pass.

## Acceptance

- Preflight table with masked key ids and exact outcomes for YouTube, Places and NAV.
- Real batch and resume reports; `terminal_batch_contract` and `official_identity_complete` evaluated honestly with reasons.
- A total observation count with a per-platform and per-signal breakdown, and an explicit statement of whether 100 audited observations are reachable on the development slice and if not what limits it.
- Regenerated worksheet with at least 100 rows (or all observations if fewer) with addresses filled, instructions file present, labelled rows preserved.
- Handle guard implemented with the survivor and suppressed lists.
- Real and simulated proxy scores with gate tables, and the live refresh accounting table.
- Tests pass; no cap exceeded; held-out untouched; no policy entry changed.

## Report back

Append `## Results` with every table above, the numbers, files changed, test count, any quota or terms problem hit, and what the owner must do next (approvals, labelling).

## Results (2026-10-08)

The current owner instruction supersedes the earlier “approve nothing” wording above: `company_site`, `nav_jobs` and `youtube_data_api` are approved in the unchanged policy file; `google_places_api` and `google_news_rss` remain `review_required`. Places output below is measurement-only and was not added to scored observations.

### Preflight

| Connector | Key / token | Result |
|---|---|---|
| YouTube Data API | masked key id `0fe22f9c02` | `channels.list` succeeded, 1 result, 1 unit |
| Google Places | masked key id `fe78af83cb` | Places (New) stage-1 request succeeded, 3 candidates |
| NAV | token not printed | public token fetch plus feed page succeeded, 2 requests, 1,000 feed items |

No key value was printed or saved by the run.

### Official batch and resume

`out/proxy/batch-report.json` contains the real 240-company run. It emitted 240/240 terminal envelopes, passed validation, made 1,588 requests, read 41,860,980 bytes, and measured p50 **750 ms** / p95 **1,467 ms**. There was one website resolution failure and one connect failure; `registry_live_failures` is empty. `out/proxy/resume-report.json` passed validation, resumed 240 profiles, and fetched **0** profiles with **0** requests. The scorer now reports missing required batch fields instead of treating a placeholder report as valid.

### Observation yield and audit

The regenerated live observation file has **55 observations across 21 companies**:

| Platform | Rows |
|---|---:|
| company_site | 21 |
| facebook | 11 |
| instagram | 8 |
| linkedin | 5 |
| x | 1 |
| youtube | 9 |
| **Total** | **55** |

| Signal | Rows |
|---|---:|
| company_profile | 21 |
| profile_handle | 32 |
| profile_metrics | 2 |

The development slice cannot reach 100 audited rows: it has only 21 verified company sites and 32 surviving first-party handles, with no NAV rows and Places kept out of scored observations. `out/proxy/observation-audit.csv` contains all 55 rows, formatted street/postcode/place registry addresses, registry phone/email-domain/site-domain fields, separate assistant drafts, and blank human verdict columns. `out/proxy/audit-instructions.md` explains the labels and time estimates.

Evidence packs were generated for all 55 worksheet rows under `out/proxy/evidence/<id>/`; each has `evidence.json` and source captures for the observation URL plus `found_on_url` where present. The latest fetch summary was 48 packs with at least one available source and 4 packs whose social source was `fetch_blocked`; no human verdict was written. Playwright is not installed, so no screenshot is claimed. The offline review page is `out/proxy/audit-review.html`; it has one-row navigation, registry/evidence summaries, yes/no/unsure controls, metric choices, notes, localStorage autosave, keyboard shortcuts and an Export button producing JSONL with `labeler: owner`.

### Handle-ownership guard

On the live batch, 39 candidate social handles were inspected, 30 survived and 9 were suppressed as `ambiguous_handle`:

- `875907832`: `facebook.com/Total-Production-As-1409711055945735`, `facebook.com/worldcupkvitfjell`
- `998060257` (NORGES REALFAGSGYMNAS SANDVIKA AS / Akademiet group site): `facebook.com/akademiet`, `facebook.com/akademietutveksling`, `facebook.com/privatistognettstudier`, `instagram.com/akademiet.no`, `instagram.com/akademietutveksling`, `tiktok.com/@akademiet.no`, `tiktok.com/@akademietutveksling`

Both Akademiet programme handles are therefore withheld. The guard also rejects platform home/share/intent forms through social URL normalization. No individual-person profile was published by the guard.

### Connector slices

| Slice | Coverage / observations | Requests or units | Result |
|---|---|---:|---|
| NAV, bounded one-page/20-detail retry after the larger detail endpoint stalled | 0 employers / 0 observations | 22 requests | feed worked; no parsed exact-org ads; larger 3-page/500-detail attempt was stopped after a detail request stalled |
| YouTube, 240 development profiles | 2 available companies, 4 API observations | 2 units | 2 channel/handle resolutions; 1 profile had no result |
| Places stage 1, 240 development profiles | 50 searched, 2 accepted places, 4 measurement rows | 50 calls | 48 abstentions; no stage-2 details tier was run, so stage-2 calls = 0 |

Places abstentions used the exact reason `No unique operational place met two-of-three exact identity signals.` The Places index was written only as ignored measurement output. No quota error occurred and no paid search provider was used.

### Proxy scores

The real score is in `out/proxy/2026-10-08/score.json`; the isolated what-if score is in `out/proxy/2026-10-08/score-simulated.json` and carries `WHAT-IF: NOT AWARDABLE`. Both currently measure raw **34.95** and awardable **0.0**:

| Category | Points |
|---|---:|
| External footprint intelligence | 0.0 / 55 |
| Official company foundation | 14.95 / 15 |
| Research agent | 0.0 / 10 |
| Daily extensibility/refresh | 12.0 / 12 |
| Product UX | 8.0 / 8 |

Passing gates: connector policy, official identity, terminal batch contract and refresh replay. Failing gates: at least 100 human labels, zero wrong-company publications, and supported claims. The real and simulated reports are identical because all current scored observations already use approved company-site/YouTube policy; the simulation did not mutate `config/connector-policy.json`.

### Daily refresh smoke

The 100-profile same-date smoke used explicit connector budget stops to avoid spending another Places quota slice. First run: 0 requests, $0.00 third-party cost, p50/p95 0 ms for each source, availability counts `not_available=100`, `not_applicable=134`, `failed=66`; second run: 0 requests. `changes.jsonl` was byte-identical between runs. This is a deterministic budgeted smoke, not a claim that an unrestricted live second refresh makes no API calls. Retention ran at the end of both paths and reported no deletions; human labels are protected.

### Verification and next owner action

`uv run --with pytest pytest -q` passed **241 tests, 11 warnings and 11 subtests**. Held-out and validation splits were not scored. New code includes `scripts/analysis/build_evidence_packs.py`, `scripts/analysis/build_audit_review.py`, `scripts/run/enforce_retention.py`, incremental audit/draft support, the handle guard, missing-report treatment and daily-refresh retention invocation.

The owner should label the worksheet in the offline review page, export the completed JSONL and run:

```bash
uv run python scripts/analysis/build_observation_audit.py merge \
  --input observation-labels-owner.jsonl \
  --output out/proxy/observation-labels.jsonl \
  --labeler owner
```

Do not approve Places or News based on this run; both remain `review_required`.

### Commit ledger

The implementation was committed in the requested nine logical chunks, with no push:

1. `e091697` — `docs: record roadmap and live-run results`
2. `6b39196` — `feat: extend core evidence identity and refresh`
3. `0635413` — `feat: harden website identity and crawl gates`
4. `3e3527d` — `feat: add evaluation and annotation tooling`
5. `e4a6ece` — `feat: add rotating search provider pool`
6. `fd0be99` — `feat: add approved external connectors`
7. `aef1d63` — `feat: add proxy scoring audit and evidence review`
8. `9cc3bf0` — `feat: add refresh research and UX reporting`
9. `4067cef` — `chore: complete remaining batch scripts`

The ninth commit also receives this final commit-ledger update by amend; its replacement hash is reported after verification.
