# How the Google ranker is built, and how it is scored

Predicted positions come from our index and our model. They are not live Google ranks. This document says which public sources the feature list follows, which signals we cannot see, and what labeled Google results are required before an accuracy number is real.

## Two stages

Stage 1 is retrieval. BM25F scores title, H1, headings, URL, anchor text, and body. Term counts are weighted and length-normalized before the BM25 saturation function, which is the fielded form in Robertson, Zaragoza, and Taylor, [Simple BM25 extension to multiple weighted fields](https://dl.acm.org/doi/10.1145/1031171.1031181) (CIKM 2004). The Microsoft Cambridge TREC 2004 writeup uses the same idea and calls the length-normalized version BM25F: [Microsoft Cambridge at TREC-14](https://trec.nist.gov/pubs/trec14/papers/microsoft-cambridge.enterprise.pdf).

Stage 2 reranks the retrieved set with LambdaMART, the boosted-tree ranker in Burges, From RankNet to LambdaRank to LambdaMART, Microsoft Research technical report MSR-TR-2010-82 (2010), via LightGBM's `lambdarank` objective. Training uses only rows in a position file you provide. Query-level cross-validation holds out whole queries so a query is never scored by a model that trained on it.

Until that file exists, scoring uses a fixed weight vector. Those weights are a prior from the public literature above (field matches and link priors), not a fit to Google and not a claim about Google's coefficients.

## What the features stand in for

The May 2024 Google Content Warehouse API leak is the public attribute list this feature set is checked against. Rand Fishkin published the account in [An Anonymous Source Shared Thousands of Leaked Google Search API Documents](https://sparktoro.com/blog/an-anonymous-source-shared-thousands-of-leaked-google-search-api-documents-with-me-everyone-in-seo-should-see-them/). Search Engine Land's [leak report](https://searchengineland.com/google-search-document-leak-ranking-442617) and [follow-up](https://searchengineland.com/unpacking-googles-massive-search-documentation-leak-442716) name attributes this model treats as targets for proxies:

| Public attribute or system | What we compute | Limit |
| --- | --- | --- |
| titlematchScore | BM25 on the title, plus the title's share of BM25F | Our BM25, not Google's title match score |
| siteFocusScore and siteRadius | Cosine of a page embedding to the centroid of pages on the same domain in the candidate set, and one minus that cosine | Hashing embeddings unless an embedding API is configured. One page on a domain gets a neutral score |
| Anchor text | BM25 over incoming anchor text stored from the crawl | Only anchors inside our crawl |
| PageRank | PageRank and domain-level PageRank on crawled links | [US patent 6,285,999](https://patents.google.com/patent/US6285999B1/en). Not web-wide PageRank |
| Homepage authority | PageRank of the registered host's home URL when that page was crawled | Missing if the homepage was not fetched |
| bylineDate, syntacticDate, semanticDate | Date from meta, time, or JSON-LD, then a decay | No date means a zero freshness feature |
| originalContentScore (discussed in leak writeups) | Share of word trigrams in the candidate set that no other candidate uses | A proxy, not Google's originality score |
| Quality / thin pages | Word count, a thin-page flag under 150 words, a simple sentence-length readability score, and a count of ad-network markers | Not a rater score |
| Navboost | `1 / log2(prior_position + 1)` when an older position is supplied | Not click logs |

DOJ trial testimony is why Navboost is called out instead of being ignored. In the exhibit [PXRD016](https://www.justice.gov/atr/media/1398886/dl), Eric Lehman (20 Sept 2023) describes Navboost as a large query-to-document table of clicks over about 13 months, and says it is not itself a machine learning system. Search Engine Land's account of Pandu Nayak's testimony, [How Google Search and ranking works](https://searchengineland.com/how-google-search-ranking-works-pandu-nayak-435395), reports that Nayak called Navboost one of the important signals, that it memorizes clicks (13 months in the testimony described there), that RankBrain rescores roughly the top 20 to 30 documents, and that DeepRank is BERT used for ranking. A separate exhibit, [PXR0357](https://www.justice.gov/atr/media/1398866/dl), describes traditional ranking as Okapi BM25 style, with RankBrain, RankEmbed, and DeepRank as later systems that learn from logs.

We do not have those click logs. Copying the position we are trying to predict into the Navboost feature would leak the label and inflate every metric. If `prior_position` matches `position` on every row, the feature is set to zero and the report says it was withheld.

## Ground truth

Any of these can be the labeled file:

- Manual or browser checks: columns `query`, `url`, `position`, optional `prior_position`.
- Search Console export: `Query`, `Page`, `Position` (or Average Position).
- A SERP API pull from the existing SerpAPI or DataForSEO adapter, saved in the same columns. That path still needs a key. It is optional.

The file is not generated here. Positions you have not recorded are left out. They are not guessed.

## Metrics

On each held-out query:

- Pairwise accuracy: among labeled URL pairs, how often the model scores the better Google position higher. This is the number to watch first.
- NDCG at 10, with gain `max(0, 11 - position)` so a top-10 result has gain and a result past 10 has none.
- Spearman correlation of predicted rank and labeled position. Constant scores make Spearman undefined, which is reported as n/a rather than zero.
- Top-3 hit rate: the labeled position-1 URL appears in the model's top 3.

`evaluate` and `train` default to leave-one-query-out. Pass `--folds kfold` for 5 folds when there are more than 8 queries.

Heading and body match scores are missing (LightGBM NaN) when the field is blank or the match is zero, so an empty heading is not stored as a numeric zero. Title match, heading match, body match, BM25F, and entity coverage are monotone increasing. A page with fewer than 150 words is placed behind every page that has a real body. That gate is applied to the predicted score before ranks and metrics.

`test --bundle` scores a manifest of saved HTML the same way: each query uses the model trained on the other queries. Bundle URLs that are already in that query replace the stored HTML and keep the crawled link features. Bundle URLs that are not in that query are added only in the after set, and only to the queries they target. A URL the crawl never saw gets crawl PageRank 0, and domain PageRank is copied from other pages on the same host. The printed rank is a predicted slot in that candidate set, not a Google position. Competitor pages that were not crawled are named and left blank.

Pages that refuse the crawler can be supplied later as files. Put HTML in `smartcrew/saved_html/` and list `url,file` in `manifest.csv`. A missing file is skipped. The index does not invent a page. The saved model is fit on all labeled queries after that, so the saved file is for scoring. Quote the cross-validated numbers, not an in-sample score.

## What would raise the numbers

Accuracy stays unreported until the file has at least two queries with two stored pages each. To move pairwise accuracy in a way that means something for SmartCrew:

1. Real Google top 10s for as many of the 150 keywords as you can check, especially `hcss alternative` and `best all-in-one dirtworks software`. Ten URLs per query beats one URL.
2. Those URLs have to be in the crawl. A labeled URL we never fetched is counted as uncovered and dropped.
3. A second, older position column if you want a Navboost-style prior. Same-day copies of `position` will be discarded.
4. More queries, not more copies of one query. Holdout is by query.
5. Optional: `SERPAPI_KEY` or DataForSEO credentials if you would rather export the top 10 than type it. Brave results are not accepted as Google labels.

A unit-test fixture can show the trainer recover an obvious order. That fixture is synthetic and is not a Google accuracy number.

## Sources

- [Robertson, Zaragoza, and Taylor, Simple BM25 extension to multiple weighted fields](https://dl.acm.org/doi/10.1145/1031171.1031181) (CIKM, 13 Nov 2004)
- [Microsoft Cambridge at TREC-14: Enterprise track](https://trec.nist.gov/pubs/trec14/papers/microsoft-cambridge.enterprise.pdf)
- [Page and Brin, US patent 6,285,999](https://patents.google.com/patent/US6285999B1/en)
- Burges, From RankNet to LambdaRank to LambdaMART: An Overview, Microsoft Research MSR-TR-2010-82 (2010)
- [Fishkin, leaked Google Search API documents](https://sparktoro.com/blog/an-anonymous-source-shared-thousands-of-leaked-google-search-api-documents-with-me-everyone-in-seo-should-see-them/) (May 2024)
- [Search Engine Land, Google Search document leak](https://searchengineland.com/google-search-document-leak-ranking-442617)
- [Search Engine Land, unpacking the leak](https://searchengineland.com/unpacking-googles-massive-search-documentation-leak-442716)
- [Search Engine Land, Pandu Nayak on ranking](https://searchengineland.com/how-google-search-ranking-works-pandu-nayak-435395)
- [DOJ trial exhibit PXRD016, Lehman testimony excerpt](https://www.justice.gov/atr/media/1398886/dl) (20 Sept 2023)
- [DOJ trial exhibit PXR0357](https://www.justice.gov/atr/media/1398866/dl)
