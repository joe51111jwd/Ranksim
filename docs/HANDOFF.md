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

## Phase C (after a session's phase B is merged): cross-review and polish

Goal: a finished app James can use for clients on the morning of Nov 14. Every session that finishes phase B gets
phase C work on a fresh branch `claude/rs-<key>-c` from the latest `claude/kind-hopper-lglg98` (ultracode, adversarial
verify, push every 30 minutes, report to the coordinator). Fix what you find in your own files; for another
session's files, fix only clear bugs, keep the edit small and flag it.

| Key | Phase C task |
| --- | --- |
| gsc | Review publish's credential handling and secretbox use. Then add Search Console opportunities: queries with high impressions at positions 8 to 20, shown on Overview and Searches. |
| track | Review infra's storage and job code against cron re-checks and concurrent pulls. Then rank change alerts in the UI (what moved since the last check). |
| discover | Run the merged app on SmartCrew and three competitor sites. Review the audit's issues and comments for accuracy and plain wording; fix false alarms. |
| audit | Review discovery and competitor output on the same sites. Then a "fix the top issues" flow: apply several suggestions to a page in one go in the editor. |
| editor | Review the publish sheet with exportHtml and diff on real pages. Then the editor on a phone and tablet: touch editing, toolbar, outline. |
| predict | Review the whole Test changes flow as a user on SmartCrew pages. Then make confidence and guidance clear and short; check every number's label. |
| publish | Review the GSC OAuth flow and token storage for security. Then a guided "connect your site" flow for WordPress, the most common client platform. |
| client | Review every client-visible screen and the share page for clarity to a non-SEO client. Then a weekly client summary (what changed, what is next) in the share page. |
| infra | Full security review of the merged app, every plugin route included. Then measure and cut cold start and report time on the merged app. |
| shell | Extend the e2e suite to every feature after the merges and run it. Then a visual consistency pass across every plugin UI at desktop and phone width. |

## Hourly coordinator loop

A scheduled task fires into the coordinator session every hour until 15:00 UTC on Nov 14 (10 AM ET, when James's
weekly usage resets). Each run: read notifications; restart any build session that is idle or failed with work left
(the 5-hour usage limit stops all sessions at once); merge finished branches in the merge order with the full suite
after each; when everything is merged and green, deploy to Vercel, check the live site and tell James; hand
finished sessions their phase C task.

## Status at 12:40 UTC, Nov 14

- Every workstream (phase A, B, C and the pre-deploy QA fixes) is merged on `claude/kind-hopper-lglg98` at ff77195.
  992 tests pass, the Playwright end-to-end suite included. All ten build sessions are idle with nothing pending.
- Not yet deployed: the production deploy of ff77195 needs James's approval (the deploy request was interrupted).
  Live https://ranksim.vercel.app still runs 78c5425. Before deploying, read docs/DEPLOY.md (first boot migrates the
  storage format; rollback requires `python -m aeo.ranksim.cloud export-legacy` first).
- Owner actions: SERPER_API_KEY (Google rankings for any site), CRON_SECRET (scheduled re-checks), and for Search
  Console the Google OAuth client per docs/plans/gsc.md "Owner setup guide". Optional RANKSIM_PUBLIC_URL.
- Known limits: prediction accuracy is below the 90% target (forward: new pages 0.61 pairwise); web fonts served
  cross-origin without CORS may fall back in the editor preview; Blob usage about 35% of the Hobby advanced-op cap
  for one owner's normal month.
