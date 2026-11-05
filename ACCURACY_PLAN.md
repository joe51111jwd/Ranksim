# Accuracy plan

Predicted agreement with labeled Google results. These are not live ranks.

## Where we are (2026-11-05, reproducible)

On a fresh crawl of the 16-query SmartCrew file (`smartcrew/serp.csv`, 135 rows, collected
2026-10-29; 128 rows covered, 458 pairs, 11 query clusters), `python -m aeo.ranksim benchmark` with
the code on this branch (crawler-artifact features removed, corrected text extraction):

| protocol | LambdaMART pairwise (95% CI) | BM25F only | random (95% of seeds) | range seen today |
| --- | --- | --- | --- | --- |
| leave one query out | 0.581 (0.499-0.648) | 0.478 | 0.424-0.572 | 0.544-0.592 |
| leave one query cluster out | 0.559 (0.503-0.609) | 0.478 | 0.424-0.572 | 0.550-0.568 |
| clusters and their URLs held out | 0.498 (0.404-0.584) | 0.478 | 0.424-0.572 | 0.498-0.566 |

"Range seen today" spans three same-day crawls, with and without the link-graph pages, and before
and after the feature and extraction fixes. Re-extracting 4 pages alone moved the last row from
0.559 to 0.498.

What that means:

- Under the protocol closest to scoring an unseen draft (query clusters and their URLs held out),
  the model is at chance and not distinguishable from BM25F alone. Under the looser protocols it
  beats BM25F by about 0.08-0.10 with paired intervals that barely exclude 0, on one of several
  comparisons, which is not enough to claim a product-level effect.
- A domain prior with no page content matches the model under leave-one-query-out. Near-duplicate
  queries and the same URLs across queries reward recognizing pages, not judging content.
- The number moves with corpus contents: adding 58 unlabeled pages to the database moved the same
  458 pairs from 0.548 to 0.579, because in-corpus PageRank and anchor text use every stored page.
- Earlier figures (0.534 on 414 pairs, 0.604 and 0.592 on 245 pairs, "gap >= 2.00 -> 0.716") came
  from databases that are not in the repo and cannot be reproduced. Do not quote them.

Details, commands, costs and failures: `docs/EXPERIMENTS.md`.

The bar for a product claim is not a single number. It is: on a held-out benchmark of several
hundred queries across sites, the model beats BM25F-only and a domain prior with paired intervals
that exclude 0, under the cluster-and-URL protocol, and its confident calls are right at the rate
they claim on data that did not choose the threshold.

## 1. Scale the labeled set to 300+ queries

Hold out by query cluster, so more distinct queries is the lever, not more near-duplicates. Spread
them across several sites and niches (construction is the seed) and across informational,
commercial, comparison and transactional intent. Ten organic URLs per query.

Collection is a pluggable `SerpProvider` (`serper`, `dataforseo`, `serpapi`). A failed request or a
provider error payload is a failure, not an empty result, and never overwrites a stored SERP.
`BRAVE_API_KEY` is not Google and is not accepted as a label source. With no key, collect refuses to
invent a top 10. Label files should carry `observed_on`, `country`, `language`, `device` and
`provider` columns (see `smartcrew/serp-template.csv`); the import report says when they are missing.

What we need: one Google SERP key and the keyword lists. No key exists in this environment, so no
new SERPs were collected.

## 2. Repeated snapshots and Google's own volatility

`python -m aeo.ranksim noise day-one.csv day-two.csv` reports pairwise agreement on URLs present
both days, agreement counting URLs that entered or left, list overlap, top-1 stability and mean
position change. It shows how stable the target is; it does not prove a hard ceiling on what a
model can reach. One snapshot cannot estimate it. What we need: the same queries collected again on
later days with the same locale and device.

## 3. Links and authority

Web-wide backlinks are not in the crawl. `authority` stays missing (NaN) unless a backlink provider
stored a value; it used to be a copy of in-crawl PageRank. In-crawl PageRank, anchors, orphan and
link-depth features describe only the pages the crawl saw. With the link-graph features actually
reaching the model (a plumbing bug kept three of them NaN before), they did not help on the fresh
crawl (0.574 vs 0.579 leave-one-query-out, 0.522 vs 0.566 with clusters and URLs held out).

## 4. Did this change beat that competitor

The product's claim is pairwise. Every draft check in the demo is appended to the prediction
ledger (`python -m aeo.ranksim ledger list|verify|observe|score`), with the candidate set, model and
data fingerprints. Later observations are appended with their date, and calls are scored only
against observations made after the prediction. That ledger is the track record; it starts empty.

## 5. Confidence

The old rule picked a score-gap threshold on the backtest pairs and reported accuracy on the same
pairs. Held out by query cluster and URL, with the threshold chosen on other folds, those
"confident" calls were right 20 of 41 and 16 of 47 times on two versions of the fresh crawl. A
threshold is now used only if held-out confident calls are right at least 0.65 of the time on at
least 30 calls. On current data every call is UNSURE.

## LLM relevance

The historical "0.485 without vs 0.394 with" was 3 of 33 pairs on the bootstrap seed corpus, which
included unpublished drafts. Random judges produce a drop that large in about 8% of runs; a judge
equal to the true label plus noise (sigma 0.5) does in about 10%. That sample cannot detect a
useful judge. `ai-eval` now reports the paired interval and refuses the paid judge run below 100
labeled pairs. AI relevance stays out of the default score until held-out evidence supports it.

## What we need from you

1. `SERPER_API_KEY`, or DataForSEO login and password, or `SERPAPI_KEY`, to collect 300+ real
   Google top 10s with dates and locale.
2. Keyword lists for the extra sites and niches.
3. Repeated collections of the same queries on later days, for volatility.
4. An OpenRouter key only once there are at least a few hundred labeled pairs, so the judge can be
   measured.
5. Saved HTML for URLs that return 403 or block the crawler (currently the G2 alternatives pages,
   thedigitalprojectmanager.com, and a Facebook answers page). Nothing is filled in for them.
