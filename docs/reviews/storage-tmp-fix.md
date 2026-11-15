# Review of the /tmp storage fix (1ae2c42)

Independent review by the coordinator: four lenses (data loss, disk bound, concurrency, Blob and errors), then one skeptic per finding that tried to refute it. Seven findings survived, and the skeptic reproduced each one with code. The refuted ones are listed at the end for reference.


## [high] data-loss: Wipe unlinks the db and project folders under other threads that hold no lock; their writes go to orphaned files and are reported as successful (src/aeo/ranksim/cloud.py:221)
SEQ: Fluid compute runs requests concurrently in threads. Request handlers and job threads write rank.db (SQLAlchemy) and projects/ without holding the cloud lock: the middleware holds it only inside refresh() and save(). 1) Thread J (a pull job, or a write request's handler) has a checked-out connection and is writing pages and project files. 2) Thread R's refresh hits disk-full twice and runs _wipe(). _dispose_engines only closes idle pooled connections, then rank.db, -wal and -shm are unlinked and projects/ is rmtree'd. 3) J's connection keeps committing into the unlinked inode with no error. The cold sync puts a new rank.db at the path. J's later file writes fail, or land in folders that the cold sync recreates. 4) J ends: on_finish runs record_job() and save(). _dirty() sees none of J's rows, because they live in the orphaned inode.
OUT: The job's or request's writes are lost silently. J's own connection still reads its rows back, so it reports success and the job is recorded as done, but none of its rows reach the real db or Blob. A write request gets 200 without the 'unsaved' header.
FIX: Never delete files that app threads may have open. Limit the wipe to the cloud bookkeeping, and let the cold sync merge storage into the existing working copy:

    def _wipe(self) -> None:
        """The disk is still full after the sweep. Drop the base copy and the bookkeeping, never the working db or project files: app threads write those without this lock. The next sync is a cold one that merges storage into the working copy."""
        shutil.rmtree(self._meta_dir, ignore_errors=True)  # base.db (+ -wal/-shm), tmp/, state.json: frees one full db copy
        self._sweep()
        self._seq, self._version, self._manifest = 0, None, _empty_manifest()
        self._installed, self._db, self._db_clean = {}, {"snapshot": None, "applied": []}, None
        self._hash_cache.clear()

How the existing code then handles it:
- With _installed == {}, _cold_sync merges every project with base {}. _merge_files keeps local files that differ and takes files that exist only in storage.
- With _db['snapshot'] None and no base.db, _merge_full runs merge_sqlite(db_file, None, theirs) in place. Local unsaved rows are kept, in-flight connections keep writing to the real file, and the snapshot becomes the new base by rename.
- _db_clean is None, so the next save uploads the local rows as a delta and the local files as changed projects.

Also update the docstring of _sync_recovering and the module Disk paragraph, so they no longer say the local copy is dropped.
REPRO: True | The defect is real, and I reproduced it. Request handlers and job threads write rank.db (through SQLAlchemy sessions) and projects/ without holding the cloud lock. studio.py's middleware takes the lock only inside cloud.refresh() and cloud.save(), and a pull job runs in its own thread and saves only in on_finish. _wipe() runs under the lock, but it disposes only idle pooled connections, then unlinks rank.db, -wal and -shm and rmtrees projects/ while those writers are running.

The window is wider than the claim says. A checked-out connection is not needed:
1. Write request W runs refresh(force=True).
2. Request R takes the lock. Its sync hits a full disk, sweeps, retries and hits a full disk again.
3. W's handler commits a row and writes a project file, then W's middleware calls save(), which blocks on the lock.
4. R runs _wipe() and the cold sync, then releases the lock.
5. W's save() sees _dirty() == False, because the new db is clean and _installed matches. It returns None and raises no CloudError, so the response is 200 without the x-ranksim-storage: unsaved header.

W's row and file are gone locally and were never uploaded. A pull job loses every page it stored before the wipe in the same way. It keeps running on the new db, its on_finish save uploads only the rows written after the wipe, and the job is recorded as done.

Experiment /tmp/claude-0/-home-user-Ranksim/f6dcc9b5-8d3e-56d1-9583-6296cdb3fbf1/scratchpad/review/v4/exp_wipe_race.py uses the app's real WAL engine and two threads. Output: 'W save returned: None | W header: None', 'B app now sees: [from-a, one]', 'B w-file exists: False', 'store rows: [from-a, one]', 'store has w-file: False'.

Experiment /tmp/claude-0/-home-user-Ranksim/f6dcc9b5-8d3e-56d1-9583-6296cdb3fbf1/scratchpad/review/v4/exp_wipe_inflight.py keeps a SQLAlchemy session mid-transaction across the wipe. Its commit raises no error (WAL mode opens no rollback journal, so SQLite's moved-file check does not fire), but the row goes to the unlinked inode, save() returns None, and the store lacks the row.

One detail of the claim is slightly off. With SQLAlchemy the job does not read its row back, because the next checkout gets a fresh connection to the new file. The core outcome holds: the write succeeds, is lost silently, and is reported as saved.

The design accepts dropping changes that were already flagged as unsaved ('Unsaved local changes cannot be saved on a full disk either'). It does not cover writes in flight or awaiting their save, which bypass the app's 'unsaved' signal.


## [medium] data-loss: base.db is now advanced by merging the delta, so a newly added column never enters it: column-only edits are marked saved without uploading, and other edits upload the column blank (src/aeo/ranksim/cloud.py:765)
SEQ: 1) A deploy adds a column to an existing table, the way store._ensure_ledger_columns runs ALTER TABLE ... ADD COLUMN on every instance's rank.db. base.db, last replaced by an older full snapshot, does not have the column. 2) Instance A saves an ordinary delta. In the old code base.db then became a copy of the working db, column included. Now merge_sqlite(base, minus, plus) only merges the columns base already has, so base never gains the column until the next full snapshot (up to 15 saves later). 3) A user edits only the new column. make_delta compares only columns both sides have, so it returns 0 rows, and _write_save sets _db_clean = sig_before and raises _NothingToSave. 4) Another instance edits the column and another field of a row. plus/minus are created with the working schema but filled only with the shared columns, so the new column arrives as its DEFAULT and readers overwrite their value with ''.
OUT: Values in the new column are marked saved but never uploaded, and other instances' deltas blank them. They live only in one instance's /tmp until a full snapshot happens; if that instance is killed first, they are lost. If the column is in rs_predictions, a hashed field arrives blank and the ledger chain no longer verifies. The old code propagated these values after the first delta.
FIX: The fix: before base.db is used as the ancestor, add to it every column that the working db has and it lacks, with the same type and default. That is the value the deploy's ALTER gave every existing row on every instance. In cloud.py:

```python
def _add_missing_columns(target: Path, source: Path) -> None:
    if not Path(target).is_file() or not Path(source).is_file():
        return
    conn = sqlite3.connect(str(target), timeout=30)
    try:
        conn.execute("ATTACH DATABASE ? AS s", (str(source),))
        ours = _tables(conn, "main")
        for table in _tables(conn, "s"):
            if table not in ours:
                continue
            have = {row[1] for row in conn.execute(f"PRAGMA main.table_info({_q(table)})")}
            for _cid, name, kind, _nn, default, _pk in conn.execute(f"PRAGMA s.table_info({_q(table)})").fetchall():
                if name not in have:
                    tail = f" DEFAULT {default}" if default is not None else ""
                    conn.execute(f"ALTER TABLE main.{_q(table)} ADD COLUMN {_q(name)} {kind or ''}{tail}")
        conn.commit()
    finally:
        conn.close()
```

Call `_add_missing_columns(self._base_db, self.db_file)` in three places:
1. In `_write_save`, just before `make_delta(self.db_file, self._base_db, ...)`.
2. In `_apply_delta`, just before `merge_sqlite(self._base_db, minus, plus)`.
3. In `_merge_full`'s else branch, before `merge_sqlite(self.db_file, base, theirs)` when base is not None.

ALTER ... ADD COLUMN only changes the schema, so it costs no extra disk.

I tested this on a copy of the repo:
- The deploy and revert scenarios keep every value on both instances.
- tests/test_disk.py, test_hosted.py, test_publish_cloud.py, test_budget.py, test_review_fixes.py, test_security_review.py, test_share.py, test_tracking.py, test_reportcache.py and test_publish.py all pass.
REPRO: True | I could not refute it. This is a regression, and it is wider than the report says.

**How a new column arises.** The app changes its schema by running ALTER TABLE ... ADD COLUMN when an engine is created (store._ensure_ledger_columns). Five columns were added this way on 2026-11-06. After a deploy, a fresh instance cold-syncs, so rank.db and base.db both become the old-schema snapshot. _merge_full then disposes the engines, the next session_factory runs the ALTER, and only rank.db gets the column.

**Why base.db never gets it.** Every delta (make_delta) and merge (_merge_table) uses only the columns that the working db, base.db and the other side all have. Before this commit the writer did `_copy_db(snap, base)` after its first delta, so its base.db gained the column. Line 765 now runs merge_sqlite(base, minus, plus), which skips the column. So no base.db gets it until the next full snapshot, which can be up to 15 saves later.

**Realistic run** (/tmp/claude-0/-home-user-Ranksim/f6dcc9b5-8d3e-56d1-9583-6296cdb3fbf1/scratchpad/review/mine/deploy_column.py): an old-schema snapshot in the store; fresh instances A and B cold-sync it; the ALTER runs on each; then A makes three saves.
- Old code: A's second save reaches B with project_slug 'proj2', and a later note-only edit uploads as save 4.
- New code: B gets project_slug '' for every row. The note-only save returns None, A marks its db clean, and B still sees note ''.

**Worse: the blanks spread back** (/tmp/claude-0/-home-user-Ranksim/f6dcc9b5-8d3e-56d1-9583-6296cdb3fbf1/scratchpad/review/mine/revert.py, with COMPACT_DELTAS lowered to 4). A's fifth save is a full snapshot.
- B merges it while B's base.db lacks the column, so B's blank values stay. B's base.db then becomes A's snapshot, which has the real values.
- B's next save edits an unrelated row. Its delta now carries the blanks against the real values, so A accepts them.
- New code: A loses all of proj1..proj5 and the note.
- Old code: A loses only q1 and q5. That is the same bug already present for the first save and for snapshot merges, but much narrower.

**The reviewer's fix is incomplete.** I tested "force a full snapshot when base.db lacks a column" (fullpatch.py). q1 is still lost, because B's _merge_full skips the column that B's base.db lacks.


## [high] disk: Cold sync keeps every downloaded save on disk until all projects are installed, so peak /tmp grows with chain history (src/aeo/ranksim/cloud.py:489)
SEQ: 1. Each pull writes new files under projects/<slug>/pulls/<pull_id>/ (project.py:1137). _write_save never rewrites them, so their 'at' entry keeps pointing at the pull's own save, and every pull save stays referenced. Those saves also carry that pull's db.minus/db.plus, or a full db.full snapshot every few saves.
2. A new instance cold-starts and _cold_sync runs: the first signed-in request, _commit when _seq==0, _catch_up(verify=True), or the retry after _wipe.
3. For each wanted project seq, `_unpack(saves.path(seq), ...)` is called. For each file whose bytes live in an older save, source_of calls `_unpack(saves.path(holder), ...)`. _Saves.path caches every file in saves.paths. Nothing is dropped until the project loop ends: `for seq in list(saves.paths): if seq not in keep: saves.drop(seq)` comes after it. So every referenced save, including its db part, is on disk at the same moment.
4. Measured with this commit's own 70 MB test database: 24 pull-like saves at the default compaction settings (each refreshes about 10% of pages and adds one project file) make the fresh instance's cold sync peak at 454 MB. Its steady state is 149 MB, and the docstring claims about 175 MB. A couple more pulls pass 512 MB.
5. On ENOSPC, _sync_recovering sweeps and retries. Moved files persist, so a retry resumes. If the second attempt also overflows, _wipe deletes that progress and the third attempt starts over.
OUT: New instances fill /tmp during cold start. Once the referenced history is larger than about twice the free space, the first refresh ends in _wipe and a 503. Once it is larger than about three times the free space (about 70 pulls at 70 MB), no refresh can ever finish: every data request returns 503 'Could not read storage'. Each forced POST, or each request after the 15 s backoff, downloads every save again. In the simulation every refresh re-downloaded 14 of the 17 saves and ended at seq 0. That burns the Hobby cap of 10,000 simple ops a month within hours, and Vercel then blocks Blob for 30 days. This is a regression: the old code held these bodies in RAM, not on the 512 MB disk.
FIX: Drop each downloaded save as soon as its members are unpacked, unless a later step still needs it.

In _Saves.__init__ add `self.keep: set[int] = set()`.

In _cold_sync, right after building `wanted`, compute `db`, `needs_db` and `keep` (these lines move up from after the loop). Then set `saves.keep = keep | set(wanted)`.

In _merge_project.source_of, right after unpacking:
```
if holder not in staged:
    staged[holder] = self._temp(f"refs-{holder}")
    _unpack(saves.path(holder), staged[holder], lambda name: name.startswith(f"p/{slug}/"))
    if holder not in saves.keep:
        saves.drop(holder)  # its project folder is unpacked: free the disk now
```

In _cold_sync's wanted loop, after the slugs of a seq are merged, add `if seq not in keep: saves.drop(seq)`.

The existing drop loop after the project loop can stay. Peak disk then holds at most one history save, plus the few saves the manifest itself names. The catch-up path (_apply_save) gets the same bound, because saves.keep is empty there. A holder shared by two projects may be downloaded twice, which costs one extra get. Collecting all holders first and unpacking each save once would avoid that, but it is not needed for correctness.
REPRO: True | I could not refute it. The sequence is possible and I reproduced it.

1. Saves stay referenced forever. Every pull writes a new pulls/<pull_id>/ folder (project.py:1137, 1343). _write_save keeps `at[rel] = before_at[rel]` for files that did not change, so a pull's files always point at that pull's save. _prunable never deletes a save named in manifest["refs"]. Nothing rewrites or removes old pulls, and the daily cron (/api/v1/cron/recheck, tracking.recheck) keeps adding them. Every pull also changes rank.db, so each of those saves carries a db.minus/db.plus delta or a full db.full snapshot.

2. A cold sync holds all of them on disk at once. On a fresh instance base={}, so every file is taken from theirs. source_of calls `_unpack(saves.path(holder), ...)` for every holder. _Saves.path keeps each downloaded tgz in saves.paths. The only drop before the database step is the `for seq in list(saves.paths): if seq not in keep: saves.drop(seq)` loop, which runs after the whole project loop. Peak /tmp therefore equals the projects plus every referenced save, and it grows with the number of pulls. That contradicts the docstring ("streams each save it needs to one file that is deleted once used", "adds at most one more full database copy"). The same build-up happens in _apply_save during catch-up, because source_of uses the same saves object.

Experiment 1 (scratchpad/review/mine/held.py, 10 MB db at 2:1, one new pull file per save): a fresh instance's cold sync held 6, 12 and 24 save files at once (16, 32 and 64 MB) for 6, 12 and 24 pulls. That is linear in history.

Experiment 2 (mine/enospc.py): the fetch raises ENOSPC once the fresh instance's folder would pass a cap. With 112 MB of history and a 35 MB cap, refreshes 1, 2 and 3 each failed with "Could not read storage: OSError.", ended at seq 0, and made 42 fetches each. The sweep retry and the _wipe cold sync both run out of space again. The same history with the fix below succeeded on the first refresh with 41 fetches.

At production scale: free space during a fresh cold sync is about 480 MB, and rank.db stores raw_html uncompressed, so a 70 MB db is roughly 14 MB per full save after gzip. A few hundred daily pulls is enough to reach about 3x free space. From then on, every new instance returns 503 on every data request and re-downloads the whole referenced history about every 15 s (FAILURE_BACKOFF), which uses up the Blob simple-op budget. The old code held these bodies in RAM, not on the 512 MB /tmp, so this is a regression introduced by the commit.


## [low] disk: The start-up sweep misses .ranksim-tmp-* temp files that _merge_files leaves inside project folders (src/aeo/ranksim/cloud.py:1201)
SEQ: 1. _merge_files in copy mode (move=False) copies each incoming file to `target.with_name('.ranksim-tmp-<hex>')` inside projects/<slug>/... and then renames it. This commit's _import_legacy uses that mode (first import and the legacy watch). The previous version used it for every merge.
2. The process is killed during shutil.copyfile, which Vercel can do at any time, while /tmp survives into the next process.
3. At the next process's first storage call, _sweep only checks .cloud/tmp, .cloud/.*, /tmp/.ranksim-incoming-*, /tmp/.ranksim-legacy-* and .rank.db.*.tmp. Nothing under projects/ is checked.
4. _Scope.files skips dot files, so the partial copy is never saved and never removed.
OUT: Each kill during such a copy leaves a partial project file, up to the size of the largest stored page or engine file, on /tmp for the life of the instance. The commit's claim that 'whatever a killed process left is removed at the next process's first storage call' does not hold for these files.
FIX: In CloudState._import_legacy, pass move=True to both _merge_files calls:
    _merge_files(self._scope(slug), bases.get(slug, {}), incoming / slug, theirs, self._hash_cache, move=True)
    ...
    _merge_files(self._scope(LOOSE), bases.get(LOOSE, {}), incoming, loose, self._hash_cache, move=True)

The staging folder (self._temp("legacy"), under .cloud/tmp) is ours. It is on the same filesystem as projects/ and is swept at start-up, so the files can be renamed into place. cloud.py then never writes a temp file inside a project folder. Each slug's manifest is computed before its merge, and the loose manifest only reads files that sit directly in incoming, so moving the files changes neither. I checked this by forcing move=True on these calls: tests/test_hosted.py, tests/test_disk.py and tests/test_publish_cloud.py all pass, including the 5 legacy and rollout tests, and the copy path is never used.

Do not add a projects/**/.ranksim-tmp-* glob to _sweep. plugins/tracking.py uses the same prefix for its own in-flight temp files, which it writes without the cloud lock.
REPRO: True | The defect is real, but it is rare and low severity. Everything I checked holds. In copy mode (move=False), _merge_files writes `target.with_name('.ranksim-tmp-<hex>')` inside projects/<slug>/... and has no cleanup if it fails. After this commit, only _import_legacy uses copy mode (cloud.py:878 and :882). That function runs during the first-deploy bootstrap, and for 14 days afterwards whenever an old deployment saves in the old format. Every commit is dated today, so the migration is still ahead.

_sweep (cloud.py:203-208) never looks under projects/. _Scope.files skips dot files, so a leftover is never saved and never removed. Only _wipe (the last-resort disk-full path) would delete it.

Experiment: /tmp/claude-0/-home-user-Ranksim/f6dcc9b5-8d3e-56d1-9583-6296cdb3fbf1/scratchpad/review/exp_tmpfile.py. I simulated a kill partway through shutil.copyfile during the legacy bootstrap. I then cleared _SWEPT and made a new CloudState on the same folder, like a new process. After its first refresh, `projects/foo/.ranksim-tmp-*` was still there.

The leftover also causes a worse problem, which I reproduced. Another instance B deletes project foo and saves a tombstone. Instance A merges it, but _remove_if_empty cannot remove projects/foo, because the dot file is still in it. _local_slugs still lists foo, and _projects_changed sees `installed.deleted=True` with files {}, so it saves foo as not deleted. B then sees an installed project with no folder and saves a tombstone again. Every write request on A or B adds another flip-flop save (saves 2 through 8 in the run). This lasts as long as A lives, and A shows a ghost folder.

Limits on likelihood: a request's refresh calls the import at the start, so the 300 s maxDuration timeout almost never lands inside a copy. The trigger needs an OOM or crash on the instance while the copy runs, with /tmp kept. The commit's own sweep design assumes that can happen. An ENOSPC error during the copy mostly fixes itself, because the retry fails again and _wipe clears everything. Before this commit every merge used copy mode with no sweep at all, so this is not a regression. It is a gap in the commit's promise that a killed process's leftovers are removed at the next process's first storage call.

Do not use the reviewer's first fix, which adds `projects/**/.ranksim-tmp-*` to _sweep. plugins/tracking.py:130 writes its own `.ranksim-tmp-*` temp files in project folders without holding the cloud lock. The disk-full sweep runs while the process is busy, so it could delete a tracking temp between its write and its os.replace, and that write would fail.


## [high] concurrency: The failure backoff is measured from when the failed sync started, so every request queued on the lock repeats it (worse with the new 3-attempt disk-full recovery) (src/aeo/ranksim/cloud.py:314)
SEQ: 1) A refresh takes the lock with now=t0 and starts a sync that fails slowly. This happens on a Blob outage: open/get retries 3 times on a 60 s timeout. It also happens through the new _sync_recovering on a disk that stays full: catch-up, sweep and catch-up again, then _wipe and a full cold re-download, all inside one lock hold. 2) Meanwhile, other ordinary requests queue on self._lock. These are requests that are due (a cold instance, `_checked is None`) or that carry a stale rs_sync hint (after a wipe `_seq` is 0, so every signed-in cookie is newer). 3) The sync fails after more than FAILURE_BACKOFF_SECONDS and sets `self._failed_at = now`, which is t0, the time the attempt started. 4) Each queued thread takes the lock, computes `now - self._failed_at >= 15` and runs the whole sync again, one after another. `_hinted = now`, also set at the start, defeats HINT_MIN_SECONDS in the same way. 5) After a wipe, every attempt is three cold syncs, and forced (write) refreshes skip the backoff completely.
OUT: Request waits grow linearly. The Nth queued request waits N times the failing sync, which is minutes and past Vercel's 300 s limit, and the Starlette threadpool fills with blocked threads. Every queued request repeats the Blob calls. With the disk full, one refresh costs 23 to 41 gets/heads/fetches in a 5-save test store, and each one downloads the full snapshot again. That burns the 10,000/month simple-op budget, after which Vercel blocks Blob for 30 days.
FIX: In CloudState.refresh in src/aeo/ranksim/cloud.py (line 314), record the failure time when the attempt ends, not when it starts. Change the except block's `self._failed_at = now` to `self._failed_at = self.clock()`. Then any refresh that was queued on the lock, or that comes from the same request's later middleware calls, sees `now - _failed_at < FAILURE_BACKOFF_SECONDS` and raises CloudError without calling storage. `_hinted` needs no change.
REPRO: True | The mechanism is real and it happens in production. One correction: the faulty line is older than this commit. `self._failed_at = now`, with `now` read right after taking the lock, came in with commit a95a045. Commit 1ae2c42 only routes the call through `_sync_recovering`.

Why a failing sync can last 15 s or more: in the installed vercel SDK, httpx errors, timeouts included, become BlobUnknownError (api_client.py `_request` and `open_download`). DEFAULT_TIMEOUT is 60 s. objects.`_retry` retries a BlobError 3 times, sleeping 0.5 s and then 2 s. So when Blob hangs, one failing operation takes about 182 s. The sync stops at that first failed call, `_failed_at` is set to the start time, and every later non-forced refresh sees `now - _failed_at` of 182 s or more, which is past the 15 s backoff, so it runs the sync again.

This is worse than the reviewer says, because it does not need concurrency. In production, app.py builds `Access(..., shared=cloud)`. On a cold instance (`stamp == 0` and `_checked is None`), `Access._revoked_since` calls `cloud.refresh()`. That happens once in `_require_login` (via `_signed_in`), again when `_sync_state` computes `trusted = _signed_in(...)`, and then `_sync_state` calls its own `cloud.refresh(...)`. So one signed-in GET during a Blob hang runs three failing syncs back to back.

Experiment: /tmp/claude-0/-home-user-Ranksim/f6dcc9b5-8d3e-56d1-9583-6296cdb3fbf1/scratchpad/review/verify_backoff/one_request.py. It uses the real Access and CloudState, with times scaled down by 100. Results at real scale:
- One cold request: 3 Blob operations, a 503 after 548 s. Vercel's 300 s limit would cut it off first.
- 4 concurrent requests: 12 operations, waits of 548, 1460, 2008 and 2190 s.
- With the fix patched in (/tmp/claude-0/-home-user-Ranksim/f6dcc9b5-8d3e-56d1-9583-6296cdb3fbf1/scratchpad/review/verify_backoff/fixed.py): 1 Blob operation in total, and every request gets a 503 at about 183 s.

The wrong outcome: requests hang past Vercel's limit and threadpool threads stay blocked, when they should get a quick 503. The backoff is meant to stop repeated storage calls, and here it does nothing.

Parts of the claim that are overstated:
- The new disk-full path is a weak trigger. A disk that is still full after `_wipe` needs something outside /tmp/ranksim using the space, and such a refresh usually ends in under 15 s.
- The 23/41 calls per refresh and the forced-refresh bypass happen with or without this fix, so the fix does not address them.
- Blob calls that time out during a hang may never reach the server, so they may not count against the operation budget.
- `_hinted` needs no change, because the failure backoff already covers that path.

The proposed fix passes tests/test_hosted.py, test_disk.py, test_publish_cloud.py and test_access.py on a scratch copy of the repo.


## [medium] concurrency: (predates this commit) record_job/update_auth mutate _extra outside the lock; a concurrent save resets it, so a job's final record or a sign-out-everywhere is dropped (src/aeo/ranksim/cloud.py:772)
SEQ: 1) A write request's middleware save() holds the lock. _write_save has already read self._extra into the manifest and is packing or uploading. 2) Meanwhile a job thread finishes. JobRegistry._store_final, then on_finish, then cloud.record_job(record), which writes into self._extra without the lock. Or Access.revoke_all calls update_auth(revoked_ms=...). 3) The upload succeeds, and `self._manifest, self._seq, self._extra = manifest, seq, {}` throws away the record that was just added. 4) on_finish's own cloud.save() takes the lock, finds _dirty() False and returns None. on_finish returns True, so JobRegistry does not write the record object either.
OUT: The job's final record is never stored. Other instances see no record and no lock, so a follower gets 'That job is gone', and a new start for the same site re-runs the job and pays for the provider calls again. For revoke_all, other instances never learn of the revocation, so stolen or old cookies stay valid there, while revoke_all reports success.
FIX: In src/aeo/ranksim/cloud.py, make both mutators take the instance's lock. It is a re-entrant lock, so update_auth's inner save() is fine:

    def update_auth(self, **changes) -> dict:
        with self._lock:
            auth = self._extra.setdefault("auth", {})
            for key, value in changes.items():
                auth[key] = max(auth.get(key) or 0, value)
            if self.enabled:
                self.save()
            return self.auth

    def record_job(self, record: dict) -> None:
        with self._lock:
            self._extra.setdefault("jobs", {})[record["id"]] = record

This is enough: every path to `_write_save` already holds `self._lock`. Those paths are `save` -> `_commit`, `refresh` -> `_bootstrap`/legacy check, and `export_legacy`/import under `state.lock`. So no change to `_extra` can land between the manifest read and the reset, and a change that arrives during a save waits and goes into the next one. The reviewer's extra step, snapshotting `_extra` and removing only the uploaded keys, is not needed once every writer is under the lock.
REPRO: True | Confirmed, though it predates this commit (the diff does not touch record_job, update_auth or the `_extra` reset). `save()` holds `self._lock` from `_write_save` reading `self._extra` into the manifest (lines 706-714) until line 772, `self._manifest, self._seq, self._extra = manifest, seq, {}`. In between it packs the tar, does the network put, and runs `_install_base`/merge_sqlite, which takes seconds when the db changed. `record_job` (line 624) and `update_auth` (line 615) write into the same `_extra` dict without the lock. On Vercel Fluid compute, jobs run in daemon threads, and while the put is in flight the GIL is released, so they can run during another thread's save. That other save can be a middleware save for a write request, another job's on_finish save, or a retry save.

Sequence, run against the real code (scratchpad `review/verify_extra/race.py`, using the real JobRegistry, the on_finish closure from `api/sites.py` and `Access(shared=cloud)`):
1. Instance A starts a middleware save, which blocks in the put.
2. A background job ends. on_finish calls `record_job`, which puts the record into `_extra`, then calls `cloud.save()`, which waits for the lock.
3. The put finishes and `_extra` is reset to `{}`.
4. on_finish's own save finds `_dirty()` False and returns None. on_finish returns True, so no `jobs/<id>.json` is written, and `_release` deletes the job lock.

Results: on A, `job_record` is None and `_extra` is `{}`. On instance B, after `refresh(force=True)`, `regB.get(job_id)` is None, so a follower stream there yields "That job is gone."

A paused job is worse (`review/verify_extra/paused.py`). Its checkpoint is lost and the lock stays with status "running". A /resume on B attaches until start + budget + grace, then resumes as stalled with checkpoint `{}`, so the checkpointed provider calls are paid again.

revoke_all during an in-flight save: it returns True, A's manifest has `revoked_ms` 0, and a fresh instance reads `revoked_ms` 0. So old cookies stay valid on every other instance. A rejects them only through its in-memory `_revoked_ms`, which a restart loses.

One part of the reviewer's outcome is wrong. "A new start for the same site re-runs the job" is not caused by this bug: a finished job deletes its lock either way, so a new start runs again even with the fix (checked).

With the two mutators taking `self._lock`, the same experiments keep the job record, the paused checkpoint path and `revoked_ms` on other instances.


## [low] concurrency: (predates this commit) On a cold instance the cookie check runs the storage sync on the event loop thread, stalling every request and blocking on the cloud lock (src/aeo/ranksim/access.py:253)
SEQ: 1) A Vercel instance starts cold, with cloud.stamp == 0 and _checked None. app.py wires Access(shared=cloud). 2) A signed-in request reaches the async _require_login middleware, which calls _signed_in, then Access.valid, then _revoked_since, then `self.shared.refresh()` directly on the event loop thread, not through run_in_threadpool. 3) That refresh takes the cloud lock and runs the whole cold sync on the loop. If a threadpool thread already holds the lock (a share-link or cron route started the sync), the loop thread blocks on the lock instead. 4) If the first sync fails, _checked stays None. Every later signed-in request then retries on the loop thread whenever the backoff has passed, and the backoff-from-start problem above keeps it from holding.
OUT: The whole instance is unresponsive for the duration of the cold sync, including /healthz, static assets and requests that need no storage. During a Blob outage that can be minutes per request, because the 60 s timeouts are retried 3 times.
FIX: Never run the cookie check's possible storage sync on the event loop. In src/aeo/ranksim/studio.py, make both middlewares call it in the threadpool:
- in _require_login: `if ui.access.enabled and not _is_open(request.url.path) and not await run_in_threadpool(_signed_in, ui, request) and not plugins.is_login_exempt(app, request.scope):`
- in _sync_state: `trusted = await run_in_threadpool(_signed_in, ui, request)`

Do the same in the two async routes that call it: api_logout in api/session.py (`everywhere and ui.access.enabled and await run_in_threadpool(_signed_in, ui, request)`) and gsc_callback in plugins/gsc.py. Access._revoked_since can stay as it is: its refresh then always runs in a worker thread, and waiting for the cloud lock there is harmless. Optionally, set _failed_at when an attempt ends rather than when it starts, so a slow failure still holds the 15 s backoff.
REPRO: True | The claim holds in the code, and it predates commit 1ae2c42. The diff does not touch access.py or studio.py. app.py wires Access(PASSWORD, secret, shared=cloud). Access.valid calls _revoked_since, and on an instance where stamp == 0 and _checked is None, _revoked_since calls self.shared.refresh() synchronously. studio.py `_require_login` and `_sync_state` are `async def` and call `_signed_in(ui, request)` directly, so that refresh runs on the event loop thread. It does Blob I/O, time.sleep backoffs and an RLock wait there. The codebase treats this as a bug elsewhere (serve.py:936: "Never wait for the project lock on the event loop: a running pull would freeze every page").

I checked the realistic boot path, because it could have refuted the claim. app.js awaits GET /api/v1/session first. That route is a sync `def`, so the cold sync usually runs in the threadpool. That does not save the loop. Any signed-in, non-open request that reaches the instance during that sync blocks the loop on cloud._lock. One such request is a second tab or a poll, or the request that lands on a new Fluid instance. If a non-open signed-in request arrives first, it runs the whole cold sync on the loop.

I ran the real app under a real uvicorn server (one loop) with a store whose get/head take 1.5 s, script /tmp/claude-0/-home-user-Ranksim/f6dcc9b5-8d3e-56d1-9583-6296cdb3fbf1/scratchpad/review/verify_loop/uv.py:
- control (only /session syncing in the threadpool): /healthz answered in 0.02 s.
- boot (/session syncing, then /api/v1/projects 0.2 s later waiting for the lock on the loop): /healthz took 2.65 s.
- direct (/api/v1/projects first): /healthz took 2.64 s.

During a Blob outage the stall is longer. The SDK timeout is 60 s, with 3 tries plus 2.5 s of sleeps. _failed_at is the start time, so a 15 s backoff has already run out when an attempt ends. Each signed-in request on a never-synced instance then retries on the loop, and the shell, assets, login and /healthz hang meanwhile.

The one thing I could not check offline is whether Vercel's Python runtime runs one shared event loop per instance. A uvicorn server or a persistent loop would; per-request asyncio.run would not. As far as I know the current Fluid runtime serves ASGI apps on one loop, so I count the claim as real. Severity is low: no data is lost, and requests that need storage would wait for the lock anyway. The wrong outcome is that requests needing no storage hang for the cold sync, or for minutes during an outage.


## Refuted

-- refuted data-loss: Disk-full wipe deletes unsaved local work without trying to save it, and even a refresh with nothing new triggers it | The code path is real, but the claim that the wipe causes avoidable data loss does not hold. I ran it on a real 14 MB tmpfs (scripts in /tmp/claude-0/-home-user-Ranksim/f6dcc9b5-8d3e-56d1-9583-6296cdb3fbf1/scratchpad/review/r2/code/).

What happens: 

-- refuted data-loss: The new TRUNCATE checkpoint after each merge into the working db blocks app writers for up to 10 s, and their writes fail with 'database is locked' | The TRUNCATE checkpoint does hold SQLite's writer lock while it waits for readers. The failure needs a reader that keeps one snapshot open for more than about 5 s, and this app never does that, so step 1 of the claimed sequence does not happen.

1. H

-- refuted disk: A save writes the whole delta (minus + plus) before choosing a full snapshot, so the saving instance holds 4 database copies when most rows changed | The mechanism is correct, but it does not cause a failure at the production size, so this is a headroom and documentation issue, not a production defect.

What I confirmed: _write_save calls make_delta(db, base, minus, plus) and only then compares mi

-- refuted disk: _wipe on a full disk does not free a database held open by a running job, and that job's later writes go to the deleted file | The claim depends on the crawl keeping a SQLite connection checked out for the whole crawl. It does not.

1. **The connection is returned after every page.** project.py:1858 opens one Session, but each page goes through `_record_fetch` -> `store_fetc

-- refuted disk: Merging a full snapshot in which most rows changed writes a rollback journal the size of the db, so the instance holds 4 copies | The measurement holds, but the claimed outcome does not happen in production at today's size.

What I confirmed: _merge_full runs merge_sqlite in place. If one save rewrote most rows, SQLite writes a rollback journal (or WAL) about the size of the ch

-- refuted concurrency: New TRUNCATE checkpoint on the live rank.db blocks app writers and holds the cloud lock up to 10 s per merge | The claim only holds if the production rank.db is in WAL mode, and it is not. The only code that turns WAL on is aeo.db._register_sqlite_pragmas, a global SQLAlchemy listener. It is registered only by aeo.db.get_engine, which only aeo/api.py and aeo/

-- refuted concurrency: _wipe deletes rank.db and project folders under the cloud lock only, while request and job threads still use them | The claimed silent data loss does not happen in production. The reviewer's experiment forces `PRAGMA journal_mode=WAL` on rank.db, but the hosted app never runs in WAL mode.

1. The hosted rank.db uses rollback-journal (DELETE) mode. The only code th

-- refuted blob-and-errors: While the disk stays full, each failed refresh makes 3 syncs, and the wipe makes the next ones cold, so Blob calls jump from 1 to 28-42 per attempt and writes skip the backoff | The storm needs the cold sync after the wipe to keep failing with a full disk. On Vercel, with every instance having the same 512 MB /tmp, neither of the two causes the reviewer gives makes that happen.

Cause 1, the database grew: if the database ha
