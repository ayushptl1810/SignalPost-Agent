# Handoff S14: facts from verified company sites (hiring, dated activity, what the company does)

**For:** Codex session S14 · **Branch:** `feat/site-facts` · **Date:** 2026-10-08 · **Depends on:** S13 (gate G4, cache, NAV) merged. Offline build and fixture tests only if your sandbox has no network; the planner runs live passes.
**Read first:** `BRAIN.md` sections 0, 8, 10; `docs/temp.md` (starter-brief review); `OUTPUT_CONTRACT.md`; `src/norway_company_agent/web/` (crawl, structured data); the cache schema in `scripts/run/build_universe_cache.py`.
**Rules:** every fetch through the safe opener; robots.txt and per-host limits; no scraping of platforms that forbid it; every material claim carries source URL, retrieval time, content hash, claim span (and reporting period where relevant); a missing value is never zero; the different-organisation-number veto and the gates are unchanged. No co-author trailers, no push, do not touch `config/connector-policy.json`.

## Why

Builderr scores recall **per information type** (70% share of pool companies where we found at least one fact of that type, 30% share of the pool's individual facts), against a verified pool from all crawlers. The starter-brief and the public sample show five areas per profile: company brief (what it does), latest financials, who runs it, working here (hiring), recent activity (dated). The starter pipeline already fills financials, roles, sub-unit locations and the registry brief for every company (100/100 `complete` in the smoke run). **Hiring is covered only by NAV and dated public activity is not covered at all**, and a type with a sparse pool counts as much as a dense one. Both live mostly on the verified company site, which is why website recall matters: it is the gateway to these types. A company with no site stays an honest `not_available`.

## Task 1: dated public activity

For each company with a published official website, extract dated items from company-owned pages, in layers: structured data first (JSON-LD `NewsArticle`/`BlogPosting`/`Article`/`Event`/`datePublished`, OpenGraph `article:published_time`), then RSS/Atom feeds discovered from `<link rel=alternate>`, then `sitemap.xml` entries under news/blog/press/aktuelt/nyheter/presse/investor paths with `lastmod`, then visible dated listings on the priority paths (`/news`, `/nyheter`, `/aktuelt`, `/blog`, `/presse`, `/investor`). Keep at most the 10 most recent items per company with title, date (ISO), URL, and a claim span; date must be parsed from the page, never inferred from retrieval time. Items older than 24 months are kept only as history flagged `old`. Output claim field `public_activity`, state `available` when at least one dated item, `not_available` when the site was checked and has none, `failed` on fetch errors. Add NAV-ad publication dates and, when a company-owned YouTube channel is linked from the site, the latest video titles and dates through the YouTube Data API (approved in the connector policy; respect the daily quota; skip when the key is absent).

## Task 2: hiring from the company's own site

Detect careers pages by priority paths (`/careers`, `/karriere`, `/jobs`, `/jobb`, `/ledige-stillinger`, `/stillinger`) and by links from the homepage; extract postings from JSON-LD `JobPosting` first, then clearly structured lists (title, location, posted/closing date, URL). Merge with the NAV index by organisation number: de-duplicate by normalised title and location, keep both source URLs, mark which sources agree. A careers page that exists but lists no open roles is `available` with zero postings only if the page explicitly says so (for example "ingen ledige stillinger"); otherwise `ambiguous`. Never count an aggregator page (finn.no, jobbnorge, indeed) as company-owned.

## Task 3: what the company does

Claim field `company_brief`: one factual sentence-level summary assembled deterministically from the registry industry label plus the site's meta description / JSON-LD `description` / first heading text when the site is published, each with its own evidence span. No model call in this task. If the site text and the registry label disagree materially, keep both and mark `ambiguous` rather than choosing.

## Task 4: completeness audit of the register sections

Without changing behaviour yet, write `scripts/analysis/section_completeness.py` that, over a profile or cache directory, reports per envelope section the state counts and, for the claim-level 30%, the number of facts emitted per company: accounting years (full available history, not only the latest), every active and inactive role, every registered sub-unit, group links. Flag any section where the pipeline emits fewer facts than the registry API returns, and any financial field that is derived rather than read (derived or filled financial values are forbidden; a fabricated financial value blocks an official run).

## Task 5: cache and runtime

Store Tasks 1-3 results in the universe cache record (schema bump), read them in the runtime lookup, and run the cheap live re-verification only for the root page. Keep a per-company time budget; a slow site must not hold a company beyond it.

## Acceptance

- Fixture-based tests for each extractor (JSON-LD variants, RSS, sitemap lastmod, careers JSON-LD, aggregator rejection, undated items rejected, "no open roles" wording) and for the state rules. `uv run --with pytest pytest -q` passes (state the count).
- A section-completeness report on the planner's 2,000-company cache and the 100-company smoke output.
- Nothing is derived from retrieval time; nothing is invented; gates unchanged.

## Report back

Append `## Results` with counts per extractor on fixtures, the completeness report summary, the commit list and the branch name.
