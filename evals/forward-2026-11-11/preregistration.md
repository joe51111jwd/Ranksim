# Pre-registration: 2026-11-11 forward test

Label: PREDICTED. The 2026-11-11 outcome file was not opened when these rows were written. This note names the metric and the bars before that file is scored.

## Primary metric

All ordered pairs among URLs that are in the pre-registered candidate list and also in the 2026-11-11 top 10 for the same query. A score tie is not correct. The denominator is correct plus wrong plus ties.

Spearman and NDCG@10 on those same URLs are secondary. They are not promotion gates.

A URL in the 2026-11-11 top 10 that is not in the candidate list is unpredicted. It is not given a score. Pairs that include it are not in the primary denominator. The report states that coverage.

The surviving-URL subset is the URLs that are in both the candidate list and the 2026-11-11 top 10. For this protocol that subset is the primary pair set.

The churn baseline is the agreement of the candidate-day order with the 2026-11-11 order on that overlap. It is not a model. A figure supplied before this file was scored will be reproduced or corrected. It is not an input and it is not a target.

## Promotion bar

An eligible scorer clears the bar when its primary pairwise is at least persistence plus 0.05, the bootstrap interval of that gain has its lower end above zero, and, for lambdamart and blend, it is also at least bm25 on the same pairs. history_aware is reported and is not eligible. Clearing the bar does not change the ranker in this run.

Margin 0.05. Bootstrap 2000 query resamples, seed 20261111. `ranker_change` stays `none` in this run even if a scorer clears the bar.

## Windows

### 2026-11-09_to_2026-11-11

Protocol time 2026-11-10T04:00:00+00:00. Candidates: every 2026-11-09 result URL (299 rows, 30 queries). Persistence is the negative of the 2026-11-09 position. LambdaMART is retrained on 2026-11-06 and 2026-11-09 labels. Each day is its own ranking group. Page text for a training row is HTML captured on or before that row's day. 215 training rows from 2026-11-06 and 218 from 2026-11-09.

Score-time text is HTML saved on 2026-11-09, otherwise HTML saved on 2026-11-06. The link graph is the union of those pages, with 2026-11-09 HTML winning when both days saved the URL. The 2026-11-10 scrape is not used.

### 2026-11-06_to_2026-11-11

Protocol time 2026-11-07T04:00:00+00:00. This is the two-step test, frozen as of 2026-11-06. Candidates: every 2026-11-06 result URL (294 rows, 30 queries). Persistence is the negative of the 2026-11-06 position. LambdaMART is fit only on the 215 HTML rows from that day. A 2026-11-09 position is not a label, a feature, or a threshold.

Score-time text and the link graph are the 2026-11-06 HTML only. A URL with no 2026-11-06 HTML is unknown content even if a later snapshot saved it.

## Scorers

The five scorers are lambdamart, bm25, persistence, blend, and history_aware. Blend weights are 1 and 1. History-aware scale is 1. An entrant base position of 11 and an entrant score of -1000 stay in the code. Every candidate in these files already has a position on the baseline day, so those entrant constants do not move a row inside the candidate list. A page that first appears on 2026-11-11 is unpredicted rather than scored as an entrant.

A page with HTML keeps its LambdaMART and BM25 scores. A page with no HTML is unknown content: the training-score mean, shifted by off-page evidence. It is not the shared floor. Blend and history-aware still treat missing HTML as no content push. They do not use the unknown-content score. That is the rule already on the branch.

Off-page tables were not loaded. Domain prior and URL overlap are the off-page parts that exist. A missing part adds nothing.

## Hyperparameters

objective lambdarank, learning_rate 0.08, num_leaves 15, min_data_in_leaf 1, num_boost_round 40 requested, monotone_constraints_method advanced. Features are FEATURE_NAMES. Monotone constraints are the product set. fit_lambdamart is called with no overrides. The thin-page gate inside predict_scores stays on. The 2026-11-06 fit kept 40 trees and the 2026-11-09 fit kept 40 trees. A smaller count means LightGBM stopped when no further split had positive gain. The requested round count was not changed.

Feature dates are fixed: 2026-11-07 for 2026-11-06 rows, and 2026-11-10 for 2026-11-09 rows and for the 2026-11-09 score time.

## Ledger

Prediction rows are PREDICTED and UNSURE. They are appended at the protocol time. The 2026-11-09 ledger file is not edited. After the outcome is opened, a REAL outcome is appended only for scoring. A call resolves only when both URLs are in the 2026-11-11 top 10. An exit is unresolved, not a win.

## Not done in this run

The ranker is not changed from these numbers. The 2026-11-10 scrape stays out. A later snapshot can pre-register score-time text captured on or before the prediction clock. That is a hypothesis, not a change made here.
