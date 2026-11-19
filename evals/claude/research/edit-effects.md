# What on-page edits do to Google rank: outside evidence

Research memo for the edit-effects theory. Written 2026-11-19 from public web sources only. Nothing about this project was sent to any outside service.

Evidence labels: STRONG = controlled split test with numbers. MEDIUM = large observational data. WEAK = anecdote, vendor marketing, or a page I could read only through a search-result summary ("not opened").

Two caveats apply to almost everything below.
1. Split tests measure organic clicks or sessions to a group of template pages over weeks. They do not measure the rank of one page on one query. A title edit can change clicks through click-through rate (CTR) with no rank change. So a click effect is an upper bound on a rank effect.
2. Vendor case-study pages are a biased sample. Vendors publish wins and surprising losses. The base rate in section 2 (about 75% of tests inconclusive) is the correction.

## Bottom line

- Most on-page edits have no effect that a large controlled test can detect. SearchPilot reports that in 2022 to 2023 nearly 75% of its tests were inconclusive, about 15% were significant positive and 7 to 8% significant negative.
- A detected effect is usually 3 to 20% of organic clicks to the page group. Moving up one position raises CTR by about 32% on average, so that is roughly 0.1 to 0.6 positions of average rank, and less if part of it is CTR.
- Normal rank noise is bigger than that. The mean absolute day-to-day move is 1.0 position for keywords at positions 4 to 10 and 2.4 at positions 11 to 20.
- The same edit type often has the opposite sign on another site. Title, date and content-removal edits are the clearest cases.
- Effects need a recrawl (Google: "a few days to a few weeks") and then build over 1 to 2 weeks. Vendors run tests for 2 to 6 weeks.
- No content tool has published evidence that its score predicts the rank change an edit causes. Published validation is cross-sectional correlation of about 0.2 to 0.3, mostly from vendors.

## 1. Controlled experiments by edit type

**Adding or removing the target keyword in the title or H1 (sign varies, STRONG).**
- Adding the category name to product page titles: -12% organic sessions (SearchPilot): https://www.searchpilot.com/resources/case-studies/does-adding-category-names-to-product-page-titles-help-seo
- Removing the category name from the title and H1 of low-traffic product pages: +28% (SearchPilot): https://searchpilot.com/resources/case-studies/aligning-keywords-with-search-queries
- Adding "Fishing: The Complete Guide" to about 220 location-page titles: +9.6%, 23-day test (SplitSignal): https://semrush.com/blog/adding-keywords-to-one-or-two-word-title-tags
- Appending three top keywords to listing-page titles: inconclusive at 95% (SearchPilot): https://www.searchpilot.com/resources/case-studies/how-does-adding-extra-keywords-to-title-tags-impact-seo
- Dom Woodman's year of SearchPilot tests: exact-match title phrasing -11%, removing synonyms -27% and -20%, adding relevant terms -10%. He said 56% of his title tag tests failed and only 6% succeeded, and that title changes "by and large" move traffic 5 to 15%: https://searchpilot.com/resources/blog/a-year-of-seo-testing-with-searchpilot
- Making the H1 match the title on about 200 travel listing pages: +12% clicks, 95%, 30 days (SplitSignal). The likely path is that Google stopped rewriting the title: https://www.semrush.com/blog/seo-split-test-result-matching-the-h1-with-the-title-tag/
- "Buy" added to 800+ product titles: +8.3% clicks. "Order" as the call to action: negative, size not given (SplitSignal): https://www.semrush.com/blog/why-seo-testing-is-a-must-practical-examples/
- A SearchPilot title test that followed best practice lost more than 20% before a later version won: https://www.searchpilot.com/resources/blog/when-seo-best-practices-backfire

**Shortening or lengthening titles (either sign; added words lean negative, STRONG but few tests).**
- Dropping a call to action from about 15,000 product titles: +6.6% clicks, 95%, 22 days, about 80% of pages recrawled in that time (SplitSignal): https://www.semrush.com/blog/seo-split-test-result-shortening-title-tags-on-product-pages/
- Dropping state names and action words so prices showed in the snippet: +11% (SearchPilot): https://searchpilot.com/resources/case-studies/shortening-title-tags-and-removing-keywords
- But removing numbers from article titles, which also shortens them: -16% (SearchPilot losing tests, next link).
- Lengthening: price after the product name -15%, "video" -20%, Easter phrases -14% (Germany) and -12% (Spain) (SearchPilot losing tests): https://searchpilot.com/resources/blog/8-losing-seo-tests
- Etsy's 2016 title test (CausalImpact on page buckets) reportedly found shorter titles won. Not opened (HTTP 403): https://www.etsy.com/codeascraft/seo-title-tag-optimization
- Cosmetic edits can matter too. All-caps product titles: +17.5% (mobile +20.4%, desktop inconclusive). SearchPilot says 45% of its capitalization tests were positive and none negative: https://www.searchpilot.com/resources/case-studies/this-test-revealed-an-unexpected-seo-signal-hiding-in-plain-sight

**Adding the year or "updated" (sign varies, small, STRONG).** Details in section 6.

**Content refresh and republishing with a new date (positive on average, MEDIUM).** Raptive matched 5,500 refreshed URLs to similar unrefreshed URLs on the same sites: Google pageviews +28% against +9%. Over 103,000 refreshes, the average rank change in the 60 days after was 0.25 positions better. Refreshes that changed word count by less than 10% gained 2%, and more than 100% gained 44% (before and after, not causal): https://raptive.com/blog/refresh-analysis-and-google-traffic-growth/ . I found no controlled test of a republish with a new date and no real content change. Google says that will not help (section 6).

**Adding body content or FAQ sections (leans positive, STRONG).**
- Spec tables and related products added to nutrition pages: nearly +20% organic traffic over almost six weeks (SearchPilot): https://www.searchpilot.com/resources/case-studies/the-seo-impact-of-enriching-page-content
- Two ChatGPT-written FAQs with FAQPage markup on 400+ category pages: +4.3% clicks, 21 days (SplitSignal): https://www.semrush.com/blog/seo-test-result-chatgpt-generated-faq/
- FAQ copy in the footer of listing pages: +9.7% clicks (SearchPilot, April 2026, not opened): https://www.searchpilot.com/resources/case-studies/will-adding-faq-content-to-footer-copy-improve-organic-traffic
- Category SEO text: +3% on one site after about three weeks, no gain or a slight decline on another (Woodman, link above).
- FAQ schema on existing FAQ content: +3% to +8% on commercial pages in 2019 (SearchPilot): https://searchpilot.com/resources/blog/faq-schema . That ran through FAQ rich results, which Google limited to government and health sites in August 2023: https://developers.google.com/search/blog/2023/08/howto-faq-changes . It does not transfer to today.

**Removing content (sign varies, STRONG).** Removing boilerplate SEO text from category pages: -4% (SearchPilot losing tests, link above). A 2024 SearchPilot retest found a significant mobile gain and no desktop change: https://searchpilot.com/resources/case-studies/removing-seo-text-2024 . A SplitSignal test removing 100 to 200 words of category copy found no significant effect (not opened): https://www.semrush.com/blog/seo-split-test-result-the-impact-of-removing-seo-copy . Removing an embedded map: -7%. Removing an expert video carousel: +4.1%: https://www.searchpilot.com/resources/case-studies/a-look-back-at-the-most-surprising-tests-of-2025

**Adding structured data (no direct rank effect, STRONG plus Google statement).** John Mueller, April 2025: "Structured data won't make your site rank better": https://www.seroundtable.com/google-structured-data-ranking-39232.html . SearchPilot: Microdata to JSON-LD had no detectable impact, dateModified schema was inconclusive, and one more question in FAQ markup gave +9%: https://www.searchpilot.com/resources/case-studies/testing-schema-markup . Woodman: category-page structured data gave +11% on two regional sites and then nothing on three later rollouts. Review schema gave +16% on one site and nothing on another (link above). Any effect runs through rich results and CTR.

**Changing internal links (positive or null in every published test I found, STRONG with publication bias).** Linking each of about 8,000 regional pages to its six nearest neighbours: +7% (SearchPilot): https://www.searchpilot.com/resources/case-studies/seo-split-test-lessons-nearby-location-links . Buttons from level 2 category pages to level 3 pages: +25% across both groups: https://www.searchpilot.com/resources/case-studies/seo-split-test-lessons-increasing-internal-linking . Search summaries also mention +5% (footer links), +5% (70+ homepage links), +11%, and one test with no noticeable impact. Those pages were not opened.

**Changing page type or intent (no controlled evidence, WEAK).** I found no split test of a true page-type change, such as a product page turned into a comparison list. The closest is a SearchPilot test that removed off-location listings to match intent. It was inconclusive after 16 days: https://searchpilot.com/resources/case-studies/matching-search-intent-test

**Pinterest (STRONG, read through a summary).** Pinterest split pages 50/50 by URL hash. A board title change made no significant difference. Text descriptions for pins gave +30% after follow-up tests. A JavaScript rendering change hurt traffic from day 2 and took a full month to recover. Summary: https://gigazine.net/gsc_news/en/20201228-demystifying-seo-pinterest . The original post returned HTTP 403: https://medium.com/pinterest-engineering/demystifying-seo-with-experiments-a183b325cf4c

## Summary table

| Edit type | Expected direction | Typical size | Days to effect | Share of tests with a detectable effect | Evidence quality | Source |
| --- | --- | --- | --- | --- | --- | --- |
| Add target keyword to title or H1 | Either sign | -12% to +10% clicks | about 7 to 30 | Woodman: 6% of title tests won, 56% failed; one inconclusive keyword test | STRONG; cross-section near zero (MEDIUM) | SearchPilot, SplitSignal, Backlinko links above |
| Remove keyword or category from title or H1 | Either sign | +28%, +11%; synonyms removed -20%, -27% | about 7 to 30 | not published | STRONG | SearchPilot links above |
| Shorten title | Either sign | +6.6%, +11%; numbers removed -16% | 22-day tests | 2 positive, 1 negative of 3 opened | STRONG, small n | SplitSignal, SearchPilot |
| Lengthen title (price, word, season) | Leans negative | -12% to -20% | weeks | losing-test list only | STRONG, selected | SearchPilot 8 losing tests |
| Year or "updated" in title | Either sign, small | -6.6% to +14% | 14 to 25 days | 4 positive, 1 null, 1 negative of 6 found | STRONG | SearchPilot, SplitSignal (section 6) |
| Visible date or dateModified only | Null or negative | -4% (one of three markets) or none; one +8% markup test | weeks | 1 positive, 1 null, 1 negative in one market | STRONG | SearchPilot, Woodman |
| Substantive refresh | Positive on average | +19 points pageviews vs matched; 0.25 positions | 60-day windows | share not reported | MEDIUM | Raptive |
| Add body content or FAQ | Leans positive | +4% to +20% | 3 to 6 weeks | category text mixed | STRONG | SearchPilot, SplitSignal |
| Remove content | Either sign | -7% to +4% | weeks | 2 negative, 2 positive, 1 null | STRONG | SearchPilot, SplitSignal |
| Add structured data | Null on rank; CTR via rich results | 0, or +9% to +20% where a rich result shows | 20+ days | most null | STRONG plus Google statement | SearchPilot, Mueller |
| Add internal links to the page | Positive | +5% to +25% | weeks | 5 of 6 found (3 seen only in summaries) | STRONG, publication bias | SearchPilot |
| Change page type or intent | Unknown | no data | unknown | no tests found | WEAK | SearchPilot intent test |
| Any on-page edit (base rate) | about 2:1 positive among detected | 3% to 20% when detected | 1 to 5 weeks | about 22 to 23% at 95% | STRONG (vendor aggregate) | SearchPilot base-rate post |

## 2. Base rates and effect size against noise

- SearchPilot, tests in 2022 to 2023: "Nearly 75% of tests are inconclusive", "About 15% ... positive and statistically significant", "7-8% ... negative and statistically significant". The number of tests is not given: https://www.searchpilot.com/resources/blog/seo-a/b-testing-realizing-the-value-of-a-good-experimentation-program . STRONG but vendor-reported.
- Woodman ran over 50 tests in a year and described 18. Roughly 7 were positive. The rest split between negative and null (link above).
- These tests need a lot of traffic. Woodman's rule of thumb is about 1,000 organic sessions a day across the tested section. SearchPilot's old rule was 30,000 sessions a month to the tested pages. Its newer model claims to detect uplifts half as large: https://searchpilot.com/resources/blog/weve-doubled-the-sensitivity-of-our-seo-split-testing-platform . SplitSignal asks for 500,000 monthly sessions on the site, 100,000 to the test group, and 300+ template pages per group: https://www.semrush.com/blog/top-faqs-for-seo-split-testing-with-splitsignal/
- Effect in positions: moving up one position raises CTR by 32.3% in relative terms on average (Backlinko, about 4 million results): https://backlinko.com/google-ctr-stats . A 3% to 20% click change is therefore at most about 0.1 to 0.6 positions of average rank. MEDIUM.
- Noise: SEOmonitor tracked 309,279 keywords daily for 41 days with no confirmed core update. Mean absolute daily move: 0.22 positions in the top 3, 1.00 at positions 4 to 10, 2.41 at 11 to 20, 4.83 at 21 to 50. Of one-day drops of 10+ positions, 42.4% bounced back in 1 to 3 days. Of keywords that fell out of the top 10, 24.3% were back the next day and 43.9% were still out after 7 days: https://www.seomonitor.com/learning-hub/dynamic-depth-crawling-research . MEDIUM.
- Our own 30 queries are noisier still: 208 of 299 Nov 9 top-10 rows were still there on Nov 11, and 194 of 296 Nov 11 rows on Nov 13 (`docs/MISSION.md`).
- Conclusion: a typical real edit effect is about one tenth to one half of one day's noise for a page at positions 4 to 10.

## 3. Time to effect

- Google: crawling "can take anywhere from a few days to a few weeks", and asking again does not speed it up: https://developers.google.com/search/docs/crawling-indexing/ask-google-to-recrawl
- SplitSignal: Googlebot visits about 60% of variant pages in week 1 and the other 40% in week 2. Changes appear a few days after the visit (FAQ link above). STRONG for those sites. A small commercial site is likely recrawled less often.
- Pinterest: effects showed "within a few days" and the gap kept widening for 1 to 2 weeks (summary link above).
- Woodman: meta description changes showed in traffic in 2 to 4 days. Title tests often needed a week. Category text needed about three weeks for +3%. Four weeks was the rule for calling a test, and no change took more than five weeks to show.
- The published SplitSignal tests above ran 16 to 30 days. The SearchPilot content test ran almost six weeks.
- Site-level quality changes are slower. Mueller (2021): such changes "often take several months to be reprocessed & reevaluated": https://www.seroundtable.com/google-quality-changes-several-months-31633.html
- Patent US 8,924,380, "Changing a rank of a document by applying a rank transition function" (inventor Ross Koningstein, filed 2012, granted Dec 30, 2014, priority June 30, 2005; Google Patents lists it as expired): https://patents.google.com/patent/US8924380B1/en . When a ranking factor changes, through the document itself or its links, the rank may move gradually over a transition period instead of at once. It describes a damped response that takes "approximately 70 days" to reach the target rank in its example. It also describes an "initially-inverse" response where the rank "may decrease for a period of approximately 20 days" before rising, plus random or time-based factors. The stated aim is to expose rank-modifying spammers who react to confusing results. The claims are not limited to suspected spam. A patent is not proof of use. I found no controlled test that shows an initial move in the wrong direction after an on-page edit. WEAK as evidence of current behavior.
- Mean reversion runs on the same timescale (SEOmonitor above). A 1 to 7 day window after an edit therefore mixes four things: the edit (if it has been recrawled), reversion from earlier moves, ordinary noise and competitors' changes.

## 4. How tools claim to model edits

- Market Brew: fits weights of its own search engine model to a target SERP with particle swarm optimization. Its CTO wrote that the fitness score can be as simple as "doing a pearson correlation between your search engine model and the public search results", with no accuracy figures: https://www.searchenginejournal.com/using-artificial-intelligence-solve-seo/125079/ . Vendor copy says "highly correlated". I found no published accuracy, backtest, or edit-lift study. WEAK.
- Surfer: Content Score has a Spearman correlation of 0.28 with position over 10,000 queries and their top 100 results. By intent it ranges from 0.19 to 0.30. It does not say whether the figure is within-query or pooled, and there is no lift study: https://surfer.ai/blog/surfer-content-score-study . MEDIUM, vendor.
- Ahrefs tested Surfer, Frase, NeuronWriter, Clearscope and its own tool on 20 keywords, with per-keyword Spearman and Kendall correlations. It found "weak correlations everywhere". Surfer, Clearscope and Frase were "very weak". The coefficients are only in a chart image I could not read: https://ahrefs.com/blog/seo-content-score-study/ . Small n, vendor.
- Clearscope: Backlinko's 2020 study used Clearscope Content Grade on a subset of URLs. In the top 30, each one-point gain in grade went with about one position higher (link in section 5). Correlational. I found no Clearscope lift study.
- MarketMuse, PageOptimizer Pro, Frase, Semrush SEO Writing Assistant: no published score-to-rank validation found. PageOptimizer Pro cites 300 to 400 single-variable tests on test pages, not live commercial SERPs: https://pageoptimizer.pro/blog-author-kyle-roof . Its success stories cannot separate the tool from other work. Semrush's readability target is the average of the top 10. Ahrefs found "virtually zero correlation" between Flesch scores and rank across 15,000 keywords (not opened): https://ahrefs.com/blog/flesch-reading-ease
- Search Engine Land reports that the vendor studies land around 0.24 to 0.28, that none control for links or authority, and that each vendor's own tool did best. It returned HTTP 403, so this is from a search summary: https://searchengineland.com/content-scoring-tools-work-but-only-for-the-first-gate-in-googles-pipeline-469871
- Academic what-if work always uses a known ranker, never Google:
  - In ranking competitions, authors make their documents more like past top-ranked documents (Raifer et al., SIGIR 2017; search summary, not opened): https://ir.webis.de/anthology/2017.sigirconf_conference-2017.49
  - A bot that swapped one sentence for one taken from higher-ranked documents improved its average rank from 3.13 to 2.67 in one round against an undisclosed LambdaMART ranker (opened): https://arxiv.org/abs/2005.12989
  - CREDENCE builds counterfactual edits for neural rankers (not opened): https://arxiv.org/abs/2302.04983
  - Another framework reports its suggested edits raise a document's rank in about 60% of cases on its own model (not opened): https://arxiv.org/abs/2409.00860
- Gap: nobody has published a validation of edit-effect predictions against real Google rank changes.

## 5. Observational studies

- Backlinko, 11.8 million results (data from 2020, Ahrefs as partner): https://backlinko.com/search-engine-ranking
  - The top result has 3.8 times the backlinks and 3 times the referring domains of positions 2 to 10, and Domain Rating rises with position.
  - Title keyword match shows "essentially zero correlation" with position. Its model predicts about 1% difference between positions 1 and 10. The H1 shows the same.
  - No relationship for word count (top-10 average 1,447 words), domain-level page speed, HTML size or schema.
  - Content Grade correlates (see section 4). MEDIUM. It reports no correlation coefficients.
- Semrush 2024: 16,298 keywords, top 20. Text relevance, measured with word embeddings, is reported as the top factor. The values are only in an image: https://semrush.com/blog/ranking-factors-semrush-study . Semrush 2017 used a random forest and put direct website visits first, with on-page factors weak (search summary, not opened): https://seroundtable.com/semrush-seo-ranking-factors-2017-study-24765.html . Direct visits mostly stand in for brand. MEDIUM to WEAK.
- Moz 2015: about 16,521 queries, top 50. Links were the factor most associated with rank and on-page keyword correlation was low (search summary, not opened): https://searchengineland.com/moz-2015-search-engine-ranking-factors-study-227766 . Searchmetrics 2016: 53% of top-20 URLs had the keyword in the title (search summary, not opened): https://ionos.com/digitalguide/online-marketing/search-engine-marketing/seo-update-2016-ranking-factors-according-to-searchmetrics . I found no SISTRIX ranking-factor correlation study.
- Page age (Ahrefs, May 2025, 1.3 million US keywords): the average #1 page is 5 years old. 72.9% of top-10 pages are more than 3 years old and 13.7% are under 1 year: https://ahrefs.com/blog/how-long-does-it-take-to-rank/ . MEDIUM.
- Variance explained: no study reports total variance explained. Single on-page factors sit near 0 (title match, word count, speed) and content scores near 0.2 to 0.3. A correlation of r explains about r squared, so 0.28 is about 8% of rank-order variance. Link and brand proxies correlate more strongly.
- Same site, same query: I found no study that compares two pages of one site on one query. Controlled split tests are the real within-site design. They hold site authority fixed by comparing control and variant pages on the same site.

## 6. Freshness

- Google's November 2011 freshness change was reported to affect 35% of searches. It targets recent events, recurring events and topics with frequent updates such as product reviews: https://techcrunch.com/2011/11/03/googles-new-algorithm-update-impacts-35-of-searches/ . Google's own post says about 6% to 10% of results changed noticeably; I saw that only in a search summary: https://search.googleblog.com/2011/11/ten-recent-algorithm-changes.html
- Google's guidance asks: "Are you changing the date of pages to make them seem fresh when the content has not substantially changed?" It says adding or removing lots of content only to look fresh will not help: https://developers.google.com/search/docs/fundamentals/creating-helpful-content
- Controlled tests of year or date in the title:
  - "in January 2020" added to listings titles: +5%. Later months were also positive; figures not given: https://www.searchpilot.com/resources/case-studies/seo-split-test-lessons-adding-month-year-to-title-tags
  - "Updated Daily" in place of the month and year: +11%: https://searchpilot.com/resources/case-studies/last-updated-in-title
  - "Updated 2022" on 985 furniture category pages: +4.9% clicks, 99%, 21 days: https://www.semrush.com/blog/seo-split-test-result-updated-2022-title
  - "(Updated) 2022" at the start of 503 ecommerce product titles, August 2022: no significant effect after 25 days (SplitSignal's summary calls it +1.5% at low confidence): https://www.semrush.com/blog/does-adding-updated-and-the-year-to-your-title-tag-help-with-organic-traffic/
  - "Daily" plus month and year on 929 travel pages: about +14% ("why SEO testing" link in section 1).
  - Month and year at the end of about 900 travel detail-page titles: -6.6% clicks, significant at 95% after 14 days, stopped at 16: https://www.semrush.com/blog/seo-split-test-result-adding-month-and-year-to-page-title
- Visible "last modified" date on travel route pages: -4% in France at 95%, inconclusive in Italy and Spain: https://www.searchpilot.com/resources/case-studies/adding-last-modified-dates-on-page . dateModified schema: inconclusive. Woodman's "date freshness markup": +8%.
- Observational counterpoint: in Zyppy's 50-site update study, winning sites had older on-page dates (774 days against 273), and 35% of winners' URLs showed no date against 4% of losers'. Small sample, correlational: https://zyppy.com/seo/google-updates-punish-good-seo/
- Read: date changes help mainly where the year shows in the snippet and the query is time-sensitive, as with "best X 2026" lists and listings. They act mostly through CTR, and the sign flips between sites. A substantive refresh helps on average (Raptive). The size grows with how much of the page changed, measured over 60 days, not days. No source shows how long a date-only lift lasts.

## What this implies for predicting up versus down after a real edit

**Edit types with a reliable sign.**
- Adding internal links that point to the page: positive or null in every published test found.
- A substantial content enrichment or rewrite that adds what the page lacked: positive on average, with size growing with the share of the page changed.
- Structured data alone and date-only changes: about zero on rank. Predict no move.
- Unreliable sign despite large effects: title and H1 keyword edits, title length, year in the title, and content removal. Treat these as mean zero with wide spread, not as best-practice positives.
- Unknown: page type or intent changes. There is no controlled data.
- Even "reliable" here means the sign is right in most published tests, not that a single page moves in that direction.

**Share of short-window moves caused by the edit.** Assumptions from sections 2 and 3:
- About 25% of edits have a real effect.
- About 50% to 80% of edits are recrawled and processed within 1 to 7 days.
- A real effect is about 0.3 to 1.0 positions.
- Noise has a standard deviation of about 1.25 to 1.5 positions per page-query at positions 4 to 10, and more below position 10.

On those assumptions the edit explains about 0.5% to 13% of the variance of a 1 to 7 day rank move on an edited page, with a central value of 2% to 3%. The formula is (share with a real effect) times (share processed) times effect squared, divided by that plus the noise variance. The effect sizes assumed here are a bit above the 0.1 to 0.6 positions implied by section 2, to be generous to the edit. An oracle that knew each edit's true effect would call the direction of the 1 to 7 day move right only about 51% to 57% of the time. Position and mean reversion explain far more, which matches the internal finding that a position-only rule scores 0.70 while the page diff adds nothing (`evals/claude/t2-edits/notes.md`).

Big rewrites, page-type changes and newly added or removed internal links could fall outside this range. So could a top-3 page with low noise. No outside source measures that.

**Sample size for a fair test.**
- Measure 14 to 28 days after the edit, not 1 to 7. Average several pulls before and after. Compare against a position-only baseline and against matched unedited pages.
- Accuracy test of up versus down, one-sided 5% level, 80% power, independent rows:
  - about 620 edited rows to show 55% against 50%
  - about 150 rows for 60%
  - about 1,700 rows for 53%
  - clustering by query and site raises all three.
- Mean rank shift, difference in differences, two-sided 5% level, 80% power, assuming a standard deviation of 2 positions for a 2 to 4 week change:
  - about 250 edited and 250 control rows for a 0.5-position effect
  - about 65 each for 1.0 position
  - about 700 each for 0.3 positions
- At the observed yield of about 16 changed-and-moved rows a week, or 7 without our own site's edits (`evals/claude/t2-edits/notes.md`):
  - 250 rows takes about 16 weeks
  - 620 rows takes 40 to 90 weeks.
- The faster route is planned, randomized edits on owned pages with pre-registered variants. That is the design SearchPilot and SplitSignal use, but with far fewer pages, so expect only large effects (around 1 position) to be detectable.
- A 90% up-versus-down target on short windows is not reachable from edit content. The noise ceiling is far below it.

## Sources I could not open

Etsy Code as Craft (403), Pinterest Medium post (403, used a Gigazine summary), Search Engine Land content-score article (403), web.archive.org (blocked), and the body of the Google 2011 freshness posts (empty page). Pages marked "not opened" above were read only through search summaries. The Originality.ai content-score figures (about 0.15 to 0.32) appeared only in secondary blogs with conflicting sample sizes, so I left them out.
