# Pre-registration: full-coverage predictor for the next snapshot

Label: PREDICTED. This note was written before the next Serper snapshot of these 30 US English queries exists. The Nov 9 to Nov 11 numbers in `backtest-nov9-nov11.md` are a backtest of an outcome that was already opened in commit `55a695c`. They are not a fitting target and not this gate.

The product ranker is not changed by this file.

## What is frozen

The scorer is `src/aeo/ranksim/full_coverage.py`. Fitted numbers live only in `params.json`. The manifest lists the sha256 of the scorer, the evaluation script, the metric code the scorer calls, and this file. A test fails when those bytes change.

History used at score time is the three committed position lists, checked by sha256:

- `evals/forward-2026-11-09/baseline_serp.csv` is 2026-11-06.
- `evals/forward-2026-11-09/forward_serp.csv` is 2026-11-09.
- `evals/forward-2026-11-11/outcome_serp.csv` is 2026-11-11. For the next snapshot this file is the previous order, not a label.

No snapshot HTML is in git. No Tranco file is in the repo. No Majestic Million file is in the repo. Open PageRank is not called. No paid API is called. The command does not fetch pages.

## Score

A URL in the new top 10 that was also in the 2026-11-11 top 10 for that query is a survivor. Its score is the negative of that 2026-11-11 position. Earlier positions are stored on the row. `survivor_history_weight` is 0 because the fit window has only one previous snapshot, so a blend is not identified. The score does not use them.

A URL in the new top 10 that was absent from the 2026-11-11 list for that query is new. Its predicted position is

```
intercept
+ coef_domain_prior * domain_prior
+ coef_url_overlap * url_overlap
+ coef_content_missing * content_missing
- bm25f_z_weight * bm25f_z
- tranco_z_weight * tranco_z
- majestic_z_weight * majestic_z
```

The score is the negative of that prediction. Higher is a better rank. It is on the same scale as a survivor's negative position. It is not a probability and not a Google rank.

`domain_prior` is the mean of `11 - position` for that registrable host on the history lists. A host that never appears gets the mean of those history rows. The same query is included. This is not the family-out prior from the Nov 9 new-page note. The outcome file is not history. A survivor's score does not read the domain prior. A new URL has no row of its own on the previous snapshot.

`url_overlap` is the share of the query's content tokens that appear in the URL.

`content_missing` is 1 when no HTML text was attached. Its coefficient is 0. Missing text adds no push and is not a penalty.

`bm25f_z` is the within-query z-score of BM25F, computed from HTML passed at score time. A missing page adds 0. The weight is 1, the same one-position-per-standard-deviation scale fixed in `forward_eval.py` before the Nov 9 labels were scored. It was not estimated on this fit, because the fit rows have no HTML in git. On the Nov 9 to Nov 11 backtest the term is 0 for every row.

`tranco_z_weight` and `majestic_z_weight` are 0. Passing either file records the value and does not change the rank.

Coefficients other than the two estimated ones were not fit on Nov 11 and were not fit on Nov 9. The estimated coefficients use Nov 6 rows as features and Nov 9 positions of URLs that were absent from that query on Nov 6 as labels. Survivor labels are not training rows. The Nov 11 file is not read by the fit.

## Metrics

The candidate set is the new snapshot's top 10. This scores order among pages that were on that list. It does not score whether a page enters the list.

An ordered pair is two URLs on the same query whose outcome positions differ. The better outcome position should have the higher score. A score tie is not correct. The denominator is correct + wrong + ties.

Buckets, from the immediately previous snapshot only:

- all pairs
- surviving-surviving, both URLs were on that previous list
- new-versus-surviving, exactly one was
- new-versus-new, neither was

NDCG@10 and mean Spearman are secondary. They are means over queries with at least two URLs. NDCG gains are 11 minus position. Spearman correlates the rank of the score with the rank of the outcome position. Higher scores are better, so agreement is positive. They are not promotion gates.

Baselines:

- Persistence-ranked-last uses the survivor score above and scores every new URL at -1000. New URLs are tied with each other and below every survivor.
- BM25, when an HTML directory is passed, is BM25F. A URL with no HTML shares a score below every URL that has BM25F. With no HTML directory, BM25 is a tie and the report says so.
- LambdaMART is not recomputed. The booster and the training HTML are not in git, and the command does not refit. The Nov 9 to Nov 11 backtest reads the scores already frozen in `evals/forward-2026-11-11/predictions.json` and ranks URLs that file did not score last.

## Promotion gate

Promotion gate for the next snapshot. This text was written before that snapshot existed.

The combined predictor clears the gate on that snapshot only when all three are true:

1. On all ordered pairs among the new snapshot's top 10, pairwise accuracy is strictly greater than persistence-ranked-last on the same pairs. Pairwise accuracy is correct / (correct + wrong + ties). A score tie is not correct. Pairs whose outcome positions are equal are not in the denominator.

2. On new-versus-surviving pairs, pairwise accuracy is strictly greater than 0.5.

3. The new-versus-surviving denominator is at least 100 pairs.

Persistence-ranked-last scores a URL that was in the immediately previous snapshot at the negative of that position. A URL that was absent from that snapshot scores -1000, tied with every other absent URL and below every survivor.

A pass does not change the product ranker, the fitted parameters, or the frozen files. The command records the gate result on the track-record ledger. One snapshot of these 30 queries is not a general forecast of Google. This gate was not chosen by looking at the Nov 9 to Nov 11 backtest.

The gate is a point comparison. It does not ask for a bootstrap interval above zero. A pass on one 30-query snapshot can be noise. `ranker_change` stays `none`.

## Command

From the repo root, after the new snapshot exists:

```bash
python scripts/score_next_snapshot.py \
  --snapshot-csv path/to/serp.csv \
  --html-dir path/to/html \
  --observed-on YYYY-MM-DD
```

`--html-dir` is optional. When it is set, the directory must contain `html_manifest.csv` with `url` and `file` columns. `file` is relative to that directory. Omit `--html-dir` when there is no HTML. Do not point it at a directory that has no manifest.

The same arguments work as `python -m aeo.ranksim score-next-snapshot`.

The CSV needs `query`, `position`, and `url` or `link`. The position column is the REAL outcome. It is not a feature. `--observed-on` must be a date after 2026-11-11. The command refuses the three frozen history files.

The command appends to `evals/preregistered/next-snapshot/track-record.jsonl`. Each run adds a PREDICTED header, one PREDICTED score row and one REAL position row per URL, and a SCORE row with the metrics and the gate. Rows are a hash chain. The command does not rewrite earlier rows and does not write `params.json`, this file, the manifest, the backtest, or the scorer.

Optional `--tranco` and `--majestic` paths are recorded. Their frozen weights are 0.

## Fit sample

The size of the Nov 6 to Nov 9 new-URL fit, the coefficients, and the in-sample RMSE are in `params.json` under `in_sample`. That block is labeled in-sample. It is not a test. Pair counts for that window are in the backtest file and are labeled the same way.

## Not done

Page HTML for Nov 6, Nov 9, and Nov 11 is not in git, so this freeze cannot estimate a BM25F coefficient or a missing-page coefficient. The backtest does not exercise BM25F.

Tranco and Majestic Million lists are not in the repo, so this freeze does not estimate those weights.

LambdaMART is not saved as a booster file. The next-snapshot command does not score it.

No new snapshot has been collected. The track record is empty until the command above is run on one.
