# Review of SPEC-MASTER-PLAN.md: hidden assumptions, missing free signals, fit to James's goal

Reviewer: independent adversarial reviewer (lens: assumptions, free signals, product fit). Written 2026-11-20.
Read-only review. No Serper request and no other outside request was made. No model was fitted or scored against ranking data. Local reads: key names in `serp_raw.raw_json` (names only, all dates, no positions, URLs or text), readable page counts per domain in `index.sqlite`, the committed round 3 design feature table (feature values only, no positions), the on-disk Search Console exports, and the collector script and today's logs. Synthetic checks used numpy with seed 20261120.

Status: complete. 12 findings: 1 BLOCKER, 9 MAJOR, 2 MINOR. Ordered most severe first: A1 BLOCKER, A2 to A10 MAJOR, A11 and A12 MINOR.

## Findings

### A1. BLOCKER. At today's balance the plan breaks the 250 floor by tomorrow, and Checkpoint 3b can no longer happen at all

Section: 0.3 D1, 4 (G0), 7 (3D.2), 9.

Claim. The plan's credit table starts from 617 and a collector spend of "about 90 a day". The lead measured 538 at 10:40 ET and expects about 508 by 11:00 ET. The real spend is 30 credits per pull, three pulls a day, plus a separate 15-credit tracker, so about 105 a day. Today's log agrees: `pull20-a.log` reads "Serper balance before: 598" at 09:44, and three 30-credit pulls bring that to 508. The D1 default is "Run stage 1 at once". Stage 1 takes the balance from about 508 to about 300. The collector's own floor is 200 (`/workspace/ranksim-data/collect_daily.sh` line 13: `RESERVE="${RANKSIM_CREDIT_RESERVE:-200}"`; lines 79 to 87 tighten its cap to `balance - reserve`). Each collector run checks the balance on its own. From 300, tomorrow's first pull leaves about 270, the second about 240 (under 250), the third about 210, and the tracker can take it to 200. That breaks James's 250 rule. Separately, 3b is now impossible even if D1 is approved at once.

Evidence.
- Today's collector logs: `pull20-a.log` 09:44, `pull20-b.log` 10:16, `pull20-c.log` 10:58 in `/workspace/ranksim-data/snapshots/`. So the next collector spend is tomorrow morning.
- G0 needs at least 468. 508 minus 468 is 40, less than one collector day. Stage 1 must start today.
- After stage 1: 508 - 208 = 300. Plan 3D.2: `N = min(60, floor((balance - 250 - 15) / 3))` = floor(35 / 3) = 11. Under 30, so "Checkpoint 3b is not run". This holds even with the collector paused today.
- If the balance check fails, the collector skips the reserve check entirely (line 73: `if [[ -n "$BALANCE" ]]`; on failure the balance call prints `{}`), and runs with its default cap of 120. So the plan should not count on the collector's own guard.

Fix. Replace the D1 row and G0 with: "G0 also requires, before any stage 1 pull, that James confirms the collector's caller sets `RANKSIM_CREDIT_RESERVE=265` or higher (the 250 floor plus the separate 15-credit tracker), or that the collector is paused. With no answer, stage 1 does not start, because spending to 300 with a 200 collector floor breaks the 250 rule." Tell James this is a same-day decision: if nothing changes, tomorrow's collector run leaves about 403, under the 468 that stage 1 needs, so stage 1 can never run either. Change the 0.2 table row for stage 3 to "0 credits. Checkpoint 3b is not affordable at the 2026-11-20 balance (N = 11 after stage 1). Stage 3 ends at the PROVISIONAL stored test unless James adds credits". Rewrite section 9 from 508 and 105 a day.

### A2. MAJOR. "One standard deviation of content score" is not a realistic meaningful edit. A typical rewrite will print "cannot tell"

Section: 2.7, 2.8 (last row), 2.9, 8 (cannot-tell rule).

Claim. Every edit magnitude James will read (2.8: "about 2 to 3 places from position 5, 4 to 6 from position 9, 6 to 10 from position 15") is for a change of one within-site standard deviation of the content score. That is the spread between different pages of one site for a search. Most of that spread comes from things a same-URL rewrite cannot change or usually does not change. A body rewrite that keeps the URL, title and H1 (the PR 45 kind of edit: "No new pages, no title or redirect changes") moves the score by a fraction of one standard deviation. At that size, the plan's own rules print "cannot tell".

Evidence.
- Label-free split of the frozen `page-blend` logistic part on its own training feature table (`evals/claude/r3/design/table.csv.gz`, 1,476 usable pages, 204 lists; no positions used). Share of the within-list variance of the score (covariance share): URL features (slug, is_home, depth, page type) 0.17. Title, H1, headings and meta 0.64. Body text and page shape 0.19. The body part alone has 0.08 of the variance, so its own spread is about 0.28 of one standard deviation of the total score. The two largest weights are `is_home` (+0.434) and `emb_title` (+0.406) (`evals/claude/r3/model/page-blend/logit.json`).
- The lead's own PR 45 call rule is plus or minus 0.10 list standard deviations (`r4-data-inventory.md` section 8, `r3_pr45_predict.py`). So the lead expects real edits near 0.1, not 1.
- If the chance to beat the old page comes from the scorer's pair calibration (correlation 0.62 to 0.69 with the within-site truth), it reaches 0.60 only at dm of 0.38 to 0.45 standard deviations. At dm 0.25 it is 0.56 to 0.57. At dm 0.1 it is 0.52 to 0.53 (normal calculation, my check).
- Expected places gained, `r1 = r0 * exp(-lambda * kappa_A * dm)`, lambda uniform on 0 to 1, kappa_A 0.3 to 0.75 (the plan's own PREDICTED range in G4): from position 14, dm 0.25 gives 0.5 to 1.2 places. From position 5, dm 0.25 gives 0.2 to 0.4 places. The one-day noise at 4 to 10 is about 1 place (plan section 1).

Fix. In 2.8 and 2.9 replace the one standard deviation row with a table by edit kind (edit size DERIVED from the feature split above, places SIMULATED): "Body rewrite, title and H1 kept (about 0.1 to 0.3 SD): under 1.2 places from 14, under 0.5 from 5, usually cannot tell. Title and H1 retargeted (up to about 1 SD): the current figures." Add to stage 4: "Task 4C.4: on the training feature table, report the distribution of dm for three virtual edit kinds (body only, title and H1 only, both), and the share of each kind that leaves cannot tell. Report it to James before G4." Add to section 0: "Most body-only rewrites will get cannot tell. The tool speaks mainly about title, H1 and topic retargeting."

### A3. MAJOR. The headline number "chance the edit beats leaving the page alone" has no defined source of uncertainty

Section: 0.1 item 5, 2.7, 2.9, 8 (output record, cannot-tell rule), 4C.2.

Claim. In the plan's formula `r1 = r0 * exp(-lambda * kappa_A * dm)`, lambda is between 0 and 1 and G4.4 requires kappa_A above 0. So the predicted change always has the sign of dm. Nothing in the formula can make an edit with positive dm worse. The memo computes the probability "with the same noise draws" (`r4-edit-simulator-design.md` 3, step 4). With shared draws, the edited page is never worse. Then `p_edit_better` only measures whether the shift is big enough to pass a rival, not whether Google agrees the edit is better. The scorer is wrong on 26% to 28% of same-site pairs against Google's settled order (plan 2.2), and that error is not in the number. The cannot-tell rule (0.40 to 0.60) then mixes "small" with "unsure". A coding agent cannot implement this field as written, and the natural implementation overstates confidence.

Evidence. Plan 8: "Cannot tell is set when `p_edit_better` is between 0.40 and 0.60". Plan 4C.2: "The band integrates over the prior." Neither text says where sign uncertainty comes from. Plan 2.9 example "64% chance". Also, the plan's word rule (IPCC, "likely" only at 66% or more) calls 0.60 to 0.66 "about as likely as not", yet the cannot-tell rule treats 0.60 to 0.66 as a call. So the example in 2.9 would be shown as a call and worded as a coin flip at the same time.

Fix. Add to 4C: "`p_edit_better` = calibrated P(Google orders the after text above the before text), read from the stage 1E reliability curve at the score gap dm, using held-out pairs only. Ties in position count as no change and are reported as their own share. `expected_change_places` and the band come from the lambda and kappa_A simulation. Cannot tell when `p_edit_better` is between 0.34 and 0.66, so a call always matches an IPCC word of likely or unlikely." Change the 2.9 example to say "illustrative, not a result" and use a value outside 0.34 to 0.66 or label it cannot tell.

### A4. MAJOR. James's real case (a client page at position 14, or outside the top 30) gets a flagged, untested or empty answer

Section: 8 (modes A to C, 4B, G4.2), 7 (3B.1), 0.1 item 1.

Claim. Walk through it. A client page sits at 14 and gets a meaningful body rewrite. Mode A needs "at least 3 pulls on at least 2 days" in the top 30. Only one stored day goes past position 10: Nov 16, one pull (`r4-data-inventory.md` 3: "No other pull goes beyond 10"). The collector pulls top 10 only (`collect_daily.sh` header: "top-10 SERPs"). So every anchor at 11 to 30 is a single pull from Nov 16, inside the core update, and it gets older every day. The movement band at 11 to 30 is "extrapolated" (3B.1). G4.2 needs 150 no-edit transitions per tier. Tier 11 to 30 has zero, because no second deep day exists. The rule "Cells with fewer rows are merged" then folds it into 4 to 10, so the band at 14 is never tested. With A2, the effect is "cannot tell". So the simulator prints: cannot tell, anchor flagged, band extrapolated. For a page outside the top 30 there is no anchor at all. Mode C depends on stage 3, whose unseen test cannot run (A1).

Evidence. Outside the top 30 is the client's common case. Search Console export on disk, last 3 months (`/workspace/seo-tracker/smartcrew/gsc-export/last3months/Queries.csv`): 429 queries. Average position 1 to 10: 86. Over 10: 343 (80%). Over 30: 231 (54%). 401 of the 429 queries are not among the 139 tracked construction searches, so no stored list exists for them at all. The `simulate-edit` interface does not say how it gets the current results page for a search that is not stored, or whether that costs credits.

Fix. Add to section 2.9 and section 0: "Today the tool answers well only for pages in the top 10 of the 30 searches the collector pulls three times a day. At 11 to 30 the anchor is one pull from Nov 16 and the band is untested. Outside the top 30 it gives tiers only, or cannot tell." Add to 4B: "Tier 11 to 30 is reported as UNTESTED until two deep days exist. It is never merged into 4 to 10 for G4.2." Add to the interface: "`simulate-edit` never pulls. For a search with no stored list it returns cannot tell with reason no_list." Make D5 (a daily page-two pull) a recommended decision, not optional, and say it is the only free way to test the band at 14.

### A5. MAJOR. The client's Search Console exports are on disk, free, and no task reads them

Section: 0.3 D2, 6 (inputs), 8 (4B, 4D).

Claim. D2's default is "Use the exports on disk only", but no stage, task or test reads them. They are the only data on the box with the client's positions past 30, and the only movement data past position 10.

Evidence. `/workspace/seo-tracker/smartcrew/gsc-export/` (last 3 months, last 28 days: Queries.csv 429 rows, Pages.csv 107 rows, each with clicks, impressions, CTR, average position) and `gsc-compare-2026-11-16/` (two 4-day windows, Nov 7 to 10 and Nov 12 to 15). 210 queries have a position in both windows: 50 at 1 to 10, 59 at 11 to 30, 101 at 31 to 100. 87 pages have a position in both windows. `r4-data-inventory.md` point 11 already says "No file in `scripts/claude/` reads them".

What each use adds, all free and local:
1. A flagged anchor for client pages at 11 to 100 (query-level average position). It fills the gap in A4.
2. The only real movement data past position 10. It can check the extrapolated 11 to 30 band (3B.1) before release.
3. A standing value for the client's own site, which is in neither Majestic Million nor Tranco (plan section 1).
Caveats: impression-weighted averages, query level not page level, one window crosses the core update start, and SearchPilot calls Search Console rank "not reliable enough" for tests (`r4-edit-simulator-design.md` 1). So use it as context and a check, never as a gate label.

Fix. Add task 4B.2: "`scripts/claude/r4_gsc.py` reads the on-disk Search Console exports read only. It gives (a) a GSC anchor for client queries with no stored top-30 row, flagged `anchor_source=gsc`, and (b) the window-to-window movement of the 59 queries at 11 to 30 and the 101 at 31 to 100, reported next to G4.2 as a check of the extrapolated band. Client rows dated after 2026-11-19 stay sealed under rule 2." Change D2's default text to "Use the exports on disk (task 4B.2)".

### A6. MAJOR. The plan never says what happens to GOAL.md Targets 1, 2 and 3, and it skips a free forward test that would score Target 1

Section: 0 (whole), 10, Appendix A.

Claim. GOAL.md's "Done when" is "All of targets 1, 2 and 3 pass on held-out data ... OR ... an honest final report". The plan's words "Target 1", "Target 2" and "Target 3" never appear (grep of `SPEC-MASTER-PLAN.md`). Its gates (G1 to G4) replace them without saying so. James cannot tell from the plan whether the goal brief is still the finish line. Also, the collector is pulling the 30 o30 searches three times a day right now (`pull20-a/b/c.log`). Those days are dated after the plan's design seal (Nov 19). New entrants and changed pages on them are exactly Target 1's rows, on held-out days, at zero extra credits. The plan uses later collector days only for the band backtest.

Evidence. GOAL.md Target 1: "predict its consensus position. Pass = within-3 accuracy beats the best of {domain prior, position 7, position 11} ... on at least two held-out days ... (use Nov 19 and later pulls as they arrive)". Plan rule 2 already keeps all fitting and design on data dated Nov 19 or earlier, so later collector days are unseen by every model. A1 shows the only unseen cross-site test (3b) cannot be afforded. So the collector days are the only unseen cross-site data the plan can get.

Fix. Add a section "0.4 What happens to the goal brief's targets" with this table, and add D8 asking James to confirm it:

| GOAL.md target | Status in this plan |
| --- | --- |
| Round 3 target (same-site pairs, lower bound above 0.65) | Kept as G1-GOAL, unchanged |
| Target 1, new or changed URL placement | Kept. Scored as Checkpoint 3c with the frozen stage 3 model on collector days dated after the freeze, o30 searches in XS-test-stored groups, client rows and PR 45 searches excluded until the PR 45 v2 ledger is frozen. Rows are URLs new to the top 10 or materially changed, since the collector stops at 10. Metric and baselines exactly as GOAL.md states them. 0 credits |
| Target 2, edit direction | Replaced by the live ledgers and rung 4. Not reachable as a held-out test before Dec 16. Say so |
| Target 3, order of movers | Retired at James's Nov 19 note ("do not spend time chasing tiny day-to-day edits"). Ask James to confirm |

Freeze the 3c split (which collector days, which searches) in stage 0, before any of those days is read.

### A7. MAJOR. The usable product (anchored mode) is scheduled last although it needs almost nothing from stages 2 and 3

Section: 0.2, 9 (Order), 8 (Inputs).

Claim. Section 0.1 says the anchored mode "does not need a site-strength model at all", and 0.2 says "Stage 4's anchored modes do not depend on stage 3 passing". Yet stage 4 starts only after stage 2 (about four working days) and stage 3 (about three days plus a pull). Mode A needs only: the frozen scorer (stage 1), the split and pair tables (2A.1, 2A.3), the list-invariant score (2A.4), the noise model (3B.1), and stage 4's own 4A to 4C. None of 2B to 2E, 3A, 3C or 3D is needed. With A1, stage 3 can only end at a PROVISIONAL stored test, so it adds less than planned.

Evidence. Plan 8 Inputs lists "the list-invariant score (stage 2), the position curve and noise model (stage 3), the rank model for pages with no anchor (stage 3, if it passed)". Only the last item is real stage 2 and 3 work, and it is for mode C only. Plan 9: "Stage 2 next (about four working days) ... Stage 3 (about three days plus the pull). Stage 4 (about one to two weeks to the release gate)".

Fix. Reorder section 9: "Stage 1. Then stage 2A (split, pair tables, list-invariant score, G2.0). Then 3B.1 (noise model). Then stage 4 for modes A and B, with G4 (Checkpoint 4A). James gets the anchored simulator here. Then stages 2B to 2E and 3, and mode C is switched on only if G3 passes (Checkpoint 4C)." This brings the first usable output about seven working days earlier and changes no gate.

### A8. MAJOR. Every stage 2 to 4 estimate is fitted inside a running core update, and the plan has no rule for what happens when it ends

Section: 3 rule 2, 6 (inputs), 7 (3B), 8 (4B, provenance), 0.1.

Claim. All design data is Nov 6 to Nov 19. The core update started Nov 10 and was not complete on Nov 20 (plan section 1). The one deep day (Nov 16), the site footprints (S1), the movement bands (4B), the noise model (3B.1) and kappa_A all come from inside the update, mixed with two pre-update days. A core update changes site-level standing, which is the very thing stage 2 estimates and the anchor carries. The plan guards only the ledger score dates ("never earlier than 7 days after Google marks the core update complete"). Product outputs and gates have no such rule, and the provenance field only prints the status.

Evidence. `r4-edit-simulator-design.md` section 3, LOCAL table: from Nov 6 (before the update) to Nov 16, only 0.31 of pages at 4 to 10 were within 1 place, 0.50 within 2, and 0.50 left the top 10. From Nov 16 to Nov 17 the same figures were 0.72, 0.82 and 0.24. The memo itself says "these are worst-case drift numbers. Re-measure after it completes." The plan does not carry that sentence into any task.

Fix. Add rule 15 to section 3: "Every stage 2 to 4 number is labeled 'measured during the November 2026 core update'. When Google marks the update complete, a refresh task reruns 3B.1, 4B and the S1 footprints on collector days dated at least 7 days after completion, before any simulator output is shown without the label 'during core update'. Gates already passed are not rerun or changed." Add to stage 2's single-signal table (2C), reported and not gated: "site turnover: share of each domain's top-10 rows on Nov 6 and 9 that it still holds on Nov 17 to 19, o30 only".

### A9. MAJOR. kappa_A, the one number that turns content into places, rests on about five sites and on positions where content order was measured reversed

Section: 2.7, 4C.1, G4.4.

Claim. kappa_A is fitted from "about 465" same-site pairs in ordinary top-30 lists, "43% from one site". Those pairs come almost all from one paged pull (Nov 16). The plan reports the estimate with and without the big site, but G4.4 gates only the pooled by-site bootstrap. With so few effective sites, a by-site bootstrap is too narrow, so G4.4 can pass on one site's behavior. Worse for James's position-14 case: the only direct check of content order with both pages at 11 to 30 went the wrong way.

Evidence. `evals/claude/r2/samesite/notes.md`: usable pairs by site: hcss.com 200, the client 52, trimble.com 52, youtube.com 38, agtek.com 26 (of 465). My arithmetic from those counts: sum of squared shares about 0.22, so about 4.5 effective sites. Same file, line 109: "8 text features, both URLs in positions 11 to 30: 29/79 (0.367) [0.260, 0.481]", mostly hcss.com 37 and youtube.com 12. It also says 162 of the 2,678 deep rows repeat a URL already listed higher. 834 of the 944 same-site pairs come from the deep lists.

Fix. Replace G4.4 with: "kappa_A by-site bootstrap 95% lower bound above 0 with all sites, and also with hcss.com left out, and also with the client's domain left out. Report the effective number of sites (1 / sum of squared site shares). If it is under 10, kappa_A is labeled PROVISIONAL in every output. Report kappa_A separately for pairs with both pages in 1 to 10 and both in 11 to 30. If the 11 to 30 estimate is not above 0, mode A gives direction only (no places) for anchors at 11 to 30." Add to section 0: "The places-per-edit scale comes from about five sites and one day of deep results."

### A10. MAJOR. Sections 0 and 2.9 will read to James as stronger than the evidence. The release gate never tests an edit effect against Google, and section 0 does not say so

Section: 0.1 items 1, 2 and 5, 0.2, 0.3, 2.9, G4.

Claim. Four sentences are likely to be over-read by a non-technical owner, and one risk is buried.
1. 0.1 item 1: "The cross-site gap is mostly solved by anchoring". Nothing has been solved: no edit has been scored (2.9 says so), lambda is unknown between 0 and 1, and anchors exist only for top-10 pages of the collector's 30 searches (A4).
2. 0.1 item 2: "Content decides little inside page one." James will read this as "editing a page-one page does nothing", which contradicts the product. The evidence is about ordering rival sites, not about editing one page.
3. 2.9: "This edit has a 64% chance ... about 1 place better ... positions 4 to 13" looks like a result. The source memo labeled the same example "illustrative only; nothing was run" (`r4-edit-simulator-design.md` 8). The plan dropped that label.
4. 0.2 column "PREDICTED chance of passing" (0.85, 0.6, 0.35, 0.7) are the lead's guesses, not derived. They look computed. Stage 3's 0.35 is moot after A1.
5. Buried risk. G4 is the release gate. G4.1 is a sanity suite, G4.2 is band coverage with no edit, G4.3 is internal consistency and same-site position backtests, G4.4 is kappa_A above 0. None compares a predicted edit effect with what Google did after an edit. The only free check of lambda (rung 4, Common Crawl versions) and the live ledgers are "Not gated, always reported". So a G4 pass releases an "expected change in places" whose size has never been checked against a real edit. Section 0 never says this.
6. Missing decision. James's Nov 19 note (GOAL.md, priority 3) asks to "combine the content scorer with a site-strength ... signal so we can run pairwise comparisons across all pages in a SERP and sort them into an approximate rank position". The plan makes anchoring the product and that combined model a fallback for pages with no anchor. That is a defensible change, but it is James's call, and no row in 0.3 asks him.

Evidence. Quotes above, from `SPEC-MASTER-PLAN.md` lines 25, 26, 215, 33 to 39 and 572 to 582. GOAL.md lines 58 to 62.

Fix. Replace item 1 with: "For a page that already ranks, we can skip modelling site strength by starting from its current position. Whether a content edit then moves it, and by how much, is not yet measured." Replace item 2 with: "Inside page one, the gap between rival sites is mostly site strength, so content alone cannot order rival sites. Editing one page can still move it, by an amount not yet measured." Prefix the 2.9 example with "Illustrative only, nothing was run:". Rename the 0.2 column "Lead's guess of passing (not computed)". Add to 0.1: "6. The release gate (G4) checks that the simulator is sane and its no-edit bands are honest. It does not check any predicted edit effect against Google. That first comes from rung 4 (one to two weeks) and the ledgers (Dec 16 at the earliest)." Add D9 to 0.3: "Accept anchoring as the main product, with the combined content plus site model used only for pages with no anchor? Default: yes, and stage 3 still runs."

### A11. MINOR. Free fields already stored in the Serper responses are unused, including the two that test hidden assumptions

Section: 6 (signal list S1 to S8), 2A.3, 4E (scoring rule).

Claim. The stored raw responses carry fields no task reads. Two of them test assumptions the plan makes silently. (a) The plan assumes the stored page text is what Google ranked on the label day (2A.3: "the stored fetch closest to the label day within 14 days"). Google's own title and snippet for that row are stored next to the position and can check it. (b) The plan assumes Google has reprocessed an edit by the ledger score date. The source memo asked for "a per-page reprocessing marker (the result-page title or snippet switching to the new text)" with rows before and after it reported apart (`r4-edit-simulator-design.md` 5). The plan's scoring rule in 4E dropped it. The collector stores title and snippet on every pull, so the marker costs nothing.

Evidence. Key names tallied read-only over all 1,532 rows of `serp_raw.raw_json` (key names only, no positions, URLs or text read): organic items carry `snippet` 13,913 of 14,670, `date` 4,916 (33.5%), `rating` and `ratingCount` 1,431 (9.8%), `sitelinks` 469 (3.2%). Top level: `relatedSearches` 1,029, `peopleAlsoAsk` 876 (both already used in 3D.1), `knowledgeGraph` 14. A grep of the plan finds no use of snippet, date, rating, sitelinks or a reprocessing marker.

Which omissions matter. Real losses: the snippet check and the reprocessing marker. Worth a slot but optional: sitelinks share on other search groups (Google's own sign of a strong navigational site; on the same search it restates the label, so leave-group-out only) and the `date` field as page age. Fine to leave out: `knowledgeGraph` (14 rows), `rating` (mostly review sites, already a site type).

Fix. Add to 2A.3: "A row's page text is flagged `text_mismatch` when no 8-word run of Google's stored snippet for that row appears in the stored main text. Report the mismatch share by day and site type in stage 2, and drop mismatched rows in a sensitivity line." Add to 4E's scoring rule: "Each edited row gets a reprocessing date: the first collector pull whose title or snippet for that URL contains text found only in the after version. Rows are reported before and after that date separately, and rows never reprocessed by the score date are reported apart and left out of the hit rate." Add S9 to the signal table: "share of other search groups where the domain's result shows sitelinks; leave-group-out".

### A12. MINOR. Two smaller hidden assumptions in the no-anchor path: the "best indexed page" rule, and known losers left out

Section: 7 (T-B), 2.4, 6 (inputs).

Claim. (a) T-B takes "the domain's indexed page with the highest content score for the search" as the page Google would rank. In a synthetic check with the scorer's within-site correlation (0.62 to 0.69), the top-scoring page of 10 is Google's top page only 0.37 to 0.42 of the time, and of 30 pages 0.25 to 0.30 (numpy, 200,000 draws, seed 20261120). The maximum of k noisy scores also rises with k (expected maximum of 10 standard normals about 1.54, of 30 about 2.04), so domains the old crawl went deep on get a higher candidate score for free. The local index is very uneven: readable pages per domain have median 4, 91 domains have none, 76 have more than 30 (`index.sqlite`, read only). (b) The plan cites known losers to explain the scale change (2.4) but no stage fits on them. Site-restricted lists of sites absent from that search's deep top 30 are the only labeled "beyond 30" pages, which T-B and the "beyond 20" tier need. train-a has up to 34 such (search, site) lists (180 lists, 146 in the deep top 30, `r4-data-inventory.md` 4; some of the 34 may be on searches with no deep list, so the true count is lower).

Fix. In T-B: "The candidate is the domain's top page in a stored site-restricted list for the search when one exists. Otherwise the best-scoring indexed page, with the log of the domain's indexed page count as a covariate in every model, baselines included." In 3A: "Add the pages of site-restricted lists whose site is absent from that search's deep top 30 as losers to every top-30 page, in a second fit reported next to the primary one."

## Unverified suspicions (not backed well enough to be findings)

1. The URL, title and body split in A2 covers the logistic half of `page-blend` only. The gradient-boosted half was not split. Its body share could be larger or smaller.
2. `bm25_body_norm` has a negative weight (-0.320) in the logistic half. Adding on-topic body text might lower the score in some cases. The direction tests of the sanity suite may or may not catch it. Not run.
3. Search Console average position past 30 rests on few impressions and may not match a Serper position. Whether a GSC anchor is usable was not tested.
4. The cross-site memo recommends "a two-term log-additive model with a query-type interaction" (`r4-cross-site-evidence.md` line 110). The step 2 model has no query-type term except the affinity flags. Whether content weight differs between brand, category and comparison searches was not checked.
5. Mode B (sibling anchor) has no gate of its own. G4.3's position backtest is the nearest test. Whether it covers new URLs that replace a sibling was not checked.
6. Brand signals from outside services (S4a, S4b) and RDAP age (S6) may add little for their privacy and time cost (the site-strength memo calls the brand effect "small"). They could move to a second pass. Not measured.
7. The stage 1E reliability curve is built on pairs of different pages. Whether it is calibrated for the before and after versions of one page is unknown. The fix in A3 uses it as the best available stand-in.

## What the plan gets right (checked)

1. Content weights are learned only from same-site pairs, with domain effects in the cross-site step. This matches the memo's simulation (cosine 0.996 against 0.74 for a joint fit, `r4-statistical-design.md` 3.3).
2. The middle guess is a baseline in every placement gate, and target-against-rival accuracy is not used as skill (the middle guess scores 0.72 to 0.75 on it, memo 4.1).
3. Stage 1 runs the round 3 preregistration unchanged, and the advance rule and fallbacks F1-A to F1-C are written before any held-out list exists.
4. Niche footprint is leave-group-out, with a planted-leak test.
5. Ties and missing values are counted apart, which is what overturned the old "authority points the wrong way" reading.

## Verdict

SAFE AFTER REQUIRED CHANGES. A1 must be settled today, before any stage 1 pull, or the shared balance falls under 250 tomorrow. A2, A3, A4 and A10 must be fixed so James knows that, as written, the tool will mostly say "cannot tell" for body rewrites and for pages past position 10, and that no gate checks an edit effect against Google.
