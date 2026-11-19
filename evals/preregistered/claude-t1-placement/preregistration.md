# Pre-registration: Target 1, new-URL placement (Claude goal run)

Label: PREDICTED. Written 2026-11-19 before any day after 2026-11-17 was read. Nothing here changes an earlier gate, the SmartCrew PR 41 ledger, or the product ranker. The earlier team's own Nov 19 hybrid gate (`evals/preregistered/hybrid-2026-11-19/`) is a different test and is not touched.

## The target, from GOAL.md

"On rows where a URL is new to the query's top 30 or whose page content changed materially, predict its consensus position. Pass = within-3 accuracy beats the best of {domain prior, position 7, position 11} with the CI of the difference above 0, on at least two held-out days not used for any design choice."

## What is frozen

The sha256 of every file below is in `manifest.json` in this folder. `scripts/claude/score_heldout.py` refuses to score when a hash differs, when the freeze is not committed, or when it is not pushed.

- `scripts/claude/t1_placement.py` and `evals/claude/t1-placement/params.json` (the predictor)
- `scripts/claude/rsdata.py`, `scripts/claude/metrics.py`, `scripts/claude/heldout.py`, `scripts/claude/score_heldout.py`, `scripts/claude/predict_ahead.py`
- every other repo file those modules import, including `src/aeo/ranksim/consensus.py`, `src/aeo/ranksim/site_history.py` and `src/aeo/crawl/urls.py`, which define the consensus median and which URLs count as the same URL. The manifest lists them all, with the Python, numpy and pandas versions.

The predictor is not refit on a held-out day.

## Truth and rows

A day's consensus position is the median over the pulls of that day's main pull session, a missing URL counts as 11, and a URL needs a strict majority of the pulls to be in the top 10 (`rsdata.consensus_table`).

Rows are the consensus top-10 rows of the outcome day, split by what the stored history says about the (search, URL) pair before that day (`rsdata.placement_rows`):

- **Strict new (the primary class).** The URL was never seen for this search in any stored list before the outcome day, and the search has a deep list (top 20 to 30) in its history. This is the brief's "new to the query's top 30" as far as the stored data can check it. The only deep list is from 2026-11-16, so "top 30" means: not in the Nov 16 top 20 to 30, and not in any top-10 pull on any earlier day.
- **Reentrant (secondary, reported).** Seen before for this search but not in the previous pulled day's consensus top 10.
- **New without a deep history (reported).** New on a search that has no deep list. Not counted as strict.
- **Survivors** are not target rows.

**Rows whose page content changed materially.** For a page that already ranks, yesterday's position is known before the prediction date, so the honest bar is persistence, not the brief's three baselines. No content-based adjustment beat persistence on design data (see Target 2). So this run's prediction for a changed page is its previous position, and those rows are reported in their own table with persistence as an extra baseline. They are never pooled into the pass decision below, because pooling would let persistence manufacture a win over "position 7".

## The predictor (PREDICTED), from `params.json`

A fixed rule with no learned weights. It reads only days before the outcome day, plus the row's search text and URL.

- New URL: the best consensus position its site held on this search yesterday. If the site was not in yesterday's top 10, the site's position in the deep list. If neither, position 9. The result is clipped to 2 through 9.
- Reentrant URL: its own position in the deep list. If it is not there, its last top-10 position. If neither, position 7. The result is clipped to 2 through 9.

The only fitted number is the default 9 for a new URL. It was the best single band3 position on every prefix of the design days. The clip costs nothing: scored rows always sit at 1 through 10, so a prediction of 1 covers the same band as 2, and 10 or 11 cover less than 9.

## Metrics

- **band3 (primary).** The repo's 3-wide band: the truth is one of the three positions starting at max(1, prediction minus 1). This is the metric of the earlier hybrid gate, which is where the brief's baseline list comes from.
- **err3 (secondary reading, always reported).** Absolute error of 3 or less. On design days "position 7" alone is right about 83% of the time under this reading, because 7 plus or minus 3 covers positions 4 through 10. A pass on err3 is not required and is not expected.

Baselines, fixed by the brief: the domain prior (the best consensus position of the same site on the same search on the previous pulled day, 11 if none, as in the earlier hybrid gate), position 7, and position 11. The best of the three is chosen on the scored rows themselves, which favours the baselines. One extra reference is reported and is not part of the brief's set: position 9, the best single position for newcomers on design days.

Intervals: 95% query bootstrap, 2000 draws, seed 20261119, paired difference on the same rows.

## Searches and held-out days

The pass decision uses the 100 SmartCrew searches only: the original 30 and the 70 added on 2026-11-16. They are also the only searches with a deep list, so they are the only ones that can have strict-new rows. Other searches in the raw store are printed on their own line and never enter the decision.

Held-out days: 2026-11-18 (pulled by the earlier team before this run, two or three pulls per search, never read during design, original 30 searches) and every later day pulled in this run for the 100 searches with three pulls per search (2026-11-19, 2026-11-20, and 2026-11-21 if pulled). Every pulled day is scored and reported. None is dropped. The scorer refuses to score a day of this run until all three pulls are in, and it does not overwrite a scored file.

Before each day of this run is pulled, the predictor's answers for that day are written to `evals/claude/ahead/`, hashed, and pushed: for every search, the position a new URL from each known site would get, the position for a new URL from any other site, and the position for every URL that could re-enter. The scorer checks its predictions against that file.

## Pass rule

On the strict-new rows, on band3:

- **Per-day pass (the strict reading of the brief).** On at least two held-out days, each with at least 20 rows from at least 10 searches, the model minus the best baseline has a lower bound above 0.
- **Pooled pass (the lenient reading).** Pooled over all held-out days, at least two days and at least 40 rows from at least 10 searches, the model minus the best baseline has a lower bound above 0.
- **Target 1 passes** when either holds. The report says which one, gives both, gives the err3 reading, and gives the model minus the position-9 reference. If the model beats the brief's baselines but not position 9, the report says the gain is only "newcomers land low on page one".

## Design numbers (days through 2026-11-17, not a test)

From `evals/claude/t1-placement/notes.md`, reproduced with `scripts/claude/heldout.py`. band3, model minus the best of the brief's three baselines on the same rows. The rule family was chosen after looking at every design slice, so these numbers lean high.

| Slice | Rows | Model | Best baseline | Difference | Model minus position 9 |
| --- | ---: | --- | --- | --- | --- |
| New, original 30, Nov 16 and 17 (three-pull truth) | 35 | 18 (0.514) | domain prior 11 | +0.200 [+0.057, +0.361] | -0.029 [-0.257, +0.175] |
| New, strict, Nov 17 only | 5 | 5 | domain prior 3 | +0.400 [+0.000, +0.800] | +0.600 |
| New, strict, emulation of the added searches (history cut to one pull plus the deep list) | 19 | 16 (0.842) | domain prior 9 | +0.368 [+0.176, +0.571] | +0.316 [+0.062, +0.545] |
| New, every design slice pooled (mostly single-pull days with no deep list) | 303 | 148 (0.488) | position 7: 103 | +0.149 [+0.075, +0.232] | +0.056 [+0.009, +0.104] |
| Reentrant, every design slice pooled | 132 | 81 (0.614) | domain prior 39 | +0.318 [+0.186, +0.447] | +0.174 [+0.000, +0.331] |

Under err3 the same predictions lose to position 7 on new rows (pooled -0.092 [-0.136, -0.043]). That is built in: with the truth between 1 and 10, a prediction of 7 covers seven positions and a prediction of 9 covers five.

Strict-new rows are rare. Design data has 5 real ones (Nov 17) and 19 emulated ones scored against the same Nov 17 outcome.

## What we expect (PREDICTED, stated before scoring)

- Nov 18 will have about 5 strict-new rows, too few to count as a day.
- Nov 19 is the first look at the 70 added searches after a three-day gap. If it has 30 or more strict-new rows, the worker's simulation puts the chance of a per-day pass between 0.40 and 0.82. Later days will again have few strict-new rows, because Nov 19 itself becomes history.
- So a per-day pass on two days is unlikely for lack of rows, and a pooled pass is possible.
- Most of any gain over the brief's baselines will come from predicting 9 instead of 7 or 11. The gain over position 9 is expected to be small.
- The err3 reading is expected to fail.
