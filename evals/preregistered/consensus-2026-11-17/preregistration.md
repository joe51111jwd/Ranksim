# Pre-registration: three-pull consensus for 2026-11-17

Label: PREDICTED. This note is committed before any 2026-11-17 pull exists. The model was not chosen from the 2026-11-16 consensus numbers below. Those numbers are DIAGNOSTIC.

Nothing here is a deploy. The product ranker is unchanged.

## Frozen model

`model-next.json` in `evals/preregistered/forward-pair-2026-11-16/`.

sha256: `bc4de5022a44c91bdb481c46f28c5374ee5477d1990d55931a21a4ce0e2a8ea7`

It is the monotone LambdaMART already frozen for the next snapshot. It was fit on single-pull labels through the 2026-11-16 16:26 ET pull. It was not refit on the three-pull consensus. `scripts/score_consensus.py --expect-sha256` refuses a different file.

The 2026-11-16 diagnostic below uses `model-heldout-nov16.json` (`c4619100d0f9db5d4dbeb563e416284631f76589cd1b4f428a60e8fb88de30f3`), which was fit through 2026-11-13 and has not seen any 2026-11-16 label.

## How a day is scored

A day is three top-10 pulls of the same 30 queries, about 30 minutes apart. A URL missing from a pull counts as position 11. A deep rank is used only when it is 11 or worse, and only to fill that absence. It is not a fourth top-10 vote.

Consensus position is the median of those three numbers. An even count rounds half up. The set of positions Google actually showed does not include an imputed 11.

The ceiling is leave-one-out: each pull predicts the consensus of the other two. Persistence predicts that same consensus from the previous day's position (11 if the URL was absent). The model predicts a rank among the URLs in that consensus, from 1 to the number of URLs. A model rank and a raw Google position are both scored as position numbers. A model rank cannot be 11, so exact match is a harder bar for the model when the consensus sits outside the candidate list.

Query bootstrap: 1000 draws, seed 20261116, percentile interval. Label every model number PREDICTED.

## DIAGNOSTIC: 2026-11-16 consensus

Three pulls: 16:26 ET (the 100-query file, original 30), 16:57 ET, and 17:29 ET. Deep file requested 16:45 ET, used only for ranks of 11 or worse. 30 queries. These figures were computed after the pairwise selection had already looked at the 16:26 pull, so they are DIAGNOSTIC. They were not used to pick `model-next.json`.

Leave-one-out, same consensus for all three rows. Denominators are three passes.

| | Exact | 3-wide | Observed-set exact | Top-3 precision | Top-3 recall | Number one |
| --- | --- | --- | --- | --- | --- | --- |
| Ceiling (another pull) | 374/1207 (0.310) [0.218, 0.410] | 625/1207 (0.518) [0.423, 0.625] | 616/1207 (0.510) [0.420, 0.601] | 165/270 (0.611) [0.493, 0.733] | 165/224 (0.737) [0.614, 0.847] | 59/90 (0.656) [0.500, 0.800] |
| Persistence (Nov 13 position) | 283/1207 (0.234) [0.176, 0.300] | 585/1207 (0.485) [0.413, 0.561] | 422/1207 (0.350) [0.291, 0.410] | 151/270 (0.559) [0.452, 0.667] | 151/224 (0.674) [0.551, 0.800] | 53/90 (0.589) [0.433, 0.744] |
| Model fit through Nov 13 | 188/1207 (0.156) [0.120, 0.201] | 487/1207 (0.403) [0.341, 0.470] | 249/1207 (0.206) [0.170, 0.253] | 169/270 (0.626) [0.537, 0.707] | 169/224 (0.754) [0.662, 0.845] | 51/90 (0.567) [0.411, 0.722] |

On this comparable score, the ceiling is ahead of persistence on every metric. The model is ahead of persistence on top-3 precision and top-3 recall only. Those intervals overlap persistence, so it is not a demonstrated win. The model is behind on exact position, on the 3-wide range, on observed-set exact, and on number one.

One score against the full three-pull median, not leave-one-out. Persistence here is closer to the median than one noisy pull is, which is why these exact rates are higher than the ceiling row above. They are a different question.

| | Exact | 3-wide | Top-3 precision | Top-3 recall | Number one |
| --- | --- | --- | --- | --- | --- |
| Persistence | 256/468 (0.547) [0.471, 0.615] | 351/468 (0.750) [0.694, 0.799] | 61/90 (0.678) [0.556, 0.789] | 61/83 (0.735) [0.619, 0.849] | 17/30 (0.567) [0.400, 0.733] |
| Model fit through Nov 13 | 65/468 (0.139) [0.103, 0.179] | 185/468 (0.395) [0.330, 0.462] | 65/90 (0.722) [0.633, 0.822] | 65/83 (0.783) [0.690, 0.871] | 15/30 (0.500) [0.333, 0.667] |

No method was refit on this consensus. The earlier single-pull position experiments already failed to beat persistence on exact position. This diagnostic does not change that.

## Gate for 2026-11-17

Collect three pulls of the same 30 queries after this file is committed. Score them with:

```
python scripts/score_consensus.py \
  --pull <pull-a>/serp_serper_live.csv \
  --pull <pull-b>/serp_serper_live.csv \
  --pull <pull-c>/serp_serper_live.csv \
  --previous <2026-11-16 consensus or the 16:26 serp> \
  --history <2026-11-06> --history <2026-11-09> --history <2026-11-11> --history <2026-11-13> --history <2026-11-16> \
  --model evals/preregistered/forward-pair-2026-11-16/model-next.json \
  --expect-sha256 bc4de5022a44c91bdb481c46f28c5374ee5477d1990d55931a21a4ce0e2a8ea7 \
  --label PREDICTED
```

The score is valid when all of these hold:

1. The model hash matches the hash above.
2. Every URL in the union of the three pulls is scored. A missing HTML file is not a reason to drop a URL.
3. The printout has the leave-one-out ceiling, persistence, and this model, with query-bootstrap intervals.
4. The label is PREDICTED.

A claim that the model beats persistence on 2026-11-17 requires a higher point estimate than persistence on top-3 recall or on number-one accuracy, on the leave-one-out consensus. Exact position and 3-wide coverage are reported. They are not a pass line. The 85% exact and 95% 3-wide targets from the single-pull work are not this gate: the inter-pull ceiling on 2026-11-16 is 0.310 exact and 0.518 3-wide, so those targets sit above the agreement of two real pulls.

Do not refit on the 2026-11-17 pulls and then quote the refit as this gate.
