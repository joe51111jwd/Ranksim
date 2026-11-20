# Review of SPEC-MASTER-PLAN.md: leakage and evaluation validity

Reviewer: independent adversarial reviewer (lens: leakage between design and held-out sets, and evaluation validity). Written 2026-11-20. Read only. No Serper request. No held-out list read. No model fitted or scored on real ranking data.

Status: complete. 9 findings: 2 BLOCKER, 5 MAJOR, 2 MINOR.

Verdict: SAFE AFTER REQUIRED CHANGES. The fresh test as written would read post-merge client rows and would let near-duplicate searches into the only unseen test. The content-across-sites gates rest on in-sample scores. All of these can be fixed in the plan text before any stage runs.

## Findings

### L1. BLOCKER. The fresh test (Checkpoint 3b) reads client rows dated after Nov 19 before the PR 45 ledger version 2 is frozen

Section: section 3 rule 2, section 7 group 3D, section 8 group 4E.

Claim. Rule 2 says no script may read any row of the client's domain dated after 2026-11-19 until the PR 45 simulator ledger is frozen in stage 4. Stage 3b pulls pages 1 to 3 of 30 to 60 new construction searches, fetches them, and scores T-A, T-B and T-C on every row, all before stage 4. Nothing in group 3D masks the client's domain or the PR 45 pages. The client will be in most of those lists, so the plan breaks its own rule by design, and ledger version 2 (the only blind real-edit check) becomes "not blind" before it is written.

Evidence.
- Plan, rule 2: "no script may read any row dated after 2026-11-19 for the PR 45 target searches, or any row of the client's domain dated after 2026-11-19." Group 3D steps 3 to 5 pull, fetch and "Score once" with no exclusion. T-B is scored "for each niche domain", and G2.1 requires that "The client's domain has one" estimate, so the client is a T-B domain by design.
- Read-only count, `ranksim_raw.sqlite`: the client's domain is in 61 of the 100 Nov 16 deep lists (102 rows at positions 1 to 30) and in 67 of the 157 stored searches. The fresh searches are drawn from the related searches of those same lists.
- Related-search candidates include 32 strings with "raken", for example "how much does raken cost per month?" and "raken time tracking". 15 of them are not linked to any stored group even by the union-find linker. Their results will very likely hold the client's `/compare/raken-alternative/` page, which PR 45 edited.
- The collector already writes post-merge client rows into the same file: snapshot 2026-11-20 has 832 ordinary rows, 95 of them on the client's domain.
- Plan, 4E: "It counts as blind only if rule 2 of section 3 held for every script. Otherwise it is labeled 'not blind'". So the plan's own order of work guarantees that label.

Fix (paste into rule 2 and group 3D).
- Rule 2, add: "Every reader of rows dated after 2026-11-19 goes through one sealed loader, `scripts/claude/r4_sealed.py`, which drops every row whose site is the client's domain and every row of a PR 45 target search or of any search that contains a PR 45 brand token (raken, busybusy, clockshark, b2w, earthwork) until `evals/claude/r4/ledgers/pr45-v2/manifest.json` exists and verifies. This covers SERP rows, page fetches of the client's site, and any Search Console data. A test plants a client row dated 2026-11-21 and checks that every stage 3 and 4 entry point drops it."
- Group 3D step 1, add: "A candidate is not eligible if it contains the client's brand or a PR 45 brand token." Step 5, add: "The client's domain is never a T-A target, a T-B domain or a T-C pair member in the fresh cell. Rival positions keep their pulled numbers, so the client's slot stays an unexplained gap."
- Alternative if James prefers: freeze ledger version 2 from mode A only before the 3b pull (mode A needs only the 3B.1 position curve and the 4C `kappa_A`, both built on design data), and say so in 4E.

### L2. BLOCKER. The fresh-search eligibility rule is either empty or rewrites the frozen groups

Section: section 7 group 3D step 1, and the test `tests/test_claude_r4_plan.py`.

Claim. Step 1 makes a candidate eligible when "the frozen linker puts it in no existing search group" and section 3 rule 3 names "the frozen linker in `scripts/claude/r3_dataset.py`". That function, `search_groups()`, is an exact-text lookup of the 155 stored searches. It returns no group for any new string, so the filter removes nothing, and near-duplicates of stored searches enter the only test the plan calls unseen. The real linker (`r2_samesite_pairs.assign_folds`) is a transitive union-find over a list. Run over stored plus candidate strings, it links most candidates and also chains separate stored groups together, so it cannot be used as written either.

Evidence (string processing only, read-only, no ranking data scored; script in /tmp, not committed).
- `r3_dataset.search_groups()` (lines 118 to 126) maps search text to `g<index>` from `universe.json`. Of 1,536 related-search and "people also ask" strings not among the stored searches, it puts 0 in a group.
- `assign_folds(stored + candidates)` links 939 of the 1,536 to a stored group, and 3 of the merged groups join two or more existing universe groups through candidate chaining.
- Example: "raken alternative free" (the PR 45 target "free alternative to raken" in other words) is eligible under `search_groups()` and linked under `assign_folds`. "raken pricing per month" and "raken pricing per year" behave the same way.
- The test in the plan, "no fresh search is in a stored search group", passes trivially under `search_groups()`.

Fix (paste into group 3D step 1).
"Eligibility is computed candidate against stored only, never through other candidates. With `t = r2_samesite_pairs._fold_tokens`, a candidate c is excluded when, for any of the 155 stored searches s: t(c) and t(s) are equal, share a token trigram, or one token set contains the other (any size); or c contains any brand token of a frozen list (every brand named in a stored search, plus the client's brand); or c contains a navigational word from a frozen list (login, sign in, app, download, careers, glassdoor, support, reddit). Candidates are then grouped among themselves with `assign_folds(candidates)` only. `tests/test_claude_r4_plan.py` asserts that 'raken alternative free', 'raken pricing per month' and 'busybusy login' are not eligible, and that the 71 universe groups are unchanged after planning."

### L3. MAJOR. The frozen content score is in-sample on most of the cross-site design rows, so kappa, kappa_A, G3.5, G4.3 and G4.4 are inflated

Section: 2A.4, 3A, 4C, G2.0, G3.5, G4.3, G4.4.

Claim. The frozen scorer was fitted once on every training list (round 3 train sites in train search groups). No per-fold model is saved. Stages 2 to 4 then score pages of those same sites on searches of those same groups in ordinary lists, and estimate the content scale from them. On those rows the score was fitted to order those very pages, and site-restricted order agrees with ordinary order on 0.838 of pairs. So the content slope is biased up, and the gates that test it (kappa above 0, `kappa_A` above 0, swap pairs, the position backtest) are close to sure passes.

Evidence.
- `scripts/claude/r3_fit.py` lines 63 to 72: one `FrozenScorer.fit(table, ...)` on the whole training table, saved to `evals/claude/r3/model/<name>/`. `r3_model.cross_validate` refits per fold and saves nothing. The folder holds only `spec.json`, `logit.json`, `gbm.txt`, `df_table.json`, `fit_info.json`.
- `evals/claude/r3/model/page-blend/fit_info.json`: in-sample pair accuracy 0.771. The same scorer with site and search group held out: 0.704 (`preregistration.md`, design table). The in-sample gap is about 0.07 on site-restricted pairs.
- Read-only counts on the Nov 16 deep lists (positions 1 to 30), using `universe.json`, `train.jsonl` and `validity.jsonl`: 85 of the 100 searches are in round 3 train groups (19 of the 33 groups present). Of 527 same-site pairs with no platform and no client page, 434 (0.82) are a train site on a train-group search, and 71 (0.135) are pairs where both pages were items of the training list for that exact search. 701 of 2,021 non-platform rows (0.347) are pages that sat in some training list.
- `kappa_A` (4C) is fitted on exactly these same-site pairs ("About 465 such pairs exist, 43% from one site"). The site with the most same-site pairs, hcss.com (332 of 834 including platforms), is a train site.
- G2.0 asks for agreement "in site-held-out cross-validation", but there are no per-fold frozen models to compare against.

Fix (paste into 2A.4 and add a line to 3A, 4C, G3.5, G4.3, G4.4).
"`r4_content_scores.py` also writes `evals/claude/r4/site/content_oof.csv.gz`: for every (search, page) whose site is a round 3 train site or whose search is in a round 3 train group, the out-of-fold score from `r3_model.cross_validate(table, kind, features, also_hold_out_queries=True, group_of=...)` with the frozen spec, seed and folds, so no row is scored by a model that saw its site or its search group. The frozen weights are not changed. Every estimate of `kappa`, the same-site kappa of 3A and `kappa_A`, and the rows of G3.5, G4.3 and G4.4, use the out-of-fold score on those rows and the frozen score elsewhere. Each is also reported on rows of non-train sites only. The run of `cross_validate` is part of the stage 2 freeze."

### L4. MAJOR. G3.5 "Content matters across sites" cannot fail on held-out data, and F1-B leans on it

Section: G3.5, the stage 3 outcome table, F1-B.

Claim. G3.5 has two parts. The first (kappa lower bound above 0) is computed on XS-design, where the content score is in-sample (L3), so it is not a held-out check. The second is a non-inferiority margin of 0.10 places on the test rows, with no interval. A content term with no real effect passes it whenever the fit gives it a small weight. So a pass of G3.5 says nothing about content across sites. F1-B ("Stage 3's kappa check (G3.5) must then confirm the content signal") and the outcome row "G3.5 fails ... The content score adds nothing" both treat it as evidence.

Evidence.
- Plan, G3.5: "`kappa` from the XS-design fit has a search-group bootstrap 95% lower bound above 0, and on the test rows the combined model's T-A error is not worse than the site-only model's by more than 0.10 places".
- SIMULATED (numpy, 40 searches of 30 rows, site-only score correlated 0.6 with the truth, content term with no real effect, placement by per-list regression of position on score, 2,000 draws): when the useless content term holds 0% of the combined score variance, the test part passes in 1.000 of draws. At 5% it passes in 0.652 (mean extra error +0.086 places). At 10% it passes in 0.103. So the test part only fails when content actively hurts. It never shows that content helps.

Fix (replace G3.5 in section 7 and Appendix A).
"G3.5. Content matters across sites. On the fresh test rows: (a) T-C decided cross-site pairs, combined minus site-only pair accuracy, paired, dyadic 95% lower bound above 0; and (b) T-A mean absolute error, site-only minus combined, two-way 95% lower bound above 0. `kappa` (from out-of-fold content scores, L3) with its interval is reported, not gated. If the power of (a) at the PREDICTED effect is under 0.8 (group 3D step 6), G3.5 is reported as 'not testable' and no stage may claim that content matters across sites. F1-B advances only if G3.5 passes in this form, else the content claim stays limited to same-site order."

### L5. MAJOR. T-A rows carry answers a new page would not have: the empty slot, a same-site sibling, and the page's own rows elsewhere

Section: T-A, group 3A ("fitted once"), rule 4, signal tables of stage 2, G3.1 to G3.4, outcome row "The no-anchor mode is supported".

Claim. T-A is meant to support mode C (a new page with no anchor). Three things in its rows are not available for a new page, and the plan only guards one of them in words, in a form the data layout cannot carry.
1. The empty slot. "keep the other pages at their known positions": with 29 rival positions known, the one missing number is the answer. The posterior-median method of 3B.2 does not use it, but nothing forbids an implementation (or the expected-rank cross-check) from seeing it, and no test checks it.
2. A same-site sibling. When another page of the target's site is in the same list, its known position tells the model the site part S(q, d) for this exact search. That is mode B, not mode C. 
3. The page's own rows. Rule 4 says the target's own rows on other searches are left out, but 3A fits the domain effects "once on XS-design", and stage 2 writes `signals` and `site_strength` "per (domain, left-out group)". Neither can drop one URL's rows. The planned test "Target-URL exclusion" has no artifact to act on.

Evidence (read-only counts, Nov 16 deep lists, positions 1 to 30, platforms and the client left out).
- 355 of 2,021 rows (0.176) have a same-site sibling in the same list.
- 414 of 2,516 rows (0.165) are URLs that also rank in the top 30 of a search in another search group on the same day. 100 of 985 top-10 rows (0.102) are also top 10 in another group.
- Plan T-A: "The site strength uses no list of this search group and none of the page's own rows." Plan 3A: "fitted once on XS-design (one list per search)". Plan 2 outputs: "one row per (domain, left-out search group)".

Fix (paste into T-A and 3A).
"T-A primary rows are pages with no same-site sibling in the list. Rows with a sibling are scored as mode B and reported apart. The model sees rivals only as an order (ranks 1 to n-1 among themselves), never their absolute positions. `tests/test_claude_r4_position.py` asserts that the prediction does not change when the hidden page's true position is moved to another slot with the rival order kept. For each distinct target URL, the domain effect and the S1 footprint are recomputed without that URL's rows (one warm-started refit per target URL, or an exact one-step update that the test checks against a full refit on a fixture), and `site_strength` gains a column `excluded_url`. T-A with the URL's rows kept is a secondary reading."

### L6. MAJOR. F1-A and F1-B cannot be computed by the frozen run, so their numbers would come from code written after the result is open

Section: stage 1 gates G1-ADV, fallbacks F1-A and F1-B, task 0.3 (`r4_gate.py`).

Claim. F1-A needs "unfitted cross-encoder minus BM25F, two-way lower bound above 0". F1-B needs "page-blend minus BM25F, two-way lower bound above 0" on the construction lists alone. The frozen `r3_score.py` computes neither. So after the held-out result is open, someone must write new scoring code to decide whether to advance. `r4_gate.py` "contains no threshold of its own" and only reads a result file, so it cannot do it either. With G1-GOAL, G1-ADV, F1-A and F1-B there are four readings of one test and three routes to advance. The preregistration says a failed test becomes design data and "any later test needs new sites (the test2 sites)", and the plan does not say what RESULTS and the product must call an advance by a fallback route.

Evidence.
- `scripts/claude/r3_score.py` line 204: differences are only `f"{main} minus {base}"`, so only page-blend minus each baseline. Line 306: slices use `block(pairs, hits, mask & consensus, main_name, [])`, an empty baseline list, so no difference is computed on the construction slice.
- `pairs.csv` (line 340) keeps one `hit|<scorer>` column per scorer, so the numbers can be computed later, but only by code that does not exist and is not in any manifest.
- Plan F1-A: "nothing fitted, already a baseline in the same run, ... so this is not a post-hoc pick". The pick rule is declared, but its computation is not.

Fix (paste into task 0.2 and the stage 1 fallbacks).
"Before stage 1 pulls, write `scripts/claude/r4_c1_fallbacks.py`. It reads only `pairs.csv` and `heldout1.slices.json`, uses `r3_model.accuracy` and `r3_model.difference` with seed 20261119 and 2000 draws, and writes `c1_fallbacks.json` with: cross-encoder accuracy and two-way interval on consensus pairs, cross-encoder minus BM25F two-way interval, and page-blend accuracy and page-blend minus BM25F on the consensus pairs of the construction slice. It is tested on a synthetic `pairs.csv` and frozen in `evals/preregistered/claude-r4-gates/` with `gates.json`. Its output is written once. RESULTS and every stage 4 provenance record state the route: 'G1-GOAL passed', or 'advanced by G1-ADV, F1-A or F1-B; G1-GOAL failed'. Rung 1 of the ladder is marked passed only for G1-GOAL."

### L7. MAJOR. Checkpoint 2 is a cross-validation on the same design data the agent tunes on, with nothing that stops repeated runs

Section: groups 2C to 2E, G2.2, G2.5, rule 1, rule 13.

Claim. G2 is computed by nested leave-group-out cross-validation over XS-design. The single-signal table (2C), the model building (2D) and the gate (2E) all use XS-design. Rule 1 only requires a freeze "before any held-out data is pulled or read", and rule 13 only limits loops after a gate "fails". A run during development is neither, so an agent can rerun `r4_stage2_eval.py gate` while changing knobs the plan leaves open until it passes. G2.5 does not catch this: a single signal cannot pass "at least 0.02 above the best single signal", so that half of the dominance check is empty by construction.

Evidence.
- Plan 2E: "nested leave-group-out cross-validation over XS-design ... Then `r4_gate.py c2`." No freeze step and no run limit is stated for stage 2.
- Choices the plan leaves open: whether G2.2 includes platform pairs (they are only "reported ... with and without platforms"), the "small fixed ridge" value, the tau grid, pair weights for lists shorter than 30 (30 of the 100 deep lists do not reach 30), how missing signals enter, and the deep-list or single-pull choice for the 40 searches with no deep list.
- G2.5: "every single signal and a within-search label shuffle run through G2.2. None passes."

Fix (paste into group 2E and G2.5).
"Before the first `gate` run, `r4_site_model.py`, `r4_stage2_eval.py`, `xs_split.json`, `site_types.tsv`, the signals file and a config file that fixes every open choice (G2.2 on non-platform pairs; ridge value; tau grid; pair weights; missing handling) are frozen with `freeze.py` into `evals/preregistered/claude-r4-stage2/`, committed and pushed. `gate` refuses to run if the manifest fails or if `evals/claude/r4/stage2/results.json` exists. During development the model may be run only through `r4_stage2_eval.py dev`, which uses the first two leave-group-out folds only and cannot write a gate result. G2.5 reads: every single signal and a within-search shuffle, run through the absolute part of G2.2 (dyadic lower bound above 0.60): the shuffle must fail, and the 0.02 margin is computed against the best single signal's own point estimate."

### L8. MINOR. The "known exposures" table misses earlier reads, and "No cross-site model has ever been fitted" is false

Section: section 3, known exposures table and the sentence under it; G4.2.

Claim. The table is what tells James how pristine the stored cross-site data is. It leaves out several earlier reads of cross-site order, including footprint-type transfers, a domain prior, and fitted cross-site models. It also misses that Nov 18 and Nov 19 were already scored as held-out days, and G4.2 then uses "transitions ending Nov 18 and 19" as its untouched test days.

Evidence.
- `RESULTS-claude.md` line 65 (round 1 target 3 design): "About 50 scorers were tried (... logistic and small tree models, validated forward in time)" on cross-site pairs. Those are fitted cross-site models on stored lists.
- The product ranker (`model-next.json`, 94% history) was fitted on cross-site lists.
- `evals/claude/r2/t1-mechanisms/notes.md` lines 32 to 46: the URL's and the site's rank on sibling searches was tested as a placement signal ("Site-level sibling transfer ... worse than c9"), using pulls of Nov 18 and 19 morning. This is the S1 footprint idea, already read.
- `evals/claude/heldout/REPORT.md` line 30 and `RESULTS-claude.md` line 165: Nov 18 and 19 were scored for new-URL placement against the domain prior and for movers against persistence.
- `r4-free-label-sources.md` section 6: Common Crawl capture counts per domain against the Nov 16 lists (boundary 0.563, 138 domains), and "The earlier team's domain prior did not help on 30 searches".

Fix (paste into section 3 and G4.2).
Add rows: "Round 1 target 3: about 50 cross-site pair scorers, including logistic and tree models, Nov 16 to 17, 30 searches." "Product ranker: fitted on cross-site lists." "Round 2 t1 mechanisms: URL and site rank on sibling searches as a placement signal, through Nov 19." "Rounds 1 and 2 held-out scoring of Nov 18 and 19: placement with the domain prior, movers against persistence." "Common Crawl capture counts against Nov 16 lists, 138 domains." Replace the sentence with: "No model with the stage 2 site features has been fitted. History-based cross-site models and footprint-type transfers have been read." In G4.2 write: "Coverage is gated only on transitions ending 2026-11-20 or later (sealed loader of L1). Nov 18 and 19 are reported as PROVISIONAL."

### L9. MINOR. The seal is a calendar date, but the merge happened inside that date

Section: section 3 rule 2, stage 2 inputs (`max_date="2026-11-19"`).

Claim. PR 45 merged about 8:00 PM ET on Nov 19 (01:00 UTC Nov 20). Rows with `snapshot_date` 2026-11-19 run past that time, so "dated 2026-11-19 or earlier" admits post-merge rows. Today none of them is a client row, so nothing has leaked, but the rule as written would let one in.

Evidence (read-only counts). `serp_raw` rows with `snapshot_date` 2026-11-19 run to 2026-11-20T01:07:50Z. The 24 rows of `claude-r3:noise-a` were fetched from 01:07:14Z, after the merge, and carry the date 2026-11-19. None holds the client's domain. `serp_results` rows of that date end at 2026-11-19T20:56:48Z, before the merge.

Fix (paste into rule 2). "For the client's domain and the PR 45 searches, the seal is the time 2026-11-20T01:00:00Z on `fetched_at_utc`, not the date. The sealed loader checks both."

## Things the plan gets right (checked)

- The XS split re-draw rule is count-based only, with a fixed seed sequence and every seed recorded. It cannot react to outcomes, so it is not a forking path.
- Stage 3a on stored lists is labeled PROVISIONAL and only 3b is called unseen, which matches the real exposure of the Nov 16 lists.
- G1-ADV and the fallbacks are written into `gates.json` and frozen in stage 0, before any held-out list is pulled, and G1-GOAL is kept exactly as preregistered.
- Rule 3 keeps test2 sites out of training pairs and domain effects, which matches the preregistration's promise that test2 stays for a later test.
- The band backtest (rung 2) already excludes PR 45 searches and client rows on later collector days.

## Unverified suspicions

- Related-search strings that the linker does not catch (for example "busybusy time tracking", "agtek earthwork 4d") probably share many result pages with the stored searches they came from. This cannot be measured without a pull, and none was made.
- The `kappa_A` estimate may be dominated by hcss.com (332 of 834 same-site pairs in the deep lists, a train site). Reporting "with and without that site" may not be enough if the out-of-fold fix of L3 is not applied.
- If D2 (Search Console) is approved during stages 2 to 4, post-merge client positions could reach a script before ledger version 2 is frozen. The L1 sealed loader covers this only if Search Console readers use it.
- The list-invariance check of 2A.4 (G2.0) asks for agreement "in site-held-out cross-validation", but no per-fold frozen models exist. An agent may run it in-sample on the training lists, where agreement is easy.
