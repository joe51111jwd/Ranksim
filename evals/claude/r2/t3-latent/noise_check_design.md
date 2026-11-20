# Target 3 permutation noise check, design transition

DESIGN, not a test. Design days only (through 2026-11-17).

Transition 2026-11-16 to 2026-11-17, search groups o30, max_date 2026-11-17.
Script: `scripts/claude/r2_t3_noise_check.py` (consensus and pair rule imported from `scripts/claude/r2_t3_noise.py`). Interval: 95% query bootstrap, 2000 draws, seed 20261119.

- Observed: 44 flips on 767 pairs (30 searches with pulls on both days, 29 with pairs).
- Shuffled (2000 permutations, seed 20261119): 45.1 expected flips on 812.2 pairs. Total shuffled flips 2.5% to 97.5%: 31 to 61. P(shuffled >= observed) = 0.570.
- Noise share of the flip count (expected / observed): 1.03 [0.78, 1.48]. Theory band 0.75 to 1.3: point inside = True. Under 0.7: False.
- Exact check (every split of each search's pulls): 45.0 expected flips on 811.9 pairs, noise share 1.02 [0.78, 1.47].

theory.md design number: {"expected_flips": 45.0, "expected_pairs": 811.9, "observed_flips": 44, "observed_pairs": 767, "noise_share": 1.02, "lo": 0.78, "hi": 1.47}. Reproduced: {"observed_matches": true, "exact_enumeration_rounded": {"expected_flips": 45.0, "expected_pairs": 811.9, "noise_share": 1.02, "lo": 0.78, "hi": 1.47}, "exact_enumeration_matches_to_stated_digits": true, "shuffled_2000_rounded": {"expected_flips": 45.1, "expected_pairs": 812.2, "noise_share": 1.03, "lo": 0.78, "hi": 1.48}, "shuffled_2000_matches_to_stated_digits": false}.
