# Same-site pairs: does on-page content order two URLs of one site?

Round 2 research step. Measured, not tuned. Design days only (SERP rows and page fetches dated 2026-11-17 or earlier; the script asserts this). Positions are REAL Google positions from Serper. Every model score is PREDICTED.

Script: `scripts/claude/r2_samesite_pairs.py` (runs end to end in about 25 seconds). Outputs in this folder: `results.json` (every number), `tables.md` (generated tables), `samesite_pairs.csv` (one row per same-site pair with feature differences and fold-safe hits, no HTML), `version_rows.csv` (Q4 rows). Every number below is copied from `tables.md` or `results.json`.

## Short answer

No. The pre-stated small model (logistic regression on feature differences, fit on one fold of searches and scored on the other) orders same-site pairs at chance on held-out searches: 226 of 465 (0.486) [0.428, 0.536]. Leave one site out gives 0.497 [0.437, 0.547]. Fixed text-match rules that fit nothing do better: BM25F alone puts the higher page first in 277 of 464 decided pairs (0.597) [0.548, 0.647], compared with 0.522 across sites. That is a real signal but a modest one. It reverses in the positions 11 to 30 zone, and the combined vote that reaches 0.61 was added after the fitted model failed. That is not "clearly better than chance".

## How it was set up

- Pair universe U1 (main): the Nov 16 deep list (absolute positions 1 to 30, one pull) for the 100 searches that have one, and the Nov 16 consensus top 10 for the other 50 searches. Every two distinct URLs in a list form a pair.
- Pair universe U2 (check): every top-10 consensus day through Nov 17. One record per (search, URL pair), labeled by the order that holds on most days.
- Same site means the same registrable domain (`rsdata.site_key`, subdomains folded). URL identity ignores scheme, www, a trailing slash and tracking parameters.
- Page per URL: the usable stored fetch (at least 50 words of main text) nearest to Nov 16, parsed with the repo extractor. 1,084 of the 1,819 URLs in pairs have one (1,012 of them fetched on Nov 16).
- Folds were fixed from the search text before any accuracy was computed. Near duplicates (same word set, a shared 3-word core, or one word set inside another, after plural and spelling folding) are linked. 155 searches form 71 groups. Fold A has 94 searches and 330 usable same-site pairs. Fold B has 61 searches and 135 pairs. The split is uneven because one group holds 35 of the dirt work and excavation searches and another holds 16 HCSS searches.
- Intervals are 95% query bootstrap, 2000 draws, seed 20261119.

## Q1. Counts

| | U1 Nov 16 lists | U2 top-10 consensus days |
| --- | ---: | ---: |
| Searches | 150 | 155 |
| All pairs | 33,281 | 8,726 |
| Same-site pairs | 944 | 282 |
| Same-site pairs per search: mean, median, max | 6.29, 3, 82 | 1.82, 1, 21 |
| Searches with at least one same-site pair | 109 | 83 |
| Sites with same-site pairs | 56 | 21 |
| Share of same-site pairs from the top 5 sites | 0.81 | 0.76 |
| Same-site pairs with usable HTML for both pages | 465 | 159 |
| ... on searches | 88 | 54 |
| ... on sites | 40 | 14 |
| ... share from the top 5 sites | 0.79 | 0.81 |
| Same-site pairs dropped as duplicate content | 5 | 7 |
| Cross-site pairs | 32,337 | 8,444 |
| Cross-site pairs with usable HTML for both pages | 16,580 | 3,741 |
| ... on searches | 134 | 139 |

- In U1, 834 same-site pairs come from the deep lists and 110 from the top-10-only searches. By position: 241 pairs have both URLs in the top 10, 481 have one, and 222 have both in positions 11 to 30.
- The top 5 sites (all U1 same-site pairs) are hcss.com 368, youtube.com 178, trimble.com 89, reddit.com 73 and smartcrewhq.com 57. With usable HTML they are hcss.com 200, smartcrewhq.com 52, trimble.com 52, youtube.com 38 and agtek.com 26. hcss.com alone is 43% of the usable pairs.
- The 465 usable U1 pairs are only 355 distinct URL pairs on 232 URLs, because near-duplicate searches list the same pages.
- U2 is thin: same-site pairs are rare inside one top 10, and only 28 same-site pairs were seen on two or more consensus days.

## Q2. Single features

Share of same-site pairs where the page with the better feature value ranks higher. Ties and missing values are left out and counted. Usable U1 pairs: 465 same-site, 16,580 cross-site.

| Feature | Rule | Same-site | Same-site ties | Cross-site | Same minus cross |
| --- | --- | --- | ---: | --- | --- |
| Search terms in title | more first | 123/204 (0.603) [0.507, 0.689] | 261 | 4868/9408 (0.517) | +0.086 [-0.004, +0.171] |
| Search terms in H1 | more first | 151/240 (0.629) [0.544, 0.697] | 225 | 5464/10480 (0.521) | +0.108 [+0.028, +0.179] |
| Search terms in URL path | more first | 134/201 (0.667) [0.584, 0.744] | 264 | 5628/11200 (0.502) | +0.164 [+0.080, +0.238] |
| Exact search phrase in title | yes first | 31/39 (0.795) [0.667, 0.941] | 426 | 1824/3322 (0.549) | +0.246 [+0.121, +0.404] |
| BM25 of main text | higher first | 262/464 (0.565) [0.516, 0.616] | 1 | 8568/16575 (0.517) | +0.048 [+0.001, +0.097] |
| BM25F title, H1, headings, body | higher first | 277/464 (0.597) [0.548, 0.647] | 1 | 8657/16577 (0.522) | +0.075 [+0.028, +0.122] |
| Search terms in first 100 words | more first | 123/219 (0.562) [0.492, 0.635] | 246 | 5108/9933 (0.514) | +0.047 [-0.019, +0.119] |
| Word count | more first | 239/465 (0.514) [0.464, 0.573] | 0 | 8161/16578 (0.492) | +0.022 [-0.030, +0.080] |
| URL depth | shallower first | 121/204 (0.593) [0.505, 0.667] | 261 | 5669/10748 (0.527) | +0.066 [-0.026, +0.139] |
| Homepage versus inner page | homepage first | 40/63 (0.635) [0.472, 0.780] | 402 | 1277/2308 (0.553) | +0.082 [-0.076, +0.219] |
| SERP title has every search term | yes first | 47/54 (0.870) [0.779, 0.964] | 411 | 2527/4657 (0.543) | +0.328 [+0.238, +0.433] |
| Newest visible date | newer first | 201/346 (0.581) [0.530, 0.639] | 33 (86 missing) | 4456/8582 (0.519) | +0.062 [+0.008, +0.126] |

Your expectation holds. Every text-match feature orders pages better within a site than across sites. Across sites every feature sits between 0.49 and 0.56, so site authority swamps on-page relevance there. Within a site the text features reach 0.56 to 0.87, and the difference has an interval above zero for H1, URL path, exact phrase, BM25, BM25F, SERP title and date.

The catch is coverage. The strongest features decide few pairs: exact phrase decides 39 of 465 and SERP title 54 of 465. Most same-site pairs tie on title terms (261 of 465). With ties counted as half, "more search terms in title first" is 253.5/465 (0.545) [0.505, 0.585]. BM25F is the only text feature that decides nearly every pair, and it reaches 0.597.

## Q3. The small model

Pre-stated: 12 features, logistic regression on differences, no intercept, L2 with C = 1, fit on fold A and scored on fold B, then the reverse. Nothing was tuned.

| Scorer, U1 unless noted | Accuracy on held-out searches |
| --- | --- |
| Fitted model, same-site | 226/465 (0.486) [0.428, 0.536] |
| ... fold A searches / fold B searches | 0.491 [0.418, 0.554] / 0.474 [0.380, 0.552] |
| ... per-search average (88 searches) | 0.457 [0.386, 0.527] |
| ... leave one site out (other fold, site removed) | 231/465 (0.497) [0.437, 0.547] |
| ... without the top 5 sites | 55/97 (0.567) [0.465, 0.651] |
| ... U2 consensus pairs, same-site | 76/159 (0.478) [0.371, 0.575] |
| Same model on cross-site pairs | 8221/16580 (0.496) [0.479, 0.515] |
| Baseline: shorter URL first, ties as half | 257.5/465 (0.554) [0.509, 0.599] (decided 231/412, 0.561) |
| Baseline: more search terms in title first, ties as half | 253.5/465 (0.545) [0.505, 0.585] (decided 123/204, 0.603) |
| Model minus title baseline | -0.059 [-0.114, -0.008] |
| Model minus shorter-URL baseline | -0.068 [-0.129, -0.012] |

| Site | Pairs | Fitted model | Leave one site out |
| --- | ---: | --- | --- |
| hcss.com | 200 | 0.435 [0.309, 0.521] | 0.440 [0.318, 0.525] |
| smartcrewhq.com | 52 | 0.577 [0.457, 0.684] | 0.615 [0.488, 0.732] |
| trimble.com | 52 | 0.615 [0.400, 0.727] | 0.635 [0.454, 0.735] |
| youtube.com | 38 | 0.263 [0.118, 0.386] | 0.263 [0.118, 0.386] |
| agtek.com | 26 | 0.462 [0.227, 0.762] | 0.500 [0.273, 0.762] |

The fitted model is worse than either one-feature baseline. The weights show why. The two folds learn opposite signs for several features: BM25 of the body -0.83 versus +1.15, title terms -0.28 versus +0.92, H1 terms +0.14 versus -0.55, date +0.87 versus -0.41. The features overlap heavily. There are few independent sites and pages: 158 of the 330 fold A pairs are hcss.com, and the 465 pairs are 355 distinct URL pairs. So the fitted weights are noise that does not carry to the other fold. On hcss.com most single features point the right way (H1 61/92, 0.663; URL depth 57/80, 0.713; BM25F 117/200, 0.585), yet the fitted model gets 0.435 there.

POST-HOC check, added after the fitted model came out at chance and not a pre-stated test: an equal-weight vote of the pre-stated directions, with nothing fitted.

| Post-hoc vote | Accuracy (ties as half) |
| --- | --- |
| 8 text features, same-site | 277/465 (0.596) [0.547, 0.640] |
| all 12 features, same-site | 284.5/465 (0.612) [0.561, 0.661] |
| 8 text features, cross-site | 8567.5/16580 (0.517) [0.499, 0.535] |
| 8 text features, same-site, per-search average | 0.589 [0.518, 0.660] |
| 8 text features, same-site, without youtube.com | 265/427 (0.621) [0.577, 0.662] |
| 8 text features, same-site, without the top 5 sites | 65.5/97 (0.675) [0.579, 0.765] |
| 8 text features, U2 same-site | 96.5/159 (0.607) [0.533, 0.682] |
| 8 text features, both URLs in the top 10 | 88.5/143 (0.619) [0.529, 0.708] |
| 8 text features, one URL in the top 10 | 159.5/243 (0.656) [0.571, 0.725] |
| 8 text features, both URLs in positions 11 to 30 | 29/79 (0.367) [0.260, 0.481] |

So the fixed prior directions carry a within-site signal of about 60%, and that signal is absent across sites. The data are too thin to learn weights on top of it. The signal reverses for pairs where both URLs sit in positions 11 to 30 (79 pairs, mostly hcss.com 37 and youtube.com 12). Those positions come from one pull of paged results, and 162 of the 2,678 deep-list rows repeat a URL already listed higher, so their order is less reliable.

## Q4. Does the score respond to real edits?

The 13 material changes (fetch pairs) from round 1 give 28 (search, URL) rows on 11 URLs, and 17 of them moved (7 up, 10 down). Each row is scored by the model fit on the other fold. URL features and the SERP title stay at their day-A values, and both versions' dates are aged to the same day, so only page content can move the score. As a control, the same pipeline scores the 735 usable round 1 rows (249 fetch pairs on 97 URLs) whose page did not change materially.

| | Material edits | No material change (control) |
| --- | ---: | ---: |
| Rows | 28 | 735 |
| Score exactly unchanged | 0 of 28 | 656 of 735 (89%) |
| Moved rows | 17 | 437 |
| Moved rows with a nonzero score change | 17 | 42 |
| Fitted model sign matches the move | 9 of 17 | 24 of 42 |
| Fitted text-only model sign matches the move | 10 of 17 | 18 of 34 |
| Post-hoc text vote sign matches the move | 7 of 16 | 18 of 34 |

- The scorer does respond to edits. Its score changed on all 28 edited rows, against 63% unchanged for the product ranker's swaps in round 1. It stayed exactly unchanged on 89% of the no-edit rows, so it is not just reacting to fetch noise.
- The features that moved were BM25F (28 rows), BM25 of the body (27), word count (27), date (26), H1 terms (8), first 100 words (6), exact phrase (5) and title terms (1). A title or H1 feature changed on 13 of the 28 rows.
- Direction is a coin flip: 9 of 17 for the fitted model and 7 of 16 for the post-hoc vote. With 17 rows on a few pages this describes the data and tests nothing. It also uses a scorer that does not beat chance on held-out pairs.

## Q5. Honest limits

How much of the same-site signal is "homepage or shallow page first" and how much is text match:

| Slice of the 465 same-site pairs | Result |
| --- | --- |
| Pairs with exactly one homepage | 63 (14%). Homepage ranks higher in 40 (0.635) [0.472, 0.780] |
| Pairs where URL depth differs | 204 (44%). Shallower ranks higher in 121 (0.593) [0.505, 0.667] |
| Structure-only fitted model (depth, homepage), all pairs, ties as half | 251.5/465 (0.541) [0.501, 0.576] |
| Pairs where structure says nothing (same depth, no homepage) | 261 (56%) |
| ... BM25F on those | 166/261 (0.636) [0.578, 0.689] |
| ... post-hoc text vote on those | 160.5/261 (0.615) [0.551, 0.673] |
| Pairs where structure decides | 204: BM25F 111/203 (0.547) [0.472, 0.624], structure 121/204 (0.593) |
| Of those, structure and BM25F disagree | 120: structure right 65, BM25F right 55 |

Structure gives about 4 points over chance on all pairs. Text match gives about 10, and most of it comes from the pairs where structure is silent. So the within-site signal is mostly not "homepage or shallow page first". Where the two disagree they are close to even.

What could order same-site pages for reasons a content edit cannot change:

- Internal links. A sparse proxy counts how many other stored pages of the same site link to the URL. The better-linked page ranks higher in 174 of 277 decided pairs (0.628) [0.556, 0.696], and in 79 of 100 (0.790) [0.696, 0.868] on the consensus pairs. That is as strong as the best text feature, and only 359 of 1,084 pages have any such link in our sample. Real internal link structure is probably a large part of within-site order.
- The SERP title. "SERP title has every search term" is the strongest feature (0.870), but it is Google's own output. Google rewrites titles toward the search for pages it already treats as relevant. It is partly a result of ranking, and it is unknown for an unpublished edit.
- Page purpose. For "hcss heavyjob pricing" the hcss.com pricing page ranks first, and every other hcss.com page in that list has a higher BM25F (`samesite_pairs.csv`). The type of page that fits the intent (pricing, comparison, video, forum thread) is a page-level factor, but a text edit does not change it.
- Page age and history. Older published date first decides 133 of 280 (0.475) [0.432, 0.519], so we see no age effect. Google's own history for a URL (links earned, clicks, time indexed) is not visible here.
- Canonical and duplicates. Only 24 same-site pairs have a page whose canonical points elsewhere, and 5 duplicate-content pairs were dropped. Too few to measure.
- Label quality. Most U1 labels come from one pull, and positions 11 to 30 come from paged results that repeat URLs. Only 28 same-site pairs have two or more consensus days.
- Concentration. hcss.com is 43% of usable pairs. Without the top 5 sites only 97 pairs remain.

## What changed after the first run

These items were fixed or added after the first full run. None of them changed a feature definition, the folds or the pre-stated model.

- Model ties count as half a hit, not as wrong. This only changed the two-feature structure model (0.260 became 0.541), which ties on 261 pairs. The 12-feature model had no ties.
- In Q4 both versions' dates are now aged to the same reference day. The first run aged each version to its own fetch day, which moved the date feature without any edit. Results were the same: 0 of 28 unchanged, 9 of 17 signs matched.
- Added, labeled post-hoc: the equal-weight vote, per-search averages, breakdowns by position zone, the Q4 no-edit control, and the structure versus BM25F head to head.

## What this means

A learned "which version is better" scorer cannot be trained on these pairs yet. There are too few independent sites, and one site dominates. Fixed text-match directions (BM25F, or the 8-feature vote) do carry a within-site signal of about 60% that does not exist across sites. If round 2 wants to pursue this, the honest next test is to freeze the text vote and BM25F now. Then score them on same-site pairs from held-out days and new searches, top 10 only, with labels from at least two consensus pulls. The data needed are more deep lists on more days and more sites with several ranking pages, not more features.
