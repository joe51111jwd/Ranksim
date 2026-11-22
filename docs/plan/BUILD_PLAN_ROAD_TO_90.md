<!--
TRANSCRIPTION NOTE (not part of the plan):
This file is a word-for-word text transcription of
docs/plan/Ranksim_build_plan_the_road_to_90.pdf
(sha256 1c31ef664b8e34382be185a4bb99a5648b43659559b45330402142f4cf4230af, 13 pages).
The PDF is the source of truth. The body text and tables were extracted from the
PDF text layer; the three figures are described in plain text where they appear,
with every label and number that is printed in the figure. Nothing was added,
removed or reworded. If this file and the PDF ever disagree, the PDF wins.
-->

# Ranksim build plan: the road to 90%

Nov 21, 2026 · @James Camarota

## The target

The target is 90% of forecasts graded as hits. The plan aims to reach it at the Confirmed forecast, about 60 days after a small seed version of the site goes live. Today's estimate for that day is about 88%, so the new parts must add 2 points. On upload day a brand-new domain does not reach 90%.

Every accuracy figure in this document is an estimate until the markers below are measured. Nothing here has been validated yet.

### How a forecast is graded

The graded outcome is the page's real Google position 90 days after launch, read by the clean reader (part 1).

| Where the page really lands | The forecast is a hit when |
|---|---|
| Positions 1–10 | it is within 2 places |
| Positions 11–20 | it is within 5 places |
| Positions 21–50 | it names the 21–50 band |
| Beyond 50, or not ranked | it says "not in top 50" |

The last row merges two older bands into one. That choice still needs your yes (see Decisions).

### Two numbers that keep the 90% honest

Most new sites land beyond position 50. A tool that always says "not in top 50" scores about 63% while knowing nothing. So two more numbers are tracked beside the 90%:

- **Page-one precision.** When Ranksim says "page one", it is right. Target 85%. Estimate at day 30 today: about 81%.
- **Page-one recall.** Of the searches that do reach page one, Ranksim called them. Target 80%. Estimate at day 30 today: about 75%.

### Where each number is expected

> **[Figure 1, page 2 — line chart]**
> Title: "The estimate reaches 88% by day 60, two points short of the 90% goal"
> Subtitle: "Estimated hit rate for a brand-new domain. Estimates, not measurements."
> X axis: "Days since the seed site went live". Points on the line:
>
> | Stage | Estimated hit rate |
> |---|---|
> | Upload day | 66% |
> | Day 7 | 74% |
> | Day 14 | 77% |
> | Day 30 | 82% |
> | Day 60 | 88% (labelled "2 points short") |
>
> Solid horizontal line: "Goal: 90%". Dashed horizontal line: "Always saying not in top 50: 63%".
> Caption: "estimates from the research reports in this conversation · 5 stages"

Day 30 and day 60 carry the plan. The new parts must add about 3 points at day 30 and 2 points at day 60.

Two cases are easier from the start. Brand-name searches are about 90%+ at upload. Same-domain redesigns are about 80% at upload, because the old site's history anchors the forecast.

## The parts

Ranksim is seven parts. Two measure, four score, one updates the forecast as Google reacts.

> **[Figure 2, page 3 — flow diagram]**
> Title: "Seven parts make the forecast. The trust model is the new one."
> Boxes, top to bottom:
>
> - **1 Clean reader** — "reads real Google positions, noise removed"
> - (the clean reader feeds three boxes side by side)
>   - **3 Site strength** — "where it ranked before"
>   - **4 Content match** — "how well the page fits"
>   - **5 Trust model** — "how far Google trusts it" (highlighted in blue as the new part)
> - (all three feed) **6 Placement model** — "gives a position, a range and a label"
> - (feeds) **7 Early-reading updater** — "moves the forecast as Google reacts, day 7 to day 60"
> - (feeds) **2 Scoreboard** — "grades every forecast against the real position"
>
> A feedback arrow runs from **2 Scoreboard** back up the right side into **5 Trust model**, labelled "Real outcomes go back in as training data".
> Caption: "how the parts connect · 7 parts, 1 feedback loop"

Live results enter at the top. Graded outcomes come back up the right side as training data.

| # | Part | What it does | Built from | Where it stands today |
|---|---|---|---|---|
| 1 | Clean reader | Reads Google's real positions without noise | A results API with a pinned city, desktop, 50 results deep, 5–7 pulls from separate sessions, a filter for poisoned pages, the middle position, graded per site | Repo pulls have no pinned location. Two pulls agree within 2 places 68% of the time |
| 2 | Scoreboard | Stores every forecast and grades it against later readings. Every accuracy number comes from here | A database and a nightly grading job | Not running as a standing system |
| 3 | Site-strength map | Says how strong each site is on each topic, from where it has ranked before | Monthly past result pages plus free strength lists | Repo finding: site strength is about three quarters of what sets rank |
| 4 | Content match scorer | Says how well a page fits the search | A pretrained language model that compares a search with a page | Untrained version scores about 0.69–0.70 on same-site pairs. Content is about 10–12% of rank |
| 5 | Trust model (new) | Estimates how much Google will trust a site it has barely seen | Thousands of real sites, what is visible on them, and how Google treated them | Not built |
| 6 | Placement model | Turns live results, the three scores and trust into a position, a range and a label: locked, contested or out of reach | Gradient-boosted trees trained on where real new pages landed | Early version: about 6.5 places off in a top 30, against 7.5 for guessing the middle |
| 7 | Early-reading updater | Moves the forecast as Google reacts to the seed site at day 7, 14, 30 and 60 | The site's own Search Console plus the clean reader | Not built. Connecting Search Console is still open decision D2 in the repo |

### The trust model, in plain words

Trust is the missing number for a new site. It can be estimated before Google has seen much, but not computed exactly.

- **What goes in.** The site's stage (age, size, pages indexed). What is on it (topic focus, writing quality). Its footprint (links, people searching its name, reviews). The search itself (how strong the current top 10 are).
- **What comes out.** A trust score for the site, compared with the trust that results page demands.
- **What it learns from.** Real sites where the answer is known: sites that rank above or below what their content alone would earn.

Your title example belongs to part 4, not part 5. A title that matches the search exactly means the page fits. On easy searches fit can win. On competitive searches trust decides.

## The data

Twelve data sources cover everything the seven parts need. Six are free and none costs more than about $120. Prices are from DataForSEO's published lists, [Labs](https://dataforseo.com/pricing/dataforseo-labs/dataforseo-google-api) and [results pages](https://dataforseo.com/help-center/serp-api-pricing-depth-update-faq), read on 21 November 2026.

| # | Data | What it tells us | Where it comes from | Cost | Feeds part |
|---|---|---|---|---|---|
| 1 | Live Google results for each target search | Who ranks now. This is the list the new page must beat | DataForSEO results API, pinned city, 50 results deep, 5 pulls | $0.0006 per page of 10. One clean reading is about $0.015 | 1, 6 |
| 2 | Past result pages for each search, month by month | How often a new site has ever broken onto that page | DataForSEO Historical SERPs | $0.00012 per monthly page. 5,000 searches over 24 months is $14.40 | 3, 6 |
| 3 | A list of new sites with their start dates | Which sites to learn from | Free daily lists of newly registered domains: [cenk/nrd](https://github.com/cenk/nrd), [shreshta-labs/newly-registered-domains](https://github.com/shreshta-labs/newly-registered-domains) | Free | 5 |
| 4 | What happened to those sites | Each site's Google traffic by month. It shows whether Google came to trust it | DataForSEO Historical Bulk Traffic Estimation | $0.12 per batch of 1,000 plus $0.0012 per site. 20,000 sites is $26.40 | 5 |
| 5 | Exact positions for a sample of sites | Which searches a site ranks for, and where | DataForSEO ranked keywords, standard Labs rate | $0.012 per request plus $0.00012 per row. 2,000 sites at 100 rows is about $48 | 5, 6 |
| 6 | Site strength lists | Authority and popularity of every site on a results page | [WebsiteLaunches top 100 million domains](https://github.com/WebsiteLaunches/top-100-million-domains), Google's Chrome popularity data on BigQuery, Common Crawl's link graph | Free | 3, 5 |
| 7 | Site text | Topic focus, writing quality, title match | Own crawler for live pages. Common Crawl for older pages | Free, plus GPU time | 4, 5 |
| 8 | The business's footprint | Reviews, people searching the brand name, existing links, any old site | A short form the agency fills in, plus brand search volume | Cents per site | 5 |
| 9 | The launch plan | Links, press and ads planned around launch | The same agency form | Free | 5, 6 |
| 10 | Google's first reaction | How fast pages get indexed, impressions, average position | The new site's own Search Console, connected by the agency | Free | 7 |
| 11 | Our own seed sites | The only direct test that early readings predict the day-90 position | 10 new domains, each with a small real site | About $120 | 2, 7 |
| 12 | Customers' launches | Every forecast and its real outcome, from day one | The scoreboard | Free. Grows with use | All |

### Checks before spending

Each paid source gets a $1 sample first. Two answers are needed before the big pulls:

- Do very small new sites show up at all in the traffic history (source 4)? If most show nothing, source 2 becomes the main way to find new sites.
- How deep and how far back do past result pages go for the chosen searches (source 2)?

### What stays out

- SmartCrew's data. It is a client and is not used to build or test.
- Any other company's private Search Console.
- The Semrush and Ahrefs exports sitting in public repos. They carry no licence.
- Bought click data. Its value is unproven. Revisit after marker 5.

Two free dumps are useful as test material only: [johnmurch/all-the-serps](https://github.com/johnmurch/all-the-serps) (4,347 Google result pages, January 2020) and the Wayback Machine's archived result pages through [webis-de/archive-query-log](https://github.com/webis-de/archive-query-log).

## Build steps

The build runs 17 weeks, from 23 November 2026 to the final check on 19 March 2027. The measuring parts come first, because no accuracy number means anything until the reader is clean.

> **[Figure 3, page 8 — Gantt timeline]**
> Title: "The 90% check lands on 19 March, 17 weeks in"
> Subtitle: "Build plan, 23 November 2026 to 19 March 2027. Diamonds are markers."
> X axis ticks: Nov 23, Dec 7, Dec 21, Jan 4, Jan 18, Feb 1, Feb 15, Mar 1, Mar 15.
>
> | Row | Kind | Dates |
> |---|---|---|
> | Reader and scoreboard | bar | Nov 23 to Nov 29 |
> | Marker 1: clean reader | diamond | Nov 30 |
> | Seed sites, read weekly | bar | Nov 30 to Feb 28 |
> | Training set | bar | Nov 30 to Dec 6 |
> | Trust model | bar | Nov 30 to Dec 13 |
> | Marker 2: trust model | diamond | Dec 14 |
> | Scoring parts, backtest | bar | Dec 7 to Dec 20 |
> | Marker 3: upload | diamond | Dec 21 |
> | Early-reading updater | bar | Dec 21 to Jan 17 |
> | Agency pilots | bar | Dec 28 to Mar 19 |
> | Marker 4: day 30 | diamond | Jan 22 |
> | Grade real outcomes | bar | Jan 18 to Mar 19 |
> | **Marker 5: the 90% check** | diamond (highlighted) | **Mar 19** |
>
> Caption: "build timeline · 8 workstreams, 5 markers"

The seed sites start in week 1 because their day-90 results set the date of the final check.

1. **Day 1, 23 Nov. Set up.** Confirm the grading rule. Put $50 into DataForSEO. Open a free BigQuery account. Buy 10 domains. Pick 3 niches that match the agencies you want. Set the repo to private.
2. **Week 1. Clean reader and scoreboard (parts 1 and 2).** Read each search from a pinned city on desktop, 50 results deep, 5 pulls from separate sessions. Drop poisoned pages, take the middle position, grade per site. Store every forecast and every later reading. Test: read 300 searches twice, a day apart. This is marker 1.
3. **Week 1. Launch the 10 seed sites (data 11).** Each is a real small site of 10–15 pages in one of the 3 niches. Connect Search Console on day one. Pick 20 target searches per site: a third easy, a third medium, a third hard. The clean reader reads all 200 searches every week until day 90, on 28 February.
4. **Week 2. Build the training set (data 2–7).** Run the $1 samples first. Take 20,000 sites that started 12–30 months ago and keep the ones with real content. Pull their traffic curves. Pull past result pages for 5,000 searches in the 3 niches. Pull exact positions for 2,000 sites. Crawl their text. Join the free strength lists.
5. **Weeks 2–3. Trust model, version 1 (part 5).** The label is how far above or below its content a site really ranks. Train on 80% of the sites. Test on the 20% the model has never seen. This is marker 2.
6. **Weeks 3–4. Wire the scoring parts together (parts 3, 4 and 6).** Backtest on past new sites. Forecast using only what was knowable in the launch month. Grade against the real position three months later. This is marker 3.
7. **Weeks 5–8. Early-reading updater (part 7).** Backtest first: update each past forecast with the site's month-1 results and grade against month 3. Then add the seed sites' real readings at day 7, 14 and 30. This is marker 4.
8. **Week 6 onward. Pilot with 2–3 agencies on real launches.** Every forecast goes on the scoreboard from day one. This is data 12, and it is the data nobody else has.
9. **Weeks 9–17. Grade the real outcomes.** The seed sites reach day 60 on 29 January and day 90 on 28 February. Grade every day-30 and day-60 forecast against the real day-90 positions. This is marker 5.

The monthly backtest in steps 6 and 7 is a stand-in. Past data comes by the month, so month 1 stands for day 30 and month 3 for day 90. The seed sites are the check that the stand-in is fair.

## Markers

Five markers stand between today and a 90% you can say to a customer. Each has one number, one test and one date. Marker 5 is the only one that licenses the word "90%" in a sales call.

| Marker | What must be true | Target | Today's estimate | How it is measured | Date |
|---|---|---|---|---|---|
| 1. Clean reader | Two clean readings of one search, a day apart, agree within 2 places | 92% or more | 92–97%. Raw pulls today: 68% | 300 searches, each read twice | Nov 30, 2026 |
| 2. Trust model | On sites it has never seen, it puts the more trusted site first | 70% of pairs or more | Unknown. A plain link count gets 63% | The fifth of the training sites held back for testing | Dec 14, 2026 |
| 3. Forecast at upload | Hit rate for brand-new domains | 75% or more | 66% today. Low to mid 70s with the new data | Backtest on past launches | Dec 21, 2026 |
| 4. Forecast at day 30 | Hit rate, plus page-one precision of 80% | 85% or more | About 82% | Monthly backtest, plus seed-site readings | Jan 22, 2027 |
| 5. Confirmed forecast at day 60 | Hit rate, plus page-one precision of 85% and recall of 80% | 90% or more | About 88% | Real day-90 outcomes from seed sites and pilots, plus the backtest | Mar 19, 2027 |

Marker 5 is graded on about 200 real forecasts from the seed sites. A 90% score on 200 forecasts could really be anywhere from about 85% to 94%. The backtest carries the larger sample, and the pilot launches add to it every week.

### If a marker is missed

Each marker has two fixes to try in order, then a fallback that keeps the plan honest. A missed marker is retested within two weeks of each fix.

| Marker missed | First fix | Second fix | If it is still short |
|---|---|---|---|
| 1. Reader agrees under 92% | Go from 5 pulls to 7–9 and tighten the poisoned-page filter | Rerun in a calm week. A Google core update moves real positions | The noise is Google's, not the reader's. Widen "within 2" to the measured noise for those positions and say so openly |
| 2. Trust model under 70% of pairs | Add footprint signals: brand searches, reviews, link growth | Train one model per niche and grow from 20,000 to 100,000 sites, about $130 more | Trust stays a rough estimate from footprint. The plan leans harder on early readings, and upload-day ranges get wider |
| 3. Upload forecast under 75% | Find the band that causes the misses and add more months of past result pages | Make the agency form (footprint and launch plan) required for every new site | Upload day ships as a first estimate with a range, never a promise. The 90% rests on markers 4 and 5, so the plan continues |
| 4. Day-30 forecast under 85% | Add more early signals: indexing speed, impression growth, first positions on easy searches | Make the seed site a standard step, 4–6 weeks before launch | Move the Confirmed forecast from day 30 to day 45–60 |
| 5. Day-60 forecast under 90% | At 85–89%: move Confirmed to day 75–90 and regrade. Later readings sit closer to the outcome | If misses cluster in contested searches: hold 90% on locked and out-of-reach calls and show contested ones with a wider range. Coverage stays at 85% of searches or more | The stop rule below |

### The stop rule

If the day-60 hit rate is under 80% after both fixes, Ranksim does not sell a 90% promise for brand-new domains. It leads with redesigns and edits of existing sites, where an anchor exists. New-site forecasts are then sold as a page-one yes or no, with the measured precision printed beside them.

### Other things that can go wrong

- **Google ships a core update mid-test.** The scoreboard shows the hit rate falling. Pause promises, keep reading, and regrade after two calm weeks.
- **The backtest and the real outcomes disagree by more than 5 points.** Trust the real outcomes. The backtest was using facts that were not knowable at launch. Fix that and rerun.
- **Seed sites do not get indexed.** That is a reading in itself. Add 5 more sites in a different niche by week 3, about $60.
- **The data provider changes price or access.** Keep the reader behind one interface. Serper is the backup at $50 per 50,000 searches, and the repo already uses it.

## Budget

The whole plan costs about $310 through marker 5, with a ceiling of about $500 if every fallback is used. That replaces the earlier $4–6K study and the $25–45K lab.

| Item | What it buys | Cost (USD) |
|---|---|---|
| Traffic curves | 20,000 new sites, monthly Google traffic | 26.40 |
| Past result pages | 5,000 searches over 24 months | 14.40 |
| Exact positions | 2,000 sites, 100 rows each | 48 |
| Clean readings | Marker 1 test, 13 weeks of seed-site readings, regrades | 68 |
| $1 samples | Checks before the big pulls | 3 |
| Seed-site domains | 10 domains at about $12 each | 120 |
| GPU rental | Scoring page text. An allowance, your quote decides | 30 |
| **Total** | | **about 310** |
| Reserve for fixes | 100,000-site growth ($130) and 5 more seed sites ($60) | 190 |

Hosting the seed sites and the BigQuery lookups fit inside free tiers. DataForSEO's smallest top-up is $50, so the cash goes in as a few top-ups.

Once live, one customer launch of 50 searches costs about $10 in readings over 13 weeks.

## Not proven yet, and decisions needed

Seven things in this plan are still assumptions. The markers exist to test them.

1. **Early readings predict the day-90 position.** No public study shows this. The seed sites and pilots are the test, and marker 5 depends on it.
2. **Every hit rate here is derived, not measured.** None has been run on Ranksim.
3. **The trust model has never been built for this use.** Google's patent US9767157B2 describes a similar idea. That does not prove ours works.
4. **The cheap traffic data may be blind to very small new sites.** The $1 sample answers this.
5. **The monthly backtest is a stand-in** for day-by-day readings.
6. **The clean reader's 92–97% is an estimate.** Since September 2026 Google has served altered pages to rank checkers. One tracker measured 27.6% of result pages affected.
7. **The headline hit rate is helped by easy calls.** Most new sites land beyond 50. Page-one precision and recall are the harder numbers.

### Decisions needed from you

- [ ] Accept the merged "not in top 50" band? Without it, the day-30 estimate is about 73%, not 82%.
- [ ] Accept that 90% is promised at the Confirmed forecast, with upload day shown as a first estimate?
- [ ] Approve about $310, with a ceiling of $500.
- [ ] Pick the 3 niches.
- [ ] Connect Search Console for the seed sites and pilots. This is decision D2 in the repo.
- [ ] Name 2–3 pilot agencies. SmartCrew's data stays out of training and tests.
- [ ] Set the Ranksim repo to private. It holds SmartCrew material and your revenue plan.

## Sources

- [DataForSEO Labs Google API pricing](https://dataforseo.com/pricing/dataforseo-labs/dataforseo-google-api), page dated 12 October 2026
- [DataForSEO SERP API pricing and depth FAQ](https://dataforseo.com/help-center/serp-api-pricing-depth-update-faq), updated 29 October 2026
- [Chrome UX Report on BigQuery](https://developer.chrome.com/docs/crux/bigquery?hl=en)
- GitHub repos linked in the data section, read on 21 November 2026
- Accuracy figures: the Ranksim repo (branch claude/goal-ranksim-works) and the three research reports from this conversation
