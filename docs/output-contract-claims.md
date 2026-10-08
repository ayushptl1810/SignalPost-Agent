# SignalPost contract claim fields

The batch envelope is a superset of [`OUTPUT_CONTRACT.md`](../OUTPUT_CONTRACT.md).
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
| `refresh_metadata` | Run identifier, retrieval metrics and declared cache marker | Runtime metadata |

Claims never derive financial values, counts, or zeros. A checked source with no
items is `not_available` and carries a checked marker in its value or evidence;
an unchecked source is `not_checked` or `failed`. Every `available` claim points
to evidence with source URL, source class, retrieval time, content hash and a
claim span.
