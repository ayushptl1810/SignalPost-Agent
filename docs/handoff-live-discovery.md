# Handoff S15: live website discovery in the official command, gate G4 as default, honest states

**For:** Codex session S15 · **Branch:** `feat/live-discovery` · **Date:** 2026-10-09 · **Depends on:** S12 and S13 (merged), the planner's fixes `8b9ea51` and `ee7e339`.
**Read first:** `BRAIN.md` (sections 0, 8, 10 and the "Live results on the official universe" block), `docs/handoff-recall-g4.md` (S13 results), `scripts/run/run_competition_batch.py`, `scripts/run/build_universe_cache.py` (`process_record`, `candidate_domains`, the G4 path), `src/norway_company_agent/registry/batch.py`.
**Network note:** your sandbox may have no outbound network. Build and test offline with fixtures; the planner runs live passes. Commits in logical chunks, no co-author trailers, no push, do not touch `config/connector-policy.json`. Do **not** build a universe-wide precompute: the owner decided against it.

## Why

The official run is **one command on Builderr's batch of about 1,000 unseen companies**. The planner's 100-company smoke through that command (`out/smoke-official/`) passes the contract in 78 s, but its website module only checks the registry-listed website (93 of 100 `not_found`). The candidate generation, DNS pre-resolution and gates that find sites for 9.4% (G3) to 11.2% (G4) of companies live only in the cache builder. They must run **inside the official command**, live, with bounded time. Measured on the official universe (1,000 random companies, 64 workers): 446 s, 6,361 requests, 333 MB, about 0.45 s per company; the work is CPU-bound HTML parsing.

## Task 1: live discovery inside `run_competition_batch.py`

- Reuse the cache builder's candidate generation, DNS-first resolution, fetch, identity gate, first-party gate and G4 (do not copy code: move the shared logic into `src/norway_company_agent/discovery/` and have both scripts call it). Registry address, phone and e-mail must be on the profile (the batch already reads the Brreg bulk file; the universe file does not carry them).
- Run discovery for every input company, concurrently, with per-company and per-run time budgets (`--company-timeout` default 20 s, `--run-budget-seconds` optional). A company that exceeds its budget returns `failed` for the website module only; the other modules and the envelope stay `complete`. Never let one slow host delay others: connect timeout 5 s, total fetch 12 s.
- Use a process pool for the CPU-bound parsing (default `min(8, cpu_count)` processes, each with a thread pool for network waits) so 1,000 companies finish in about 10 to 20 minutes on a laptop. Report achieved seconds per company and total runtime in the run report.
- Keep `--resume`, the checkpoint, idempotence on the same snapshot, the previous-profiles input and the material-change output working. Add a `--discovery off|g3|g4` flag, **default g4**.
- If a cache directory is given (`--cache`), keep using it first; absence of a cache is the normal case.
- NAV: if a complete, fresh NAV index file is passed (`--nav-index`), use it for hiring claims and as a homepage candidate; otherwise hiring is `not_checked`, never zero.

## Task 2: G4 hardening (precision)

1. **Domain uniqueness:** after discovery for the whole input batch (and, when a cache is used, against the cache), any registered domain published as the official website of two or more different organisation numbers is demoted to `related_only` for all of them, unless the page shows exactly one organisation number matching. Test with two synthetic companies sharing a domain.
2. **Chain/brand pages:** the planner found `Mathisen VVS AS -> rorkjop.no` (a plumbing chain member page). Add detection for franchise/chain/member-directory pages (page names several different legal entities or addresses; path under `/butikker`, `/finn-forhandler`, `/avdelinger`, `/medlemmer`; domain whose registry rows differ in name from the legal name by more than a generic tail). Demote to `related_only`. Add the rorkjop case as a fixture.
3. **Gap review:** the planner's looser rule found 41 strong unpublished candidates in 1,000 companies where G4 published 18 (`out/sample1000b-report.json`, cache `cache/sample1000b/`). Write `scripts/analysis/g4_gap_report.py` that lists, for each strong-looking candidate G4 refused, which condition blocked it (so the planner can decide which conditions to relax). Do not relax any rule in this task.

## Task 3: honest states

The planner measured 47% of cache records with website state `failed`, almost all unresolved guessed domains or pages that were uncertain. Define and test:
- `available`: a candidate passed the gate (published).
- `ambiguous`: at least one candidate loaded and carried name/address/registry evidence but did not pass the gate.
- `not_available`: every candidate failed DNS resolution, returned 404/410, or loaded without any entity evidence.
- `blocked`: robots or policy blocked every otherwise promising candidate.
- `failed`: a candidate that resolved failed with a timeout, TLS error or 5xx on the **only** promising candidate(s).
Document the mapping in a short note next to `OUTPUT_CONTRACT.md` and make the recall report count each state.

## Task 4: sharding for ad-hoc larger runs

Keep `--shard-size/--shard-index` in the cache builder and make the batch runner accept `--shard-index/--shard-count` so a large local test can use several processes. No universe-wide run is planned.

## Task 5: submission hygiene (small)

- `README.md`: replace the starter text with the run command, inputs/outputs, declared APIs (Brreg open data, NAV public feed, YouTube Data API v3 only when a key is present), no model calls, expected cost per 100 companies ($0 default; Serper only with `--search-fill`), caches, secrets via environment variables, outbound URL policy (safe opener, robots.txt, per-host rate limit), and a one-paragraph limitation statement.
- A script `scripts/run/clean_machine_check.sh` that clones the repo at a given commit into a temp directory, runs `uv sync`, the unit tests, the 100-company smoke and the validator, and prints pass/fail. Document it in the README.

## Acceptance

- Tests for: shared discovery module, live discovery in the batch (fixture-based, offline), time-budget containment (a slow host does not delay others and does not fail the envelope), domain uniqueness, chain-page demotion (rorkjop fixture), state mapping, resume/idempotence with discovery on. `uv run --with pytest pytest -q` passes (state the count).
- With discovery on, the 100-company smoke validator still passes and every envelope is terminal.
- No gate weakened; identity gate, first-party gate, different-organisation-number veto and SSRF protection unchanged.

## Report back

Append `## Results` with the commit list, the branch name, the test count and anything you could not verify offline. The planner will run the live 100 and 1,000 passes.

## Results

- Branch: `feat/live-discovery` (not pushed; no full-universe precompute started).
- Shared discovery is now in `src/norway_company_agent/discovery/`; both the
  cache builder and official batch call the same candidate, DNS-first, safe
  opener, identity, first-party and G4 path. The batch defaults to
  `--discovery g4`, uses a bounded process pool, per-company/run budgets,
  cache-first re-verification, fresh-complete NAV input, resume/checkpoints,
  material changes and `--shard-index/--shard-count`.
- G4 precision controls include whole-input and cache-aware registered-domain
  uniqueness, related-only demotion, chain/member-directory detection for
  paths such as `/finn-forhandler`, and the `Mathisen VVS AS -> rorkjop.no`
  fixture. `scripts/analysis/g4_gap_report.py` reports the conditions blocking
  strong unpublished candidates. State semantics are documented in
  `docs/output-contract-states.md`.
- Offline verification: `uv run --with pytest pytest -q` -> **272 passed, 11
  warnings, 11 subtests**. The targeted live-discovery fixtures passed 19
  tests. Compile and diff checks also passed.
- Live 100-company smoke yield, p95 network timing, and the 1,000-company
  runtime/request/disk measurements were **not run**: this session had no
  outbound network. The planner must run them, including the required network
  preflight, on the official-shaped input.
- Commits on this branch:
  - `89a8bf1` — `refactor: share live discovery engine`
  - `e0b5bca` — `feat: run bounded discovery in competition batch`
  - `df46cc0` — `feat: add g4 gap and state tooling`
  - `3482d0c` — `docs: refresh live discovery runbook`
  - `523eb57` — `fix: harden g4 and discovery states`
  - The final BRAIN/handoff report is the next documentation commit after this
    entry is staged; no generated `data/` or `out/` files were included.
