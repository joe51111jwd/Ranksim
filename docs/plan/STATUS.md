# Forecast system status (23 November 2026)

This is the status of the build plan in `docs/plan/BUILD_PLAN_ROAD_TO_90.md`. It is written for James. Labels: **REAL** is an observed position. **PREDICTED** is model output. **SYNTHETIC** is generated data and is never accuracy.

## The short version

- All seven parts of the plan are built and tested. The command line runs the whole flow.
- No marker of the plan has passed, and none could on the data we have. Every real number below comes from free public data about sites that already rank. The plan's markers are about brand-new sites.
- On that free stand-in data the forecasts are far from the bars. Day 30 style forecasts hit 0.606 in version 1 (0.605 in version 2, PROVISIONAL) against a bar of 0.85. Placement of a site that first appears hits 0.481 in version 1 (0.457 in version 2, PROVISIONAL) against a bar of 0.75.
- On all cells together the forecasts beat every baseline. They do not in every slice. On sites new to a search, always saying "not in top 50" does better. On young sites the lookup table does a little better. In version 2, on easy searches, guessing the middle of the page beats placement.
- Real marker results need three things from you: a DataForSEO login, an approved budget, and the seed sites.

## What is built

| Part of the plan | Where | State |
| --- | --- | --- |
| 1. Clean reader | `reader.py`, `noise.py` | Built. Not yet run on live pulls. Marker 1 needs the DataForSEO login. |
| 2. Scoreboard | `scoreboard.py`, `grading.py`, `markers.py` | Built. Hash chained. Forecasts are frozen before outcomes. |
| 3. Site strength map | `strength.py` | Built and scored on the free panel. |
| 4. Content match scorer | `content.py` | Built. Uses a pinned pretrained model, with a lexical fallback. |
| 5. Trust model | `trust.py` | Built and scored on a stand-in. Below the bar. |
| 6. Placement model | `placement.py` | Built and scored on a stand-in. Below the bar. |
| 7. Early-reading updater | `updater.py` | Built and scored on a stand-in. Below the bar. |
| Live forecasting | `forecasting.py` | Built: `train`, `upload`, `update`, `due`, `strength sync`. |
| Paid cohort path (marker 3) | `paidset.py` | Built. Proven on SYNTHETIC tables only. No paid data exists. |
| Budget guard | `budget.py` | Built. Approved budget is $0. Nothing was spent. |

The forecast package is about 41,700 lines. Its test suite has 903 tests and passes with no keys.

## Results on the free panel (stand-in, not a marker result)

Positions are REAL. Forecasts are PREDICTED. The cells are sites that already appeared on a search in free DataForSEO sample data, read on single country-level pages. They are not new sites at launch.

Version 1 was scored once. Then we found a data bug and fixed it. Search volume and search intent had been used before they were published. The fix changed the search volume feature on 70% of the 541,367 feature rows and removed intent on 74%. Version 2 is the same test after that fix and the other bug fixes. Version 2 reads the same held-out searches and dates a second time, so it is marked PROVISIONAL and is not fresh evidence. Its design files say that no marker verdict and no headline accuracy claim is made on it.

Version 2 changed several things at once, so it cannot show how much the data bug alone moved version 1. Test A barely moved. Placement, the one model that used the leaked volume, fell from 0.481 to 0.457.

### Test A. One reading, then the outcome 28 to 74 days later (stand-in for the day 30 forecast, marker 4)

| | Version 1 | Version 2 (PROVISIONAL) |
| --- | --- | --- |
| Graded cells | 78,212 | 23,962 |
| Hit rate of the updater | 0.606 | 0.605 |
| Persistence (keep the reading) | 0.570 | 0.569 |
| Lookup table | 0.586 | 0.579 |
| Always not in top 50 | 0.508 | 0.499 |
| Page one precision | 0.832 | 0.832 |
| Page one recall | 0.762 | 0.772 |
| Share of outcomes inside the 80% range | 0.716 | 0.719 |
| Marker 4 bar for the hit rate | 0.85 | 0.85 |

Version 2 grades fewer cells for two reasons. It only grades held-out sites on held-out searches, and it leaves brand searches out of the headline (329 cells in test A, 48 in test C). Version 1 graded held-out searches with any site, brand searches included.

The updater beats persistence by 0.036 in both versions (version 2 interval 0.026 to 0.045, resampling sites). It beats the lookup table by 0.021 in version 1 and by 0.026 in version 2 (0.020 to 0.031).

With a longer wait of 75 to 130 days the hit rate is 0.605 in version 1 and 0.589 in version 2.

### Sites new to a search (inside test A)

| | Version 1 | Version 2 (PROVISIONAL) |
| --- | --- | --- |
| Cells | 14,467 | 4,353 |
| Hit rate of the updater | 0.820 | 0.823 |
| Always not in top 50 | 0.867 | 0.869 |

Here the model is worse than the plainest guess, by 0.047 in version 1 and 0.046 in version 2. Version 2 added a rule that may switch to that guess, chosen on validation rows only. It did not switch in this window, because the gain on validation rows was not clear (lower bound just under 0). In the longer window it did switch, and there the two are equal at 0.841.

On young sites (1,345 cells in version 1 and 377 in version 2 in this window) the lookup table is a little ahead of the updater in every window. The gap is clear in only one of the four: version 1 with the longer wait, 0.044 (0.004 to 0.089).

### Test B. Does a second reading help

A second reading adds 0.003 in version 1 and 0.007 in version 2 (0.004 to 0.010). The gain is real but small.

### Test C. Where a site lands when it first appears (stand-in for the upload forecast, marker 3)

| | Version 1 | Version 2 (PROVISIONAL) |
| --- | --- | --- |
| Graded cells | 11,228 | 3,372 |
| Hit rate of the placement model | 0.481 | 0.457 |
| Always not in top 50 | 0.434 | 0.422 |
| Gain over that baseline | +0.047 (0.041 to 0.053) | +0.035 (0.024 to 0.046) |
| Average miss for landings in 1 to 30, model | 6.73 places | 7.17 places |
| Average miss for landings in 1 to 30, guess the middle | 7.10 places | 7.25 places |
| Share of outcomes inside the 80% range | 0.671 | 0.643 |
| Marker 3 bar for the hit rate | 0.75 | 0.75 |

This test only has sites that did appear. It cannot say how often a new site appears at all. The plan quoted 6.5 places against 7.5. That figure came from a simulation. On real pages the model is not clearly better than guessing the middle: in version 2 the two intervals overlap almost fully (6.87 to 7.49 against 7.00 to 7.50).

Placement almost never forecasts page one. In version 1, 683 entrants landed in the top 10 and the model called page one 3 times. In version 2 it was 2 calls for 237 landings. In version 2, on easy searches, guessing the middle of the page hits 0.442 and placement hits 0.427.

### How good can any forecast look

Two single pulls of the same search less than 2 days apart agree on 0.898 of positions under the grading rule (154,796 positions). A forecast graded on one pull cannot do much better than that. The clean reader exists to lift this ceiling, and it has not been run on live pulls yet.

### Site strength and trust

| | Version 1 | Version 2 (PROVISIONAL) |
| --- | --- | --- |
| Strength orders pairs inside the top 10 | 0.542 | 0.542 |
| The Chrome popularity list alone, same pairs | 0.545 | 0.545 |
| Strength orders pairs across position 10 | 0.598 | 0.598 |
| Strength, top 10 against 51 to 100 (AUC of the raw score) | 0.636 | 0.636 |
| The Chrome popularity list alone, same AUC | 0.641 | 0.641 |
| Strength minus what the page demands, same AUC | 0.626 | 0.626 |
| Trust pair accuracy (marker 2 bar is 0.70) | 0.610 | 0.613 |
| Trust gain over plain link count | not measured | +0.043 (0.014 to 0.076) |
| Trust gain over the best single measure | +0.021 (-0.003 to 0.046) | +0.021 (-0.005 to 0.049) |

Strength is unchanged because its design did not change and the data fix did not touch positions or list values. Free strength lists order sites only a little better than chance, and the fitted score is no better than the Chrome popularity list alone.

Trust is a stand-in on sites that already rank, with result titles standing in for page content. Version 2 changed the trust comparison from the best single measure to the plain link count, which is the plan's wording, after the version 1 result was seen. That change makes the gain check pass on the stand-in. Against the best single measure, the comparison version 1 used, the gain is still not clear.

### Ranges are too narrow after the training period

The 80% ranges cover 0.80 of outcomes on training-period rows and 0.72 on held-out rows. Version 2 set the ranges on the most recent 90 days of training. That did not close the gap. The likely cause is a shift between the training period and the test period. The held-out rows are also other sites on other searches, so the cause is not proven.

### One choice that is yours: the point rule

The forecast point is the median of the predicted distribution. A point chosen only to score well under the grading rule would score 0.648 instead of 0.605 on test A. That gain games the rule (finding A6 in `PLAN_AUDIT.md`: a point of 15 hits every real position from 11 to 20). It does not make the forecast more accurate, and the 0.648 was read on held-out rows. The code uses the median. Say so if you want the other rule.

## SYNTHETIC checks (never accuracy)

- The whole flow runs on generated data: `python -m aeo.ranksim forecast demo --dir <temp dir>`.
- At the plan's size (10 seed sites, 20 searches each) the day 60 hit rate varied from 0.69 to 0.875 over 20 generated worlds with the same model. So one seed cohort cannot tell 0.85 from 0.90. The figures are in `evals/forecast/backtest/synthetic_pipeline/report-v1.json` under `seed_spread`.
- The paid cohort path runs end to end on generated tables: join, train, freeze, score, report.

## What was wrong and is fixed

- Eight bug hunts ran. In each one, a finder had to prove a bug with a failing test, a second reviewer tried to knock it down, and then a fixer worked on it. The commit messages count 95 confirmed bugs. The journals give 94 to 98 depending on how duplicates are counted.
- Not all were fixed in full. Two reader findings were kept as deliberate rules and are marked as our reading in the code. One scoring finding is only partly fixed (see the next section). Version 2 could not be given fresh held-out rows, so it is labeled PROVISIONAL instead.
- A final independent check then found more: a brand rule that missed names written with a hyphen, and commands that printed version 2 numbers without the PROVISIONAL label. Those are fixed in the commits after this report was first written.
- The worst ones overall: search volume, intent and traffic values were read before they were known. Training rows got model inputs fitted on their own search. A forecast could be recorded before its stage day. A report could grade sites the model was trained on. A scored run could be repeated or run from uncommitted code.
- A scored run is now refused unless its design is committed and pushed, the code is clean and matches the design, and that version was not scored before.

## What you should know about the evidence

- Version 1 was scored before the scored-run checks existed. The strength and backtest designs were committed together with their reports, not before them. The backtest manifests name a commit whose code is not the code that made the numbers. The trust design was committed and pushed before its report. We cannot repair that history. The version 1 files are kept as they are.
- Version 2 was scored under the checks. All six designs were frozen at commit `61d6fe5` and pushed in `3ec234d` before any version 2 number existed. Reports: `4e6cf24` (strength, trust) and `bc46ad4` (backtests).
- The version 2 backtest was started through its Python function with the reason text from its frozen design, because the command line could not read that text back. The design matched its frozen hash and every check ran. The command is fixed and has a test.
- The free panel ends on 30 April 2026. There are no rows left that no scored run has read. Fresh evidence can only come from the paid pulls or the seed sites.
- Training rows no longer get model inputs fitted on their own search. They still get inputs from models fitted on later pages of other searches. Held-out rows do not: their inputs come from models fitted only on pages before the cutoff.
- The strength score was fitted on pages that include the held-out site for 72% of test A rows and 56% of test C rows (the site's pages on training searches before the cutoff). The version 2 reports count this openly. How much it moves the results was not measured.

## Known limits

- `forecast strength sync` was tested against fake list files only. The real Common Crawl file is about 2.5 GB and has not been downloaded through this command.
- A forecast for a new site from the free-panel model goes through a fixed 0.16 chance of reaching the top 50. That number is arithmetic on published figures, not something we measured. It is replaced by measured outcomes once 100 graded cells from 10 sites exist, or by a model trained on the paid cohort.
- The forecast uses Search Console numbers only from UI and daily exports. API and range exports can be imported but are not read by the forecast yet.
- The paid join costs about 0.1 seconds per cell, so 20,000 cells take about 40 minutes.
- A list published on the same day as a forecast counts as known that day. The comparison is by date, not by hour.
- GitHub Actions runs only on `main` and on pull requests. It has not run on this branch. The full suite passes in a clean clone on Python 3.13 with no keys.

## What I need from you

1. **DataForSEO login.** Needed for marker 1 and for every paid pull. Enter it through the secure credential form when asked. Never paste it in chat.
2. **Budget approval.** The plan estimates about $310, with a ceiling of about $500. The audit puts cash out nearer $350, because top-ups come in $50 steps and the repair option for altered pages adds about $14. The $310 includes about $120 for the seed domains you buy yourself. The guard is at $0 and each kind of pull needs a $1 sample first. Tell me the amount to approve.
3. **Seed sites.** Buy the 10 domains, pick the 3 niches and the city, and build the sites. The plan's schedule has them live by 30 November.
4. **Search Console access** for the seed sites.
5. **Pilot agencies.** A pilot must launch by 19 December to reach day 90 before marker 5.
6. **Grading rule.** Confirm the readings listed in section C of `PLAN_AUDIT.md`. The rule is frozen in code as version 1.
7. **Marker 1 rule.** Sign off the 0.92 measured-share reading.
8. **The point rule.** Median, or the point that maximises hits (see above).
9. **The 20,000 site trust set.** The plan's filter kept about 4% of newly registered sites in our real run (49 of 1,120). So 20,000 kept sites needs about 460,000 registrations, or the set is about 900 sites. Many of the 49 kept sites were spam pages, so the real yield is lower. Decide which.
10. **Past pages.** 24 or 30 months. Sites 24 to 30 months old need 30.
11. **Marker 5 at 80% to 84.9%.** The plan does not say what happens there.
12. **Public Search Console exports.** Four exports that their owners published are used as examples for the importer and for priors. The plan's stay-out list names other companies' private Search Console. These four are public, and you said on 22 November that licence terms do not limit which public data is used for this internal training work. Say so if you want them left out.
13. **Repo visibility.** The repo should be private before real client data lands in it.
14. **Pull request.** None was opened. Say the word and I will open one so CI runs.

## Where things are

- Plan, word for word: `docs/plan/BUILD_PLAN_ROAD_TO_90.md` (the PDF is beside it).
- Exact definitions: `docs/plan/IMPLEMENTATION_SPEC.md`.
- What the plan gets wrong or leaves open: `docs/plan/PLAN_AUDIT.md`.
- Commands, step by step: `docs/plan/RUNBOOK.md`.
- Public data found and used: `docs/plan/PUBLIC_DATA_CATALOG.md`.
- Evidence: `evals/forecast/` (designs, reports and manifests, version 1 and version 2).
