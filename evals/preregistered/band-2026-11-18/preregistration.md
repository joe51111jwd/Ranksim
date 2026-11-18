# Pre-registration: position bands for 2026-11-18

Label: PREDICTED. This note is committed before any 2026-11-18 pull. It does not change the 2026-11-17 consensus gate in `evals/preregistered/consensus-2026-11-17/preregistration.md`.

Nothing here is a deploy. The product ranker is unchanged.

## What a band is

For a query, a date, and a URL, the truth is the lowest position, the highest position, and the full list of positions across that day's pulls. A pull that omits the URL contributes 11, or a deep rank when that rank is 11 or worse. The deep list is not an extra top-10 vote.

A prediction is a lower bound and an upper bound. Width is the inclusive count of positions. The Winkler score is that width, plus 10 times the distance outside the band, averaged over the pull positions. Alpha is 0.2 and was fixed before the forward days. Lower is better.

The cross-day ceiling is how well one day's observed band predicts the next day's band. It is the last observed band once the previous day has three pulls. Nov 16 is the only 3-pull day in hand, so that ceiling cannot be computed yet. Nov 18 is the first day it can be, if Nov 17 was collected as three pulls.

## Frozen rule

`band-calibration.json` sha256: `f05eca375fa6d476e9fb51b5a6365219a316120707eed3533a57d02a09cdcabf`

Model: `evals/preregistered/forward-pair-2026-11-16/model-next.json`

sha256: `bc4de5022a44c91bdb481c46f28c5374ee5477d1990d55931a21a4ce0e2a8ea7`

The family is monotone LambdaMART, already frozen. It was fit on single-pull labels through the 2026-11-16 16:26 ET pull. It was not refit for this band rule.

Persistence band: yesterday's consensus position ± 6, clipped to positions 1 through 30. A URL that was absent yesterday is centered at 11.

Model band: the model's rank inside that day's URL set, then an offset. Survivors use -5 to +5. New URLs use -6 to +6.

Last observed band: yesterday's minimum and maximum. A new URL is the point 11.

k and the offsets were chosen on the Nov 9 query-group CV and the Nov 9 to Nov 11 forward window, by the lowest Winkler score. Nov 13, Nov 16, Nov 17, and Nov 18 did not choose them. The menu of offsets was symmetric ±k and one-sided bands, k from 0 to 10. The chosen k is 6, inside that menu.

## Forward checks, not the gate

Label: PREDICTED. These days were scored after the rule was frozen.

| Day | System | Winkler | Coverage | Full containment | Mean width |
| --- | --- | --- | --- | --- | --- |
| 2026-11-13 | Persistence ± 6 | 14.875 [13.439, 16.150] | 0.850 [0.803, 0.902] | 0.850 | 11.425 |
| 2026-11-13 | Last observed band | 30.250 [24.714, 35.471] | 0.203 [0.140, 0.268] | 0.203 | 1.000 |
| 2026-11-13 | Model band | 15.430 [12.793, 18.474] | 0.845 [0.783, 0.906] | 0.845 | 10.355 |
| 2026-11-16 | Persistence ± 6 | 17.770 [16.016, 19.426] | 0.834 [0.798, 0.872] | 0.720 | 11.809 |
| 2026-11-16 | Last observed band | 29.589 [25.242, 33.575] | 0.344 [0.296, 0.398] | 0.083 | 1.000 |
| 2026-11-16 | Model band | 27.043 [19.692, 34.537] | 0.699 [0.625, 0.782] | 0.565 | 10.754 |

Nov 13 is one pull. Nov 16 is three pulls. On Nov 16 the called URL, the one with the best center, lands at #1 on 0.511 of pulls for persistence (predicted chance 0.143) and on 0.444 of pulls for the model (predicted chance 0.167). It lands in the top 3 on 0.667 of pulls for persistence (predicted chance 0.429) and on 0.744 for the model (predicted chance 0.500). The full table is `evals/experiments-2026-11-16/band-metrics.md`.

The Nov 16 model band does not beat persistence on Winkler score or on coverage. That check did not change the frozen rule.

## Added diagnostic on 2026-11-17

The Nov 17 pulls are not in this workspace, so there is no Nov 17 band number. The consensus gate for that day stays as it was committed. When the pulls are scored, pass `--band-calibration evals/preregistered/band-2026-11-18/band-calibration.json`. The script prints a block labeled ADDED DIAGNOSTIC. That block is not part of the Nov 17 gate.

## Gate for 2026-11-18

Three pulls of the same 30 queries, collected after this file. Score every URL in the union of those pulls and the previous day's URLs. A missing pull position is 11, or a deep rank of 11 or worse.

The gate passes only when all of these are true:

1. The model file matches `bc4de5022a44c91bdb481c46f28c5374ee5477d1990d55931a21a4ce0e2a8ea7` and `band-calibration.json` matches `f05eca375fa6d476e9fb51b5a6365219a316120707eed3533a57d02a09cdcabf`.
2. The model's mean Winkler score is strictly lower than the persistence band and strictly lower than the last observed band.
3. The model's band coverage is greater than or equal to the persistence band and greater than or equal to the last observed band.
4. Every URL in that union receives a band. A missing HTML file is not a reason to drop a URL.
5. The label is PREDICTED.

Do not refit on the 2026-11-18 pulls and then quote the refit as this gate.
