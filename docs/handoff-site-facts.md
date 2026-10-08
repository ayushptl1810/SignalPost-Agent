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

## Acceptance

- Fixture tests for each extractor (JSON-LD variants, RSS, sitemap lastmod, Norwegian dates, careers JSON-LD, "no open roles" wording, aggregator rejection, undated items rejected, robots-blocked page) and for the state rules; envelope validator and audit tests; a test that the report HTML builds from the committed smoke result and contains no external URLs except source links. `uv run --with pytest pytest -q` passes (state the count).
- `scripts/run/clean_machine_check.sh` passes at your final commit.
- No keyed API call in the default path (the existing test must still pass); default third-party cost stays 0.
- No gate weakened.

## Report back

Append `## Results` with the commit list, the branch name, the test count, extractor hit rates on your fixtures, the section-completeness summary on the committed smoke result, and anything you could not verify offline. The planner will run the live 100- and 1,000-company passes, audit the new claims by hand, and time the run.
