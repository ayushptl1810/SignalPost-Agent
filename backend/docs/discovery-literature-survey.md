# Finding the exact official website of a registered company: survey and recommendations

**Date:** 2026-10-07 · **Sources read in full:** 9 · **Searches:** 30+ · **Confidence:** high for the official-statistics findings (primary documents read), medium for commercial products (vendor docs), low for competitions (no public winner write-ups exist for this task).

## Executive summary

1. **This task already has a name and a decade of prior art.** National statistics offices call it *URL finding*: given a business-register unit, find its website. Statistics Netherlands, Istat, Hesse, Statistics Austria and others built and benchmarked it in the ESSnet Big Data and Web Intelligence Network (WIN) projects. Our architecture (registry signals, search, crawl, feature extraction, classifier) is the same shape as theirs.
2. **Measured state of the art is about 83–90% accuracy,** not 99%. Five countries scored 82.7–89.6% against 500 manually annotated companies each. Statistics Netherlands reached F1 0.84 using only search-result features. These are accuracy numbers over "right URL or right no-URL", so they are not directly comparable to our precision-first gate, but they show what mature systems achieve.
3. **Search recall is the ceiling.** The correct domain appeared in Google results for only about 72% of Dutch enterprises with the best query, and 87–91.5% in Hesse. Statistics Netherlands sends six query variants per company because one is not enough.
4. **Most small companies have no website, and mature systems model that explicitly.** Statistics Netherlands could attach no URL to about 3.04M of 4.63M legal units (66%). In the 10+ employee annotation sample, 74.4% had a URL. Our batch is much smaller firms, so expect lower yield than any published figure.
5. **AI search engines are not an accuracy-first substitute.** Istat compared its ML pipeline with AI-search setups: accuracy 0.896 vs 0.37–0.78, false positives 3.2% vs 10.6–20.2%. Agreement of four to five AI engines settled only 43% of cases.
6. **The deterministic rule we use has a published, defensible counterpart.** Istat's annotation protocol accepts a site as correct on "1+ strong and 1+ weak" evidence, or "3 weak" (name, address parts) with no contradictions. By that standard `arkjv.no` (name + street + postcode + municipality + email domain) is correct, which supports relaxing our first-party gate.
7. **Norway has an unused high-precision source:** Norid's public lookup resolves an organisation number to the domains the organisation holds. Automation terms are unknown and need to be confirmed with Norid.

## 1. Prior art in official statistics

| Source | What it did | Result |
|---|---|---|
| [WIN URL-finding methodology report](https://cros.ec.europa.eu/system/files/2023-12/20220131_url_finding_methodology.pdf) | Consolidated pipelines from four countries: search, scrape, extract, classify | Search-only features (CBS) F1 0.84; website-level F1 0.59–0.82 elsewhere (logistic regression, gradient boosting, SVM) |
| [WIN OBEC annotation exercise](https://win2025.stat.gov.pl/Content/Presentations/I.1.%20Heidi%20Kuehnemann.pdf) (Kühnemann, Hesse) | 500 manually annotated companies per country | Accuracy: Austria 87.4%, Bulgaria 83.6%, Hesse 87.8%, Italy 89.6%, Lithuania 82.7% |
| [Istat comparison](https://win2025.stat.gov.pl/Content/Presentations/I.2.%20Donato%20Summa.pdf) (Summa) | ML pipeline vs six AI-search setups, 500 companies | Pipeline accuracy 0.896; AI setups 0.37–0.78 (column labels are not legible in the extracted slides, so I report the range) |
| [Statistics Netherlands](https://win2025.stat.gov.pl/Content/Presentations/III.2.%20Arnout%20van%20Delden.pdf) (van Delden) | Links a third-party scraper's URLs to the register with a logistic regression over agreement variables | 66% of legal units end up with no URL from any source; fitted model beat an expert-weighted formula |
| [CBS discussion paper](https://www.cbs.nl/en-gb/background/2020/01/searching-for-business-websites) | Six query variants per company, search-result features only | F1 0.84 (as cited in the WIN report; I could not open the paper's PDF) |
| [SNStatComp/urlfinding](https://github.com/SNStatComp/urlfinding) | Open-source Python tool: train, test, predict; six queries per record | No published metrics in the repo |

**Population caveat.** The WIN samples are enterprises with 10 or more employees. Our batch has many tiny and dormant entities, so the share with a website, and therefore the yield, will be lower.

## 2. What the methodology report prescribes

From the [WIN report](https://cros.ec.europa.eu/system/files/2023-12/20220131_url_finding_methodology.pdf):

- **Training and test data.** Build it manually: search each enterprise, visit results, compare with the register. Better than labeling the finder's own output, which depends on the search engine used. If manual work is infeasible, use deterministic links (a VAT or register ID on the page) as training data, accepting the bias.
- **Sampling.** Draw a random sample, stratified by activity. If the population has many micro-firms, *oversample the larger ones*, because small firms often have no URL and a sample dominated by them has too few positives. Check sample size by bootstrapping the test set and watching how much the score moves.
- **Search terms.** Best CBS query: legal/trade name + postal code + "contact" + exclusion operators (correct domain in results for 72.2%). Municipality name works nearly as well and is safer where postcodes collide with other numbers. Istat improved results by adding the VAT ID.
- **Search engine.** Google was best: 74.8% (Italy) and 89% (Hesse) of domains matched, against 57.6–64.7% for Bing, Yahoo and DuckDuckGo. The Google API was a little worse than normal Google search (66.7% and 87%).
- **Blocklist exclusion in the query** raised Hesse matches from 83% to 87% (name only) and from 87% to 89% (name + municipality).
- **Features.** Agreement between register data and page text (name, address, IDs, phone, email) using regular expressions that tolerate spelling variation, string similarity (Jaro-Winkler, Levenshtein) between enterprise name and page title, and search position (used with caution). Aggregate to domain level, usually with the maximum.
- **Registrar data.** If the URL is among the domains the enterprise holds in the national registry, that is a strong indicator (Finland's registry API returns domains by business ID).
- **Model structure.** Three options: classify only found URLs; one combined model over the four cases (right URL, wrong URL, correctly no URL, missed URL); or two models, one predicting "has a website" and one "is this URL the right one", each with its own features.
- **Evaluation.** Use an independent test set and cross-validation. Report at legal-unit level, using F1/precision/recall for the binary variants and a four-case confusion matrix for the combined model.
- **Blocklists.** Directories, municipalities, e-commerce platforms and social media contain all the enterprise's data but are not its website. The lists grow to hundreds of domains. Frequent domains can still be real shared sites, so the blocklist needs review, and a detection model is suggested as future work.

## 3. AI search engines and LLMs

- Istat found a task-specific ML pipeline more accurate (0.896) than every AI-search setup (0.37–0.78), with a much lower false-positive rate (3.2% vs 10.6–20.2%). The pipeline's claimed positives were 98% real, against 56–96% for the AI setups. Their practical suggestion: fully automate the roughly 43% of records where four to five AI engines agree and keep the existing system for the rest. Local small-model agents were "currently not up for the task" ([Summa, 2025](https://win2025.stat.gov.pl/Content/Presentations/I.2.%20Donato%20Summa.pdf)).
- For *pairwise matching* (given two descriptions, are they the same entity), the best LLMs match fine-tuned models trained on thousands of examples with zero or a few examples, and are more robust to unseen entities, but the prompt needs tuning per model and dataset ([Peeters, Steiner, Bizer](https://arxiv.org/abs/2310.11244)). This supports using an LLM as a *judge on a fetched page and registry record*, not as a source of URLs.

## 4. Commercial products

| Product | What it does | What we can take |
|---|---|---|
| [People Data Labs](https://docs.peopledatalabs.com/docs/output-response-company-enrichment-api) | Company enrichment returns a logarithmic 1–10 `likelihood`; callers set `min_likelihood`. A matching website, ticker or street address raises confidence. | Publish a calibrated probability, not only a boolean. Additional matching inputs raise confidence. |
| [Clearbit Name to Domain](https://help.clearbit.com/hc/en-us/articles/8502992633111-Autocomplete-Name-to-Domain-and-Risk-API-FAQ) | Free API, now legacy and unsupported. Vendor states results are automated and "sometimes outlier inaccurate", and that company names are not unique. | Name-only matching is known to be unreliable, even for a vendor. |
| D&B ([matching basics](https://www.dnb.co.uk/content/dam/english/dnb-data-insight/DNB_The_Basics_On_Data_Matching.pdf)) | Confidence code plus a match-grade string showing how each field agreed; risk-averse use cases demand a higher score; email domain is one matching attribute. (From search snippets; I could not open the document.) | Per-field agreement grades are a good audit format for evidence. |
| [Splink](https://dataingovernment.blog.gov.uk/2022/09/23/splink-fast-accurate-and-scalable-record-linkage/) (UK Ministry of Justice) | Open-source Fellegi–Sunter linkage with blocking and partial match weights; a Companies House matching service compares name, website domain, registered address and social handles together ([thedatacity](https://docs.thedatacity.com/our-data/third-party-data/matching-third-party-data)). | A principled way to combine weak signals once labels exist. |

No commercial source I found publishes precision for exact legal-entity website matching.

## 5. Information-retrieval and entity-resolution literature

- **TREC homepage finding.** For queries naming a site, URL characteristics (type, length) and inbound anchor text were the most useful signals; URL features helped far less for non-homepage targets ([TREC 2002 overview](https://pages.nist.gov/trec-browser/trec11/web/overview)). Practical reading: prefer short root-domain URLs over deep pages, and penalise subpages.
- **Fellegi–Sunter** formalises match / non-match / insufficient evidence (cited in `discovery-research.md`). Splink implements it and trains without labels.
- **Firm websites in economics.** [Kinne & Axenbeck](https://madoc.bib.uni-mannheim.de/46518) built a large-scale scraper for German firm websites. The search results did not give URL-matching accuracy figures.

## 6. Competitions and shared tasks

- **Builderr / Signalpost challenge:** nothing public found, no winners or leaderboard. Insufficient data.
- **The closest thing to a shared task is the WIN OBEC annotation exercise** (above), which gives a protocol, an evaluation script and per-country baselines. Istat's annotation rule:

  | Evidence | Website correct? |
  |---|---|
  | 1+ strong (VAT/tax ID) and 1+ weak (name, address parts) | yes |
  | 2 or 3 strong | yes |
  | 1 strong, medium (trade-register ID) and 1+ weak | yes |
  | 3 weak, no contradictions | yes |
  | 3 weak but a contradicting strong ID | no |
  | 1 strong only | no |
  | 1+ strong and 2+ weak, but a strong/medium contradiction | unclear, check the register |

- **FEIII** (financial entity identification): winners used *simple local heuristics*, thresholded block purging and Jaccard matching ([ISI paper](https://www.isi.edu/results/publications/14922/local-domain-independent-heuristics-for-the-feiii-challenge-lessons-and-observations/)). Lesson: simple, tuned, thresholded rules are hard to beat, which supports keeping our gate interpretable.

## 7. Gap analysis against our pipeline

| Established practice | Our state | Gap |
|---|---|---|
| Manually annotated, stratified test set, 500 units | 8 labels (mine), 2 published sites | Large |
| Evaluate "no website" explicitly (four-case matrix) | Scorecard has outcomes but no labeled true negatives | Medium |
| 6 query variants, Google, blocklist exclusion in query | 2 queries, Serper (Google results), blocklist applied after retrieval | Medium |
| Learned classifier over agreement features | Hand-set additive scores and boolean gates | Medium |
| Search-result-only features as a cheap first filter | Every candidate is fully crawled (about 6 requests) | Medium |
| Registrar domain lookup by business ID | Not used | Large potential |
| JS rendering for thin pages | Detected (`js_fallback_candidate`) but not systematically rendered | Small to medium |
| Per-stratum reporting (NACE, size) | Not reported | Small |

## 8. Recommendations, ranked by expected gain per effort

1. **Build the evaluation set the WIN way before changing the gate.** 300–500 companies sampled at random, stratified by activity and oversampling the larger firms so there are enough sites. Annotate by manual search (not by labeling our own output), using Istat's strong/medium/weak table as the labeling rule, and record "no website" explicitly. Check size by bootstrapping. *Gain: everything else becomes measurable.*
2. **Adopt Istat's evidence tiers as the first-party gate and measure.** `arkjv.no` meets the "3 weak, no contradiction" row (name, street, postcode, municipality) plus an email-domain match. Test the relaxation on the labeled set and keep the contradiction veto. *Gain: recovers real misses without a new model.*
3. **Add query variants and put the blocklist in the query.** Add a "name + postcode + kontakt" variant and exclusion operators for blocklisted domains, so the ten results are not spent on directories. WIN measured +2 to +4 points from exclusions and +4 from adding the municipality. *Gain: raises the recall ceiling.*
4. **Ask Norid about automated lookup by organisation number.** A public lookup resolves org numbers to the domains an organisation holds; Finland's equivalent was treated as a strong feature. Terms for automated use are not stated on the pages I read. *Gain: potentially the highest-precision candidate source in Norway.*
5. **Two-stage model once there are labels.** Stage one predicts "has a website" (features: NAV hit, employees, email present, legal form, number of search results). Stage two judges the URL (our agreement signals). Logistic regression is enough: WIN's logistic regression, gradient boosting and SVM landed within a few points of each other. Keep hard vetoes (directory, shared domain, contradicting org number) outside the model.
6. **Cheap pre-filter from search results.** Title and snippet similarity, domain frequency and position, as CBS did (F1 0.84 with no scraping), to avoid crawling obviously wrong candidates.
7. **Render JavaScript only when a page is thin.** In Hesse's comparison, Playwright recovered 88.7% of annotated URLs vs 85.8% for a plain request, and the poorest headless option fell to 64.6%. A fallback, not a default.
8. **Use AI search as a candidate source or tie-breaker, never as the gate.** Their false-positive rate is 10–20%.
9. **Report per-stratum metrics** (NACE, size band, legal form) so regressions in one segment are visible.

## 9. Gaps in this survey

- No competition winner write-ups found for company-website matching, including the Builderr challenge.
- I could not open the CBS discussion paper PDF, so its model details are second-hand from the WIN report.
- Istat's AI-search columns are unlabeled in the extracted slides; only the range is reported.
- D&B details come from search snippets only.
- Norid's rules on automated queries were not stated on the pages I read.
- All WIN accuracy figures are over 10+ employee firms and are not comparable with our precision-gated, tiny-firm population.

## Sources

1. [WIN URL-finding methodology report](https://cros.ec.europa.eu/system/files/2023-12/20220131_url_finding_methodology.pdf)
2. [Using web-scraped data to enhance the statistical business register (slides)](https://cros.ec.europa.eu/system/files/2023-12/Using_Web_Scrapped_Data_to_Enhance_the_Quality_of_the_statistical_business_register_slidedeck.pdf)
3. [van Delden et al., Use of dedicated business websites (WIN 2025)](https://win2025.stat.gov.pl/Content/Presentations/III.2.%20Arnout%20van%20Delden.pdf)
4. [Summa, Identifying official firm websites: ML vs AI search engines (WIN 2025)](https://win2025.stat.gov.pl/Content/Presentations/I.2.%20Donato%20Summa.pdf)
5. [Kühnemann, URL finding: looking back and ahead (WIN 2025)](https://win2025.stat.gov.pl/Content/Presentations/I.1.%20Heidi%20Kuehnemann.pdf)
6. [CBS, Searching for business websites](https://www.cbs.nl/en-gb/background/2020/01/searching-for-business-websites)
7. [SNStatComp/urlfinding](https://github.com/SNStatComp/urlfinding)
8. [People Data Labs company enrichment output](https://docs.peopledatalabs.com/docs/output-response-company-enrichment-api)
9. [Clearbit Name to Domain FAQ](https://help.clearbit.com/hc/en-us/articles/8502992633111-Autocomplete-Name-to-Domain-and-Risk-API-FAQ)
10. [D&B, The basics of data matching](https://www.dnb.co.uk/content/dam/english/dnb-data-insight/DNB_The_Basics_On_Data_Matching.pdf)
11. [Splink, UK Government Data blog](https://dataingovernment.blog.gov.uk/2022/09/23/splink-fast-accurate-and-scalable-record-linkage/)
12. [The Data City, matching third-party data](https://docs.thedatacity.com/our-data/third-party-data/matching-third-party-data)
13. [Peeters, Steiner, Bizer, Entity matching using LLMs](https://arxiv.org/abs/2310.11244)
14. [TREC 2002 web track overview](https://pages.nist.gov/trec-browser/trec11/web/overview)
15. [FEIII local heuristics](https://www.isi.edu/results/publications/14922/local-domain-independent-heuristics-for-the-feiii-challenge-lessons-and-observations/)
16. [Kinne & Axenbeck, Web mining of firm websites](https://madoc.bib.uni-mannheim.de/46518)
17. [Norid lookup service](https://norid.no/en/domeneoppslag/oppslagstjeneste) and [privacy page](https://norid.no/en/domeneoppslag/personvern)
18. [NAV job-vacancy feed](https://navikt.github.io/pam-stilling-feed/)
