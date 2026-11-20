# Pre-registration: round 2 loop 1, Target 2 Instrument A, excess movement of changed pages (Claude goal run)

Label: PREDICTED. Written 2026-11-19, about 20:15 ET, before any day after 2026-11-17 was read. Nothing here changes an earlier gate, the three round 1 preregistrations, the SmartCrew PR 41 ledger, or the product ranker. Every rule and number below is taken from `evals/claude/theory.md` section 2, the docstring of `scripts/claude/r2_t2_excess.py`, and its design run. Nothing is new and nothing is tuned.

This instrument measures. It predicts nothing. Its numbers are real Google positions from Serper. The only PREDICTED item is the theory's statement below.

## What is frozen

The sha256 of every file below is in `manifest.json` in this folder, written with `scripts/claude/freeze.py closure` on the module `scripts.claude.r2_t2_excess`.

- `scripts/claude/r2_t2_excess.py` (the instrument)
- `scripts/claude/t2_dataset.py`, `t2_changes.py` (the material-change rule `t2-material-v1`), `t2_common.py`, `t2_direction.py` and `evals/claude/t2-edits/params.json` (read when the dataset rows are built), `src/aeo/crawl/extract.py`
- `scripts/claude/rsdata.py`, `metrics.py`, `score_heldout.py`, `heldout.py`, `r2_score.py`, `freeze.py`, and every other repo file they import. The manifest lists them all, with the Python, numpy and pandas versions.

Nothing is fitted. The script calls `r2_score.require_frozen_dir` on this folder before it loads anything and refuses to overwrite a scored file.

## What is measured

- Rows: `t2_dataset.build_rows(max_date)`, one row per (search, URL, fetch pair A to B), with exactly the held-out selection of `score_heldout.score_t2`: position at B from 2026-11-18 or later, searches in groups o30 and a70, usable rows only.
- A row is changed when the round 1 material flag is set. Each changed row is compared with the mean of the unchanged rows in its stratum: the SERP date pair plus the start band of the position at A (1, 2 to 3, 4 to 6, 7 to 10, 11 for absent). A changed row in a stratum without an unchanged row is dropped and counted.
- Outcomes: moved (position at B differs from position at A), mean absolute change in consensus position, and crossed (left or entered the top 10).
- Interval: 95% cluster bootstrap, 2000 draws, seed 20261119, control means re-estimated inside every draw. Clusters: search (primary), URL and site.
- Cuts: all rows; start 2 to 10; without the client site (smartcrewhq.com, changed and control rows alike); start 2 to 10 without the client site; material against rows with no change at all; any change against none.

## Searches and held-out days

- Searches: o30 and a70.
- One run: `python scripts/claude/r2_t2_excess.py --prereg evals/preregistered/claude-r2-t2-excess --max-date 2026-11-19`. It covers every row whose position at B comes from 2026-11-18 or 2026-11-19. Output in `evals/claude/heldout-r2/claude-r2-t2-excess/`.
- Nov 18 and Nov 19 are used as pulled, as in the dated addenda of the three round 1 preregistrations. The instrument has no pull-count guard.
- The run through 2026-11-19 is made once.

## Prediction made before scoring (PREDICTED, copied from theory.md section 2)

"A frozen excess-movement test on held-out rows: do changed pages move more than matched unchanged pages? This is claim 2's central number, measured again on about four times as many pages. PREDICTED: the difference in P(moved) is below +0.20 and its interval includes zero."

For the row count, theory.md section 2 expects about 26 changed pages, 56 changed rows and about 35 changed-and-moved rows on the Nov 16 to Nov 19 window.

## Rule

- The prediction holds when, for material against matched unchanged rows, all rows, outcome P(moved), search clusters, the difference is below +0.20 and its 95% interval includes zero (`prediction_check.prediction_held`).
- The theory is wrong (theory.md, "What would prove this wrong"): "On held-out rows, changed pages move clearly more than matched unchanged pages (difference above +0.20 with the interval above zero). Then edits matter more in short windows than the theory says, and a content rule deserves a new attempt." (`prediction_check.falsifier_met`)
- If no changed row has a matched unchanged row, the script reports the check as not evaluable, and the report says NOT ENOUGH ROWS.

## Design numbers (DESIGN, not a test)

From `evals/claude/r2/t2-excess-design/excess-design.md` (rows with position at B on or before 2026-11-17), which reproduces `evals/claude/r2/t2-signal/notes.md` Q1 on all 16 numbers. 763 usable rows, 28 changed (25 matched), 11 changed URLs on 5 sites, 18 changed rows on the client site, 30 searches.

| Comparison | Changed rows matched | Changed | Matched unchanged | Difference in P(moved), search clusters |
| --- | ---: | ---: | ---: | --- |
| Material vs not, all rows | 25 of 28 | 0.640 | 0.557 | +0.083 [-0.082, +0.267] |
| Material vs not, start 2 to 10 | 16 of 16 | 0.875 | 0.660 | +0.215 [+0.033, +0.400] |
| Material vs not, without the client site | 10 of 10 | 0.800 | 0.543 | +0.257 [-0.003, +0.496] |
| Material vs no change at all | 25 of 28 | 0.640 | 0.558 | +0.082 [-0.093, +0.281] |
| Any change vs none | 97 of 101 | 0.536 | 0.540 | -0.004 [-0.121, +0.114] |

On design rows the prediction held (+0.083, below +0.20, interval includes zero).

## Date and seal

Written 2026-11-19 before any SERP row or page fetch dated 2026-11-18 or later was read by this run. The instrument was run on design days only (`--design`, max_date 2026-11-17, its own extraction cache). No held-out row was scored before this file and its manifest were committed and pushed.
