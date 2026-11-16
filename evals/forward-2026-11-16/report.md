# 2026-11-16 full-coverage score

Label: PREDICTED for every score. Label: REAL for every outcome position. `ranker_change` is `none`.

The scorer, coefficients, and gate text are the frozen full-coverage line. This run did not refit them. The frozen command hardcodes 2026-11-11 as the previous snapshot. The input path here appends the 2026-11-13 position file after the frozen Nov 6, Nov 9, and Nov 11 lists. Persistence is the negative of the 2026-11-13 position. Domain history includes that before file because it is history, not the outcome. That path change is outside `full_coverage.py`.

## Gate

The pre-registered rule is unchanged. A pass does not change the product ranker, the fitted parameters, or the frozen files.

Gate pass: true. ranker_change: none.

| Check | Result |
| --- | --- |
| All-pairs pairwise accuracy strictly greater than persistence-ranked-last | True. Combined 1016/1341 (0.757644), 322 wrong, 3 ties. 30 queries. Persistence-ranked-last 856/1341 (0.638330), 243 wrong, 242 ties. 30 queries. |
| New-versus-surviving pairwise accuracy strictly greater than 0.5 | True. 282/415 (0.679518), 133 wrong, 0 ties. 25 queries. |
| New-versus-surviving denominator at least 100 pairs | True. 415 pairs. |

## Pairwise table

Pairwise accuracy is correct / (correct + wrong + ties). A score tie is not correct. Pairs with equal REAL positions are not in the denominator. NDCG@10 and mean Spearman are means over queries, secondary to the gate. Higher PREDICTED scores are better. Frozen LambdaMART is not in this command.

| Pair type | Combined (PREDICTED) | Persistence-ranked-last |
| --- | --- | --- |
| All | 1016/1341 (0.757644), 322 wrong, 3 ties. 30 queries. NDCG@10 0.852882. Spearman 0.627421. | 856/1341 (0.638330), 243 wrong, 242 ties. 30 queries. NDCG@10 0.837650. Spearman 0.544684. |
| Surviving vs surviving | 595/684 (0.869883), 89 wrong, 0 ties. 27 queries. | 595/684 (0.869883), 89 wrong, 0 ties. 27 queries. |
| New vs surviving | 282/415 (0.679518), 133 wrong, 0 ties. 25 queries. | 261/415 (0.628916), 154 wrong, 0 ties. 25 queries. |
| New vs new | 139/242 (0.574380), 100 wrong, 3 ties. 19 queries. | 0/242 (0.000000), 0 wrong, 242 ties. 19 queries. |

## Coverage

REAL rows scored: 299/299. Surviving rows 199. New rows 100. Domain seen on 86 of the new rows. HTML rows 192. Queries 30.

Outcome pairs with a PREDICTED score: 1341/1341 (1.000000). This is the share of REAL ordered pairs that were scored.

BM25F on the supplied HTML is a baseline and not a gate scorer. All pairs 607/1341 (0.452647), 566 wrong, 168 ties. 30 queries. NDCG@10 0.609226. Spearman 0.029049. Surviving vs surviving 297/684 (0.434211), 302 wrong, 85 ties. 27 queries. New vs surviving 199/415 (0.479518), 155 wrong, 61 ties. 25 queries. New vs new 111/242 (0.458678), 109 wrong, 22 ties. 19 queries.

## Overlap

199 of 298 2026-11-13 rows are in the 2026-11-16 top 10 for the same query. 199 of 299 REAL rows were on that before list. Before keys 298. Outcome keys 299.

## Prior window check

The 2026-11-13 window was recomputed with the frozen history ending 2026-11-11 and the snapshot HTML. Pair counts, NDCG@10, and Spearman match the published report. The line was not refit. Those recomputed per-query counts are the first window of the pooled bootstrap.

## Pooled forward tests

The two windows are Nov 11 to Nov 13 and Nov 13 to Nov 16. Counts are PREDICTED full-coverage scores against REAL positions. The resample unit is the query. A draw takes 30 query names with replacement and keeps both windows of each drawn query together. Draws: 2000. Seed: 20261116. The rate pools pairs inside the draw. It is not the average of per-query rates. The 95% interval is the 2.5 and 97.5 percentiles of those draws, with the same linear percentile used by the new-page bootstrap.

Combined pairwise accuracy: 2008/2673 (0.751216). 95% CI 0.704319 to 0.797887.

Persistence-ranked-last: 1718/2673 (0.642724).

Margin, combined minus persistence-ranked-last: 0.108492. 95% CI 0.059505 to 0.162379.

The margin interval is above zero.

This interval was not part of the pre-registered gate. The gate above is the point comparison on the Nov 16 window only.

2026-11-11 to 2026-11-13: combined 992/1332 (0.744745); persistence-ranked-last 862/1332 (0.647147).
2026-11-13 to 2026-11-16: combined 1016/1341 (0.757644); persistence-ranked-last 856/1341 (0.638330).

## Domain context

smartcrewhq.com appears 29 times in the REAL top 10s. This is context from the outcome file. It was not used to fit, score, or decide the gate.

- query `all in 1 dirt work software` position 6 https://www.smartcrewhq.com/all-in-one-dirt-work/
- query `all in one dirt work management software` position 1 https://www.smartcrewhq.com/best-all-in-one-dirt-work-and-excavation-software/
- query `all in one dirt work management software` position 7 https://www.smartcrewhq.com/dirt-work-software-with-crm/
- query `all in one dirt work software` position 6 https://www.smartcrewhq.com/all-in-one-dirt-work/
- query `all in one dirtwork software` position 9 https://www.smartcrewhq.com/earthwork-software/
- query `all in one excavation software` position 1 https://www.smartcrewhq.com/best-all-in-one-dirt-work-and-excavation-software/
- query `all in one heavy civil software` position 4 https://www.smartcrewhq.com/heavy-civil-construction-software/
- query `all-in-one heavy civil construction software` position 2 https://www.smartcrewhq.com/all-in-one-heavy-civil-software/
- query `b2w alternative` position 5 https://www.smartcrewhq.com/compare/b2w-alternative/
- query `best all in 1 dirt work software` position 6 https://www.smartcrewhq.com/blog/best-excavation-software-20-50-employees/
- query `best all in one dirt work management software` position 1 https://www.smartcrewhq.com/best-all-in-one-dirt-work-and-excavation-software/
- query `best all in one dirt work management software` position 6 https://www.smartcrewhq.com/all-in-one-dirt-work/
- query `best all in one dirt work software` position 9 https://www.smartcrewhq.com/blog/best-excavation-software-20-50-employees/
- query `best all in one excavation software` position 1 https://www.smartcrewhq.com/best-all-in-one-dirt-work-and-excavation-software/
- query `best all in one excavation software` position 7 https://www.smartcrewhq.com/best-all-in-one-dirt-work-software/
- query `best all in one heavy civil software` position 7 https://www.smartcrewhq.com/all-in-one-heavy-civil-software/
- query `best all-in-one dirt work and excavation software` position 8 https://www.smartcrewhq.com/blog/best-excavation-software-20-50-employees/
- query `best all-in-one dirt work management software` position 3 https://www.smartcrewhq.com/blog/best-excavation-software-20-50-employees/
- query `best all-in-one dirtworks software` position 1 https://www.smartcrewhq.com/all-in-one-dirt-work-software/
- query `best all-in-one dirtworks software` position 2 https://www.smartcrewhq.com/all-in-one-dirt-work/
- query `best heavy civil construction software` position 7 https://www.smartcrewhq.com/heavy-civil-construction-software/
- query `dirt work contractor software` position 7 https://www.smartcrewhq.com/blog/best-software-small-dirt-work-contractors/
- query `dirt work management software` position 2 https://www.smartcrewhq.com/dirtwork-software/
- query `dirt work software with crm` position 2 https://www.smartcrewhq.com/dirt-work-software-with-crm/
- query `excavation contractor software` position 9 https://www.smartcrewhq.com/blog/best-work-order-software-excavation-contractors/
- query `hcss alternative` position 4 https://www.smartcrewhq.com/compare/hcss-alternative/
- query `hcss alternative` position 8 https://www.smartcrewhq.com/compare/hcss-heavybid-alternative/
- query `hcss heavybid alternative` position 6 https://www.smartcrewhq.com/compare/hcss-heavybid-alternative/
- query `hcss heavyjob alternative` position 3 https://www.smartcrewhq.com/compare/hcss-heavyjob-alternative/

## Outcome file

REAL Serper organic results. Requests 30. Credits 30. Statuses {"200": 30}. Request window 2026-11-16T08:37:44.801010-05:00 to 2026-11-16T08:39:03.847081-05:00. Outcome CSV sha256 `fe518b6ea1d0491bc1ea08f7e03cf7735745a63ac56de9d11be73576c0053043`. requests.jsonl sha256 `1f19983e24db727bdbf8cb919cd8a6c03cb39ef9af533d04ef576b68e2553a41`. The requests file is not committed. No API key is recorded here.

Queries that did not return 10 REAL rows: excavation software has 9 REAL rows.

Spearman is defined on 30 of 30 queries with at least two REAL rows.

HTML on disk: 85 distinct files, 89 manifest URLs with a file, 148 manifest rows. Manifest rows by recorded reason. An empty reason on a row that has a file is counted as file_present: {"challenge_page": 10, "error:SSLError": 1, "file_present": 89, "http_403": 7, "http_429": 9, "robots_disallow": 32}. The loader attached 89 URLs. REAL rows with HTML: 192. REAL rows with no HTML: 107. A missing page adds no BM25F push. Its frozen coefficient is 0.

Persistence-ranked-last Spearman is defined on 29 queries and undefined for: best all-in-one dirt work and excavation software. Full coverage Spearman is defined on 30 queries.

## Ledger

Appended to `evals/preregistered/next-snapshot/track-record.jsonl`. Rows 1199. Chain head `fa4929a575cb745cfe52269954477b4b75703183d43c4510d8383664806d3db2`. Earlier rows were not rewritten.
