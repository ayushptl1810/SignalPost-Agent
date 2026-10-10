# Spec S28: close the gap to Builderr's reference crawler

Read `BRAIN.md` first. All paths here are relative to `backend/`. Branch:
`feat/reference-gap` from `main`. Baseline: `uv run --with pytest pytest -q`
→ 398 passed, 84 subtests.

## Why

We ran `main` on Builderr's own published 100-company sample
(`out/ref100/builderr-sample100.json`, their crawler's output; our run is in
`out/ref100/profiles.jsonl` and `out/ref100/envelopes.jsonl`). Both files are on
disk and readable offline.

| Family | Builderr | Us | Overlap |
|---|---|---|---|
| Official website (registered domain) | 97 | 70 | 68 |
| Social handles | 103 | 81 | 75 |

Why we miss 27 of their websites:

| Group | Count | Cases (org number) | Root cause |
|---|---|---|---|
| A | 10 | Atea 920237126, Nasjonal Jazzscene 990167826, Work System 913170296, Sildajazzen 869938432, Kitchn 919694580, Vestbo 979214502 + 933064883, Husflid Holmestrand 931932217 (`norskflid.no/holmestrand`), Nordic Supply Partner 970601902 | The registry itself declares the website, but our brand, chain or identity vetoes block it. |
| B | 3–4 | RSV Gruppen 982215668 | The registry email domain `renholdvest.no` hangs for the full registry tier (25 s). Tiers run one after the other, so the correct guessed domain `rsvgruppen.no` (0.8 s) is never evaluated in time. |
| C | 4 | Storebrand 953299216, Netsolution 985628572, Mosa Designlab 991449175, Sjø-Sport 880376012 | Guessed (name-derived) domains time out at 6 s under load. |
| D | 2 | Takstolfabrikken 929034597 (exact org number on `takstolfabrikken.com`, identity 1.0, still rejected), Atea (`legal_name_all_tokens` false although identity is 0.95) | Gate bugs. |
| E | 7 | barnehage.no, dolly.no (different org number), odd domains | Out of scope. Our rejections there are right. |

Precision risks are reported by competitors who received Builderr's per-family
feedback (not confirmed by Builderr):
- The scorer appears to read **one fact per claim**. List-valued claims may not
  be counted.
- News evidence should be the **article page**, not the listing page.
- Builderr matches on these keys: website = registered domain, social =
  `facebook.com/handle`, hiring = careers URL, news = `title (ISO datetime with offset)`.
- "Each scored entry ran twice", so refresh correctness and the false-change
  rate are probably scored.

## Hard rules (unchanged)

- Never loosen these: the different-org-number veto (`contradicted`), the
  directory/blocklist vetoes, SSRF, robots, the 48-fetch cap, the DNS semaphore
  and `config/connector-policy.json`.
- No model calls, no keyed APIs. Your sandbox has no network: prove everything
  with offline fixtures and claim no live numbers.
- Do not tune thresholds by looking at more of Builderr's sample than the cases
  named above. The sample is our measuring stick; the planner's random
  999-company run is the check.
- One commit per task, `<type>: <description>`, no co-author lines. No tag,
  push or submission.

## Task 0: reference comparison script

Add `scripts/analysis/reference_compare.py
--reference out/ref100/builderr-sample100.json --profiles <profiles.jsonl>
[--output report.json]`.

- **Websites:** compare registered domains of `web.value.final_url` (reference)
  and `evidence.website.value.final_url` (ours). Print matched,
  reference-only and ours-only counts, plus a per-org list of misses with our
  discovery state and each candidate's source/state/failure kind/first-party
  status.
- **Social:** compare `external.handles[].url` with our
  `website.value.social_links[].url`, lowercased.
- Fixture test with a 3-row reference and 3-row profile file.

## Task 1: fetch all candidate tiers at the same time

`discovery/engine.py` `process_record` fetches the registry-linked tier, then
the name-derived tier. A dead strong candidate burns the whole company budget.

- Submit both tiers at once, so registry-linked and name-derived fetches run
  together. Concurrency per company: at most 6 fetches, plus the existing
  per-host policy and the global caps.
- **Evaluate results in the existing strength order.** A weaker candidate may
  publish only once every stronger candidate has finished, or has failed or
  timed out. Keep the "first publishable in strength order wins" semantics
  exactly.
- Once a candidate publishes and all stronger ones are resolved, cancel the
  remaining futures.
- Timeouts: registry-linked 15 s per request (unchanged); name-derived
  6 s → **10 s** per request. The company budget stays 30 s.
- Fixtures:
  - a hanging registry email domain plus a fast valid name-derived domain:
    the name-derived domain is published within the company budget;
  - a slow but valid registry website plus a fast valid name-derived domain:
    the registry website wins;
  - all candidates dead: honest `failed`/`not_available`, not a hang.

## Task 2: registry-declared website rule

New G4 rule `g4_registry_declared` in `web/first_party.py`, after the
existing rules. It applies when **all** of these hold:

- `candidate_source == "registry_website"`;
- the registry's own `hjemmeside` value normalises to this candidate (same
  registered domain; a declared path such as `norskflid.no/holmestrand` is
  kept and published as the declared URL);
- the page was fetched with 2xx, and its final registered domain equals the
  declared one, or a same-site redirect leads there. A cross-domain redirect
  is allowed only if the final page carries the company's org number, or
  every legal-name token, or the declared domain's label;
- not `contradicted`, not `directory_marker`/`listing_path_marker`/
  `blocked_host`, not a parked/for-sale page (add a small detector:
  "domain is for sale", "kjøp dette domenet", common parking hosts), and
  fewer than 3 other org numbers on the page;
- at least one corroboration: the org number on the page; **or** a
  distinctive legal-name token (not in the generic-word list) in the
  title, og:site_name, JSON-LD name or visible text; **or** the registry email
  domain equals the site's domain; **or** the registry phone or postcode
  appears on the page.

This rule overrides `related_entity` / `group_or_brand` /
`chain_or_member_directory_page` / `group_franchise_or_cooperative_marker`
**only** for this registry-declared candidate. It never overrides those
vetoes for name-derived, email-derived or NAV-derived candidates.

Domain uniqueness: a domain claimed by several organisations stays demoted,
**except** when every organisation keeping it publishes it through
`g4_registry_declared`. In that case all keep it and the report counts
`shared_registry_declared_domains`. This is the Vestbo case.

Fixtures: Work System and Kitchn-style brand pages (published), Norsk Flid
member path (published with its path), a parked declared domain (not
published), a declared site showing a different org number (vetoed), a
declared site cross-redirecting to an unrelated domain with no corroboration
(not published), the same brand page reached as a name-derived candidate (not
published), and Vestbo shared by two orgs that both declare it (both published).
Export every `g4_registry_declared` row in `export_g4_audit.py` for the
planner's hand audit.

## Task 3: gate bugs

1. **Exact org number:** a checksum-valid organisation number that equals the
   company's, found in a legal/contact/footer/JSON-LD position (identity
   score 1.0), publishes through a new rule `g4_exact_org_number`. It needs
   the `safe` conditions, not `contradicted`, and fewer than 3 other org
   numbers on the page. It applies to any candidate source. This is the
   Takstolfabrikken case.
2. **`legal_name_all_tokens`:** in `assess_g4_ownership`, build the token
   set from the same evidence the identity gate uses (title, og:site_name,
   JSON-LD names, visible text), not only `_page_text`. Find out why it is
   false for a `ATEA ASA` page with title "Atea" and add that fixture.

## Task 4: one fact per claim

Keep all existing claims and the kit `profile` block unchanged, for backward
compatibility. **Add** repeated claims, one per fact:

- `social_profile`: value is the canonical URL
  (`https://facebook.com/<handle>`). Add `match_key` = host + path, lowercase,
  no scheme or www. Its own evidence: source_url = the verified page that
  links the profile, claim_span = the anchor's visible text or the href,
  extraction_method `html_link` or `jsonld_sameas`.
- `hiring_signal`: one claim per careers URL (value = careers URL) and one per
  posting (value = posting URL, title in `value_details`). Evidence
  source_url = that page.
- `dated_news`: value = `"<title> (<published_at>)"`, with `title`,
  `published_at` and `url` in `value_details`. Evidence source_url = **the
  article URL**, `discovered_via` = the feed or listing URL. Fetch the article
  pages of at most the 3 newest recent items, within the existing site-fact
  time budget (6 s each, 2 extra requests budgeted beyond the current 6).
  Only article-fetched items whose title and date match become `dated_news`
  claims, with claim_span = the title as found in the article and
  content_sha256 = the article hash. Unverified items stay only in the legacy
  `public_activity` list.

The validator and `envelope_audit.py` must accept repeated fields and check
that each one has its own evidence.

## Task 5: refresh without false changes

`core/refresh.py` `diff_profile`:

- Do not emit a change when the current evidence for that field is
  `failed`, `source_error`, `blocked` or `not_checked` (a transient failure
  is not a removal). Count these as `suppressed_transient` in the report.
- Compare lists as sets of normalised keys (social `match_key`, posting URL,
  news `url`+`published_at`), so order or cosmetic differences are not
  changes.
- Fixtures: a run-2 website timeout gives no change; a reordered social list
  gives no change; a genuinely removed posting gives one change.

## Report

Add an "S28 offline result" paragraph to `BRAIN.md` section 0, in the style of
the S20–S27 paragraphs. Run Task 0's script on `out/ref100/profiles.jsonl`
(the current baseline) and paste the baseline numbers. The planner will re-run
the 100 reference companies and the random 999 live.

## Planner acceptance (live, not for Codex)

- Reference-100 websites go from 70 to ≥ 85 with no new wrong-company rows.
- On the random 999: zero wrong-company publications in a hand audit of
  every `g4_registry_declared` and `g4_exact_org_number` row; websites
  ≥ 106; run time within budget.
- Two consecutive runs on the same 100 companies: false changes ≈ 0.
