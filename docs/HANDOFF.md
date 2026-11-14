# Ranksim handoff (Nov 14, 2026)

Read this, then `docs/BUILD_PLAN.md` (the contract), `docs/MISSION.md` (hard rules) and `AGENTS.md` (setup).

## Where things are

- Live app: https://ranksim.vercel.app (Vercel project `ranksim`, team `team_A9lGsQGuDKsMAfeYLhN3j6eM`,
  project id `prj_JX9BpoJyoJxOyEPZIVxfbUui9TdS`). Private: one owner password (`RANKSIM_PASSWORD`). Data persists
  in a private Vercel Blob store (`BLOB_READ_WRITE_TOKEN`). Deployed from commit 78c5425 (before the P0 refactor).
- Old production at https://ranksim-demo.vercel.app is untouched.
- Integration branch: `claude/kind-hopper-lglg98`. It holds the P0 foundation at e0c256c. All 392 tests pass.
- The live SmartCrew project (`smartcrew`, www.smartcrewhq.com) holds 30 searches with the real Google results
  from `evals/forward-2026-11-09/forward_serp.csv` (seen Nov 9), 184 stored ranking pages and a trained engine.
- Coordinator session: `session_01JdxycpoqXYfgU7hXsH7ary`. It merges, deploys and reports to James.

## The P0 foundation (landed)

- Frontend: `static/js/` ES modules (core, hooks, app, screens/*, editor/*), `static/css/` split, plugin JS and CSS
  auto-included from `js/plugins/` and `css/plugins/`. Hook API documented at the top of `static/js/hooks.js`.
  Before and after screenshots matched pixel for pixel.
- Backend: `api/` routers (common, session, projects, sites, editor), `autopilot.py` split into site, discovery,
  competitors, report and audit, and `plugins/` with hooks register, extend_report, discovery_sources,
  audit_checks, after_pull, settings_schema and csp, plus helpers data_dir, workspace_dir, save, json_error,
  login_exempt and csrf_exempt routes. All documented in the `aeo.ranksim.plugins` docstring.

## The ten build sessions

Each works on its own branch `claude/rs-<key>` from the integration branch. Phase A (core logic in new files) is
done and pushed for all ten. Phase B (rebase on P0, wire into hooks, UI, browser checks) started at 03:12 UTC,
stopped at 03:13 UTC when every session hit the 5-hour usage limit, and was restarted after the 04:30 UTC reset.

| # | Key | Session id | Phase A head | Phase A result |
| --- | --- | --- | --- | --- |
| 1 | gsc | session_01JdxycqqJbyirHeF7AYgq9x | 95c2a79 | `secretbox.py` (AES-GCM, key from RANKSIM_SECRET of 32+ chars), `plugins/gsc.py` (OAuth, refresh, revoke, property match, 28 days of Search Analytics, hooks), 26 tests. Adds `cryptography>=42`. |
| 2 | track | session_01Jdxycrp4EHHJo6KHkXvQ7r | beea79b | `plugins/tracking.py` (monthly search counter, cap `RANKSIM_MONTHLY_SEARCH_CAP` default 500, 6 h reuse, provider status, per-project re-check schedule, `/api/v1/cron/recheck` with Bearer CRON_SECRET, rank history), SERP features captured in `serp.py` and stored at `google.json searches[i].features`, cron in `vercel.json`. 38 tests. |
| 3 | discover | session_01JdxycrWiwTNWXHFEGYt99a | d01a619 | `discovery_next.py` (sources, intent, scoring, cap 20), `competitor_profiles.py` (competitor vs directory vs platform, profiles, content gaps). 33 tests. |
| 4 | audit | session_01Jdxycro8RXUXDRyubBP7pB | 19db010 | `audit_rules.py` (about 40 issue kinds, exact selectors, comment, fix, suggestion, impact and effort). 37 tests. |
| 5 | editor | session_01JdxycqcZQ19jq1NDVchQF3 | 301f755 | `js/editor/roundtrip.js`, `model.js`, `history.js`, `diff.js`, `blocks.js` (edits applied to the original HTML, scripts kept), `plugins/drafts.py`. 56 node tests plus 11. |
| 6 | predict | session_01JdxycqumVjfHSbHQLyjYZK | 4e365e8 | `predict.py`, `predict_eval.py`, `predict_service.py`, `evals/predict-2026-11-14/`. Forward, scored once: new vs new 92/151 (0.61), full list anchored 1056/1341 (0.79) vs persistence 931/1341 (0.69). Pages refetched Nov 14. Not 90%. |
| 7 | publish | session_01Jdxycr8FqA6wYXhpZ9hk7n | 197bf9f | `publishing/` (ownership by meta tag, .well-known file or DNS TXT; WordPress, Webflow, Shopify, GitHub PR, signed webhook; history and rollback), `plugins/publish.py`. 57 tests. Needs `secretbox` from gsc. |
| 8 | client | session_01JdxycnrjbYx44Tqr9oLoqz | c22f86b | `plugins/share.py` (signed revocable links at `/r/<token>`, CSV, branding, change log, print view `/report/<slug>`), `static/share/`. 23 tests. |
| 9 | infra | session_01Jdxycr4Am2fFHu3rSo9YvR | 1ad07ee | `cloud.py` per-project storage with merge and migration, `objects.py`, `jobs.py` (persisted jobs, locks, resume), crawler SSRF guard and retries, shared login limits, lazy LightGBM. |
| 10 | shell | session_01JdxycsK1krQRF4CnXttd8D | 517aae7 | `js/charts.js` (SVG charts, 12 node tests), `tests/e2e/` Playwright suite with fakes. |

## Cross-session decisions already made

- Workspace-wide plugin data goes in `plugins.workspace_dir(ui, feature)` = `<projects root>/_plugins/<feature>/`
  (GSC account, tracking usage, agency branding). No dot folders. Infra must persist `_plugins/`.
- Public routes (share links, the cron) use `endpoint.login_exempt = True` and check their own token. Share pages
  set their policy through the `csp(path)` plugin hook.
- SERP features shape (track) is read by discover from `searches[i].features`.
- The editor adds `ctx.exportHtml()` and `ctx.diff()` for publish. The audit annotations plugin handles
  `?focus=<selector>` in the editor through `ctx.select`.
- `report.py` is unowned: discover and audit each make one small flagged edit there.

## Merge order for the coordinator

1. infra (storage and jobs underneath everything), 2. gsc (secretbox), 3. track, 4. discover, 5. audit,
6. editor, 7. predict, 8. publish, 9. client, 10. shell. Run the full suite after each merge. Then ask the shell
session to extend e2e across all features, run it, deploy, seed and check the live site.

## Owner actions James needs to take

- `SERPER_API_KEY` in Vercel (project `ranksim`, Environment Variables). Without it only SmartCrew has Google data.
- `CRON_SECRET` in Vercel for scheduled re-checks.
- For Search Console: a Google Cloud OAuth client (Web application), Search Console API enabled, redirect URI
  `https://ranksim.vercel.app/api/v1/gsc/callback`, then `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`.
- `RANKSIM_SECRET` is already set (43 characters), which the encryption needs.

## Known limits

- Pages drawn entirely by scripts (SmartCrew's homepage) show the text Google reads, not their full design.
- Prediction accuracy is below the 90% target (see predict above). Every prediction is labeled PREDICTED.
