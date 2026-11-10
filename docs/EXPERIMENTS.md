# Experiment log

One entry per experiment, newest last. Record what was run, on which data, what came out,
and what it cost, including failures, so the next session does not repeat them. Numbers are
predicted agreement with labeled Google results, never live Google ranks.

Conventions for every entry:

- **Data**: label file and its sha256, corpus fingerprint from `benchmark` (sha256 over every
  stored page's URL and HTML hash), crawl date, `as_of` date used for date features.
- **Protocol**: `loqo`, `cluster` or `cluster-url` (see `src/aeo/ranksim/benchmark.py`).
- **Denominators**: pairs, queries, query clusters, covered and uncovered rows.
- **Uncertainty**: 95% percentile bootstrap over query clusters; a "luck" band from random
  scores on the same pairs.
- **Cost**: SERP calls, LLM calls and estimated dollars, crawl fetches and wall time.

---

## 2026-11-05 E1. Test suite depends on the developer's environment

- Clean venv, `pip install -e ".[dev]"`, Python 3.13: 63 passed with no provider keys.
- With `OPENROUTER_API_KEY` exported (any value) `test_stub_llm_and_unmeasured_relevance`
  failed; with `SERPER_API_KEY` exported `test_auto_serp_refuses_to_invent_results_without_keys`
  failed. A filled-in `.env` (README setup step) has the same effect, because `Settings` reads
  `./.env`. One test also created `data/llm-cache/` inside the checkout.
- Fix: autouse fixtures in `tests/conftest.py` (strip every Settings env var, disable `.env`,
  refuse non-loopback sockets), lazy cache directories, CI matrix with and without fake keys.
- After: same pass count with no keys, each key alone, all keys, and a populated `.env`.
- Cost: $0.

## 2026-11-05 E2. Where the AI-eval sample (35 rows, 12 queries, 33 pairs) came from

- `ai-eval` calls `bootstrap.seed_corpus_if_empty`. On a fresh clone that stores 25 pages:
  19 third-party pages from `smartcrew/saved_html/` and 6 pages from `smartcrew/pr8_v3/`,
  which are unpublished drafts stored under the live SmartCrew URLs.
- Joined to `smartcrew/serp.csv` (135 rows, sha256 `4bcc6715f997...`): 35 covered rows, 100
  uncovered, LOQO over 12 queries, 33 pairs, pairwise 0.485, NDCG 0.814, Spearman 0.032. This
  matches the historical AI-eval baseline exactly, so that run used the seed corpus, not a
  crawl. 6 of the 35 rows score draft HTML against Google's positions for the live pages.
- On these 33 pairs random scores land between 0.303 and 0.697 for 95% of seeds. The reported
  AI delta (0.485 to 0.394) is 3 pairs. It is not distinguishable from luck in either direction.

## 2026-11-05 E3. Fresh crawl of the 16-query file

- Command (from a copy of the repo, scratch DB):
  `python -m aeo.ranksim index-serp smartcrew/serp.csv` then `evaluate`, then
  `graph --serp smartcrew/serp.csv --out <scratch>`, then `evaluate` and `graph-eval` again.
- index-serp: 121 URLs attempted, 116 fetched, 19 from saved HTML, 1 robots block, 4 errors;
  31 seconds wall time. Graph crawl: 50 fetches, 63 graph pages, 4,662 edges, 58 pages stored;
  44 seconds. No SERP or LLM calls. Cost $0 (bandwidth only).
- Labeled coverage: 128 of 135 rows. Uncovered: G2 HCSS alternatives (403, 3 rows, including
  position 1 for "hcss alternative"), G2 B2W Track alternatives (403), thedigitalprojectmanager
  (403), Capterra BluVue alternatives (404, page removed), a Facebook answers page (robots).
  The pages that block crawlers are some of the strongest results, so coverage is biased.
- `evaluate` (LOQO) before the graph crawl: pairwise 0.548 on 458 pairs. After the graph crawl
  stored 58 more unlabeled pages: 0.579 on the same 458 pairs. In-corpus PageRank and anchor
  text depend on every stored page, so the metric moves with unlabeled corpus contents.
  The benchmark now prints a corpus fingerprint for that reason.
- Historical figures (0.604 on 245 pairs, 0.534 on 414 pairs, 83 pages and 14,068 edges) came
  from databases that are not in the repo. They cannot be reproduced and should not be quoted.

## 2026-11-05 E4. Baselines and held-out protocols (`benchmark`)

- `python -m aeo.ranksim benchmark --as-of 2026-10-29` on the E3 corpus (fingerprint
  `f7603b465308...`, 163 stored pages), 128 covered rows, 16 queries, 11 query clusters,
  458 pairs, 2,000 cluster bootstrap resamples.

  | protocol | lambdamart | bm25f only | linear prior | domain prior | random 95% band |
  | --- | --- | --- | --- | --- | --- |
  | loqo | 0.579 (0.490-0.649) | 0.487 | 0.485 | 0.566 | 0.424-0.572 |
  | cluster | 0.568 (0.495-0.632) | 0.487 | 0.485 | 0.504 | 0.424-0.572 |
  | cluster-url | 0.566 (0.466-0.657) | 0.487 | 0.485 | 0.410 | 0.424-0.572 |

- LambdaMART minus BM25F: +0.092 (-0.009 to +0.176), +0.081 (-0.002 to +0.141), +0.079
  (-0.031 to +0.160). Every interval includes 0. BM25F alone and the hand-weighted prior are
  at chance. A domain prior with no page content matches the model under LOQO, which is a sign
  LOQO rewards recognizing pages and domains seen in near-duplicate queries.
- NDCG@10 for random scores on this query mix is 0.640; LambdaMART is 0.70-0.74.
- 29 of 86 labeled URLs appear in more than one query (78 of 135 rows); the cluster protocols
  exist because of that.

## 2026-11-05 E5. The score-gap confidence threshold out of sample

- `calibrate` picks a threshold on all LOQO pairs and reports accuracy on the same pairs.
  On the pre-graph corpus it picks gap >= 2.00 with 0.698 on 23% of pairs, close to the
  historical "2.00 -> 0.716 on 33%". Held out by query cluster and URL, with each fold's
  threshold chosen on other folds, those confident calls were right 20 of 41 times. On the
  post-graph corpus: in-sample gap >= 1.00 (0.668 on 53%), held out 16 of 47.
- The 150-word gate rewrites thin pages' scores to floor-1, floor-2, ..., which creates score
  gaps of exactly 1.0 and 2.0. Part of any historical high-gap slice may be those rewritten
  pairs.
- Product change: `loo_fit` keeps a threshold only if held-out confident calls are right at
  least 0.65 of the time on at least 30 calls. On current data every call is UNSURE.

## 2026-11-05 E6. Metric and feature plumbing bugs

- NDCG and top-3 broke score ties by input order, which is label order: a constant scorer got
  NDCG 1.000. Now the exact expectation over tied orders. LambdaMART numbers did not change
  (no ties); tied baselines lost 0.01-0.02 NDCG.
- `orphan`, `link_depth` and `crawl_inlinks` were NaN on every training example; `authority`
  was a copy of in-corpus PageRank. With the graph features actually in the model: LOQO 0.574
  vs 0.579 without, cluster-url 0.522 vs 0.566. No evidence the link-graph features help.

## 2026-11-05 E7. Why AI relevance "hurt" (no LLM calls made)

- No LLM key exists here, so the real judge was not re-run; its historical scores are not in the
  repo. Instead `judge.relevance_experiment` was run on the E2 seed corpus (33 pairs) with
  synthetic judges, 200 seeds each, `llm_relevance` unconstrained as shipped:

  | judge | mean delta | 5th / 95th percentile | share of runs <= -0.091 |
  | --- | --- | --- | --- |
  | uniform random | +0.040 | -0.091 / +0.152 | 8.0% |
  | true label + noise, sigma 0.1 | +0.222 | +0.061 / +0.364 | 0% |
  | true label + noise, sigma 0.25 | +0.104 | -0.061 / +0.274 | 2.5% |
  | true label + noise, sigma 0.5 | +0.048 | -0.092 / +0.212 | 10% |

- Exact binomial intervals: 16/33 (0.485) is 0.308-0.665, 13/33 (0.394) is 0.229-0.579. A paired
  sign test on at most 3 discordant pairs gives p = 0.25 at best.
- Conclusion: the -9.1 point result is what chance produces routinely on 33 pairs, and even a
  moderately useful judge is not reliably detectable at this size. It is not evidence that AI
  relevance hurts or helps. Six of the 35 judged rows were unpublished drafts.
- One `ai-eval` run makes 35 judge calls, 1 explanation and 1 rewrite call (37), matching the
  historical count. The rewrite prompt sent only the first 6,000 characters of raw HTML, which for
  the default draft is entirely `<head>`; the rewrite never saw the body.
- Changes: the experiment now reports the paired interval and the random band, the judge feature is
  monotone, `ai-eval` refuses the paid run below 100 pairs, and the rewrite loop sends extracted
  text and rejects keyword stuffing. AI relevance stays out of the default score.
- Cost: $0.

## 2026-11-05 E8. Crawl-to-crawl variation and an accidental fast crawl

- The test and audit sandboxes set a lowercase `https_proxy`, which overrides `HTTPS_PROXY=` in
  httpx, so "network blocked" runs were not blocked. One audit run re-crawled the ~121 labeled URLs
  with no delay (robots.txt still respected). That was not intended; the delay default (0.4 s per
  host) should be used. A second audit run (the branch inventory) pulled about 137 third-party URLs
  through the other branch's project command for the same reason, with the 0.4 s delay and robots
  respected. Its LOQO result on its own same-day crawl was 0.544 on 458 pairs against
  0.548 on mine before the graph crawl, so two same-day crawls differ by about 0.004 here.
- A probe test reached openrouter.ai through the loopback proxy, past the socket guard. Tests now
  drop proxy variables, and a regression test checks that httpx cannot reach the internet.

## 2026-11-05 E9. Same-day crawl variation, feature artifacts and extraction

- Three independent crawls of the same 135 labels on the same day (mine, and two by audit agents)
  gave LOQO pairwise 0.548, 0.544 and 0.576 straight after `index-serp` (458 pairs each). Part of
  that was the `speed` feature, which recorded our crawler's own timing (politeness sleep, the
  first robots.txt fetch, the proxy) and so differed between crawls.
- Removing `speed`, `orphan`, `link_depth` and `crawl_inlinks` from the model (they measure the
  crawl, not the page) moved results by less than 0.02 under every protocol. The change was made
  because of what the features measure, not because of the score.
- The extractor took the first `<main>`/`<article>`; on this crawl 4 stored pages had their word
  count corrected by `reextract`. That alone moved clusters-and-URLs-held-out pairwise from 0.559
  to 0.498 and LOQO from 0.587 to 0.581.
- Current code on the re-extracted corpus (fingerprint changes with re-extraction only through the
  derived fields; HTML unchanged): LOQO 0.581 (0.499-0.648), cluster 0.559 (0.503-0.609),
  cluster-url 0.498 (0.404-0.584); BM25F 0.478; random 0.424-0.572; LambdaMART minus BM25F
  +0.103 (+0.012 to +0.185), +0.081 (+0.017 to +0.128), +0.020 (-0.076 to +0.099). Held-out
  confident calls: 114 of 183, 62 of 88, 7 of 28 right.
- Reading: with 11 independent query clusters, any single number here moves by several points
  with small, legitimate data changes. Only a larger, frozen, dated benchmark (BACKLOG P0-1, P0-2)
  can settle whether the model beats BM25F on unseen pages.

## 2026-11-05 E10. Fair tie handling and the domain prior

- An independent review found that benchmark pairwise counted score ties as wrong while the random
  band has no ties, which penalized tie-heavy baselines. With ties at half credit (their expected
  value under random tie-breaking), on the E9 corpus:

  | protocol | LambdaMART | domain prior | BM25F | linear prior |
  | --- | --- | --- | --- | --- |
  | loqo | 0.581 | 0.602 | 0.478 | 0.474 |
  | cluster | 0.559 | 0.571 | 0.478 | 0.474 |
  | cluster-url | 0.498 | 0.548 | 0.478 | 0.474 |

- The domain prior reads no page content: each domain's mean labeled relevance in the training
  queries. It matches or beats the content model under every protocol. On this data, the model's
  page features add nothing measurable over "which domains rank".
- The same review found the confidence gate checked per-fold thresholds but shipped a different
  one. Scored directly, the shipped gap of 1.00 was right on 122 of 232 held-out pairs (0.526).
- Next measurement that matters: the same comparison on a frozen, dated, multi-site benchmark
  (BACKLOG P0-1, P0-2), where a domain prior cannot memorize one niche's winners.

## 2026-11-05 E11. Independent cross-check of the baselines

- A separate agent recomputed baselines with its own scripts on its own same-day crawl (pre-branch
  model code, 458 pairs, 16 queries, query bootstrap; numbers below are its, not `benchmark`'s):
  LambdaMART LOQO 0.585 (0.519-0.644); BM25F 0.487; hand-weighted linear prior 0.489; domain prior
  0.566 ties-as-wrong / 0.602 ties-as-half; URL prior (same URL's mean position in training
  queries) 0.618 ties-as-half; random 0.499 (5th-95th 0.437-0.559).
- Training without queries that share a labeled URL with the held-out query: 0.502 (0.438-0.568),
  against 0.558 for dropping the same number of training queries at random. URL identity is what
  carries between near-duplicate queries.
- Feature-group ablations (paired against the full model): dropping all text-match features 0.592,
  dropping quality/length features 0.605, dropping link features 0.546, dropping freshness 0.557.
  None differs from the full model; after a Bonferroni correction over its 32 comparisons, no
  difference against the model or against BM25F excludes 0.
- It agrees with E4, E5, E9 and E10. It also found that confidence is calibrated on all pairs while
  the product labels a check from the gap to the adjacent page (median adjacent gap 0.345; 17% at or
  above 1.0), that 9 of the 16 labeled queries have no intent and default to "commercial", and that
  the thin-page gate's synthetic score offsets count as confident gaps. These are in the backlog.

## 2026-11-05 E12. Frozen snapshot and the first final-split run

- `benchmark-freeze` on the fresh crawl (`reextract.db`, 168 stored rows, link graph of 2026-11-05)
  with `--as-of 2026-11-05`, seed 0, final share 0.3: 11 query clusters; final split = 5 queries in
  3 clusters (`best heavy civil construction software`, `hcss alternative`, `hcss heavybid
  alternative`, `hcss heavyjob alternative`, `heavy civil field management software`); dev = 11
  queries in 8 clusters. Labels sha256 4bcc6715f997, corpus sha256 f7603b465308. The snapshot holds
  third-party HTML, so it is not in git and lives only in this session's scratch space.
- Two dev runs from the snapshot gave byte-identical JSON (apart from `created_at`). Dev, 344 pairs:
  LambdaMART cluster-url 0.532 (0.402-0.624); BM25F 0.480; domain prior 0.562; random 0.413-0.584.
- One `--final` run for the current configuration, model id `lgbm-98ceb518acedd909` (MODEL_FEATURES,
  default monotone set, 40 rounds, 15 leaves), recorded in the snapshot's `results.jsonl`. 114 pairs:

  | protocol  | LambdaMART | BM25F | linear prior | domain prior | random (95% of seeds) |
  |-----------|-----------:|------:|-------------:|-------------:|----------------------:|
  | final     | 0.561      | 0.474 | 0.439        | 0.583        | 0.351-0.632           |
  | final-url | 0.579      | 0.474 | 0.439        | 0.575        | 0.351-0.632           |

  No confidence threshold met the bar on held-out dev pairs, so every final pair stays UNSURE.
- Reading: every number sits inside the random band, and only 3 clusters were scored. The first
  render of this run printed "lambdamart beats bm25f_only" from a bootstrap over 3 clusters; that
  interval is not trustworthy, so the report now gives no verdict below 10 scored clusters
  (`MIN_VERDICT_CLUSTERS`). The final split is there so that later model changes (B16) are judged
  on queries nobody tuned on; with 16 labeled queries it cannot show an effect of this size either way.
  It becomes useful when the label set grows (C1).

## 2026-11-05 E13. Calibrating the statistic the check labels

- Before: the threshold was chosen on every labeled pair, but the check labeled the predicted rank
  from the gap to the adjacent page, and gaps came after the 150-word rule, which places a thin page
  exactly 1.0 below the last full page (then 2.0, ...). With a 0.5 threshold a 100-word draft was
  CLEARER against every competitor (reproduced on the previous commit with a fixed-score model).
- Now: two statistics, each with its own threshold and both held-out checks (deployed threshold and
  cross-fitted): head-to-head gaps label competitor rows; each page's smallest gap to its predicted
  neighbours, counted right only if both neighbour orders are right, labels the predicted rank. Gaps
  come from the model's own scores, and pages the rule placed are left out of calibration and are
  always UNSURE in the check, with a note that a rule placed them.
- On the fresh crawl (`freeze-src.db`, 16 queries): 443 head-to-head pairs and 126 adjacent cases
  in leave-one-query-out. Head-to-head: a 1.00 gap looked accurate in-sample (0.665 on 53%) but was
  right on 114 of 217 held-out cases, and cross-fitted 7 of 28. Adjacent: no threshold reaches 0.65
  on 30 cases even in-sample. Dev split of the snapshot, all protocols: no cross-fitted adjacent
  call. Every call stays UNSURE, as before; the change matters once the data supports a threshold.

## 2026-11-10 E14. The integration branch's forward check, with ties counted as half

Analysis of committed files only, not a new run and not a new claim. Source:
`evals/forward-2026-11-09/report.json` on PR #5 (`cursor/integration-review-175f`, commit f76f7f1):
models frozen on a Nov 6 Serper snapshot of 30 queries, scored once against the Nov 9 snapshot of
the same queries, 1,341 pairs over the Nov 9 candidate lists. That report counts a tie as wrong. This
benchmark counts a tie as half (what random tie-breaking scores), so the figures below are
(correct + ties / 2) / pairs from the same counts.

| scorer | ties | published (ties wrong) | ties as half | draft vs draft (116 pairs) | draft vs ranking page (281 pairs) |
|---|---:|---:|---:|---:|---:|
| LambdaMART (content) | 104 | 0.507 | 0.546 | 0.414 | 0.528 |
| BM25 | 105 | 0.444 | 0.484 | 0.444 | 0.472 |
| persistence (keep Nov 6 order) | 195 | 0.694 | 0.767 | 0.500 (all ties) | 0.712 |
| equal-weight blend | 29 | 0.702 | 0.713 | 0.591 | 0.705 |
| history-aware rule | 29 | 0.751 | 0.762 | 0.591 | 0.712 |

- With ties counted as half, persistence is at least as good as the history-aware rule; counting
  ties as wrong is what put the rule ahead, because persistence ties every pair of new pages.
- Content models are near chance overall and below it on draft vs draft (16 queries). This matches
  E4 to E12: the core use case, placing a page Google has not ranked, is not supported yet.
- Limits: one 2.5-day interval on one site's 30 queries; no intervals can be computed from the
  committed file (it has no per-query counts); the page HTML behind the content scores is private,
  so only persistence and overlap can be recomputed from git. The forward ledger rows carry a stated
  time of 2026-11-07 although they were written after the Nov 9 results existed; I2 makes this
  branch's ledger date such rows by when they were written.

## 2026-11-10 E15. One client-rendered shell, and how much one row moves the numbers

- B6 marks pages whose stored HTML is an empty app shell (an empty `#root`/`#__next` mount point,
  a noscript asking for JavaScript, or almost no visible text, with scripts). On the fresh crawl
  (`freeze-src.db`, 163 fetched pages) exactly one page qualifies: `https://www.dirtworksoftware.com/`
  (0 words, empty `#root`). It is a labeled result on a dev-split query. The low-text SmartCrew
  pages (58 to 172 words) are card lists with their text in the HTML and are not flagged.
- Frozen dev split, before and after leaving that one row out (344 to 337 pairs, 200 resamples):

  | protocol | LambdaMART before | after | BM25F before | after | domain prior before | after |
  |---|---:|---:|---:|---:|---:|---:|
  | loqo | 0.605 | 0.632 | 0.480 | 0.487 | 0.634 | 0.642 |
  | cluster | 0.520 | 0.543 | 0.480 | 0.487 | 0.564 | 0.571 |
  | cluster-url | 0.532 | 0.528 | 0.480 | 0.487 | 0.562 | 0.568 |

- Reading: one row out of 92 moves a headline number by up to 2.7 points. Every interval is wider
  than any of these changes and only 8 clusters are scored, so no verdict changes. This is a data
  cleaning decision made from inspecting the crawl, not from any result; the model configuration
  and its id are unchanged, so the recorded final-split run (E12) is not repeated.

## 2026-11-10 E16. A content-blind site prior on the frozen dev split

- `site_prior` scores a page by how many training queries list its site (registrable domain) among
  their labeled results. It is the web-UI branch's "site history" signal (PR #2 `site_history.py`)
  without its fitted scale, with the direction fixed in advance. Held-out queries never count, and
  under cluster-url the held-out URLs are dropped from training first, so only other pages of the
  same site vote. Specified before this run; not tuned.
- Frozen dev split (11 queries, 8 clusters, 337 pairs, 1000 resamples), same folds for every row:

  | protocol | LambdaMART | BM25F | domain prior | site prior | random 95% band |
  |---|---:|---:|---:|---:|---:|
  | loqo | 0.632 | 0.487 | 0.642 | 0.656 (0.585 to 0.705) | 0.418 to 0.582 |
  | cluster | 0.543 | 0.487 | 0.571 | 0.602 (0.545 to 0.682) | 0.418 to 0.582 |
  | cluster-url | 0.528 | 0.487 | 0.568 | 0.614 (0.581 to 0.666) | 0.418 to 0.582 |

- Reading: a signal that reads no page text is the best row under every protocol here, including
  the one closest to scoring an unseen draft. With 8 clusters no verdict is given, and these are
  same-snapshot numbers, not accuracy. It agrees with two independent results: PR #2's lab (site
  history beat the content model) and PR #5's forward check (keeping the previous order beat the
  content models, E14). For the product question "does my page beat these competitors", the site
  a page is on may matter more than its text on this kind of query; a page edit cannot move this
  signal, and the check must not suggest otherwise.
- No persistence row: `smartcrew/serp.csv` has no `prior_position` column.

