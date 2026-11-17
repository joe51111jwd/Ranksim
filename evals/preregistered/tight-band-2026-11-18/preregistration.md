# Pre-registration: tight bands for 2026-11-18

Label: PREDICTED. This file is a second gate. It does not change `evals/preregistered/band-2026-11-18/preregistration.md` and it does not change the 2026-11-17 consensus gate.

Nothing here is a deploy. The product ranker is unchanged.

## What is frozen

`tight-calibration.json` sha256: `5a6ecf15ed5ced9719d5cf0db175050b40ae1091e72f60533db2f1b8b80ce21f`

A band is 1, 2, or 3 positions wide. The center and the width are least squares on four features known before the outcome day:

- seen or new
- last position, with 11 when the URL was absent
- the URL's mean absolute move across past pulls, absent counted as 11
- the same mean for the query

The coefficients were fit on Nov 6 to Nov 9 and Nov 9 to Nov 11. Width 1 is an expected absolute error at most 1.684, width 2 up to 2.197, otherwise width 3. Those two cuts are the winning pair among 5 quantile pairs of the fit-window expected error, chosen on Nov 11 to Nov 13 by the highest coverage, then the lower Winkler score, then the narrower band. Nov 16, Nov 17, and Nov 18 were not used.

The frozen point answers every URL. Persistence ± 1 also answers every URL, and the gate requires an answered fraction at least that high. The precision-at-coverage curve below shows what abstaining would do. Those rows are not the frozen point.

Winkler uses the same alpha 0.2 as the wide-band file: inclusive width, plus 10 times the distance outside the band. It is averaged over the positions of the URLs that received a band.

## Gate

On the 2026-11-18 pulls, after this file is committed, the gate passes only when all of these are true:

1. `tight-calibration.json` matches the sha256 above.
2. The model's coverage of the positions inside the bands it issued is at least the persistence ± 1 coverage plus 0.05.
3. The model's answered fraction is greater than or equal to the persistence ± 1 answered fraction.
4. Every URL in the union of that day's pulls and the previous days' URLs is either given a band or counted as an abstention. A missing HTML file is not a reason to drop a URL.
5. The label is PREDICTED.

Persistence ± 1 is yesterday's position, one step down and one step up, clipped at the edges. The last observed band, clipped to width 3 when it is wider, is reported beside it. It is not an extra pass line.

The Nov 13 selection window does not clear this gate. Model coverage there is 0.355 against persistence ± 1 at 0.576. The gap is -0.221. That window chose the cuts, so it is not a pass.

## Nov 16 forward check, not used to choose

Three pulls, features stopped at Nov 13. Label: PREDICTED.

| System | Coverage | Answered | Winkler | Mean width |
| --- | --- | --- | --- | --- |
| Model | 0.303 [0.259, 0.353] | 690/690 | 27.380 [24.138, 30.409] | 2.245 |
| Persistence ± 1 | 0.583 [0.537, 0.633] | 690/690 | 23.585 [20.097, 26.956] | 2.957 |
| Last band clipped to width 3 | 0.441 [0.384, 0.492] | 690/690 | 27.222 [23.318, 30.995] | 1.000 |

The model does not beat persistence ± 1 here. This check did not change the frozen cuts.

## Precision at answered fraction, Nov 13

Precision is the share of observed positions inside the issued bands. The answer-all row is the frozen point.

| Abstain above | Answered fraction | Coverage | Full containment | Winkler | Mean width |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2.101 | 0.429 | 0.489 | 0.489 | 13.111 | 1.422 |
| 2.197 | 0.462 | 0.467 | 0.467 | 13.446 | 1.463 |
| 2.375 | 0.550 | 0.441 | 0.441 | 13.931 | 1.708 |
| 2.603 | 0.649 | 0.438 | 0.438 | 14.465 | 1.906 |
| 2.808 | 0.763 | 0.400 | 0.400 | 15.270 | 2.070 |
| 3.091 | 0.847 | 0.387 | 0.387 | 15.788 | 2.162 |
| 3.222 | 0.901 | 0.369 | 0.369 | 16.576 | 2.212 |
| none | 1.000 | 0.355 | 0.355 | 17.271 | 2.290 |

## Nov 17

The Nov 17 pulls are not in this workspace, so the Nov 17 consensus gate and the band diagnostics have not been run on them. When the pulls are here, `scripts/score_consensus.py` runs the existing Nov 17 gate. `--band-calibration evals/preregistered/band-2026-11-18/band-calibration.json` prints the wide-band diagnostic. `--tight-calibration evals/preregistered/tight-band-2026-11-18/tight-calibration.json` prints this tight-band diagnostic. Neither diagnostic changes the Nov 17 gate.
