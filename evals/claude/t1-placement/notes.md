# T1 new-URL placement: design notes (PREDICTED)

Design data only: snapshot days <= 2026-11-17, loaded only through `rsdata.load_rows(rsdata.DESIGN_MAX_DATE)`. Nothing dated Nov 18 or later was loaded. No Serper calls were made.

Metric choice: band3 is primary and err3 is secondary (lead decision, 2026-11-19). The spec below is chosen for band3.

## Final spec (`params.json`)

Each target row is predicted on its own, from history strictly before the outcome day plus the row's query and URL.

- **new**: if the URL's site is in the query's consensus top 10 on the previous pulled day, predict its best position there (`dp`). Otherwise, if the site is in the query's latest deep list, predict its deep position. Otherwise predict 9. Clip to [2, 9].
- **reentrant**: predict the URL's own position in the query's latest deep list (if <= 30). Otherwise predict its last consensus top-10 position on this query. Otherwise predict 7. Clip to [2, 9].
- **survivor** (not scored): previous consensus position, clip [1, 10].

Why clip to [2, 9]: scored rows always have truth 1-10, so prediction 1 has the same band3 window as 2, and 10 or 11 are dominated by 9.

The only fitted number is the default 9. It is the best band3 constant on every prefix of design days (Nov 9; Nov 9-11; Nov 9-13; Nov 9-16). There are no model weights.

The reentrant clip was [3, 7] in an earlier draft, chosen for both metrics. I changed it to [2, 9] after the band3-primary decision. Both windows had already been scored on every slice (`reclip37`, `reclip29` in `design_eval.md`). This change was not a new experiment.

## Design numbers for the frozen spec (band3)

Source: `design_eval.md`, section "Frozen spec", produced by `t1_placement.predict_rows` with features recomputed from history. The diff is model minus the best of {domain prior, 7, 11} on that slice, with a query-bootstrap 95% CI. "c9" is the reference constant 9.

| slice | class | n | model band3 | best baseline | diff vs best | c9 | model minus c9 | err3 (secondary) model vs c7 |
|---|---|---|---|---|---|---|---|---|
| Nov 9 o30 | new | 97 | 41 (0.423) | c7 32 | +0.093 [-0.022, +0.240] | 37 | +0.041 [-0.043, +0.141] | 68 vs 81: -0.134 [-0.204, -0.062] |
| Nov 11 o30 | new | 65 | 39 (0.600) | dp 26 | +0.200 [+0.120, +0.277] | 32 | +0.108 [+0.017, +0.200] | 50 vs 53: -0.046 [-0.127, +0.045] |
| Nov 13 o30 | new | 68 | 31 (0.456) | c7 23 | +0.118 [-0.036, +0.280] | 27 | +0.059 [-0.019, +0.132] | 51 vs 53: -0.029 [-0.119, +0.078] |
| Nov 16 o30 | new | 30 | 13 (0.433) | dp 8 | +0.167 [+0.053, +0.310] | 17 | **-0.133** [-0.375, +0.103] | 18 vs 25: -0.233 [-0.452, +0.000] |
| Nov 17 o30 (strict new) | new | 5 | 5 (1.000) | dp 3 | +0.400 [+0.000, +0.800] | 2 | +0.600 [+0.200, +1.000] | 5 vs 5: 0 |
| Nov 17 rev | new | 38 | 19 (0.500) | c7 15 | +0.105 [-0.060, +0.379] | 16 | +0.079 [+0.000, +0.227] | 28 vs 31: -0.079 [-0.205, +0.115] |
| a70 emulation (strict new) | new | 19 | 16 (0.842) | dp 9 | +0.368 [+0.176, +0.571] | 10 | +0.316 [+0.062, +0.545] | 17 vs 15: +0.105 [-0.105, +0.263] |
| pooled o30 Nov 16 + 17 | new | 35 | 18 (0.514) | dp 11 | +0.200 [+0.057, +0.361] | 19 | -0.029 [-0.257, +0.175] | 23 vs 30: -0.200 [-0.389, +0.000] |
| pooled all slices | new | 303 | 148 (0.488) | c7 103 | +0.149 [+0.075, +0.232] | 131 | +0.056 [+0.009, +0.104] | 220 vs 248: -0.092 [-0.136, -0.043] |
| Nov 11 o30 | reentrant | 23 | 11 | c7 7 | +0.174 [-0.077, +0.367] | 6 | +0.217 [-0.042, +0.433] | 16 vs 15: +0.043 |
| Nov 13 o30 | reentrant | 36 | 22 | dp 11 | +0.306 [+0.029, +0.525] | 17 | +0.139 [-0.162, +0.368] | 30 vs 28: +0.056 |
| Nov 16 o30 | reentrant | 32 | 19 | dp 10 | +0.281 [-0.056, +0.500] | 13 | +0.188 [-0.167, +0.455] | 26 vs 24: +0.062 |
| Nov 17 o30 | reentrant | 41 | 29 | c7 14 | +0.366 [+0.162, +0.571] | 22 | +0.171 [+0.000, +0.343] | 37 vs 34: +0.073 |
| a70 emulation | reentrant | 33 | 24 | c7 10 | +0.424 [+0.148, +0.605] | 13 | +0.333 [+0.000, +0.526] | 30 vs 27: +0.091 |
| pooled all slices | reentrant | 132 | 81 (0.614) | dp 39 | +0.318 [+0.186, +0.447] | 58 | +0.174 [+0.000, +0.331] | 109 vs 101: +0.061 [-0.065, +0.190] |

Against the brief's three baselines, the spec wins band3 on every slice. The interval excludes 0 on Nov 11, Nov 16, the a70 emulation, both o30-consensus and all-slice pools, and (for reentrant) Nov 13, Nov 17 and the emulation.

Against constant 9, the spec beats it only modestly. Pooled new is +0.056 [+0.009, +0.104]. On the a70 emulation it is +0.316 [+0.062, +0.545]. It **loses to constant 9 on the Nov 16 consensus day** (-0.133) and ties on the pooled o30 consensus days (-0.029). Most of the gain over the brief's baselines comes from predicting 9 instead of 7 or 11. The site anchors add a little on top of that, mostly where a deep list exists.

Err3 (secondary): for new rows the spec is significantly worse than constant 7 (pooled -0.092). This is structural, not a fitting problem. With truth in 1-10, prediction 7 has a wider err3 window than 8, 9 or 10, and prediction 4 has a wider one than 1, 2 or 3. So err3 can only be gained by moving rows into 4-6 that will land in the top 3. Even an in-sample, test-fitted 27-cell optimum gains only +0.07 to +0.10 per day (`bound.txt`).

## a70 emulation

Setup: o30 queries, outcome Nov 17 3-pull consensus. History is only the `smartcrew-q100:2026-11-16` pull plus the Nov 16 deep list. This was checked in code: history sources = those two, history days = Nov 16.

Results:
- new (n=19): band3 16/19 against dp 9/19 (+0.368 [+0.176, +0.571]) and against c9 10/19.
- reentrant (n=33): band3 24/33 against c7 10/33.

The emulated gap is 1 day, while a70 on Nov 19 has a 3-day gap. The anchors will be staler on Nov 19, so expect a smaller gain.

## Strict new on design data

Strict new means class `new` on a query that has a deep list in history. On design data that is only o30 Nov 17 (5 rows) and the emulation (19 rows; the same outcome day with a70-style history). Both favour the spec, but n is tiny.

## Forward-chained and other honest checks

- **Automatic forward-chained selector** (`design_eval.md`, "Forward-chained selection"): 738 new-row specs and 234 reentrant specs, chosen on earlier days with a both-metric objective. It did not produce a stable or passing rule for new: the chosen spec changed every day and no out-of-sample day passed both metrics. It was optimizing both metrics, so it does not test the band3-primary spec.
- **The frozen spec's only fitted value (default 9) is forward-chained by construction**: it is the best band3 constant on each prefix. The rule family itself (site anchor, deep fallback, clip) was chosen after looking at all design slices, including Nov 17 and the emulation. Treat the design numbers as optimistic.
- **Best constant fitted on earlier days** with the both-metric objective is 7 on every day, which equals the c7 baseline. Constant 9 is the band3-only fitted constant; its numbers are shown above.

## Tried and not chosen (all in `design_eval.md`; none hidden)

- Constants 5-9.
- Domain prior clipped with default 7, 8 or 9.
- dp+1.
- Cross-query URL prior (`xq_key_min`) before or after dp, with offsets 0 or 1.
- Median of dp / xq URL / xq site.
- UGC-host shift of -2.
- Deep-site fallback.
- err3-safe windows [4,7] and [2,7].
- "dp <= 3 -> 4".
- Reentrant windows [2,9], [2,8], [3,8], [2,7], [3,7], [4,7] (plus a forward-chained window choice).
- A site-strength feature (number of sibling queries where the site is top 3): it separated top-3 entrants on the single-pull days but not on the consensus days (`explore.txt` and its script).

The cross-query URL prior did not improve band3 over the dp-only rule (pooled 141 vs 147 hits for new).

## Power (`power.md`)

These figures assume the held-out effect equals the design effect. "Rows" means scored rows per day.

- **new band3**:
  - Pool of all slices: +0.149, about 200 rows for 80% power. Simulated P(pass) is 0.40 at 30 rows, 0.55 at 60, 0.76 at 120.
  - Consensus-like pool (o30 Nov 16 plus the emulation): +0.245 against dp, about 25 rows for 80% power. P(pass) is 0.82 at 30 rows and 0.98 at 60.
  - The truth is probably in between.
  - Nov 18 o30 will likely have only about 5 strict-new rows (Nov 17 had 5), so Nov 18 alone cannot pass. Nov 19 and 20 with a70 need about 30 or more strict-new rows a day.
  - If the bar were "beats constant 9" instead, the pooled gain is only +0.056 and would need several hundred rows.
- **reentrant band3**: +0.318 pooled, about 35-40 rows for 80% power. P(pass) is 0.53-0.56 at 30 rows and about 0.88 at 60.
- **err3 (secondary)**: P(pass) about 0 for new under this spec. Under the err3-safe variants it is 0.03-0.16 at 120 rows.

## Leakage checks (all run in `t1_design_eval.py`; results in the `Checks:` line of `design_eval.md`)

- **Sealed loader**: SERP rows come only from `rsdata.load_rows("2026-11-17")`, and the max day is asserted to be 2026-11-17. No t1 script opens SQLite, reads snapshot folders, or calls Serper (checked by grep).
- **predict_rows asserts history < target day.** A negative test passes history that includes the target day and confirms the assertion fires (`seal_assert_fires: true`).
- **Row independence**: predicting each row alone, in reverse order, gives identical outputs (`row_independent: true`). No feature reads another target row, the outcome SERP, its titles or snippets, or co-listed URLs.
- **Features recomputed from history only** (`equal_to_table_path: true`).
- **Feature layer matches the label layer**: the class and domain prior recomputed from history match `rsdata.placement_rows` on every design row (asserted).
- **Nov 16 targets carry no deep-list feature**, because the deep list was pulled that same day (`nov16_no_deep_features: true`). The Nov 16 morning pull is also excluded, because history is strictly before the outcome day.
- **Design-level facts used, not outcome rows**:
  - The clip [2, 9] uses the evaluation design: scored rows are consensus top 10, so truth is 1-10. Baselines are conditioned the same way.
  - Class membership (new / reentrant) is computed from history, but which rows are scored is decided by the outcome. That is inherent to the task.
- **Researcher degrees of freedom**: the rule family was chosen after seeing all design slices; see above.

## Bugs in rsdata / metrics

None found.

One note on `placement_rows`: on Nov 16 the same-day deep list counts as "seen" only from Nov 16 onward, via `first >= outcome_day`. That is the correct behaviour.

## sha256 (at the time of writing)

```
8addfe663041494af601f8c6c0833366380f6749e1745495250b3f07becb5db6  scripts/claude/t1_placement.py
451102c516ea17f32cf0fe35c3da7f593f176754b7e39f8aaa854ff85b252886  evals/claude/t1-placement/params.json
b5852b6b23f6e9aa2aaa51bf77068b09a75d638712aeab4880919a0e3851a958  scripts/claude/rsdata.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  scripts/claude/__init__.py
f7dccd6ac816f7e4ee7fe0da68a8f8f009b9ebf4a1b54df17323e27e3d5407c0  src/aeo/ranksim/__init__.py
4d241399b9a14cc15e79f374caa1338a13bd6e3c8b55d49724421dfd18f0ef59  src/aeo/ranksim/consensus.py
9b0d36ceff1042e57203671b589137f25f0ce8015ad22bfbefb978b0826d14df  src/aeo/ranksim/site_history.py
77eed6fe4a3041e3d33b4716c5a93242a313ed1de3c9dfe18f2343bc52634ba5  src/aeo/__init__.py
f4710241737af8291224ac6cc98173b1aac674bbdb93112580eda3885cd5170e  src/aeo/crawl/__init__.py
0d715e4b2b88bf82f4518e296265b51bfb7478c2ce16c9beb37b999615bf42d0  src/aeo/crawl/urls.py
7f78a78e1118f8889fefd7fcbc980da990907dbae11284fa263bdbfc7ba2d14f  src/aeo/errors.py
81673f3e4d0f026a7d064cd401276a8b5843dabe142911a1db3df692cf44380d  scripts/claude/metrics.py   (scoring, not imported by t1_placement)
```

## Reproduce

```
python scripts/claude/t1_design_eval.py   # design_eval.md / .json (about 35 s)
python scripts/claude/t1_power.py         # power.md / .json (about 70 s)
python scripts/claude/t1_bound.py > evals/claude/t1-placement/bound.txt
python scripts/claude/t1_explore.py > evals/claude/t1-placement/explore.txt
```
