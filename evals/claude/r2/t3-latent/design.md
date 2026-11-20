# Target 3 latent-order scorer, design transition

DESIGN, PREDICTED, not a test. Design days only (through 2026-11-17).

Transition 2026-11-16 to 2026-11-17, search groups o30, max_date 2026-11-17.
Half-life 2 days, Jeffreys prior 0.5 on each side, P(flip) = p^3 + 3 p^2 (1 - p). Nothing fitted.
Script: `scripts/claude/r2_t3_latent.py`. Intervals: 95% query bootstrap, 2000 draws, seed 20261119.

Scored pairs (rsdata.flip_pairs): 767 on 29 searches, 44 flipped. Persistence is right on 723/767 (0.943) [0.903, 0.970].
Flips expected by the model (sum of P(flip) over scored pairs): 74.3.
Day A pair universe (no day B information): 1120 pairs; R1 calls 37, R2 calls 80.

| Rule | Calls | Flipped pairs called right (flip recall) | Calls that were right (precision) | All pairs: rule minus persistence |
| --- | ---: | --- | --- | --- |
| R1: P(flip) above 0.5 | 12 | 10/44 (0.227) [0.075, 0.393] | 10/12 (0.833) [0.600, 1.000] | +0.010 [+0.001, +0.025] |
| R2: largest set with mean P(flip) at least 0.5 | 41 | 17/44 (0.386) [0.212, 0.583] | 17/41 (0.415) [0.224, 0.600] | -0.009 [-0.034, +0.011] |

Calibration of the noise model on the scored pairs (a check, not a rule):

| P(flip) | Pairs | Expected flips | Observed flips |
| --- | ---: | ---: | ---: |
| [0, 0.05] | 472 | 12.3 | 5 |
| (0.05, 0.2] | 186 | 23.2 | 5 |
| (0.2, 0.5] | 97 | 31.8 | 24 |
| (0.5, 1] | 12 | 7.0 | 10 |

Votes by pull day (weight = 0.5 ** (age / 2), age in calendar days before day A):

| Day | Age | Weight | Pulls |
| --- | ---: | ---: | ---: |
| 2026-11-06 | 10 | 0.031 | 30 |
| 2026-11-09 | 7 | 0.088 | 30 |
| 2026-11-11 | 5 | 0.177 | 30 |
| 2026-11-13 | 3 | 0.354 | 30 |
| 2026-11-16 | 0 | 1.000 | 120 |

Diagnostics (not part of the rules):

- R2 with its budget run over the scored pairs instead of day A's universe: calls / right: 30 / 12
- scored pairs with P(flip) exactly 0.5 (weighted tie): 0
