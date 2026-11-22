# Audit of the build plan: what is wrong or unclear, and what the code does about it

Audited 22 November 2026 against `docs/plan/BUILD_PLAN_ROAD_TO_90.md`. Every number here was recomputed with code or read from a live page or from this repo. Nothing in this file is an accuracy result.

Status words: **Fixed in code** means the software refuses or corrects it. **Reported** means every report prints it. **Needs James** means only you can settle it.

## A. Things that would make a marker mean less than it says

| # | Finding | Evidence | What we did |
| --- | --- | --- | --- |
| A1 | Marker 5 cannot include any pilot. A launch must happen by 19 December to reach day 90 by 19 March. Pilots start 28 December. | 28 Dec plus 90 days is 28 March | Reported. Each marker report counts eligible forecasts by source and prints "0 eligible" for a source with none. **Needs James**: launch a pilot by 19 Dec, or move marker 5 for pilots |
| A2 | Marker 4 (22 January) cannot grade any seed forecast against its real outcome. The seed sites reach day 90 on 28 February. | 30 Nov plus 90 days is 28 Feb | Reported. On 22 January the seed part is shown as interim only. The backtest carries marker 4 until 28 February |
| A3 | Marker 5 rests on 10 sites, not 200 independent forecasts. Searches on one site rise and fall together. The plan's "85% to 94%" is right only if all 200 were independent. | Wilson interval for 180 of 200 is 85.1 to 93.4. With forecasts clustered in 10 sites it is about 81 to 95, and as wide as 77 to 96 if a site's searches move together strongly | Fixed in code. Intervals resample whole sites. The independent-forecast interval is printed beside it and labeled |
| A4 | 200 forecasts cannot tell 88% from 90%. A product that is truly at 88% would show 90% or more about 1 time in 4. A product truly at 90% would show under 90% about 4 times in 10. | Binomial, n = 200. Telling 88 from 90 needs about 1,550 independent forecasts | Reported. **Needs James**: accept that marker 5 on seed sites is a first reading, and that the claim firms up as pilot launches are graded |
| A5 | Page-one precision and recall at marker 5 would rest on about 20 to 36 cases. That cannot test an 85% or 80% bar. | 85% on 20 cases has an interval of about 64 to 95 | Fixed in code. Precision and recall print NOT MEASURABLE when the count is under 30 |
| A6 | The grading rule can be passed by knowing only the band. A point of 15 hits every real position from 11 to 20. A point of 25 hits every real position from 20 to 50. A tool that knows the right band and nothing else already scores 92 to 97%. | Enumerated every case | Reported. Each report prints the plain band match, a mirrored variant of the rule, and the share of points sitting on 15 and 25 |
| A7 | The rule is not symmetric. Forecast 21 for real 20 is a hit. Forecast 20 for real 21 is a miss. | Tolerance comes from the real position | Reported, same as A6. The plan's table is kept as written |
| A8 | The 63% for "always says not in top 50" depends on which searches are picked. It is not a property of Google. The seed searches are picked by us. | The plan picks a third easy, a third medium, a third hard | Fixed in code. The empty tool is scored on the same forecasts in every report, and skill over the best baseline is printed beside the hit rate |
| A9 | Several fallbacks rewrite the test after the result is known: widening "within 2", moving Confirmed to day 75 to 90, regrading after a core update, rerunning the backtest after real outcomes arrive. | Plan fallback table | Fixed in code. The grading rule has a version and a hash on every grade. A regrade is a new row beside the original. A later Confirmed day is reported as a shorter lead time |
| A10 | "Move Confirmed to day 75 to 90" turns the forecast into the outcome. A day-90 forecast graded on day 90 measures only reading noise. | Plan, marker 5 first fix | Fixed in code. A number is called a forecast only with at least 28 days of lead. Every report shows hit rate by lead time |
| A11 | A day-60 forecast may be no better than copying the day-60 reading. That is fine as a product. It is not model skill. | No baseline in the plan | Fixed in code. Persistence of the latest reading is a baseline in every report |
| A12 | A hit rate of 80 to 84.9% at marker 5 has no defined action. The first fix covers 85 to 89 and the stop rule starts under 80. | Plan fallback table and stop rule | **Needs James** |

## B. Leakage in the backtest (markers 2 and 3)

| # | Finding | What we did |
| --- | --- | --- |
| B1 | Today's strength lists would be used as if known in the launch month. A site that launched two years ago and did well is in today's lists because it did well. | Fixed in code. Every feature value carries its snapshot date. The feature builder refuses a value dated after the forecast date. A source with no dated history is refused in backtests |
| B2 | For past sites the target searches would be picked from where the site later ranked. That selects on the outcome. | Fixed in code. Backtest target searches are picked by a frozen rule that does not look at later rankings, and include searches where the site never appears |
| B3 | The trust label ("how far above or below its content a site ranks") depends on the content scorer. Its errors become the label. | Fixed in code. The content model version is frozen and hashed into the label table. Only out-of-fold content scores are used |
| B4 | Survivorship. "Keep the ones with real content" judged today keeps the sites that lived. Positions exist only for sites that rank. | Fixed in code. Dead and never-ranking sites stay in with their own label. Missing traffic is missing, not zero. The funnel counts are printed |
| B5 | An 80/20 split by site still puts the same searches on both sides. | Fixed in code. The split is by launch cohort in time, then by site, with no search group on both sides |
| B6 | "70% of pairs" is not defined, and the 63% link-count baseline comes from a different kind of pair. | Fixed in code. One frozen pair set. The link-count baseline is computed on the same pairs |
| B7 | The backtest would grade the sites the trust model trained on. | Fixed in code. Only held-out sites are graded |
| B8 | Past result pages are one snapshot per month. One snapshot is noisier than a clean reading, and "month 1" can be anywhere from about 1 to 60 days after launch. | Reported. Each backtest cell stores the real days since launch. Backtest results are labeled single snapshot and are never pooled with real outcomes |
| B9 | The plan takes sites that started 12 to 30 months ago but buys 24 months of past result pages. Sites that started 24 to 30 months ago have no launch-month page. | **Needs James**: buy 30 months (about $3.60 more), or use sites 12 to 24 months old. The code handles either |

## C. Terms the plan leaves open

Each of these had to be fixed to build anything. The reading we chose is in `IMPLEMENTATION_SPEC.md` and marked OUR READING. **Needs James** to confirm or change.

1. **What is graded: the page or the site.** The plan says "the page's real position" and also "graded per site". We grade the site's best position for the search and store the exact page's position as a second number.
2. **The launch date.** We use the day the site first served its real pages on the domain.
3. **What a forecast is.** One point (1 to 50, or "not in top 50"). The range and the label are shown and reported. They are not graded.
4. **Marker 1.** We count each site in the top 10 of either reading. They agree when the two positions differ by 2 or less. A site missing from one reading counts as position 51.
5. **"Separate sessions."** An API has no browser session. We use separate provider tasks at separate times and flag a reading whose pulls look like copies.
6. **"Poisoned page."** We use DataForSEO's own definition: fewer than half of the usual sites appear. The rule is frozen and the drop rate is stored with every reading.
7. **"Page one."** A point of 10 or better, and a real position of 10 or better.
8. **Confirmed.** Day 60. One line of the plan says day 30.
9. **5 or 7 pulls.** The parts table says 5 to 7. The steps and the budget say 5. We default to 5.

## D. Numbers in the plan that are not what they look like

| Plan says | What we found |
| --- | --- |
| "Two pulls agree within 2 places 68% of the time" | This figure is not written anywhere in the repo. It can be reproduced from the stored 19 November pulls (1,404 of 2,063) only by counting a page missing from the other pull as position 11. Counting only pages present in both pulls gives about 87%. Marker 1 measures its own raw-pull baseline in the same test, so the comparison does not rest on this number |
| "Site strength is about three quarters of what sets rank" | Derived from one pair of numbers in the old master plan (0.597 and 0.522). The plausible range is 45% to 95%. It is not a measurement |
| "About 6.5 places off in a top 30, against 7.5" | A simulation in the old master plan. No placement model was ever built. The one placement rule that was tested on held-out data did not beat always guessing position 7 |
| "A plain link count gets 63%" | Real, but on a different question: Majestic referring subnets on pairs that straddle position 10 in one day of result lists. It is not a trust test on new sites |
| "66% today", "always not in top 50 scores 63%", the 66/74/77/82/88 curve | No source in the repo. The repo has no data deeper than position 30 and has never computed a hit rate under this grading rule |
| "Untrained version scores about 0.69 to 0.70" | Real and reproduced: 0.694 on 5,623 same-site pairs. It was measured on construction software searches only and was never tested on held-out lists |
| "Without the merged band the day-30 estimate is about 73%" | The older band definitions are not in the plan, so this cannot be checked |

## E. Data and cost findings, checked against the live pages

| # | Finding | Effect |
| --- | --- | --- |
| E1 | All unit prices in the plan match DataForSEO's pages today. All cost lines add up ($309.80, ceiling $499.80). | None |
| E2 | The $0.0006 per page is the Standard queue price. The repo's current DataForSEO code calls the Live endpoint at $0.002, caps depth at 20, sends a country and not a city, and treats a failed task as an empty result. | Fixed in code. The new reader uses the Standard queue, a city code, depth 50, and treats a failed task as a failed pull |
| E3 | Since 26 September 2026 Google has sent scrapers altered result pages. DataForSEO shipped a repair option on 16 November 2026 (`resolve_poisoned_results`). It adds the price of one page per task. | A clean reading costs about $0.018, not $0.015, with the option on. The reading budget rises from $68 to about $82. One public tracker still measured about 30% of pages altered on 3 November, so this is ongoing. Past result pages stored since mid September 2026 without the repair cannot be fixed afterwards and are left out of training |
| E4 | DataForSEO's API page says past result pages go back 12 months. A help article says they go back to August 2021. The plan buys 24 months. | The $1 sample must settle this before the $14.40 pull. The sample command reports the earliest month returned |
| E5 | Past result pages, traffic history and ranked keywords are country level only. Only the live reader can be pinned to a city. | Models are trained on country pages and applied to city pages. The seed sites measure the gap |
| E6 | The free lists of newly registered domains do not cover May 2024 to November 2025 well. One keeps only recent weeks. The other is a small sample that is mostly non-English domains after mid 2024. | The public data hunt found better sources with start dates (see `PUBLIC_DATA_CATALOG.md`) |
| E7 | The WebsiteLaunches list is one snapshot from November 2025 with no dates. Majestic publishes only today's file. Both would leak in a backtest. | Fixed in code by B1. Dated sources replace them: Common Crawl ranks (56 releases since 2017), OpenPageRank history (51 dates since 2018), Chrome popularity (monthly since 2021), Tranco (daily) |
| E8 | The plan has no budget line for brand search volume (source 8), for the readings of the 5 extra seed sites, or for a 7 to 9 pull reader. Cash out is about $350, not $310, because DataForSEO top-ups come in $50 steps. | **Needs James**: the ceiling of $500 still holds if not every fallback is used |
| E9 | Weekly readings to day 90 are 13. Adding the day 30, 60 and 90 readings that fall off the weekly grid makes 16. Day 30 falls on 30 December. | The schedule command prints both counts and the cost of each |
| E10 | The step list says the seed sites launch in week 1. The dates in the plan (day 60 on 29 January, day 90 on 28 February) are only right for a launch on 30 November, which is week 2. | The seed manifest freezes one launch date per site. Every date is computed from it |

## F. Things only you can do

1. Confirm the grading rule and the readings in section C.
2. Approve the spend. The code refuses every paid call until an approval is recorded.
3. A DataForSEO login. No key is in this environment.
4. Buy the 10 domains and put real sites on them. Each launch date sets its own day 90.
5. Pick the 3 niches and the city the reader is pinned to.
6. Connect Search Console for the seed sites.
7. Name the pilot agencies, and launch at least one by 19 December if pilots are to count at marker 5.
8. Set the repo to private.
9. A free BigQuery account is optional now. Public mirrors cover the Chrome popularity data.
