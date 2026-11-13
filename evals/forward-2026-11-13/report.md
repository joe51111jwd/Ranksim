# Nov 13 full-coverage score

Label: PREDICTED for every score. Label: REAL for every outcome position. `ranker_change` is `none`.

The scorer, coefficients, gate text, and frozen files are the bytes from freeze commit `3ad26130b1d093d64b21c08f8907b789ed61f37e`. This run did not refit them. One snapshot of these 30 queries is not a general forecast of Google. The gate is a point comparison on this file. It does not ask for a bootstrap interval above zero. A pass on one 30-query snapshot can be noise.

## Gate

HTML was passed, so the pre-registered combined line uses BM25F on new URLs. The frozen weight is 1. A missing page adds 0. Survivor scores do not read BM25F. With `survivor_history_weight` at 0, a survivor's score is the negative of its 2026-11-11 position. This is the gate-defining configuration in the frozen text. The command was run once.

| Check | Result |
| --- | --- |
| All-pairs pairwise accuracy strictly greater than persistence-ranked-last | Pass. Combined 992/1332 (0.744745). Persistence-ranked-last 862/1332 (0.647147). |
| New-versus-surviving pairwise accuracy strictly greater than 0.5 | Pass. 354/522 (0.678161). |
| New-versus-surviving denominator at least 100 pairs | Pass. 522 pairs. |

Gate pass: true. The product ranker, the fitted parameters, and the frozen files stay as they were.

## Outcome

REAL Serper organic results, `gl=us`, `hl=en`, `num=10`, 30 calls, 30 credits, HTTP 200. Request window 2026-11-13T15:45:29-05:00 to 2026-11-13T15:46:33-05:00. Same 30 queries as 2026-11-06. 298 organic rows. Two queries returned 9 rows (`excavation software`, `excavation company software`). The scored CSV is `outcome_serp.csv` in this folder. sha256 `c90318e2dc3c386e36d93da0dee27cccbef58b8b34a91014743b3843c369585a`. It matches the slim file that was scored.

HTML stayed out of git. The snapshot manifest has 96 stored pages (92 distinct files: five SmartCrew URLs finished on the same final page). The loader attached those 96 URLs. 56 manifest URLs had no file (robots, challenge pages, HTTP 403, two SSL errors, one HTTP 429). Across the 298 REAL rows, 216 had HTML and 82 did not. Missing text is not a penalty. Its coefficient is 0.

The previous list is `evals/forward-2026-11-11/outcome_serp.csv`. It was history, not a fitting label.

## Persistence overlap, Nov 11 to Nov 13

194 of 296 Nov 11 rows are in the Nov 13 top 10 for the same query. 102 exited. 104 of 298 Nov 13 rows are new. On URLs that stayed, the Nov 11 order matches the Nov 13 order on 518/605 pairs (0.856198), 87 wrong, 0 ties. Those 605 pairs are 605/1332 of the Nov 13 ordered pairs (0.454204). Row coverage of the Nov 13 list is 194/298 (0.651007).

The combined survivor score is that same Nov 11 order, so combined and persistence-ranked-last match on every surviving-versus-surviving pair.

## Pairwise table

Pairwise accuracy is correct / (correct + wrong + ties). A score tie is not correct. Pairs with equal REAL positions are not in the denominator. Every position inside a query was distinct, so the denominator is every ordered pair. NDCG@10 and mean Spearman are secondary. They are means over the full top 10, not sliced by pair type. Higher scores are better, so agreement is positive. Frozen LambdaMART is not in this command. The booster and the training HTML are not in git, and the command does not refit.

| Pair type | Combined (PREDICTED) | Persistence-ranked-last | Frozen LambdaMART |
| --- | --- | --- | --- |
| All | 992/1332 (0.744745), 337 wrong, 3 ties. NDCG@10 0.855685. Spearman 0.586086. 30 queries. | 862/1332 (0.647147), 265 wrong, 205 ties. NDCG@10 0.853605. Spearman 0.556366 on 29 queries. | Not scored |
| Surviving vs surviving | 518/605 (0.856198), 87 wrong, 0 ties. 28 queries. | 518/605 (0.856198), 87 wrong, 0 ties. 28 queries. | Not scored |
| New vs surviving | 354/522 (0.678161), 168 wrong, 0 ties. 29 queries. | 344/522 (0.659004), 178 wrong, 0 ties. 29 queries. | Not scored |
| New vs new | 120/205 (0.585366), 82 wrong, 3 ties. 26 queries. | 0/205 (0.000000), 0 wrong, 205 ties. 26 queries. | Not scored |

Coverage: 298/298 REAL rows scored. Surviving rows 194. New rows 104. Domain seen on 81 of the 104 new rows. Spearman for persistence-ranked-last skips `best all-in-one dirt work and excavation software`, where all 10 URLs are new and share the score -1000.

The all-pairs gap versus persistence-ranked-last is 130 correct pairs (992 versus 862). 120 of those are new-versus-new, where persistence-ranked-last ties every pair and those ties sit in the denominator. 10 are new-versus-surviving (354 versus 344). Surviving-versus-surviving adds none.

BM25F on the same HTML, from the same run, is a baseline and not a gate scorer. All pairs 543/1332 (0.407658), 679 wrong, 110 ties, NDCG@10 0.562746, Spearman -0.139440. Surviving vs surviving 248/605 (0.409917), 61 ties. New vs surviving 210/522 (0.402299), 31 ties. New vs new 85/205 (0.414634), 18 ties.

## Ledger

`evals/preregistered/next-snapshot/track-record.jsonl`. 598 rows. 299 PREDICTED (one run header plus 298 score rows, confidence UNSURE). 298 REAL outcome rows. One SCORE row with the metrics and the gate. Chain head `57d0d8b34ced931a97be93c701b616131466ad3087f590cb72933afc5a5e8a40`. Created 2026-11-13T20:52:08Z. Snapshot sha256, params sha256, and scorer sha256 match the freeze manifest. No HTML and no key are in the ledger.

Scored once. A second run of the command would append another ledger chain. It would not be a new pre-registered test.
