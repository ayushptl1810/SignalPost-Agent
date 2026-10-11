# S29 — Competitor harvest: every usable idea from the public Signalpost repos

Written 2026-10-10 by the planner for Codex (implementer) and the planner/owner (reviewer).
Base: `main` at `4663dac` (S26 hardening, S27 recall families, S28 reference gap, v2 ready at `501df3c`).
All paths are relative to `backend/`. Run everything from `backend/`.

## 0. Why this spec exists

Recall (50 points) decides the competition. On the 5 Oct board, every entrant scored 5–17.5 of 50 on recall. The unmodified starter kit gets full synthesis (12) and UX (8).
Builderr emails per-entrant feedback. Two competitors published theirs:
- livingmangal `PROJECT_RECORD.md` §9;
- Jaswanth `docs/SCORER_NOTES.md`.

The feedback names four scored external families and shows how values are matched:

| Family | Builderr example value | What it implies |
|---|---|---|
| Company website | `elopak.com` (ELOPAK ASA) | registered domain |
| Social profile | `linkedin.com/company/g3-gausdal-treindustrier-sa`, linked from `g3i.no` | lower-cased URL with no scheme or `www`, linked from the verified site |
| Hiring signal | `https://www.equinor.com/careers` | **a careers page URL counts on its own** |
| Dated news | `bedre sosial funksjon etter hjerneskade (2025-09-22t20:00:00+02:00)` | `title (published datetime)`, the whole string lower-cased, the source's own timestamp with offset, **and about one year old when quoted** |

I cloned and read about 20 public competitor repos with `--depth 1` into the planner scratchpad. I executed nothing. This spec turns every idea that is still useful into tasks, and records every idea we reject, with the reason.

**Licence rule:** most of those repos have no licence. **Reimplement ideas; never paste their code.**

## 1. Where we stand (live 999, `out/s28b-live1000/`, before `0ecc67e`)

| Family | Companies / 999 | Notes |
|---|---|---|
| Website | 114 | S27 106, S26 99; about 11 lost per run to load timeouts |
| Social profile | `social_profile` 92 claims (companies about 50) | two-link-per-platform cap |
| Hiring signal | 35 claims (27 companies on legacy `hiring`) | 17 verified careers pages |
| Dated news | **10** companies (`dated_news`), 15 legacy `public_activity` | **the weakest family by far** |

Reference-100 (Builderr's own sample, `out/ref100/`): websites match 75 of 97 after S28. Social matched 35 of 103 in the S28 Task 0 offline run (before the S27/S28 live fixes). No reference number exists yet for news or hiring.

**The news gap, with the code that causes it:** Jaswanth reports dated news for about half of sites with a verified website. Their proxy-200 run had website 26%, news 14%; sample-100 had website 71, news 34. Our current numbers on 999 are website 114, news 10.
- **3-item cap.** `src/norway_company_agent/site_facts.py:1331-1335` promotes at most **3** activity items to `dated_news`.
- **24-month age cut.** It promotes only items **not older than 24 months** (`_mark_old`, line 642, 730 days).
- **Article fetch required.** Each promoted item must also succeed a separate article fetch inside the shared extra-request budget.
- **Recent-only availability.** `public_activity` is `available` only with a recent item (line 1108).

Builderr's own example item was about a year old when quoted, and 30% of each family's score is per-fact recall. We are throwing away facts the pool contains.

## 2. Ground rules (unchanged, non-negotiable)

- **Identity gate stays absolute.** Keep the identity gate, the different-org-number veto, the directory/blocklist, brand/chain and manager-site rules, domain uniqueness, SSRF protection, robots, and the 48-fetch/DNS caps. Zero wrong-company publications.
- **Search is never claim evidence.** Every published fact is re-fetched from the company's own site or an official source.
- **No own-account credentials at runtime.** No LinkedIn, Meta, Google News, DuckDuckGo or other platform scraping.
- **No change of behaviour when an optional key or source is absent.** The default command stays $0 with no keys.
- **Every new fetch is inside the per-company deadline and the run budget.** S26: default 1,500 s, soft phase at 80% skips site facts.
- **One task, one logical commit.** No co-author lines. No push, tag or submission.
- **Fixtures for every rule.** Each fixture names the competitor repo or real page it came from; real pages go into `tests/fixtures/` (as S25 did for NAV).

## 3. Idea ledger (every repo, every idea, verdict)

**Verdicts:**
- **ADOPT**: implement as described.
- **ADAPT**: implement with the change noted.
- **HAVE**: we already do it; verify only.
- **REJECT**: do not implement, for the reason given.

### Website discovery and identity

| # | Idea | Source | Verdict | Task |
|---|---|---|---|---|
| W1 | Registry email-domain candidate | Nikita `email_domain_discovery.py`, Hardik, Abhinav, anur4ag | HAVE | — |
| W2 | Sub-unit `hjemmeside` and sub-unit email domains as candidates | Sahvendra `site_research.py`, Hardik | HAVE (sub-unit websites); verify that sub-unit **email** domains are candidates | T7 |
| W3 | Homepage declared in an exact-org-number NAV ad (`employer.homepage`) | Abhinav `CRAWLERS.md:32`; Jaswanth REV4 E3 (81.8% agreement with registry; 15 of 37 beyond-registry candidates passed strict proof, 0 wrong) | HAVE (`discovery/engine.py:56`); measure its yield | T0 |
| W4 | Wikidata P2333 (org number) → P856 (official website), batch SPARQL POST in chunks of 200, CC0 | Samkit `wikidata.py`; Jaswanth REV4 E2 (88.8% agreement; 14 of 58 passed strict proof, 0 wrong) | ADOPT, candidate only, fail-open | T7 |
| W5 | Fagfolkguiden `/bedrift/{slug}-{orgnr}` JSON-LD `sameAs` as a website candidate (robots allows `/bedrift/`) | Prikshit `external_connectors.py:329`; Sankeerth `harvest_fagfolkguiden.py` | ADAPT: candidate source only, off by default, measure first; requires a slug | T7 |
| W6 | OpenStreetMap `ref:NO:orgnr` + `website` | Jaswanth REV4 E1 | REJECT: 60.7% agreement, mostly sub-unit outlets, ODbL share-alike unclear, +2 per 360 | — |
| W7 | Naive-Bayes ranker over name-derived domains | Sankeerth `train_ranker.py` | REJECT: it only orders DNS guesses we already probe; no recall gain | — |
| W8 | 59k "LinkedIn Norway" CSV as candidates | Sankeerth `data/linkedin_norway_59k.csv` | REJECT: unknown licence, LinkedIn-derived | — |
| W9 | Exa neural search with a hardcoded key | Kartik `discovery.py` | REJECT: own-account key, leaked secret; the loosened name-token rule is unsafe | — |
| W10 | Search provider cascade (Brave → Tavily → SerpAPI) with comma key pools, 429/403 failover and a query budget | livingmangal `search_api.py` | ADAPT: only providers whose key **Builderr** supplies via env; no DuckDuckGo/Wikipedia scraping | T11 |
| W11 | Officially-linked control rules plus disqualifiers. D1: fansite, wiki or katalog markers in title/h1/meta. D2: contact delegated to another organisation's domain. D3: three or more distinct org numbers on the site, which means a directory | Sahvendra `docs/website-identity-analysis.md` (arasenstadion.no fan-site false match) | ADOPT as vetoes on every non-org-number rule | T8 |
| W12 | A phone shared by 5 or more registry entities is an accountant/manager phone and never proof | Biswajeet `proof.py` (`SHARED_PHONE_THRESHOLD = 5`) | ADOPT as a corroboration veto | T8 |
| W13 | Parked/for-sale domain detection | Hardik README, Sankeerth README | ADOPT as a veto | T8 |
| W14 | Workplace sports clubs (`BIL`, "bedriftsidrettslag") quarantined | Sankeerth README | ADOPT as a brand/chain-style veto | T8 |
| W15 | Registered CEO or board-member name on the site as a corroborator for name rules | Hardik README ("name + address/postcode/city/phone/person"); the playbook's leader bridge | ADAPT: corroborator only, never sole proof | T8 |
| W16 | 406 → retry with `Accept: */*`; one homepage retry on timeout/429/5xx; 401/403 → `blocked` | Jaswanth V4 item 5 / REV4 E7 | ADOPT (verify which parts we have) | T6 |
| W17 | Run-wide per-domain page and robots cache: a domain is fetched once per run however many companies point at it | Sahvendra `SiteSession`, Nikita domain cache (in-run part only) | ADOPT (in-memory, per run) | T6 |
| W18 | Per-host circuit breaker after repeated failures | rasaldushyant `budget.py` | ADOPT | T6 |
| W19 | JS-shell homepage accepted via sitemap liveness | Jaswanth REV4 E7 | REJECT: 0 qualifying sites measured | — |

### Social profile

| # | Idea | Source | Verdict | Task |
|---|---|---|---|---|
| S1 | A non-X social URL on a verified site whose text contains the site's first domain label (3+ chars) is publishable | Nikita `identity.py` `_verified_website_social_identity` (#1 on the board) | HAVE (S27 `758f34c`, two-link cap); verify against reference-100 | T0/T5 |
| S2 | The handle equals the domain label with a TLD suffix (`x.com/g3i_no` on `g3i.no`), X allowed in that exact form | Hardik README | ADOPT | T5 |
| S3 | Organisation JSON-LD `sameAs` links on the verified site count as officially linked | Sahvendra `docs/public-activity-experiment.md` method step 2 | ADOPT | T5 |
| S4 | Collect profiles from header, nav, footer and social-bar links, plus links on the contact/about pages (not only the homepage) | anur4ag `web.py` `social_links`; Sahvendra (homepage + up to 3 same-site pages) | ADOPT | T5 |
| S5 | Exclude share/intent/plugin URLs; canonical `platform.com/handle` | starter kit `normalize_social_links.py` | HAVE | — |
| S6 | Wikidata P2013/P2003/P4264/P2397/P7085 social IDs | Samkit `wikidata.py` | REJECT as evidence (third-party claim). Use only to **prioritise** a link already present on the verified site; no task | — |
| S7 | LinkedIn guest typeahead `jobs-guest/api/typeaheadHits` with a name match | Prikshit `external_connectors.py:98` | REJECT: LinkedIn terms (source policy names it); name-only identity | — |

### Hiring signal

| # | Idea | Source | Verdict | Task |
|---|---|---|---|---|
| H1 | The careers page URL itself is the hiring fact, even with zero postings | Builderr feedback (Equinor) | HAVE (S27 `8d2202c`) | — |
| H2 | A careers page counts only when its h1/title/h2 names careers/jobs, and a blind probe must not redirect home | Jaswanth `webclaims.py` `careers_heading`, `try_candidates(probe=True)` | ADOPT (verify against our S27 rule) | T3 |
| H3 | Depth 2: careers links found on about/company pages reached from the menu | Jaswanth REV4 E5 (+2 on sample-100) | ADOPT | T3 |
| H4 | Careers pages taken from the site's own sitemap (**URL discovery only**, the page is fetched and its heading checked) | Jaswanth `_sitemap_page_urls` | ADAPT: path-segment match on the first two segments; never dates | T3 |
| H5 | ATS/job-board links on the careers page: finn.no `/job/`, webcruiter, teamtailor `/jobs/\d+`, recman, jobylon, easycruit, reachmee | Jaswanth `JOB_PLATFORMS`; Hardik ("careers page checked for a link to a job board") | ADOPT (posting facts; the careers page stays the signal) | T3 |
| H6 | JSON-LD `JobPosting` counts only when `hiringOrganization` is this company, not a recruiter's client | anur4ag `own_posting` | ADOPT | T3 |
| H7 | Careers sub-pages with apply markers (`søknadsfrist`, `tiltredelse`, `stillingsprosent`, `apply now`) are postings | Jaswanth `APPLY_MARKERS` | ADOPT | T3 |
| H8 | Explicit "no openings" text recorded, but the careers signal is still published | Jaswanth `NO_OPENINGS` | ADOPT (store as `explicit_no_openings`) | T3 |
| H9 | Careers crawled before news so a budget cut does not lose the hiring signal | Jaswanth REV4 E5b | ADAPT: order hiring and news probes so neither starves; see T2/T3 budget | T2/T3 |
| H10 | NAV ads verified per ad on the public page | ours (S25) | HAVE | — |
| H11 | NAV site search by company name | Prikshit `discover_nav_jobs`, livingmangal `fetch_nav_arbeidsplassen_jobs` | REJECT: name search; our org-number index plus live verification is stricter and complete | — |
| H12 | LinkedIn guest job search | Prikshit, starter kit | REJECT: platform terms | — |

### Dated news

| # | Idea | Source | Verdict | Task |
|---|---|---|---|---|
| N1 | Publish up to 20 dated items per company, newest first, **no age cut** | Jaswanth REV4 E6 (news facts +20–24%, cost +1.5%) | ADOPT | T1 |
| N2 | `published_at` = the source's own timestamp string, kept verbatim when it is ISO; RFC-822 feed dates converted to ISO with offset; date-only when the source states only a date | Jaswanth `published_at()` | HAVE in part (S27 `3fe0aea`); verify verbatim behaviour | T1 |
| N3 | Date ladder on an article page: `meta[property=article:published_time]` → JSON-LD `datePublished` (Article/NewsArticle/BlogPosting first) → `time[datetime]` inside `article`/`main` → Norwegian text date in the first 1,500 chars (`22. september 2025`, `22.09.2025`) → URL date `/2025/09/22/` | Jaswanth `article_date()` | ADOPT (check against ours) | T2 |
| N4 | Inline dated teasers on the homepage and listing cost no fetch | Jaswanth `_listing_inline_items`, `_jsonld_article_items` | ADOPT | T2 |
| N5 | Listing page that only links to an archive: follow one hop | Jaswanth `discover_news` | ADOPT | T2 |
| N6 | Listing pagination: page 2 when fewer than 20 items (`/page/2/`, `?page=2`, `?side=2`, `?paged=2`) | Jaswanth V4 item 3 (+80% news facts on proxy-200, +63% on sample-100, coverage unchanged) | ADOPT | T2 |
| N7 | Feed discovery by platform when no `<link rel=alternate>`: WordPress `/feed/` (+ `?paged=2`) and `wp-json/wp/v2/posts`; Wix `/blog-feed.xml`; Squarespace `<listing>?format=rss`; else `/rss.xml`, `/feed` | Jaswanth `discover_news` | ADOPT | T2 |
| N8 | News probe paths when nothing is linked: `/aktuelt`, `/nyheter`, `/news`, `/blogg`, `/presse`, `/en/news`; landing URL must still be a news path | Jaswanth `NEWS_PROBE_PATHS` | ADOPT | T2 |
| N9 | News vocabulary: `aktuelt, nyheter, nyhet, nyheiter, news, blogg, blog, presse, pressemeldinger, siste-nytt, artikler, artikkel, nyhetsarkiv, innsikt`; skip segments `side, page, kategori, category, tag, author, feed, arkiv` | Jaswanth `NEWS_SEGMENTS`/`NEWS_SKIP_SEGMENTS` | ADOPT | T2 |
| N10 | Sitemap used **only to discover article URLs** when a JS listing links nothing statically; each article is fetched and dated from its own page | Jaswanth `_sitemap_article_urls`; Abhinav `CRAWLERS.md:22` | ADAPT: S27 rightly dropped sitemap *dates/slugs*. Re-enable sitemap as URL discovery only, last resort, at most 4 article fetches | T2 |
| N11 | Each item cites its own article page when it can be fetched; feed items with their own `pubDate`/`published` are publishable without a fetch | Jaswanth "upgrade" step; Builderr v2 feedback ("retain the exact public page") | ADOPT | T1 |
| N12 | Google News RSS | starter, livingmangal, Prikshit | REJECT: news.google.com robots `Disallow: /`; the kit marks it `review_required`; the identity of micro-companies is weak | — |
| N13 | Brreg update feed (`/oppdateringer/enheter`) as "activity" | Sahvendra `source-expansion-results.md` | REJECT: not news (no title or article); mapping unconfirmed | — |
| N14 | LLM classification of announcements | Dev-angPatil `announcements.py`, sruthi `llm_service.py` | REJECT for now: no model key; deterministic extraction is enough | — |

### Envelope shape, evidence, synthesis, operations

| # | Idea | Source | Verdict | Task |
|---|---|---|---|---|
| E1 | Emit the same facts also in Builderr's sample layout: `external.handles [{platform,url,rightsStatus}]` and `external.company_site.{posts,jobs}`, plus `external.observations` in the starter's `external_footprint` schema | Jaswanth V4 item 4 (their v2 got **0.0% social** despite finding links, suspected unread shape); `out/ref100/builderr-sample100.json` | ADOPT (no new facts, same evidence ids) | T9 |
| E2 | Evidence spans verbatim from one text node or attribute of the cited page; accounts spans as printed (`288000.00`); registry year cites the live record, not the 150 MB bulk file | Jaswanth V4 item 1 (re-found verbatim 61% → 100%) | ADOPT as an audit plus fixes | T10 |
| E3 | Evidence scores move in near-integer steps, which suggests about one point per failed check | Jaswanth SCORER_NOTES §5 | Context for T10 | — |
| E4 | Mark a discovery stopped by a transient error as incomplete, so refresh never reports removals | Jaswanth V4 item 5 | HAVE (`d27eaa5`) | — |
| E5 | Chunked runner: checkpoint envelopes per chunk, validate the chunk, fall back on failure | Biswajeet `chunked.py` | HAVE in spirit (S26 signal/exit finalisation); no task | — |
| E6 | Real-attempt counting (retries and redirects), not one per module | rasaldushyant `budget.py` | ADOPT as report accuracy | T6 |
| E7 | Synthesis that separates sourced growth signals from inference | Builderr v2 feedback quoted by livingmangal §9.1; Hardik README item 7 | ADOPT (small) | T12 |
| E8 | Use Builderr's sample-100 as a fixed benchmark and measure a noise floor by running twice | Jaswanth REV4 ("Noise floor", `eval/compare_runs.py`) | ADOPT | T0 |
| E9 | Fagfolkguiden aggregate ratings (reviews family) | Prikshit, Sankeerth | REJECT: no evidence that reviews is a scored family; Google-sourced aggregate | — |
| E10 | YouTube channel via yt-dlp search | Prikshit `discover_youtube_channel` | REJECT: platform search; site-linked YouTube is already covered by S4 | — |
| E11 | Annual-report PDF OCR for workforce | starter, Nikita | REJECT: Brreg copies are image-only (Sahvendra: 100 of 100), 8 s per company | — |

## 4. Tasks

Order matters. T0 comes first, because every later task reports its effect with T0's tool.

### T0 — Four-family reference benchmark and noise floor

**Goal:** one command that gives per-family company recall and fact recall against Builderr's sample-100, using Builderr's matching keys. Also report the noise floor.

- Extend `scripts/analysis/reference_compare.py` (S28 `a748231`) to all four families.
- **Normalisation:**

  | Family | Reference side | Ours |
  |---|---|---|
  | Website | registered domain from `web.value.final_url` | `official_website` |
  | Social | `external.handles[].url` and `web.value.social_links[].url` → lower-case, strip scheme and `www.`, strip the trailing `/` | `social_profile` |
  | Hiring | `external.linkedin.jobs` (sparse) + careers URLs if present | `hiring_signal` |
  | News | `external.linkedin.posts[].date_published` + title; report ours (`dated_news` count, companies) even when the reference has no site news | `dated_news` |

  The sample's news reference is LinkedIn posts, which we will not match. Report "ours" absolutely for news and hiring, and "matched" only where the reference has comparable site-derived values.
- **Output** for each family:
  - reference companies;
  - ours;
  - matched companies;
  - reference-only (listed);
  - ours-only (listed);
  - facts matched;
  - the `0.7·company + 0.3·fact` proxy.
- **`--compare A B` mode:** per-family gained and lost companies between two runs, like Jaswanth's `compare_runs.py`.
- **Noise floor:** document a procedure that runs the reference-100 twice on unchanged code and records the spread per family. Any later task claims a gain only above that spread.
- Also report: website candidate source counts for published sites (registry, email, sub-unit, NAV homepage, name-derived, Wikidata, ...), so W3/W4 yields are visible.
- **Fixture:** a three-company miniature reference and our envelopes, with the expected per-family table.

**Acceptance:** the command runs offline on `out/ref100/` and prints the table; the tests pass.

### T1 — Publish all dated news (largest expected gain)

**Goal:** stop discarding dated facts.

- **No age cut.**
  - `public_activity` is `available` when **any** correctly dated item exists.
  - The `old` flag stays on items and the note still says "no activity in the last 24 months" when that is true. Synthesis may use recency; recall must not.
  - Raise the item cap from 10 to **20** (`site_facts.py:1102`, `max_items`).
- **`dated_news` claims for every publishable item, up to 20, newest first.** An item is publishable when one of these holds:
  - (a) its date and title come from its own article page (N3 ladder);
  - (b) it comes from a feed entry with its own `pubDate`/`published`/`updated` and an item link on the company's registered domain;
  - (c) it is JSON-LD `datePublished` on a page of the site with its own `headline`/`name` and `url`.

  Items seen only as HTML listing teasers need (a) **or** a `time[datetime]` inside the same card element as the link and title. Sitemap `lastmod` never dates anything.
- **Article upgrade.**
  - Item to upgrade: any publishable item whose cited page is a listing or feed.
  - Upgrade: fetch its article page when budget remains, newest first, and cite the article as the source.
  - If the upgrade fetch fails, keep the item with the feed/listing as source (still valid under (b)/(c)).
  - Remove the "at most three recent items" rule at lines 1325–1335.
- **Value format.** `published_at` is the verbatim ISO string from the source when the source gives one (N2). The claim value stays `"{title} ({published_at})"` with `value_details {title, published_at, url}` (contract.py:399).
- **Evidence.** Each claim has its own evidence id: source URL (the article if fetched, else feed/page), `retrieved_at`, `content_sha256`, `claim_span` = verbatim title, `date_span` = verbatim date string, `effective_at` = `published_at`, `extraction_method` (e.g. `article_page:meta_published_time`, `feed_rss_pubdate`, `jsonld_datePublished`).
- **Validation stays.** Keep these checks: no item dated after retrieval; no crawl-date items; placeholder/listing-slug titles rejected (existing `ACTIVITY_PLACEHOLDER_TITLES`, `ACTIVITY_LISTING_SLUGS`); duplicate titles/URLs collapsed.
- **Fixtures:**
  - a 2023 feed item published with `old: true`;
  - a site with 14 feed items producing 14 claims;
  - a teaser without a card-level date not published unless upgraded;
  - a failed upgrade keeping the feed-sourced claim;
  - `published_at` verbatim `2025-09-22T20:00:00+02:00`.

**Acceptance:**
- Reference-100 and live 999 `dated_news` companies and facts rise by more than the noise floor.
- Junk detector `activity_*` counts stay 0.
- Hand audit of 30 random new news claims: correct title, date and company.

### T2 — News discovery breadth

Implement N3–N10 inside the existing site-facts fetch path, using the same safe opener, robots handling and deadline.

**Order of work per verified site.** Stop when 20 publishable items are collected or the budget runs out.
1. Homepage inline teasers and JSON-LD articles (no fetch).
2. Linked news listing (first of up to 2 candidates). If it links no articles, follow one hop to a linked archive (N5).
3. Listing pagination, page 2 only (N6).
4. Feeds:
   - declared `<link rel=alternate>` first;
   - otherwise the platform guess (N7) — WordPress `/feed/` then `?paged=2`, then `wp-json/wp/v2/posts?per_page=20&_fields=link,title,date,date_gmt` when there is no feed (title from `title.rendered`; `date` is site-local without an offset — use `date_gmt` + `Z` only when `date` lacks an offset);
   - otherwise `/rss.xml`, `/feed`.
5. Probe paths (N8), only when nothing is linked. The landing URL must still contain a news segment.
6. Sitemap article URLs, last resort (N10): only same-domain URLs whose path has a news segment (N9). At most 4 article fetches, each dated from its own page.
7. Article upgrades (T1).

**Budget:**
- A separate news fetch counter (default 6: listing, archive or page-2, two feeds, two probes) plus the article cap (default 6). Both are configurable.
- The total per-company extra-request cap rises from 6 to **12** only if T6's per-domain cache keeps the run's total requests within +15% of `s28b`. Otherwise it stays 8 and the news/article caps share it.
- **Starvation guard:** the hiring and news counters are separate (H9). Each family gets its minimum (2 requests) before the other takes more.

**Fixtures:**
- one real or realistic page per platform: WordPress feed with page 2, wp-json, Wix `blog-feed.xml`, Squarespace `?format=rss`;
- a listing that only links an archive;
- a JS listing with a sitemap holding three `/nyheter/<slug>` URLs;
- Norwegian text dates (`22. september 2025`, `22.09.2025`);
- a URL date;
- a page dated in the future (rejected).

**Acceptance:**
- Live 999 news companies at least 3× S28 (10 → 30 or more) if T1 + T2 work as Jaswanth measured. Report the real number either way.
- Runtime within +15% of `s28b` (1,012 s).
- 0 junk.

### T3 — Hiring breadth

Starting from S27 (`8d2202c`, `87c277e`), add H2–H8:
- **Heading test (H2):** a careers candidate is accepted only if its h1/title/first 6 h2 name careers or jobs (Norwegian and English vocabulary), and probes must land on a careers path. Keep S27's "career terms must start a path word".
- **Depth 2 (H3):** when no careers link is on the homepage, fetch up to 2 about/company pages (`om-oss`, `om`, `about`, `selskapet`, `bedriften`, `hvem-er-vi`, ...) and look there.
- **Sitemap discovery (H4):** sitemap page URLs whose first two path segments contain a job segment, only when depth 2 found nothing. Reuse the sitemap fetched for T2, at most 1 sitemap fetch per company.
- **ATS links (H5)** on the careers page or homepage → posting facts with `platform`. The link text must be a real title, not "les mer"/"søk her"; otherwise use the nearest previous h2–h4. The careers page stays the `hiring_signal`.
- **JobPosting (H6)** accepted only if `hiringOrganization.name` is absent or matches this company (name tokens or org number). A recruiter posting for a client is rejected.
- **Apply markers (H7):** careers sub-pages under the careers path with apply markers become postings (title = page h1/title). At most 8 sub-pages are checked; a job-posting cap of 25 and the existing per-family fetch cap apply.
- **No-openings text (H8)** stored as `explicit_no_openings: true` + verbatim span. The careers signal is still `available`.

**Fixtures:**
- careers link only on `/om-oss`;
- careers in the sitemap only;
- a blind probe redirecting to the homepage (rejected);
- a recruiter JobPosting for another employer (rejected);
- Teamtailor link list;
- a "for tiden ingen ledige stillinger" page (signal kept, flag set);
- Upheads-style future date as closing (keep).

**Acceptance:**
- Live 999 `hiring_signal` companies rise above 35 by more than the noise.
- A 20-claim hand audit finds 0 non-careers pages (no `/utstillinger/`-type errors).

### T4 — NAV index freshness (procedure, small code)

- The shipped index is dated 2026-10-08, and ads expire daily.
- Before **each** revision commit, the planner rebuilds the index: `scripts/connectors/run_nav_jobs_connector.py`, then `rekey_nav_index.py`.
- Code: the run report already states the index date. Add a warning line to the report when the index is more than 7 days old at run time. No runtime feed scan (token endpoint flaky, about 65 s, and 245 MB for the full scan per Sahvendra).
- Investigate why PETROLINK has 3 index ads but only 1 published, with 0 rejections recorded (recall engineer finding). Fix if it is a bug.

**Acceptance:** stale warning fixture; a PETROLINK explanation recorded in BRAIN.

### T5 — Social breadth

- **S2:** accept a handle equal to the verified site's domain label plus TLD separator (`g3i_no`, `g3i.no`, `g3ino`) on any platform including X. Keep the two-link-per-platform cap.
- **S3:** Organisation/LocalBusiness JSON-LD `sameAs` on the verified site's pages → officially linked (still normalised, share URLs excluded).
- **S4:**
  - collect links from header/nav/footer/social-bar regions of the homepage, plus the contact and about pages already fetched for identity or site facts (no extra fetch);
  - if neither was fetched, allow **one** extra fetch of the first linked contact/about page;
  - footer links on chain/franchise sites keep the existing brand rules.
- **Measure** on reference-100 with T0 (social matched was 35 of 103 offline before S27/S28). Report gained/lost.

**Fixtures:**
- `x.com/g3i_no` on `g3i.no` accepted;
- `sameAs` LinkedIn accepted;
- footer-only Instagram accepted;
- a parent-group LinkedIn on a franchise page still blocked;
- a personal profile (`facebook.com/ola.nordmann`) still blocked;
- `sharer.php` excluded.

**Acceptance:** reference-100 social matched up by more than the noise; a 30-link hand audit finds 0 non-own handles.

### T6 — Stop losing sites to load (website family)

About 11 sites per run are lost to timeouts and connect failures, more since S28's concurrent tiers.
- **W17 run-wide per-registered-domain cache (in memory):** robots result, homepage document and redirect chain are shared across companies in one run. Identity is still assessed per company. Bounded by count and bytes. The cache must not cross runs (cold-run rule).
- **W18 per-host circuit breaker:** after 3 consecutive connect/timeout failures on one host, stop fetching it for the rest of the run. The affected candidates are `failed` with `failure_kind: circuit_open`, which is honest and second-chance eligible only if the breaker opened in the first minute.
- **W16:**
  - HTTP 406 → retry once with `Accept: */*`;
  - one homepage retry on 429/502/503/504, honouring `Retry-After` up to 5 s;
  - 401/403 → `blocked`.
  - Verify which parts exist; add the missing ones.
- **Concurrency of discovery tiers (S28 `6874084`):** add a per-company in-flight candidate cap (default 3, configurable) instead of 6 concurrent, and measure. Strong-source candidates (registry website/email, NAV homepage, sub-unit) always go in the first wave.
- **E6:** count real attempts (retries and redirect hops) in the report.

**Fixtures:** shared domain fetched once for two companies but assessed twice; breaker opens after 3 failures; 406 retry; 503 + `Retry-After`.

**Acceptance:**
- Live 999 sites lost to timeout/connect at least halved versus `s28b` (report the list).
- Total requests do not rise.
- 0 wrong-company.

### T7 — More website candidates

- **W4 Wikidata (on by default, fail-open):**
  - One SPARQL POST per 200 input org numbers at batch start: `VALUES ?orgnr {"923609016" "923 609 016" ...}`, `?item wdt:P2333 ?orgnr; OPTIONAL wdt:P856 ?website`.
  - Identifying UA, timeout 20 s, total wall cap 30 s for the batch. On any error the source is skipped and reported.
  - An item is used only if exactly one item carries that org number.
  - The website becomes a candidate with source `wikidata_p856` and goes through the **unchanged** gates. It is never evidence.
  - Cache only in memory for the run.
- **W2:** add sub-unit `epostadresse` domains as candidates if absent (generic mail hosts excluded, as now).
- **W5 Fagfolkguiden (off by default, `--candidate-fagfolkguiden`):**
  - One request to `https://www.fagfolkguiden.no/bedrift/{slug}-{orgnr}`, only for companies still without a site after free sources. Slug from the legal name, lower-cased, ASCII-folded, legal suffix removed, spaces → `-`.
  - Read JSON-LD `sameAs`/`url` → candidate `fagfolkguiden_sameas`.
  - Robots allows `/bedrift/`. A directory is never evidence.
  - Measure on 999 before proposing default-on.
- T0 reports yield per source.

**Fixtures:** SPARQL JSON response parsing (plain and spaced org number, duplicate-item rejection); network failure → skipped; Fagfolkguiden JSON-LD parsing; disabled by default.

**Acceptance:** each source reports candidates, verified and published counts on live 999; 0 wrong-company in a hand audit of every site these sources add.

### T8 — Safety vetoes for every non-org-number rule (W11–W15)

Apply these to G4 rules (name+address, registry-declared, registry-exact, name-postcode) and to any new candidate source. A page with this company's own valid org number is exempt from D2/W12/W14/W15, not from D1/D3/W13.
- **D1 third-party markers** in title/h1/meta description: `fansite`, `fan site`, `fanside`, `supporter`, `uoffisiell`, `unofficial`, `ikke offisiell`, `wiki`, `katalog`, `bedriftsoversikt`, `firmaliste` → veto.
- **D2 delegated contact:** the site lists contact emails, none on its own registered domain, and at least one on another registered domain that is not a free-mail host → veto (arasenstadion.no → `lsk@lsk.no`).
- **D3 directory shape:** three or more distinct checksum-valid org numbers on the crawled pages → veto.
- **W12:** a registry phone shared by 5 or more entities in the bulk snapshot is not corroboration. Precompute counts once per run from the bulk file already loaded.
- **W13 parked/for-sale:** page text or title matching `domain (is )?for sale`, `dette domenet er til salgs`, `parked`, `buy this domain`, known parking hosts (sedo, dan.com, afternic, bodis, parkingcrew, one.com default page, "Web Hosting by", "Coming soon" with under 200 visible chars) → veto.
- **W14:** a legal name with `BIL`/`bedriftsidrettslag`/`idrettslag` matched only through the employer's name → no name-only publication.
- **W15:** a registered `DAGL`/`LEDE` person's full name on the site counts as **one** corroborator for name rules. It never stands alone.

**Fixtures:** arasenstadion.no replica (D1+D2); a three-org-number list page (D3); a shared accountant phone (W12); a Sedo parking page (W13); a corroboration case where the CEO name lifts a name+postcode candidate that had one corroborator.

**Acceptance:** re-run the S27/S28 hand-audited sets. **No previously correct site is lost** (list any that are), and the fixtures pass.

### T9 — Envelope mirror in Builderr's sample layout (E1)

Our envelopes are valid, but nobody knows which path the scorer reads for social, news and hiring. Jaswanth's v2 scored 0.0% social while publishing social links in a list-valued claim. Add, with no new facts:
- `external.handles`: `[{platform, url, rightsStatus: "approved"}]`, the same normalised URLs as `social_profile` claims.
- `external.company_site`:
  - `{posts: [{text: title, date_published: published_at, source: url, retrievedAt, hash, sourceClass: "company_owned"}], jobs: [{title, url, posted, source, retrievedAt, hash}]}`;
  - `careers_page: {url, retrievedAt, hash}` when a careers signal exists.
  - This mirrors the sample's `external.linkedin` key names (`date_published`, `source`, `retrievedAt`, `hash`).
- Keep `profile.evidence.website.value.social_links` (the kit path) exactly as now.
- `scripts/analysis/envelope_audit.py`: assert the mirror equals the claims (no extra or missing facts).

**Acceptance:** envelope validator and audit pass; the smoke envelope size grows by under 10%.

### T10 — Evidence span audit (E2)

- New `scripts/analysis/span_audit.py`. For a sample of N published claims (default 100, seeded):
  - re-fetch the cited `source_url` through the safe opener;
  - check that `claim_span` (and `date_span` for news) occurs verbatim in the visible text **or** in an attribute value of the fetched page;
  - report percent verbatim by field, plus failures.
- **Fix systematic failures found:**
  - spans we compose (concatenations, normalised numbers, synthetic registry lines) become verbatim fragments;
  - accounts spans use the value as printed in the API response the evidence hashed;
  - registry claims cite the live Enhetsregisteret record URL, not the bulk file, where a live record was fetched.
- Report the before/after percentages in BRAIN.

**Acceptance:** 98% or more verbatim on the planner's live sample; no claim loses its evidence.

### T11 — Optional search for candidate websites (off unless Builderr supplies a key)

**Providers and keys.**
- Read keys only from env: `BRAVE_API_KEY` / `BRAVE_SEARCH_API_KEY`, `SERPER_API_KEY`, `TAVILY_API_KEY`, `SERPAPI_API_KEY`.
- Comma-separated pools are allowed. Absent keys mean zero behaviour change and zero imports of provider SDKs (plain HTTPS).
- Document the exact variable names in README "Models / APIs".

**Who is searched (adaptive).** Only companies without a published site after the free sources and after T6/T7. Larger companies first (registry employees, then legal form ASA/AS with accounts revenue), so a capped budget goes where sites exist.

**Queries.** One query per company: `"<legal name>" <orgnr>`. A second query `"<legal name minus legal-form suffix>" <municipality>` only if the first returns no acceptable candidate.

**Candidates.**
- Results go through the existing search-candidate pre-filter: directories, social and blocklist rejected; registered domain only.
- At most 3 candidates per company go through the **unchanged** gates.
- Results are kept in memory only; nothing is written to disk.

**Budget.** `SIGNALPOST_SEARCH_MAX_QUERIES` (default 1,000 per run) and a wall cap (10% of the run budget).

**Failure handling.**
- On 429/403: next key, then next provider.
- Three consecutive provider failures open the circuit for the run.
- **No search failure may alter an envelope's terminal state** (Karthik's 429 crash cost him 4.8 synthesis + 4.8 UX).

**Reporting.** Queries, provider, cost estimate (Brave $5 per 1,000), candidates, verified, published.

**Fixtures:** fake provider returning a directory, the right site, then a 429; key pool rotation; absent keys = no calls; a provider exception never changes envelope states.

**Acceptance:** with no keys, the 100 smoke run is byte-for-byte the same claims as before; with a test key (planner's own key, local only, never committed or used in an official run), a 100-company run publishes only gate-verified sites.

### T12 — Synthesis: sourced signals apart from inference (E7)

- In the company brief or summary, add a `signals` list. Each item is a sourced fact:
  - revenue change between the two latest filed years;
  - registered employees;
  - hiring signal present / number of postings;
  - latest dated news date;
  - leadership change within 12 months (roles `last_changed`);
  - new sub-unit.

  Each item carries evidence ids.
- Add a separate `inference` list of one-line conclusions, each naming the signal ids it rests on, and an `unknowns` list (existing `unknown_areas`).
- No model. Deterministic templates only. Missing stays missing.

**Fixture:** a company with growth, one with decline, one with no accounts (no revenue signal, listed as unknown).

**Acceptance:** contract validator passes; 10 hand-read summaries contain no unsupported statements.

## 5. Revision plan (deadline: revisions close 18 Oct)

- **Now:** v2 is ready at `501df3c` and still not pushed. Push and send the v2 mail first. Every day v1 runs drags the mean down, and nothing here blocks v2.
- **v3 (target 14 Oct):** T0, T1, T2, T3, T5, T6, T8, T9.
  - These are the four families plus safety, and no new external source.
  - Gate: live 999 + reference-100 per-family table, noise floor, hand audits, clean-machine check, 2-CPU Docker 1,100/600 s check (S26 procedure).
- **v4 (target 17 Oct):** T7, T10, T12, plus T11 wired but off.
  - T11 is default-on only if Builderr's official-run instructions name a search key variable.
  - T4 rebuild before each of v3/v4.
- **Reserve:** one revision for a crash fix after a Builderr-reported failure. Never spend it on polish.

## 6. Planner verification checklist (after each Codex report)

1. Merge with `--no-ff`; full suite passes.
2. T0 table on reference-100: before vs after per family, against the noise floor.
3. Live 999: runtime, RSS, timeouts, per-family companies and facts, sites lost (listed), envelope audit + junk detector.
4. Hand audits:
   - every new website;
   - 30 news claims;
   - 20 hiring signals;
   - 30 social links.
   - Wrong-company must be 0.
5. Span audit (after T10) at 98% or more verbatim.
6. Clean-machine check at the candidate commit; 2-CPU Docker 1,100-row 600 s run is valid.
7. BRAIN.md updated with numbers, decisions, next step.

## 7. Source map (where each idea was read)

Planner scratchpad clones: `/private/tmp/claude-501/-Users-ayushpatel-Documents-GitHub-SignalPost-Agent/a28d5ba0-fb14-4688-aa42-5ee26caf7531/scratchpad/competitors/`. These are temporary; this table is the durable record.

| Repo | Board | What we took |
|---|---|---|
| `Jaswanth-dev-69/signalpost` | 53.04 (recall 8.05) | Scorer notes, Builderr v2 feedback examples, news pipeline (N1–N10), careers pipeline (H2–H8), envelope mirror (E1), verbatim spans (E2), Wikidata/NAV-homepage/OSM measurements, noise floor |
| `livingmangal/signalpost-agent` | 59.75 | Builderr v2 feedback (four families), Karthik's 429 failure, search cascade with key pools (W10) |
| `nikita-webdeveloper/signalpost-agent` | #1, 60.80 | Domain-token social rule (S1, have), email-domain discovery (W1, have) |
| `Sahvendra7/signalpost-agent` | — | Fan-site false match and disqualifiers D1–D3 (W11), `sameAs` profiles (S3), run-wide `SiteSession` (W17), NAV/Brave cost measurements |
| `gautamhardik/signalpost` | 58.57 | Own-name social handle `g3i_no` (S2), person corroboration (W15), parked-domain veto (W13), growth signals vs inference (E7) |
| `biswajeetdev/signalpost-agent` | 57.93 (evidence 29.00) | Shared-phone threshold (W12), chunked checkpoints (E5) |
| `abhinavlenka/signalpost-agent` | 58.31 (evidence 28.99) | NAV-ad homepage (W3, have), sitemap article discovery (N10) |
| `Samkit1707/signalpost-entry` | — | Wikidata batch SPARQL (W4) |
| `San245o/sp-intelligence-agent` (Sankeerth) | 46.12 | Fagfolkguiden `sameAs` (W5), sports-club veto (W14); rejected: ranker, LinkedIn CSV |
| `prikshitpb0901-oss/builder_ai` | 58.57 | Rejected: LinkedIn typeahead/jobs, NAV name search, reviews |
| `anur4ag/signalpost` | — | Region-based social links (S4), JobPosting `hiringOrganization` check (H6) |
| `rasaldushyant17/signalpost-agent` | 18.40 | Per-host circuit breaker, real-attempt counting (W18, E6) |
| `kartikchilkoti/singlepost` | 60.05 | Rejected: Exa with a hardcoded key |
| `Dev-angPatil`, `sruthi-r-22`, `iamjaydatt2006`, `martyn-in` | — | Nothing new beyond the above (LLM classifiers rejected; JSON-LD job extraction already covered) |
