# Pre-registration: monotone LambdaMART for the next snapshot

Label: PREDICTED. This note freezes the ranker that won the early-window rule in `evals/experiments-2026-11-16/log.md`. It was not chosen because it was the highest number on 2026-11-16. That highest number, across 21 families, is optimistic and is not this model.

The product ranker is not changed by this file. Nothing here is a deploy.

## What is frozen

`scripts/score_forward_pair.py` loads `model-next.json` and does not refit. `manifest.json` records the sha256 of that file, of the two held-out fits, and of `offpage.tsv`. The command refuses a frozen filename whose bytes do not match the manifest.

- `model-heldout-nov13.json` was fit on transitions through 2026-11-11. It is the model that produced the 2026-11-13 number below.
- `model-heldout-nov16.json` was fit on transitions through 2026-11-13. It is the model that produced the 2026-11-16 number below.
- `model-next.json` was fit on transitions through 2026-11-16. It is the model for the next snapshot. It has seen the 2026-11-16 labels, so it must not be quoted as the source of the 2026-11-16 number.

The family is monotone LambdaMART: 80 trees, 7 leaves, learning rate 0.05, at least 20 rows in a leaf, L2 2, one thread, seed 0. Features that should rise with relevance are constrained to do so. The feature list is history of earlier snapshots, on-page text when HTML exists, Tranco, Majestic Million, and Common Crawl domain ranks from `cc-main-2026-may-jun-jul` (file dated 2026-07-28, before these snapshots). A missing page still gets a score. Its on-page features are zero and `content_present` is 0. The SERP title is still used.

`offpage.tsv` stores the Tranco popularity (`1/log2(rank+1)`), Majestic `log1p(referring subnets)`, and the Common Crawl values for domains that appeared in the five snapshots. A domain absent from that table is not listed. Pass `--tranco`, `--majestic`, or `--cc` to look up a domain the next snapshot introduces. Open PageRank was not called. No paid API is called.

## Held-out numbers, already observed

These are forward tests. They are not the gate.

| Eval | Train through | Combined | 95% CI by query | New vs new | New vs surviving | Surviving vs surviving | Persistence-ranked-last |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-13 | 2026-11-11 | 1016/1332 (0.762763) | [0.716, 0.805] | 123/205 (0.600000) | 386/522 (0.739464) | 507/605 (0.838017) | 862/1332 (0.647147) |
| 2026-11-16 | 2026-11-13 | 1039/1341 (0.774795) | [0.720, 0.823] | 159/242 (0.657025) | 299/415 (0.720482) | 581/684 (0.849415) | 856/1341 (0.638330) |

90% was not reached. On 2026-11-16, 90% would be 1207/1341. The selected model is 168 pairs short. Surviving URLs already agree with the previous day on 595/684 pairs (0.869883). Among survivor pairs that flipped from one snapshot to the next and were still both present on the snapshot after that, 34% to 44% flipped back. That reshuffle is not a stable direction the page text can learn. New-vs-new on the selected model is 0.657, up from 0.574 for the published frozen line on the same pairs, and still far from the roughly 0.93 new-URL accuracy that 90% combined would require if survivor pairs stayed at 0.870.

## Gate for the next snapshot

The next file is a new Serper top 10 of these same 30 queries, collected after this note. Score it with `model-next.json` and with history that ends in the 2026-11-16 snapshot. Do not refit.

The gate passes only when all four are true:

1. On all ordered pairs in the new top 10, pairwise accuracy is strictly greater than persistence-ranked-last on the same pairs. Pairwise accuracy is correct / (correct + wrong + ties). A score tie is not correct. Pairs with equal outcome positions are not in the denominator.
2. New-versus-new pairwise accuracy is strictly greater than 0.5.
3. New-versus-surviving pairwise accuracy is strictly greater than 0.5.
4. Every organic row in the new file receives a score. A missing HTML file is not a reason to drop a row.

Persistence-ranked-last scores a URL that was in the immediately previous snapshot at the negative of that position. A URL that was absent scores -1000, tied with every other absent URL and below every survivor.

A pass does not change the product ranker. One snapshot of these 30 queries is not a general forecast of Google. The bootstrap interval on 2026-11-16 was [0.720, 0.823]. A later snapshot inside that range is not evidence the model moved. This gate does not require the point estimate to beat 0.775 or to reach 0.90.

```
python scripts/score_forward_pair.py \
  --history path/to/2026-11-06 \
  --history path/to/2026-11-09 \
  --history path/to/2026-11-11 \
  --history path/to/2026-11-13 \
  --history path/to/2026-11-16 \
  --snapshot path/to/NEXT \
  --offpage-cache evals/preregistered/forward-pair-2026-11-16/offpage.tsv
```

## Position metrics

The same command prints exact position, within one position, mean absolute error, range coverage, and confident #1. Label: PREDICTED. Every organic row is counted.

The point prediction frozen from the early windows is yesterday's position for a URL that was already listed, and position 10 for a new URL. Monotone LambdaMART ranks are printed beside that. They lost the early-window comparison on exact hits.

Ranges are at most 3 positions wide. Offsets in `position-calibration.json` were chosen to reach 95% coverage on Nov 9 query-group residuals and Nov 11 forward residuals. A 90% rule is stored and printed as a reference. On those calibration rows the widest legal range covers 0.647, so the 95% rule and the 90% rule are the same offsets. Nov 13 and Nov 16 did not choose them.

Held-out coverage of that frozen range is 187/298 (0.628) on Nov 13 and 180/299 (0.602) on Nov 16. Exact hits are 100/298 (0.336) and 106/299 (0.355) for the persistence point, and lower for the ranker. Within one position is about 0.57 to 0.58. Mean absolute error is about 2.1 positions for persistence and about 1.6 for the ranker. The 85% exact target and the 95% range target are not met.

Confident #1 uses a score gap of 0.7528, the best early-window precision (0.829). The 0.90 precision target was not reached on the early windows, so the stricter threshold is the same gap. On Nov 16 that call is right on 11/19 queries (0.579) and is made on 19/30 queries. Naming yesterday's #1 is right on 17/30 (0.567) and is made on every query.

Across the five snapshots the #1 URL is the same on the next day for 22/30, 21/30, 16/30, and 17/30 queries. The #2 URL stays #2 on 13/30 to 16/30. The #3 URL stays #3 on 10/30 to 14/30. A top-3 URL is still in the top 3 on the next day about 60% to 67% of the time. The top rank moves often enough that it is not a fixed lock. #2 and #3 are not stable.

The position-range gate on the next snapshot passes only when all of these are true:

1. The pairwise gate above passes.
2. Every organic row receives a point and a range.
3. Every range is at most 3 positions wide.
4. The share of rows whose real position falls inside the frozen 95% range is at least 0.95.

The 90% range is reported and is not the gate. Exact-position accuracy, mean absolute error, and confident-#1 precision are reported with the persistence baseline. They are not a second cutoff. One snapshot of these 30 queries is not a general forecast of Google. The Nov 13 and Nov 16 files do not clear the 95% range bar.
