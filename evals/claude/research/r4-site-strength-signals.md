# Site strength signals: what is free, what this box can reach, what the evidence says

Research memo, 2026-11-20 (written between about 00:30 and 07:30 ET). Round 4 input for `SPEC-MASTER-PLAN.md`. Research and theory only: no production code, no repo file edited except this new memo, no git state changed, zero Serper credits, no call to google.serper.dev or scrape.serper.dev, no held-out list read or pulled. Only neutral test domains (example.com, example.org, wikipedia.org, mozilla.org) were sent to outside services. Nothing about the client, the project or any person went into a search or a URL.

It does not repeat `ranking-mechanics.md`, `edit-effects.md` or `r3-matched-pairs.md`. It points to them.

Grades: STRONG (primary source read in full or in the relevant part), MEDIUM (credible secondary source, or a vendor's own primary page about its own study), WEAK (single informal source), SNIPPET ONLY (seen only in a search summary), UNVERIFIED. "Box test" means a live request made from this box on 2026-11-20 with curl, timeout 20 seconds unless stated.

Pair accuracy conversions in this memo use pair accuracy = 0.5 + 0.5 x Kendall tau (Bifet et al., in `r3-matched-pairs.md`) and tau about two thirds of Spearman for small, roughly normal, within-query correlations. They are my arithmetic, not the authors' numbers.

## What this means for the master plan

1. **Treat global authority as a gate, not as an order inside the top 10.** The best evidence: Moz's Domain Authority has mean Spearman 0.26 with position over the top 50 results, but 0.071 when the same method is restricted to the top 10 (Capper, Moz, 2017, STRONG). Converted, that is about 0.52 pair accuracy inside a top 10. Our measured 0.38 to 0.42 inside niche top 10s is that near-zero effect plus a selection effect (section 2). Do not feed Tranco, Common Crawl PageRank or harmonic centrality into the cross-site order as a plain "higher is better" feature. Use them only to answer "can this site reach page one at all" and as a fallback for sites with no niche footprint.
2. **Make the main site-strength signal niche-relative and leave-one-search-group-out.** Build it from data already on the box: the site's CTR-weighted visibility across the niche's other search groups (SISTRIX-style definition, section 8), its best position on related searches, and its share of in-links from other niche domains. The data is there: 155 stored searches, 724 domains in design-day top 10s, 198 domains in 3 or more searches, and 82% of distinct (search, URL) top-10 rows belong to those domains (box count, counting the target search itself). Publish evidence points this way, though none of it is a clean test: Semrush 2024 lists a URL's other top-20 positions (0.22) and its organic traffic (0.33) among its highest correlates (MEDIUM), and topic-sensitive PageRank beat global PageRank on precision at 10, 0.512 against 0.276 (Haveliwala 2002, STRONG, small study).
3. **Download the Common Crawl domain graph and compute in-degree two ways: all referring domains, and referring domains inside the niche.** It is free, keyless and fast enough here: release `cc-main-2026-aug-sep-oct`, domain edges 7.1 GiB plus vertices 942.6 MiB. It covers 547 of the 579 round 3 universe sites (94%). The top-1M lists cover only 31% to 48%. Ahrefs' 2025 study found referring domains correlate about twice as well as its domain score (0.255 against 0.131, MEDIUM). Niche in-degree is the free stand-in for topical authority.
4. **Test cross-site combination now, on data already held, with zero credits.** The ordinary top-10 pulls (100 searches, three pulls a day from Nov 16) give cross-site consensus pairs. Fit a pairwise model on [content score difference, niche standing difference, global gate] on training search groups. Score it on held-out search groups and sites, with the same freeze rules as round 3. The test2 site fold (never used) is the natural held-out cell for a second test.
5. **Add brand demand as a second site feature, from free endpoints that answer here.** Google autocomplete answers with no key (HTTP 200). Wikidata official-website lookups answer, fastest through QLever (2,564,939 P856 statements counted in 0.84 s). Wikipedia pageviews and external-link counts answer. Inside top 10s branded search volume did slightly better than DA (0.10 against 0.071, Capper, STRONG). The effect is small. Treat it as a gate and tie feature, not a main driver.
6. **Use the Common Crawl URL index as the Wayback substitute.** index.commoncrawl.org fails here exactly like web.archive.org (TLS EOF). The same index is readable through data.commoncrawl.org: a 104.5 MB `cluster.idx` plus HTTP range reads returned 316 example.com captures and a full WARC record (box test). This unblocks the "historical natural experiments" idea (theory R3.9 item 4) and page counts per domain, at zero cost.
7. **Do not spend effort on Open PageRank, Moz, Ahrefs, Semrush, Bing, Cloudflare Radar or Google Trends.** Open PageRank now needs a Keywords Everywhere sign-in and is built from the same Common Crawl graph. Moz's free API is 50 rows a month. Ahrefs' terms forbid automated use outside its paid API. Semrush is paid. Bing's competitor backlink view is manual and needs a verified own site, and the Bing Search APIs were retired on 11 Aug 2025. Radar needs a token and is CC BY-NC. Trends returned HTTP 429, and its official API is an alpha by application.
8. **Domain age is a gate for new domains only.** RDAP answers for .com, .net, .org, .ai, .app and others, but the IANA bootstrap has no RDAP server for .io, .co, .us or .de, and WHOIS port 43 does not connect from this box. crt.sh answers but is slow and flaky. Use age only to flag domains under about two years old (see `ranking-mechanics.md` 1.5 for why that matters).
9. **Expect modest gains and say so in the plan.** No published free signal has been shown to order pages inside a top 10 much better than coin. The strongest correlates in vendor studies (traffic and visibility) are partly outcomes of ranking. The leave-one-group-out design is what keeps the niche footprint from being circular. Without it, the footprint just restates the label.

## 1. Live reachability from this box (2026-11-20)

What it means for the plan: everything the plan needs from Common Crawl, Wikidata, Wikipedia, Google autocomplete, RDAP and the top lists is reachable now with no key. The two index front ends that are blocked (index.commoncrawl.org, web.archive.org) have a working alternative for Common Crawl only. Only HTTP and HTTPS leave the box. Raw TCP to WHOIS (port 43) and to crt.sh's public Postgres (port 5432) hung until killed.

| Source | Test | HTTP | Reachable |
| --- | --- | --- | --- |
| data.commoncrawl.org | HEAD root | 200 | yes |
| CC domain ranks file (latest) | 64 KiB range GET, then full 2.54 GB download | 206, 200 | yes |
| CC host ranks, domain edges | HEAD | 200, 200 | yes |
| CC `cluster.idx` (CC-MAIN-2026-43) | full GET, 104,520,381 bytes | 200 | yes |
| CC CDX block and WARC record | range GETs | 206 | yes |
| CC columnar index parquet | 1 KiB range GET | 206 | yes |
| index.commoncrawl.org | GET collinfo.json, three tries | 000 (TLS EOF); HTTP gives 500 | **no** |
| web.archive.org | HEAD | 000 (TLS EOF) | **no** |
| commoncrawl.github.io/cc-webgraph-statistics | HEAD | 200 | yes |
| Tranco zip | HEAD, then full download 9,691,075 bytes | 307 then 200 | yes |
| Tranco API latest list / rank of example.com | GET | 200 / 200 (10.6 s) | yes |
| Majestic Million CSV | 1 KiB range GET | 206 | yes (a full GET timed out once at 120 s; a copy dated Nov 18 is on the box) |
| Cisco Umbrella top 1M | HEAD, then full download 12,740,258 bytes | 200 | yes |
| crux-top-lists on raw.githubusercontent.com | HEAD, then full download 8,834,006 bytes | 200 | yes |
| radar.cloudflare.com/domains | HEAD | 403 | **no** (bot challenge) |
| Cloudflare Radar API, no token | GET /radar/ranking/top | 400 | answers; needs token |
| Open PageRank API, no key | GET | 401 (message: moved to Keywords Everywhere) | answers; needs key |
| query.wikidata.org SPARQL | GET | 200 (7.6 s first, 0.18 s later) | yes |
| QLever Wikidata (qlever.dev/api/wikidata) | GET, redirect followed | 308 then 200 | yes |
| www.wikidata.org API wbgetentities | GET | 200 | yes |
| Wikimedia pageviews API | GET | 200 | yes |
| en.wikipedia.org API exturlusage | GET | 200 | yes |
| dumps.wikimedia.org enwiki externallinks | HEAD (5,036,480,852 bytes, 2026-11-07) | 200 | yes |
| suggestqueries.google.com (client=firefox) | GET | 200 | yes |
| www.google.com/complete/search (client=chrome) | GET | 200 | yes |
| trends.google.com explore API (unofficial) | GET | 429 | **no** (rate limited on first call) |
| Knowledge Graph Search API, no key | GET | 403 | answers; needs key |
| CrUX API, no key | POST | 403 | answers; needs key |
| rdap.org | GET example.com | 302 to rdap.verisign.com, then 200 | yes |
| crt.sh JSON | GET (three tries) | timeout at 20 s; 200 in 13.5 s; 200 in 2.7 s | intermittent |
| WHOIS port 43, crt.sh Postgres 5432 | raw TCP | hung | **no** |
| Moz Links API, no key | POST | 401 | answers; needs key |
| Ahrefs API v3, no key | GET | 403 | answers; paid |
| Semrush API, no key | GET | 403 | answers; paid |
| Bing Webmaster API, no key | GET | 400 | answers; own sites only |
| HN Algolia search | GET | 200 | yes |
| GitHub search API | GET | 200 once, then "rate limit exceeded" for the shared egress IP | partly |
| iTunes Search API | GET | 200 | yes |
| reddit.com search.json | GET | 403 | **no** |

Throughput from data.commoncrawl.org varied a lot: a 32 MiB range at 89 MB/s, the first 1.77 GB of the rank file at 3 MB/s (596 s), the last 774 MB at 72 MB/s. One earlier streaming attempt stalled for about two hours, probably during a box suspend. Plan for downloads to disk with resume (`curl -C -`), not long pipes.

## 2. The wrong-way authority result, explained with evidence

What it means for the plan: the earlier team's result (Tranco 0.392 of 469 pairs, CC PageRank 0.420, harmonic centrality 0.377, inside top 10s on 30 searches; `COORDINATION.md` line 213 and 214) is not a bug and not surprising. Published studies show domain authority is near zero inside a top 10. A selection effect can push it below 0.5 in niche commercial searches. The fix is a niche-relative measure, not a better global one.

**Published sizes, domain-level authority against Google position.**

| Study | Sample | Unit and method | Domain-level result | Page or other result | Grade |
| --- | --- | --- | --- | --- | --- |
| Moz 2015 ranking factors, https://moz.com/search-ranking-factors/correlations | 17,600 Google US searches; top 50 per Capper | mean Spearman per search | DA 0.26; root domains linking to the root domain 0.26; MozRank of root domain 0.25 | Page Authority 0.37; root domains linking to the page 0.30; exact-match .com domain 0.20 | MEDIUM. Values read from the chart SVG geometry; one bar cross-checked against the chart's own tooltip (0.25) |
| Capper 2017, Moz blog, https://moz.com/blog/rankings-correlation-study-domain-authority-vs-branded-search-volume | about 5,000 non-branded US searches from STAT, **top 10 only** | same method as Moz 2015 | DA 0.071 | branded search volume 0.10; DA adds "barely any" explained variation once branded volume is in | STRONG |
| Ahrefs 2025 (Stox), https://ahrefs.com/blog/links-matter-less-but-still-matter | top 1,000,000 US searches by volume, top 20 | Spearman | Domain Rating 0.131 | referring domains 0.255, backlinks 0.248; 2019 run on low-volume non-branded searches: referring domains 0.29. Links correlate less for commercial and transactional searches, more for navigational and branded | MEDIUM |
| Semrush 2024 ranking factors, PDF at https://dmboard.media/wp-content/uploads/2024/02/2024_Ranking_Factors_Study_1708082540.pdf | 16,298 searches, top 20 | Spearman between position and the factor's **average per position** (20 points, not per search) | domain Authority Score 0.21 | URL organic traffic 0.33; URL's number of other top-20 positions 0.22; referring IPs 0.18 | MEDIUM for method and the 0.33, 0.22, 0.18 (read in the PDF text). SNIPPET ONLY for 0.21 (chart is an image; figure from https://fahlout.com/research/topical-authority and a Spanish summary) |
| Backlinko (with Ahrefs data), https://backlinko.com/search-engine-ranking | large, older | "strongly correlates", no coefficient given | not given | not given | SNIPPET ONLY |

Converted to pair accuracy (my arithmetic): DA 0.26 over the top 50 is about 0.59; DA 0.071 inside a top 10 is about 0.52; DR 0.131 over the top 20 is about 0.54; referring domains 0.255 is about 0.59. Semrush's per-position averages cannot be converted. Nobody publishes a top-10-only study of a free popularity list (Tranco, CrUX, Umbrella) or of Common Crawl ranks against Google position: searched, not found.

**Why ours points the wrong way, in order of strength.**

1. Gate, not order (STRONG for the pattern). Capper's own reading: DA "has much more to do with what it takes to get you onto the front page than it has to do with ranking in the top few results once you're there." Our pairs are all inside the top 10, so a near-zero effect is expected before anything else.
2. Selection among entrants (my inference, WEAK until tested). If a page enters the top 10 when topical match plus site standing clears a bar, the low-authority entrants are the ones with very strong topical match (specialist vendor pages built for the exact search), while high-authority entrants can enter with a weaker match (directories, review aggregators, general publishers, forums). If order inside the top 10 then follows topical match and topic-specific clicks more than global standing, global authority becomes negatively tied to position. The round 1 finding that content alone is also near or below coin across sites (BM25 0.444 to 0.559) fits this: inside the selected set the two parts trade off. Test: split our cross-site pairs into generalist-against-specialist and like-against-like, and check whether the wrong-way sign lives in the first group.
3. Global popularity is not topical standing (STRONG for the general claim, small study). Haveliwala's topic-sensitive PageRank raised precision at 10 from 0.276 (one global PageRank) to 0.512 (topic-biased PageRank), 10 queries, 5 volunteers, https://www.cs.ucr.edu/~vagelis/classes/CS172/publications/topicsen.pdf. The Google leak has site-topic fields, `siteFocusScore` and `siteRadius` (already cited in `ranking-mechanics.md` section 4). Tranco, CrUX and Umbrella measure visits or DNS lookups across the whole web. They say nothing about construction software.
4. Niche coverage and domain score are nearly unrelated (WEAK to MEDIUM, vendor, outcome is AI citations not rankings). Floyi measured "ranked coverage" (share of a topic map's searches where a site holds a top-20 position) and Ahrefs DR on the same sites: they correlate at 0.11, and coverage tracks AI-answer citations at 0.33 to 0.51 against 0.09 to 0.16 for DR, https://floyi.com/blog/topical-authority-vs-domain-authority/.
5. Sampling (our numbers). 0.392 of 469 pairs has a plain binomial interval of about 0.35 to 0.44. Pairs share searches, so the true interval is wider. It rests on one day (Nov 6) and 30 searches. The direction is probably real; the size is not well pinned.

Google's own words point the same way: quality is "largely static and largely related to the site rather than the query" (PXR0356, in `r3-matched-pairs.md`), so it shifts a site's level across all searches, while topicality decides among sites that all cleared the quality bar. One patent ties site-level standing to brand demand directly: US 8,682,892 (Panda and Ofitserov, priority 2012) computes a per-domain or per-host factor as the ratio of independent links to "reference queries" (searches that name the site or are navigational to it), then normalizes it within groups of similar reference-query counts, https://patents.google.com/patent/US8682892B1/en. STRONG as a design read through a summarizing fetch; no evidence of deployment.

## 3. Common Crawl web graph (host and domain ranks, in-degree)

What it means for the plan: this is the only free source of real link data that covers small B2B sites. Use its in-degree, not its rank columns, and compute a niche in-degree from the edges.

- Latest release: `cc-main-2026-aug-sep-oct`, built from crawls CC-MAIN-2026-34, -39 and -43. Index page: https://data.commoncrawl.org/projects/hyperlinkgraph/cc-main-2026-aug-sep-oct/index.html. Release list: https://commoncrawl.org/web-graphs. STRONG (read in full; box test).
- Host graph: 242.8 million nodes, 2.4 billion edges. Domain graph: 135.0 million nodes (134,979,706 in the stats file), 1.8 billion edges (1,817,202,146), 66.7% dangling. Unit: host (reversed, `com.example.www`) or registrable domain (reversed, `com.example`).
- Files under `https://data.commoncrawl.org/projects/hyperlinkgraph/cc-main-2026-aug-sep-oct/`:

| File | Size | Content |
| --- | --- | --- |
| `domain/...-domain-ranks.txt.gz` | 2.4 GiB (2,544,184,794 bytes, last modified 2026-10-22) | columns `#harmonicc_pos #harmonicc_val #pr_pos #pr_val #host_rev #n_hosts`, sorted by harmonic centrality position |
| `domain/...-domain-vertices.txt.gz` | 942.6 MiB | `id, rev domain, num hosts` |
| `domain/...-domain-edges.txt.gz` | 7.1 GiB | `from_id, to_id` |
| `domain/...-domain.graph`, `-domain-t.graph` | 3.8 GiB, 3.7 GiB | BVGraph and its transpose (in-links) |
| `host/...-host-ranks.txt.gz` | 4.5 GiB | same columns without `n_hosts` |
| `host/...-host-vertices.paths.gz`, `-host-edges.paths.gz` | 1.7 GiB in 48 files, 10.9 GiB in 192 files | host ids and edges |

- Access: no key, no signup. Terms: https://commoncrawl.org/terms-of-use (not re-read here). Cadence: a new release roughly every month in 2025 to 2026, each covering three crawls (release list above). Tooling: https://github.com/commoncrawl/cc-webgraph (Java 11+, WebGraph and LAW libraries; there is a Python binding, `pyccwebgraph`). No Java is installed on this box. The plain text files need no Java.
- Box result: streaming the whole domain rank file and matching the 579 round 3 universe sites found **547 (94%)**, 227 of 237 train sites. Median harmonic centrality position 2,314,403; 10th percentile 3,456; 90th percentile 31,944,798; median PageRank position 637,541. Only 221 of 547 are in the top 1 million by harmonic centrality. Wall time 18 min 39 s for 134,979,707 lines (user time 1 min 45 s, so disk or suspend bound). The matched ranks sit in `/tmp/r4-site-strength/cc/cc_universe_ranks.json` (scratch, not durable). The count used site names only, across all three folds, and read no list, position or label.
- Cheapest lookup for a few thousand domains: download the rank file once (2.4 GiB) and filter by reversed name in one pass. For in-degree, download vertices (942.6 MiB), map the wanted domains to ids, then stream edges (7.1 GiB) once and count, per target, all distinct sources and the sources that are themselves niche domains. That single pass gives the global referring-domain count and the niche in-degree. No sorted or indexed text variant exists. The BVGraph transpose allows random access to in-links but needs Java. No free public DuckDB or Athena route exists for the graph (Athena runs on the user's own AWS bill).
- Published evidence on Common Crawl ranks against Google position: none found. Common Crawl's own SEO post (https://commoncrawl.org/blog/how-seos-are-using-common-crawls-web-graph-data-for-ai-ranking-signals) discusses AI citations, not rankings. MEDIUM for that absence statement (searched twice).
- Open PageRank is a rescaled version of this graph (section 5), so it adds nothing once the graph is local.

## 4. Common Crawl URL index (the free Wayback substitute)

What it means for the plan: historical versions of competitor pages and per-domain page counts are available here at zero cost, through data.commoncrawl.org, even though index.commoncrawl.org is blocked.

- CDX API at https://index.commoncrawl.org/ : **unreachable** from this box (TLS EOF, like web.archive.org).
- Same index as files: `https://data.commoncrawl.org/cc-index/collections/CC-MAIN-2026-43/indexes/cluster.idx` (104,520,381 bytes) maps SURT key ranges to blocks in `cdx-00000.gz` to `cdx-00299.gz`. Box test: binary search for `com,example)/`, range GET of the block, 316 captures found, each with WARC filename, offset and length; a range GET of one WARC record returned the full HTTP response with `<title>`. STRONG (box test).
- Columnar index: `https://data.commoncrawl.org/crawl-data/CC-MAIN-2026-43/cc-index-table.paths.gz` lists 900 Parquet files (subsets warc, crawldiagnostics, robotstxt). Parquet range reads work (HTTP 206). With pyarrow or DuckDB (neither installed here) one can filter by `url_host_registered_domain` and read only the needed row groups. That gives the page count per domain per crawl.
- Latest crawl: CC-MAIN-2026-43 (https://commoncrawl.org/latest-crawl).
- Coverage of small B2B sites per crawl: not measured here. The graph match (94%) says most universe domains are at least linked; it does not say their pages were captured.
- First capture date of a domain needs a lookup in each older crawl's `cluster.idx` (about 100 MB each). Feasible for a few thousand domains, but slow; RDAP is cheaper for .com.

## 5. Open PageRank (now at Keywords Everywhere)

What it means for the plan: skip it. It needs a sign-in, its quota is small, and it is derived from the Common Crawl graph we can read directly.

- The old endpoint `https://openpagerank.com/api/v1.0/getPageRank` returns 401 with: "OpenPageRank moved to openpagerank.keywordseverywhere.com. Old DomCop API keys no longer work: sign in with your Keywords Everywhere API key, mint a free OPR API key (30,000 domains/month) and send it in this same API-OPR header." STRONG (box test).
- New page https://keywordseverywhere.com/openpagerank : "a 0 to 10 score built from Common Crawl's open web graph"; free plan 30,000 domains a month; up to 100 domains a call; `POST /v1/domains/bulk` with a Bearer token; "No card required to begin"; monthly history since 2018. Rate limit and license not stated. MEDIUM (read through a summarizing fetch).
- Unit: registrable domain. Signup: a Keywords Everywhere account. Whether that sign-up is free was not stated on the page: UNVERIFIED.

## 6. Top lists: Tranco, Majestic, Umbrella, Cloudflare Radar, CrUX

What it means for the plan: they cover about half of the niche's sites at best, they measure popularity, and inside our top 10s they pointed the wrong way. Keep Majestic RefSubNets and the CrUX bucket only as gate features.

Coverage of the 579 round 3 universe sites (box count, membership only):

| List | Date | Listed | Train sites | Median rank of listed |
| --- | --- | --- | --- | --- |
| Tranco top 1M | 2026-11-19 | 276 (48%) | 111 of 237 | 137,568 |
| Majestic Million | copy of 2026-11-18 | 280 (48%) | 112 of 237 | 98,283 |
| Cisco Umbrella top 1M | 2026-11-20 | 193 (33%) | 82 of 237 | 279,287 |
| CrUX top 1M (buckets) | 2026-10 | 181 (31%) | 73 of 237 | bucket 500,000 |
| Common Crawl domain graph | 2026 Aug to Oct | 547 (94%) | 227 of 237 | HC position 2.3 million |

- **Tranco.** https://tranco-list.eu/ . Unit: registrable domain. Latest default list `GQ9JK`, created 2026-11-19, providers `crux, farsight, majestic, radar, umbrella`, 30-day window (2026-10-21 to 2026-11-19), Dowdall combination (API response, box test, STRONG). Download https://tranco-list.eu/top-1m.csv.zip (9.7 MB). API https://tranco-list.eu/api/ranks/domain/{domain} returns daily ranks for at least the past 30 days, limit 1 query per second (API documentation page, STRONG). No key for reads. Attribution and licenses of inputs: Umbrella "available free of charge", Majestic CC BY 3.0, CrUX CC BY-SA 4.0, Cloudflare Radar CC BY-NC 4.0, Farsight default list only (Tranco home page, STRONG). Measures popularity, not links.
- **Majestic Million.** https://downloads.majestic.com/majestic_million.csv (80,299,165 bytes, last modified 2026-11-20 06:00 GMT). Columns `GlobalRank, TldRank, Domain, TLD, RefSubNets, RefIPs, IDN_Domain, IDN_TLD, PrevGlobalRank, PrevTldRank, PrevRefSubNets, PrevRefIPs` (file read, STRONG). Daily. CC BY 3.0 (via Tranco). Unit: registrable domain. RefSubNets is a real link count (referring class C subnets), but only for the top million.
- **Cisco Umbrella.** http://s3-us-west-1.amazonaws.com/umbrella-static/top-1m.csv.zip (12.7 MB, last modified 2026-11-20 01:13 GMT). Unit: host name, subdomains listed separately (first rows `google.com`, `gstatic.com`, `www.google.com`; 1,547 of the first 2,000 rows have three or more labels; box read). Free of charge (via Tranco, MEDIUM). Based on DNS lookups through Cisco's resolvers: UNVERIFIED here.
- **Cloudflare Radar.** Web page https://radar.cloudflare.com/domains returns 403 here. API `https://api.cloudflare.com/client/v4/radar/datasets?datasetType=RANKING_BUCKET` and `POST .../radar/datasets/download`, or `.../radar/datasets/ranking_top_1000` style aliases, all with `Authorization: Bearer <API_TOKEN>`. Buckets 200, 500, 1,000, 2,000, 5,000, 10,000, 20,000, 50,000, 100,000, 200,000, 500,000, 1,000,000. Ordered top 100 daily; buckets weekly over 7 days; built from DNS queries to 1.1.1.1. https://developers.cloudflare.com/radar/investigate/domain-ranking-datasets/ (MEDIUM, summarizing fetch). Needs a Cloudflare account for the token (free plans exist; not verified in this session). CC BY-NC 4.0 (via Tranco). Already inside Tranco, so little extra value.
- **Chrome UX Report rank.** Unit: origin (`https://www.example.com`), magnitude buckets. The public mirror https://raw.githubusercontent.com/zakird/crux-top-lists/main/data/global/current.csv.gz (8.8 MB) holds the top 1M; latest monthly file in the repo is `202610.csv.gz`; monthly, "typically on the second Tuesday" (README, STRONG). Buckets seen in the current file: 1,000; 5,000; 10,000; 50,000; 100,000; 500,000; 1,000,000 (box read). The full list (about 15 million origins per the README) is only in BigQuery, `chrome-ux-report.experimental.global` (and per-country tables) with `experimental.popularity.rank`. BigQuery sandbox: no card, 1 TiB of queries a month, 10 GiB storage for the life of the project, tables expire after 60 days, https://docs.cloud.google.com/bigquery/docs/sandbox (STRONG). Needs a Google account and a Cloud project: a free sign-up. The CrUX API (https://developer.chrome.com/docs/crux/api) needs a free Cloud API key, allows 150 queries a minute per project, returns Core Web Vitals per origin or URL, and has no rank field (MEDIUM, summarizing fetch). The README cites research that the CrUX list is "significantly more accurate than" Alexa and Tranco for real popularity (https://zakird.com/papers/toplists.pdf, not opened: SNIPPET ONLY for that claim).

## 7. Wikidata and Wikipedia (entity standing, brand demand)

What it means for the plan: a free, reachable, license-clean way to mark "this site is a known entity" and to rank brand interest. Expect it to separate incumbents from small vendors, not to order two small vendors.

- **Official website (P856).** SPARQL at https://query.wikidata.org/sparql works here. Limits: 60 s per query, 60 s of processing per minute per user agent and IP, 30 error queries a minute, 5 parallel queries per IP, HTTP 429 with Retry-After beyond that, and a User-Agent is required, https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual (MEDIUM, summarizing fetch). Exact-URL matching is brittle (a query for `<https://www.mozilla.org/>` returned nothing, because stored values differ in form). The practical route is one bulk pull of all P856 values and host matching locally. QLever's public Wikidata endpoint does that fast: 2,564,939 P856 statements counted in 0.84 s, and a CONTAINS filter returned results at once (box test; https://qlever.dev/api/wikidata). Wikidata data is CC0 (https://www.wikidata.org/wiki/Wikidata:Licensing, page reached; MEDIUM).
- **Sitelink count** (number of Wikipedia language editions with an article): `wbgetentities&props=sitelinks` works (Q52 has 381). Can be pulled in the same QLever query with `wikibase:sitelinks`.
- **Pageviews.** `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{Article}/monthly/{start}/{end}` works with no key; data from 1 July 2015 (https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html, MEDIUM). Rate limit: not found on the pages read. Most small B2B vendors have no article, so this only grades incumbents.
- **Wikipedia external links to a domain** (editorial citations): API `list=exturlusage&euquery=<domain>` works; bulk alternative is `enwiki-latest-externallinks.sql.gz` (5.0 GB, 2026-11-07). A free, link-like signal with a clear meaning.
- Published validation of Wikipedia or Wikidata presence against Google position: not found.

## 8. Brand demand (autocomplete, Trends, knowledge panel)

What it means for the plan: autocomplete is the only brand-demand source that is free, keyless and answering. Its legal footing is grey. Brand demand is a small effect inside a top 10 (Capper: 0.10).

- **Google autocomplete.** `https://suggestqueries.google.com/complete/search?client=firefox&hl=en&q=<text>` and `https://www.google.com/complete/search?client=chrome&q=<text>` both returned 200 with suggestion lists (box test). No official API and no published limit. Google's terms forbid "using automated means to access content from any of our services in violation of the machine-readable instructions on our web pages (for example, robots.txt files ...)" (https://policies.google.com/terms, STRONG). www.google.com/robots.txt disallows `/search` and many paths but has no rule for `/complete` (box read); suggestqueries.google.com has no robots.txt (404). So low-volume use is not barred by robots, but it is unofficial and can change or block at any time. Suggested signals: does the bare brand name autocomplete to itself; how many of the 10 suggestions contain it; does "<brand> " complete with product words (pricing, login, reviews).
- **Google Trends.** The unofficial explore endpoint returned 429 on the first call from this box. trends.google.com/robots.txt disallows `/explore?` and `/trends/explore?`. The official Trends API is an alpha by application only, with no stated price, https://developers.google.com/search/apis/trends (STRONG). Not usable now.
- **Knowledge panel.** Seeing a panel needs a Google results page (Serper credits). The Knowledge Graph Search API (https://developers.google.com/knowledge-graph) needs a key, is "not suitable for use as a production-critical service", and is being moved to Cloud Enterprise Knowledge Graph (MEDIUM). Free quota not stated on the page read. Wikidata presence is a free stand-in.
- Evidence on brand demand and rankings: Capper 2017 (above, STRONG): branded search volume 0.10 against DA 0.071 inside top 10s. Patent US 8,682,892 (above) uses "reference queries" per site. Moz 2015: mentions of the domain name on the web 0.17 to 0.19 (MEDIUM).

## 9. Domain age

What it means for the plan: cheap for .com, patchy for the TLDs SaaS vendors like (.io, .co). Use it only to flag very young domains.

- **RDAP.** https://rdap.org/domain/<domain> redirects to the registry (302). For example.com it reached https://rdap.verisign.com/com/v1/domain/example.com with event `registration 1995-08-14` (box test). rdap.org limit: "a maximum of 10 requests in 10 seconds" (https://about.rdap.org/, MEDIUM); call the registry servers from the IANA bootstrap directly for bulk. IANA bootstrap https://data.iana.org/rdap/dns.json (published 2026-11-19, 1,204 TLDs) has servers for com, net, org, ai, app, dev, uk, ca, au, software, tech and biz, and **none for io, co, us or de** (box read). WHOIS on port 43 does not connect from this box.
- **Certificate transparency first seen.** https://crt.sh/?q=<domain>&output=json works but is slow and intermittent (one timeout at 20 s, then 13.5 s and 2.7 s). For example.org: 58 certificates, earliest `not_before` 2011-01-11 (box test). Public CT logging became general around 2018, so this bounds age only for younger domains (general knowledge, UNVERIFIED here).
- **Common Crawl first capture**: possible through the per-crawl `cluster.idx` files (section 4); slow.
- Evidence: young domains are rare in top 10s (SE Ranking 2026, Ahrefs 2025, in `ranking-mechanics.md` 1.5, MEDIUM). Semrush 2024 has a domain-age factor in its URL and domain group; I did not read its value.

## 10. Other free sources, and what their terms allow

What it means for the plan: none of these is worth building on. Two small ones (app-store and GitHub presence) are free and legal but only fit some niches.

- **Moz.** Free API: "one request every ten seconds, up to 50 rows per month" (https://moz.com/products/api/pricing, read by curl, STRONG); paid plans from $20. Needs a Moz account. Not usable at our scale.
- **Ahrefs.** Terms forbid, "except through Ahrefs API", using the services "through an automated means", and forbid scraping (https://ahrefs.com/legal/terms, read by curl, STRONG). The API is paid. "Ahrefs Free" (formerly Webmaster Tools) works only on verified own sites (SNIPPET ONLY). Manual checks of a handful of domains in the free checker are allowed; automation is not.
- **Bing Webmaster Tools.** Since June 2020 the Backlinks report has a "similar sites" view that shows referring domains and anchors for any domain, if you have one verified site of your own (https://www.seroundtable.com/bing-webmaster-tools-links-similar-sites-29541.html, MEDIUM). No API for it was mentioned. Manual only. The Bing Search APIs were retired on 11 Aug 2025 (https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement, SNIPPET ONLY; consistent reports at https://www.seroundtable.com/bing-search-apis-retiring-august-11-2025-39406.html).
- **Semrush.** API answers 403 without a key and is paid. Not pursued.
- **Review sites (G2, Capterra).** Not tested. Their presence for a brand can be read from our own stored SERPs (their pages already appear in niche results), which costs nothing and touches no terms.
- **GitHub.** Search API works without a key but hit the shared-IP limit after one call; a free token raises it. Only relevant for developer tools.
- **App stores.** iTunes Search API works without a key (200). Relevant only if the niche's vendors ship mobile apps (construction field software often does).
- **Hacker News (Algolia API).** Works without a key. Weak fit for construction software.
- **Reddit.** JSON search returns 403 here without OAuth.
- **Number of indexed pages.** Google `site:` counts need Serper credits and Google says the counts are not meant for diagnostics (`r3-matched-pairs.md` section 1). Free proxy: Common Crawl capture count per registrable domain (section 4), plus our own local index.
- **Our own crawl's link graph.** `/workspace/ranksim-data/index/index.sqlite` holds 178,448 edges, but only 4,245 cross a domain boundary, and only 117 destination domains have any outside in-link, median 2 referring domains (box count). It cannot stand in for off-page links. Common Crawl must.

## 11. Signals computable from data already on the box

What it means for the plan: the strongest candidates are here, and they cost nothing. The rule that makes them honest is leave-one-search-group-out.

- **SERP footprint and visibility index.** Definition to copy: SISTRIX collects about 1,000,000 searches, "usually 100 results per keyword", weights each ranking by search volume and the expected click-through rate of its position, and sums per domain; it says small niches may be too thin and recommends a project-specific index for them (https://www.sistrix.com/visibility-index/calculation, MEDIUM, summarizing fetch). Our version: per site and per search group g, sum over searches outside g of CTR(position) (optionally times volume), plus the site's best position on searches in sibling groups. Validation: SISTRIX publishes none on that page. An academic study of 32 German company sites found visibility indices did not correlate highly with visitor traffic (Härting, Mohl, Steinhauser and Möhring, BIS 2016, LNBIP; title and authors confirmed through Crossref, finding SNIPPET ONLY). Semrush 2024 found a URL's number of other top-20 positions correlates 0.22 with its position and domain organic traffic is "strong" (MEDIUM). Coverage here: 155 searches, 724 domains on design days; 311 domains in 2 or more searches, 198 in 3 or more, 121 in 5 or more, 61 in 10 or more; 82% of the 3,586 distinct (search, URL) top-10 rows belong to domains in 3 or more searches (box count, includes the target search, so the leave-group-out share is lower). In-repo evidence: the site's own best position on the same search (`dp`) is the anchor of the frozen Target 1 rule and beat a flat 7 on new-row design slices (`evals/claude/t1-placement/notes.md`), while cross-search site priors were tried and not chosen there. An older domain prior from other query families did not help on 30 searches (`docs/MISSION.md`).
- **Site topical focus.** Leak fields: `siteFocusScore` ("how much a site is focused on one topic") and `siteRadius` ("how far page_embeddings deviate from the site_embedding"), cited in `ranking-mechanics.md` section 4 (STRONG that they exist; weight unknown). Our version: embed the site's pages in the local index (8,192 pages with text), take the site centroid, measure its similarity to the niche or search-group centroid (focus), the spread of its pages around the centroid (radius), and the share of its pages that are about the niche. Published validation of any topical-authority measure against Google position: none found beyond vendor blogs (the Floyi coverage study above is the most concrete, outcome AI citations). Haveliwala 2002 is the only rigorous evidence that topic-biased authority beats global authority, and it used human relevance, not Google order.
- **Internal link graph and home-page prominence.** Already in the round 3 site-position tier (about half a point inside a site, `theory.md` R3.3). For cross-site use, the number of pages a site has on the topic and how prominently its home page links to them is a reasonable site-level depth feature. No published validation found.
- **Brand term in the search, exact-match domain.** Moz 2015: query equals the domain name with .com 0.20, equals the domain name 0.16, partial match 0.07 (MEDIUM, chart geometry). Moz reads this as anchor text and brand effects, not a domain bonus. For our niche it mainly flags navigational searches ("<brand> pricing", "<brand> alternative"), where the named brand's own site should rank first. Make it a separate feature so it does not leak into site strength.

## 12. Ranked shortlist of signals worth testing

Ranked by expected value for the cross-site gap, then cost. All are zero Serper credits.

| Rank | Signal | Unit | Source and reachability today | Cost |
| --- | --- | --- | --- | --- |
| 1 | Niche visibility, leave-one-search-group-out (CTR-weighted footprint, best position on sibling searches) | registrable domain per search group | local SERP store; reachable | no download; about half a day to build and freeze |
| 2 | Niche in-degree: distinct niche domains linking to the site | registrable domain | CC domain vertices 942.6 MiB plus edges 7.1 GiB; reachable | 10 to 60 min download at observed speeds; one streaming pass of 1.8 billion edges, likely about 1 hour |
| 3 | Global referring-domain count (CC in-degree), as a gate and for sites with no footprint | registrable domain | same pass as rank 2; reachable | included above |
| 4 | Site topical focus and radius from own pages, pages-on-topic count | site | local index plus CC columnar page counts; reachable | a few hours; no network for the local part |
| 5 | Brand demand: autocomplete self-completion and suggestion count; Wikidata P856 presence and sitelinks; Wikipedia pageviews and external-link count | brand, domain | suggestqueries.google.com, QLever, Wikimedia APIs; all reachable | a few thousand calls, about 1 to 2 hours polite; autocomplete is unofficial |
| 6 | Majestic RefSubNets, Tranco, CrUX bucket, CC harmonic centrality: gate only, never a direct order | domain or origin | all reachable, files under 100 MB except CC | already downloadable in minutes |
| 7 | Domain age (RDAP registration, crt.sh first certificate) | registrable domain | RDAP reachable for .com, .net, .org and others, not .io, .co, .us, .de; crt.sh intermittent | a few thousand calls at 1 per second |
| 8 | Common Crawl historical page versions (not a site signal, but unblocks natural experiments) | URL | data.commoncrawl.org cluster.idx and range reads; reachable | about 100 MB per crawl index, then small range reads |

Needs a free sign-up (not reachable today without one): Open PageRank (Keywords Everywhere account), CrUX full list (Google Cloud project for BigQuery sandbox), CrUX API and Knowledge Graph API (Cloud API key), Cloudflare Radar (API token), GitHub at volume (token), Moz (account, 50 rows a month). Forbidden or paid for automated use: Ahrefs, Semrush. Blocked from this box: index.commoncrawl.org, web.archive.org, radar.cloudflare.com web download, Google Trends unofficial endpoint (429), Reddit JSON (403), WHOIS port 43, crt.sh Postgres.

## 13. How to test these without waiting on Google (proposal for the plan)

1. Truth: cross-site consensus pairs from the ordinary three-pull days already stored (Nov 16 onward), positions 1 to 10, and separately 1 to 30 from the deep list. Same consensus rule as `RESULTS-claude.md` section 0.
2. Split by search group (train groups only for design) and by site fold. Every niche feature for a pair in group g is computed from searches outside g, on days before the outcome day.
3. Score each signal alone first, by pair type: both sites with footprint; generalist against specialist (a site that appears across many unrelated groups against one that appears in few); navigational searches apart.
4. Then the combination: pairwise logistic regression on [page-blend score difference, niche standing difference, global gate indicator], fitted on train groups, frozen, scored on held-out groups and the test2 sites with two-way intervals.
5. Report against the persistence ceiling for URLs that already rank, and on new or changed URLs separately, because that is where the product needs the cross-site part.

## Sources not opened or not verified

moz.com blocks the fetch tool (read with curl instead). Springer chapter page (login redirect; metadata from Crossref). The CrUX top-lists paper (not opened). Cisco Umbrella's own terms and methodology (not read). Cloudflare's free-plan token terms (not read). Semrush's Authority Score correlation (image only). Backlinko's study page (not opened). Ahrefs Free FAQ (snippet). Knowledge Graph API free quota (not stated on the page read). Keywords Everywhere sign-up terms (not stated).
