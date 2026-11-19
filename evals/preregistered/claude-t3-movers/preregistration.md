# Pre-registration: Target 3, pairwise order of movers (Claude goal run)

Label: PREDICTED. Written 2026-11-19 before any day after 2026-11-17 was read. Nothing here changes an earlier gate, the SmartCrew PR 41 ledger, or the product ranker.

## The target, from GOAL.md

"Among URL pairs whose relative order flipped between consecutive days, predict the new order better than persistence (which gets 0 by construction) and better than 50% with CI lower bound above 0.5."

## What is frozen

The sha256 of every file below is in `manifest.json` in this folder. `scripts/claude/score_heldout.py` refuses to score when a hash differs, when the freeze is not committed, or when it is not pushed.

- `scripts/claude/t3_movers.py` and `evals/claude/t3-movers/params.json` (scorer A)
- `scripts/claude/t3_contested.py` (scorer B)
- `scripts/claude/rsdata.py`, `scripts/claude/metrics.py`, `scripts/claude/heldout.py`, `scripts/claude/score_heldout.py`

No scorer is refit on a held-out day.

## Truth

A day's consensus position is the median over the pulls of that day's main pull session, a URL missing from a pull counts as 11, and a URL needs a strict majority of the pulls to be in the top 10 (`rsdata.consensus_table`). A pair is scored when both URLs are in the consensus top 10 on both days and strictly ordered on both days. It flipped when day two reverses day one's order.

## Held-out transitions

Every pair of consecutive calendar days from 2026-11-17 to 2026-11-18 onward for which both days have pulls in the raw store: 17 to 18 and 18 to 19 (original 30 searches), 19 to 20 and any later day pulled in this run (all 100 searches). Every such transition is scored and reported. None is dropped. The pooled test uses all of them together. A transition with a gap of more than one day is reported but is not part of the pooled test.

Day 2026-11-18 was pulled by the earlier team before this run started. It was not read during design. Days from 2026-11-19 on are pulled after this file is pushed.

## Two frozen scorers

**Scorer A, pull-majority order** (`t3_movers.score_urls`). Yesterday's consensus top 10, re-ordered by the Kemeny order of pairwise majorities over every top-10 pull of yesterday. Earlier days only break exact ties. It overrides yesterday only where most of yesterday's own pulls disagree with yesterday's median order.

**Scorer B, a contested pair flips** (`t3_contested.call_flips`). It calls a flip on every pair that at least one of yesterday's top-10 pulls showed in the opposite order. No fitted parameter.

## Pass rule

- **C1, the brief's wording.** Pooled over the held-out consecutive-day transitions, the share of flipped pairs whose new order the scorer predicted has a 95% query-bootstrap lower bound above 0.5 (2000 draws, seed 20261119, queries resampled, a query's pairs from all days stay together). At least 100 flipped pairs from at least 20 searches are required.
- **C2, a guard added by this run.** On all scored pairs of the same transitions, flipped or not, the same scorer is at least as accurate as persistence (yesterday's order). Point estimate of the difference at or above 0. The interval is reported.
- **Target 3 passes** only when one scorer meets C1 and C2 together.
- A scorer that meets C1 and fails C2 is reported as "literal wording only". It is not counted as a pass. Reason: reversing yesterday's order scores 100% on C1 and is useless. For any scorer, all-pairs accuracy minus persistence equals (right flip calls minus wrong flip calls) divided by all pairs, so C2 is the same as "at least half of the flip calls are right".

Per-transition and per-search-group numbers are reported. They do not decide the pass.

## Design numbers (days through 2026-11-17, not a test)

One consecutive-day consensus transition exists in design data: Nov 16 to Nov 17 on the original 30 searches, 767 pairs, 44 flips (5.7%).

| Scorer | Flip accuracy (C1) | Flip calls right | All pairs minus persistence (C2) |
| --- | --- | --- | --- |
| A | 10/44 (0.227) [0.075, 0.393] | 10 of 14 | +0.008 [+0.001, +0.016] |
| B | 35/44 (0.795) [0.629, 0.917] | 35 of 190 | -0.156 [-0.209, -0.099] |
| B, with only the three voting pulls of Nov 16 | 31/44 (0.705) [0.455, 0.882] | 31 of 138 | -0.099 [-0.152, -0.046] |

About 50 other scorers (smoothed means, mean reversion, trend, deep-list rank, sibling-search rank, logistic and small tree models) are in `evals/claude/t3-movers/notes.md`. None reached C1 while holding C2. The best point with at least half of its calls right caught 41% of flips, with the threshold picked after the fact.

## What we expect (PREDICTED, stated before scoring)

Scorer A will fail C1 and hold C2. Scorer B will meet C1 and fail C2. So Target 3 is expected to fail under this rule. The reason is in the notes: with a three-pull consensus on both days, a pair flips with probability above one half only when the ranking really changed overnight, and nothing in the history we have predicts those changes.
