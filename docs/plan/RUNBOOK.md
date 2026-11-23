# Runbook for the measuring half of the build plan

This is for James. It follows the build steps of `docs/plan/BUILD_PLAN_ROAD_TO_90.md` in order.
Each section lists the commands to run, what they print, what they cost, and what only you can do.
Every amount below was printed by the commands themselves from the price table in `budget.py`, read on 22 November 2026.

## How every command behaves

All commands start with `python -m aeo.ranksim forecast`. Run them from the repo folder with the project's Python.

```
. .venv/bin/activate
python -m aeo.ranksim forecast --help
```

Rules that hold for every command:

1. A command that can cost money is a dry run unless you add `--execute`. The dry run prints the provider, the number of calls, the exact cost and what is left of your approved budget. It sends nothing and spends nothing. The cost is the same amount `--execute` checks and reserves: a search holding a Google operator (`site:`, `intitle:` and the like) costs five times the pages on DataForSEO, and the dry run counts that.
2. With `--execute` the command still stops when the budget guard refuses. The message says how to approve more.
3. Money always comes from the price table in `budget.py`. No amount is typed into the commands.
4. Add `--json` to any command to get machine output.
5. Exit code 0 means it worked. Exit code 1 means a check failed, for example a marker not passed or a broken chain. Exit code 2 means the command was used wrong or something is not set up.
6. No command prints a key. Keys are also scrubbed from anything a command writes.
7. Data lives in `data/forecast/` unless you pass `--dir` or set `RANKSIM_FORECAST_DIR`. `--dir` applies to every part of a command: the scoreboard, the caches, the bundles, the frozen trust model and the content model. That folder is not committed. If `RANKSIM_SCOREBOARD_URL` is set, the scoreboard lives there instead. `status` prints where it is. `demo` writes into `--workdir`, or into the `--dir` folder when `--workdir` is not given, or else into a new temporary folder. It refuses a folder that is not empty, so its SYNTHETIC files never land beside real data.
8. The committed export lives in `evals/scoreboard/` (or where `RANKSIM_EXPORT_DIR` points). Once it exists, `status`, `report`, `marker`, `grade`, `export` and `verify` check that the database still holds every forecast and grade the export names. On a database whose newest rows were cut off they stop or report it, with exit code 1. Until the first export, `status` says there is none yet. `export` writes to `evals/scoreboard/` under the folder you run it from, whatever `--dir` says. When you test in a temporary folder, pass `export --out <folder>` and `verify --against <folder>` with the same folder, so no test scoreboard lands in the repo.

To see the whole flow work before spending anything, run the demo. It uses a simulated world and costs nothing. Every line it prints starts with SYNTHETIC.

```
python -m aeo.ranksim forecast demo
```

It trains a synthetic model bundle on other new sites of the same world (all of their day 90 outcomes come before the first demo launch), imports the agency forms, registers the demo sites, reads them on days 0, 7, 14, 30, 60 and 90, makes the upload forecast on launch day and one update on each stage day through the same live path `upload` and `update` use, grades at day 90, and shows three refusals (a late forecast, a replaced forecast and an edit). Then it prints the stage reports with all four baselines and the marker verdicts. It takes under a minute. Synthetic results can never pass a marker, so every verdict says NOT A MARKER RESULT, with the result of the synthetic code check beside it. That is correct.

## Step 1. Day 1, 23 November. Set up

What only you can do:

1. Confirm the grading rule and the readings marked OUR READING in `docs/plan/IMPLEMENTATION_SPEC.md`.
2. Put $50 into DataForSEO. That is their smallest top up.
3. Give the DataForSEO login to the environment. Set `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` in your shell or in a local `.env` file. Never commit that file. The commands only say whether a login is set, never what it is.
4. Buy the 10 domains. Pick the 3 niches and the one city the reader is pinned to.
5. Set the repo to private.
6. A free BigQuery account is optional now.

Then run:

```
python -m aeo.ranksim forecast status
python -m aeo.ranksim forecast budget prices
python -m aeo.ranksim forecast locations --name Austin
python -m aeo.ranksim forecast budget approve --amount 50 --by James --note "first DataForSEO top up"
python -m aeo.ranksim forecast budget show
```

What they print and cost:

1. `status` prints how many sites, searches, readings, forecasts and grades the scoreboard holds, whether the hash chains verify, whether a committed export exists to check against, the budget, whether each login is set (yes or no), how many problems the plan's own schedule has, the stage forecasts due in the next 3 days, and the stages that were missed. Free.
2. `budget prices` prints every unit price with its source page, and what the plan's items cost. For example one clean reading (5 pulls, 50 deep, Standard queue, repair option on) costs 0.018000 USD. Free.
3. `locations --name <city>` searches DataForSEO's free list of places and prints only cities with their codes. Austin, Texas is 1026201. Free. It needs the login. Without a login, download DataForSEO's public locations CSV and add `--csv <file>`.
4. `budget approve` records that you approved spending up to that amount. It moves no money. Until you run it, every paid call is refused, because the approved amount starts at 0.
5. `budget show` prints approved, spent and remaining, any open reservations, and the $1 samples recorded so far. Free.

## Step 2. Week 1. Clean reader, scoreboard and marker 1

Marker 1 is due on 30 November. It reads 300 searches twice, 20 to 28 hours apart.

First the $1 sample of the reader. It must come before any reading that costs more than $1.

```
python -m aeo.ranksim forecast sample serp --searches searches.txt --location-code 1026201
python -m aeo.ranksim forecast sample serp --searches searches.txt --location-code 1026201 --execute
```

The dry run prints the calls and the cost. With 300 searches it picks the first 277 single pulls, which cost 0.997200 USD. With `--execute` it sends them and records what came back: how many pulls worked, how many were partial or repaired, and the fewest and most results. If the sample learned nothing (for example no pull came back with results), it prints that the sample was NOT recorded and why, exits with code 1, and the gate for big pulls stays closed. The money spent is still shown.

Then freeze the marker 1 sample. Put every candidate search in a text file, one per line.

```
python -m aeo.ranksim forecast marker1 sample --candidates candidates.txt --n 300 --out marker1_searches.txt
```

It draws 300 searches with a fixed seed, freezes the list in the scoreboard and writes it to the file. Free. Running it again with the same list changes nothing. A different list under the same name is refused.

Take the readings in round mode. Altered pages come in spells: pulls taken minutes apart are often altered the same way, and then the altered pulls outvote the clean one. Pulls taken hours apart mostly are not. So one reading is five rounds spread over one UTC day, and the reading is built from the stored rounds at the end of the day.

One round, on day A:

```
python -m aeo.ranksim forecast read-round --searches marker1_searches.txt --location-code 1026201
python -m aeo.ranksim forecast read-round --searches marker1_searches.txt --location-code 1026201 --execute
```

The dry run prints the cost of one round. One round of 300 searches is 300 tasks and costs 1.080000 USD. With `--execute` it takes one pull of every search, stores the pulls, and records the round in the scoreboard. It builds no reading yet.

Schedule five rounds across the day, at least an hour apart, so every round finishes on the same UTC date. The Standard queue answers in up to 45 minutes. For example, with cron on a machine set to UTC:

```
0 1 * * *   cd /path/to/Ranksim && .venv/bin/python -m aeo.ranksim forecast read-round --searches marker1_searches.txt --location-code 1026201 --execute
30 5 * * *  cd /path/to/Ranksim && .venv/bin/python -m aeo.ranksim forecast read-round --searches marker1_searches.txt --location-code 1026201 --execute
0 10 * * *  cd /path/to/Ranksim && .venv/bin/python -m aeo.ranksim forecast read-round --searches marker1_searches.txt --location-code 1026201 --execute
30 14 * * * cd /path/to/Ranksim && .venv/bin/python -m aeo.ranksim forecast read-round --searches marker1_searches.txt --location-code 1026201 --execute
0 19 * * *  cd /path/to/Ranksim && .venv/bin/python -m aeo.ranksim forecast read-round --searches marker1_searches.txt --location-code 1026201 --execute
30 23 * * * cd /path/to/Ranksim && .venv/bin/python -m aeo.ranksim forecast assemble --date $(date -u +\%F)
```

After the last round, assemble the readings of that UTC date:

```
python -m aeo.ranksim forecast assemble --date 2026-11-28
```

`assemble` takes the pulls that completed on that UTC date, builds one clean reading per search, and stores it. Each reading gets the latest earlier clean reading of the same search as history (at most 21 days old), and the suspect sites of every round it came from. It prints how many readings it built, how many are degraded and why, and how many searches had a history. Free. Running it twice changes nothing.

Run `assemble` only after the last round of the day. A search keeps one reading per UTC date: if `assemble` already stored one for that date, a later run leaves it as it is, says how many searches have pulls that were not used, and exits with code 1. Pulls of one search taken less than 60 seconds apart (a cron line pasted twice, a retry loop) count as one pull, so they never make a clean reading on their own.

Reading B is the same five rounds and the same `assemble` on the next day, so the two readings are about 24 hours apart. Both readings cost 10.800000 USD in all.

`read` still works and takes all five rounds in one process, one minute apart. Use it only when a day of rounds is not possible: its pulls share one spell of altered pages more often. `--gap-seconds` cannot go below 60.

Then judge marker 1:

```
python -m aeo.ranksim forecast marker1 report --location-code 1026201
```

It pairs reading A and reading B of each search, prints the share of top 10 units that agree within 2 places and the interval over searches. When every pull of the paired readings is stored, it also prints the same number for single raw pulls on the same searches and the same number with the altered-page filter switched off; when they are not stored, it says the filter-off number is missing. One line names the reader version and its settings. Add `--reader-version 1` to rebuild the readings with the older rule (the default is 2). It ends with the verdict. Free. Exit code 0 only when marker 1 passed.

Every search of the frozen list counts. A search with no usable pair (degraded readings, or no reading B 20 to 28 hours after A) is not dropped: when fewer than 92% of the frozen searches give a pair, the headline is NOT MEASURABLE and marker 1 cannot pass. Marker 1 is judged only on the frozen sample. `--searches <file>` is for looking at a subset: unless the file holds exactly the frozen list, the output starts with NOT A MARKER RESULT and the exit code is 1.

Keep a copy the outside world can date:

```
python -m aeo.ranksim forecast export
python -m aeo.ranksim forecast verify
```

`export` writes the scoreboard to `evals/scoreboard/` with the head hashes. Ask for it to be committed. If the folder already holds an export that the database no longer starts with, `export` refuses to overwrite it, prints what is missing and exits with code 1. `verify` checks both hash chains, the spend ledger, and that the committed export is still the start of the database. That last check catches recent rows that were cut off, which the chain alone cannot see. Once the export exists, every report and grade makes the same check. Free.

## Step 3. Week 1. Launch the 10 seed sites

What only you can do:

1. Build a real small site of 10 to 15 pages on each domain.
2. Connect Search Console for each site on day one.
3. Choose the launch date. The plan's dates only work for a launch on 30 November. The schedule command shows why.

Pick the searches:

```
python -m aeo.ranksim forecast seeds template --out seeds
```

It writes `seeds/seed_candidates.csv` with the columns search, difficulty_value, difficulty_source and volume. Fill it with every candidate search of one niche, with the difficulty from one named metric. The example row is only an example. Then pick, once per niche:

```
python -m aeo.ranksim forecast seeds pick --candidates seeds/plumber.csv --niche plumber --domain one.com --domain two.com --domain three.com --launch-date 2026-11-30 --location-code 1026201 --out seeds/seed_manifest.json
python -m aeo.ranksim forecast seeds validate --manifest seeds/seed_manifest.json
python -m aeo.ranksim forecast seeds schedule --manifest seeds/seed_manifest.json
```

`seeds pick` gives each site 20 searches, as close to a third easy, a third medium and a third hard as 20 allows, with no search on two sites of one niche. Picking more sites of a niche into the same manifest later never reuses a search the niche already has. `--allow-shared` lets sites of a niche share searches and records that in the manifest; without it `seeds validate` treats a shared search as an error. Free. `seeds validate` prints errors and warnings. Exit code 1 when there are errors. `seeds schedule` prints each site's reading dates and stage dates, which marker each date falls after, the reading cost, and the plan's own schedule problems. For 200 searches the weekly grid is 13 readings for 46.800000 USD, and with days 30, 60 and 90 added it is 16 readings for 57.600000 USD.

Freeze before launch, then put the sites on the scoreboard:

```
python -m aeo.ranksim forecast seeds freeze --manifest seeds/seed_manifest.json
python -m aeo.ranksim forecast seeds register --manifest seeds/seed_manifest.json
```

`seeds freeze` writes the hash beside the manifest. A frozen manifest is never rewritten. Commit both files. It refuses a manifest when any site launches on or before the day the manifest was created or the day it is frozen: searches picked after launch could be chosen from early rankings. `seeds register` adds the sites with their launch dates and the searches with their difficulty, and freezes the manifest hash in the scoreboard. It refuses to add a site on or after its launch date. Registering the same manifest again later is fine and adds nothing. Free.

Only for a pilot site that was already live when the agency joined: `seeds freeze --manifest <file> --after-launch-reason "<why>"` writes the reason into the manifest and its hash, and then freeze and register accept it. The freeze refusal prints that command with your manifest file in it.

Stage forecasts. Each site has an upload forecast on launch day and d7, d14, d30 and d60 forecasts on days 7, 14, 30 and 60. The scoreboard refuses a stage forecast dated after its stage day, and a seed or pilot forecast written more than one day after its date. So each stage job must run on or before its stage day. `status` lists the stages due in the next 3 days and the stages that were missed. A missed stage is reported as missed and is never backfilled: a later forecast is a shorter lead, never that stage. The commands that make these forecasts are `upload` and `update`, in steps 6 and 7. Each morning run:

```
python -m aeo.ranksim forecast due
```

It lists every seed and pilot stage due today or in the next 3 days, with the command to run for each, and every stage that was missed. Free. Exit code 1 when a stage was missed.

### Look new sites up in the strength lists

A brand-new site is in no stored strength list, and neither are many of the sites on its result pages. Before each forecast, after that day's clean reading is assembled, look them up:

```
python -m aeo.ranksim forecast strength sync --site one.com --date 2026-11-30
python -m aeo.ranksim forecast strength sync --site one.com --date 2026-11-30 --fetch
python -m aeo.ranksim forecast strength sync --all-due --fetch
```

1. It collects the site and every site on the clean readings the forecast for that date would use (the same pages `upload` and `update` read).
2. For each list (Common Crawl domain ranks, the Chrome UX Report top list, the Tranco list) it names the release published on or before the date, never a later one, and how many sites still need a lookup in it.
3. Without `--fetch` it is a dry run and downloads nothing. It says what a fetch would download: the Common Crawl ranks file is large (about 2.5 GB, streamed once and not stored, tens of minutes); a CrUX month is about 9 MB; a Tranco list is about 23 MB. A file already on disk is read again, not downloaded.
4. With `--fetch` it downloads these free public files, resumes a broken download, and marks a site as looked up only when the whole release was read. A failed or cut download marks nothing and says FAILED; run the same command again to resume. Exit code 1 when a list failed.
5. With no network, or a proxy that refuses the connection, each list fails at its first connection, with no retry. Within seconds it prints a FAILED line under each list, saying the first connection to that host could not be made. Nothing is marked looked up, and the exit code is 1. A download that had started and then broke is retried up to 8 times, waiting up to 60 seconds between tries, and each wait prints one line on the error output, so a long wait is never silent.
6. When the newest list for the date is not published yet (lists come out weeks after their data), it uses the newest one on or before the date. The forecast then says how many days old each list is.
7. Open PageRank is not looked up: its file holds only today's top sites, so it is never a model input.
8. `--all-due` takes every site whose upload forecast is due in the next 3 days and every site whose stage forecast is due today or in its grace day. With `--all-due`, `--date` is today.

What a missing list value means. A site that was looked up in a list release and is not in it gets that list's floor, the value of a site weaker than every listed site. That is a real fact: the site is not listed. A site that was never looked up in a release has a missing value (NaN), never zero and never the floor, because nobody checked. `upload` and `update` print one line naming the exact `strength sync` command when sites on the page were never looked up, and each forecast line still says how many model features are missing.

### What a missed stage day means

A stage forecast must be dated on or before its stage day and written within one day of its date. On the day after the stage day `due` still lists it, and `upload` or `update` dates it on the stage day. Once that day has passed with no forecast, the stage is missed for that site. `due`, `status`, `upload` and `update` say MISSED and nothing is recorded. It is never backfilled, because a forecast made later has seen later readings and is a shorter lead. Report it as missing. The next stage still counts. A stage is MISSED only for the searches that have no forecast at that stage. When every search already has one, `upload` and `update` say "Done" and exit with code 0.

Every week until day 90, and on days 30, 60 and 90, take five rounds across the day and then assemble, as in step 2:

```
python -m aeo.ranksim forecast read-round --manifest seeds/seed_manifest.json
python -m aeo.ranksim forecast read-round --manifest seeds/seed_manifest.json --execute
python -m aeo.ranksim forecast assemble --date <that UTC date> --manifest seeds/seed_manifest.json
```

One round of 200 searches is 200 tasks and costs 0.720000 USD. One reading (five rounds) is 1,000 tasks and costs 3.600000 USD.

Each week, save the Search Console exports for each site:

```
python -m aeo.ranksim forecast gsc import --site one.com --file exports/one-com.zip --kind ui
```

It checks the file, copies it under `data/forecast/imports/gsc/`, and freezes its sha256 in the scoreboard. The kinds are ui (the export zip or folder), daily, range, api (JSON, with `--dimensions date,query`) and index. Search Console numbers feed the early reading updater. They are never graded outcomes. The updater uses only exports fetched on or before its as_of date, and the newest two days of an export, still preliminary, count as unknown, never as zero. Free. SmartCrew files are refused: anything under a smartcrew folder, any file or zip entry whose name mentions SmartCrew, and any export with a query or page naming it, whatever `--site` says. A UI export named after another property than `--site` (Search Console names it `<property>-Performance-on-Search-<date>.zip`) is refused too.

## Step 4. Week 2. Build the training set

Run the three $1 samples first. Each is a dry run until you add `--execute`.

```
python -m aeo.ranksim forecast sample historical-serps --searches niche_searches.txt
python -m aeo.ranksim forecast sample traffic --sites small_new_sites.txt
python -m aeo.ranksim forecast sample ranked-keywords --sites some_sites.txt
```

1. `sample historical-serps` answers how deep and how far back past result pages go. At the plan rate of 1 page per search per month it fits 333 searches for 0.999000 USD. It prints the earliest and latest month returned.
2. `sample traffic` answers whether very small new sites show up in the traffic history. It fits 733 sites in one task for 0.999600 USD. It prints the share of sites with data.
3. `sample ranked-keywords` fits 41 sites at 100 rows for 0.984000 USD.

Note on past result pages: the budget guard reserves 1 page per search per month, the plan's own rate, so 5,000 searches over 24 months is 14.400000 USD. That is still on the safe side. DataForSEO's free history samples (117 locales, 12 months) hold 53,091 dated pages for 11,691 searches, about 0.38 pages per search per month, with a median gap of 62 days between two pages of one search. At that rate the same pull would be 5.449423 USD. `pull plan`, the `pull past-pages` dry run and `budget prices` print both figures. The $1 sample measures the real count before the big pull, and the provider's own cost field is what gets charged.

Then build the free inputs. None of these costs money. Each writes under `data/forecast/cache/`.

```
python -m aeo.ranksim forecast panel build
python -m aeo.ranksim forecast panel summary
python -m aeo.ranksim forecast universe build --niche plumbing --seed-term plumber --seed-term "water heater" --n 300
python -m aeo.ranksim forecast cohort build --name nov25 --repo cenk --start 2025-10-27 --end 2025-11-09 --before <crawl id before> --after <crawl id after>
python -m aeo.ranksim forecast updates show
```

1. `panel build` turns the public DataForSEO sample files under `data/forecast/public/dataforseo_samples/` into the panel in `data/forecast/cache/panel/` (or `--out`). A new or empty folder is filled. A folder that already holds a panel is refused unless you add `--overwrite`. With `--overwrite` the new panel is built beside the old one and swapped in only when it is complete, so a failed build leaves the old panel as it was. `panel summary` prints the searches, pages, rows, dates and the pages at risk of being altered.
2. `universe build` picks the searches of one niche in equal difficulty thirds. It reads the Kaggle keyword shards and the DataForSEO keyword samples under `data/forecast/public/`. Add `--download` to fetch a missing Kaggle shard. It uses the newest month of each search's metrics. They pick searches and are never launch-month features.
3. `cohort build` collects the free evidence for newly registered domains (the public list, RDAP, DNS and Common Crawl) and applies the frozen rules. `--registrations <csv>` with the columns site and listed_on reads your own list instead. It then runs the spam and parking filter on the kept sites and prints how many each rule removed. The filter reads only the domain name, the launch page and a name server reading taken near the launch day. It never reads today's name servers on an older site, traffic or rankings. Every site stays in the table with its reason.
4. `updates show` prints Google's ranking update calendar. `updates sync --fetch` refreshes the cached copy from Google's public status dashboard. Without `--fetch` it fetches nothing. The copy that ships with the code is never changed.
5. `content score --search <search> --file <page>` scores one saved page and says which ran: the pinned local model or the lexical fallback.

Then see what the big pulls cost and what is still missing:

```
python -m aeo.ranksim forecast pull plan
python -m aeo.ranksim forecast pull plan --universe data/forecast/cache/universe/universe_plumbing_US.parquet --cohort data/forecast/cache/newsites/cohort_nov25.parquet
```

Without inputs it prices the plan's own counts: past result pages for 5,000 searches are 14.400000 USD over 24 months and 18.000000 USD over 30 months, traffic curves for 20,000 sites are 26.400000 USD, and exact positions for 2,000 sites at 100 rows are 48.000000 USD. Past result pages at the rate seen in the free history samples are 5.449423 USD over 24 months and 6.811778 USD over 30 months. The three $1 samples are capped at 3.000000 USD, and the totals include them. The total with 24 months is 91.800000 USD at the plan rate and 82.849423 USD at the sample rate; with 30 months it is 95.400000 USD and 84.211778 USD. The budget guard reserves the plan rate. It also prints what is approved and which $1 samples are still missing, with the command for each. Free.

The three big pulls. Each is a dry run until you add `--execute`:

```
python -m aeo.ranksim forecast pull past-pages --universe data/forecast/cache/universe/universe_plumbing_US.parquet --months 24
python -m aeo.ranksim forecast pull traffic --cohort data/forecast/cache/newsites/cohort_nov25.parquet
python -m aeo.ranksim forecast pull positions --cohort data/forecast/cache/newsites/cohort_nov25.parquet --max-sites 2000
```

1. The dry run prints the cost of the whole job, how many calls already have a saved answer, what is still to pay, and what the budget guard would say. It sends nothing. A universe or cohort table written by `paid synthetic` is refused with one sentence saying it is SYNTHETIC, dry runs included: a paid pull never prices made-up site names. A table that does not exist is refused with its name and the command that builds it.
2. With `--execute` the guard checks what is still to pay before the first call. Without an approval, or without the $1 sample of that kind when the pull costs more than $1, it stops with the approve command or the sample command and sends nothing.
3. Each pull is a job with frozen dates. If it stops part way, run the same command again. Saved answers are read back and never paid twice.
4. `--months` counts calendar months with this month included. 24 months asked on 22 November 2026 start on 1 December 2024.
5. `pull traffic` and `pull positions` take the kept sites of the cohort by default. Add `--which all` to take every registered site. `--max-sites` picks the sites in a fixed order that does not look at how they did.
6. Past result pages go into `data/forecast/cache/panel_labs/`, beside the free sample panel. Their estimated traffic column stays empty on older pages until a real response shows it is dated by the page.

## Step 5. Weeks 2 and 3. Trust model, marker 2

Train a model bundle and judge marker 2. All free. None of these scores a held-out row.

```
python -m aeo.ranksim forecast train --data panel --name panel-v1
python -m aeo.ranksim forecast train --data synthetic --name synthetic-v1
python -m aeo.ranksim forecast train --data paid --name paid-v1
python -m aeo.ranksim forecast marker --number 2
```

1. `train` fits the trust, placement and updater models on one source and writes a bundle to `data/forecast/models/<name>/` with a manifest: what it was trained on, the label (REAL or SYNTHETIC), whether its cells include searches a site never ranked for, the training cutoff, the features, the band prior fitted on its own training rows (step 6) and a sha256 over every file. Loading a bundle checks the hash. A bundle is never replaced: a second run with the same `--name` is refused with exit code 2, before any training runs. `paid train` refuses the same way before it joins anything.
2. `--data panel` reads the panel frame under `data/forecast/cache/features/` (build it first with `python -m aeo.ranksim.forecast.features build-panel`), keeps the training side of the frozen split only, and uses the frozen trust model under `<data folder>/models/trust/`. The panel only holds sites that appeared on a search, so this bundle is conditional and every upload forecast made with it passes the appearance gate (step 6). It prints the marker 2 reading of the newest scored trust report with its label, as `marker --number 2` does (item 5).
3. `--data synthetic` trains on the simulated world. It takes several minutes (about 8 minutes of processor time). Its bundle is SYNTHETIC and is refused for any seed or pilot site. It prints a SYNTHETIC marker 2 check that can never pass.
4. `--data paid` trains on past new sites: the cell table in `data/forecast/cache/trainingset/cells/` (written by `paid cells`, step 6) joined with the past result pages in `data/forecast/cache/panel_labs/` (written by `pull past-pages`). Pass `--frame <folder>` to use another training-set folder. Without the pages or the cell table it names what is missing and the command for each, trains nothing and exits with code 1. The traffic curves and exact positions are not needed by this bundle: they are for the trust model. With both inputs it joins the train split only into upload cells, searches where a site never ranked included, and writes a bundle that forecasts with no appearance gate. It prints the kept cells, every dropped cell by reason, the share of never-ranked cells and the content match backend. The cohort backtest part of step 6 has the details.
5. `marker --number 2` reads the newest scored report under `evals/forecast/trust` and judges it. Nothing is rescored. It prints the pair accuracy, the gain over link count alone (the plan's measure, which decides the verdict) and the gain over the best single measure (stricter, printed beside it). The free panel version is a stand-in for the plan's 20,000 new sites, so it prints NOT A MARKER RESULT (STAND-IN) with what the bars would give, and exits with code 1. The newest trust report is now `report-v2.json`, a second look at the held-out rows version 1 already read. The command prints NOT A MARKER RESULT (STAND-IN, PROVISIONAL) and marks every number of it SECOND LOOK: PROVISIONAL (61.3% of pairs). It is no marker verdict and no accuracy claim. The first look, `report.json`, gave 61.0% of pairs against the 70% bar. When no scored report exists it says so and exits with code 1.

## Step 6. Weeks 3 and 4. Scoring parts and the backtest, marker 3

See what the frozen backtests found. Free. It scores nothing:

```
python -m aeo.ranksim forecast backtest
```

It lists each frozen design under `evals/forecast/backtest/`, its design versions and every scored report with its headline number and label. The panel designs are marked STAND-IN: their cells are sites already seen on a search, read on one country-level page, not new launches. Version 2 reports are second looks at the same held-out rows and are listed as SECOND LOOK: PROVISIONAL: their numbers are not fresh held-out accuracy. The first look (version 1) of design C (where a site lands the first time it appears) had a hit rate of 48.1% on 11,228 cells, and its best baseline was always saying not in top 50.

A second look at the same held-out rows is possible, and is reported as a second look:

```
python -m aeo.ranksim forecast backtest run --reason "<why the designs are looked at again>"
```

It first prints that this is another look at the same held-out rows. Version 2 is already scored, so the next look is version 3. The first run freezes it (prereg-v3.json beside the old files, with the reason inside), scores nothing and exits with code 1. Commit and push those files, then run the same command with the same reason: it scores the designs once and writes report-v3.json beside the earlier reports. Any further look at the free panel is PROVISIONAL, because the panel ends on 30 April 2026 and version 1 already read its held-out rows up to that day. `forecast backtest run` picks the next design version itself and has no `--design-version` flag. The module command `python -m aeo.ranksim.forecast.backtest run --design-version <k>` scores one frozen version and reads the reason from that frozen design; when it refuses, it prints one sentence starting "Refused:" and exits with code 2. Both refuse a dirty source tree, a design that is not committed and pushed, and a design already scored. Free, but ask the lead before running either.

The upload forecast. Run it on or before each site's launch day, after a clean reading of its searches and after `strength sync` (step 3):

```
python -m aeo.ranksim forecast strength sync --site one.com --fetch
python -m aeo.ranksim forecast upload --site one.com
python -m aeo.ranksim forecast upload --site one.com --record
python -m aeo.ranksim forecast upload --all-due --record
```

1. Without `--record` it is a dry run. It prints each forecast (point, range, label and model version, all PREDICTED), the appearance gate, the searches it skipped and why, and how many forecasts it would record. It records nothing. With `--record` the forecasts are frozen on the scoreboard. Free.
2. The appearance gate. The REAL bundles trained so far only know sites that appeared on a search. A new site has appeared nowhere, so each forecast is the chance the site is in the top 50 on day 90 times where it would land if it did, with the rest on NOT IN TOP 50. That chance comes from at least 100 graded seed and pilot outcomes from at least 10 sites once they exist. Until then it is a prior that was not fitted, 0.16, worked out from published numbers in `forecasting.py`. With that prior nearly every point is NOT IN TOP 50, and the command says so in one sentence. That is the honest answer until the paid training data exists.
3. The bundle is the newest one under `data/forecast/models/` unless you pass `--bundle <name>`. A SYNTHETIC bundle is refused for a seed or pilot site. A bundle trained on outcomes later than the forecast date is refused.
4. A search with no clean reading on or before the date that is not degraded is skipped and named. It is never called NOT IN TOP 50. Each forecast line also says how many model features are missing. Until `strength sync --fetch` has run for the date, a brand-new site and many sites on its pages were never looked up in the strength lists, so most strength features are missing (NaN, never zero). The command then prints one line with the exact `strength sync` command to run. After the sync, a site that is not in a list gets the list's floor. A line also says, for each list, how many days before the date its release was published. A cell that already has a forecast at that stage is listed and left alone: a forecast is never replaced.
5. Content match. A bundle trained with the content match score as a feature (the paid training set) gets the live score from the site's own page text only when the live scorer ran the same backend the bundle was trained on. Otherwise that input is missing (NaN) and the forecast's notes say why. Other bundles never use the score; it is only recorded. When a saved page of the site is on file, the live scorer loads the same pinned content model, so it too downloads it once from Hugging Face when it is missing, unless `RANKSIM_MODEL_DOWNLOAD=0` is set.
6. What it refuses and what to do: an upload dated after launch (too late, it cannot be made), a seed or pilot forecast more than one day after its date (run it on the day), an unregistered site (run `seeds register`), no bundle (run `train`). `upload` and `update` check the site and the calendar before they ask for a bundle, so an unregistered site, a MISSED stage, a stage forecast asked for before its stage day, or a date later than today is reported even when no bundle exists. A stage forecast is dated on its stage day: `update --site` before that day builds nothing and says which day to run it on. Exit code 1 when any search was skipped, refused or blocked, 2 when the command was used wrong.

Then judge marker 3:

```
python -m aeo.ranksim forecast report --source backtest --stage upload --as-of 2026-12-21
python -m aeo.ranksim forecast marker --number 3 --source backtest --as-of 2026-12-21
```

`report` prints the hit rate, the baselines, page one precision and recall, and every rate under 30 forecasts as NOT MEASURABLE. `marker` prints the verdict. Exit code 0 only when the marker passed. Free. The frozen backtests write their reports to `evals/forecast/backtest/`, not to the scoreboard, so `report --source backtest` finds no graded forecast until backtest forecasts are recorded on the scoreboard, and says so.

The spec requires four baselines on the same forecasts: always NT50, persistence, the band prior by difficulty and site strength only. Where the band prior comes from:

1. Seed and pilot reports use the band prior stored in the model bundle that made the forecasts (found from the forecasts' model version). Every bundle trained from now on fits it at training time on its own training rows only: per difficulty third, the one point that is a hit most often under the grading rule. The bundle stores the table, the cut points of the difficulty thirds, the number of rows, the grading rule and one sentence saying which rows it came from. For a panel bundle those rows are sites that appeared on the search, so the band prior is conditional on appearing, and the sentence says so.
2. The report prints one line saying where the band prior came from.
3. If one of the bundle's training sites is graded in the same report, the report is refused.
4. If the forecasts come from more than one bundle, the report says so and uses none, rather than mix. A bundle trained before band priors were stored still works, and the report says the band prior is missing.
5. Backtest reports, and seed reports whose forecasts were not made by a model bundle, fit it on graded backtest forecasts of the training split, on sites not graded in the report. When there are none, the report says the band prior is missing and why. The site strength only point is stored with every forecast `upload` and `update` make, from the bundle's strength-only model on the same date and through the same gate. Forecasts made another way have none, and the report says how many lack it. Markers 3 to 5 cannot pass while a baseline is missing.

### The cohort backtest on past new sites (the plan's marker 3)

This is the plan's real test of marker 3: upload-day forecasts for past new sites, trained on some of them and graded on the others, searches where a site never ranked included. It needs the paid past result pages. Everything below the pulls is free, and none of it calls a paid service. One download can happen: `paid join`, `paid train` and `cohort-backtest score` score the launch page text with the pinned content model, and when it is not under `<data folder>/models/minilm-l6` they download it once from Hugging Face (about 88 MB, no login). To forbid the download, set `RANKSIM_MODEL_DOWNLOAD=0` (or `HF_HUB_OFFLINE=1`). With the download forbidden and no model there, `paid join` and `paid train` use the lexical scorer and print that backend, and `cohort-backtest score` of a bundle trained on the model is refused. `--content-backend lexical` chooses the lexical scorer outright. `RANKSIM_MODELS_DIR` points at another models folder.

```
python -m aeo.ranksim forecast pull past-pages --universe data/forecast/cache/universe/universe_plumbing_US.parquet --months 30 --execute
python -m aeo.ranksim forecast paid cells --cohort data/forecast/cache/newsites/cohort_nov25.parquet --pages data/forecast/cache/newsites/pages_nov25.jsonl --universe data/forecast/cache/universe/universe_plumbing_US.parquet
python -m aeo.ranksim forecast paid join
python -m aeo.ranksim forecast paid train --name paid-v1
python -m aeo.ranksim forecast cohort-backtest freeze --bundle paid-v1
git add evals/forecast/backtest/cohort-upload/prereg.json && git commit && git push
python -m aeo.ranksim forecast cohort-backtest score --bundle paid-v1
```

1. `pull past-pages` (step 4) is the only paid step. Run its dry run first, with its $1 sample and an approval. The pages go to `data/forecast/cache/panel_labs/`. Use `--months 30` when cohort sites started 24 to 30 months ago (PLAN_AUDIT B9).
2. `paid cells` writes the training-set folder `data/forecast/cache/trainingset/cells/`: the target searches of each kept cohort site, picked by the frozen label-blind rule from its launch-era page text, the frozen split (by launch month, then by site, no search group on both sides), the universe and the launch page text. It reads no ranking. It prints the split counts and how many sites had no launch text or no launch date. Repeat `--universe` for more niches: a search listed in more than one niche is one search, so a site gets at most one cell for it, and the command prints how many searches sit in more than one niche. A cohort or universe from `paid synthetic` (labelled SYNTHETIC, or with the synthetic world's markers) is refused: SYNTHETIC rows never become a REAL folder. It never replaces a folder that holds a cell table: pass a new `--out`.
3. `paid join` joins the train split with its past result pages and prints what it kept and why each other cell was dropped. Nothing is trained or written. The rules are frozen in `paidset.JoinRule` and hashed into every bundle and design. The input page, strength and start evidence are dated on or before the earliest plausible launch date (the launch date minus its uncertainty), stored as `inputs_as_of`. The input page is the latest page dated on or before it, at most 62 days old, not at poison risk and 50 deep. The site's launch page text counts as known on the day it was captured (never after the launch date) and that day is stored as `content_date`. Each row's `as_of` is the newest date of any input it used, so every input is dated on or before it, and `lead_days` runs from it. The outcome is the site's position on the page closest to day 90, between day 60 and day 120, among clean pages at least 50 deep. The page is chosen without looking at the site, so whether a cell is kept never depends on its outcome: when no clean page in the window is 50 deep, every cell of that search and site is dropped and counted. A site missing from the chosen page is NOT IN TOP 50, a REAL observation, and the cell is kept. A launch date more uncertain than 45 days, or with no stated uncertainty, drops the cell. A training outcome dated on or after the earliest test forecast date drops the cell, so the model never learns from a later fact. Brand searches are counted and left out of training. A cell table that repeats a (site, search), puts a site or a search group on both sides of the split, or whose split the frozen split rule does not reproduce (an edited table, or one from an older rule) is refused.
4. `paid train --name <name>` does the join and fits the bundle (`train --data paid` does the same on the default folders). It never reads a test row. It trains nothing when no kept training cell is a search where the site never ranked. It prints what `train` prints plus the join counts. A bundle from `paid synthetic` tables is SYNTHETIC and every line says so.
5. `cohort-backtest freeze --bundle <name>` writes `evals/forecast/backtest/cohort-upload/prereg.json`. It holds the test rows (their count and a hash of their keys), the split and target rule hashes, the panel fingerprint, the join rule, the bundle hash and its features, the band prior fitted on training cells, the grading rule, the four baselines and the hash of the source code. It reads no test outcome and scores nothing. It ends with the full score command, every option spelled out, that runs as printed. When these test rows were already scored, it prints instead the freeze and score commands of the next design version, with `--design-version` and `--reason "<why>"`: put your reason in both.
6. Commit and push the prereg file and keep the source tree under `src/aeo/ranksim/forecast` clean.
7. `cohort-backtest score --bundle <name>` scores the test split once and writes `report.json` and `manifest-v1.json` beside the prereg. They are never overwritten. It prints the hit rate with its site cluster interval, the four baselines (always NOT IN TOP 50, persistence on the input page, the band prior, site strength only), the skill over the best one, page one precision and recall (NOT MEASURABLE under 30 cases), range coverage, the share of never-ranked cells, every dropped test cell by reason, and the marker 3 verdict from `markers.evaluate_marker`. Each graded cell's held-out flag is computed (its site and search group are not on the train side), never assumed. On REAL tables this is the plan's marker 3, not a stand-in. Exit code 0 only when marker 3 passed.

When it refuses, and what to do:

1. "is not committed", "differs from HEAD" or "on no remote branch": commit and push the prereg file, then score again.
2. "the forecast source differs" or "the forecast source code changed after": the code changed after the freeze. Commit it, then freeze a new design version: `cohort-backtest freeze --bundle <name> --design-version 2 --reason "<why>"`, commit and push it, and score with the same `--design-version 2 --reason "<why>"`.
3. "does not match the code's design": the cells, the pages, the bundle or the join rule changed after the freeze. Freeze a new design version with the reason.
4. "was already scored" or "These held-out test rows were already scored": the test rows of a training set are scored once, whatever the design name or root (every manifest under the backtest folders and the training set's own `scored_looks.jsonl` are checked). A second look needs design version 2 or later with its reason, and is reported as a SECOND LOOK: PROVISIONAL, with no marker verdict. `cohort-backtest freeze` says so when it sees an earlier look.
5. "was trained on another cell table" or "labels are never mixed": train the bundle again on the same folder, and never mix REAL and SYNTHETIC files.
6. "learned content_match from ... scores": pass `--content-backend` with the backend the bundle was trained on.

To see the whole path work first, use the SYNTHETIC tables. Nothing they produce is accuracy, every line says SYNTHETIC and the marker result is NOT A MARKER RESULT (SYNTHETIC). A SYNTHETIC design is written only under `--root`, and only a SYNTHETIC run may skip the commit and push checks:

```
python -m aeo.ranksim forecast paid synthetic --niches 3 --searches-per-niche 60 --new-sites 45
python -m aeo.ranksim forecast paid train --set data/forecast/cache/trainingset/synthetic --name syn-paid
python -m aeo.ranksim forecast cohort-backtest freeze --set data/forecast/cache/trainingset/synthetic --bundle syn-paid --root /tmp/designs
python -m aeo.ranksim forecast cohort-backtest score --set data/forecast/cache/trainingset/synthetic --bundle syn-paid --root /tmp/designs --skip-git-check
```

The freeze line prints the score command for its own folders, which you can copy as it is. The test rows of a synthetic folder are scored once too: a second run on the same folder needs the next design version, and the freeze prints those commands.

The synthetic run takes about a minute. The real join builds the live features for every cell, about a tenth of a second each, so 20,000 cells take about 40 minutes.

## Step 7. Weeks 5 to 8. Early reading updater, marker 4

The stage forecasts. Run each on its stage day (day 7, 14, 30 and 60 after launch), after that day's clean reading is assembled and after the strength sync for that day (new sites appear on the pages as they move):

```
python -m aeo.ranksim forecast strength sync --all-due --fetch
python -m aeo.ranksim forecast update --site one.com --stage d7
python -m aeo.ranksim forecast update --site one.com --stage d7 --record
python -m aeo.ranksim forecast update --all-due --stage d30 --record
```

It works like `upload`: a dry run without `--record`, the same gate, the same skips and refusals. It also uses the site's own position in the latest clean reading on or before the date and the one before it, and Search Console signals from the UI and daily exports imported on or before the date (API and range exports are not read yet). An export whose stored copy no longer has the sha256 frozen at import is skipped, and the forecast's notes say so. Free. When the stage day has passed it prints MISSED, records nothing and exits with code 1 (see "What a missed stage day means" in step 3).

The gate also holds after the update. The updater of a conditional bundle learned from sites that appeared, so on a search where no clean reading on or before the date has shown the site inside the top 50, the updated chance of the top 50 is capped at the gate value. Once a reading shows the site inside the top 50, the updater's answer is used as it is. The model version says so (`stage-cap-gate-...`). A bundle with no updater is refused for a stage forecast, because the forecast would ignore the readings.

`--all-due` takes only sites whose stage day is today, or was yesterday (the grace day, dated on the stage day), so every stage forecast it records is dated on its stage day and uses that day's reading. A site whose stage day is still to come is listed as "Not yet" and nothing is built for it, because a forecast recorded early could never be replaced. A stage counts as done for a site only when every registered search has a forecast at it: a run that skipped some searches (no clean reading yet) leaves the stage due for those searches, and a later run fills only them. A site whose stage was missed is listed as MISSED, naming the searches that never got a forecast when others did, and the command exits with code 1. It is safe to run the `--all-due` line every day from a scheduler. With `--all-due`, `--date` is the calendar's today, not the forecast date: on the grace day, `--date <grace day>` still dates each stage forecast on its stage day and records it. With `--site`, `--date` is the forecast date. `due` prints the command to run now only for stages due today or in grace, and "Run it on <date>" for later ones.

Judge marker 4 on 22 January:

```
python -m aeo.ranksim forecast marker --number 4 --source backtest --as-of 2027-01-22
python -m aeo.ranksim forecast marker --number 4 --source seed --as-of 2027-01-22
```

On that date no seed site has its day 90 outcome yet, so the seed verdict is NOT MEASURABLE YET. That is expected.

## Step 8. Week 6 onward. Pilots with 2 or 3 agencies

What only you can do: name the agencies, and launch at least one pilot by 19 December if pilots are to count at marker 5.

```
python -m aeo.ranksim forecast form template --out forms/agency_form.json
python -m aeo.ranksim forecast form validate --file forms/filled.json
python -m aeo.ranksim forecast form import --file forms/filled.json
python -m aeo.ranksim forecast seeds register --manifest pilots/pilot_manifest.json --source pilot
```

`form template` writes the blank form with one plain question per answer. `form validate` checks a filled form and prints the brand terms it implies. Exit code 1 when it is not valid. `form import` copies it under `data/forecast/imports/forms/`, freezes its sha256 and its brand terms, and prints the terms. A pilot's sites and searches go on the scoreboard with the same manifest steps as the seed sites, frozen first, with `--source pilot`. Import the form BEFORE `seeds register`: register takes the brand terms from the form so that searches naming the brand are flagged and kept out of the headline, and it refuses a pilot whose form is not imported. A site on the scoreboard is never changed, so a form imported after registering that would change the brand flags is refused. All free. Then make each pilot's forecasts with `upload` and `update`, as in steps 6 and 7. They read the newest form filled in on or before the forecast date.

## Step 9. Weeks 9 to 17. Grade the real outcomes, marker 5

Every night, with the date you are grading as of:

```
python -m aeo.ranksim forecast grade --date 2027-02-28
```

It grades every forecast whose day 90 reading is in, and prints how many it graded and why the others wait. A degraded reading is never used. Exit code 1 when the scoreboard refused a grade. Free.

On 19 March:

```
python -m aeo.ranksim forecast report --source seed --stage d60 --as-of 2027-03-19
python -m aeo.ranksim forecast marker --number 5 --source seed --as-of 2027-03-19
python -m aeo.ranksim forecast marker --number 5 --source pilot --as-of 2027-03-19
python -m aeo.ranksim forecast export
python -m aeo.ranksim forecast verify
```

Seed, pilot and backtest results are always reported apart. Marker 5 passes only on real day 90 outcomes. The seed and pilot reports print one line saying which bundle the band prior came from (step 6).

## When something goes wrong

1. A paid command says Refused by the budget guard. Either the approved amount is used up or the $1 sample of that kind is not recorded yet. The message names the command to run, for example `python -m aeo.ranksim forecast budget approve --amount <USD> --by <name> --note <why>`.
2. `verify` says the chains do not verify, or the export is not a prefix of the database. Stop. Do not record or grade anything. Tell the engineer what it printed.
3. A command says DataForSEO needs a login. Set `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` and run it again.
4. `train`, `backtest`, `upload`, `update` or `due` says a network request failed. That is one sentence and exit code 1. Nothing more was done. Check the connection and run the same command again. `strength sync --fetch` with no network prints a FAILED line under each list instead, marks nothing looked up and exits with code 1 (step 3).
5. `import` rebuilds an empty scoreboard from a committed export: `python -m aeo.ranksim forecast import --from evals/scoreboard`. It refuses a scoreboard that already holds rows.
