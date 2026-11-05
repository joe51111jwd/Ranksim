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
  host) should be used. Its LOQO result on its own same-day crawl was 0.544 on 458 pairs against
  0.548 on mine before the graph crawl, so two same-day crawls differ by about 0.004 here.
- A probe test reached openrouter.ai through the loopback proxy, past the socket guard. Tests now
  drop proxy variables, and a regression test checks that httpx cannot reach the internet.
