# Output contract

The default primary output emits one starter-shaped JSON object per input
organisation number. Its top-level keys are exactly:

```text
run_id, organisation_number, state, started_at, completed_at, modules, profile
```

`modules` has exactly these eight keys:

```text
registry, accounting_obligation, registry_live, financials,
roles, group, locations, website
```

Each module value has exactly `state`, `retry_count`, and `final_timestamp`.
The `profile` keys are exactly:

```text
organisation_number, name, legal_form, employees, bankrupt, liquidating,
municipality, municipality_number, industry_code, industry_label, website,
latest_submitted_accounts, evidence, run_metrics
```

`profile.evidence` contains the same eight module keys. Every evidence record
has exactly:

```text
field, status, source_type, source_class, source_url, retrieved_at, value,
as_of, note, content_sha256, source_row_key, effective_at
```

The website evidence value has exactly:

```text
requested_url, final_url, registered_domain, title, description,
main_text_excerpt, social_links, structured_organisations, content_sha256,
extraction_state, pages, crawl_errors, identity_assessment,
discovered_social_links, social_link_assessments
```

Each website page has exactly `url`, `title`, `main_text_excerpt`,
`identity_text_excerpt`, and `content_sha256`. The primary starter output has no
claims, full evidence array, run, operations, changes, errors, external, or
unknown_areas blocks.

Use `--envelope-shape full` or `SIGNALPOST_ENVELOPE_SHAPE=full` when the rich
contract is required. In default starter mode the same rich envelopes are
written beside the primary output as `<stem>.full.jsonl`; the API and frontend
adapt either shape. The full contract's scored claims retain the rules below.

Allowed availability states are `available`, `not_available`, `blocked`,
`not_applicable`, `ambiguous`, `failed` and `not_checked`. A checked source
with zero jobs or zero locations is different from a source that was not
checked; use `not_checked` rather than publishing an unchecked zero.

For the full contract's scored fields `official_website`, `social_profile`,
`hiring_signal` and `dated_news`, an `available` claim has a non-empty string
`value`. The available `official_website` value is the published final URL, in
the same canonical form as the first evidence row's `source_url`; the complete
website extraction object is retained in `value_details`.
