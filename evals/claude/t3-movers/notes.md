# Target 3 (pairwise order of movers): design notes

Design data only: SERP rows via `rsdata.load_rows("2026-11-17")`. No held-out day was read.
Every number below comes from `python -m scripts.claude.t3_design_eval`
(stdout saved in `design_eval_stdout.txt`, full JSON in `design_results.json`).
Every model output is PREDICTED. Truth is the real Serper consensus.

## Verdict

No scorer I found meets both parts of the planned pass rule on design data, and I do not expect
one to meet it on held-out days.

- Where a scorer is at least as good as persistence on all pairs, it catches about a quarter of
  the flips. The recommended scorer gets 10/44 = 0.227 [0.075, 0.393] on o30 16->17, with
  precision 10/14. The most optimistic learned frontier (query-CV on the same day, threshold
  picked after the fact) reaches 0.41 recall at precision 0.51. Even fitted in-sample, a 3-to-5
  feature model tops out at 0.43-0.45 recall with precision >= 0.5.
- To get flip accuracy above 0.5 you have to override yesterday on low-evidence pairs. Precision
  then falls to about 0.3, and all-pairs accuracy falls 3 to 6 points below persistence.
  Example: call a flip whenever more than a quarter of yesterday's pulls disagreed. That gets
  flip accuracy 0.773 [0.615, 0.909] but all-pairs -0.063 [-0.103, -0.025] versus persistence.
  This is the "diluted reversal" the brief rules out.

So the honest answer: flips are not predictable above 50% without giving up more on the
non-flipped pairs.

## The algebra that decides it

For a scorer that "calls a flip" on a set C of pairs:
all-pairs accuracy - persistence accuracy = (TP - FP) / N, where TP = flips called and FP = non-flips called.
So "all-pairs not below persistence" is exactly "precision of flip calls >= 0.5". The pass rule
therefore needs a set of pairs that holds more than half of all flips (with a CI) and is at least
half flips. On o30 16->17 the flip base rate is 44/767 = 5.7%. That means about 22 or more
flips inside a set of 44 or fewer pairs, picked from history alone.

Why that is hard. When day_a and day_b both use a 3-pull consensus and pull noise is stationary,
a pair whose per-pull reversal probability is q flips with probability 3q^2 - 2q^3. That is below
0.5 whenever q < 0.5, and day_a's own consensus is evidence that q < 0.5. Empirically, pairs where
1 of the 3 voting pulls on Nov 16 disagreed flipped 24/130 = 0.18 (model value for q = 1/3: 0.26).
Flips with P > 0.5 need either (a) day_a's consensus to contradict day_a's own pull majority (rare
median artifacts), or (b) a real change between days. History does not predict (b): trend
extrapolation and mean reversion have precision 0.13 to 0.39 on every transition (tables below). On day_b, 18 of
the 44 o30 flips were shown by all 3 pulls, 23 by 2 of 3, and 3 by 1 of 3 (median artifacts).
About 40% are clean, real-looking changes.

## Data (design transitions)

| transition | pairs | flips | flip rate | day_a pulls |
|---|---|---|---|---|
| o30 11-06 -> 11-09 | 667 | 73 | 0.109 | 1 |
| o30 11-09 -> 11-11 | 724 | 99 | 0.137 | 1 |
| o30 11-11 -> 11-13 | 605 | 87 | 0.144 | 1 |
| o30 11-13 -> 11-16 | 721 | 90 | 0.125 | 1 |
| o30 11-16 -> 11-17 | 767 | 44 | 0.057 | 4 (1 morning + 3 voting evening) |
| rev 11-16 -> 11-17 | 388 | 11 | 0.028 | 1 |

Only o30 16->17 looks like the held-out transitions (consecutive days, consensus on both ends).
rev has one pull per day and no earlier history, so every history-only scorer equals persistence on it.

## What I tried (every variant is in the stdout and JSON)

Columns: flip acc = recall on true flips; non-flip acc; all-pairs model vs persistence; paired difference;
precision of flip calls. All CIs are 95% query-bootstrap intervals from `metrics.py` (2000 draws, fixed seed).

### A. URL-level "persistence + bounded correction" scorers

`score = cons_a + lam * (target - cons_a)`. lam is the override knob. The full grid covers 5
half-lives x 4 lams for EW means, plus every target at lam 0.25, 0.5 and 1. Representative rows
on o30 16->17:

| scorer | flip acc | non-flip acc | all vs persist | all - persist | precision |
|---|---|---|---|---|---|
| persistence | 0/44 0.000 | 723/723 1.000 | 0.943 vs 0.943 | +0.000 | n/a |
| mean of day_a voting pulls, lam=1 | 5/44 0.114 [0.026,0.200] | 688/723 0.952 | 0.904 | -0.039 [-0.061,-0.019] | 5/40 0.125 |
| mean of all day_a pulls + deep, lam=1 | 7/44 0.159 [0.087,0.250] | 690/723 0.954 | 0.909 | -0.034 [-0.050,-0.018] | 7/40 0.175 |
| EW mean of all pulls h=2d, lam=1 | 11/44 0.250 [0.136,0.364] | 683/723 0.945 | 0.905 | -0.038 [-0.060,-0.016] | 11/51 0.216 |
| EW mean h=inf (all pulls equal), lam=0.5 | 7/44 0.159 [0.070,0.321] | 706/723 0.976 | 0.930 | -0.013 [-0.025,-0.001] | 7/24 0.292 |
| EW mean h=inf, lam=1 | 18/44 0.409 [0.269,0.571] | 655/723 0.906 | 0.877 | -0.065 [-0.098,-0.036] | 18/86 0.209 |
| mean reversion to days before day_a, lam=1 | 26/44 0.591 [0.457,0.750] | 587/723 0.812 | 0.799 | -0.143 [-0.190,-0.099] | 26/162 0.160 |
| trend (3-day line), lam=1 | 3/44 0.068 [0.000,0.156] | 710/723 0.982 | 0.930 | -0.013 [-0.024,-0.003] | 3/16 0.188 |
| Nov 16 deep-list position, lam=1 | 18/44 0.409 [0.238,0.612] | 662/723 0.916 | 0.887 | -0.056 [-0.094,-0.018] | 18/79 0.228 |
| same URL on sibling queries, lam=1 | 23/44 0.523 [0.327,0.741] | 571/723 0.790 | 0.774 | -0.168 [-0.232,-0.107] | 23/175 0.131 |

On the four older o30 transitions pooled (6->16; single-pull day_a, 2-3 day gaps, 349 flips),
the best precision of any lam > 0 variant was 0.39 (EW h=4, lam=0.5: 27/69). Recall there was
0.077 and all-pairs -0.006 [-0.013, +0.001]. Every mean, trend, deep or cross-query scorer
loses to persistence on all pairs once it calls a non-trivial number of flips.

### B. Learned pairwise P(flip) models

Pair features, history only (`t3_movers.pair_features`): 1/gap, top position, share of day_a
voting pulls and of all day_a pulls with y above x, share of earlier pulls with y above x, last
earlier day's order, and differences of vote mean, EW mean, earlier mean, deep position and
sibling-query mean. Models: four logistic regressions (3 to 11 features) and a depth-2 LightGBM.

- Forward chaining (train on o30 transitions that end on or before the test day_a). AUC is
  0.70-0.83, but at P > 0.5 the models call almost nothing on 16->17. For example, LR-all calls
  2/4 flips correctly (recall 0.045). The models were trained where the flip rate is 11-14%
  (single pulls) and tested where it is 5.7%, and the within-day features do not vary in
  training. Post-hoc best recall at precision >= 0.5 on 16->17 is 0.000-0.136.
- Query-grouped 5-fold CV inside o30 16->17. This is not forward in time, but it is the only
  place the within-day features vary. Over 5 fold seeds, at P > 0.5:
  LR-day (1/gap, rev_vote, rev_a) recall 0.14-0.27, precision 0.55-0.71, all-pairs +0.001 to +0.009.
  LR-day+hist recall 0.18-0.27, precision 0.53-0.71. LR-all recall 0.23-0.34, precision 0.54-0.67.
  GBM recall 0.14-0.21, precision 0.47-0.69.
  Post-hoc best recall at precision >= 0.5 (threshold picked on the test fold, so optimistic):
  0.27-0.41 (LR-day), 0.30-0.41 (LR-day+hist), 0.25-0.46 (LR-all), 0.21-0.27 (GBM).
  Adding the older transitions to training made all of these worse (miscalibration).
- In-sample on o30 16->17 (fit and score on the same 767 pairs; an optimistic ceiling, not a result).
  Best recall at precision >= 0.5: LR-day 0.43, LR-day+hist 0.45, LR-all 0.64 (11 features),
  GBM 0.82 (AUC 0.977, clearly memorized). Even a 3-to-5-feature model fitted to the test pairs
  themselves cannot put half the flips in a set that is at least half flips.

Out-of-fold trade-off curve of LR-day+hist (query CV on o30 16->17, fold seed 0). Thresholds are
swept after the fact, so this is the most optimistic learned frontier, not a forecast:

| P(flip) threshold | flip acc | precision | all - persist |
|---|---|---|---|
| 0.6 | 9/44 0.205 | 9/10 0.900 | +0.010 [+0.003,+0.022] |
| 0.5 | 11/44 0.250 | 11/17 0.647 | +0.007 [-0.005,+0.022] |
| 0.4 | 15/44 0.341 | 15/29 0.517 | +0.001 [-0.013,+0.019] |
| 0.3 | 18/44 0.409 | 18/35 0.514 | +0.001 [-0.016,+0.019] |
| 0.25 | 19/44 0.432 | 19/47 0.404 | -0.012 [-0.035,+0.012] |
| 0.2 | 23/44 0.523 | 23/63 0.365 | -0.022 [-0.056,+0.005] |
| 0.15 | 30/44 0.682 | 30/89 0.337 | -0.038 [-0.074,-0.004] |

The best learned point with precision >= 0.5 is recall 0.41. Recall first exceeds 0.5 at precision 0.37.

### C. Pairwise pull majority (Kemeny), the recommended family

For each query, take day_a's consensus top 10 and, for every pair, the share of day_a's top-10
pulls (all of them, voting or not) that put i above j. Earlier days' pulls get a small weight.
The order is the exact Kemeny order of those shares, with day_a's consensus as tie-break. So the
scorer overrides yesterday only where a strict weighted majority of the pulls disagrees with the
median consensus. The result is a URL-level score (rank in the Kemeny order), as `score_urls`
requires.

Trade-off as the weight w of earlier days grows (half-life 2 days), o30 16->17:

| w | flip acc | non-flip acc | all vs persist | all - persist | precision |
|---|---|---|---|---|---|
| 0 (day_a pulls only) | 4/44 0.091 [0.000,0.205] | 721/723 0.997 | 0.945 vs 0.943 | +0.003 [+0.000,+0.007] | 4/6 0.667 |
| 0.001 (below the 1e-3 consensus tie-break, so the same as 0) | 4/44 0.091 [0.000,0.205] | 721/723 0.997 | 0.945 vs 0.943 | +0.003 [+0.000,+0.007] | 4/6 0.667 |
| 0.1, 0.25, 0.5, 1 (earlier days break exact 2-2 ties only) | 10/44 0.227 [0.075,0.393] | 719/723 0.994 | 0.950 vs 0.943 | +0.008 [+0.001,+0.016] | 10/14 0.714 |
| 2 | 11/44 0.250 [0.075,0.442] | 720/723 0.996 | 0.953 | +0.010 [+0.001,+0.025] | 11/14 0.786 |
| 4 | 15/44 0.341 [0.179,0.531] | 698/723 0.965 | 0.930 | -0.013 [-0.033,+0.007] | 15/40 0.375 |
| 8 | 18/44 0.409 [0.225,0.590] | 669/723 0.925 | 0.896 | -0.047 [-0.079,-0.014] | 18/72 0.250 |

On the older single-pull transitions (6->16 pooled), w <= 1 equals persistence, because one pull
is the whole day. w = 2 calls 151 flips at precision 0.278 (-0.025 all-pairs), w = 4 calls 304 at
0.293 (-0.046), and w = 8 calls 343 at 0.306 (-0.049).

Pair-level confidence curve (call a flip when the weighted share of pulls with y above x exceeds
theta, frozen weights), o30 16->17:

| theta | flip acc | non-flip acc | all - persist | precision |
|---|---|---|---|---|
| 0.6 | 3/44 0.068 [0.000,0.147] | 723/723 1.000 | +0.004 [+0.000,+0.011] | 3/3 |
| 0.5 | 10/44 0.227 [0.075,0.393] | 721/723 0.997 | +0.010 [+0.001,+0.025] | 10/12 0.833 |
| 0.45 / 0.4 | 16/44 0.364 [0.212,0.542] | 701/723 0.970 | -0.008 [-0.032,+0.010] | 16/38 0.421 |
| 0.35 / 0.3 | 19/44 0.432 [0.242,0.646] | 693/723 0.959 | -0.014 [-0.045,+0.010] | 19/49 0.388 |
| 0.25 | 34/44 0.773 [0.615,0.909] | 641/723 0.887 | -0.063 [-0.103,-0.025] | 34/116 0.293 |
| 0.2 | 35/44 0.795 [0.629,0.917] | 557/723 0.770 | -0.171 [-0.228,-0.112] | 35/201 0.174 |
| 0.0 | 42/44 0.955 [0.879,1.000] | 243/723 0.336 | -0.571 [-0.677,-0.457] | 42/522 0.080 |

Precision drops below 0.5 between recall 0.23 and 0.36. The first theta whose flip-accuracy CI
lower bound clears 0.5 (theta = 0.25: 1 of 4 pulls disagreed) costs 6.3 points of all-pairs
accuracy.

## Recommended frozen spec

`scripts/claude/t3_movers.py` with `evals/claude/t3-movers/params.json`
(`target = kemeny, prev_weight = 0.1, prev_half_life = 2, prev_cap = 0.9, margin = 0, deep_as_pull = false`).

- For each query, day_a is the last pulled day before day_b. Take day_a's consensus top 10.
- Re-order it by the exact Kemeny order of pairwise pull majorities over every top-10 pull on day_a.
- Earlier days only break exact day_a ties. A day_a pull moves a pair's weighted margin by 0.5 or 0,
  an earlier pull by at most 0.5 times its weight, and `prev_cap` keeps the earlier total under 1.
  So a strict day_a majority always wins. Without the cap, the total earlier weight would be about
  0.33 (17->18), 0.44 (18->19) and 0.53 (19->20), so the cap should not trigger there either.
- score = position in that order (lower = better). Keys outside day_a's top 10 get cons_a + 0.5;
  unseen keys get 12.
- No parameter was fitted by optimization. How the weight was settled, in order:
  - I first tried 0.5. On design data it is identical to every w from 0.1 to 1.
  - On held-out 17->18, a weight of 0.5 with no cap would let Nov 16's four pulls (total 1.41)
    override a 2-1 split on Nov 17. That regime was never tested here, and the w >= 4 rows show it
    has low precision.
  - I then tried 0.001. It does NOT act as a tie-break, because its margin is smaller than the
    1e-3 consensus tie-break inside the Kemeny objective. It falls back to persistence on ties
    (4/44, the w = 0.001 row).
  - 0.1 plus the cap is the smallest tested weight that behaves as an exact tie-break.

Design numbers through the public `score_urls` API (one call per query and day_b):

| transition | flip acc | non-flip acc | all vs persist | all - persist | precision |
|---|---|---|---|---|---|
| o30 16->17 | 10/44 0.227 [0.075,0.393] | 719/723 0.994 [0.985,1.000] | 0.950 vs 0.943 | +0.008 [+0.001,+0.016] | 10/14 0.714 |
| rev 16->17 | 0/11 | 377/377 | 0.972 vs 0.972 | +0.000 | n/a (no calls) |
| pooled consecutive | 10/55 0.182 [0.054,0.321] | 1096/1100 0.996 | 0.958 vs 0.952 | +0.005 [+0.001,+0.011] | 10/14 0.714 |
| o30 6->16 pooled | 0/349 | 2368/2368 | 0.872 vs 0.872 | +0.000 | n/a (single-pull day_a) |

It is the most accurate scorer I found on all pairs, and its precision stays above 0.5. It will
almost surely fail the flip-accuracy half of the rule.

## Held-out expectation

- 17->18: day_a (Nov 17) has 3 pulls in one 75-minute session and no morning pull. 2-2 splits
  cannot happen, so the scorer fires only where the 3-pull majority contradicts the median
  consensus, or on exact ties from both-absent pulls. The time-reversed check (Nov 17's pulls
  against Nov 16's order, a diagnostic and not a forecast) fired on only 2 pairs, 0 of them
  correct. Expect very few calls and flip accuracy around 0 to 0.15.
- 18->19: day_a (Nov 18) may have only 2 pulls for some queries (pull-c incomplete). That makes
  1-1 splits that Nov 17 breaks, which is the same mechanism that gave 10/14 here. Expect flip
  accuracy around 0.1 to 0.3.
- 19->20 with a70: history is Nov 16 (one pull) and Nov 19. Same mechanism, thin tie-break.
- Chance of clearing "flip accuracy CI lower bound > 0.5": essentially zero. With the
  query-bootstrap design effect measured here (1.65), P(clear) is about 4e-8 at the design recall
  of 0.227 and n = 45 flips. Even at the design upper bound of 0.39, it is about 0.001 at n = 45
  and 0.00003 at n = 150.
- Chance that all-pairs is not below persistence: good but not certain. Design precision was
  10/14 from few queries, and with very few calls the difference will be close to 0.
- Flips needed for the CI lower bound to clear 0.5 if the true flip accuracy were r (deff 1.65):
  r = 0.55: about 627; r = 0.6: about 152; r = 0.65: about 64; r = 0.7: about 33. At the design
  estimate (0.227) no number of flips is enough, because the rate is below 0.5.

## Diagnostics

- Within-day evidence on o30 16->17 (share of Nov 16 voting pulls with y above x). At 0: 9/507
  flipped. At 1/6: 4/122. At 1/3: 24/130. At 1/2 (ties): 4/5. At 2/3: 3/3. Over all 4 Nov 16
  pulls, 2-2 splits flipped 13/35.
- Earlier-day order: pairs reversed on Nov 13 flipped 20/111. Pairs agreeing flipped 19/602.
- Time-reversed check (o30): the strict majority of Nov 17's pulls against Nov 17's consensus
  "predicts" Nov 16's order on 0/2 pairs. So the pure within-day rule is thin. Most of the 10/14
  comes from 2-2 splits of a 4-pull day broken by Nov 13.
- Concentration: the 14 flip calls of the recommended scorer on o30 16->17 fall in 6 of the 29
  queries (`design_results.json`, frozen -> precision -> queries). The +0.008 all-pairs gain rests
  on a handful of queries.
- Selection caveat: the Kemeny family (7 variants) and the tie-break weight were compared on o30
  16->17 itself, the only design transition where they differ. The 10/14 is therefore an in-sample
  pick among a handful of nearly parameter-free rules, not an out-of-sample estimate.

## Leakage checks

- Rows come only from `rsdata.load_rows(rsdata.DESIGN_MAX_DATE)`. The SQLite DB and snapshot
  folders are never opened directly. No Serper calls.
- `HistoryContext` and `score_urls` assert that the history's max day is earlier than day_b. The
  eval confirms that passing history that contains day_b raises `AssertionError`.
- For each prediction day the eval builds the context from `rows[day < day_b]`. It also asserts
  that the scorer's own day_a equals the transition's day_a, and that the scorer's day_a consensus
  equals the truth table's a_x and a_y on every pair.
- The evaluator picks the requested keys from day_b (pairs present on both days). A key's score
  is computed over day_a's consensus top 10 and does not depend on which keys are requested. The
  eval checks this by scoring keys one at a time against the full request (3 keys per query and
  transition).
- Query groups for the sibling-query feature come from `rsdata.query_groups` on history rows only.
- Learned models: forward chaining trains only on transitions ending on or before the test day_a.
  Query CV is same-day and labeled as not forward. In-sample is labeled as a ceiling. None of
  them is the recommended spec.

## Possible issues in rsdata / metrics (not edited)

- `metrics.bootstrap_rate` on a subset (flipped pairs, or called pairs for precision) resamples
  only the queries in that subset. With few clusters the percentile interval is anti-conservative.
  For example, precision 10/14 (14 calls in 6 queries) gets [0.600, 1.000], and 4/6 also gets
  [0.600, 1.000]. The bootstrap only resamples the few queries that hold calls. Treat the precision
  CIs as unreliable. A rule for the frozen test could require a minimum number of query clusters. The all-pairs difference CIs
  use all queries and are fine.
- `rsdata.query_groups` labels 31 queries as o30. One, "all-in-one heavy civil software", appears
  only on Nov 6. This is harmless for Target 3.
- No correctness bug found in `flip_pairs`, `consensus_table` or `consensus_session`. On Nov 16
  the session rule picks the 3 evening pulls, and the morning pull stays available as non-voting
  history, which this scorer uses.

## Reproduce

```
python -m scripts.claude.t3_design_eval            # full run, writes design_results.json
python -m scripts.claude.t3_design_eval --skip-models
```

sha256 at the time of the final design run:

```
e4134cba6ad7836dfbfc2f07f941e5cd896ff714122f4a32cf36ea50a4b35bfe  scripts/claude/t3_movers.py
06042ba3178e983efc4ab541b36695948b5e377c125dd3f8a01cbc6bb8733c9c  scripts/claude/t3_design_eval.py
cca97592bad0d343aa92d2aaf1635a39cb709eddcbcf931f460cc7296f60de9d  evals/claude/t3-movers/params.json
```
