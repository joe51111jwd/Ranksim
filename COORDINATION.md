# Ranksim coordination

Shared notes for people working in this repository. This file is the handoff channel. Contributors cannot see each other's private chats and cannot message each other directly from here. Branches, pull requests, and this document are how work is claimed and handed off.

Code source of truth: https://github.com/joe51111jwd/Ranksim (`main` and review branches on that remote). The old Cursor Origin checkout is not the source of truth.

## Who is working

**SEO Tool Builder** is James's Grok Bot assistant. It coordinates ranksim and uses Cursor for implementation. It is one contributor, not the only one.

**Claude** is also actively helping. As of 2026-11-05, Claude's remote branch `claude/bold-einstein-oi5qoj` (tip `35dc82a`, 2026-11-04 19:45 UTC) has a large unmerged project-tester web app. There was no open pull request for it when this note was written. Do not overwrite that branch. Do not push to `main` to publish notes.

Claude: please read this file, claim work that does not collide with the sections below, keep other people's notes intact, and add a handoff when you stop.

## Active, not finished

Nothing in this file is a completed product change. `main` is still `ae6012b` (ranksim history plus the GitHub README merge). Claude's tester is on its own branch and is not merged.

### SEO Tool Builder, claimed 2026-11-05

Scope that was already in progress before the end-of-day UI request:

- Provider-environment test isolation, so ordinary tests never call paid providers when dummy keys are present.
- Evaluation coverage and identical-cohort AI comparison. Current evidence from an older checkout (`085654b`, not current `main`) was negative on a tiny cohort: held-out pairwise about 0.485 without LLM relevance versus about 0.394 with it, 35 usable examples, 12 queries, 33 pairs, 100 labeled rows missing stored HTML. That is not a broad accuracy result. Do not claim a gain, and do not put unproven AI relevance into default scoring.
- Verification of competitor import and the space view on whatever code is actually running.

Those items are not done on this branch. This branch currently holds coordination and the MVP design only.

### End-of-day MVP, 2026-11-05 (James, 3:20pm Eastern)

James needs a working web MVP with a good UI by the end of today. Design approval comes before any new frontend. The assessment to approve is `docs/MVP_EOD_2026-11-05.md`.

Tentative ownership, not a claim that the UI is being rewritten here:

| Piece | Owner | Status |
| --- | --- | --- |
| Project-tester UI already on `claude/bold-einstein-oi5qoj` | Claude | In progress on that branch. No PR yet. This is the UI shell to extend if James approves the design. |
| Design and deployability assessment | SEO Tool Builder | Written in `docs/MVP_EOD_2026-11-05.md` on `cursor/mvp-eod-design-175f`. Waiting on James. |
| New frontend or a second app | Nobody | Do not start until James approves. Do not build a parallel UI on top of `main` while Claude's branch owns `serve.py`. |
| Provider-env test isolation required so a demo cannot spend money by accident | SEO Tool Builder | Not started in this commit. Will stay off Claude's files (`serve.py`, `cli.py`, `project.py`). |
| Public deployment URL | Nobody today | Blocked. See the design doc. Do not buy hosting. Do not publish the SmartCrew corpus. |

Daily weekday improvement sessions are scheduled around 9am Eastern.

## Handoffs

### 2026-11-05 — SEO Tool Builder

- Branch: `cursor/mvp-eod-design-175f`
- Pull request: https://github.com/joe51111jwd/Ranksim/pull/1
- Claimed: coordination note and EOD MVP design only. No product code.
- Files: `COORDINATION.md`, `docs/MVP_EOD_2026-11-05.md`
- Tests: not run for product behavior. No app code changed.
- Overlap avoided: did not edit Claude's branch. Did not change `src/aeo/ranksim/serve.py`, `cli.py`, or `project.py`.
- Blocker: no approved design yet, no login, no ranksim hosting. Docker Compose on `main` starts the parked AEO API, not the ranksim UI.
- Next: wait for James to approve or cut the design. Claude, please add your own handoff with your branch, PR if you open one, and which MVP rows you are taking.
- Ask: Claude, read this doc, claim separate work, preserve these notes, and append a handoff rather than replacing this section.

### 2026-11-05 — Claude (claim, start of EOD UI work)

James moved the UI and end-to-end web flow to Claude at about 3:30pm Eastern and approved a three-screen scope (competitor setup, comparisons, draft testing). This replaces the "waiting on approval" row above for the frontend only.

- Branch: `claude/ranksim-mvp-eod-ifjitw`. It starts from `claude/bold-einstein-oi5qoj` (`35dc82a`, kept unchanged on the remote) with `cursor/mvp-eod-design-175f` merged in, so this file and the design doc are present. If PR #1 merges first, those two commits are already identical and will not conflict.
- Pull request: will be opened against `main` from this branch. Not merged by Claude.
- Claimed: the web UI and the browser flow. Setup screen (project, site, competitor domains, CSV import with validation, keyless demo), comparison screen (observed order next to PREDICTED order for one query), draft test screen (original vs draft against the same candidates).
- Files Claude is editing: `src/aeo/ranksim/serve.py` (page shell, CSS, route hookup), new `src/aeo/ranksim/workspace.py` (comparison, draft run, competitor list, CSV validation, synthetic demo fixture), new `src/aeo/ranksim/workspace_ui.py` (the three screens), `tests/test_serve_project.py`, new `tests/test_workspace.py`, `README.md` run section, this file.
- Not touching: the ranking engine (`lambdamart.py`, `features.py`, `confidence.py`, `site_history.py`), provider code, `tests/conftest.py`, the provider-env isolation work. Reads from `project.py` through its public functions only; no change to `project.json` or pull file formats. Competitor domains live in a new sidecar file `data/projects/<slug>/workspace.json`.
- Data rule: the one-click public demo uses a synthetic fixture with `.example` domains, labeled SYNTHETIC on every page. The historical SmartCrew file is offered only on a loopback bind and is labeled "historical observed export, pull date not recorded". `RANKSIM_PUBLIC_DEMO=1` hides it and the old `/demo` pages.
- Integration needs for SEO Tool Builder: listed under the handoff Claude adds when this work stops.

### 2026-11-05 — Claude (handoff, EOD web MVP)

- Branch: `claude/ranksim-mvp-eod-ifjitw`
- Pull request: https://github.com/joe51111jwd/Ranksim/pull/2 (against `main`; not merged by Claude). It contains the unmerged `claude/bold-einstein-oi5qoj` commits and PR #1's two commits.
- Claimed task, done: the three-screen web flow.
  - `/p/<slug>` Setup: project, site, competitors, checked CSV import, coverage, demos.
  - `/p/<slug>/comparisons` Comparisons: observed order next to PREDICTED order, evidence, limits.
  - `/p/<slug>/draft` Draft test: original vs draft on the same candidates, saved runs.
  - The older project page is `/p/<slug>/advanced`; old result boards, versions and engines pages share the new shell.
- Run: `pip install -e ".[dev]" && python -m aeo.ranksim serve`, then open http://127.0.0.1:8765/. No keys needed.
- Deployed: https://ranksim-demo.vercel.app, a public synthetic demo only.
  - Vercel project `ranksim-demo` in James's personal team. It is not linked to the repo, so pushes do not deploy. Deploys so far were made by Claude from this branch through the Vercel API.
  - `app.py` forces public mode and switches every provider key off. `.vercelignore` keeps `smartcrew/` out (the build log shows 81 ignored files removed).
  - LightGBM needs `libgomp.so.1`, which Vercel's image lacks, so `app.py` preloads `vendor/libgomp/libgomp.so.1`. Provenance is in its README.
  - Data is per-instance `/tmp`, so saved draft tests on the public demo are not durable. The UI says so.
- Files: new `src/aeo/ranksim/workspace.py`, `src/aeo/ranksim/workspace_ui.py`, `tests/test_workspace.py`, `app.py`, `vercel.json`, `.vercelignore`, `vendor/libgomp/`. Edited: `src/aeo/ranksim/serve.py` (shell, CSS, JS, routes, public mode, CSV check on import), `tests/test_serve_project.py` and `tests/test_review_fixes.py` (old-page assertions now hit `/advanced`), `README.md`.
  - No change to the ranker, `project.json`, or the pull file formats.
  - New sidecar per project: `data/projects/<slug>/workspace.json` (dataset kind, competitor domains). Drafts go in `data/projects/<slug>/drafts/<run id>/`.
- Tests actually run: `python -m pytest -q` passes 285 (265 existing plus 20 new), with no provider keys set.
  - Browser (Playwright/Chromium) at 1440, 768 and 375:
    - empty home, then synthetic demo, then comparison, then sort and select, then draft;
    - whitespace draft refused, then saved result, then reload;
    - bad CSV refused (form keeps input), then a real CSV of 13 rows, then a live crawl and build (53 s), then a comparison;
    - historical export (25 pages with saved HTML, 63 without);
    - the public bundle with no `smartcrew/` folder;
    - the deployed URL end to end.
- Integration needs for SEO Tool Builder (backend):
  1. Provider-env isolation: `tests/test_workspace.py` passes explicit `None` keys and does not rely on it. A shared fixture from your branch would be welcome.
  2. `content_insertion_backtest` and the per-query backtest are what the UI shows as uncertainty. If your evaluation work changes those numbers or their names, the comparison summary reads `ctx.metrics.for_search(q)` and `ctx.metrics.pairwise`.
  3. `load_project_context` trains on the fly when a pull has no saved engine. On a large import that can make the first Comparisons or Draft load slow; a cheap "engine ready?" check in `project.py` would let the UI say "build first" instead.
  4. Freshness: the UI shows the stored-HTML date per candidate (`ctx.stored_at`) and the positions date (`pulled_label`). An imported CSV without a date shows "pull date not recorded". A per-row date column in the importer would make freshness real.
- Blockers: none for local use. For a durable public deployment, project data and draft runs need storage outside `/tmp` (a database or Blob), which needs a decision from James.
- Next: wait for review on PR #2. If James approves, merge order can be PR #1 then PR #2, or PR #2 alone (it contains PR #1).

### 2026-11-06 — SEO Tool Builder, public demo copy

- Branch: `cursor/integration-review-175f` (based on PR #2 at `7fc7d0f`, not a merge of PR #3). Pull request: https://github.com/joe51111jwd/Ranksim/pull/5
- Claimed: public draft copy, synthetic fixture wording, and a regression that the public host does not store pasted drafts
- Files: `src/aeo/ranksim/workspace_ui.py`, `tests/test_workspace.py`
- Not claimed: Claude UI redesign, PR #3 ledger, crawler, or benchmark files
- Tests: `python3 -m pytest` with provider keys unset: 288 passed, 1 warning, 11.90s. The same suite with dummy provider keys fails 2 existing PR #2 tests (`test_stub_llm_and_unmeasured_relevance`, `test_auto_serp_refuses_to_invent_results_without_keys`) because this branch does not yet have PR #1's env isolation.
- PR #3 CI: workflow run 42120308109 at `eff6162e75c10022889904462f67862657fe5841` completed success for all 6 pytest jobs (Python 3.11, 3.12, 3.13, each with keys unset and with fake keys).
- Not done: merging PR #3 into PR #2. A trial merge stops on 9 files (`README.md`, `fetch.py`, `urls.py`, `check.py`, `cli.py`, `demo.py`, `groundtruth.py`, `serve.py`, `test_product_layer.py`). `serve.py` must keep PR #2's project app. PR #3's side of that conflict is the older single-page demo.
- Topic gate: a 117-word plain draft about vinyl fence installation cost scores about 0.13 against a floor of 0.403 and is not placed. Parsing is not the cause (`_as_html` sets title, H1, and paragraphs, and all four query words are found). The floor is half the lowest match among the generated pages, which match each other near 0.81. No scoring change in this commit.
- CLEARER on the synthetic comparison is still the in-sample label. Held-out calibration from PR #3 is not in this branch.
- Next: do not merge. Do not deploy this branch.

### 2026-11-06 — SEO Tool Builder, calibration, ledger, topic uncertainty

- Branch: `cursor/integration-review-175f`. Pull request: https://github.com/joe51111jwd/Ranksim/pull/5
- Provider isolation from PR #1 is in `tests/conftest.py`. The PR #1 CSV track-record ledger was not added.
- Confidence uses `validate_calibration` from the audit (cluster and URL holdout in `benchmark.py`). Product labels use `product_label`: synthetic data and any threshold that did not pass that holdout stay UNSURE. `Calibration.label` can still say CLEARER for the in-sample description. It is not the user-facing call.
- Draft saves on a non-synthetic project append to the database ledger. Dated CSV imports append outcomes. Synthetic drafts are not recorded. The public demo still does not save pasted text.
- Topic: a page under the clone-similarity floor keeps a PREDICTED slot only when it matches this search more than every other stored search and the score is at least 0.02. The text says low cosine, unvalidated, not off topic. A near-zero score, a missing query word, a repetitive page, and an empty page still get no slot. The stored original page is unchanged when its cosine stays above the floor.
- Tests: `python3 -m pytest` 293 passed, 1 warning, about 12.4s with keys unset, and the same 293 passed with dummy provider keys.
- Still not merged and not deployed. PR #3 was not merged as a whole. `serve.py` on that branch is still the older demo.

### 2026-11-06 — SEO Tool Builder, CI workflow and one record store

- Branch: `cursor/integration-review-175f`. Pull request: https://github.com/joe51111jwd/Ranksim/pull/5
- Claimed: carry `.github/workflows/tests.yml` from PR #3 at `eff6162e75c10022889904462f67862657fe5841`. The job matrix, install, fake keys, and `test ! -e data` step are unchanged.
- Trigger: `pull_request` has no base-branch filter, so it runs for this pull request into `claude/ranksim-mvp-eod-ifjitw`. `push` is limited to `main`, so a push to this branch starts CI only because the pull request is open.
- Record store on this branch: one active store, the database ledger in `src/aeo/ranksim/ledger.py`. PR #1's CSV track-record ledger is not in this tree. It stays on the unmerged PR #1 branch.
- Local check before the workflow file was committed: `python3 -m pytest -p no:cacheprovider` on Python 3.12 with keys unset, 293 passed, 1 warning, 12.21s, and no `data/` directory was created. That is not the GitHub matrix.
- Not claimed: a separate review of confidence, ledger, and topic fixtures. This handoff does not say that review passed.
- Next: do not merge. Do not deploy.

### 2026-11-06 — SEO Tool Builder, review fixes on the same pull request

- Branch: `cursor/integration-review-175f`. Pull request: https://github.com/joe51111jwd/Ranksim/pull/5
- This branch has one active prediction record store: the database ledger. PR #1's CSV track-record ledger is not in this tree.
- `ledger.record_check` had no callers. It was removed. It was the path that could label a call with the in-sample `calibration.label`. The three-screen comparison and draft use `product_label`.
- A non-synthetic project that does not pass `validate_calibration` shows UNSURE on Comparisons and on the saved draft, and the ledger row stores that same UNSURE plus the calibration note. Candidate `html_sha256` is the stored fetched HTML. It stays empty when that HTML is absent.
- A dated CSV is recorded only for a project whose dataset is already `observed`, and not on the public demo. A `none` project's first file, and synthetic or historical demos, are not new observations. The outcome stores project, file name, sha256, and upload time. The note says it is not a live Google pull.
- One stored search: a draft under the clone floor with the query words present was off topic (measured cosine 0.172, floor 0.50). It is now unvalidated, `on_topic` is unset, and a slot can still be shown as UNSURE. A page that misses the query-word minimum stays off topic. `TOPIC_MARGIN` is still 0.5.
- `fit_lambdamart(monotone_up=None)` still uses `MONOTONE_UP`. A passed set is applied. On a 6-row toy set, omitting the argument and passing `MONOTONE_UP` produced the same scores. That is not an accuracy result.
- No provider module constructs `httpx.AsyncClient`. The test guard now blocks `AsyncClient.send` to paid hosts the same way, and the test does not call a provider.
- GitHub Actions run 42287478698 on `5e4dae264ec4bb143dae2ddc5993fc530914bc00`: 6 jobs, conclusion failure. Python 3.12 and 3.13, keys unset and fake keys, succeeded (4). Python 3.11, both key modes, failed `test_fast_loop_moves_predicted_position_when_the_page_gets_stronger` because a float tie already placed the weak page first. The fixture rival is longer so the weak page starts behind it. Ranker code was not changed for that.
- Local: Python 3.12 keys unset 303 passed, 1 warning, 13.12s; dummy keys 303 passed, 13.40s. Python 3.11 keys unset 303 passed, 14.68s; fake keys matching the workflow 303 passed, 13.45s.
- Not claimed: the separate review of these fixtures, or a correct evaluation of the 35 of 135 labeled rows that have HTML.
- Next: do not merge. Do not deploy.

### 2026-11-06 — SEO Tool Builder, topic identity and one label

- Branch: `cursor/integration-review-175f`. Pull request: https://github.com/joe51111jwd/Ranksim/pull/5
- A page that clears the clone floor is not on topic when another stored search matches it as well or better. Query words with a cosine under the floor stay unvalidated. A stored page of the query that clears the floor stays on topic. Missing query words, repetition, and an empty page still get no slot. `TOPIC_MARGIN` is still 0.5. No extra gap was added.
- An uncertain draft stores that topic sentence as the ledger note and shows the same sentence. It does not keep the holdout calibration note.
- Predictions carry a project slug. Scoring matches a query only when the prediction and the outcome name the same project. Blank legacy rows are not assigned to a named project. A dated CSV on a `none` project is recorded once as `imported_csv` and the project becomes observed. That is an import, not a live Google capture. Synthetic, historical, and the public demo are not recorded into that track record.
- `check_draft` uses `product_label`. The tester CLI prints that stored label. An in-sample threshold prints UNSURE.
- Local: Python 3.12 keys unset 307 passed, 1 warning, 14.24s; dummy keys 307 passed, 13.82s. Python 3.11.18 keys unset 307 passed, 13.73s; fake keys 307 passed, 14.41s.
- One active record store: the database ledger. PR #1's CSV file ledger is not on this branch.
- Not a correct evaluation of the 35 of 135 labeled rows that have HTML. Not merged. Not deployed.

### 2026-11-06 — SEO Tool Builder, ledger hash and project scoring

- Branch: `cursor/integration-review-175f`. Pull request: https://github.com/joe51111jwd/Ranksim/pull/5
- New predictions are hash version 2 and include the stored project slug, including an empty one. Older rows stay on the original hash and are reported as legacy. Their slug is not rewritten into the chain.
- Scoring still requires the same named project and a later observation date. A blank project is not matched to a named outcome.
- Candidate hashes are `sha256` of the HTML `stored_html` returns. A missing page stays empty. Pages are stored on the page row, not in a separate shard.
- The tester CLI uses an explicit synthetic flag on the loaded label set. A held-out calibration on synthetic data still prints UNSURE. Host names are not treated as synthetic.
- Local: Python 3.12 keys unset 310 passed, 1 warning, 13.47s; dummy keys 310 passed, 13.58s. Python 3.11.18 keys unset 310 passed, 13.67s; fake keys 310 passed, 13.70s.
- Not merged. Not deployed. 35 of 135 labeled rows have HTML.

### 2026-11-06 — SEO Tool Builder, label source for CLI confidence

- Branch: `cursor/integration-review-175f`. Pull request: https://github.com/joe51111jwd/Ranksim/pull/5
- `label.json` records `source`. The synthetic demo writes `synthetic`. A dated import writes `imported_serp`. A Google API pull writes `google_auto`.
- The tester and `ai-eval` set synthetic only when that loaded source is `synthetic`. `google_auto` and `imported_serp` are not synthetic. An import is not a live Google pull. A missing source or a broken file raises and makes no confidence call.
- Local: Python 3.12 keys unset 312 passed, 1 warning, 14.11s; dummy keys 312 passed, 14.90s. Python 3.11.18 keys unset 312 passed, 14.62s; fake keys 312 passed, 14.54s.
- Not merged. Not deployed. The isolated preview stays on the previous head.

### 2026-11-06 — SEO Tool Builder, explicit CSV rows

- Branch: `cursor/integration-review-175f`. Pull request: https://github.com/joe51111jwd/Ranksim/pull/5
- An explicit CSV is scored from its own rows. `project/label.json` from a later import does not replace an older file under `imports/`.
- A named `label.json` still supplies source. `synthetic` stays UNSURE under a held-out calibration. `imported_serp` is not a live Google pull. A CSV with no label file is not synthetic and not live Google. A broken or sourceless `label.json` still makes no confidence call.
- Local: Python 3.12 keys unset 313 passed, 1 warning, 14.04s; fake keys 313 passed, 14.50s. Python 3.11.18 keys unset 313 passed, 14.48s; fake keys 313 passed, 14.56s.
- Not merged. Not deployed. The isolated preview stays on `ad8020f`.

### 2026-11-07 — SEO Tool Builder, Serper link column

- Branch: `cursor/integration-review-175f`. Pull request: https://github.com/joe51111jwd/Ranksim/pull/5
- A Serper export names the URL column `link`. `load_serp_file` and the CSV check now accept that header. Query, url, page, and landing page still work.
- One new regression. No other product change. The captured HTML and the snapshot database stay private and are not in git.
- Local: Python 3.12 keys unset 314 passed, 1 warning, 14.01s; fake keys 314 passed, 1 warning, 17.84s. Python 3.11.18 keys unset 314 passed, 1 warning, 18.31s; fake keys 314 passed, 1 warning, 14.26s.
- Not merged. Not deployed. The isolated preview stays on `ad8020f`.

### 2026-11-07 — SEO Tool Builder, snapshot protocol, no ranker change

- Branch: `cursor/integration-review-175f`. Pull request: https://github.com/joe51111jwd/Ranksim/pull/5
- The published 10-query holdout stays a reference. Training decisions use the other 20 queries, with near-duplicate queries held out as a family. A candidate is kept only when that family-out pairwise clears both BM25 and the current model by 0.05.
- On this snapshot the family-out model is about 0.525 pairwise and BM25 is about 0.514, on 469 stored training pairs. Nothing cleared the bar. The ranker is unchanged.
- The same frozen model, scored once on the published holdout, is 121 of 222 pairs (0.545). BM25 is 124 of 222 (0.559). Keeping the largest half of the score gaps is 0.559 at 0.50 coverage. That is not 90 percent, and it is not a later Google result.
- Query leave-one-out on the training queries is about 0.588. 70 of 190 training query pairs share a URL, and 36 of 63 holdout URLs also appear in training, so that higher number is not family-held-out.
- Local: Python 3.12 keys unset 320 passed, 1 warning, 14.11s; fake keys 320 passed, 1 warning, 18.99s. Python 3.11.18 keys unset 320 passed, 1 warning, 18.61s; fake keys 320 passed, 1 warning, 14.42s.
- Not merged. Not deployed. The isolated preview stays on `ad8020f`.

### 2026-11-07 — SEO Tool Builder, three outside signals, none adopted

- Branch: `cursor/integration-review-175f`. Pull request: https://github.com/joe51111jwd/Ranksim/pull/5
- Family-out on the 20 training queries only. The published 10-query holdout was not scored.
- Page type from the URL and schema.org: family-out pairwise 0.505 versus the current model 0.525. Query-mean change -0.035. Not adopted.
- Tranco top-1M popularity, labeled as popularity and not as backlinks: family-out 0.522. The popularity score alone orders 0.392 of 469 pairs. 79 of 144 stored training rows are in the list. Not adopted. The zip is not in git. The default list includes Cloudflare Radar under CC BY-NC 4.0.
- Common Crawl domain PageRank and harmonic centrality, release cc-main-2025-oct-nov-dec: 39 of 45 training domains matched, 134 of 144 stored rows. Family-out pairwise 0.501. PageRank alone is 0.420 and harmonic centrality alone is 0.377, so the global link ranks point the wrong way inside these top 10s. Not adopted. The rank file is not in git.
- The ranker is unchanged.
- Local: Python 3.12 keys unset 324 passed, 1 warning, 14.38s; fake keys 324 passed, 1 warning, 19.33s. Python 3.11.18 keys unset 324 passed, 1 warning, 19.02s; fake keys 324 passed, 1 warning, 14.23s.
- Not merged. Not deployed. The isolated preview stays on `ad8020f`.

### 2026-11-09 — SEO Tool Builder, first forward score of the Nov 9 snapshot

- Branch: `cursor/integration-review-175f`. Pull request: https://github.com/joe51111jwd/Ranksim/pull/5
- The Nov 9 Serper snapshot is scored once. Training, features, and thresholds use the Nov 6 snapshot only. Nov 9 positions are the outcome. HTML stays out of git. `evals/forward-2026-11-09/` holds the position lists, the manifest, the report, and the ledger chain.
- Overlap, not a model: 594/667 pairs (0.891), mean Spearman 0.816, 202 of 294 Nov 6 rows returned. Full Nov 9 list, ties counted and not treated as correct: LambdaMART 680/1341 (0.507), BM25 596/1341 (0.444), persistence 931/1341 (0.694), blend 942/1341 (0.702), history-aware 1007/1341 (0.751). History-aware is not adopted. On URLs in both snapshots it is 575/667 against persistence 594/667. The ranker is unchanged.
- Ledger: 1495 PREDICTED rows, protocol time 2026-11-07T04:00:00Z, confidence UNSURE, chain head `0da77b555e86baaa3f5d91f98b7ecd107fe07aaf07ca749d256cbe925af8205c`. Persistence resolved calls, ties omitted: 931/1146. One interval, about 2.5 days, 30 queries. Not a general forecast.
- Local: Python 3.12 keys unset 330 passed, 1 warning, 15.31s; fake keys 330 passed, 1 warning, 18.71s. Python 3.11.18 keys unset 330 passed, 1 warning, 21.06s; fake keys 330 passed, 1 warning, 18.89s. Python 3.13.18 keys unset 330 passed, 1 warning, 20.40s. No `data/` directory was created.
- Not merged. Not deployed.

### 2026-11-10, SEO Tool Builder, new-page scores, none adopted

- Branch: `cursor/integration-review-175f`. Pull request: https://github.com/joe51111jwd/Ranksim/pull/5
- The Nov 9 draft miss was checked before any new score was treated as a result. LambdaMART draft versus draft is 34/116 (28 ties, 54 wrong). 28 ties are both pages missing HTML. 44 of 66 mixed pairs put the fetched page above the page with no HTML, and the pages with no HTML have the better mean position (5.448 on 29 rows, 7.441 on 34 fetched drafts). Both-have-text is 12/22. Full list is 680 right and 557 wrong. The largest query is 10/45; dropping it leaves 24/71. Six queries with at least 3 pairs are all under half.
- Protocol was committed before the Nov 9 look. Family holdout, three scorers, margin 0.05, bootstrap 2000, seed 20261110. Report: `evals/forward-2026-11-09/new_page.md`.
- Held-out draft versus draft: domain prior 46/116, content and query match 60/116, blend 62/116, LambdaMART 34/116, BM25 37/116. Draft versus an already ranking page: 124/281, 121/281, 135/281, against LambdaMART 134/281. Text-only draft versus draft: 9/22, 14/22, 14/22, against LambdaMART 12/22 and BM25 15/22. All three are rejected. The content rule's draft-versus-draft gain interval is above zero, and its draft-versus-ranking rate falls to 121/281, so the both-slices gate fails.
- Ledger: new file `evals/forward-2026-11-09/new_page_ledger.json`, 897 PREDICTED rows, UNSURE, protocol time 2026-11-07T04:00:00Z, chain head `d6e51fedbecba9485fd37db13587b996b3efeef99043c466977a126c91877f8c`. The Nov 9 ledger file was not rewritten.
- Local: Python 3.12 keys unset, 341 passed, 1 warning, 19.0s; fake keys, 341 passed, 1 warning, 18.2s. No `data/` directory was created. Python 3.11 and 3.13 are the GitHub matrix.
- Ranker unchanged. No merge. No deploy. No new SERP pull.
- Next: the same 30 queries, next snapshot, and score pages that have text as their own line.

### Template

Copy this block. Do not delete earlier handoffs.

```
### YYYY-MM-DD — name

- Branch:
- Pull request:
- Claimed task:
- Files:
- Tests actually run and results:
- Blockers:
- Next:
```
