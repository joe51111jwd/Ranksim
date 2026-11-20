# Pre-registration: round 2 loop 1, Target 2 Instrument B, long-window edit test (Claude goal run)

Label: PREDICTED. Written 2026-11-19, about 20:15 ET, before any day after 2026-11-17 was read. Nothing here changes an earlier gate, the three round 1 preregistrations, the SmartCrew PR 41 ledger, or the product ranker. Every rule and number below is taken from `evals/claude/theory.md` section 2 and the docstring of `scripts/claude/r2_t2_longwindow.py`. Nothing is new and nothing is tuned.

This file freezes the instrument only. It cannot be scored before 2026-12-03: the script refuses a `--max-date` before that day, because no change seen by the daily re-fetch (fetch day B on or after 2026-11-19) has 14 days of later pulls until then.

## What is frozen

The sha256 of every file below is in `manifest.json` in this folder, written with `scripts/claude/freeze.py closure` on the module `scripts.claude.r2_t2_longwindow`.

- `scripts/claude/r2_t2_longwindow.py` (the instrument) and `scripts/claude/r2_t2_excess.py` (the excess-movement statistics it imports)
- `scripts/claude/t2_dataset.py`, `t2_changes.py` (the material-change rule `t2-material-v1`), `t2_common.py`, `t2_direction.py` and `evals/claude/t2-edits/params.json` (the frozen round 1 direction rules and their parameters), `src/aeo/crawl/extract.py`
- `scripts/claude/rsdata.py`, `metrics.py`, `score_heldout.py`, `heldout.py`, `r2_score.py`, `freeze.py`, and every other repo file they import. The manifest lists them all, with the Python, numpy and pandas versions.

Nothing is fitted. A real run calls `r2_score.require_frozen_dir` on this folder before it loads anything and refuses to overwrite a scored file.

## What is measured

- Rows: `t2_dataset.build_rows(max_date)`, usable rows of searches in groups o30 and a70, fetch day B on or after 2026-11-19. Events are rows with a material change. Controls are rows without one.
- Levels (absent from the consensus top 10 counts as 11): pre is the mean consensus position over the search's pulled days in [fetch day A minus 3 days, A]. Post is the mean over pulled days in [fetch day B plus 14 days, B plus 28 days], only days on or before max_date. A row needs at least 2 pulled days on each side, otherwise it is dropped and counted. Moved means |post minus pre| is at least 1.0. Up means post is lower than pre.
- Report 1, excess movement as in Instrument A: each changed row against the mean of the unchanged rows in its stratum (fetch date pair plus the start band of pre rounded half up). P(moved) and mean |post minus pre|. 95% cluster bootstrap, 2000 draws, seed 20261119, control means re-estimated in every draw. Search, URL and site clusters. Cuts: start 2 to 10, without the client site, material against no change, any change against none. Sensitivities: band-only strata, clean controls, complete post windows only.
- Report 2, direction on changed-and-moved rows, PREDICTED by the frozen round 1 content-aware rule (`t2_direction.predict_direction`) and position-only rule, with the long-window direction as truth. Accuracy blocks come from `score_heldout._t2_block`.
- Report 3, the round 1 pass checks from `evals/preregistered/claude-t2-edits/preregistration.md`.

## Searches and held-out days

- Searches: o30 and a70.
- Days: pre levels from the pulled days in the 3 days up to fetch day A, post levels from 14 to 28 days after fetch day B. A run is made with `python scripts/claude/r2_t2_longwindow.py --prereg evals/preregistered/claude-r2-t2-longwindow --max-date YYYY-MM-DD`, max_date 2026-12-03 or later. Output in `evals/claude/heldout-r2/claude-r2-t2-longwindow/`.
- theory.md fixes no run date beyond "from Dec 3". Every run made is reported with its max_date. None is dropped.

## Prediction made before scoring (PREDICTED, from theory.md section 2)

theory.md states no number for the 14 to 28 day window. What it says, copied:

- "Longer windows. Measure 14 to 28 days after the change, with several pulls averaged on each side, against matched unchanged pages. This is where published tests see effects."
- "About 250 edited rows with 250 controls shows a half-position shift."
- "PREDICTED: H cannot pass on any number of rows this month." That statement is about short windows in November. A run from 2026-12-03 is outside it.

## Pass rule and what would prove the theory wrong

- Target 2 (round 1 rule, applied by the script to changed-and-moved rows): **L**, n at least 40, the content-aware rule's 95% lower bound above 0.55 and its rate above the larger class share; **H**, content-aware minus position-only with a 95% lower bound above zero. Target 2 passes only when L and H both hold. With fewer than 40 rows the result is NOT ENOUGH ROWS.
- theory.md section 2, "What would prove this wrong", written for held-out rows: changed pages move clearly more than matched unchanged pages (difference above +0.20 with the interval above zero); the frozen content-aware rule beats the position-only rule; the position rule falls to coin level on changed rows while staying near 0.70 on unchanged ones. theory.md does not say whether these were meant for the long window. The report gives the matching long-window numbers next to them.

## Design numbers

None that count. The only design-day run is a CODE CHECK ONLY dry run (`evals/claude/r2/t2-excess-design/longwindow-dry-run-code-check.md`, days through 2026-11-17, a 2 to 6 day post window instead of 14 to 28). It found 13 changed-and-moved rows on 6 URLs of 1 site, so n was below 40 and L and H were both false. It is not a result.

## Date and seal

Written 2026-11-19 before any SERP row or page fetch dated 2026-11-18 or later was read by this run. The instrument was run only as the design-day code check above.
