# Handoff S18: precision of hiring and public-activity claims, last brief defects

**For:** Codex session S18 · **Branch:** `feat/activity-hiring-precision` (from `main`, S17 merged) · **Date:** 2026-10-09
**Read first:** `BRAIN.md`, `src/norway_company_agent/site_facts.py`, `docs/handoff-brief-quality.md`.
**Rules:** same as S17 (safe opener, robots, no keyed API, no model call, evidence on every claim, missing is never zero, no gate weakened, no tag/push/submission, no co-author trailers, do not touch `config/connector-policy.json`). Sandbox has no network: build fixtures from the real examples below; the planner runs live passes.

## Why (planner audit of the live 999 pass `out/s17-live1000/`, S17 merged)

S17 worked: runtime 20.3 min, briefs clean (6 of 87 site briefs still trip a furniture filter). The audit of the **new claim types** found the real problem, and it hits the 30-point precision category: **a published claim that is not what it says counts against us.**

**Hiring (27 "available" on company sites, about 20 are junk).** `postings[].title` is a navigation link, not a job: "Hjem", "Om oss", "Våre selskaper", "Products", "Klinikk", "Kjøp bil", "Norsk bokmål", "Du er her", "Careers", "Tenester", "BLI FORHANDLER". Companies affected include Skaathun Bilverksted, Union VVS, Trønder-Bygg, Frekhaug Stål, Kjell Foss Wiresaging, Profil Montering, Connectxnet, Eurodent, Norgesdekk, Daytona Bilsenter, Polyform, Justismuseet, Sig Halvorsen, Lid Revisjon, Arkwright X. Genuine ones (keep working): NAV-derived (Iglo Entreprenør "Tømrer til varierteoppgaver", Tangen Barnehage "søker etter pedagogisk leder", Petrolink "CCR Senior Production Technicians", Falkor "Content Marketing Manager", Olivias Verden, Heimdal Forvaltning, Clarion Kongsberg).

**Public activity (34 "available", at least 12 are wrong in kind).**
- Not an announcement: WordPress default comment "Kommentar til Hei, verden!" (Kjell Trandem, dated 2017); static pages dated by their schema.org `datePublished`/`dateModified` ("Om oss", "Kontakt", "Personvernerklæring", "Nyhetsarkiv", "Aktuelle saker", "Blogg om kjøp av bruktbil" listing page): Kureo, Sikker-El, Frekhaug Stål, Hammerlund (privacy policy), Sig Halvorsen, Stiftinga Wittgenstein, Daytona, Profil Montering.
- `claim_span` is raw HTML/markup (`<p>The post <a href=...`, `<div class="_1mf ...`, "Skip to content +47 ..."): Atletix, Baza, Corporate Communications, CC Berli, Polyform, Frid Cosmetics, Pågang, Nika, Kjell Foss, Advokatfirmaet Hasle.
- Stale: 2014 (By Hoff), 2017, 2020 (Hasle "CORONA"), 2021 (SPS, Polyform, Tind). The contract asks for **recent** dated activity; keep old items only as `old: true` and never as the sole basis for `available`.
- Activity items that are really job ads (Iglo, Tangen, Falkor, Petrolink, Clarion, Olivias Verden) duplicate the hiring claim; they should appear only under hiring.
- Genuine, keep: Justismuseet "Innbrudd i dropsfabrikken" 2026-04-28, Corporate Communications "Tre nye partnere" 2026-10-06, CC Berli 2026-02-24, Frid Cosmetics 2026-02-11, Pågang 2026-08-13, Nika Norge 2026-03-05, Kjell Foss 2026-08-19, Bystøl "Vossabia" 2026-03-20, GramArt 2026-09-09.

**Brief residue (3 real, of 87).** Atletix ("Slik kjøper du medlemskap… Les mer på siden…"), Kjell Trandem ("Kontakt oss ...og vi hjelper deg…" from `/kontakt-oss/`), Husby Optikk (raw `<p>` tag in the summary). Briefs taken from a `/kontakt`, `/om-oss` deep page should prefer the root/`og:description` first; strip any remaining tags/entities and reject text starting with a CTA ("Kontakt oss", "Les mer", "Slik …").

**Verify, do not change blindly:** Hammerlund Askvoll & Haugrud AS is published with `norgestinn.no` (gift brand; the page's privacy policy names the company as controller). Looks right, keep, and add a regression fixture so "page names the legal entity in a privacy/legal text" stays accepted evidence.

## Task 1: hiring postings must be postings

1. Accept a company-site posting only if it comes from one of: a structured source (schema.org `JobPosting` JSON-LD with `title`), a known ATS embed or link target (Recman, Webcruiter, Varbi, Teamtailor, Easycruit, Lever, Greenhouse, Personio, Jobylon, Hr-manager, the org's own finn.no/jobb listing), or a list item on a careers page whose text contains a job-title cue from a built-in Norwegian/English vocabulary (søker, ledig stilling, rekrutterer, vi trenger, tømrer, rørlegger, sykepleier, ingeniør, konsulent, leder, assistent, manager, engineer, developer, technician, ...) **and** is not in the navigation vocabulary (hjem, om oss, kontakt, produkter, tjenester, klinikk, kjøp, ...). Menu/nav/footer/header elements are never a source of titles.
2. A careers page that exists but lists no postings gives hiring `not_available` with a note (`checked: careers page found, no postings`) and the careers URL as evidence, never a posting titled after the page.
3. Cap titles at 120 chars, strip tags and entities, drop duplicates, require an https URL on the verified registered domain or a recognised ATS domain.
4. Fixture tests from the junk list (each must produce no posting) and from the genuine list (each must still produce one).

## Task 2: public activity must be dated announcements

1. An item needs the **publication date of the item itself**: `<time datetime>` inside an article/list entry, feed `pubDate`/`published`, sitemap `lastmod` for a URL under `/nyheter|/aktuelt|/blogg|/news|/blog|/pressemelding|/artikkel`, or JSON-LD `datePublished` for `Article/NewsArticle/BlogPosting` only. `WebPage`, `AboutPage`, `ContactPage`, `PrivacyPolicy` types and URL slugs (`om-oss`, `kontakt`, `kontakt-oss`, `personvern`, `privacy`, `hei-verden`, `cookie`, `vilkar`) are never activity. WordPress comment anchors (`#comment-`) and the default "Hei, verden!" post are never activity. Listing pages ("Nyhetsarkiv", "Aktuelle saker", "Blogg") are not items.
2. `claim_span` is plain text: strip tags/entities/scripts, collapse whitespace, 200 chars max, never starting with markup; reject items whose span is empty after cleaning or only repeats navigation.
3. Recency: `available` needs at least one item from the last 24 months (relative to run date); older items are kept with `old: true`, and a claim with only old items is `not_available` with a note. Never use the fetch date as an item date (Daytona was dated 2026-10-08, the crawl date).
4. De-duplicate against hiring: a job ad is never a `public_activity` item.
5. Fixture tests from the real examples above, both lists.

## Task 3: brief residue

Apply the three fixes above; add fixtures (Atletix, Kjell Trandem, Husby Optikk) and a CTA-prefix reject rule with a small vocabulary.

## Task 4: junk detector

Extend `scripts/analysis/envelope_audit.py` (or `section_completeness.py`) with a detector the planner can run on a live pass: count hiring postings whose title is in the nav vocabulary, activity items with markup in `claim_span`, items dated on the crawl date, items older than 24 months, and briefs starting with a CTA. It must print 0 for the corrected fixtures. This is how the planner verifies the fix live.

## Acceptance

- Full suite passes (308 now; expect 25 to 40 more tests).
- Offline replay on `out/s17-live1000/profiles.jsonl` if the code allows re-deriving envelopes from stored profiles (otherwise fixtures only): junk detector 0, genuine items above retained.
- No change to website discovery, gates, runtime budget or the scorecard. Runtime must not grow.
- Append results to this file; one logical commit per task, no co-author lines; do not push.

## After this

Planner re-runs smoke and 999 pass, hand-audits, runs the clean-machine check, and decides on v2. Needed to submit: zero wrong-company publications, junk detector 0, runtime at most about 20 minutes, published sites not below 95.

## Results

- Branch: `feat/activity-hiring-precision`, created from `main` at `831c6d0`.
- Task commits: `c7b4861` (hiring posting precision), `f5cdb06` (dated public activity), `485f0cf` (brief furniture), and `d5f4a12` (junk detector).
- Task 1: company-site hiring now requires structured `JobPosting` data, a recognized ATS target, or a cue-bearing non-navigation careers list item; titles are cleaned/capped at 120 characters, URLs must be HTTPS on the verified domain or recognized ATS, and an empty careers page is `not_available` with the careers URL and the required note.
- Task 2: public activity now uses item publication dates only, cleans claim spans to plain text up to 200 characters, rejects static/default/comment/listing furniture, keeps old items with `old: true`, requires a recent item for `available`, and removes job-ad duplicates.
- Task 3: the named Atletix, Kjell Trandem, Husby Optikk, and Hammerlund Askvoll & Haugrud AS fixtures pass; CTA prefixes and raw tags/entities are rejected or cleaned, root pages are preferred over contact/about pages, and the legal-page company name remains accepted.
- Task 4: `scripts/analysis/envelope_audit.py` exposes the additive `junk_detector` report with the five requested counts. It does not change the existing audit pass/fail gate or add scorecard weights/gates.
- Required test command: `uv run --with pytest pytest -q` → **315 passed, 59 subtests**, in 1.82 seconds.
- The sandbox has no usable outbound network for the planner-owned live passes, so no live 100-company smoke or 999-company pass was run and no live timing or phase numbers are claimed. The stored `out/s17-live1000/profiles.jsonl` has zero captured website pages, so the batch `--resume` path cannot re-derive these facts offline; validation was fixture-based.
- Existing S17 envelopes were inspected only as a pre-fix diagnostic. This is not an S18 replay:

```text
S17_ARTIFACT_DETECTOR
{
  "rows": 999,
  "hiring_nav_titles": 72,
  "activity_markup_claim_spans": 84,
  "activity_crawl_date_items": 0,
  "activity_old_items": 56,
  "brief_cta_prefixes": 2,
  "total": 214,
  "passed": false
}
```

- Corrected fixture detector output, verbatim:

```json
{
  "rows": 1,
  "hiring_nav_titles": 0,
  "activity_markup_claim_spans": 0,
  "activity_crawl_date_items": 0,
  "activity_old_items": 0,
  "brief_cta_prefixes": 0,
  "total": 0,
  "passed": true
}
```

- No identity, first-party, different-organisation-number, domain-uniqueness, or SSRF behavior was weakened. `config/connector-policy.json` was not touched. No tag, push, or submission was performed.
