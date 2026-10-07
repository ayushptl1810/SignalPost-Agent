# Scripts map

Use the folders below instead of searching one large flat scripts directory.

| Folder | Purpose | Examples |
| --- | --- | --- |
| `run/` | Main workflows | `run_competition_batch.py`, `run_search_discovery.py`, `run_brave_discovery.py`, `run_scrapy_websites.py` |
| `connectors/` | Optional source connectors | LinkedIn experiments, Google News, YouTube, annual reports, reviews |
| `analysis/` | Scoring and evaluation | completeness, external footprint, research-agent evaluation |
| `transform/` | Normalize or build evidence | identity gates, site activity/news, social links |
| `demo/` | Local demos and prototypes | `ask_agent.py`, `build_prototype.py` |

The competition path is:

1. `run/run_competition_batch.py` — registry baseline and terminal envelopes.
2. `run/run_search_discovery.py` — Serper candidate discovery, sitemap-aware contact/legal crawl, first-party verification, and classifier annotation.
3. `run/run_scrapy_websites.py` — permitted website crawling.
4. `transform/` — identity gates and publishable evidence.
5. `analysis/` — audit and scoring.

The discovery runner reads `SERPER_API_KEY` from `.env` or the process environment. It
does not persist raw search responses, query text, titles, or snippets. It compares
registry contact/address fields locally and does not put those values into search
queries. Use the
deterministic `rules` classifier by default; install the optional `classifier` extra
to try the local Laya typed-decision model.

## Evaluating a discovery round

```bash
# 1. (optional, free) refresh the NAV employer index: org number -> registered homepage + active ads
uv run python scripts/connectors/run_nav_jobs_connector.py --output out/nav-employer-index.jsonl --report out/nav-report.json

# 2. run discovery. Registry/NAV/name-derived candidates run first; search only runs if they fail.
#    --no-search needs no API key. --ledger enables the negative cache (default 30 days).
uv run python scripts/run/run_search_discovery.py --input out/smoke-profiles.jsonl --output out/run-N.jsonl \
  --report out/run-N-report.json --limit 100 --ledger out/ledger.jsonl --nav-index out/nav-employer-index.jsonl

# 3. score it and compare with the previous round
python3 scripts/analysis/score_discovery_run.py --profiles out/run-N.jsonl --report out/run-N-report.json \
  --output out/scorecard-N.json --previous out/scorecard-M.json [--labels out/labels.jsonl]

# 4. label what the scorecard flags, then rescore with --labels to get measured precision
python3 scripts/analysis/review_candidates.py --scorecard out/scorecard-N.json --profiles out/run-N.jsonl \
  --labels out/labels.jsonl --also-unverified 10
```

The scorecard prints COVERAGE (verified sites over all companies), YIELD (over those queried),
TRUST (share of verified sites with a strong identity match, a contact/address corroborator and a
unique domain), PRECISION (only with labels, as a Wilson lower bound), cost, provider error rate, the
biggest funnel loss, suspected directory domains, and a PASS / FAIL / INSUFFICIENT_LABELS gate
(labeled precision lower bound under 0.90, or a trust drop above 5 points). Only a labeled precision
bound can actually prove accuracy; TRUST is a proxy that says "look here".
