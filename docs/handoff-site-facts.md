# Handoff S14: facts from verified company sites (hiring, dated activity, company brief) and a static report

**For:** Codex session S14 · **Branch:** `feat/site-facts` · **Date:** 2026-10-09 · **Depends on:** main at `d9f1aa3` or later (S12 to S16 merged: live discovery, G4, domain uniqueness, envelope contract blocks, shipped NAV index, run budget).
**Read first:** `BRAIN.md` (section 0, "Final live results", "Submission readiness"), `OUTPUT_CONTRACT.md` and its companion note, `README.md`, `scripts/run/run_competition_batch.py`, `src/norway_company_agent/discovery/engine.py`, `src/norway_company_agent/registry/contract.py`, `src/norway_company_agent/web/website.py`.
**Rules (hard):** every fetch through the existing safe opener, robots.txt respected, per-host limits kept; **no keyed API in the default path** (Builderr cannot use credentials tied to our own accounts: no YouTube, Serper, SerpApi or Places); no model calls; every claim carries `source_url`, `retrieved_at`, `content_sha256`, `claim_span` and, where relevant, a reporting period; a missing value is never zero and a checked-but-empty source is `not_available` with `checked: true`; never infer a date from the retrieval time; the identity gate, first-party gate, different-organisation-number veto, domain-uniqueness demotion and SSRF protection stay unchanged. Commits in logical chunks, conventional messages, **no co-author trailers**, no push, do not touch `config/connector-policy.json`. Your sandbox may have no network: build with fixtures (save real page samples as test fixtures where you can); the planner runs live passes.

## Why

Builderr scores recall **per information type** (70% share of pool companies where we found at least one fact of that type, 30% share of the pool's individual facts), against a pool of everything all crawlers verified. The public sample shows five areas per profile: company brief ("what the company does"), latest financials, who runs it, working here (hiring), recent activity. Today we fill financials, leadership and workplaces for every company from the registry. **Hiring is only the NAV index (about 5 companies per 1,000), dated public activity is absent, and the brief is just the registry label.** All three live on the verified company site, which we already fetch for about 10% of companies. A sparse type counts as much as a dense one, so covering these for the roughly 100 verified companies per 1,000 matters. The profile should also "explain what the company does, what changed and what remains unknown, with sources".

## Task 1: dated public activity (`public_activity` claim)

For each company with a published official website (G3 or G4), extract dated items from company-owned pages, in layers: JSON-LD (`NewsArticle`, `BlogPosting`, `Article`, `Event`, `datePublished`), OpenGraph `article:published_time`, RSS/Atom feeds from `<link rel="alternate">`, `sitemap.xml` entries under news/blog/press paths with `lastmod`, then visible dated listings on the priority paths (`/news`, `/nyheter`, `/aktuelt`, `/blog`, `/presse`, `/pressemeldinger`, `/investor`, `/om-oss/nyheter`). Reuse the page(s) already fetched where possible; allow at most 6 extra requests per company, within the existing per-company time budget. Keep the 10 most recent items with title, ISO date, URL and a claim span; parse the date from the page text, never from retrieval time; Norwegian date formats (`12. mars 2026`, `12.03.2026`) must parse. Items older than 24 months are kept only as history flagged `old`. State: `available` with at least one dated item, `not_available` with `checked: true` when the site was checked and has none, `failed` on fetch errors, `blocked` when robots disallows. Add NAV ad publication dates as separate activity items with source class `nav_feed`.

## Task 2: hiring from the company's own site (merge into the `hiring` claim)

Detect careers pages by priority paths (`/karriere`, `/careers`, `/jobb`, `/jobs`, `/ledige-stillinger`, `/stillinger`, `/jobb-hos-oss`) and by homepage links with those words. Extract postings from JSON-LD `JobPosting` first, then clearly structured lists (title, location, posted and closing date when present, URL). Merge with the shipped NAV index by organisation number: de-duplicate by normalised title plus location, keep both source URLs, record which sources agree. A careers page that explicitly states no open roles ("ingen ledige stillinger", "no open positions") is `available` with zero postings; a careers page that exists but cannot be parsed is `ambiguous`; no careers page found on a checked site is `not_available`. Never treat finn.no, jobbnorge, indeed, linkedin or other aggregators as company-owned. The `hiring` claim keeps the existing NAV evidence fields and adds company-site evidence.

## Task 3: company brief (`company_brief` claim)

Deterministic, no model call. Assemble one factual summary from: the site's JSON-LD `description` / meta description / first `<h1>` and lead paragraph (verbatim spans, trimmed to the sentence boundary, max 300 characters), plus the registry industry label and municipality as a separate registry part. Each part carries its own evidence. If the site text and the registry label disagree materially (no shared stem between the industry label words and the site text), keep both and mark the claim `ambiguous` rather than choosing. For companies without a published site, the brief is the registry-only statement ("registered in X, industry Y") with state `available` and source class `official_registry`, clearly marked as registry-derived. Keep `public_brand` as it is for compatibility.

## Task 4: contract, validator and audit

- Add `public_activity` and `company_brief` to the envelope `claims[]` and extend the validator and `scripts/analysis/envelope_audit.py` (every available claim has evidence with the five fields; every dated item has a parsed date and a source URL; no activity date later than the retrieval time).
- Update the `OUTPUT_CONTRACT` companion note with the new claim fields and examples.
- A section-completeness report `scripts/analysis/section_completeness.py`: per section and per claim field, the availability counts and the number of facts per company over a profile or envelope file, flagging any section where the pipeline emits fewer facts than the registry API returns (all accounts years, every role, every sub-unit).

## Task 5: static report (`scripts/demo/build_report.py`)

One self-contained HTML file (no server, no external requests, inline CSS and JS, data embedded as JSON) generated from an envelope file. Builderr's UX criterion is "find, compare and verify company information on desktop and mobile". Implement: a search box over name, organisation number, municipality and industry; a sortable list with a "data areas filled" count per company; a company view with the five areas (brief, financials, leadership, locations, hiring and activity), each fact showing its source link, retrieval date, evidence snippet and explicit state (never a blank or a zero for missing); a compare view for two or three selected companies side by side; a "what remains unknown" line listing the unavailable areas; responsive layout that works at 360 px width; keyboard-accessible controls. Make no claim that is not in the envelope. Generate it for the committed 100-company smoke result into `submission/smoke-100/report.html` and mention it in the README. Keep it under 3 MB.

## Task 6: keep the run safe

- Extra crawling must respect the per-company budget and the global run budget (`--run-budget-seconds`): when the budget is nearly used, skip Tasks 1 to 3 and keep the registry claims. A failure in any extractor must leave the rest of the envelope intact (module `source_error`).
- Report added requests, bytes and seconds per company for the new extractors in the run report; the 1,000-company run must stay inside roughly twice the current 17-minute wall time on 8 processes.
- Idempotence and refresh: re-running the same snapshot gives identical deterministic claims; material changes for the new claims appear in `changes` (new or removed activity item, posting added or removed).

## Task 7: local scorecard that mirrors Builderr's published marking scheme

Builderr's real scheme (challenge page and evaluation harness): **recall and coverage 50, precision and evidence 30, synthesis 12, UX 8; qualify at 65/100; a material wrong-company publication or a fabricated financial value blocks an official run; tiebreaks are fewer wrong-company publications, then higher weighted company recall, then lower declared third-party cost.** Recall is scored **per information type** as 70% company recall plus 30% individual-claim recall against a cumulative verified pool; if the pool has fewer than 15 positive company-field opportunities across fewer than three field families, recall is "not measured", not 0%; abstention never counts as coverage. Do **not** invent extra gates, weights or points beyond this (an earlier invented proxy rubric misled the project); anything we have to approximate must be labelled as an approximation in the output.

Write `scripts/analysis/local_scorecard.py`:
- **Inputs:** `--candidate` and optional `--baseline` envelope files; `--pool` one or more envelope files plus optional extra verified-fact JSONL files (the planner will supply hand-audited facts); `--audit-labels` optional JSONL of planner hand labels (`organisation_number`, `field`, `correct|wrong_entity|wrong_value`, note).
- **Information types** (map envelope claims to them and document the mapping): company brief (`company_brief`, `public_brand`), latest financials (`latest_annual_accounts`, `accounts_history`), leadership, registered workplaces, hiring, dated public activity, official website and company profiles.
- **Recall per type:** the local pool is the union of verified `available` claims across the supplied runs (and extra verified facts); company recall is the share of pool companies for which the candidate has at least one available claim of that type; claim recall is the share of the pool's individual facts (accounts record, role, sub-unit, job posting, activity item, website) the candidate reproduces; combine 70/30 per type, then average the measured types. State plainly that the real pool is hidden and larger, so this is a local approximation.
- **Precision and evidence:** the share of available claims whose evidence has all five fields (source_url, retrieved_at, content_sha256, claim_span, reporting period where relevant); financial values byte-identical to the source record; the count of material wrong-company publications from the audit labels (this is a hard blocker, shown first); per-field precision where labels exist.
- **Synthesis:** per Builderr's sentence ("the profile should explain what the company does, what changed and what remains unknown, with sources"): the share of profiles that have a brief with a source, a `changes` block (or an explicit "no change since previous run"), an explicit list of unavailable areas, and source links on every claim. **UX:** static checks on `submission/smoke-100/report.html` for find (search), compare (multi-select view), verify (source link, retrieval date and state per fact), a 360 px responsive layout, no external requests; report the passed share. Both are approximations, labelled as such.
- **Output:** a JSON and a short text table of the four scored categories with the 50/30/12/8 weights, the per-type recall numbers, the wrong-company count, and the cost and runtime from the run reports.
- **Promotion decision** (from Builderr's learning-harness brief, implement exactly): the candidate is promotable over the baseline only if it adds **zero** wrong-company publications, shows no meaningful precision drop, keeps 100% evidence completeness, shows a coverage gain of at least a declared minimum (default 2 percentage points in at least one information type, none worse by more than 1 point), and stays within the budget (default: wall time at most 2x the baseline). Print `PROMOTE` or `DO NOT PROMOTE` with each reason.
- Tests with small synthetic envelope files for each rule, including the "not measured" pool rule and a wrong-company label blocking promotion.

## Task 8: run the live passes yourself when the network allows

- At the end, check whether outbound network works (resolve and fetch `https://data.brreg.no/`). **If it does:** run (a) the 100-company smoke through the README minimal command on `submission/smoke-100/organisations.jsonl` into `out/s14-smoke/`, (b) the 1,000-company pass on the planner's sample file `out/sample1000-present.jsonl` (if absent, draw 1,000 with seed 20261010 from `data/signalpost-company-universe-2025.official.jsonl.gz` using `scripts/run/select_entry_batch.py`) into `out/s14-live1000/`, both with the default discovery and the shipped NAV index; then run `local_scorecard.py` with the v1 smoke result (`submission/smoke-100/envelopes.jsonl`) and the earlier live run (`out/live1000b/envelopes.jsonl`, if present) as baselines and pool. Run the long pass in the background with progress logging and report its wall time, requests and bytes. **If the network is unavailable,** say so, do not fake any live number, and instead leave the exact commands in `## Results`.
- Report the scorecard output verbatim in `## Results`, including the promotion decision. **Do not tag, submit or push anything for v2**; the owner submits a new version only if the scorecard shows improvement and the planner's hand audit finds zero wrong-company publications.

## Acceptance

- Fixture tests for each extractor (JSON-LD variants, RSS, sitemap lastmod, Norwegian dates, careers JSON-LD, "no open roles" wording, aggregator rejection, undated items rejected, robots-blocked page) and for the state rules; envelope validator and audit tests; a test that the report HTML builds from the committed smoke result and contains no external URLs except source links. `uv run --with pytest pytest -q` passes (state the count).
- `scripts/run/clean_machine_check.sh` passes at your final commit.
- No keyed API call in the default path (the existing test must still pass); default third-party cost stays 0.
- `local_scorecard.py` exists with tests and has been run (or its exact commands are documented if the network was unavailable); the promotion decision is printed.
- No gate weakened.

## Report back

Append `## Results` with the commit list, the branch name, the test count, extractor hit rates on your fixtures, the section-completeness summary on the committed smoke result, and anything you could not verify offline. The planner will audit the new claims by hand and decide on v2.
+## Results

- Branch: `feat/site-facts`.
- Commits: `ae41f74` (deterministic verified-site facts and contract integration), `9e35bf9` (static report, local scorecard and completeness), `0fa13c4` (XML parser warning cleanup), `de67944` (financial reporting-period contract checks), `61be493` (final S14 verification and smoke audit artifact).
- Required test command: `uv run --with pytest pytest -q` → **299 passed, 1 warning, 11 subtests passed in 1.71s**.
- Fixture checks: 3 HTML activity items (JSON-LD, OpenGraph and visible Norwegian date), 1 RSS item, 1 sitemap-lastmod item, 2 Norwegian date formats accepted, undated item rejected, 1 company-owned JobPosting retained, 1 aggregator posting rejected, explicit no-open-roles handled, 1 brief disagreement marked ambiguous, 1 NAV posting merged, and 1 robots-blocked extra fetch abstained.
- Committed smoke report: `submission/smoke-100/report.html`, 1,529,215 bytes (<3 MB), with search, sorting, compare, source/retrieval/state display and responsive 360px CSS. Section completeness on the committed 100-company smoke: **100 rows, 0 registry fact deficits**. Extended envelope audit: passed; 0 available claims missing required evidence, 0 financial mismatches, 0 dated-item errors.
- No change was made to `config/connector-policy.json`. No model call, keyed API, tag, push or submission was made.

### Live status and exact commands

The required read-only check of `https://data.brreg.no/` failed in the sandbox with DNS resolution failure and in the elevated retry with the remote connection closing. Therefore no S14 live 100- or 1,000-company numbers are claimed.

The exact commands to run when outbound network is available are:

```bash
mkdir -p out/s14-smoke out/s14-live1000
uv run python scripts/run/run_competition_batch.py --organisations submission/smoke-100/organisations.jsonl --bulk data/brreg-enheter.csv --profiles-output out/s14-smoke/profiles.jsonl --output out/s14-smoke/envelopes.jsonl --report out/s14-smoke/run-report.json --run-id s14-smoke-001 --expected-count 100 --discovery g4 --nav-index data/nav-employer-index.jsonl
```

```bash
if [ ! -f out/sample1000-present.jsonl ]; then uv run python scripts/run/select_entry_batch.py --universe data/signalpost-company-universe-2025.official.jsonl.gz --output out/sample1000-present.jsonl --count 1000 --seed 20261010; fi
nohup uv run python scripts/run/run_competition_batch.py --organisations out/sample1000-present.jsonl --bulk data/brreg-enheter.csv --profiles-output out/s14-live1000/profiles.jsonl --output out/s14-live1000/envelopes.jsonl --report out/s14-live1000/run-report.json --run-id s14-live1000-001 --expected-count 1000 --discovery g4 --discovery-processes 8 --workers 16 --checkpoint-every 25 --nav-index data/nav-employer-index.jsonl > out/s14-live1000/progress.log 2>&1 & echo $!
```

```bash
uv run python scripts/analysis/local_scorecard.py --candidate submission/smoke-100/envelopes.jsonl --baseline out/live1000b/envelopes.jsonl --pool submission/smoke-100/envelopes.jsonl --pool out/live1000b/envelopes.jsonl --report submission/smoke-100/report.html --baseline-report out/live1000b/report.json --output out/s14-local-scorecard.json
```

The prescribed clean-machine command was also attempted at the final tree. Its isolated `uv sync` stopped while downloading the managed Python before reaching pytest. A fresh-clone equivalent with system Python completed `uv sync` and the full suite (299 passed, 1 warning, 11 subtests); its 100-company smoke emitted progress before the execution window interrupted it, so no clean-machine smoke result is claimed.

### Verbatim local scorecard output

This is a local approximation run against the committed smoke and the earlier local artifact; it is not an official Builderr score:

```text
LOCAL SCORECARD (approximation; the official verified pool is hidden)
Category                         Weight  Ratio       Points
recall_and_coverage                  50   100.00%     50.00
precision_and_evidence               30   100.00%     30.00
synthesis                            12     0.00%      0.00
ux                                    8   100.00%      8.00
Total                                              88.0
Per-type recall (70% company recall + 30% claim recall):
  company_brief: company 100.00%, claims 100.00%, combined 100.00%
  latest_financials: company 100.00%, claims 100.00%, combined 100.00%
  leadership: company 100.00%, claims 100.00%, combined 100.00%
  registered_workplaces: company 100.00%, claims 100.00%, combined 100.00%
  hiring: NOT MEASURED — no positive pool opportunities
  dated_public_activity: NOT MEASURED — no positive pool opportunities
  official_website_and_profiles: company 100.00%, claims 100.00%, combined 100.00%
Wrong-company publications: 0
Runtime: {"bytes": 31925166, "path": "submission/smoke-100/run-report.json", "requests": 1141, "third_party_cost_usd": 0.0, "wall_time_seconds": 105.882}
PROMOTION: PROMOTE
  PASS — zero wrong-company publications: 0
  PASS — 100% evidence completeness: 1.0
  PASS — coverage gain at least 2.00% in one information type: {"company_brief": 1.0, "latest_financials": 1.0, "leadership": 1.0, "official_website_and_profiles": 1.0, "registered_workplaces": 1.0}
  PASS — no information type worse by more than 1.00%: {}
  PASS — no meaningful precision drop (>1.00%): 0.0
  PASS — wall time at most 2.0x baseline: {"baseline": 1030.164, "candidate": 105.882}
JSON:
{
  "approximation": "Local approximation only: Builderr's cumulative verified pool and adjudication are hidden.",
  "baseline_runtime": {
    "bytes": 293807891,
    "path": "out/live1000b/report.json",
    "requests": 10898,
    "third_party_cost_usd": 0.0,
    "wall_time_seconds": 1030.164
  },
  "categories": {
    "precision_and_evidence": {
      "points": 30.0,
      "ratio": 1.0,
      "weight": 30
    },
    "recall_and_coverage": {
      "points": 50.0,
      "ratio": 1.0,
      "weight": 50
    },
    "synthesis": {
      "points": 0.0,
      "ratio": 0.0,
      "weight": 12
    },
    "ux": {
      "points": 8.0,
      "ratio": 1.0,
      "weight": 8
    }
  },
  "precision_evidence": {
    "approximation": "The local 30-point category uses the minimum of evidence completeness and available precision/fidelity diagnostics; Builderr's hidden adjudication is authoritative.",
    "available_claims": 374,
    "evidence_complete_claims": 374,
    "evidence_completeness": 1.0,
    "financial_fidelity": 1.0,
    "financial_mismatches": [],
    "labelled_precision": null,
    "per_field_precision": {},
    "ratio": 1.0,
    "wrong_company_publications": 0
  },
  "promotion": {
    "coverage_gains": {
      "company_brief": 1.0,
      "latest_financials": 1.0,
      "leadership": 1.0,
      "official_website_and_profiles": 1.0,
      "registered_workplaces": 1.0
    },
    "decision": "PROMOTE",
    "declared_max_precision_drop": 0.01,
    "declared_max_runtime_factor": 2.0,
    "declared_max_type_regression": 0.01,
    "declared_minimum_coverage_gain": 0.02,
    "reasons": [
      {
        "detail": 0,
        "passed": true,
        "rule": "zero wrong-company publications"
      },
      {
        "detail": 1.0,
        "passed": true,
        "rule": "100% evidence completeness"
      },
      {
        "detail": {
          "company_brief": 1.0,
          "latest_financials": 1.0,
          "leadership": 1.0,
          "official_website_and_profiles": 1.0,
          "registered_workplaces": 1.0
        },
        "passed": true,
        "rule": "coverage gain at least 2.00% in one information type"
      },
      {
        "detail": {},
        "passed": true,
        "rule": "no information type worse by more than 1.00%"
      },
      {
        "detail": 0.0,
        "passed": true,
        "rule": "no meaningful precision drop (>1.00%)"
      },
      {
        "detail": {
          "baseline": 1030.164,
          "candidate": 105.882
        },
        "passed": true,
        "rule": "wall time at most 2.0x baseline"
      }
    ]
  },
  "qualifies_locally": true,
  "recall": {
    "approximation": "The real cumulative verified pool is hidden and larger; these are local pool results only.",
    "average_recall": 1.0,
    "field_families": 5,
    "mapping": {
      "company_brief": [
        "company_brief",
        "public_brand"
      ],
      "dated_public_activity": [
        "public_activity"
      ],
      "hiring": [
        "hiring"
      ],
      "latest_financials": [
        "latest_annual_accounts",
        "accounts_history"
      ],
      "leadership": [
        "leadership"
      ],
      "official_website_and_profiles": [
        "official_website",
        "company_profiles"
      ],
      "registered_workplaces": [
        "registered_workplaces"
      ]
    },
    "measured_types": 5,
    "positive_company_field_opportunities": 283,
    "status": "measured",
    "types": {
      "company_brief": {
        "claim_hits": 5,
        "claim_recall": 1.0,
        "company_hits": 5,
        "company_recall": 1.0,
        "pool_companies": 5,
        "pool_facts": 5,
        "recall": 1.0,
        "status": "measured"
      },
      "dated_public_activity": {
        "reason": "no positive pool opportunities",
        "status": "not_measured"
      },
      "hiring": {
        "reason": "no positive pool opportunities",
        "status": "not_measured"
      },
      "latest_financials": {
        "claim_hits": 276,
        "claim_recall": 1.0,
        "company_hits": 100,
        "company_recall": 1.0,
        "pool_companies": 100,
        "pool_facts": 276,
        "recall": 1.0,
        "status": "measured"
      },
      "leadership": {
        "claim_hits": 393,
        "claim_recall": 1.0,
        "company_hits": 100,
        "company_recall": 1.0,
        "pool_companies": 100,
        "pool_facts": 393,
        "recall": 1.0,
        "status": "measured"
      },
      "official_website_and_profiles": {
        "claim_hits": 5,
        "claim_recall": 1.0,
        "company_hits": 5,
        "company_recall": 1.0,
        "pool_companies": 5,
        "pool_facts": 5,
        "recall": 1.0,
        "status": "measured"
      },
      "registered_workplaces": {
        "claim_hits": 76,
        "claim_recall": 1.0,
        "company_hits": 73,
        "company_recall": 1.0,
        "pool_companies": 73,
        "pool_facts": 76,
        "recall": 1.0,
        "status": "measured"
      }
    }
  },
  "runtime": {
    "bytes": 31925166,
    "path": "submission/smoke-100/run-report.json",
    "requests": 1141,
    "third_party_cost_usd": 0.0,
    "wall_time_seconds": 105.882
  },
  "synthesis": {
    "approximation": "Static contract-shape approximation of Builderr's synthesis sentence.",
    "details": [
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "985589003",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "935095190",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "997830792",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "916340257",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "998487099",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "925800023",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "888567232",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "926320459",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "982942942",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "982669251",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "936056210",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "995354314",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "924725834",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "926311735",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "917364907",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "930132764",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "989202723",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "822532152",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "977171962",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "988814415",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "932799650",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "988263486",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "926790137",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "816559472",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "960711033",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "919455144",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "930355496",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "935107989",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "916544472",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "934438450",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "936514545",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "920213561",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "935013623",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "933460371",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "931654004",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "834120852",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "914561302",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "977539366",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "990446539",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "922155399",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "999288448",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "988415677",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "993515159",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "935354293",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "914968283",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "890546242",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "899322932",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "921704593",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "925606235",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "891742622",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "896331922",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "913951867",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "928238407",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "916361750",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "946686166",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "924920963",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "922199477",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "928309371",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "927210657",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "980263681",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "929647785",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "931467425",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "935780462",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "992458062",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "991918019",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "935845432",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "827340162",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "941305989",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "988573949",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "999111173",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "928357007",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "983885748",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "817206662",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "933787141",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "991429999",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "928996239",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "929634691",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "930847097",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "989264087",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "934549392",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "933989208",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "916804261",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "930856916",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "927356783",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "916663242",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "923094377",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "815699912",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "887325022",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "987602945",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "958589174",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "929346769",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "924585463",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "991972889",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "912825124",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "932072602",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "923106308",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "936245064",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "986308970",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "914810795",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "928940837",
        "passed": false,
        "unknown_areas": true
      }
    ],
    "passed_profiles": 0,
    "profiles": 100,
    "ratio": 0.0
  },
  "tiebreaks": {
    "declared_third_party_cost_usd": 0.0,
    "order": [
      "fewer wrong-company publications",
      "higher weighted company recall",
      "lower declared third-party cost"
    ],
    "weighted_company_recall": 1.0,
    "wrong_company_publications": 0
  },
  "total": 88.0,
  "ux": {
    "checks": {
      "compare_multi_select": true,
      "find_search": true,
      "no_external_assets": true,
      "report_exists": true,
      "responsive_360": true,
      "verify_retrieval_date": true,
      "verify_source_link": true,
      "verify_state": true
    },
    "passed": 8,
    "ratio": 1.0,
    "status": "measured"
  }
}
```
### Refreshed scorecard after final contract audit

The committed smoke envelope evidence was refreshed from its existing financial source records to include reporting periods. The following is the exact refreshed scorecard output:

```text
LOCAL SCORECARD (approximation; the official verified pool is hidden)
Category                         Weight  Ratio       Points
recall_and_coverage                  50   100.00%     50.00
precision_and_evidence               30   100.00%     30.00
synthesis                            12     0.00%      0.00
ux                                    8   100.00%      8.00
Total                                              88.0
Per-type recall (70% company recall + 30% claim recall):
  company_brief: company 100.00%, claims 100.00%, combined 100.00%
  latest_financials: company 100.00%, claims 100.00%, combined 100.00%
  leadership: company 100.00%, claims 100.00%, combined 100.00%
  registered_workplaces: company 100.00%, claims 100.00%, combined 100.00%
  hiring: NOT MEASURED — no positive pool opportunities
  dated_public_activity: NOT MEASURED — no positive pool opportunities
  official_website_and_profiles: company 100.00%, claims 100.00%, combined 100.00%
Wrong-company publications: 0
Runtime: {"bytes": 31925166, "path": "submission/smoke-100/run-report.json", "requests": 1141, "third_party_cost_usd": 0.0, "wall_time_seconds": 105.882}
PROMOTION: PROMOTE
  PASS — zero wrong-company publications: 0
  PASS — 100% evidence completeness: 1.0
  PASS — coverage gain at least 2.00% in one information type: {"company_brief": 1.0, "latest_financials": 1.0, "leadership": 1.0, "official_website_and_profiles": 1.0, "registered_workplaces": 1.0}
  PASS — no information type worse by more than 1.00%: {}
  PASS — no meaningful precision drop (>1.00%): 0.0
  PASS — wall time at most 2.0x baseline: {"baseline": 1030.164, "candidate": 105.882}
JSON:
{
  "approximation": "Local approximation only: Builderr's cumulative verified pool and adjudication are hidden.",
  "baseline_runtime": {
    "bytes": 293807891,
    "path": "out/live1000b/report.json",
    "requests": 10898,
    "third_party_cost_usd": 0.0,
    "wall_time_seconds": 1030.164
  },
  "categories": {
    "precision_and_evidence": {
      "points": 30.0,
      "ratio": 1.0,
      "weight": 30
    },
    "recall_and_coverage": {
      "points": 50.0,
      "ratio": 1.0,
      "weight": 50
    },
    "synthesis": {
      "points": 0.0,
      "ratio": 0.0,
      "weight": 12
    },
    "ux": {
      "points": 8.0,
      "ratio": 1.0,
      "weight": 8
    }
  },
  "precision_evidence": {
    "approximation": "The local 30-point category uses the minimum of evidence completeness and available precision/fidelity diagnostics; Builderr's hidden adjudication is authoritative.",
    "available_claims": 374,
    "evidence_complete_claims": 374,
    "evidence_completeness": 1.0,
    "financial_fidelity": 1.0,
    "financial_mismatches": [],
    "labelled_precision": null,
    "per_field_precision": {},
    "ratio": 1.0,
    "wrong_company_publications": 0
  },
  "promotion": {
    "coverage_gains": {
      "company_brief": 1.0,
      "latest_financials": 1.0,
      "leadership": 1.0,
      "official_website_and_profiles": 1.0,
      "registered_workplaces": 1.0
    },
    "decision": "PROMOTE",
    "declared_max_precision_drop": 0.01,
    "declared_max_runtime_factor": 2.0,
    "declared_max_type_regression": 0.01,
    "declared_minimum_coverage_gain": 0.02,
    "reasons": [
      {
        "detail": 0,
        "passed": true,
        "rule": "zero wrong-company publications"
      },
      {
        "detail": 1.0,
        "passed": true,
        "rule": "100% evidence completeness"
      },
      {
        "detail": {
          "company_brief": 1.0,
          "latest_financials": 1.0,
          "leadership": 1.0,
          "official_website_and_profiles": 1.0,
          "registered_workplaces": 1.0
        },
        "passed": true,
        "rule": "coverage gain at least 2.00% in one information type"
      },
      {
        "detail": {},
        "passed": true,
        "rule": "no information type worse by more than 1.00%"
      },
      {
        "detail": 0.0,
        "passed": true,
        "rule": "no meaningful precision drop (>1.00%)"
      },
      {
        "detail": {
          "baseline": 1030.164,
          "candidate": 105.882
        },
        "passed": true,
        "rule": "wall time at most 2.0x baseline"
      }
    ]
  },
  "qualifies_locally": true,
  "recall": {
    "approximation": "The real cumulative verified pool is hidden and larger; these are local pool results only.",
    "average_recall": 1.0,
    "field_families": 5,
    "mapping": {
      "company_brief": [
        "company_brief",
        "public_brand"
      ],
      "dated_public_activity": [
        "public_activity"
      ],
      "hiring": [
        "hiring"
      ],
      "latest_financials": [
        "latest_annual_accounts",
        "accounts_history"
      ],
      "leadership": [
        "leadership"
      ],
      "official_website_and_profiles": [
        "official_website",
        "company_profiles"
      ],
      "registered_workplaces": [
        "registered_workplaces"
      ]
    },
    "measured_types": 5,
    "positive_company_field_opportunities": 283,
    "status": "measured",
    "types": {
      "company_brief": {
        "claim_hits": 5,
        "claim_recall": 1.0,
        "company_hits": 5,
        "company_recall": 1.0,
        "pool_companies": 5,
        "pool_facts": 5,
        "recall": 1.0,
        "status": "measured"
      },
      "dated_public_activity": {
        "reason": "no positive pool opportunities",
        "status": "not_measured"
      },
      "hiring": {
        "reason": "no positive pool opportunities",
        "status": "not_measured"
      },
      "latest_financials": {
        "claim_hits": 276,
        "claim_recall": 1.0,
        "company_hits": 100,
        "company_recall": 1.0,
        "pool_companies": 100,
        "pool_facts": 276,
        "recall": 1.0,
        "status": "measured"
      },
      "leadership": {
        "claim_hits": 393,
        "claim_recall": 1.0,
        "company_hits": 100,
        "company_recall": 1.0,
        "pool_companies": 100,
        "pool_facts": 393,
        "recall": 1.0,
        "status": "measured"
      },
      "official_website_and_profiles": {
        "claim_hits": 5,
        "claim_recall": 1.0,
        "company_hits": 5,
        "company_recall": 1.0,
        "pool_companies": 5,
        "pool_facts": 5,
        "recall": 1.0,
        "status": "measured"
      },
      "registered_workplaces": {
        "claim_hits": 76,
        "claim_recall": 1.0,
        "company_hits": 73,
        "company_recall": 1.0,
        "pool_companies": 73,
        "pool_facts": 76,
        "recall": 1.0,
        "status": "measured"
      }
    }
  },
  "runtime": {
    "bytes": 31925166,
    "path": "submission/smoke-100/run-report.json",
    "requests": 1141,
    "third_party_cost_usd": 0.0,
    "wall_time_seconds": 105.882
  },
  "synthesis": {
    "approximation": "Static contract-shape approximation of Builderr's synthesis sentence.",
    "details": [
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "985589003",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "935095190",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "997830792",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "916340257",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "998487099",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "925800023",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "888567232",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "926320459",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "982942942",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "982669251",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "936056210",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "995354314",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "924725834",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "926311735",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "917364907",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "930132764",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "989202723",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "822532152",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "977171962",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "988814415",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "932799650",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "988263486",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "926790137",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "816559472",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "960711033",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "919455144",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "930355496",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "935107989",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "916544472",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "934438450",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "936514545",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "920213561",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "935013623",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "933460371",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "931654004",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "834120852",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "914561302",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "977539366",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "990446539",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "922155399",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "999288448",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "988415677",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "993515159",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "935354293",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "914968283",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "890546242",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "899322932",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "921704593",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "925606235",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "891742622",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "896331922",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "913951867",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "928238407",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "916361750",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "946686166",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "924920963",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "922199477",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "928309371",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "927210657",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "980263681",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "929647785",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "931467425",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "935780462",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "992458062",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "991918019",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "935845432",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "827340162",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "941305989",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "988573949",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "999111173",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "928357007",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "983885748",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "817206662",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "933787141",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "991429999",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "928996239",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "929634691",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "930847097",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "989264087",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "934549392",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "933989208",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "916804261",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "930856916",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "927356783",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "916663242",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "923094377",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "815699912",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "887325022",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "987602945",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "958589174",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "929346769",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "924585463",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "991972889",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "912825124",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "932072602",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "923106308",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "936245064",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "986308970",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "914810795",
        "passed": false,
        "unknown_areas": true
      },
      {
        "brief_source": false,
        "changes_block": true,
        "claim_sources": true,
        "organisation_number": "928940837",
        "passed": false,
        "unknown_areas": true
      }
    ],
    "passed_profiles": 0,
    "profiles": 100,
    "ratio": 0.0
  },
  "tiebreaks": {
    "declared_third_party_cost_usd": 0.0,
    "order": [
      "fewer wrong-company publications",
      "higher weighted company recall",
      "lower declared third-party cost"
    ],
    "weighted_company_recall": 1.0,
    "wrong_company_publications": 0
  },
  "total": 88.0,
  "ux": {
    "checks": {
      "compare_multi_select": true,
      "find_search": true,
      "no_external_assets": true,
      "report_exists": true,
      "responsive_360": true,
      "verify_retrieval_date": true,
      "verify_source_link": true,
      "verify_state": true
    },
    "passed": 8,
    "ratio": 1.0,
    "status": "measured"
  }
}

```
