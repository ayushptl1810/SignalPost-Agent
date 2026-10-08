# Handoff S16: submission hardening (everything the official run can punish)

**For:** Codex session S16 · **Branch:** `feat/submission-hardening` · **Date:** 2026-10-09 · **Depends on:** main at `2807b9c` or later (S15 merged, planner fixes `9621dba`, `9749d6d`, `67c34af`).
**Read first:** `BRAIN.md` (section 0 and the "Final live results" block), `OUTPUT_CONTRACT.md`, `README.md`, `scripts/run/run_competition_batch.py`, `src/norway_company_agent/registry/batch.py`, `src/norway_company_agent/discovery/`, `scripts/connectors/rekey_nav_index.py`, `scripts/run/clean_machine_check.sh`.
**Network note:** your sandbox may have no outbound network. Build and test with fixtures; the planner runs live passes. Commits in logical chunks, conventional messages, **no co-author trailers**, no push, do not touch `config/connector-policy.json`. No new dependencies without pinning. Do not weaken the identity gate, first-party gate, different-organisation-number veto, domain-uniqueness demotion or SSRF protection.

## Why (what Builderr states, verified 2026-10-09 against `challenges/signalpost`, the playbook and the evaluation harness)

- The official batch (about 1,000 companies, chosen after a cutoff) is handed to us **at run time as a file**; we must "return exactly one terminal result envelope for every input company". "A missing row is not [a valid answer]." "Every company comes back, including the ones you found nothing for."
- "Each official run has a fixed time and resource budget" (values unpublished). "A timeout or missing result is not scored." "An entrant-caused failed or missed batch scores zero."
- A "credential tied to your own account on another service" cannot be used (they cannot reproduce it). So runtime search (`--search-fill`, Serper) and the YouTube Data API key are **not usable in the official run**; the default run must make no keyed calls.
- Clean-install check: "Clone your own repository into a new folder at the pinned commit, make an empty virtual environment, run only your declared install step, then your run command." One pasteable command. Pinned dependencies.
- "Published material claims have source, retrieval time and reporting period where relevant." "Missing values are never silently converted to zero." Fabricated financial values or a material wrong-company publication prevent an official run. Refresh must preserve prior evidence and expose material changes; re-running the same snapshot is idempotent.
- `OUTPUT_CONTRACT.md` defines the envelope as `organisation_number`, `run`, `claims[]` (field, value, availability, confidence, evidence_ids), `evidence[]` (id, source_url, source_class, retrieved_at, content_sha256, claim_span), `changes`, `errors`, `operations`.

## Task 1: never miss a row

- If an input organisation number is absent from the registry snapshot (the planner's local snapshot lacked 1 of 1,000 sampled official-universe numbers, and the current code raises `ValueError` for the whole batch), emit a terminal envelope for that input and keep the batch going. The envelope state is the closest allowed terminal failure state in `registry/batch.py` (not `complete`), every module is `not_found` or `source_error` with the note `absent from registry snapshot`, and there are no claims. Malformed or non-numeric inputs get the same treatment. Add tests with an absent number, a duplicate input, a non-numeric entry and an empty line.
- Any exception inside one company's processing (registry, financials, roles, group, locations, website, NAV) must be contained: that module becomes `source_error` with the exception class in `errors`, the other modules and the envelope survive. Add a fault-injection test per module.

## Task 2: run budget and graceful degradation

- Add a global run budget: `--run-budget-seconds` and the environment variable `SIGNALPOST_RUN_BUDGET_SECONDS` (flag wins). Default when unset: no global deadline, but the per-company budget (`--company-timeout`, default 20 s) always applies.
- When 80% of the global budget is used, stop starting new website discovery; remaining companies still get their cheap registry-only modules and a website module `failed` with the note `run budget exhausted`, so the output file always has every row. A hard stop at 95% writes the remaining envelopes immediately from the registry-only data.
- Auto-size concurrency from the machine: discovery processes default `min(8, os.cpu_count())`, worker threads default `2 * processes`, and honour `SIGNALPOST_WORKERS` if set. Report cpu count, processes, workers, wall time, requests and bytes in the run report. Keep peak memory bounded: the NAV index must be loaded once per process, not pickled per task; pass only the small per-company slice of the index (`_discover_process` currently receives the whole index).
- Process-pool start-up and a dead worker must not lose companies: if a worker process dies, re-run that company once in the parent with a short budget, otherwise `failed` for the website module only. Test with a worker that raises and one that exits.
- Keep `--resume` and the checkpoint behaviour; a re-run of the same snapshot must be byte-identical for deterministic modules (existing idempotence test) with discovery on.

## Task 3: no keyed calls by default, honest declarations

- Audit every network call in the default command path and list them in `README.md` under "APIs and sources used": Brreg open data (registry bulk supplied via `--bulk`, entity/roles/accounts/sub-unit endpoints), company websites through the safe opener, DNS. NAV is data shipped in the repo (Task 4), not a live call.
- Make sure no code path reads `SERPER_API_KEY`, `SERPAPI_KEY`, `YOUTUBE_DATA_API_KEY`, `PLACES_API_KEY` unless an explicit opt-in flag is passed; add a test that runs the default batch with those variables set to bogus values and asserts zero requests to those hosts (use a recording opener).
- Expected cost: `$0` third-party; the run report's `third_party_cost_usd` must be 0 in the default path.
- `README.md`: finish the submission text: one pasteable command (a single line using `uv run`), setup (`uv sync`), Python version, inputs (`--organisations`, `--bulk`), outputs, run report fields, model use (none), APIs/licences (Brreg NLOD, NAV feed terms, company websites under robots.txt), source-rights statement, outbound URL policy (safe opener, public IPs only, redirect validation, robots.txt, per-host rate limit), secrets policy (none required), limitations (about 10% of companies get a verified website; no search; hiring covers only NAV-listed employers).

## Task 4: ship the NAV index, honestly dated

- Commit the parent-keyed NAV index (`out/nav-full-parent.jsonl`, 8.2 MB, about 3,554 employers) as `data/nav-employer-index.jsonl` plus its `.meta.json` (built_at, since, unique ad uuids, active ads, rekeyed_to_parent), and un-ignore both in `.gitignore`. It is public-universe material and must be declared in the README. The planner will rebuild it before each submitted version.
- The runtime must **use the shipped index by default** (`--nav-index` defaults to `data/nav-employer-index.jsonl` when present). Staleness rules: drop ads whose `expires` date has passed at read time; record `index_built_at` in the evidence of every `nav_jobs` claim; do **not** treat the whole index as incomplete merely because it is older than 7 days (that rule would make a one-week-old complete index useless). If the index is older than 14 days still use it, set `stale: true` in the run report and in each claim's evidence.
- A company absent from a complete index has no active NAV ads at build time: that is `not_available` with `checked: true`, and the evidence names the index date it was checked against. It is never reported as zero postings without that date.
- Add `scripts/run/refresh_nav_index.sh`: walks the feed (existing builder), downloads the Brreg sub-unit bulk, re-keys to parents, writes the shipped file and meta. Document it. Test re-key and expiry filtering with fixtures (the re-key script already has one test).

## Task 5: envelope conforms to the published contract

The terminal envelopes the batch writes today have `run_id, organisation_number, state, started_at, completed_at, modules, profile, changes` but **not** the `claims[]` / `evidence[]` / `operations` / `errors` blocks that `OUTPUT_CONTRACT.md` describes. We do not know which shape the evaluator reads, so emit the **superset**: keep the current fields and add the contract's `run` object (run_id, started_at, completed_at, terminal_status), `claims[]`, `evidence[]`, `changes`, `errors`, `operations` (requests, runtime_ms, third_party_cost_usd), derived from the profile without changing any value.

Claims to emit (field names stable, documented in a companion note next to `OUTPUT_CONTRACT.md`): `legal_identity`, `public_brand` (website title/description when published), `latest_annual_accounts` (with reporting period, currency, each value read from the accounts record, never derived), `accounts_history` (older records), `leadership` (each role with person/organisation, role code, last changed), `registered_workplaces` (each sub-unit with address), `group_links`, `official_website`, `company_profiles` (company-owned links only), `hiring` (NAV ads, each with title, published, expires, source URL), `refresh_metadata`. Each claim has `availability` from the six allowed states, `confidence`, `evidence_ids`; each evidence has `source_url`, `source_class`, `retrieved_at`, `content_sha256`, `claim_span`. A checked source with zero items is `not_available` with `checked: true`; an unchecked source is `not_checked` or `failed`, never zero. Extend the envelope validator to check the new blocks (every claim references existing evidence ids; every available claim has at least one evidence record with the five fields).

Add `scripts/analysis/envelope_audit.py` that runs over an envelope file and prints, per claim field, the availability counts, the number of available claims lacking any required evidence field, and any financial value that is not byte-identical to the source record. The planner will run it on live output.

## Task 6: blocked and source_error triage

In the planner's live run of 999 companies the website module returned `blocked_policy` for 30 companies and `source_error` for 25. Write `scripts/analysis/website_state_report.py` that, from a profiles file, lists for every non-complete website result the candidate that drove the state and the exact reason (robots.txt disallow, policy blocklist, directory marker, resolution, TLS, timeout, 4xx/5xx, budget). Fix any case where our own heuristics produce `blocked` without a real robots or policy block: a `blocked` state must name the robots rule or the policy that blocked it, with the URL. Do not relax robots.txt handling.

## Task 7: clean-machine reproducibility

- `scripts/run/clean_machine_check.sh` must also run the batch in the fresh clone **without** the parent repository's `data/` or `out/` on any path and with an empty environment (`env -i` plus `PATH` and `HOME`), and must fail if the run reads anything outside the clone and its temp directory except the supplied inputs.
- Add a test that scans `src/` and `scripts/run/` for hard-coded `data/` paths and verifies each default exists in `git ls-files` (the directory blocklist was untracked and a clean clone silently stopped rejecting directory sites).
- Pin the Python version in `pyproject.toml` (`requires-python`) and `uv.lock`; state the version in the README. Confirm `uv sync` from a clean clone installs only what the run needs (no torch/transformers on the default path; those stay in optional extras).

## Acceptance

- Tests for every task above; `uv run --with pytest pytest -q` passes (state the count).
- The clean-machine check passes at your final commit with discovery and the shipped NAV index on a 100-company input.
- A run over a 100-company input with one absent organisation number, one duplicate and one malformed line returns one terminal envelope per valid input line and a clear error entry for the others.
- No default-path call to a keyed API; default third-party cost 0.
- The envelope audit script reports zero available claims missing required evidence fields on the 100-company output.
- No gate weakened.

## Results

- Branch: `feat/submission-hardening`, based on `main` at `5370dc9`. No push was performed and `config/connector-policy.json` was not changed.
- Full verification: `UV_CACHE_DIR=/tmp/signalpost-uv-cache uv run --with pytest pytest -q` → **283 passed, 11 subtests**.
- Offline 100-row fixture run: `out/s16-smoke/`. It emitted 100/100 envelopes, passed all envelope validation checks, loaded `data/nav-employer-index.jsonl` by default, made 0 requests, and reported `third_party_cost_usd: 0.0`.
- Envelope audit: `out/s16-smoke/envelope-audit.json` (also runnable with `scripts/analysis/envelope_audit.py`) reported `rows: 100`, zero available claims missing required evidence, zero financial value mismatches, and `passed: true`.
- Shipped NAV metadata: `data/nav-employer-index.jsonl.meta.json` records `complete: true`, `rekeyed_to_parent: true`, `parents: 3554`, `active_ads: 9926`, `unique_feed_uuids: 74621`, and `built_at: 2026-10-08T14:46:08.654917Z`.
- Commits:
  - `cb3e89c` — `feat: harden batch input handling`
  - `f0dfaab` — `fix: keep empty website evidence auditable`
  - `4a548ac` — `feat: enforce bounded discovery budgets`
  - `e05505b` — `feat: ship dated parent keyed nav index`
  - `0627bca` — `feat: add contract and website audits`
  - `fc6bc6a` — `chore: harden clean machine submission`
  - `a47c487` — `docs: record submission hardening results`
  - `40ef797` — `docs: fix handoff commit reference`
  - `3eb1195` — `docs: list final audit fix`
  - `da3ccdf` — `docs: fix sampling invocation`
- Offline limitation: the clean-machine clone installed Python 3.12 and ran the full 283-test suite, but its live G4 phase was interrupted after outbound network resolution/fetches were unavailable in the sandbox. The planner must rerun `scripts/run/clean_machine_check.sh` with live supplied inputs; the fixture batch and contract/audit checks are verified locally.

## Report back

Append `## Results` with the commit list, the branch name, the test count, the envelope audit output on the 100-company run, and anything you could not verify offline. The planner will run the live 100- and 1,000-company passes and the clean-machine check.
