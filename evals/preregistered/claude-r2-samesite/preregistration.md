# Pre-registration: round 2 loop 1, same-site content check (Target 2 diagnostic, Claude goal run)

Label: PREDICTED. Written 2026-11-19, about 20:15 ET, before any day after 2026-11-17 was read. Nothing here changes an earlier gate, the three round 1 preregistrations, the SmartCrew PR 41 ledger, or the product ranker. Every rule and number below is taken from `evals/claude/theory.md` section 2, the docstring of `scripts/claude/r2_samesite_check.py`, and its design dry run. Nothing is new and nothing is tuned.

This is a diagnostic, not a Target pass. It compares two different pages that already rank, at one point in time. It does not show that changing a page changes its rank.

## What is frozen

The sha256 of every file below is in `manifest.json` in this folder, written with `scripts/claude/freeze.py closure` on the module `scripts.claude.r2_samesite_check`.

- `scripts/claude/r2_samesite_check.py` (the check)
- `scripts/claude/r2_samesite_pairs.py` (the design study's pair builder, BM25F corpus and sign vote, imported unchanged)
- `scripts/claude/t2_changes.py` and `scripts/claude/t2_common.py` (page extraction and the fetch table), `src/aeo/crawl/extract.py`
- `scripts/claude/rsdata.py`, `metrics.py`, `score_heldout.py`, `heldout.py`, `r2_score.py`, `freeze.py`, and every other repo file they import. The manifest lists them all, with the Python, numpy and pandas versions.

Nothing is fitted. The script calls `r2_score.require_frozen_dir` on this folder before it loads anything and refuses to overwrite a scored file.

## What is measured

- Pairs: for each listed day and each search in groups o30 and a70 (groups computed from SERP rows dated 2026-11-17 or earlier), every two URLs in that day's consensus top 10 at different positions, built by `r2_samesite_pairs.pairs_u2`. Same site means the same registrable domain. Cross-site pairs of the same lists are the reference.
- Pooled table: one record per (search, URL pair) across the listed days, labeled by the order that holds on most days (exact splits dropped). A per-day table uses each day alone.
- Pages: for each URL the usable stored fetch (at least 50 words of main text) nearest the pair's day, never after the last listed day. A pair without a usable page for both URLs, or whose two pages have the same title and main text, is counted and left out.
- Scorers, both fixed: **BM25F** over title, H1, h1 to h3 headings and main text (higher first), and the **text vote**, a sign vote over seven text features (title_cov, h1_cov, slug_cov, title_phrase, bm25_body, bm25f, first100_cov). theory.md names "the eight-feature text vote". The script drops the eighth feature, serp_title_all, because the SERP title is Google's own output. A tie counts as half a hit.
- Metric: share of pairs where the scorer puts the higher-ranked URL first, same-site and cross-site, and same-site minus cross-site. Also reported: same-site without the top 5 sites, decided pairs only, and new against seen pairs.
- Intervals: 95% query bootstrap, 2000 draws, seed 20261119.

## Searches and held-out days

- Searches: o30 and a70, the 100 SmartCrew searches.
- Held-out days: 2026-11-18 and 2026-11-19, in one run: `python scripts/claude/r2_samesite_check.py --prereg evals/preregistered/claude-r2-samesite --days 2026-11-18,2026-11-19`. Output in `evals/claude/heldout-r2/claude-r2-samesite/`.
- Nov 18 has the original 30 only (two or three pulls each), scored as is. Nov 19 has the original 30 (pulled in the morning by the earlier team's collector, third pull stopped early) and the 70 added searches (three pulls by this run). As in the dated addenda of the three round 1 preregistrations, Nov 19 is scored as pulled, so this run uses `--allow-incomplete`.
- The run is made once.

## Prediction made before scoring (PREDICTED, copied from theory.md section 2)

"A frozen same-site content check on held-out days (top 10 only, consensus order): BM25F and the eight-feature text vote, nothing fitted. This is the brief's counterfactual diagnostic done on real pairs. It is a diagnostic, not proof. PREDICTED: between 0.55 and 0.65, above the cross-site rate."

## Rule

As coded in `prediction_check`, on the pooled records: for each of BM25F and the text vote, the same-site rate (point estimate, ties half) is at least 0.55 and at most 0.65, and it is above that scorer's cross-site rate on the same lists. The prediction holds only when all four parts hold. The interval of the same-site minus cross-site difference is reported and is not part of the prediction. theory.md gives no separate "what would prove this wrong" for this check, and no Target passes or fails on it.

## Design numbers (DESIGN, not a test)

- theory.md, from the design study (`evals/claude/r2/samesite/notes.md`, Nov 16 lists, positions 1 to 30): BM25F puts the higher same-site page first in 277 of 464 pairs (0.597) [0.548, 0.647] against 0.522 across sites. On every top-10 consensus day through Nov 17 and every search group (its universe U2): BM25F 96/159 (0.604), cross-site 0.515; eight-feature vote 96.5/159 (0.607).
- This script's own dry run on design days 2026-11-16 and 2026-11-17, o30 and a70 (`evals/claude/r2/samesite-check-design/`), pooled: 68 usable same-site pairs on 31 searches and 9 sites, 94% of them from the top 5 sites (hcss.com 44).

| Scorer | Same-site | Cross-site | Same minus cross |
| --- | --- | --- | --- |
| BM25F | 34/68 (0.500) [0.375, 0.684] | 1157/2217 (0.522) [0.491, 0.552] | -0.022 [-0.156, +0.174] |
| Text vote | 38/68 (0.559) [0.475, 0.673] | 1158/2217 (0.522) [0.495, 0.550] | +0.036 [-0.049, +0.151] |

On that dry run the prediction did not hold: BM25F was 0.500, below 0.55 and below its cross-site rate. The same script on all design consensus days and all groups gives BM25F 96/158 (0.608) and the text vote 95.5/158 (0.604).

## Date and seal

Written 2026-11-19 before any SERP row or page fetch dated 2026-11-18 or later was read by this run. The script was run on design days only (`--design`, days through 2026-11-17). No held-out day was scored before this file and its manifest were committed and pushed.
