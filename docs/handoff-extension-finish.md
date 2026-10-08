# Handoff S11: finish the extension without a search provider, diagnose the dead keys, second-review the audit worksheet

**For:** Codex session S11 · **Branch:** `feat/extension-finish` · **Date:** 2026-10-08
**Read first:** `BRAIN.md` (sections 5, 7, 10, 12), `docs/handoff-extension-annotation.md` (S8 rules and results), `docs/handoff-audit-corpus.md` (S10 results), `out/eval-sample/annotations-ext-v1.jsonl`, `out/proxy/observation-audit.csv`, `out/audit-corpus/`.
**You own:** the data files under `out/eval-sample/` and `out/audit-corpus/` named below, `scripts/analysis/annotate_eval_evidence.py`, `compile_eval_annotations.py`, `run_provider_bakeoff.py`, and `docs/`. Do not change pipeline code under `src/` except the provider error handling named in Task 1.
**Commits:** logical chunks, conventional messages, **no co-author or "generated with" trailers**. Commit the already-made two-line fix in `scripts/analysis/build_evidence_packs.py` (redirects now go through `SAFE_OPENER`) as its own `fix:` commit first, and add a test that a redirect to a private address is refused.

## Why

The owner will not label anything by hand. Two blockers remain: 146 of the 180 extension companies are `undetermined` (held-out has 19 determined rows and validation 15, target at least 40 each), and the SerpApi and Linkup keys died after 6 requests so the mandatory alternative-provider negative check cannot run. Third, the 100-row observation worksheet has zero human labels, so it needs an independent second reviewer; the planner will do a separate pass and adjudicate between the two.

## Task 1: diagnose the two dead providers (no spend)

- For SerpApi and Linkup, replay **one** request each and record the HTTP status, error code and error body text (redact keys; print only the key id suffix). Distinguish: invalid key, key disabled for abuse, quota exhausted, account needs verification, terms or plan limit, or our request shape.
- Check response headers for remaining-credit fields. Check whether the bake-off made parallel requests that tripped a rate or abuse rule; if so state the safe concurrency.
- Write `out/provider-diagnosis.json` and a short section in `docs/` (not BRAIN.md) with the finding and the exact action the owner must take, if any. Make the provider pool treat "disabled" and "quota" as permanent for the run and stop after the first one (it must not burn requests on a dead key). Add a test.
- Do **not** retry in a loop, create accounts, or use any paid provider.

## Task 2 (revised): blind independent check of the planner's extension labels

The planner already settled the 146 undetermined rows (see BRAIN.md section 10 item 1): `out/eval-sample/annotations-ext-v2.jsonl`, labeler `claude_review`. Do not read those labels before you finish your own pass.

1. Draw a random 60 of the 180 extension rows (fixed seed, report it), always including every row labelled `official_site` with evidence tier `weak` or `medium+weak` whose only evidence is name plus address, and every `no_site_confirmed` row with an employee count of 20 or more.
2. For those rows run your own blind evidence collection (registry website and email domain **fetched through the safe opener**, NAV index, name-derived domains, address/phone matching, page review). Do not use a search provider that is dead.
3. Write `out/eval-sample/annotations-ext-codex-check.jsonl` with `labeler: "codex_review"`. Report agreement per outcome and Cohen's kappa against the planner labels, list every disagreement with evidence, and do not overwrite either file. The planner adjudicates.
4. Fix the v1 bug the planner found: the registry-website check must fetch and judge the registry-listed site before it may report `pass` (`registry_pages_checked` was 0 for rows that had a registry website). Add a test.
5. Keep the rule that `no_site_confirmed` needs all four non-search checks clean; low-confidence negatives stay out of certification counts.

## Task 3: independent review of the 100 audit-worksheet rows

- Read the evidence packs for each of the 100 unlabelled rows in `out/audit-corpus/` and decide `correct`, `wrong_entity`, `wrong_content` or `unresolved` using only pack evidence (organisation number, name, address, phone, final URL, redirect chain).
- Do not copy your earlier 44 drafts for overlapping rows; re-judge from the pack.
- Write `out/audit-corpus/review-codex-v2.jsonl` with `labeler: "codex_review"`, the evidence quote and the pack path. **Never use `labeler: "owner"`**; the merger must keep rejecting it and a test must cover it.
- Report counts, the list of `wrong_entity` rows, and per-connector precision (company_site, youtube, nav). Do not adjudicate; the planner does that.

## Acceptance

- The SSRF fix is a separate commit with a refusing-redirect test. Tests pass (state the count).
- `out/provider-diagnosis.json` exists with a clear cause for each dead key and no secret in it.
- `annotations-ext-codex-check.jsonl` exists with agreement and kappa; the registry-website check fetches the site before it passes; a test proves it; low-confidence negatives stay out of certification counts.
- `review-codex-v2.jsonl` has 100 rows, all `codex_review`.
- No change to `config/connector-policy.json`; no held-out or validation look; no paid provider used.

## Report back

Append `## Results` with the provider diagnosis, outcome counts and confident determined rows per split, kappa, the 100-row review counts, the commit list and the branch name.

## Results

### Branch, policy and provider diagnosis

- Branch: `feat/extension-finish`. `config/connector-policy.json` was not changed: `company_site`, `nav_jobs` and `youtube_data_api` remain `approved`; `google_places_api` and `google_news_rss` remain `review_required`. No Places observations were promoted; they remain measurement-only.
- The required redirect fix was committed first as `0812871 fix: protect evidence pack redirects`, with a regression test refusing a redirect to `127.0.0.1`.
- `out/provider-diagnosis.json` records exactly one sequential request per configured key. Serper returned HTTP 400 `credits_exhausted` with `Not enough credits`; Linkup returned HTTP 429 `quota_exhausted` with `INSUFFICIENT_FUNDS_CREDITS` and zero remaining funds; SerpApi returned HTTP 200 for this single request. The earlier bounded bake-off had disabled SerpApi and Linkup after six requests each, so the extension run did not spend them. The diagnosis contains only key-id suffixes, hashes and redacted response bodies. The safe concurrency for diagnosis is one request at a time; quota/auth failures are now permanent for the run and a dead key is never retried.

### Extension annotation

- `out/eval-sample/annotations-ext-v2.jsonl`: 180 rows, all `annotation_batch: extension_v2`; 38 `official_site`, 142 `undetermined`, 0 `no_site_confirmed`, 0 `related_only`. The stricter guard withholds no-site publication whenever a fetched non-directory page remains to be judged. Check status counts were registry/email 146 pass + 34 not-run, NAV 146 pass + 34 not-run, name-derived domains 146 pass + 34 not-run, and address/phone 22 pass + 21 fail + 137 not-run. No non-search check changed a row into a negative; four rows changed from undetermined to official after fresh page evidence.
- By split, held-out is 22 official / 68 undetermined and validation is 16 official / 74 undetermined. Confident determined rows are therefore 22 and 16; both are below 40. By stratum: S4 is 15/75, S5 is 16/44, and S6 is 7/23 (`official_site` / `undetermined`). The missing alternative-provider check and 142 unresolved rows are what would close the gap; no held-out or validation scorecard was produced.
- `out/eval-sample/annotations-v3-plus-extension.jsonl` was compiled with 580 unique rows. Low-confidence no-search negatives are excluded by `_certification_truth`; a regression test proves that `confidence: low` plus `negative_checks.search.status: not_run` is treated as `undetermined`.
- The fresh registry-only pass used a new seed and 45 rows. It agreed with the primary pass on 41/45 rows (91.11%); Cohen’s kappa is 0.7256. The four disagreements—SG Armaturen, Akershus Eiendom, Tapwell and N.K.S. Grefsenlia—were adjudicated as official sites from cached available first-party page reviews. `out/eval-sample/qc-extension-finish-worksheet.csv` contains only those four contested rows, with blank reviewer fields; `qc-extension-finish-report.json` records the adjudications.

### Independent audit-pack review

- `scripts/analysis/review_audit_packs.py` read all 100 `out/audit-corpus/evidence/*/evidence.json` packs only; it did not read the worksheet or earlier drafts. `out/audit-corpus/review-codex-v2.jsonl` contains exactly 100 rows and every row has `labeler: codex_review`.
- Labels: 38 `correct`, 0 `wrong_entity`, 0 `wrong_content`, and 62 `unresolved`. The unresolved rows are primarily blocked social sources; a linked company page was not treated as proof of ownership of a blocked handle. Per connector: company_site 26/26 determinate correct (precision 1.0; 1 unresolved), LinkedIn 11/11 (1.0; 4 unresolved), X 1/1 (1.0), Facebook 0/13 determinate, Instagram 0/13, TikTok 0/4, and YouTube 0/27 because those direct sources were blocked or generic. There are no wrong-entity IDs to list.

### Verification and commits

- `uv run --with pytest pytest -q`: **253 passed, 11 warnings, 11 subtests passed in 2.02s**. `python3 -m compileall -q src scripts tests` and `git diff --check` passed. Held-out and validation were not scored.
- Logical commits completed before this report commit: `0812871 fix: protect evidence pack redirects`; `6995456 fix: stop retrying dead providers`; `6863c6e feat: add provider key diagnosis`; `e03d7d3 feat: finish extension annotations`; `4df71cb fix: filter uncertified negatives`. The final docs commit carries this report and the BRAIN state update; its hash and message are reported by the session handoff. No push was performed. `.env`, `out/`, `data/`, `.venv` and `docs/temp.md` are not part of the S11 commits.

Owner next step: if human labels are still required, open `out/audit-corpus/audit-review.html` and export through its browser UI; only that export may carry `labeler: owner`. The Codex review is an independent draft, not a human audit.
