# Held-out scoring report: round 1 predictors and round 2 loop 1 instruments (Claude goal run)

Scored 2026-11-19, 20:23 to 20:41 ET. Each predictor and instrument was scored once, exactly as its preregistration says. No file was overwritten, `--force` was never used, and no frozen file was edited. Model outputs are PREDICTED. Google positions are REAL (Serper, consensus of the day's pulls). Intervals are 95% query bootstraps, 2000 draws, seed 20261119, unless a table says otherwise.

This text was produced by the scoring worker and saved by the lead unchanged (the worker's session could not write the file).

| What | Commit |
| --- | --- |
| Round 1 freeze (`claude-t1-placement`, `claude-t2-edits`, `claude-t3-movers`) | `4c8b557` (earlier) |
| Round 2 loop 1 freeze (`claude-r2-t3-latent`, `claude-r2-samesite`, `claude-r2-t2-excess`, `claude-r2-t2-longwindow`) | `d854bf0` |
| Round 1 results (`evals/claude/heldout/`) | `dfb00bd` |
| Round 2 results (`evals/claude/heldout-r2/`) | `c4aab8e` |

Before scoring, all seven `claude-*` manifests verified. Test suite: 587 passed, 6 failed, all 6 in `tests/test_layered_fetch.py` (the known DNS issue on this box).

## Days and pulls (REAL)

| Day | Searches | Pulls per search | How it was scored |
| --- | --- | --- | --- |
| 2026-11-17 (day A only) | original 30 | 3 for all 30 | design day, used only as yesterday |
| 2026-11-18 | original 30 | 2 for 15, 3 for 15 | as pulled. The scorers need no flag for this day. |
| 2026-11-19 | original 30 and added 70 | 2 for 6, 3 for 94. All six 2-pull searches are original 30 searches. Every added 70 search has 3. | as pulled, with `--allow-incomplete`, as the dated addenda of the round 1 preregistrations and the round 2 preregistrations say |

The 70 added searches have no pulls on Nov 17 or Nov 18. So every Target 3 transition and the latent scorer cover the original 30 only. Searches outside the 100 (Revorion and others) are reported apart and never enter a decision.

## Summary

| Item | Rule | Result | Prediction made before scoring | Held? |
| --- | --- | --- | --- | --- |
| Target 1, strict new, band3 | beat best of domain prior, 7, 11 with interval above 0 | **FAIL** (pooled +0.007 [-0.095, +0.125]) | theory.md: pass, probability about 0.8 | **No** |
| Target 1, per-day reading | two days with 20+ rows each passing | **FAIL** (Nov 18: 1 row, NOT ENOUGH ROWS; Nov 19: 134 rows, fail) | theory.md: not reachable this week | Yes |
| Target 1, err3 reading | same, absolute error 3 or less | **FAIL** (-0.126 [-0.192, -0.045]) | fail | Yes |
| Target 2, L and H | n 40 or more, L and H both | **NOT ENOUGH ROWS** (33 changed-and-moved rows) | about 35 rows through Nov 19, too few; H fails | Yes |
| Target 3, scorer A | C1 and C2 | **FAIL** (C1 fails, C2 holds) | A fails C1, holds C2 | Yes |
| Target 3, scorer B | C1 and C2 | **FAIL** (C1 fails, C2 fails) | theory.md: B passes the brief's wording alone; fails the guard | **No** on the wording, yes on the guard |
| Latent-order scorer R1, R2 | recall lower bound above 0.5 with at least half the calls right | **FAIL** on both transitions | recall 0.35 to 0.55, precision 0.40 to 0.55, lower bound under 0.5, Target 3 fails | **No** on the ranges, yes on the fail |
| Noise check | noise share 0.75 to 1.3 on each transition | 1.03 and 0.99 | 0.75 to 1.3 | Yes |
| Same-site check | both scorers 0.55 to 0.65 and above cross-site | **did not hold** (0.529 and 0.520) | 0.55 to 0.65, above cross-site | **No** |
| Excess-movement test | P(moved) difference below +0.20, interval includes 0 | **held** (+0.036 [-0.065, +0.140]) | below +0.20, interval includes 0 | Yes |
| Long-window test | round 1 L and H at 14 to 28 days | **not scored** (script refuses before 2026-12-03) | no number in theory.md | n/a |

No target passed. The two theory falsifiers that fired: Target 1 ("the frozen rule does not beat the best brief baseline on the pooled held-out strict-new rows"), and possibly Target 3 item 2 (see the latent section).

## Target 1: new-URL placement (`claude-t1-placement`)

Rule (preregistration): on strict-new rows, band3. Per-day pass needs two days, each with at least 20 rows on 10 searches, with model minus the best brief baseline above 0. Pooled pass needs two days, 40 rows, 10 searches, lower bound above 0. Position 9 is a reference, not a baseline. Predictions PREDICTED, truth REAL.

band3 (primary):

| Slice | Rows | Searches | Model | Domain prior | Position 7 | Position 11 | Model minus best baseline | Position 9 (reference) | Model minus position 9 | Verdict |
| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- |
| Strict new, Nov 18 | 1 | 1 | 1/1 (1.000) | 1/1 (1.000) | 0/1 (0.000) | 0/1 (0.000) | domain prior: +0.000 [+0.000, +0.000] | 0/1 (0.000) | +1.000 | NOT ENOUGH ROWS |
| Strict new, Nov 19 | 134 | 41 | 47/134 (0.351) [0.273, 0.447] | 24/134 (0.179) [0.118, 0.250] | 47/134 (0.351) [0.285, 0.422] | 13/134 (0.097) [0.054, 0.146] | position 7: +0.000 [-0.108, +0.120] | 41/134 (0.306) [0.236, 0.398] | +0.045 [-0.016, +0.114] | FAIL |
| Strict new, Nov 19, original 30 | 12 | 5 | 4/12 (0.333) [0.154, 1.000] | 3/12 (0.250) | 6/12 (0.500) [0.333, 1.000] | 0/12 (0.000) | position 7: -0.167 [-0.417, +0.400] | 2/12 (0.167) | +0.167 [+0.000, +0.800] | reported |
| Strict new, Nov 19, added 70 | 122 | 36 | 43/122 (0.352) [0.277, 0.444] | 21/122 (0.172) | 41/122 (0.336) [0.264, 0.414] | 13/122 (0.107) | position 7: +0.016 [-0.092, +0.140] | 39/122 (0.320) | +0.033 [-0.031, +0.105] | reported |
| **Strict new, pooled** | 135 | 42 | 48/135 (0.356) [0.277, 0.448] | 25/135 (0.185) [0.125, 0.262] | 47/135 (0.348) [0.287, 0.419] | 13/135 (0.096) [0.054, 0.143] | position 7: **+0.007 [-0.095, +0.125]** | 41/135 (0.304) [0.232, 0.386] | +0.052 [-0.009, +0.125] | **FAIL** |
| Reentrant, Nov 18 | 39 | 18 | 21/39 (0.538) [0.385, 0.710] | 13/39 (0.333) | 14/39 (0.359) | 8/39 (0.205) | position 7: +0.179 [-0.020, +0.400] | 17/39 (0.436) | +0.103 [-0.054, +0.294] | secondary |
| Reentrant, Nov 19 | 116 | 59 | 81/116 (0.698) [0.602, 0.795] | 30/116 (0.259) | 37/116 (0.319) | 27/116 (0.233) | position 7: +0.379 [+0.256, +0.496] | 72/116 (0.621) | +0.078 [-0.046, +0.207] | secondary |
| Reentrant, pooled | 155 | 66 | 102/155 (0.658) [0.571, 0.745] | 43/155 (0.277) | 51/155 (0.329) | 35/155 (0.226) | position 7: +0.329 [+0.217, +0.435] | 89/155 (0.574) | +0.084 [-0.023, +0.193] | secondary |
| All non-survivors, Nov 18 | 40 | 19 | 22/40 (0.550) [0.395, 0.730] | 14/40 (0.350) | 14/40 (0.350) | 8/40 (0.200) | domain prior: +0.200 [+0.000, +0.421] | 17/40 (0.425) | +0.125 [-0.027, +0.323] | reported |
| All non-survivors, Nov 19 | 250 | 82 | 128/250 (0.512) [0.435, 0.605] | 54/250 (0.216) | 84/250 (0.336) | 40/250 (0.160) | position 7: +0.176 [+0.086, +0.277] | 113/250 (0.452) | +0.060 [-0.008, +0.133] | reported |
| All non-survivors, pooled | 290 | 89 | 150/290 (0.517) [0.449, 0.592] | 68/290 (0.234) | 98/290 (0.338) | 48/290 (0.166) | position 7: +0.179 [+0.098, +0.268] | 130/290 (0.448) | +0.069 [+0.004, +0.134] | reported |
| Other search groups, Nov 18 | 40 | 13 | 26/40 (0.650) | 13/40 (0.325) | 13/40 (0.325) | 5/40 (0.125) | domain prior: +0.325 [+0.182, +0.528] | 20/40 (0.500) | +0.150 [+0.000, +0.357] | not in decision |
| Other search groups, Nov 19 | 63 | 13 | 22/63 (0.349) | 11/63 (0.175) | 22/63 (0.349) | 6/63 (0.095) | position 7: +0.000 [-0.139, +0.197] | 20/63 (0.317) | +0.032 [-0.068, +0.184] | not in decision |

err3 (secondary reading, strict new):

| Slice | Rows | Model | Domain prior | Position 7 | Position 11 | Model minus position 7 | Model minus position 9 |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| Nov 18 | 1 | 1/1 | 1/1 | 0/1 | 0/1 | domain prior best: +0.000 | +1.000 |
| Nov 19 | 134 | 84/134 (0.627) [0.554, 0.722] | 58/134 (0.433) | 102/134 (0.761) [0.709, 0.823] | 41/134 (0.306) | -0.134 [-0.197, -0.053] | +0.052 [+0.000, +0.117] |
| Pooled | 135 | 85/135 (0.630) [0.556, 0.721] | 59/135 (0.437) | 102/135 (0.756) [0.702, 0.815] | 41/135 (0.304) | -0.126 [-0.192, -0.045] | +0.059 [+0.006, +0.126] |

Target 1 decision (`pooled.json`): days passing 0, per-day pass false, pooled pass false, **Target 1 FAIL**.

Predictions made before scoring, and whether they held:

- theory.md: "the pooled reading ... passes with probability about 0.8 if the design effect holds." **Wrong.** The model ties position 7 on the pooled strict-new rows.
- theory.md, what would prove the theory wrong: "The frozen rule does not beat the best brief baseline on the pooled held-out strict-new rows." **This happened.**
- theory.md said the win "comes mostly from door 2" (newcomers land low). On held-out strict-new rows the position 9 reference (0.304) scored below position 7 (0.348). On design days position 9 was above position 7 (131 against 103 of 303).
- theory.md: the per-day reading is not reachable this week. **Held.** Nov 18 had 1 strict-new row.
- theory.md and the preregistration: err3 fails. **Held.**
- Preregistration: Nov 18 has about 5 strict-new rows. It had 1. Too few either way. **Held.**
- Preregistration: if Nov 19 has 30 or more strict-new rows, a per-day pass has probability 0.40 to 0.82. Nov 19 had 134 rows and failed.
- Preregistration: the gain over position 9 is small. **Held** (+0.052 [-0.009, +0.125]).
- theory.md expected about 70 strict-new rows on Nov 19 for the added 70. There were 122.
- The reentrant class (secondary, not part of the pass rule) beat the best brief baseline on Nov 19 and pooled.

Changed pages that ranked yesterday (descriptive, from the Target 2 run, never part of a pass): 31 rows. band3: persistence 18/31 (0.581) [0.393, 0.767], domain prior 16/31 (0.516), position 7 8/31 (0.258), position 11 9/31 (0.290); persistence minus domain prior +0.065 [+0.000, +0.152]. err3: persistence 20/31 (0.645), domain prior 20/31 (0.645), difference +0.000 [-0.094, +0.091].

## Target 2: edit direction (`claude-t2-edits`)

Rule (preregistration): on held-out changed-and-moved rows, **L** needs n of 40 or more, the content-aware rule's lower bound above 0.55, and its rate above the larger class share. **H** needs content-aware minus position-only with a lower bound above 0. Target 2 passes only when both hold.

Rows through 2026-11-19 (REAL positions): 871 rows, 752 usable, 342 usable URL fetch pairs, 25 material URL fetch pairs, 63 material rows, **33 material and moved rows** on 17 URLs, 7 sites and 27 searches, 428 moved control rows. Every row's position at B comes from Nov 19. No row has its position at B on Nov 18 (date pairs Nov 6, 9, 11, 13, 16, 17 and 18 to Nov 19).

| Rows | n | Searches | Up / down | Larger class | Content-aware rule (PREDICTED) | Position-only rule (PREDICTED) | Content step alone | Frozen default "down" | Content-aware minus position-only | Content-aware minus default |
| --- | ---: | ---: | --- | ---: | --- | --- | --- | --- | --- | --- |
| **Changed and moved** | 33 | 27 | 12 / 21 | 0.636 | 27/33 (0.818) [0.647, 0.943] | 27/33 (0.818) [0.647, 0.943] | 21/33 (0.636) [0.457, 0.806] | 21/33 (0.636) [0.457, 0.806] | +0.000 [+0.000, +0.000] | +0.182 [+0.034, +0.333] |
| Changed and moved, without the client site | 8 | 7 | 3 / 5 | 0.625 | 6/8 (0.750) [0.429, 1.000] | 6/8 (0.750) [0.429, 1.000] | 5/8 (0.625) | 5/8 (0.625) | +0.000 | +0.125 [+0.000, +0.300] |
| Changed and moved, top 10 at A | 28 | 24 | 7 / 21 | 0.750 | 22/28 (0.786) [0.613, 0.935] | 22/28 (0.786) [0.613, 0.935] | 21/28 (0.750) | 21/28 (0.750) [0.560, 0.909] | +0.000 | +0.036 [+0.000, +0.115] |
| Unchanged and moved (control) | 428 | 99 | 115 / 313 | 0.731 | 316/428 (0.738) [0.695, 0.779] | 316/428 (0.738) [0.695, 0.779] | 313/428 (0.731) | 313/428 (0.731) [0.684, 0.776] | +0.000 | +0.007 [-0.027, +0.043] |
| Unchanged and moved, top 10 at A | 411 | 99 | 98 / 313 | 0.762 | 299/411 (0.727) [0.682, 0.771] | 299/411 (0.727) [0.682, 0.771] | 313/411 (0.762) | 313/411 (0.762) [0.716, 0.805] | +0.000 | -0.034 [-0.066, -0.005] |

Decision: n at least 40 false, L false, H false, **Target 2 NOT ENOUGH ROWS** (not a pass). The content step changed no call on any of the 33 rows: the content-aware and position-only rules agree everywhere.

Predictions made before scoring, and whether they held:

- theory.md: "about 35 rows through Nov 19 (too few to run the test)". There were 33. **Held.**
- theory.md: "H cannot pass on any number of rows this month." **Held** (difference exactly 0).
- theory.md: L is "about even" once there are 40 rows. Not tested: 33 rows.
- theory.md claim 1: position state decides direction about 70% of the time, edited or not. The position-only rule is 0.818 on changed rows and 0.738 on control rows.
- theory.md, what would prove the theory wrong: the content-aware rule beats the position-only rule (it did not), or the position rule falls to coin level on changed rows while near 0.70 on unchanged ones (it did not). Neither happened.

## Target 3: pairwise order of movers (`claude-t3-movers`)

Rule (preregistration): **C1**, pooled over consecutive held-out transitions, flip accuracy lower bound above 0.5, with at least 100 flipped pairs from at least 20 searches. **C2**, all-pairs accuracy at least persistence (point estimate). Target 3 passes only when one scorer meets both. Pairs are both URLs in both consensus top 10s, strictly ordered both days. Original 30 searches only (see above).

| Transition | Searches with pairs | Pairs | Flips (searches with flips) | Scorer | Flip calls | Flip accuracy (C1) | Calls that were right | Model all pairs | Persistence all pairs | Model minus persistence (C2) |
| --- | ---: | ---: | --- | --- | ---: | --- | --- | --- | --- | --- |
| Nov 17 to 18 | 28 | 673 | 59 (23) | A | 2 | 2/59 (0.034) [0.000, 0.100] | 2/2 (1.000) | 616/673 (0.915) | 614/673 (0.912) [0.874, 0.945] | +0.003 [+0.000, +0.007] |
| Nov 17 to 18 | 28 | 673 | 59 (23) | B | 110 | 27/59 (0.458) [0.296, 0.636] | 27/110 (0.245) [0.142, 0.378] | 558/673 (0.829) | 614/673 (0.912) | -0.083 [-0.140, -0.032] |
| Nov 18 to 19 | 27 | 562 | 39 (15) | A | 6 | 5/39 (0.128) [0.026, 0.265] | 5/6 (0.833) [0.400, 1.000] | 527/562 (0.938) | 523/562 (0.931) [0.886, 0.962] | +0.007 [-0.002, +0.020] |
| Nov 18 to 19 | 27 | 562 | 39 (15) | B | 77 | 14/39 (0.359) [0.186, 0.595] | 14/77 (0.182) [0.080, 0.342] | 474/562 (0.843) | 523/562 (0.931) | -0.087 [-0.152, -0.025] |
| **Pooled** | 28 | 1235 | 98 (25) | **A** | 8 | 7/98 (0.071) [0.018, 0.157] | 7/8 (0.875) [0.500, 1.000] | 1143/1235 (0.926) | 1137/1235 (0.921) [0.885, 0.949] | +0.005 [+0.000, +0.012] |
| **Pooled** | 28 | 1235 | 98 (25) | **B** | 187 | 41/98 (0.418) [0.288, 0.580] | 41/187 (0.219) [0.141, 0.317] | 1032/1235 (0.836) | 1137/1235 (0.921) | -0.085 [-0.134, -0.042] |

Decision: scorer A: C1 false, C2 true, **FAIL**. Scorer B: C1 false, C2 false, **FAIL**. **Target 3 FAIL.** The pooled test has 98 flipped pairs, under the 100 that C1 requires, so C1 could not have passed on count alone. Both lower bounds are also under 0.5.

Searches outside the 100 (Revorion, one pull a day, not in the decision): Nov 17 to 18, 356 pairs, 32 flips, no calls by either scorer. Nov 18 to 19, 295 pairs, 29 flips, no calls.

Predictions made before scoring, and whether they held:

- Preregistration: scorer A fails C1 and holds C2. **Held.**
- Preregistration: scorer B fails C2. **Held.** Whether B meets C1 was left open.
- theory.md section 4: on the brief's wording alone the contested-pair rule (B) passes, "and it means nothing". **Wrong.** B caught 41 of 98 flips (0.418), lower bound 0.288. On design days it caught 35 of 44.
- theory.md: Target 3 with the persistence guard fails. **Held.**

## Round 2: latent-order scorer (`claude-r2-t3-latent`)

Rule (preregistration, from theory.md): a rule passes on a transition when its flip recall has a lower bound above 0.5 and it is no worse than persistence on all pairs. Original 30 searches. P(flip) and calls use only pulls on or before day A. Each transition scored once. There is no pooled interval; the sum line is plain counts.

| Transition | Searches | Pairs | Flips | Model-expected flips | Day A universe calls R1 / R2 | Rule | Calls (scored pairs) | Flip recall | Precision | All pairs minus persistence | Recall bound above 0.5 | Not below persistence |
| --- | ---: | ---: | ---: | ---: | --- | --- | ---: | --- | --- | --- | --- | --- |
| Nov 17 to 18 | 28 | 673 | 59 | 56.3 | 51 / 120 | R1 | 19 | 9/59 (0.153) [0.034, 0.314] | 9/19 (0.474) [0.200, 0.750] | -0.001 [-0.016, +0.012] | no | no |
| Nov 17 to 18 | 28 | 673 | 59 | 56.3 | 51 / 120 | R2 | 49 | 15/59 (0.254) [0.093, 0.470] | 15/49 (0.306) [0.143, 0.446] | -0.028 [-0.050, -0.007] | no | no |
| Nov 18 to 19 | 27 | 562 | 39 | 58.1 | 79 / 161 | R1 | 39 | 13/39 (0.333) [0.143, 0.524] | 13/39 (0.333) [0.130, 0.594] | -0.023 [-0.054, +0.014] | no | no |
| Nov 18 to 19 | 27 | 562 | 39 | 58.1 | 79 / 161 | R2 | 81 | 22/39 (0.564) [0.345, 0.741] | 22/81 (0.272) [0.116, 0.459] | -0.066 [-0.108, -0.014] | no | no |
| Sum of both (no interval) | 28 | 1235 | 98 | 114.4 | | R1 | 58 | 22/98 (0.224) | 22/58 (0.379) | -0.011 | | |
| Sum of both (no interval) | 28 | 1235 | 98 | 114.4 | | R2 | 130 | 37/98 (0.378) | 37/130 (0.285) | -0.045 | | |

Verdict: **FAIL** for R1 and R2 on both transitions. Calibration check (not a rule): on Nov 18 to 19, pairs with P(flip) above 0.5 were expected to flip 24.8 times and flipped 13 times.

Predictions made before scoring, and whether they held:

- theory.md: "flip recall 0.35 to 0.55 with precision 0.40 to 0.55" (the design notes compare this with R2). **Wrong.** R2 recall was 0.254 on Nov 17 to 18 (below) and 0.564 on Nov 18 to 19 (above). R2 precision was 0.306 and 0.272, below 0.40 on both. Summed, R2 recall 0.378 is inside the range and precision 0.285 is below it.
- theory.md: "The lower bound stays under 0.5." **Held.**
- theory.md: "Target 3 with the guard fails." **Held.** Both rules are below persistence on both transitions (point estimates).
- theory.md, what would prove the theory wrong, item 2: "The latent-order scorer's calls are right far less than 40% of the time." theory.md gives no number for "far less". R2 was right 30.6% and 27.2% of the time (upper bounds 0.446 and 0.459). R1 was right 47.4% and 33.3%. A reader may judge this falsifier met for R2. This report does not decide it.
- Item 3 (a scorer reaches a recall lower bound above 0.5 with half its calls right): did not happen.
- Not a stated prediction: on design days R1 beat persistence (+0.010 [+0.001, +0.025]). On both held-out transitions it did not.

## Round 2: permutation noise check (`claude-r2-t3-latent`)

Rule: noise share of the flip count = shuffled expected flips / observed flips. PREDICTED 0.75 to 1.3 on each transition. Under 0.7 would prove the theory wrong. 2000 permutations, seed 20261119.

| Transition | Searches (with pairs) | Pulls day A / day B | Observed flips / pairs | Shuffled expected flips / pairs | Shuffled total, 2.5% to 97.5% | P(shuffled at least observed) | Noise share [95% CI] | Exact check | In 0.75 to 1.3 | Under 0.7 |
| --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| Nov 17 to 18 | 30 (28) | 3 all / 2 for 15, 3 for 15 | 59 / 673 | 60.6 / 711.7 | 47 to 75 | 0.616 | 1.03 [0.79, 1.37] | 60.7 / 711.3, 1.03 [0.78, 1.37] | yes | no |
| Nov 18 to 19 | 30 (27) | 2 for 15, 3 for 15 / 2 for 6, 3 for 24 | 39 / 562 | 38.5 / 582.3 | 29 to 49 | 0.496 | 0.99 [0.74, 1.40] | 38.4 / 582.5, 0.98 [0.74, 1.40] | yes | no |

Verdict: the prediction **held** on both transitions. The falsifier did not fire. The flips seen on both held-out transitions are the number that reshuffling the same pulls produces.

## Round 2: same-site content check (`claude-r2-samesite`)

Rule (preregistration): diagnostic, no Target passes on it. The prediction holds when BM25F and the seven-feature text vote each order same-site top-10 pairs at a rate between 0.55 and 0.65 (point estimate, ties half) and above their own cross-site rate on the same lists. Scorer outputs PREDICTED, orders REAL.

Pairs: 182 same-site pairs on Nov 18 and 19 together. 125 lacked usable HTML for both pages, 6 were duplicate content. 51 scored, on 25 searches and 10 sites (46 distinct URL pairs). The top 5 sites hold 88% of them (hcss.com 20, smartcrewhq.com 10, slashdot.org 8, trimble.com 4, agtek.com 3). 1,761 cross-site pairs scored on 91 searches.

| Days | Same-site pairs / searches / sites | BM25F same-site | BM25F cross-site | BM25F same minus cross | Text vote same-site | Text vote cross-site | Text vote same minus cross |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Pooled, Nov 18 and 19** | 51 / 25 / 10 | 27/51 (0.529) [0.408, 0.707] | 903/1761 (0.513) [0.478, 0.548] | +0.017 [-0.106, +0.196] | 26.5/51 (0.520) [0.406, 0.637] | 909.5/1761 (0.516) [0.482, 0.550] | +0.003 [-0.113, +0.136] |
| Nov 18 | 14 / 10 / 5 | 10/14 (0.714) [0.499, 0.929] | 284/545 (0.521) | +0.193 [-0.073, +0.422] | 6.5/14 (0.464) [0.143, 0.786] | 276.5/545 (0.507) | -0.043 [-0.380, +0.272] |
| Nov 19 | 42 / 21 / 9 | 20/42 (0.476) [0.340, 0.706] | 818/1600 (0.511) | -0.035 [-0.176, +0.195] | 21.5/42 (0.512) [0.367, 0.667] | 824/1600 (0.515) | -0.003 [-0.150, +0.169] |

Prediction check (pooled): BM25F between 0.55 and 0.65: no (0.529). BM25F above cross-site: yes (0.529 against 0.513). Text vote between 0.55 and 0.65: no (0.520). Text vote above cross-site: yes (0.520 against 0.516). **The prediction did not hold.**

- theory.md: "PREDICTED: between 0.55 and 0.65, above the cross-site rate." **Wrong.** Both scorers are under 0.55, and their edge over cross-site pairs is +0.017 and +0.003 with intervals well across zero.
- The design study number in theory.md (BM25F 277/464, 0.597) came from Nov 16 deep lists. This script's own design dry run on Nov 16 and 17 top-10 lists had already given BM25F 0.500. The preregistration printed both.

## Round 2: excess-movement test (`claude-r2-t2-excess`)

Rule (preregistration): the prediction holds when, for material against matched unchanged rows, all rows, P(moved), search clusters, the difference is below +0.20 and its interval includes zero. The theory is wrong if the difference is above +0.20 with the interval above zero. 95% cluster bootstrap, 2000 draws, seed 20261119. Positions REAL. The instrument predicts nothing.

Rows through 2026-11-19: 752 usable rows on 100 searches (o30 365, a70 387). 63 changed rows (60 matched, 3 dropped for lack of a control), 25 changed URLs on 10 sites, 51 changed rows on the client site, 30 changed rows starting at 2 to 10, 33 changed rows moved. Every position at B is from Nov 19.

| Comparison | Clusters | Changed rows matched | Outcome | Changed | Matched unchanged | Difference [95% CI] |
| --- | --- | ---: | --- | ---: | ---: | --- |
| **Material vs not, all rows** | search | 60 of 63 | P(moved) | 0.517 | 0.481 | **+0.036 [-0.065, +0.140]** |
| Material vs not, all rows | URL | 60 of 63 | P(moved) | 0.517 | 0.481 | +0.036 [-0.122, +0.160] |
| Material vs not, all rows | site | 60 of 63 | P(moved) | 0.517 | 0.481 | +0.036 [-0.170, +0.191] |
| Material vs not, all rows | search | 60 of 63 | mean abs change | 2.12 | 1.49 | +0.62 [-0.04, +1.26] |
| Material vs not, all rows | search | 60 of 63 | P(crossed top 10) | 0.317 | 0.245 | +0.071 [-0.046, +0.195] |
| Material vs not, start 2 to 10 | search | 28 of 30 | P(moved) | 0.786 | 0.814 | -0.028 [-0.184, +0.129] |
| Material vs not, start 2 to 10 | URL | 28 of 30 | P(moved) | 0.786 | 0.814 | -0.028 [-0.212, +0.132] |
| Material vs not, start 2 to 10 | site | 28 of 30 | P(moved) | 0.786 | 0.814 | -0.028 [-0.244, +0.196] |
| Material vs not, start 2 to 10 | search | 28 of 30 | mean abs change | 2.71 | 2.29 | +0.42 [-0.46, +1.43] |
| Material vs not, without the client site | search | 11 of 12 | P(moved) | 0.636 | 0.598 | +0.038 [-0.230, +0.339] |
| Material vs not, without the client site | search | 11 of 12 | mean abs change | 1.73 | 1.71 | +0.01 [-1.21, +1.70] |
| Material vs not, start 2 to 10, without the client site | search | 7 of 8 | P(moved) | 0.857 | 0.864 | -0.007 [-0.322, +0.203] |
| Material vs no change at all | search | 60 of 63 | P(moved) | 0.517 | 0.486 | +0.030 [-0.074, +0.138] |
| Any change vs none | search | 168 of 171 | P(moved) | 0.518 | 0.531 | -0.014 [-0.082, +0.052] |
| Any change vs none | URL | 168 of 171 | P(moved) | 0.518 | 0.531 | -0.014 [-0.095, +0.064] |
| Any change vs none | search | 168 of 171 | mean abs change | 1.83 | 1.62 | +0.21 [-0.19, +0.61] |

Verdict: the prediction **held** (+0.036, below +0.20, interval includes zero). The falsifier did not fire.

- theory.md expected about 26 changed pages, 56 changed rows and about 35 changed-and-moved rows. There were 25 changed URLs, 63 changed rows and 33 changed-and-moved rows. **Held.**
- On design rows the start 2 to 10 cut was +0.215 [+0.033, +0.400]. On held-out rows it is -0.028 [-0.184, +0.129].

## Round 2: long-window test (`claude-r2-t2-longwindow`)

Frozen in `d854bf0`, not scored. The script refuses a `--max-date` before 2026-12-03. theory.md gives no number for this window.

## Flags, deviations and notes for a leakage reviewer

1. **`--allow-incomplete` was used five times**, every time on a run whose outcome day is Nov 19: Target 1 Nov 19, Target 3 Nov 18 to 19, latent scorer Nov 18 to 19, noise check Nov 18 to 19, and the same-site check (Nov 18 and 19 in one run). The round 1 addenda and the round 2 preregistrations say Nov 19 is scored as pulled with this flag. For Target 1, Target 3, the latent scorer and the same-site check, the run was first made without the flag and refused before writing anything (`{'2': 6, '3': 94}`, and `{'2': 6, '3': 24}` for the original 30 alone). The noise check covers the same 30 searches as the latent scorer, so it was run with the flag directly. Nov 18 needed no flag.
2. **Round 3 page fetches inside the Nov 19 seal.** Round 3 (`scripts/claude/r3_fetch.py`) appended about 1,200 plain-HTTP page fetches to the raw `pages` table between 19:30 and 19:53 ET on Nov 19. That is ET day Nov 19, inside `--max-date 2026-11-19`. The frozen code takes the latest usable fetch of a day, so these fetches enter the Target 2 dataset, the excess test and the same-site check. The rows were audited afterwards (page-fetch metadata only, no HTML, no SERP rows). Nothing was rescored.
   - Target 2 and the excess test: 30 of 752 usable rows on 13 URLs. 12 of those URLs had no other Nov 19 fetch, so their rows exist only because of the round 3 fetch. One is a changed-and-moved row (gitnux.org/best/dirt-takeoff-software, "best all-in-one dirtworks software", position 6 on Nov 9 to absent on Nov 19). Both rules called it "down", which was right. The other 29 are unchanged rows (22 moved).
   - Same-site check: 8 of the 316 URLs in scored pairs have a round 3 evening fetch (4 of them also had an earlier Nov 19 fetch). They touch 7 of the 51 pooled same-site pairs, all on hcss.com and mostly its home page. BM25F got 3 of those 7 right, and the text vote 3. They also touch 90 cross-site pairs.
   - These fetches came after the Nov 19 rank observations (original 30 pulled 09:38 to 10:51 ET, added 70 at 14:38 to 15:56 ET). The Target 2 preregistration describes "after" pages from re-fetch passes whose URL list was fixed before the day's pulls. Round 3's list came from its own site-restricted pulls. No decision depends on these rows: Target 2 is short of 40 rows with or without the gitnux row, and the content-aware and position-only rules agree on every row.
3. Round 3 SERP pulls are written to `serp_raw` only, not to `serp_results` (per the docstring of `scripts/claude/r3_pull.py`). So they cannot enter any consensus used here. This was checked in the code, not in the database.
4. The written-before-the-pull checks: Nov 18 has no ahead file (pulled before this run), so Target 1 Nov 18 and Target 3 Nov 17 to 18 report `ahead_file: false`. For Nov 19: Target 1 checked 313 of 313 rows, Target 3 checked 857 of 857 pairs for each scorer, 0 mismatches. The original 30 were pulled on the morning of Nov 19 before that file existed. The round 1 addenda say so. The proof that those predictors came first is commit `e91637d`.
5. Choices made when writing the round 2 preregistrations (before scoring) that a reviewer should check: the latent scorer's pass rule is theory.md's wording, per transition, without round 1's 100-flip minimum and without a pooled interval (the frozen script has none). The same-site check uses seven features, not the "eight-feature" vote theory.md names, because the script drops the SERP title. The long-window preregistration fixes no run date beyond the script's 2026-12-03 floor.
6. The Target 3 pooled test has 98 flipped pairs, 2 under C1's minimum of 100.
7. No scorer crashed. No environment fix was needed. No frozen file was edited.
