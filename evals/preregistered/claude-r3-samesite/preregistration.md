# Pre-registration: round 3, history-free order of same-site pairs (Claude goal run)

Label: PREDICTED. Written 2026-11-19, about 23:00 ET, after an independent review of the theory and of the test plan (`evals/claude/r3/reviews/theory-review.md`) and after every change that review required. At this time no held-out list of this test has been pulled, by anyone. Nothing here changes an earlier gate, the SmartCrew PR 41 ledger, or the product ranker.

## The target, from GOAL.md (Round 3)

"History-free pairwise accuracy on held-out same-site matched pairs with CI lower bound above 0.65 (stretch 0.75), clearly above BM25F alone (0.597 now) and above the current ranker's content part ... freeze before scoring, 95% CIs by query and by site."

## What is frozen

The sha256 of every file below is in `manifest.json` in this folder. `scripts/claude/r3_score.py` refuses to score when a hash differs, when the freeze is not committed, or when it is not pushed.

- The scorers: `evals/claude/r3/model/page-blend/` (primary), `page-site-blend/` and `text-logit/` (secondary), each with its fitted weights and the document-frequency table BM25 uses. They were fit once on the training lists (`scripts/claude/r3_fit.py`). They are not refit.
- The pretrained text models the features use, pinned by revision and sha256 in `evals/claude/r3/models.json`.
- The split, the held-out plan and its report slices: `evals/claude/r3/plans/universe.json`, `heldout1.jsonl`, `heldout1.meta.json`, `heldout1.slices.json`.
- The current ranker and its off-page file, used for the baseline: `evals/preregistered/forward-pair-2026-11-16/model-next.json`, `offpage.tsv`.
- This test's settings: `test.json` in this folder.
- The code: `scripts/claude/r3_score.py` and every repo file it imports (pull data layer, page store, parser, features, semantic models, dataset, scorers, baselines, plans, metrics, the ranker's own feature code). The manifest lists every file, and also `scripts/claude/r3_pull.py` and `r3_fetch.py`, which make the data.

## The split

Fixed from design days (search results through 2026-11-17) and a seed, before any round 3 model existed (`scripts/claude/r3_plans.py`). `universe.json` as committed is the split.

- Universe: every (search, site) combination where the site appeared in a stored list for the search on design days. Left out: the client's site, user-content platforms, and sites plain HTTP has never been able to read. 2,004 combinations, 579 sites, 155 searches.
- Sites: train, test1 or test2 by a seeded hash (0.40, 0.35, 0.25). The 36 sites of the first validity study are train sites.
- Search groups: near-duplicate wordings are linked into 71 groups. The 19 groups the validity study touched are train groups. The other 52 are test groups.
- Training lists: train sites with train groups (223 lists in the cell, 182 with a scorable pair, 5,623 pairs, 87 sites, 19 search groups). All are construction software searches.
- Held-out lists: test1 sites with test groups. 177 combinations on 93 sites. The plan takes 104: one list for each of the 93 sites, then a second list for 11 sites, in a seeded order, at most 8 lists per search. It is the only draw of this cell that is pulled. It covers 46 searches in 35 search groups. 67 lists are construction software searches and 37 are revenue recovery, revenue leakage and music royalty searches, a topic area with no training list.
- No held-out list shares a site or a search group with any list used to fit or choose the scorer. The test2 sites stay unused.
- What the design did see of the held-out cell: the round 2 same-site study read the ordinary Nov 16 top-30 lists of every search, including these sites and searches, and its findings suggested the feature families. The universe was drawn from those lists. The report has a slice without the pairs those ordinary lists already showed.

## Truth

A list is one search of the form `site:<site> <search>`: up to 10 pages of one site in Google's order (Serper, gl=us, hl=en). This is called site-restricted order. Each held-out list is pulled twice, the second pull at least 30 minutes and at most 6 hours after the first. Both pulls happen after the commit that freezes this folder is pushed. Each request is sent once per pull. A list that returns no results is a valid answer and is not pulled again. If a pull stops part way (a request failed four times), the same command is run again and resumes from the stored chunks.

Pages: after the first pull ends, one plain-HTTP fetch pass (`scripts/claude/r3_fetch.py --label heldout1-a`, robots.txt obeyed) fetches the listed pages that have no good plain-HTTP fetch stored since 2026-11-16. No browser fetch, no second pass. The scorer reads no page version stored after that pass ends. A page is readable when its main text has at least 50 words.

The pair universe is every two readable pages of a list's own site in the first pull. It is fixed before the second pull is read. Under the second pull, with a page that is missing from its top 10 counted as below every page that is present, each pair is:

- a **consensus pair** when the second pull orders it the same way (both pages present in the same order, or only the lower page missing);
- a **disagreement** when the second pull orders it the other way (both present and flipped, or only the higher page missing);
- **left out** when both pages are missing from the second pull.

## The scorer (PREDICTED)

Primary: `page-blend`. For each (search, page) it reads the search text, the URL and the page's own HTML. Features: BM25F and other word-match counts in title, H1, headings, meta description, body and URL path, a pretrained cross-encoder and embedding model on the title text and on body passages, URL depth and page-type words, and page shape (size, headings, links, schema types). Two models on those features, a logistic regression on pair differences and a small LambdaMART, are each standardised inside the list and averaged. Nothing in it reads a position, a rank history, a Google-written title or snippet, or anything about the site's authority.

Secondary, reported and not part of the pass rule: `page-site-blend` (the same plus the page's position in its site: linked from the home page, links and anchor text from the other listed pages) and `text-logit` (text match only, no URL, no shape).

Baselines on the same pairs:

- BM25F alone, the same definition as the round 2 study that gave 0.597 (IDF from the training pages).
- The current product ranker with an empty history and the same placeholder position for every row (`scripts/claude/r3_ranker_baseline.py`), in two variants: the title slot blank, and the title slot holding the page's own HTML title. That is its content part. The scorer must beat both.
- The unfitted cross-encoder on the title text and the unfitted vote of ten match features. They are not part of the pass rule. They show how much the fitting adds.

A page with no score from a scorer makes its pairs ties for that scorer. Those pairs are counted.

## Metric and intervals

Pair accuracy: the share of pairs where the scorer gives the higher number to the page listed higher. A tie counts half. Intervals are 95% percentile bootstraps, 2000 draws, seed 20261119, three ways: over sites, over search groups (near-duplicate wordings move together), and two-way (sites and search groups both resampled, a pair weighted by the product of the two multiplicities). Differences between two scorers are paired on the same pairs.

## Pass rule

Applied by the script, on the consensus pairs, for the primary scorer, with the two-way interval:

1. at least 40 sites and 800 consensus pairs;
2. the two-way lower bound is above 0.65;
3. primary minus BM25F: two-way lower bound above zero;
4. primary minus each variant of the current ranker's content part: two-way lower bound above zero.

The target passes when all four hold. The stretch passes when, in addition, the two-way lower bound is above 0.75.

The two-way interval is wider than the "by query and by site" intervals the brief names. Both one-way intervals are reported, and the report says whether the result would clear 0.65 under them.

Always reported next to the primary number, whatever they show: accuracy on all pairs of the first pull (the single-pull reading), half credit on all pairs (a disagreement counts one half), the consensus pairs with both pages in the second pull, every status with its count and accuracy, pull-to-pull agreement, the macro average by site, coverage (lists with results, pages readable by list position), the secondary scorers and the unfitted baselines, and these slices: pairs with the list's first page, pairs without it, adjacent pairs, gaps of 2 to 4 and of 5 or more, same template (same first folder and depth), both in the top five, each topic area, one-word brand searches and the rest, searches whose brand is also in a training search, without the two sister domains of training sites, with and without the pairs both seen in a design-day ordinary list of the search, and swap pairs (descriptive only: few sites have two lists).

## Predictions made before scoring (PREDICTED, from `evals/claude/theory.md` R3.5)

- Size: 75 to 90 lists and 65 to 80 sites with a scorable pair, 2,000 to 2,800 pairs in the first pull, 1,500 to 2,100 consensus pairs, 35 search groups or fewer.
- The two pulls agree on about 0.88 of the pairs both hold.
- Primary scorer on consensus pairs: 0.69 (range 0.64 to 0.73). On all pairs of the first pull: about 0.68.
- Two-way lower bound on consensus pairs: about 0.65 (range 0.60 to 0.69). Pass at 0.65 with probability about 0.45. The reviewer's estimate is 0.4. The stretch does not pass.
- Primary minus BM25F about +0.08, two-way interval above zero. Primary minus the current ranker's content part about +0.10, above zero. Primary minus the unfitted cross-encoder about +0.01, interval across zero.
- The revenue and royalty lists score lower than the construction lists.

## What would prove the theory wrong

- The primary scorer does not beat BM25F with the two-way interval above zero.
- Its point estimate on consensus pairs is under 0.65.
- The second pull agrees with the first on far fewer than 0.8 of the pairs both hold.

## Design numbers (training lists only, not a test)

From `evals/claude/r3/design/tables.md` and `extra.md`. 5,623 pairs on 87 sites and 19 search groups. The scorer family and the tiers were chosen after looking at about ten variants on these lists, so the numbers lean high.

| Scorer | Site held out | Site and search group held out |
| --- | --- | --- |
| BM25F alone | 0.614 [0.588, 0.640 by site] | same |
| Current ranker with no history (both title variants give the same scores) | 0.591 [0.568, 0.612] | same |
| Cross-encoder on the title text, nothing fitted | 0.694 [0.673, 0.716] | same |
| page-blend | 0.709 [0.689, 0.729], two-way [0.669, 0.742] | 0.704 [0.684, 0.724], two-way [0.668, 0.736] |
| page-site-blend | 0.714 [0.695, 0.734] | 0.710 [0.691, 0.729], two-way [0.673, 0.746] |

With site and search group held out, page-blend minus the unfitted cross-encoder is +0.010 [-0.005, +0.024]. On 20 training lists pulled twice (594 pairs), page-blend was right on 0.668 to 0.672 of all first-pull pairs and 0.675 to 0.686 of the consensus pairs as defined above.

## What a pass would and would not mean

A pass says: on consensus pairs of site-restricted lists, for sites and search wordings the scorer never saw, it ordered two pages of one site the way both Google pulls did, clearly better than BM25F and the current ranker's content part. Searches of the same kind as some held-out searches (brand plus alternative, pricing, reviews) were in training. The scorer reads the page's text and URL, and an off-the-shelf cross-encoder does most of the work. It is a test on settled pages. It does not measure the effect of an edit. It is an upper bound on how well the scorer can tell which of two edits of one page will rank better. The first real edit check is the SmartCrew PR 45 ledger, frozen from before and after HTML with this same scorer before any after-merge result is read.

## One run

The test is scored once. A result file is never overwritten. No list is added, dropped or pulled a third time. If the test fails, the held-out lists become design data, and any later test needs new sites (the test2 sites) and its own preregistration.
