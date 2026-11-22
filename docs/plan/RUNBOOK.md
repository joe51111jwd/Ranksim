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

1. A command that can cost money is a dry run unless you add `--execute`. The dry run prints the provider, the number of calls, the exact cost and what is left of your approved budget. It sends nothing and spends nothing.
2. With `--execute` the command still stops when the budget guard refuses. The message says how to approve more.
3. Money always comes from the price table in `budget.py`. No amount is typed into the commands.
4. Add `--json` to any command to get machine output.
5. Exit code 0 means it worked. Exit code 1 means a check failed, for example a marker not passed or a broken chain. Exit code 2 means the command was used wrong or something is not set up.
6. No command prints a key. Keys are also scrubbed from anything a command writes.
7. Data lives in `data/forecast/` unless you pass `--dir` or set `RANKSIM_FORECAST_DIR`. That folder is not committed. If `RANKSIM_SCOREBOARD_URL` is set, the scoreboard lives there instead. `status` prints where it is.

To see the whole flow work before spending anything, run the demo. It uses a simulated world and costs nothing. Every line it prints starts with SYNTHETIC.

```
python -m aeo.ranksim forecast demo
```

It prints the seed manifest hash, the reading runs, the marker 1 numbers, the grading summary, three refusals (a late forecast, a replaced forecast and an edit), the stage reports and the marker verdicts. Synthetic results can never pass a marker, so every verdict says NOT A MARKER RESULT, with the result of the synthetic code check beside it. That is correct.

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

1. `status` prints how many sites, searches, readings, forecasts and grades the scoreboard holds, whether the hash chains verify, the budget, whether each login is set (yes or no), and how many problems the plan's own schedule has. Free.
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

The dry run prints the calls and the cost. With 300 searches it picks the first 277 single pulls, which cost 0.997200 USD. With `--execute` it sends them and records what came back: how many pulls worked, how many were partial or repaired, and the fewest and most results.

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

Reading B is the same five rounds and the same `assemble` on the next day, so the two readings are about 24 hours apart. Both readings cost 10.800000 USD in all.

`read` still works and takes all five rounds in one process, one minute apart. Use it only when a day of rounds is not possible: its pulls share one spell of altered pages more often.

Then judge marker 1:

```
python -m aeo.ranksim forecast marker1 report --location-code 1026201
```

It pairs reading A and reading B of each search, prints the share of top 10 units that agree within 2 places, the interval over searches, the same number with the poisoned page filter switched off, and the same number for single raw pulls on the same searches. It names the reader version and its settings. Add `--reader-version 1` to rebuild the readings with the older rule (the default is 2). It ends with the verdict. Free. Exit code 0 only when marker 1 passed.

Keep a copy the outside world can date:

```
python -m aeo.ranksim forecast export
python -m aeo.ranksim forecast verify
```

`export` writes the scoreboard to `evals/scoreboard/` with the head hashes. Ask for it to be committed. `verify` checks both hash chains, the spend ledger, and that the committed export is still the start of the database. That last check catches recent rows that were cut off, which the chain alone cannot see. Free.

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

`seeds pick` gives each site 20 searches, as close to a third easy, a third medium and a third hard as 20 allows, with no search on two sites of one niche. Free. `seeds validate` prints errors and warnings. Exit code 1 when there are errors. `seeds schedule` prints each site's reading dates and stage dates, which marker each date falls after, the reading cost, and the plan's own schedule problems. For 200 searches the weekly grid is 13 readings for 46.800000 USD, and with days 30, 60 and 90 added it is 16 readings for 57.600000 USD.

Freeze before launch, then put the sites on the scoreboard:

```
python -m aeo.ranksim forecast seeds freeze --manifest seeds/seed_manifest.json
python -m aeo.ranksim forecast seeds register --manifest seeds/seed_manifest.json
```

`seeds freeze` writes the hash beside the manifest. A frozen manifest is never rewritten. Commit both files. `seeds register` adds the sites with their launch dates and the searches with their difficulty, and freezes the manifest hash in the scoreboard. Free.

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

It checks the file, copies it under `data/forecast/imports/gsc/`, and freezes its sha256 in the scoreboard. The kinds are ui (the export zip or folder), daily, range, api (JSON, with `--dimensions date,query`) and index. Search Console numbers feed the early reading updater. They are never graded outcomes. Free. SmartCrew files are refused.

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

Note on past result pages: the budget guard reserves 1 page per search per month, the plan's own rate, so 5,000 searches over 24 months is 14.400000 USD. That is still on the safe side. DataForSEO's free history samples (117 locales, 12 months) hold 53,091 dated pages for 11,691 searches, about 0.38 pages per search per month, with a median gap of 62 days between two pages of one search. At that rate the same pull would be 5.449423 USD. Every dry run and `budget prices` print both figures. The $1 sample measures the real count before the big pull, and the provider's own cost field is what gets charged.

The big pulls are coming in the next step. There is no command for them yet.

## Step 5. Weeks 2 and 3. Trust model, marker 2

Coming in the next step: the `train` command and the marker 2 report. `forecast marker --number 2` says so and exits with code 2 until then.

## Step 6. Weeks 3 and 4. Scoring parts and the backtest, marker 3

Coming in the next step: the `backtest` and `upload` commands. Once backtest forecasts are on the scoreboard, judge marker 3 with:

```
python -m aeo.ranksim forecast report --source backtest --stage upload --as-of 2026-12-21
python -m aeo.ranksim forecast marker --number 3 --source backtest --as-of 2026-12-21
```

`report` prints the hit rate, the baselines, page one precision and recall, and every rate under 30 forecasts as NOT MEASURABLE. `marker` prints the verdict. Exit code 0 only when the marker passed. Free.

## Step 7. Weeks 5 to 8. Early reading updater, marker 4

Coming in the next step: the `update` command. The seed readings for it are already being taken in step 3. Judge marker 4 on 22 January:

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

`form template` writes the blank form with one plain question per answer. `form validate` checks a filled form and prints the brand terms it implies. Exit code 1 when it is not valid. `form import` copies it under `data/forecast/imports/forms/` and freezes its sha256. A pilot's sites and searches go on the scoreboard with the same manifest steps as the seed sites, frozen first, with `--source pilot`. All free. The upload forecasts are coming in the next step.

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

Seed, pilot and backtest results are always reported apart. Marker 5 passes only on real day 90 outcomes.

## When something goes wrong

1. A paid command says Refused by the budget guard. Either the approved amount is used up or the $1 sample of that kind is not recorded yet. The message names the command to run, for example `python -m aeo.ranksim forecast budget approve --amount <USD> --by <name> --note <why>`.
2. `verify` says the chains do not verify, or the export is not a prefix of the database. Stop. Do not record or grade anything. Tell the engineer what it printed.
3. A command says DataForSEO needs a login. Set `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` and run it again.
4. `import` rebuilds an empty scoreboard from a committed export: `python -m aeo.ranksim forecast import --from evals/scoreboard`. It refuses a scoreboard that already holds rows.
