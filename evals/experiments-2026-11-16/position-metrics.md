# Position metrics, 2026-11-16

Label: PREDICTED. Real positions are the Serper labels. Every top-10 row is counted.

Point method selected on the early windows only: `persistence_last`. Early exact accuracy of that choice is 0.382.

Ranges are at most 3 positions wide. The applied range is calibrated to 95% coverage on Nov 9 query-group CV residuals and Nov 11 forward residuals. Calibration coverage was 0.647 at mean width 2.879. The 90% reference rule had calibration coverage 0.647 at mean width 2.879. Nov 13 and Nov 16 did not choose the offsets.

Confident #1 uses the monotone LambdaMART score gap between the model's own #1 and #2. Thresholds chosen on the early windows: 0.7528 (target precision 0.80, early precision 0.829, early coverage 0.583) and 0.7528 (target precision 0.90, early precision 0.829, early coverage 0.583).

## Scores

Nov 11 was used for selection. Nov 13 is the model trained through Nov 11. Nov 16 is the model trained through Nov 13. Cells are count/n (rate) [bootstrap 95% CI by query], or mean [CI] with n. The 95% range is the one that is used. The last column is coverage of the 90% reference range.

| System | Eval | Exact | Within 1 | MAE | Range coverage, 95% rule | Mean width | Surviving coverage | New coverage | Range coverage, 90% rule |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| persistence_last | 2026-11-11 (selection forward) | 114/296 (0.385) [0.297, 0.493] | 182/296 (0.615) [0.512, 0.713] | 1.821 [1.358, 2.306] n=296 | 194/296 (0.655) [0.562, 0.747] | 2.872 [2.851, 2.896] n=296 | 156/208 (0.750) [0.664, 0.829] | 38/88 (0.432) [0.368, 0.522] | 194/296 (0.655) [0.562, 0.747] |
| model_rank | 2026-11-11 (selection forward) | 102/296 (0.345) [0.270, 0.418] | 196/296 (0.662) [0.586, 0.741] | 1.446 [1.153, 1.717] n=296 | 191/296 (0.645) [0.561, 0.725] | 2.838 [2.813, 2.859] n=296 | 150/208 (0.721) [0.631, 0.803] | 41/88 (0.466) [0.337, 0.598] | 191/296 (0.645) [0.561, 0.725] |
| persistence_last | 2026-11-13 (held out) | 100/298 (0.336) [0.264, 0.408] | 174/298 (0.584) [0.493, 0.676] | 2.081 [1.653, 2.515] n=298 | 187/298 (0.628) [0.544, 0.709] | 2.883 [2.862, 2.903] n=298 | 143/194 (0.737) [0.642, 0.812] | 44/104 (0.423) [0.352, 0.514] | 187/298 (0.628) [0.544, 0.709] |
| model_rank | 2026-11-13 (held out) | 81/298 (0.272) [0.225, 0.323] | 184/298 (0.617) [0.550, 0.679] | 1.651 [1.407, 1.913] n=298 | 181/298 (0.607) [0.544, 0.667] | 2.862 [2.842, 2.880] n=298 | 137/194 (0.706) [0.638, 0.772] | 44/104 (0.423) [0.318, 0.529] | 181/298 (0.607) [0.544, 0.667] |
| persistence_last | 2026-11-16 (held out) | 106/299 (0.355) [0.258, 0.455] | 171/299 (0.572) [0.465, 0.680] | 2.087 [1.552, 2.603] n=299 | 180/299 (0.602) [0.505, 0.703] | 2.880 [2.860, 2.900] n=299 | 143/199 (0.719) [0.617, 0.802] | 37/100 (0.370) [0.290, 0.460] | 180/299 (0.602) [0.505, 0.703] |
| model_rank | 2026-11-16 (held out) | 88/299 (0.294) [0.208, 0.383] | 172/299 (0.575) [0.485, 0.669] | 1.625 [1.284, 1.987] n=299 | 176/299 (0.589) [0.497, 0.679] | 2.819 [2.800, 2.840] n=299 | 134/199 (0.673) [0.562, 0.767] | 42/100 (0.420) [0.314, 0.520] | 176/299 (0.589) [0.497, 0.679] |

## Confident #1

| Eval | Rule | Precision | Coverage | Calls |
| --- | --- | --- | --- | --- |
| 2026-11-11 | model broad | 16/20 (0.800) [0.600, 0.955] | 20/30 (0.667) [0.500, 0.833] | 20/30 |
| 2026-11-11 | model strict | 16/20 (0.800) [0.600, 0.955] | 20/30 (0.667) [0.500, 0.833] | 20/30 |
| 2026-11-11 | persistence yesterday #1 | 21/30 (0.700) [0.533, 0.867] | 30/30 (1.000) [1.000, 1.000] | 30/30 |
| 2026-11-13 | model broad | 14/20 (0.700) [0.478, 0.889] | 20/30 (0.667) [0.500, 0.833] | 20/30 |
| 2026-11-13 | model strict | 14/20 (0.700) [0.478, 0.889] | 20/30 (0.667) [0.500, 0.833] | 20/30 |
| 2026-11-13 | persistence yesterday #1 | 16/30 (0.533) [0.367, 0.700] | 30/30 (1.000) [1.000, 1.000] | 30/30 |
| 2026-11-16 | model broad | 11/19 (0.579) [0.357, 0.800] | 19/30 (0.633) [0.467, 0.800] | 19/30 |
| 2026-11-16 | model strict | 11/19 (0.579) [0.357, 0.800] | 19/30 (0.633) [0.467, 0.800] | 19/30 |
| 2026-11-16 | persistence yesterday #1 | 17/30 (0.567) [0.400, 0.733] | 30/30 (1.000) [1.000, 1.000] | 30/30 |

## REAL stability of the top spots

This section describes the Serper lists. It is not a model score.

| From | To | Queries | #1 same | #2 same | #3 same | Top 3 URLs still in the top 3 |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-11-06 | 2026-11-09 | 30 | 22/30 (0.733) | 13/30 (0.433) | 14/30 (0.467) | 60/90 (0.667) |
| 2026-11-09 | 2026-11-11 | 30 | 21/30 (0.700) | 16/30 (0.533) | 11/30 (0.367) | 56/90 (0.622) |
| 2026-11-11 | 2026-11-13 | 30 | 16/30 (0.533) | 12/30 (0.400) | 11/30 (0.367) | 59/90 (0.656) |
| 2026-11-13 | 2026-11-16 | 30 | 17/30 (0.567) | 13/30 (0.433) | 10/30 (0.333) | 54/90 (0.600) |

## Would more queries or filled page text move these metrics?

On the early windows, monotone LambdaMART exact-position accuracy is 0.317 on 442 rows with HTML and 0.366 on 153 rows without HTML.
Giving the missing rows the with-HTML rate would move the early exact rate from 0.329 to 0.317. That is the most page text can add on this evidence. It stays far from 85% exact position accuracy.
The bootstrap intervals on 30 queries are a few points wide. Tracking about 100 queries would narrow them. It would not move a rate that sits well below 0.85 up to that target. The limit is day-to-day movement inside the top 10, which these five snapshots already measure. Spending Serper credits on more queries or on refilling blocked pages is not justified for these metrics. Rows without HTML were not the less accurate slice (0.366 exact without HTML, 0.317 with it), so filling those pages would not raise exact-position accuracy. A width-3 range cannot cover 95% or even 90% of these residuals: both rules fall back to the same maximum-coverage offsets, and held-out coverage stays near 0.60. More queries would tighten the interval around that rate. They would not move it to 0.85 or 0.95.
