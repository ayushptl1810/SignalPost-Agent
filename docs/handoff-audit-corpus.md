# Handoff S10: observation precision, label provenance, audit corpus, NAV fix, research run

**For:** Codex · **Date:** 2026-10-08 · **Rule:** do not open or score held-out or validation; never spend a paid search provider; never change connector approvals in `config/connector-policy.json`.
**Read first:** `BRAIN.md`, `docs/roadmap.md`, the `## Results` of `docs/handoff-live-run.md`, `out/proxy/observation-labels.jsonl`, `out/proxy/observation-audit.csv`, `out/proxy/evidence/`.

## What the last session found (verified by the planner)

1. **Label provenance is wrong.** All 44 labels in `out/proxy/observation-labels.jsonl` carry `labeler: owner`, but they were produced by the assistant's own review, not by the owner. They are useful drafts, not human labels, and they also have empty `notes`. The `external_audit_at_least_100` gate is defined as human-labelled; counting assistant output as `owner` misstates the audit trail.
2. **Seven of the 44 labels say the observation is not the exact entity** (about 84% observation precision against a required 99.5%). They come from two companies only:
   - **Creonordic Prosjekt AS** (org 898467872): the registry lists `www.creonordic.no`; the site, plus Facebook, Instagram and LinkedIn handles taken from it, were published. Four wrong observations.
   - **Norges Realfagsgymnas Sandvika AS** (org 998060257): the registry lists `www.akademiet.no`, a group site. The handle guard withheld Facebook, Instagram and TikTok but **let YouTube through** (`@akademiet_no`, two rows, and the channel's `profile_metrics`). Three wrong observations.
3. **Root cause: the registry-listed website path skips the first-party and group checks.** Both sites were published with `source_type: registry_linked_company_website`, identity score 0.95 (name tokens only) and **no first-party assessment** (`first_party: None`). Our website annotations for both companies are `undetermined` (group or brand site, not proven first-party), and undetermined rows are excluded from the website scorecard, so these wrong publications were invisible there.
4. **Open items from the live run:** NAV produced 0 observations because the larger run stalled on a detail request (so NAV is unmeasured, not zero); Places accepted 2 of 50 candidates with **zero stage-2 calls**, which should be impossible if acceptance needs a phone or website signal; `out/proxy/research-report.json` is empty, so the research agent scores 0 because the evaluation was never run; only 55 observations from 21 companies exist, so 100 audited observations cannot be reached on the development slice.

## Tasks, in this order

### Task 0: repair label provenance (small, first)

- Relabel the existing 44 rows `labeler: codex_review` (keep the ids and verdicts as drafts; do not delete). The owner will label with the review page; only rows exported from `out/proxy/audit-review.html` (which sets `labeler: owner` and `export_source: "audit-review.html"` with a random session id) may carry `owner`.
- `build_observation_audit.py merge` must **refuse** `labeler: owner` from any file lacking that export marker, and must require non-empty `notes` (URL checked and what was seen) for any assistant `no` or `unsure`.
- The external report must state `human_labeled`, `assistant_labeled` and the share of each, and `external_audit_at_least_100` counts **human labels only**. Assistant labels appear in the worksheet as hints (`assistant_draft_*` columns) and in the review page as a collapsed hint the owner can open; they never prefill the verdict.
- Backfill the empty `notes` on the 7 assistant `no` rows and the 11 unresolved rows from the evidence packs (what was checked, which URL, what it showed).
- Tests for each rule, including a rejected forged `owner` file.

### Task 1: registry-listed sites go through the same gate (precision, core)

- Observations (company profile, handles, metrics) may be built **only** from a site that passes the identity gate **and** the first-party gate (`--gate g3`) with its veto list. A registry-listed website does not skip either. Treat `registry_website_match` as medium evidence, not sufficient alone: an identity score of 0.95 from name tokens only is not enough; require strong evidence (the organisation number in a legal or contact position) or medium evidence plus a legal-page name match, with no contradiction.
- **Group and brand detection** for any site: flag `group_or_brand` and publish nothing when (a) the site's own name or title is dominated by a token that is not in the legal name (Akademiet vs Norges Realfagsgymnas Sandvika), (b) the legal or imprint text names a different legal entity or several entities, (c) the page lists subsidiaries or multi-country entities, or (d) several unrelated companies in the batch share the registered domain. Record the reason and `related` status; related sites may be kept as `related_only` evidence but produce no scored observations.
- Add the two real cases as regression fixtures built from the evidence packs: `creonordic.no` and `akademiet.no` must yield no scored observations.
- **Scorecard gap:** add to `score_discovery_run.py` and the proxy report a `published_on_undetermined` count with the list of organisation numbers, so a site published for a company whose annotation is `undetermined` is visible and counted as an item to review (not silently excluded). Report it in `BRAIN.md`-ready form.

### Task 2: one handle guard for all platforms

- If any platform's handles for a company are withheld as `ambiguous_handle`, withhold the other platforms too unless each handle independently shows strong name similarity to the legal name or the verified site title.
- YouTube specifically: publish a channel only when its title or handle has strong name similarity to the legal name or verified title **or** the channel's description or links point back to the company's registered domain. `@akademiet_no` must be withheld; keep the second-proof check as a recorded signal.
- Tests with the Akademiet and Creonordic examples (all platforms withheld) and with a clean company (all published).

### Task 3: NAV that cannot stall, and a measurement that means something

- Find the cause of the stalled detail request. Add per-request timeouts (default 20 s), two retries with backoff, a per-run circuit breaker (abort the slice after 5 consecutive failures), a maximum concurrency of 4, and skip-and-record `detail_timeout`. Persist progress so a rerun resumes.
- Add `--self-test`: take any employer with a valid organisation number from a live feed page and confirm that `collect()` for a synthetic profile of that organisation returns observations. This separates "no ads for these companies" from "the matching path is broken".
- Rerun the targeted lookup on the 240 development companies (90-day window, name pre-filter, exact organisation-number verification). Report requests, runtime, timeouts, matched employers, observations and coverage. A zero is acceptable only if the self-test passed.

### Task 4: the audit corpus (reach 100 human-labellable observations)

- Draw `out/audit-corpus/manifest.jsonl`: 300 companies from strata S4, S5, S6 and S7 using seed `20261009`, **excluding every organisation number in `out/eval-sample/manifest-v2.jsonl`** (all 580 evaluation and extension rows). Mark them `purpose: observation_audit`. They are never used to tune thresholds, to make website precision claims, or as evaluation data.
- Run for these companies: official batch with network, discovery with `--no-search --gate g3` and the Task 1 and Task 2 rules (no paid search), then the observation builders, the YouTube connector (official API, within 500 units), and NAV (Task 3). No Places calls.
- Export the incremental worksheet (`export`) so it contains the existing rows plus the new ones, with evidence packs, to a target of at least 100 unlabelled-by-human rows. Report the yield per company by stratum and how many rows each platform and signal contributes. If the yield is below 100, draw a second batch of 300 with the same exclusion list (seed `20261010`) and say so.

### Task 5: make the research agent report real

- Run `scripts/analysis/evaluate_research_agent.py` for real on the development profiles and observations with the original and the external suites; write `out/proxy/research-report.json` with the fields the scorer reads (`score`, `qualification_passed`, `external_footprint_qa_passed`). Fix whatever blocks it. Report the score, and if it is capped, why.

### Task 6: Places audit (no new calls)

- Do not make further Places calls. Explain from the code and the stored measurement rows how 2 of 50 candidates were accepted with zero stage-2 calls. If acceptance can happen without a phone or website signal, that violates the two-of-three rule: fix it, add a test, and mark the 2 accepted places as unverified in the measurement file. Keep Places measurement-only; do not touch the policy.

### Task 7: rerun, report, commit

- Run `run_proxy_score.py` (real and what-if). Report the gate table and, separately, observation precision from assistant labels (clearly marked as not human).
- Commit in logical chunks (suggested: provenance repair and tests; registry-site gate and group detection; handle guard; NAV fix; audit corpus tooling and manifests excluded from git if under `out/`; research run; docs). **No co-author trailers and no "generated with" lines; messages only `type: description`.** Do not push. Never commit `.env`, `out/`, `data/`, `.venv` or files over 5 MB.

## Acceptance

- The 44 existing labels are `codex_review`, the human count is 0 until the owner exports from the review page, and merging a forged `owner` file is refused.
- `creonordic.no` and `akademiet.no` produce no scored observations; `@akademiet_no` is withheld; `published_on_undetermined` is reported.
- NAV self-test result, requests, timeouts and the real coverage number.
- `out/audit-corpus/manifest.jsonl` exists with no overlap with the 580 evaluation rows (prove with a check), and the worksheet holds at least 100 rows for the owner or states why not.
- A real research report and the score it earns.
- Places: explanation and fix or confirmation, no new calls. Full suite passes; held-out untouched; no policy change.

## Report back

Append `## Results` with the tables above, the commit list (hash and message), and what the owner must do next (label the worksheet with `audit-review.html`).

## Results

### Provenance and website gates

- The 44 pre-existing observation labels were repaired to `labeler: codex_review`; all have evidence-derived notes. Human labels remain **0**. `build_observation_audit.py merge` rejects forged `owner` rows unless they carry the browser export marker and session id; assistant `no`/`unsure` rows require notes. The review-page export includes `labeler: owner`, `export_source: audit-review.html`, a random `export_session_id`, and notes without pre-filling the verdict.
- The registry-listed Creonordic site (`898467872`) and Akademiet group site (`998060257`) now fail the combined identity/first-party gate. The Akademiet `@akademiet_no` YouTube handle is withheld by the cross-platform guard. The proxy report exposes 7 `published_on_undetermined` organisation numbers: `814416232`, `898467872`, `911812401`, `981416759`, `984868677`, `994628577`, `998060257`.
- Connector policy approvals were not changed. `company_site`, `nav_jobs`, and `youtube_data_api` remain approved; `google_places_api` and `google_news_rss` remain `review_required`. Places data is measurement-only.

### Audit corpus and live measurements

| Artifact | Result |
|---|---|
| Audit manifest | 300 companies, seed `20261009`, S4/S5/S6/S7 = 100/80/60/60, zero overlap with `manifest-v2.jsonl`; SHA-256 `3ad13e6422df0d9b5c0dc6cfeb43d7faa2b3f1546191dc3f6a96847b3849f87f` |
| Official batch | 300/300 terminal envelopes; 2,532 requests; 92,002,801 bytes; p50 764 ms; p95 1,777 ms; 4 resolution failures; validation passed |
| No-search G3 | 300/300 complete; 0 provider queries; 59 verified sites; 1 related entity; 1,457 crawl requests; 59 registry-derived/email/subunit verified sites combined |
| Connector observations | 146 combined site/YouTube/NAV rows; YouTube 20 rows from 10 units; NAV 0 audit matches |
| Worksheet | `out/audit-corpus/observation-audit.csv`: 100 new, unlabelled-by-human rows; existing 44 drafts remain in `out/proxy/observation-labels.jsonl` |
| Evidence packs | 100 packs under `out/audit-corpus/evidence/`: 90 available and 10 `fetch_blocked`; no human verdict columns were filled; Playwright screenshots were not claimed because Playwright is unavailable |

The NAV bounded run recorded 502 requests, 0 timeouts, 0 detail errors and 0 circuit-breaker trips. Its self-test passed for organisation `887968942`, returning one synthetic observation after 500 detail requests. No new Places calls were made; 4 stored rows were marked `unverified_pending_stage2_audit`.

### Research and proxy report

The research report is real and saved at `out/proxy/research-report.json`: **5/12**, `external_footprint_qa_passed: true`, `qualification_passed: false`. The original single-company fixture was not present in the 240-profile development corpus, so its single-company score is 0; the external 16-case suite passed.

The external report now distinguishes `human_labeled: 0` from `assistant_labeled: 44`. Assistant-only entity precision is 0.8409 (7 wrong of 44), coverage is 0.0875, and qualification remains false. Held-out and validation were not scored.

### Provider bake-off and extension annotation

SerpApi and Linkup were each attempted on the 50-row development bake-off slice. Each consumed 6 bounded requests before its configured key was disabled, returned 50 provider errors, and produced no usable recall metric. No provider was selected and no raw search cache was written. Serper was not retried because it is exhausted; Brave and Tavily had no configured key.

The extension artifacts are:

- `out/eval-sample/extension-search-v1.jsonl` and `extension-pages-v1.jsonl`: 180 fresh direct-check rows and 386 page records (38 registry-listed sites and 348 name-derived checks).
- `out/eval-sample/annotations-ext-v1.jsonl`: 180 rows, 34 direct exact-site positives and 146 `undetermined`. No row was labelled `no_site_confirmed` because the alternative-provider check was unavailable; incomplete negative checks are forced to `undetermined`.
- `out/eval-sample/annotations-v2-plus-extension.jsonl`: 580-row merged artifact, with the original 400 rows preserved and extension rows marked `extension_v1`.
- `out/eval-sample/qc-extension-worksheet.csv`: 64-row owner QC worksheet covering all 34 positives plus the required uncertain strata.

A blind 45-row independent registry-only pass produced 95.56% agreement and Cohen’s kappa **0.6457**. The two disagreements were adjudicated from the first pass’s exact-organisation page evidence as official sites (`kp.no` for Kronstadposten and `grovenfitness.no` for Groven Fitness). Because both search-provider keys failed and 146 rows lack the alternative-provider check, this is a provisional annotation corpus, not a held-out precision certification. No held-out or validation scorecard was produced.

### Verification and commits

Final verification: `uv run --with pytest pytest -q` → **248 passed, 11 warnings, 11 subtests passed in 4.22s**; `git diff --check` and `python -m compileall -q src scripts tests` also passed.

Commit hashes and messages will be listed here after the final suite passes and the nine logical commit chunks are created. The owner must next open `out/audit-corpus/audit-review.html` (and, for the extension, `out/eval-sample/qc-extension-worksheet.csv`) and label rows manually; only browser-exported `owner` labels count toward the external audit gate. Do not score held-out or validation until those labels and the required independent search evidence exist.
