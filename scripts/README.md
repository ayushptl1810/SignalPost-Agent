# Scripts map

Use the folders below instead of searching one large flat scripts directory.

| Folder | Purpose | Examples |
| --- | --- | --- |
| `run/` | Main workflows | `run_competition_batch.py`, `run_brave_discovery.py`, `run_scrapy_websites.py` |
| `connectors/` | Optional source connectors | LinkedIn experiments, Google News, YouTube, annual reports, reviews |
| `analysis/` | Scoring and evaluation | completeness, external footprint, research-agent evaluation |
| `transform/` | Normalize or build evidence | identity gates, site activity/news, social links |
| `demo/` | Local demos and prototypes | `ask_agent.py`, `build_prototype.py` |

The competition path is:

1. `run/run_competition_batch.py` — registry baseline and terminal envelopes.
2. `run/run_brave_discovery.py` — candidate website discovery.
3. `run/run_scrapy_websites.py` — permitted website crawling.
4. `transform/` — identity gates and publishable evidence.
5. `analysis/` — audit and scoring.
