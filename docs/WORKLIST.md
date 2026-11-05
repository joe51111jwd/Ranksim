# Worklist for PR #3 (`claude/ranksim-audit-8nshyt`)

This session's checklist, kept in git as asked and checked off as each item lands with its commit.
Detail and acceptance criteria for every item are in `docs/BACKLOG.md`; evidence in
`docs/EXPERIMENTS.md`.

Cross-agent coordination: the shared board is `COORDINATION.md` on PR #1
(`cursor/mvp-eod-design-175f`), and the web UI is claimed by another Claude session on PR #2
(`claude/ranksim-mvp-eod-ifjitw`). This list does not replace that board. To avoid colliding with
PR #2, items here stay out of the UI files it owns: `src/aeo/ranksim/serve.py`, `demo.py`,
`workspace.py`, `workspace_ui.py`, `project.py`, `tests/test_serve_project.py`, `tests/test_workspace.py`.

Legend: `[x]` done (commit noted), `[ ]` open, `[-]` blocked (reason given).

## A. Lookout and integration

- [x] A1. Watch for new code: subscribed to PR #3 activity; `git fetch origin` before each item.
  Found on 2026-11-05: PR #1 (agent board, provider-isolated tests, `coverage.py`) and PR #2
  (three-screen web UI, public synthetic demo), both open against `main`.
- [x] A2. Dry-run merge with PR #1 and PR #2: PR #1 conflicts only in `tests/conftest.py`; PR #2
  (which contains PR #1) conflicts in 9 files: `README.md`, `crawl/fetch.py`, `crawl/urls.py`,
  `ranksim/check.py`, `cli.py`, `demo.py`, `groundtruth.py`, `serve.py`, `tests/test_product_layer.py`.
  Plan when either lands on `main`: merge `main` into this branch (no force-push); take PR #2's
  `serve.py`/`demo.py` UI and re-apply only this branch's non-UI behaviour there (ledger record on
  check, held-out calibration note, draft provenance); keep this branch's crawler and label-import
  logic and fold in PR #2's smaller edits to the same functions; union the two conftest fixtures.
- [ ] A3. When `main` moves: merge it here, resolve, run the full suite on 3.11 and 3.13, push,
  confirm CI. Repeat each time `main` moves.

## B. Backend work (no UI files)

- [ ] B1. Freeze the benchmark: `benchmark freeze` writes a dataset manifest and page snapshot;
  `--final` runs once per model id and appends to a results log (BACKLOG P0-2).
- [ ] B2. Calibrate on the statistic the check uses: adjacent-page gaps, pre-gate scores, gated
  pairs excluded (P1-13).
- [ ] B3. Crawler: connect to the vetted IP (DNS rebinding), deadline covers response headers,
  honour Retry-After with per-host backoff (P0-4 remainder).
- [ ] B4. robots.txt RFC 9309 matcher (wildcards, longest match on 3.11/3.12); refuse a placeholder
  user agent for third-party crawls (P1-6).
- [ ] B5. URL match key for joining labels to pages; persist final URL and canonical; flag labeled
  URLs that redirect (P1-4).
- [ ] B6. Detect client-rendered (JS shell) pages and report them as a coverage reason (P1-5).
- [ ] B7. Per-label coverage table with thin/empty/JS-shell reasons (`--coverage-csv`); saved HTML
  and seeds found from the repo root, not the cwd (P1-9).
- [ ] B8. One intents source for training, checks and space; every labeled query has an intent or
  is reported as unknown (P1-14).
- [ ] B9. Space metrics: top-k only when the set has more than k pages; observed top 10 means
  position <= 10; head-to-head perspective; business vs SERP competitors (P1-1, backend only).
- [ ] B10. Competitor import validation: csv.reader, IDNA, reject IPs/localhost/ports, report
  rejected lines (P1-2).
- [ ] B11. SERP history append-only with locale/device/collected_at; `export-serp`; `--keywords`
  file input (P1-7).
- [ ] B12. Attribution scores before and after separately against the same peers (P1-8).
- [ ] B13. `test --bundle` inserts only matching pages; report renders bundle runs and computes
  accuracy from the label file (P1-10).
- [ ] B14. LLM explanation numbers checked against the brief; provider selector and model-format
  check (P1-11).
- [ ] B15. Site prior (content-blind) as one more benchmark baseline row (P1-12).
- [ ] B16. LambdaMART regularization and linear label gain, chosen a priori, measured on the
  benchmark and documented whatever the result (P1-3).
- [ ] B17. Independent review of B1-B16 (multi-lens review with adversarial verification); fix
  confirmed defects with regression tests.

## C. Blocked on credentials or decisions

- [-] C1. 300+ dated queries across sites and niches (P0-1): needs a SERP key.
- [-] C2. Repeated snapshots for volatility and ledger outcomes (P0-3): needs a SERP key.
- [-] C3. Non-ranking candidates / "enters the top 10" task (P0-5): needs deeper SERP collection.
- [-] C4. AI relevance judge rerun: needs an LLM key and a few hundred labeled pairs.
- [-] C5. Durable shared deployment (storage, accounts): needs a decision from James.
