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

(Sections are added as work completes.)
