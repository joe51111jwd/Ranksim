# Nov 11 forward test

Label: PREDICTED. The ranker is unchanged. `ranker_change` is `none`.

The predictions were committed in `8365d68` before this outcome was opened. This file only scores those rows. It does not refit LambdaMART and it does not move a threshold.

One interval on the same 30 US English queries (`gl=us`, `hl=en`, `num=10`). Not a general forecast.

## Outcome

Serper organic results from 2026-11-11T09:34:22-05:00 to 2026-11-11T09:35:31-05:00. 296 organic rows, 135 unique URLs, 91 HTML files saved outside git. The position list in git is `outcome_serp.csv` (query, position, link only). Full SERP sha256 `e4c76d7336febb64e7e54d62f00f7b0a90adfe92f7110934e82d5bd9ca51767b`.

## Primary metric

All ordered pairs among URLs that were in the pre-registered candidate list and also in the Nov 11 top 10 for that query. A score tie is not correct. Spearman and NDCG@10 are secondary. No scorer cleared the pre-registered bar (beat persistence by 0.05, with the bootstrap gain interval above zero, and, for LambdaMART and the blend, stay at least at BM25). History-aware was not eligible.

The supplied overlap check is reproduced: the Nov 9 order matches Nov 11 on 625/724 pairs (0.863260), and 208 of 299 Nov 9 rows persisted. There were 0 ties in that overlap.

## Nov 9 to Nov 11

Protocol time 2026-11-10T04:00:00Z. LambdaMART was retrained on 215 Nov 6 rows and 218 Nov 9 rows. Both fits kept 40 trees. Persistence is the Nov 9 order. 28 queries had at least two surviving URLs.

| Scorer | Overlap pairs | NDCG@10 | Spearman |
| --- | --- | --- | --- |
| LambdaMART | 508/724 (0.701657), 210 wrong, 6 ties | 0.860142 | 0.485835 |
| BM25 | 321/724 (0.443370), 397 wrong, 6 ties | 0.625496 | -0.056863 |
| Persistence | 625/724 (0.863260), 99 wrong, 0 ties | 0.935011 | 0.714402 |
| Blend | 545/724 (0.752762), 179 wrong, 0 ties | 0.851061 | 0.542440 |
| History-aware, not eligible | 609/724 (0.841160), 115 wrong, 0 ties | 0.933287 | 0.691512 |

Coverage: 208 of 299 Nov 9 rows are in the Nov 11 top 10. 91 exited. 88 of 296 Nov 11 rows were not in the Nov 9 list, so they are unpredicted. Scored pairs are 724 of 1314 Nov 11 pairs (0.550989). Row coverage is 208/296 (0.702703).

Ledger calls for this window resolve only when both URLs are in the Nov 11 list. Persistence is 625 of 724 resolved calls (0.863260), with 617 unresolved. LambdaMART is 508 of 718 resolved calls (0.707521). The 6 ties are in the primary denominator and are not ledger calls. 299 PREDICTED rows per scorer.

## Nov 6 to Nov 11

Protocol time 2026-11-07T04:00:00Z. Frozen on Nov 6 data only: 215 training rows, Nov 6 HTML, Nov 6 order. 30 queries had at least two surviving URLs.

| Scorer | Overlap pairs | NDCG@10 | Spearman |
| --- | --- | --- | --- |
| LambdaMART | 396/583 (0.679245), 179 wrong, 8 ties | 0.851800 | 0.419675 |
| BM25 | 252/583 (0.432247), 322 wrong, 9 ties | 0.666012 | -0.024620 |
| Persistence | 483/583 (0.828473), 100 wrong, 0 ties | 0.918977 | 0.621696 |
| Blend | 417/583 (0.715266), 166 wrong, 0 ties | 0.847186 | 0.473189 |
| History-aware, not eligible | 461/583 (0.790738), 122 wrong, 0 ties | 0.907474 | 0.594675 |

Coverage: 192 of 294 Nov 6 rows are in the Nov 11 top 10. 102 exited. 104 of 296 Nov 11 rows were not in the Nov 6 list, so they are unpredicted. Scored pairs are 583 of 1314 Nov 11 pairs (0.443683). Row coverage is 192/296 (0.648649).

## Ledger

2,965 PREDICTED rows. That count and the prediction hashes are the same as in `8365d68`. Chain head `738daf5722eb5861767e80d9843d93f4a38901ec8750105fcfd1ecc0865753b9`. 300 REAL outcome rows were appended after the outcome was opened (30 queries, 5 scorers, 2 windows). The Nov 9 ledger file was not edited.

## Next snapshot

Do not retune LambdaMART, the blend, or the history-aware scale on these labels. Persistence still orders the URLs that stayed, and none of the content scorers cleared the bar.

The open gap is the URLs that were not on the previous list. This run left 88 of 296 Nov 11 rows unpredicted. The next pre-registration should score those entrants as their own line, using only page text captured before the outcome, and should keep this overlap metric separate. The Nov 10 scrape stayed out of this freeze. A later pre-registration can allow score-time text captured before the prediction clock. It should still stay out of the training labels.
