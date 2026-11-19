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
