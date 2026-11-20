# Free Google ranking labels and page data: sources, costs and a data plan (round 4 research)

Research memo, 2026-11-20. Research and theory only. No Serper call was made (0 credits spent). No held-out list of the round 3 test (heldout1) was pulled or read. Nothing about the client, the project or any person was sent to an outside service. Web searches used generic topical words only. Common Crawl lookups were done by downloading the public index file and asking for byte ranges, so no URL left this box.

Grades: STRONG (primary source read in the relevant part), MEDIUM (credible secondary source read), WEAK (one informal source), SNIPPET ONLY (seen only in a search summary), UNVERIFIED. "LOCAL" means measured on this box from stored data, with the method given. Every LOCAL number is from design-day data (Nov 16 lists or earlier) and is a diagnostic, not a test.

This memo does not repeat `ranking-mechanics.md`, `edit-effects.md` or `r3-matched-pairs.md`.

## What this means for the master plan

1. **The free Serper budget is nearly gone, so plan around stored data.** 617 credits minus the 250 floor leaves 367. The other team's collector spends about 90 a day, so that margin lasts about 4 days even if this run spends nothing. The collector script's own default reserve is 200, not 250 (`/workspace/ranksim-data/collect_daily.sh`, line 13). Unless its caller sets `RANKSIM_CREDIT_RESERVE=250`, it can spend below the 250 floor. Pull the round 3 test (208 credits) first, or ask James to cap or pause the collector before anything else.
2. **Treat the stored lists as the main cross-site training set (0 credits).** On this box there are 890 ordinary result lists for 157 searches with 903 distinct hosts. There are also positions 1 to 30 for 100 searches from Nov 16 (2,678 rows, 1,376 URLs) and about 250 site-restricted training lists. Split these by search group and site now, before any cross-site model exists, and freeze the split.
3. **Use page 2 and page 3 for cross-site labels, not more page 1 lists.** LOCAL: `num=100` returns 10 results and costs 1 credit, and `page=2` and `page=3` cost 1 credit each. A top-30 list costs 3 credits and holds about 415 cross-site pairs. A list 20 to 30 deep holds on average 64 pairs of a small site against a top-10k domain. A top-10 list holds 42 and 6.9. Per credit that is about 3 times as many cross-site pairs and about 3 times as many small-versus-big pairs.
4. **The authority signal sits at the page-1 boundary, not inside page 1.** LOCAL, Nov 16 deep lists, Majestic referring subnets as a site-strength proxy. Pairs with one site in the top 10 and the other at 11 to 30: 0.633 [0.607, 0.659]. Pairs inside the top 10: 0.517 [0.488, 0.546]. Pairs inside 11 to 30: 0.506 [0.485, 0.527]. So site strength mostly decides who reaches page 1, and labels from positions 11 to 30 are where a site-strength model can learn. A two-stage target fits this: first the band (page 1 or not), then the order inside the band from content.
5. **Ask the collector owner for a free swap.** The collector pulls the original 30 searches three times a day (90 credits). Turning one of the three daily page-1 pulls into a page-2 pull keeps the cost at 90. It gives positions 11 to 20 every day and leaves two page-1 pulls for the consensus. This needs the other team's and James's OK.
6. **Ask James to connect Google Search Console for the client's site (free, owner approval needed).** It is the only free source of Google positions for the small vendor site across every search where it got an impression, including positions past page 1, for up to 16 months back. It also gives a before and after view for the PR 45 pages. Limits: positions are averages of the topmost result, a deep position is only recorded when a user actually views that results page, rare queries are hidden, and data lags 2 to 3 days.
7. **Fix the Common Crawl route in the fetch ladder.** `index.commoncrawl.org` fails from this box (TLS error), so the ladder's Common Crawl rung cannot work here. `data.commoncrawl.org` works. The per-crawl `cluster.idx` (about 104 MB, downloaded in 1.8 s) plus one byte-range request per URL finds captures, and a second range request returns the archived HTML. LOCAL coverage: 22% of robots-blocked and 15% of 403 or challenge URLs we could not read have a capture in the last 3 crawls.
8. **Use Common Crawl as the free replacement for Wayback for natural experiments, but expect few cases.** LOCAL: of 120 small-site URLs that rank in the Nov 16 top 30, 38 (32%) have 2 or more good captures in the last 6 crawls (about May to Oct 2026) and 13 (11%) have 3 or more. For 80 big-site URLs it is 13 (16%) and 8 (10%). The index line carries a content digest, so changed pages can be found without downloading any page.
9. **Do not spend credits on Serper scrape unless nothing else works.** LOCAL: 63 scrapes on Nov 10 cost 2, 6 or 10 credits, 5.3 on average (334 credits). Reddit pages cost 6 and Facebook and Quora pages 10. Most vendor and review pages cost 2.
10. **More free Google lists exist only behind a free signup that James must approve.** No other source is both free and open. The largest is the DataForSEO $1 trial at $0.0006 per page of 10 results (about 1,600 pages if the trial can use the standard queue, UNVERIFIED). Next come SerpApi at 250 searches a month and HasData at 1,000 credits a month (5 to 10 per call). Each needs a calibration check against Serper before its lists are mixed in. A second Serper account is forbidden by Serper's terms.
11. **Do not build on old or off-topic SERP datasets.** The Webis Archive Query Log can only be analysed inside a sandbox, and the Webis product SERP corpus is restricted. The one open recent set with Google ranks (3,000 news searches, Oct 2025 to Jan 2026) is off-topic. Common Crawl has no Google result pages, because Google's robots.txt blocks `/search` and CCBot obeys robots.txt. Off-topic lists would teach a generic big-domain prior, and inside our top 10s global authority already points the wrong way.
12. **Free site-standing features to test (0 credits):** sitemap size and the count of on-topic URLs in the sitemap (LOCAL: 37 of 41 sampled ranking hosts serve a readable sitemap), Common Crawl capture counts per domain, internal-link crawl, Majestic referring subnets, and the site's footprint across the 157 stored searches. Test them only on the page-1 boundary pairs from point 4, with a frozen split.

## 1. The credit budget, as it really is

What it means: almost no new search labels can be bought from the shared Serper balance. The plan has to run on stored data plus at most one or two small, targeted pulls.

- Balance 617, floor 250, so 367 can be spent. The round 3 held-out test needs 208 (104 lists, two pulls each). That leaves about 159.
- The other team's collector spends about 90 a day from the same balance. In about 4 days it alone brings the balance down to 250.
- LOCAL, `collect_daily.sh`: `RESERVE="${RANKSIM_CREDIT_RESERVE:-200}"` and a default cap of 120 per run. The script lowers its cap so the balance stays at or above the reserve it was given. With the default it stops at 200, which is 50 under this run's 250 rule. How the other team calls it is not known from here (no crontab on this box).
- So credits for this plan are a coordination question for James. They are not a technical one.

## 2. Serper mechanics (November 2026)

What it means: one page of 10 results costs 1 credit in every form we use. Deeper positions cost 1 credit per extra page. There is no cheaper path to positions 11 to 50.

| Item | Finding | Source | Grade |
| --- | --- | --- | --- |
| Free signup | "Get 2,500 free queries", "No credit card required" | https://serper.dev/ | STRONG |
| One account only | Terms forbid users to "Register more than one account at any point in time" | https://serper.dev/terms | STRONG |
| Paid packs | $50 for 50k credits ($1.00/1k), 50 queries per second; $375 for 500k ($0.75/1k), 100 qps; $1,250 for 2.5M ($0.50/1k), 200 qps; $3,750 for 12.5M ($0.30/1k), 300 qps. "Credits valid for 6 months". Not allowed here (paid) | https://serper.dev/ | STRONG |
| When credits are charged | "Every time you receive successful responses to queries, we will deduct credits" | https://serper.dev/ | STRONG |
| Rate limit of the free credits | Not stated on the public pages | https://serper.dev/ | not found |
| Credits per search with `num` above 10 | Third-party pages say 2 credits for 11 to 100 results. Serper's public site does not say. The repo's `docs/INDEX.md` also says 2 | https://apiserpent.com/blog/serper-pricing-credits-explained (search summary) | SNIPPET ONLY, contradicted locally |
| `num=100` today | 100 calls on Nov 16 2026 with `num=100`: every one returned at most 10 organic results (mean 9.85) and was charged 1 credit (the `credits` field of each response) | LOCAL, `serp_raw` table of `/workspace/ranksim-data/ranksim_raw.sqlite`, and `README.md` there | STRONG (LOCAL) |
| `page=2`, `page=3` | 100 page-2 calls and 70 page-3 calls: 1 credit each, mean 9.97 and 9.94 results. Serper's `position` restarts at 1 on each page | LOCAL, same table | STRONG (LOCAL) |
| Pages 4 and 5 (positions 31 to 50) | Never requested here. Expected 1 credit each | none | UNVERIFIED |
| Site-restricted search (`site:<site> <search>`) | 274 calls, 1 credit each, mean 8.3 organic results | LOCAL, same table | STRONG (LOCAL) |
| Scrape endpoint | 63 charged calls on Nov 10: 22 at 2 credits, 30 at 6, 11 at 10. Total 334, mean 5.3. By host: reddit.com 6 (22 of 22), facebook.com 10 (7 of 8), quora.com 10, softwareadvice.com and trustradius.com 6, g2.com, capterra.com, linkedin.com and vendor sites 2 | LOCAL, `/workspace/ranksim-scrape-2026-11-10/manifest.csv` | STRONG (LOCAL) |
| Why depth costs more now | Around Sept 10 2025 the `&num=100` parameter stopped working. A tool vendor said it "now takes 10 requests (10x the cost)" for 100 results | https://www.searchenginejournal.com/google-modifies-search-results-parameter-affecting-seo-tools/556080/ | MEDIUM |
| Google's comment | The parameter "is not something that we formally support", attributed to Google via Search Engine Land | https://www.seroundtable.com/google-search-drops-100-results-parameter-40097.html and others (search summary) | SNIPPET ONLY |

**Cheapest way to positions 11 to 30 or 11 to 50.** `page=2` plus `page=3` is 2 credits for 11 to 30. Pages 2 to 5 should be 4 credits for 11 to 50, untested past page 3. Asking for `num` above 10 buys nothing. If page 1 for the same search was already pulled that day (the collector does this for the original 30), the extra depth costs only the deeper pages.

**Cross-day caution.** Pages 2 and 3 pulled on a different day from page 1 can double-count or miss a URL that moved across the page boundary. Pull all pages of one search within minutes.

## 3. Other free sources of Google result lists

What it means: Search Console for the client's own site is the one free, approved-by-owner, high-value source. Everything else is closed, not Google, or a small free tier behind a signup.

| Source | What it gives | Free quota | Status for us | Grade and source |
| --- | --- | --- | --- | --- |
| Google Programmable Search (Custom Search JSON API) | 10 results per call | "100 search queries per day for free", then $5 per 1,000 up to 10k a day | "closed to new customers". Existing customers "have until January 1, 2027". Not usable | STRONG, https://developers.google.com/custom-search/v1/overview |
| Same, order compared with google.com | Results are "unlikely to match those returned by Google Web Search". No Onebox, real-time or universal results | | Order not documented | STRONG, https://support.google.com/programmable-search/answer/70392 |
| Same, measured | 2006 study of Google, Yahoo and MSN APIs against the web pages, daily for 5 months: Google's API results were about 14% different | | Old, other API | MEDIUM (search summary of the paper plus its poster listing), McCown and Nelson, JCDL 2007, https://sites.harding.edu/fmccown/pubs/se_apis_www07_poster.pdf |
| Google Search Console API, client's site | Clicks, impressions, CTR and average position by query, page, date, country, device | Free. 50,000 rows per day per site per search type. `rowLimit` up to 25,000 with `startRow` paging. 1,200 queries a minute per site | **Needs James to grant read access (OAuth).** Highest-value free source | STRONG: https://developers.google.com/webmaster-tools/v1/searchanalytics/query, https://developers.google.com/webmaster-tools/limits, https://developers.google.com/search/blog/2022/10/performance-data-deep-dive |
| Search Console history | "you'll have 16 months of data" | | | STRONG, https://developers.google.com/search/blog/2018/01/introducing-new-search-console |
| Search Console position | "the topmost position occupied by a link to your property or page in search results, averaged across all queries". A result "on page 3 of search results, but the user only views page 1" has its position "not recorded for that query" | | Deep positions appear only when a user looks that deep | STRONG, https://support.google.com/webmasters/answer/7042828 |
| Search Console gaps | Anonymized queries are "not issued by more than a few dozen users over a two-to-three month period" and have no row. Data "typically available after 2-3 days". With page and query grouping "some data may be dropped" | | Long-tail vendor searches may be hidden | STRONG, Google blog above and https://developers.google.com/webmaster-tools/v1/how-tos/all-your-data |
| Bing Webmaster Tools API | Bing data for a verified site, API key or OAuth | Free | Bing, not Google. Needs site verification by the owner | STRONG, https://learn.microsoft.com/en-us/bingwebmaster/getting-access |
| Bing Search API | | | Retired August 11, 2025 | STRONG, https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement |

Free tiers of other Google SERP APIs. Every one needs a signup, so **every one needs James's approval**. None needs payment as far as the pages say. Each has its own scraping location and device, so its order must be checked against Serper on shared searches before mixing.

| Provider | Free quota | Card | Notes | Grade and source |
| --- | --- | --- | --- | --- |
| DataForSEO | $1 trial balance, usable "for an unlimited period of time". Google organic $0.0006 per page of 10 (standard queue), $0.0012 priority, $0.002 live. Depth billed per 10 results | Not stated on the help page. Secondary sources say none | About 1,600 standard pages or 500 live pages, if the trial covers them (UNVERIFIED). "Opening additional trial accounts is prohibited". $50 minimum top-up after | STRONG, https://dataforseo.com/pricing/google-serp/google-organic-serp-api and https://dataforseo.com/help-center/how-does-your-free-unlimited-trial-work |
| SerpApi | 250 searches a month, 50 an hour | Not stated | Cached, errored and failed searches do not count | STRONG, https://serpapi.com/pricing |
| HasData | 1,000 credits every month | "No credit card required" | 5 or 10 credits per call, so 100 to 200 Google pages a month | STRONG (page read), https://hasdata.com/prices |
| SearchApi.io | 100 free requests | "No credit card required" | Renewal not stated | STRONG, https://www.searchapi.io/pricing |
| Zenserp | 50 searches a month | Not stated | | STRONG, https://zenserp.com/pricing-plans/ |
| Scrapingdog | 100 credits | "no credit card required" | Google SERP Light 5 credits, Advance 10, so 10 to 20 calls | STRONG, https://www.scrapingdog.com/pricing/ |
| serpstack | 100 searches a month | Not stated | Free plan is for non-commercial use, so **not allowed** for this project | STRONG, https://serpstack.com/pricing |

## 4. Open datasets of real result lists that include Google

What it means: none of them can train a site-strength model for this niche today. The only open, recent one is about news. Old lists reflect a Google that has had many core updates since, and sites' standing moves with core updates.

| Dataset | Size and dates | URLs and positions | Licence and access | Host reachable (HEAD, 20 s) | Grade |
| --- | --- | --- | --- | --- | --- |
| Webis Archive Query Log (AQL-22) | Whole log: 356M queries, 166M result pages, 1.7B results, 550 providers, 25 years of Internet Archive captures. Google row: 72.7M queries, 20.0M unique, 34.0M SERPs and 270.9M results (both estimates) | Parsed results include "result titles, referenced URLs, snippets" in ranked order. Page offsets give later positions | Analysis only inside the TIRA sandbox: submit a Docker image, "analysis results are blinded until reviewed". No download | webis.de 200, files.webis.de 200 | STRONG, paper PDF https://downloads.webis.de/publications/papers/reimer_2023.pdf, abstract https://arxiv.org/abs/2304.00413 |
| Webis Product SERP Corpus 2024 (from "Is Google Getting Worse?", ECIR 2024) | 7,392 product review searches, 2022 to 2023, Startpage (Google proxy), Bing, DuckDuckGo | SERP crawls | Zenodo record is "restricted": "Access for research purposes only. No redistribution permitted." | zenodo.org 200 | STRONG, https://zenodo.org/api/records/10797507 and https://github.com/webis-de/ecir24-seo-spam-in-search-engines |
| Hugging Face `goker/comp-serp-data-v2` | 3,000 searches made from news headlines (Oct 2025 to Jan 2026). 172,017 rows: Google 59,666, Mojeek 57,163, Brave 55,188 | Engine, rank 1 to 20, URL, HTML size and page features | MIT | huggingface.co 200 | MEDIUM (dataset card and stats file read, data not opened), https://huggingface.co/datasets/goker/comp-serp-data-v2 |
| Kaggle, flight ticket searches (advertools example) | About 4,000 rows, 10 results per search | Yes | Unknown | kaggle.com answered 404 to HEAD (site up, HEAD inconclusive) | SNIPPET ONLY, https://baselight.app/u/kaggle/dataset/eliasdabbas_search_engine_results_flights_tickets_keywords |
| The Markup Google audit on Kaggle | Audit of Google's own properties on result pages | Category shares, not a rank tracker | Unknown | | SNIPPET ONLY, https://baselight.app/u/kaggle/dataset/thedevastator_a_large_scale_analysis_of_google_search |
| Common Crawl result pages | None. Google's robots.txt has `Disallow: /search` (fetched today) and CCBot is "checking first the robots.txt" | n/a | n/a | n/a | STRONG, https://www.google.com/robots.txt and https://commoncrawl.org/faq |
| Rank-volatility indexes (MozCast, Semrush Sensor and similar) | Daily aggregate scores | No URLs or positions as far as found | Free to view | not checked | UNVERIFIED |

Other Hugging Face results for "serp", "google-search" and "serper" were agent traces, toy sets or gated (for example a CC BY-NC Azerbaijani set that needs a login). Nothing else large with Google URLs and positions was found.

**Honest judgment.** Old or off-topic lists teach which global domains Google likes. Inside our top 10s that kind of signal points the wrong way (Tranco 0.392, Common Crawl PageRank 0.420, harmonic centrality 0.377, earlier team). A site-strength model for a niche needs lists from that niche, at the depth where small sites appear (section 7). The news set could at most pretrain a generic "is this page type a news or reference page" feature. It is not worth building on now.

## 5. Page content for pages we cannot fetch, and earlier versions of pages

What it means: Common Crawl is the only free archive that works from this box. It works through `data.commoncrawl.org` only. It fills a minority of the gaps and gives a modest number of before and after pairs.

Reachability from this box (Nov 20 2026, about 05:55 UTC, HEAD with 20 s timeout, then GET where noted):

| Host | Result |
| --- | --- |
| `index.commoncrawl.org` (CDX API) | TLS error ("unexpected eof"), 3 of 3 tries. Plain HTTP gives 500. Unreachable |
| `data.commoncrawl.org` | 200. `cluster.idx` of CC-MAIN-2026-43 is 104,520,381 bytes and downloaded in 1.8 s. WARC record range fetch works and returns HTML |
| `web.archive.org` | Timeout, as round 2 found |
| `archive.org/wayback/available` | 200. Gives capture timestamps only. The page itself lives on `web.archive.org`, so content cannot be read |
| `archive.ph`, `archive.today` | Timeout |
| `r.jina.ai` | Timeout |
| `cc.bingj.com` | 400 |

**Common Crawl lookup without the CDX server.** Each crawl publishes a `cluster.idx` with one line per block of 3,000 index lines (886,601 blocks in CC-MAIN-2026-43, all keys lowercase). Binary search the URL's SURT key in it, then fetch that one block (about 250 KB, gzip) by byte range from `cdx-NNNNN.gz`. Each matching line gives status, MIME type, content digest and the WARC file, offset and length. A last range request fetches the WARC record. Crawls available on `data.commoncrawl.org` today run up to CC-MAIN-2026-43. One crawl's lookups once stalled for 33 minutes behind the proxy, so a job needs short timeouts and resume.

LOCAL coverage, sample from the Nov 16 lists at positions 1 to 30, user-content platforms left out, URL matched on host (without `www.`) plus path, query string and trailing slash ignored, status 200 only:

| Sample | In latest crawl (2026-43) | In 1 or more of the last 6 crawls (2026-21 to 2026-43) | In 2 or more | In 3 or more |
| --- | --- | --- | --- | --- |
| 120 URLs on small sites (registrable domain not in the Majestic Million) | 25 (21%) | 67 (56%) | 38 (32%) | 13 (11%) |
| 80 URLs on big sites (Majestic rank 10,000 or better) | 9 (11%) | 21 (26%) | 13 (16%) | 8 (10%) |
| 60 robots-blocked URLs we never read | | 13 (22%) in the last 3 crawls | | |
| 60 URLs that gave 403, 429 or a challenge page | | 9 (15%) in the last 3 crawls | | |

Big sites are captured less often per URL, probably because Common Crawl spreads a limited per-site budget over millions of pages. Robots-blocked pages are mostly missing for the same reason they block us: CCBot obeys robots.txt.

**Natural experiments.** The digest field changes when the stored bytes change, so pages with two captures can be screened for change from the index alone. Byte changes include dates and widgets, so the round 1 material-change rule still has to be applied to the text. About one small-site URL in three has two or more captures in six months. Most of those are a single pair of versions, months apart.

**Other free archives and readers.**

- Google cache: cache links left results in early 2024. Google later said "The cache: search operator no longer works in Google Search" (Sept 2024). MEDIUM, https://searchengineland.com/google-search-completely-kills-the-cache-feature-446904 and https://searchengineland.com/google-search-officially-retires-cache-link-437122.
- Bing cache: Bing removed the cache link and operator in December 2024. MEDIUM, https://searchengineland.com/bing-officially-removes-cache-link-from-search-results-449220 and https://www.seroundtable.com/bing-drops-cache-link-officially-38566.html.
- Jina Reader: 20 requests a minute without a key, 500 with a free key, 10M free tokens per new key. It "respects website access controls" and does not bypass anti-bot protection. Some Jina models are CC BY-NC. Unreachable from this box anyway. STRONG, https://jina.ai/reader/.
- archive.today: unreachable from this box. Terms not checked.
- Stored Wayback pages: the raw store already holds 138 Wayback pages fetched earlier from another route. LOCAL, `pages` table.

## 6. Seeing a site's standing without a search credit

What it means: free site-level signals carry some information about who reaches page 1. None orders pages inside page 1. A site-strength feature should be judged on boundary pairs, not on top-10 pairs.

| Signal | LOCAL result on Nov 16 lists (positions 1 to 30, 100 searches, best position per domain, ties count one half, 95% bootstrap by search) | Notes |
| --- | --- | --- |
| Majestic referring subnets (free CSV, already on disk at `/workspace/kw-sample-2026-11-13/majestic_million.csv`, absent counts as 0) | All pairs 0.571 [0.558, 0.584], n 21,348. Inside top 10: 0.517 [0.488, 0.546], n 3,656. Top 10 against 11 to 30: 0.633 [0.607, 0.659], n 10,712. Inside 11 to 30: 0.506 [0.485, 0.527], n 6,980 | Design day, one pull, construction software only. A diagnostic, not a test |
| Common Crawl capture count per domain (latest crawl) | Partial run: 138 of 513 domains (the first ones alphabetically) before a time cap. All pairs 0.525 (n 1,231), top 10 0.498 (n 205), boundary 0.563 (n 590) | Too few domains to judge. Cost: one or a few 250 KB range requests per small domain |
| Sitemap | 37 of 41 randomly sampled ranking hosts served a readable sitemap (16 plain lists, median 500 URLs; 21 sitemap indexes, median 9 child sitemaps). The run hit its time cap at 41 of 80 hosts | Gives site size and the count of on-topic URLs (URL words matching the search). Untested as a signal |
| Footprint in stored lists | Free from the 890 stored lists: in how many related searches a site appears, and how high | A site-level feature, not page history. The earlier team's domain prior did not help on 30 searches. With 157 searches and the deep lists it can be retested on boundary pairs |
| Common Crawl host graph (harmonic centrality, PageRank) | Earlier team: points the wrong way inside top 10s | Download host is reachable. Retest on boundary pairs only |
| Search Console (client's site) | The client's own standing across all its searches | Owner approval needed |

No published study was found that validates sitemap size or Common Crawl page counts against Google position. Not found.

## 7. Label efficiency: most cross-site information per credit

What it means: deeper pages of searches already pulled give the most cross-site and small-against-big pairs per credit. Site-restricted lists give no cross-site pairs. Stored lists cost nothing. Pairs inside one list are strongly dependent, so the honest unit of evidence is the search (and the site), not the pair.

LOCAL yield, Nov 16 lists (100 searches with page 1, 100 with page 2, 70 with page 3):

| Design | Credits per search | Cross-site pairs per search | Pairs of a small site (not in Majestic Million) against a top-10k domain | Distinct domains added | Share of rows on small sites |
| --- | --- | --- | --- | --- | --- |
| (a) One ordinary page-1 pull of a new search | 1 | 42.4 | 6.9 | 216 domains over 100 searches | 22.0% |
| (b) Pages 2 and 3 of a search already pulled (positions 11 to 30) | 2 extra (3 for the whole top 30) | 415 for the whole top 30 (70 searches with page 3) | 64.3 for the whole list (100 searches, 20 or 30 deep) | 290 of the 444 domains seen at 11 to 30 never appear in a top 10 | 34.6% at 11 to 20, 36.8% at 21 to 30 |
| (c) Site-restricted list | 1 | 0 (about 31 same-site pairs per list with pairs, round 3 training) | 0 | 0 | n/a |
| (d) Reuse of stored lists | 0 | 890 lists, 157 searches, 903 hosts | Nov 16 deep lists: about 6,400 | n/a | n/a |

What (b) buys that (a) does not: small sites appear about 1.6 times as often, and pairs that straddle the page-1 boundary appear, which is where site strength shows (section 6). What it costs: Google's order is noisier further down (peer-reviewed, cited in `ranking-mechanics.md`). Noise at positions 11 to 30 has **not** been measured on this box. One repeat pull of pages 2 and 3 for about 15 searches (30 credits) would measure it before anything is built on it.

What (a) buys: a new search is a new independent unit (new search group, new mix of sites). For a held-out test that is worth more than more pairs from a search already seen.

Published guidance:

- Yilmaz and Robertson (SIGIR 2009) studied the trade-off between number of queries and judgments per query for learning to rank and found "shallow judgments are more cost effective than deep judgments". MEDIUM (as described by a 2025 paper, the 2009 abstract is closed): https://arxiv.org/html/2506.23191v1. The same 2025 paper finds shallow-judged sets "generally enhance generalization" of BERT rerankers. STRONG for that paper: https://arxiv.org/abs/2506.23191.
- For evaluation, deep judging "is often preferable to shallow judging when topics are selected intelligently" (Kutlu et al.). STRONG (abstract): https://arxiv.org/abs/1701.07810.
- Active selection of comparisons: with an embedding assumption, a ranking of n items can be found with about d log n adaptively chosen comparisons, while random choice needs "almost all pairwise comparisons". STRONG (abstract): https://arxiv.org/abs/1109.3701 (Jamieson and Nowak 2011). Our labels come in whole lists per credit, so pair-level adaptivity does not apply directly. List-level adaptivity does: choose searches where small sites sit near the page-1 boundary.
- For neural rankers, Althammer et al. (2023) found no active learning strategy "that outperformed random selection of training instances". MEDIUM (as described in https://arxiv.org/html/2506.23191v1).

Reading for this plan: many searches with moderate depth beat a few searches with full depth for training. For the cross-site question, moderate depth means positions 1 to 30, not 1 to 10, because the signal lives at the boundary. Choose searches at random within the niche for the held-out set. Do not pick them by a model's uncertainty.

## 8. A concrete zero-credit and low-credit data plan

### Zero credits (start now)

1. **Freeze a cross-site split of stored data.** The 100 searches with Nov 16 positions 1 to 30, plus all stored page-1 pulls, split by search group and by site with the same seeded-hash style as round 3. Keep test2 sites untouched. Training and design use the train part only.
2. **Use the collector's daily page-1 pulls as free fresh truth** for the 30 original searches (three pulls a day, consensus).
3. **Fill page text from Common Crawl** through `data.commoncrawl.org`: for every top-30 URL without text, look up the last 3 to 6 crawls. Expect roughly 15 to 22% of blocked URLs and about half of small-site URLs to have some capture. Record capture date. Text captured weeks before the label day is a known gap.
4. **Build the site-standing features** (sitemap size, on-topic sitemap URLs, Common Crawl domain counts, internal-link crawl, Majestic, footprint in stored lists). Judge them on boundary pairs (top 10 against 11 to 30) of the train split only.
5. **Mine natural experiments from Common Crawl digests**: URLs with two or more captures whose text changed materially, joined to their current position in the stored lists. Expect a few dozen usable cases, not hundreds.
6. **Search expansion for later pulls**: the stored responses already hold 1,028 distinct related searches and 542 "people also ask" questions (LOCAL). Use them to choose new searches without spending credits.

### Needs James's approval, still free

7. Search Console read access for the client's site (OAuth). Pull query by page by date for 16 months.
8. Swap one of the collector's three daily page-1 pulls for a page-2 pull of the same 30 searches (same 90 credits a day).
9. One free signup for extra Google lists (DataForSEO $1 trial is the largest). Before mixing, pull 20 searches from both it and Serper on the same day and check pair agreement against the 0.87 two-pull yardstick.
10. Set `RANKSIM_CREDIT_RESERVE=250` in the collector's caller, or confirm it is set.

### Low credits (only after the round 3 test is pulled, about 159 left at most)

11. **Deep-band noise check**: pages 2 and 3 twice, 40 minutes apart, for 15 train searches. 60 credits (30 if page 1 and one deep pull already exist that day).
12. **Cross-site held-out set**: pages 1 to 3 for new searches from new search groups, pulled once. At 3 credits a search, 30 searches cost 90 credits. That is small (30 searches), so the interval will be wide, and this should be said up front.
13. No Serper scrapes. If a key page cannot be read any other way, budget 2 credits for a vendor page and 6 to 10 for forum pages.

### Credit cost table

| Action | Serper credits | What it returns |
| --- | --- | --- |
| Ordinary search, page 1 (`num=10`) | 1 | about 9.8 results |
| Ordinary search with `num=100` | 1 | still 10 results, no gain |
| `page=2` | 1 | positions 11 to 20 |
| `page=3` | 1 | positions 21 to 30 |
| Positions 11 to 30 | 2 | 20 results |
| Positions 11 to 50 | 4 (pages 4 and 5 untested) | 40 results |
| Whole top 30 of a new search | 3 | about 415 cross-site pairs, about 64 or more small-against-big pairs |
| Same, pulled twice for a consensus | 6 | |
| Site-restricted search | 1 | mean 8.3 pages of one site |
| Scrape | 2, 6 or 10 (mean 5.3) | page text |
| Round 3 held-out test as planned | 208 | 104 lists, two pulls each |
| Collector, per day (other team) | about 90 | 30 searches, page 1, three pulls |
| Common Crawl lookup and page fetch | 0 | archived HTML |
| Sitemaps, robots, internal-link crawl | 0 | site size and structure |
| Search Console (owner OAuth) | 0 | client's positions, 16 months |
| Stored lists | 0 | 890 lists, 157 searches, 903 hosts |

## Method notes for the LOCAL numbers

- Credits per call: the `credits` field of every stored response in the `serp_raw` table, grouped by `num`, `page` and whether the search starts with `site:`.
- Scrape costs: the `credits` column of `/workspace/ranksim-scrape-2026-11-10/manifest.csv` (64 rows, 1 without a value).
- Depth yields and the Majestic test: rows of source `smartcrew-index-deep:2026-11-16:abspos` in `serp_results`. Domains are registrable domains (last two labels, three for common two-part country suffixes). Small means not in the Majestic Million. Bootstrap: 2,000 draws over searches, seed 20261119.
- Common Crawl: CC-MAIN-2026-21, -25, -30, -34, -39, -43 `cluster.idx` and index blocks from `data.commoncrawl.org`. Samples seeded (20261120 and 7). User-content platforms (Reddit, Quora, Facebook, LinkedIn, YouTube, Instagram, Pinterest, TikTok, X) left out.
- The scripts were run inline and not saved, as this run may only create this memo. Each step can be rebuilt from the description above.
