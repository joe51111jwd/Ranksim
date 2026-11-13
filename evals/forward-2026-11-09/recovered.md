# Recovered-content forward scores, Nov 6 frozen, Nov 9 outcome

Label: PREDICTED. One interval, about 2.5 days, the same 30 queries. This is not a general forecast. The ranker is unchanged.

The gate and the holdout were committed in `6a0910a` before these scores were computed. The browser wait cap is `4391bb5` and does not change the score rule. Machine-readable copy: `recovered_report.json`.

## Pages recovered

Missing means the snapshot manifest has no HTML file. HTTP ran first. Headless Chromium ran only after HTTP failed. Serper scrape did not run: `SERPER_API_KEY` was unset and the credit cap was 0.

| Snapshot | Missing URLs | Recovered | HTTP | Browser | Still missing |
| --- | --- | --- | --- | --- | --- |
| Nov 6 | 46 | 3 | 2 | 1 | 43 |
| Nov 9 | 52 | 4 | 2 | 2 | 48 |
| Unique URLs | 65 | 5 | 3 | 2 | 60 |

A URL in both snapshots is counted on each day and once in the unique row. The five pages are:

- HTTP: `https://thedigitalprojectmanager.com/tools/best-construction-project-management-software/` (6,691 words)
- Browser: `https://www.sage.com/en-us/sage-construction/heavy-civil/` (2,165 words)
- HTTP: `https://topconsolutions.com/dirt-logic/` (311 words)
- HTTP: `https://meanjin.com.au/fiction/dirt-work/` (252 words)
- Browser: `https://www.zoho.com/crm/customers/dirt-rocks.html` (892 words)

Of the 60 that stayed missing, 36 were blocked by robots.txt for the bot and for the browser user agent. 12 were HTTP challenge pages, and the browser still got a challenge. 12 were HTTP 403, and the browser still got a challenge. Those reasons are on each row in `recovered_report.json`.

At score time, 10 Nov 9 rows used recovered HTML (some URLs appear on more than one query). 71 rows still had no HTML. 183 used Nov 6 HTML. 35 used the original Nov 9 fetch. Training stayed on the original 215 Nov 6 rows. Recovered HTML was not a training row.

## Off-page sources

Used, no signup key:

- Majestic Million referring subnets, 52 of 84 domains. Free CSV, CC BY 3.0. https://downloads.majestic.com/majestic_million.csv
- Tranco popularity, 50 of 84 domains. This is not a backlink count. https://tranco-list.eu/top-1m.csv.zip

Wired, not used in the numbers:

- Common Crawl domain PageRank. The public file is free and keyless and is multiple gigabytes, so it was not downloaded. A local file still loads through `load_cc_pagerank`.
- Open PageRank. Free signup key at https://www.domcop.com/openpagerank/. The client stays off until `OPEN_PAGERANK_API_KEY` is set.

Not wired, because each needs a free signup key:

- Moz Link API: https://moz.com/products/api
- Majestic API beyond the Million CSV: https://developer.majestic.com/

Clicks, impressions, and on-site behavior are not available. They are not in these scores.

## Protocol, fixed before these scores

LambdaMART is fit on the original Nov 6 HTML only. Nov 9 positions are the outcome. They are not features or weights.

A page with HTML keeps its model score. A page with no HTML has `content_missing` 1. Its BM25 and LambdaMART ordering scores are the mean of the Nov 6 training scores, shifted by the average within-query z-score of the off-page parts that exist: domain prior, URL overlap, Majestic referring subnets, and Tranco popularity. A missing part adds nothing. The domain prior for a query uses Nov 6 rows from the other query families only. This is not the old shared floor of -1e9.

Intervals are a query bootstrap: 2000 resamples, seed 20261110, the 2.5 and 97.5 percentiles. A query's pairs stay together. NDCG@10 uses that seed. Spearman uses seed 20261111.

A change is adopted only when every draft slice below beats the published LambdaMART rate by 0.05, is at least BM25 on that same slice, and the bootstrap interval of (new rate minus the published rate) has its lower end above zero. The published rates are the committed counts: draft versus draft 34/116, both have text 12/22, draft versus already ranking 134/281, draft has text 87/167.

## Scores

Pairwise counts every ordered pair. A tie is in the denominator and is not correct. 1,341 pairs, 30 queries.

| Scorer | All pairs | NDCG@10 | Spearman |
| --- | --- | --- | --- |
| Published LambdaMART | 680/1341 (0.507) | 0.715 | 0.096 |
| LambdaMART, this run | 885/1341 (0.660) [0.624, 0.691] | 0.777 [0.724, 0.828] | 0.413 [0.328, 0.488] |
| BM25, this run | 678/1341 (0.506) [0.466, 0.547] | 0.626 [0.577, 0.677] | 0.029 [-0.079, 0.139] |
| Persistence | 931/1341 (0.694) [0.605, 0.773] | 0.902 [0.848, 0.946] | 0.649 [0.544, 0.739] |
| Equal-weight blend | 934/1341 (0.696) [0.655, 0.738] | 0.836 [0.782, 0.885] | 0.535 [0.440, 0.627] |
| History-aware rule, not adopted | 1009/1341 (0.752) [0.701, 0.800] | 0.879 [0.828, 0.925] | 0.625 [0.518, 0.724] |

Persistence is the same 931/1341 as the published run. The candidate set is the same. Blend moved from 942/1341 to 934/1341, and the history-aware rule from 1007/1341 to 1009/1341, because a few recovered pages now have a BM25 value.

| Slice | Published LambdaMART | This run, LambdaMART | This run, BM25 |
| --- | --- | --- | --- |
| Draft vs draft | 34/116 (0.293) | 76/116 (0.655) [0.393, 0.760] | 59/116 (0.509) [0.427, 0.611] |
| Draft vs draft, both have text | 12/22 (0.545) | 18/31 (0.581) [0.353, 0.727] | 19/31 (0.613) [0.482, 0.867] |
| Draft vs already ranking | 134/281 (0.477) | 182/281 (0.648) [0.578, 0.713] | 149/281 (0.530) [0.466, 0.598] |
| Draft vs already ranking, draft has text | 87/167 (0.521) | 111/176 (0.631) [0.556, 0.697] | 84/176 (0.477) [0.401, 0.561] |

The text slices grew because recovered pages entered them. 18/31 is not 12/22 with a few extra right answers on the same pairs.

## Decision

Not adopted. Draft versus draft, both with text, is 0.581. The bar is the published 0.545 plus 0.05, which is 0.595. The same slice is below BM25 at 0.613. The gain interval is [-0.212, 0.182]. The other three draft slices cleared their bars. The gate requires every slice. `ranker_change` is `none`.

The all-pairs move from 0.507 to 0.660 is mostly the 71 rows that still have no HTML. They no longer share one score under every fetched page. That is the unknown-content rule. It is not a pasted-draft win.
