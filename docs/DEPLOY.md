# First production deploy: storage migration runbook

For whoever promotes the integration branch to production (https://ranksim.vercel.app). It covers what the first
boot does to the live Blob store, how to check it worked, how to roll back without losing work, and what not to do.

## What changes in storage

The live deployment keeps everything in one gzipped tarball under `ranksim/state/<time>-<rand>.tgz`, with
`ranksim/HEAD` naming the newest. The new code keeps a numbered chain of saves under `ranksim/v2/c/<n>.tgz` (each holds
the projects that changed, a row-level delta of the rank database or a full snapshot, and the manifest) and a pointer
hint `ranksim/v2/HEAD`. Design and Blob operation budget: the docstring of `src/aeo/ranksim/cloud.py`.

The new code never deletes anything under `ranksim/state/`. The old code never reads `ranksim/v2/`. That is what makes
both the migration and a rollback safe.

## Before you deploy

1. Environment variables (Production):
   - `RANKSIM_PASSWORD` (as today) and `BLOB_READ_WRITE_TOKEN` (as today).
   - `RANKSIM_SECRET`: required. At least 32 random characters (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).
     It signs session cookies, the CSRF token, share links, and encrypts stored Google and publishing tokens. Without
     it, sessions are keyed by the password alone, share links break across instances, and Search Console and
     publishing cannot store tokens. Setting it for the first time signs everyone out once. Changing it later signs
     everyone out, breaks every share link, and forces reconnecting Google and publishing targets.
   - `CRON_SECRET`: required for the daily re-check cron in `vercel.json` (Vercel sends it as a Bearer token).
   - Google results key (`SERPER_API_KEY`, or `SERPAPI_KEY`, or the DataForSEO pair) as today.
   - Optional: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` (Search Console), `RANKSIM_MONTHLY_SEARCH_CAP`,
     `RANKSIM_JOB_BUDGET_SECONDS` (default 240).
2. Preview deployments: make sure Preview does NOT get the production `BLOB_READ_WRITE_TOKEN` (connect a separate Blob
   store to Preview, or none). A preview running the new code against the production store migrates it early. That is
   still safe (old-format saves made afterwards are merged for 14 days), but it is easier to reason about one migration
   at deploy time.
3. Write down the name of the newest `ranksim/state/` object (Vercel dashboard, Storage, the Blob store). Do not click
   around the store more than needed: dashboard browsing counts as advanced operations on Hobby.
4. Deploy in one step to 100 percent of traffic (no rolling release or traffic split), so old and new code overlap only
   for the minutes old instances keep serving.

## What the first boot does

On the first request that needs storage (any signed-in request), one instance:

1. Finds no chain (`ranksim/v2/c/000000000001.tgz` missing) and lists `ranksim/state/` once (one list, once per store,
   ever).
2. Downloads the newest old tarball and unpacks it into its `/tmp` working folder: projects and `rank.db`.
3. Writes save number 1 with every project and a full snapshot of the database, records the old tarball's name and
   etag in the manifest, writes `ranksim/v2/HEAD`, and saves `ranksim/v2/legacy-base.json` (what the import brought,
   per project, for later merges).

Expect the first request to take as long as downloading and re-uploading the data (seconds for tens of MB). If several
instances boot at once, they race to create save number 1. That is a create-only put: one wins, the others merge its
save and continue. Nothing is lost or duplicated.

For 14 days after that, each instance checks the old pointer (`ranksim/HEAD`, one head) at most every 30 minutes. If an
old instance saved in the old format after the migration, that save is merged in: only what the old instance changed
since the import is applied, newer edits on the new side are kept, and importing the same tarball twice changes nothing.

## How to verify

1. Sign in. The projects list shows every project the old deployment had.
2. Open a project's report. Search rows, competitors and issues show as before.
3. From a machine with the production token (`vercel env pull`), run:
   ```bash
   python -m aeo.ranksim.cloud status
   ```
   It prints the newest save number (1 or more), the projects in storage, and the imported old tarball's name, which
   must match the name you wrote down.
4. Run one analysis of a small site. It streams progress and ends with a report. Refresh mid-analysis: it reattaches.
5. In the Vercel dashboard (Storage, Blob, Usage), operations rise by a handful, not hundreds. Expected steady use for
   one owner is under half of the Hobby caps (see the phase C notes in `docs/plans/infra.md`).

## How to roll back safely

**A rollback loses every change made on the new deployment unless you run `export-legacy` first.** The old deployment
only reads `ranksim/state/`, so without step 1 it starts from the state at the moment of migration: projects,
analyses, pulls, edits and settings made on the new deployment do not appear in it (they stay in `ranksim/v2/` and come
back if you deploy the new code again, but the old deployment never sees them).

1. Always first, before touching Vercel: hand the newest state back to the old format:
   ```bash
   python -m aeo.ranksim.cloud export-legacy
   ```
   It writes a new `ranksim/state/<time>-<rand>.tgz` with every project and the rank database, and points `ranksim/HEAD`
   at it, exactly as the old code packs them. Tested against the old code (`tests/test_hosted.py`
   `test_export_legacy_hands_the_newest_state_back_to_the_old_format`, plus an end-to-end check with the live commit's
   `cloud.py`).
2. Roll back in Vercel (Instant Rollback to the previous production deployment).
3. Sign in and check the projects and a report.

Rolling forward again later: if that happens within 14 days of the first migration, the new code merges what the old
deployment saved in the meantime by itself. After that, run `python -m aeo.ranksim.cloud import-legacy` once (with the
production token) right after the deploy.

## What not to do

- Do not delete anything under `ranksim/state/` or `ranksim/v2/` by hand. The old tarballs are the rollback; the chain
  is the live state. Old saves nobody references are deleted by the app (deletes are free).
- Do not overwrite `ranksim/v2/c/*` objects. They are immutable; a save is a create-only put of the next number.
- Do not change `RANKSIM_SECRET` or `RANKSIM_PASSWORD` during the deploy unless you mean to sign everyone out (and, for
  the secret, break share links and stored tokens).
- Do not point a local or preview app at the production token except to run the `status`, `export-legacy` or
  `import-legacy` commands.
- Do not script loops of `vercel blob list` or dashboard browsing on Hobby: each listing is an advanced operation, and
  Blob is blocked for 30 days past the monthly cap.
- Do not run a rolling release that keeps old and new production versions side by side for days.

## Bundle and limits (checked 2026-11-14)

- Every runtime file is committed and none is ignored: `static/js/**` (app, core, hooks, charts, screens, editor,
  plugins), `static/css/**` (and `css/plugins/**`), `static/fonts/**`, `static/share/**`, `studio.html`, every module
  under `api/` and `plugins/` (drafts, gsc, predict_impact, publish, share, tracking), and `vendor/libgomp/libgomp.so.1`.
  A copy of the deploy tree with everything `.vercelignore` and `excludeFiles` drop removed (5.3 MB) boots in private
  and public mode. Every script, style and font the shell and its modules import answers 200. All six plugins load
  with none failed, and the share page assets answer.
- Dependencies installed fresh for Linux (`pip install --target`) take 324 MB uncompressed (scipy 141 MB with its
  libs, numpy 70 MB, sqlalchemy 28 MB, cryptography 16 MB, lxml 12 MB, lightgbm 10 MB). The Vercel Python runtime
  limit is 500 MB uncompressed. The only new dependency since the live build is `cryptography`.
- `httpx` is pinned below 0.29 and `vercel-blob` below 0.7: the crawler's SSRF guard hooks httpcore 1.x internals and
  the storage code is tested against the vercel-blob 0.6 API. Re-test before raising either bound.
- `/tmp` (512 MB): the database is kept once plus a compact base (about half its size); no sync or save writes a
  second full copy. With 30 MB of projects: about 135 MB at rest and at peak with a 70 MB database, 208 MB with 120 MB
  (`tests/test_disk.py`). Leftover temp files from a killed instance are removed at start-up; a full disk is retried
  after cleanup, then the local copy is reloaded.
- The function keeps `maxDuration` 300. Long analyses pause at 240 s and continue in the next request.
- `vercel.json` keeps the daily cron `17 9 * * *` (Hobby allows one run a day).
