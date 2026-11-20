# Cross-site order: published evidence and how Google mixes page relevance with site standing

Research memo, 2026-11-20. Public web sources only. Nothing about the client, the project or any person went into a search, a URL or a request. Zero Serper credits spent. No held-out list was read. It does not repeat `ranking-mechanics.md`, `r3-matched-pairs.md` or `edit-effects.md`; it points to them.

Grades. STRONG: primary source read in full or in the relevant part. MEDIUM: credible secondary source read, or a primary abstract only. WEAK: one informal source. SNIPPET ONLY: seen only in a search summary. UNVERIFIED: could not confirm. A patent shows a design, not a deployment. A leaked field shows that a signal exists, not its weight.

Three local numbers in this memo are new and are not outside evidence: a simulation (section 5.2, code in the appendix), a Spearman to pair-accuracy conversion (section 2.2), and a descriptive count over our own ordinary top 10s (section 5.3). Each is labeled LOCAL.

## What this means for the master plan

1. **Shape of the cross-site model.** Score each (page, search) as topicality plus site standing, added in log space, and train it on pairs. Google's own call notes say top-level signals are "a linear combination of log of individual raw signals" and are combined into one "Final IR" score, with topicality, Navboost and quality as the high-level buckets (PXR0357, PXR0356). The Panda patent multiplies a page's initial score by a site factor. The frozen page-blend is the topicality term. The site term is new.
2. **Do not use global popularity or global link rank as the site term.** Inside our top 10s they point the wrong way (Tranco 0.392, Common Crawl PageRank 0.420, harmonic centrality 0.377). Section 5 explains why: range restriction shrinks any true authority effect inside a top 10, global popularity measures breadth rather than standing on the topic, and big platforms (video, social, review directories) take slots low on the page. Reddit is the one large platform that sits high.
3. **Build a niche-conditioned site term from free data.** Candidates: (a) the site's footprint in the 155-search universe computed leave-one-search-group-out (share of other groups where it ranks in the top 10 or 30, median position there); (b) site type (vendor, review directory, listicle publisher, forum, video, social, other); (c) site topical focus (similarity of the site's indexed pages to the search topic, an analog of the leaked `siteFocusScore`); (d) niche link counts from the local edges table (links from other indexed niche pages), a topic-restricted authority. All are free.
4. **Guard the site term against domain memorization.** A 2017 practitioner model got "OVER 80%" on top-10 pairs while it was "just learning: domain A > domain B > domain C", and 65 to 66% once domains were held out (Critchlow slides). Compute footprint features leave-group-out and evaluate with sites and search groups held out, exactly as in round 3.
5. **Fit site intercepts, then predict them from features.** Fit a Bradley-Terry model with a per-site intercept on training groups of the ordinary lists. Then learn a map from the free site features in item 3 to the intercept, so an unseen site gets an intercept. This is the step that lets a small vendor be placed against incumbents.
6. **Split by search type before fitting weights.** Google: "The weight applied to each factor varies depending on the nature of your query." Use at least: brand or navigational, "alternative" and "vs", "best" and "top", pricing and free, generic category. Our own top 10s show platform slots sitting low on category and "best" searches and Reddit sitting high on all types (section 5.3). Fit type priors and type-specific site weights.
7. **Set targets from the ceiling, not from the same-site number.** For cross-site pairs inside an ordinary top 10 with sites held out, 0.62 to 0.66 would match the best transparent published results. 0.70 has no transparent precedent. Expect adjacent pairs near 0.55. Never compare a cross-site number with the same-site 0.709.
8. **Predict position by anchored insertion, and score tiers.** Keep the incumbents in their observed consensus order. Insert the changed page with P(page above incumbent j) from the pairwise model. Predicted position is 1 plus the sum of those probabilities. Report a band. Inside a top 10 a flat guess at position 5 is within 3 places for 7 of 10 positions, and a model with pair accuracy 0.65 is within 3 about 0.73 of the time (section 6), so exact position inside a top 10 is a weak test. Score tiers (top 3, 4 to 10, 11 to 30, below 30) and the direction of change for an edit.
9. **Use the deep list for the site term's test.** Range restriction is weaker over 20 to 30 places. In the simulation, an authority component that is half of the score orders 0.72 of pairs across the candidate pool but 0.58 inside the top 10.
10. **Two cheap falsification checks on design data before building more.** (a) Fit content-only, site-only and both on the training groups' ordinary lists; keep the site term only if "both" beats content-only with the two-way interval above zero. (b) Recompute the global popularity sign after controlling for site type. If the wrong sign disappears, the cause is page composition, not anti-authority, and type priors may be enough.
11. **Do not buy or pull authority data.** Industry link metrics correlate 0.13 to 0.37 (Spearman) with position over top 20 to 50 lists, which is 0.54 to 0.63 pair accuracy before range restriction to a top 10. They are paid. No Serper credits are needed for anything in this memo.

## 1. Every transparent attempt to predict Google's order from outside features

What it means for the plan: the best held-out results are about 62% to 67% of pairs, and in every old study the strongest feature was a Google-published or link-based authority number that we do not have. Content-only models did well only when authority was removed by design.

| Study | Data and sample | Target and metric | Held out? | Result | Link or site against content | Grade |
| --- | --- | --- | --- | --- | --- | --- |
| Bifet, Castillo, Chirita, Weber, AIRWeb 2005 [PDF](https://ingmarweber.de/wp-content/uploads/2013/07/An-Analysis-of-Factors-Used-in-Search-Engine-Ranking.pdf) | 4 categories of 12 queries (7 train, 2 validation, 3 test), Google top 100, 2005 Google API | Pair precision, all pairs | Yes, test queries | Test: Arts 63.7%, States 64.6%, Spam 62.5%, Multiple 67.5%. Pairs further apart ("shifted"): 69.1% to 78.1% | Strongest single feature: in-links (States 66.3%, Spam 57.0%) or toolbar PageRank (Arts 58.7%, second to a language feature at 59.4%); text similarity only for long technical queries (69.7%). Best models were "only marginally better" than the strongest single feature, and the best pruned trees were mostly one node | STRONG |
| Su, Hu, Kuzmanovic, Koh, WI-IAT 2010, ACM TWEB 2014 [PDF](https://networks.cs.northwestern.edu/publications/a8-su.pdf) | 60 single-word keywords (15 train, 45 test), top 100 each, 17 features | Recall of Google's top 10 out of the top 100 (not order) | Yes, test keywords | "7 out of the top 10 pages for 78% of evaluated keywords"; LP model: hit rate above 80% for 54% of keywords | "page rank is the dominant factor", then keyword in host name, title, meta description, URL path. Content only on pages from the last 24 hours: about 80% hit for 92% of keywords. Content only on ordinary pages: "much worse" after removing PageRank and age | STRONG |
| Luh, Yang, Huang, Online Information Review 2016 [abstract](https://emerald.com/insight/content/doi/10.1108/OIR-04-2015-0112/full/html?skipTracking=true) | Not stated in the abstract | Re-rank top results with a weighted sum of a few factors, compare with original ranks | Not stated | Best with the top 20 results; no accuracy figure visible | "PageRank ... is the dominant factor", title second, snippet and URL about equal | MEDIUM (abstract only) |
| Evans, Internet Research 2007 [abstract](https://www.emerald.com/insight/content/doi/10.1108/10662240710730470/full/html) | 50 optimized pages from one SEO contest query, 50 non-optimized pages | Descriptive | No | No statistics in the abstract | "Recognizes the importance of PageRank and links" plus directories and bookmarking sites | MEDIUM (abstract only) |
| Zhu and Wu, 2011 (IEEE, "Research and Analysis of Search Engine Optimization Factors Based on Reverse Engineering") [record](https://search.isc.ac/inventory/49/2658414.htm) | 200,000 crawled pages, Google top 20 | Descriptive factor analysis | Unknown | Five "core" factors (not named in what I saw) | PageRank, URL and HTML features | SNIPPET ONLY |
| Critchlow (Distilled), "Gaps in the algorithm", 2017 [slides](https://www.slideshare.net/slideshow/gaps-in-the-algorithm/78085820) | Google top 10 only, "every pair of URLs for every query"; size not given | "does page A outrank page B for query X" | Domains held out in the final version | "OVER 90%" (rank leaked into features), then "OVER 80%" while "just learning: domain A > domain B > domain C", then "65-66% accuracy on large diverse keyword sets" with unseen domains | Features: Moz Domain Authority, linking root domains, TLD, H1 match, links on page; "fairly naive on-page metrics" | MEDIUM (primary slides, no method) |
| SearchPilot quiz, 2016 model and 2025 rerun [post](https://www.searchpilot.com/resources/blog/ai-outperforms-humans-in-our-seo-quiz) | New queries; pair count and pair selection not stated | Which of two pages ranks higher | Unknown | 2016 TensorFlow model "about two-thirds"; Gemini "scoring 75%" | Not stated | WEAK |
| Salminen et al. 2019 | 733 page-one pages, 121 Finnish keywords | Reported 0.86 is an NDCG-like average, not pair accuracy | Unknown | See `r3-matched-pairs.md` | Links most important | MEDIUM (covered earlier) |
| Almadhoun and Malim, Web Intelligence 2025 [record](https://journals.sagepub.com/doi/10.3233/WEB-230239) | Google results across categories, on-page features from a crawler | Rank class | Unknown | "over 25% higher accuracy" with multi-category training; absolute numbers not seen | Internal linking the strongest on-page factor | SNIPPET ONLY (abstract via search) |
| Oakes, Search Engine Land 2016 [article](https://searchengineland.com/experiment-trying-predict-google-rankings-253621) | 500,000 results, service queries in 200 US cities, top 20, Majestic link data | Rank prediction | Unknown | Not seen (page blocked) | Trust Flow, Citation Flow, text statistics | SNIPPET ONLY |
| LLM-based attempts through 2026 | | | | Only the SearchPilot Gemini figure above. I found no paper that tests an LLM against Google's live order. LLM-judge papers compare with human labels, not Google | | not found |

Also seen and set aside: a 2021 classifier of expert-labelled on-page SEO quality (54.7% to 69.7% against a 48.8% majority class, [MDPI](https://mdpi.com/1999-5903/13/1/9)) predicts expert labels, not Google order (SNIPPET ONLY). A 2022 master's thesis reports 64% for an LSTM on a random row split of repeated monthly rows ([NCI](https://norma.ncirl.ie/6121/1/shubhamgarg.pdf)); the split leaks, so the number means nothing (WEAK). A 2024 voice-search classifier reports 77.98% on 2,960 pages ([Springer](https://link.springer.com/article/10.1007/s10462-024-10780-9)); target not seen (SNIPPET ONLY).

Two lessons for us:
- The old studies' strongest feature was Google's own published toolbar PageRank or Google's own `link:` counts. Neither exists today. That makes their 62% to 67% an optimistic yardstick for us, not a floor.
- Domain identity is the easiest thing to learn and the easiest thing to leak. Critchlow's drop from over 80% to 65 to 66% when domains were held out is the size of that leak on top-10 pairs.

## 2. Industry correlation studies, converted to pair accuracy

What it means for the plan: public link and authority metrics order about 0.54 to 0.63 of pairs over top 20 to 50 lists. Inside a top 10 they will do worse. Domain-level scores do worse than page-level link counts in the newest large study.

### 2.1 The numbers

| Metric | Study | Sample and positions | Statistic | Value | Pair accuracy (LOCAL conversion) | Grade |
| --- | --- | --- | --- | --- | --- | --- |
| Domain Rating (Ahrefs) | Ahrefs, Jan 2025 [post](https://ahrefs.com/blog/links-matter-less-but-still-matter) | Top 1,000,000 US keywords, top 20 | Spearman; per-SERP or pooled not stated | 0.131 | 0.544 | STRONG (vendor primary, thin method) |
| Referring domains (page, by inference) | same | same | same | 0.255 (followed 0.250) | 0.586 | STRONG (same caveat) |
| Backlinks | same | same | same | 0.248 | 0.583 | STRONG (same) |
| Internal inlinks | same | same | same | 0.117 | 0.539 | STRONG (same) |
| Keyword in URL | same | same | same | 0.034 | 0.511 | STRONG (same) |
| Referring domains, 2019 study | same post | 2,000 to 5,000 searches a month, non-branded | same | 0.29 | 0.597 | STRONG (same) |
| Domain Authority Score (Semrush) | Semrush 2024 [PDF](https://lp.semrush.com/rs/519-IIY-869/images/2024-Ranking-Factors-Study-ENG.pdf) | 16,298 English keywords, top 20, about 300,000 positions | "Spearman correlation ... between a position and a factor's average value per position" | 0.21 | not convertible | STRONG for the value, method ambiguous |
| Page Authority Score (Semrush) | same | same | same | 0.19 | not convertible | same |
| Referring domains, domain and URL level (Semrush) | same | same | same | 0.18 and 0.18 | not convertible | same |
| Text relevance (Semrush, embeddings) | same | same | same | 0.47 | not convertible | same |
| URL organic traffic, domain organic traffic (Semrush) | same | same | same | 0.33, 0.28 | not convertible | same |
| Page Authority (Moz) | Moz 2013 / 2015 | 14,000+ keywords (2013), 16,521 SERPs May 2015, top 50 | Mean of per-keyword Spearman | 0.39 / 0.37 | 0.633 / 0.626 | Method MEDIUM ([2013 slides](https://www.slideshare.net/slideshow/moz-2013-ranking-factors-matt-peters-mozcon-24036187/24036187), [2015 deck](https://speakerdeck.com/distilled/rand-fishkin-search-ranking-factors-in-2015-what-data-opinions-and-testing-reveal)); 0.39 MEDIUM (several secondary sources); 0.37 WEAK |
| Domain Authority (Moz) | Moz 2013 / 2015 | same | same | 0.27 / 0.26 | 0.590 / 0.587 | WEAK (one compilation, [here](https://www.bestseopodcast.com/research/ranking-factor-studies); moz.com and web.archive.org were unreachable) |
| Linking root domains to the page (Moz) | Moz 2013 / 2015 | same | same | 0.29 / 0.30 | 0.597 / 0.601 | WEAK (same compilation) |
| Keyword in title / in body (Moz 2015) | same | same | same | 0.02 / 0.11 | 0.507 / 0.537 | WEAK (same) |
| Backlinks / referring domains (Searchmetrics 2015) | Searchmetrics | 10,000 keywords, top 30 | Per-keyword Spearman | 0.28 / 0.22 | 0.594 / 0.574 | WEAK (values from the compilation, read off charts) |
| Domain Rating against URL Rating (Backlinko, Ahrefs data) | [Backlinko](https://backlinko.com/search-engine-ranking) | 11.8 million results, top 10 | No coefficients | "a website's overall authority had a stronger correlation to rankings than the authority of the page itself"; median UR 12 at positions 1 to 6 against 11 at 7 to 10 | none | STRONG for the statements |
| SISTRIX | | | | No ranking-factor correlation study found | | not found |

Notes:
- The two newest large studies disagree on domain against page. Backlinko (2020 data) says site authority correlates more than page authority. Ahrefs (2025) gives Domain Rating 0.131 against page referring domains 0.255. Both are vendor studies of the same vendor's metrics.
- Semrush's stated method correlates position with per-position averages. A clean monotone trend over 20 averages would give a value near 1, so 0.21 cannot be read as within-SERP order. Do not convert it.
- Moz averaged per-SERP Spearman over 50 positions. That includes many far pairs that are easy to order.

### 2.2 Conversion and what it implies (LOCAL)

For a bivariate normal, Pearson r = 2 sin(pi times Spearman / 6), Kendall tau = (2 / pi) arcsin(r), and pair accuracy = 1/2 + arcsin(r) / pi. For values below 0.5, pair accuracy is close to 0.5 + Spearman / 3. Ties are ignored, and averaging per-SERP correlations first makes the conversion approximate.

- Spearman 0.30 is pair accuracy 0.601. 0.37 is 0.626. 0.47 is 0.662.
- In reverse: pair accuracy 0.65 needs Spearman 0.44. Our same-site 0.709 corresponds to Spearman 0.59. Persistence at 0.87 corresponds to 0.91.
- So the best single public metric, on deep lists, orders about 60% to 63% of pairs. That is in line with Bifet and Critchlow.
- A small correlation does not mean a small weight. In the simulation of section 5.2, an authority term that makes up half of Google's score shows a per-SERP Spearman of 0.30 over the top 50, 0.25 over the top 20 and 0.21 over the top 10. The Moz-type values are what an important but range-restricted signal looks like.

## 3. Learning-to-rank datasets: what each feature family carries

What it means for the plan: these datasets measure agreement with human labels, not with an engine's order, so they only bound our question. Two lessons carry over. Click features add a few NDCG points on top of everything else. A text-only model reaches about half the NDCG of a production feature model whose candidate lists the production engine chose.

- Yahoo 2010 challenge: one text-match feature carries most of the label signal (BM25F-SD NDCG 0.732 against 0.790 for a GBDT on all features). Already in `ranking-mechanics.md` section 5. STRONG.
- MSLR-WEB10K, click features: a LambdaMART with and without the three click features (query-URL click count, URL click count, URL dwell time) scored nDCG 0.4636 and 0.4410 on the test set ([Ferro, Lucchese, Maistro, Perego, IRJ 2019 preprint](https://iris.unive.it/retrieve/e4239ddd-fcd0-7180-e053-3705fe0a3322/special_issue_IRJ_2019.pdf)). STRONG.
- MSLR-WEB30K, importance: PageRank, query-URL click count and LMIR had the highest LambdaMART importance (Lucchese et al. 2022, in `ranking-mechanics.md`). STRONG.
- MSLR link group (PageRank, SiteRank, in- and out-links, QualityScore): I found no published ablation that removes it. not found.
- Istella22 ([Dato, MacAvaney, Nardini, Perego, Tonellotto, SIGIR 2022](https://openportal.isti.cnr.it/data/2022/469573/2022_469573.preprint.pdf)), 2,198 test queries, 220 production features (including PageRank), candidates from the Istella engine's first stage. STRONG:

| Method | NDCG@10 | P@1 |
| --- | --- | --- |
| BM25 default / tuned | 0.228 / 0.385 | 0.433 / 0.434 |
| monoT5 trained on MS MARCO (text only, transfer) | 0.299 | 0.557 |
| monoT5 tuned on Istella (title, URL, text) | 0.452 | 0.858 |
| LambdaMART on the 220 features | 0.819 | 0.956 |
| LambdaMART plus monoT5 score | 0.815 | 0.951 |

  The authors say the LambdaMART result is high "at least in part" because "the same features are used by the ranking models in the Istella engine itself; to an extent, the model is able to learn to mimic the documents that annotators were shown." Adding the neural text score added nothing. Reading for us: a text model, even one tuned in domain, gets about half of what the engine's own feature set gets. Predicting Google's order is the mimicking task, and the engine's own non-text features are what we lack.

## 4. How Google combines topicality with site-level standing

What it means for the plan: the documented structure is staged and mostly log-additive, with a site-level quality term that is mostly query-independent, a click memory per query and document, and late re-rankers we cannot see. A two-term log-additive model with a query-type interaction is a fair first approximation. The late stages (RankBrain on the top 20 to 30, twiddlers) are part of why the top-10 ceiling is low.

### 4.1 Stages

- Retrieval walks postings lists sorted "in such a way that the likely good pages, which are high quality -- so sometimes these are sorted by page rank ... are sort of earlier"; this gives tens of thousands of documents (Nayak, 18 Oct 2023 PM, p. 6406 to 6407, [transcript](https://thecapitolforum.com/wp-content/uploads/2023/10/101823-USA-v-Google-PM.pdf)). STRONG. Consequence: the candidate set is enriched with high-quality pages that match the words only loosely.
- "a bunch of signals" then cut tens of thousands to "several hundred". Navboost "helps" pull documents into that set but "is not the only factor", "because there might be lots of documents that don't have clicks" (same transcript, p. 6404 to 6407). STRONG.
- "RankBrain looks at the top 20 or 30 documents and may adjust their initial score"; "too expensive to run on hundreds or thousands of results" (same transcript, p. 6431). STRONG.
- Twiddlers "re-rank a set of already selected results" (PXR0357, [DOJ](https://www.justice.gov/atr/media/1398866/dl)). STRONG.

### 4.2 How signals combine

- "Top-level signals are a linear combination of log of individual raw signals. Signals are formulated such that their impact on ranking is monotonic relative to the signal." A debugger shows each of the 10 blue links with a score "for each top-level signal as well as corresponding total score 'Final IR' across all signals" (PXR0357). STRONG (call notes). The final combination is redacted. Log-linear means multiplicative in raw signals.
- High-level buckets of the final IR score: "ABC - topicality", "Navboost", "Quality" (PXR0356, [DOJ](https://www.justice.gov/atr/media/1398871/dl)). STRONG. Topicality: T* "combines (at least) these three signals [anchors, body, clicks] in a relatively hand-crafted way".
- Signals are hand-crafted curves: engineers "take a sigmoid or other function and figure out the threshold", and "For the majority of signals, Google ... performs a regression" on content, clicks and rater labels (PXR0356). STRONG.
- Quality: "Generally static across multiple queries and not connected to a specific query. However, in some cases Quality signal incorporates information from the query ... a site may have high quality but general information so a query interpreted as seeking very narrow/technical information may be used to direct to a quality site that is more technical" (PXR0356). STRONG. This is the one primary statement that site standing is query-dependent for narrow searches.
- "Q is largely static and largely related to the site rather than the query"; "If competitors see the logs, then they have a notion of 'authority' for a given site"; PageRank "is used as an input to the Quality score"; a "(popularity) signal that uses Chrome data" exists (PXR0356). STRONG.
- Nayak: page quality "is largely nothing to do with clicks and, in fact, as we noted, can be anticorrelated with clicks" (18 Oct 2023 AM, p. 6336, [transcript](https://thecapitolforum.com/wp-content/uploads/2023/10/20231018-APM-BT24-AM-Google.pdf)). STRONG.
- Google admits site signals lift pages hosted on the site: site reputation abuse is third-party content placed to "rank better than it could otherwise on a different site", using signals from the host's "first party content" ([Search Central, 19 Nov 2024](https://developers.google.com/search/blog/2024/11/site-reputation-abuse)). STRONG.
- Google said in 2016 it has no "website authority score" ([SER](https://www.seroundtable.com/google-no-website-authority-score-22874.html)). SNIPPET ONLY. The leak and PXR0356 show site-level quality inputs exist under other names.

### 4.3 Site-level fields in the 2024 Content Warehouse leak

Fields already quoted in earlier memos (`siteAuthority` "applied in Qstar", `hostAge`, `siteFocusScore`, `siteRadius`, `onsiteProminence`, `homepagePagerankNs`, `crapsAbsoluteHostSignals` and the host-level and new-host click fields, `titlematchScore`, `siteQualityStddev`, `nsrEpoch`, `queriesForWhichOfficial`) are not repeated. New ones that bear on cross-site order, all STRONG that the field and its text exist:

| Field | Text | Where | Reading |
| --- | --- | --- | --- |
| `topPetacatTaxId` | "Top petacat of the site. Used in SiteboostTwiddler to determine result/query matching." | [PerDocData](https://google-api-content-warehouse.hexdocs.pm/0.4.0/GoogleApi.ContentWarehouse.V1.Model.PerDocData.html) | A site-level topic category is matched against the query in a twiddler. A specialist site's category matches narrow queries. |
| `hostNsr` | "Site rank computed for host-level sitechunks." | PerDocData | A per-host site rank exists. |
| `priorAdjustedNsr` | "NSR - prior. Estimate of whether the site is above/below average NSR in its slice." | [NsrData](https://google-api-content-warehouse.hexdocs.pm/0.4.0/GoogleApi.ContentWarehouse.V1.Model.QualityNsrNsrData.html) | Site quality is also expressed relative to a slice. |
| `impressions`, `chromeInTotal` | "Site-level impressions."; "Site-level Chrome views." | NsrData | Site-level popularity inputs. |
| `smallPersonalSite` | "Score of small personal site promotion" | NsrData | A promotion for small personal sites exists. |
| `tofu`, `clutterScore` | "Site-level tofu score: site quality predictor based on content."; "Delta site-level signal in Q* penalizing sites with a large number of distracting/annoying resources" | NsrData | Content-based site quality. |
| `authorityPromotion` | "converted from QualityBoost.authority.boost" | [CompressedQualitySignals](https://google-api-content-warehouse.hexdocs.pm/0.4.0/GoogleApi.ContentWarehouse.V1.Model.CompressedQualitySignals.html) | An authority boost term. |
| `unauthoritativeScore` | "Used as one of the web page quality qstar signals." | same | |
| `lowQuality`, `serpDemotion` | "S2V low quality score ... applied in Qstar"; "serp demotion: applied in Qstar." | same | Demotions inside Q*. |
| `pandaDemotion`, `babyPandaV2Demotion` | Panda fields from "SiteQualityFeatures"; "New BabyPanda demotion, applied on top of Panda." | same | Site-quality demotions stack. |
| `experimentalQstarSiteSignal` | "in order to quickly run LEs with new site components." | same | Q* has site components. |

### 4.4 Patents on site-level quality

- **US 8,682,892, "Ranking search results"** (Navneet Panda, Vladimir Ofitserov; priority 28 Sep 2012; Google Patents lists it "Expired - Fee Related") [link](https://patents.google.com/patent/US8682892B1/en). STRONG as a design. For each group of pages (a site), count independent incoming links (IL) and "reference queries" (RQ), searches "classified as referring to a resource in the group", for example queries containing the site's name or URL. The factor is "M=IL/RQ". It is normalized within partitions of sites with similar RQ counts (NM = (M - m) / m, where m is a central value of M in the partition). Claim 7: "multiplying the initial score for the search result resource by the resource-specific modification factor". Claim 8: when the query is navigational to the page, the factor "does not modify the initial score". Thresholded formulas damp the effect for high initial scores. Google has not said this is the Panda update. The ratio compares links with brand demand. The parts I read do not say whether a high or a low ratio is rewarded; the common reading that links far in excess of brand searches are penalized is UNVERIFIED.
- **US 9,031,929, "Site quality score"** (April R. Lehman, Navneet Panda; priority 5 Jan 2012; listed "Active") [link](https://patents.google.com/patent/US9031929B1/en). STRONG as a design. Claim 1: a ratio whose numerator is the count of unique queries "categorized as referring to a particular site" (brand or navigational queries, claims 2 to 4), reduced by a threshold, and whose denominator is the count of unique queries "followed by a user selection of a search result" on that site, "raised to a power that is greater than zero and less than one". Reading: brand demand relative to the site's total clicked-query footprint. A small vendor with real brand searches and a narrow footprint can score well. A big generalist with a huge footprint is not rewarded in proportion to its size.
- Host crowding (at most about two listings per site, more when relevant) is in `ranking-mechanics.md` section 3. STRONG.

## 5. Query type, specialists against generalists, and why authority can point the wrong way

What it means for the plan: the balance between site standing and page relevance changes by search type, and the published evidence says authority matters most on navigational and brand searches. On narrow commercial searches, the documented mechanisms (query-dependent quality, site topic matching, brand-to-footprint ratios) favour focused sites. A negative sign for global popularity inside a top 10 is expected from selection and page composition, not evidence that authority hurts.

### 5.1 Evidence by query type

- Google: "The weight applied to each factor varies depending on the nature of your query" ([How Search Works](https://www.google.com/search/howsearchworks/how-search-works/ranking-results/)). STRONG.
- Navigational: in the TREC-2001 entry-page task (145 test topics), a content-only language model had MRR 0.3375; a URL-form prior raised it to 0.7705, and an in-link prior to about 0.50 ("over 70% of all entry pages at rank 1"). The same paper: "HITS and PageRank based methods have not been able to improve retrieval effectiveness for Ad Hoc search tasks" ([Kraaij, Westerveld, Hiemstra, SIGIR 2002](https://liacs.leidenuniv.nl/~kraaijw/papers/websigir2002.pdf)). STRONG, old TREC labels. The Panda patent exempts navigational queries. `queriesForWhichOfficial` marks official pages.
- Links by type (Ahrefs 2025, link above): "navigational queries have the highest correlation"; "Links correlate better with branded searches than non-branded searches", with the caveat that popular brands "tend to have more links"; informational queries correlate more than commercial and transactional; "links correlate better for searches with higher search volumes". No per-segment coefficients except local queries at 0.33. STRONG (vendor primary, thin method). Reading: on low-volume commercial searches like ours, links matter least.
- "best X software" searches: 100 queries, 973 US results, page types tagged by an LLM, collection date not stated. Vendor-owned pages are 18.6% of page one; a listicle or Reddit thread is #1 in 71 of 100; Reddit is #1 in 34 and present in 91; 88 of 111 YouTube videos sit at positions 6 to 10; the category leader's domain is in the top 10 for only 54% of queries ([TripleDart, 2026](https://www.tripledart.com/research/who-owns-page-one-in-b2b-saas)). WEAK (one vendor study).
- Narrow and technical searches: Quality can "direct to a quality site that is more technical" (PXR0356, STRONG). Site topic category matched to the query in SiteboostTwiddler (`topPetacatTaxId`, STRONG existence). Google's August 2024 core update aims to show "small or independent sites that are creating useful, original content" when relevant ([Search Central](https://developers.google.com/search/blog/2024/08/august-2024-core-update), STRONG statement, no effect size). The 2023 "hidden gems" change for forums, blogs and lesser-known sites ([SER](https://seroundtable.com/google-search-hidden-gems-ranking-algorithm-36388.html)): SNIPPET ONLY.
- Long tail: RankEmbed "can perform poorly for tail queries" (PXR0357, STRONG). I found no study that measures the authority-relevance balance on low-volume B2B searches. not found.
- Smaller specialist sites beating large generalists on narrow commercial searches: I found no controlled published study. not found. The evidence is the mechanisms above, plus our own design data (5.3).

### 5.2 Why global authority can be negative inside a top 10 (LOCAL simulation)

Setup: per search, 300 candidates. Site authority A and topical relevance R are standard normal. Google's score S = A + R + H, where H is a hidden part (clicks, other signals) with standard deviation 0.7, about 20% of the variance. The top 10 by S is the result page. Pair accuracy is over all 45 pairs. 600 to 800 searches per cell. Code in the appendix.

| Quantity | Value |
| --- | --- |
| Pair accuracy of A (half of the score) over the whole candidate pool | 0.72 |
| Same, inside the top 10 | 0.58 |
| Per-SERP Spearman of A with score: top 50 / top 20 / top 10 | 0.30 / 0.25 / 0.21 |
| Pair accuracy of R alone inside the top 10 | 0.58 to 0.59 |
| Perfect A plus R, hidden share 20% | 0.68 to 0.69 |
| Perfect A plus R, hidden share 6% | 0.78 |
| Perfect A plus R, no hidden part | 0.99 |
| A and R correlated minus 0.6 among candidates: A alone / R alone inside the top 10 | 0.53 / 0.54 |
| Popularity proxy G = 0.5 A + 0.87 U, where U is breadth and corr(U, R) = minus 0.5 | 0.495 inside the top 10; Spearman 0.01 over the top 50 |
| Same with G = 0.3 A + 0.95 U, corr(U, R) = minus 0.7 | 0.472 inside the top 10; Spearman minus 0.09 over the top 50 |

Mechanisms, in order of what the numbers support:
1. **Range restriction.** Selection on the total score compresses every component inside the top 10. A signal worth half the score orders only 0.58 of top-10 pairs. Pure selection on an additive score does not flip the sign by itself.
2. **The proxy measures breadth, not standing on the topic.** Tranco and host-graph centrality rank huge general platforms first. If breadth goes with weaker topical match among the candidates, the proxy falls to 0.47 to 0.50 inside the top 10, even though it still carries some true authority.
3. **Retrieval enrichment.** Postings are sorted with high-quality pages first (4.1), so loosely matching pages of strong sites enter the candidate set. Those that reach the top 10 tend to sit low because their topicality is weak. This feeds mechanism 2.
4. **Page composition and late re-ranking.** Video, social and directory results take slots, mostly low on the page (TripleDart: 88 of 111 videos at 6 to 10; our data below). These are the most popular domains on the web.
5. Our measured 0.377 to 0.420 is below the simulated 0.47 to 0.50. Mechanism 4 is the most likely source of the extra drop. The check in recommendation 10 tests this.

### 5.3 Our own ordinary top 10s (LOCAL, descriptive, design data)

Read-only count over `serp_results`, ordinary searches only, snapshot dates from Nov 16, 11,056 top-10 rows on 156 searches. Rows from repeated pulls are not independent, and the window is inside the November 2026 core update. Platforms here are video, social, Google properties and review directories (YouTube, Facebook, LinkedIn, Google, Capterra, G2, Software Advice, TrustRadius, GetApp, Slashdot, SourceForge and similar), counted apart from Reddit. Search types are a quick regex on the wording.

| Search type (searches) | Platforms: mean position, share in top 3 | Reddit: mean, top 3 | All other sites: mean, top 3 |
| --- | --- | --- | --- |
| Generic category (47) | 6.41, 0.16 | 4.38, 0.55 | 5.34, 0.31 |
| "best" and "top" (22) | 6.58, 0.12 | 3.23, 0.64 | 5.69, 0.28 |
| Pricing and free (18) | 6.27, 0.14 | 3.66, 0.62 | 5.32, 0.32 |
| "alternative" and "vs" (29) | 5.56, 0.28 | 4.28, 0.46 | 5.65, 0.28 |
| Other (40) | 5.37, 0.29 | 3.46, 0.55 | 5.52, 0.30 |

By domain: YouTube mean 6.73 with 43% of rows at 8 to 10; Capterra 6.60; LinkedIn 5.81; Facebook 5.68; Reddit 3.83; all rows 5.44. The globally most popular domains sit below the average position on category, "best" and pricing searches, which is what a negative Tranco sign needs. Reddit is the exception and needs its own prior.

## 6. A realistic ceiling for history-free cross-site prediction

What it means for the plan: the literature supports about 0.60 to 0.67 pair accuracy for cross-site order from outside features, measured on deep lists or with Google-published link counts. Inside a top 10, with no click history and no Google link data, 0.62 to 0.66 would be a good result. Position accuracy inside a top 10 is mostly decided by noise and flat guesses already do well, so the product claim should be tiers and direction.

What the literature supports:
- 62.5% to 67.5% of pairs on held-out queries over the top 100, with Google's own PageRank and link counts (Bifet). STRONG.
- 65 to 66% on top-10 pairs with domains held out, with Moz link metrics and simple on-page features (Critchlow). MEDIUM.
- About two-thirds for a 2016 model, 75% for Gemini, method unknown (SearchPilot). WEAK.
- Best single public link metric: Spearman 0.37 to 0.39 over the top 50, about 0.63 of pairs. WEAK to MEDIUM.
- Content-only signals in cross-site lists: Spearman 0.02 to 0.13 for keyword and topic features (Moz), 0.28 for a content score (Surfer, in `edit-effects.md`). Our own BM25 inside top 10s: 0.444 to 0.559.

What it does not support:
- Any transparent cross-site result at or above 0.70 inside a top 10.
- Any result for a site the model has never seen with only free data.
- Any transfer from cross-sectional order to the effect of an edit (`r3-matched-pairs.md` section 5).
- Claims of 80% and above: they came from leaked domain identity (Critchlow) or have no method (vendor claims).

Our own bounds, already measured: one pull against another agrees on 0.87 of pairs; the single-pull label ceiling is about 0.93. The simulation adds a structural bound: even perfect knowledge of authority and topicality gives about 0.69 inside a top 10 if a hidden part (clicks, Navboost, late re-rankers) holds 20% of the score's variance, and 0.78 if it holds 6%. Navboost is a large, documented signal, so the hidden share is unlikely to be small.

From pair accuracy to position (LOCAL, latent normal model, 10 items, 4,000 lists):

| Pair accuracy | Mean absolute position error | Within 1 place | Within 3 places | Exact |
| --- | --- | --- | --- | --- |
| 0.50 (random) | 3.29 | 0.28 | 0.58 | 0.10 |
| 0.60 | 2.72 | 0.36 | 0.68 | 0.14 |
| 0.65 | 2.45 | 0.40 | 0.73 | 0.16 |
| 0.685 | 2.22 | 0.44 | 0.77 | 0.18 |
| 0.75 | 1.81 | 0.52 | 0.85 | 0.23 |
| 0.85 | 1.13 | 0.71 | 0.95 | 0.36 |

A flat guess at position 5 is within 3 places for positions 2 to 8, that is 0.70 of uniformly spread positions, with a mean error of 2.5. A model at 0.65 pair accuracy barely beats it inside a top 10. The useful outputs are: whether a page belongs in the top 3, the top 10, the top 30 or below; and whether a change moves it up or down against the incumbents.

## 7. Not opened, not found

- Not opened: moz.com and web.archive.org (blocked from the fetcher), Search Engine Land (Cloudflare or 403), Search Engine Watch (403), the Almadhoun and Malim paper (403), the Searchmetrics whitepapers, the Luh and Evans full texts (paywalled), the Craswell et al. 2005 SIGIR paper on query-independent evidence (only its abstract was seen).
- Not found: a SISTRIX ranking-factor correlation study; a published ablation of the MSLR link-feature group; any LLM study against Google's live order; any controlled study of small specialist sites against large generalists; any published within-top-10 correlation for DA, DR or Authority Score.
- The Moz 2015 coefficients rest on one secondary compilation. The 2013 Page Authority 0.39 is confirmed by several secondary sources but not by a Moz page I could open.

## Appendix: simulation code (LOCAL, run with numpy only)

```python
import numpy as np
rng = np.random.default_rng(7)
def pairacc(p, t):
    dp = p[:, None] - p[None, :]; dt = t[:, None] - t[None, :]
    iu = np.triu_indices(len(t), 1); s = (dp * dt)[iu]
    return np.mean(np.where(s > 0, 1.0, np.where(s == 0, 0.5, 0.0)))
def one_search(N=300, sH=0.7, rhoUR=-0.5, g_load=0.5):
    A = rng.normal(size=N)
    U, R = rng.multivariate_normal([0, 0], [[1, rhoUR], [rhoUR, 1]], size=N).T
    S = A + R + rng.normal(0, sH, N)          # Google's score
    G = g_load * A + np.sqrt(1 - g_load**2) * U  # global popularity proxy
    top = np.argsort(-S)[:10]
    return pairacc(A[top], S[top]), pairacc(G[top], S[top]), pairacc((A + R)[top], S[top])
print(np.mean([one_search() for _ in range(800)], axis=0))
```

With this seed the code prints about 0.583, 0.506 and 0.677 (A, popularity proxy, perfect A plus R). The table in 5.2 came from runs with other seeds and 600 to 800 searches; values move by about 0.01 between seeds.

Position table: draw 10 true scores t from a standard normal, prediction p = t + noise with standard deviation s, compare the rank of each item under p and t, average over 4,000 lists for each s.
