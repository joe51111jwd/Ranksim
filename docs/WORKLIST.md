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
- [x] A2b. 2026-11-10 lookout: new PR #4 (`cursor/t006-html-coverage-175f`, HTML snapshot join),
  PR #5 (`cursor/integration-review-175f`, base PR #2: held-out confidence, project slugs in the
  ledger, a frozen 20/10 split, a Nov 6 to Nov 9 forward eval), PR #6 (`docs/MISSION.md`,
  `AGENTS.md`, UI brief), PR #1 moved to ca8dff2 (SERP JSONL import, CSV track record), and
  `claude/kind-hopper-lglg98` (the Studio, on top of PR #5, no PR). `main` has not moved. Dry-run
  merges against this branch: PR #6 none; PR #1 and PR #4 `tests/conftest.py`; PR #5 and
  kind-hopper 16 files (README, crawl/fetch.py, crawl/urls.py, benchmark.py, check.py, cli.py,
  confidence.py, demo.py, groundtruth.py, lambdamart.py, ledger.py, serve.py, store.py, conftest,
  test_product_layer.py, test_ranksim.py). PR #5 holds byte copies of this branch's benchmark.py
  (eff6162) and ledger.py, then diverges. Nothing on any board assigns work to this PR.
  Decision: do not merge PR #5 or kind-hopper into this branch (they are not on `main` and carry
  UI-owned files and `evals/` data). Make this branch interoperable instead (section I), so either
  merge order resolves by simple rules.
- [ ] A3. When `main` moves: merge it here, resolve, run the full suite on 3.11 and 3.13, push,
  confirm CI. Repeat each time `main` moves. Resolution guide if PR #5 lands first: take `main`
  for UI files (serve.py, demo.py, workspace*, project.py); keep this branch's crawl/*, benchmark.py
  and snapshot.py. ledger.py and confidence.py carry PR #5's API (hash version 2, project slugs,
  `product_label`, `held_out`) but not all of its behaviour: PR #5 tests that expect CLEARER from a
  head-to-head threshold on a neighbour gap, scoring of blank-slug rows, or scoring of rows without
  a write time (its forward ledger) will fail and must be updated with the merge.

## I. Interoperability with PR #5 and PR #6 (added 2026-11-10)

Rules from `docs/MISSION.md` (PR #5/#6) that this branch follows: never present a training,
same-snapshot or test-tuned score as accuracy; select on training data only and score held-out and
forward data once; no merge to `main` or deploy without James; no purchases; reports in short plain
sentences with no em or en dashes.

- [x] I1. Ledger interoperability: `project_slug` on predictions and outcome provenance columns
  (project_slug, source_file, source_sha256, uploaded_at) with server defaults; hash version 2
  exactly as PR #5 ad8020f, version-aware `verify_ledger`; writers pass an explicit slug; scoring
  matches outcomes to the same project; keep `record_check`; a NOT NULL failure is not retried as a
  fork race. Done: hash checked against PR #5's own `_row_hash`; `default` project for the CLI and
  local demo; migration adds the columns as `DEFAULT ''` like PR #5.
- [x] I2. Ledger wall clock: store `recorded_at` from the clock next to the caller's `created_at`;
  scoring counts an outcome only if it was observed after the row was actually recorded, and
  reports backdated rows as protocol-time replays, not forward evidence. Done: `recorded_at` sits
  inside the hashed payload, so PR #5's version 2 verify still passes and the time cannot be moved
  unnoticed; an outcome dated in the future is refused.
- [x] I3. Confidence API: `Calibration.held_out`, `product_label(gap, synthetic=..., kind=...)`
  routed to the predicted-rank or head-to-head threshold, synthetic data always UNSURE, a
  too-few-clusters note, and one helper that labels a predicted rank from ungated scores so UI
  callers can switch to it. Done: `product_label` keeps PR #5's signature and returns (label,
  note) from the predicted-rank threshold; `rank_confidence` and `pair_confidence` are the helpers;
  a threshold without the held-out flag never gives CLEARER.
- [x] I4. check.py: UNSURE when fewer than half of the labeled candidates have stored pages, and a
  `synthetic` flag that forces UNSURE. Done: both also set every competitor row to UNSURE; the
  draft's own URL is not counted as a competitor.
- [x] I5. Label import accepts a Serper export whose URL column is `link` (as PR #5 847ef71).
- [x] I6. Docs: `--final` is a same-snapshot holdout, not accuracy; README numbers labeled as
  same-snapshot agreement; E14 recomputes PR #5's committed forward figures with ties as half
  (analysis of f76f7f1, not a new claim).
- [x] I7. Adversarial review of I1 to I6 (workflow); fix confirmed defects with regression tests.
  Done: 5 lenses over 2088b15..e883b45, 18 findings each given an independent reproducer; 11
  confirmed (3 medium, 8 low) and fixed in cf4369c (ledger) and 7ce2dd1 (benchmark, snapshot);
  7 refuted; 3 low ones not verified (one duplicate, one doc line fixed here, one in serve.py below).

Requests for other roles, drafted here and not posted (posting on another role's PR needs James):
- PR #5 / SEO Tool Builder: the thin-draft CLEARER bug fixed in B2 (`product_label` on gated
  neighbour gaps); `_same_project` never resolves blank-slug rows; `evals/` is not excluded in
  `.vercelignore`/`vercel.json` at 384c4a6 although Vercel builds the branch; "pre-registered"
  wording and hand-set `created_at` on forward ledger rows; point `workspace._gap_confidence` and
  `project._confidence` at the I3 helper (`rank_confidence`); after that change PR #5's
  `test_in_sample_clearer_is_not_a_product_label` must set the predicted-rank threshold, because
  `product_label(2)` on a head-to-head threshold alone is the B2 bug; PR #5's benchmark.py `labeled_coverage` imports
  `EVALUATION_EXCLUDED_SOURCES`, which its store.py lacks.
- PR #2 (UI owner): the demo banner shows `fit.calibration.note` (the head-to-head note) while the
  predicted rank is labeled from `adjacent_note`; show the note that matches each label.
- PR #5: its forward ledger rows have no write time; this branch's ledger counts them as undated and
  does not score them (I7). `forward_eval` can stamp `payload.recorded_at` with the real write time.
- PR #2 (UI owner): the space table in `demo.py` shows `predicted_top3` over `predicted_queries`,
  which counts "rank 3 of 3" as top 3; use `predicted_top3_qualified` over `predicted_top3_queries`
  (and the top 10 pair), and say whose record a head-to-head row is (B9).
- PR #4: after a union `conftest.py`, `test_later_snapshot_replaces_the_same_url_and_mock_scores_
  stay_on_that_cohort` fails against this branch's one-cluster guard; pr8_v3 drafts stored under
  live URLs are counted as covered.
- PR #1: `track-record score` compares raw query text, while label import casefolds queries.
- PR #2 (UI owner): the main draft screen (`workspace_ui.py`, through `workspace.run_draft`) already
  says a PREDICTED order is not a forecast (`DRAFT_LIMIT_NOTE`); it could add the reason from
  `check.check_scope` (the model learned only from pages that already rank, so it cannot say
  whether Google would show a new page at all), and the old `/check` in `demo.py` could show
  `CheckResult.scope` near the verdict (B18, P0-5).
- PR #2 (UI owner), from the B17 review: `serve.py` builds its own intent map from keywords.csv
  and `/check` passes `state.intent_for.get(query, "commercial")`, so it skips the label-file column and
  the shared rule; use `intents.query_intents(labels).intents` with `intent_for` (they agree on the
  SmartCrew labels today, checked on all 16 queries). `build_space` is called without `intents` or
  `kinds`. `_fetch_html` swallows `ProviderConfigError`, so with the placeholder `CRAWL_USER_AGENT`
  every URL check says only "Could not fetch"; show its message. `score_site` and `suggest_links`
  now default to the shared intents in the backend, but the demo trains `self.fit` with the
  keywords-only map (`serve.py` line 53), so pass one shared map to `build_examples`, `score_site`,
  `suggest_links` and `build_space` (B20 F16: with an `intent` column in the label file, the
  whole-site table scored a query with another intent than the model was trained on).
- PR #2 / PR #5 owners: `project.guess_intent` and `forward_eval.py` (hard-coded "commercial") can
  call `intents.query_intents(labels)` / `intent_for(query, intents)` so every path agrees.

Order for section B after section I: B7, B5, B6, B15 (with a persistence baseline), B8, B16, B3,
B4, B10, B11, B12, B13, B14, B9, B17. Notes from the review: B5 must not change `normalize_url`
(PR #4 tests rely on it) and should match PR #2's `project.loose_key`; B8 provides one backend
function for intents; B14 defaults to OpenRouter gpt-4o-mini (MISSION); B16 uses an adoption rule
fixed in advance.

## B. Backend work (no UI files)

- [x] B1. Freeze the benchmark: `benchmark-freeze` writes a dataset manifest and page snapshot;
  `--final` runs once per model id and appends to a results log (BACKLOG P0-2). Done: snapshot
  module, dev/final split by query cluster, verdicts withheld below 10 scored clusters; first final
  run recorded in E12.
- [x] B2. Calibrate on the statistic the check uses: adjacent-page gaps, pre-gate scores, gated
  pairs excluded (P1-13). Done: separate head-to-head and predicted-rank thresholds, rule-placed
  pages always UNSURE (E13).
- [x] B3. Crawler: connect to the vetted IP (DNS rebinding), deadline covers response headers,
  honour Retry-After with per-host backoff (P0-4 remainder). Done: `crawl/guard.py`; local tests
  for rebinding, dripped headers and a 429 storm; a real fetch through the proxy still works.
- [x] B4. robots.txt RFC 9309 matcher (wildcards, longest match on 3.11/3.12); refuse a placeholder
  user agent for third-party crawls (P1-6). Done: own matcher, tests pass on 3.11 and 3.13; the
  refusal happens at the first real fetch so mock runs on unresolvable hosts still work, and crawl
  loops stop on it instead of logging it per URL.
- [x] B5. URL match key for joining labels to pages; persist final URL and canonical; flag labeled
  URLs that redirect (P1-4). Done: `urlkey.match_key` equals PR #2's `loose_key` on test vectors;
  `normalize_url` unchanged; benchmark numbers on the snapshot dev split unchanged.
- [x] B6. Detect client-rendered (JS shell) pages and report them as a coverage reason (P1-5). Done:
  left out of training and evaluation; a shell draft is refused with a request for rendered HTML;
  one shell in the real crawl (E15).
- [x] B7. Per-label coverage table with thin/empty/JS-shell reasons (`--coverage-csv`); saved HTML
  and seeds found from the repo root, not the cwd (P1-9). Done: reasons covered, thin, empty,
  draft_only, blocked, error, not_attempted (JS shell comes with B6); from another directory the
  old code flagged 0 bundled drafts, so they counted as evaluation rows.
- [x] B8. One intents source for training, checks and space; every labeled query has an intent or
  is reported as unknown (P1-14). Done: `intents.query_intents`; the what-if rule is now the shared
  rule; 9 of 16 SmartCrew queries fall through to the default and are listed; numbers unchanged.
- [x] B9. Space metrics: top-k only when the set has more than k pages; observed top 10 means
  position <= 10; head-to-head perspective; business vs SERP competitors (P1-1, backend only).
  Done: new `*_qualified`/`*_queries` fields (old fields kept for the UI, request above); REAL top
  10 is position 1 to 10; `kind` column in competitors.csv; the CLI view says whose record it is.
- [x] B10. Competitor import validation: csv.reader, IDNA, reject IPs/localhost/ports, report
  rejected lines (P1-2). Done: `space.competitor_host`; `competitors import` prints each rejected
  line with its reason. The UI path (`workspace.parse_competitors`, PR #2) is unchanged.
- [x] B11. SERP history append-only with locale/device/collected_at; `export-serp`; `--keywords`
  file input (P1-7). Done: `rs_serp_collections`; identical re-imports skipped; `noise --from-db`.
  Raw provider payloads are not stored.
- [x] B12. Attribution scores before and after separately against the same peers (P1-8). Done:
  before, the old version was scored against its own edited copy (originality 0.000 -> 0.105 for a
  one-sentence edit of an original page); now 0.948 -> 0.953.
- [x] B13. `test --bundle` inserts only matching pages; report renders bundle runs and computes
  accuracy from the label file (P1-10). Done: targets from the manifest `query` column or
  keywords.csv; no position for a query without stored competitors; the report and the bundle text
  call the number same-snapshot agreement, not accuracy (MISSION).
- [x] B14. LLM explanation numbers checked against the brief; provider selector and model-format
  check (P1-11). Done: a reply with a number not in its brief falls back to the template (limit:
  a number present anywhere in the brief passes); `RANKSIM_LLM_PROVIDER`; model names checked.
  No LLM was called (no key in this environment).
- [x] B15. Site prior (content-blind) as one more benchmark baseline row (P1-12). Done: `site_prior`
  counts training queries that list the page's site; `persistence` (earlier Google position) is a
  row when labels carry `prior_position`. Dev split: site prior is the best row under every
  protocol, 8 clusters, no verdict (E16).
- [x] B16. LambdaMART regularization and linear label gain, chosen a priori, measured on the
  benchmark and documented whatever the result (P1-3). Done: `--candidate regularized` on the dev
  split, fixed adoption rule; +0.003 under cluster-url on 8 clusters, not adopted, no final run
  (E17).
- [x] B17. Independent review of B1-B16 (multi-lens review with adversarial verification); fix
  confirmed defects with regression tests. B1, B2 and I1 to I6 were reviewed in I7; B3 to B16 and
  the I7 fixes (e883b45..f281274) were reviewed here. Done: 6 lenses, 45 findings, 36 after merging
  duplicates; the top 20 were each given an adversarial verifier and all 20 were confirmed (2 high
  and 6 medium were rated one step lower); the other 16 (low) were reproduced by the fixers before
  any change. Fixed in five area branches, each with an independent reviewer and one follow-up
  round (merged in 73b286d, 6f99c9f, 601bea3, 3018d15, bf53f94), plus f21fee5 (whole-site intents)
  and the wording sweep below. Main fixes: the robots matcher no longer backtracks (a hostile
  robots.txt could hang a crawl), a robots.txt past the deadline, cut off or BOM-prefixed fails
  closed, backoff follows redirects, the pinned connection tries every vetted address; Search
  Console averages can no longer be exported as dated snapshots past the ledger guard, undated
  label files are not treated as dated snapshots, `noise --from-db` compares like with like;
  what-if, space and coverage use one page identity and leave JS shells out, and the pr8 bundles
  score their new pages; a final split is registered once per model, including E12
  (`smartcrew/final_runs.jsonl`), `report` no longer crashes after a single-page test; ledger rows
  without a write time are never scored, `--expect-head` refuses an empty value, the number guard
  catches glued digits. Every user-facing same-snapshot number now says agreement, not accuracy
  (`evaluate`, `train`, calibration notes, three-way and bundle output). Demo files owned by PR #2
  got requests instead (above).
- [x] B20. Review of the B17 fixes as merged (87511c0..15f23b2): multi-lens review with adversarial
  verification; fix confirmed defects with regression tests. Review: 7 lenses, 22 findings verified,
  20 confirmed (1 high, 7 medium, 12 low), 2 refuted. Fixed on four area branches, each with an
  independent reviewer and one follow-up round, and merged. Every new test fails on 15f23b2.
  - [x] Evaluation (F0, F17, F19): `docs/accuracy.md` had listed the `--final` number among the
    checks of unseen results; it now says a final run is a same-snapshot holdout from the same
    frozen label file, so its number is agreement, not accuracy, and the `--final` printout says so.
    A docs test reads README and docs for that claim. Final runs stored under the 87511c0 key are
    found again from the snapshot folder they name, adding rows only. A damaged `results.jsonl`
    line is named with its file and line number.
  - [x] Crawler (F1, F8, F9, F10, F11): every redirect hop is checked against its own robots.txt
    before any request to it; a robots.txt request waits out or defers on a 429/503 backoff
    instead of caching the host as unreachable; robots.txt is read as UTF-8 first; dot segments
    are removed before the robots check, as httpx removes them before sending (`normalize_url`
    changes only for URLs that contain them); a host httpx cannot encode returns an error. Review
    follow-up: a hop that resolves to a private address is reported as unsafe, not as robots.
  - [x] SERP store (F3, F6, F7, F12, F13, F14, F18): Search Console averages that an older version
    stored as a results list are marked as not Google (kept, not deleted) when the file is read
    again, and no reader uses them as a results page. Rows from 87511c0 are read as dated when they
    were stored at the observed day's midnight, and an undated import no longer marks them undated.
    Re-importing an earlier version of an undated file makes it the latest again. A non-ISO
    `observed_on` refuses the file before anything is stored. A draft at a label's slash twin no
    longer blocks the recrawl, and a stored URL keeps its own page in the corpus join.
  - [x] Observe (F2, F5, F4, F15): `ledger observe` refuses, and records nothing from, a file with
    rows from another day, undated rows, unreadable dates or another provider. `noise FILE FILE`
    pairs like with like and says what it left out. Keyword exports with title rows above the header
    are read from the header down, and a titled metric table with no query column is refused.
  - [x] F16: the demo trains with a keywords-only intent map while its whole-site table uses the
    shared source. `serve.py` is PR #2's: README exception and the request above corrected.

- [ ] B21. Review of the B20 fixes as merged (15f23b2..bc813d6), including the review follow-up
  commits and the merge of the four area branches: multi-lens review with adversarial
  verification; fix confirmed defects with regression tests.

- [x] B18. A check says which question it answers (P0-5, backend half): where the page would sit
  among the stored results, not whether Google would show a new page at all; the evidence it cites
  is stated with its size (E14, E16, E18). Done: `CheckResult.scope`, printed by the CLI; the web UI
  is PR #2's (request above). Tests fail on the previous commit (no `check_scope`).

- [x] B19. Review of f281274..6f5f3ba (E18 protocol and results, B18) with an independent E18
  recomputation and adversarial verification; fix confirmed defects with regression tests. Done:
  3 lenses, 9 findings, 7 verified: 3 confirmed and fixed, 2 refuted. E18 recomputed exactly and its
  protocol was not edited after the results; two descriptions corrected (E18 Review note). A check
  now finds the user's page under any spelling (match key), so its stored copy is no longer a
  competitor and the scope no longer calls a ranked page new; the scope gives E16's real size (11
  queries). 4 new tests fail on 6f5f3ba. The same spelling gap in what-if is a B17 finding.

## C. Blocked on credentials or decisions

- [-] C1. 300+ dated queries across sites and niches (P0-1): needs a SERP key.
- [-] C2. Repeated snapshots for volatility and ledger outcomes (P0-3): needs a SERP key. PR #5 holds
  two Serper snapshots (Nov 6, Nov 9); each further 30-query snapshot costs about 30 credits.
- [-] C3. Non-ranking candidates / "enters the top 10" task (P0-5): needs deeper SERP collection.
  E18 used PR #5's two snapshots for a pre-registered test of content-blind signals on new entrants:
  no evidence yet (195 entrant pairs). Page-text models on entrants need the private HTML.
- [-] C4. AI relevance judge rerun: needs an LLM key and a few hundred labeled pairs.
- [-] C5. Durable shared deployment (storage, accounts): needs a decision from James.
