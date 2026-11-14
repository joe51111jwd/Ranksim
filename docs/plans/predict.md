# Predict workstream plan (session 6, branch `claude/rs-predict`)

Goal: make Test changes more useful without overstating it. Every position shown is PREDICTED.
No training score or same-snapshot score is ever shown as accuracy.

## What the product will do

1. **Pages already ranking: persistence anchor.** "Now" is the page's real observed position.
   "With the edit" moves it by the change in a pairwise content score (`predict.anchored_position`).
   With no edit the PREDICTED position equals the observed one. The move itself has no forward
   validation yet: no labeled before and after edits exist.
2. **New pages and drafts: placement model.** A pairwise logistic model on page features plus the
   site's presence on related searches. The PREDICTED position is 1 plus the expected number of
   current top 10 pages that beat the draft. New pages carry a handicap (`entrant_logit`) learned
   from pages that entered a top 10 between Oct 29 and Nov 6.
3. **One call, every search.** Per search: PREDICTED position now and with the edit, the move,
   and a confidence label. UNSURE unless that search's backtest (held-out query family, same
   snapshot) ordered at least 65% of at least 30 pairs. The backtest number and pair count are
   shown and called a backtest, not accuracy.
4. **What would move you up.** Compare the draft with the pages PREDICTED just above it on the
   model's features (title and H1 match, search words in body and subheadings, term coverage, word
   count, subheadings, tables, FAQ, structured data, links). List the few gaps with a positive
   weight that kept the same sign in every training fold. Called guidance, not a guarantee.

## Evaluation protocol (fixed before scoring)

- Code: `src/aeo/ranksim/predict_eval.py`. Output: `evals/predict-2026-11-14/`.
- Step `train` reads only the Nov 6 list (`evals/forward-2026-11-09/baseline_serp.csv`), the
  Oct 29 list (`smartcrew/serp.csv`, used only for the new-page handicap and persistence slope),
  and page HTML. It never opens the Nov 9 file.
- Feature set (content, site, content+site) and L2 penalty are picked by query-family-out
  pairwise on Nov 6 (4 families from `snapshot_protocol.assign_families`). The site features
  leave out the page's own URL and the search itself, so a training page is scored the way a
  brand new page on the same site would be.
- `training.json` and `model.json` are committed before step `score` runs. `score` checks the
  model hash and refuses to run twice.
- Step `score` reads Nov 9 once. Returning pages are anchored at their Nov 6 position. Each new
  entry (URL not in that search's Nov 6 top 10) gets its PREDICTED position. Pairs are scored as in
  `evals/forward-2026-11-09/report.json` (ties count as not correct). Slices: entrant vs returning,
  entrant vs entrant, never-seen vs returning, never-seen vs never-seen, and the full list.
- Pre-registered scorers: `hybrid_with_prior` (primary), `hybrid_no_prior` (Nov 6 only, no
  handicap), and the reference `persistence_bottom` (every entrant below every returning page,
  0.704 on entrant vs returning in the existing report, which was public before this work).
- Coverage: entrants without fetched HTML are not scored and are listed.

## Caveats

- The snapshots' own stored HTML is private and was not in this environment. All 192 URLs were
  fetched again on 2026-11-14 (167 returned HTML). That is 8 days after Nov 6 and 5 after Nov 9.
  Pages may have changed. HTML is not in git; `evals/predict-2026-11-14/fetch_manifest.json` lists
  status and sha256 per URL.
- One interval, 30 searches in one niche. Not a general forecast.
- The Oct 29 list is an older export with heavy churn (96 of 160 Nov 6 rows were new).

## Training results (selection only, not accuracy)

Family-out pairwise on Nov 6, 931 pairs, 250 of 294 rows with HTML:
content only 0.457 to 0.467, site only 0.503 to 0.507, content+site 0.552 to 0.563. Chosen:
content+site, L2 0.3. New-page handicap from Oct 29 to Nov 6: entrants ranked above returning
pages in 34% of 330 pairs, `entrant_logit` -0.84. Persistence slope 0.40 per position (100 pairs).

Features with the same positive sign in all 4 folds: title match, body match, subheadings,
structured data, site presence. Word count, FAQ and links were negative in every fold, so the
guidance never tells a user to add words or links on this data.

## Forward results (scored once on 2026-11-09)

Model frozen at commit dd566b7 before scoring. File: `evals/predict-2026-11-14/report.json`.
Intervals: `intervals.json`, 95%, bootstrap over the 30 searches, computed after scoring from the same rows.

Coverage: 299 Nov 9 rows, 202 returning, 97 new entries. 83 of 97 entries had fetched HTML and were
scored. 14 were not (they sit tied at the bottom for every scorer and are dropped from entrant slices).
50 entries were in no Nov 6 search at all (46 scored); those are the closest case to a real draft.

| Slice | Hybrid with handicap | Hybrid, Nov 6 only | Persistence (entrants at bottom) |
| --- | --- | --- | --- |
| Full list, 1341 pairs | 0.788 (1056), 95% 0.74 to 0.83 | 0.773 (1037) | 0.694 (931) |
| Returning pages only, 667 | 0.891 (594) | 0.891 | 0.891 |
| New vs returning, 398 | 0.749 (298), 95% 0.70 to 0.80 | 0.701 (279) | 0.719 (286) |
| New vs new, 151 | 0.609 (92), 95% 0.53 to 0.68 | 0.609 | cannot order (0) |
| Never seen vs returning, 180 | 0.783 (141) | 0.756 | 0.722 (130) |
| Never seen vs never seen, 87 | 0.644 (56), 95% 0.59 to 0.80 | 0.644 | cannot order (0) |

Plain reading:
- Most of the full-list gain over persistence comes from ordering new pages among themselves,
  which persistence cannot do. That ordering is modestly above chance (0.61, interval 0.53 to 0.68).
- Against pages already ranking, the model is close to "new pages land below everything"
  (0.749 vs 0.719 on the same 398 pairs, intervals overlap). It is not a clear gain there.
- The absolute PREDICTED position of a new page is not better than a constant guess:
  mean error 2.55 positions vs 2.42 for always guessing position 6 (the training median).
  So the product should present a draft's slot as a rough PREDICTED range with UNSURE, and lean on
  order among candidates and on guidance.
- Not 90%. One interval, one niche, HTML fetched 5 to 8 days late. The pre-existing LambdaMART
  figures (0.477 draft vs ranking, 0.293 draft vs draft) used the private snapshot HTML and a
  different draft definition, so they are not a like-for-like comparison.

## Product guardrails added after scoring (do not change the scored numbers)

- A new page that uses none of a search's words gets no PREDICTED slot for that search.
- Guidance compares against pages from other sites only.
