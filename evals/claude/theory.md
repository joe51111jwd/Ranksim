# Ranksim round 2 theory (Claude goal run)

Written 2026-11-19, before anything in round 2 was built and before any day after 2026-11-17 was read. Every count below is from design days (through Nov 17) and comes from a committed script. Every forecast is labeled PREDICTED.

Sources: the two research memos in `evals/claude/research/` (public sources, each claim graded), and the design-day studies in `evals/claude/r2/` (new entrants, same-site pairs, flip noise, edit signal, Wayback backfill).

## 0. Why pages move: the picture the research gives

Three things move a URL between two daily top-10 lists. They differ in size and in whether anything we can see predicts them.

1. **Order noise at the same moment.** Two identical requests at the same time return the same URLs in a noisy order (Hannak et al., Jaccard 0.998, Kendall tau 0.88). Noise is smallest at position 1 and grows down the page. Our own pulls 30 minutes apart agree on the exact position 35% to 47% of the time. A three-pull median removes some of it, not all.
2. **Site-level steps.** Google copies site-level quality values into every page's serving record, and a core update changes them in stages. The November 2026 core update started on Nov 10 and was not marked complete on Nov 19. Every pull from Nov 11 on sits inside it.
3. **Page-level change and click memory.** Click statistics accumulate over 13 months. A new URL starts with none and borrows its site's standing. An on-page edit can only act after Google reprocesses the page (a few days to a few weeks), and controlled split tests find that about three in four on-page edits have no detectable effect at all.

What follows from that, target by target, is below. Each section gives the mechanism, why it should hit the number, the data it needs, a back-of-envelope from real counts, what gets built, and what would prove the theory wrong.

## 1. Target 1: where a new URL lands

### Mechanism

A URL that is new to a search's top 10 comes in through one of two doors.

- **Door 1, the site's own slot.** The site already holds a place on that search and Google changes which of its pages it shows. The new URL inherits the site's place. Reason: site-level quality and host-level click statistics exist from day one, URL-level clicks do not (leak fields `siteAuthority`, `crapsNewUrlSignals`, host-level impressions; evidence that the fields exist is strong, the link to placement is medium).
- **Door 2, the bottom of the page.** A URL with no standing on that search enters where slots open, and slots open at the bottom. Reason: click starvation (sworn testimony: fresh pages "may start out with no clicks at all") and no documented boost for new pages.

### What the design data says

From `evals/claude/r2/t1-mechanisms/notes.md`, pooled over the six design slices (303 new rows):

- Slots open at the bottom. The share of URLs that leave rises from 23 of 163 at position 1 to 82 of 142 at position 10. New rows land at 8 to 10 in 131 of 303 cases.
- Door 1 is mostly a swap. Of 101 new rows whose site was on page one the day before, 84 came with a same-site URL leaving and 17 were additions. The swapped slot is within the 3-wide band for 40 of 84, against 24 of 84 for a flat guess of 9.
- A second page of a site does not cluster next to the first (1 of 17 additions landed directly below it), so "site position plus one" is no better than "site position".
- The rule "site's slot, else 9" is the round 1 frozen rule. It gets 148 of 303 against 103 for position 7 and 131 for a flat 9.
- Tested and rejected: moving a URL's position over from a sibling search, in four forms, with or without weighting by how much the two result lists overlap (44 of 96 against 46 for a flat 9 and 52 for the frozen rule). The first position that was unstable yesterday carries no slot information.
- No honeymoon was found in the outside evidence, so no decay term is added.

### Why this should hit the number, and the back-of-envelope

The brief's baselines are the domain prior (11 when the site was not there), position 7 and position 11. The domain prior knows door 1 and is blind to door 2. Position 7 covers 6 to 8 and misses the bottom. The rule covers both doors.

- Design effect on 3-wide accuracy, model minus the best of the three baselines: +0.149 [+0.075, +0.232] pooled, +0.200 [+0.057, +0.361] on the two three-pull days.
- Rows needed: about 25 to 60 rows for 80% power at +0.20 to +0.25, about 200 at +0.15 (`evals/claude/t1-placement/power.md`).
- Rows expected: the last three-day gap on three-pull truth (the original 30 searches on Nov 16) gave 30 new rows on 30 searches. The 70 added searches on Nov 19 have the same gap, so about 70 strict-new rows on that one day. Later days give about 5 per 30 searches, so about 17 per day on 100 searches.
- PREDICTED: the pooled reading (at least two held-out days, at least 40 rows) passes with probability about 0.8 if the design effect holds. The strict reading (two separate days with 20 or more rows each and an interval above zero on each) is not reachable this week for lack of rows on any day but Nov 19.

Known risk, stated now: the one design slice that looks like Nov 19 (three pulls, three-day gap, the original 30 on Nov 16) is where the site slot was weakest (3 of 13 swaps in band against 6 for a flat 9). After three days the site's old slot is stale. The rule still beat the best brief baseline on that slice (+0.167 [+0.053, +0.310]), because the win over the brief's baselines comes mostly from door 2.

### What gets built

Nothing new. The frozen round 1 predictor (`scripts/claude/t1_placement.py`, preregistration `evals/preregistered/claude-t1-placement/`) is this mechanism, and the research found no second mechanism that adds to it. Building a variant would be tuning, not theory.

### The other reading of "within 3"

If "within 3" means an absolute error of 3 or less, position 7 alone covers positions 4 to 10 and is right about 83% of the time. To beat it a rule must pick out the entrants that land in the top 3. Two flags exist on every design slice (the site already holds a top-3 slot on this search: 20 of 48 land top 3; the URL is top 3 on a sibling search: 16 of 40), but a flag has to be right more often than its rows land at 8 to 10, and 40% is not enough. PREDICTED: this reading fails. It would take a top-3 flag with precision well above one half, which nothing in the data or the research offers.

### What would prove this wrong

- The frozen rule does not beat the best brief baseline on the pooled held-out strict-new rows.
- Or it beats them but not a flat 9. Then door 1 adds nothing on held-out days and the whole gain is "newcomers land low".

## 2. Target 2: does a changed page go up or down

### The number, read two ways

- **The brief's wording (L).** On real (search, URL) rows where the page changed and the rank moved, call up or down better than the larger class and better than a coin, with the lower bound above 0.55, on 40 or more rows.
- **What the product needs (H).** The page diff has to add something over a rule that never reads the page. Round 1 froze this as a guard: the content-aware rule must beat the position-only rule on the same rows.

### Mechanism

A short-window move of a ranking page is mostly the page returning to where it usually sits. A URL outside the top 10 can only come in. A URL at 1 can only fall. In between, a URL that sits below its own recent average tends to come back up and one above it tends to fall back. That is order noise around a slow level (section 0, item 1). An edit can only act after Google reprocesses the page, and then only on text match and page-level quality, while site standing and 13 months of clicks stay where they were.

So the theory makes two separate claims:

1. **Position state decides direction about 70% of the time, edited or not.** This is what hits the brief's wording.
2. **In windows of 1 to 7 days the page diff adds almost nothing, and no test of 40 rows can show that it adds anything.** This is why H is out of reach this week, and what it would take is computable.

### Evidence for claim 1

- Design days: the position-only rule is right on 12 of 17 changed-and-moved rows and on 303 of 437 unchanged-and-moved rows (0.693 [0.647, 0.736]). The difference between the two groups is +0.007 [-0.224, +0.222].
- Outside: of 37,008 drops out of a top 10 in a 41-day study with no core update, 24% were back the next day and 56% within a week (SEOmonitor, medium evidence).

### Evidence for claim 2

Outside (`evals/claude/research/edit-effects.md`):

- In published split tests about 75% of on-page edits had no detectable effect, about 15% a positive one, 7% to 8% a negative one. Those tests use hundreds of pages and weeks of traffic.
- A detected effect is 3% to 20% of clicks, which is at most 0.1 to 0.6 positions. Ordinary day-to-day movement at positions 4 to 10 is 1.0 position.
- The sign of title and H1 keyword edits, title length, a year in the title and content removal flips from site to site. Only substantial content additions and new internal links lean one way.
- Google says reprocessing takes "a few days to a few weeks". Vendors run tests for 2 to 6 weeks.
- No content tool or rank simulator has published a check of predicted edit effects against real rank changes.

Inside (`evals/claude/r2/t2-signal/notes.md`, `evals/claude/r2/samesite/notes.md`):

- Changed pages do not move more. Rows with a material change moved 0.640 of the time against 0.557 for unchanged rows from the same date pair and start band: +0.083 [-0.082, +0.267]. They did not move further (-0.22 positions [-1.05, +0.59]). Movement does not grow with the size of the diff.
- Upper bound on the share of moves on changed pages that the edit can have caused: 0.335 on all rows, 0.415 on rows that start at positions 2 to 10 (point estimate 0.246).
- So a perfect content reader, used only where the edit caused the move, adds at most 0.10 accuracy over the position rule (0.17 at the upper bound).
- Google showed the new title for 2 of 11 title-tag changes we saw. Substantive title changes on the results page are clean (1 false change in 1,865 same-day pull pairs) but rare (15 through Nov 17), and rows did not move more after them.
- Holding the site fixed, text match does order pages: BM25F puts the higher of two same-site pages first in 277 of 464 pairs (0.597 [0.548, 0.647]) against 0.522 across sites. That is the size of the content signal in the best case, cross-sectionally. Applied to the 17 real edits, the sign of the score change matched the move 9 times.

### Back-of-envelope

**L.** If the true accuracy is 0.70 and rows cluster as on design days (1.4 moved rows per changed page), the lower bound clears 0.55 at about 60 rows, not 40: at 40 rows the bound is about 0.53, at 60 about 0.56, at 80 about 0.58. Rows expected: 576 URLs have a Nov 19 re-fetch and an earlier plain fetch. At the design rate (13 material changes in 262 usable pairs) that is about 26 changed pages, 56 rows, and about 35 changed-and-moved rows on the Nov 16 to Nov 19 window. One more daily re-fetch adds about 15. PREDICTED: about 35 rows through Nov 19 (too few to run the test), about 50 through Nov 20, about 65 through Nov 21. The chance that L passes when it can first be run is about one half.

**H.** From the measured inputs (share of interior rows 0.824, share of moves caused by the edit 0.246 with an upper bound of 0.415, position rule 0.651 on interior rows), a content step that is right 70% of the time on the rows where the edit caused the move needs 676 changed-and-moved rows at the point estimate and 396 at the upper bound, and only if it somehow fires on those rows alone. A content step that fires wherever the page changed can never beat the position rule unless edits cause more than 43% of moves. At 16 rows a week on the URLs tracked through Nov 17 that is 25 to 42 weeks. PREDICTED: H cannot pass on any number of rows this month.

### What would make H reachable

1. **Longer windows.** Measure 14 to 28 days after the change, with several pulls averaged on each side, against matched unchanged pages. This is where published tests see effects.
2. **More rows.** About 250 edited rows with 250 controls shows a half-position shift. Daily re-fetch of every tracked URL (785 fetchable now, against 98 through Nov 17) gives about eight times the design yield, but only for searches with a daily multi-pull rank, which costs credits: 100 searches at three pulls is 300 credits a day.
3. **Planned edits.** Randomised edits on pages we control, with variants written down first. This is the only design with a known cause. It needs James and is not done here.
4. **Earlier versions for free.** The Wayback Machine has dated copies of some tracked pages from before our first fetch. That extends the before side back in time (`evals/claude/r2/wayback/`).

### What gets built in loop 1

- No new short-window direction rule. The theory says none can show a gain, so another variant would be a knob, not a theory. The frozen round 1 rule is scored for L and H as soon as there are 40 rows.
- A frozen **excess-movement test** on held-out rows: do changed pages move more than matched unchanged pages? This is claim 2's central number, measured again on about four times as many pages. PREDICTED: the difference in P(moved) is below +0.20 and its interval includes zero.
- A frozen **same-site content check** on held-out days (top 10 only, consensus order): BM25F and the eight-feature text vote, nothing fitted. This is the brief's counterfactual diagnostic done on real pairs. It is a diagnostic, not proof. PREDICTED: between 0.55 and 0.65, above the cross-site rate.
- The **long-window collector**: the daily re-fetch keeps running, and a scorer for the 14 to 28 day window is frozen now and run when the first changes are old enough (from Dec 3).

### What would prove this wrong

- On held-out rows, changed pages move clearly more than matched unchanged pages (difference above +0.20 with the interval above zero). Then edits matter more in short windows than the theory says, and a content rule deserves a new attempt.
- The frozen content-aware rule beats the position-only rule on held-out rows.
- The position rule falls to coin level on changed rows while staying near 0.70 on unchanged ones. That would mean edits scramble direction, which is also an edit effect.

## 3. Target 3: which pairs swap order overnight

### Mechanism

Over a day or two, each pair of URLs on a results page has a stable chance of appearing in one order rather than the other. A single pull is one noisy draw from it. A three-pull consensus is a less noisy draw, but still a draw. When two consecutive days show a pair in opposite orders, the usual cause is two draws landing on different sides, not a change at Google.

### What the design data says

From `evals/claude/r2/t3-noise/` (the one consecutive-day consensus transition in design data, Nov 16 to Nov 17, the original 30 searches, 767 pairs, 44 flips):

- **The flips are what pull noise alone produces.** Shuffle each search's six voting pulls between the two days and count flips the same way: 45.0 expected on 811.9 pairs. Observed: 44 on 767. Noise share of the flip count 1.02 [0.78, 1.47].
- **Real order changes are too few to see.** In a model that lets a pair's order chance be redrawn between the days, the best estimate of the redraw rate is zero and its upper bound puts about 2 of the 44 flips down to real change. That bound treats pairs as independent, so it is too tight, but the permutation count says the same thing.
- **Flips come from pairs that already alternate.** 38 of the 44 flips are in the 222 pairs whose reverse order had shown in at least 2 of the 8 pulls up to day one (17% of those flip). Pairs whose reverse order had never shown: 4 flips in 396 (1%).
- **Real change needs days, not hours.** Two single pulls disagree on 7.0% of pairs inside one session, 8.0% at 17 hours, 10.1% at 25 hours, 13.0% at 2 days, 15.4% at 4 to 5 days and 20.3% at 10 to 11 days.
- Outside evidence agrees: two identical searches at the same moment give the same URLs in a noisy order (Kendall tau 0.88).

### What follows for any predictor

Take one pair whose consensus shows its usual order with probability q. On a day it shows the unusual order it flips back tomorrow with probability q. On a day it shows the usual order it flips with probability 1 minus q. Two consequences:

1. Calling a flip on every pair that is in its unusual order today catches exactly half of all flips, and more than half of those calls are right. That is the same as saying "tomorrow will show the long-run pull-majority order". It beats yesterday's consensus on all pairs. So a denoised order is a better forecast than persistence. This is the useful product fact.
2. To catch more than half, a predictor must also call flips on pairs that sit in their usual order, and those calls are right less than half the time. With precision held at one half, the best reachable flip recall for one pair is 1/(2q).

Numbers, with the distribution of q fitted on the design transition (three model families):

| What the predictor knows | Flip recall with half of its calls right |
| --- | --- |
| Every pair's true q, nothing changes between days | 0.60 to 0.72 |
| Same, with real change at its upper bound | 0.54 to 0.66 |
| Day one's 3 pulls plus 12 more recent pulls | 0.56 to 0.65 |
| Day one's 3 pulls plus 6 more | 0.55 to 0.59 |
| Day one's 3 pulls plus 3 more | 0.50 |
| Day one's 3 pulls plus 1 more | 0.41 |
| Day one's 3 pulls only | 0.00 |

On the real design pairs, a predictor of this kind that sees the four Nov 16 pulls calls 38 pairs and is right on 16: recall 0.36, precision 0.42. Adding the four older single pulls (Nov 6 to 13) makes it worse (24 of 102 right), because over a week or more the order chances themselves have moved. Only the last day or two of pulls count.

### Back-of-envelope: is the number reachable?

- **The brief's wording alone** (more than half of the flipped pairs called, lower bound above 0.5): yes, and it means nothing. "A pair that any of yesterday's pulls showed reversed" holds 35 of 44 flips. Reversing every pair holds 44 of 44.
- **With the guard that the predictor is no worse than persistence on all pairs** (which is the same as "at least half of its flip calls are right"): the ceiling is 0.60 to 0.72 with perfect knowledge and about 0.50 with the pulls we have. A lower bound above 0.5 needs a true recall near 0.58 and about 300 flipped pairs, or 0.55 and about 700 (the design interval on 44 flips is plus or minus 0.2). That takes 12 or more recent pulls per search, so at least 6 pulls a day, on 100 searches for several days: about 600 credits a day. The balance is 891.
- PREDICTED for the held-out days in hand (Nov 17 to 18 with about 4 extra recent pulls, Nov 18 to 19 with about 7 that are up to two days old, about 90 flips in all): flip recall 0.35 to 0.55 with precision 0.40 to 0.55. The lower bound stays under 0.5. **Target 3 with the guard fails, and it fails because the thing to be predicted is measurement noise.**

### What would make it reachable, and what is worth predicting instead

- More pulls per day would lift the ceiling toward 0.6, at a price, to predict noise. Not worth buying.
- Over three days or more, most consensus flips are real: the projected noise share at three pulls is about 0.42 at 2 to 3 days, 0.34 at 4 to 5 and 0.28 at 6 to 7. That is the horizon at which "which of two pages will be ahead" is a question about Google and not about the measurement. It needs multi-pull days on both ends, which exist from Nov 16 on. This is the natural loop 2.

### What gets built in loop 1

- **The latent-order scorer** (`scripts/claude/r2_t3_latent.py`). For each pair it pools the pull-by-pull order votes of recent days, weighted by age with a two-day half-life, turns them into a probability that tomorrow's three-pull consensus shows the reverse order, and calls flips two ways: every pair above one half, and the largest set whose expected share of right calls is at least one half. No weight is fitted.
- **The noise check** (`scripts/claude/r2_t3_noise_check.py`). The permutation count above, frozen, run on every held-out consecutive transition. PREDICTED: the noise share of the flip count is between 0.75 and 1.3 on each.

### What would prove this wrong

- On held-out transitions the observed flips clearly exceed the shuffled-pull expectation (noise share under 0.7). Then real overnight change is common and a different predictor is worth building.
- The latent-order scorer's calls are right far less than 40% of the time. Then order chances are not stable even for a day.
- Any frozen scorer reaches a flip-recall lower bound above 0.5 while at least half of its calls are right.

## 4. Summary of predictions made before scoring (all PREDICTED)

| Target | Reading | Prediction | Why |
| --- | --- | --- | --- |
| 1 | 3-wide band, pooled held-out days | Pass, probability about 0.8 | Two doors: the site's slot, else the bottom of the page |
| 1 | 3-wide band, two separate days | Not reachable this week | Only Nov 19 has 20 or more strict-new rows |
| 1 | absolute error of 3 or less | Fail | Position 7 covers positions 4 to 10 |
| 2 | brief's wording (L) | About even, once there are 40 rows; about 35 rows expected through Nov 19 | Position state decides direction about 70% of the time |
| 2 | content beats position (H) | Fail on any number of rows this month | An edit explains a few percent of a short-window move |
| 3 | brief's wording alone | Pass for the contested-pair rule, and it means nothing | Reversing everything also passes |
| 3 | with the persistence guard | Fail | One-day flips are pull noise. Ceiling 0.60 to 0.72, reachable about 0.50 |

---

# Round 3 theory: validate the edit simulator without waiting on Google

Written 2026-11-19 between 20:30 and 21:30 ET. At this point the round 3 scorer is not frozen and no held-out list has been pulled. Every count below comes from a committed script and from training lists only (train sites and train search groups, see R3.3). Every forecast is labeled PREDICTED. Outside sources are in `evals/claude/research/r3-matched-pairs.md`.

## R3.0 What round 3 asks

Round 2 showed that an edit cannot be scored by waiting for Google: days to weeks, about one edit in four detectable, a core update in the way. Round 3 says: today's results are already Google's verdict on every settled page. A scorer that orders pages it has never seen, from content alone, answers the same question as "which version ranks better", and that can be tested today. The targets: history-free pair accuracy on held-out same-site matched pairs with the lower bound above 0.65 (stretch 0.75), clearly above BM25F alone and above the current ranker's content part, plus Target 1 as already designed.

## R3.1 The idea, attacked before anything was built

The idea is kept, with three repairs. Each repair answers an objection that the research memo and the design data raised.

**Objection 1: where do thousands of same-site pairs come from?** Ordinary results hold few. Google shows at most about two pages per site, so 100 searches with a top-30 list gave 465 usable same-site pairs, 43% of them from one site (round 2, `evals/claude/r2/samesite/notes.md`). The repair is the site-restricted search: `site:<site> <search>` returns up to 10 pages of one site, in Google's order for that search, for one credit. That is up to 45 ordered pairs with the site held fixed.

**Objection 2: is that order Google's real order?** Google staff say a site-restricted search with a keyword runs on the normal ranking path (one 2021 statement and one 2017 remark, medium evidence), and nobody has published a measurement. So it was measured first (R3.3, study 1). It agrees with the ordinary list about as well as two ordinary pulls a few days apart agree with each other.

**Objection 3: within a site, order is not only content.** Site quality cancels in a same-site pair. What remains is topic match (body, anchors, clicks per search) and page-level links. Only the page's own text and URL can be read from its HTML. Internal links can be read from the site's other pages. Clicks and outside links cannot be seen. So content alone has a ceiling well under the label ceiling, and the scorer is built in tiers so the report can say how much comes from the page itself.

**Objection 4: settled order is not edit response.** A settled page carries months of clicks and links. An edit changes the text at once and the rest slowly. So accuracy on settled pairs is an upper bound for edit direction, not edit accuracy. Two things address this without waiting: swap pairs (two pages of one site whose order reverses between two searches, which no search-blind page property can explain), and a ledger of PREDICTED directions for the real SmartCrew PR 45 edit, frozen from before and after HTML before any after-merge result is read. PR 45 outcomes are a later confirmation, not part of this test.

## R3.2 Mechanism

For one search, Google has to choose which page of a site answers it. The theory:

1. **The page that declares the search's topic wins.** Title, H1, URL path and meta description are the page's own statement of what it is about. A page whose statement matches the search beats a page that only mentions the words in its body.
2. **Body match is second.** It separates pages whose statements are equally close.
3. **Position in the site is third.** The home page and pages linked from it sit higher than deep pages when topic match is equal.
4. Everything else that orders two pages of one site (clicks on that search, outside links to that page, age) is invisible here and sets the ceiling.

Why this should hold: Google's own testimony calls anchors, body and clicks the parts of topicality, and says quality is mostly a site property that does not depend on the search (trial exhibit PXR0356, strong). In a same-site pair the site part cancels, so topicality decides, and the body-and-title part of topicality is in the HTML.

## R3.3 Real counts (training lists only)

**The split, fixed before any model existed** (`scripts/claude/r3_plans.py universe`, `evals/claude/r3/plans/universe.json`). Universe: every (search, site) combination where the site appeared in a stored list for the search on design days (through Nov 17), without the client's site, without user-content platforms, and without sites that plain HTTP cannot read. That is 2,004 combinations on 579 sites and 155 searches. Sites are split by a seeded hash into train (0.40), test1 (0.35) and test2 (0.25, kept back for a later loop). Search groups (near-duplicate wordings are linked, 71 groups) are split into train (the 19 groups the first study touched) and test (the other 52). Training lists are train sites with train groups. Held-out lists are test1 sites with test groups: 177 combinations on 93 sites. No training list shares a site or a search group with a held-out list.

**Study 1: is the site-restricted order a usable label?** (`scripts/claude/r3_validity.py`, `evals/claude/r3/validity/tables.md`)

- 70 site-restricted lists on 36 sites, pulled Nov 19, against the ordinary Nov 16 top-30 list of the same search.
- The site's best ordinary page is first in the site-restricted list in 42 of 53 cases and in the top two in 49.
- Pairs with both URLs in both lists keep the same order in 62 of 74 (0.838) [0.738, 0.906 by site]. The lists are three days apart.
- Yardstick: two ordinary pulls of one search 2 to 3 days apart agree on 0.880 of same-site pairs and 0.870 of all pairs.
- Label noise: the same site-restricted list pulled twice 40 minutes apart keeps the order of 486 of 554 pairs (0.877) [0.799, 0.944]. Adjacent pairs 0.839, pairs five or more places apart 0.932. If each pull shows the true order of a pair with probability p, then p squared plus (1 minus p) squared is 0.877, so p is about 0.93. No scorer can beat about 0.93 against a single pull.

**Study 2: what orders the pages of one site?** (`scripts/claude/r3_design.py`, `evals/claude/r3/design/tables.md`)

- 250 training lists pulled (70 plus 180 credits). 212 are inside the train cell. 182 lists have a scorable pair. 5,623 pairs on 87 sites and 80 searches, 1,160 distinct readable pages. The largest site holds 2.4% of the pairs.
- Single features, larger value first, ties half: cross-encoder on title, H1 and meta 0.694, embedding of the same text 0.687, search terms in the title 0.659, in the H1 0.634, in the URL path 0.630 (focus), BM25F 0.614, anchor text of same-site links 0.589, linked from the home page 0.527, word count no signal. This is the mechanism's order: declared topic first, body second, site position third.
- Scorers, site-held-out cross-validation (each site scored by a model that never saw it):

| Scorer | Pair accuracy | Minus BM25F |
| --- | --- | --- |
| BM25F alone | 0.614 [0.588, 0.640] | |
| Unfitted vote of 10 text and URL match features | 0.682 [0.660, 0.702] | +0.068 [+0.049, +0.088] |
| Cross-encoder on the title text alone, nothing fitted | 0.694 [0.673, 0.716] | +0.080 [+0.051, +0.109] |
| Logistic regression, text only | 0.693 [0.672, 0.714] | +0.079 |
| Blend of logistic regression and LambdaMART, text plus URL plus page shape | 0.709 [0.689, 0.729] | +0.095 [+0.066, +0.122] |
| Same blend plus site position (home link, links and anchors from listed pages) | 0.714 [0.695, 0.734] | +0.100 [+0.071, +0.128] |

  Intervals are 95% bootstraps over sites.
- Holding out the search group as well as the site costs about one point: 0.704 becomes 0.696 on the 3,318 pairs of the smaller search groups (61 sites), with the two largest groups kept in training.
- The signal has a plateau. Seventeen other ways of reading topic match (a larger cross-encoder, title alone, H1 alone, URL words, headings, the intro, embeddings of each, and unfitted mixes) all land between 0.636 and 0.695. Nothing found on the page lifts the blend past about 0.71.
- Slices of the blend (text plus URL plus shape): pairs with the list's first page 0.879, pairs without it 0.661, adjacent pairs 0.580, pairs five or more places apart 0.832, both in the top five 0.732, same template (same first folder and depth) 0.700 against BM25F 0.640.
- Swap pairs: 286 rows on 37 sites where two pages of one site appear in two lists in opposite orders. A search-blind scorer gets exactly 0.500 on these. BM25F 0.563, the unfitted vote 0.645, the blend 0.612. So the scorers do read the search against the page, with site and page identity held fixed.
- The current ranker with no history (its content part) orders 0.565 of the pairs of the first 44 lists, against BM25F 0.576 on the same pairs.
- Label noise against the scorer: on 18 training lists pulled twice, the blend is right on 0.649 of all pairs of the first pull, 0.682 of the pairs both pulls agree on (365 pairs), and 0.545 of the pairs the second pull reversed (55 pairs). A third of its misses on single-pull labels are noise in the label.

## R3.4 What gets built

1. **The frozen scorer**: the blend of a logistic regression on feature differences and a small LambdaMART, on the text, URL and page-shape tiers (`scripts/claude/r3_model.py`, fixed settings, nothing tuned). It reads the search, the URL and the page's own HTML. No rank history, no Google-written title or snippet, no site authority. It is fit once on all training lists and saved with a sha256 manifest. The same scorer plus the site-position tier is reported as a secondary line, and text only as an ablation.
2. **Consensus labels for the test**: every held-out list is pulled twice, at least 30 minutes apart. The primary truth is the order both pulls agree on, the same idea as the consensus positions used for Targets 1 to 3. Pairs the two pulls disagree on have no consensus order. They are counted and reported, and the single-pull accuracy on all pairs is reported next to the primary number.
3. **The held-out test** (`scripts/claude/r3_score.py`): test1 sites with test search groups, pulled only after the freeze commit is pushed. The script refuses to run otherwise and never overwrites a result.
4. **The PR 45 ledger**: the frozen scorer on before and after HTML of the 15 edited pages, for each target search, frozen before any after-merge result is read.

## R3.5 Back of envelope: is the number reachable?

- Budget: 618 credits now, 250 must stay, and the other team's daily collector spends about 90 a day from the same balance. The test gets 104 lists pulled twice (208 credits). About 150 credits stay free for a second loop on the test2 sites.
- Expected size, from the training yield (86% of lists have a scorable pair, 31 pairs per such list, 64% of sites have one): about 89 lists, 60 sites and 2,750 single-pull pairs. About two thirds of those survive as consensus pairs (365 of 552 in the twice-pulled training lists): about 1,800.
- Expected accuracy: cross-validated 0.709, minus about 0.01 for unseen search groups, plus about 0.03 on consensus pairs. PREDICTED 0.72 on consensus pairs (range 0.69 to 0.75) and 0.70 on all single-pull pairs (range 0.67 to 0.72).
- Expected interval: the site bootstrap on 61 sites and 3,318 pairs had a half-width of 0.028. With 60 sites and fewer pairs it is about 0.03 to 0.035.
- PREDICTED lower bound on consensus pairs: about 0.69 (range 0.65 to 0.72). **PREDICTED: the 0.65 target passes with probability about 0.75. The 0.75 stretch does not pass.** On single-pull labels the lower bound would be about 0.665, a coin-flip pass.
- PREDICTED differences: scorer minus BM25F about +0.09, interval above zero. Scorer minus the current ranker's content part about +0.12, interval above zero.
- Context from outside: the best transparent published result for predicting Google's order of a pair from outside features is 62.5% to 67.5% on held-out queries (Bifet et al. 2005), and content-tool scores correlate with position at about 0.28, which is roughly 59% of pairs. A lower bound above 0.65 would be at the top of what anyone has published. A stretch of 0.75 has no transparent precedent, and the plateau above says page content alone will not reach it.

## R3.6 What would prove this wrong

- The frozen scorer does not beat BM25F on held-out sites and searches with the interval above zero. Then the gain was fitted to the training sites.
- Its accuracy on consensus pairs is under 0.65 at the point estimate. Then topic match does not order a site's pages on unseen sites the way it did in training.
- It is at or under 0.5 on held-out swap pairs. Then it is not reading the search against the page, only page properties.
- The second pull agrees with the first on far fewer than 0.8 of pairs. Then the label is too unstable to test anything.

## R3.7 What a pass would and would not mean

A pass means: shown two pages of a site it has never seen, for a search it has never seen, the scorer picks the one Google ranks higher about 7 times in 10, from the pages alone, and it does this clearly better than BM25F and the current ranker's content part. That is the product decision "which of two versions fits this search better" for versions as different as two pages of one site.

It does not mean that an edit predicted to help will move the rank. Two candidate edits of one page differ less than two pages do, and a settled rank also holds clicks and links that an edit does not move at once. Accuracy on adjacent pairs (0.58 in training) is the honest guide for close calls. The PR 45 ledger is the first real check, and it is small.

## R3.8 Target 1 in round 3

Unchanged. The round 1 rule (site's own slot, else the bottom of the page) is frozen and is scored on Nov 18 and Nov 19 as designed (section 1 above). Round 3 adds nothing to it.

## R3.9 If the test fails: next ways that still do not wait on Google

1. **Crawl each site's link structure** (free): real internal link counts and anchor text for every listed page. The two crude site-position features already add half a point to one point. This is the largest unread signal that is still history-free.
2. **More lists per site** to raise swap pairs from hundreds to thousands, and train on swaps only, so the scorer learns pure search-to-page match.
3. **Wayback versions** of pages that changed months ago, scored against their settled rank now. Blocked from this box (web.archive.org does not answer here), runnable from any other network with `scripts/claude/r2_wayback.py`.
4. **Three pulls per list** for labels as clean as the consensus positions of Targets 1 to 3.
