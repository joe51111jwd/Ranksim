# Pre-registration: round 2 loop 1, Target 3 instruments, latent-order scorer and noise check (Claude goal run)

Label: PREDICTED. Written 2026-11-19, about 20:15 ET, before any day after 2026-11-17 was read. Nothing here changes an earlier gate, the three round 1 preregistrations, the SmartCrew PR 41 ledger, or the product ranker. Every rule and number below is taken from `evals/claude/theory.md` section 3, the docstrings of `scripts/claude/r2_t3_latent.py` and `scripts/claude/r2_t3_noise_check.py`, and `evals/claude/r2/t3-latent/notes.md`. Nothing is new and nothing is tuned.

## What is frozen

The sha256 of every file below is in `manifest.json` in this folder, written with `scripts/claude/freeze.py closure` on the modules `scripts.claude.r2_t3_latent` and `scripts.claude.r2_t3_noise_check`.

- `scripts/claude/r2_t3_latent.py` (the scorer) and `scripts/claude/r2_t3_noise_check.py` (the check)
- `scripts/claude/r2_t3_noise.py` (the consensus and pair code the check imports)
- `scripts/claude/rsdata.py`, `scripts/claude/metrics.py`, `scripts/claude/freeze.py`, and every other repo file they import, including `src/aeo/ranksim/consensus.py`, `src/aeo/ranksim/site_history.py` and `src/aeo/crawl/urls.py`. The manifest lists them all, with the Python, numpy and pandas versions.

Nothing is fitted. The half-life (2 days), the prior (0.5 on each side) and both call rules are fixed by theory.md. `score` refuses to run unless this manifest verifies, lists the script, and is committed and pushed. It refuses to overwrite a scored file.

## What is measured

**Latent-order scorer.** For one transition, day A to day B, on every search whose last pulled day before day B is day A:

- Scored pairs are the `rsdata.flip_pairs` pairs: both URLs in the consensus top 10 on both days and strictly ordered on both days. A pair flipped when day B reverses day A's order.
- Each top-10 pull dated on or before day A votes once for the order it shows (missing URL counts as 11), with weight 0.5 to the power (age / 2), age in calendar days before day A. p = (weighted reverse votes + 0.5) / (all weighted votes + 1). P(flip) = p^3 + 3 p^2 (1 - p). No pull dated after day A is read.
- R1 calls a flip on every pair with P(flip) above 0.5. R2 calls the largest leading set of day A's pair universe, sorted by P(flip), whose mean P(flip) is at least 0.5 (ties taken or left together).
- Reported per rule: flip recall (flipped pairs called), precision (calls that were right), and all pairs accuracy minus persistence (yesterday's order). Intervals: 95% query bootstrap, 2000 draws, seed 20261119.

**Noise check.** For each search, the voting pulls of day A and day B (each day's consensus session) are pooled and shuffled between the days, keeping the pull count per day. Consensus and flips are recomputed with the rsdata rules. 2000 permutations, seed 20261119. Noise share of the flip count = expected flips under shuffling / observed flips, with a 95% query bootstrap interval (2000 draws, seed 20261119). The exact expectation over every split is printed as a check.

## Searches and held-out transitions

- Searches: groups o30 and a70 (the default `--groups`). Only searches whose last pulled day before day B is day A are scored.
- Transitions: 2026-11-17 to 2026-11-18 and 2026-11-18 to 2026-11-19. On both, only the original 30 searches have pulls on both days (the 70 added searches were not pulled on Nov 17 or Nov 18). Any later consecutive transition pulled in this run is scored the same way when it exists.
- Nov 18 was pulled by the earlier team, two or three pulls per search. It is scored as is.
- Nov 19 for the original 30 was pulled by the earlier team's collector in the morning, and its third pull stopped early. As in the dated addenda of the three round 1 preregistrations, it is scored as pulled: the 18 to 19 runs use `--allow-incomplete`. The 17 to 18 runs do not need it.
- Each transition is scored once by each script, with `--max-date` equal to day B and output in `evals/claude/heldout-r2/claude-r2-t3-latent/`.
- The frozen scripts have no pooled mode. The report gives each transition on its own, and the plain sum of the counts over both transitions with no interval.

## Predictions made before scoring (PREDICTED, copied from theory.md section 3)

- Latent-order scorer: "PREDICTED for the held-out days in hand (Nov 17 to 18 with about 4 extra recent pulls, Nov 18 to 19 with about 7 that are up to two days old, about 90 flips in all): flip recall 0.35 to 0.55 with precision 0.40 to 0.55. The lower bound stays under 0.5. Target 3 with the guard fails, and it fails because the thing to be predicted is measurement noise." The design notes compare this range with R2.
- Noise check: "PREDICTED: the noise share of the flip count is between 0.75 and 1.3 on each" held-out consecutive transition.

## Pass rule and what would prove the theory wrong (from theory.md section 3)

- Target 3 with the guard: a rule passes on a transition when its flip recall has a 95% lower bound above 0.5 and it is no worse than persistence on all pairs (the same as at least half of its flip calls right). The scorer reports these as `recall_lower_bound_above_half` and `not_below_persistence`. Both must be true for the same rule.
- The theory is wrong if any of these happens (theory.md, "What would prove this wrong"):
  1. "On held-out transitions the observed flips clearly exceed the shuffled-pull expectation (noise share under 0.7)."
  2. "The latent-order scorer's calls are right far less than 40% of the time." theory.md gives no number for "far less". This file does not add one. The report gives the precision and its interval.
  3. "Any frozen scorer reaches a flip-recall lower bound above 0.5 while at least half of its calls are right."

## Design numbers (DESIGN, not a test)

From `evals/claude/r2/t3-latent/design.md` and `noise_check_design.md`. Nov 16 to Nov 17, original 30 searches, rows through 2026-11-17: 767 scored pairs, 44 flipped, persistence right on 723 of 767.

| Rule | Calls | Flip recall | Precision | All pairs minus persistence |
| --- | ---: | --- | --- | --- |
| R1 | 12 | 10/44 (0.227) [0.075, 0.393] | 10/12 (0.833) [0.600, 1.000] | +0.010 [+0.001, +0.025] |
| R2 | 41 | 17/44 (0.386) [0.212, 0.583] | 17/41 (0.415) [0.224, 0.600] | -0.009 [-0.034, +0.011] |

Noise check, same transition: observed 44 flips on 767 pairs, shuffled 45.1 expected flips on 812.2 pairs, noise share 1.03 [0.78, 1.48]. Exact enumeration 1.02 [0.78, 1.47], the number in theory.md.

## Date and seal

Written 2026-11-19 before any SERP row or page fetch dated 2026-11-18 or later was read by this run. Both scripts were built and run on design days only (`rsdata.load_rows("2026-11-17")`). No held-out transition was scored before this file and its manifest were committed and pushed.
