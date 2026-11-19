# Ranksim goal run: results log (Claude, started Nov 18 2026 late evening ET)

Branch `claude/goal-ranksim-works`. Brief: `GOAL.md`. Every model number below is PREDICTED and comes from a script in `scripts/claude/`. Real Google positions come from Serper and are labeled REAL or "truth".

Status: IN PROGRESS. This file is updated after every meaningful experiment. The final report and the plain-English summary go at the bottom when the run ends.

## 0. Ground rules used in this run

- Design data: snapshot days through 2026-11-17. Nothing dated 2026-11-18 or later was read while designing. Nov 17 is treated as a design day because the earlier team already scored a diagnostic on it.
- Held-out days: 2026-11-18 (already pulled by the earlier team, sealed, never read before the freeze) and every later day pulled during this run (Nov 19 onward).
- The seal is enforced in code. `scripts/claude/rsdata.py` only returns SERP rows and page fetches dated on or before the `max_date` it is given. Design scripts pass `2026-11-17`.
- Consensus truth: median position over the pulls of the day's main pull session, a missing URL counts as 11, and a URL needs a strict majority of the pulls to count as top 10. For a three-pull day this equals the earlier team's consensus for every top-10 position.
- Intervals: 95% query bootstrap, 2000 draws, seed 20261119 (`scripts/claude/metrics.py`).
- "Within 3" is scored two ways because the brief says "within-3" and the earlier gates say "3-wide": `band3` (the repo's 3-wide band) and `err3` (absolute error of 3 or less). A Target 1 pass needs both.

## 1. Setup facts (Nov 18, 23:40 ET to Nov 19, 01:00 ET)

- Tests before any change: 463 passed, 6 failed. The 6 failures are all in `tests/test_layered_fetch.py` and come from this machine's DNS (every host resolves to a 198.18.x.x proxy address, which the fetch ladder refuses as non-public). They fail the same way on the untouched checkout. Not caused by this run.
- Serper balance at start: 1,209 credits. Reserve kept: 250 (scripts refuse to go under 260).
- Raw store at start: 7 pulled days for the original 30 searches (Nov 6, 9, 11, 13 single pulls; Nov 16, 17 three-pull consensus; Nov 18 two to three pulls, sealed), one day (Nov 16) for the 70 added searches plus a top-20/30 deep list, three single-pull days for the 15 Revorion searches.
- A Google core update is running through about Nov 24 (noted in the SmartCrew PR 41 ledger). Every day in this run sits inside it.

## 2. How much target data exists (design days, REAL counts)

Computed with `scripts/claude/rsdata.py` on days through Nov 17.

| Outcome day | Truth | Survivors | New (never seen in any stored list) | Reentrant (seen before, not in yesterday's top 10) |
| --- | --- | ---: | ---: | ---: |
| Nov 16, original 30 | 3-pull consensus | 209 | 30 | 32 |
| Nov 17, original 30 | 3-pull consensus | 223 | 5 | 41 |

- Once the Nov 16 deep list (top 20 to 30) is in the history, a URL that is truly new to a search's top 30 and lands in the consensus top 10 is rare: 5 rows on Nov 17 across 30 searches.
- Consecutive-day consensus flips: Nov 16 to Nov 17 has 767 ordered pairs on the original 30 searches and 44 of them flipped (5.7%).
- Pages with HTML on two or more dates through Nov 17: 134 URLs.

These counts set the power of every test below. They are small.

## 3. Experiments

### 3.1 Free HTML re-fetch pass (Nov 19, 03:28 to 04:00 ET). Done.

Script `scripts/claude/refetch_html.py`, notes `evals/claude/pipeline/notes.md`. One plain-HTTP fetch of every URL seen in a top-10 pull in the last 5 days, robots.txt obeyed, appended to the raw store as new dated rows.

- 1,208 URLs on 506 hosts. 785 came back with HTML. 423 did not (209 robots disallow, 125 HTTP 403, 57 challenge pages, 32 other).
- 576 of the 785 have an earlier plain-HTTP fetch to compare against. This is the "after" side for content changes between Nov 16 and Nov 19.
- The repo logs hold hashed URLs only, so the held-out day's URL list is not exposed.

### 3.2 Target 3 design: pairwise order of movers (design days only, not a test). Done.

Worker notes: `evals/claude/t3-movers/notes.md`. Script: `scripts/claude/t3_design_eval.py`. My own harness (`scripts/claude/heldout.py`) reproduces the worker's headline numbers exactly.

The one consecutive-day consensus transition in design data is Nov 16 to Nov 17 on the original 30 searches: 767 ordered pairs, 44 flipped (5.7%).

| Scorer (PREDICTED) | Flipped pairs called right | Flip calls that were right | All pairs, model minus persistence |
| --- | --- | --- | --- |
| Persistence | 0/44 | no calls | 0 |
| A: pull-majority (Kemeny) order of yesterday's pulls | 10/44 (0.227) [0.075, 0.393] | 10 of 14 | +0.008 [+0.001, +0.016] |
| Long-run mean position | 24/44 (0.545) [0.353, 0.784] | 24 of 53 | -0.007 [-0.030, +0.019] |
| Mean reversion to earlier days | 26/44 (0.591) [0.457, 0.750] | 26 of 162 | -0.143 [-0.190, -0.099] |
| B: flip every pair that one of yesterday's pulls showed reversed | 35/44 (0.795) [0.629, 0.917] | 35 of 190 | -0.156 [-0.209, -0.099] |

What this says:

- For any scorer, "all pairs no worse than persistence" is the same as "at least half of its flip calls are right". Only 5.7% of pairs flip, so a scorer has to find a set of pairs that is at least half real flips and holds more than half of all flips.
- About 50 scorers were tried (smoothed means, mean reversion, trend, deep-list rank, sibling-search rank, logistic and small tree models, validated forward in time). The best point that keeps half of its calls right catches 41% of flips, and that is with the threshold picked after the fact. Forward-chained models call almost nothing.
- Why: with a three-pull consensus on both days, pull noise alone flips a pair less than half the time. A flip with probability above one half needs a real overnight change, and position history does not predict those. Pairs that the earlier days had in the opposite order still flipped only 20 times out of 111.
- Scorer B meets the brief's literal wording on design data, but it is 10 to 16 points worse than persistence over all pairs. It is a volatility flag, not an order predictor. Reversing yesterday's order would score 100% on the same wording.

Decision: both scorers are frozen and scored on held-out days (`evals/preregistered/claude-t3-movers/`). Target 3 counts as passed only if one scorer meets the brief's wording and is no worse than persistence on all pairs. Expected result, stated before scoring: A fails the wording, B fails the guard.
