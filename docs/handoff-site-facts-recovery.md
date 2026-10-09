# Handoff S19: site facts for recovered sites, hiring false positives, over-eager "ambiguous" briefs

**For:** Codex session S19 · **Branch:** `feat/site-facts-recovery` (from `main` at the S18 verdict commit) · **Date:** 2026-10-09
**Read first:** `BRAIN.md` (S18 verdict), `src/norway_company_agent/site_facts.py`, `scripts/run/run_competition_batch.py` (lines about 460 to 610 and 654 to 680).
**Rules:** same as S17/S18 (safe opener, robots, no keyed API, no model call, evidence on every claim, missing is never zero, no gate weakened, no tag/push/submission, no co-author trailers, do not touch `config/connector-policy.json`). Sandbox has no network: fixtures only; the planner runs live passes.

## Root-caused by the planner (live 999 pass `out/s18-live1000/`)

**Bug A: second-chance recoveries never get site facts (recall loss).** `run_competition_batch.py` runs `enrich_site_facts(...)` inside the per-company worker, then later the second-chance block (`_run_discovery_bounded` with 25 s, `_apply_discovery`) can publish a website for a company that failed the first time. Those companies are never enriched, so they get `public_activity {"items": [], "checked": false}`, hiring `not_available` and the registry-only brief although a verified site exists. Evidence: Stiftelsen Justismuseet (website `complete`, `justismuseet.no`, but `site_fact_requests: 0`, activity `checked:false`; its genuine 2026 news item was lost versus the previous run). Same path for every one of the 5 to 6 recovered sites per 999 pass, and for any recovered G4 site. Fix: after `_apply_discovery` in the second-chance loop, run the same enrich + attach step (factor the existing block into one helper used by both paths, with the same budget guards and remaining-budget accounting), update `run_metrics`, and set module states consistently. Test: a fixture where first discovery fails, second succeeds, and the profile ends with site facts attached.

**Bug B: briefs are marked `ambiguous` far too often.** 63 of 999 are `ambiguous`, and 62 of them are ordinary correct site descriptions flagged `site_disagrees_with_registry` (examples: "Vi utfører alle typer rørleggertjenester…" vs registry "Rørleggerarbeid"; "Møre og Romsdal reklametrykk… selvklebende folier" vs a print/sign industry; "Resultatbygg … Byggmester og entreprenørfirma" vs "Oppføring av bygninger"; Sumogrizzly speaker vs "Annen utøvende kunstnerisk virksomhet"). The S17 spec said ambiguous **only** when the site text clearly contradicts the registry (different language register, template or parked page). Fix:
1. compare with a stem/prefix match (shared prefix of at least 5 letters after lower-casing and Norwegian suffix stripping: rørlegger-tjenester/-arbeid, entreprenør, bygg/oppføring) plus a small built-in industry synonym map (bygg/byggmester/entreprenør/tømrer/oppføring; rør/vvs/rørlegger; reklame/skilt/trykk/folie/grafisk; revisjon/regnskap/rådgivning; frisør/hår/skjønnhet; restaurant/mat/kafé/servering; ...);
2. default to `available`; mark `ambiguous` only for parked/template text, or text in a clearly different register that also shares no industry stem;
3. a registry industry of "Uoppgitt", a residual class ("Annen ...") or a holding/management company never causes ambiguity.
Tests: all the examples above are `available`; a "domain for sale" and a "Welcome to WordPress" snippet are still rejected.

**Bug C: hiring false positives (3 of 11 live).** Arkwright X: postings titled "Oslo", "Careers in Oslo" (a location list, not roles). Polaris Media: "Ledige stillinger" (a section heading). GramArt: "Vi trenger ikke et statsfinansiert Spotify", "Julehilsen fra daglig leder" (blog posts matched on the cues "vi trenger" / "leder"). Fix in the job-title acceptance of `parse_jobs_html`:
1. reject a title that is a heading or label equal to or starting with ledige stillinger / careers / karriere / jobs / join us / jobb hos oss / open positions / stillinger, or a bare place name (single capitalised token or `Careers in <place>`);
2. cue words must match a whole job noun phrase, not "vi trenger" / "søker" / "leder" alone: title at most 12 words, no sentence-final punctuation, no negation (ikke, no longer, not), and not from a URL path of blogg/news/nyheter/aktuelt/artikkel;
3. a posting needs either a structured source (JobPosting JSON-LD, ATS link) or a title that is a job noun (list of at least 80 Norwegian/English roles) optionally with percent/location;
4. otherwise `not_available` with the existing "careers page found, no postings" note.
Tests: the five junk titles produce no posting; the genuine ones from the S18 handoff still do.

**Observation, small change:** the 22 `activity_old_items` the junk detector prints are items flagged `old:true` inside claims that are available because of other recent items; fine by contract. Rename the detector line to `activity_old_items_informational` and exclude it from `passed`.

## Task order and commits

1. Bug A (helper + second-chance enrichment + test), one commit.
2. Bug B (brief ambiguity), one commit.
3. Bug C (hiring titles), one commit.
4. Detector rename, docs, append results to this file, one commit.

## Acceptance

- Full suite passes (317 now, expect 15 to 25 more).
- No change to discovery gates, runtime budgets or website publication rules. Runtime must not grow by more than the cost of enriching about 5 recovered sites.
- Offline: on the stored `out/s18-live1000/profiles.jsonl`, re-deriving briefs gives `ambiguous` below 10 of 999 and still rejects every template fixture (fixtures only if replay is not possible; say so).
- Append results to this file; do not push.

## After this

Planner re-runs smoke and 999 pass and checks: Justismuseet-type recoveries carry activity, hiring false positives 0, ambiguous briefs below 10, published sites at least 95, runtime about 20 minutes. Then hand-audit and the clean-machine check at `HEAD`. If all pass, v2 is ready for the owner's decision to submit (push + mail).

## Results

2026-10-09, Codex S19 on `feat/site-facts-recovery` from `main`.

- Task 1 Bug A: `3209868 fix: enrich recovered site facts`. A shared bounded enrichment/attachment helper is used by both the normal and second-chance discovery paths; the recovery fixture verifies website, activity, brief and hiring evidence are attached after discovery recovers.
- Task 2 Bug B: `d1f5098 fix: relax brief industry disagreement`. Named plumbing, print/sign, building, speaker/artist and residual/holding fixtures are available rather than ambiguous; the existing accounting-versus-fish contradiction remains ambiguous; parked and WordPress text remains rejected.
- Task 3 Bug C: `68011cf fix: reject non-job hiring titles`. The parser rejects the Arkwright X location labels, Polaris’ “Ledige stillinger” heading and both GramArt blog titles, while retaining the S18 genuine, JSON-LD and ATS fixtures.
- Task 4 detector/docs: this commit. The detector field is `activity_old_items_informational`; it remains counted for reporting but is excluded from the `passed` decision. The informational-only regression passes.
- Required test command and output:

  ```text
  $ uv run --with pytest pytest -q
  319 passed, 67 subtests passed in 1.94s
  ```

- Offline replay of `out/s18-live1000/profiles.jsonl`:

  ```text
  {'rows': 999, 'available_websites': 100, 'rederived_statuses': {'available': 92, 'ambiguous': 8}, 'ambiguous_count': 8, 'ambiguous_orgs': ['998002877', '932711028', '991872108', '925386022', '919809930', '976322614', '930054143', '918988742']}
  ```

  The stored artifact had 100 available websites and enough captured page fields for deterministic brief replay. The 20 noisy brief fixtures, including parked/domain-for-sale and WordPress cases, passed their rejection/availability assertions.
- No live 100-company smoke or 1,000-company pass was run from this sandbox; no live timing, coverage, or precision number is claimed. Planner-owned live commands remain in the handoff/README workflow.
- No discovery gate, identity/first-party rule, different-organisation-number veto, domain-uniqueness rule, SSRF protection, runtime budget, or `config/connector-policy.json` was changed. No tag, push, or submission was performed.
