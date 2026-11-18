# Position bands

Label: PREDICTED. A URL's truth on a day is the min, the max, and the full list of its pull positions. A pull that omits it contributes 11, or a deep rank of 11 or worse when that rank is known.

Alpha for the Winkler score is 0.2, fixed before these forward days. The score is the inclusive width, plus 10 times the distance outside the band. Lower is better.

Persistence half-width k=6 and the model offsets were chosen on Nov 9 and Nov 11 only, by the lowest Winkler score. Nov 13 and Nov 16 did not choose them.

Model offsets, by bucket: surviving -5 to 5, new URLs -6 to 6. Calibration Winkler 13.798, coverage 0.864, mean width 10.212.
Persistence calibration Winkler 14.414, coverage 0.868, mean width 11.380.

The model band is centered on the model's rank inside that day's URL set, then shifted by the frozen offsets. It is not a raw Google position. The Nov 16 model is `model-heldout-nov16`, fit through Nov 13.

There is one 3-pull day, Nov 16. The cross-day ceiling, how well one day's band predicts the next day's band, waits for a second 3-pull day.

## Forward checks

| Day | System | Winkler | Coverage | Full containment | Mean width | #1 Brier | Top-3 Brier |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-13 | Persistence ± k | 14.875 [13.439, 16.150] n=400 | 0.850 [0.803, 0.902] n=400 | 0.850 [0.803, 0.902] n=400 | 11.425 [11.324, 11.521] n=400 | 0.066 [0.063, 0.069] n=400 | 0.158 [0.145, 0.171] n=400 |
| 2026-11-13 | Last observed band | 30.250 [24.714, 35.471] n=400 | 0.203 [0.140, 0.268] n=400 | 0.203 [0.140, 0.268] n=400 | 1.000 [1.000, 1.000] n=400 | 0.070 [0.044, 0.095] n=400 | 0.155 [0.112, 0.196] n=400 |
| 2026-11-13 | Model band | 15.430 [12.793, 18.474] n=400 | 0.845 [0.783, 0.906] n=400 | 0.845 [0.783, 0.906] n=400 | 10.355 [10.199, 10.505] n=400 | 0.063 [0.060, 0.067] n=400 | 0.136 [0.123, 0.148] n=400 |
| 2026-11-16 | Persistence ± k | 17.770 [16.016, 19.426] n=1587 | 0.834 [0.798, 0.872] n=1587 | 0.720 [0.661, 0.785] n=529 | 11.809 [11.654, 11.948] n=529 | 0.051 [0.046, 0.056] n=1587 | 0.126 [0.115, 0.138] n=1587 |
| 2026-11-16 | Last observed band | 29.589 [25.242, 33.575] n=1587 | 0.344 [0.296, 0.398] n=1587 | 0.083 [0.039, 0.138] n=529 | 1.000 [1.000, 1.000] n=529 | 0.055 [0.040, 0.073] n=1587 | 0.141 [0.106, 0.172] n=1587 |
| 2026-11-16 | Model band | 27.043 [19.692, 34.537] n=1587 | 0.699 [0.625, 0.782] n=1587 | 0.565 [0.477, 0.664] n=529 | 10.754 [10.546, 10.947] n=529 | 0.049 [0.044, 0.054] n=1587 | 0.106 [0.098, 0.115] n=1587 |

Nov 13 is one pull, so each observed band is a single position. Nov 16 is three pulls plus deep ranks for absences.

On Nov 16 the model band's Winkler score is 27.043 against persistence 17.770, and its coverage is 0.699 against persistence 0.834. That forward check would not pass the Nov 18 gate. It was not used to change k or the offsets.

## Called URL versus how often it lands there

The called URL is the one with the best center in the query. Its predicted chance is the share of its band that covers the spot. The observed rate is the share of pulls that put it there.

| Day | System | Predicted #1 chance | Observed #1 rate | Predicted top-3 chance | Observed top-3 rate |
| --- | --- | --- | --- | --- | --- |
| 2026-11-13 | Persistence ± k | 0.143 | 0.533 [0.367, 0.700] n=30 | 0.429 | 0.767 [0.600, 0.900] n=30 |
| 2026-11-13 | Last observed band | 1.000 | 0.533 [0.367, 0.700] n=30 | 1.000 | 0.767 [0.600, 0.900] n=30 |
| 2026-11-13 | Model band | 0.167 | 0.533 [0.367, 0.700] n=30 | 0.500 | 0.767 [0.600, 0.900] n=30 |
| 2026-11-16 | Persistence ± k | 0.143 | 0.511 [0.367, 0.667] n=90 | 0.429 | 0.667 [0.500, 0.811] n=90 |
| 2026-11-16 | Last observed band | 1.000 | 0.511 [0.367, 0.667] n=90 | 1.000 | 0.667 [0.500, 0.811] n=90 |
| 2026-11-16 | Model band | 0.167 | 0.444 [0.289, 0.611] n=90 | 0.500 | 0.744 [0.600, 0.878] n=90 |

## Added diagnostic for 2026-11-17

The 2026-11-17 consensus gate is unchanged. These pulls are not in this workspace, so there is no Nov 17 band number yet.
When the three pulls exist, `scripts/score_consensus.py --band-calibration evals/preregistered/band-2026-11-18/band-calibration.json` prints this same table and labels it ADDED DIAGNOSTIC. That print is not part of the Nov 17 gate.

