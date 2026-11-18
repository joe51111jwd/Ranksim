# Hybrid new-URL placement

Label: PREDICTED. Seen URLs keep yesterday's position. New URLs, and URLs whose crawled text changed, are inserted by the monotone LambdaMART score against those survivors. The Nov 18 gates were not changed.

A new-URL row is a URL that was absent yesterday and whose consensus position is in the top 10. A URL that shows in one pull and is absent from the others has a median of 11 and is not a placement.

The median new-URL position on Nov 11 and Nov 13 is 7. Including Nov 16 it is 7. The Nov 19 gate uses 7. The Nov 16 row below uses 7 so that day does not grade a baseline fit on itself.

Nov 11 uses a model fit on Nov 9. Nov 13 uses `model-heldout-nov13`. Nov 16 uses `model-heldout-nov16`, which has not seen Nov 16 labels.

| Day | System | Exact | 3-wide | Top-3 entry | Observed-set exact |
| --- | --- | --- | --- | --- | --- |
| 2026-11-11 | Hybrid | 16/88 (0.182) [0.118, 0.259] | 46/88 (0.523) [0.396, 0.667] | 6/20 (0.300) [0.118, 0.476] | 16/88 (0.182) [0.118, 0.259] |
| 2026-11-11 | Median new position | 11/88 (0.125) [0.081, 0.167] | 30/88 (0.341) [0.264, 0.416] | 0/20 (0.000) [0.000, 0.000] | 11/88 (0.125) [0.081, 0.167] |
| 2026-11-11 | Position 11 | 0/88 (0.000) [0.000, 0.000] | 16/88 (0.182) [0.112, 0.270] | 0/20 (0.000) [0.000, 0.000] | 0/88 (0.000) [0.000, 0.000] |
| 2026-11-11 | Domain prior | 6/88 (0.068) [0.022, 0.129] | 32/88 (0.364) [0.248, 0.512] | 6/20 (0.300) [0.136, 0.529] | 6/88 (0.068) [0.022, 0.129] |
| 2026-11-11 | Hybrid minus domain |  | 0.159 [0.000, 0.329] |  |  |
| 2026-11-13 | Hybrid | 16/104 (0.154) [0.091, 0.222] | 46/104 (0.442) [0.328, 0.554] | 5/23 (0.217) [0.045, 0.417] | 16/104 (0.154) [0.091, 0.222] |
| 2026-11-13 | Median new position | 10/104 (0.096) [0.056, 0.138] | 32/104 (0.308) [0.235, 0.380] | 0/23 (0.000) [0.000, 0.000] | 10/104 (0.096) [0.056, 0.138] |
| 2026-11-13 | Position 11 | 0/104 (0.000) [0.000, 0.000] | 19/104 (0.183) [0.133, 0.241] | 0/23 (0.000) [0.000, 0.000] | 0/104 (0.000) [0.000, 0.000] |
| 2026-11-13 | Domain prior | 6/104 (0.058) [0.011, 0.107] | 29/104 (0.279) [0.192, 0.374] | 5/23 (0.217) [0.045, 0.409] | 6/104 (0.058) [0.011, 0.107] |
| 2026-11-13 | Hybrid minus median |  | 0.135 [0.018, 0.256] |  |  |
| 2026-11-16 | Hybrid | 5/62 (0.081) [0.017, 0.159] | 25/62 (0.403) [0.309, 0.481] | 6/13 (0.462) [0.154, 0.786] | 14/62 (0.226) [0.097, 0.379] |
| 2026-11-16 | Median new position | 4/62 (0.065) [0.017, 0.118] | 15/62 (0.242) [0.153, 0.347] | 0/13 (0.000) [0.000, 0.000] | 8/62 (0.129) [0.060, 0.220] |
| 2026-11-16 | Position 11 | 0/62 (0.000) [0.000, 0.000] | 12/62 (0.194) [0.109, 0.296] | 0/13 (0.000) [0.000, 0.000] | 1/62 (0.016) [0.000, 0.058] |
| 2026-11-16 | Domain prior | 6/62 (0.097) [0.016, 0.208] | 18/62 (0.290) [0.185, 0.422] | 7/13 (0.538) [0.286, 0.833] | 7/62 (0.113) [0.027, 0.224] |
| 2026-11-16 | Hybrid minus domain |  | 0.113 [-0.071, 0.259] |  |  |

## Nov 17 diagnostic, not used to choose

Model is `model-next`, which has not seen Nov 17. Median new position is the frozen value 7. No Nov 17 HTML, so no page is marked content-changed.

| System | Exact | 3-wide | Top-3 entry | Observed-set exact |
| --- | --- | --- | --- | --- |
| Hybrid | 6/22 (0.273) [0.087, 0.478] | 6/22 (0.273) [0.087, 0.478] | 2/3 (0.667) [0.000, 1.000] | 7/22 (0.318) [0.120, 0.550] |
| Median new position | 0/22 (0.000) [0.000, 0.000] | 6/22 (0.273) [0.115, 0.474] | 0/3 (0.000) [0.000, 0.000] | 1/22 (0.045) [0.000, 0.125] |
| Position 11 | 0/22 (0.000) [0.000, 0.000] | 6/22 (0.273) [0.095, 0.435] | 0/3 (0.000) [0.000, 0.000] | 0/22 (0.000) [0.000, 0.000] |
| Domain prior | 4/22 (0.182) [0.000, 0.360] | 9/22 (0.409) [0.167, 0.619] | 2/3 (0.667) [0.000, 1.000] | 4/22 (0.182) [0.000, 0.360] |
| Hybrid minus domain |  | -0.136 [-0.321, 0.111] |  |  |

The Nov 19 gate passes only when this difference, on that day's new URLs, has a query-bootstrap lower bound above 0 against the best of the three baselines.
