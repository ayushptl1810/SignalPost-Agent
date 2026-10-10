# Starter-brief review: are we missing a directory or portal? (2026-10-08)

Read: builderr.ai/starter-briefs/signalpost.md, signalpost-learning-harness.md, signalpost-agent-playbook.md, signalpost-sources.md, and builderr.ai/docs/signalpost-evaluation-harness.md. Fetched pages were summarized by the fetch tool, so exact wording may differ from the source.

## Answer

No hidden directory or government portal is named anywhere. The sources page lists only:
- Brønnøysundregistrene: Enhetsregisteret (bulk + entity API: identity, legal form, address, industry, employee count), Regnskapsregisteret (annual accounts API + copies), official roles endpoints, official sub-unit records.
- Company-owned: verified site, sitemap, news/investor/careers/location/contact pages, embedded structured data, social/video profiles linked from the verified site.
- External: official or licensed platform APIs, search APIs (candidate discovery only), public pages whose terms/robots allow it, licensed feeds (news, reviews, jobs, traffic, company data).
- Restricted: unofficial LinkedIn/Meta/Glassdoor/Indeed/Google clients are not acceptable just because code exists; may be tested privately for candidates, but published claims need a durable permitted source.
- No prices, licence terms or quotas are given for any source.

"Official data is the identity anchor." It does not identify the brand or website. Search results are candidates, not evidence.

## Facts from the briefs that matter for strategy

- Seven envelope sections: (1) legal identity and public brand, (2) latest annual accounts and history, (3) leadership and registered workplaces, (4) verified official website and company-owned profiles, (5) hiring and dated public activity, (6) claim-level evidence and availability state, (7) refresh metadata and material changes.
- Sections 1-3 and 7 are mostly official-register or internal data and need no search. Search is only needed for the website (section 4) and what hangs off it. Hiring can come from the NAV feed (exact by org number).
- Scoring: recall/coverage 50 (per information type, 70% company recall + 30% individual-fact recall, measured against a cumulative versioned pool of independently verified findings from all submitted crawlers and Builderr's own); correctness 30; synthesis 12; UX 8. Qualify at 65/100 on an official run.
- Blockers for official status: material wrong-company publication OR a fabricated financial value.
- If the verified pool has under 15 positive company-field opportunities across at least 3 field families, recall is "not measured", not 0%.
- Official batch: 1,000 companies, may grow to 1,100; same batch, cutoff, network policy and resource budget for all entrants. Public universe 411,160 organisation numbers (our filter gives 420,476; reconcile).
- Ranking = mean over all scheduled daily batches while a version is active. Entrant-caused failures score zero after reproduction. Ties: fewer wrong-company publications, then higher weighted company recall, then lower declared third-party cost.
- Versions: v1 plus up to four revisions, closing 18 Oct 2026; a revision takes effect only after being frozen for the next daily run. Challenge closes 21 Oct.
- Official-run checks: 100-company smoke result in the artifact; exactly one envelope per company; material claims have source, retrieval time and reporting period; missing never becomes zero; re-running the same snapshot is idempotent; refresh preserves prior evidence and exposes changes; pinned deps and one evaluator command; source rights, secrets and outbound URL policy documented.
- External caches must be declared; cached public-universe material is allowed; source timestamps, refresh behaviour and evidence cutoff are enforced for everyone.
- Secrets only via documented environment variables. LLM use allowed within a small API budget; Builderr can supply model keys for scoring; personal credentials for other services cannot be used.
- Local checks named: `npm run check:signalpost`, `check:signalpost-showcase`, `check:signalpost-challenge`.
- Learning-harness brief: strategy registry with stable names and versions; log every attempt; scoring order is wrong-company publications, claim precision, evidence validity, coverage/recall, refresh correctness, runtime/requests/cost; a challenger is promoted only with zero added wrong-company publications, no meaningful precision drop, 100% evidence completeness, a declared minimum coverage gain and within budget; freeze code, lockfiles, strategy routing, prompts, model versions, thresholds, allowlists and budgets before each daily batch; hidden scores must not tune the running system; a failed refresh must never erase the last supported value.
- Strategies listed in the learning harness: registry-provided websites, sitemap and robots.txt discovery, static homepage crawl, targeted paths (about, contact, leadership, locations, careers, news), JSON-LD/OpenGraph extraction, search-provider candidate discovery, a leader-to-company bridge, browser rendering only for confirmed JS-only pages, annual-account PDF fallback.
- Playbook source ladder (most to least trusted): official registers and annual accounts; verified company sites and feeds; official or licensed platform APIs; permitted public pages with recorded terms; search providers for candidates only. Priority paths: /about, /om-oss, /contact, /kontakt, /leadership, /ledelse, /locations, /careers, /jobs, /news, /investor. Extract in layers: structured data, DOM attributes, clean text, deterministic rules, then the model. Keep conflicting candidates. Immutable snapshots with URL, redirects, timestamp, hash, parser version, access policy; every claim needs a snapshot plus selector/span/cell/PDF page.
- Playbook trap list: read the official batch at run time and process as a batch; one paste-able run command; clean-machine verification (fresh clone at pinned commit, empty venv, declared install, run); every company gets one row with one of the six states.
- Suggested tooling: Scrapy, scrapy-playwright, extruct, Trafilatura, Pydantic, RapidFuzz, tldextract, phonenumbers, pypdf, Docling, Tesseract, pytest, DuckDB, OpenTelemetry.
- Prize context: main pool $2,000 ($1,200/$500/$300), JBOX bonus $500, four $100 community awards (6 Sep, 20 Sep, 4 Oct, 18 Oct); winner may partner with Håvard Liltved Dalen to launch Signalpost in Norway.

## What this means for the search-cost problem

- Search is a candidate generator for one section of seven, so its cost should be capped, not scaled to the universe.
- Coverage is mostly won by filling register sections (identity, accounts, roles, sub-units) for every company plus explicit states, not by website recall.
- A company with no real website should return an honest `not_available`; it is not a recall loss against a pool of verified findings.
- Cost is a tiebreak, so a search-free default run is an advantage.

## Candidate search-free discovery routes to evaluate (only the first group comes from the briefs)

From the briefs: sitemap/robots discovery, leader-to-company bridge (use roles data), annual-account PDF fallback (may contain a website/email; unverified), JSON-LD/OpenGraph.

From the council (all UNVERIFIED for Norway): Norid org-number lookup (public lookup / registrar WHOIS reportedly returns all .no domains held by an org number; check rate limits, terms, RDAP fields, bulk agreement; private-person holders masked; agency domains give partial coverage); Wikidata P2333 -> P856 (notable companies only); Common Crawl scan for org numbers near "Org.nr"/"Organisasjonsnummer" as an offline recall check.

## Other gaps worth checking

1. Confirm the envelope emits accounts, roles and sub-units for every company with explicit states (a grep showed `registry/official.py` references roles, sub-units and the website field; emission per company was not verified).
2. Confirm accounts values are never inferred or filled; a fabricated financial value blocks an official run.
3. The public 100-company product sample at builderr.ai/signalpost (100 profiles) was not read; it may show the expected profile shape and what counts as a good profile. Not checked.
4. The starter kit (`signalpost-starter-kit.tar.gz`) and the `npm run check:signalpost*` checks were not examined; run the checks if the starter kit provides them.
5. Per-company wall time and request budget under a 1,000-1,100 company daily batch with unknown time and resource limits. Entrant-caused failures score zero, so failure containment (timeouts, resume) matters more than peak accuracy.
6. Revisions take effect only after freezing for the next daily run, so the last safe revision should land before 18 Oct with slack.
7. Declare caches, sources, models and licences in the README; document secrets and outbound URL policy.
8. The Codex external council opinion was not obtained earlier (invocation error); Gemini is not installed.
