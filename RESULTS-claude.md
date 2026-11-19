# Ranksim goal run: results log (Claude, started Nov 18 2026 late evening ET)

Branch `claude/goal-ranksim-works`. Brief: `GOAL.md`. Every model number below is PREDICTED and comes from a script in `scripts/claude/`. Real Google positions come from Serper and are labeled REAL or "truth".

Status: IN PROGRESS (round 2 started Nov 19, 14:20 ET). This file is updated after every meaningful experiment. The final report and the plain-English summary go at the bottom when the run ends.

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

### 3.3 Target 2 design: edit direction (design days only, not a test). Done.

Worker notes: `evals/claude/t2-edits/notes.md`. Scripts: `scripts/claude/t2_changes.py`, `t2_dataset.py`, `t2_direction.py`, `t2_eval.py`. My own scoring code reproduces the worker's numbers on the changed rows.

How many real changed pages exist through Nov 17:

- 303 same-method fetch pairs of the same URL on different days. 262 are usable (both versions have at least 50 words of main text).
- 13 material changes under the frozen rule (title or H1 changed, or a body edit of at least 250 tokens and 2%). The rule flags 0 of 54 same-day re-fetches, so it does not fire on rotating widgets and dates.
- 28 (search, URL) rows with a material change. 17 of them moved (7 up, 10 down). They come from 10 URLs on 4 sites. 8 of the 13 changed pages are the client's own site.
- The brief asks for n of 40 or more. Design data has 17.

Direction accuracy, PREDICTED, forward in time:

| Rows | Larger class | Position-only rule (never reads the page) | Frozen rule (position plus content diff) | Content diff alone |
| --- | --- | --- | --- | --- |
| 17 changed and moved | 10/17 (0.588) | 12/17 (0.706) [0.462, 0.895] | 11/17 (0.647) [0.438, 0.846] | 10/17 (0.588) |
| 437 unchanged and moved | 265/437 (0.606) | 303/437 (0.693) [0.647, 0.736] | same | n/a |

- Frozen rule minus position-only on the 17 changed rows: -0.059 [-0.222, +0.143]. The content step fired on 7 rows and was right on 4.
- Position-only accuracy on changed rows minus unchanged rows: +0.007 [-0.224, +0.222]. Changed pages are no more predictable than unchanged ones.
- A rule that never looks at the page already clears the brief's literal bar (beats the larger class, lower bound above 0.55 on all moved rows). Most of that edge is the boundary: a URL outside the top 10 can only go up and a URL at 1 can only go down. Inside the top 10 it is 0.664 against 0.651 for "always down".
- So the preregistration adds a guard: Target 2 only counts as passed when the content-aware rule beats the position-only rule on the same rows.

Yield: 0.018 material changes per URL per day (0.008 without the client's site). The current tracked set gives about 16 changed-and-moved rows a week. Reaching n of 40 takes about 2.5 weeks. Showing that content beats position takes 8 to 70 weeks on the current set, depending on effect size, and about 6.6 times less if all roughly 650 URLs of the 100 searches are re-fetched daily.

### 3.4 Could the client's own edits fill the gap? No. Done.

Scout note: `evals/claude/t2-edits/smartcrew_scout.md` (read-only, days through Nov 17).

- The SmartCrew site repo on this machine has exact before and after content for every edit, and 68 commits touched page copy between Oct 25 and Nov 18.
- But only about 12 (search, page) rows have a material edit, at least 20 hours before the next pull, a rank before and after, and a move. They sit on 4 pages and 2 edit days. 10 of the 12 are a page entering or leaving the top 10.
- Edits ship in sitewide batches during a Google core update (Nov 10 to about Nov 24). Rows with no edit moved as often (85%) as rows with one (84%). A model that always says "up" would win.
- One fact for the earlier team: SmartCrew PR 41 was merged and deployed on Nov 17 at 22:05 ET. The PR 41 draft ledger in this repo still says "not merged". This run does not touch that ledger.

### 3.5 Counterfactual pairwise diagnostic. DIAGNOSTIC ONLY, not proof. Done.

Script `scripts/claude/t2_counterfactual.py`, output `evals/claude/t2-edits/counterfactual.json`. The frozen ranker `model-next.json` (hash checked) on the 70 added searches, which it was not trained on (training files re-hashed and checked). 1,440 pairs of pages that both rank and both have stored HTML. PREDICTED.

| Check | Result |
| --- | --- |
| Ranker puts the higher page first | 842/1440 (0.585) [0.549, 0.621] |
| Lower page given the higher page's title, H1 and headings: score goes up | 450/1440 (0.312) [0.263, 0.358]. Unchanged in 63% of pairs |
| Lower page given the higher page's title, H1, headings and body: score goes up | 714/1440 (0.496) [0.456, 0.537] |
| Higher page given the lower page's title, H1, headings and body: score goes down | 462/1440 (0.321) [0.279, 0.363] |

History features carry 94% of the ranker's gain and on-page content 3.3%. It cannot tell which of two versions of a page is better. That is the central product gap: nothing in the current model responds to an edit.

## 4. Round 2 (started Nov 19, 14:20 ET): research, theory, build, held-out test, repeat

Round 2 brief: `GOAL.md`, section "Round 2". The order of work is research, then a written theory (`evals/claude/theory.md`), then a reviewer attack on the theory, then the build, then a frozen held-out test. Each loop is logged below with its theory, what was built, the held-out result, and why it failed or passed.

### 4.0 Round 1 freeze and today's data (Nov 19, 14:20 to 14:45 ET). Done.

- The three round 1 preregistrations are frozen with sha256 manifests in commit `4c8b557`. The predictor files are byte-identical to commit `e91637d`, which was pushed at 08:27 ET on Nov 19. No held-out day had been read at the freeze.
- A fact found at the start of round 2: the earlier team's daily collector pulled the original 30 searches on Nov 19 between 09:38 and 10:51 ET (source `daily:2026-11-19`, 831 rows, the third pull stopped early). That was after `e91637d` was pushed and before this run wrote any "written before the pull" file. Each preregistration has a dated addendum that says so. Nov 19 for the original 30 is scored as pulled, like Nov 18.
- The frozen predictors' answers for Nov 19 were written and pushed before this run pulled anything (`evals/claude/ahead/ahead-2026-11-19.json.gz`, sha256 `b3e5d229...`).
- This run pulls the 70 added searches three times on Nov 19 (14:38, about 15:12, about 15:46 ET), 70 credits each. Serper balance before: 1,101. The raw responses are in the append-only store and in `evals/claude/serp/2026-11-19/`.
- Tests at the start of round 2: 485 passed, 6 failed. The 6 are the same DNS failures in `tests/test_layered_fetch.py` as before.
- Held-out days are still unread. Nothing dated Nov 18 or later is scored until the round 2 loop 1 predictors are frozen too, so both rounds are scored on days neither has seen.

### 4.1 Loop 1, step 1: research (Nov 19, 14:45 to 16:15 ET). Done except the flip-noise study.

Seven workers ran in parallel. Two read public sources. Five measured design days only (through Nov 17). Nothing here is a predictor.

Outside evidence (`evals/claude/research/ranking-mechanics.md`, `evals/claude/research/edit-effects.md`, every claim graded and linked):

- A Google core update started on Nov 10 and was not marked complete on Nov 19. Every pull from Nov 11 on is inside it.
- Two identical searches at the same moment return the same URLs in a noisy order (peer-reviewed: Jaccard 0.998, Kendall tau 0.88). Noise grows down the page.
- A new URL has no click history and borrows its site's standing. No source documents a boost for new pages.
- In published split tests about 75% of on-page edits had no detectable effect. A detected effect is at most 0.1 to 0.6 positions. Ordinary daily movement at positions 4 to 10 is 1.0 position. The sign of title edits flips from site to site.
- Google says reprocessing an edit takes a few days to a few weeks. Tests run 2 to 6 weeks.
- No content tool or rank simulator has published a check of predicted edit effects against real rank changes.

Design-day measurements:

| Study | Script and notes | Main result (REAL counts, days through Nov 17) |
| --- | --- | --- |
| Where new URLs land | `scripts/claude/r2_t1_mechanisms.py`, `evals/claude/r2/t1-mechanisms/notes.md` | Slots open at the bottom: 23 of 163 URLs at position 1 left, 82 of 142 at position 10. Of 101 new rows whose site was already on page one, 84 were a same-site swap. The swapped slot is in band for 40 of 84 against 24 for a flat 9. Moving a URL's position over from a sibling search does not help (44 of 96 against 46 for a flat 9). |
| Do edits move rank | `scripts/claude/r2_t2_signal.py`, `evals/claude/r2/t2-signal/notes.md` | Changed pages moved 0.640 of the time against 0.557 for matched unchanged pages: +0.083 [-0.082, +0.267]. They did not move further. At most 0.335 of moves on changed pages can be due to the edit. A perfect content reader could add at most 0.10 to 0.17 accuracy over a position-only rule. A fair test needs 170 to 2,735 changed-and-moved rows. Google showed the new title for 2 of 11 title changes. |
| Content with the site held fixed | `scripts/claude/r2_samesite_pairs.py`, `evals/claude/r2/samesite/notes.md` | 465 same-site pairs with HTML for both pages. BM25F puts the higher page first in 277 of 464 (0.597 [0.548, 0.647]) against 0.522 across sites. A fitted 12-feature model fails on held-out searches (0.486). Applied to the 17 real edits, the sign of the score change matched the move 9 times. |
| Flip noise | `scripts/claude/r2_t3_noise.py` | Still running when this was written. |
| Earlier page versions from the Wayback Machine | `scripts/claude/r2_wayback.py` | Still running when this was written. |

Today's three pulls of the 70 added searches finished at 14:40, 15:14 and 15:56 ET. Serper balance after: 891.

