# New-page scores, Nov 6 frozen, Nov 9 outcome

Label: PREDICTED. One interval, about 2.5 days, the same 30 queries. This is not a general forecast. The ranker is unchanged.

Machine-readable copy: `new_page_report.json`. Predictions: `new_page_ledger.json` (a new chain, 897 PREDICTED rows, confidence UNSURE, protocol time 2026-11-07T04:00:00Z, chain head `d6e51fedbecba9485fd37db13587b996b3efeef99043c466977a126c91877f8c`). The Nov 9 ledger file is untouched.

## Why draft versus draft was 34/116

A draft here is a Nov 9 URL with no Nov 6 position on that query and no page stored on Nov 6. There are 63 such rows. 34 have Nov 9 text used as the page a user would paste. 29 have no HTML. 16 queries have at least one draft-versus-draft pair (116 pairs).

The comparator treats a higher score as the better rank. On the full Nov 9 list LambdaMART is right on 680 pairs and wrong on 557 (104 ties). The forward CSV matches the ledger outcomes on every row (0 mismatches). The draft miss is not a flipped label.

The 116 pairs split three ways:

| Pair | Right | Wrong | Ties |
| --- | --- | --- | --- |
| Both pages have no HTML | 0 | 0 | 28 |
| One page has text, one does not | 22 | 44 | 0 |
| Both pages have text | 12 | 10 | 0 |

The 28 ties are the missing-page score, the same number on every page with no HTML. Ties sit in the denominator and are not counted right, so a coin flip on the other 88 pairs would land at 44/116 (0.379). The observed 34/116 (0.293) is lower than that because the missing-page score always ranks the fetched page above the page with no HTML, and the pages with no HTML actually rank better on average (mean Nov 9 position 5.448 on 29 rows, versus 7.441 on 34 fetched drafts). That one rule is wrong on 44 of the 66 mixed pairs and right on 22.

Both-have-text is 12/22 (0.545). That is the stand-in for a pasted draft. It is a small count.

One query, "all in one dirtwork software", is 10/45. Dropping it leaves 24/71 (0.338). Six queries have at least 3 draft pairs, and all six are under half. The result is spread across queries.

A domain prior can see some of these hosts. 37 of the 63 draft rows use a domain that already appeared on a different Nov 6 query. 26 use a domain that is absent from the Nov 6 file.

## Protocol, fixed before these scores

Queries are grouped with the existing family rule (content-token Jaccard, and every "alternative" query together). Four families. Each family is scored from the other families' Nov 6 rows only. Nov 9 positions are the outcome. They are not features, blend-weight labels, or thresholds.

Three scorers:

1. Domain prior. Mean Nov 6 relevance (11 minus position) of the host on the other families. An unseen host gets that training mean.
2. Content and query match. Within the query, the z-score of URL-token overlap with the query, plus the z-score of the frozen BM25. Missing text adds 0. The missing-page sentinel is not used.
3. Calibrated blend. The same URL and BM25 terms, with the content weight fixed at 1, and a domain weight in {0, 0.25, 0.5, 0.75, 1} chosen on Nov 6 family-out order. The four held-out weights were 0.75, 0.5, 0.5, and 0.75.

Intervals are a query bootstrap: 2000 resamples, seed 20261110, the 2.5 and 97.5 percentiles. A query's pairs stay together. Random order is right on half of pairs in expectation, its expected Spearman is 0, and its mean NDCG@10 on these lists is 0.590.

A scorer is adopted into the pasted-draft path only when both text slices (draft versus draft, and draft versus a page that already ranked) clear LambdaMART by 0.05, are at least BM25, and the bootstrap interval of the gain sits above 0. The same rule on the slices that still include pages with no HTML is recorded and does not by itself change the pasted path. Nothing cleared either rule.

## Held-out results

Pairwise counts every ordered pair. A tie is in the denominator and is not correct. NDCG@10 and Spearman are means over the 30 queries.

| Scorer | Draft vs draft | Draft vs draft, both have text | Draft vs already ranking | Draft vs already ranking, draft has text | All pairs | NDCG@10 | Spearman |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Random | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 | 0.590 | 0.000 |
| LambdaMART | 34/116 (0.293) [0.229, 0.389] | 12/22 (0.545) [0.286, 0.778] | 134/281 (0.477) [0.408, 0.554] | 87/167 (0.521) [0.453, 0.601] | 680/1341 (0.507) [0.448, 0.568] | 0.715 [0.655, 0.779] | 0.096 [-0.020, 0.215] |
| BM25 | 37/116 (0.319) [0.238, 0.490] | 15/22 (0.682) [0.455, 0.917] | 118/281 (0.420) [0.350, 0.504] | 71/167 (0.425) [0.348, 0.517] | 596/1341 (0.444) [0.393, 0.501] | 0.604 [0.556, 0.656] | -0.045 [-0.157, 0.078] |
| Domain prior | 46/116 (0.397) [0.333, 0.630] | 9/22 (0.409) [0.211, 0.591] | 124/281 (0.441) [0.371, 0.519] | 79/167 (0.473) [0.389, 0.579] | 671/1341 (0.500) [0.448, 0.554] | 0.617 [0.593, 0.641] | 0.214 [0.099, 0.336] |
| Content and query match | 60/116 (0.517) [0.443, 0.667] | 14/22 (0.636) [0.400, 0.857] | 121/281 (0.431) [0.343, 0.516] | 70/167 (0.419) [0.307, 0.525] | 672/1341 (0.501) [0.461, 0.543] | 0.561 [0.528, 0.600] | 0.065 [-0.041, 0.173] |
| Calibrated blend | 62/116 (0.534) [0.439, 0.672] | 14/22 (0.636) [0.400, 0.864] | 135/281 (0.480) [0.415, 0.544] | 83/167 (0.497) [0.409, 0.581] | 719/1341 (0.536) [0.500, 0.573] | 0.592 [0.553, 0.637] | 0.127 [0.027, 0.232] |

Ties inside those fractions: LambdaMART draft versus draft 28, both-have-text 0, versus ranking 29, text draft versus ranking 0. BM25: 29, 1, 29, 0. Domain prior: 46, 10, 53, 32. Content and query match: 9, 1, 15, 0. Blend: 4, 0, 8, 0.

Content-and-query-match raises draft-versus-draft by 0.224, and that gain interval is [0.068, 0.379]. The same rule drops draft-versus-ranking from 134/281 to 121/281. The gate requires both slices. It is rejected. The blend does the same thing (62/116, gain interval [0.084, 0.400], and 135/281 against a bar of 0.527). On the 22 text pairs, BM25 is 15/22 and both new text rules are 14/22. Those gain intervals include zero.

The ledger omits ties, so its rates are higher than the pairwise rates. Resolved calls: domain prior 671/1144, content and query match 672/1283, blend 719/1317.

## Decision

Rejected for the pasted-draft path. Rejected for the slices that include pages with no HTML. `ranker_change` is `none`.

## Next

Take the next Serper snapshot of these same 30 queries, and score new pages that have text as their own line. Twenty-two text-versus-text pairs are too few to adopt a ranker. Keep the no-HTML pairs in the report so the missing-page score stays visible, and keep them out of the pasted-draft decision.
