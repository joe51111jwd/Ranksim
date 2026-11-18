# Pre-registration: hybrid new-URL placement for 2026-11-19

Label: PREDICTED. This file does not change the Nov 18 gates in `evals/preregistered/band-2026-11-18/` or `evals/preregistered/tight-band-2026-11-18/`, and it does not change the Nov 17 consensus gate.

Nothing here is a deploy. The product ranker is unchanged.

## What is frozen

`hybrid.json` sha256: `f60724809e8b331ef546766f88961105c5311c6c19c6239f09423248a5f63c18`

Model for the gate: `evals/preregistered/forward-pair-2026-11-16/model-next.json`

sha256: `bc4de5022a44c91bdb481c46f28c5374ee5477d1990d55931a21a4ce0e2a8ea7`

It was fit on single-pull labels through Nov 16. It has not seen Nov 17 or Nov 19.

A URL seen yesterday keeps yesterday's consensus position. Its band is that position plus or minus the minimum of 2 and the rounded mean absolute move of that URL across earlier days. The cap of 2 was the coverage winner among caps 0, 1, and 2 on seen URLs through Nov 16.

A new URL, and a URL whose crawled text changed on both days, is inserted into that order. Its slot is the number of surviving URLs the model ranks above it. Other URLs shift down. Ties among new URLs break by model score, then URL key.

A new-URL row is a URL absent yesterday whose consensus position is in the top 10. A one-pull flicker whose median is 11 is not a placement.

The median new-URL position, frozen from those rows through Nov 16, is 7.

## Gate

On the 2026-11-19 pulls, after this file is committed, score new-URL rows only. The gate passes only when all of these are true:

1. `hybrid.json` and `model-next.json` match the hashes above.
2. On 3-wide coverage, the hybrid's rate is higher than the best of these three baselines: every new URL at position 7, every new URL at 11, and the domain's best position on that query yesterday (11 if the domain is new to the query).
3. The query-bootstrap 95% interval for the hybrid minus that best baseline has a lower bound above 0. Seed 20261116, 1000 draws.
4. Every new-URL row is scored. A missing HTML file is not a reason to drop one.
5. The label is PREDICTED.

Exact position, top-3 entry, and observed-set exact are reported. They are not the pass line. 3-wide means the truth falls in the three positions starting at max(1, prediction − 1).

Do not refit on the 2026-11-19 pulls and then quote the refit as this gate.

## Selection, through Nov 16

Nov 11 uses a model fit on Nov 9. Nov 13 uses `model-heldout-nov13`. Nov 16 uses `model-heldout-nov16`, which has not seen Nov 16 labels. The Nov 16 baseline row uses median 7, the same value the later days freeze.

| Day | Hybrid 3-wide | Best baseline | Difference |
| --- | --- | --- | --- |
| Nov 11 | 46/88 (0.523) [0.396, 0.667] | Domain prior 32/88 (0.364) [0.248, 0.512] | 0.159 [0.000, 0.329] |
| Nov 13 | 46/104 (0.442) [0.328, 0.554] | Median position 32/104 (0.308) [0.235, 0.380] | 0.135 [0.018, 0.256] |
| Nov 16 | 25/62 (0.403) [0.309, 0.481] | Domain prior 18/62 (0.290) [0.185, 0.422] | 0.113 [-0.071, 0.259] |

Nov 11's lower bound is 0, not above 0. Nov 16's lower bound is below 0. Those days are not the gate. The full table is `evals/experiments-2026-11-16/hybrid.md`.

## Nov 17 diagnostic, not used to choose

22 new URLs with a top-10 consensus. Model is `model-next`.

| System | Exact | 3-wide | Top-3 entry | Observed-set exact |
| --- | --- | --- | --- | --- |
| Hybrid | 6/22 (0.273) [0.087, 0.478] | 6/22 (0.273) [0.087, 0.478] | 2/3 (0.667) [0.000, 1.000] | 7/22 (0.318) [0.120, 0.550] |
| Median position 7 | 0/22 (0.000) [0.000, 0.000] | 6/22 (0.273) [0.115, 0.474] | 0/3 | 1/22 (0.045) [0.000, 0.125] |
| Position 11 | 0/22 | 6/22 (0.273) [0.095, 0.435] | 0/3 | 0/22 |
| Domain prior | 4/22 (0.182) [0.000, 0.360] | 9/22 (0.409) [0.167, 0.619] | 2/3 (0.667) [0.000, 1.000] | 4/22 (0.182) [0.000, 0.360] |

Hybrid minus the best baseline, the domain prior, is −0.136 [−0.321, 0.111]. The lower bound is not above 0. This day would not pass the gate. It was not used to choose the rule.
