# Tight bands, width at most 3

Label: PREDICTED. The ±6 band gate in `evals/preregistered/band-2026-11-18/` is unchanged.

The center is a least-squares prediction of today's position from four features: seen or new, last position, the URL's mean absolute move across past pulls, and that same mean for the query. Width is 1, 2, or 3 from a second least-squares prediction of the absolute error. The fit uses Nov 6 to Nov 11. The two width cuts were chosen on Nov 11 to Nov 13, among 5 answer-every-URL settings. Each setting is a pair of quantiles of the fit-window expected error. The winner is the highest coverage, then the lower Winkler score, then the narrower band.

Chosen cuts: width 1 when the expected absolute error is at most 1.684328959003786, width 2 up to 2.1973751202085015, otherwise width 3. The frozen point does not abstain. Persistence ± 1 answers every URL, and the gate requires an answered fraction at least that high.

On the Nov 13 selection window the model coverage is 0.355 and persistence ± 1 is 0.576. The gap is -0.221. The gate needs +0.05 on a later day. This window chose the cuts, so it is not the gate.

## Nov 13 selection window

| System | Coverage of issued bands | Answered | Winkler | Mean width |
| --- | --- | --- | --- | --- |
| Model, answer all | 0.355 [0.316, 0.392] n=524 | 1.000 (524/524) | 17.271 [15.159, 19.420] n=524 | 2.290 |
| Persistence ± 1 | 0.576 [0.502, 0.653] n=524 | 1.000 (524/524) | 19.183 [15.231, 22.982] n=524 | 2.943 |
| Last band clipped to width 3 | 0.391 [0.329, 0.451] n=524 | 1.000 (524/524) | 23.328 [18.951, 27.673] n=524 | 1.000 |

## Precision at answered fraction, Nov 13

Precision here is the share of observed positions that fall inside the bands that were issued. Answered fraction is how many URLs received a band. Rows that abstain are not in the Winkler score. The answer-all row is the frozen point. The other rows were not eligible for the gate, because they answer fewer URLs than persistence ± 1.

| Abstain above | Answered fraction | Coverage | Full containment | Winkler | Mean width |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2.100883773848092 | 0.429 | 0.489 | 0.489 | 13.111 | 1.422 |
| 2.1973751202085015 | 0.462 | 0.467 | 0.467 | 13.446 | 1.463 |
| 2.3754393935815585 | 0.550 | 0.441 | 0.441 | 13.931 | 1.708 |
| 2.603143750440879 | 0.649 | 0.438 | 0.438 | 14.465 | 1.906 |
| 2.8076414350734105 | 0.763 | 0.400 | 0.400 | 15.270 | 2.070 |
| 3.091394262234236 | 0.847 | 0.387 | 0.387 | 15.788 | 2.162 |
| 3.222107162697937 | 0.901 | 0.369 | 0.369 | 16.576 | 2.212 |
| none | 1.000 | 0.355 | 0.355 | 17.271 | 2.290 |

## Nov 16 forward check, not used to choose

Nov 16 here is the three pulls plus deep ranks for absences. Features still stop at Nov 13. It was scored after the calibration file was written.

| System | Coverage of issued bands | Answered | Winkler | Mean width |
| --- | --- | --- | --- | --- |
| Model, answer all | 0.303 [0.259, 0.353] n=2070 | 1.000 (690/690) | 27.380 [24.138, 30.409] n=2070 | 2.245 |
| Persistence ± 1 | 0.583 [0.537, 0.633] n=2070 | 1.000 (690/690) | 23.585 [20.097, 26.956] n=2070 | 2.957 |
| Last band clipped to width 3 | 0.441 [0.384, 0.492] n=2070 | 1.000 (690/690) | 27.222 [23.318, 30.995] n=2070 | 1.000 |

## Nov 17

The Nov 17 pulls are not in this workspace. When they arrive, `scripts/score_consensus.py` runs the existing Nov 17 consensus gate, and `--band-calibration` plus `--tight-calibration` print the two band diagnostics. Those diagnostics are not part of the Nov 17 gate. Label: PREDICTED.

