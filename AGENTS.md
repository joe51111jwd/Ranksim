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

## Forecast system (the build plan)

The plan is `docs/plan/BUILD_PLAN_ROAD_TO_90.md` (the PDF beside it is the original). Read it in full before touching `src/aeo/ranksim/forecast/`. The exact definitions the code follows are in `docs/plan/IMPLEMENTATION_SPEC.md`. What the plan gets wrong or leaves open is in `docs/plan/PLAN_AUDIT.md`. The step by step commands are in `docs/plan/RUNBOOK.md`.

```bash
python -m aeo.ranksim forecast --help
python -m aeo.ranksim forecast demo --dir <temp dir>     # the whole flow on SYNTHETIC data, never an accuracy claim
python -m pytest tests/test_forecast_*.py tests/test_provider_isolation.py
```

Rules that are enforced in code, do not work around them:

- A run that reads held-out rows (`strength run`, the trust run, `backtest run`) is refused unless its design file is committed and pushed, the tree under `src/aeo/ranksim/forecast` is clean and matches the freeze, and that design version was not scored before.
- Evidence under `evals/forecast/` is never edited or replaced. A new look at the same held-out rows is a new design version with a written reason, and its reports are marked PROVISIONAL. `forecast backtest run --reason "..."` picks the next version itself. The strength, trust and cohort-backtest commands take `--design-version k`, with `--reason "..."` when the design is frozen.
- Every paid call is a dry run without `--execute`, needs a recorded approval and a $1 sample first, and goes through the spend ledger. The approved budget starts at $0.
- A forecast is frozen before its outcome exists and is never replaced. A missed stage day is reported and never backfilled.
- A result on the free panel's already-ranking sites is a stand-in. It can never pass or fail a marker of the plan. A SYNTHETIC result is never an accuracy number.
- The free panel ends on 30 April 2026 and versions 1 and 2 have read all of its held-out rows. Do not tune anything on those reports. Fresh evidence comes only from the paid cohort or the seed sites.

Results and what James has to decide: `docs/plan/STATUS.md`.

Large data is not in git. It lives under `data/forecast/` (ignored). `data/forecast/cache/PANEL_VERSIONS.md` says which panel each scored version used.
