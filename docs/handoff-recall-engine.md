# Handoff S12: recall engine (universe-scale cache, full NAV index, runtime lookup)

**For:** Codex session S12 · **Branch:** `feat/recall-engine` · **Date:** 2026-10-08 · **Depends on:** nothing open (S11 is done; do not continue extension labelling).
**Read first:** `BRAIN.md` sections 0 and 10, `docs/temp.md` (council review), `README.md`, `OUTPUT_CONTRACT.md`, `src/norway_company_agent/web/` (identity gate, first-party gate, SSRF opener), `src/norway_company_agent/external/nav_jobs.py`, `scripts/run/run_competition_batch.py`.
**You own:** new `scripts/run/build_universe_cache.py`, `src/norway_company_agent/cache/`, `scripts/connectors/run_nav_jobs_connector.py` (extend), the runtime lookup path in the competition batch, and `docs/cache-declaration.md`. Do not loosen the identity gate, the first-party gate, the different-organisation-number veto, or SSRF protection. Commits in logical chunks, conventional messages, **no co-author trailers**.

## Why (the real rubric)

The live rubric (fetched 2026-10-08, with the evaluation contract at `https://builderr.ai/docs/signalpost-evaluation-harness.md`): recall and coverage 50, precision and evidence 30, synthesis 12, UX 8; qualification at 65/100; a material wrong-company publication blocks qualification. Recall per external field family is **70% company recall plus 30% claim recall, measured against a cumulative verified pool of everything all submitted crawlers and Builderr's own crawlers found**. Abstention does not count as coverage. The official batch is about 1,000 random universe companies; every entrant gets the same fixed time and resource budget (values unpublished); timeouts are unscored; **cached public-universe material is allowed if declared**, with source timestamps and refresh behaviour.

So the lever is: find, for as many of the 411,160 universe companies as possible, every verified external fact that anyone might find, with zero wrong-company publications. Search providers are dead, so we win by precomputing offline across the whole universe with free registry-grade sources, then answering the official batch from the cache in seconds (and re-verifying live where cheap).

## Task 1: universe-scale cache builder (no search provider, no paid API)

`scripts/run/build_universe_cache.py` reads `data/signalpost-company-universe-2025.jsonl.gz`, processes it in shards ordered by employee count descending (so a partial run still captures the larger firms first), and writes a resumable, append-only cache `cache/universe/<shard>.jsonl.gz` (git-ignored; declared in the docs). Per organisation number it stores: candidate domains with their source, the identity-gate and first-party-gate result, the verified official website if any (final URL, `retrieved_at` as ISO-8601 UTC, content_sha256, evidence span), company-owned outbound social/profile links found on that site (with the handle-ownership guard), contact fields found on the site (phone, email, address) only when they match the registry, and an explicit state per field family (`available`, `not_available` when checked and absent, `ambiguous`, `blocked`, `failed`; never silently empty).

Candidate sources, in order (all free, none are search):
1. Registry `hjemmeside` from the bulk file. **Fetch and judge it** (the S8 pass wrongly reported "pass" without fetching).
2. Registry email domain when it is not a free-mail or accountant/host domain.
3. Sub-unit (underenheter) bulk file `hjemmeside` and email domains: download the public Brreg sub-unit CSV from `https://data.brreg.no/enhetsregisteret/api/underenheter/lastned/csv`, join by parent organisation number.
4. NAV employer homepage and contact email domains from the full NAV index (Task 2).
5. Parent/group domain from the group links already in the profile, used as **related only**, never as the entity's official site.
6. Name-derived `.no` (and `.com` only when the legal name is distinctive) domains: DNS-resolve first, fetch only if it resolves, then run the full gate. Keep the existing G3 thresholds. The medium-evidence path (email/phone/name without an organisation number) stays a labelled candidate with `availability: ambiguous`, not a published site, unless an organisation number or the registry itself ties the domain to the entity.

Rules: respect robots.txt and per-host rate limits (at most 1 request per second per host, global concurrency configurable, default 32), identify with a clear User-Agent, use the safe opener everywhere, and honour `Retry-After`. No login walls, no scraping of platforms whose terms forbid it (LinkedIn, Meta, Indeed): their URLs are recorded only when linked from the company's own site and the state is `blocked` for any content. Norid's holder lookup is out: port-43 whois returns no holder and the web lookup forbids commercial and bulk use.

**Pilot first:** run the builder on a stratified 5,000-company sample (use the existing universe strata) and report: wall time, requests, bytes, per-source yield (how many verified sites each candidate source found), failure/blocked rates, and a projection for the full 411k. Stop after the pilot report; do not start the full run. Include `--limit` and `--resume`.

## Task 2: full NAV job index

Extend `external/nav_jobs.py` and `run_nav_jobs_connector.py` to build the **complete** active-ad index, not a two-day slice: walk every feed page, fetch every ad detail (bounded concurrency, resume file, circuit breaker), and store employer organisation number to ads (title, published, expires, link, location, application URL) plus homepage and contact email domains. Support an incremental refresh using the feed `since` header. Report total ads, distinct employers, distinct organisation numbers that are in the universe, requests and wall time. A company with zero ads in a complete index is `not_available` with `checked: true`; a company not covered by a complete index run is unchecked/`failed`, never zero. Fix the earlier "0 matches in 300 companies" cause or explain it with numbers (the earlier index held only 89 employers).

## Task 3: runtime lookup

Make `run_competition_batch.py` read the cache first. For each input organisation number: a cache hit gives the claims instantly; then do a cheap live re-verification of the cached website (one GET through the safe opener, same gate; update `retrieved_at` and `content_sha256`, record a material change if the verified fact changed) within a configurable per-company budget. A cache miss falls back to the existing live path (registry + registry website + no-search G3). The terminal envelope contract, previous-snapshot input and material-change output must keep working, and re-running the same snapshot must be idempotent. Add the cache path, build date and per-source retrieval times to the run report.

## Task 4: local recall measurement (our own pool, since the real pool is hidden)

`scripts/analysis/recall_report.py`: on a random 1,000-company sample drawn with a fixed seed from the universe (not a certification set), report per field family the company coverage and the claim count, the per-state counts, and a "lost to abstention" count. Also output a 100-row random sample of **published** website claims with evidence snippets so the planner can audit for wrong-company errors by hand. Do not tune thresholds against this sample.

## Task 5: search calibration (small, capped)

Search works again (verified 2026-10-08): Serper `SERPER_API_KEY` has 2,499 credits and a 5 requests/second limit; SerpApi `SERPAPI_KEY` is on the free plan with 248 of 250 searches left this month (it can be slow, allow 60 s timeouts). Tavily and Linkup were removed from the owner's setup. Update `data/search-providers.json` (remove tavily/linkup, set serpapi allowance to 250 monthly) and the provider-pool tests accordingly, keeping the fail-fast on exhausted keys.

The credits are far too few to search the universe, and the official run may not have our keys, so **the cache is the product and search is only a measuring stick and a gap-filler**:
1. Draw a random 400-company sample from the universe (fixed seed, stratified like the pilot), run the cache builder on it, then run **one Serper query per company** (`"<legal name> <municipality>"`, Norwegian locale), pass every result through the same identity and first-party gates, and report: how many verified official sites search finds that the free sources missed (the recall gap), how many the free sources find that search missed, and what the missed ones have in common (domain pattern, company type, size, missing registry fields). Use the gap to propose new free candidate generators. Cap spend at 450 Serper credits; use SerpApi only for the highest-value 100 misses.
2. Cache every raw result locally (git-ignored, replay-only afterwards) so nothing is paid for twice.
3. Do not make runtime search a dependency. If search keys are present at runtime they may fill cache misses within a hard per-run cap, otherwise the run must complete without them and say so in the run report.

## Declaration

Write `docs/cache-declaration.md`: what the cache contains, how it was built, sources and their terms, refresh policy (rebuild or incremental), size, and the cutoff behaviour, so it can go into the submission email.

## Acceptance

- Pilot report on 5,000 companies with the projection for the full universe.
- Complete NAV index built and reported; the runtime path returns NAV-based job claims for companies in the index.
- Runtime lookup passes the existing terminal-contract check and the idempotence test; tests added for the cache builder (resume, ordering, gate pass/fail), the runtime lookup, and "checked-but-empty is not_available, unchecked is not zero".
- Search calibration report (gap, overlap, shared traits of misses) with spend within the cap.
- No weakening of any gate; no paid API; no new dependency without pinning; `uv run --with pytest pytest -q` passes (state the count); no push.

## Report back

Append `## Results` with pilot yields per candidate source, the full-run projection (hours, requests, disk), NAV numbers, the recall report output, the 100-row audit sample path, commit list and branch name.

## Results

Implemented on branch `feat/recall-engine`. The missing declared JSONL input was
materialised from the local compressed Brønnøysund snapshot. It contains 420,474
eligible rows; `data/universe-metadata.json` still declares the older 411,160-row
population, so the discrepancy is recorded rather than hidden. No full-universe
cache run was started.

Pilot: 5,000 selected companies, resumable append-only shards under
`cache/universe/`. The completed mixed-access run measured 1,902 requests and
147,397,875 bytes. Per-source verified-site yield was registry website 46,
registry email domain 18, name-derived `.no` 17, and distinctive `.com` 3.
The cache contained 84 published official websites, 34 ambiguous, 222 checked
not-available, 59 blocked and 4,687 failed website states; unchecked families
remain `not_checked`. The real-network first 500-row checkpoint took about one
minute; the linear full-run projection is approximately 14 hours, 159,948
requests and 80,000,000 compressed-cache bytes. This projection is explicitly
low-confidence because the remaining pilot rows resumed under network-denied
conditions.

NAV: the implementation now walks uncapped feed pages/details when the public
feed is reachable, uses safe-opener requests, one-second host pacing,
Retry-After, resume progress and a completeness sidecar. The complete attempt
here stalled during the public TLS/feed response before producing a page. A
bounded no-op report is therefore explicitly `complete_index: false`, 0 ads,
0 employers, 0 in-universe organisations and 0 requests; it is not a zero-job
claim. The older bounded local index had 89 employers from 152 requests, which
explains the earlier 0-match result and is not used as a complete cache source.
Report: `out/nav-full-report.json`.

Local recall measurement: `out/recall-report.json`, fixed-seed sample of 1,000
companies. Because the pilot is employee-ordered rather than a random full
cache, the report correctly shows `not_checked` for most sampled families;
official-website coverage is 0/1,000 in this local measurement. The published
website evidence sample is
`out/published-claims-audit-100.jsonl`, with 84 rows available (target 100 was
not met; no rows were fabricated).

Search calibration: `out/search-calibration/report.json` and local raw replay
path `out/search-calibration/raw-results.jsonl`. Both configured keys were
present, but the first Serper request failed with `ProviderTransientError` in
this environment; 0 Serper and 0 SerpApi queries were charged, so no
search-vs-free-source gap or shared miss traits can be claimed. Search remains
non-runtime and capped at 400/100 in the command.

Tests and commits are recorded below after the final full-suite run. The branch
is `feat/recall-engine`; do not push.
