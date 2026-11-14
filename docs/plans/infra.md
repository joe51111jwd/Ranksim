# Infra plan: make the hosted app reliable enough for clients

Workstream 9 (`claude/rs-infra`). Owner files: `cloud.py`, `access.py`, `app.py`, `vercel.json`, `tests/test_hosted.py`,
`tests/test_jobs.py`, and after P0 `api/sites.py` plus the new `jobs.py`. New shared helper: `objects.py`.

## Problems today

1. One gzipped tarball holds every project plus the SQLite rank database. Two writers at once: last save wins and the
   other's work is lost. Every save uploads everything.
2. One global lock: only one site can be analyzed at a time across the app (409 "Another site is being analyzed").
3. An analysis lives inside one streaming request. It dies with the request (300 s limit, closed tab, frozen instance)
   and the UI cannot reattach after a refresh or from another instance.
4. Login failures are counted per instance, so the limit does not hold across Vercel instances.
5. `POST /api/v1/fetch` checks addresses before connecting, but DNS can answer differently at connect time (rebinding).
6. Cold start imports LightGBM and friends before the login screen can answer.

## Design

### Object store (`objects.py`)
One small interface (`put/get/head/list/delete`) with three backends: `BlobStore` (Vercel Blob, private), `DirStore`
(local folder, for local runs), `MemoryStore` (tests). Rule everywhere: anything correctness depends on is written
under a new immutable, time-sortable name (`new_name`), and readers list to find the newest. Overwritable names are
only hints. Blob reads of immutable names cannot be stale; lists are not cached.

### Per-project storage (`cloud.py`)
```
ranksim/v2/projects/<slug>/v/<time>-<rand>.tgz   one project folder, immutable, with a manifest and its base version
ranksim/v2/projects/<slug>/HEAD                  pointer hint for that project
ranksim/v2/db/v/<time>-<rand>.db.gz              the rank database, immutable
ranksim/v2/HEAD                                  global hint, changes on every save
ranksim/v2/migrated                              old tarball imported (holds its name)
```
- Save uploads only the projects whose files changed (manifest of sha256, cached by size and mtime) and the database
  only when its file changed. Saves are smaller and independent.
- Load and save are three-way merges at file level against the version this instance last synced: files changed on
  one side only come from that side; a file both sides changed keeps the merging side's copy (the newer work). After a
  write the writer lists again and merges anything that appeared meanwhile (bounded rounds).
- Deleting a project writes a tombstone version (no files), so deletion reaches other instances.
- The rank database stays one shared object because pages and Google results are keyed by URL and search and are
  reused across projects. Splitting it per project would change every caller of `ui.database` across most
  workstreams. Instead it is merged row by row (`merge_sqlite`) against the last synced copy: natural-key tables by key
  (newer timestamp wins a real conflict), append-only tables by row content, `rs_serp` per search as a group (newest
  pull wins), and the hash-chained prediction ledger stays one valid chain (unsaved local rows are re-chained after
  the other side's new rows; only rows that were never saved can be re-hashed).
- Migration: the first refresh that finds no new-format data imports the newest old tarball (`ranksim/state/`),
  saves it in the new format and writes `ranksim/v2/migrated`. Old tarballs are never deleted, so a rollback finds them.
  Gap: during a rollout, an instance still running the old code could save an old-format tarball after the migration;
  that save would not be imported. Keep the rollout window short (one deploy, no traffic split).
- Kept: immutable versions, pointer is a hint, list to confirm. Old versions are pruned only beyond the newest three
  and only when ten minutes old, so a reader mid-download still finds them.

### Jobs (`jobs.py`, wired in phase B in `api/sites.py`)
- A job has an id, kind, lock key, status (queued, running, paused, done, error), events with sequence numbers, a
  checkpoint, a result. Its state is persisted as immutable snapshots under `ranksim/v2/jobs/<id>/`, throttled, with a
  heartbeat at least every 10 s. Any instance can read it.
- `GET /api/v1/jobs/{id}?cursor=N` returns events since N plus status. A job whose heartbeat is older than 45 s is
  reported stalled and resumable.
- Per-site locks across instances: immutable lock entries under `ranksim/v2/locks/<hash(key)>/`; the earliest entry
  of a live job wins. Starting an analysis of a site that is already running attaches to the running job instead of
  failing. Different sites run at the same time.
- Long work checks a time budget (default 240 s of the 300 s limit). Before the budget runs out it saves a checkpoint,
  the project and database are saved, and the job becomes paused. `POST /api/v1/jobs/{id}/resume` continues it in a
  new request, on any instance. Crawls resume without refetching pages already fetched in the job (`ResumableFetch`
  serves them from the stored page).
- The start request still streams events (same UX as today) while the job is also polled and reattachable.

### Crawler (`aeo/crawl/`)
Connect and read timeouts plus an overall cap, retries with backoff and jitter on connect errors, timeouts, 429 and
502 to 504 (Retry-After honored, capped), every redirect hop recorded and re-validated with loop detection, nested
sitemap indexes, gzip sitemaps and robots.txt `Sitemap:` lines, a `script_drawn` flag for pages whose crawlable HTML
is nearly empty because scripts draw them, a clear `RanksimBot/1.0` user agent. Robots respected as today.

### Security
- SSRF: the connection itself goes only to a vetted public address (a guarded network backend checks every address
  at connect time), which closes DNS rebinding; private, loopback, link-local, metadata, CGNAT, IPv4-mapped IPv6 and
  numeric-encoding tricks are refused on every redirect hop.
- Login rate limit across instances: each failure is an immutable object (hashed client only, never the password);
  limits per client and global, with progressive back-off; falls back to the in-memory limit if storage fails.
- Session revocation: a password change changes the signing key (everyone signed out, tested). Cookies now carry an
  issue time, and `Access.revoke_all()` signs out every session on every instance ("sign out everywhere").
- CSP, CSRF, cookie flags and dependencies reviewed; findings in the phase A report.

### Performance
Measure `import app` and first requests; make LightGBM and other heavy imports lazy so the shell and login answer
without loading the model stack.

## Tests
`tests/test_hosted.py` (storage: concurrent saves on two projects, two writers on one project, deletion, database
merge incl. ledger chain, migration from the old format, nothing uploaded when nothing changed), `tests/test_jobs.py`
(reattach from another instance, lock behavior, stale takeover, pause and resume without refetching),
`tests/test_crawl_robust.py` (SSRF incl. rebinding and redirects, retries, sitemaps, script-drawn pages),
`tests/test_access.py` (rate limits across instances, revocation, password change).

## Phase B (after P0)
Convert analyze and pull in `api/sites.py` to jobs with per-site locks and reattach, add the jobs routes, smallest
possible edit to the open-URL flow in `js/app.js` for job polling (flagged as a shell-session file), Playwright check
against a local server.

## Owner actions
None required for storage or jobs: the existing `BLOB_READ_WRITE_TOKEN` is used. Optional `RANKSIM_JOB_BUDGET_SECONDS`.

## Status (phase B done)
- Analyze and pull are jobs (`api/sites.py`), keyed `site:<domain>`. The NDJSON stream is unchanged apart from a
  `job` id on the start line and a `paused` line; posting again resumes. New routes: `GET /api/v1/jobs`,
  `GET /api/v1/jobs/{id}?cursor=N`, `POST /api/v1/jobs/{id}/resume`.
- A paused job keeps Google's answers in its checkpoint and stores pages already fetched, so a resume asks Google
  once and fetches each page once. A resumed analysis skips the site read.
- Not paused mid-step: the site read (about 30 pages) and model training. A very slow site read could still reach
  the 300 s limit; the job then shows as stalled after 45 s and the next post takes it over from its checkpoint.
- After a refresh the progress timer restarts from the refresh (cosmetic, `js/app.js`).
- Browser check (Playwright, local server, slow fake sites): two sites analyzed at once, no 409; a refresh
  mid-analysis reattached and finished; a paused analysis was re-posted by the app and finished.

## Phase C: Blob budget, report speed, security

Vercel Hobby Blob: 10,000 simple (head, get on a cache miss) and 2,000 advanced (put, list, copy) operations a
month; Blob is blocked for 30 days past a cap; deletes are free.

- Storage is a numbered chain of saves (`ranksim/v2/c/<n>`). A save is one create-only put of the next number, which
  works as compare-and-swap; the manifest rides inside each save; a pointer hint is written every 8 saves. The rank
  database goes up as row-level deltas, with a full snapshot every 15 deltas.
- Ordinary requests make no Blob call. An instance looks for a newer save (one get) on a cold start, before a write,
  every 5 minutes, or when the `rs_sync` cookie says it is behind (read-your-writes across instances).
- Jobs: the lock is one create-only put with a deadline; the final or paused record rides on the job's own save.
- Login: wrong passwords cost nothing; a global lockout is published at most once a day per instance; sign out
  everywhere reaches other instances within 5 minutes (at once on the instance that did it and on cold instances).
- Measured (tests/test_budget.py, counting store): an hour of clicking with 10 writes 20 simple / 11 advanced; one
  analysis 3 / 2; one pull 3 / 2; one cron save 1 / 1; idle 0; 200 wrong passwords 0.
- Report: page facts cached by content hash (warm report 0.75 s to 0.03 s on a 92-page test project). Public demo
  builds on first use, not at startup.
- Security review of every route: signed-out requests never touch storage, share-link views never save, the guard
  middleware runs outermost, cross-site form posts are refused, more secrets scrubbed.
