# Target 3, round 2 research: why do pairs swap order between days?

Measurement only. No predictor was built, tuned or frozen. Design days only: rows come from `rsdata.load_rows("2026-11-17")` and the script asserts that the latest day loaded is 2026-11-17. No snapshot folder or SQLite file was opened directly, no network, no Serper. Every number below is printed by `python -m scripts.claude.r2_t3_noise` (about 60 seconds). The full output is in `stdout.txt` and all numbers are in `results.json`.

Transition studied: Nov 16 to Nov 17, original 30 searches. Nov 16 has one morning pull (08:38 ET) and three voting evening pulls (16:26, 16:57, 17:29 ET). Nov 17 has three pulls (09:32 to 10:47 ET). All sources used the same Serper settings (US, en, num=10). The pair table equals `rsdata.flip_pairs`: 767 pairs, 44 flips, 29 searches with pairs. My query bootstrap reproduces `metrics.bootstrap_rate` exactly on that table (44/767, 0.057 [0.030, 0.097]). Intervals are 95% query bootstraps (2000 draws, seed 20261119). Rates on a subset resample all 29 searches, not only the searches in the subset.

## Headline

1. The 44 flips of Nov 16 to Nov 17 are what pure same-day noise produces. Shuffling the six voting pulls between the two days gives 45.0 expected flips against 44 observed.
2. A noise model that lets pairs change overnight fits best with no change at all. At the 95% upper bound, about 2 of the 44 flips are real order changes.
3. Real change does exist, but it builds up over days, not hours. Single-pull disagreement rises from 0.070 within a session to 0.130 at about 2 days and 0.203 at 10 to 11 days.
4. An oracle that knew each pair's true order probability would catch about 0.60 to 0.72 of flips with half its calls right. Using only day one's three pulls, the same oracle catches none in the model. On real data it catches 7/44 (the median artifacts and exact ties). The gap is almost all estimation noise from too few pulls, not real change.
5. Pulls are not exchangeable within a session. Identical top-10 lists are much more common inside a session than across days. Which single pull dissents matters a lot (0.27 versus 0.02 flip rate). This points to a few SERP "versions" being served, rather than independent coin flips per pair.

## Q1. Noise share of flips

Permutation test. For each search, all 20 ways to split the six voting pulls (3 evening of Nov 16, 3 of Nov 17) into two pseudo-days of three were enumerated exactly (10 distinct up to swapping; the script checks the symmetry). The pseudo-days use the same consensus rule and pair rule.

| Quantity | Value |
| --- | --- |
| Observed | 44 flips / 767 pairs = 0.0574 |
| Expected under exchangeable pulls | 45.0 flips / 811.9 pairs = 0.0554 |
| Null spread of total flips (one random split per search, 100000 draws) | 30 to 61; P(null >= 44) = 0.56 |
| Null spread of the flip rate | 0.0373 to 0.0755; P(null rate >= 0.0574) = 0.42 |
| Null rate, query bootstrap | 0.0554 [0.0346, 0.0785] |
| Noise share of the flip count (null flips / observed flips) | 1.02 [0.78, 1.47] |
| Noise share of the flip rate | 0.97 [0.73, 1.40] |
| Excess flips (observed minus null) | -1.0 [-13.2, +13.0] |

So the flip count gives no sign of real change. At most about 13 of the 44 flips could be excess, at the 95% bound.

This test has a weak spot. A pair that truly swapped (3 to 0 on Nov 16, 0 to 3 on Nov 17) pools to 3 against 3 and flips under every shuffle, so the flip count cannot see it. Two sharper checks:

- Pull level, on URL pairs present in both pulls: same-day disagreement 0.0701 [0.0498, 0.0928], cross-day 0.0798 [0.0556, 0.1065]. The difference is 0.0097 [0.0002, 0.0201], with permutation P = 0.057. That is a small, borderline cross-day excess.
- Clean swaps are almost absent. Among the 611 scored pairs with three informative votes on both days, 1 pair went from unanimous one way to unanimous the other way. The stationary model expects 1.3 (model check table below).

Nov 16 alone (morning pull against the evening session, 8 hours apart). Each of the four pulls was held out in turn against the consensus of the other three.

| Comparison | Flips / pairs | Rate [CI] |
| --- | --- | --- |
| Morning pull vs evening consensus | 56 / 841 | 0.0666 [0.0377, 0.1054] |
| Each evening pull vs the other three (mean) | 33.7 / 785.0 | 0.0429 [0.0249, 0.0643] |
| Morning minus evening | | +0.0237 [+0.0002, +0.0547] |

Permutation P(null flips >= 56) = 0.020 and P(null rate >= observed) = 0.057. At the pull level, morning vs evening is 0.0760 and evening vs evening is 0.0594. The difference is +0.0166 [-0.0101, +0.0486]. The morning pull is somewhat more different from the evening pulls than they are from each other. But that excess is about the same size as the gap in noise level between sessions (below). The 8-hour gap shows no clear real change.

### Are pulls exchangeable?

Pooled share of URL pairs ordered differently between two pulls. In brackets is the number of the 30 searches whose two top-10 lists are identical in order.

| pull | 16m | 16e1 | 16e2 | 16e3 | 17a | 17b | 17c |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 16m | - | 0.067 (6) | 0.094 (4) | 0.069 (6) | 0.086 (1) | 0.117 (1) | 0.102 (1) |
| 16e1 | 0.067 (6) | - | 0.038 (5) | 0.059 (4) | 0.074 (1) | 0.085 (1) | 0.079 (0) |
| 16e2 | 0.094 (4) | 0.038 (5) | - | 0.085 (5) | 0.068 (0) | 0.090 (2) | 0.102 (1) |
| 16e3 | 0.069 (6) | 0.059 (4) | 0.085 (5) | - | 0.061 (1) | 0.095 (1) | 0.070 (2) |
| 17a | 0.086 (1) | 0.074 (1) | 0.068 (0) | 0.061 (1) | - | 0.087 (5) | 0.078 (2) |
| 17b | 0.117 (1) | 0.085 (1) | 0.090 (2) | 0.095 (1) | 0.087 (5) | - | 0.073 (5) |
| 17c | 0.102 (1) | 0.079 (0) | 0.102 (1) | 0.070 (2) | 0.078 (2) | 0.073 (5) | - |

- Identical top-10 lists: 26 of 180 same-session pull pairs, against 9 of 270 Nov 16 evening vs Nov 17 pull pairs. The difference is 0.111 [0.052, 0.189]. Pulls inside one session are correlated, so the three pulls of a day are not three independent draws.
- Same-session noise differs by session. Nov 16 evening is 0.0594 [0.0353, 0.0892] and Nov 17 is 0.0794 [0.0522, 0.1108], a difference of -0.020 [-0.059, +0.017]. On scored pairs the asymmetry runs the other way: 73 pairs had one dissenting vote on Nov 16 and none on Nov 17, and 50 the reverse (binomial p = 0.047). Every model below is symmetric in the two days and cannot fit that.

## Noise model: is any of the 44 real?

For each scored pair, in a fixed orientation (alphabetical), I counted the pulls on each day that show the later URL on top (3 votes per day, fewer when both URLs are missing from a pull). The per-pull order probability q has a symmetric distribution G across pairs. Three shapes of G were fitted to the same data: a free-form grid (NPMLE), a mirrored Beta, and a mirrored logit-normal. On top of G, four ways for a pair to change overnight:

- stationary: no change.
- fresh: with probability pi the pair draws a new q from G.
- swap: with probability pi, q becomes 1 - q.
- drift: logit(q) moves by Normal(0, tau).

| Model | Log-lik | Fitted change | Model flip rate at 3 pulls |
| --- | --- | --- | --- |
| NPMLE stationary | -1319.81 | none | 0.0517 |
| NPMLE fresh / swap | -1319.81 | pi = 0.0000 | 0.0517 |
| Beta stationary / fresh | -1319.96 | pi = 0.0000 | 0.0521 |
| Logit-normal stationary / fresh | -1320.09 | pi = 0.0000 | 0.0525 |
| Beta drift | -1319.96 | tau = 0.011 | 0.0521 |

Every change model fits best at zero change. Profile likelihood, as 2*(max - LL) at chosen pi values (one-sided 95% bound at 2.706):

| Model | pi 0.005 | 0.01 | 0.02 | 0.05 | 0.1 | 95% upper bound |
| --- | --- | --- | --- | --- | --- | --- |
| NPMLE fresh | 1.02 | 2.59 | 6.47 | 20.58 | 47.66 | pi 0.0103 |
| NPMLE swap | 2.67 | 6.65 | 15.94 | 47.95 | 108.30 | pi 0.0050 |
| Beta fresh | 1.07 | 2.64 | 6.48 | 20.47 | 47.45 | pi 0.0102 |
| Logit-normal fresh | 1.09 | 2.66 | 6.49 | 20.43 | 47.34 | pi 0.0101 |
| Beta drift (tau 0.2, 0.3, 0.5, 1.0) | 0.75 | 1.93 | 6.93 | 43.79 | | tau 0.34 |

| Model at its 95% upper bound | True order changes per scored pair | Real flips among the 44 | Real changes the consensus missed |
| --- | --- | --- | --- |
| NPMLE fresh | 0.0052 | 1.9 (0.04) | 0.3 |
| NPMLE swap | 0.0050 | 1.8 (0.04) | 0.3 |
| Beta fresh | 0.0051 | 1.9 (0.04) | 0.3 |
| Logit-normal fresh | 0.0051 | 1.9 (0.04) | 0.3 |
| Beta drift | 0.0031 | 1.1 (0.02) | 1.3 |

The profile bounds treat pairs as independent. A query bootstrap of the Beta fresh model, refit 2000 times, gives pi in [0.0000, 0.0073], and in 77% of the draws pi is below 0.001. The real share of the 44 flips is [0.00, 0.06].

Model check (611 pairs with 3 votes on both days; a cell is pooled with its mirror):

| Votes (day 1, day 2) | Observed | NPMLE stationary | Beta stationary | Logit-normal stationary | Beta fresh, pi = 0.010 |
| --- | --- | --- | --- | --- | --- |
| (0,0) | 422 | 429.7 | 429.3 | 429.2 | 427.9 |
| (0,1) | 50 | 60.5 | 61.3 | 61.5 | 61.4 |
| (0,2) | 8 | 9.6 | 9.1 | 8.9 | 9.3 |
| (0,3) clean swap | 1 | 1.3 | 1.4 | 1.4 | 3.4 |
| (1,0) | 73 | 60.5 | 61.3 | 61.5 | 61.4 |
| (1,1) | 33 | 28.7 | 27.2 | 26.7 | 26.5 |
| (1,2) | 13 | 11.3 | 12.4 | 12.8 | 11.9 |
| (1,3) | 11 | 9.6 | 9.1 | 8.9 | 9.3 |

The only misfit is the day asymmetry ((0,1) against (1,0)). That is a difference in noise level between sessions, not a change in order.

## Q2. Structure of flips

Flip rate per category, with the number of flips expected under exchangeable pulls. Null categories are computed from the pseudo-day one of each shuffle.

| Split | Category | Flips | Non-flips | Flip rate [CI] | Null flips / null pairs | Observed minus null |
| --- | --- | --- | --- | --- | --- | --- |
| Rank distance on day one | 1 | 23 | 132 | 0.148 [0.097, 0.208] | 21.5 / 160.7 | +1.5 |
| | 2 | 6 | 131 | 0.044 [0.014, 0.080] | 10.7 / 148.1 | -4.7 |
| | 3+ | 15 | 460 | 0.032 [0.004, 0.072] | 12.9 / 503.1 | +2.1 |
| Voting pulls showing the reverse order | 0 of 3 | 13 | 616 | 0.021 [0.007, 0.039] | 16.6 / 662.0 | -3.6 |
| | 1 of 3 | 28 | 107 | 0.207 [0.103, 0.331] | 25.2 / 144.6 | +2.8 |
| | 2+ of 3 (pull majority reverses the median) | 3 | 0 | 1.000 | 3.2 / 5.2 | -0.2 |
| Earlier days (Nov 6, 9, 11, 13 pulls) | mostly agree with day one | 14 | 565 | 0.024 [0.008, 0.044] | 17.8 / 609.7 | -3.8 |
| | mostly reversed | 19 | 103 | 0.156 [0.082, 0.248] | 17.8 / 129.8 | +1.2 |
| | tied or silent | 11 | 55 | 0.167 [0.036, 0.317] | 9.5 / 72.3 | +1.5 |
| Newcomer | either URL new to the top 10 on day one | 15 | 174 | 0.079 [0.023, 0.179] | 12.8 / 229.5 | +2.2 |
| | either new on Nov 13 (none on day one) | 14 | 175 | 0.074 [0.028, 0.141] | 11.6 / 206.9 | +2.4 |
| | both in the Nov 11 and Nov 13 top 10 | 15 | 374 | 0.039 [0.015, 0.072] | 20.6 / 375.5 | -5.6 |
| Position band on day one | both in top 3 | 6 | 58 | 0.094 [0.019, 0.177] | 3.6 / 65.8 | +2.4 |
| | both in 4-10 | 30 | 294 | 0.093 [0.046, 0.156] | 29.5 / 343.4 | +0.5 |
| | mixed | 8 | 371 | 0.021 [0.000, 0.057] | 11.9 / 402.7 | -3.9 |
| Site | same site | 1 | 20 | 0.048 [0.000, 0.100] | 0.6 / 22.2 | +0.4 |
| | different sites | 43 | 703 | 0.058 [0.029, 0.099] | 44.4 / 789.7 | -1.4 |

- No category shows flips well above the exchangeable null. The largest excess is +2.8 flips. Where flips happen is just where same-day noise is high: adjacent ranks, contested pairs, the 4 to 10 band, and pairs that earlier days ordered the other way.
- Reversion against departure: 19 flips go back to the order the earlier days mostly showed, 14 leave it, and 11 have no earlier majority.
- Day-two strength of the flips: 18 were shown by all three Nov 17 pulls, 23 by two, and 3 by one (median artifacts).
- With all four Nov 16 pulls: 0 of 4 reversed flips 9/577, 1 of 4 flips 21/154, and a 2-2 split flips 14/36 (0.389).
- Movers: 14 of the 44 flips are adjacent swaps. The flips come from 18 distinct URLs that did most of the moving (top five drove 6, 4, 4, 3 and 2 flips). Of the 223 URLs in both top 10s, 140 kept their position, 59 moved 1, 14 moved 2, 4 moved 3 and 6 moved 4 or more.
- Big jumps are flickering URLs, not clean moves. Of the 9 driver URLs that moved 3 or more places, 8 were already spread over 4 or more places across one day's pulls. Examples of (Nov 16 evening pulls) then (Nov 17 pulls): (11, 10, 1) then (1, 1, 1); (1, 1, 7) then (5, 8, 8); (10, 11, 4) then (3, 3, 4).
- Concentration: 18 of 30 searches have a flip, and the top 5 searches hold 28 of the 44. Per-search flips track the per-search noise expectation (Spearman 0.76, p < 0.0001). They do not track the same search's Nov 13 to 16 flip rate (Spearman 0.06, p = 0.77).

## Q3. Ceiling: the precision-budget oracle

The oracle ranks pairs by P(flip | day-one state, q) and calls them in that order while its expected precision stays at least 0.5.

(a) Distribution of q, the chance that a 3-pull consensus shows the pair's majority order. 'Pairs' is the share of scored pairs; 'flips' is the share of expected flips (weight 2q(1-q)).

| G | 0.5-0.6 | 0.6-0.7 | 0.7-0.8 | 0.8-0.9 | 0.9-0.95 | 0.95-0.99 | 0.99-1 | Flip-weighted mean q | Flip-weighted mean of 1/(2q) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| NPMLE, pairs | 0.000 | 0.000 | 0.001 | 0.072 | 0.167 | 0.166 | 0.593 | 0.913 | 0.549 |
| NPMLE, flips | 0.000 | 0.000 | 0.010 | 0.316 | 0.455 | 0.193 | 0.026 | | |
| Beta, pairs | 0.006 | 0.009 | 0.019 | 0.055 | 0.077 | 0.226 | 0.607 | 0.854 | 0.599 |
| Beta, flips | 0.052 | 0.081 | 0.134 | 0.261 | 0.205 | 0.218 | 0.049 | | |
| Logit-normal, pairs | 0.010 | 0.013 | 0.020 | 0.047 | 0.064 | 0.218 | 0.627 | 0.830 | 0.623 |
| Logit-normal, flips | 0.098 | 0.112 | 0.138 | 0.223 | 0.169 | 0.203 | 0.057 | | |

The three shapes fit equally well (log-lik -1319.81, -1319.96, -1320.09). They disagree on how much mass sits near q = 0.5, and that is what drives the bound. Sensitivity is therefore real: flips are mostly pairs with q of 0.8 to 0.95, and the share at q below 0.8 ranges from 1% to 35%.

(b) Cheating bound, oracle knows the true q (model, 3-pull consensus on both days):

| G | Recall at precision >= 0.5, no change | Recall with change at its 95% upper bound |
| --- | --- | --- |
| NPMLE | 0.596 | 0.536 (fresh), 0.533 (swap) |
| Beta | 0.691 | 0.629 (fresh), 0.640 (drift) |
| Logit-normal | 0.721 | 0.658 (fresh) |

The empirical version that estimates q from both days' six votes calls 37 pairs, 34 of them right. That is recall 34/44 = 0.773 [0.571, 0.921] at precision 0.919. It is inflated: the three day-two votes it reads are the same votes that decide the outcome. Its own expected recall is 0.55. Use the model numbers above as the cheating ceiling.

(c) Honest version, votes up to and including day one, scored on the real 44:

| Votes seen | Calls | Right | Realized recall [CI] | Realized precision [CI] | Oracle's expected recall |
| --- | --- | --- | --- | --- | --- |
| Nov 16 voting pulls (3) | 8 | 7 | 0.159 [0.000, 0.351] | 0.875 | 0.04 to 0.11 |
| All Nov 16 pulls (4, adds the morning) | 38 | 16 | 0.364 [0.200, 0.545] | 0.421 [0.222, 0.621] | 0.20 to 0.43 |
| All 8 pulls up to day one (adds Nov 6 to 13) | 102 | 24 | 0.545 [0.328, 0.794] | 0.235 [0.120, 0.367] | 0.48 to 0.68 |

The same rows hold under every prior tried (NPMLE fit on those votes, Beta, Jeffreys). With three votes the only calls are the 3 median artifacts and 5 exact ties (7 right). Adding the morning pull turns 36 pairs into 2-2 ties; those flip at 0.39, not the 0.5 the oracle expects, so realized precision falls to 0.42. Adding Nov 6 to 13 badly overcalls (precision 0.235), because those pulls are not stationary (see Q4). With 8 votes, the best realized point at precision of at least 0.5 is 5/6 calls (recall 0.114).

(d) More stationary history. Exact Bayes oracle under the fitted model, with k extra stationary pulls before day one plus day one's three:

| Extra pulls k (total) | NPMLE | Beta | Logit-normal | Beta, change at upper pi |
| --- | --- | --- | --- | --- |
| 0 (3) | 0.000 | 0.000 | 0.000 | 0.000 |
| 1 (4) | 0.421 | 0.414 | 0.411 | 0.380 |
| 3 (6) | 0.500 | 0.500 | 0.500 | 0.459 |
| 6 (9) | 0.546 | 0.579 | 0.591 | 0.529 |
| 9 (12) | 0.556 | 0.610 | 0.631 | 0.557 |
| 12 (15) | 0.559 | 0.625 | 0.649 | 0.569 |
| 15 (18) | 0.560 | 0.632 | 0.659 | 0.576 |
| 18 (21) | 0.562 | 0.639 | 0.667 | 0.582 |
| true q | 0.596 | 0.691 | 0.721 | 0.629 |

A Monte Carlo run of 400000 simulated pairs (Beta at upper pi, k = 6) gives recall 0.524 at precision 0.501, against the exact 0.529.

Gap decomposition. From day one's 3 pulls (recall 0) to the true-q bound (0.60 to 0.72), the whole gap is estimation noise in the stationary model. Real change at its 95% upper bound costs about 0.05 to 0.06 more at every depth. At the fitted value (no change) it costs nothing.

Recall at stricter expected precision (Beta G, stationary world):

| Precision floor | k=1 | k=6 | k=12 | k=18 | True q |
| --- | --- | --- | --- | --- | --- |
| 0.5 | 0.414 | 0.579 | 0.625 | 0.639 | 0.691 |
| 0.55 | 0.000 | 0.554 | 0.599 | 0.616 | 0.662 |
| 0.6 | 0.000 | 0.536 | 0.576 | 0.592 | 0.634 |
| 0.7 | 0.000 | 0.472 | 0.531 | 0.548 | 0.582 |

What is reachable with half the calls right? With only a day's three pulls, essentially nothing: 0 in the model and 7/44 on real data. Reaching about 0.58 needs roughly 9 stationary pulls of the same pairs (the day's 3 plus 6 more from the same period), and about 0.63 needs 15 to 21. That assumes the extra pulls are close enough in time to be stationary, which Nov 6 to 13 are not.

## Q4. More pulls or a longer horizon

Under the fitted model the Nov 16 to 17 flips are all noise. The noise flip rate per scored pair falls with pulls per day:

| Pulls per day | NPMLE | Beta | Logit-normal | Beta fresh at upper pi 0.0102: total / noise share | Bootstrap noise share, Beta fresh |
| --- | --- | --- | --- | --- | --- |
| 1 | 0.1200 | 0.1200 | 0.1200 | 0.1229 / 0.964 | |
| 3 | 0.0517 | 0.0521 | 0.0525 | 0.0554 / 0.913 | [0.937, 1.000] |
| 5 | 0.0268 | 0.0313 | 0.0331 | 0.0348 / 0.859 | [0.895, 1.000] |
| 7 | 0.0150 | 0.0220 | 0.0246 | 0.0257 / 0.807 | [0.847, 1.000] |
| 9 | 0.0088 | 0.0169 | 0.0200 | 0.0207 / 0.759 | [0.791, 1.000] |
| 15 | 0.0022 | 0.0101 | 0.0135 | 0.0142 / 0.646 | |
| infinite | 0 | 0 | 0 | 0.0051 / 0 | |

Even at the upper bound, real change at a one-day gap is 0.005 flips per scored pair (about 4 of 767). At 9 pulls per day, 76% of flips would still be noise.

Longer horizons do show real change. Single-pull disagreement on URL pairs present in both pulls; the floor is same-session pulls:

| Gap | Pull pairs | URL pairs | Disagreement [CI] | Minus same-session floor | Real-change share | Implied pi |
| --- | --- | --- | --- | --- | --- | --- |
| under 2 h (same session) | 180 | 4496 | 0.0701 [0.0498, 0.0928] | 0 | 0 | 0 |
| 8 h (Nov 16 morning vs evening) | 90 | 2264 | 0.0760 [0.0489, 0.1105] | +0.006 [-0.023, +0.039] | 0.08 [-0.43, 0.39] | 0.014 |
| 17 h (Nov 16 evening vs Nov 17) | 270 | 6219 | 0.0798 [0.0556, 0.1065] | +0.010 [+0.000, +0.020] | 0.12 [0.00, 0.23] | 0.023 |
| 25 h (Nov 16 morning vs Nov 17) | 90 | 2059 | 0.1010 [0.0673, 0.1407] | +0.031 [+0.004, +0.063] | 0.31 [0.05, 0.49] | 0.072 |
| about 2 days | 90 | 1996 | 0.1298 [0.0990, 0.1644] | +0.060 [+0.029, +0.095] | 0.46 [0.26, 0.62] | 0.139 |
| about 3 days | 120 | 2650 | 0.1317 [0.1002, 0.1665] | +0.062 [+0.031, +0.095] | 0.47 [0.28, 0.61] | 0.143 |
| 4-5 days | 270 | 5157 | 0.1540 [0.1289, 0.1816] | +0.084 [+0.060, +0.108] | 0.55 [0.42, 0.66] | 0.195 |
| 6-7 days | 240 | 3908 | 0.1804 [0.1452, 0.2221] | +0.110 [+0.077, +0.144] | 0.61 [0.49, 0.71] | 0.257 |
| 8-9 days | 120 | 1880 | 0.1915 [0.1454, 0.2474] | +0.121 [+0.079, +0.170] | 0.63 [0.50, 0.73] | 0.282 |
| 10-11 days | 180 | 2748 | 0.2034 [0.1599, 0.2491] | +0.133 [+0.091, +0.177] | 0.66 [0.52, 0.75] | 0.310 |

Consecutive single-pull days: Nov 6 to 9 is 0.109 (real-change share 0.36 [-0.01, 0.59]), Nov 9 to 11 is 0.137 (0.49 [0.21, 0.65]), Nov 11 to 13 is 0.144 (0.51 [0.28, 0.67]), and Nov 13 to Nov 16 morning is 0.130 (0.46 [0.19, 0.63]). These equal the consensus flip rates of those transitions (73/667, 99/724, 87/605), because one pull is the whole day.

Rough projection to consensus flips. This uses the Beta G with pi set to the variogram's implied value for each gap. The variogram's pairs are a broader set than scored pairs, so treat it as an order of magnitude. Each cell is flip rate / noise share of flips:

| Gap | 1 pull | 3 pulls | 5 pulls | 9 pulls | Real flips only |
| --- | --- | --- | --- | --- | --- |
| 17 h | 0.129 / 0.92 | 0.062 / 0.83 | 0.042 / 0.74 | 0.028 / 0.60 | 0.011 |
| 25 h | 0.147 / 0.79 | 0.084 / 0.60 | 0.065 / 0.47 | 0.052 / 0.32 | 0.036 |
| about 2 days | 0.173 / 0.65 | 0.114 / 0.43 | 0.096 / 0.31 | 0.084 / 0.19 | 0.069 |
| 4-5 days | 0.194 / 0.56 | 0.140 / 0.34 | 0.123 / 0.23 | 0.111 / 0.14 | 0.098 |
| 10-11 days | 0.238 / 0.44 | 0.191 / 0.24 | 0.177 / 0.15 | 0.167 / 0.09 | 0.155 |

Caveats: single pulls are noisier; the early days were pulled at different times of day; and the floor itself differs by session by about 0.02. Gaps under a day sit inside that session-to-session variation.

## Q5. Pre-specified leads

Nothing was tuned. Lift is the flip rate when flagged divided by the flip rate when not flagged.

| Lead | Flagged pairs | Flagged flips | Precision [CI] | Rate if not flagged | Lift [CI] | Recall [CI] |
| --- | --- | --- | --- | --- | --- | --- |
| (a) higher URL entered the top 10 on day one | 98 | 8 | 0.082 [0.012, 0.225] | 0.054 | 1.52 [0.23, 4.63] | 0.182 [0.024, 0.356] |
| (a') lower URL entered on day one (contrast) | 145 | 12 | 0.083 [0.027, 0.183] | 0.051 | 1.61 [0.53, 3.84] | 0.273 |
| (b) lower URL's site rose on other searches, Nov 13 to 16 | 272 | 12 | 0.044 [0.011, 0.095] | 0.065 | 0.68 [0.21, 1.43] | 0.273 |
| (b') higher URL's site fell on other searches, Nov 13 to 16 | 406 | 20 | 0.049 [0.025, 0.085] | 0.066 | 0.74 [0.40, 1.62] | 0.455 |
| diagnostic, reads day two: lower URL's site rose Nov 16 to 17 | 425 | 27 | 0.064 [0.031, 0.110] | 0.050 | 1.28 [0.46, 3.92] | 0.614 |
| (c) only the last day-one pull (evening 3) shows the reverse | 48 | 13 | 0.271 [0.068, 0.577] | 0.043 | 6.28 [1.84, 14.86] | 0.295 [0.088, 0.448] |
| control: only the morning pull reversed | 52 | 4 | 0.077 [0.000, 0.242] | 0.056 | 1.38 [0.00, 5.20] | 0.091 |
| control: only evening 1 reversed | 8 | 3 | 0.375 [0.000, 1.000] | 0.054 | 6.94 [0.00, 25.03] | 0.068 |
| control: only evening 2 reversed | 46 | 1 | 0.022 [0.000, 0.100] | 0.060 | 0.36 [0.00, 1.85] | 0.023 |
| (d) habitual: reverse order in 2+ of the 8 pulls up to day one | 222 | 38 | 0.171 [0.090, 0.269] | 0.011 | 15.55 [8.01, 51.85] | 0.864 [0.750, 0.960] |
| once: reverse order in exactly 1 of 8 | 149 | 2 | 0.013 [0.000, 0.035] | 0.068 | 0.20 [0.00, 0.50] | 0.045 |
| (d) novel: reverse order never seen in 8 | 396 | 4 | 0.010 [0.000, 0.022] | 0.108 | 0.09 [0.00, 0.23] | 0.091 |

- (a) Newcomer decay: no clear lift. A new lower URL has about the same lift as a new higher URL.
- (b) Site momentum: no lift, and slightly negative. Even the version that reads day two shows none (1.28 [0.46, 3.92]). Flips are not part of site-wide moves across searches.
- (c) Last pull: lift 6.28 [1.84, 14.86], but it is not about recency. When only evening 2 dissents, pairs flip 1/46 times, and evening 2 sits between evening 1 and evening 3 in time. Evening 3 minus evening 2 is +0.249 [+0.030, +0.562]. Homogeneity across the three evening pulls gives chi2 p = 0.0014 (pairs treated as independent). It looks like a property of which pull, which fits the SERP-version idea. Evening 2 is the pull least like evening 3 (0.085) and most like evening 1 (0.038).
- (d) Habitual against novel is the strongest lead: lift 15.6 [8.0, 51.9]. Only 4 of the 44 flips had a reverse order never seen in any of the 8 earlier pulls. But precision is 0.171, far from 0.5. It marks the volatile pairs, which is the same thing as the "contested" flag from round 1, not a direction predictor.

## What I would tell the theory step

- On a one-day gap with three pulls a day, the flips are noise. Theories about content changes, newcomers or site momentum have nothing in this transition to explain: the model allows at most about 2 real flips among the 44.
- Target 3's pass rule needs flip recall with a lower bound above 0.5 at precision of at least 0.5. Even an oracle that knows each pair's true order probability reaches only 0.60 to 0.72 recall at precision exactly 0.5. A history-only scorer needs many same-period pulls of day one (about 9 for 0.58, 15 to 21 for about 0.63) to get near that. Three pulls give nothing beyond median artifacts and ties.
- Real change shows up at 2 or more days (real-change share about 0.46 at 2 days, 0.66 at 10 to 11 days). A longer horizon, or more pulls per day, raises the share of flips that are real. At a 2-day gap with 9 pulls, about 19% of flips would be noise; at 1 day with 9 pulls, 60%.
- Same-session pulls are correlated, and the dissent pattern depends on which pull dissents. A model with a few SERP versions served at random may describe the noise better than independent per-pair coin flips. If so, more pulls spread over time, not back to back, would buy more information per credit.

## Limits

- One consecutive-day consensus transition (29 searches, 44 flips). Every interval is wide.
- The noise models treat pairs as independent given q. Pairs inside one search share pulls, so profile bounds are too narrow. The query bootstrap of the Beta model is the safer interval, and it agrees (real share of flips at most 0.06).
- The models are symmetric in the two days. The data show a borderline day asymmetry in noise level (73 against 50, p = 0.047).
- The model's consensus is the pull majority for a pair; the real rule is a median of positions. They differ in the 3 median-artifact pairs here.
- The variogram uses all URL pairs present in both pulls, a broader and more volatile set than scored pairs. The projection by gap is approximate.
