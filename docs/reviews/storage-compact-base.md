# Review of the compact-base storage change (e7fe6a3)

Second adversarial review, run on 2026-11-15 against `08bede0..e7fe6a3` (commits 425cac4 and fa19556).
17 agents. Every finding below was reproduced by the reviewer and then again by an independent skeptic.
Production stays on 08bede0 until the blocking findings are fixed.

Repro scripts are in `docs/reviews/repro/compact-base/`. They import `/home/user/Ranksim/src`, so they run
against whatever the checkout holds. Most take a scratch folder as the first argument.

## What to fix, grouped

### A. The disk-full last resort (`_wipe`) is unsafe. Remove it. (findings 3, 8, 9, 10; all block)

`_wipe` drops base.rows, state.json, `_installed`, `_seq` and `_manifest`, but keeps the working copy. The cold
sync that follows merges storage with no common ancestor, so a stale local copy counts as local edits and the next
save reverts other instances' edits and brings back their deletions everywhere. The post-wipe cold sync also needs
more disk than the wipe freed, so it fails again, leaving the instance at seq 0, repeating three full-history cold
syncs per refresh (a Blob op storm) and forgetting the shared auth state (revoked sessions accepted).

Fix: delete `_wipe`. In `_sync_recovering`, sweep temps and retry once; if the disk is still full, let the error
reach `refresh()`, which backs off. Keep `_seq`, `_installed`, `_db`, `_manifest`, state.json and base.rows. Rewrite
`tests/test_disk.py::test_a_disk_still_full_drops_only_the_bookkeeping_and_keeps_writes_in_flight` to expect a
CloudError while full, unchanged stamp/base/state/auth, a catch-up in at most 2 store calls once space frees, and
the local row and file still reaching the other instance on the next save. Any future space-freeing step must keep
state.json and base.rows, and must never run `merge_sqlite(db, None, theirs)` or `_merge_files` with base {} on a
working copy that may be stale. Update the module Disk paragraph.

### B. Schema skew breaks the compact base (findings 1, 2, 4 block; 5 does not but shares the fix)

Two causes:
1. A delta or snapshot carrying a column the working db lacks (fresh instance cold-syncing before its engine
   migrates) is merged on the shared columns only, so the value is dropped and the fresh instance's base disagrees
   with everyone else's. Later saves then ship a deletion of a row (finding 1) or blank a value everywhere (5).
   Fix: before merging a delta or snapshot, ALTER-add to the working db every column the incoming table has and it
   lacks (`_add_columns_like(db_file, plus)` in `_apply_delta` before `merge_sqlite`, and with `theirs` in
   `_merge_full`'s non-fresh branch; or do it inside `merge_sqlite`'s table loop). `_base_add_columns` then follows.
2. A delta from an older schema (rollout overlap, rollback, preview sharing the store) is hashed in `_base_apply`
   over the delta's columns, not the base's. Minus rows miss, plus rows are stored short, the base holds two entries
   for one key, and every later save fails with `IntegrityError: UNIQUE constraint failed` (keyed tables) or deletes
   rows the old deployment wrote (log tables). Fix: in `_base_apply`, for a narrower delta table, either narrow the
   table's base entries to the delta's columns first (finding 2's corrected fix; `_base_add_columns` re-widens), or
   match minus rows by a narrowed hash and pad plus rows with the working db's column defaults (finding 4's).
   Apply the same rule where `_merge_full`'s else branch feeds `_base_apply`.

Note: e7fe6a3 adds no column, so this is armed by the next column-adding deploy (for example
`cursor/keyword-volume-difficulty-ae89` adds `fetch_method` and `fetch_log` to rs_pages). It still has to be fixed
before that deploy, and the reviewers judged it blocking.

### C. A write committed during a catch-up is marked clean and never uploaded (findings 6, 7; 7 blocks)

`_apply_delta` and `_merge_full`'s non-fresh branch set `_db_clean = _stat_sig(db_file)` after merging, so an app
commit that lands between the merge's COMMIT and the stat is folded into "clean". Compacting saves now reach readers
through `_apply_delta`, where the slow `_base_apply` sits in that window, so the loss rate is about 1.5x production.
Fix: set `_db_clean = None` after any merge into the working db (keep the stat only in the fresh rename branch). The
next save's `make_base_delta` takes `sig_before` first and decides.

## Refuted (no change needed)

- _base_add_columns stores the raw DEFAULT literal, not the value SQLite reads for existing rows, so after the second save every save fails
- _base_add_columns gives the base the default without the column's type affinity: after a migration that adds e.g. REAL DEFAULT 0, every later save ships the whole table and a full snapshot, for good
- A cold sync downloads a save twice when it is one project's newest save and also holds another project's files (the commit's 'fetch each such save once' does not hold with more than one project)

## Findings in full

### 1. After a column-adding deploy, a fresh instance's base disagrees with the others, and its next save deletes a row from storage and every other instance

Severity high. Blocks deploy: True. Lens: base-correctness. `src/aeo/ranksim/cloud.py:1720`

**Sequence.** Setting: one deploy adds a column to a keyed table with no timestamp column, such as rs_keywords (query PK). Every instance runs the same ALTER when its engine is created. 1) Instance A migrates, sets the new column on row k1 (project_slug='acme') and saves a delta. The newest full snapshot still predates the deploy, so the cold-sync window lasts until the next compaction (up to 15 saves). 2) Fresh instance C cold-syncs. _merge_full renames the pre-deploy snapshot into place as rank.db and disposes the engines. In the same _apply_db call it then applies A's delta to that file before any engine has re-run the migration. merge_sqlite only merges columns both sides have, so the value is dropped, and _base_apply's layout() hashes k1 without the column. When C's engine migrates, k1 gets the default ''. _base_add_columns gives the base the same default, so C's main and base agree on '' while every other base holds 'acme'. 3) A edits another field of k1 (intent) and saves. The minus row is k1(intent=info, slug=acme). 4) On C, merge_keyed sees mine != base, so both sides changed; with no stamp column it keeps C's row. _base_apply hashes the minus row, finds no matching entry, and deletes nothing. It then adds the plus row, so C's base holds both k1('',info) and k1(acme,commercial). 5) C saves anything, even an unrelated row. make_base_delta finds k1(acme,commercial) missing from C's main and puts it in minus. 6) A applies it: theirs None, mine == base, so merge_keyed DELETEs k1.

**Outcome.** Row k1 is deleted on A, in storage, and on every instance that cold-starts afterwards. Only C keeps a stale copy. Reproduced: new code gives 'a after c's save: [(k2,...)]' and 'fresh b: [(k2,...)]', with k1 gone. Old code keeps k1 everywhere. If C instead edits k1 itself, its minus holds two k1 rows and every later save from C fails with IntegrityError. Dropping the new column's values on a fresh instance (step 2) also happens in 08bede0; the row deletion is new.

**Reviewer fix.** Bring the working db up to the schema a delta carries before merging it. In _apply_delta, and in _merge_full's non-fresh branch, before merge_sqlite: for each table in the plus file, run ALTER TABLE main.<t> ADD COLUMN <name> <type> DEFAULT <default> on the working db for every column the plus table has and the working db lacks. This is the same DDL as the deploy's migration, read from PRAGMA p.table_info, and it is a schema-only change. The fresh instance then merges 'acme' instead of dropping it, and its base agrees with everyone else's. A narrower alternative: in _merge_full's fresh path, run the app's migration (store.session_factory on the renamed file) before _apply_db applies the deltas.

**Evidence.** Scratch script: (review scratch) exp_cold_column_delete.py (pass 'old' to run against 08bede0's cloud.py). New code output: "c after cold sync: [('k1','info',''),...]", "c after a's edit: [('k1','info',''),...]", "c save: ...000004", "a after c's save: [('k2','info','')]", "fresh b: [('k2','info','')]". Old code: "a after c's save: [('k1','commercial','acme'),...]", "fresh b: [('k1','commercial',''),...]". Mechanism: _base_apply (lines 1712-1721) deletes by _row_hash over the delta's columns and ignores misses. make_base_delta (lines 1867-1899) puts every unmatched base entry into minus. The cold-sync order is _merge_full line 609 (rename) followed by _apply_db lines 554-566 (deltas).

**Skeptic verdict.** I could not refute it. The sequence is possible and the result is wrong.

How it happens in the code: the studio middleware runs refresh, and so the cold sync, under the cloud lock. _cold_sync calls _apply_db once. In that one call _merge_full takes the fresh path: it calls _dispose_engines and renames the pre-deploy snapshot over rank.db (line 609). The loop at lines 554-566 then applies every delta through _apply_delta. No engine is created in between, so the migration in store.session_factory does not run until the next request opens a session. That means a delta carrying the new column is merged into a file that does not have the column yet. merge_sqlite merges only the columns both sides share, so the value is dropped. _base_apply then hashes the rows over the pre-deploy columns. Once C's engine migrates, _base_add_columns gives the base the same default, so C's main and base agree with each other but not with every other instance's base. Every fresh Vercel instance that starts between the first such delta and the next compaction (up to 15 saves) gets this skewed base.

Reproduced:
- The reviewer's script (review2/exp_cold_column_delete.py) gives "a after c's save: [('k2',...)]" and "fresh b: [('k2',...)]" on the new code, so k1 is deleted on A, in storage and on later fresh instances.
- My own check, (review scratch) v_coldcol/exp_variant.py, shows the mechanism. After A's second edit, C's base.rows holds two k1 entries, (info, slug '') and (commercial, slug 'acme'), while A's base holds one. C's main keeps (info, '') and misses A's edit, because merge_keyed sees both sides changed and the table has no stamp column.
- The variant also reproduces. If C edits k1 itself, make_base_delta puts both base entries into minus. Both inserts go into m.rs_keywords, which keeps the PRIMARY KEY, so every later save from C fails with "IntegrityError: UNIQUE constraint failed: rs_keywords.query". C's writes are never uploaded until the instance dies.
- The variant also reaches stamped keyed tables such as rs_pages (fetched_at), but only as the IntegrityError case: the newer stamp wins, so no row is deleted.

What has to happen first: a later deploy must ALTER-add a column to an existing keyed table and write non-default values into it. Today that means rs_keywords for the deletion, or rs_pages for the IntegrityError. e7fe6a3 adds no column itself. store.py and ledger.py are unchanged since 08bede0, and both earlier ADD COLUMN migrations (8dc23df, 0a8fbb2) are already in 08bede0, so production snapshots already carry those columns. Deploying e7fe6a3 alone does not set this off; the next column-adding deploy does. Such migrations are a regular practice here (two so far), and _base_add_columns was added to support them.

Compared with 08bede0 in the same scenario: the old code never ships the new column's values in deltas. A's save of the value returned None, which is a separate existing loss. But its full-copy base and main stay consistent, so k1 survives everywhere and no save fails. The new code instead deletes rows across all instances and storage, or blocks every save from a fresh instance. That is a regression relative to production.

**Skeptic's corrected fix.**

Before merging a delta or snapshot, bring the working db up to the columns that file carries. Add a helper in cloud.py:

def _add_columns_like(db_file: Path, other: Path) -> None:
    conn = sqlite3.connect(str(db_file), timeout=30, isolation_level=None)
    try:
        conn.execute("ATTACH DATABASE ? AS o", (str(other),))
        ours = _tables(conn, "main")
        for table in _tables(conn, "o"):
            if table not in ours:
                continue
            have = {name for name, _t, _p in _columns(conn, "main", table)}
            for _cid, name, kind, notnull, default, pk in conn.execute(f"PRAGMA o.table_info({_q(table)})").fetchall():
                if name in have or pk:
                    continue
                ddl = f"ALTER TABLE main.{_q(table)} ADD COLUMN {_q(name)} {kind}"
                if default is not None:
                    ddl += f" DEFAULT {default}" + (" NOT NULL" if notnull else "")
                conn.execute(ddl)
        conn.execute("DETACH DATABASE o")
    finally:
        conn.close()

Call it in two places:
1. In _apply_delta, right after _dispose_engines(self.db_file) and before merge_sqlite(self.db_file, minus, plus): call _add_columns_like(self.db_file, plus). This covers both cold-sync deltas and catch-up deltas.
2. In _merge_full's non-fresh branch, before _base_add_columns(self._base_db, self.db_file): call _add_columns_like(self.db_file, theirs).

_base_add_columns, which already runs after this, then gives the base the same column and default. _base_apply removes the minus row and adds the plus row with the real value. The app's own migration later sees the column and skips it, because it checks whether the column is already there.

Checked against a patched copy at (review scratch) v_coldcol/fixed_cloud.py:
- The reviewer's script keeps k1 as ('commercial', 'acme') on A, on C and on a fresh B.
- C's base matches A's.
- The IntegrityError variant saves normally.
- The 188 cloud-related tests pass (test_disk, test_publish_cloud, test_hosted, test_budget, test_gsc, test_reportcache, test_security_review, test_tracking).

A narrower fix also works: in _merge_full's fresh path, run store.session_factory on the renamed file before _apply_db applies the deltas. It only covers migrations that the app's code knows about.


### 2. A delta or snapshot from an instance without a new column corrupts the compact base: saves fail permanently, or appended rows are deleted

Severity high. Blocks deploy: True. Lens: base-correctness. `src/aeo/ranksim/cloud.py:1715`

**Sequence.** Two deployments share the store with different schemas. The module docstring expects this: a preview deployment shares production's Blob store for up to two weeks, and old instances finish in-flight jobs after a deploy. N runs the deploy that added column X (ALTER), and _base_add_columns gave N's base the column. O lacks X. 1) O updates row K of that table and saves. Its minus and plus are created from O's schema, without X. 2) N applies the delta: merge_sqlite is fine, but _base_apply.layout() returns (delta columns ∩ known), without X. It hashes the minus row over fewer columns than the stored hash, deletes nothing, and adds the plus row with a hash over fewer columns. N's base now holds 11 entries for 10 rows. 3) N's next save of anything: make_base_delta treats the stale old K entry and the short K entry as missing and puts both in minus. insert_m raises 'UNIQUE constraint failed' (line 1899). This happens on every save, because nothing rebuilds the base. For an append-only id table (like rs_outcomes): O appends a row. N's base keeps a short entry that never matches, so every N delta carries that row in minus with X=NULL. O's merge_log compares without X and deletes the row O wrote. The same corruption happens when O's full snapshot reaches N through _merge_full's non-fresh branch.

**Outcome.** On N, every save fails with 'Could not save to storage: IntegrityError' until the instance dies. Because _write_save raises before packing, project files are not uploaded either, so N's writes are lost when the instance recycles. For appended rows, every old-schema instance deletes a row it wrote itself: instances diverge, and N carries a spurious minus row in every delta forever. With 08bede0 (base.db advanced by merge_sqlite over the shared columns) the same sequences save normally and keep every row.

**Reviewer fix.** In _base_apply, for each table in the delta whose columns lack some of known[table]: before deleting and adding, narrow that table's base entries to the delta's columns. Re-hash and re-pack each body over the intersection, then update tables.names. Minus rows then match their entries exactly. At the next save, _base_add_columns re-adds the column with its default, and the working db's real values travel as an ordinary change. Also check the DELETE rowcount per minus row: a miss means the base is out of step, and the save should fail loudly instead of the base silently keeping the entry.

**Evidence.** Scratch scripts: (review scratch) exp_mixed_schema.py gives "n base entries: 11 (10 rows)", then on N's first save "sqlite3.IntegrityError: UNIQUE constraint failed: rs_keywords.query" at make_base_delta line 1899. exp_mixed_log.py gives "o after n's saves: [(1, 'seed')]": O's row 2 ('observed by o') was deleted. The *_old.py copies run the same steps on 08bede0's cloud.py: every save succeeds and every row survives.

**Skeptic verdict.** I could not refute it. The cause is that `_base_apply` (cloud.py:1712-1735) hashes a delta's rows using only the columns the delta itself has (`layout()` = the delta's columns that the base knows). `_base_add_columns` has already given the base the new column, so every stored hash covers more columns than the delta's rows.

What happens on N, the instance with the new column:
- A minus row from an old-schema instance (O) never matches its stored entry, so nothing is deleted.
- The plus row is stored under a hash over fewer columns.
- On N's next save, `make_base_delta` puts both the stale entry and the short entry in minus.
- For a keyed table, the two minus rows share the primary key, so `insert_m` at line 1899 raises IntegrityError.
- The base is never rebuilt, so every later save fails the same way. `_write_save` fails before packing, so project files are not uploaded either.
- For an append-only table with an integer id, the short entry stays in N's base for good. Every N delta then carries a minus copy of that row with the new column set to NULL, and O's `_merge_log` (which compares without that column) deletes the row O wrote. Instances diverge.

Can this happen on Vercel? Yes:
- Jobs run up to 300 s (vercel.json maxDuration) and save once when they end (sites.py:562 and 705). Old instances finishing jobs after a deploy therefore save deltas in the old schema.
- The module docstring (cloud.py:95-96) says preview deployments share the production Blob store.
- The app adds columns with ALTER TABLE ADD COLUMN (`store._ensure_ledger_columns`).
- The pending branch origin/cursor/keyword-volume-difficulty-ae89 (2026-11-14) adds `fetch_method` and `fetch_log` to rs_pages this way. rs_pages is keyed by url, so that branch hits the IntegrityError path.

My own reproduction: verify_real_schema.py uses the app's real schema (store.session_factory) and that branch's exact DDL. N cold-starts from the store, its engine then runs the ALTERs, N saves once, and O (warm, old schema) re-fetches a page in a job and saves.
- On e7fe6a3, after N catches up, N's base holds 7 entries for 6 rows. Every N save then fails with "UNIQUE constraint failed: rs_pages.url", and N's project edit never reaches storage.
- On 08bede0, with otherwise identical steps, N saves and storage gets both the edit and every row.

I also re-ran the reviewer's exp_mixed_schema.py and exp_mixed_log.py with the same results: IntegrityError, and O's row ("observed by o") deleted on O. verify_two_new.py (two new-schema instances plus O): on e7fe6a3 both new instances' saves fail, and their edits never reach O. On 08bede0, saves succeed and no rows are lost.

Why it blocks: this deploy alone does not trigger it, because 08bede0 and e7fe6a3 have the same schema. But it makes the next column-adding deploy, or a preview of one, break saves on warm new-schema instances. Production today handles that same case. The failure lasts until the instance recycles, and unsaved writes are lost then.

The scripts are in (review scratch)  (verify_real_schema.py, verify_two_new.py, vfix_exp_mixed_schema.py, vfix_exp_mixed_log.py). The patched copy is in fixcopy/ under the same folder.

**Skeptic's corrected fix.**

In `_base_apply` (src/aeo/ranksim/cloud.py), right after `known = {...}` and still inside the BEGIN IMMEDIATE transaction, first narrow any table whose delta lacks columns the base knows to the delta's columns. Then apply the deletes and adds as now:

```python
for schema in ("m", "p"):
    for table in _tables(conn, schema):
        if table not in known:
            continue
        theirs = {name for name, _t, _pk in _columns(conn, schema, table)}
        names = [c for c in known[table] if c in theirs]
        if not names or names == known[table]:
            continue
        updates = []
        for rowid, body in conn.execute("SELECT rowid, body FROM rows WHERE tbl = ?", (table,)).fetchall():
            row = _row_unpack(body)
            values = [row.get(c) for c in names]
            updates.append((_row_hash(names, values), _row_pack(names, values), rowid))
        conn.executemany("UPDATE OR REPLACE rows SET h = ?, body = ? WHERE rowid = ?", updates)
        conn.execute("UPDATE tables SET names = ? WHERE tbl = ?", (json.dumps(names), table))
        known[table] = names
```

The minus rows then match exactly. `_base_add_columns` already runs before every `make_base_delta` (lines 739 and 615) and before each applied delta (line 579), so it adds the column back with its default. Rows whose value in the working database differs from the default then travel as ordinary changes.

Verified in the patched copy:
- All three scenarios save normally and keep every row, and two new-schema instances agree on the new column.
- The 14 test files that mention cloud (including test_disk.py and test_publish_cloud.py) all pass.

I would not add the reviewer's "fail on a DELETE miss" check. Turning a silent miss into a hard failure is not needed for this fix.

Cost: the narrowing pass rewrites that table's base entries, and `_base_add_columns` rewrites them again, for each old-schema delta that touches the table. That only happens while two schemas overlap. A cheaper alternative is to match minus rows by narrowed hash and pad plus rows with the column defaults from the working database, but that is more code.


### 3. The disk-full wipe throws away the agreed state (base.rows and state.json's _installed) but keeps the working copy, so the next save reverts other instances' edits and brings back their deletions everywhere

Severity high. Blocks deploy: True. Lens: sync-paths. `src/aeo/ranksim/cloud.py:232`

**Sequence.** 1) Instances A and B are both at save 1. B holds project files foo/page.html='one' and foo/old.txt, a project bar, keyword k1 with notes='orig', and rank-check row k1.
2) A edits foo/page.html to 'two', deletes foo/old.txt, deletes project bar, sets k1.notes='edited by A' and deletes the k1 rank check. A saves 2 (or a compacting save, which any of the next 16 saves can be).
3) B refreshes. Catch-up writes to disk and hits ENOSPC. _sync_recovering sweeps and retries, hits ENOSPC again, then calls _wipe. _wipe rmtree's .cloud, which deletes base.rows and state.json, and resets _installed={}, _db={snapshot: None} and _seq=0.
4) The cold sync after the wipe merges every project against base {}. In _merge_files, b is None, so any local file that differs from storage counts as 'both changed' and B's stale copy is kept (it logs 'kept this server's copy'). Files and projects that storage deleted are never touched, so they stay. _installed[slug] is then set to storage's manifest. For the db, if a snapshot lies between B's last applied save and the newest one, _merge_full runs merge_sqlite(db, None, theirs) with no ancestor. That keeps B's stale keyed rows when the table has no newer stamp (rs_keywords has none), and keeps log rows that storage deleted. The base is then built from theirs.
5) B's next save happens on any write, a job's on_finish or update_auth. _projects_changed and make_base_delta(db, base=theirs) see B's stale files and rows as local changes and upload them.

**Outcome.** Every edit other instances made since this instance last synced is silently reverted on all instances. Their deleted files and deleted projects come back, and so do their deleted rows (rows only when a compaction falls in the gap). In the experiment, A and a fresh instance C end with foo/page.html='one', foo/old.txt and project bar present again, and, with a compacting save, k1.notes='orig' and the deleted rank check back. Production 08bede0 ends with 'two', no old.txt, no bar and the edited row, because its wipe dropped the local copy instead. So this is a regression introduced by the fix for review item 1. The same loss of _installed also follows a write of state.json that itself hits ENOSPC (it runs at the end of every _sync and is retried through the same recovery). A kill during the rmtree that leaves base.rows but not state.json has the same effect.

**Reviewer fix.** Keep the bookkeeping that makes merges three-way. In _wipe, do not delete .cloud/state.json, and do not reset _seq, _installed or _db. Drop only base.rows (plus the temps _sweep already removed). With _seq kept, the next _sync is a catch-up, not a cold sync. Deltas merge into the working db without needing the base (_apply_delta skips the base when it is missing), and compacting saves are read as deltas. Project merges stay three-way against _installed. Because has_base is False, the next save uploads a full snapshot of an up-to-date db, and the base is rebuilt from it after the put. If the disk is still too full for that, let refresh fail with CloudError rather than merging against an empty ancestor.

**Evidence.** Reproduced in (review scratch) exp_wipe_revert.py. The first two tar extracts raise ENOSPC. Default compaction gives: A after B's save: page.html 'one', old.txt True, bar True ['x.txt']; fresh C the same. With --compact the db is reverted as well: k1 'orig' and the deleted rank check k1 back on A and C. The same script against 08bede0 (old_exp_wipe_revert.py, code extracted with git archive into review2/old) keeps A's edits and deletions. The code involved: _wipe at cloud.py:227-236; _merge_files keeps local when m != b with b=None (1253-1255); _merge_project tombstone: _remove_if_empty cannot remove a non-empty folder (534-536); _merge_full no-base branch (620-624).

**Skeptic verdict.** I could not refute it. The sequence happens in this code and the result is wrong.

How it happens:
- `_wipe` (cloud.py:227-236) rmtree's `.cloud`, which deletes `state.json` along with `base.rows`. It also resets `_seq=0`, `_installed={}` and `_db={snapshot: None}`, but it keeps the working copy.
- The next `_sync` is therefore `_cold_sync`. Every project goes through `_merge_project` with base `{}`.
- In `_merge_files` (1250-1255), any file where the local copy differs from storage has `m != b` with `b=None`. The stale local copy is kept and the code logs 'kept this server's copy'. A file storage deleted has `t == b == None`, so it is left alone.
- A tombstoned project stays, because `_remove_if_empty` cannot remove a non-empty folder.
- `_installed[slug]` is then set to storage's manifest. On the next save, `_projects_changed` sees the stale files as local edits and uploads them.
- For the database, after a wipe `_apply_db` always re-merges the snapshot with no ancestor (`_merge_full` no-base branch, 620-624). When a compaction falls in the gap, B's stale keyed rows and rows that storage deleted are kept. `make_base_delta` against the new base then uploads them.
- If the gap holds only deltas, the database converges, because every delta is re-applied. Only files and projects are reverted in that case. This matches the claim.

Independent reproduction with a real kernel ENOSPC, no monkeypatching:
- Setup: B's working folder sits on an 8 MB tmpfs I mounted. A filler file, standing in for another writer on /tmp, leaves 16 KB free while B catches up on A's save 2. The filler is removed afterwards.
- With the new code, B's refresh fails: catch-up, then the sweep retry, then `_wipe`, then the cold sync also hits ENOSPC. Once the space is free, the next refresh finishes the cold sync against an empty ancestor. B's unrelated save then reverts A everywhere: page.html goes back to 'one', old.txt and project bar come back, and with a compacting save k1.notes is back to 'orig' and the deleted rank check returns.
- With production code (08bede0) the same run ends with 'two', no old.txt, no bar and A's row edits intact.

The reviewer's own script reproduces the same result.

Limits:
- It needs the disk to fill during a catch-up while this instance is behind storage.
- A `state.json` write that hits ENOSPC on its own is mostly harmless, because the instance has just caught up.

When this path is taken, though, an instance silently reverts edits other instances already saved and brings back their deletions for all instances. Production loses only this instance's unsaved local changes on the same path. So for this sequence the new code is worse, which is why it blocks the deploy.

Scripts:
- (review scratch) skeptic_wipe/real_enospc.py (usage: `<src dir> <tmpfs mount> [--compact]`)
- (review scratch) skeptic_wipe/check_full.py

**Skeptic's corrected fix.**

Keep the bookkeeping in `_wipe` and drop only the base (cloud.py:232-235):

```python
def _wipe(self) -> None:
    _unlink_db(self._base_db)  # the base only: state.json (_seq, _installed, _db) keeps every merge three-way
    self._sweep()
    self._db_clean = None      # no base: the next save uploads a full snapshot, which rebuilds the base
    self._hash_cache.clear()
```

Do not reset `_seq`, `_version`, `_manifest`, `_installed` or `_db`, and do not delete `state.json`. What this does:
- The retry after the wipe is a catch-up, not a cold sync.
- Project merges stay three-way against `_installed`.
- Deltas apply without a base: `_apply_delta` skips the base when it is missing.
- A compacting save is applied through its delta, and `db.full` is skipped.
- Because `_db_clean` is None and there is no base, B's next save takes the full path. It uploads a snapshot of the up-to-date database and rebuilds `base.rows` after the put.
- If the disk is still full, refresh raises CloudError instead of merging against an empty ancestor.

Also update the docstrings of `_wipe` and `_sync_recovering`, and the module's Disk paragraph.

I tested this on a scratch copy ((review scratch) skeptic_wipe/fixed):
- Both my real-ENOSPC run and the reviewer's script now keep A's edits and deletions on A, B and a fresh instance C, with and without compaction.
- B's save 3 is a db.full snapshot, B's base is rebuilt, and B's new row reaches A.
- tests/test_disk.py, test_hosted.py, test_publish_cloud.py, test_budget.py and test_review_fixes.py all pass. That includes the review item 1 test: the writer's connection keeps the same inode across the wipe, and the 'before wipe' and 'after wipe' rows plus local.json are uploaded.


### 4. _base_apply hashes a narrower delta's rows over the delta's columns, not the base's: after a deploy adds a column, the base keeps stale duplicates, saves fail with IntegrityError forever, and old-schema rows are deleted elsewhere

Severity high. Blocks deploy: True. Lens: sync-paths. `src/aeo/ranksim/cloud.py:1715`

**Sequence.** Two deployments save to the same Blob store: a preview that shares the production store (the module docstring plans for this), or old instances finishing requests and jobs during a rollout. Deploy Y's migration adds a column C to an existing table (as _ensure_ledger_columns does). Deploy X does not have C.
1) A Y instance's db gets C from its engine. The next _apply_delta or _write_save runs _base_add_columns, so Y's base knows C and every base row is re-hashed over names that include C. Y does not need to have saved for this.
2) An X instance changes an existing row R, for example a pull job refetching an rs_pages row, and saves a delta. X's minus and plus tables are created from X's schema, without C.
3) Y catches up. In _base_apply, layout() keeps only the delta's columns, so minus R is hashed without C, matches no base entry, and the old R entry stays. Plus R is stored hashed and packed without C. Y's base now holds two entries for R.
4) Y's next save runs make_base_delta. Both entries are 'missing', so two minus rows with R's primary key go into m.rs_pages, and the insert raises IntegrityError: UNIQUE constraint failed.
Log-table variant (rs_outcomes): X appends outcome q3. The stale entry hashed without C never matches anything Y computes, so every later Y save carries q3 in minus with C=NULL. On X instances, and on fresh instances whose db has no C yet, _merge_log deletes q3 because the match is on the shared columns and q3 is not in plus.

**Outcome.** Keyed tables: every save on every Y instance that applied X's delta fails ('Could not save to storage: IntegrityError'), and keeps failing after later deltas and compacting saves (the delta path never rebuilds the base). All of that instance's writes stay local, are flagged unsaved, and are lost when the instance is recycled. Log and ledger tables: rows written by the old deployment are deleted on old-deployment instances and on every instance that cold-starts afterwards, while the Y writer keeps them, so instances diverge. Production 08bede0, which advanced base.db with merge_sqlite, keeps saving and keeps q3 in the same runs, so this is a regression.

**Reviewer fix.** Keep every base entry hashed over the base's own column list for its table (known[table]). In _base_apply, when a delta table lacks some known columns, match each minus row by re-hashing the table's base entries on the delta's columns (as make_base_delta's re-hash branch does) and delete the matching entry. Before hashing and packing each plus row, fill the missing known columns with that column's default from the working db, which is the value merge_sqlite's delete-and-insert leaves in the working db. Apply the same rule in _merge_full's else branch, where a snapshot without the column is turned into a delta with make_base_delta(theirs, base).

**Evidence.** (review scratch) exp_column_skew.py: Y's base for rs_pages holds both {'url': 'https://a/1', 'html': 'v1', ..., 'note': ''} and {'url': 'https://a/1', 'html': 'v2', 'fetched_at': '2026-11-02'} (no note). Y save attempts 1 to 3 all fail with 'UNIQUE constraint failed: rs_pages.url'. exp_skew_persist.py: still failing after X's compacting save. exp_column_skew_log.py: X and a fresh instance end with outcomes [q1, q2, y0, y1, y2], so X's q3 is gone. The same scripts against 08bede0 (old_exp_column_skew*.py) save normally and keep q3.

**Skeptic verdict.** I could not refute it. The mechanism holds.

**How the bug happens.** In `_base_apply`, `layout()` hashes each delta row over `delta columns ∩ known[table]`. The base's own entries are hashed over all of `known[table]`, which `_base_add_columns` has just widened. `_apply_delta` calls `_base_add_columns` immediately before `_base_apply`, so Y does not need to have saved first.

**What follows for a narrower delta.**
- Its minus rows match no base entry.
- Its plus rows are stored hashed and packed without the new column.
- The base then holds two entries for the same key.

**Why every later save fails.** In `make_base_delta`, the sets `base_names` and `shared` are equal, so it takes the fast path and compares against the stored hashes. Neither entry matches the working row, so both are reported missing. Both are inserted into `m.<table>`, which has the real PRIMARY KEY, and the insert raises IntegrityError. That rolls back every save on that instance. Nothing rebuilds the base:
- `_apply_save` applies only the delta part of a compacting save.
- The `_merge_full` else branch runs `_base_apply` too, and also ends in IntegrityError (`v/mergefull.py`).

**The trigger needs two code versions writing at once.** Within one deployment, every writer goes through `store.session_factory`, which runs the migration first, so every delta carries the column. Two versions do overlap in these cases:
- During a rollout. DEPLOY.md says "old and new code overlap only for the minutes old instances keep serving". A job on an old instance runs up to its 240 s budget and saves when it finishes.
- During an Instant Rollback while jobs are still running.
- With a preview that shares the production store. DEPLOY.md says not to do this but calls it "still safe".

Column-adding migrations are routine: `_ensure_ledger_columns` added five columns on 2026-11-06, and `_base_add_columns` exists for exactly this case. e7fe6a3 itself changes no schema relative to 08bede0 (store.py and ledger.py are unchanged), so deploying it cannot trigger this. The next deploy that adds a column will.

**My own reproduction, with the real models and the real engine migration path** (`v/rollout.py`). An X instance saves an rs_pages change after a Y instance has served one read request and never saved. Every later save on Y fails with "UNIQUE constraint failed: rs_pages.url". Y's keywords never reach the store, and a fresh instance does not see them. The same script against the 08bede0 tree saves normally.

**The log-table variant is confirmed** (rs_outcomes, the table real migrations have used).
- Y's save 4 carries minus q3 (note NULL) and plus q3.
- From save 5 on, Y's saves carry minus q3 alone, because the stale entry is never matched.
- X deletes q3, because `_merge_log` matches on the columns both sides share.
- A fresh instance also deletes it, because the cold sync merges deltas into the old-schema snapshot before its engine adds the column.
- Instances that already have the column keep q3, so the instances diverge.

**Effect when it happens.** Every Y instance that applied the narrower delta while its db already had the column cannot save until it is recycled. Its writes are flagged unsaved and are lost with /tmp. Production keeps saving in the same sequence, so this is a regression; it should be fixed before any deploy that adds a column.

Experiments are in (review scratch) v/ (`rollout.py`, `mergefull.py`, `fix/`).

**Skeptic's corrected fix.**

Change only `_base_apply` in src/aeo/ranksim/cloud.py, and pass the working db path at its two reader call sites:
- in `_apply_delta`: `_base_apply(self._base_db, minus, plus, self.db_file)`
- in `_merge_full`'s else branch: `_base_apply(self._base_db, minus, plus, self.db_file)`

`_write_save` needs no change: its own deltas carry every known column, because `_base_add_columns` runs first.

The rule: a delta table is narrower when it lacks some columns of `known[table]`. Only those tables are treated differently. Every base entry stays hashed over the base's own column list.

**1. Minus rows of a narrower table.** Build an index of the table's base entries, keyed by their hash over the delta's columns. Delete every entry that matches a minus row. This is the same match `merge_sqlite` uses.
```python
def narrow_index(table, names):
    index = {}
    for rowid, body in conn.execute("SELECT rowid, body FROM rows WHERE tbl = ?", (table,)):
        row = _row_unpack(body)
        index.setdefault(_row_hash(names, [row.get(c) for c in names]), []).append(rowid)
    return index
...
if table in known and len(names) < len(known[table]):
    index = narrow_index(table, names)
    conn.executemany("DELETE FROM rows WHERE rowid = ?",
        [(rid,) for values in conn.execute(select).fetchall() for rid in index.get(_row_hash(names, values), [])])
else:
    ...existing delete by (tbl, h)...
```

**2. Plus rows of a narrower table.** Fill each lacking column with its default from the working db, by evaluating `PRAGMA table_info(...)` `dflt_value` as `_base_add_columns` does. That default is the value `merge_sqlite`'s insert leaves in the working db. Then hash, pack and group over `known[table]`. Skip a plus row whose narrow hash already matches an entry, because `merge_sqlite` keeps our row in that case.
```python
lacking = [c for c in known.get(table, names) if c not in names]
full = known[table] if lacking else names
index = narrow_index(table, names) if lacking else {}
fill = defaults_for(table, lacking) if lacking else {}
gi = full.index(group) if group in full else None
for values in ...p rows...:
    if lacking:
        if _row_hash(names, values) in index:
            continue
        row = {**fill, **dict(zip(names, values))}
        values = [row[c] for c in full]
    rows.append((table, _row_hash(full, values), values[gi] if gi is not None else None, pos, _row_pack(full, values)))
```
When the table is not narrower, keep `names` exactly as now, so a different column order cannot change any hash.

**Tested** on a copy (`.../review2/v/fix/src/aeo/ranksim/cloud.py`):
- The keyed, log, persist, rollout and `_merge_full` experiments all save, and q3 is kept on every instance.
- The full test suite passes. Three data-file tests fail only when run from the scratch directory, and fail there with the original code too.


### 5. A new instance's cold sync merges the deltas after an older snapshot into a db that has no migrated column yet, dropping that column's values; its later edits then blank them everywhere (not covered by _base_add_columns)

Severity medium. Blocks deploy: False. Lens: sync-paths. `src/aeo/ranksim/cloud.py:609`

**Sequence.** 1) A deploy adds column C (ALTER via _ensure_ledger_columns when the engine is created). The newest snapshot predates it.
2) Instance Y1, already running, sets C on row R. With the new _base_add_columns, Y1 uploads delta d carrying R.C='checked by hand'.
3) Y2 cold-starts. _merge_full's fresh path renames the snapshot, which has no C, into place as rank.db and disposes the engines. Within the same lock hold, _apply_db merges d with merge_sqlite. _merge_table compares only the columns ours, theirs and base all have (1427), so C is skipped. No engine has re-run the migration yet.
4) The next request's engine adds C with default '' to Y2's db, and Y2's next save adds '' to its base. Y2 now shows R.C='' and treats it as agreed.
5) Y2 later edits R, for example a page refetch. Its delta carries R with C='', and Y1 takes it because the stamp is newer.

**Outcome.** Until the next compaction, every instance that cold-starts after a column migration loses that column's values set since the snapshot, so instances diverge. The first edit of such a row on a new instance wipes the value on all instances. If the column is in the rs_predictions hash (project_slug is), rows merged this way no longer match their row_hash, and the ledger chain fails verification on that instance. Fix 4 of this change claims deltas now carry such values. They do between instances that were already running, but not into new ones. The drop itself predates the change: 08bede0 never uploaded the value at all.

**Reviewer fix.** Before rows from a snapshot or delta are merged into the working db, give the working db every column the incoming table has and it lacks. In merge_sqlite, for each table both sides have, run ALTER TABLE main.<t> ADD COLUMN <name> <type> DEFAULT <default> using theirs' PRAGMA table_info. Alternatively, run the app's migration (store._ensure_ledger_columns through a fresh engine) right after _merge_full renames the snapshot into place and before _apply_db applies the deltas. _base_add_columns then picks the column up for the base.

**Evidence.** (review scratch) exp_cold_before_alter.py: Y1's save 2 uploads the note, then 'Y2 note of page 1 after its cold sync: [('',)]', then after Y2's refetch 'Y1 note of page 1 after Y2's refetch: [('', 'v2')]'. The fresh path is cloud.py:600-610, the delta loop _apply_db 554-566, and the column intersection 1427.

**Skeptic verdict.** I tried to refute it and could not. The defect is real, but it predates this change and needs a future deploy that adds a column. e7fe6a3 adds none.

How it happens: _merge_full's fresh path (cloud.py:600-610) renames the old-schema snapshot into place and calls _dispose_engines, which pops the factory from store._factories. Only the next store.session_factory call re-runs _ensure_ledger_columns. In the same _cold_sync call, under the lock, _apply_db (554-566) applies every delta after that snapshot through merge_sqlite. _merge_table (1427) uses only the columns that ours, theirs and base all have, so the new column is skipped. When the engine is re-created, the ALTER gives the column its DEFAULT. The next save's _base_add_columns writes the same default into the base, so the row counts as agreed and nothing is uploaded.

Reproduced twice:
1. The reviewer's exp_cold_before_alter.py (Y2 sees ''; after Y2's refetch Y1 also has '').
2. My own run with the real engine lifecycle: store.session_factory at app start, refresh, then session_factory again, so the real _ensure_ledger_columns runs the ALTER on rs_outcomes.uploaded_at, and the old snapshot was made without that column. It is at (review scratch) skeptic_cold_alter/run.py. Y1 inserts outcome q1 with uploaded_at='2026-11-15T10:00' and saves a delta. The fresh instance Y2 ends with q1 uploaded_at=''. Y2.save() then returns None: it treats the blank as saved.

The sequence is realistic on Vercel. After a deploy, several fresh instances cold-start while the newest snapshot predates the ALTER, and the window stays open until the next compaction (up to COMPACT_DELTAS=15 saves). If a fresh instance does that compaction, its snapshot carries the blanks, so later cold starts lose the values for good. The project adds columns this way: five on 2026-11-06.

Not worse than production. I ran both experiments against 08bede0's code ((review scratch) old/src) and got the same final state. Y2 has the blank in both. Y2's refetch blanks Y1 in both. Production is broader still: its base never gains the column, so it does not upload the value even between instances that were already running. This change narrows the loss to instances that cold-start inside the window. The claim that fix 4 covers new columns is therefore incomplete, but nothing regresses.

**Skeptic's corrected fix.**

In merge_sqlite, give the working db every column of a shared table that theirs has and it lacks before rows are merged. _apply_delta already calls _base_add_columns after merge_sqlite, so the base then takes the column with the same default, and the minus/plus hashes match.

In merge_sqlite's table loop:

            if table not in our_tables:
                conn.execute(sql)
                our_tables[table] = sql
            else:
                _add_their_columns(conn, table)

def _add_their_columns(conn, table: str) -> None:
    have = {row[1] for row in conn.execute(f"PRAGMA main.table_info({_q(table)})")}
    for _cid, name, kind, _nn, default, _pk in conn.execute(f"PRAGMA t.table_info({_q(table)})").fetchall():
        if name not in have:
            tail = f" DEFAULT {default}" if default is not None else ""
            try:
                conn.execute(f"ALTER TABLE main.{_q(table)} ADD COLUMN {_q(name)} {kind or ''}{tail}")
            except sqlite3.OperationalError:
                pass  # e.g. a non-constant default: merged on the shared columns as before

Verification, on a patched copy at (review scratch) skeptic_cold_alter/fixsrc (the repository is unchanged):
- The reviewer's experiment: Y2 keeps 'checked by hand', and after Y2's refetch Y1 still has it.
- My engine-lifecycle run: Y2 keeps uploaded_at='2026-11-15T10:00'.
- tests/test_disk.py and tests/test_hosted.py: 48 passed.


### 6. (predates this change) A row a request thread commits during another thread's catch-up is marked clean and never uploaded, and the writer's save returns as if saved

Severity medium. Blocks deploy: False. Lens: sync-paths. `src/aeo/ranksim/cloud.py:581`

**Sequence.** 1) Write request W on instance B runs its forced refresh, releases the lock, and its handler starts.
2) Request R on B has a stale rs_sync hint (another user saved on instance A) or is due, so it takes the lock and catches up A's save. _apply_delta reads clean=True because the db matches _db_clean, merges the delta into the db, then runs _base_add_columns and _base_apply.
3) Meanwhile W's handler commits row X to rank.db. App threads do not take the cloud lock.
4) _apply_delta sets _db_clean = _stat_sig(db_file), a signature that already includes X. _merge_full's else branch does the same, with a longer window: make_base_delta(theirs) scans the whole snapshot before the merge.
5) W's middleware calls save(). _dirty() sees the db as clean and, if no project file changed, returns None.

**Outcome.** W gets 200 without the x-ranksim-storage: unsaved header, but X is not uploaded. Other instances do not have it, and it is lost if B is frozen or recycled before some later db write sends it along with the next delta. A job's last page writes can be lost the same way: its on_finish save then uploads only the job record, without its final rows.

**Reviewer fix.** Do not trust a stat signature taken after a merge that ran while app threads could write. In _apply_delta and in _merge_full's else branch, set _db_clean = None whenever rows were merged into the working db. The next save's make_base_delta then decides cleanliness, and it records sig_before before it reads the db, which is the safe order. If a full scan after every catch-up costs too much, compare PRAGMA data_version from a connection opened before the merge and mark the db clean only if no commit other than the merge's own happened.

**Evidence.** (review scratch) exp_clean_absorb.py inserts a row from a second connection during _base_apply. Output: "B's own save after its write: None", A has [from a, k1], B has [..., 'written by a request on b']. The same result against 08bede0 (the hook is in its base.db merge), so this predates the change.

**Skeptic verdict.** I could not refute this. The sequence is possible and the result is wrong.

**Why the sequence is possible.** `refresh()` takes `_lock` before it checks whether a look is due, so a request that arrives during a catch-up waits in the middleware. A request whose handler was already running is different: it is past its own refresh and holds no cloud lock. Its handler commits to rank.db through SQLAlchemy. Job threads do the same. In `_apply_delta` (cloud.py:568-581), the order is:
1. `clean = self._db_is_clean()`
2. `merge_sqlite` (BEGIN IMMEDIATE ... COMMIT)
3. `_base_add_columns` and `_base_apply`, which work only on base.rows and do not lock rank.db
4. `self._db_clean = _stat_sig(self.db_file)`

Any app commit between steps 1 and 4 is folded into the signature that counts as clean. Two kinds of commit land there:
- A writer blocked by the merge's IMMEDIATE lock gets in right after COMMIT, during step 3.
- A writer that held the write lock before BEGIN IMMEDIATE commits first, while the merge waits for it.

The writer's save then waits on the lock. After that, `_dirty()` sees the db as clean and returns None, or for a job, `_write_save` sets `db_kind = None` and uploads only the manifest with the job record. The response gets no `x-ranksim-storage: unsaved` header. The row is missing from storage and from every other instance. It goes up only if a later write on B changes the signature before B is frozen or recycled.

The `_merge_full` else branch has the same order. With a base present, `make_base_delta(theirs)` scans the whole snapshot before the merge, with rank.db unlocked, so the window there lasts seconds.

**Reproduced with real threads and no hooks** (`(review scratch) v5/race_threads.py`):
- Instance A changes 50 pages and saves. B catches up in one thread. In another thread, a "handler" on B commits one row through a SQLAlchemy engine at a random moment, then calls `b.save()`.
- At e7fe6a3, 3 of 12 runs lost the row: save returned None, and a fresh instance C did not have it.
- The timeline from `race_log.py` in the same folder: the merge ended at 0.278 s, W committed at 0.282 s, R took the signature at 0.425 s, and W's save saw a clean db and returned None.
- The reviewer's hooked `exp_clean_absorb.py` also reproduces it.

**Not worse than production 08bede0.** Production has the same pattern. Its `_apply_delta` merges the delta into base.db between COMMIT and the signature. `window.py` (same folder, 24 MB db) measured the gap between the merge's COMMIT and the signature:

| Delta | 08bede0 | e7fe6a3 |
|---|---|---|
| 1 page | 28 ms | 8 ms |
| 5 pages | 28-35 ms | 18-22 ms |
| 20 pages | 41-46 ms | 58-70 ms |
| 40 pages | 57-78 ms | 110-131 ms |

So the window is smaller for typical small deltas and larger for big ones. My 50-page run lost 0 of 12 on 08bede0, but only because production turned that large change into a full-only save, which readers merge with a short window after COMMIT. The new code makes it a compact save, and caught-up readers apply its delta. The seconds-long `_merge_full`-with-base window in the new code is on a rare path: the verify cold sync after pruning, or a full-only save, which is in practice only save 1. Overall this predates the change at a similar size, so it does not block the deploy. It should still be fixed.

**Skeptic's corrected fix.**

In `src/aeo/ranksim/cloud.py`, never record a stat signature taken after rows were merged into the working db.

1. In `_apply_delta`, replace `self._db_clean = _stat_sig(self.db_file) if clean else None` with `self._db_clean = None`, and drop the now unused `clean = self._db_is_clean()`.
2. In `_merge_full`, in the else branch only, replace `clean = self._db_is_clean()` with `clean = False`. Keep `clean = True` in the fresh-instance branch, where the snapshot is renamed into place.

The next save's `make_base_delta` then decides whether the db is clean. It takes `sig_before` before it reads, which is the safe order. If nothing changed, it still costs no upload: `_NothingToSave` sets `_db_clean = sig_before`.

The extra cost is one db scan, plus at most one get when the catch-up is more than 2 s old, and only on a save that would otherwise have skipped the db. Reads never save, so they cost nothing more.

I applied this to a scratch copy (`(review scratch) v5/fixed/src`). The threaded experiment then lost 0 of 12 runs, `exp_clean_absorb` uploads the row, and the cloud-related test files all pass, including test_budget, test_hosted, test_disk and test_review_fixes.


### 7. A write that commits while a refresh merges another instance's save is marked clean, so it is never uploaded. Compacting saves now reach caught-up readers through this path, which widens the window.

Severity medium. Blocks deploy: True. Lens: concurrency-memory. `src/aeo/ranksim/cloud.py:581`

**Sequence.** Setup: instance X has a clean db (`_db_clean` matches) and another instance has saved since X last looked.
1. Thread R on X runs refresh(): _catch_up -> _apply_save -> _apply_delta. It reads `clean = self._db_is_clean()` (True), then merge_sqlite(db_file, minus, plus) takes BEGIN IMMEDIATE on rank.db.
2. Write request W (or a job thread) on X commits a row meanwhile. In rollback-journal mode its commit waits in SQLite's busy handler (5 s pysqlite default) for the merge's lock.
3. The merge commits and releases rank.db. W's commit lands right away, while R is still in _base_add_columns and _base_apply. Those steps run on base.rows, with rank.db unlocked, and take 0.15-0.4 s for a 50-150 page delta, because every row is hashed and zlib-compressed.
4. R runs line 581, `self._db_clean = _stat_sig(self.db_file)`. The signature now includes W's row.
5. W's middleware save(): _dirty() is False, it returns None, and the response is 200 with no x-ranksim-storage header.
   Job variant: the job's last rows land in the window. on_finish runs record_job + save. _dirty() is True only because of _extra, and _write_save sees the db as clean (db_kind None), so it uploads the job record as 'done' without the rows.
The same assignment at line 625 (_merge_full, non-fresh branch) has a wider window: the clean check comes before make_base_delta(theirs) and _base_apply. The old code renamed the base there instead.
Why this is worse than at 08bede0: the old code applied a large delta as a 'full' save through _merge_full, where only a fast rename sat between the merge commit and the stat, so the busy-sleeping writer woke up after the stat. Now a compacting save carries the delta, and readers apply it through _apply_delta, with the slow _base_apply after the working-db commit. Small deltas lost writes this way before as well.

**Outcome.** The row exists only in X's /tmp. Other instances never get it: divergence, and the user sees the edit vanish when routed elsewhere. It is uploaded only if some later write on X changes the db signature and a save then scans. If X is frozen and recycled first (for example after a cron or job run, or the user stops working), the row is lost, while the request got 200 and the job is stored as done.

**Reviewer fix.** Never mark the working db clean from a stat taken after the merge. In _apply_delta (line 581) and in _merge_full's non-fresh branch (line 625), use the same rule as _write_save: take the signature before a scan, and mark clean only if the scan finds nothing.

    def _mark_clean_after_merge(self, clean: bool) -> None:
        if not clean or not self._base_db.is_file():
            self._db_clean = None
            return
        sig = _stat_sig(self.db_file)  # before reading: any later write changes it
        minus, plus = self._temp("minus", ".db"), self._temp("plus", ".db")
        try:
            self._db_clean = sig if make_base_delta(self.db_file, self._base_db, minus, plus) == 0 else None
        finally:
            _unlink_db(minus); _unlink_db(plus)

This adds one scan per merge (about 0.1 s for 70 MB, 0.8 s if every row changed) and no Blob call. A simpler alternative is to set `_db_clean = None` after every such merge, so the next save's scan confirms it. The fresh-instance rename branch can keep its stat, since no writer exists yet.

**Evidence.** Deterministic reproduction: (review scratch) exp/clean_race.py. A row committed by another connection when _base_apply starts gives:
- 'B db clean after refresh: True'
- "B's write request middleware save(): None"
- B local rows include 'written-on-b', but a fresh instance (= storage) has only ['from-a', 'one'].
- Job variant: the on_finish save uploads save 4 with job record {'status': 'done'}, while 'job-last-row' is missing from storage.

With real threads, a SQLAlchemy engine (default 5 s busy timeout), and the write started at a random time during B's catch-up of a 50-page delta: exp/clean_race_threads.py lost the row in 9 of 20 runs. The identical script against 08bede0 (old/clean_race_threads_old.py) lost 0 of 20.

exp/window.py shows the window directly:
- New code, 150-page delta: the clean check at 0.349 s, the working-db merge done at 0.491 s, the stat at 0.900 s.
- Old code: a 'full' save, merged and then stat'ed within milliseconds.
- For 5-page deltas both versions lose about 40-50% of writes that land inside the 70 ms refresh, so the pattern itself predates this change.

**Skeptic verdict.** The defect is real. The sequence can happen on Vercel: a write request's handler, or a job thread, runs without the lock after its own refresh. Meanwhile a second request on the same instance runs refresh() and applies another instance's save. That second request can be any POST, a GET whose cookie hint is newer, the 300 s periodic look, or a job-status poll (refresh with hint=stamp+1, every 5 s).

In _apply_delta, `clean` is read before merge_sqlite. A writer blocked on the merge's lock commits as soon as the merge commits. If it lands before the stat at line 581, the working db is marked clean with the row in it but not in base.rows. The middleware's save() then returns None. The on_finish save uploads the job record without its last rows. A row only goes up if a later db write on that instance changes the signature, so it is lost if the instance is recycled first.

Reproduced:
- The reviewer's deterministic script (exp/clean_race.py) behaves as described: clean=True, save() returns None, storage lacks 'written-on-b' and 'job-last-row', and the job record is uploaded as 'done'.

The pattern is not new. The same injection against 08bede0 (rf_clean/old_det.py) loses the row the same way. There the window is the second merge_sqlite, on base.db.

Whether it is worse than production was measured with real threads, the app's SQLAlchemy engine (5 s busy timeout), and a ~62 MB database built with the real Ranksim schema (rf_clean/realdb.py, race*.py):
- Ordinary catch-ups are about the same in both versions:
  - one-row edits: new lost 65 of 98, old 72 of 98
  - 50-page pulls: new 13 of 19, old 10 of 17
- Compaction catch-ups are clearly worse:
  - A compacting save now carries a delta, so caught-up readers apply it through _apply_delta, and the slow _base_add_columns/_base_apply step runs after the working db is unlocked.
  - In 08bede0 it was a full save read through _merge_full, where only the rename sat between the merge's COMMIT and the stat.
  - New lost 7 of 17 (pull10: 6 of 12, pull25: 1 of 5). Old lost 1 of 19 (1 of 12, 0 of 7).
- Over a cycle of 15 deltas plus one compaction, total exposure is about 1.5 times production's: roughly 550 ms against 370 ms for edit-heavy use.
- The reviewer's "0 of 20" for old comes from a toy where every catch-up went down 08bede0's full path. With realistic data, old also loses most writes inside ordinary catch-ups.

The line 625 branch (_merge_full, non-fresh with a base) has a wider window: the clean check comes before a full make_base_delta(theirs) scan. But it is nearly unreachable with a writer in flight. Full saves now come almost only from the bootstrap, and the long-idle verify path cannot overlap a write, because that write's own forced refresh would have done the verify.

Verdict: the bug predates this change, but the change makes it measurably worse on compaction catch-ups. The fix is two lines and also fixes the production behaviour. With it, the full pytest suite passes and the threaded races lost 0 of 12 compaction trials and 0 of 42 edit trials.

**Skeptic's corrected fix.**

Stop marking the working db clean from a stat taken after a merge into it. The next save's scan decides instead: it takes sig_before first, then marks clean only if it found nothing or uploaded. Keep the stat only in the fresh-instance rename branch of _merge_full.

In src/aeo/ranksim/cloud.py:

1. _apply_delta, line 581:
-        self._db_clean = _stat_sig(self.db_file) if clean else None
+        self._db_clean = None  # an app commit may land between the merge's COMMIT and any stat: the next save's scan decides
   (The `clean = self._db_is_clean()` line above it can be removed.)

2. _merge_full, non-fresh branch (around line 612):
-                clean = self._db_is_clean()
+                clean = False  # as in _apply_delta: never mark clean from a stat taken after a merge

Cost: after a catch-up, the next save() scans the working db once with make_base_delta. That already happens whenever the write touched the db. If that save finds nothing, it sets _db_clean = sig_before, so later saves are cheap again. At most one extra get when the save is not fresh. No call storm.

Verified on a copy at (review scratch) rf_clean/fixrepo:
- the full pytest suite passes;
- the reviewer's deterministic repro now uploads 'written-on-b' and 'job-last-row';
- the threaded races lose 0 of 12 compaction trials and 0 of 42 edit trials.

The reviewer's first option, a make_base_delta scan inside every refresh, is also correct, but it adds a full-database scan to every catch-up while holding the lock.


### 8. A disk-full wipe resets the manifest that cookie checks read without the lock: revoked sessions are accepted during the resync, and after a failed resync they are served the kept working copy

Severity low. Blocks deploy: True. Lens: concurrency-memory. `src/aeo/ranksim/cloud.py:234`

**Sequence.** 1. The owner signs out everywhere: revoke_all -> update_auth(revoked_ms) is saved. Instance X has caught up, so a stolen pre-revocation cookie is refused there.
2. A refresh on X hits ENOSPC twice. _wipe() sets `self._manifest = _empty_manifest()` and `_seq = 0`, leaves `_checked` set, and runs the cold sync under the lock. That takes seconds of downloads.
3. Meanwhile a request with the stolen cookie arrives on X. _require_login -> _signed_in -> Access.valid -> _revoked_since: stamp == 0, but `_checked` is not None, so it does not refresh and reads `cloud.auth`, which is now revoked_ms 0. The cookie is valid.
4. _sync_state computes trusted = True, waits on the lock and proceeds. The handler runs as the owner.
5. If the resync fails (CloudError), X keeps `_seq` 0 and the empty manifest. Each later request with the stolen cookie and no rs_sync hint passes _signed_in. refresh() is not due (`_checked` is recent) and returns False before the backoff check, so it is served.
Before this change, _wipe also deleted the working db and the projects, so step 5 served nothing. Now _wipe keeps them, so the full project data is served. The global login lockout (lock_until) is dropped in the same window.

**Outcome.** A revoked session cookie can read and write all project data on that instance. This lasts during the post-wipe cold sync. After a failed resync it lasts until REFRESH_EVERY_SECONDS (300 s) has passed since the last good check, and again in each later gap whenever the next due look keeps failing. Sign-out-everywhere does not hold on that instance.

**Reviewer fix.** In _wipe, keep the shared login state of the manifest being dropped, and make the instance count as never checked. Then ordinary requests do not trust it until a resync succeeds:

    def _wipe(self) -> None:
        auth, jobs = self.auth, dict(self._manifest.get("jobs") or {})
        shutil.rmtree(self._meta_dir, ignore_errors=True)
        self._sweep()
        self._seq, self._version = 0, None
        self._manifest = {**_empty_manifest(), "auth": auth, "jobs": jobs}
        self._checked = None  # due again: a failed resync answers 503 instead of serving the kept copy as checked
        ...

The cold sync replaces _manifest with storage's newest one, so the kept auth only covers the window and the failure case.

**Evidence.** Reproduction with the real Access(shared=cloud) and CloudState: (review scratch) exp/revoked_wipe.py. Output:
- 'B accepts the stolen cookie after catching up the revocation: False'
- after two forced ENOSPC errors and a slow cold sync: 'during B's wipe + cold sync, B accepts the stolen cookie: True'
- after the resync fails: 'after the failed resync, B accepts the stolen cookie: True | B._seq 0 | B._checked set: True'
- 'a non-forced refresh (an ordinary GET) right after: False -> the request is served from the kept working copy: ['secret project data']'

Code paths: access.py _revoked_since refreshes only when `stamp == 0 and _checked is None`, and otherwise reads shared.auth. cloud.py:234 resets _manifest (auth included). refresh() returns False when nothing is due, before the backoff check at line 309.

**Skeptic verdict.** I could not refute it. The sequence can happen. With shared=cloud, Access._revoked_since never stores the manifest's revoked_ms in Access._revoked_ms. It reads cloud.auth on every check, with no lock, and calls refresh() only when stamp == 0 and _checked is None. _wipe (cloud.py:234) swaps in _empty_manifest(), which sets auth.revoked_ms and lock_until to 0, and sets _seq to 0. It leaves _checked set, and _extra holds only changes not yet saved. So on an instance that learned of the revocation from another instance's save, a pre-revocation cookie validates in both _require_login and _sync_state from the wipe onward. Requests run in threads on Fluid compute, and refresh() holds the lock during the cold sync while the cookie check does not wait for it.

I ran the reviewer's script and my own variants against e7fe6a3 and against a git-archive copy of 08bede0 (scratchpad/review2/refute_wipe/variants.py and revoked_wipe_any.py):
- Both versions accept the stolen cookie while the post-wipe cold sync runs. In both, after a successful sync the waiting request's handler runs on the restored data, and the cookie is refused only after that. So the window during the resync is NOT new: production's _wipe also reset _manifest and _seq.
- After a failed resync, both versions keep accepting the cookie (_seq 0, empty manifest, _checked still set). Production had deleted the working copy, so a GET that is not due was served an empty db ('no such table: rs_keywords'). The new _wipe keeps rank.db and the projects, so the same GET is served the full working copy (['secret project data']). That part is new.

Corrections to the claim:
- In the failed state the new exposure is read-only. A write forces a refresh that has to succeed, and that case is the same as production's window during the sync.
- There are no later gaps. _checked does not advance on failure, so once REFRESH_EVERY_SECONDS has passed since the last good check, every request is due and gets the 15 s backoff 503 or a new sync attempt.
- The window opens only when the refresh that hit ENOSPC was forced (a write) or hinted while _checked was recent. It lasts at most 300 s from the last good check.
- The new code makes this state more likely than before. Its wipe frees only .cloud (about 35 MB). The cold sync then needs room for the snapshot temp and a new base, so a genuinely full disk tends to fail the resync again. Production's wipe freed about 135 MB, so its resync usually succeeded.

Other conditions: an attacker who holds a stolen pre-revocation cookie, a revocation made on another instance, and a double ENOSPC on X. Severity is low.

I tested the keep-auth fix by monkeypatching _wipe. With the same failing cold sync, the stolen cookie is refused both during the sync and after it fails ('during: False', 'after: False', auth.revoked_ms kept).

**Skeptic's corrected fix.**

In CloudState._wipe, carry the shared login state (and job records) of the manifest being dropped into the fresh one. Assign it in a single statement, so lock-free readers never see an empty auth:

    def _wipe(self) -> None:
        kept = {"auth": self.auth, "jobs": dict(self._manifest.get("jobs") or {})}
        shutil.rmtree(self._meta_dir, ignore_errors=True)
        self._sweep()
        self._seq, self._version, self._manifest = 0, None, {**_empty_manifest(), **kept}
        self._installed, self._db, self._db_clean = {}, {"snapshot": None, "applied": []}, None
        self._hash_cache.clear()

This is enough. A successful cold sync replaces _manifest with storage's newest one, and auth values only grow. A _bootstrap on an empty store writes the kept revocation into save 1, which is correct. The reviewer's extra `self._checked = None` is not needed for the security issue. It would only change availability: every cookie check would wait on the lock during the cold sync, and the owner would get 503s instead of the kept copy after a failed resync.


### 9. The disk-full last resort can no longer succeed: the instance is left at seq 0 with no bookkeeping, and every later refresh repeats three failing full-history cold syncs (Blob op storm, 503s, shared auth state forgotten)

Severity high. Blocks deploy: True. Lens: disk-and-budget. `src/aeo/ranksim/cloud.py:341`

**Sequence.** 1) Instance B is at seq N with a valid base.rows. Disk pressure (a large re-pull delta on a growing db, or anything else on /tmp) makes B's catch-up fail with ENOSPC. The sweep runs, the retry fails again, and _sync_recovering calls _wipe(). The new _wipe frees only .cloud (base.rows, about 0.5 db, plus state.json) and resets _seq=0, _installed={}, _db and _manifest. 2) The cold sync that follows keeps the working db and project files, which the old wipe deleted. It still stages a full second copy of every project file, because installed={} puts every older-holder file into `needs` even though local already has it. Then it unpacks the full snapshot (`theirs`, 1x db) and builds base_tmp (0.5x db). Its peak is therefore about at-rest + db + projects + save tgz, more than any normal step needs and far more than the wipe freed. It fails again whenever a catch-up has already failed twice. 3) _seq stays 0, so every later refresh runs _sync -> _cold_sync, sweep -> _cold_sync, then _wipe -> _cold_sync. That is three cold syncs, each re-reading the pointer, probing heads and re-downloading the newest save, every wanted save and every holder save (the whole pull history), and each fails again. Non-forced requests repeat this every FAILURE_BACKOFF_SECONDS (with _checked stale, every request is due, and every signed-in rs_sync hint is greater than 0). Write requests (force=True) and job on_finish saves (_commit -> _cold_sync, twice) skip the backoff. 4) While B is stuck, its _manifest is _empty_manifest(), so CloudState.auth returns revoked_ms=0 and lock_until=0, and Access._revoked_since no longer sees a sign-out-everywhere or the global login lockout.

**Outcome.** I measured this on a real tmpfs with the same free space for both versions (scratchpad review2/code/exp_wipe2.py MODE=update SNAP=1, exp_storm.py). With 6-10 MB free and a 60-pull history, 08bede0's wipe recovers on the first refresh (72 ops once, then 0-1 per refresh). e7fe6a3 returns 503 on every refresh, stays at seq 0, and spends 39-90 Blob ops per refresh. At production size (about 60+ holder saves, so about 3 x 68 ops per refresh, up to 4 refreshes a minute while someone uses the app), that is tens of thousands of ops an hour. The 10,000/month Hobby simple-op cap would be used up in minutes, after which Vercel blocks Blob for 30 days for every instance. B also re-downloads the full history (data transfer) on every attempt. B's auth shows {'lock_until': 0, 'revoked_ms': 0} after the failure, while 08bede0 kept the values. This is a regression: 08bede0's post-wipe cold sync needed about at-rest + tgz, so it recovered. The tests miss it because they fake disk-full by raising from merge_sqlite, which never models the post-wipe sync's larger disk need.

**Reviewer fix.** Remove the wipe. After the sweep retry also hits a full disk, let the error reach refresh(), which backs off. _seq, base.rows, state.json and _installed then stay intact, so the next try is an ordinary catch-up (1-2 gets) that succeeds as soon as space frees up:

    def _sync_recovering(self, now):
        try:
            return self._sync(now)
        except Exception as exc:  # noqa: BLE001
            if not _disk_full(exc):
                raise
        self._sweep()
        return self._sync(now)  # still full: refresh() reports it and backs off; seq, base and bookkeeping stay

Then drop _wipe or leave it unused, and update the module docstring and test_a_disk_still_full_... accordingly. I verified this with the patched module (scratchpad review2/code/fixpatch.py): 2 Blob ops per failed refresh, the seq is kept, and the instance recovers with no data damage once the pressure is gone.

**Evidence.** exp_storm.py (60 pulls, 3 sites, B on a tmpfs, then a re-pull compacting save from A). 6 MB free: [08bede0] (ok, 62, 72), (ok, 62, 1), (ok, 62, 0) ... ; [e7fe6a3] (503, 0, 15), (503, 0, 39) x4. 10 MB free: [e7fe6a3] (503, 0, 74), (503, 0, 90) x4. With the fix: (503, 61, 2) x5. exp_wipe2_auth.py: [08bede0] B auth after refresh 1: lock_until 1763200000, revoked_ms 1763200000000; [e7fe6a3] B auth after refresh 1: lock_until 0, revoked_ms 0. Code: _wipe (line 227) rmtrees .cloud and resets _manifest. _cold_sync's needs loop (lines 422-432) has no local-hash check, so it stages all files when installed is {}. _merge_full's base-missing branch (line 621) needs theirs + base_tmp next to the kept db.

**Skeptic verdict.** I tried to refute this and could not. The code path is as described, and I reproduced it.

How it happens:
- `_wipe` (cloud.py:227) now removes only `.cloud`: base.rows (about 0.5 of the db) and state.json. It resets `_seq`, `_installed` and `_manifest`.
- The cold sync that follows keeps the working db and project files. With no base, `_merge_full` goes to line 621 and needs `theirs` (one full db) plus `base_tmp` (half a db) next to the db it kept. So it needs about at-rest + 1 db.
- A catch-up of an ordinary delta needs only about 3 x the changed part of the db. So whenever such a catch-up fails twice for lack of space, the cold sync after the wipe fails too.
- `_seq` then stays 0. Every later attempt runs three cold syncs: `_sync`, the retry after the sweep, then wipe and sync again. Each one re-reads the pointer, probes heads, and downloads the newest save, the snapshot, the deltas and (after the wipe) every holder save again.
- Attempts happen every 15 s for any signed-in request, because any cookie hint is greater than 0. Forced writes and job saves skip the backoff.
- `_manifest` stays empty, so `auth` returns `revoked_ms` 0 and `lock_until` 0. `Access` then no longer sees a sign-out-everywhere or the global login lockout.

Reproduced on my own tmpfs:
- The reviewer's `exp_storm.py` with 6 MB free: 08bede0 gives (ok, 62, 72), then 0-1 ops per refresh. e7fe6a3 gives (503, 0, 15), then (503, 0, 39) on every refresh.
- `exp_wipe2_auth.py`: e7fe6a3's auth after the failure is `lock_until` 0 and `revoked_ms` 0. 08bede0 keeps both values.

The reviewer compared both versions at the same free space. For a fairer test of "worse than production", I gave both the same total space (`mine/abs_cap.py`: B's folder alone on a tmpfs of fixed size, 60-save history).
- Large re-pull (a compacting save that rewrites about 40% of rows): at 30-34 MB, 08bede0 recovers through its wipe (72 ops once, then 0-1). e7fe6a3 returns 503 on every refresh, stays at seq 0, and makes 74-90 Blob ops per refresh. From 38 MB up, both are fine.
- Small deltas (a 10% re-pull, or new pages): e7fe6a3 does better. It gets stuck only at 22-24 MB, where 08bede0 does not even fit at rest. 08bede0 itself gets stuck and storms at 28-30 MB, where e7fe6a3 is fine.

So the storm mechanism also exists in production. What changed is that the new last resort helps in none of my runs. For large re-pulls there is a band where production recovers and this commit gets stuck. On real data (70 MB db, 30 MB of projects, 512 MB /tmp) that band needs either about 300 MB of other files on /tmp or a db of about 190-220 MB. At today's sizes neither version reaches this path.

I still set blocks_deploy to true for three reasons:
- In that band the outcome is a 503 on every attempt, plus a Blob op storm that can use up the Hobby cap. Vercel then blocks Blob for 30 days for every instance.
- Sign-outs and the login lockout are forgotten while an instance is stuck.
- When the wipe path does eventually succeed, the merge without a base reverts other instances' edits. In `exp_wipe2_auth.py` a deleted row came back, an edited row was reverted, `project.json` went back to v1 and a deleted file came back.

The fix removes all of this at no cost. With it patched in, a stuck refresh costs (503, 61, 2): the seq, base and auth state are kept. After the pressure goes away the instance catches up in 2 ops. In the large re-pull band it stays at 503 (cheaply) until space frees up or the instance is recycled, where 08bede0 recovered. That is acceptable because 08bede0's recovery deleted files other threads were writing (the earlier high finding). `test_disk.py` and `test_hosted.py` pass with the fix, except the one test that asserts the wipe.

**Skeptic's corrected fix.**

In src/aeo/ranksim/cloud.py, drop the last resort. After the sweep, retry once. If the disk is still full, let the error reach refresh(), which records `_failed_at` and backs off. `_seq`, base.rows, state.json, `_installed` and `_manifest` (auth) all stay as they were:

    def _sync_recovering(self, now: float) -> bool:
        """_sync, and when the disk is full: remove temp files and try once more. Still full: the error goes to
        refresh(), which backs off. The seq, base and bookkeeping are kept, so the next try is an ordinary catch-up."""
        try:
            return self._sync(now)
        except Exception as exc:  # noqa: BLE001 - only a full disk is handled here
            if not _disk_full(exc):
                raise
            print(f"ranksim: the disk is full while reading storage ({str(exc)[:120]}); removing temp files.", flush=True)
        self._sweep()
        return self._sync(now)

Then:
- Delete `_wipe`, which is now unused.
- Update the module docstring: replace "if it is still full, the base and bookkeeping are dropped and storage is merged into the working copy again, once" with "if it is still full, the refresh fails and backs off; nothing local is dropped".
- Rewrite `tests/test_disk.py::test_a_disk_still_full_drops_only_the_bookkeeping_and_keeps_writes_in_flight` to check that:
  - refresh raises `CloudError` while `merge_sqlite` keeps failing,
  - `b.stamp`, `.cloud/base.rows`, `.cloud/state.json` and `b.auth` are unchanged,
  - once `merge_sqlite` works again, the next refresh catches up to `a.stamp` with no more than 2 store calls,
  - the local row and file still reach A with the next save.

Verified in a scratch copy ((review scratch) mine/repo): `test_disk.py` and `test_hosted.py` pass, apart from the wipe test that has to be rewritten.


### 10. After the wipe, stale local files and rows count as local edits and are re-uploaded: other instances' edits are reverted and their deletions are resurrected

Severity high. Blocks deploy: True. Lens: disk-and-budget. `src/aeo/ranksim/cloud.py:1254`

**Sequence.** 1) Instance B is at seq N. Instance A makes save N+1, for example a compacting save after a big re-pull. It edits alpha/project.json, deletes beta/pages/p1.html, deletes rs_keywords row 'query 5' and edits row 'query 6'. 2) B's catch-up of N+1 runs out of disk twice. If it fails while the stream is extracted (a large db.minus/db.plus), no project has been merged yet. _wipe then empties _installed and deletes base.rows, but keeps B's working files and rows, which are still at N. 3) The cold sync after the wipe either succeeds or, as in the finding above, succeeds later once the pressure goes away. _merge_project runs with base={}. alpha/project.json has m=v1 and t=v2, and m != b(None), so _merge_files counts it as 'both changed', keeps B's stale v1 and logs 'kept this server's copy'. p1.html has t=None=b, so it is kept. For the db, _merge_full takes the no-base branch, merge_sqlite(db, None, theirs). Row 'query 5' (deleted in theirs) is never touched, so it stays. Row 'query 6' differs on both sides with an equal stamp, so B's old value stays. base.rows is then built from theirs. 4) B's next unrelated write saves. _projects_changed sees local v1 != installed v2 and p1.html as an extra file. make_base_delta sees 'query 5' and the old 'query 6' as rows the base lacks. All of them are uploaded as B's changes. 5) Every other instance applies the save: base=v2 and mine=v2, so theirs (v1) is taken.

**Outcome.** Measured on a real tmpfs (exp_wipe2.py MODE=update SNAP=1, 6 MB free). After the pressure went away, B made one unrelated save, and then A showed: deleted row back=True, edited row project='demo' (reverted from 'moved'), alpha/project.json={'v': 1} (reverted), deleted file back=True. Under 08bede0, in the same run, all four stayed correct, because its wipe deleted the local copy and the cold sync took storage's version. With 10 MB free (the failure came after the project merge), only the rows were damaged. The edits and deletions are lost for every instance, with no error shown. A file edit always reverts, because files have no timestamps, and deleted rows and files always come back. This regression comes from the fix for review item 1. That fix treated every local difference as unsaved work, but without a base a stale copy cannot be told apart from a local edit.

**Reviewer fix.** Do not drop the ancestor. The fix above, which removes the wipe and keeps _installed, _seq, _db and base.rows, means the next sync is a catch-up with the correct ancestors, so stale files and rows are updated rather than re-uploaded. If any last resort that frees space is kept, it must keep state.json (_installed, _seq, _db and _manifest, a few KB) so that project merges still have their base. For the db it must never run merge_sqlite(db, None, theirs) on a working copy that may be stale. It should keep base.rows, or refuse the merge and report the error.

**Evidence.** exp_wipe2.py, MODE=update SNAP=1 phi=6: '[08bede0] ... on A afterwards: deleted row back=False, edited row project='moved', alpha/project.json={"v": 2}, deleted file back=False' versus '[e7fe6a3] ... deleted row back=True, edited row project='demo', alpha/project.json={"v": 1}, deleted file back=True'. phi=10: e7fe6a3 'deleted row back=True, edited row project='demo''. Insert-only delta at phi=4 (exp_wipe.py): e7fe6a3 'alpha/project.json={"v": 1}, deleted file back=True'. With the fix patched in: all four correct. Code: _wipe (227-236) clears _installed. _merge_files (1253-1255) keeps m when m != b. merge_sqlite(self.db_file, None, theirs) (line 621): _merge_keyed only touches keys present in theirs or base, and _merge_log deletes only rows the base has.

**Skeptic verdict.** I could not refute it. The claim holds, and I reproduced it two ways.

How it happens:
- `_wipe` (cloud.py:227-236) now deletes all of `.cloud`: state.json and base.rows. It sets `_installed={}`, `_seq=0` and `_db.snapshot=None`, but keeps the working files and rows. A wipe only follows a failed sync, and a catch-up runs because a newer save exists, so those kept files and rows are almost always older than storage.
- The cold sync then merges with no common ancestor.
  - Files: `_merge_files` gets base={}. A file A edited has m!=b(None), so B's old copy is kept (line 1253). A file A deleted has t==b==None, so B's copy is kept. A project A deleted (gamma) is kept too, because its tombstone has files={}.
  - Database: when the newest manifest's snapshot is newer than B's state (a compacting or full save), `_merge_full` runs `merge_sqlite(db, None, theirs)` (line 621). `_merge_keyed` and `_merge_log` only touch keys that are in theirs or in the base. Rows A deleted therefore stay. Edited rows are 'both changed', and rs_keywords has no timestamp column, so B's old value wins.
- base.rows is then built from storage. B's next save (`_projects_changed`, `make_base_delta`) uploads the stale files and rows as B's own edits. Every instance accepts them, because for them mine == base.

What I ran:

1. Deterministic ENOSPC injected into B's first two sync attempts, using the real rs_keywords schema (`skeptic_stale/exp.py`).
   - e7fe6a3:
     - compact/early: k5 came back, k6's edit reverted to 'orig', a deleted rank-check row came back, alpha/project.json went back to v1, beta/p1.html came back, and deleted project gamma came back.
     - delta/early: all the file and project damage above.
     - compact/late: only the rows were damaged.
     - delta/late: correct.
   - 08bede0: correct in all four cases.
2. The reviewer's exp_wipe2.py on a real 60 MB tmpfs, MODE=update SNAP=1.
   - 6 MB free: e7fe6a3 brought the deleted row and file back, reverted the edited row to 'demo', and reverted alpha/project.json to v1. 08bede0 kept all four correct.
   - 10 MB free: e7fe6a3 damaged only the rows.

The 'early' failure happens naturally. `_apply_save` reads the whole stream before merging anything, and the db.minus, db.plus and db.full parts are the largest writes. A space shortfall during a catch-up repeats the same way after the sweep, so `_wipe` follows. After a wipe there is no way out: if the process restarts, state.json is gone and the next sync is the same base-less cold sync.

Why it blocks the deploy: in the same sequence 08bede0 leaves every instance correct, because its wipe deleted the local copy and reloaded it from storage. 08bede0's own risk on this path (review item 1) is losing this instance's in-flight or unsaved writes. The new code instead overwrites edits that other instances had already saved, brings back their deletions, and spreads that to every instance with no error. That is worse.

**Skeptic's corrected fix.**

Drop the last resort instead of narrowing it. Without base.rows and the installed manifests, a stale working copy cannot be told apart from local edits, so no wipe that removes them is safe. Remove `_wipe`. In `_sync_recovering`, sweep and retry once; if the disk is still full, let the error propagate. `refresh()` then reports it and backs off from the end time. `_seq`, `_installed`, `_db`, `_db_clean`, state.json and base.rows all stay, so the next sync after the pressure eases is an ordinary catch-up with the correct ancestors:

```python
def _sync_recovering(self, now: float) -> bool:
    """_sync; when the disk is full, remove temp files and try once more. Still full: the error goes up and refresh()
    backs off. The base, state.json, _seq, _installed and _db are never dropped: without the common ancestor a stale
    working copy would be merged and saved as local edits, reverting other instances' changes."""
    try:
        return self._sync(now)
    except Exception as exc:  # noqa: BLE001
        if not _disk_full(exc):
            raise
        print(f"ranksim: the disk is full while reading storage ({str(exc)[:120]}); removing temp files.", flush=True)
    self._sweep()
    return self._sync(now)
```

Also delete `CloudState._wipe`. Update the module Disk paragraph so it says storage is never merged without the base. Rewrite the wipe test in tests/test_disk.py (around lines 330-368). It should expect a CloudError while the disk is full, and after the pressure goes away the local rows and file should still be intact and saved by the next save.

The sweep-only patch, run with exp_fix.py on the four injection cases, gave all-correct results: no revert and no resurrection, and B's own row was still uploaded.

The sweep-only version is no worse on the Blob budget than the current code. Each failed refresh is two catch-up attempts instead of three syncs that include a full cold sync.

Any future space-freeing step must keep state.json and base.rows. It must also never run `merge_sqlite(db, None, theirs)` or `_merge_files` with base={} on a working copy that may be stale.

