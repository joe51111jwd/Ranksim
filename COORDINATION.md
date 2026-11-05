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
