# BRAIN: project context that must survive new chats and compaction

**Read this first, then `docs/handoff-*.md` for any open task. Update this file at the end of every session that changes state** (what was done, numbers, decisions, next steps). Keep it under about 300 lines; move history into git, not here.
**Last updated:** 2026-10-08 · **Repo:** `/Users/ayushpatel/Documents/GitHub/SignalPost-Agent` · **Python 3.12+, `uv`** · **Tests:** `uv run --with pytest pytest -q` → 257 passed, 11 warnings, 11 subtests

## 0. REALITY CHECK (2026-10-08, supersedes the proxy rubric in section 1)

The proxy rubric, the seven gates, "awardable 0", the 100-human-label audit and the 99.5%/760 arithmetic in sections 1 and 8 were **invented by us** and are not Builderr's. The live rules (page `https://builderr.ai/challenges/signalpost`, contract `https://builderr.ai/docs/signalpost-evaluation-harness.md`, verified 2026-10-08): **recall and coverage 50, precision and evidence 30, synthesis 12, UX 8; qualify at 65/100; a material wrong-company publication blocks qualification.** Recall is scored per external field family as 70% company recall + 30% claim recall against a cumulative verified pool of everything all crawlers (entrants and Builderr's own) found; abstention never counts as coverage. Tiebreaks: fewer wrong-company publications, then weighted company recall, then lower declared third-party cost. Official batch about 1,000 random universe companies, same time and resource budget for all; daily evaluation, final rank is the mean over daily batches; up to four revised commits before 18 Oct; closes 21 Oct. **Declared caches of public-universe material are allowed.** Leaderboard (5 Oct): 0 qualified, top 60.8, everyone loses marks on recall. Owner decision: win the recall game; UX later; no fixed timeline, build as work finishes.
**Strategy:** universe-scale offline precompute with free registry-grade sources (registry website, registry/sub-unit email domains, sub-unit websites, NAV homepage, name-derived domains through the identity gate), the complete NAV job index, company-owned outbound social links, answered from a declared cache at runtime with cheap live re-verification. Zero wrong-company publications stays absolute. Norid lookup is out (port-43 whois returns no holder; the web lookup forbids commercial/bulk use). Search (Serper 2,499 credits, SerpApi 248/250) works again but is far too small for 411k companies, so it is a measuring stick and gap-filler only; the run must complete without it. Stopped: extension/audit labelling, provider bake-off, proxy scorer.

**Official universe found (2026-10-08):** the frozen file is `https://builderr.ai/signalpost-company-universe-2025.jsonl.gz` (411,160 rows, 12.6 MB, columns organisation_number, name, legal_form, employees, bankrupt, liquidating, municipality, municipality_number, industry_code, industry_label, website, latest_submitted_accounts). Saved as `data/signalpost-company-universe-2025.official.jsonl.gz` (git-ignored; the Builderr starter kit's `README` also downloads it). The file Codex materialised from our Brreg bulk (420,474 rows, `data/signalpost-company-universe-2025.jsonl.gz`) differs: 11,997 of its rows are not in the official universe and 2,683 official rows are missing from it. **All universe-wide work (cache builds, samples, projections) must use the official file**; the 8.7% website-yield and G4 numbers above came from the substitute and should be re-measured. Registry website is present for 44,855 official rows (10.9%) and an employee count for 59,821 (14.5%). The starter kit has no npm checks (the `check:signalpost*` commands are Builderr-side).

**Live results on the official universe (2026-10-08 evening, planner):**
- **100-company smoke (submission artifact):** `out/smoke-official/` (`smoke-companies.jsonl`, `envelopes.jsonl`, `profiles.jsonl`, `report.json`), run `smoke-official-002`: 100/100 envelopes `complete`, validation passed, 546 requests, 3.0 MB, 78 s with 8 workers, p95 1.1 s, third-party cost $0, zero searches. A dead registry-listed host used to turn the whole envelope into `submission_error`; fixed (`failed` source status now maps to module `source_error`, commit 8b9ea51). Website module: 93 not_found, 6 complete, 1 source_error.
- **1,000-company sample, seed 20261010 (`out/sample1000.jsonl.gz`)**, cache builder with 64 workers: 446 s, 6,361 requests, 333 MB, so about 0.45 s/company; at that rate the 411k universe is about 52 hours on one process (CPU-bound HTML parsing; shard across processes with `--shard-index`). Published official websites: **G3 94/1,000 (9.4%)**, **G4 112/1,000 (11.2%, +19%)** with 0 removed. Yield by source: registry website 42, registry email 15, name-derived .no 36, .com 1.
- **Bug found and fixed:** the official universe file has no address/phone/email, so the first 1,000 run lost every address and email signal (G3 only 73, 0 G4 additions). The builder now joins the Brreg bulk snapshot onto the universe rows (commit ee7e339). Always pass the bulk file (defaults to `data/brreg-enheter.csv`).
- **G4 audit of the 18 added:** 17 look right (own-brand domain, name and registered address match), 1 questionable (Mathisen VVS AS -> `rorkjop.no`, a plumbing chain page). Fix needed: a post-pass that drops or demotes any registered domain claimed by two or more organisations across the build (domain uniqueness), plus chain/brand detection. My looser rule found 41 strong unpublished candidates, so G4 still leaves about 23 on the table; review which rule blocks them.
- **State semantics still off:** 47% of records have `official_website` = `failed` (almost all unresolved guessed domains and uncertain pages); should be `not_available`/`ambiguous`.
- **NAV:** rebuild started (`out/nav-full.log`); result pending.

**Starter-brief review (2026-10-08, raw in `docs/temp.md`):** no hidden portal exists; the sources are Brreg (Enhetsregisteret, Regnskapsregisteret accounts API, roles, sub-units), company-owned pages, permitted APIs, search only for candidates. The envelope has seven sections; recall is scored **per information type** (profile areas: company brief, latest financials, who runs it, working here/hiring, recent dated activity), 70% company recall + 30% claim recall against the verified pool, so a sparse type counts as much as a dense one. The starter already fills registry, accounts, roles, sub-units for 100/100 smoke companies; **gaps are hiring (NAV only), dated public activity (none) and the company brief (registry label only)**, all of which come from the verified site, so website recall is the gateway. A company with no site is an honest `not_available`, not a recall loss against the pool; cost is a tiebreak, so a search-free default run is an advantage and search stays capped/optional. Blockers for official status: a material wrong-company publication **or a fabricated financial value**. Entrant-caused failures score zero, so containment (timeouts, resume) matters more than peak accuracy. Builderr can supply model keys for scoring; LLM use is allowed within a small budget; secrets only via documented env vars. Unread: the public 100-profile sample's data and the starter kit's `npm run check:signalpost*` checks. Open questions for the organisers: the formal list of information types, per-company time/resource budget, whether runtime search keys are available.

**Council review (4 independent reviewers, 2026-10-08; raw text in `docs/temp.md`, may be deleted).** Agreed: adopt the real rubric; freeze website discovery at G3 and keep the identity gate, different-org-number veto, SSRF protection and honest `failed` vs `blocked` (they protect the wrong-company rule and the 30-point precision category); cut certification work; we have a verified batch engine with no face (the research agent `scripts/demo/ask_agent.py` is a 25-line CLI over stored profiles with no LLM and no live research on unseen companies; no UI; README still the starter), so synthesis (12) and UX (8) need something a judge can open, but the owner has parked UX for later; only 21 of 240 dev companies have external observations, so external connectors alone cannot carry coverage. **Method caveats to remember:** the G3 dev result (100% on 27, Wilson LB 87.5%) is in-sample because G3 was designed after reading dev failures; adjudication covered only the 24 disagreement rows (agreeing rows never re-checked, biasing precision upward); v1 negatives came from the same search engine as the pipeline; undetermined rows were dropped from denominators; the strong evidence tier (org number on page) is also the publication rule, so labels and predictions agree by construction; same-model-family QC (kappa 0.65-0.73). The cheapest trustworthy truth is deterministic: org-number regex plus checksum for published sites, Brreg `hjemmeside`/email/NAV matches as non-model oracles, and a hand check of about 30-100 published profiles for wrong-company errors. Discovery notes: search is a weak last resort for Norwegian micro-companies; the ~70% website-recall ceiling is a population fact (S1-S3 mostly have no site), real gains are in S4-S7 (~22% of the universe); consider requiring the org number next to a label ("Org.nr", "Organisasjonsnummer", "NO ... MVA") and corroborating across independent registry sources; optional offline recall checks via Wikidata P2333->P856 or a Common Crawl org-number scan. Process note: 21 commits in a day and 11+ handoffs; Claude-plans/Codex-builds pays off for engineering, not for evaluation.

## 1. What this project is

The Builderr "Signalpost" company-research challenge (submissions go to `submit@builderr.ai`; rules in `README.md`, `OUTPUT_CONTRACT.md`, section 0 above). An agent takes Norwegian organisation numbers, builds a source-attributed, exact-entity profile per company, and refreshes it daily. The universe is about 411k active Norwegian entities that filed 2025 accounts (`data/universe-metadata.json`); the official batch (about 1,000 companies) is supplied by Builderr, not chosen by us. Missing or blocked data is never converted into a zero or neutral fact; ambiguous candidates abstain (state `ambiguous`/`not_available`). (The earlier 55-point proxy rubric, seven gates, 99.5%/760 arithmetic and the proxy scorer are retired; see section 0.)

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

## 7. Search providers and credits (UPDATED 2026-10-08: search works again)

**Current:** Serper `SERPER_API_KEY` balance 2,499 credits (5 req/s); SerpApi `SERPAPI_KEY` free plan, 248/250 left this month (slow, use long timeouts). Tavily and Linkup removed. Credits are small next to 411k companies, so search is for calibration and gap-filling only; the declared cache is the product (S12 Task 5). Older notes below are history.

- **Serper is out of credits** (one-time 2,500 free queries, no monthly reset; paid is $1.00 per 1,000, $50 = 50,000, valid 6 months). It answers HTTP 400 "Not enough credits". The development run used 382 queries; reruns were uncached.
- **Refreshing free options (2026, verify at signup):** Tavily 1,000 credits/month (basic search = 1); Linkup about $20/month top-up reported; Brave $5/month credit but its terms forbid storing results; SerpApi about 100/month per account; Google Custom Search is closed to new customers and ends 2027-01-01. YouTube Data API: 10,000 units/day, resets daily (channels.list = 1 unit). Google Places (New): free monthly caps per SKU (Text Search Pro 5,000, Enterprise/ratings 1,000), reset monthly.
- **Serper terms are silent on caching;** keep replay caches local, git-ignored, never published; prefer URL-only caching. Check SerpApi's terms (`serpapi.com/legal`) before using several accounts of the same provider.
- **Built (S1, tested, never run live):** provider pool with rotation, triage and one-query default, cache policy, `scripts/analysis/run_provider_bakeoff.py`. Keys present in `.env`: Serper (spent), SerpApi, Linkup; Tavily declined (needs a card). The free allowances of SerpApi and Linkup are unconfirmed (check each dashboard); no bake-off has been run, so no provider is chosen yet.
- **Google Cloud (owner, done):** Places API (New) and YouTube Data API v3 keys in `.env` (`PLACES_API_KEY`, `YOUTUBE_DATA_API_KEY`). Places daily quotas set to 50 `SearchTextRequest` and 30 `GetPlaceRequest` (unused Places methods capped at 5), so a charge cannot occur inside the free tier (Enterprise tier: 1,000 free per month, $35 per 1,000 beyond). The Places design is two-stage (cheap Pro search, then Enterprise details only for a name-and-address match).

## 8. Strategy: where the points are (rewritten 2026-10-08)

Recall (50 points) is 70% share of companies covered plus 30% share of claims found, per external field family, against the cumulative pool of everything any crawler found. Levers, in order: (1) websites for every company that has one, via free registry-grade sources and the identity gate; (2) the complete NAV job index (jobs are named in the page's requested topics); (3) company-owned outbound social/profile links from verified sites (handle-ownership guard), `blocked` for restricted platforms; (4) contact/address fields that match the registry; (5) more field families once the organisers' list is known; (6) claim-level recall (more claims per covered company). Precision (30): exact organisation number or registry tie, evidence span, retrieval date; zero wrong-company publications. Synthesis (12) and UX (8) come after recall. Detailed spec: `docs/handoff-recall-engine.md`.

## 9. Key decisions and why

- Directories are rejected by structure: the site **root** is judged, listing-shaped paths and blocklisted domains are vetoed, and the page's own email domain is not ownership evidence. (Six of nine early wrong publications were deep directory pages.)
- A different organisation number on the page vetoes publication; group numbers are related evidence only.
- Registry-listed websites are still verified (3 of 7 in the first pilot were lapsed, a different brand, or unreachable).
- Evaluation runs never use the negative-cache ledger, and fatal provider errors invalidate a run instead of being scored.
- Annotation truth uses Istat's evidence table; publication is stricter than annotation.
- Held-out gets logged looks; validation is one look with `--final`.
- Prior art: WIN/ESSnet "URL finding" (accuracy 83-90%, six query variants, oversample larger firms, blocklists, annotation tiers). Full survey in `docs/discovery-literature-survey.md`.

## 10. Next steps (updated 2026-10-08, S15 offline)

**S12 done and merged to main (09cfce4).** Codex's sandbox has no outbound network, so its pilot was mostly "failed" and its NAV/search runs stalled; **the planner re-ran the live passes** from the owner's machine:
- Random 2,000 sample (`cache/random2000/`, `out/random2000-report.json`): **8.7% of companies get a published official website** (173); by candidate source registry website 100, email domain 28, name-derived .no 44, .com 1; 1,080 s, 6,546 requests, 394 MB, so a full 420k run is about 63 hours at 16 workers (too slow). 79% of records are `failed`, almost all plain NXDOMAIN (should be `not_available`).
- Search calibration (400 companies, 400 Serper credits, results in `out/search-calibration2/`): cache published 34, search-verified 22, both 11, **search-only 11, cache-only 23**. Search adds about +2.75 points absolute (+32% relative); the finds are small firms on name-derived domains.
- **Unpublished-but-strong candidates:** 67 more of the 2,000 (3.4 points, +39%) have a page matching registered address+postcode+all name tokens, or a registry-listed site with address/phone match; the G3 gate leaves them unpublished. This is the cheapest recall available (gate G4).
- NAV: the feed is a change log. A 150-day window has **195,437 ACTIVE entries** (many repeats of the same ad uuid) and the old builder did one detail call per entry at about 1/s and wrote only at the end. Needs uuid dedupe, concurrency, periodic flush.
- Norid, search providers other than Serper/SerpApi: not needed.

**S13 implemented on `feat/recall-g4` (offline; not merged or pushed).** G4 is
additive and remains separate from `official_website` until the planner audits
the exported rows. Name variants, DNS-first resolution, sharding, honest
NXDOMAIN states, NAV UUID dedupe/checkpointing/expiry, and opt-in Serper
search-fill are implemented. The local v1 replay reproduced 173 G3 rows and
found 50 signal-only G4 additions, all name/address; raw page spans were not in
that fixture, so no G4 certification was claimed. Full tests: 266 passed, 11
warnings, 11 subtests.

**S15 implemented on `feat/live-discovery` (offline fixtures; no live crawl).**
The cache builder and official batch now share `src/norway_company_agent/discovery/`.
The official command defaults to bounded G4 discovery, uses DNS-first candidate
resolution and a process pool, supports per-company/run budgets, cache-first
reverification, fresh-complete NAV input, resume/checkpoint, material changes,
and sharding. Whole-batch domain uniqueness and chain/member-page demotion are
post-discovery vetoes; the `rorkjop.no` case is covered by a fixture. Honest
states are documented in `docs/output-contract-states.md`, and
`scripts/analysis/g4_gap_report.py` explains strong unpublished candidates.
The README and clean-machine smoke script document the declared APIs, costs,
safe-opener policy and reproducibility path. Full suite: **272 passed, 11
warnings, 11 subtests**. Network yield, 100/100 smoke runtime, and 1,000-row
timing remain planner-owned because this session had no outbound crawl.

1. **Planner:** run the live 2,000 timing pass with `--gate g4` only after
   auditing `scripts/analysis/export_g4_audit.py` output; run the NAV rebuild,
   inspect unique UUIDs and flush/resume behaviour, and decide on the full
   overnight universe cache.
2. **Owner:** confirm the accepted G4 audit threshold and whether to enable
   capped Serper search-fill; keep `config/connector-policy.json` unchanged.
3. **Frozen:** identity gate, first-party gate, different-org veto, SSRF
   protection, and no extension/audit labelling. Do not publish G4-only rows
   until human review confirms zero wrong-company publications.

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
- S12 implemented the declared universe-cache builder, exact-org runtime lookup/reverification, complete-vs-unchecked NAV semantics, local recall report, search calibration, and cache declaration on `feat/recall-engine`. The 5,000-row pilot materialised the local universe at **420,474** rows (the older metadata says 411,160), measured 1,902 requests and 147,397,875 bytes across a resumed mixed-access run, and produced 84 published website claims. The complete NAV attempt stalled during the public TLS/feed request; the resulting index is explicitly incomplete and the prior bounded run had 89 employers. Search calibration had keys present but the endpoint timed out before a usable query. No full 420k cache run was started.
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

`README.md`, `OUTPUT_CONTRACT.md` (task and output contract) · `docs/external-footprint-loop.md` (rubric, connector order, identity graph) · `docs/external-connectors.md` (provider and sentiment gates) · `docs/architecture-options.md` (stack decisions) · `docs/norway-sources.md` (source map) · `docs/discovery-literature-survey.md` (cited prior art) · `docs/evaluation-sampling-plan.md` (answer-key protocol) · `docs/roadmap.md` (points mechanics, session map, file ownership, shared contracts, owner actions) · `docs/handoff-live-run.md` (S9, results recorded) · `docs/handoff-audit-corpus.md` (S10, results recorded) · `docs/handoff-extension-annotation.md` (S8, results recorded) · `docs/handoff-extension-finish.md` (S11, done) · `docs/handoff-recall-engine.md` (S12, done) · `docs/handoff-recall-g4.md` (open S13) · `docs/handoff-live-discovery.md` (open S15) · `docs/handoff-site-facts.md` (queued S14) · `docs/temp.md` (raw council review, folded into section 0) · `config/connector-policy.json` (owner approvals) · `out/proxy/` (observations, audit worksheet, proxy scores) · `out/docs-archive/` (finished handoffs S1-S7 and the four earlier website handoffs, local only) · `out/eval-sample/` (manifests, annotations, scorecards, QC worksheets).
