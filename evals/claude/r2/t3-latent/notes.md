# Target 3, round 2 loop 1: the latent-order scorer and the noise check

Built from `evals/claude/theory.md`, section 3, "What gets built in loop 1". Nothing is fitted on any data. The half-life (2 days), the prior (0.5 on each side) and both call rules come from the theory text. Design data only: every run loads rows through `rsdata.load_rows("2026-11-17")`. No held-out day was opened and `score` was not run on any held-out transition.

## What was built

| File | What it does |
| --- | --- |
| `scripts/claude/r2_t3_latent.py` | The latent-order scorer. `design` runs Nov 16 to Nov 17 on the original 30 searches. `score` is for the lead after the freeze. |
| `scripts/claude/r2_t3_noise_check.py` | The permutation noise check. Same `design` and `score` contract. Imports the consensus and pair code from `scripts/claude/r2_t3_noise.py`. |
| `tests/test_claude_r2_t3_latent.py` | 16 tests on synthetic pulls. No database. |
| `design.json`, `design.md` | Latent scorer on the design transition. |
| `noise_check_design.json`, `noise_check_design.md` | Noise check on the design transition. |

### The latent-order scorer

- Pairs: every strictly ordered pair in day A's consensus top 10, x above y, as in `rsdata.flip_pairs`. Probabilities and calls use day A only. The scored rows are the `rsdata.flip_pairs` rows, with `flipped` from there.
- Votes: every top-10 pull of the search dated on or before day A. Each pull votes once for the order it shows. A missing URL counts as 11. A pull that shows neither URL does not vote. Deep lists do not vote.
- Weight: 0.5 to the power (age / 2). Age is calendar days from the pull's day to day A. Every pull on day A weighs 1, including the Nov 16 morning pull.
- p = (weighted reverse votes + 0.5) / (all weighted votes + 1).
- P(flip) = p^3 + 3 p^2 (1 - p), the chance that a majority of three fresh pulls shows the reverse order.
- R1: call every pair with P(flip) above 0.5.
- R2: sort day A's pairs by P(flip), highest first. Call the largest leading set whose mean P(flip) is at least 0.5. Equal values are taken or left together.
- The code asserts that the features read no row dated after day A. A test checks that changing day B's pulls changes no probability and no call.
- The docstring says which votes are used and why it is not circular. In short: day A's order is a function of day A's pulls, so it adds nothing beyond them, and each pull counts once. The event predicted is day B's consensus, and none of its pulls is read.

### The noise check

For each search, the voting pulls of the two days are pooled and shuffled between the days, keeping the pull count per day. Both consensus lists and the flips are recomputed with the rsdata rules. 2,000 shuffles, seed 20261119. It reports observed flips and pairs, expected flips and pairs, the spread of shuffled flips, and the noise share of the flip count (expected over observed) with a 95% query bootstrap interval. It also reports the exact expectation over every split of each search's pulls. The observed count is checked against `rsdata.flip_pairs` in code.

## Design numbers (DESIGN, PREDICTED, not a test)

Nov 16 to Nov 17, original 30 searches: 767 scored pairs, 44 flipped. Persistence is right on 723 of 767. Votes per pair come from 8 pulls: the four Nov 16 pulls at weight 1, and Nov 13, 11, 9 and 6 at 0.354, 0.177, 0.088 and 0.031.

| Rule | Calls | Flip recall | Precision | All pairs minus persistence |
| --- | ---: | --- | --- | --- |
| R1 | 12 | 10/44 (0.227) [0.075, 0.393] | 10/12 (0.833) [0.600, 1.000] | +0.010 [+0.001, +0.025] |
| R2 | 41 | 17/44 (0.386) [0.212, 0.583] | 17/41 (0.415) [0.224, 0.600] | -0.009 [-0.034, +0.011] |

- Day A's universe has 1,120 pairs. R1 calls 37 of them and R2 calls 80. Only 12 and 41 of those calls are in the scored pairs. The rest lost a URL from day B's top 10 or tied on day B.
- R2's lowest called P(flip) is 0.33. On the scored pairs the model expects R2's calls to be right 48% of the time. They were right 41.5% of the time.
- The model expects 74.3 flips on the scored pairs. There were 44. The excess sits at low P(flip): below 0.2 it expects 35.5 flips and there were 10. From 0.2 to 0.5 it expects 31.8 and there were 24. Above 0.5 it expects 7.0 and there were 10. With about five weighted votes per pair, the Jeffreys prior pulls quiet pairs up.
- If R2's budget ran over the scored pairs instead of day A's universe, it would call 30 and be right on 12. That reads day B, so it is a diagnostic only.

## How this compares with the theory

- The theory expected a predictor of this kind that sees the four Nov 16 pulls to call 38 pairs and be right on 16: recall 0.36, precision 0.42. R2 calls 41 and is right on 17: recall 0.386, precision 0.415. That is the expected result, within one pair.
- R1 is the narrow version. It is right on 10 of 12 calls and beats persistence on all pairs by +0.010 [+0.001, +0.025]. That matches the round 1 pull-majority scorer A (10 of 14 calls right, +0.008). Its recall is far from one half.
- The theory's held-out prediction is flip recall 0.35 to 0.55 with precision 0.40 to 0.55, and a lower bound under 0.5. On design data R2 sits in that range and its lower bound is 0.212. Neither rule meets Target 3 with the persistence guard on design data.

## Noise check: does it reproduce the theory's design number?

| Quantity | theory.md | Exact, every split | 2,000 shuffles, seed 20261119 |
| --- | --- | --- | --- |
| Observed | 44 flips on 767 pairs | 44 on 767 | 44 on 767 |
| Expected under shuffling | 45.0 flips on 811.9 pairs | 45.0 on 811.9 | 45.1 on 812.2 |
| Noise share of the flip count | 1.02 [0.78, 1.47] | 1.02 [0.78, 1.47] | 1.03 [0.78, 1.48] |

- The exact enumeration reproduces the design study bit for bit (1.0227 [0.784, 1.465]).
- The 2,000 shuffles differ in the third digit. That is Monte Carlo error: each search's expectation is the mean of 2,000 random splits instead of all 20.
- 2.5% to 97.5% of shuffled totals: 31 to 61 flips. P(shuffled at least 44) = 0.57.

## Choices made where the brief left room

1. Age for the half-life is in calendar days from the pull's day to day A. Hours from the session would give the Nov 16 morning pull a weight of 0.89, but the brief says same-day pulls weigh 1.
2. R2's budget runs over day A's pair universe, not over the scored pairs. Which pairs survive into day B's top 10 is day B information. With this choice no call depends on day B.
3. R2 runs once per transition over the whole scored population. For `score` that is the o30 and a70 searches together, unless `--groups` says otherwise. The per-group tables slice the same calls.
4. R2 takes or leaves pairs with equal P(flip) together. "Largest set" could also mean cutting inside a tie group, but that needs an arbitrary order among equal pairs.
5. P(flip) is B3 of the smoothed share, as the brief says. The noise study's empirical oracle used the posterior mean of B3 instead. Both are fixed and neither is fitted.
6. The noise check uses 2,000 shuffles as its main number, as the brief says. The exact enumeration is printed beside it as a check.
7. `score` (both scripts) refuses to run unless `--prereg-dir` holds a `manifest.json` that verifies with `freeze.verify_manifest` and lists the script itself, and the frozen files are committed and pushed. It also refuses if `--max-date` is not `--day-b`, if day B is a design day, or if the output exists (unless `--force`). On days from Nov 19 it needs three pulls per search unless `--allow-incomplete`. It scores only searches whose last pulled day before day B is day A.
8. Flip recall and precision use `metrics.bootstrap_rate` on the flipped or called subset, as the round 1 harness does. That resamples only searches in the subset. The noise study notes this is somewhat narrow.

## For the freeze

The noise check imports `scripts/claude/r2_t3_noise.py` (and scipy) and `scripts/claude/r2_t3_latent.py`. A closure freeze picks both up:

```bash
python scripts/claude/freeze.py closure evals/preregistered/claude-r2-t3-latent scripts.claude.r2_t3_latent scripts.claude.r2_t3_noise_check -- <preregistration.md>
python scripts/claude/r2_t3_latent.py score --day-a 2026-11-18 --day-b 2026-11-19 --max-date 2026-11-19 --out evals/claude/heldout-r2/claude-r2-t3-latent --prereg-dir evals/preregistered/claude-r2-t3-latent
python scripts/claude/r2_t3_noise_check.py score --day-a 2026-11-18 --day-b 2026-11-19 --max-date 2026-11-19 --out evals/claude/heldout-r2/claude-r2-t3-latent --prereg-dir evals/preregistered/claude-r2-t3-latent
```

Each design run takes about one second.
