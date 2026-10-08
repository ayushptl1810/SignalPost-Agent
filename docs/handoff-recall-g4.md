# Handoff S13: raise website recall (gate G4), fix throughput and states, finish NAV

**For:** Codex session S13 · **Branch:** `feat/recall-g4` · **Date:** 2026-10-08 · **Depends on:** S12 (merged to main).
**Read first:** `BRAIN.md` sections 0, 8, 10; `docs/handoff-recall-engine.md` (S12 results); `scripts/run/build_universe_cache.py`; `src/norway_company_agent/web/first_party.py`; `src/norway_company_agent/external/nav_jobs.py`.
**Network note:** your sandbox has no outbound network, so every live measurement below was run by the planner on the owner's machine. Build and unit-test offline; do not try to run live crawls. The planner will run the live passes. Commits in logical chunks, no co-author trailers, do not push, do not touch `config/connector-policy.json`.

## What the planner measured (2026-10-08, real network)

1. **Random 2,000-company sample** (seed 20261009, universe file `out/random2000.jsonl.gz`, cache `cache/random2000/`): 1,080 s wall, 6,546 requests, 394 MB, 16 workers. **173 of 2,000 (8.7%) got a published official website.** Candidate-source yields: registry website 100, registry email domain 28, name-derived .no 44, .com 1. By size: 100+ employees 4/14, 20-99 27/67, 5-19 62/212, no employee count 80/1,707 (4.7%). Projection for 420,474 companies at the same rate: about 63 hours; this must come down (see Task 3).
2. **Search calibration (400 of those companies, 400 Serper credits):** the cache alone published 34; search plus a loose address/name check verified 22; both 11; search-only 11; cache-only 23. So the cache holds about 76% of the union and search would add about 2.75 points of absolute coverage (about +32% relative). Search-only finds are small firms whose own domain is name-derived (`tegnsatt.no`, `armbil.no`, `norveibil.no`, `jpbyggogbetong.no`, `originaltalks.no`, `ardalshjelpen.no`, `erik-hoel.no`, `pixlo-dvnor.no`, `hypro.no`).
3. **Unpublished candidates that already carry strong evidence.** Re-reading the 2,000 cache records, 67 more companies (3.4 points, +39% on the 173) have an available candidate page that matches the registered street address and postcode plus every legal-name token on an own-brand domain (51), or a registry-listed website/email domain with a matching address and phone (16), but the current gate (G3) leaves them unpublished. Examples that look right: `jakosushi.no`, `hellbilverksted.no`, `vamec.no`, `tredet.no`, `norwindoffshore.no`, `skaperverket.no`, `squareone.no`. Examples that need related-only handling: cooperative-housing sites (`bbl.no`, `ringbo.no`, `helgelandbbl.no`), franchise or brand sites (`privatmegleren.no`), sibling/group sites (`krausnaimer.no`, `trysilposten.no`, `ragde.no`, `rvsas.no`).

## Task 1: gate G4 (recall without weakening precision)

Add `--gate g4` (keep G0-G3 reproducible, make G4 the cache default only after the planner's audit; until then G4 results go to a separate field `official_website_g4`). G4 publishes a candidate when it passes everything G3 requires **or**:

- (a) **name+address:** every legal-name token appears on the site root or its contact/about/privacy pages **and** the registered street address and postcode both appear on those pages, on an own-brand domain (not a directory, listing path, blocklist, shared domain, or social platform), with no contradicting valid organisation number on any inspected page; or
- (b) **registry tie:** the candidate is the registry-listed website or the registry email domain **and** the registered address or the registry phone matches on the pages, with no contradicting organisation number.

Demote to `related_only` (never official) when the domain serves several unrelated legal entities, is a franchise/brand/chain/cooperative/housing-association/group site, or the page's own legal name differs in a way that signals a sibling company. Add a deterministic detector with a test list built from the examples above. Record the rule used (`g3`, `g4_name_address`, `g4_registry_tie`) and the evidence span for every published claim. The different-organisation-number veto and SSRF protections are unchanged.

Add the new fields to the cache record, bump the schema version, and keep `official_website` semantics for G3 intact. Provide `scripts/analysis/export_g4_audit.py` that writes every G4-only publication from a cache directory as JSONL with the evidence snippet, so the planner can audit them by hand.

## Task 2: candidate generation gaps

Add the missing name-derived variants and test them against the search-only examples in the measurements above: hyphenated joined name (`erik-hoel.no`, `pixlo-dvnor.no`), the joined name with "og"/"and" kept and dropped (`jpbyggogbetong.no`), first distinctive token (`hypro.no`), first two tokens joined, stripped company-form suffixes and generic words, and transliteration of æ/ø/å to ae/o/a and a/o/aa. Keep DNS-first (fetch only resolvable names). Add a test per variant. Do not add `.com` variants for non-distinctive names.

## Task 3: throughput and states

- Make the builder fast enough for the whole universe: DNS pre-resolution in a large async pool (default 256 concurrent lookups) before any fetch, a per-host limiter that keeps 1 request/second per host but no global bottleneck, fetch concurrency default 64 with `--workers`, skip `.com` unless distinctive, and short timeouts (connect 5 s, total 12 s) so one slow host cannot hold a company for 30 s (218 of 2,000 companies took more than 20 s). Target: under 0.1 s per company on average so the full run is about 12 hours; report the achieved rate on a 2,000 sample **offline with a recorded fixture** (the planner will time it live).
- **State semantics:** when every candidate domain failed DNS resolution (NXDOMAIN) the website state is `not_available` (checked, no site found), not `failed`. Use `failed` only for timeouts, TLS errors and 5xx on a candidate that resolved. 1,576 of the 2,000 records were `failed`, almost all plain NXDOMAIN. Update the tests and the recall report accordingly.
- Keep resume and append-only shards; add `--shard-size` and `--shard-index` so the build can be split across processes or machines.

## Task 4: complete NAV job index, correctly

The planner measured the feed: a 150-day window yields **195,437 ACTIVE entries** because the feed is a change log and the same ad appears for every edit; the old builder fetched one detail per entry at about 1 detail/second and wrote the index only at the end (a killed run lost everything).

- De-duplicate by ad uuid and keep only the latest `sistEndret` per uuid before any detail call; report the unique-uuid count.
- Fetch details with 8-16 workers and a global limiter of about 8 requests/second (the feed is public but be polite), honour `Retry-After`.
- Flush the index and the resume file every 500 details so a crash loses almost nothing.
- Store per organisation number: ads (uuid, title, published, expires, location, application URL, source URL `https://arbeidsplassen.nav.no/stillinger/stilling/<uuid>`), homepage and contact email domains.
- Expiry: drop ads past their expiry date at read time; the runtime must treat a stale index (older than 7 days) as incomplete.
- Test with a recorded feed fixture that includes repeated uuids and INACTIVE entries.

## Task 5: search as an optional runtime and cache gap-filler

Search with a name-and-municipality query finds sites that free sources miss (about +32% relative). Serper costs about $1 per 1,000 queries and `SERPER_API_KEY` currently holds about 2,100 credits. Implement, behind flags and default off:
- `--search-fill` in the competition batch: for companies with no published website after the cache lookup, run one Serper query (cap by `--search-budget`, default 0), pass up to 3 non-directory results through G4, cache the raw result locally, and record the cost in the run report. If keys are missing or the budget is 0, the run must complete exactly as before.
- `scripts/run/build_search_cache.py`: the same step offline over a cache directory with resume, 5 requests/second, and a hard credit cap argument, so the owner can decide later whether to pay for universe-wide search (about $420 for 420k queries).

## Acceptance

- Tests for G4 (each rule, each related-only detector, the contradiction veto), the new name variants, NXDOMAIN-to-`not_available`, NAV dedupe/flush/resume, and the search-fill off-by-default path. `uv run --with pytest pytest -q` passes (state the count).
- Offline replay of the G4 decision over the planner's cache records (the planner will copy `cache/random2000` into your sandbox as a fixture): reproduce 173 G3 publications unchanged and report the number of added G4 publications by rule.
- No weakening of the identity gate, first-party gate, different-organisation-number veto or SSRF protection; no paid API calls without a flag; no push.

## Report back

Append `## Results` with the counts above, the commit list, the branch name, and anything you could not verify offline.

## Results

Implemented offline on branch `feat/recall-g4`. G3 remains the default cache
gate and `official_website` keeps its old meaning. `--gate g4` adds the
separate `official_website_g4` claim, records `g3`, `g4_name_address` or
`g4_registry_tie`, and exports G4-only rows with
`scripts/analysis/export_g4_audit.py`. Group, franchise, chain and
co-operative examples are deterministic `related_only` vetoes; the different
organisation-number veto, first-party gate and SSRF-safe opener remain in the
path.

Name discovery now includes joined and hyphenated names, connector-kept and
connector-dropped forms, first and first-two distinctive tokens, generic-tail
trimming, `æ/ø/å` spelling variants, and distinctive-only `.com` candidates.
The builder has DNS pre-resolution (default 256 workers), per-host one-second
pacing, 64 fetch workers by default, `--shard-size`, `--shard-index`, and
resolution failures become `not_available` when every candidate is unresolved.
The builder writes a v2 cache schema and reports rate/projection fields. No
live crawl was run in this sandbox, so the requested 2,000-company timing and
the under-12-hour target remain unverified; the planner's live baseline remains
about 63 hours, 1,376,211 requests and 82.8 GB of uncompressed JSON-equivalent
cache projection for 420,474 rows (1,080 seconds, 6,546 requests and 393.7 MB
over 2,000 rows).

Offline G4 replay: `out/g4-replay-report.json` over `cache/random2000`
reproduced **173 G3** publications and found **50 G4 additions**, all
`g4_name_address` (50; `g4_registry_tie` 0). The fixture is the S12 v1 cache,
so it contains recorded booleans but not raw page evidence spans; the 50 rows
are a conservative signal replay and are not a promotion decision. The
planner's earlier live re-read measured 67 strong unpublished candidates (51
name/address and 16 registry-tie), which cannot be reproduced exactly from the
older fixture without the missing page evidence.

NAV now deduplicates the feed change log by UUID/latest `sistEndret`, handles
latest inactive retractions, fetches with up to 16 workers and a 0.125-second
client interval, flushes the index and progress every 500 details, records
`source_url`, filters expired ads on read, and treats complete indexes older
than seven days as incomplete. The planner's measured 150-day feed contained
195,437 active entries; its unique-UUID count, detail run, employer count and
live flush timing were not available offline. The code now reports those
numbers as `unique_feed_uuids`, `details_processed`, `flushes` and related
metadata.

Search remains optional and off by default. `--search-fill` with an explicit
positive `--search-budget` uses at most one Serper query per cache miss, stores
raw results locally, tests up to three non-directory URLs through G4, and
reports cost. `scripts/run/build_search_cache.py` provides the capped 5 req/s
cache builder with resume. No search query was run here. The planner's prior
calibration found cache-only 23, search-only 11, overlap 11; search added
about 2.75 percentage points, mostly small firms on name-derived domains.

The existing local recall report is `out/recall-report.json`; the requested
published-claims audit sample path is `out/published-claims-audit-100.jsonl`
(84 available rows, not fabricated to 100). Full verification:
`uv run --with pytest pytest -q` → **266 passed, 11 warnings, 11 subtests**.

Commits on `feat/recall-g4`:

- `be10653` — `feat: add additive g4 website gate`
- `78fa0ae` — `perf: add dns first cache sharding`
- `94f2e8e` — `feat: add nav index rebuild and search fill`
- `30feade` — `test: cover offline g4 replay`
- `docs: report recall g4 results` — this final handoff/BRAIN documentation commit; the final hash is reported by the session.

No push was performed and `config/connector-policy.json` was not changed.
