# Matched pairs and `site:` labels: outside evidence for the round 3 design

Research memo, 2026-11-19, public web sources only. Nothing about this project, the client or any person was sent to an outside service. It does not repeat `ranking-mechanics.md` or `edit-effects.md`; it points to them.

Labels as in the earlier memos (STRONG, MEDIUM, WEAK). "Snippet only": seen only in a search summary. UNVERIFIED: could not confirm.

## What this means for the round 3 design

1. `site:` plus a keyword runs on Google's normal ranking path. Bare `site:` does not, and its order is "relatively random". Keyword pulls are usable labels; bare pulls are not.
2. Nobody has published how well `site:`+keyword order agrees with ordinary rank order. We must measure it first, on pairs seen in both.
3. Inside one site, Google's quality score is "largely static and largely related to the site rather than the query". Same-site pairs cancel most site authority.
4. What remains is topicality (anchors, body, clicks) plus page-level link and quality signals. Only body and title are in the HTML. Internal links need a crawl. Clicks and external links are invisible.
5. Swap pairs cancel query-independent page signals, not anchor text or per-query clicks. They isolate topicality, not pure content.
6. The best transparent result for predicting Google pair order from outside features is 62.5% to 67.5% on held-out queries (Bifet et al. 2005). A vendor quiz reports 75% for Gemini with no method. Experienced humans scored 46% to 56%.
7. Small cross-encoders beat BM25 by about 5 to 10 nDCG points zero-shot on human relevance labels, and lose on some datasets. None is validated against Google order.
8. Same-template siblings are confounded by index selection, internal-link prominence and per-query click memory. Match on template, depth and inlinks; keep pairs where both pages are indexed and retrieved.
9. I found no published case of validating an edit predictor cross-sectionally and then on real edits. Transfer is an assumption that PR 45 and Wayback cases must test.
10. A CI lower bound of 0.65 is at the top of published results. 0.75 has no transparent precedent.

## 1. Does `site:domain keyword` order reflect normal ranking?

Google statements:
- Bare `site:` "doesn't rank the results. It will generally show the shortest URL for the prefix at the top, but otherwise the results are relatively random." [Search Central](https://developers.google.com/search/docs/monitor-debug/search-operators/all-search-site). STRONG.
- Mueller (Jan 2017) on a bare site query: "I wouldn't like pull any information on the order that's displayed there". With a keyword: "if you do a site query and some keywords, that's easier because then we kind of know what to do with ranking." [SER](https://www.seroundtable.com/google-site-command-rank-order-23305.html). MEDIUM.
- Gary Illyes (June 2021), asked whether core updates change `site:` results: "site: on its own isn't directly (it's using a different, disconnected ranking path)" and "site:+query is affected (it's using the normal ranking path)". [SER](https://www.seroundtable.com/google-site-command-rankings-core-updates-31593.html). MEDIUM: one informal tweet, but the only direct statement.
- Bare `site:` order once tracked "roughly" PageRank (Matt Cutts, 2011, [SER](https://www.seroundtable.com/google-site-command-ranking-12858.html)). The current doc above replaces that.

What the warnings refer to:
- Mueller (Aug 2021): "a site: query is not meant to be complete, nor used for diagnostics purposes", in the context of counts: "don't worry about the counts shown in a site: query." [SEJ](https://www.searchenginejournal.com/google-a-site-search-doesnt-show-all-pages/416662/). MEDIUM.
- Operators "are bound by indexing and retrieval limits"; Illyes: "all search operators are heavily affected by our systems' retrieval limits". [SER](https://www.seroundtable.com/google-search-operators-limitations-31800.html). MEDIUM. This is about which pages appear, not order. Large sites can lose pages from a `site:`+keyword list.
- So the warnings cover counts, completeness and bare-`site:` order. None says `site:`+keyword order differs from normal ranking. A journalist's claim that site search "does not use Google's regular ranking algorithm" ([SEJ 2021](https://www.searchenginejournal.com/google-shows-how-to-use-search-operators-for-seo/414377/), [2020](https://www.searchenginejournal.com/google-site-search/355248/)) conflicts with Illyes for keyword queries. WEAK.

Known differences from an ordinary SERP:
- Host crowding: ordinary results "generally won't show more than two web page listings from the same site" (`ranking-mechanics.md` section 3). Searchmetrics (2019): two URLs from one domain in 44.2% of searches, three in 3.5% ([SEJ](https://www.searchenginejournal.com/googles-diversity-update-gives-smaller-sites-a-chance-to-rank/314635), MEDIUM). In a `site:` list, the third page and below have no ordinary position to check against. Whether the second ordinary listing sits at its own score position or is pulled up beside the first is undocumented. UNVERIFIED.
- Near-duplicate filtering: old forum posts describe one result shown when titles and snippets match, so near-identical siblings may collapse. [SER archive](https://www.seroundtable.com/archives/000518.html). Snippet only, WEAK, UNVERIFIED for today.
- Personalization: Danny Sullivan (Aug 2021): "extremely light, actually fairly rare, when it comes to results themselves actually having a ranking change", location excluded. [SER](https://www.seroundtable.com/google-personalization-light-rare-ranking-signal-31956.html). MEDIUM. Serper fixes location, so the risk is small.
- Measured agreement between `site:`+keyword order and ordinary order: I found no published study. UNVERIFIED.

Reading: `site:`+keyword order is probably Google's normal score order for that site's pages, minus crowding and dedup filters, truncated by retrieval limits. That rests on one tweet and one 2017 remark, so measure it. Bare `site:` order would teach a model URL length.

## 2. Which page of a site ranks for a query

- Mueller (Sept 2025): "If you have 3 different pages appearing in the same search result, that doesn't seem problematic to me." [SEJ](https://www.searchenginejournal.com/google-answers-seo-question-about-keyword-cannibalization/556472/). MEDIUM.
- Ahrefs (Feb 2024) had 9.7k keywords where its own site ranked with several URLs; of 80 reviewed, "I found only one case that needed action". Histories showed URLs "ranking and then dropping for months only to reappear". [Ahrefs](https://ahrefs.com/blog/multiple-rankings-study/). MEDIUM.
- Trial exhibit PXR0356, notes of an 18 Feb 2025 call with Google engineer HJ Kim [DOJ](https://www.justice.gov/atr/media/1398871/dl), STRONG (call notes):
  - Anchors are "a source page pointing to a target page", Body is "terms in the document", Clicks is "how long a user stayed". "ABC signals are the key components of topicality", and "Topicality is connected to a given query".
  - Quality is "Generally static across multiple queries"; "Q is largely static and largely related to the site rather than the query".
  - PageRank is "a single signal relating to distance from a known good source", an input to Quality.
- Google: "Our ranking systems are designed to work on the page level" and "Site-wide signals and classifiers are also used"; good site-wide signals do not mean "all content from a site will always rank highly". [Search Central](https://developers.google.com/search/help/helpful-content-faq). STRONG.

Factors. "Ours": (a) page HTML alone, (b) site crawl, (c) invisible. Leak fields from the [Content Warehouse docs](https://google-api-content-warehouse.hexdocs.pm/0.4.0/GoogleApi.ContentWarehouse.V1.Model.PerDocData.html); a field shows a signal exists, not its weight.

| Factor | Unit | Source | Ours |
| --- | --- | --- | --- |
| Body and title term match ("B"; older approach "in the style of Okapi BM25", PXR0357) | page, query | PXR0356, `ranking-mechanics.md` 1.1 | a |
| Internal anchors (`isLocal`, "on the same domain"); links are "a signal when determining the relevancy of pages" | page, query | [AnchorsAnchor](https://google-api-content-warehouse.hexdocs.pm/0.4.0/GoogleApi.ContentWarehouse.V1.Model.AnchorsAnchor.html), [Google](https://developers.google.com/search/docs/crawling-indexing/links-crawlable) | b |
| External anchors and links | page, query | same | c |
| `onsiteProminence`: "measures the importance of the document within its site" | page | PerDocData | b, approximate |
| PageRank; `homepagePagerankNs` ("page-rank of the homepage of the site") | page, site | PerDocData, PXR0356 | c (b: internal share only) |
| Navboost, "a QD table, a query-to-document lookup table" (PXR0357) | page, query | `ranking-mechanics.md` 1.1 | c |
| Chrome-based "(popularity) signal" | unclear | PXR0356 | c |
| `siteAuthority`, NSR, site chard and tofu, `titlematchScore` ("Titlematch score of the site") | site | [CompressedQualitySignals](https://google-api-content-warehouse.hexdocs.pm/0.4.0/GoogleApi.ContentWarehouse.V1.Model.CompressedQualitySignals.html), [NsrData](https://google-api-content-warehouse.hexdocs.pm/0.4.0/GoogleApi.ContentWarehouse.V1.Model.QualityNsrNsrData.html) | cancels in a pair |
| `pqData` ("Encoded page-level PQ signals"), `contentEffort` | page | same, `ranking-mechanics.md` 4 | a, via our proxy |
| Page embedding, `siteRadius` ("how far page_embeddings deviate from the site_embedding"), `siteFocusScore` | page, site | [TopicEmbeddings](https://google-api-content-warehouse.hexdocs.pm/0.4.0/GoogleApi.ContentWarehouse.V1.Model.QualityAuthorityTopicEmbeddingsVersionedItem.html) | a page; b distance to site |
| `OriginalContentScore` (0 to 127) | page | PerDocData | a, via proxy |
| `lastSignificantUpdate` | page | PerDocData | partly a (visible dates) |
| `queriesForWhichOfficial` ("the official page") | page, query | PerDocData | c; brand queries |

Studies:
- Zyppy: 23M internal links, 1,800 sites, about 520k URLs, against Search Console clicks. URLs with 0 to 4 internal links averaged 2 clicks; 40 to 44 links got "4 times that many". Anchor text variety had the strongest association. Pooled, "mostly a correlation study". [Zyppy](https://zyppy.com/seo/internal-links-study/). MEDIUM.
- Salminen et al. 2019 (733 page-one pages, 121 Finnish keywords): links mattered most, keyword features little. Its "average accuracy of 0.86" is an NDCG, high even for random order on page one, not pair accuracy. [PDF](http://www.bernardjjansen.com/uploads/2/4/1/8/24188166/a6-salminen.pdf). MEDIUM.

No source gives the within-site weights of B (ours), A (partly ours via crawl), C (not ours) and page-level link and quality signals.

## 3. Same-template pages as natural experiments

- I found no published ranking analysis that uses same-site, same-template siblings cross-sectionally to isolate content. Searches returned vendor checklists without data.
- The controlled version exists: split tests divide one template's pages into control and variant groups matched on traffic, variability and seasonality ([SearchPilot](https://searchpilot.com/resources/blog/how-searchpilot-analyses-test-data), MEDIUM, vendor).
- Su et al. (ACM TWEB 2014, Google data from about 2010) used a different natural control: only pages from the last 24 hours (`tbs=qdr:d`), so age and PageRank were equal. [PDF](https://networks.cs.northwestern.edu/publications/a8-su.pdf). STRONG method, old data.
  - Content features alone recovered about 80% of Google's top 10 for 92% of keywords, and about 90% for 77%. On ordinary queries with PageRank and age, 7 or more of 10 for 78% of keywords.
  - The metric is top-10 membership out of the top 100, not order inside the top 10.
  - Their first attempt, cloned and artificial sites, failed: Google indexed "less than 20 per domain" and crawled "once per week".

Pitfalls:
- Index selection. Google's doorway examples include "pages targeted at specific regions or cities that funnel users to one page" and "substantially similar pages that are closer to search results than a clearly defined, browseable hierarchy" [spam policies](https://developers.google.com/search/docs/essentials/spam-policies), STRONG. Mueller on 1,300 city pages (2019): "That sounds like doorway pages" ([SER](https://www.seroundtable.com/google-city-landing-pages-doorway-pages-28670.html)). On many unindexed URLs (Nov 2026): "Usually this is a sign that Google's quality systems are unconvinced about your site" ([SER](https://www.seroundtable.com/google-crawled-discovered-currently-not-indexed-42507.html)). MEDIUM. Visible siblings are a selected set, which can understate content effects.
- Site-level suppression should mostly cancel in a pair. Whether it acts per template or per page is undocumented.
- Click history differs per URL: Navboost is per query and URL, so a bigger-city sibling carries more clicks. Same-query pairs do not remove this.
- Competition differs per query. Comparing siblings' ordinary positions across queries mixes in competitor strength. Same-query pairs avoid it.
- Internal links differ by template position (hub or leaf, pagination depth). See Zyppy above.

## 4. Neural relevance against BM25, and content against Google order

Benchmarks (human relevance labels, not Google order). STRONG.
- MS MARCO, in-domain: `ms-marco-MiniLM-L-6-v2` TREC DL19 nDCG@10 74.30, dev MRR@10 39.01 ([SBERT](https://www.sbert.net/docs/cross_encoder/pretrained_models.html)); BM25 0.5058 and 0.1840 ([Pyserini](https://castorini.github.io/pyserini/2cr/msmarco-v1-passage.html)). Two sources.
- BEIR, 18 datasets zero-shot ([Thakur et al. 2021](https://ar5iv.labs.arxiv.org/html/2104.08663); averages computed from the per-dataset table): BM25 0.423, BM25 plus MiniLM cross-encoder 0.476 (better on 16 of 18), TAS-B 0.415, ANCE 0.389, DPR 0.237. BM25 wins Touché-2020, 0.367 against 0.271.
- Reranking BM25's top 1000 ([Rosa et al. 2022](https://ar5iv.labs.arxiv.org/html/2206.02873)): BM25 0.4328, MiniLM 0.4889, monoT5-220M 0.4780, monoT5-3B 0.5321. BM25 still wins Touché, ArguAna and Signal-1M.
- Dense, 15 BEIR datasets: BM25 41.7, E5 base before fine-tuning 42.9 ("the first model that outperforms the strong BM25 baseline" without labels), fine-tuned 48.7 ([E5](https://arxiv.org/abs/2212.03533)); GTE-base 44.2 ([GTE](https://ar5iv.labs.arxiv.org/html/2308.03281)). BGE and `bge-reranker-base` against BM25 on BEIR: UNVERIFIED.
- No paper I found reports pairwise accuracy. Bifet et al. give the conversion: pair precision = 50% + 50% x Kendall tau.

Content and Google order:
- Bifet, Castillo, Chirita and Weber (AIRWeb 2005) [PDF](https://ingmarweber.de/wp-content/uploads/2013/07/An-Analysis-of-Factors-Used-in-Search-Engine-Ranking.pdf), STRONG, old data.
  - 4 categories of 12 queries (7 train, 2 validation, 3 test), top 100 results, pairwise classification.
  - Best single feature: 57% to 70% pair precision. Best model on test queries: 62.5% to 67.5% on all pairs.
  - "the scoring function cannot be approximated well"; "many crucial features are hidden and cannot be observed from the outside".
  - Text similarity alone: 69.7% on long technical queries, 50.9% to 55.4% in the other three categories.
- SearchPilot quiz, "which of two ranking pages ranks higher":
  - 2016, almost 300 people: UK 46%, US "around 56%" (Moz post read via a [mirror copy](https://leslierichardsattorney.blogspot.com/2016/10/most-seos-are-no-better-than-coin-flip.html); moz.com blocked).
  - 2025 re-run: Gemini "scoring 75%"; a 2016 TensorFlow model got "about two-thirds" ([SearchPilot](https://www.searchpilot.com/resources/blog/ai-outperforms-humans-in-our-seo-quiz)).
  - MEDIUM to WEAK: pair count, pair selection and model inputs are unpublished.
- Content-tool scores correlate with position at Spearman about 0.2 to 0.3 (Surfer 0.28, 10,000 queries; vendor, no link controls; `edit-effects.md` section 4). My arithmetic: if within-query and roughly bivariate normal, Kendall tau is about two thirds of Spearman, so 0.28 means about 59% pair accuracy, close to our BM25F 0.597.
- Query-to-page cosine similarity against Google position: no published numbers found. UNVERIFIED.

## 5. How experimenters validate without waiting on one page

- Split tests compare variant pages with a forecast built from control pages (SearchPilot, first CausalImpact, then a neural network, link above). CausalImpact uses control series as "synthetic controls" ([Brodersen et al. 2015](https://research.google/pubs/inferring-causal-impact-using-bayesian-structural-time-series-models/)), STRONG. Both need weeks and traffic, and measure clicks, not rank.
- SERP similarity: practitioners treat two queries as one intent when their top 10 share 3 or more URLs ([HubSpot](https://blog.hubspot.com/marketing/keyword-clustering)) or 4 to 6 ([OnCrawl](https://www.oncrawl.com/on-page-seo/keyword-clustering-using-python-serp-api/)). Snippet only, WEAK. Useful to define "parallel" queries for swap pairs.
- Draft scorers (Surfer, Clearscope, Market Brew) score against today's SERP; none publishes validation against rank changes after edits (`edit-effects.md` section 4). One vendor claims "92%" confidence of top 10 ([Medium](https://datamarketinglabs.medium.com/successfully-predicting-google-rankings-at-the-cutting-edge-of-data-science-41a486dcfda1); snippet only, HTTP 403). WEAK.
- Cross-sectional validation then confirmation on edits: no precedent found. The nearest are Su et al. (a cross-sectional model; their own-page test failed on slow crawling) and ranking competitions against known rankers (`edit-effects.md`). No published number for how cross-sectional accuracy transfers to edits.

## 6. Objections and fixes

**Objection 1. The label may not be Google's ordinary order.** Agreement is unmeasured. Crowding hides the third page and below, dedup may collapse siblings, retrieval limits truncate large sites. "Normal ranking path" rests on one tweet. Deep pairs may be pages no ordinary SERP shows.
- Calibrate first: for same-site pairs that also appear in an ordinary top 30 for the same query, measure how often `site:` order agrees. Freeze that rate as the label ceiling.
- Pull each `site:` query on 2 or 3 days. Keep pairs whose order never changes; report the flip rate as label noise.
- Score only pairs where both pages are in the `site:` top 5; report accuracy by depth and rank gap.
- Drop brand and navigational queries, homepages, and pairs where one URL path is a prefix of the other.

**Objection 2. Within-site order is not content order.** Quality cancels, but anchors, PageRank, onsite prominence and per-URL clicks remain. A model can win by learning "hub beats leaf".
- Match pairs on URL template, path depth and crawled internal inlink count; report accuracy within inlink-matched strata.
- Fit HTML-only and HTML-plus-crawl models (internal inlinks, internal anchor text). Count only HTML-only as "content"; report the gap.
- Use swap pairs, requiring parallel queries (3 or more shared top-10 URLs). Anchors and clicks still differ per query; say so.

**Objection 3. Settled order is not edit response.** Settled pages carry up to 13 months of click memory and years of links. An edit moves B quickly, A and C slowly. `edit-effects.md` found unreliable signs and small effects. The November 2026 core update is still rolling out (`ranking-mechanics.md` section 0), so "settled" is weak this month.
- Report cross-sectional accuracy as an upper bound for edit direction, never as edit accuracy.
- Test transfer on the PR 45 ledger, Wayback pairs with settled outcomes, and the counterfactual swap diagnostic.
- Report pairs of recently first-seen pages separately, where click memory is small (the Su et al. idea).
- Re-pull labels after the update is marked complete.

**Also.** 45 pairs per credit come from 10 URLs and one query, so they are highly dependent. Bootstrap by site and query, and size the test in sites, not pairs.

## Sources

Not opened: moz.com (blocked; mirror used), Search Engine Land (403), Data SEO Labs on Medium (403), SAGE Web Intelligence 2025 paper (403), the original Searchmetrics 2019 report (not found). The "simulated traffic" description of `onsiteProminence` in some blogs is not in the field text I read: UNVERIFIED.

All links are inline. Primary: Search Central (`site:` operator, ranking FAQ, spam policies, links), PXR0356, hexdocs 0.4.0 leak fields, CausalImpact, and the Bifet, Su, Salminen, Thakur, Rosa, E5 and GTE papers. Google staff as reported: SER 23305, 31593, 12858, 31800, 31956, 28670, 42507; SEJ 416662, 556472. Vendors: Ahrefs, Zyppy, SearchPilot, Moz mirror, Searchmetrics via SEJ 314635, Montti (SEJ 414377, 355248), HubSpot, OnCrawl, SER 000518.
