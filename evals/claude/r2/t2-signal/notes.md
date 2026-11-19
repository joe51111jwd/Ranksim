# Round 2, Target 2 research: do real edits move rank, and does the results page show re-indexing?

Measurement only. No predictor was built or tuned. Real Google positions come from Serper. Every number below comes from `scripts/claude/r2_t2_signal.py`. Its outputs are in this folder: `results.json` holds all numbers, `tables.md` holds every generated table, and `change_rows.csv`, `serp_units.csv` and `title_tag_timeline.csv` hold the row-level data. Reproduce from the repo root with the venv active: `python -m scripts.claude.r2_t2_signal`. The run takes about 20 seconds and gives byte-identical output on every run.

## Bottom line

1. On all changed rows the data shows no reliable edit effect. Rows whose page changed materially moved 0.640 of the time against 0.557 for matched unchanged rows: +0.083 [-0.082, +0.267]. They moved no further and crossed the top 10 boundary no more often. Pages with any change (101 rows) moved about as often as unchanged pages: -0.004 [-0.121, +0.114]. Movement shows no dose response to the size of the diff.
2. One cut is positive. On rows starting inside the top 10 at positions 2 to 10, changed rows moved 14 of 16 times against 0.660 expected: +0.215 [+0.033, +0.400]. This is weak evidence. The moves were no larger (+0.13 positions [-0.79, +0.96]), the site-cluster interval spans zero, and the rows come from 10 URLs on 5 sites. The same AGTEK edit sent two searches up and two down.
3. Upper bound on the share of moves on changed pages that the edit can have caused: 0.335 on all rows (0.392 with URL clusters), and 0.415 on interior rows (point estimate 0.246). A content reader that is perfect, and that is used only on the rows where the edit caused the move, can add at most about 0.10 accuracy (0.17 at the upper bound) over the position-only rule on changed-and-moved rows.
4. A fair content-versus-position test needs at least 170 to 2,735 changed-and-moved rows even in the best case. That is 11 to 171 weeks at the current yield of 16 rows a week. In the realistic case the content step cannot know which moves the edit caused, and with f at its point estimate it loses to the position rule outright.
5. Substantive SERP title changes are a clean signal: same-day noise is 1 in 1,865 pull pairs. But they are rare: 15 re-index events through Nov 17, on 10 URLs and 8 sites. Rows did not move more after them, and Google showed a new title tag for only 2 of 11 observed tag changes. The date field almost never changes.
6. The data cannot show a lag. Next-window comparisons are null, apart from an excess that comes only from the client's own pages.

## Data and seal

- SERP rows come from `rsdata.load_rows("2026-11-17")`. The script has one local loader for the title, snippet and Serper `date` fields. Its SQL hard-codes `snapshot_date <= '2026-11-17'`, it opens the DB read-only, and it asserts on the maximum day. The script asserts that this loader returns exactly the same top-10 rows as `rsdata.load_rows`: 4,626 rows.
- Page fetches come only through `t2_dataset.build("2026-11-17")`. Maximum SERP day loaded: 2026-11-17. Maximum page fetch ET day used: 2026-11-17. The latest indexed fetch is 2026-11-18T03:13Z, which is 22:13 ET on Nov 17. The round 1 extraction cache in /tmp holds 1,713 page ids. None of them is outside the sealed index, and no page outside the sealed index was used.
- No snapshot directory, held-out file, network call, Serper call or git state change was used. No existing file was edited.
- Pulled days: the original 30 searches on Nov 6, 9, 11, 13, 16 and 17 (four pulls on Nov 16, three on Nov 17, one on each other day). The 15 Revorion searches on Nov 16 and 17. The 70 added searches and the PR 41 searches on Nov 16 only.
- Intervals are 95% query bootstraps with 2,000 draws and seed 20261119. Plain rates and paired differences use `metrics.bootstrap_rate` and `bootstrap_difference`. Stratified differences need the control means re-estimated in every draw. They use the same random draws over sorted clusters (`strat_effect`). As sensitivities, the script also resamples URLs and sites, and runs a version with the control means held fixed through the metrics helper.

## Q1. Excess movement

Rows are the round 1 change dataset: 763 usable (search, URL, fetch pair) rows. 28 rows have a material change: 11 URLs, 5 sites, 13 URL pairs, 18 of the rows on the client's site. The script reproduces round 1: 17 of the 28 moved (7 up, 10 down), and the position rule scores 12/17 on changed rows and 303/437 on unchanged rows.

Each changed row is compared with the mean of the unchanged rows in its stratum. A stratum is the date pair plus the start band (1, 2 to 3, 4 to 6, 7 to 10, or 11 for absent). "Moved" means the consensus position differs. "Crossed" means the row left or entered the top 10.

| Comparison | Changed rows matched | Outcome | Changed | Matched unchanged | Difference [95% CI] | Share caused, point / upper |
| --- | ---: | --- | ---: | ---: | --- | --- |
| Material vs not, all rows | 25 of 28 | P(moved) | 0.640 | 0.557 | +0.083 [-0.082, +0.267] | 0.129 / 0.335 |
| | | mean abs change | 1.720 | 1.936 | -0.216 [-1.049, +0.593] | |
| | | P(crossed top 10) | 0.160 | 0.281 | -0.121 [-0.258, +0.083] | |
| Same, URL clusters | 25 of 28 | P(moved) | 0.640 | 0.557 | +0.083 [-0.113, +0.348] | 0.129 / 0.392 |
| Material vs no change at all | 25 of 28 | P(moved) | 0.640 | 0.558 | +0.082 [-0.093, +0.281] | 0.129 / 0.348 |
| Material vs not, start 2 to 10 | 16 of 16 | P(moved) | 0.875 | 0.660 | +0.215 [+0.033, +0.400] | 0.246 / 0.415 |
| | | mean abs change | 2.125 | 1.995 | +0.130 [-0.785, +0.960] | |
| | | P(crossed top 10) | 0.125 | 0.228 | -0.103 [-0.325, +0.154] | |
| Start 2 to 10, URL clusters | 16 of 16 | P(moved) | | | +0.215 [+0.032, +0.389] | 0.246 / 0.400 |
| Start 2 to 10, site clusters | 16 of 16 | P(moved) | | | +0.215 [-0.108, +0.384] | 0.246 / 0.408 |
| Without the client site, all rows | 10 of 10 | P(moved) | 0.800 | 0.543 | +0.257 [-0.003, +0.496] | 0.322 / 0.528 |
| | | mean abs change | 2.000 | 1.420 | +0.580 [-0.700, +1.918] | |
| Without the client site, start 2 to 10 | 8 of 8 | P(moved) | 0.875 | 0.622 | +0.253 [-0.010, +0.500] | 0.290 / 0.514 |
| Any change vs none | 97 of 101 | P(moved) | 0.536 | 0.540 | -0.004 [-0.121, +0.114] | 0.000 / 0.186 |
| | | mean abs change | 1.237 | 1.793 | -0.556 [-1.116, +0.091] | |
| | | P(crossed top 10) | 0.144 | 0.241 | -0.096 [-0.182, +0.003] | |

Three changed rows on the full sample had no unchanged row in their stratum and were dropped.

The interior changed rows: 16 rows, 14 moved (5 up, 9 down), 10 URLs, 5 sites, 8 rows on the client site. By URL:

| URL | Up | Down | Static |
| --- | ---: | ---: | ---: |
| agtek.com/solutions/takeoff | 2 | 2 | 0 |
| agtek.com/products/underground | 1 | 0 | 0 |
| bidsyncai.com heavy civil post | 0 | 1 | 0 |
| ownrops.com land clearing guide | 0 | 1 | 0 |
| hcss.com project management product page | 0 | 0 | 1 |
| smartcrewhq.com all-in-one-dirt-work | 0 | 2 | 1 |
| smartcrewhq.com all-in-one-dirt-work-software | 1 | 1 | 0 |
| smartcrewhq.com blog small contractors | 0 | 1 | 0 |
| smartcrewhq.com heavybid alternative | 1 | 0 | 0 |
| smartcrewhq.com dirt work software with crm | 0 | 1 | 0 |

Dose. Any change is broken down by edit tokens (main-text tokens not covered by a 5-gram shared with the other version) and compared with the 663 no-change rows on 92 URLs, of which 402 moved.

| Diff size | Rows | Moved | P(moved), diff [CI] | Mean abs change, diff [CI] |
| --- | ---: | ---: | --- | --- |
| 1 to 49 tokens | 42 | 23 | 0.590 vs 0.510, +0.080 [-0.050, +0.216] | 1.56 vs 1.68, -0.11 [-0.80, +0.73] |
| 50 to 249 tokens | 55 | 27 | 0.481 vs 0.557, -0.075 [-0.215, +0.072] | 0.91 vs 1.86, -0.96 [-1.69, -0.17] |
| 250 tokens or more | 3 | 2 | 0.667 vs 0.670, -0.003 [-0.700, +0.387] | 2.00 vs 1.85, +0.15 [-3.04, +3.34] |
| Title or H1 changed | 26 | | 0.609 vs 0.541, +0.067 [-0.106, +0.276] | 1.61 vs 1.95, -0.34 [-1.18, +0.41] |

Within-stratum slope on log(1 + edit tokens): P(moved) -0.0017 [-0.0290, +0.0269], mean abs change -0.131 [-0.258, +0.018]. Bigger diffs do not go with more movement. Pages with mid-size diffs moved less far. Those diffs are mostly rotating widgets on large, stable sites, so this reads as a site-type effect, not an edit effect.

How to read the share. The share caused is max(0, difference / changed rate). It equals the probability that the edit caused a move only if two things hold: an edit never stops a move that would otherwise have happened, and edited pages are otherwise like their matched controls. Both assumptions favour the edit, so the share is an upper bound on edits causing moves. It does not bound edits that flip the direction of a move that would have happened anyway. The data cannot bound that: the position rule scores 12/17 on changed rows and 303/437 on unchanged rows, which is too few rows to see a drop.

## Q2. The size of a fair test

D is defined on one changed-and-moved row: 1 if the content-aware rule is right and the position-only rule is wrong, -1 for the reverse, and 0 if they agree. The test needs the 95% interval of mean D above zero with 80% power:

n = DEFF x (1.960 + 0.842)^2 x (E[D^2] - mu^2) / mu^2

- Best case, gated: the content step is used only on rows where the edit caused the move. Then mu = s f (q - a_c) and E[D^2] = s f (q (1 - a_c) + (1 - q) a_c).
- Realistic case, ungated: the content step is used wherever it fires, and it fires at rate r whether or not the edit caused the move. Then mu = s r (f (q - a_c) + (1 - f)(0.5 - a0)) and E[D^2] = s r (f (q (1 - a_c) + (1 - q) a_c) + (1 - f) 0.5).
- Rows starting at 1 or 11 give D = 0, because both rules make the forced call there.

Inputs, all from the script:

| Input | Value |
| --- | --- |
| s, share of changed-and-moved rows that start at 2 to 10 | 14/17 = 0.824 |
| f, share of interior changed-and-moved rows whose move the edit caused | 0.246 point, 0.415 upper (query clusters; 0.400 with URL clusters) |
| q, content step right on caused rows | 0.6, 0.7, 0.8 |
| a_c, position rule right on caused rows | 0.5 main case (an edit sets the direction regardless of the position cue); 0.651 pessimistic |
| a0, position rule right on interior unchanged moved rows | 250/384 = 0.651 [0.606, 0.694] |
| r, frozen content step fires on interior changed-and-moved rows | 5/14 = 0.357 |
| DEFF, moved rows per changed URL pair (worst case, all rows of a page agree) | 17/12 = 1.417 |
| Yield, changed-and-moved rows | 0.0234 per URL-day (17 over 728 URL-days), 16.0 a week on the 98 tracked URLs, 16.3 a week per 100 URLs |

Rows needed (weeks at 16.0 a week). "Never" means mu is zero or below, so the content rule cannot beat the position rule.

| a_c | f | Gated, q = 0.6 | Gated, q = 0.7 | Gated, q = 0.8 | Ungated, q = 0.6 | Ungated, q = 0.7 | Ungated, q = 0.8 |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| 0.5 | 0.246 | 2,735 (171) | 676 (42) | 294 (18) | never | never | never |
| 0.5 | 0.415 | 1,616 (101) | 396 (25) | 170 (11) | never | never | 14,534 (907) |
| 0.5 | 0.5 | 1,339 (84) | 326 (20) | 139 (9) | never | 31,534 (1,969) | 3,397 (212) |
| 0.5 | 1.0 | 664 (41) | 158 (10) | 64 (4) | 1,879 (117) | 461 (29) | 199 (12) |
| 0.651 | 0.246 | never | 10,063 (628) | 1,002 (63) | never | never | never |
| 0.651 | 0.415 | never | 5,958 (372) | 589 (37) | never | never | never |

The ungated rule only breaks even when f is at least 0.602, 0.430 or 0.335 for q = 0.6, 0.7 or 0.8 (with a_c = 0.5). The data puts f at 0.246 with an upper bound of 0.415. With a perfect content reader (q = 1), the gated gain over the position rule on changed-and-moved rows is s f (1 - a_c): 0.101 at the point estimate and 0.171 at the upper bound. That caps what any content-diff predictor can add on these rows.

## Q3. SERP-side change signals

"Display" titles are compared as lowercase text. "Substantive" titles also ignore a brand or tag-line segment of at most 3 tokens (" : r/estimators", " - AGTEK", " | Contractor Software"), segment reorders and truncation with an ellipsis. Across days the display title churns a lot, and almost all of that churn is suffixes and truncation.

(a) and (b) Noise floor, compared pull against pull. Across days, the last pull of one day is compared with the first pull of the next pulled day.

| Comparison | Pull pairs | Display title differs | Substantive title differs | Snippet differs | Date shown in one only | Date value differs |
| --- | ---: | --- | --- | --- | --- | --- |
| Same day, all | 1,865 | 50 (0.027) [0.012, 0.044] | 1 (0.001) [0.000, 0.002] | 445 (0.239) | 29/801 (0.036) | 0/763 |
| Same day, under 1.5 h apart | 1,238 | 22 (0.018) | 1 (0.001) | 296 (0.239) | 14/541 (0.026) | 0/521 |
| Same day, over 4 h apart | 627 | 28 (0.045) | 0 | 149 (0.238) | 15/260 (0.058) | 0/242 |
| Consecutive pulled days, all | 1,282 | 100 (0.078) [0.055, 0.102] | 14 (0.011) [0.005, 0.017] | 317 (0.247) | 70/523 (0.134) | 1/447 (0.002) |
| Nov 6 to 9 | 202 | 46 (0.228) | 4 (0.020) | 55 (0.272) | 32/78 (0.410) | 0/46 |
| Nov 9 to 11 | 208 | 6 (0.029) | 1 (0.005) | 49 (0.236) | 3/81 (0.037) | 1/78 |
| Nov 11 to 13 | 194 | 13 (0.067) | 1 (0.005) | 48 (0.247) | 10/75 (0.133) | 0/64 |
| Nov 13 to 16 | 242 | 26 (0.107) | 7 (0.029) | 62 (0.256) | 22/108 (0.204) | 0/85 |
| Nov 16 to 17 | 436 | 9 (0.021) | 1 (0.002) | 103 (0.236) | 3/181 (0.017) | 0/174 |

- The substantive title changes across days about 11 times as often as within a day (0.011 against 0.001), so it carries a real but rare signal.
- The snippet changes as often within a day as across days (0.239 against 0.247). It carries no signal.
- The date field appears on 1,798 of 4,626 observations, and 17 of those values are not dates. Its value changed once in 447 comparisons. Whether a date is shown flips 3.6% of the time within a day. The Nov 6 pull was taken at 22:13 ET, while the others ran in the daytime. That pull shows far fewer brand suffixes and dates, so the Nov 6 to 9 display churn (0.228) and date flips (0.410) are a pull-time artifact.

(c) Title tag changes seen in our own fetches. There were 11 changes on 9 URLs. For each pulled day, the SERP title was checked against the old and the new tag with the substantive rule.

| URL | Tag change window | What the SERP showed | Ranks in the change window |
| --- | --- | --- | --- |
| agtek.com/solutions/takeoff | Nov 13 to 16 | New tag from Nov 16, the first pulled day after the change, on all 12 searches | 3 to 1, 3 to 2, 4 to 6, 5 to 6, 11 to 5, 11 to 11; all unchanged on Nov 17 |
| hcss.com construction project management page | Nov 13 to 16 | New tag from Nov 16, on all 8 searches | 2 to 2 to 2 |
| agtek.com/products/underground | Nov 13 to 16 | Still the old tag on Nov 16 and 17 | 4 to 2 on Nov 16, before the SERP showed the new title |
| smartcrewhq.com heavybid alternative | Nov 9 to 11 | Still the old tag through Nov 17 | 10 to 9 |
| smartcrewhq.com dirt work software with crm | Nov 9 to 11 | Still the old tag through Nov 17 | 2 to 6 |
| smartcrewhq.com all-in-one-dirt-work-software (first change) | Nov 9 to 11 | Old tag still shown on one search on Nov 16; otherwise neither tag | 3 to 1 |
| Five other client changes (all-in-one-dirt-work twice, all-in-one-dirt-work-software second change, best-all-in-one, blog) | Nov 9 to 16 | Neither tag; Google shows its own title | mixed |

Google showed the new tag for 2 of 11 changes, both on non-client pages, on the first pulled day after our fetch saw them (lag 0 pulled days, at most 3 calendar days). For 4 changes it still showed the old tag 1 to 8 days later. For 5 changes it never used either tag. The SERP title therefore reveals re-indexing only for pages whose tag Google actually displays.

(d) Re-index events. A re-index event is a substantive title change or a date value change between consecutive pulled days, with the title stable across every pull of each day. "Title shown before" marks events where the new title had already appeared for that URL on an earlier day (on any search): these are rewrites or alternation, not fresh crawls. "Strict" events exclude them.

| Day pair | Gap days | (search, URL) shown both days | Multi-pull both days | Display title changes | Substantive title events | Date value events | Re-index events | Per calendar day | URLs | Sites | Title shown before | Strict events (URLs, sites) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Nov 6 to 9 | 3 | 202 | 0 | 46 | 4 | 0 | 4 | 1.3 | 4 | 4 | 1 | 3 (3, 3) |
| Nov 9 to 11 | 2 | 208 | 0 | 6 | 1 | 1 | 2 | 1.0 | 2 | 2 | 0 | 2 (2, 2) |
| Nov 11 to 13 | 2 | 194 | 0 | 13 | 1 | 0 | 1 | 0.5 | 1 | 1 | 0 | 1 (1, 1) |
| Nov 13 to 16 | 3 | 242 | 0 | 19 | 7 | 0 | 7 | 2.3 | 4 | 4 | 2 | 5 (2, 2) |
| Nov 16 to 17 | 1 | 436 | 233 | 6 | 1 | 0 | 1 | 1.0 | 1 | 1 | 0 | 1 (1, 1) |
| Total | | 1,282 | 233 | 90 | 14 | 1 | 15 | | 10 | 8 | 3 | 12 (9, 8) |

- Same-day noise would produce about 0.1 false events per day pair.
- The only date event is the HCSS Google Play page ("Oct 21, 2026" to "Nov 3, 2026", rank 7 throughout).
- Only the Nov 16 to 17 pair can confirm stability with several pulls on both days, and its one event (smartcrewhq.com hcss-alternative, "hcss alternative" to "HCSS Alternative: Read This Before You Sign") was shown in a single pull on one of the days.

(e) Movement after an event, stratified by window and start band. The same-window comparison covers d1 to d2, where the event is seen; the next window covers d2 to the next pulled day.

| Window | Signal | Event rows | P(moved), diff [CI] | Mean abs change, diff [CI] |
| --- | --- | ---: | --- | --- |
| Same | Re-index event | 15 | 0.600 vs 0.594, +0.006 [-0.246, +0.295]; URL clusters [-0.369, +0.256] | 0.87 vs 1.34, -0.47 [-0.98, +0.03] |
| Same | Strict event | 12 | 0.667 vs 0.611, +0.056 [-0.218, +0.394] | 1.00 vs 1.42, -0.42 [-0.95, +0.12] |
| Same | Display title change (noisy) | 90 | 0.567 vs 0.543, +0.024 [-0.063, +0.104] | 1.23 vs 1.13, +0.11 [-0.23, +0.42] |
| Next | Re-index event | 14 | 0.357 vs 0.542, -0.185 [-0.399, +0.018]; URL clusters [-0.420, +0.075] | 0.79 vs 1.24, -0.45 [-1.00, +0.14] |
| Next | Strict event | 11 | 0.364 vs 0.550, -0.186 [-0.384, +0.083] | 0.73 vs 1.23, -0.50 [-1.10, +0.36] |
| Next | Display title change (noisy) | 84 | 0.607 vs 0.560, +0.047 [-0.096, +0.167] | 1.85 vs 1.56, +0.29 [-0.24, +0.76] |

Rows did not move more after a re-index event, and if anything moved less in the next window. Direction cannot be tested. Of the title-event rows that moved, only 2 in the same window and 2 in the next window changed the number of search terms in the SERP title. The term rule got 2/2 and 1/2, and the position rule got 1/2 and 2/2. No moved row had a date event.

Changed pages and SERP events. Of the 28 material rows, 6 had a re-index event in the same window (5 moved), 9 were shown on both days with no event (8 moved), and 13 were not shown on both days (4 moved). A visible re-index did not separate movers from non-movers.

## Q4. Lag

| Window after the fetch window | Rows | Material rows | P(moved), diff [CI] | URL-cluster CI | Mean abs change, diff [CI] | URL-cluster CI |
| --- | ---: | ---: | --- | --- | --- | --- |
| Next pulled window | 763 | 28 | 0.429 vs 0.401, +0.027 [-0.166, +0.205] | [-0.109, +0.152] | 2.18 vs 1.07, +1.11 [-0.36, +2.65] | [-0.14, +2.45] |
| Next window, pages that did not change again | | | 0.455 vs 0.410, +0.044 [-0.126, +0.184] | | | |
| Next window, without the client site | | 10 | 0.100 vs 0.248, -0.148 [-0.321, +0.058] | | 0.10 vs 0.54, -0.44 [-0.77, -0.11] | |
| Next window, client site only | | 6 of 18 matched | 0.500 vs 0.200, +0.300 [-0.250, +0.750] | | 3.83 vs 0.94, +2.89 [-1.25, +6.39] | |
| Window after that | 534 | 15 (all client) | 0.400 vs 0.281, +0.119 [-0.094, +0.327] | [-0.225, +0.429] | 2.13 vs 0.78, +1.35 [+0.09, +2.51] | [-0.69, +3.33] |

Counts on the 28 material rows: 17 moved in the window where the change was seen. 12 of 28 moved in the next window, and 6 of 15 in the window after that. Of the 11 rows that did not move in the change window, 3 moved in the next window. The 10 non-client changed rows all come from changes between Nov 13 and Nov 16, so they have only one later day (Nov 17), and on it they were calmer than their controls. The lagged excess in mean abs change comes only from the client's pages, for example 3 to 1 to 11 to 1, and 11 to 8 to 11 to 5. Those pages bounce during the core update and were edited again in batches. On the SERP side, rows moved less, not more, in the window after a re-index event. The two pages where Google showed the new title moved (AGTEK takeoff) or held (HCSS) in the same window as the change, and then held on Nov 17.

The data cannot say whether edits act with a lag. The fetch windows are 2 to 5 days long and the edit time inside them is unknown. Only 15 changed rows, all on the client site, have two later pulled days. Every next-window test spans zero once URL clustering is used.

## What this means for Target 2

- In this data, an edit does not visibly move rank beyond what position and noise already explain. The only positive cut (interior rows move more often) is fragile and shows no direction.
- Even taking the upper bound at face value, edits cause at most about 4 in 10 interior moves on changed pages. A content-diff predictor can add at most about 10 to 17 accuracy points on changed-and-moved rows, and only if it knows which moves the edit caused.
- Proving a real content edge needs hundreds of changed-and-moved rows at best, and is impossible for an ungated rule if f is near 0.25. At 16 rows a week that is months to years on the current URL set.
- The SERP title is a clean marker of re-indexing when Google displays the tag (same-day noise 0.1%), and it timed the two clear cases to the first pulled day. But it covers only 2 of 11 tag changes here, is rare overall (0.5 to 2.3 events a day across all tracked searches), and did not predict movement. The date field and the snippet are useless for this.

## Caveats

- Material rows are few: 28 rows, 13 URL pairs, 5 sites, with 18 rows on the client's site. The client edits in sitewide batches during a Google core update, which confounds edit and trend.
- A fetch on day B exists mostly when the URL was still in the top 10 on B (round 1 selection check). Changed and unchanged rows share this selection, but P(crossed top 10) is biased low for both.
- The interior cut is one of several looked at. Its query-cluster and URL-cluster intervals clear zero barely, and the site-cluster interval does not.
- The share caused assumes that edits never stop moves and that edited pages are otherwise like their matched controls. It does not bound direction flips.
- The substantive title rule (brand segment of at most 3 tokens, reorder, truncation) was set by reading examples on these design days. Without it, display churn would swamp the signal (0.078 across days against 0.027 within a day).
- Nov 6 was a night pull with a different display style. Single-pull days cannot confirm within-day stability; the same-day substantive noise rate (0.001) suggests this costs about 0.1 false events per day pair.
