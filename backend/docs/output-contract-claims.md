# SignalPost contract claim fields

The default primary batch envelope is the exact starter shape described in
[`OUTPUT_CONTRACT.md`](../OUTPUT_CONTRACT.md). It deliberately has no
`claims` array. The claim fields below describe the optional `full` shape,
available through `--envelope-shape full` or the `.full.jsonl` sidecar; they are
also the internal shape used by the API adapter.
The stable `claims[].field` values are:

| Field | Meaning | Primary source |
| --- | --- | --- |
| `legal_identity` | Registry identity record, including organisation number and legal name | Brønnøysund bulk snapshot |
| `public_brand` | Published website title and description | Company-owned website |
| `latest_annual_accounts` | The latest supplied accounts record, including its reporting period and currency | Brønnøysund accounts endpoint |
| `accounts_history` | Older accounts records as returned by the source | Brønnøysund accounts endpoint |
| `leadership` | Roles with person or organisation, role code and last-changed value | Brønnøysund roles endpoint |
| `registered_workplaces` | Registered sub-units and their addresses | Brønnøysund sub-unit endpoint |
| `group_links` | Registry-reported group relationships | Brønnøysund group endpoint |
| `official_website` | Website that passed identity and first-party publication gates | Safe website opener and registry candidates |
| `company_profiles` | Company-owned social/profile links found on the published site | Published company website |
| `hiring` | Active exact-organisation NAV ads, with title, dates and source URL | Dated parent-keyed NAV index |
| `public_activity` | Up to ten dated company-owned activity items, plus separately marked NAV publication dates | Verified company pages, feeds/sitemaps, and NAV feed |
| `company_brief` | Deterministic registry statement and, when available, short company-site description parts | Brønnøysund bulk snapshot and verified company website |
| `refresh_metadata` | Run identifier, retrieval metrics and declared cache marker | Runtime metadata |

Claims never derive financial values, counts, or zeros. A checked source with no
items is `not_available` and carries a checked marker in its value or evidence;
an unchecked source is `not_checked` or `failed`. Every `available` claim points
to evidence with source URL, source class, retrieval time, content hash and a
claim span. Financial evidence rows also carry the source reporting period
whenever the claimed record exposes one.

The scored repeated fields (`social_profile`, `hiring_signal`, and
`dated_news`) publish a non-empty string in `claims[].value` and put structured
context in `value_details`. `official_website` follows the same boundary: its
available value is the final URL string matching the first evidence
`source_url`, while the complete website object is retained in `value_details`.

`hiring` may contain `postings`, `company_site_postings`, `nav_ads`, and
`sources_agree`; each posting has its own source URL and explicit posted/closing
dates when the source supplies them. `public_activity` contains `items`; each
item has a parsed ISO `date`, `url`, `title`, `claim_span`, and `source_class`.
Dates are never filled from retrieval time. `company_brief` contains `parts`
with independent source evidence and may be `ambiguous` when the site wording
does not share a deterministic stem with the registry industry label; the
registry part is retained in that case.
