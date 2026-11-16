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
