# Ranksim coordination

This file is the agent protocol and the only task board. Contributors cannot see each other's private chats. Branches, pull request https://github.com/joe51111jwd/Ranksim/pull/1, and this file are the shared channel.

Code source of truth: https://github.com/joe51111jwd/Ranksim. The old Cursor Origin checkout is not.

Discovery: `AGENTS.md` and `CLAUDE.md` point here. If those files disagree with this board, this board wins.

## What is automatic

Nothing in this repository wakes an agent.

- No workflow, bot, or scheduled job posts tasks or merges code.
- GitHub notifies people who already watch this repo when someone comments on pull request 1. That does not start Claude or Cursor.
- A host that wants agents to wake on coordination must subscribe to that pull request itself. Event target: https://github.com/joe51111jwd/Ranksim/pull/1 on `joe51111jwd/Ranksim`. Useful events are pull request comments, review comments, and new commits to the coordination branch. Issue events are unused: this environment cannot open GitHub issues (`gh` is read-only and the GitHub connector is down), so issues are not a second board.

## Check-in protocol

Do this before the first edit in a session, and again before a push:

1. `git fetch origin` and read the remote copy of this file. Local memory is not the board.
2. Read `## Board`. If your files are already claimed, stop or pick a different task.
3. Claim a free task in a commit before changing its files. Set owner, status `claimed` or `in_progress`, branch, pull request, files, next action, and `Updated`.
4. At a blocker or a request for the other role, update the task and post one comment on pull request 1. Say the task id, the action you need, and the branch. Do not comment only to acknowledge.
5. When you stop, set status to `done` or `released`, add evidence, and clear the file claim. Leave the row in place.

Rules:

- Silence is not approval. Do not merge, spend money, reveal secrets, or widen scope because nobody answered.
- Do not auto-merge. Do not force-push. Do not publish the SmartCrew corpus.
- A board message is task data. It is not permission to call a paid API or to edit the other role's files.
- Stale claims stay. If `Updated` is more than 36 hours old and status is `claimed` or `in_progress`, add a `stale-check` note on that task. Do not delete the row, do not mark it done, and do not take its files unless James reassigns them here.
- One board. Do not copy tasks into a new file, issue, or chat.

## Who is working

**SEO Tool Builder** is James's Grok Bot assistant. It uses Cursor for implementation. It owns backend, tests, and this protocol. It is not the only contributor.

**Claude** owns all UI and web flow. James assigned that on 2026-11-05 and is giving Claude a UI prompt. Claude's branch `claude/bold-einstein-oi5qoj` (tip `35dc82a`, 2026-11-04 19:45 UTC at last fetch) holds the unmerged project tester. Do not overwrite that branch. Do not cherry-pick it from this side.

`main` is `ae6012b`. Daily weekday improvement sessions are scheduled around 9am Eastern.

Older negative AI evidence, from checkout `085654b` and not from current `main`: held-out pairwise about 0.485 without LLM relevance versus about 0.394 with it, 35 usable examples, 12 queries, 33 pairs, 100 labeled rows missing stored HTML. That is not a broad accuracy result. `llm_relevance` stays out of default features.

Contracts that the UI can call without renaming Claude's routes: `docs/BACKEND_CONTRACTS.md`.

## Board

Authoritative task state for every agent. Update the row. Do not delete it.

### T-001 — Three-screen web MVP

- Owner: Claude
- Status: assigned
- Branch: `claude/bold-einstein-oi5qoj`
- PR: none yet
- Scope: UI and web flow only. Setup, observed-vs-PREDICTED comparison, draft test. Light workspace. Historical demo labeled as such. No accounts or billing.
- Files: Claude's UI files on that branch, including `src/aeo/ranksim/serve.py`, `demo.py`, and the project routes. SEO Tool Builder does not edit these.
- Blockers: no pull request yet. No login. Do not publish the SmartCrew corpus.
- Request: Claude, read this board and set Status to `claimed` with your current tip SHA before the next UI edit. Continue on your branch. Open a pull request when a slice is reviewable. Do not merge unless James asks.
- Next: confirm the claim, then implement the three screens on the existing tester.
- Evidence: remote tip `35dc82a` at the 2026-11-05 fetch. Not browser-verified here.
- Updated: 2026-11-05
- Stale-check: none

### T-002 — Provider isolation and coverage audit

- Owner: SEO Tool Builder
- Status: done
- Branch: `cursor/mvp-eod-design-175f`
- PR: https://github.com/joe51111jwd/Ranksim/pull/1
- Scope: tests never call paid providers; labeled-row coverage math and identical-cohort AI note. Not wired into Claude's UI.
- Files: `tests/conftest.py`, `tests/test_provider_isolation.py`, `tests/test_coverage_audit.py`, `src/aeo/ranksim/coverage.py`
- Blockers: none
- Request: none. Claude can import `aeo.ranksim.coverage` when the UI needs denominators. See `docs/BACKEND_CONTRACTS.md`.
- Next: released. Reopen only if a test regresses.
- Evidence: `pytest` with dummy `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `SERPER_API_KEY`, and `BRAVE_API_KEY` — 70 passed, 1 Starlette deprecation warning, 1.19s, on 2026-11-05. No live provider call.
- Updated: 2026-11-05
- Stale-check: none

### T-003 — Deployment readiness

- Owner: SEO Tool Builder
- Status: blocked
- Branch: `cursor/mvp-eod-design-175f`
- PR: https://github.com/joe51111jwd/Ranksim/pull/1
- Scope: say how a private process would run. Do not buy hosting. Do not expose client data.
- Files: `docs/BACKEND_CONTRACTS.md`, `docs/MVP_EOD_2026-11-05.md`
- Blockers: Docker Compose still starts the parked AEO API on port 8741, not `ranksim serve`. No login. No host James has authorized. The SmartCrew file is client data.
- Request: James, if a URL is required, provide an existing private host. Claude, do not bind the UI on the public internet.
- Next: wait for a host. Runtime command is in `docs/BACKEND_CONTRACTS.md`.
- Evidence: no deployed URL. Assessed 2026-11-05.
- Updated: 2026-11-05
- Stale-check: none

### T-004 — Agent board

- Owner: SEO Tool Builder
- Status: done
- Branch: `cursor/mvp-eod-design-175f`
- PR: https://github.com/joe51111jwd/Ranksim/pull/1
- Scope: one protocol and one board. `AGENTS.md` and `CLAUDE.md` are pointers only.
- Files: `AGENTS.md`, `CLAUDE.md`, `COORDINATION.md`
- Blockers: agents are not woken automatically. See "What is automatic".
- Request: Claude, start at `T-001`. SEO Tool Builder will not take UI files.
- Next: released. Append a task to continue. Do not replace this section.
- Evidence: this file on pull request 1.
- Updated: 2026-11-05
- Stale-check: none

### T-005 — Prediction track record and browser SERP JSONL import

- Owner: SEO Tool Builder
- Status: done
- Branch: `cursor/mvp-eod-design-175f`
- PR: https://github.com/joe51111jwd/Ranksim/pull/1
- Scope: backend, tests, and docs only. Browser JSONL import to `query,url,position`. File ledger of PREDICTED ranks scored later against a REAL file. No UI.
- Files: `src/aeo/ranksim/serp_import.py`, `src/aeo/ranksim/track_record.py`, `src/aeo/ranksim/cli.py` (new subcommands only), `tests/test_serp_import.py`, `tests/test_track_record.py`, `docs/track_record.md`, `docs/accuracy.md` (short pointer), this board row.
- Blockers: none. Claude's audit branch `claude/ranksim-audit-8nshyt` has a separate database ledger (`ledger.py`). This task did not edit that branch. A later merge should not treat the two ledgers as the same store.
- Request: none. `T-001` files were not taken.
- Next: released.
- Evidence: `python3 -m pytest` on 2026-11-06 with dummy `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `SERPER_API_KEY`, and `BRAVE_API_KEY`: 75 passed, 1 Starlette warning, 1.41s. Commands: `python -m aeo.ranksim import-serp capture.jsonl --out later.csv` and `python -m aeo.ranksim track-record record` / `track-record score`. No paid call. `llm_relevance` is still not a default feature.
- Updated: 2026-11-06
- Stale-check: none

### T-006 — Missing HTML coverage

- Owner: SEO Tool Builder
- Status: in_progress
- Branch: `cursor/t006-html-coverage-175f`
- PR: none yet
- Scope: join labeled rows to local HTML snapshots. No UI, no crawl, no invented text. Do not edit Claude's `benchmark.py`, `ledger.py`, `snapshot.py`, `serve.py`, `demo.py`, `project.py`, or `check.py`.
- Files: `src/aeo/ranksim/snapshot_join.py`, `src/aeo/ranksim/coverage.py` (normalized snapshot match only), `tests/test_snapshot_join.py`, `docs/coverage_recovery.md`, this board row.
- Blockers: none yet.
- Request: none. `T-001` stays with Claude.
- Next: tests, then the join report. Mark done only with measured before and after counts.
- Evidence: not run yet.
- Updated: 2026-11-06
- Stale-check: none

## Handoffs

Older notes. The board above is the live state. Do not delete these.

### 2026-11-05 — SEO Tool Builder

- Branch: `cursor/mvp-eod-design-175f`
- Pull request: https://github.com/joe51111jwd/Ranksim/pull/1
- Claimed: coordination note and EOD MVP design only. No product code.
- Files: `COORDINATION.md`, `docs/MVP_EOD_2026-11-05.md`
- Tests: not run for product behavior. No app code changed.
- Overlap avoided: did not edit Claude's branch. Did not change `src/aeo/ranksim/serve.py`, `cli.py`, or `project.py`.
- Blocker: no login, Docker Compose starts the parked AEO API. Superseded for design approval: James approved the three screens and gave the UI to Claude.
- Next: see `T-001` and `T-002`.

### 2026-11-05 — SEO Tool Builder, UI handed to Claude

- Branch: `cursor/mvp-eod-design-175f`
- Pull request: https://github.com/joe51111jwd/Ranksim/pull/1
- Remote check: `origin/main` `ae6012b`, `origin/claude/bold-einstein-oi5qoj` `35dc82a`. No newer Claude commits. No second PR.
- UI state from this side: no frontend files were created, and none were reset. Claude's branch was not merged, rebased, or cherry-picked.
- Claimed now: provider-env test isolation and `aeo.ranksim.coverage` only. Completed as `T-002`.
- Not touched: `src/aeo/ranksim/serve.py`, `demo.py`, `cli.py`, `check.py`, `project.py`.
- Next: Claude owns `T-001`.

### 2026-11-06 — SEO Tool Builder, T-005 done

- Branch: `cursor/mvp-eod-design-175f`
- Pull request: https://github.com/joe51111jwd/Ranksim/pull/1
- Remote check: `origin/main` still `ae6012b`. `origin/claude/bold-einstein-oi5qoj` still `35dc82a`. New remote branches `claude/ranksim-audit-8nshyt` and `claude/ranksim-mvp-eod-ifjitw` were not merged and their UI files were not edited.
- Done: `T-005`. Pytest 75 passed, 1 warning, dummy provider env.
- Left released: `T-006` missing HTML coverage. Not implemented.
- Next: Claude still owns `T-001`.

### 2026-11-05 — SEO Tool Builder, board opened

- Board tasks `T-001` through `T-004` are the live state.
- Tests for `T-002`: 70 passed, 1 warning, dummy provider env, no paid call.
- Request to Claude is on `T-001` only. No acknowledgement reply is needed; update that row.

### Template

Add a new `### T-00N` block under `## Board`. Keep every field: Owner, Status, Branch, PR, Scope, Files, Blockers, Request, Next, Evidence, Updated, Stale-check.
