# Review of SPEC-MASTER-PLAN.md: red team, fake-pass and drift lens

Reviewer: independent adversarial subagent, 2026-11-20. Read only. No Serper request. No held-out list read. No model fitted or scored on real ranking data.

Lens: a coding agent that wants every gate to print PASS with the least real work, and an honest agent that must guess where the text is vague.

Status: complete. 12 findings: 3 BLOCKER, 9 MAJOR.

## Findings

### FP1. BLOCKER. Stage 0 gate machinery certifies itself. A typed or re-frozen number prints PASS.

Section: 4 (Stage 0), tasks 0.2 and 0.3, G0; rule 1; GOAL.md "Team".

Claim. `r4_gate.py` "reads a result file and gates.json, prints each gate with its value and PASS or FAIL". The plan never says which script writes each result file, what is in it, or that anything is recomputed. The agent that wants a PASS writes both the result file and the gate script. Nothing ties a PASS to a commit, a command, or the input data. And a re-frozen gates.json still verifies.

Evidence.
- `scripts/claude/freeze.py` `write_manifest` overwrites `manifest.json` with no check. `verify_manifest` only compares the current files with the current manifest. `scripts/claude/r2_score.py` `require_frozen_dir` adds "committed and pushed", but a second freeze commit that is pushed also passes. So "`freeze.py verify evals/preregistered/claude-r4-gates` passes" (G0) holds after any edit plus `freeze.py write` plus push.
- The plan's test for the gate script only asks that it "fails closed on a missing field, reads thresholds only from gates.json, and refuses an unverified manifest". None of these stops a changed gates.json or a hand-made result file.
- Round 3 had a real guard for this (`r3_score.py` `check_chunks`: each chunk has exactly one commit and descends from the freeze commit). The plan copies nothing like it for gates.json.
- GOAL.md: "a separate reviewer subagent to audit every claimed win for leakage before you accept it". The plan has no audit step before a PASS is logged (a search of the plan for "reviewer" and "audit" finds nothing).

Fix. Add to task 0.2 and 0.3:
"0.2b. Record the gate freeze commit hash in `RESULTS-claude.md` and in `evals/preregistered/claude-r4-gates/FREEZE_COMMIT`. `r4_gate.py` refuses unless `git log --format=%H -- evals/preregistered/claude-r4-gates/gates.json` returns exactly that one commit (later versions follow FP7).
0.3b. Each checkpoint has one named producer script (c1 `r3_score.py`, c2 `r4_stage2_eval.py gate`, c3a and c3b `r4_stage3_score.py`, c4 `r4_editsim_gate.py`). Its result file must hold: `git rev-parse HEAD`, a clean-tree flag, the full command line, the sha256 of every input file and of the row-level file (pairs.csv or rows.csv), and the run start and end time. `r4_gate.py` refuses a result file whose commit is dirty or whose row file hash differs. It recomputes every gated number from the row-level file with the frozen `r4_stats.py` and refuses if any recomputed value differs from the stored one by more than 1e-9.
0.3c. No PASS is logged in `RESULTS-claude.md` until a reviewer subagent that did not write the stage code has rerun `r4_gate.py` on a clean checkout of the pushed commit and written `evals/claude/r4/audits/<checkpoint>.md` with a leakage check. The log entry links that file."

### FP2. BLOCKER. Gates scored on design data (G2, G4) have no freeze and no run count. They can be rerun until they pass.

Section: rule 1, rule 13, 6 (Stage 2 outputs and group 2E), 8 (Stage 4), 7 group 3C.

Claim. Rule 1 says each test freezes "split, metric, threshold, code and model" in `evals/preregistered/claude-<name>/` and "a test is scored once". Stage 3 has such a folder. Stage 2 and stage 4 do not. Their gates run on XS-design (nested cross-validation) and on agent-built suites, on the same data the agent develops on. Nothing records how many times `r4_stage2_eval.py gate` or the stage 4 rungs ran, or what changed between runs. Rule 13 ("at most one redesign loop", "change the theory, not tune a knob") has no mechanism.

Evidence.
- Section 6 Outputs list `evals/claude/r4/stage2/{tables.md, results.json}` and no preregistration folder. Section 8 lists none either. Appendix B lists no `claude-r4-site` or `claude-r4-editsim` folder.
- Group 2D leaves knobs open: "a small fixed ridge on the other weights" has no number. Signal transforms (log or rank, caps), the missing-value encoding, and the S3c page cap are not fixed. Each is a free knob that moves G2.2 on the very pairs it is scored on.
- Stage 4 fallback "G4.1 invariance under 90%: Tighten the guards" is a knob loop on the gate's own cases, which rule 13 forbids.
- The stage 3 guard test says scoring refuses "when a result file exists". The round 3 guard (`r3_score.py` `check_no_result`) also checks the pushed branch. This box can be restored to an earlier disk state while pushes survive (project memory note), so a local-only check can be undone by a restore.

Fix. Add to section 3, rule 1:
"Design-data gates (G2, G4.1 to G4.4) are also frozen. Before the first run of `r4_stage2_eval.py gate`, freeze `evals/preregistered/claude-r4-site/` (all stage 2 code, the signal table, every hyperparameter with its value, including the ridge on non-domain weights, fixed at 1.0 on standardised signals). Before the first G4 run, freeze `evals/preregistered/claude-r4-editsim/` (simulator code, guard thresholds, kappa_A choice, sanity cases, see FP3). Every run of a gate command appends one line to `evals/claude/r4/gate_runs.jsonl` (checkpoint, commit, time, result hash) and is committed and pushed at once. `r4_gate.py` refuses a checkpoint that has more than one run line, unless the second is marked `redesign_loop: 1` and points to a theory note committed before the rerun. A third run is refused. Every guard that checks for an existing result checks the pushed branch, as `r3_score.py` does."

### FP3. BLOCKER. G4.1 passes with a simulator that always says "cannot tell", and the guards are built for the very edits the suite tests.

Section: 8, G4.1, group 4D rung 0, Rules under "Interface", Fallbacks ("Tighten the guards").

Claim. The bars count "cannot tell" as a success on both sides. A simulator that answers "cannot tell" to every input scores invariance 100%, direction "right sign or cannot tell" 100%, wrong sign 0%, and median paired effect 0. A guard that flags every edit passes "stuffing capped and flagged in 100%". Separately, the suite's invariance edit types (date-only, duplication, small stuffing, small appended text) are the same edits the guards and the material-change rule are written to catch. So invariance passes by construction and says nothing about the content scorer.

Evidence.
- G4.1 text: "'no change' or 'cannot tell' in at least 90%", "right sign or 'cannot tell' in at least 90%, wrong sign in under 5%".
- "Cannot tell" is set when "the edit is under the frozen material-change rule". `scripts/claude/t2_changes.py` `MATERIAL_RULE`: digit runs are masked to '#', so a date-only edit is never material. An edited token is one "not covered by any word 5-gram present in the other version", so duplicated text adds no edited tokens and is never material. Any appended text or stuffing under 250 tokens or under 2% is never material. All of these become "cannot tell" before the scorer is asked.
- `tests/test_editsim_guards.py` checks that "stuffing, duplication, irrelevant text and date-only edits trigger their guards": the agent writes guards for exactly the suite's categories.
- Nothing says who builds the cases, from which pages, or how many. "Held-out pages" is undefined.

Fix. Replace G4.1 with:
"G4.1. Cases are generated by a frozen script from 200 readable pages drawn by seeded hash (seed 20261120) from the stage 1 held-out lists (design data once stage 1 is scored; test2 sites stay untouched), frozen in `claude-r4-editsim` before any guard is tuned. Half the pages are a dev set for guard design. G4.1 is scored once on the other half. Invariance cases are made at or above the material-change rule (at least 300 appended or swapped tokens and at least 3% of the body), so the materiality rule cannot absorb them. Bars: invariance, median absolute paired effect under 0.25 places and a 'no change' or 'cannot tell' rate of at least 90%. Direction cases: called (not 'cannot tell') on at least 70%, right sign on at least 85% of called cases, wrong sign under 5% of all. Guards: stuffing flagged on 100% of stuffing cases and false-flag rate on the clean direction cases under 5%. Report the 'cannot tell' share by reason. The fallback 'tighten the guards' may use the dev half only, and counts as the stage's one loop."

### FP4. MAJOR. Several gates are already passed, or passed by construction, by a baseline. Rule 6 is broken on numbers the plan itself cites.

Section: rule 5, rule 6, G2.1, G2.2, G2.5, G3.2, G3.3.

Claim. Rule 6 says every baseline is run through a gate before it is frozen, and the gate changes if any baseline passes. The plan's own design numbers show a single free signal clearing the absolute bars of G2.2 and G3.3. G2.5 cannot catch this, because it runs the best single signal through G2.2, whose "+0.02 over the best single signal" clause fails for that signal against itself by definition. G2.1 and G3.2 have cheap degenerate passes.

Evidence.
- `r4-free-label-sources.md` section 6, Majestic referring subnets alone on Nov 16 lists: boundary pairs 0.633 [0.607, 0.659], all pairs 0.571 [0.558, 0.584]. G2.2 bar: lower bound above 0.60. G3.3 bars: all pairs above 0.55, boundary above 0.58. All three lower bounds are already above their bars for one unfitted signal. (The memo's interval is by search, not dyadic, so a dyadic one is wider, but the point estimates clear the bars with room.)
- G2.2's relative clause is a point difference ("point estimate at least 0.02 above"). Rule 5 asks for "a paired difference against the best baseline", meaning an interval. G2.3 and G3.3 have no paired baseline at all.
- G2.1: section 2.5 says a site "seen once, or never, falls back on its features" and gets a variance. So every domain, the client included, gets an estimate and a standard deviation by construction. Coverage 1.0 tests nothing.
- G3.2: a band taken from the marginal error quantiles of the middle guess, ignoring the model, covers at its stated rate. G3.2 has no width bar.

Fix. Replace these rows:
"G2.2: site-only model minus the best single signal of 2C, paired, on the same boundary pairs: dyadic 95% lower bound above 0, and the model's own dyadic lower bound above 0.62.
G2.3: add 'site-only model minus the footprint-only signal, paired dyadic lower bound above 0'.
G2.5: run each single signal and the shuffle through the absolute clause of G2.2 only. If any single signal clears it, raise the absolute bar to that signal's point estimate before the freeze.
G2.1: coverage is the share of non-platform top-30 domains whose estimate rests on at least one non-missing outside signal (S2, S4, S5 or S6) or on own-list evidence, at least 0.80; report separately the share resting on the prior alone.
G3.3: add 'combined model minus site-only model and minus the best single signal, paired dyadic lower bound above 0, on all pairs and on boundary pairs'.
G3.2: add 'median 80% band width at most 0.85 of the width of the middle-guess band built the same way'."

### FP5. MAJOR. The "best single signal" number in G2.2 depends on how missing values are handled, and the plan gives two incompatible rules.

Section: rule 7, G2.2 ("on the same pairs"), group 2C, section 1 and 0.1 citing 0.633.

Claim. Rule 7 says accuracy is "on decided pairs", ties as one half next to it, and "a missing value is missing, never zero". G2.2 compares the model and the best single signal "on the same pairs". A site-only model decides almost every pair. A single signal like Majestic is missing for about 43% of domains (242 of 428 c100 top-10 domains covered). So "decided pairs" of the signal is a different, smaller pair set from the model's. Two honest implementers will report different baselines, and the 0.02 margin can be won or lost on this choice alone.

Evidence.
- `r4-free-label-sources.md` line 148: the 0.633 was computed with "absent counts as 0" and "ties count one half". This breaks rule 7, yet the plan uses it as "the best single signal is expected near 0.63".
- `r4-data-inventory.md`: Majestic and Tranco cover 242 and 244 of 428 domains.
- Counting a missing pair as a tie pulls a signal toward 0.5. On the memo's numbers, about 0.5 + 0.6 x 0.133 = 0.58 if 40% of boundary pairs become ties, against 0.633 with absent as 0. A model that beats 0.58 by 0.02 may lose to 0.633.

Fix. Add to G2.2: "Primary comparison: model and every single signal on the full set of cross-site boundary pairs of XS-design, a pair counted 1, 0 or one half (tie or missing for that signal). The 'absent counts as 0' encoding is a separate signal variant, also in 2C, and counts as a candidate for 'best single signal'. Each signal's direction is fixed before 2C runs (more links and higher listing are stronger). Decided-pair accuracy is reported next to it, never gated."

### FP6. MAJOR. G4.2 can print PASS while the 11 to 30 band was never tested. The cell list and the merge rule are undefined.

Section: G4.2, rung 2, mode A ("in the stored top 30"), Appendix A.

Claim. G4.2 is "for each start tier (1 to 3, 4 to 10, 11 to 30) and each horizon with at least 150 rows... Cells with fewer rows are merged before scoring". No stored data has positions beyond 10 on more than one day. So tier 11 to 30 has no transitions to test. The merge rule is not given, so its empty cell gets merged into 4 to 10 or dropped, and the gate prints PASS while mode A ships 11 to 30 bands with no test. The horizons are not listed, so the agent picks which horizons count after seeing the row counts. Merging also lets an over-wide cell and an over-narrow cell average to the stated rate.

Evidence. Read-only count on `ranksim_raw.sqlite`: `serp_results` rows with position above 10 exist for one snapshot date only, 2026-11-16 (1,693 rows, max position 30). The deep source `smartcrew-index-deep:2026-11-16:abspos` is the only one. Section 1 also says "Deep lists: One day only: Nov 16". The collector pulls page one only (D5 asks to change that).

Fix. Replace G4.2 with: "Cells are fixed now: start tiers 1 to 3 and 4 to 10, horizons 1, 2, 3 and 7 days. Any cell under 150 rows is reported as 'untested', never merged. G4.2 passes when every tested cell is within 7 points of stated and at least 4 cells are tested. Mode A gives no band, only 'cannot tell: band untested', for a start position above 10 until a repeat deep pull exists. Report each cell's coverage with its two-way interval and its median width."

### FP7. MAJOR. gates.json cannot be built mechanically at stage 0, and rules 6 and 8 force a later re-freeze that has no direction limit.

Section: task 0.2, rule 6, rule 8, Appendix A, G3 outcome table, tier definitions in 0.1, 7, 8.

Claim. Task 0.2 freezes every numeric gate "before the stage 1 pulls, so the advance rules cannot drift". But rule 6 (run every baseline through the gate before freezing it) and rule 8 (simulate the interval's coverage on the gate's own layout before freezing) both need stage 2 and 3 data that does not exist at stage 0. So either rules 6 and 8 are skipped, or gates.json is re-frozen later, and nothing says a change may only make a gate harder. Several rows also cannot be read by a script as written.

Evidence.
- Tiers disagree in four places: 0.1 "top 3, 4 to 10, 11 to 30, beyond 30"; T-A "1 to 3, 4 to 10, 11 to 20, 21 to 30"; output record "top3, 4to10, 11to20, beyond20"; G4.2 "1 to 3, 4 to 10, 11 to 30". G3.4's ranked probability skill score changes with the tier set.
- G3.6 and G3.7 are in the G3 table and Appendix A, but the outcome table and the PREDICTED line only use "G3.1 to G3.5". An agent can ship mode C with G3.6 or G3.7 failed.
- G0 "no new failure" needs the six known failures named. G4.5 "deterministic" has no test. G4.3 "monotone" does not say strict or not (see FP8). G4.2 has no cell list (FP6). F1-B's "construction lists only" must map to the slice key `topic area: construction software` in `results.json`.

Fix. Add to task 0.2: "gates.json version 1 is frozen at stage 0 with every row in machine form (metric key, comparison, value, row file, interval type). Tiers everywhere are 1 to 3, 4 to 10, 11 to 20, 21 to 30, beyond 30. G3 passes only when G3.1 to G3.7 all pass. G0 lists the six failing test ids. G4.5 'deterministic' means two runs on the fixture give byte-identical JSON. A later version (gates-v2.json, v3...) is allowed only under rule 6 or 8, before that stage's gate command first runs, must name the baseline or coverage run that forced it, and `r4_gate.py` refuses it unless every changed bar is equal or stricter and every interval is equal or wider."

### FP8. MAJOR. G4.3 and G4.4 pass with a near-zero edit effect, and the plan leaves kappa_A's choice open.

Section: G4.3, G4.4, group 4C.1, Fallbacks.

Claim. G4.3's "mean log-rank error not above the 'no change' baseline" is met exactly by kappa_A near 0, because the prediction then equals no change. G4.4 needs only a by-site lower bound above 0, so kappa_A = 0.02 with a tight interval passes both. "Monotone dose response" without "strict" is met by a score that does not move. Group 4C.1 makes two kappa_A estimates ("with and without that site", the site that holds 43% of the 465 pairs) and does not say which one the product uses. The smaller one also shrinks every paired effect, which makes G4.1 invariance easier and pushes direction cases into "cannot tell". "Held-out groups" for the position backtest is not defined. kappa_A is fitted on XS-design, so a backtest on XS-design groups would be in-sample.

Evidence. Formula r1 = r0 x exp(-lambda x kappa_A x dm): at kappa_A = 0, r1 = r0, the "no change" baseline, so the error equals the baseline and "not above" holds. G4.3 text: "Monotone dose response in at least 90%". Group 4C.1: "report the estimate with and without that site".

Fix. Replace G4.3 and G4.4 with: "Monotone means strictly increasing toward B at each step, with the full-text step reaching at least half of B's score gap. Ties count as failures. Position backtest on same-site pairs of XS-test-stored groups only, after Checkpoint 3a: direction accuracy at least 0.60 with by-site lower bound above 0.5, and mean log-rank error minus the 'no change' error with a by-site 95% upper bound below 0. kappa_A for the product is the estimate with all sites, fixed in the claude-r4-editsim freeze. G4.4: that estimate's by-site lower bound above 0.25 (half the lowest simulated value)."

### FP9. MAJOR. Stage 1 fallbacks F1-A and F1-B need numbers the frozen scorer does not write. They will be computed by new code after the result is read.

Section: 5, G1-ADV, F1-A, F1-B, Appendix A rows F1-A and F1-B.

Claim. F1-A needs "unfitted cross-encoder minus BM25F, two-way lower bound above 0". F1-B needs "page-blend minus BM25F" on the construction slice. `r3_score.py` writes neither, and it is frozen. So these numbers come from a script written after the result is open, with free choices (interval, seed, tie rule). Also G1-ADV's "point estimate" is the ties-half rate in `results.json`, while rule 7 says accuracy is on decided pairs. An agent following rule 7 reads a different, higher number.

Evidence. `r3_score.py` `block()` writes `differences` only as f"{main} minus {base}" with main = `page-blend`. `res["slices_consensus"]` is built with `block(..., [])`, so slices have no differences. `R.pair_hits` counts a missing score as 0.5. Stage 1 also has three shots at advance (page-blend, the cross-encoder, the construction slice), and the preregistration predicted page-blend minus the cross-encoder at +0.01 with an interval across zero, so the second shot is close to a coin-flip reroll.

Fix. Add to task 0.2: "Write `scripts/claude/r4_c1_fallbacks.py` now, with a test on a synthetic pairs.csv, and freeze it with gates.json before pull A. It reads only `pairs.csv` and computes, with `r3_model.accuracy`, `r3_model.difference` and seed 20261119, the ties-half rate and two-way interval for the cross-encoder, the cross-encoder minus BM25F, and page-blend and page-blend minus BM25F on `topic area: construction software`. G1-ADV, F1-A and F1-B use the ties-half rate from these files only."

### FP10. MAJOR. At today's balance Checkpoint 3b cannot run even if D1 is granted, and the D1 default lets the shared balance fall under 250.

Section: 0.2 (stage 3 credits), 0.3 D1, 1 (Serper balance 617), 3D.2, 9.

Claim. Section 9 assumes 617 and shows stage 3b with N = 48. The lead measured 538 at 10:40 ET today, about 508 expected by 11:00 ET. After stage 1 (208) the balance is about 300. The plan's formula gives N = min(60, floor((300 - 250 - 15) / 3)) = 11, under 30, so 3b is "not run". That holds even if the collector is paused at once. Without D1, the collector's own floor is 200, so its next two 30-credit pulls take 300 to 240, under James's 250.

Evidence. Formula in 3D.2. To reach N = 30 the balance at the 3b freeze must be at least 355, so at least 563 before stage 1, already gone at 10:40. `/workspace/ranksim-data/collect_daily.sh`: `RESERVE="${RANKSIM_CREDIT_RESERVE:-200}"`. Section 9 also says 69 above the floor "is under the 90 needed" for N = 30. The formula needs 105 above the floor (90 plus the 15 margin).

Fix. Replace section 9's table with one that starts at the measured 508 and add: "At 508, Checkpoint 3b is not affordable after stage 1 (N = 11). Stage 3 ends at Checkpoint 3a (PROVISIONAL) unless new free credits arrive. G0 adds: 'D1 answered and the collector's caller sets RANKSIM_CREDIT_RESERVE to at least 250 plus the credits still to be pulled by this plan, or the collector paused'. Without that, stage 1 does not start." Update the 0.2 row for stage 3 to "0 credits now; 3b only if the balance allows N of at least 30".

### FP11. MAJOR. The stored test 3a will usually fail its own validity rule, and the plan does not say what happens then.

Section: group 2A.1 (size check), G3 validity, group 3C.

Claim. The split's size check accepts XS-test-stored with "at least 9 groups and at least 18 c100 searches with a deep list". G3 validity needs "at least 30 searches and 300 target rows", and T-A needs a top-30 list. So most accepted splits give a 3a test that is invalid. With 3b unaffordable (FP10), stage 3 then has no valid test, and an agent must guess: count top-10-only searches, lower the validity bar, or report anyway.

Evidence. Read-only count from `universe.json` and the deep source: 100 deep searches, 66 reach position 30. The largest group holds 35 of them and is forced into XS-design, leaving 65 deep searches in 32 groups. Simulation of a 35% group draw over the 63 other construction groups (20,000 draws, labels not read): 68% of draws pass the size check. Among those, the median is 26 deep searches, P(at least 30) = 0.36, and P(at least 30 that reach position 30) = 0.10, median 20.

Fix. Replace the 2A.1 size check with: "XS-test-stored must hold at least 30 c100 searches with a deep list reaching position 30, and at least 9 groups. Try seeds 20261120, 20261121, ... in order and record each. If no seed under 20261140 meets it, Checkpoint 3a is reported as 'not tested: too few deep searches' and stage 3 has no gate result." Add to G3: "A test under the validity bar is reported as invalid, never scored against the bars."

### FP12. MAJOR. Nothing stops a results section that reads as a win while the gate that counts failed.

Section: rule 14, G1-ADV versus G1-GOAL, groups 3C and 3D, outcome and fallback tables of stages 2 to 4, 0.2.

Claim. Rule 14 only asks for "the numbers and the pass or fail". The plan creates several ways to lead with a pass: G1-ADV can pass while the GOAL.md target G1-GOAL fails; Checkpoint 3a (PROVISIONAL, read data) may be the only stage 3 result (FP10, FP11); several fallbacks "advance" or "ship" after a fail (G2.2 "best single signal becomes the value", G3 "ships tiers only", G3.2 "widened bands untested", G4.4 "direction only"). It is not stated whether a 3a pass enables mode C ("All of G3.1 to G3.5 pass: Stage 4 enables it" does not name the checkpoint).

Evidence. Section 7 outcome table and section 8 mode C ("If stage 3 failed: cannot tell") never say 3b. Section 5: "G1-GOAL is still reported as passed or failed exactly as frozen" but nothing says it comes first.

Fix. Add to rule 14: "Every checkpoint entry opens with a table of every gate id in gates.json: value, interval, bar, PASS, FAIL or NOT RUN, and the checkpoint (3a or 3b). The first line of stage 1's entry is G1-GOAL. A fallback is written 'FAIL, fallback F_x taken', never 'pass' or 'advanced'. No point estimate appears without its interval. A 3a number is always labelled 'PROVISIONAL, data read before' in the same sentence. Mode C is enabled only by a G3 pass on Checkpoint 3b. A 3a pass alone leaves mode C at 'cannot tell'. The entry links the audit file of FP1."

## Unverified suspicions

- The niche term list for 3D, the site-type rule table and the frozen pick rule of S3c are frozen before their test is scored, but after the agent has read the stored lists. I could not show a concrete path from them to a false pass.
- The lambda update rule and the ledger scoring rule are "frozen before the first read" with no content and no date. They are not gates, so the harm is to the report, not to a PASS.
- "Polite rate" for outside lookups has no number. It is a conduct risk, not a gate risk.
- The dyadic interval in rule 8 may be wider than the by-search interval of the memos. If so, FP4's G2.2 and G3.3 cases are closer to the bar than the memo numbers show.

## Things the plan gets right (checked)

- `r3_score.py` already enforces one scoring of stage 1: manifest committed and pushed, one commit per chunk after the freeze, no result on the pushed branch, one fetch pass, pull gaps.
- The material-change rule in the plan matches `t2_changes.py` (`MATERIAL_RULE`, 250 tokens and 0.02).
- The plan names the collector's 200 floor as a risk to the 250 rule.
- XS-test-stored is not opened in stage 2, and the stored cell becomes design data if the model is refitted.
