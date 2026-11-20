# Round 3 theory and held-out test: adversarial review

Reviewer: independent subagent, 2026-11-19, about 22:00 to 22:30 ET. Read-only review. No SERP was pulled, no credit spent, no page fetched, no file changed except this one. Nothing from `evals/claude/heldout*`, `evals/claude/serp/`, `/workspace/ranksim-data/snapshots`, or raw-store SERP rows dated 2026-11-18 or later was opened. Inputs read: GOAL.md, `evals/claude/theory.md` (R3.0 to R3.9), `evals/claude/research/r3-matched-pairs.md`, every `scripts/claude/r3_*.py` (committed and the lead's uncommitted working tree as of about 22:10, including the two-pull `r3_score.py`, the two-way interval in `r3_model.py`, `r3_plans.py heldout1` and the draft `evals/preregistered/claude-r3-samesite/test.json`), `evals/claude/r3/plans/*`, `evals/claude/r3/validity/*`, `evals/claude/r3/design/*`, and the round 3 chunk files `validity-a`, `train-a`, `noise-a` (training side only). My own recomputations ran on `evals/claude/r3/design/table.csv.gz` with the repo's own `r3_model` functions; the scripts are in `/tmp/r3review/` (not committed).

## 1. Verdict

**SAFE TO FREEZE AFTER THE REQUIRED CHANGES.**

I found no feature in the primary scorer that reads the label, Google-written text or rank history, and the site and search-group split holds. What has to change before the freeze is the definition of the truth labels, the clustering used for the intervals, what the freeze covers and how the pull and fetch steps are run, the strength of the ranker baseline, and several statements in the theory that are unreproducible, selectively quoted, or leave out a shift between the training and test data. The theory's pass probability of about 0.75 is too high. My estimate is about 0.55 as currently planned and about 0.4 under the primary definition I require (section 5).

## 2. Required changes (most important first)

**R1. Fix the primary truth definition so that it does not throw away informative pairs.** Files: `scripts/claude/r3_score.py` (`confirm_pairs`), `evals/preregistered/claude-r3-samesite/test.json`, theory R3.4 item 2.
- As now written, a pair counts as "unconfirmed" and leaves the primary set whenever either page is missing from the second pull. On the 20 twice-pulled training lists (594 first-pull pairs) that drops 174 pairs (29%), on top of 55 flipped pairs (9%). The second pull still orders a pair when exactly one page is present: the present page is higher. Throwing that away is a selection that helps the scorer. With site-CV blend scores, the scorer gets 0.567 on pairs whose lower page dropped out of pull B, and those are the pairs the planned rule removes.
- What I measured (site-CV blend scores, then the leave-one-search-group-out scores from C below):
  - first pull, all pairs: 0.652 and 0.660 (594 pairs)
  - planned rule, consensus with both pages present: 0.682 and 0.671 (365 pairs, 61%)
  - "absent ranks below" consensus: 0.659 and 0.666 (455 pairs, 77%)
  - half credit on all pairs (a disagreement counts 0.5): 0.636 and 0.643
- The planned rule adds up to 3 points over the single pull. The rule below adds well under 1 point.
- **The single primary definition I accept:** the pair universe is every pair of readable own-site pages in pull A, fixed before pull B is read. A pair is a consensus pair when pull B orders it the same way, where a page missing from pull B's top 10 ranks below every page that is present. A pair is a disagreement when pull B orders it the other way under the same rule. A pair is excluded only when both pages are missing from pull B. Primary accuracy is computed on consensus pairs.
- Why this one: it keeps Google's denoised verdict, it is the same for every scorer, it uses all the information both pulls give, and its expected value is within about 1 point of the single-pull reading, so it is no goalpost move.
- **Reported next to it, with equal prominence:** pull-A single-pull accuracy on all pairs; half-credit accuracy on all pairs; the both-present consensus number (the current plan); counts and scorer accuracy for each status (consensus, disagreement, both missing, and which page was missing); pull-to-pull agreement. All of this goes in the same table for the primary scorer, BM25F, the ranker baselines, the unfitted cross-encoder and the vote.

**R2. Cluster the search bootstrap on search groups, not query strings, and name the interval used for the pass rule.** Files: `scripts/claude/r3_model.py` (`accuracy`, `difference`, `twoway_interval`), `scripts/claude/r3_score.py`, `test.json`.
- The held-out plan has 46 searches, and several are near-duplicates that the frozen linker already groups. Examples: "revenue leakage", "revenue leakage detection", "revenue leakage software"; "royalty audit", "music royalty audit", "music royalty audit services". Resampling query strings treats them as independent and makes the interval too narrow. Map each query to its `universe.json` group and resample groups.
- The new `twoway_interval` (product of site and search multiplicities) is the right tool. Cluster it on site and search group.
- The pass rule must name one interval. I recommend the lower bound of the two-way (site x search group) interval, at least 40 sites and 800 consensus pairs. Today `r3_score.py` uses min(site, query) while `fmt` prints the two-way interval, which gives two candidate numbers after the fact. Pick one before the freeze.

**R3. The freeze manifest must cover the whole scoring path.** File: `evals/preregistered/claude-r3-samesite/manifest.json` (and `test.json`).
- `require_frozen_dir` only protects the files listed in the manifest. If the manifest holds only `test.json`, then `r3_score.py`, the feature code or the pair rule could change after pull A is visible.
- List with sha256: `scripts/claude/r3_score.py`, `r3_dataset.py`, `r3_features.py`, `r3_model.py`, `r3_pages.py`, `r3_data.py`, `r3_semantic.py`, `r3_ranker_baseline.py`, `r3_design.py` (strata and swap pairs), `r3_plans.py`, `scripts/claude/r2_samesite_pairs.py`, `scripts/claude/t2_changes.py` (extractor), `scripts/claude/metrics.py`, `src/aeo/ranksim/pair_order.py`, the ranker model and offpage files it loads, `evals/claude/r3/models.json` (semantic model pins), `evals/claude/r3/plans/universe.json`, `heldout1.jsonl`, `heldout1.meta.json`, and every file in `evals/claude/r3/model/page-blend`, `page-site-blend` and `text-logit`.

**R4. Pin the page versions and freeze the fetch protocol.** Files: `scripts/claude/r3_dataset.py` (`build_table`), `scripts/claude/r3_ranker_baseline.py` (`scores`), `scripts/claude/r3_score.py`, `test.json`.
- `build_table` and the ranker baseline call `r3_pages.load_pages` with no `max_fetched_at`, so the newest good fetch wins. Nothing stops a selective re-fetch, or a browser fetch of the pages that failed, after pull A has been read. That would change which pairs exist after the labels were seen.
- Write the rule into `test.json`: one `r3_fetch.py --label heldout1-a` plain-HTTP pass, started after pull A and finished before scoring. No browser fetch and no second pass. Pass the cutoff time into `build_table` and the ranker baseline as `max_fetched_at`.
- Write the `page_id` of both pages of each pair into `pairs.csv`.
- Report coverage: share of listed pages that were readable, by position in the list.

**R5. Guarantee there is no re-pull and no out-of-order pull.** Files: `scripts/claude/r3_score.py` (`check_pulls`), `test.json`. The code must check all of the following and refuse otherwise:
- (a) No request from `heldout1.jsonl` appears under any label in `evals/claude/r3/serp/` other than the two named labels, or in `serp_raw` with a source like `claude-r3:%` other than those two. The chunk files are searchable now, and today none of `validity-a`, `train-a`, `noise-a` contains a held-out site or test-group search.
- (b) Every chunk file of both labels equals its first committed version (`git log --diff-filter=A`). Remember that a box restore rolls back the disk but not pushes or credits.
- (c) The freeze commit is an ancestor of every chunk commit (`git merge-base --is-ancestor`). This is stronger than comparing the committer time with the client clock, which is what the code does now.
- (d) For every list, the gap between the two pulls is at least 30 minutes and at most a stated limit. I suggest 6 hours: the code has a minimum and no maximum, so pull B could be postponed.
- (e) A list that returns no results is never pulled again. A request that fails four times may be retried once, within a stated window, and the retry is logged.

**R6. Make the current-ranker baseline the strongest honest version.** Files: `scripts/claude/r3_ranker_baseline.py`, `test.json`.
- Blanking the Google-written SERP title is correct, because using it would be leakage. But the ranker was trained to lean on `serp_title_coverage` (prior weight 8.0 in `pair_order.py`), so with it zeroed the ranker is handicapped.
- Add a variant that puts the page's own HTML `<title>` into the `SnapRow.title` slot. That is a history-free, Google-free reconstruction of what the ranker expects there.
- Freeze both variants. The pass condition "beats the current ranker's content part" must hold against the stronger of the two.
- Commit the script behind "0.565 against BM25F 0.576 on the first 44 lists". At the moment it is not reproducible from the repo.
- Also: a pair where any scorer has no score must count as a tie for that scorer, and those pairs must be counted. Today `fillna(0.0)` gives a missing page a score of zero, which can win or lose against negative or positive scores.

**R7. Correct the theory text before it is frozen with the test.** File: `evals/claude/theory.md`.
- (a) R3.3 study 1, selective quote. "first in the site-restricted list in 42 of 53 cases" leaves out 17 of the 70 lists, where the site's best ordinary page was not in the site-restricted top 10 at all. The honest figure is 42 of 70 first, 17 of 70 absent.
  - Also say what the 0.838 rests on: 74 pairs, of which 51 are one page in the ordinary top 10 against one in 11 to 30 (large-gap pairs), and only 13 have both pages in the ordinary top 10 (4 lists).
  - The 0.880 yardstick is 44 of 50 pairs on 5 sites.
  - Pairs where only one page made the site-restricted list and both sit at ordinary positions 11 to 30 agree at 0.40 (10 of 25).
  - Most held-out pairs involve pages that never appear in an ordinary top 30, so their order is "site-restricted order", unvalidated against ordinary order. The theory must use that term and not "Google's order".
- (b) R3.3, the strict cross-validation. `tables.md` commits 0.671 [0.648, 0.693] for the blend when both site and search group are held out. The theory instead cites "0.704 becomes 0.696 on 3,318 pairs", which no committed script produces.
  - The committed strict number is misleading in the other direction: its training side shrinks to 9 to 47 lists (305 to 1,546 pairs, against about 4,500) because every group of the held-out fold is dropped.
  - My leave-one-search-group-out run within site folds gives 0.704 [0.684, 0.724] by site (section 4). That supports "about one point". Commit a script for whichever number the theory cites, and say why the 0.671 column is not used.
- (c) R3.5 leaves out a shift in topic area between training and test. 37 of the 104 held-out lists are revenue recovery, revenue leakage, unbilled revenue or music royalty searches, a topic area with zero training lists. Every training search is construction software, and one search group (35 searches) holds 34% of training pairs. The prediction must name this risk, and the report must give accuracy for each topic area.
- (d) R3.5, "plus about 0.03 on consensus pairs". That only holds under the both-present rule and with site-CV scores. With leave-one-group-out scores it is +0.011, and under R1's rule +0.006. Restate the prediction (section 5).
- (e) R3.5 yield. "About 89 lists, 60 sites" contradicts itself: 89 of 104 lists cannot sit on only 60 of 93 sites when most sites have one list. Training rates are 82% of in-cell requests and 81% of in-cell sites with a scorable pair, and 80% of listed pages readable. Give a range. Mine: about 1,500 to 2,000 consensus pairs under R1 on about 55 to 75 sites, lower if the .gov and JavaScript-heavy held-out sites fetch badly.
- (f) R3.6, the swap-pair falsifier. heldout1 gives 82 of its 93 sites a single list, so swap pairs can only come from the 11 sites with two lists. Expect a few dozen rows at most. Either state that count and call the swap test descriptive, or drop it as a falsifier.
- (g) Commit or remove the numbers with no committed source: the 17 semantic readings at 0.636 to 0.695; the 0.704 to 0.696 strict number; the ranker 0.565 against 0.576 on 44 lists; the twice-pulled 0.649, 0.682 and 0.545. I reproduce 365 consensus and 55 flipped exactly, but get 0.652 on 594 pairs and 20 lists where the theory says 0.649 on 552 and 18. GOAL.md requires every number to come from a committed script.
- (h) Exposure of the test cell during design. The round 2 same-site study (`r2_samesite_pairs.py`, the source of 0.597) read the ordinary Nov 16 top-30 order of every search, including test1 sites with test groups, and its findings shaped the round 3 features and the vote directions. The universe was also drawn from design-day lists of the test cell. This is not fitting leakage, but the `r3_score.py` docstring line "never used for any design choice" is false as written. Reword it, and add a slice (R9).

**R8. Fix the framing in R3.7 and fix the sentences the final report may not use** (section 2a below).

**R9. Pre-state these slices and baselines in `test.json`, reported on the same consensus pairs (not part of the pass rule).** Files: `scripts/claude/r3_score.py`, `test.json`.
- Baselines: the unfitted cross-encoder on title, H1 and meta (`ce_title` alone) and the unfitted vote. In my leave-one-group-out run the blend beats `ce_title` alone by only +0.010 [-0.005, +0.024]. The report must show how much the fitted model adds over an off-the-shelf model.
- Slices:
  - per topic area (construction, revenue and royalty)
  - bare brand searches ("b2w", "agtek", "workyard", "hcss", "revorion", "quoteiq"; 13 lists)
  - held-out searches whose brand also appears in a training search (b2w, agtek, quoteiq, assignar, procore, hcss)
  - excluding the sister domains of training sites (`hcssapps.com` against train `hcss.com`, `revorion.ai` against train-fold `revorion.fr`)
  - excluding pairs where both pages were in a design-day ordinary list for that search (R7h)
  - the existing same-template, without-first-page and adjacent slices
  - macro average by site

### 2a. Sentences to forbid in the final report

- "The simulator picks the better of two edits about 7 times in 10" or anything else that puts this accuracy on edits. This is a cross-sectional ordering of different pages. Edit transfer is untested until PR 45 and later checks.
- "Google ranks X higher" for site-restricted order deeper than about the top two, without the word "site-restricted".
- "On a search it has never seen" without "searches of the same kind (brand plus alternative, pricing, reviews) were in training".
- "From the page alone" without "including its URL, scored partly by an off-the-shelf cross-encoder that does most of the work".
- Any comparison of the held-out consensus number with the round 2 0.597 as if they were measured the same way. 0.597 is a single-pull reading of ordinary top-30 pairs. Only the paired difference on the same held-out pairs counts.
- "Same-site matched pairs" for the all-pairs number without saying that 22% of pairs include the list's first page (0.879 in training), and without the without-first-page (0.661) and same-template (0.700) figures next to it.

A sentence I would accept for a pass: "On consensus pairs of site-restricted lists for sites and search wordings the scorer never saw, it ordered two pages of one site the way both Google pulls did X% of the time [interval], against BM25F Y% and the current ranker's content part Z% on the same pairs. This measures how well page text and URL match the search across different pages. It does not measure the effect of an edit."

## 3. Recommended, optional

1. Report the pigeonhole two-way interval alongside the one-way intervals even if R2 picks it as the pass interval. With 82 of 93 sites carrying one list, two-way is close to min(site, group), so the gap shows how much the clustering choice matters.
2. Key the parsed-page cache (`/workspace/ranksim-data/cache/r3_pages/parsed-v1.pkl`, keyed by page id only) on a hash of the extractor code, so that a changed extractor cannot silently reuse stale parses.
3. Have `r3_score.py` also refuse if the result directory exists on `origin/claude/goal-ranksim-works`, not only on local disk (box restores roll back local disk).
4. Record in `heldout1.meta.json` the exact command and confirm that `heldout1` is the only redraw of the test cell (seed tags `heldout1-first` and `heldout1-second`). Nothing suggests several tags were tried, but the meta file should say so.
5. `fetchable_sites()` in `r3_plans.py` reads fetch success up to 2026-11-20T02:00Z, after the design date, and decides which sites are in the universe. It reads no rank, so it is not leakage. But re-running `universe` later could give a different universe, so record in the meta file that `universe.json` as committed is the frozen split.
6. Report pair-weighted and macro-by-site accuracy together. A 10-result list gives 45 pairs and a 3-result list gives 3.
7. If credits allow later, a third pull of the same plan would make the consensus label as clean as the Targets 1 to 3 consensus positions (theory R3.9 item 4).

## 4. Claims checked

| Claim (theory section) | Checked how | Reproduced? |
| --- | --- | --- |
| Universe 2,004 combos, 579 sites, 155 searches, 71 groups (R3.3) | `universe.json` | Yes (sites 237/197/145, groups 19 train / 52 test) |
| Held-out cell 177 combos on 93 sites | `universe.json` | Yes |
| No training list shares a site or a search group with a held-out list | Python over `universe.json`, `validity.jsonl`, chunk files, `table.csv.gz` | Yes. Validity sites and groups all train; heldout1 all in test1 x test; no existing pull holds a held-out site or test-group search |
| Site's best ordinary page first in 42 of 53, top two in 49 | `validity/tables.md` | Numbers yes; framing no (17 more lists have it absent, R7a) |
| Same order in 62 of 74 (0.838) [0.738, 0.906] by site | `validity/tables.md` | Yes; 51 of 74 are one-top-10 against 11 to 30 |
| Yardstick 0.880 same-site, 0.870 all pairs | `validity/tables.md` | Yes; same-site is 50 pairs on 5 sites |
| Label noise 486 of 554 (0.877) [0.799, 0.944]; adjacent 0.839; 5+ apart 0.932; p about 0.93 | `validity/tables.md`, arithmetic | Yes (p = 0.934) |
| 212 lists in cell, 182 with pairs, 5,623 pairs, 87 sites, 80 searches, 1,160 pages, largest site 2.4% | `design/results.json` | Yes |
| Single features (ce_title 0.694 ... home_linked 0.527) | `design/tables.md` | Yes |
| Scorer table, site-held-out CV (BM25F 0.614 ... blend 0.709, +site 0.714) | `tables.md`, and I re-ran the blend and logit text CV | Yes (0.7093, 0.6926) |
| Holding out search groups costs about one point, 0.704 to 0.696 on 3,318 pairs | No committed source; committed strict column says 0.671 | Not reproducible as stated; my leave-one-group-out gives 0.704 (cost about 0.005), so the conclusion holds (R7b) |
| 17 other semantic readings at 0.636 to 0.695 | No committed output found | Not checkable (R7g) |
| Slices (first page 0.879, without 0.661, adjacent 0.580, gap 5+ 0.832, top five 0.732, same template 0.700 against 0.640) | `tables.md` | Yes |
| Swap pairs 286 rows on 37 sites; BM25F 0.563, vote 0.645, blend 0.612 | `results.json` | Yes |
| Ranker content part 0.565 against BM25F 0.576 on the first 44 lists | No committed output | Not checkable (R6) |
| Twice-pulled lists: 0.649 all, 0.682 on 365 consensus, 0.545 on 55 reversed | Recomputed from `noise-a` chunks and site-CV scores | 365, 55, 0.682, 0.545 yes; all pairs 0.652 on 594 pairs and 20 lists, not 0.649 on 552 and 18 |
| About two thirds survive as consensus | Same | 365 of 594 = 0.61 under the planned rule; 455 of 594 = 0.77 under R1 |
| Expected yield 89 lists, 60 sites, 2,750 pairs | Training rates | Internally inconsistent (R7e) |
| Interval half-width about 0.03 to 0.035 | 300 simulated 60-site test sets drawn from my leave-one-group-out hits | Slightly optimistic: 0.036 for min(site, query) on about 1,870 single-pull pairs, about 0.04 on consensus pairs |
| PREDICTED 0.72 on consensus pairs, pass probability about 0.75 | Section 5 | Too high: no allowance for the topic-area shift; the +0.03 consensus gain depends on the planned rule |
| BM25F same definition as round 2 | `r2_samesite_pairs.Corpus`, `r3_features` | Yes: same fields, weights, k1, b; IDF from training pages instead of the round 2 reference pages, which only reweights terms inside a pair. Fair |
| Frozen scorer reads no rank, no SERP title or snippet, no history | Code read, plus the 53 features in `model/page-blend/spec.json` checked against position, rank, list_len, n_results, title, snippet, date and the site tier | Yes (section A) |

### A. Leakage, item by item

| Item | In primary? | Can it leak the order of a pair? |
| --- | --- | --- |
| `position`, `rank`, `list_len`, `n_results` columns in the table | No; none is in any tier. `rank` is only the LambdaMART training grade | No |
| SERP title, snippet, date (loaded by `r3_data`) | Never used as features. The cross-encoder text is the HTML title, H1 and meta. URL features use the path only, so a `#:~:text=` fragment cannot get in | No |
| Rank history | None in the primary. The ranker baseline gets one empty snapshot | No |
| Which pages are in the list (Google's choice, cut at 10) | Defines the pair universe only | No: every within-list operation (`zscore_in_list`, `_filled` list-mean imputation, vote percentile ranks) is a function of the unordered set. Framing: the scorer is never asked about pages outside a site's top 10 |
| Document-frequency table | Yes (BM25 features) | No: built from training pages only and frozen; the working-tree `r3_fit` rebuilds it on the kept pages, which is correct |
| Anchor and in-list link features from the other listed pages; home-page links | No, site tier (secondary) only | No order; they depend on Google's choice of top 10, so a mild selection channel, secondary only |
| Home-page fetch of test1 sites (19:37 Nov 19, before the freeze) | No, site tier only | No: content only |
| Semantic cache | Yes | No: keyed by model, revision, query and text; models pinned by sha256 |
| Which stored fetch of a page is used | Yes | No order leak, but a process risk: newest fetch wins and the fetch protocol is not frozen (R4) |
| "Usable" filter (fetch success, at least MIN_WORDS words) | Defines the pair universe | No order leak; the selection is the same for every scorer; report coverage by position (R4) |
| Query intent flags | Yes (tree model) | No: search text only |
| Pull B | Never enters features (`build_table([label_a])`) | No |

## 5. Probability that the test passes

My recomputations on the training lists:
- Leave-one-search-group-out within site folds, pair accuracy with site intervals:
  - blend (text, URL, shape): 0.704 [0.684, 0.724]
  - logit on text only: 0.685
  - unfitted cross-encoder on title text: 0.694
  - unfitted vote: 0.682
  - BM25F: 0.614
- Paired differences for the blend: minus BM25F +0.090 [+0.062, +0.116]; minus the unfitted cross-encoder +0.010 [-0.005, +0.024]; minus the vote +0.022 [+0.004, +0.040].
- Accuracy by search group varies widely, 0.42 to 0.82 among groups with at least 46 pairs.
- Choosing among about ten fitted variants on site-only CV adds at most about 0.005 of optimism, because the top variants are within 0.01 of each other and highly correlated.
- Simulated held-out-sized samples (60 sites, one list each, single-pull labels) from these hits:
  - mean lower bound 0.670 (half-width 0.036)
  - P(lower bound > 0.65) = 0.94 with no shift
  - that drops to 0.74, 0.48, 0.26 and 0.06 at shifts of -0.01, -0.02, -0.03 and -0.04

What decides the result is the shift between training and held-out data, which the theory does not price in. 36% of held-out lists are in a topic area with no training data. 13 lists are bare brand searches, some on odd sites ("site:google.com b2w", "site:rev.io revorion"). My expected shift is about -0.025, with a standard deviation of about 0.03.

- **As planned** (both-present consensus, min of site and query bound): point estimate about 0.69, lower bound about 0.655. **P(pass) about 0.55.**
- **Under R1 and R2** (absent-below consensus, search-group clustering): point estimate about 0.68, lower bound about 0.64. **P(pass) about 0.4.**
- The stretch (0.75) does not pass, which agrees with the theory.
- The beats-BM25F and data-count checks pass with probability above 0.9. Beating the ranker's content part is very likely even after R6.

**Most likely failure:** the lower bound lands between 0.62 and 0.65. The point estimate would be 0.67 to 0.69, pulled down by the revenue and royalty lists and the brand-navigational lists, and the interval is about 0.04 wide because there are only about 40 search groups. The scorer would still clearly beat BM25F and the ranker. The second risk is that the scorer turns out not to beat the unfitted cross-encoder. That would not fail the rule as written, but it would change what a pass means.
