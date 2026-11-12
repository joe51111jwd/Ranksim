# Backtest: Nov 9 to Nov 11 full coverage

Label: BACKTEST. Not a forward test and not a promotion.

The 2026-11-11 outcome was opened in commit 55a695c, before this scorer existed. Coefficients were fit on 2026-11-06 to 2026-11-09 new URLs only and were not changed after these numbers were computed. This is not the promotion gate.

No snapshot HTML is in git. content_missing is 1 on every backtest row. bm25f_z is 0. The precommitted BM25F weight was not exercised.

Tranco and Majestic Million files are not in the repo. Their frozen weights are 0.

LambdaMART and BM25 baselines are the scores frozen in `evals/forward-2026-11-11/predictions.json` for the Nov 9 to Nov 11 window. A Nov 11 URL that file did not score gets -1000, tied with the other unscored URLs and below every scored URL. This run did not refit LambdaMART. The booster and the training HTML are not in git.

Pairwise counts ordered pairs whose outcome positions differ. A score tie is in the denominator and is not correct. NDCG@10 and mean Spearman are on the full outcome list for each query. Higher scores are better. NDCG gains are 11 minus position.

Outcome rows 296. Surviving 208. New 88. Scored 296. Domain seen on 75 of the new rows. HTML rows 0.

| Scorer | All pairs | Surviving-surviving | New vs surviving | New vs new | NDCG@10 | Mean Spearman |
| --- | --- | --- | --- | --- | --- | --- |
| Combined (persistence + new-URL line) | 1034/1314 (0.786910), 4 ties | 625/724 (0.863260) | 300/404 (0.742574) | 109/186 (0.586022), 4 ties | 0.896326 | 0.672776 |
| Persistence, new URLs ranked last and tied | 915/1314 (0.696347), 186 ties | 625/724 (0.863260) | 290/404 (0.717822) | 0/186 (0.000000), 186 ties | 0.886654 | 0.649894 |
| Frozen LambdaMART, new URLs ranked last | 798/1314 (0.607306), 192 ties | 508/724 (0.701657), 6 ties | 290/404 (0.717822) | 0/186 (0.000000), 186 ties | 0.822425 | 0.476683 |
| Frozen BM25, new URLs ranked last | 611/1314 (0.464992), 192 ties | 321/724 (0.443370), 6 ties | 290/404 (0.717822) | 0/186 (0.000000), 186 ties | 0.614660 | 0.111018 |

Frozen surviving URLs with no prediction-file match: 0. Persistence disagreements against that file: 0.

Surviving-surviving reproduces the published Nov 9 to Nov 11 overlap: persistence 625/724, LambdaMART 508/724 with 6 ties, BM25 321/724.

## Fit sample, Nov 6 to Nov 9

IN-SAMPLE. These pairs are the fit window. They are not a test and not a gate.

| Scorer | All pairs | Surviving-surviving | New vs surviving | New vs new | NDCG@10 | Mean Spearman |
| --- | --- | --- | --- | --- | --- | --- |
| Combined, in-sample | 1037/1341 (0.773304), 22 ties | 594/667 (0.890555) | 347/479 (0.724426) | 96/195 (0.492308), 22 ties | 0.914548 | 0.677160 |
| Persistence, new URLs ranked last, in-sample | 931/1341 (0.694258), 195 ties | 594/667 (0.890555) | 337/479 (0.703549) | 0/195 (0.000000), 195 ties | 0.902317 | 0.648714 |

The all-pairs gap versus persistence-ranked-last is 119 correct pairs (10 of them new-versus-surviving, 109 new-versus-new). Persistence-ranked-last ties every new-versus-new pair, so those pairs add nothing to its correct count. On the fit window, new-versus-new was 96/195 (0.492308), with 22 ties in that denominator. Under the rule that a tie is not correct, that rate is not above a coin flip.

Predicted positions on the fit rows sit in a narrow band. See `predicted_position_min` and `predicted_position_max` in `params.json`. A new URL is not predicted at the top of the list.

## Frozen gate applied to this backtest

NOT A PROMOTION. Computed so the backtest can be read against the frozen rule. The rule was not edited after this number.

Pass: True. Checks: {"beats_chance_on_new_vs_surviving": true, "beats_persistence_ranked_last_on_all_pairs": true, "new_vs_surviving_pairs_at_least_minimum": true}.

A pass or a fail here does not promote the ranker and does not edit the gate.
