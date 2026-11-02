# Accuracy plan

Predicted agreement with labeled Google results. These are not live ranks.

The current backtest is leave-one-query-out on the SmartCrew file from 2026-10-29: 16 queries, 122 stored rows out of 135, 414 pairs. Pairwise accuracy is 0.534. NDCG at 10 is 0.695. Spearman is 0.075. Top-3 hit rate is 0.500. Pairwise is the number that has to move. 0.70 is the bar for a product that can say a draft beats a competitor without a shrug.

## Why 0.534 is the start

Sixteen queries is a small training set. Several labeled URLs still have no HTML (G2 alternatives, one Facebook thread, Capterra BluVue, and a handful of 403s). Heading and body matches of zero are stored as missing, directional text features are monotone, and pages under 150 words rank behind fuller pages. That stopped a 27-word shell from winning on a missing-value bug. It also lowered pairwise from 0.604 to 0.534, because the old number was partly those unconstrained splits.

## 1. Scale the labeled set to 300+ queries

Hold out by query, so more queries is the lever, not more copies of one query. Spread them across several niches (construction is the seed; add others only when you have real top-10s). Ten URLs per query.

Collection is a pluggable `SerpProvider`. Google order comes from:

- `SERPER_API_KEY` (recommended for this volume). The adapter is `serper` and posts to `https://google.serper.dev/search`.
- `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` (already wired).
- `SERPAPI_KEY` (already wired).

`BRAVE_API_KEY` is not Google and is not accepted as a label source. With no key, collect refuses to invent a top 10.

What we need: one of those Google keys, plus the niche keyword lists. We do not have a key in this environment, so no new SERPs were collected.

## 2. Daily snapshots and the noise ceiling

Google's own order moves. If two snapshots of the same queries only agree 0.80 of the time, a model at 0.85 is fitting noise. The metric is `python -m aeo.ranksim noise day-one.csv day-two.csv`. It reports pairwise agreement, how often the top URL is the same, and the mean absolute position change, on URLs present both days.

One snapshot cannot estimate that. The 2026-10-29 file compared with itself is a perfect match, and the command says so. It is not a noise ceiling. What we need: the same queries exported again on a later day.

## 3. Backlinks and domain authority

Web-wide backlinks are not in the crawl. The authority feature stays empty unless `RANKSIM_AUTHORITY=dataforseo` and the DataForSEO login above is set. Until then it is not a measured accuracy gain.

The link crawl does add in-crawl signals: internal PageRank, anchor text, orphan pages (no in-crawl inlinks, excluding the homepage), and link depth. Those are features on pages the crawl actually saw. They are not a substitute for a backlink index.

A polite crawl on 2026-11-02 stored 68 HTML pages (70 fetches, 1 robots block, then the cap). 41 of 135 labeled rows are still uncovered. On that rebuilt corpus, leave-one-query-out pairwise accuracy is 0.592 without the link-graph features and 0.604 with them (245 pairs, 16 queries). The delta is +0.012. That is not a meaningful gain, and it is not comparable to the earlier 0.534, which was measured on a larger stored set (122 rows, 414 pairs) that is not in this environment. `python -m aeo.ranksim graph-eval` reprints both numbers. If the features do not vary, the command says a delta is not evidence.

## 4. Did this change beat that competitor

A pre-publish check asks a pairwise question: after the edit, does our page outscore competitor Y on the stored set. Scoring that question against reality needs a real before and after Google observation (the URL, the competitor URL, and whether Google then ranked us above them). We do not have that labeled change file. The demo rescores the draft and will not invent the Google outcome.

## 5. Calibrated confidence

The ranker always emits a score. On the rebuilt crawl, pairs whose score gap is at least 2.00 had pairwise accuracy 0.716, and that is 33% of backtest pairs. Smaller gaps stay UNSURE. The SmartCrew sample draft on `hcss heavyjob alternative` is one of those: PREDICTED 1 of 7, labeled UNSURE. CLEARER is only the high-gap slice, and the page still says PREDICTED. Until the labeled set is larger, most checks should land on UNSURE.

## LLM relevance

An optional judge can score how well a page satisfies the query and add `llm_relevance` for a second held-out run. It runs only with `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. Neither is set here, so no judge scores were created and no accuracy delta is reported.

## What we need from you

1. `SERPER_API_KEY`, or DataForSEO login and password, or `SERPAPI_KEY`, to collect 300+ real Google top 10s.
2. Keyword lists for the extra niches.
3. A second export of the same queries on a later day, for the noise ceiling.
4. `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` if you want the judge, the rewrite loop, and LLM explanations instead of the stub.
5. Saved HTML for the URLs that still return 403 or block the crawler. Those rows stay uncovered. Nothing is filled in for them.
