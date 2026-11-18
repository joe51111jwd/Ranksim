# 2026-11-17 consensus scorecard

Label: PREDICTED. The Nov 18 gates were not changed. The model is `model-next.json`, sha256 `bc4de5022a44c91bdb481c46f28c5374ee5477d1990d55931a21a4ce0e2a8ea7`, which matches the pre-registered hash.

Pulls: 09:31–09:33 ET, 10:03–10:05 ET, and 10:46–10:47 ET. 30 queries in every pull. Persistence is the Nov 16 three-pull consensus position, with 11 or a deep rank when the URL was absent that day. A Nov 17 pull that omits a URL counts as 11. There is no Nov 17 deep file.

## Consensus gate

The pre-registered scorecard is valid: the hash matches, every URL in the union is scored, and the leave-one-out ceiling, persistence, and the model are below with query-bootstrap intervals.

The model does not beat persistence. Top-3 recall is tied at 188/222 (0.847). Number-one accuracy is 60/90 for the model and 73/90 for persistence. The claim rule requires a higher point estimate on one of those two.

Leave-one-out, same consensus for the ceiling, persistence, and the model.

| | Exact | 3-wide | Top-3 recall | Number one |
| --- | --- | --- | --- | --- |
| Ceiling | 418/1141 (0.366) [0.269, 0.464] | 699/1141 (0.613) [0.523, 0.704] | 177/222 (0.797) [0.709, 0.870] | 75/90 (0.833) [0.711, 0.933] |
| Persistence | 414/1141 (0.363) [0.288, 0.436] | 706/1141 (0.619) [0.535, 0.696] | 188/222 (0.847) [0.778, 0.908] | 73/90 (0.811) [0.678, 0.922] |
| Model | 260/1141 (0.228) [0.182, 0.279] | 587/1141 (0.514) [0.434, 0.589] | 188/222 (0.847) [0.776, 0.909] | 60/90 (0.667) [0.500, 0.811] |

Top-3 precision is 177/270 (0.656) [0.548, 0.759] for the ceiling, 188/270 (0.696) [0.611, 0.781] for persistence, and 188/270 (0.696) [0.611, 0.778] for the model.

Full-day consensus, one score against the median of all three pulls:

| | Exact | 3-wide | Top-3 recall | Number one |
| --- | --- | --- | --- | --- |
| Persistence | 267/436 (0.612) [0.546, 0.678] | 343/436 (0.787) [0.740, 0.828] | 72/85 (0.847) [0.765, 0.920] | 25/30 (0.833) [0.700, 0.967] |
| Model | 81/436 (0.186) [0.146, 0.233] | 196/436 (0.450) [0.369, 0.537] | 71/85 (0.835) [0.750, 0.906] | 22/30 (0.733) [0.567, 0.867] |

## Wide-band diagnostic

Not the Nov 17 gate. Not a change to the Nov 18 gate. The last observed band is the cross-day ceiling: Nov 16's min-to-max predicts Nov 17.

| System | Winkler | Coverage | Full containment | Mean width |
| --- | --- | --- | --- | --- |
| Persistence ± 6 | 13.276 [12.742, 13.787] n=1755 | 0.943 [0.928, 0.959] n=1755 | 0.872 [0.834, 0.909] n=585 | 12.017 |
| Nov 16 band, the cross-day ceiling | 10.066 [8.864, 11.322] n=1755 | 0.806 [0.764, 0.838] n=1755 | 0.591 [0.515, 0.659] n=585 | 3.764 |
| Model band | 30.682 [19.961, 43.061] n=1755 | 0.676 [0.586, 0.770] n=1755 | 0.602 [0.509, 0.705] n=585 | 10.203 |

## Tight-band diagnostic

Not the Nov 17 gate. Width at most 3. Every URL received a band.

| System | Coverage | Winkler | Mean width |
| --- | --- | --- | --- |
| Model | 864/2112 (0.409) [0.368, 0.454] | 16.858 [15.247, 18.393] | 2.109 |
| Persistence ± 1 | 1478/2112 (0.700) [0.655, 0.744] | 14.695 [12.438, 16.834] | 2.957 |
| Last band clipped to width 3 | 1239/2112 (0.587) [0.546, 0.623] | 16.871 [14.268, 19.321] | 1.000 |

The tight gate wants model coverage at least 0.05 above persistence ± 1. The gap here is 0.409 − 0.700 = −0.291. This day is not the Nov 18 gate.
