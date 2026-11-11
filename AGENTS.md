# Agents

Read [docs/MISSION.md](docs/MISSION.md) first. It has the mission, the hard rules, and the current results. Do not merge to main or deploy without James's explicit authorization. Never commit or print API keys.

## Setup

Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Tests

```bash
python -m pytest
```

GitHub Actions (`.github/workflows/tests.yml`) runs that suite on Python 3.11, 3.12, and 3.13, once with provider keys unset and once with fake keys. The suite must pass with no real keys.

## Forward eval

Stored HTML is not in git. The command needs the private snapshot directories:

```bash
python -m aeo.ranksim forward-eval \
  --baseline <2026-11-06 snapshot dir> \
  --forward <2026-11-09 snapshot dir> \
  --out evals/forward-2026-11-09/report.json \
  --ledger-json evals/forward-2026-11-09/ledger.json
```

The scored file already in git is `evals/forward-2026-11-09/report.json`. The new-page check that does not need the private HTML is `evals/forward-2026-11-09/new_page.md`. The Nov 10 recovered-content look is `evals/forward-2026-11-09/recovered.md`. The Serper scrape fill is `evals/forward-2026-11-09/scrape.md`. Read `docs/MISSION.md` before treating any rerun as a new accuracy claim.

The 2026-11-11 predictions were frozen before that outcome was opened:

```bash
python -m aeo.ranksim forward-freeze \
  --nov6 <2026-11-06 snapshot dir> \
  --nov9 <2026-11-09 snapshot dir> \
  --out-dir evals/forward-2026-11-11
```

Score that frozen file only after the prediction commit is pushed. It does not refit. The scored result is `evals/forward-2026-11-11/report.md`.

```bash
python -m aeo.ranksim forward-score \
  --predictions evals/forward-2026-11-11/predictions.json \
  --outcome <2026-11-11 snapshot dir> \
  --ledger-json evals/forward-2026-11-11/ledger.json \
  --out evals/forward-2026-11-11/report.json
```

A saved Serper scrape folder (manifest.csv plus raw JSON) is imported with no key:

```python
from aeo.ranksim.serper_scrape import load_scrape_dir
pages = load_scrape_dir("path/to/scrape", captured_on="2026-11-10")
```

Refetch URLs that have no stored HTML. Serper scrape stays off unless you pass a credit cap and `SERPER_API_KEY` is set. Do not commit the HTML or the key.

```bash
python -m aeo.ranksim refetch-missing <snapshot dir> --out recovery.json --delay 1
```

```bash
python -m aeo.ranksim new-page-eval \
  --ledger evals/forward-2026-11-09/ledger.json \
  --baseline-csv evals/forward-2026-11-09/baseline_serp.csv \
  --forward-csv evals/forward-2026-11-09/forward_serp.csv \
  --out evals/forward-2026-11-09/new_page_report.json
```
