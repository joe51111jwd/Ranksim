# Review of SPEC-MASTER-PLAN.md: credit budget and feasibility

Reviewer: independent adversarial reviewer (credits and feasibility lens). Written 2026-11-20.
Read only. Zero Serper credits spent. No request to any serper.dev host. No held-out list read.
Balance used for all sums: 538 at 10:40 ET Nov 20 (lead's reading), about 508 after today's third collector pull.

Status: complete. 12 findings: 2 BLOCKER, 6 MAJOR, 4 MINOR.

Verdict: SAFE AFTER REQUIRED CHANGES. C1 and C2 must be fixed before any agent starts stage 1, and the stage 1 window closes about 09:00 ET on Nov 21. C3 to C8 change what James is told stage 3, stage 4 and the ledgers can deliver.

## Findings

### Measured facts used below (all read only, no Serper call)

- Collector logs `/workspace/ranksim-data/snapshots/pull20-a.log`, `pull20-b.log`, `pull20-c.log`: balance 598 at pull A (09:41 ET), 568 at pull B, 538 at pull C. Pull C stored 24 of 30 searches and then crashed on a ReadTimeout. Same crash on Nov 19 (24 of 30) and Nov 18 (15 of 30). `pull20.done` is dated 10:58 ET, so today's collector run is over.
- Balance now: 514 if only stored responses were charged, about 508 if the usual unmatched charge applied (see next line). I use 508.
- Unmatched charges: from 1226 (pull18-c start) to 1187 (pull19-a start) the store holds 33 responses for 39 credits. From 1127 (pull19-c start) to 598 (pull20-a start) it holds 523 responses for 529 credits. About 6 credits a day are charged with no stored response.
- Other-team schedule from `serp_raw.fetched_at_utc`, Nov 17 to 20: a 15-credit tracker at about 09:10 ET, then collector pulls at about 09:30, 10:03 and 10:37 ET (30, 30, 15 to 30). Real spend is 84 + 15 + about 6 = about 105 a day, not 90.
- `collect_daily.sh` line 13: `RESERVE="${RANKSIM_CREDIT_RESERVE:-200}"`, line 15 cap 120 per run. It tightens its cap to `balance - reserve`, so with the default it spends down to 200.
- `scripts/claude/r3_pull.py` lines 138, 170, 192: default reserve 260. A pull is refused at start if `balance - 104 < 260`, so each 104-search pull needs at least 364 when it starts. Each 25-search chunk is rechecked. Lines 103 to 112: a network error is retried up to 4 times.
- `evals/preregistered/claude-r3-samesite/test.json`: labels fixed to `heldout1-a` and `heldout1-b`, 30 minutes to 6 hours between pulls. `r3_score.py` `check_pulls` refuses a gap over 6 hours, and `check_no_other_pull` refuses if any plan search appears under another round 3 label.

### C1. BLOCKER. G0's 468 is not a safe start for stage 1, and the real deadline is tomorrow about 09:00 ET

Plan text. Section 4, task 0.4: "Stage 1 may start only if balance is at least 468 (250 floor, 208 pulls, 10 safety)". Section 9: "stage 1 must be pulled before the collector's second run from now". D1: "After two more collector days stage 1 no longer fits."

What is wrong.
- `r3_pull.py` keeps a 260 reserve, not 250. At exactly 468, pull A leaves 364 and pull B leaves 260. The "10 safety" is used up by the script's own reserve. One extra credit before pull B starts (the 15-credit tracker, a collector pull, or the about 6 unmatched credits a day) makes pull B refuse to start.
- From 508 now, pull A leaves 404. Pull B then needs at least 364 at its start. So at most 40 credits of other spending may fall between the start of pull A and the start of pull B. The 09:10 ET tracker (15) fits. The tracker plus the 09:30 ET collector pull (45) does not.
- After 09:30 ET on Nov 21 the balance is about 508 - 15 - 30 = 463, under 468. Stage 1 never fits again. The deadline is not "the collector's second run" or "two more collector days". It is the first other-team run tomorrow.
- If pull B is refused or delayed past 6 hours, the round 3 test is lost for good. `r3_score.py` refuses a gap over 6 hours. `test.json` fixes the labels. `check_no_other_pull` refuses a fresh pair of labels for the same plan. The 104 credits of pull A are wasted and James's first priority cannot be scored.

Fix to paste into task 0.4 and G0.
"0.4 Read the balance B and the other team's schedule (tracker about 09:10 ET, collector about 09:30, 10:03, 10:37 ET). Let S be the other-team credits scheduled between the planned start of pull A and the planned end of pull B, plus 10 for unmatched charges. Stage 1 may start only if B is at least 260 + 208 + S + 5. Pull A starts only after the collector's last pull of the day has ended (after about 11:00 ET), and pull B must end before 09:00 ET the next morning, so S is normally 10 and the bar is 483. If the bar is not met, or if pull A cannot start by 08:15 ET Nov 21, stop and ask James."
In section 9 and D1 replace the deadline sentences with: "Measured 508 at 11:00 ET Nov 20. Stage 1 fits only if both pulls run before the tracker at about 09:10 ET on Nov 21. After the collector's first pull on Nov 21 the balance is about 463 and stage 1 no longer fits."

### C2. BLOCKER. The plan's default path takes the shared balance under 250 the morning after stage 1

Plan text. D1 default: "Run stage 1 at once." Section 9: "The collector's default floor is 200. Unless its caller sets 250, it can take the shared balance below James's floor on its own."

What is wrong. After stage 1 the balance is about 508 - 208 = 300. The collector's next run (about 105 credits a day, reserve 200) takes it to about 200 by 10:50 ET Nov 21. So James's "at least 250 always unspent" rule breaks within a day of the plan's own spend, on the plan's default path. The plan names the risk but still makes running stage 1 the default when D1 is unanswered.

Fix to paste into D1 and G0.
"D1 is required, not optional. G0 adds: James confirms in writing that the collector's caller sets RANKSIM_CREDIT_RESERVE=250 (or that the collector and the 15-credit tracker are paused) before pull A starts. Default if no answer by 08:00 ET Nov 21: stage 1 does not run, and the plan reports that the credits for it were not available."

### C3. MAJOR. Checkpoint 3b cannot run under the plan's own formula, with or without D1

Plan text. Section 7, 3D.2: "N = min(60, floor((balance - 250 - 15) / 3)). If N is under 30, Checkpoint 3b is not run". Section 9 table: "Stage 3b, fresh test, N = 48 by the formula (3 credits a search) | 144". Section 0.2: Stage 3 "90 to 180" credits. D1: "until the stage 1 and stage 3 pulls are done".

What is wrong. The 617 in the plan is from before today's tracker and collector run. From 508, stage 1 leaves 300. N = floor((300 - 265) / 3) = 11, under 30, even if the collector is paused this minute. Without a pause the balance is under 250 the next day (C2) and N is negative. N = 30 needs 355 at the stage 3 freeze, so 563 today. There are 508. So the only unseen cross-site test in the plan cannot happen on free credits, and D1 cannot buy it.

Fix to paste.
Section 9 table: "Now 508 (258 above the floor). Stage 1: 208, leaves 50. Stage 3b: N = floor((300 - 265) / 3) = 11, under 30, so 3b is not run. Stage 3 ends at the PROVISIONAL stored test 3a unless James approves D3 or new credits."
Section 0.2, stage 3 row: "0 credits (3b not affordable after stage 1). Gate applied to 3a only, PROVISIONAL."
D1: delete "and stage 3".

### C4. MAJOR. Checkpoint 5 needs about 225 credits per score date, but the plan lists it at 0

Plan text. Section 8, 4E: "truth is the consensus of at least 3 pulls on the score date". Rung 5: "0 extra credits, weeks". Section 9 lists no credits for ledger reads.

What is wrong.
- The PR 41 ledger (`evals/draft-ledger/smartcrew-pr41.jsonl`) has 147 rows on 74 distinct searches. Only 7 of them are in `/workspace/ranksim-data/queries/smartcrew-30-original.txt`, the only list the collector pulls each day.
- Of the 9 PR 45 target searches (`scripts/claude/r3_pr45.py`, SEARCHES), only `b2w alternative` is in that list.
- So 3 pulls on a score date need 3 x (67 + 8) = 225 credits. Two score dates need about 450. After stage 1 there are about 50 above the floor.
- Even the 8 covered searches need the collector to still be running in December. With a 250 reserve it stops about a day after stage 1 (C2). Paused under D1, it gives nothing.

Fix to paste into 4E and section 9.
"Checkpoint 5 is unfunded. Each score date costs about 225 credits (3 pulls of 67 PR 41 searches and 8 PR 45 searches that the collector does not pull). With about 50 credits above the floor after stage 1, no ledger can be read under the frozen truth rule. Each read needs James to approve credits (D3 or a new free balance). Otherwise the ledgers stay unscored and the report says so."

### C5. MAJOR. D1 and D5 remove the collector days that rung 2, G4.2's fallback, 4B and Checkpoint 5 rely on

Plan text. D1: "Cap or pause the other team's daily collector". D5: "Swap one of the collector's three daily page-one pulls for a page-two pull". Rung 2: "tested on transitions ending Nov 18 and 19 and on later collector days". G4.2 fallback: "retest on later collector days. No release before coverage holds on days the widths did not see". 4B: "extrapolated and flagged until the collector's data covers them".

What is wrong.
- A paused collector makes no later days. A collector held at reserve 250 stops after about half a day once stage 1 has left the balance at about 300. Either way "later collector days" do not come. Then the G4.2 fallback can never be met, so one failed G4.2 blocks release for good.
- The collector already misses its third pull for some searches. In `serp_raw`, the 30 searches at list positions 25 to 30 had only 2 pulls on Nov 19 and on Nov 20, and positions 16 to 30 on Nov 18 (ReadTimeout in the pull C logs). With D5 every search would get at most 2 page-one pulls a day. Then the anchor rule (3 pulls) and the Checkpoint 5 truth rule (3 pulls on the score date) fail for every search.
- `collect_snapshot.py` (lines 632 to 645) has `--num` but no page option. D5 needs a code change to the other team's tool. It is not a settings swap.

Fix to paste.
Rung 2: "Widths are set on transitions ending on or before Nov 17. Coverage is tested on transitions ending Nov 18 and 19 only. Before any width is set, those test transitions are split by search group into two halves, frozen now: half 1 for G4.2 and half 2 for the fallback retest."
G4.2 fallback: "retest on half 2. If half 2 also fails, release with the bands marked untested."
D5: "Only if the collector keeps 3 full page-one pulls a day. Otherwise dropped."

### C6. MAJOR. The anchor rule cannot be met for positions 11 to 30 or for 40 of the 155 searches

Plan text. Mode A: "The page is in the stored top 30 of the search (at least 3 pulls on at least 2 days for the anchor ...)". Appendix C: "Anchor: ... from at least 3 pulls on at least 2 days".

What is wrong (counts from `serp_results`, through Nov 19).
- Positions 11 to 30 exist from one pull on one day only (`smartcrew-index-deep:2026-11-16:abspos`). No page at 11 to 30 can have an anchor. Mode A is a top-10 mode only.
- The 35 PR 41 baseline searches have 1 pull (Nov 16). The 4 ClockShark searches have 1 pull (Nov 17). The PR 45 target `raken pricing` has 1 pull. That is 39 of the 74 PR 41 ledger searches with no possible anchor.
- The rule is ambiguous. Read as "3 pulls on each of 2 days", only the 30 collector searches qualify, because the other 70 of the 100 have 3 pulls only on Nov 19.
- After Nov 19 only the 30 collector searches get new pulls. Any other fresh anchor costs 3 credits a search a day, and section 9 has no credits for it. So for a real edit in December the anchor will be weeks old.

Fix to paste into section 8 and Appendix C.
"Anchor: the median of at least 3 page-one pulls in total, over at least 2 distinct days, the latest no more than 3 days before the prediction. Mode A covers top-10 pages only. A page at 11 to 30, or with an older or thinner anchor, gets mode A with the flag 'thin anchor' and a band widened by the movement over the anchor's age. A fresh anchor costs 3 credits per search per day. Section 9 lists this as a per-use cost."

### C7. MAJOR. G4.2 cannot be computed for the 11 to 30 tier, and censored outcomes can fake coverage

Plan text. G4.2: "For each start tier (1 to 3, 4 to 10, 11 to 30) and each horizon with at least 150 rows: realized coverage ... within 7 points of stated."

What is wrong. I counted transitions that end Nov 18 or Nov 19 (rung 2's test days), using the consensus top 10 from `rsdata`.
- Tier 11 to 30: 0 rows at every horizon.
- Only horizons 1, 2 and 3 days reach 150 rows. Tier 1 to 3 has 243, 251 and 334 rows. Tier 4 to 10 has 553, 566 and 768. Horizons 5 to 13 have 87 rows (tier 1 to 3) and about 200 rows (tier 4 to 10).
- The end days have top-10 pulls only. 18% to 24% of tier 1 to 3 outcomes and 32% to 60% of tier 4 to 10 outcomes are pages that left the top 10, so their true position is unknown.
- `rsdata.py` sets `ABSENT = 11`. An agent who reuses it will score a band such as "4 to 13" as covering a page that fell to 40. That gives a false pass.

Fix to paste into G4.2.
"Coverage counts a row whose page left the top 10 as covered only if the band's upper end is 'beyond 10' (open). Otherwise the row counts as not covered. Coverage with those rows left out is reported next to it. Tier 11 to 30 and every horizon over 3 days are reported as untested. They are not merged into another cell."

### C8. MAJOR. The split's size check does not match the 3a validity bar, and 3a is now the only stage 3 test

Plan text. 2A.1: "Size check: XS-test-stored must hold at least 9 groups and at least 18 c100 searches with a deep list." G3: "Validity: at least 30 searches and 300 target rows" (applied to 3a as PROVISIONAL).

What is wrong. `evals/claude/r3/plans/universe.json` has 71 groups. 63 are construction groups (140 searches). The largest (35 searches, all in the 100) is forced into XS-design. That leaves 32 groups with 65 searches that have a Nov 16 deep list, and 30 groups whose searches have a single top-10 list only. I simulated 2000 seeds with a per-group hash at 35% (and also an exact 22-group quota). The mean was 21.9 test groups and 22.8 deep-list searches (5th to 95th percentile 10 to 38).
- The size check passes on about 0.69 of seeds.
- At least 30 deep-list test searches: 0.24 of seeds (0.35 of the seeds that pass the size check).
- At least 30 searches when the top-10-only lists count: 0.73 (0.97).
The plan does not say which searches count toward validity. If only the top-30 lists count, 3a is invalid about two times in three. If the top-10-only lists count, T-A mixes 10-row and 30-row lists, and the middle guess means two different things. After C3, 3a is the only stage 3 test, so this decides whether stage 3 says anything.

Fix to paste into 2A.1 and G3.
"Size check: XS-test-stored holds at least 30 searches with a Nov 16 deep list and at least 300 readable, non-platform rows in those lists. T-A and T-C on 3a use deep-list searches only. The 35 PR 41 and 4 ClockShark searches in the test fold are a separate, reported slice. Seeds are tried in order from 20261120 and every seed is recorded (about 4 to 5 seeds are expected)."

### C9. MINOR. kappa_A rests on far fewer independent sites than "about 465 pairs" suggests

Plan text. 4C.1: "About 465 such pairs exist, 43% from one site, so report the estimate with and without that site." G4.4: "by-site bootstrap 95% lower bound above 0".

What is wrong. I counted same-site pairs in the Nov 16 top-30 lists, non-platform, both pages with a stored fetch of at least 500 bytes. That is a looser rule than "readable", and it gives 378 pairs. hcss.com has 205 (54%). trimble.com has 70 and the client's domain 53. Without hcss.com there are 173 pairs. 47 of them are in the largest group, which is forced into XS-design, and 126 are elsewhere. So XS-design is expected to hold about 129 pairs, mostly from 3 sites. 174 of hcss.com's 205 pairs sit in one group of 16 searches, which has a 35% chance of landing in the test fold. A by-site bootstrap over about 3 effective sites cannot give a stable lower bound.

Fix to paste into 4C.1 and G4.4.
"Name the site (hcss.com). Report pairs and sites in XS-design with and without it. G4.4 is valid only if XS-design holds at least 8 sites with at least 5 pairs each after hcss.com is removed. Otherwise G4.4 is reported as untested, and mode A uses the fallback of G4.4 (direction only)."

### C10. MINOR. Stage 1 steps miss two things the scorer's guards require

Plan text. 1C.2: "Fetch pass, once, plain HTTP only: `python scripts/claude/r3_fetch.py --label heldout1-a`." 1A: "the three model folders ... check it against `freeze.py --help` first".

What is wrong.
- `r3_fetch.py` writes `evals/claude/r3/fetch/summary-heldout1-a-<stamp>.json` but does not commit it. `r3_score.py` `fetch_cutoff` refuses unless exactly one such summary exists and it was committed once. If every URL is already fresh, `r3_fetch.py` returns before writing any summary (`if args.dry_run or not todo: return 0`), and the scorer can then never run.
- `freeze.py` hashes paths with `read_bytes`, so a folder passed as an extra file crashes it. Every model file must be listed (the list is in `r4-data-inventory.md` section 10). `freeze.py` has no `--help`. Any unknown argument prints the docstring and exits 2.

Fix to paste into 1C.2 and 1A.
"1C.2: run `r3_fetch.py --label heldout1-a --dry-run` first. If `to_fetch` is 0, stop and ask, because the scorer needs one summary. Then run it for real. Commit and push the one `summary-heldout1-a-*.json` and its sidecar `.jsonl` once, before pull B. 1A: pass the model files one by one, as listed in `r4-data-inventory.md` section 10, never a folder."

### C11. MINOR. Stage 2 may start during the pull gap, but it reuses files that the round 3 manifest freezes

Plan text. Section 9: "Stage 2 groups A to C may start while stage 1's second pull waits out its gap". Appendix B: reuse `rsdata.py`, `r3_model.py`, `r3_features.py`, `r3_semantic.py`, `r3_dataset.py`, `metrics.py`, `offpage.py`, `site_history.py`. 2A.4: "replaces in-list standardisation with frozen pooled scales".

What is wrong. A static trace of `r3_score.py`, `r3_pull.py` and `r3_fetch.py` imports gives about 47 repo files for the closure manifest. They include `scripts/claude/rsdata.py`, `r3_model.py`, `r3_dataset.py`, `r3_features.py`, `r3_semantic.py`, `metrics.py`, `t2_changes.py`, `src/aeo/ranksim/offpage.py`, `src/aeo/ranksim/site_history.py` and `src/aeo/ranksim/metrics.py`. `r2_score.require_frozen_dir` runs `verify_manifest` at scoring time. A stage 2 edit to any of them, made in the gap (for example adding the list-invariant scale to `FrozenScorer`), makes `r3_score.py` refuse until the edit is reverted.

Fix to paste into section 3 rule 1 or section 9.
"Until `evals/claude/r3/heldout/claude-r3-samesite/results.json` is committed, no commit may change any file listed in `evals/preregistered/claude-r3-samesite/manifest.json`. Stage 2 work in the gap only adds new files. Stage 2 and stage 4 never edit a frozen file in place. They wrap it in a new module."

### C12. MINOR. The "frozen linker in r3_dataset.py" is a lookup table and cannot screen new searches

Plan text. 3.3: "the frozen linker in `scripts/claude/r3_dataset.py`". 3D.1: "the frozen linker puts it in no existing search group".

What is wrong. `r3_dataset.search_groups()` only maps the 155 stored searches to their group ids from `universe.json`. A new string gets no group, so every candidate would pass the screen, including near-duplicates of stored searches. The real linker is `r2_samesite_pairs.assign_folds`. It is a union-find, so running it on stored plus new searches can chain and merge frozen groups. The pool itself is large enough. The stored responses through Nov 19 hold 1,028 distinct related searches (994 after removing the 155 stored searches) and 542 "people also ask" strings. Testing the linker's pair rule against each stored search, one pair at a time, leaves 460 related searches and 396 questions with no link.

Fix to paste into 3.3 and 3D.1.
"Linker: the pair rule of `r2_samesite_pairs.assign_folds` (same folded tokens, a shared token trigram, or one token set inside the other with at least 2 tokens), applied to each candidate against each stored search one pair at a time. Frozen groups are never merged. New groups are formed among the candidates only."

## Things the plan gets right (checked)

- The guards accept the commits the plan inserts. The gates commit lands before the round 3 freeze. The PR 45 ledger commit lands after it. `r3_score.py` takes the freeze commit as the last commit touching `manifest.json` and only needs each chunk commit to descend from it.
- The stage 1 commands match the code. `r3_pull.py` takes `--label`, an absolute `--plan` and `--max-credits 104`, and `heldout1.jsonl` has 104 lines. `freeze.py closure <dir> <modules> -- <files>` exists. `r3_pr45.py predict` verifies the round 3 manifest first and refuses to overwrite.
- Disk is enough for stage 2. `/workspace` has 48 GB free against about 10.6 GB of Common Crawl files. scipy, sklearn, lightgbm and onnxruntime are in `.venv`. pyarrow is missing, and the plan already says to use `index.sqlite` and allows `.csv.gz`.
- The fresh-search pool counts match the store: 1,028 related searches and 542 questions through Nov 19.
- The plan already flags that the collector's default reserve is 200, not 250.

## Unverified suspicions

- Serper may bill timed-out requests. About 6 credits a day are charged with no stored response. `r3_pull.py` retries a network error up to 4 times, so a pull with many timeouts could cost more than 104.
- Memory. The box has 15 GB of RAM, about 6 GB free and no swap. Loading the 135 million row Common Crawl vertex file into pandas would likely fail. The memo streams it, but the plan does not require that.
- One streaming pass over 1.8 billion edges in Python may take several hours, not about 1.
- The collector's caller is not on this box (no crontab), so I assumed the script default reserve of 200.
- The searches that miss the third daily pull are the last ones in the collector's file order. If that order changes, the 7 PR 41 searches in the collector list could lose their third pull too.
