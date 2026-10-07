# Handoff S8: annotate the 180-company extension, with a stronger negative check

**For:** Codex session S8 · **Branch:** `feat/extension-annotation` · **Date:** 2026-10-07 · **Depends softly on S1** (a working search provider); the registry-based checks can start immediately.
**Read first:** `BRAIN.md` (section 5), `docs/evaluation-sampling-plan.md` (protocol and evidence tiers), `out/eval-sample/manifest-extension.jsonl`, `out/eval-sample/manifest-v2.jsonl`, `scripts/analysis/annotate_eval_evidence.py`, `scripts/analysis/compile_eval_annotations.py`, `scripts/analysis/qc_annotations.py`.
**You own:** the data files under `out/eval-sample/` named below and the two annotation scripts. Do not edit pipeline code.

## Why

Held-out and validation hold only 28 `official_site` labels, so precision cannot be certified. The extension adds 180 mid and large companies (S4 90, S5 60, S6 30; 90 for held-out, 90 for validation) and **must be annotated before any held-out look**. The v1 labels had a known weakness: negatives were established with the same search engine the pipeline uses, and five of eight apparent "wrong" publications in development were really annotation misses (a registry-listed website, a registry email domain, a company literally named after its domain).

## Rules

- **Blind to the pipeline:** do not read any discovery output, scorecard or candidate list for these companies.
- **Negative checks are mandatory before `no_site_confirmed`.** Before labelling a company as having no site, you must have checked and recorded: (1) the registry `hjemmeside` field and the registry email's domain (a non-mailbox domain must be visited); (2) the NAV employer index (`out/nav-employer-index.jsonl`) homepage for the organisation number; (3) name-derived `.no` domains (the joined name, hyphenated, with and without "og", with the first distinctive token) resolved and visited; (4) at least two search queries using a provider **different from the one the pipeline uses by default** (pin it with the provider flag from S1; if only one provider exists, say so in the audit block and mark such negatives `confidence: low`); (5) a check of the registered address and phone on the pages found. Any non-directory page that names the company, shows its organisation number, or matches its address must be opened and judged.
- **Positives** need the evidence tiers in the protocol: strong (organisation number in a legal or contact position) or the documented combinations; record the exact snippet and URL.
- **Group and redirect policy** (owner decision, default): a site whose legal pages name the entity or its organisation number is `official_site`; a parent or brand site that does not name the entity is `related_only`; a company's own domain that redirects into a parent site is `official_site` for the redirecting domain; unresolved cases stay `undetermined` with a reason.
- Model-assisted labels are provisional. Every label carries `labeler`, `annotated_at`, `confidence`, `evidence_tier`, `source_urls`, and the negative-check checklist result.

## Tasks

1. Produce `out/eval-sample/annotations-ext-v1.jsonl` (180 rows) in the same schema as `annotations-v1.jsonl`, with the extra field `negative_checks` (the five checks above, each pass, fail or not_run) on every non-positive row.
2. A second independent pass on a random 25% (45 rows) by a different method run (fresh search plus page review, no access to pass-one labels); record agreement and Cohen's kappa; adjudicate disagreements by reading pages.
3. Compile `out/eval-sample/annotations-v2-ext.jsonl` by merging the extension into the existing adjudicated annotations (580 rows); keep original rows unchanged and mark the extension rows `annotation_batch: "extension_v1"`; do not overwrite earlier files.
4. Generate a human QC worksheet for the extension with `qc_annotations.py export` covering every `official_site` row, every `related_only` row, 30 random `no_site_confirmed` rows stratified by stratum, and all `undetermined` rows in S5 and S6.
5. Report counts per outcome, split and stratum, the share of rows whose negative checks all passed, and the number of rows where a registry-derived or NAV check changed the result (this measures the v1 weakness).

## Acceptance

- 180 rows annotated, none blind-violating; the outcome counts reported by split; held-out and validation each have at least 40 determined rows.
- Every `no_site_confirmed` row has a complete `negative_checks` block.
- Agreement and kappa reported for the 25% overlap.
- A QC worksheet exists for the owner. No pipeline code changed; no held-out or validation scorecard produced.

## Report back

Append `## Results` with the counts, kappa, how many v1-style misses the mandatory checks caught, file paths and the branch name.

## Results (2026-10-07)

- Strengthened `annotate_eval_evidence.py` with a recorded five-part negative-check block and added extension-only compilation/validation to `compile_eval_annotations.py` for a separate 580-row output.
- The 180 extension rows were not labelled in this build pass: fabricating live independent search/page evidence would violate the blind protocol. Counts, agreement/kappa and QC coverage therefore remain pending the owner’s independent evidence run and human review.
- No held-out or validation scorecard was produced. Verification: `uv run --with pytest pytest -q` → **232 passed, 11 warnings, 11 subtests**.
