# SignalPost public-universe cache declaration

The recall cache is an append-only, git-ignored JSONL gzip dataset keyed by
Norwegian organisation number. Each record contains the candidate source,
retrieval timestamp, response hash, identity-gate result, first-party-gate
result, evidence excerpt, company-owned outbound social links, registry-matched
contact claims, and NAV job claims. Every field family has an explicit state:
`available`, `not_available`, `ambiguous`, `blocked`, `failed`, or
`not_checked`. A cache hit is never a licence to publish an unchecked absence.

It is built by `scripts/run/build_universe_cache.py` from the frozen
`data/signalpost-company-universe-2025.official.jsonl.gz` input, or reproducibly
materialised from the local compressed Brønnøysund entity snapshot with
`--bulk`. Candidate sources are the registry website, registry email domain,
sub-unit website/email domain, exact NAV employer homepage/email domain, and
identity-gated name-derived domains. The website and first-party gates in
`src/norway_company_agent/web/` are unchanged. Restricted social platforms are
recorded only as links published by a verified company site; their content is
not scraped.

The NAV index is the public active-ad feed and its detail pages. It is complete
only when every feed page and active detail has been attempted without a cap or
circuit-breaker stop. Incremental refreshes use the feed's `since` header and
retain the completeness flag. A complete empty lookup is `not_available`; an
incomplete lookup is `failed`/`not_checked`.

At runtime the official batch reads this declared cache first, then performs
one safe-opener homepage re-verification with the same identity and first-party
gates. Cache misses use the existing live registry path. Search is calibration
only and is not required for a run.

Refresh policy: run the bounded pilot first, inspect the 100-row published-site
audit, then schedule a full rebuild or incremental refresh. `built_at`, source
retrieval times, response hashes, and the manifest are retained for replay and
material-change detection. The cache cutoff is the source retrieval time in
the record; it must not be represented as current data without re-verification.

The cache and raw search calibration responses are local artefacts under
`cache/` and `out/`; they are not committed or submitted. The pilot report
records wall time, requests, bytes, source yields, failure/block rates, and a
full-run projection. No paid API is used by the builder.
