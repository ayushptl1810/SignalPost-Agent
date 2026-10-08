# BRAIN: project context that must survive new chats and compaction

**Read this first, then `docs/handoff-*.md` for any open task. Update this file at the end of every session that changes state** (what was done, numbers, decisions, next steps). Keep it under about 300 lines; move history into git, not here.
**Last updated:** 2026-10-08 · **Repo:** `/Users/ayushpatel/Documents/GitHub/SignalPost-Agent` · **Python 3.12+, `uv`** · **Tests:** `uv run --with pytest pytest -q` → 253 passed, 11 warnings, 11 subtests

## 1. What this project is

The Builderr "Signalpost" company-research challenge (submissions go to `submit@builderr.ai`; rules in `README.md`, `OUTPUT_CONTRACT.md`). An agent takes Norwegian organisation numbers, builds a source-attributed, exact-entity profile per company, and refreshes it daily. The universe is about 411k active Norwegian entities that filed 2025 accounts (`data/universe-metadata.json`); the official batch is supplied by Builderr, not chosen by us.

**Scoring rubric (competition proxy, `docs/external-footprint-loop.md`):** external-footprint intelligence **55** (exact-entity attribution 10, multi-source breadth 10, workforce/jobs 7, ratings/reviews 8, buzz/engagement 7, qualified sentiment 10, freshness 3), official company foundation 15, research agent 10, daily extensibility/refresh 12, product UX 8. Target: qualified score of at least 80/100. Baseline at last measurement: **33.97 raw, zero awardable**, because no external observation has passed the blind audit.

**Qualification gates per connector:** exact-entity precision at least 99.5%, metric correctness at least 98%, complete evidence (URL, retrieval time, content hash, claim span), **zero wrong-company publications**. Missing or blocked data is never converted into a zero or neutral fact; ambiguous candidates abstain. Proving 99.5% with zero errors needs about 760 audited publications, so we publish only on near-certain evidence.

## 2. How we work

- **Claude (planning, audit, adjudication):** analyses results, writes `docs/handoff-*.md` specs, adjudicates labels, never builds large features.
- **Codex (implementation):** builds from handoff docs, runs the pipeline, appends results to the handoff.
- **The owner (decisions, keys, human QC):** accounts and API keys, spot-checking labels, deciding policy.
- **Communication:** plain language first, details second. Report outcomes faithfully, including failures.
- **Rules that never bend:** accuracy before volume; tune only on the development split; held-out and validation stay sealed; never publish a site on evidence a directory could also produce.

## 3. Environment gotchas

- A **GateGuard hook** blocks the first Write/Edit/Bash per file or session until facts are stated (callers, no duplicate file, data shape, the user's instruction verbatim); then retry the same call. It can be turned off with `ECC_GATEGUARD=off`.
- The tool safety classifier sometimes fails transiently on WebFetch/Bash/Write; retry once, otherwise continue elsewhere.
- Claude's `WebSearch` is US-based and returns mostly directories for Norwegian micro-companies. It can confirm a site when it finds one but cannot prove a site does not exist.
- `data/brreg-enheter.csv` is gzip-compressed despite the name. `out/` is git-ignored but persists locally.
- The Bash working directory can drift after a `cd`; use absolute paths.

## 4. Architecture and code map

Pipeline (`scripts/run/run_search_discovery.py`): registry profile -> candidate generation -> crawl -> identity gate -> first-party gate -> publication, with ledger/caches around it.

- **Candidates, in order:** NAV employer homepage (exact org number), registry email domain, registered sub-unit websites, DNS-checked name-derived domains (variants keep "og", trim generic words like bygg/transport/holding), then search (only if those fail; triage skips are specified in handoff 5).
- **Crawl:** site root (not the matched deep page), robots respected, SSRF checks on resolved and connected IPs, per-host throttling (max 2 concurrent, 1 s spacing), robots and candidate-fetch caches, 8 concurrent workers, 60 s per-company timeout, atomic checkpoint and `--resume`.
- **Identity gate** (`core/identity.py`): exact org number (checksum-validated, `core/orgnumber.py`) scores 1.0, full legal-name tokens 0.95; footer, contact and JSON-LD legal identifiers are captured; a page that shows a **different** valid org number is vetoed (Njord case); registry group numbers are only related evidence.
- **First-party gate** (`web/first_party.py`): vetoes for directories, listing paths, blocklist (`data/blocklist-domains.txt`), shared domains; publication rule `--gate g3` (Istat-style strong/weak evidence). G0, G1, G2 stay reproducible. The self-referential "page shows its own email domain" signal was removed.
- **Failure honesty:** DNS/connect/TLS errors are `failed`, never `blocked`; network preflight and a 20% resolution-failure breaker; fatal provider errors (out of credits) stop the run; scorecard refuses runs with `provider_fatal`.
- **Replay:** `--search-cache`, `--fetch-cache`, `--replay-only`; redirect chains are recorded so aliases are scored as matches only when a redirect proves it.
- **Other modules:** `registry/` (Brreg), `external/nav_jobs.py` (NAV feed index builder, `scripts/connectors/run_nav_jobs_connector.py`), `web/related.py` (group-member pages are related, never official), `web/constraints.py` (one domain cannot be exact for several entities), `core/ledger.py` (negative cache; never used in evaluation runs).
- **Evaluation tools** (`scripts/`): `run/select_eval_sample.py`, `run/make_eval_batch.py`, `analysis/score_discovery_run.py` (label-free scorecard plus `--annotations` mode, weights, Wilson bounds, look log), `analysis/qc_annotations.py` (blind QC export, disagreement export, merge to v2, Cohen's kappa), `analysis/review_candidates.py`, `analysis/run_gate_matrix.py`, `analysis/compare_gate_runs.py`.
- **Existing connectors not yet integrated into the score:** Google News RSS, YouTube search, LinkedIn guest (experimental, rights unresolved), annual-report workforce, Fagfolkguiden reviews, sentiment model runner.

## 5. Evaluation set (the answer key)

- **Universe filter:** active entities whose latest accounts year is 2025: **420,476 rows** by our filter vs 411,160 in the frozen metadata. The frozen `signalpost-company-universe-2025.jsonl.gz` is not in `data/`; record the filter and hashes.
- **Sample:** 400 companies, seed 20261007, manifest SHA-256 `05e7e89d2d59eae1b3520e369b6090cc76e648dce19900501469f82f509926be`, bulk SHA-256 `8483831efe73d873460e4c6765c98f46a1937a59b763c074ea32a6d2e657bc70`. Files in `out/eval-sample/`.
- **Strata** (population, share, registry-listed website): S1 AS no employee count other activity 177,731 (42.3%, 9.4%) n=100; S2 same, property NACE 68, 88,739 (21.1%, 4.4%) n=40; S3 holding/unspecified NACE 64,00, 59,979 (14.3%, 0.9%) n=40; S4 5-19 employees 41,798 (9.9%, 18.8%) n=80; S5 20-99, 14,603 (3.5%, 30.1%) n=60; S6 100+, 2,468 (0.6%, 45.5%) n=30; S7 non-AS forms 35,158 (8.4%, 33.4%) n=50.
- **Splits:** development 240, held-out 80, validation 80 (hash rank within stratum). The first 60 are the pilot.
- **Extension:** 180 extra S4/S5/S6 companies (seed 20261008, 90 held-out, 90 validation) are annotated in S11 from fresh non-search checks. The current conservative artifact has 38 official sites and 142 undetermined rows; no low-confidence no-site label is eligible for certification because the alternative-provider check was unavailable. `manifest-extension.jsonl` and the combined 580-row `manifest-v2.jsonl` exist.
- **Annotations:** `annotations-v1.jsonl` (Codex, search plus page checks; 58 official_site, 32 related_only, 175 no_site_confirmed, 135 undetermined). Weakness: negatives used the same search engine as the pipeline. `annotations-v2-adjudicated.jsonl`: 24 disagreement rows adjudicated by Claude after reading the live pages (10 outcomes changed: 5 no-site -> official, 2 related -> official, 2 undetermined -> official, 1 domain swap). **These are model-assisted, not human gold.** Human spot-check priority: Geminor NO AS and Strand Unikorn (group/redirect policy), Hafjell-Kvitfjell (event site run by the company), Njord (kajakk.com unreachable from our side), Vindkraft Nord (broken TLS, unverified), then about 20 of the other changed rows.
- **Protocol:** `docs/evaluation-sampling-plan.md` (evidence tiers, outcome codes, metrics, acceptance criteria). Evidence tiers: strong = org number in a legal/contact position, medium = registry email domain or phone, weak = name or address.

## 6. Results so far (development split, 240 companies)

| Run | Precision | Recall | Notes |
|---|---|---|---|
| First baseline | 31% (4 of 13) | 15% | **Invalid**: the registry-site stage ran with no DNS, so 43 sites were recorded as blocked |
| v2, network fixed, v1 labels | 65% (15 of 23) | 54% | 5 of 8 "wrong" were annotation errors |
| v2, adjudicated labels | **96% (23 of 24), Wilson LB 80%** | 61% | 1 real error: Njord, a different org number on the page |
| Replay G0 (extraction fixes) | 100% (23), LB 85.7% | 59% | no-search, registry-derived candidates only |
| Replay **G3** | **100% (27), LB 87.5%** | **69%**, abstention 91.7% | adds Better Brand, OK Kjemi, Kristiansen og Stensrud, Norest Bygg, all true; Njord vetoed |

Not yet measured: Pixotope, Jan Stenersen and Kjeldsberg (need search candidates), held-out, validation (no looks logged). Four cached pages show name/address only, no org number, email or phone; G3 abstains, which is intended.
**Caution:** G3's medium-evidence path (OK Kjemi: email, phone, name, no org number) should stay a candidate rather than a publication until a held-out audit shows zero errors, given the 99.5% bar.

## 7. Search providers and credits (current blocker)

- **Serper is out of credits** (one-time 2,500 free queries, no monthly reset; paid is $1.00 per 1,000, $50 = 50,000, valid 6 months). It answers HTTP 400 "Not enough credits". The development run used 382 queries; reruns were uncached.
- **Refreshing free options (2026, verify at signup):** Tavily 1,000 credits/month (basic search = 1); Linkup about $20/month top-up reported; Brave $5/month credit but its terms forbid storing results; SerpApi about 100/month per account; Google Custom Search is closed to new customers and ends 2027-01-01. YouTube Data API: 10,000 units/day, resets daily (channels.list = 1 unit). Google Places (New): free monthly caps per SKU (Text Search Pro 5,000, Enterprise/ratings 1,000), reset monthly.
- **Serper terms are silent on caching;** keep replay caches local, git-ignored, never published; prefer URL-only caching. Check SerpApi's terms (`serpapi.com/legal`) before using several accounts of the same provider.
- **Built (S1, tested, never run live):** provider pool with rotation, triage and one-query default, cache policy, `scripts/analysis/run_provider_bakeoff.py`. Keys present in `.env`: Serper (spent), SerpApi, Linkup; Tavily declined (needs a card). The free allowances of SerpApi and Linkup are unconfirmed (check each dashboard); no bake-off has been run, so no provider is chosen yet.
- **Google Cloud (owner, done):** Places API (New) and YouTube Data API v3 keys in `.env` (`PLACES_API_KEY`, `YOUTUBE_DATA_API_KEY`). Places daily quotas set to 50 `SearchTextRequest` and 30 `GetPlaceRequest` (unused Places methods capped at 5), so a charge cannot occur inside the free tier (Enterprise tier: 1,000 free per month, $35 per 1,000 beyond). The Places design is two-stage (cheap Pro search, then Enterprise details only for a name-and-address match).

## 8. Strategy: where the points are

**Gate-first (read `docs/roadmap.md` section 1).** `score_competition_v3.py` returns an awardable score of 0 unless all seven gates pass, and every external category is 0 unless the external report passes qualification (at least 100 human-labelled observations, zero wrong entity, precision at least 0.995). Several scorer inputs have **no producer yet**: `connector_policy_passed`, `fresh_coverage`, `external_footprint_qa_passed`, `external_intelligence_presented`, the UX report, and the observation audit. So the first job is closing gates with data we already have (verified sites and their handles), then adding connectors. In the local proxy the coverage denominator is all companies, so a signal present for 5% of companies earns about 5% of its points; do not over-fit to it.
Website discovery is only one input to attribution and breadth (about 10 of 100 points). Code for sessions S1-S7 is built and tested (see section 12); the open specs are `docs/handoff-live-run.md` (S9, first live measurements) and `docs/handoff-extension-annotation.md` (S8, 180 unlabelled companies). File ownership and contracts are in `docs/roadmap.md`. Plan, by points per risk:

1. **Finish and freeze website discovery** (handoff 5, one recording run, human spot-check, annotate the extension, one held-out look). Stop chasing recall beyond about 70%.
2. **Get a real proxy score.** `scripts/analysis/score_competition_v3.py` needs external, refresh, research and UX reports that do not exist in `out/` yet; generate them for the 240 development companies so we track points.
3. **Connectors,** each through the repo's own rule (100 development, then 100 validation; keep only if it adds 5 points of coverage with zero wrong-company publications): NAV jobs (7 points, official feed keyed by exact org number, connector built, not integrated); YouTube (buzz and handles, cross-linked from verified sites); Google Places (reviews 8 points, phone/address identity, website candidates; about 3,000 calls/month for a daily 100-company refresh, so roughly $70/month beyond the free cap); news mentions; **sentiment last** (needs 300 labeled snippets, 90% precision, macro-F1 0.80; abstaining may beat risking it).
4. **Daily refresh loop** (12 points): 100 frozen companies per day, change detection, cost and latency report. Mostly engineering, so do it early.
5. **Research agent and UX** (18 points): polish at the end.

## 9. Key decisions and why

- Directories are rejected by structure: the site **root** is judged, listing-shaped paths and blocklisted domains are vetoed, and the page's own email domain is not ownership evidence. (Six of nine early wrong publications were deep directory pages.)
- A different organisation number on the page vetoes publication; group numbers are related evidence only.
- Registry-listed websites are still verified (3 of 7 in the first pilot were lapsed, a different brand, or unreachable).
- Evaluation runs never use the negative-cache ledger, and fatal provider errors invalidate a run instead of being scored.
- Annotation truth uses Istat's evidence table; publication is stricter than annotation.
- Held-out gets logged looks; validation is one look with `--final`.
- Prior art: WIN/ESSnet "URL finding" (accuracy 83-90%, six query variants, oversample larger firms, blocklists, annotation tiers). Full survey in `docs/discovery-literature-survey.md`.

## 10. Open decisions and next steps (updated 2026-10-08)

**Owner decision: the owner labels nothing by hand.** Codex and the planner (Claude) do all labelling, recorded honestly as `codex_review` / `claude_review`. The scorer still counts only `owner` labels toward the 100-label gate, so that gate stays unmet and its effect on the real hidden scorer is unknown; the owner emails Builderr (submit@builderr.ai) to ask whether independent model-checked labels satisfy the audit gate, plus Places data retention and the hidden coverage denominator.

1. **Extension S11 (2026-10-08):** `out/eval-sample/annotations-ext-v2.jsonl` has 180 rows marked `extension_v2`: 38 `official_site`, 142 `undetermined`, and zero certifiable negatives. Held-out is 22 official / 68 undetermined; validation is 16 official / 74 undetermined; both remain below 40 confident determined rows. The scorer excludes any future low-confidence `no_site_confirmed` row whose search check is `not_run`. The fresh 45-row registry-only comparison agrees 41/45 (91.11%), κ=0.7256; the four disagreements were adjudicated to official sites from cached first-party page evidence. `annotations-v3-plus-extension.jsonl` has 580 rows. This is not a held-out precision certification.
   **Codex S11 (`docs/handoff-extension-finish.md`), revised scope:** commit the SSRF fix; diagnose the dead SerpApi/Linkup keys; run a blind independent `codex_review` pass over a random 45 of the 180 extension rows plus all 94 positives' disputed ones and report agreement and kappa (Task 2 is no longer "settle the 146"); independent `codex_review` pass over the 100-row audit worksheet.
2. **Planner (Claude), after S11:** independent `claude_review` pass over the same 100 rows, adjudicate disagreements with Codex, write the result to `out/audit-corpus/`, re-run the proxy score. Also spot-check adjudicated website rows (Geminor NO, Strand Unikorn, Hafjell-Kvitfjell, Njord, Vindkraft Nord).
3. **Owner, small:** check the SerpApi and Linkup dashboards (valid key, quota, verification, card requirement) and tell the planner; send the Builderr email above. Optional: decide whether to buy Serper credits ($1 per 1,000 queries) to run the alternative-provider negative checks, which is the only way to reach 40 confident determined rows per split.
4. **After S11:** if held-out and validation each have at least 40 confident determined rows, take one logged held-out look; validation is a single `--final` look. Otherwise stay sealed.
5. **State recap:** S1-S11 done. Human labels 0; assistant labels remain separate from human audit labels. Extension S11 is 38 official / 142 undetermined, with no certifiable negatives. The proxy raw score remains 34.95 and awardable 0. Places stays measurement-only; policy unchanged (company_site, nav_jobs, youtube_data_api approved; Places and News review_required).
6. **Do not** open validation, run a 500-company search batch, spend a paid provider without the owner, or relax the website gate. Realistic proxy ceiling if the gates pass is about 46 raw (about 51-56 with the research agent); do not tune to the proxy.

## 11. Commands

```bash
uv run --with pytest pytest -q
# sample and batch
uv run python scripts/run/select_eval_sample.py --output out/eval-sample/manifest.jsonl --summary out/eval-sample/summary.json
uv run python scripts/run/make_eval_batch.py --manifest out/eval-sample/manifest.jsonl --split development --output out/eval-sample/development-input.jsonl
# discovery (no search needs no key); never pass --ledger for evaluation
uv run python scripts/run/run_search_discovery.py --input <profiles.jsonl> --output <out.jsonl> --report <report.json> --limit 240 --no-search --gate g3
# score against the answer key
uv run python scripts/analysis/score_discovery_run.py --profiles <out.jsonl> --report <report.json> --annotations out/eval-sample/annotations-v2-adjudicated.jsonl --split development --manifest out/eval-sample/manifest.jsonl --output <scorecard.json>
```

## 12. Latest implementation state (2026-10-08)

- The unified workspace now contains the S2-S8 implementation pieces: owner-controlled connector policy, verified-site/social observation builders, blind observation audit export/merge, proxy orchestrator and gate details, NAV/YouTube/Places connector contracts, connector registry, daily refresh/change output, external-aware research, UX checklist and extension annotation safeguards. S10 adds provenance enforcement, evidence packs, an offline audit review page, the 300-company audit corpus, conservative extension evidence collection, and stronger negative-check semantics.
- Live preflight succeeded: YouTube key id `0fe22f9c02` returned one channel result, Places key id `fe78af83cb` returned three stage-1 candidates, and NAV returned a token plus a 1,000-item feed. Keys themselves were never printed.
- The official 240-company batch passed its terminal contract: 1,588 requests, p50 750 ms, p95 1,467 ms, two website network failures, zero `registry_live` failures/mismatches. Resume passed with 240 resumed profiles and zero profile fetches.
- Regenerated live observations contain **55** rows across **21** companies: company_site 21, facebook 11, instagram 8, linkedin 5, x 1, youtube 9; signals are company_profile 21, profile_handle 32 and profile_metrics 2. The audit worksheet has all 55 rows, formatted registry addresses, instructions, separate drafts, 55 evidence packs and an offline review page. Playwright is not installed, so screenshots are absent and not claimed.
- The handle guard evaluated 39 live social candidates, retained 30 and suppressed 9 as `ambiguous_handle`; both Akademiet programme handles are suppressed. Places produced 4 measurement-only rows from 50 stage-1 calls and remains `review_required`; it was not added to scored observations. The policy file is unchanged: `company_site`, `nav_jobs`, and `youtube_data_api` approved; Places and News remain `review_required`.
- Label provenance is corrected: all 44 existing rows in `out/proxy/observation-labels.jsonl` are `codex_review`, with notes; human labels are 0. `external-report.json` therefore has `human_labeled: 0`, `assistant_labeled: 44`, assistant precision 0.8409, coverage 0.0875, and qualification false. The real and isolated what-if proxy scores remain raw **34.95**, awardable **0.0**; `published_on_undetermined` lists 7 organisations including Creonordic and Akademiet.
- S10 audit corpus: 300 companies, seed `20261009`, strata S4/S5/S6/S7 = 100/80/60/60, zero overlap with all 580 evaluation rows. Official batch emitted 300/300 with 2,532 requests, p50 764 ms, p95 1,777 ms, and 1.33% resolution failures. No-search G3 completed 300/300 with 0 provider queries, 59 verified sites and 1 related entity. Site/YouTube/NAV observation build produced 146 rows; YouTube used 10 units and NAV matched 0 audit companies. The incremental worksheet has 100 unlabelled rows and 100 evidence packs (90 available, 10 fetch-blocked); the owner must label it in the offline review page.
- NAV’s bounded run recorded 502 requests, 0 timeouts, 0 detail errors, and 0 matches; the self-test passed for organisation `887968942` with one synthetic observation after 500 detail requests. Research evaluation scored 5/12, with external QA true but the original single-company fixture absent from the 240-profile development corpus, so qualification is false. Places made no new calls and marked 4 stored rows unverified.
- Provider bake-off: the earlier bounded run saw SerpApi and Linkup fail after six requests each. S11's one-request diagnosis found SerpApi HTTP 200 once, while Linkup returned HTTP 429 `INSUFFICIENT_FUNDS_CREDITS`; Serper remains HTTP 400 out of credits. Provider quota/auth failures are now permanent for the run and do not retry a dead key. No provider was selected and no raw search cache was written.
- S11 extension output: fresh registry/name-derived checks produced 38 official sites and 142 undetermined; no low-confidence negative is counted for certification. The independent 45-row registry-only comparison agreed 91.11%, κ=0.7256; four disagreements were adjudicated from cached first-party page evidence. The missing alternative-provider check means this is not a held-out precision certification and no held-out/validation scorecard was produced.
- S11 audit-pack review: 100 packs were independently read from `out/audit-corpus/evidence/` only. Labels are 38 `correct`, 0 `wrong_entity`, 0 `wrong_content`, and 62 `unresolved`; company-site precision is 26/26, LinkedIn 11/11, X 1/1, and YouTube 0/27 determinate because its sources were blocked. All rows are `codex_review`, never `owner`.
- The daily refresh smoke covered 100 development profiles with explicit zero-request connector budget stops; the same-date rerun made zero requests and produced byte-identical `changes.jsonl`. This is a deterministic budgeted smoke, not a claim that a second live API refresh is free.
- Required verification: targeted S11 tests → **37 passed, 11 warnings**; full suite → **253 passed, 11 warnings, 11 subtests**. Held-out and validation were not scored. Policy state is unchanged: company_site, nav_jobs and youtube_data_api approved; google_places_api and google_news_rss remain review_required.
- Owner audit state: `out/proxy/observation-labels.jsonl` contains 44 merged owner labels; 11 ambiguous rows remain in the review page for a future browser pass. Re-run the proxy score after any further labels; continue keeping Places observations measurement-only.

## 13. File index

`README.md`, `OUTPUT_CONTRACT.md` (task and output contract) · `docs/external-footprint-loop.md` (rubric, connector order, identity graph) · `docs/external-connectors.md` (provider and sentiment gates) · `docs/architecture-options.md` (stack decisions) · `docs/norway-sources.md` (source map) · `docs/discovery-literature-survey.md` (cited prior art) · `docs/evaluation-sampling-plan.md` (answer-key protocol) · `docs/roadmap.md` (points mechanics, session map, file ownership, shared contracts, owner actions) · `docs/handoff-live-run.md` (S9, results recorded) · `docs/handoff-audit-corpus.md` (S10, results recorded) · `docs/handoff-extension-annotation.md` (S8, results recorded) · `docs/handoff-extension-finish.md` (open S11) · `config/connector-policy.json` (owner approvals) · `out/proxy/` (observations, audit worksheet, proxy scores) · `out/docs-archive/` (finished handoffs S1-S7 and the four earlier website handoffs, local only) · `out/eval-sample/` (manifests, annotations, scorecards, QC worksheets).
