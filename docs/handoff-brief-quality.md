# Handoff S17: company-brief quality, runtime and recall robustness (v2 gate)

**For:** Codex session S17 · **Branch:** `feat/brief-quality` · **Date:** 2026-10-09 · **Depends on:** main with S14 merged (`cd7adf8`) plus planner fixes up to the second-chance retry.
**Read first:** `BRAIN.md`, `docs/handoff-site-facts.md` (S14 results), `src/norway_company_agent/site_facts.py`, `scripts/run/run_competition_batch.py`.
**Rules:** same hard rules as S14 (safe opener, robots, no keyed API, no model call, evidence on every claim, missing never zero, no gate weakened, no tag/push/submission, no co-author trailers, do not touch `config/connector-policy.json`). Your sandbox may have no network: use fixtures built from the real samples below; the planner runs live passes.

## Why (measured by the planner on the live 999-company pass, `out/s14-live1000/`)

- **The brief is noisy.** For the 89 companies with a published site, **64 briefs contained page furniture**: menu words ("Hjem Om oss Kontakt", "MENY", "Skip to Content", "Open Menu Close Menu", "top of page", "Logg inn", "Powered by SmugMug"), address and phone lines, and mixed English/Norwegian ("Registered in X; industry: Y"). 63 of 89 were marked `ambiguous` mostly because the shared-stem check is too strict. This hurts the synthesis score ("explain what the company does") and the credibility of the claim.
- **Runtime doubled.** The 999-company pass took about 33 minutes against 17.5 before S14 (limit agreed: at most 2x; the official time budget is unknown, so this is the main risk).
- **Website recall dipped** from 99 to 89 published sites, mostly registry-linked sites that timed out under load (the planner added a second-chance retry; keep it).
- Other live numbers to keep: hiring available 25 (NAV plus company sites), public activity available 32, accounts history 917 of 999, envelope audit clean (0 missing evidence, 0 financial mismatches).

## Task 1: a clean, honest company brief

Rewrite `extract_company_brief` so the site part is a proper description or nothing:
1. Candidate sources, in order: JSON-LD `description` (Organization/LocalBusiness/WebSite), `<meta name="description">`, `og:description`, then the first paragraph inside `<main>`/`<article>`/the largest text block.
2. Reject candidates that look like furniture: fewer than 8 words or fewer than 50 characters; more than 25% of tokens in a Norwegian/English navigation vocabulary (hjem, om oss, kontakt, meny, logg inn, søk, cookies, personvern, skip to content, menu, home, about, contact, login, powered by, copyright, ©, top of page, nyhetsbrev...); a phone number, e-mail address or street address; ALL-CAPS runs; repeated phrases. Strip everything after the second sentence; keep at most 2 sentences and 280 characters, cut at a sentence boundary; remove duplicate whitespace and stray replacement characters.
3. The registry part is written in Norwegian and kept separate: "Registrert i <kommune>. Bransje: <næringskode-tekst>." (omit the industry when the registry says "Uoppgitt").
4. States: `available` when a clean site description exists (the claim value has the site sentence first, the registry line second, each with its own evidence span); `available` with `registry_derived: true` when only the registry part exists; `ambiguous` **only** when the site text clearly contradicts the registry industry (no word of the registry industry label, no synonym from a small built-in Norwegian industry word list, and the site text is a different language register such as a template/parked page); a template or parked-page description ("this domain is for sale", "under construction", "Coming soon", default WordPress text) is rejected entirely.
5. Fixture tests built from the real noisy examples above (Kitech, Resipientanalyse, Arkwright, Radiatorservice, Aquascan, Langesund Bad, Thomas Angells Stiftelser, Dag-Inge Aas, Atletix, VOKKS): each must produce either a clean 1 to 2 sentence site description or the registry-only brief, never furniture. Add a test corpus of at least 20 hand-written HTML snippets for edge cases.

## Task 2: runtime back under control

- Measure where S14 spends its time (add per-phase timing to `run_metrics`: discovery, activity, hiring, brief, DNS, fetch, parse) and report the breakdown on a fixture run.
- Cap extra work per verified site: at most 4 extra requests (feed, sitemap, careers page, news page), 6 s per request, 12 s total for all S14 extractors per company; skip the extractors when less than 40% of the per-company budget remains or when the global run budget is in its soft phase.
- Make the three heavy parses (activity, hiring, brief) reuse the already-parsed soup of the pages the gate fetched rather than re-parsing HTML.
- Reduce thread oversubscription: the discovery tier pools (up to 6 threads per company) times 16 outer workers can exceed 100 concurrent fetch threads on 8 cores. Bound the process-wide number of concurrent fetches (default 48) with a semaphore, and keep per-host pacing. Document the defaults.
- Target (to be confirmed by the planner live): 999 companies in at most 20 minutes on 8 cores, with website recall at least as good as the 17.5-minute run (99 published).

## Task 3: recall robustness for registry-linked sites

Keep the second-chance retry in `run_competition_batch.py`. Extend it: companies whose registry website or registry e-mail domain resolved but timed out get one retry with a 25 s budget; add a unit test with a fetcher that fails once and then succeeds. Report `second_chance` attempted/recovered in the run report (already present). Also make sure a candidate that loaded a page but failed the gate is never retried (only failures are).

## Task 4: honest scorecard

The local scorecard uses our own runs as the pool, so recall comes out near 100% by construction (it printed 93 on the 100-company smoke). That number says nothing about the real score. Change `scripts/analysis/local_scorecard.py` to (a) label the recall and synthesis categories as `self_pool_not_comparable` in the text output and the JSON, (b) show the **absolute counts** per information type (companies covered, facts) next to the ratios, (c) keep the regression checks and the promotion rule (they are meaningful: zero added wrong-company publications, evidence completeness, no regression, runtime ratio), and (d) print the total only as "not an estimate of the official score".

## Acceptance

- Tests for every task; `uv run --with pytest pytest -q` passes (state the count). The envelope validator and audit still pass on the fixtures.
- On a fixture of the ten examples, zero briefs contain furniture words; registry-only briefs are in Norwegian.
- `scripts/run/clean_machine_check.sh` passes.
- If outbound network works, run the 100-company smoke into `out/s17-smoke/` and report timing, the per-phase breakdown and the brief hit rates; otherwise leave the exact commands. **No tag, push or submission.**

## Report back

Append `## Results` with commits, branch, test count, the before/after brief examples for the ten companies, the runtime breakdown and anything unverified. The planner runs the live 100- and 1,000-company passes and the hand audit.
