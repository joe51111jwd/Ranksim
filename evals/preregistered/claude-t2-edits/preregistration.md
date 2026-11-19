# Pre-registration: Target 2, edit direction (Claude goal run)

Label: PREDICTED. Written 2026-11-19 before any day after 2026-11-17 was read. Nothing here changes an earlier gate, the SmartCrew PR 41 ledger, or the product ranker.

## The target, from GOAL.md

"Build a dataset of real (query, URL) pairs where the page HTML changed between snapshots and the consensus rank moved. Predict up vs down from the content diff plus features. Pass = accuracy on held-out pages/queries/time beats the majority-class baseline and a 'no information' coin with CI lower bound above 0.5 + margin, n >= 40. If there are not enough real changed pages, say so and build the collection pipeline that will get them, plus a counterfactual pairwise test (diagnostic only, not proof)."

## What is frozen

The sha256 of every file below is in `manifest.json` in this folder. `scripts/claude/score_heldout.py` refuses to score when a hash differs, when the freeze is not committed, or when it is not pushed.

- `scripts/claude/t2_changes.py` (the material-change rule, version `t2-material-v1`)
- `scripts/claude/t2_common.py`, `scripts/claude/t2_dataset.py` (fetch pairing, alignment to pull days, row building)
- `scripts/claude/t2_direction.py` and `evals/claude/t2-edits/params.json` (the direction rule)
- `scripts/claude/rsdata.py`, `scripts/claude/metrics.py`, `scripts/claude/score_heldout.py`

Nothing is refit on a held-out day.

## Rows

One row per (search, URL, fetch pair A to B). A and B are consecutive days on which the URL has a usable plain fetch by the same method. The position at A is the search's consensus on the latest pulled day in [A minus 1, A]. The position at B is the consensus on the earliest pulled day in [B, B plus 1]. Out of the top 10 counts as 11. A row "moved" when the two positions differ.

- **Material change.** Both versions extract at least 50 words of main text, and one of: the title changed; the H1 changed; at least 250 edited tokens that are also at least 2% of the two versions' tokens; or the word count changed by at least 25% and at least 150 words. Digits are masked. Headings, dates, schema and link counts never make a change material on their own. On design data this flagged 0 of 54 same-day re-fetches.
- **Held-out rows.** Rows whose position at B comes from 2026-11-18 or later. The "after" pages come from the free re-fetch pass of 2026-11-19 03:28 to 04:00 ET and any later pass. Each pass fetches a URL list fixed before that day's pulls: every URL seen in a top-10 pull in the previous five days. That removes the selection problem in the design data, where a page was only re-fetched when it was still ranking.
- **Test rows.** Held-out rows with a material change and a move. **Control rows**: held-out rows with a move and no material change.

## The direction rule (PREDICTED), from `params.json`

In order: a URL outside the top 10 at A can only go up; a URL at 1 can only go down. If the change is material and the query-term count in the title plus the H1 went up, predict up; if it went down, predict down. Otherwise mean reversion: worse than its own earlier average by more than half a position gives up, better gives down. Otherwise down. The **position-only rule** is the same without the content step.

## Pass rule

- **L, the brief's wording.** On the test rows, n is at least 40, the rule's accuracy has a 95% query-bootstrap lower bound above 0.55 (the coin plus a margin of 0.05, fixed here), and its accuracy is above the larger class's share of those rows.
- **H, a guard added by this run.** On the same rows the rule beats the position-only rule, with the lower bound of the paired difference above 0.
- **Target 2 passes** only when L and H both hold.
- L without H is reported as "literal wording only" and is not counted as a pass. Reason, from design data: the position-only rule, which never looks at the page, scores 242/346 (0.699) [0.647, 0.744] on all moved rows and the same on changed and unchanged pages. Clearing L therefore says nothing about edits.

Also reported: accuracy without the client's own site, accuracy on rows inside the top 10 at A, and the same numbers on control rows.

## Design numbers (days through 2026-11-17, not a test)

303 same-method fetch pairs, 262 usable, 13 material. 28 rows with a material change, 17 of them moved (7 up, 10 down), from 10 URLs on 4 sites. 8 of the 13 changed pages are the client's own site.

| Rows | Larger class | Position-only rule | Frozen rule (position plus content) |
| --- | --- | --- | --- |
| 17 changed and moved | 10/17 (0.588) | 12/17 (0.706) [0.462, 0.895] | 11/17 (0.647) [0.438, 0.846] |
| 437 unchanged and moved | 265/437 (0.606) | 303/437 (0.693) [0.647, 0.736] | same as position-only |

Frozen rule minus position-only on the 17 rows: -0.059 [-0.222, +0.143].

## What we expect (PREDICTED, stated before scoring)

The held-out window (Nov 16 to Nov 19 and Nov 20) will probably give fewer than 40 changed-and-moved rows, in which case the test cannot be run and the report says so with the count. If n does reach 40, L may hold and H will not. Either way Target 2 is expected not to pass. The collection pipeline (`scripts/claude/refetch_html.py`, `scripts/claude/collect_day.sh`) and the counterfactual diagnostic (`scripts/claude/t2_counterfactual.py`) are the deliverables the brief asks for in that case.
