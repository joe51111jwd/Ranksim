# Backend contracts for the web UI

Task state lives only in the board section of `COORDINATION.md`. This file is the call contract, not a second task list.

Checked 2026-11-05 against `origin/main` `ae6012b` and Claude's branch `claude/bold-einstein-oi5qoj` `35dc82a`. SEO Tool Builder did not change these routes. Claude owns the UI and these handlers. Do not treat a field rename as safe until Claude's branch says so.

## Ownership

- Claude: every screen, route, template, and stylesheet. Branch `claude/bold-einstein-oi5qoj`. No pull request was open at this check.
- SEO Tool Builder: tests, provider isolation, and `aeo.ranksim.coverage`. No UI files on `cursor/mvp-eod-design-175f`.

## What `main` serves today

`python -m aeo.ranksim serve` defaults to host `0.0.0.0` and port 8765. Routes:

| Method | Path | Body |
| --- | --- | --- |
| GET | `/` | Optional `demo=1` runs the sample draft. |
| GET | `/space` | Competitor board from `smartcrew/competitors.csv` or the labeled domains. |
| POST | `/check` | Form fields `query`, `url`, `html`, `ai` (`ai=1` asks for a rewrite only when a key exists). |

REAL positions come from `smartcrew/serp.csv` (`query,url,position`). PREDICTED ranks are among stored pages for that query. Missing HTML is `PREDICTED n/a`. There is no login.

## What Claude's branch serves

`python -m aeo.ranksim serve` defaults to `127.0.0.1:8765`. `create_app` still accepts a positional `DemoState` and then mounts the old demo at `/demo` and `/check`. New project routes:

| Method | Path | Inputs that the handler reads |
| --- | --- | --- |
| GET | `/healthz` | JSON `{"ok": true}`. |
| GET | `/` | Project list. `demo=1` redirects to `/demo?demo=1`. |
| POST | `/projects` | Form `name`, `site`, `searches`, plus CSRF. Redirects to `/p/{slug}`. |
| GET | `/p/{slug}` | Project page. Query `job`, `msg`. |
| POST | `/p/{slug}/pull` | CSRF. Starts a background pull. Redirects with `?job=`. |
| POST | `/p/{slug}/test` | Multipart. HTML paste or upload, page URL. `simulate` is ignored unless an LLM key is set. |
| GET | `/p/{slug}/jobs/{job_id}` | Progress page. Redirects to the result when the job is done. |
| GET | `/api/jobs/{job_id}` | JSON: `id`, `kind`, `slug`, `title`, `status`, `message`, `error`, `result_url`, `created_at`, `elapsed`, `steps`, `timings`. Unknown id is 404 `{"error": "no such job"}`. |
| GET | `/p/{slug}/results/{rid}` | Result board. |
| GET | `/p/{slug}/results/{rid}.json` | Same board as JSON. |
| GET | `/p/{slug}/versions` | Query `url`. |
| GET | `/p/{slug}/compare` | Query `url`, `a`, `b`. |
| GET | `/p/{slug}/engines` | AI engine rows. A missing key is `not run`, not a fabricated answer. |
| POST | `/p/{slug}/searches` | Form `searches`, CSRF. |
| POST | `/p/{slug}/import` | File field `csv_file` (`.csv` with `query,url,position`). Optional `snapshot_date` as `YYYY-MM-DD`. Optional `add_searches`. CSRF. |
| GET | `/space`, `/demo/space` | Legacy competitor board when a legacy state exists. |

POST bodies require the CSRF field Claude's forms already send. A pull or import that finds the project lock busy returns the busy page instead of a second job.

Import does not invent positions. The committed SmartCrew file has no pull date; Claude's importer records that as pull date not recorded unless `snapshot_date` is set.

## Coverage helper Claude can call

`aeo.ranksim.coverage.audit_labels(labels, pages, label_source=..., page_source=..., snapshot_urls=...)`

Returns stored rows, missing rows, singleton queries, pair count, a `pair_cohort_id` for the rows that enter pairs, and `broad_claim`. `broad_claim` is false below 16 queries or 100 pairs. That is a reporting bar, not a confidence interval.

`same_pair_cohort` is the check for an AI versus non-AI comparison. `ai_comparison_note` refuses a benefit claim when the delta is negative, the cohort differs, or the bar is missed. `llm_relevance` is not in `FEATURE_NAMES`. This branch does not add it to default scoring.

`format_audit` is plain text. Nothing here was wired into `cli.py` or `serve.py`, because those files are Claude's.

## Integration blockers

- Do not merge this branch into Claude's branch by rewriting `serve.py`. The coverage module is additive.
- `main` still binds `0.0.0.0` with no login. Claude's branch binds loopback unless `--host` is widened, and then prints that there is no login.
- Docker on `main` runs `uvicorn aeo.main:app` port 8741 (the parked AEO API), not `ranksim serve`.
- No public URL was deployed. The SmartCrew corpus stays in the private repo. A public demo needs a synthetic fixture labeled as example data, not this historical file.
- Provider tests on this branch clear `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, SERP keys, and a `.env` file, and block live calls to those vendors. Mock transports still work. Claude's UI must keep showing `not run` when those keys are absent.

## Runtime if a private host is approved later

Python 3.11 or newer. From a checkout of Claude's UI branch, with no provider keys:

```bash
pip install -e '.[dev]'
python -m aeo.ranksim serve --host 127.0.0.1 --port 8765
```

Reach it through an existing private tunnel. Do not publish port 8765 on the open internet. Do not put API keys in the page. This command was not given a public URL here.
