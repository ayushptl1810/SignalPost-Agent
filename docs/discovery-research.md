# SignalPost external discovery research

**Date:** 2026-10-06  
**Purpose:** research-backed design for finding external company sources safely.

## Executive conclusion

Discovery is not “search the company name and use the first result.” It is:

> Given a canonical Norwegian legal entity, discover external candidates and prove which candidate represents that exact entity.

A result may instead be a parent, subsidiary, branch, brand, directory, housing association, or unrelated same-name company. The recommended architecture is:

~~~text
Registry entity
  -> deterministic candidate sources
  -> bounded search candidate generation
  -> domain normalization and fetch
  -> field-level entity verification
  -> exact / related / ambiguous / rejected / abstain
  -> evidence-backed output with provenance and history
~~~

Search is for recall. The fetched page or authoritative source must supply identity proof.

## Research-backed principles

### Separate retrieval from verification

Record-linkage research separates blocking/candidate generation, pair scoring, decision thresholds, and review. [Fellegi and Sunter](https://doi.org/10.1080/01621459.1969.10501049) formalized match, non-match, and insufficient evidence. That third outcome is essential here: not every registry entity has a public-facing website.

[Fast Record Linkage for Company Entities](https://arxiv.org/abs/1907.08667) supports company matching using name, location, industry, rules, machine learning, and blocking. [BLINK](https://aclanthology.org/2020.emnlp-main.519/) and [Ditto](https://www.vldb.org/pvldb/vol14/p50-li.pdf) support retrieving candidates first and applying expensive pairwise ranking only to the top candidates.

[IBM’s company-homepage research](https://research.ibm.com/publications/a-machine-learning-approach-to-discovering-company-home-pages) is directly applicable: candidate sites should be classified using page content, and the system should be able to conclude that no valid homepage was found.

[Microsoft’s global-constraint work](https://www.microsoft.com/en-us/research/publication/improving-entity-resolution-with-global-constraints/) supports modeling parent, subsidiary, branch, brand, and group relationships explicitly instead of flattening them into one website field.

### Use identifiers before names

The strongest Norway-specific source is the [Brønnøysundregistrene Enhetsregisteret API](https://data.brreg.no/enhetsregisteret/api/dokumentasjon/en/index.html). Start with organisation number, legal name, address, industry, status, main/sub-entity relations, and registered internet addresses.

Also check:

- raw organisation number;
- NO-prefixed form;
- number followed by MVA;
- historical names;
- registered email and phone;
- main and sub-entities.

[Altinn’s guidance](https://info.altinn.no/en/start-and-run-business/planning-starting/registration-of-the-enterprise/organisation-number) gives a useful verification rule: the organisation number is expected on the enterprise website, customer communication, and business documents. Inspect legal notices, terms, footers, contact pages, and invoices.

[Norid RDAP](https://teknisk.norid.no/en/integrere-mot-norid/) and [Norid domain lookup](https://www.norid.no/en/domeneoppslag) may link a .no domain to its subscriber organisation. Where access and privacy rules permit, that is strong ownership evidence. [Kartverket’s address API](https://www.kartverket.no/api-og-data/eiendomsdata/brukarrettleiing-adresse-api) can normalize places and addresses, but address agreement alone is not ownership proof.

### Use independent official sources as corroboration

For entities with no registry-listed website:

- [NAV job data](https://navikt.github.io/pam-stilling-feed/) can connect an organisation number to an employer, job page, application URL, or email domain.
- [Doffin procurement data](https://data.norge.no/nb/datasets/a77b0408-85f9-3e12-8a66-8d500b492e9d/kunngjoringer-av-offentlig-anskaffelser) can contain supplier IDs, legal names, contacts, and sites.
- [TED/eForms](https://docs.ted.europa.eu/api/latest/) can provide European procurement identifiers, VAT IDs, and websites.
- [European e-Justice registers](https://e-justice.europa.eu/topics/registers-business-insolvency-land/business-registers-search-company-eu_en) can help with foreign branches and parents.

These are corroboration sources, not a replacement for the Norwegian registry.

### Preserve provenance and history

[OpenCorporates](https://api.opencorporates.com/documentation/API-Reference) preserves identifiers, source URLs, publishers, retrieval times, source types, and confidence. [Dun & Bradstreet](https://docs.dnb.com/direct/2.0/en-US/company/latest/match/rest-API) demonstrates explainable matching precedence: stable registration ID, then name/address, address, telephone, postal code, domain, and email domain.

[Diffbot](https://www.diffbot.com/products/knowledge-graph) tracks entity origins, relationships, redirects, and historical identity. We should not overwrite a domain when a company rebrands or changes providers. Keep a candidate history with first seen, last seen, relationship, status, and evidence hash.

[OpenRefine reconciliation](https://openrefine.org/docs/technical-reference/reconciliation-api) and [Dedupe](https://docs.dedupe.io/en/stable/) support ranked candidates, active learning, and human review. Borderline cases need an explicit review or abstention state.

## Proposed pipeline

### 1. Canonical profile

Create a target profile from registry data:

- organisation number and variants;
- legal name and suffix-stripped name;
- historical names;
- municipality, postal code, and normalized address;
- phone, email, and email domain;
- organisation form, industry, status, and roles;
- main/sub-entity and group relationships;
- registry-listed sites.

Keep raw and normalized values.

### 2. Candidate generation

Run sources in this order:

1. registry-listed websites;
2. main/sub-entity expansion;
3. Norid domain evidence;
4. email, phone, and official documents;
5. NAV, Doffin, TED, or e-Justice where relevant;
6. search-provider fallback.

Bounded search queries:

~~~text
"LEGAL COMPANY NAME" "123456789"
"LEGAL COMPANY NAME" "MUNICIPALITY"
"LEGAL COMPANY NAME" "ORGNR"
"LEGAL COMPANY NAME" kontakt
"LEGAL COMPANY NAME" site:.no
~~~

Deduplicate by registered domain. Search snippets explain why a URL was fetched, not why it is the exact company.

### 3. Normalize and fetch safely

For each URL:

- allow only HTTP or HTTPS;
- canonicalize host, port, fragments, and tracking parameters;
- preserve the complete redirect chain;
- re-verify after cross-domain redirects;
- reject loopback, private, and link-local destinations;
- use HTTPS first;
- retain HTTP, TLS, and access failures as status evidence.

Hostname similarity, DNS, and TLS are useful score features but are not identity proof.

Fetch the homepage plus at most three or four high-value pages: about, contact, legal/privacy/terms, and possibly locations, jobs, or news. Respect the site’s robots policy; [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html) defines the robots protocol. Cap page size, requests, redirects, and total time. Cache response metadata and content hashes.

Extract:

- organisation number or VAT number;
- legal name, alternate name, URL, and same-as links;
- address and telephone;
- contact email and domain;
- footer, legal, and contact text;
- parent/group claims;
- industry, services, and locations.

[Google Organization structured data](https://developers.google.com/search/docs/appearance/structured-data/organization) and [Schema.org Organization](https://schema.org/Organization?content_language=English) define useful fields such as legalName, vatID, iso6523Code, address, telephone, url, and sameAs. Treat markup as evidence to validate, not as unquestionable truth.

### 4. Score identity with interpretable signals

Hard signals:

- exact organisation number on a first-party page;
- registry-listed website;
- Norid subscriber identity match;
- official NAV, Doffin, or TED record connecting the organisation number to the domain.

Strong corroborators:

- exact legal name;
- matching address and postal code;
- matching phone;
- matching official email domain;
- historical name match;
- explicit legal relationship.

Weak signals:

- search rank;
- hostname or brand similarity;
- matching industry;
- social profile;
- directory listing;
- map pin;
- DNS or TLS agreement.

Contradictions:

- different organisation number;
- incompatible place or country without a branch explanation;
- page identifies another legal entity;
- parent page does not identify the target;
- parked, placeholder, directory, or aggregator page;
- stale redirect with no current relationship.

Do not use name-in-hostname as a hard crawl gate. Brands and group domains are common.

### 5. Make the decision explicit

| Decision | Meaning |
|---|---|
| verified_same_entity | Hard identifier or multiple strong signals prove the legal entity. |
| verified_related_entity | Parent, subsidiary, branch, group, brand, or managed profile is credible but not exact. |
| candidate_only | Plausible but insufficient evidence to publish. |
| rejected | Contradictory or clearly unrelated. |
| no_credible_candidate | Bounded discovery completed with no publishable result. |
| abstained_low_confidence | Candidates remain ambiguous. |
| blocked | Access prevented by robots, TLS, HTTP, or policy. |
| failed | Internal/provider error; not evidence of no website. |

Only verified_same_entity should populate the official website field.

## Search provider choice

For the first search experiment, use one provider behind an abstraction. This implementation
selects Serper as the initial provider because the team already has access to it, its API
returns structured organic results, and the adapter can keep provider-specific details out
of candidate scoring and verification. Brave remains a viable later comparison provider;
its [API documentation](https://api-dashboard.search.brave.com/api-reference/web/search/post)
documents web search, country/language controls, and response metadata.

Use a provider-neutral interface:

~~~text
SearchProvider.search(query, country, language, count) -> SearchResponse
~~~

Add Serper, Tavily, Google Custom Search, or another provider only if an experiment shows enough recall gain to justify cost and policy complexity. Do not scrape DuckDuckGo or Google result pages. Common Crawl’s [CDXJ index](https://www.commoncrawl.org/cdxj-index) is useful for historical hints, not current identity proof.

### Why Serper plus a classifier

The implementation uses Serper for bounded result retrieval and keeps the provider
behind a small adapter. This is a practical first provider because it returns structured
organic results over an API, avoids browser scraping, supports a small request budget,
and can be replaced without changing candidate scoring or website verification.

The retrieval layer does not use an LLM to generate company facts. Candidate pages are
fetched independently and then passed through two decision layers:

1. A deterministic rules classifier (`rules_v1`) provides a cheap, reproducible baseline
   and catches organisation-number matches, exact names, relationship markers, and
   insufficient evidence.
2. Laya is an optional local typed-decision classifier (`--classifier laya`) for candidate
   triage. It is useful here because it returns one of a closed set of relationship
   labels with probabilities rather than free-form text. The multilingual checkpoint is
   better aligned with Norwegian pages than an English-only classifier.

Laya is preferred over a hosted Jev-style classifier for the first implementation when
   reproducibility and submission portability matter: it avoids another API key, network
   dependency, and per-call charge, and its local model can be packaged or run by the
   evaluator. The trade-offs are a larger local dependency, slower/less predictable CPU
   execution, and the need to calibrate zero-shot decisions on hard negatives. A hosted
   classifier may be easier operationally and stronger after calibration, but introduces
   provider availability, cost, and reproducibility variables. It should be an experiment,
   not the publication gate.

The classifier is deliberately not authoritative. A candidate can be classified as
`exact_entity` and still fail the final identity gate if its independently fetched page
does not contain sufficient legal-entity evidence. This protects precision and preserves
the distinction between exact entity, related entity, and plausible-but-unverified.

See [Laya's classification documentation](https://laya.tools/laya-for-classification)
and the [Apache-2.0 multilingual model card](https://huggingface.co/convaiinnovations/laya)
for the classifier behaviour and local model details.

## Triage and budget

High priority:

- customer-facing industry;
- employees or active jobs;
- phone or email;
- customer-facing address;
- brand-like name;
- known external links.

Low priority:

- holding, investment, property, foundation, association, sameie, or dormant entity;
- no employees, phone, email, or public-facing address;
- administrative sub-entity.

Suggested budget:

| Priority | Budget |
|---|---|
| High | Official checks, one exact search, one localized/brand fallback, and three to four page fetches for leading domains. |
| Medium | Official checks, one exact search, fetch only strong candidates. |
| Low | Official checks and at most one bounded search; abstain without strong evidence. |

## Evaluation experiment

Use the current 93 companies without registry websites, stratified by entity type and priority.

Compare the current one-query/hostname-gate baseline with:

1. registry checks first;
2. two bounded query variants;
3. registered-domain deduplication;
4. relaxed hostname gate;
5. homepage plus legal/contact verification;
6. explicit exact/related/abstain decisions.

Label top candidates as exact entity, parent/group, branch/sub-entity, brand, managed profile, directory, wrong same-name company, or no credible result.

Measure:

- candidate recall at 5 and 10;
- precision of accepted official websites;
- wrong-company rate;
- related-entity classification;
- abstention precision;
- evidence completeness;
- requests, latency, and search cost;
- failures separately from no-website outcomes.

The primary go/no-go metric is precision among accepted exact-entity websites.

## Implementation sequence

### Phase 1: make discovery trustworthy

1. Add triage.
2. Split discovery into query generation, provider parsing, and candidate normalization.
3. Remove hostname similarity as a hard gate.
4. Add registered-domain deduplication and redirect-chain capture.
5. Extend the identity model with legal name, org/VAT identifiers, address, phone, email domain, and page-level structured fields.
6. Discover sitemap URLs and prioritize contact, about, legal, and team pages within a bounded crawl budget.
7. Require first-party contact/domain evidence before promoting an exact-entity page as the official website.
8. Add related, ambiguous, blocked, failed, and abstained states.

The first working slice is now implemented as `scripts/run/run_search_discovery.py`:

~~~text
profile
  -> two bounded Serper queries
  -> deterministic candidate scoring and registered-domain deduplication
  -> robots/sitemap discovery plus bounded contact/legal crawl
  -> rules or optional Laya relationship classification
  -> exact-entity identity gate plus first-party contact/address gate
  -> provenance-preserving evidence record
~~~

Run the baseline with:

~~~bash
uv sync
uv run python scripts/run/run_search_discovery.py \
  --input out/smoke-profiles.jsonl \
  --output out/search-discovery-profiles.jsonl \
  --report out/search-discovery-report.json \
  --classifier rules
~~~

To evaluate Laya locally, install the optional extra first and keep the same runner:

~~~bash
uv sync --extra classifier
uv run python scripts/run/run_search_discovery.py \
  --input out/smoke-profiles.jsonl \
  --output out/search-discovery-laya.jsonl \
  --report out/search-discovery-laya-report.json \
  --classifier laya
~~~

### Phase 2: exploit Norway-specific sources

1. Registry site and main/sub-entity expansion.
2. Norid resolver, subject to access/privacy/rate constraints.
3. Address consistency.
4. NAV employer evidence.
5. Doffin/TED evidence.

### Phase 3: learn from labels

1. Add hard-negative fixtures.
2. Calibrate an interpretable logistic/GBDT or Fellegi–Sunter-style score.
3. Add active-learning review for borderline candidates.
4. Consider a cross-encoder or LLM reranker only for top candidates.

### Phase 4: refresh

1. Persist domain and relationship history.
2. Store timestamps, hashes, and source provenance.
3. Re-check strong candidates on a freshness schedule.
4. Keep superseded domains.

## Final recommendation

Build this smallest reliable loop first:

~~~text
Brreg entity
  -> registry/Norid/official-source candidates
  -> one exact search plus one bounded fallback
  -> top-domain fetch
  -> org-number/name/address/phone/email verification
  -> exact / related / abstain decision
  -> evidence record
~~~

The major discovery gain will come from increasing the number of well-verified candidate domains, not from accepting more top-ranked results.
