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

The scored file already in git is `evals/forward-2026-11-09/report.json`. The new-page check that does not need the private HTML is `evals/forward-2026-11-09/new_page.md`. Read `docs/MISSION.md` before treating any rerun as a new accuracy claim.

```bash
python -m aeo.ranksim new-page-eval \
  --ledger evals/forward-2026-11-09/ledger.json \
  --baseline-csv evals/forward-2026-11-09/baseline_serp.csv \
  --forward-csv evals/forward-2026-11-09/forward_serp.csv \
  --out evals/forward-2026-11-09/new_page_report.json
```
