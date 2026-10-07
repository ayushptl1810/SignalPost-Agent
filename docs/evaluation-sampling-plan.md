# Evaluation set: sampling and annotation plan

**Status:** v1 evidence run complete; network-corrected development v2 saved; extension drawn; human QC still required · **Date:** 2026-10-07 · **Basis:** [discovery-literature-survey.md](./discovery-literature-survey.md) (WIN/ESSnet protocol) and the registry data in `data/brreg-enheter.csv`.

**Purpose.** Give every future round a trustworthy answer to "how accurate are the websites we publish, and how many real ones do we miss?" Until this exists, the scorecard gate stays at `INSUFFICIENT_LABELS`.

## 1. What the data says (computed 2026-10-07)

Population: active entities whose latest submitted accounts year is 2025.

| Stratum | Companies | Share | Registry lists a website |
|---|---|---|---|
| S1 AS, no employee count, other activity | 177,731 | 42.3% | 9.4% |
| S2 AS, no employee count, property (NACE 68) | 88,739 | 21.1% | 4.4% |
| S3 AS, no employee count, holding / unspecified (NACE 64, 00) | 59,979 | 14.3% | 0.9% |
| S4 AS/ASA, 5–19 employees | 41,798 | 9.9% | 18.8% |
| S5 AS/ASA, 20–99 employees | 14,603 | 3.5% | 30.1% |
| S6 AS/ASA, 100+ employees | 2,468 | 0.6% | 45.5% |
| S7 non-AS forms (BRL, ESEK, STI, FLI, NUF, …) | 35,158 | 8.4% | 33.4% |
| **Total** | **420,476** | | **11.0%** |

What this means for the design:

- **A simple random sample would be 78% S1–S3.** Those are mostly property, holding and tiny firms where most truth is "no website". A random 400 yields few real sites, so too few published ones to measure precision.
- **S2 and S3 are where a precision-first system fails worst**: a wrong URL for a company that has none. They have to be in the set, to measure false positives.
- **Larger and non-AS strata have the highest website rates (30–45%).** Oversampling them gives enough published sites, as the WIN report advises ("oversample the larger firms").
- **Discrepancy to resolve:** this filter gives 420,476 rows against 411,160 in `data/universe-metadata.json`. The frozen universe file `signalpost-company-universe-2025.jsonl.gz` named in the README is not in `data/`. Use the frozen file if it can be obtained; otherwise record the filter and the CSV hash.

## 2. Design

**Sample size: 400, stratified, drawn once with a fixed seed.** Weights (N_h / n_h) turn stratum results back into population estimates.

| Stratum | n | Why |
|---|---|---|
| S1 | 100 | The dominant real-world case |
| S2 | 40 | Measures false positives where truth is mostly "no site" |
| S3 | 40 | Same, for holding and unspecified firms |
| S4 | 80 | Mid-size, about 1 in 5 list a site |
| S5 | 60 | |
| S6 | 30 | Nearly all have sites; tests exact-vs-group confusion (parents, brands) |
| S7 | 50 | Co-ops, foundations, foreign branches: many shared or manager sites |
| **Total** | **400** | |

**Splits:** assign by hash of organisation number *within each stratum* so every split sees every stratum: **development 240** (tune thresholds and rules), **held-out 80** (checked once per candidate change), **validation 80** (opened once, at the end). This keeps the 60/20/20 ratio of the competition's own evaluation split.

**Extension for measurable held-out precision.** The original held-out and validation sets together contain only 28 `official_site` labels, so a 0.90 Wilson lower bound cannot be reached even with perfect predictions. `scripts/run/select_eval_sample.py --extension` therefore draws 180 additional, previously unused companies from S4/S5/S6: 90 S4, 60 S5 and 30 S6. Rank parity assigns 90 to held-out and 90 to validation. The generated `out/eval-sample/manifest-extension.jsonl` is immutable evidence of the draw; `manifest-v2.jsonl` is the combined 580-row manifest with recomputed S4–S6 weights. The extension must be annotated before it can be used as gold; drawing it does not itself create labels.

**Phasing, to control annotation effort:**

1. **Pilot, 60 companies** (random from S1 and S4). Measures annotation time per company, the base rate of real sites, and annotator agreement. Adjust n if the base rate is far from expectation.
2. **S4–S7 first (220 companies).** They produce most of the published sites, so precision becomes measurable earliest.
3. **S1–S3 next (180).** Abstention and false-positive evaluation.

**Why 400.** A published-site precision with Wilson lower bound ≥ 0.90 needs enough published sites. Computed:

| Published sites (all correct) | Wilson 95% lower bound |
|---|---|
| 20 | 0.84 |
| 35 | 0.90 |
| 50 | 0.93 |
| 100 | 0.96 |

With one wrong in 60 the bound is 0.91; two wrong in 100 gives 0.93. For recall, N true sites gives about ±14 points at N=50, ±10 at N=100, ±7 at N=200. So the plan needs **at least 35 published sites in held-out plus validation combined, and ideally 100 in total**. If the pilot suggests the pipeline will publish fewer, raise S4–S7 and cut S1.

## 3. Annotation protocol

Adapted from the Istat annotation table used in the WIN exercise ([Summa, 2025](https://win2025.stat.gov.pl/Content/Presentations/I.2.%20Donato%20Summa.pdf)).

**Annotators are blind to our pipeline's output** (manual search, not labeling our own results), because labeling our output inherits our search engine's blind spots ([WIN report, §3](https://cros.ec.europa.eu/system/files/2023-12/20220131_url_finding_methodology.pdf)).

**Fixed search recipe per company, logged:** (1) `"<legal name>" <municipality>`; (2) `"<legal name>" <organisation number>`; (3) the Brreg record (address, email, phone, website field); (4) the company's email domain if it is not a mailbox provider; (5) one check of directory and social results to spot a social-only company. Time-box 6 minutes; log queries and time.

**What counts as the official site:** the company's own dedicated website. Not a directory, a social profile, a marketplace listing, a manager's or accountant's site, or a parent group's site.

**Evidence tiers:**

| Tier | Evidence |
|---|---|
| Strong | Organisation number on the site (any format, valid checksum) in a legal footer, terms, imprint or contact page |
| Medium | Registered email domain equals the site domain; registered phone matches; Norid domain-holder match if obtained |
| Weak | Legal name, street address, postcode + place |

**Decision rule (from Istat's table):**

| Evidence | Decision |
|---|---|
| 1+ strong and 1+ weak, no contradiction | official site |
| 2+ strong | official site |
| 1+ medium and 2+ weak, no contradiction | official site |
| 3 weak (name, street, postcode/place), no contradiction | official site |
| Any contradicting identifier (another org number, another legal entity named) | not this company |
| Strong only, or fewer than 3 weak | undetermined |

**Outcome codes per company:**

- `official_site` with domain (secondary sites allowed).
- `no_site_confirmed`: the recipe was followed and no dedicated site exists (a social-only company is recorded here, with `social_profile` noted).
- `related_only`: only a parent, group or brand site exists.
- `undetermined`: conflicting or insufficient evidence. Reported as a rate and excluded from precision and recall denominators.

**Quality control:** a second annotator on a random 25% (100 companies); target Cohen's κ ≥ 0.80 on {site, no site, undetermined}; disagreements adjudicated by a third look. Every label stores the annotator, the date, the evidence tier and the page URL. `scripts/analysis/qc_annotations.py export` creates a deterministic blind worksheet; `merge` writes a new v2 annotation file with agreement, Cohen's κ, and changed-outcome counts. The current v1 held-out and validation labels are single-reviewer until that QC worksheet is completed and merged.

**Model-assisted drafts.** An assistant may draft evidence, but a human must open the page for every final `official_site` label, and the label carries `labeler`. Assistant-only labels are provisional and cannot be used as final held-out or validation gold. The 8 existing labels in `out/labels.jsonl` should be re-annotated independently and treated as development-only.

**Current v1 run.** The 52 previously undetermined pilot rows were independently searched and page-checked, then the remaining 340 rows were searched with the fixed recipe and bounded page fetching. The compiled artifact is `out/eval-sample/annotations-v1.jsonl` (240 development, 80 held-out, 80 validation), with 58 `official_site`, 32 `related_only`, 175 `no_site_confirmed`, and 135 `undetermined` rows. The old eight pilot labels remain development-only; held-out and validation rows use fresh evidence and do not reuse those labels. These are still Codex-assisted annotations, not the final human gold set: every `official_site` row should receive a human opening/check before the competition score is treated as final. Each row has source URLs and an audit block; the raw search/page evidence is retained alongside the labels.

## 4. Metrics

Computed on determined units only, at company level, per stratum and weighted to the population:

1. **Published precision** with a Wilson 95% lower bound. The headline number.
2. **Wrong-company rate per 1,000 companies** (weighted). The harm metric.
3. **Has-site recall** (published correct ÷ companies with a real site).
4. **Abstention precision**: of companies we leave unpublished and annotators determined, the share that truly have no site. Low values mean we are missing sites.
5. **Four-case confusion matrix** (right URL, wrong URL, correctly none, missed), as the WIN report recommends for combined evaluation.
6. **Undetermined rate** and **cost** (requests and search calls per company).

## 5. Rules for using the set

- Tune only on development. Held-out is evaluated once per candidate change and every look is logged. Validation is evaluated once, at the end.
- Freeze thresholds and rules before opening validation (the README's "freeze before the daily run" rule).
- Never add an annotated company to training data for a learned model if it is in held-out or validation.

## 6. Acceptance criteria

On the extended held-out plus validation sets combined (340 rows after the extension is annotated), a pipeline version is publishable when **all** hold:

- Published precision point estimate ≥ 0.95 **and** Wilson lower bound ≥ 0.90 (needs about 35 published sites with none wrong).
- Weighted wrong-company rate ≤ a ceiling you choose per 1,000 companies.
- Abstention precision and recall are reported; recall must beat the previous version before it can replace it.
- No stratum group (small AS, sized AS, non-AS) is worse than 0.85 point precision when it has 10 or more published sites.

The scorecard gate also requires at least 35 determined published companies in the selected split. If the extended held-out/validation run still has fewer than 35, report `INSUFFICIENT_PUBLISHED`, do not lower the threshold after seeing the result, and draw/annotate another pre-registered extension or leave the release unverified. Validation is opened once with `--final`; repeated validation looks require the explicit `--force-validation` override and are logged.

## 7. Effort and open decisions

- **Annotation effort:** about 400 × 6 min ≈ 40 hours for one annotator plus about 10 hours for the 25% overlap. With assistant-drafted evidence and a human verification pass, roughly 15–20 hours.
- **Decisions needed from you:** (a) the precision target and wrong-company ceiling in §6; (b) who annotates and how many hours are available; (c) whether assistant drafts are acceptable for the development split; (d) whether to start with the 60-company pilot.

## 8. Implementation status and next steps

Implemented artifacts and commands:

- `scripts/run/select_eval_sample.py`: frozen 400-row sampler plus the S4/S5/S6 extension.
- `scripts/run/make_eval_batch.py`: converts a manifest split into batch-runner input.
- `scripts/analysis/score_discovery_run.py`: annotation-aware scorecard and held-out/validation look log.
- `scripts/analysis/qc_annotations.py`: blind QC export and v2 merge.
- `out/eval-sample/manifest-extension.jsonl`, `manifest-v2.jsonl`, and the extension summary exist locally; the extension still needs annotation.
- `out/eval-sample/qc-worksheet.csv` and its JSONL mirror exist locally and need human completion.

The original development baseline remains in `out/eval-sample/development-scorecard.json` for comparison. The network-corrected v2 run is saved in `out/eval-sample/development-discovery-v2.jsonl`, with report `out/eval-sample/development-discovery-report-v2.json` and scorecard `out/eval-sample/development-scorecard-v2.json`. It completed all 240 companies with zero provider errors or company timeouts; scored against the original v1 labels it showed 65% precision and 54% recall (23 determined published sites), but 24 disagreement rows were then adjudicated by reading the live pages (`annotations-v2-adjudicated.jsonl`, model-assisted and awaiting a human spot-check), and against those labels the same run scores 96% precision (23 of 24, Wilson lower bound 80%) and 61% recall. Treat the v1 figures as superseded; current numbers live in `BRAIN.md` section 6. The remaining work is to complete QC, annotate the extension, and only then run the held-out and final validation commands.
