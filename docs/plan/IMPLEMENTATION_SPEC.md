# Implementation spec for the build plan (the road to 90%)

This file turns `docs/plan/BUILD_PLAN_ROAD_TO_90.md` into exact definitions and module contracts.
The build plan is the product direction. This file is how the code reads it. Where the plan leaves a term
open, the reading chosen here is marked **OUR READING** so James can change it. A change to any frozen
definition makes a new version. It never rewrites an old grade.

Hard rules that always hold (from `docs/MISSION.md` and `GOAL.md`):

- Never fake a result. Model output is labeled PREDICTED. Observed Google positions are labeled REAL. Synthetic data is labeled SYNTHETIC and is never reported as accuracy.
- No leakage. A forecast may use only data whose snapshot date is on or before its `as_of` date. The code refuses anything later.
- No spending without a recorded approval. Every paid call goes through the budget guard. The default approved amount is 0.
- Never commit or print a key. No merge to main. No deploy.
- SmartCrew's private data (everything under `smartcrew/`, its Search Console exports, the PR 41 and PR 45 ledgers) is never used to train or test. Rows for `smartcrewhq.com` are dropped by the loaders.
- Reports to James use short plain sentences with no em dashes or en dashes.

## 1. Package

All new code lives in `src/aeo/ranksim/forecast/`. Tests live in `tests/test_forecast_*.py`.
CLI: `python -m aeo.ranksim forecast <command>`. Existing modules are not rewritten. They are imported where useful.

| Module | Part | Holds |
| --- | --- | --- |
| `types.py`, `grading.py`, `stats.py`, `chain.py`, `asof.py`, `config.py` | core | the shared contract. Other modules import these and do not redefine them |
| `budget.py` | all | spend ledger, approval, caps, cost estimates, the $1 sample gate |
| `dataforseo.py`, `serper.py` | 1 | typed provider clients. Raw JSON is saved before parsing |
| `reader.py`, `noise.py` | 1 | pulls, the poisoned-page filter, the clean reading, marker 1 |
| `scoreboard.py`, `grade_job.py`, `report.py`, `markers.py` | 2 | the database, the nightly grading job, marker reports |
| `seeds.py`, `agency_form.py`, `gsc.py` | 2, 5, 7 | seed-site manifest, the agency form, Search Console import |
| `sources/` | data | loaders for public and paid datasets. Every value carries a snapshot date |
| `strength.py` | 3 | site-strength map |
| `content.py` | 4 | content match scorer |
| `trust.py` | 5 | trust model |
| `placement.py` | 6 | placement model: position, range, label |
| `updater.py` | 7 | early-reading updater |
| `backtest.py` | 3 to 7 | the monthly backtest for markers 3 and 4 |
| `synth.py` | tests | a synthetic world with known truth, for end-to-end tests only |
| `pipeline.py`, `cli.py` | all | the commands a person runs |

Code style matches the repo: a module docstring that says what is REAL and what is PREDICTED, `from __future__ import annotations`, dataclasses, upper-case module constants for every threshold and seed, sync `httpx` clients injected through a `client=` argument, missing values stay `None` or NaN and never become 0. The literal string `AsyncClient` must not appear anywhere in `src/` (an existing test forbids it). Tests must pass with no keys and must never reach a paid host: provider tests build `httpx.Client(transport=httpx.MockTransport(handler))`.

## 2. Grading rule v1 (frozen in `grading.py`)

Unit: one (site, search). A site is its registrable domain (`aeo.ranksim.site_history.site_key`).

**Outcome (OUR READING).** The site's position in the clean reading taken 90 days after `launch_date`: the best organic position of any URL of the site, 1 to 50, or `NT50` when the site is not in the top 50, not ranked, or not indexed. The reading used is the one closest to day 90 inside days 87 to 93. If none exists the forecast stays ungraded. The position of the exact target URL is stored too and reported as a second number.

**Launch date (OUR READING).** The UTC date the site first served its real pages on the domain. For seed sites it is in the frozen seed manifest. For backtest sites it is estimated and the estimate is stored with its source.

**Forecast.** Exactly one point: an integer 1 to 50, or `NT50`. The forecast also carries a range and a label (locked, contested, out of reach). Only the point is graded.

| Where the site really lands | The forecast is a hit when |
| --- | --- |
| 1 to 10 | the point is a position and is within 2 places |
| 11 to 20 | the point is a position and is within 5 places |
| 21 to 50 | the point is in 21 to 50 |
| NT50 | the point is NT50 |

This is the plan's table read literally. Known quirks, reported and not hidden: the tolerance comes from the real position, so the rule is not symmetric (forecast 21 for real 20 is a hit, forecast 20 for real 21 is a miss). A point of 15 hits every real 11 to 20. A point of 25 hits every real 20 to 50. Because of that every report also prints the plain four-band match rate, a symmetric variant (tolerance taken from the forecast's band), and the share of points that sit on 15 or 25.

**Page one.** A page-one call is a point of 10 or better. A page-one outcome is a real position of 10 or better. Precision = page-one outcomes among page-one calls. Recall = page-one calls among page-one outcomes. Neither is printed as a pass when its count is under 30 (`MIN_COUNT_PRECISION_RECALL`); the report says NOT MEASURABLE with the count.

**Brand searches** (the search contains the site's brand name) are flagged, left out of the headline, and shown apart.

**Stages.** `upload` (as_of on or before launch), `d7`, `d14`, `d30`, `d60` (the Confirmed forecast). A forecast stores `as_of`, and every input it used must be dated on or before `as_of`. `lead_days = outcome_date - as_of`. A number is called a forecast only when `lead_days >= 28`. Moving Confirmed later than day 60 is reported as a shorter lead time, never as the same marker.

**Baselines printed on the same forecasts, every time:** always NT50; persistence (the latest clean reading on or before `as_of`, NT50 when none); band prior by search difficulty tercile, fitted on training data only; site strength only. Skill = (hit rate minus best baseline) / (1 minus best baseline).

**Intervals.** Cluster bootstrap that resamples sites (2,000 draws, seed 20261122). The Wilson interval is printed next to it and labeled "if forecasts were independent".

## 3. Clean reader (part 1)

- Provider: DataForSEO SERP API, Google organic, **Standard queue, normal priority** (`task_post`, `tasks_ready`, `task_get/advanced`). That is the $0.0006 per page of 10 the plan budgets. Live costs 3.3 times more and is used only when asked for by name. Backup: Serper, paged.
- Request: one **city** `location_code` (a Google geo target id of type City), `language_code` en, `device` desktop, `depth` 50, `resolve_poisoned_results` true. The city is one frozen value per project.
- A reading is `k` pulls (default 5) of the same request, posted as separate tasks at least `MIN_PULL_GAP_SECONDS` apart. **OUR READING of "separate sessions"**: separate provider tasks at separate times. If two pulls of a reading return identical ordered URL lists and identical provider timestamps, the reading is flagged `pulls_not_independent`.
- Every pull stores: provider, endpoint, task id, requested and completed UTC times, location, device, language, depth, cost, status code, the provider's own result time and check URL, the path and sha256 of the raw JSON. Raw JSON is written before parsing. A task error is a failed pull. It is never an empty result.
- Position = organic rank counted over organic results only (`rank_group`), not the absolute slot on the page.
- **Altered-page filter, version 2.** Version 1 dropped any pull that disagreed with the others. It failed on real pulls. On the 19 November 2026 pulls stored in this repo, altered pages were correlated inside one session: in at least four searches two altered pulls agreed with each other and outvoted the one clean pull. So version 2 does not trust the majority:
  1. Drop a pull whose status is not OK, a partial pull with too few organic results (30 at depth 50), and a pull where Google answered a corrected spelling.
  2. Group the remaining pulls into clusters. Two pulls are in one cluster when at least half of each one's top slots are held by sites that appear in the other's top 50. Slots are counted, not distinct sites (**OUR READING**), so a run of one unrelated site weighs as many slots as it fills. The rule was calibrated this way on the real pages.
  3. One cluster: keep it all. A page where one site really holds many slots is the truth. One exception (**OUR READING**): when the last clean reading is known, the cluster shares less than half of its top sites with it, and the cluster also shows one of the altered-page signals below, the reading is `degraded`. Without this a page that is altered in every pull would be stored as real.
  4. More than one cluster: pick the cluster to keep by signals that do not depend on its size, in this order: pulls the provider repaired; overlap with the last clean reading of the same search (the provider's own definition of an altered page is that fewer than half of the usual sites appear); no single unrelated site holding four or more of the top ten slots; no sites that also fill slots on unrelated searches of the same round; and only then the larger cluster.
  5. A tie the signals cannot break makes the reading `degraded`. It never picks one silently. A reading still needs 3 kept pulls, so a clean minority that is too small is `degraded`, not wrong.

  Measured false alarms on 53,091 result pages from before the altered-page era: two pulls of one search less than a day apart land in different clusters 1 time in 2,671. Known misses on the 19 November pulls: two searches where the altered pulls are the majority and show none of the signals. Only an earlier clean reading fixes those, so the scoreboard passes the last clean reading in. Every reading stores the reader version that made it. The drop rate is stored per reading.
- **Depth.** A reading built from pulls shallower than 50 results is `degraded` (`shallow_depth`). A site missing from a short page is unknown. It is not NT50.
- **Rounds.** Because altered pages come in spells (a public tracker found most spells end within 3 days and a retry is clean about 3 times in 4), the pulls of one reading are taken hours apart, not seconds: one round of pulls per search, five rounds across a day, then the readings are assembled from the stored pulls. `MIN_PULL_GAP_SECONDS` is only the floor.
- **The middle position.** Per site: its best organic position in each kept pull, or 51 when absent. The clean position is the median of those values (for an even count, the mean of the two middle values rounded half up, as `consensus.median_position` does). A median above 50 is `NT50`. With 5 pulls a site is in the top 50 only when it shows in at least 3.
- Marker 1 (**OUR READING**): 300 searches, frozen list, each read twice 20 to 28 hours apart. Unit = (search, site) where the site is in the top 10 of either reading. Agree = the two clean positions differ by 2 or less, with absent counted as 51. Headline = share of units that agree. Also printed: the same over all top-50 units, per band, the entry and exit rate, the agreement under grading rule v1 (reading A taken as the forecast of reading B), and the same numbers for single raw pulls on the same searches (the real baseline, measured, not quoted). Interval: bootstrap over searches. Each search counts once. Searches whose readings are degraded are not dropped quietly: when fewer than 0.92 of the frozen searches give a usable pair (**OUR READING**, `MARKER1_MIN_MEASURED_SHARE`), the headline is NOT MEASURABLE and the rate on the measured searches is printed beside it. The grading-rule agreement takes reading A as the forecast of reading B, one direction.

## 4. Scoreboard (part 2)

SQLite database (default `data/forecast/scoreboard.db`, env `RANKSIM_SCOREBOARD_URL`). Tables: `sites`, `searches`, `pulls`, `readings`, `reading_positions`, `forecasts`, `grades`, `core_updates`, `freezes`, `spend`. `forecasts` and `grades` are append-only and hash-chained (`prev_hash`, `row_hash` = sha256 of the canonical JSON of the row plus `prev_hash`). No update, no delete.

Refusals (each has a test):

- A forecast whose inputs include a reading fetched after its `as_of`.
- A grade for a forecast created at or after the outcome reading was fetched.
- A second forecast for the same (site, search, stage, model version).
- A grade from a degraded reading.
- Any report when the hash chain does not verify.

- A REAL reading that is not degraded and names a kept pull which is not stored in the same database. Import runs the same check.
- A stage forecast dated after launch day plus its stage day plus `FORECAST_FROZEN_GAP_DAYS` (1). A missed stage day loses that forecast for that stage. A later forecast is a shorter lead, never that stage.

A regrade is a new row with `kind = regrade`, a reason, and the grading rule version. Reports print the original grade beside it. Core updates come from Google's status dashboard and carry the time we recorded them. Seed, pilot and backtest results are reported apart and are never pooled into one headline.

`export` writes the chains and the head hashes as JSONL under `evals/scoreboard/` so they can be committed (git is the outside timestamp). `import` rebuilds the database from that export and verifies it.

The nightly job (`forecast grade`) takes an explicit UTC date, finds forecasts whose outcome is due, grades them, and prints what it could not grade and why.

## 5. Budget guard

`budget.py` keeps `data/forecast/spend.jsonl` (append-only). `approve(amount, by, note)` records an approval. Every paid call asks `reserve(item, estimated_cost)` first. It is refused when approved minus spent is too small, or when the item is a big pull whose $1 sample is not recorded yet. After the call the provider's own `cost` field is recorded. Every paid CLI command is a dry run unless `--execute` is passed, and a dry run prints the exact cost. Prices live in one table with the date they were read.

## 6. The scoring parts

Common rule: every feature value is a `Dated` value (`asof.py`) with its source and snapshot date. `AsOfGuard(as_of).take(value)` raises `LeakError` when the snapshot is later than `as_of`. A source with no dated history (for example today's Majestic file) may be used for live forecasts and is refused in backtests.

- **Part 3, site strength (`strength.py`).** Per site and month: global strength from dated free lists (Common Crawl domain ranks, OpenPageRank history, Chrome popularity buckets, Tranco), and topical strength from where the site ranked on past result pages of the niche up to that month. Shrunk toward the feature-based estimate when a site has few observations. For a search it also gives the strength profile of the current results page: what the top 10, 20 and 50 demand.
- **Part 4, content match (`content.py`).** A pretrained cross-encoder (the pinned ONNX model in `evals/claude/r3/models.json`) scores the search against the page's title, H1, meta description and best body passages. No fitting is needed. A lexical BM25F score is the fallback when the model files are absent, and the output says which one ran.
- **Part 5, trust (`trust.py`).** Features known at `as_of`: stage (age, pages, indexed pages), content (topic focus, depth), footprint (links, brand demand, reviews, from the agency form and dated lists), and the search's demand. Label: how far above or below its content the site really ranks, built with out-of-fold content scores from a frozen content model. Split: by launch cohort in time, then by site, and no search group on both sides. Marker 2 pairs: two held-out sites on the same search (or same niche and month) whose labels differ by more than a frozen threshold; a tie scores 0.5. The link-count baseline is computed on the same pairs. Sites that died or never ranked stay in with their own label. Missing traffic is missing, not zero.
- **Part 6, placement (`placement.py`).** Gradient-boosted trees give a probability for each band (1 to 3, 4 to 10, 11 to 20, 21 to 50, NT50) and a position inside the band. The point is the median of the predicted distribution. The range is a calibrated 80% interval. Label: `locked` when one band holds at least 0.80 of the probability and it is not NT50, `out_of_reach` when NT50 holds at least 0.80, else `contested`. The thresholds are frozen with the model version.
- **Part 7, early readings (`updater.py`).** Takes the earlier forecast as the prior and the readings up to the stage (clean positions, whether the site has entered the top 50, the trend, Search Console counts when present) and returns the updated distribution. Version 1 is a shrunk transition table (band at the stage to band at day 90, by prior band), which works on small samples. A tree model replaces it only when it beats the table on held-out data. Search Console positions are never used as outcomes.
- **Backtest (`backtest.py`).** For past new sites: `as_of` is the launch month, target searches are picked by a frozen rule that does not look at later rankings, the forecast is graded against the month-3 snapshot, and month 1 feeds the updater. Only held-out sites are graded. Each graded cell stores the real days since launch. Backtest labels are single snapshots and are labeled as such.

Rules added after the bug hunts (each has a test):

- **Scored runs.** A run that reads held-out rows (`strength run`, the trust run, `backtest run`) is refused unless its design file is committed and pushed, the source tree under `src/aeo/ranksim/forecast` is clean and matches the freeze, and that version has not been scored. Reports, manifests and model files carry a version number (`prereg-v2.json`, `report-v2.json`) and are never overwritten. A new version needs a written reason. The manifest records the commit, the sha256 of every source file and of every model file it loaded.
- **Held out means site and search.** A graded backtest cell is a held-out site on a held-out search with a forecast date on or after the cutoff. Training rows use training sites only. A report that was not given held-out flags certifies nothing.
- **Brand searches in the backtest** follow the scoreboard rule: a registered brand term, or the site's first label, inside the search text. They are left out of the headline and shown apart. **OUR READING:** a label shorter than 3 characters must match as a whole unit: its characters in order, with only characters that are not letters or digits between them and none directly before or after. So a-1.com is a brand search on "a-1 plumbing" and x.com is not one on "xbox". When in doubt the rule flags a search as brand, which takes an easy hit out of the headline rather than leaving it in. The version 2 backtest was scored with the earlier whole-word rule.
- **Headline intervals resample sites.** The search-resampled interval is printed beside it.
- **Registration records fetched today are undated.** They are left out of every backtest feature, for the same survivorship reason as Open PageRank start dates. A live forecast may use them.
- **Marker 2 pairs** use labels that differ by more than the threshold, not by the threshold exactly.
- **Band prior (OUR READING).** For each difficulty tercile, the one constant point (1 to 50 or NT50) that is a hit most often on the training cells of that tercile under the grading rule. Ties go to the smaller point. Brand searches are left out. It is a constant point, not a distribution.
- **Excluded sites** are matched after normalising the site (`types.is_excluded_site`), so `www` and subdomain spellings are excluded too.
- **The DataForSEO sandbox is SYNTHETIC** (`types.SYNTHETIC_PROVIDERS`). `read_clean` holds the 60 second gap floor for real providers.
- **Cluster bootstrap draws** are made in chunks of 200. The intervals are bit for bit those of one block of draws with the same seed.

Rules of design version 2 (each has a test):

- **A second look is PROVISIONAL.** A design version whose held-out rows were already read by an earlier scored version says so in its design file and in every report. Its marker verdicts are neither passed nor failed. The free panel ends on 30 April 2026, so every later version of the free-panel designs is a second look. Fresh evidence needs fresh rows: the paid cohort or the seed sites.
- **Out-of-fold model inputs (OUR READING).** Search groups and sites are each cut into 3 folds by a hash. A training or validation row takes its trust input from a model fitted without its search fold and without its site fold, and its strength score from a score fitted without its search fold. Held-out rows take the main models. The report counts, apart, the rows whose strength score was fitted on pages that hold the row's own site. That count is not 0: the strength design splits by search and time only.
- **Sites new to a search.** On validation rows where the site is new to the search, the updater version in use stays unless always NT50, the table or the model beats it with a search-cluster lower bound above 0. Among those the largest gain wins. A winning always NT50 rule is a real forecast with all probability on NT50: point, range, label and probabilities all say NT50. The idea came from the version 1 held-out result, and the design file says so.
- **Recent calibration slice.** The range level and the temperature are set on training rows whose outcome date is in the last 90 days before the cutoff, when there are at least 2,000 such rows, else on every calibration row. The rule reads dates only. The idea came from the version 1 held-out result, and the design file says so.
- **Design files name their models.** A version 2 design names the strength and trust models the run loads, and the run refuses a mismatch before it reads an outcome. Every report of a run is written at the end, so a crash writes nothing and does not use the version up.

## 7. Markers (frozen in `markers.py`)

| Marker | Passes when | Also printed, no bar |
| --- | --- | --- |
| 1 | headline agreement of section 3 is 0.92 or more | bands, raw-pull baseline, interval |
| 2 | 0.70 or more of the frozen pairs, and the gain over link count on the same pairs has a lower bound above 0 | counts of right, wrong, tied, missing |
| 3 | upload hit rate 0.75 or more on held-out backtest launches, and above the best baseline with a lower bound above 0 | per band, per difficulty, skill |
| 4 | day-30 hit rate 0.85 or more and page-one precision 0.80 or more | recall, seed readings as interim only |
| 5 | Confirmed hit rate 0.90 or more, precision 0.85 or more, recall 0.80 or more, on real day-90 outcomes | site-cluster interval, backtest apart |

**Stand-in rule (OUR READING).** A report whose cells are not what the marker is about (for example the free panel's sites that already rank, instead of new site launches) is marked `stand_in` with a reason. Its verdict is never passed or failed. What the bars would give is kept under `stand_in_passed`, and the text says in one sentence why it does not count. SYNTHETIC reports follow the same rule. Marker 2's verdict uses the gain over link count, the plan's wording. The gain over the best single strength measure is stricter and is printed beside it.

A marker report always shows how many forecasts were eligible by source and says so when a source has none (pilots cannot reach day 90 by 19 March unless they launch by 19 December; seed sites reach day 90 on 28 February when they launch on 30 November).

## 8. Data layer contracts (`sources/`)

Public and paid data land in the same shapes, so a model trained on the free samples runs unchanged on the paid pulls.

**The panel** (`sources/panel.py`): one row per organic result per dated result page, stored as parquet under `data/forecast/cache/panel/`. Columns:

`dataset, locale, location_code, language, keyword, keyword_id, snapshot_time, snapshot_date, rank, rank_absolute, domain, site, url, title, description, is_new, previous_rank_absolute, main_domain_rank, page_rank, etv, kd, search_volume, intent, items_count, se_results_count`

`rank` is the organic rank (`rank_group`). `site` is the registrable domain. `keyword_id` is the first 16 hex characters of sha256 of `locale|keyword`. A value the source does not give is null. The DataForSEO history samples and the paid Historical SERPs endpoint return the same item fields, so one parser serves both. Rows for excluded sites are dropped at load. Result pages dated on or after 2026-09-13 that were not collected with the provider's repair option are flagged `poison_risk` and are left out of training. Google has served altered pages to scrapers since then, and one public tracker still measured about 30% of pages altered on 3 November 2026, so the window has no end date yet.

**Strength as of a date** (`sources/strength_lists.py`): `lookup(sites, as_of) -> dict[site, Dated]`, where the value is the newest snapshot *published* on or before `as_of`. Sources: Common Crawl domain ranks (per release, with its publication date), OpenPageRank history, Chrome popularity buckets, Tranco. Today's Majestic file is undated. Materialised values are cached as parquet under `data/forecast/cache/strength/`.

**Site start dates** (`sources/domain_age.py`): first-seen month from Common Crawl, the first month in OpenPageRank, and RDAP registration dates for small sets. Each value says which source it came from. A registration date is not a launch date, and a re-registered expired domain looks new: both are flagged, not hidden.

**Google updates** (`sources/updates.py`): the dated list of ranking updates from Google's Search Status Dashboard, for the scoreboard's `core_updates` table.

**Paid pulls** (`trainingset.py`, `forecast pull ...`): traffic curves and past result pages use one window, N calendar months ending in the month of the pull day. Past result pages bought from DataForSEO are written to `data/forecast/cache/panel_labs/`, apart from the free sample panel. A paid pull checks the cost of its unpaid calls against the budget guard before its first call and refuses the whole batch when the approval or the $1 sample gate says no. A resumed job never pays twice.

**Values dated by their record, not by the page.** Search volume counts as known 45 days after its month ends. Intent and `etv` are null on pages older than their record date. `etv` from the paid Historical SERPs endpoint is held back the same way until a real response shows it follows the page month (`dfs_samples.LABS_ETV_IS_PAGE_DATED`). No model feature reads `etv`.

**New-site cohort filter (OUR READING)** (`newsites.SpamRule` v1): parking name servers read within 14 days of launch, a spam-shaped domain name, or template phrases on the launch page. It reads only facts knowable at registration or launch. Rows are never deleted. Each gets its rule and a kept flag, and the command prints how many sites each rule removed and how many it could not judge.

`start_estimates.jsonl` is a reporting file. Each row carries the date its evidence was collected, and the loader refuses an earlier `as_of` unless asked for reporting only.

Downloads go under `data/forecast/public/` (ignored by git). Every loader records the URL, the fetch time and the sha256 of what it read in a `SOURCES.json` beside the cache, and is resumable. Nothing that is personal data about individuals is loaded.

## 9. Live forecasting (`forecasting.py`, `forecast train | upload | update | due`)

**One page, never a later one.** A live forecast for a search uses the latest stored clean reading of that search that is not degraded, taken on or before the forecast date and not after the moment the forecast is made. A search with no such reading is skipped and named. It is never called NT50. Features come from that page through the same functions the backtests use. Strength values go through the dated lists, and a value published after the date raises `LeakError`.

**Model bundles.** A bundle is a folder under `data/forecast/models/<name>/` with the placement model, a placement model fitted on site strength only, the updater, the trust model and `bundle.json`. The manifest records the training source, the label (REAL or SYNTHETIC), whether never-ranked cells were in training, the training cutoff, the features and the sha256 of every file. Loading checks every hash. A bundle is never replaced. A bundle whose training cutoff is after the forecast date raises `LeakError`. A bundle trained on SYNTHETIC rows never forecasts a seed or pilot site.

**Appearance gate (OUR READING).** The free panel only holds sites that appeared on a search, so a placement model trained on it answers "where does a site land, given that it appears". Such a bundle is called conditional. It forecasts a real site only through a gate: forecast = P(in top 50 on day 90) times the landing distribution inside the top 50, with the rest on NT50. P is fitted on graded seed and pilot outcomes known on the forecast date (brand searches out, original grades only) once there are at least 100 cells from at least 10 sites. Until then P is the fixed prior 0.16. The prior is arithmetic on published figures, written out step by step in `forecasting.py`: 6.11% of pages reach the top 10 within a year, 40.82% of those arrive in the first month and the rest are assumed to arrive evenly over months 2 to 12, so 51.58% of them are in by day 90 (0.0315), and places 11 to 50 are assumed at most four times as likely as places 1 to 10 (0.0315 x 5 = 0.158, rounded to 0.16). None of these figures is a Ranksim result. Every forecast line and model version says "prior, not fitted". A bundle trained on cells that include never-ranked searches (the paid cohort) needs no gate.

**Stage forecasts under a conditional bundle.** The updater of a conditional bundle learned from sites that appeared. Until a clean reading shows the site inside the top 50 for that search, P(in top 50) of a stage forecast is capped at the gate value. After that the updater's answer is used as it is.

**Site strength only baseline for live forecasts (OUR READING).** The bundle's placement model fitted on strength features only, applied to the same row on the same date through the same gate, stored with each forecast when it is made.

**One forecast per site, search and stage.** The live commands write one forecast per (site, search, stage) across all model versions, so models cannot be shopped. A cell that already has a forecast at that stage is listed and left alone.

**Dates.** A stage forecast is dated on its stage day. It can be written on the stage day or on the one grace day after it (`FORECAST_FROZEN_GAP_DAYS`). After that the stage is reported MISSED and is never backfilled. `--all-due` never records a stage before its day.

**Search difficulty and volume in live forecasts.** A seed manifest's difficulty and volume feed the model only when the manifest names the same difficulty metric the bundle was trained with.

**Search Console signals.** Only frozen exports fetched on or before the forecast date are read. A stored copy whose hash no longer matches its freeze is skipped and counted.

**List lookups for new sites (`forecast strength sync`).** A site that was searched in a list release and is absent gets the floor value, a REAL and knowable fact. A site that was never searched gets a missing value. The sync command collects the site and every site on the pages its forecast will use, names for each list the release published on or before the date, and with `--fetch` downloads it. Sites are marked searched only when the release was read to its end. A cached row stored before the lookup never hides the lookup. Open PageRank is not synced: its absence is never a floor. **OUR READING:** the Tranco release for a date is the first-of-month list on or before it, and when the newest list is not published yet the fetch tries up to 3 earlier months and never a later one.

**Band prior for seed and pilot reports.** Each bundle stores a band prior fitted on its own training rows only, with the sites and search groups it was fitted on. A seed or pilot report uses the band prior of the bundle that made its forecasts. A fitting site that is graded in the same report is refused. A conditional bundle's band prior is conditional on appearing, and the report says so. Forecasts from more than one bundle in one report get no band prior rather than a mixed one.

**Content match as a model input.** A bundle may carry the feature `content_match`. The live path feeds it only when the live scorer's backend is the one named in the bundle manifest. Otherwise the value is missing and a note says why.

**Stage calendar.** A stage counts as recorded only when every registered search of the site has a forecast at that stage. Otherwise it stays due, or is MISSED, for exactly the open searches, and the commands name them.

## 10. The paid cohort path (`paidset.py`, `forecast paid ...`, `forecast cohort-backtest ...`)

This is the plan's route to marker 3: an upload-day forecast for a brand-new site, trained and tested on past new sites, with the searches a site never ranked for kept in. No paid data exists yet. The path is proven end to end on SYNTHETIC tables only, and a SYNTHETIC run never passes a marker.

**Upload cells (join rule v2, OUR READING).** One cell per cohort site and target search. The forecast date is the estimated launch date. The launch date is an estimate, so the result page, strength and start evidence are held to the earliest plausible launch date (launch minus its uncertainty, `inputs_as_of`). The site's own launch page text counts as known on the day it was captured, never after the estimated launch date. The stored `as_of` is the later of the two, and the lead time runs from it. Frozen limits: the input page is at most 62 days old; a launch uncertainty that is unknown or over 45 days drops the cell; the outcome is the clean page at least 50 deep closest to day 90 inside days 60 to 120, chosen without looking at the site, and its real day is stored; the month 1 page for the updater is the one closest to day 30 inside days 14 to 45. A site absent from an outcome page at least 50 deep is NOT IN TOP 50, a REAL observation. A window with no page that deep drops the cell. Every drop is counted by reason. Nothing unknown becomes NOT IN TOP 50.

**Split and time.** The split is the frozen rule of `trainingset.py`: by launch cohort in time, then by site, with no search group on both sides. A cell table that does not reproduce that rule is refused. A training cell whose outcome page is dated on or after the earliest held-out forecast date is dropped and counted. Training never reads a held-out row.

**Training.** `forecast train --data paid` fits a bundle on the train split only. The bundle is refused unless its training rows include never-ranked cells. Such a bundle needs no appearance gate. A bundle from SYNTHETIC tables is labeled SYNTHETIC and never forecasts a seed or pilot site. Traffic curves and exact positions are not placement inputs or outcomes, so the join does not need them.

**The frozen cohort backtest.** `cohort-backtest freeze` writes the design: the hash of the held-out row keys, the split and target rule hashes, the join rule, the bundle hash, the band prior fitted on training cells, the grading rule and the markers. `cohort-backtest score` scores the held-out split once under the scored-run protocol of section 6. The same held-out rows are scored once whatever the design is called. A later version is a second look and is PROVISIONAL. On REAL tables the report is the plan's marker 3 and is not a stand-in.

## 11. Findings about the plan itself

The audit of the plan is in `docs/plan/PLAN_AUDIT.md`. The code is built so that each finding is either fixed by a refusal or printed in the reports.
