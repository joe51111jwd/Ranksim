# Adversarial review of SPEC-MASTER-PLAN.md: math, ceilings, gates

Reviewer: independent subagent, lens "wrong ceilings, wrong arithmetic, gates that are impossible or too easy". Read only. Zero Serper credits. No held-out list read. No model scored on real ranking data.

Status: complete. 12 findings: 2 BLOCKER, 6 MAJOR, 4 MINOR. Severity order below.

## Findings

### B1. BLOCKER. Credit arithmetic: at today's balance the fresh test (Checkpoint 3b) cannot run, and the stage 1 default breaches the 250 floor within a day

Section: 0.2, 0.3 (D1), 4 (task 0.4, G0), 7 (group 3D step 2), 9.

Plan text: "Balance is 617 (read 2026-11-20 09:04 ET). 367 can be spent." Section 9: "Stage 1 ... 208 | 159", "Stage 3b, fresh test, N = 48 by the formula ... 144 | 15". Section 0.2 lists stage 3 at "90 to 180" credits with "0.35 for all gates" on the fresh test. D1 default: "Run stage 1 at once."

Recomputed from 508 (lead's measured value for 11:00 ET):
- Spendable above the floor: 508 - 250 = 258.
- After stage 1 (208): balance 300, which is 50 above the floor.
- Group 3D formula: N = min(60, floor((300 - 250 - 15) / 3)) = floor(35 / 3) = 11. That is under 30, so 3b is "not run".
- The smallest 3b (N = 30) needs 90 credits, so a balance of at least 355 at its freeze. Stage 1 plus the smallest 3b needs 208 + 90 + 265 = 563 today. 508 is 55 short, even if the collector is paused this minute.
- The collector spends about 105 a day, not 90 (three pulls of 30 plus the 15-credit tracker). Its own reserve is 200 (`/workspace/ranksim-data/collect_daily.sh` line 13). After stage 1 the balance is 300, so the collector's next day takes it to about 200 to 195. That is under James's 250 floor.
- G0 checks only balance at least 468 before stage 1. At 508 it passes, so the plan would spend 208 and leave the floor exposed to the next collector run. Tomorrow the balance is about 403, under 468, so stage 1 must run today or never.

Why it matters: the stage table tells James that stage 3 has an unseen test with a 0.35 pass chance. With 508 there is no unseen test of the cross-site model at all. Stage 3 is the PROVISIONAL stored test only, and that test has its own size problem (finding M1). The D1 default also makes a hard-rule breach likely on the next collector day.

Fix (paste into the plan):
- Section 1 row "Serper balance": "508 (lead, 2026-11-20 11:00 ET). Floor 250. Stage 1 needs 208. The collector spends about 105 a day (90 for pulls, 15 for a tracker)."
- Section 9 table: "Now | | 258. Stage 1 | 208 | 50. Stage 3b | not affordable (N = 11 under 30) | 50."
- G0 and task 0.4: "Stage 1 may start only if D1 is answered with the collector paused, or with `RANKSIM_CREDIT_RESERVE=250` confirmed in its caller, and the balance is at least 468. If D1 is not answered today, stage 1 does not run, because tomorrow the balance is under 468."
- D1 default: "No pull. Stage 1 waits for D1."
- Section 0.2 stage 3 row: "Credits 0. Gate on Checkpoint 3a only (PROVISIONAL). No unseen cross-site test is affordable unless James adds credits from another free source (D3) or drops stage 1."

### B2. BLOCKER. G4.2 cannot be passed by a perfectly calibrated simulator

Section: 8, gate G4.2 and its fallback; Appendix A.

Plan text: "For each start tier (1 to 3, 4 to 10, 11 to 30) and each horizon with at least 150 rows: realized coverage of the 50%, 80% and 90% bands within 7 points of stated." Fallback: "Widen the bands ... No release before coverage holds."

Evidence 1, integer positions. Positions are whole numbers. `r4-edit-simulator-design.md` section 3 table: Nov 16 to 17, start band 1 to 3, "Same place 0.70". So the narrowest possible band (the anchor position alone) already covers about 0.70. No band of whole positions that contains the anchor position can cover 0.43 to 0.57, and a band that leaves out the most likely position is not a usable band. The 50% band in tier 1 to 3 at a one-day horizon fails by 13 points or more, whatever the model. Widening, the stated fallback, makes it worse. The tier 1 to 3 cell will reach 150 rows: about 3 rows per collector search, about 25 to 30 usable searches a day after the PR 45 and client exclusions, so two to three test days.

Evidence 2, chance failure. Simulation (numpy, seed 20261122, synthetic only): a band with exactly the stated coverage, 150 rows per cell. Independent rows: a cell fails one of its three bands 10.7% of the time (the 50% band alone 8.6%: sd 0.041, and 0.07 is 1.7 sd). Rows clustered 10 per search with search-level spread of error scale (lognormal sd 0.3 to 0.5): 23% to 35% per cell. With 3 cells the chance that a perfect band fails somewhere is 0.29 (independent) to 0.72 (clustered). With 6 cells, 0.49 to 0.92. At 400 rows per cell and independent rows it drops to 0.014 for 3 cells.

So the stated "Chance that all of G4 pass: about 0.7" is not coherent. Under this rule G4 is near certain to fail for reasons that say nothing about the simulator.

Fix (paste into G4.2 and Appendix A): "Coverage is scored with randomized boundary credit: a band [L, U] gets credit 1 inside, 0 outside, and the stated fractional weight at its edge positions, so a stated 50% band can be 50% on whole positions. Pass per cell: the stated rate lies inside the two-way cluster bootstrap 95% interval of realized coverage (search and day), and the realized coverage is at least stated minus 0.10. Cells need at least 400 rows and at least 40 searches; smaller cells are merged before scoring. Before freezing, run a calibrated synthetic band through the rule on the gate's own row layout. Its pass rate must be at least 0.90."

### M1. MAJOR. The stored test (Checkpoint 3a), now the only stage 3 test, usually fails its own validity rule

Section: 6 group 2A task 1 (split size check), 7 gate G3 "Validity", group 3C.

Plan text: split size check "XS-test-stored must hold at least 9 groups and at least 18 c100 searches with a deep list". G3: "Validity: at least 30 searches and 300 target rows" and "(applied to Checkpoint 3b; applied to 3a as PROVISIONAL)". T-A and T-C are defined on "its top 30".

Evidence: I counted, read only, which of the 100 Nov 16 deep-list searches fall in which search group of `evals/claude/r3/plans/universe.json` (no positions read). The 100 deep searches sit in 33 groups: sizes 35, 14, 7, 7, 5, 2, 2, 2, 2 and 24 singletons. The 35-search dirt work group goes to XS-design by rule. Simulating the 35% group draw over the other 62 construction groups (200,000 draws): deep searches in XS-test-stored average 22.7 (10th to 90th percentile 11 to 35). The size check passes in 68% of draws. Among draws that pass it, only 36% reach 30 deep searches. Rows: about 25 best-position rows per deep search, about 72% readable, roughly three quarters non-platform, so about 13 to 14 target rows per search, about 300 to 310 rows at 23 searches. Both validity bars are marginal or failed.

The plan never says whether the top-10-only lists (the 35 PR 41 baseline searches and 4 ClockShark searches) count toward the 30 searches. If they do, T-A mixes a 10-deep middle guess (MAE 2.5) with a 30-deep one (MAE 7.5) in one mean.

Since 3b is not affordable (B1), stage 3 most likely ends with an invalid test.

Fix (paste into 2A task 1 and G3): "Size check: XS-test-stored must hold at least 30 c100 searches with a deep list, at least 12 groups and at least 300 readable non-platform top-30 rows; try seeds 20261120, 20261121 and so on until it holds, recording every seed. T-A and T-C use deep-list searches only; top-10-only searches are a separate, reported slice. If no seed among the first 20 meets the check at a 35% share, use a 45% share (P of at least 30 deep searches rises from 0.24 to 0.48 per draw)." Also: "Before the 3a freeze, compute each G3 gate's power at the PREDICTED effect for the actual 3a size (group 3D step 6 applies to 3a too)."

### M2. MAJOR. The kappa_A estimator is biased down by a factor of about 0.4 to 0.8 and barely responds to the true value; the log-rank ratio is not constant at the top or past 20

Section: 2.7, 8 group 4C task 1, G4.4, the PREDICTED line of stage 4.

Plan text: "one standard deviation of content score moved a page to 0.47 to 0.61 times its position number, from any start". "`kappa_A` ... SIMULATED range 0.5 to 0.75. It is estimated in stage 4 from same-site pairs in ordinary top-30 lists". "PREDICTED ... `kappa_A` 0.3 to 0.75 (lower than the simulated range, because lists cut at 30 pull the estimate down)". "`lambda`: prior uniform on 0 to 1."

Evidence 1, the table it cites. `r4-statistical-design.md` 5.3 gives new position over start: R = 3 N = 100: 2.77/5 = 0.55, 5.12/9 = 0.57, 9.0/15 = 0.60. R = 3 N = 1000: 0.48, 0.46, 0.46. So 0.46 to 0.60 holds for R = 3 only. Over R = 1 to 10 the same table gives 0.34 to 0.73, so kappa_A = -ln(ratio) spans 0.31 to 1.08.

Evidence 2, start position. My rerun of sim 5b (seed 20261125, starts 1 to 50): position 1 cannot move (ratio 1.00). Start 2: 0.64 to 0.67. Start 3: 0.54 to 0.60. With N = 100, R = 3 the mean log-rank change is 0.47 at start 2, 0.68 at 5, 0.54 at 15, 0.41 at 30 and 0.29 at 50. So "from any start" is false. The formula r1 = r0 exp(-kappa dm) gives positions under 1 for latent anchors near the top and overstates moves at 1 to 3 and past about 20 when the pool is small.

Evidence 3, the estimator (simulation, seed 20261126, 5 pages per site, content score corr 0.62 with the within-site part). Target: the mean log-rank change of a +1 sd edit over starts 4 to 30. Plan's estimator: OLS of log-rank gap on score gap over same-site pairs both in the top 30.

| World | Target kappa_A | Estimator | Ratio | With host crowding (at most 2 per site in top 10) |
| --- | --- | --- | --- | --- |
| N 100, R 3 | 0.52 | 0.40 | 0.77 | 0.45 |
| N 1000, R 3 | 0.80 | 0.43 | 0.54 | 0.46 |
| N 1000, R 1 | 1.09 | 0.40 | 0.37 | 0.41 |
| N 1000, R 10 | 0.52 | 0.39 | 0.75 | 0.45 |

The estimate sits near 0.40 to 0.46 whatever the truth. Deeper windows barely help: N 1000, R 3 gives 0.35, 0.42, 0.45, 0.47 for windows of 10, 30, 60, 120. The cause is selection on the outcome, not only the cut at 30: inside the selected set the true within-site spread is squeezed while the score's own noise is not, so the slope shrinks (a truncated regression).

Why it matters: the simulator would show moves about half of the "SIMULATED upper figure" in 2.8, and the number would not tell James which world he is in. A ledger-learned lambda cannot repair it, because the prior caps lambda at 1, while the needed correction is a factor of 1.3 to 2.7. G4.4 (lower bound above 0) checks only the sign.

Fix (paste into 4C task 1, 2.7 and G4.4): "kappa_A is estimated with a selection-aware fit: the top-k Plackett-Luce likelihood with a per-search rest-of-field term (`r4-statistical-design.md` 2.4 item 3), on ordinary lists with the frozen list-invariant score. The OLS slope is reported next to it as a lower figure. Before use, the estimator must recover a known kappa_A within 20% on the sim 5b world for R = 1, 3 and 10 and N = 100 and 1000 (`tests/test_editsim_effect.py`). The edit is applied on the latent scale and mapped back through the fitted position curve, never as r0 times a ratio; positions 1 to 3 use the curve, and the output is clipped at 1. Positions past 30 return the tier 'beyond 30' only. The lambda prior is uniform on 0 to 1 on the corrected kappa_A."

### M3. MAJOR. G2.2's absolute bar is already met by one free signal, so the gate rests on an unintervalled point difference against a winner-picked comparator

Section: 6, G2.2, G2.5, the PREDICTED line; rule 6 of section 3.

Plan text: G2.2 "dyadic 95% lower bound above 0.60, and point estimate at least 0.02 above the best single signal of group 2C on the same pairs". G2.5 "every single signal and a within-search label shuffle run through G2.2: none passes". "PREDICTED ... (the best single signal is expected near 0.63) ... The main risk is that the niche footprint adds less than 0.02 over the best single signal."

Evidence:
- `r4-free-label-sources.md` section 6: Majestic alone on boundary pairs is 0.633 [0.607, 0.659] over 100 searches. That lower bound is already above 0.60.
- That figure counts ties as one half and absent domains as 0. G2.2 scores decided pairs. With a tie share t, decided accuracy is (0.633 - 0.5 t) / (1 - t). Small-site row shares in the same memo (22% at 1 to 10, about 36% at 11 to 30) put t near 0.08, so Majestic on decided pairs is about 0.645, lower bound about 0.62 at 100 searches and about 0.61 at the 60 to 65 XS-design searches (half-width times sqrt(100/65), plus about 12% for dyadic over search-only, memo 6.3). The comparator the plan predicts (0.63) is on a different metric from the gate.
- So under G2.5 one of two things happens. If "passes G2.2" means the full rule, no single signal can pass, because no signal is 0.02 above itself. The check is empty. If it means the absolute bar, Majestic very likely passes, and rule 6 forces a gate change at freeze time that the plan has not written.
- What remains is "point at least 0.02 above the best single signal": no interval, and "best" is the maximum of about 20 signals measured on the same pairs, which biases the bar upward. The footprint (S1a to S1c) is itself a group 2C single signal, so the "main risk" sentence is self-contradictory: the model must beat the footprint itself by 0.02.

Fix (paste into G2.2 and G2.5): "G2.2: on decided boundary pairs, the site-only model minus the best single signal chosen on XS-design folds other than the scored fold (nested, so the choice is out of sample): dyadic 95% lower bound of the paired difference above 0, and point difference at least 0.02. Absolute bar: dyadic lower bound above 0.62. Every single signal is scored on decided pairs with missing values left missing, ties reported. G2.5: each single signal, the footprint alone, and a within-search shuffle are scored against the absolute bar of G2.2. If any single signal clears 0.62, the absolute bar becomes that signal's point estimate on XS-design, written into gates.json before the freeze." PREDICTED line: "best single signal (likely the footprint) expected 0.64 to 0.68 on decided pairs."

### M4. MAJOR. An always "cannot tell" simulator passes G4.1, and G4.3's direction bar has no baseline

Section: 8, G4.1, G4.3, the "Cannot tell" rule; rule 5 of section 3.

Plan text: G4.1 "Invariance cases: ... 'no change' or 'cannot tell' in at least 90%. Direction cases: right sign or 'cannot tell' in at least 90%, wrong sign in under 5%. Stuffing capped and flagged in 100%". G4.3 "Position backtest on held-out groups: direction accuracy at least 0.60 with a by-site lower bound above 0.5". Rule 5: "A gate needs an absolute bar and a paired difference against the best baseline."

Evidence:
- A simulator that answers "cannot tell" for every edit, with a paired effect of 0, has invariance 100% and median effect 0, direction "right or cannot tell" 100% and wrong sign 0%. The stuffing guard is a separate rule and can flag on its own. So G4.1 passes with no predictive content. Nothing in G4 sets a minimum share of calls. The plan's own "cannot tell" rule (p_edit_better between 0.40 and 0.60, or effect under the noise floor) with lambda uniform on 0 to 1 pushes many real edits into that state.
- G4.3's direction call is the sign of the score gap, so it tests the scorer, not the simulator. BM25F already gets 277 of 464 (0.597) on the same kind of pairs, same-site pairs in ordinary top-30 lists (`RESULTS-claude.md` 4.1). The 0.60 bar sits 0.003 above that and has no paired comparison.
- The product goal is an approximate rank after a meaningful change. A release gate that a silent simulator passes does not test that goal.

Fix (paste into G4.1, G4.3 and Appendix A): "G4.1 direction cases: right sign in at least 75%, cannot tell in at most 20%, wrong sign under 5%. G4.1b, new: on held-out pages with a material edit (rung 3 virtual edits that pass the material-change rule), the simulator makes a call (not cannot tell) on at least 50%, and the share is reported by mode. G4.3 position backtest: direction accuracy at least 0.60 and the paired difference against BM25F has a by-site 95% lower bound above 0; report it with and without the site that holds 43% of the pairs."

### M5. MAJOR. G3 passes a model whose content term adds nothing, and G3.1 leaves out the strongest simple baseline

Section: 7, G3.1, G3.5, Appendix C "Raw domain prior"; rule 5 of section 3.

Plan text: G3.1 "paired difference ... above 0 against the middle guess and against the raw domain prior". Appendix C: "Raw domain prior: the unshrunk mean position of the page's site on other search groups". G3.5 "Content matters across sites: `kappa` from the XS-design fit has a search-group bootstrap 95% lower bound above 0, and on the test rows the combined model's T-A error is not worse than the site-only model's by more than 0.10 places".

Evidence:
- The plan's own lesson in 2.6: "at our accuracy an unshrunk placement is worse than guessing the middle". `r4-statistical-design.md` 6.5 item 2 defines the baseline as "the site's shrunk mean position in other searches". The plan swapped in the unshrunk version, the weaker one.
- The best simple baseline for T-A is the stage 2 site-only model (or the footprint), which G3.1 does not include. A site-only model with no content term can therefore pass G3.1.
- G3.5's first clause is fitted on XS-design, so it is in-sample. Its test-set clause is non-inferiority: a combined model whose content term does nothing has a paired difference near 0 and passes "not worse by more than 0.10 places" most of the time. No G3 gate checks on held-out rows that content helps across sites, yet the gate is titled "Content matters across sites".
- Size of what content could add: `r4-statistical-design.md` sim 1b (R = 3, N = 100), a site estimate correlated 0.7 gives 0.579 alone (sim 1, site proxy column) and 0.600 with content. About +0.02 in pair accuracy, roughly 2% to 3% of MAE (table 2.6). At about 300 rows that is not detectable, so the honest claim is "not tested", not "content matters".

Fix (paste into G3.1, G3.5 and Appendix C): "Raw domain prior is replaced by the shrunk domain prior (posterior mean of the site's position on other groups, shrunk toward the middle with tau from XS-design). The model scored in G3.1 is whichever of the combined model and the site-only model has the lower leave-group-out T-A error on XS-design, chosen and written into the preregistration before the freeze. Its baselines stay the middle guess and the shrunk domain prior. G3.5 is renamed 'No harm from content' and keeps its non-inferiority clause. A new reported line, not a gate: site-only minus combined T-A error with its two-way interval. The product may say content changes cross-site placement only if that lower bound is above 0."

### M6. MAJOR. The headline ceilings are quoted for R = 3 only, against the true order, and narrower than the memo they cite; R itself is barely pinned down

Section: 0.1 item 4, 2.2, 2.8 rows 4 to 7.

Plan text: 0.1: "even a perfect site value plus our content scorer reaches 0.66 to 0.71". 2.2: "`R is about 3` (plausible range 1 to 10). So among candidate pages the site part holds about 75% of the score variance, the content score about 10% to 12%". 2.8: "content plus a perfect site value | 0.66 to 0.71 | SIMULATED" with no R stated; "Any model ... at most about 0.69".

Evidence:
- `r4-statistical-design.md` sim 1b, r_s = 1.0: R = 1 gives 0.657 and 0.622; R = 3 gives 0.707 and 0.664; R = 10 gives 0.775 and 0.732. The memo's own summary (point 2) says "0.62 to 0.78". The plan kept only the R = 3 cells and wrote "even", which reads as an upper bound. The 0.58 to 0.60 row is also R = 3 only (0.577 to 0.600 over all R at r_s = 0.7).
- R's uncertainty. Linear form 1 + R = (0.597 - 0.5) / (0.522 - 0.5) = 4.41, R = 3.4. Same-site sd about 0.025 (interval [0.548, 0.647], `RESULTS-claude.md` 4.1). Cross-site 8657 of 16577; the 8-feature vote on the same pairs has [0.499, 0.535], so sd about 0.009 (`evals/claude/r2/samesite/notes.md`). A normal bootstrap of both (10^6 draws) gives R percentiles 2.5%: 0.77, 50%: 3.4, 97.5%: 22.2; 0.7% of draws have a_cross at or under 0.5 (R unbounded). P(1 to 10) is 0.88. So the site share runs from about 44% to 96%, and content (rho squared 0.40 to 0.47 over 1 + R) from about 2% to 27%, before the linear form's own bias (sim 1: true R = 1 reads 1.3 to 1.7).
- The "at most about 0.69" row comes from `r4-cross-site-evidence.md` 5.2 with N = 300 candidates. My rerun (seed 7, hidden share 20%, true order): N = 30 gives 0.744, N = 100 0.704, N = 300 0.675, N = 1000 0.663. It is a pool-size-dependent figure, not a bound for "any model".
- All of these are against the true order. Against one pull the memo says subtract 0.01 to 0.03.

Fix (paste into 0.1 item 4 and 2.8): "SIMULATED, against the true order: inside a top 10, content plus a perfect site value reaches 0.62 to 0.78 depending on R (0.66 to 0.71 at R = 3). Against a single pull subtract 0.01 to 0.03." In 2.2: "R is about 3 (95% range about 0.8 to 22 from sampling alone), so the site part holds about 45% to 95% of the candidate score variance (75% at R = 3)." In 2.8: "Any model ... at most about 0.66 to 0.74 depending on the candidate pool (0.69 at 300 candidates)." Every SIMULATED row states R and N.

### m1. MINOR. Label-noise numbers are mislabeled or overstated, and the "90% is above the label ceiling" sentence is false

Section: 0.1 item 4, 2.2, 2.8 rows 1 and 3, the sentence after the 2.8 table.

Plan text: "Two pulls of the same search agree on 0.87 to 0.93 of pairs." 2.8: "Pair agreement of two pulls (the label ceiling) | 0.87 to 0.93 | REAL". 2.2: "Corrected, the scorer is right on 0.72 to 0.74 of pairs ... 40% to 47%". After 2.8: "[90%] is above the label ceiling of most pairs".

Evidence:
- REAL two-pull agreement on all pairs is 0.870 (ordinary) and 0.877 (site-restricted) (`RESULTS-claude.md` 5.1). 0.932 is the subset five or more places apart. The 0.91 to 0.93 figure is DERIVED (one pull against the latent order). The row mixes a REAL all-pairs number with a subset number and calls the range REAL.
- The ceiling a model faces is one pull against the truth: p with p^2 + (1 - p)^2 = 0.870 gives p = 0.930; for adjacent pairs 0.839 gives 0.912; model B gives 0.913; the jump mixture 0.938. So 0.90 is below the single-pull ceiling for every gap, including adjacent pairs. The true statement is that 0.90 leaves only 0.01 to 0.04 of room under the ceiling.
- The 0.74 upper end uses model A (independent flips). The only REAL check is in `evals/claude/theory.md` R3.3: on 20 twice-pulled training lists the blend goes from 0.672 (single pull) to 0.686 (consensus), and from 0.668 to 0.675. Model A predicts at least +0.024 (true 0.698, consensus label error 0.5%), before counting that consensus pairs are an easier subset. The measured +0.007 to +0.014 fits model B (+0.010), not model A.

Fix: 2.8 row 1: "Two pulls agree on 0.870 to 0.877 of all pairs (REAL); one pull against the latent order 0.91 to 0.94 (DERIVED)". 2.2 and 2.8 row 3: "about 0.72 (model B, the one the twice-pulled lists support), rho about 0.63, about 40% explained; 0.74 under independent flips is an upper figure". After 2.8: "The 90% target is 0.01 to 0.04 under the single-pull ceiling. Reaching it needs near-perfect knowledge of Google's latent order, which no history-free method has shown."

### m2. MINOR. The stage 3 placement prediction does not follow from its own pair-accuracy prediction

Section: 7, PREDICTED line; G3.1.

Plan text: "T-C all pairs 0.58 to 0.64 ... T-A error 10% to 18% below the middle guess". G3.1 needs "at least 8% below the middle guess".

Evidence: table 2.6 (n = 30, posterior median, true order) gives 6.75 at a = 0.60 (10% below 7.5) and 6.26 at 0.65 (16.5%). My simulation against one pull (jump mixture q = 0.10, seed 20261120): a = 0.58 gives 6.4% to 7.1%, 0.60 gives 7.9% to 8.8%, 0.62 gives 10.2% to 11.1%, 0.65 gives 13.3% to 16.0%. So T-C 0.58 to 0.64 implies T-A about 6% to 15%, not 10% to 18%, and the 8% bar sits in the middle of the predicted range. Power of the 8% point bar (simulation, seed 20261121, 15 rows per search, per-search accuracy sd 0.05): at 30 searches 0.42 at a = 0.58, 0.58 at 0.60, 0.87 at 0.62; the lower-bound part against the middle guess is 0.91 or more in all of these. The 8% point bar, not the interval, decides G3.1.

Fix: "PREDICTED: T-A error 6% to 15% below the middle guess (from T-C 0.58 to 0.64 through table 2.6, against one pull). G3.1 passes against the middle guess with probability about 0.4 to 0.9; it needs pair accuracy of about 0.60 or more." Drop "on the fresh test" from the 0.35, since 3b is not affordable (B1).

### m3. MINOR. "5 to 10 appearances pin a domain effect to 0.3 to 0.4 logits" uses the wrong information figure for our lists

Section: 2.5.

Plan text: "SIMULATED: a domain needs about 5 to 10 listed appearances to pin its effect to plus or minus 0.3 to 0.4 logits."

Evidence: the source, `r4-rank-aggregation.md` line 110, gives about 0.71 units of Fisher information per appearance in "a fully ordered list of 10 equal pages", and only about 0.12 for a listed page "in a top 10 drawn from a pool of 50". With a prior sd of 1, the posterior sd is 1 / sqrt(1 + k I). At I = 0.71: k = 5 gives 0.47, k = 10 gives 0.35, sd 0.30 needs about 14. At I = 0.12, which is our case (Google's top 10 or 30 is selected from a pool): k = 10 gives 0.67, sd 0.40 needs about 44 appearances, sd 0.30 about 85. In c100 only 31 of 428 domains appear on more than 10 searches. Also, "0.3 to 0.4" is a posterior sd, not a plus or minus range.

Fix: "SIMULATED: with selected lists (about 0.12 units of information per listed appearance), a domain needs about 40 or more appearances for a posterior sd of 0.4 logits; with full ordered lists about 7 to 14. Almost every domain in our data rests mainly on its features."

### m4. MINOR. G2.3 has no baseline comparison and is not covered by the dominance check

Section: 6, G2.3, G2.5; rule 5 of section 3.

Plan text: G2.3 "point at least 0.58, dyadic lower bound above 0.52". G2.5 runs baselines "through G2.2" only. Rule 5: "A gate needs an absolute bar and a paired difference against the best baseline."

Evidence: on small-site boundary pairs (both domains absent from Majestic and Tranco), Majestic and Tranco tie by construction, so the only live single signals are the footprint, focus and sitemap ones. A footprint-only score can pass G2.3 by itself, and G2.5 never tests that. Size: small-site rows are about 22% of top-10 rows and about 36% of rows at 11 to 30 (`r4-free-label-sources.md` section 7), so roughly 8% of boundary pairs (about 550 on XS-design) qualify. With heavy domain sharing the dyadic half-width will be about 0.05, so "point at least 0.58" and "lower bound above 0.52" are nearly the same condition.

Fix: "G2.3: point at least 0.58, dyadic lower bound above 0.52, and the paired difference against the footprint-only score has a point estimate above 0 (reported with its interval). G2.5 also runs every single signal through G2.3's absolute bar."

## Unverified suspicions (not backed well enough to be findings)

1. The cross-site BM25F 0.522 includes many pairs at positions 11 to 30, where round 2 saw BM25F reverse (mostly one site). The same-site 0.597 has a different position mix. So the linear R estimate may be biased in a direction I could not pin down without scoring real data.
2. G1-ADV's 0.85 may be optimistic. On the 20 twice-pulled training lists the blend scored 0.675 to 0.686 on consensus pairs, below the 0.704 cross-validation figure the 0.69 prediction starts from. If the held-out center is nearer 0.66, the pass chance is nearer 0.6. Those 20 lists may simply be harder.
3. The band backtest sets widths on transitions ending by Nov 17 and tests on Nov 18 and later, inside the core update. Drift between those windows (the movement table changes a lot between 1-day and 10-day windows) may fail G4.2 even after the B2 fix.
4. The size of the kappa_A bias in real data depends on how many pages per site compete. My worlds give 11 to 39 same-site pairs per list against about 4.6 in the real 465 pairs. The direction is robust in every world I ran. The factor is not pinned.
5. The dyadic interval for boundary pairs (about 415 pairs per top-30 list, one domain in many pairs) has not been coverage-checked on that layout. Rule 8 requires it before the freeze, which is right, but the G2.2 and G3.3 power numbers in the plan come from a top-10 layout with 28 pairs per search.
6. My read-only count finds 66 of the 100 deep lists with a maximum position of 30 or more, not 70. Probably short page-3 responses. Not material.

## Checked and correct

- Table 2.6 matches `r4-statistical-design.md` 4.2 cell for cell. The win-count bias formula (3.7 at n = 10, a = 0.7) and the Diaconis and Graham bounds are right.
- G0's 468 (250 + 208 + 10) and the section 9 arithmetic are internally right from 617. Only the input balance and the collector rate are stale.
- G1-ADV's 0.85 is coherent with the preregistered prediction (0.69, half-width about 0.04): the 0.65 point bar binds, and P is about 0.85 to 0.87 under that prediction.
- G3.2's window is wide enough: a perfectly calibrated band fails it by chance 0.1% to 7.5% of the time at 300 to 450 clustered rows (my simulation).
- The section 1 values match their sources (RESULTS 5.1, 4.1, 4.3; theory R3.3; MISSION; inventory counts 256, 58, 31, 242 and 244; the recount table), apart from the items above.

## Verdict

SAFE AFTER REQUIRED CHANGES. Two blockers (B1 credits from 508, B2 the impossible G4.2) and six major gate or number problems (M1 to M6) must be fixed in the text before any stage is handed over. None of the fixes needs new data or credits.

Simulation scripts used for this review were kept outside the repo (in /tmp) and use numpy only on synthetic data, plus read-only counts of search-group membership. No model or signal was scored on real ranking data.
