# Serper scrape fill, Nov 6 frozen, Nov 9 outcome

Label: PREDICTED. One interval, about 2.5 days, the same 30 queries. This is not a general forecast. The ranker is unchanged.

The gate was committed in `bb18150` before these scores were computed. The rules are the same as `6a0910a`. Every draft slice must clear. Machine-readable copy: `scrape_report.json`.

## What was filled in

A Nov 10 Serper scrape covered 64 unique URLs that had no stored HTML on Nov 6 or Nov 9. The raw files are text and metadata (title, description), not HTML. The importer reads that folder locally. It does not call Serper and it does not need a key. Source is `serper_scrape`. Capture date is 2026-11-10.

Usable text is longer than 500 characters. 61 of 64 URLs cleared that bar. Three did not: `https://help.hcss.com/s/article/VIDEO-How-to-Setup-Package-Alternates-in-HeavyBid` (456 characters), `https://www.facebook.com/61590479262574/posts/running-your-dirt-company-off-of-text-messages-sticky-notes-and-3-apps-that-dont/122156726283349308/` (74 characters), and a Quora URL whose scrape returned an error and no text. Those three stay unknown content.

Caveat: the words were captured on Nov 10. That is three to four days after the Nov 6 SERP and one day after the Nov 9 SERP. The text can differ from the page Google ranked.

## Coverage

Training stayed on the original 215 Nov 6 HTML rows. No scrape URL was a training row.

At score time, of 299 Nov 9 rows: 183 used Nov 6 HTML, 35 used the original Nov 9 fetch, 78 used scrape text, and 3 had no usable text. A URL on more than one query is counted once per query, which is why 61 URLs become 78 rows. Pairwise is still 1,341 pairs over 30 queries. Persistence is still 931/1341, so the candidate set did not change.

## Protocol

LambdaMART is fit on the original Nov 6 HTML only. Nov 9 positions are the outcome. They are not features or weights.

A page with HTML or usable scrape text keeps its model score. A page with no usable text has `content_missing` 1 and the unknown-content score from `6a0910a` (Nov 6 training-score mean, shifted by off-page evidence, family-out domain prior). It is not the old shared floor.

Intervals are a query bootstrap: 2000 resamples, seed 20261110. NDCG@10 uses that seed. Spearman uses seed 20261111.

Promote only when every slice below beats the published LambdaMART rate by 0.05, is at least BM25 on that slice, and the bootstrap interval of (new rate minus the published rate) has its lower end above zero. Published counts: draft versus draft 34/116, both have text 12/22, draft versus already ranking 134/281, draft has text 87/167.

## Scores

| Scorer | All pairs | NDCG@10 | Spearman |
| --- | --- | --- | --- |
| Published LambdaMART | 680/1341 (0.507) | 0.715 | 0.096 |
| LambdaMART, this run | 909/1341 (0.678) [0.633, 0.721] | 0.798 [0.736, 0.857] | 0.432 [0.317, 0.545] |
| BM25, this run | 734/1341 (0.547) [0.510, 0.587] | 0.665 [0.615, 0.717] | 0.142 [0.035, 0.253] |
| Persistence | 931/1341 (0.694) [0.605, 0.773] | 0.902 [0.848, 0.946] | 0.649 [0.544, 0.739] |
| Equal-weight blend | 923/1341 (0.688) [0.649, 0.729] | 0.846 [0.798, 0.890] | 0.495 [0.399, 0.589] |
| History-aware rule, not adopted | 1004/1341 (0.749) [0.704, 0.789] | 0.890 [0.843, 0.930] | 0.614 [0.510, 0.711] |

| Slice | Published LambdaMART | This run, LambdaMART | This run, BM25 |
| --- | --- | --- | --- |
| Draft vs draft | 34/116 (0.293) | 68/116 (0.586) [0.508, 0.717] | 57/116 (0.491) [0.411, 0.617] |
| Draft vs draft, both have text | 12/22 (0.545) | 68/116 (0.586) [0.519, 0.725] | 57/116 (0.491) [0.415, 0.636] |
| Draft vs already ranking | 134/281 (0.477) | 184/281 (0.655) [0.569, 0.736] | 143/281 (0.509) [0.421, 0.597] |
| Draft vs already ranking, draft has text | 87/167 (0.521) | 184/281 (0.655) [0.568, 0.735] | 143/281 (0.509) [0.418, 0.593] |

Draft versus draft and both-have-text are the same 68/116. The scrape put text on the drafts that had none, so the text slice grew from 22 pairs to 116. There are no ties in the LambdaMART draft slices.

## Decision

Not adopted. Both-have-text is 0.586. The bar is the published 0.545 plus 0.05, which is 0.595. The gain interval is [-0.038, 0.172]. That slice is above BM25 (0.491), and the other three slices cleared their bars. The gate requires every slice. `ranker_change` is `none`.
