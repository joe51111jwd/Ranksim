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
- **Poisoned-page filter (frozen rule).** A pull is dropped when: its status is not OK; or it is partial with fewer than 30 organic results; or Google answered a corrected spelling; or fewer than half of its top-20 sites appear in the top 50 of any other pull of the same reading (this is DataForSEO's own definition of a poisoned page, applied leave-one-out). A pull the provider repaired (`from_web`) is kept and flagged. The drop rate is stored per reading. A reading with fewer than 3 kept pulls is `degraded` and is not used as an outcome.
- **The middle position.** Per site: its best organic position in each kept pull, or 51 when absent. The clean position is the median of those values (for an even count, the mean of the two middle values rounded half up, as `consensus.median_position` does). A median above 50 is `NT50`. With 5 pulls a site is in the top 50 only when it shows in at least 3.
- Marker 1 (**OUR READING**): 300 searches, frozen list, each read twice 20 to 28 hours apart. Unit = (search, site) where the site is in the top 10 of either reading. Agree = the two clean positions differ by 2 or less, with absent counted as 51. Headline = share of units that agree. Also printed: the same over all top-50 units, per band, the entry and exit rate, the agreement under grading rule v1 (reading A taken as the forecast of reading B), and the same numbers for single raw pulls on the same searches (the real baseline, measured, not quoted). Interval: bootstrap over searches.

## 4. Scoreboard (part 2)

SQLite database (default `data/forecast/scoreboard.db`, env `RANKSIM_SCOREBOARD_URL`). Tables: `sites`, `searches`, `pulls`, `readings`, `reading_positions`, `forecasts`, `grades`, `core_updates`, `freezes`, `spend`. `forecasts` and `grades` are append-only and hash-chained (`prev_hash`, `row_hash` = sha256 of the canonical JSON of the row plus `prev_hash`). No update, no delete.

Refusals (each has a test):

- A forecast whose inputs include a reading fetched after its `as_of`.
- A grade for a forecast created at or after the outcome reading was fetched.
- A second forecast for the same (site, search, stage, model version).
- A grade from a degraded reading.
- Any report when the hash chain does not verify.

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

## 7. Markers (frozen in `markers.py`)

| Marker | Passes when | Also printed, no bar |
| --- | --- | --- |
| 1 | headline agreement of section 3 is 0.92 or more | bands, raw-pull baseline, interval |
| 2 | 0.70 or more of the frozen pairs, and the gain over link count on the same pairs has a lower bound above 0 | counts of right, wrong, tied, missing |
| 3 | upload hit rate 0.75 or more on held-out backtest launches, and above the best baseline with a lower bound above 0 | per band, per difficulty, skill |
| 4 | day-30 hit rate 0.85 or more and page-one precision 0.80 or more | recall, seed readings as interim only |
| 5 | Confirmed hit rate 0.90 or more, precision 0.85 or more, recall 0.80 or more, on real day-90 outcomes | site-cluster interval, backtest apart |

A marker report always shows how many forecasts were eligible by source and says so when a source has none (pilots cannot reach day 90 by 19 March unless they launch by 19 December; seed sites reach day 90 on 28 February when they launch on 30 November).

## 8. Data layer contracts (`sources/`)

Public and paid data land in the same shapes, so a model trained on the free samples runs unchanged on the paid pulls.

**The panel** (`sources/panel.py`): one row per organic result per dated result page, stored as parquet under `data/forecast/cache/panel/`. Columns:

`dataset, locale, location_code, language, keyword, keyword_id, snapshot_time, snapshot_date, rank, rank_absolute, domain, site, url, title, description, is_new, previous_rank_absolute, main_domain_rank, page_rank, etv, kd, search_volume, intent, items_count, se_results_count`

`rank` is the organic rank (`rank_group`). `site` is the registrable domain. `keyword_id` is the first 16 hex characters of sha256 of `locale|keyword`. A value the source does not give is null. The DataForSEO history samples and the paid Historical SERPs endpoint return the same item fields, so one parser serves both. Rows for excluded sites are dropped at load. Result pages dated on or after 2026-09-13 that were not collected with the provider's repair option are flagged `poison_risk` and are left out of training. Google has served altered pages to scrapers since then, and one public tracker still measured about 30% of pages altered on 3 November 2026, so the window has no end date yet.

**Strength as of a date** (`sources/strength_lists.py`): `lookup(sites, as_of) -> dict[site, Dated]`, where the value is the newest snapshot *published* on or before `as_of`. Sources: Common Crawl domain ranks (per release, with its publication date), OpenPageRank history, Chrome popularity buckets, Tranco. Today's Majestic file is undated. Materialised values are cached as parquet under `data/forecast/cache/strength/`.

**Site start dates** (`sources/domain_age.py`): first-seen month from Common Crawl, the first month in OpenPageRank, and RDAP registration dates for small sets. Each value says which source it came from. A registration date is not a launch date, and a re-registered expired domain looks new: both are flagged, not hidden.

**Google updates** (`sources/updates.py`): the dated list of ranking updates from Google's Search Status Dashboard, for the scoreboard's `core_updates` table.

Downloads go under `data/forecast/public/` (ignored by git). Every loader records the URL, the fetch time and the sha256 of what it read in a `SOURCES.json` beside the cache, and is resumable. Nothing that is personal data about individuals is loaded.

## 9. Findings about the plan itself

The audit of the plan is in `docs/plan/PLAN_AUDIT.md`. The code is built so that each finding is either fixed by a refusal or printed in the reports.
