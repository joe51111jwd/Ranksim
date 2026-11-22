# Public data catalog

Found 22 November 2026 by nine hunters, each followed by a skeptic that downloaded a fresh sample and tried to refute the find. Every source below was opened and parsed in this environment without an account. Corrections from the skeptics are folded in.

James's instruction of 22 November: licence terms do not limit which public data is used for this internal training work. So nothing was dropped for its licence. Each row records where the data comes from. Four limits were kept: no keys or passwords found in public places, no sign-ups or spending, no datasets that are personal data about individuals, and no SmartCrew private data.

## The short answer

- **No public dataset gives new-site launches with their Google position 90 days later.** Every hunter looked for it. It does not exist in public. The plan's paid pulls (past result pages, traffic history, exact positions) and the seed sites are still needed for the trust model and the full backtest.
- **One real backtest-shaped set is free.** DataForSEO publishes sample files of its paid databases for 117 locales. Together: about 11,700 searches, about 53,000 dated result pages, 100 results deep, May 2025 to April 2026, in the same layout the paid Historical SERPs endpoint returns. It is small per country (100 searches each) and is country level, not city level.
- **Site strength as of a past date is fully covered for free.** Common Crawl domain ranks (54 releases since 2017, monthly since April 2024), OpenPageRank history, Chrome popularity by month, Tranco by day.
- **Reader noise has real data.** Repeated pulls of the same search in the DataForSEO samples, and a 65-city repeated-pull study from 2022.
- **Google's update calendar is free and official.**

## Sources the build uses

| Use | Source | What it holds | Dates | Limits |
| --- | --- | --- | --- | --- |
| Backtest panel (parts 3, 6, 7) | DataForSEO sample databases, `data.dataforseo.com/examples/databases/google_history/serp_advanced/<CC>_<lang>.json.gz` | 117 locales, 100 searches each, a dated result page about every 46 days, organic results about 96 deep, each with the site's DataForSEO rank, plus search volume, difficulty and intent | May 2025 to Apr 2026 | Country level. 100 searches per locale. No licence stated. Google began serving altered pages to scrapers in mid Sep 2026. The sample ends before that |
| Mechanics at depth (parts 3, 6) | Same host, `google/serp_advanced/<CC>_<lang>.json.gz` | 1,000 searches per locale, one result page each, about 76 deep | Mar to Apr 2026 | One date per search |
| Search metadata | Same host, `google/keywords` and `google_history/keywords` | 1,000 searches per locale with volume, cost per click, competition, difficulty, intent, and 12 months of volume | Apr 2025 to Apr 2026 | Sample size |
| Trust model examples (part 5) | Same host, `whois/full/data.json.gz` | 1,000 domains with creation date, counts of ranked searches by position bucket, estimated traffic, referring domains. 274 were created in 2023 or later, 99 of those rank | Metrics about Mar 2026 | 1,000 rows. Metrics are one snapshot, so not usable as launch-month features |
| Site strength by date (parts 3, 5) | Common Crawl web graph domain ranks, `data.commoncrawl.org/projects/hyperlinkgraph/` | Every domain's harmonic centrality and PageRank position per release, 2 to 3.5 GB each | 54 releases, May 2017 to Oct 2026, monthly since Apr 2024 | Each release covers three crawls and is published after them. The publication date is the leakage key |
| Site strength by date | OpenPageRank history, `download.openpagerank.net/top10milliondomains-history.csv.zip` | 10 million domains with a score at 51 dates | 2018 to Oct 2026, monthly since May 2024 | Only domains in today's top 10 million are in the file. Using it to pick a cohort of young sites leaks the outcome. Fine as a feature for rivals |
| Site strength by date | Chrome popularity lists, `github.com/zakird/crux-top-lists` | Top 1 million origins per month in popularity buckets | Feb 2021 to Oct 2026 | Coarse buckets. New sites rarely appear. Bucket scheme changed over time |
| Site strength by date | Tranco, `tranco-list.eu` | Top 1 million domains for any day | 2019 to today | Blends other lists, including Chrome data since 2024 |
| Site size and first sighting | Common Crawl host index, `huggingface.co/datasets/commoncrawl/host-index-testing-v2` | One row per host per crawl: pages fetched, rank | 26 crawls, Dec 2021 to Apr 2025 | A host appears when it is first linked to, not when it launches. 204 GB in total |
| Launch-month page text (parts 4, 5) | Common Crawl page index and archive, `data.commoncrawl.org` (`cluster.idx` plus byte-range reads) | The stored HTML of a site in a given crawl | 2008 to today | Small sites are often missing from a given crawl. The Wayback Machine is blocked from this environment |
| Start dates | RDAP registration lookups, `rdap.org` | Registration date per domain | Current record | Rate limited, so thousands not millions. A re-registered expired domain looks new |
| Start dates | New-domain lists in git history: `github.com/cenk/nrd` (since Aug 2025, about 70,000 a day), `github.com/shreshta-labs/newly-registered-domains` (since Jan 2024, small sample) | Domains by the day they were registered | See left | Neither covers May 2024 to mid 2025 well |
| Start dates | Kaggle `wotschofsky/171-million-domain-names-whois-dns-dnssec` | 171 million domains with registration date and name servers | Snapshot Oct 2024 | 26 GB. 16% have no date. About 9% of 2023 to 2024 registrations sit on parking name servers |
| New-site cohort with launch-day pages | Kaggle `hopehaowang/nrd2024` | 1.5 million domains registered 10 to 16 Aug 2024, with launch-day home page HTML for the 10 Aug cohort and abuse labels for about 2,000 | Aug 2024 | Labels mark abuse, not how Google treated the site |
| Launch lists | Show HN posts through the Hacker News search API. Product Hunt launch lists on Hugging Face | Sites with a public launch day | 2023 to 2025 | Median domain age at launch was 281 days, so launch day is not registration day. User names are dropped |
| Reader noise (part 1) | Repeated pulls inside the DataForSEO history samples | The same search read again, about 2,900 pairs. About 2,700 of them are less than a day apart | 2025 to 2026 | Country level, single pulls. Mostly a same-day measure, not a day-apart one |
| Reader noise | Wellesley Dobbs study, Harvard Dataverse `doi:10.7910/DVN/YFAH9X` | About 1,000 to 1,700 searches read in 65 US cities, mornings for 6 days and evenings for 20 | Jun to Jul 2022 | About 8 organic results per page. One topic. The tweets file is left out (personal data) |
| Google updates (part 2) | Google Search Status Dashboard history | 41 dated ranking updates with start and end | Dec 2021 to Nov 2026 | None |
| Poisoned-page rates | Nozzle public study files | Daily share of altered result pages by country and device | Aug to 3 Nov 2026 | One vendor's measure |
| Content match checks (part 4) | Kaggle `eliasdabbas/flights-serps-and-landing-pages` | Top 10 for 200 searches in two countries with the landing pages' text | Mar 2020 | Google's search API, one niche, old |
| Early-signal examples (part 7) | Search Console exports their owners published: a site launched Sep 2026 (`github.com/thanhnt1910/gsc-data`), a young site's 13 months of daily data (Kaggle Tuscany bike route), a 2021 site (Dataverse `LJOAKJ`), a large shop's daily 2022 export (Zenodo 7687893) | Daily clicks, impressions, position | 2021 to 2026 | One site each. See the note below |

Note on the Search Console exports. The plan's "what stays out" list names "any other company's private Search Console". These four were published by their owners, so they are not private, and under your 22 November instruction they are used as examples for the importer and for priors. Say so if you want them left out.

## Published numbers used as starting points

These are other people's measurements. They set priors. They are not Ranksim results.

| Number | Source |
| --- | --- |
| 1.74% of new pages reach the top 10 within a year. 6.11% for English pages with real content. Of the pages that get there, 40.82% do so in the first month | Ahrefs, 2025, pages first seen Sep to Oct 2023 |
| 13.7% of top-10 pages are under a year old. 72.9% are over three years old | Ahrefs, 2025 |
| 2.03% of top-10 domains are under two years old. Those young top-10 domains have a median of 45 referring domains | SE Ranking, 2026 |
| Of 28,000 new domains that already ranked somewhere, 7.65% stayed in the top 100 for 13 months. The often-quoted 41% and 19% are shares of that surviving 7.65%, not of all new domains | Semrush, 2023, corrected by our skeptic |
| 14% of pages are indexed within 7 days, 65% within 30, 77% within 90. 62% of submitted pages are never indexed | IndexCheckr, 2025 |
| Altered result pages for scrapers: about 8% of pages since 13 Sep 2026, near 28% on 29 Sep, 9% on 9 Oct, and back to 30% on 3 Nov. It is ongoing, not a closed episode. 78% of spells recover within 3 days. A retry returns a normal page about 3 times in 4 | Nozzle and AccuRanker, 2026 |
| Same search, same city, morning against evening: 83% of results within 2 places. Two cities in the same pull: 64% at the same position | Measured here on the Wellesley Dobbs data, 2022 |
| Same search read again, mostly within the same day: 92 to 94% of top-10 domains still present | Measured here on the DataForSEO samples |
| 3.15% of sites launched on Show HN in 2023 entered the Tranco top million within 12 months | Measured here |

## Looked for and not found

- Google result lists for the same searches month by month, 20 to 100 deep, in US English, in public, at any real size. The DataForSEO US sample has 100 searches.
- Monthly Semrush, Ahrefs or Similarweb histories for young domains.
- Search Console exports or rank-tracker histories for new sites at scale.
- The Webis archive of old Google result pages. It is only reachable through a research sandbox, and most of its Google pages were captured once.

## Left out on purpose

- Files that hold personal data: the raw Yandex leak table (employee logins), the Dobbs tweets file, a German result set with people's names and profile pages.
- Anything behind a login, a request form or a token: BigQuery tables, Cloudflare Radar, the TUM top-list archive, a restricted product-search corpus on Zenodo.
- Repackaged copies of Common Crawl ranks. The original has every release.

## Worth starting now, because the source keeps no history

- `smet.cz` daily new-domain lists keep about 94 days. Saving them daily builds the cohort the trust model needs for 2027.
- Majestic Million publishes only today's file. Saving it daily gives a dated link-strength series.
- Past copies of several lists exist on the Wayback Machine, which this environment cannot reach. They can be fetched from another network.
