# Ranksim build plan (Nov 14, 2026)

This is the contract every build session works from. Read it fully before writing code. Also read
`docs/MISSION.md` (hard rules) and `AGENTS.md` (setup, tests).

Coordinator: session `session_01JdxycpoqXYfgU7hXsH7ary` on branch `claude/kind-hopper-lglg98`. It owns
integration, merging, deploys and the live site. Build sessions never deploy, never merge to `main`, never
touch the live site at https://ranksim.vercel.app, and never use its password.

## 1. What James asked for (the product spec)

James runs SEO for clients. He needs this working for clients as fast as possible.

1. **One input.** The user types a site or page address. Everything else is automatic. No CSV uploads,
   no user-supplied Google key. "We" get the data (server-side provider keys set by the owner).
2. **Google Search Console** is the only thing a user connects, and it is a simple on/off.
3. **Searches (keywords) auto-pulled.** The searches a site shows up for, found automatically.
4. **Competitors auto-pulled.** Who takes the top 10 for those searches, found automatically.
5. **Issues with automatic comments.** "Look at all the issues. These are specific issues." Each part of
   the page gets an automatic comment that points at the exact element and says what to change.
6. **Where you rank.** Positions per search, and charts of everything that ranks.
7. **Edit and test.** Change any page in a visual editor and test how the change would rank before
   publishing. Predictions are labeled PREDICTED.
8. **Edit anything, publish only what you own.** Any URL opens in the editor (yours or a competitor's).
   Pushing a change live requires proving ownership of the site.
9. **Fast feedback.** Show results as soon as they exist. Typing a URL shows the live page immediately.
10. **Client ready.** James uses it for client sites and shows results to clients.

## 2. Design language (fixed, do not change)

Bento layout from James's reference video. Warm off-white page `--bg #f6f6f3`, soft gray tiles
`--tile #ededE8`, white cards `--card #fff`, one blue accent `--blue #3358eb`, black tiles `--dark #111`.
Font: Geist (sans) and Geist Mono, bundled. No serif fonts. No gradients. No glass or blur. No chat
bubbles. Generous spacing, minimal copy, short plain sentences. No em dashes or en dashes anywhere in UI
copy (a test checks this). Everything must work at phone width (16px gutters, no horizontal scroll).

## 3. Hard rules (from docs/MISSION.md, enforced in review)

- Never fake results. Label predictions PREDICTED. Never present a training score, a same-snapshot score,
  or a test-tuned score as accuracy.
- Select features and models on training data only. Held-out and forward data are scored once.
- No merge to `main`, no production deploy. No paid purchases. Never commit or print API keys or secrets.
- The test suite must pass with no real keys (`python -m pytest`). Use fakes for every network call.
- Do not upload `smartcrew/` or private corpora anywhere.

## 4. Architecture contract

The coordinator is landing a foundation refactor (call it P0) on `claude/kind-hopper-lglg98` that makes
the code plugin-shaped so ten sessions can build in parallel without editing the same files. Until P0 lands,
put new code in NEW files only. After P0 lands you get a message; rebase onto it and wire your feature in
through the hooks below.

### 4.1 Backend layout after P0

```
src/aeo/ranksim/
  studio.py              # register(app, ui): shell, assets, then api.* routers, then plugins
  api/                   # existing Studio JSON routes split by area, each with register(app, ui)
    session.py           # /api/v1/session, login, logout
    projects.py          # list, create, delete, settings, import, setup, overview, report
    sites.py             # /api/v1/sites (analyze stream), _analyze, pull stream, _run_pull
    editor.py            # page, fetch (any URL), compare, test, tests history
    common.py            # _error, _json_body, _text, _check_header_csrf, _not_public, google_status...
  site.py                # SitePage, page_facts, read_site (crawl of the user's site)
  discovery.py           # brand_of, brand_tokens, discover_searches
  competitors.py         # share_of_top10, pick_competitors
  audit.py               # audit(), issue builders, CHECKS registry
  report.py              # build_report() (calls plugins.extend_report at the end)
  autopilot.py           # thin re-export of the names above, kept for compatibility
  plugins/
    __init__.py          # discovery + hook calls (see 4.2)
    <feature>.py         # one module per feature, owned by one session
```

### 4.2 Backend plugin hooks (`aeo.ranksim.plugins`)

Every module in `src/aeo/ranksim/plugins/` is imported at startup (sorted by name). A module may define any
of these functions. All are optional. A plugin that raises is logged and skipped, never fatal.

| Hook | Signature | Called |
| --- | --- | --- |
| `register` | `register(app, ui) -> None` | once, after core routes. Add FastAPI routes here. |
| `extend_report` | `extend_report(report: dict, *, project, meta, database, ctx, ui) -> None` | end of `build_report`. Add keys under your own namespace, e.g. `report["gsc"] = {...}`. |
| `discovery_sources` | `discovery_sources(*, project, pages, ui) -> list[dict]` | during analysis. Return `{"q": str, "weight": float, "source": str}` rows that discovery merges into the search list. |
| `audit_checks` | `audit_checks() -> list[callable]` | by `audit.audit`. Each check is `check(ctx) -> list[issue dict]` (issue shape in 4.4). |
| `after_pull` | `after_pull(*, project, report, ui) -> None` | after every pull or analysis finished. |
| `settings_schema` | `settings_schema() -> dict` | optional, describes plugin settings shown in Settings. |

Helpers available to plugins: `plugins.call_all(name, **kwargs) -> list`, `plugins.collect(name, **kwargs)`
(flattens lists). Routes use the shared helpers in `api/common.py` (CSRF header check, JSON body limits,
`_not_public` for the anonymous demo, `_error`). Every write route needs the `x-ranksim-csrf` header.
Every route except the shell, assets, session and login requires the signed-in owner (middleware does it).

Per-project plugin data: store it under the project folder, `project.dir / "plugins" / "<feature>" /`.
Cloud storage mirrors the projects folder, so it persists on Vercel automatically.

### 4.3 Frontend layout after P0

No build step. Native ES modules, `script-src 'self'` CSP, so no inline scripts and no CDN scripts.

```
src/aeo/ranksim/static/
  studio.html            # shell: loads /studio/js/app.js as a module, plus every plugin module
  css/base.css           # design system tokens and shared components
  css/<area>.css         # per-area styles; css/plugins/<feature>.css auto-included
  js/core.js             # h(), icon(), api (get/post/stream), state S, router, toast, sheets, helpers
  js/hooks.js            # on(name, fn), call(name, ctx), collect(name, ctx); registerScreen, registerTile...
  js/app.js              # boot, routing, top bar, login, home, open-URL flow
  js/screens/overview.js, issues.js, searches.js, competitors.js, settings.js
  js/editor/preview.js   # cleanDoc, frameSource, settleStreamedContent, revealHidden, serialize
  js/editor/editor.js    # editor screen, side panel, drafts
  js/editor/test.js      # Test changes, result tiles
  js/editor/publish.js   # Publish sheet
  js/plugins/<feature>.js  # auto-loaded, one per feature
```

Frontend hooks (`js/hooks.js`), all optional:

- `registerScreen(key, {label, nav, order, render})` adds a screen at `/app/<slug>/<key>`.
- `registerOverviewTile({order, render(report)})` adds a tile to the Overview bento.
- `registerEditorPanel({order, render(ctx)})` adds a tile to the editor side column.
- `registerEditorTool({order, render(ctx)})` adds a toolbar control in the editor.
- `registerSettingsSection({order, render(ctx)})` adds a section to Settings.
- `registerSearchColumn({order, header, cell(row)})` adds a column to the Searches table.
- `on('report-loaded' | 'editor-ready' | 'draft-changed' | 'test-finished', fn)` events.

Server side, the shell lists `js/plugins/*.js` and `css/plugins/*.css` automatically. Adding a file is enough.

### 4.4 Shapes

Issue (audit):
`{id, severity: "high"|"medium"|"low", kind, title, comment, fix, page, search?, element?: {selector, text, attr?}, suggestion?: string, evidence?: [str]}`
`element.selector` is a CSS selector for the exact element in the page HTML, so the editor can pin the
comment on it and "Fix in editor" can jump there. `suggestion` is ready-to-paste replacement text.

Report (`GET /api/v1/projects/{slug}/report`): keep every existing key. Add new data under a namespaced key.

## 5. Workstreams

Each workstream is one session on its own branch `claude/rs-<key>`, branched from `claude/kind-hopper-lglg98`.
"Owns" means only that session edits those files. Shared files: touch only with the smallest possible edit,
and say so in your final message.

| # | Key | Workstream | Owns |
| --- | --- | --- | --- |
| 1 | gsc | Google Search Console on/off: OAuth, property pick, 28-day queries and pages, feed searches | `plugins/gsc.py`, `js/plugins/gsc.js`, `css/plugins/gsc.css`, `tests/test_gsc.py` |
| 2 | track | Search data provider health, cost meter and cap, scheduled re-checks (Vercel cron), rank history over time, SERP features | `plugins/tracking.py`, `js/plugins/history.js`, `css/plugins/history.css`, `tests/test_tracking.py` |
| 3 | discover | Better search discovery (site, SERP related searches, people also ask, competitor titles, plugin sources), intent, competitor discovery and profiles, content gaps | `discovery.py`, `competitors.py`, `js/screens/searches.js`, `js/screens/competitors.js`, `tests/test_discovery.py` |
| 4 | audit | Issues engine depth with exact element, comment, fix and suggestion; comments pinned on the page in the editor | `audit.py`, `js/screens/issues.js`, `js/plugins/annotations.js`, `css/plugins/annotations.css`, `tests/test_audit.py` |
| 5 | editor | Editor power: edits patched onto the original HTML (download keeps scripts), undo and redo, outline, insert FAQ and table and section blocks, diff vs original, saved draft versions | `js/editor/editor.js`, `js/editor/preview.js`, `css/editor.css`, `plugins/drafts.py`, `tests/test_drafts.py` |
| 6 | predict | Prediction quality and guidance: persistence for ranking pages, a draft predictor evaluated on new top-10 entries, test a draft on all its searches, what would move it up | `tester.py`, `whatif.py`, `api/editor.py` test route, `js/editor/test.js`, `tests/test_predict*.py`, new files under `evals/` |
| 7 | publish | Ownership proof (meta tag, DNS TXT, file) and publishing (WordPress, Webflow, Shopify, GitHub, webhook), verify live, history, rollback | `plugins/publish.py`, `js/editor/publish.js`, `css/plugins/publish.css`, `tests/test_publish.py` |
| 8 | client | Client delivery: read-only share links, printable report, CSV export, agency branding, change log | `plugins/share.py`, `js/plugins/share.js`, `static/share/`, `css/print.css`, `tests/test_share.py` |
| 9 | infra | Reliability on Vercel: per-project storage objects, per-site locks so several analyses run at once, resumable long jobs and job status polling, crawler robustness, login rate limits, security review | `cloud.py`, `access.py`, `api/sites.py`, `app.py`, `vercel.json`, `tests/test_hosted.py`, `tests/test_jobs.py` |
| 10 | shell | App shell and charts: Overview charts of everything that ranks, per-search SERP view, home and empty and loading and error states, mobile, accessibility, bento consistency, Playwright end-to-end suite | `js/app.js`, `js/core.js`, `js/hooks.js`, `js/screens/overview.js`, `js/screens/settings.js`, `css/base.css`, `tests/e2e/` |

Cross-stream needs go through hooks, not edits to another stream's files. Examples: GSC queries reach
discovery through `discovery_sources`. Rank history reaches the Overview through `registerOverviewTile`.
Audit comments reach the editor through `registerEditorPanel` and the `editor-ready` event.

## 6. Rules for every build session

1. Ultracode: use workflows for substantive work. Adversarially verify before you call something done.
2. Work only on your branch `claude/rs-<key>`. Commit often with clear messages. Push with
   `git push -u origin claude/rs-<key>`.
3. Tests: `python -m pytest` must pass with no keys. Add tests for everything you build. Mock every network
   call (see `tests/test_hosted.py` `FakeGoogle`, `tests/test_project.py` `FakeCrawler`).
4. Run the app locally to see it work (`python -m aeo.ranksim serve` or the snippet in section 7) and drive it
   with Playwright. Chromium is preinstalled; `require('playwright')` works.
5. Never deploy, never touch the live site, never merge to `main`, never print or commit secrets.
6. Honesty: label predictions PREDICTED, state coverage, never invent data. If a feature needs an owner
   action (an API key, an OAuth client), build it fully, make it light up when configured, and show a clear
   "not set up yet" state otherwise.
7. When finished, push, then report to the coordinator with the `send_message` tool (session id above):
   what you built, files touched (flag any shared file), tests added and passing, what needs an owner
   action, known gaps. Keep it short and plain.

## 7. Running locally

```bash
pip install -e ".[dev]"
python -m pytest
RANKSIM_PASSWORD=localtest RANKSIM_SECRET=localsecret RANKSIM_DATA_DIR=/tmp/rs-data \
  python -m uvicorn app:app --host 127.0.0.1 --port 8765
```

Private mode needs the password `localtest`. `POST /api/v1/demo` builds the synthetic demo project.
To try a real site without a Google key, import a SERP CSV (`query,position,link`) such as
`evals/forward-2026-11-09/forward_serp.csv` for SmartCrew through `POST /api/v1/projects/{slug}/import`,
then `POST /api/v1/projects/{slug}/pull`.
