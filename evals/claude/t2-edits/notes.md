# Target 2 (edit direction): design-data notes

Owner: Target 2 worker. Data: design days only (SERP rows and page fetches dated 2026-11-17 or earlier, via the sealed `rsdata` loaders). No Serper call, no Nov 18+ snapshot read, no direct SQL. Every number below comes from a real run of a script in `scripts/claude/t2_*.py`. Model outputs are PREDICTED.

## Bottom line

1. There are not enough real changed pages. On design data: 303 same-method URL fetch pairs (all http), 262 usable, **13 material changes**, **28 (query, URL) rows with a material change, 17 of which moved** (7 up, 10 down). Those 17 rows come from 10 URLs on 4 sites, and 8 of the 13 changed URL pairs are the client's own site (smartcrewhq.com). n >= 40 is not reachable on design data.
2. On those 17 rows the page diff carries no measurable signal. The frozen predictor with its content step gets 11/17 (0.647). The same rule without the content step gets 12/17 (0.706). Paired difference: -0.059 [-0.222, +0.143].
3. **A content-free rule already clears the brief's literal pass bar.** Position alone (boundary effects plus mean reversion) scores 242/346 (0.699) [0.647, 0.744] on all moved test rows, against a majority baseline of 0.584. So "beats majority and coin with CI low > 0.55" does not prove content signal. **Target 2 should only count as passed when the content-aware predictor beats the position-only rule on the same changed rows (paired CI above 0).** The lead should freeze that requirement in the preregistration.
4. Direction is no more predictable on changed pages than on unchanged ones. Position-rule accuracy on material rows minus control rows: +0.007 [-0.224, +0.222].
5. Part E diagnostic: the frozen ranker gets 0.585 [0.549, 0.621] pairwise on a70 pairs. When the better page's title, H1, headings and body are swapped into the worse page, the edited page scores higher in only 0.496 [0.456, 0.537] of pairs, a coin flip. 94% of the model's gain is history features. It cannot rank edits.
6. The yield arithmetic is in section 7. At the observed change rate a scheduled re-fetch of the current 98 tracked URLs gives about 16 changed-and-moved rows a week, or about 7 a week without the client's own edits. n = 40 takes about 2.5 weeks. Showing that content beats position takes about 8 to 70 weeks on the current URL set, depending on effect size, unless far more URLs are tracked.

## 1. Material-change rule (frozen, `t2_changes.py`, RULE_VERSION `t2-material-v1`)

`diff_pages(old_html, new_html, url="", query=None) -> dict` is deterministic and pure. It reads no SERP, rank, date or DB. Text comes from the repo's own extractor (`aeo.crawl.extract.extract_html`, the one `pair_order.parse_html` uses for the frozen ranker).

Rule text, as stored in `MATERIAL_RULE`:

> usable (both versions extract >= 50 main-text words) AND any of: (a) title token sequence changed; (b) H1 token sequence changed (including gaining or losing an H1); (c) main-text edit >= 250 tokens AND >= 0.02 of the two versions' combined tokens, where an edited token is one not covered by any word 5-gram present in the other version; (d) main-text word count changed by >= 25% AND by >= 150 words. Tokens are lowercase [a-z0-9]+ with every digit run masked to '#'. Headings, meta description, schema types, canonical, dates and link counts are reported but never make a change material on their own.

How the thresholds were chosen (full table: `rulecheck.md`):
- Noise reference: 61 same-URL, same-method http re-fetches a few hours apart on Nov 16 ET (13:xx and 21:xx UTC). Of the 54 usable pairs, **0 are flagged**. Most design pairs have byte-identical masked main text (the median edit is 0 tokens).
- Masking digits and comparing tokens removes dates, counters, prices and case/punctuation edits. Example: smartcrew's "# person" to "#-person" heading edit, and "With" to "with" in a title, are not changes.
- Heading-set change is **not** a trigger. On the same day, hcss.com's homepage rotates 3 "industry insights" headings in and 3 out (Jaccard distance 0.35), the same size as some real heading edits.
- Examples below the text threshold, inspected by hand and judged noise or trivial: hcss.com rotating insight cards (edit 26 to 95 tokens); arch.tamu.edu "related news" (59 to 185); superconstruct.io "related posts" (65 to 127); agilityportal.io sidebar posts (103 to 113); dirtmatch.com (123); myquoteiq.com testimonial swap (33); trimble.com video "similar resources" (21 tokens on a 114-word page); stackct.com awards blurb swap (45); play.google.com reviews block (9).
- Examples above it, judged real edits: bidsyncai.com heavy-civil post rewritten (297 tokens, headings +2/-5); ownrops.com land-clearing guide copy rewrite (321); hcss.com construction project management product page (347, plus title and H1). Title/H1 triggers: agtek.com underground ("Underground Utility & Excavation Takeoff Software" to "Underground Utility Estimating Software"); agtek.com takeoff; smartcrewhq.com all-in-one pages ("Best all in one dirtworks software" to "Best All in One Dirtworks Software (2026): Honest Contractor Guide", H1 "All-in-One Dirtwork" to "All in One Dirt Work"); smartcrewhq.com dirt-work-software-with-crm, heavybid-alternative and best-software-small-dirt-work-contractors.
- The gap between the largest noise edit (185 tokens) and the smallest real body rewrite (297) is narrow. The threshold of 250 sits in it. This is a judgement call made on design data, and on held-out data it is applied mechanically.
- Unusable pairs (one side under 50 words) are never material: YouTube watch/shorts pages (about 24 extracted words), mitti.com, dirtworksoftware.com, and fetches that returned an 8-word error page (earthworksos.com, earthworkssoftware.com and remoteae.com in the Nov 16 21:xx UTC pass).
- Rule (d) did not trigger on any design pair. It is untested.

## 2. Alignment and row definition (`t2_common.py`, `t2_dataset.py`)

- A fetch's day is the America/New_York date of `fetched_at_utc`, the same convention as SERP `snapshot_date`. For example, the "Nov 6" snapshot pages were fetched at 2026-11-07T03:1x UTC, 22:1x ET on Nov 6. Note that `rsdata.load_page_index().fetch_day` is the **UTC** date, so it must not be compared to SERP days directly.
- Kept fetches: `fetch_method` in {http, browser}, HTML of 500 bytes or more, status 200 or unrecorded. Challenge pages, 4xx/5xx, robots blocks, serper_scrape and wayback are dropped. Date-only stamps (exactly T00:00:00Z: the Nov 16 index crawl, 7,739 http, and a browser pass, 96) never enter pairs, because their fetch time and crawler setup are unknown.
- One fetch per (key, method, day): the latest timed fetch whose text extracts to 50 words or more; if none qualifies, the latest timed fetch. The choice reads page content only, never a rank.
- Pairs are consecutive available fetch days of the same key and the same method. On design data every pair is http to http; there is no browser-to-browser pair across days.
- The SERP day for fetch A is the query's latest pulled day in [A-1, A]. For fetch B it is the earliest pulled day in [B, B+1]. On design data both always equal the fetch day: all 894 rows have serp_day_a = day_a and serp_day_b = day_b. Positions come from `rsdata.consensus_table` (single pull on Nov 6/9/11/13, voting session on Nov 16/17; absent or flicker = 11).
- Row universe: the (query, key) appeared in the query's consensus table on some day up to A. This uses information up to A only.
- Features: diff features (A vs B page), query-term counts and coverage in title/H1/headings/body at A and B and their deltas, and history up to and including A (pos_a, prev_pos, hist_mean_before, reversion = pos_a minus hist_mean_before, trend, days seen, spread and pulls on A, gap days). Labels: pos_b, delta, move.

## 3. Dataset counts (`counts.json`, `dataset.csv`, `fetch_pairs.csv`)

| A -> B | URL pairs | usable | material | rows | material rows (up/down/none) | control rows (up/down/none) |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| 11-06 -> 11-09 | 72 | 60 | 0 | 189 | 0 (0/0/0) | 160 (34/67/59) |
| 11-06 -> 11-11 | 3 | 3 | 0 | 3 | 0 (0/0/0) | 3 (1/1/1) |
| 11-06 -> 11-13 | 4 | 4 | 0 | 4 | 0 (0/0/0) | 4 (0/4/0) |
| 11-06 -> 11-16 | 2 | 1 | 0 | 2 | 0 (0/0/0) | 1 (0/1/0) |
| 11-09 -> 11-11 | 74 | 61 | 5 | 227 | 9 (2/3/4) | 175 (47/50/78) |
| 11-09 -> 11-13 | 7 | 6 | 1 | 9 | 1 (0/1/0) | 6 (2/2/2) |
| 11-09 -> 11-16 | 2 | 2 | 0 | 2 | 0 (0/0/0) | 2 (0/2/0) |
| 11-11 -> 11-13 | 62 | 52 | 1 | 217 | 5 (1/1/3) | 171 (44/60/67) |
| 11-11 -> 11-16 | 7 | 6 | 1 | 13 | 3 (0/1/2) | 5 (0/5/0) |
| 11-13 -> 11-16 | 70 | 67 | 5 | 228 | 10 (4/4/2) | 208 (44/73/91) |
| **total** | **303** | **262** | **13** | **894** | **28 (7/10/11)** | **735 (172/265/298)** |

- Only o30 queries contribute. a70 and p35 have a single SERP day, and the Nov 17 fetches (32 http, 9 browser) hit URLs with no earlier same-method fetch, so there is no Nov 16 to Nov 17 pair.
- Changed-and-moved rows: 17 from 10 URLs, 4 sites (smartcrewhq.com, agtek.com, bidsyncai.com, ownrops.com) and 12 queries. Without the client site: 8 rows.
- The scout note `smartcrew_scout.md` (written separately, not edited here) finds about 12 strict edit-and-move rows on 4 client pages, all inside a sitewide rise. It does not rescue n >= 40.

**Selection bias (important for held-out design).** The snapshot fetchers only fetched URLs in that day's top 10, so a fetch at B is partly conditioned on the B outcome. Among rows in the top 10 at A with a usable fetch at A, a same-method fetch on the next SERP day exists for 506/671 that stayed in the top 10 but only 122/257 that dropped out (`selection_check.csv`). The dataset therefore under-represents drops. A held-out collector must re-fetch a fixed URL list chosen at A (every URL seen up to A), whatever happens at B.

## 4. Direction predictor and controls (`t2_eval.py`, `eval.md`, `eval.json`, `design_predictions.csv`)

Test rows: usable pair, rank moved, day_a >= Nov 9, so every method has a prior labelled transition. Fitted models (logistic regression, C = 0.5, not tuned) are strictly forward. A test row with fetch day A uses a model fit on rows with label day B <= A, excluding every row of the test row's site and of its query. The fixed rules were designed on all design rows, so their design numbers are in-sample for those design choices. Query-bootstrap 95% CIs (`metrics.bootstrap_rate`, 2000 draws).

| subset | n (up) | majority | position_rule | content_rule | both_rule (frozen) | position_logit | content_logit | both_logit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| material | 17 (7) | 10/17 (0.588) [0.375, 0.769] | 12/17 (0.706) [0.462, 0.895] | 10/17 (0.588) [0.357, 0.824] | 11/17 (0.647) [0.438, 0.846] | 12/17 (0.706) [0.500, 0.882] | 10/17 (0.588) [0.333, 0.810] | 11/17 (0.647) [0.308, 0.864] |
| control | 329 (137) | 192/329 (0.584) [0.531, 0.629] | 230/329 (0.699) [0.646, 0.744] | 192/329 (0.584) | 230/329 (0.699) [0.646, 0.744] | 200/329 (0.608) [0.557, 0.650] | 188/329 (0.571) [0.519, 0.615] | 200/329 (0.608) [0.545, 0.666] |
| all | 346 (144) | 202/346 (0.584) [0.533, 0.629] | 242/346 (0.699) [0.647, 0.744] | 202/346 (0.584) | 241/346 (0.697) [0.644, 0.741] | 212/346 (0.613) [0.564, 0.653] | 198/346 (0.572) [0.523, 0.616] | 211/346 (0.610) [0.544, 0.671] |
| material, top 10 at A | 15 (5) | 10/15 (0.667) | 10/15 (0.667) | 9/15 (0.600) | 9/15 (0.600) | 10/15 (0.667) | 10/15 (0.667) | 9/15 (0.600) |
| control, top 10 at A | 295 (103) | 192/295 (0.651) [0.581, 0.709] | 196/295 (0.664) [0.616, 0.708] | 192/295 (0.651) | 196/295 (0.664) | 179/295 (0.607) | 187/295 (0.634) | 178/295 (0.603) |

Coin = 0.5 by definition.

Paired differences, same rows:
- material: both_rule minus position_rule **-0.059 [-0.222, +0.143]**; both_logit minus position_logit -0.059 [-0.385, +0.158]; content_rule minus majority +0.000 [-0.158, +0.188].
- all moved rows: position_rule minus majority **+0.116 [+0.058, +0.166]**; both_rule minus position_rule -0.003 [-0.013, +0.006].
- The content step fired on 7 of the 17 material rows and was right on 4. It changed the position rule's answer on 3 and made it worse on net.

Material minus control, queries resampled jointly: position_rule +0.007 [-0.224, +0.222]; both_rule -0.052 [-0.244, +0.149]; position_logit +0.098 [-0.119, +0.302]; both_logit +0.039 [-0.291, +0.263].

Most of the position rule's edge comes from the boundaries. pos_a = 11 can only go up, and pos_a = 1 can only go down. Within rows in the top 10 at A, the position rule barely beats "always down" (0.664 vs 0.651 on control rows). The fitted logistic models are worse than the fixed rule: there are few training rows, and site and query are held out.

## 5. Frozen predictor (`t2_direction.py`, `params.json`)

`predict_direction(features_row) -> 'up' | 'down'` (PREDICTED). In order: pos_a >= 11 gives up; pos_a <= 1 gives down. If the pair is material, content score = 1.0 x q_title_delta + 1.0 x q_h1_delta (change in query-term counts in the title and H1); a positive score gives up, a negative one down. This is an a-priori sign rule and is **not fit**. Next comes mean reversion: pos_a minus hist_mean_before above 0.5 gives up, below -0.5 gives down. Otherwise the default, down. `predict_direction_position_only` is the same rule without the content step, and it is the baseline that matters. The predictor reads only pos_a, has_prev, hist_mean_before, material, q_title_delta and q_h1_delta; it never reads pos_b, move, delta or day-B SERP data.

**Mechanical held-out entry point:**

```python
from scripts.claude.t2_dataset import build_rows
rows = build_rows(max_date="2026-11-20", min_day_a="2026-11-16")
scored = rows[rows["usable"] & (rows["move"] != "none")]
# truth: scored["move"]; PREDICTED: pred_both_rule, pred_position_rule, pred_content_rule, pred_majority
```

`build_rows(max_date: str = rsdata.DESIGN_MAX_DATE, min_day_a: str | None = None) -> pandas.DataFrame` returns every design-dataset column plus `pred_position_rule`, `pred_content_rule`, `pred_both_rule`, `pred_majority` ('down') and `pred_label` = 'PREDICTED'. The sealed loaders exclude everything dated after `max_date`. Pass a later `max_date` only after the preregistration is committed. With the default arguments it reproduces the design numbers above: on the 346 test rows, both 0.697 and position 0.699; material 11, 12 and 10 of 17.

Held-out notes for the scorer:
- Nov 19 fetches pair with each URL's latest earlier same-method fetch. For the o30 snapshot URLs that is usually Nov 16 http (13:xx or 21:xx UTC). Browser fetches only pair with browser fetches.
- If a Nov 19 fetch has no SERP pull the same day, the frozen tolerance uses the query's first pull within one day after it.
- Selection: if the Nov 19 re-fetch list was chosen from Nov 19 SERP membership, the bias in section 3 applies. If it is a fixed list, it does not.
- Expect very few material rows. At the design rate (0.018 material changes per URL-day across 98 URLs), Nov 16 to Nov 19 gives roughly 5 material URL pairs and about 6 changed-and-moved rows.

## 6. Counterfactual pairwise diagnostic (part E): DIAGNOSTIC ONLY, not proof

Script: `t2_counterfactual.py`. Outputs: `counterfactual.json`, `counterfactual_pairs.csv`. Status: complete, full run (all 70 a70 queries). The gain-share fields were recomputed afterwards with `--gain-only`, because the first run grouped LightGBM `Column_i` names wrongly (fixed in the file).

Results (PREDICTED, DIAGNOSTIC ONLY, query-bootstrap 95% CI, 70 queries):
- Coverage: 689 a70 top-10 rows; 478 have a usable Nov 16 page (http 250 URLs, browser 31, browser date-only 30). 1,440 (H, L) pairs. Off-page values exist for 488/689 rows.
- Plain pairwise accuracy, score(H) > score(L): **842/1440 (0.585) [0.549, 0.621]**, with 19 score ties.
- The frozen ranker is almost all history. Gain share by block: history **0.940** (url_mean_relevance alone 0.72, domain_query_relevance 0.10, last_relevance 0.04), off-page 0.021, URL 0.004, SERP title 0.002, on-page content **0.033** (bm25f 0.013, word_count_log 0.010).

| swap | score(L*) > score(L) | score(L*) = score(L) | score(H*) < score(H) | score(H*) = score(H) | score(L*) > score(H) | mean delta L / H |
| --- | --- | --- | --- | --- | --- | --- |
| head (title + H1 + headings) | 450/1440 (0.312) [0.263, 0.358] | 0.629 [0.576, 0.683] | 71/1440 (0.049) [0.030, 0.071] | 0.642 [0.595, 0.690] | 0.444 [0.405, 0.484] | +0.035 / +0.034 |
| head_body | 714/1440 (0.496) [0.456, 0.537] | 0.133 [0.098, 0.173] | 462/1440 (0.321) [0.279, 0.363] | 0.135 [0.102, 0.177] | 0.423 [0.384, 0.461] | +0.022 / +0.023 |
| head_body_serp | 721/1440 (0.501) [0.462, 0.542] | 0.121 [0.087, 0.160] | 474/1440 (0.329) [0.287, 0.371] | 0.125 [0.093, 0.164] | 0.420 [0.381, 0.459] | +0.022 / +0.023 |

For scale: the median within-query score standard deviation is 0.294, and the median |score(H) - score(L)| is 0.243.

Reading: swapping the better page's title, H1 and headings leaves L's score unchanged in 63% of pairs. When the score does move, it is not toward the better page: H also gains on average when it takes L's weaker signals (+0.034). With the body swapped too, the "improving edit" is preferred in 0.496 [0.456, 0.537] of pairs, a coin flip, and the "worsening edit" lowers H in only 0.321. The frozen ranker cannot be used to compare two versions of a page: its on-page features carry about 3% of its gain, and their response is not aligned with Google's order. This is a diagnostic on one day's single pull, not proof about Google.

What it does. It uses the 70 a70 queries (single top-10 pull `smartcrew-q100:2026-11-16`) and their Nov 16 ET page fetches, parsed with the ranker's own `parse_html` and requiring 50 words or more. History for `build_transition` is the o30 snapshot pulls of Nov 6, 9, 11 and 13 (all before the outcome day). Pairs: H ranked better than L, both with a usable page. Swaps:
- `head`: H's title, H1 and h1-h3 heading text go into L.
- `head_body`: also H's main body text and word count.
- `head_body_serp`: also H's SERP title.

L keeps its URL, page-type flags, dates and off-page values. H* is the mirror. Every swap rebuilds the whole query's candidate set, because BM25F IDF, the shared vocabulary and the within-query z-scores depend on the other pages. Reported with query-bootstrap CIs: score(L*) > score(L), score(H*) < score(H), exact ties, score(L*) > score(H), plain pairwise accuracy score(H) > score(L), and the frozen model's gain share by feature block.

Verification of "a70 was not in the ranker's training". `model-next.json` (sha256 bc4de502...a8ea7, checked by the script) was fit by `scripts/run_pair_experiments.py` on transitions of the five 30-query snapshot files listed in `evals/experiments-2026-11-16/log.md`. Their sha256 values match these files on disk:
- `/workspace/ranksim-live-baseline-2026-11-06/serp_serper_live.csv` (c70ca990...)
- `/workspace/ranksim-live-snapshot-2026-11-{09,11,13,16}/serp_serper_live.csv` (49a0c8d4..., e4c76d73..., 336211a3..., 65d0581d...)

The log shows 294 to 299 rows per file, about 30 queries times 10. The counterfactual script re-hashes these files and intersects their query column with the a70 set; the result is in `counterfactual.json` under `training_files`. Only the query column and hash are read, for provenance, never the rows as data. Caveat: `offpage.tsv` covers only domains from those five files, so a70-only domains get no off-page values. This does not affect a swap, because the domain stays fixed, but it weakens the plain pairwise accuracy.

## 7. Yield and weeks needed (`eval.json` -> `yield_and_power`)

Observed on design data: 262 usable URL pairs cover 728 URL-days on 98 URLs. Material rate: 13/728 = **0.0179 per URL-day**. Without the client site: 5/640 = 0.0078. Each material URL pair gives 2.15 (query, URL) rows, and 0.61 of those rows move. Design effect (moved rows per changed page): 1.42.

- Changed-and-moved rows per week: **about 16 for the current 98-URL set**, or 16.3 per 100 URLs. Without the client's own edits: **8.75 per 100 URLs**.
- n = 40 with a Wilson 95% CI low above 0.55 needs at least 29/40 correct (above 0.5: 27/40).
- Rows needed for 80% power to get the CI low above 0.55 (margin 0.05; one-sided alpha 0.025; design effect 1.42; at least 40):

| true accuracy | rows | weeks, current 98 URLs | weeks per 100 non-client URLs |
| --- | ---: | ---: | ---: |
| 0.60 | 1091 | 68 | 125 |
| 0.65 | 269 | 16.8 | 30.7 |
| 0.70 | 117 | 7.3 | 13.3 |
| 0.75 | 64 | 4.0 | 7.3 |
| 0.80 | 40 | 2.5 | 4.6 |

- **The test that matters is content beating position.** On design, the content step changes the position answer on 17.6% of changed rows. Detecting that it is right on 70% of those disagreements takes about 50 disagreement rows, which is about 278 changed-and-moved rows, about 17 weeks at the current set. At 80% right it is about 124 rows (7.7 weeks); at 60% it is about 1,112 rows (69 weeks). Scaling the tracked set to the full q100 top 10 (about 650 URLs) divides these times by about 6.6, if the change rate holds outside the client site.

## 8. What data would fix it (collection pipeline)

1. A scheduled re-fetch of a **fixed URL list**: every URL seen in any tracked query's top 10 (or top 30 from the deep pull) up to day A. Use plain http with the same headers every day, plus browser only for URLs that block http, always compared browser to browser. Collect daily if possible, at least every 2 to 3 days, in the same session as a 3-pull SERP consensus for all 100 queries. Fetch whatever B's SERP shows, to remove the selection bias in section 3.
2. Track the full q100 set (about 650 URLs), not 98. Exclude, or report separately, the client's own site, whose edits are deliberate experiments correlated with a sitewide trend (see `smartcrew_scout.md`).
3. Score with `build_rows`. The pass rule should require that `pred_both_rule` beats `pred_position_rule` on the same changed-and-moved rows, with the paired query-bootstrap CI above 0, as well as the brief's n >= 40 and CI low above 0.55.
4. Controlled edits: real edits on pages we own, with randomized direction (some edits add query terms, some remove them), are the only way to break the confound between "who edits" and "what is trending".

## 9. Leakage risks checked

- Seal: SERP rows only via `rsdata.load_rows(DESIGN_MAX_DATE)`, pages only via `load_page_index(DESIGN_MAX_DATE)` + `load_page_html`. `build()` asserts that the max SERP day and the max ET fetch day are no later than max_date. No snapshot directory from Nov 18 or later was read, and no direct SQL was run. The only other files read are the five frozen-ranker training SERP CSVs (Nov 6 to Nov 16), for hash and query-name provenance in part E.
- Features vs labels: pos_b, delta and move are written after all features and are never read by `predict_direction`. History uses days up to the SERP day matching A. The day-B page content is allowed by the brief.
- The fetch selection reads page content only (word count), never ranks. However, the existence of a day-B fetch depends on day-B SERP membership (section 3). This is an outcome-dependent sample on design data, documented and not corrected.
- Fitted models: forward (label day B <= test A), with the test row's site and query held out. The fixed rules and the material thresholds were chosen on all design rows, so design accuracies for them are in-sample. The frozen file is only a real test on held-out days.
- Content features and rank: the material rule was tuned on noise pairs and hand inspection of diffs, never on moves.
- Duplicate pages: smartcrewhq.com/all-in-one-dirt-work and /all-in-one-dirt-work-software have identical text (probably an alias). They are separate keys on the same site, and site grouping keeps them together.
- Counterfactual: a70 queries verified absent from the training files. The swap keeps URL, domain and off-page values fixed, so it isolates on-page fields.

## 10. Caveats and possible issues in rsdata/metrics (reported, not edited)

- `load_page_index().fetch_day` is the UTC date, while SERP `day` is the ET date. The Nov 6 snapshot pages show fetch_day 2026-11-07. This is not a seal bug (the seal limit is max_date+1 T05:00Z, which is right for EST), but joining on `fetch_day` misaligns. `t2_common.fetch_table` converts to ET.
- The Nov 16 index crawl and browser-pass records carry date-only stamps `2026-11-16T00:00:00.000Z`, which convert to Nov 15 19:00 ET. Treat them as date-only.
- `query_groups`: o30 has 31 queries on Nov 6 (one query pulled only that day). Harmless.
- `metrics` worked as documented. No bug found.

## 11. Files and sha256 (final)

```
7f70062aef22f042fc738c587a55e0821091497f915b9f4b195072b3de5e7a59  scripts/claude/t2_changes.py
a5f56d0918bef859a7ee4ad9f3d0b90da618193ecdfc1b39ac45ac8b637694ca  scripts/claude/t2_common.py
3778ba574b39941f00083131fd3283ae54d4896b1cf6891ba142ea05a643070b  scripts/claude/t2_counterfactual.py
f25484edf5047135fdfde1241392e6b0bdc31770a4859ff44fe37abc2b5cdfcd  scripts/claude/t2_dataset.py
ade3917f7ee0de9da2592272670e72c005ca86a452ba7cfc1178bc98911617e3  scripts/claude/t2_direction.py
ebffcb173fa767f260b1f97c93f33adcecd7b4098b1699021d853aa245b9277b  scripts/claude/t2_eval.py
724846e13b49cde6a275567d2cfaa7cce7763b89d7ec983d8659d3888a2af9a1  evals/claude/t2-edits/params.json
```

Reproduce: `python -m scripts.claude.t2_dataset && python -m scripts.claude.t2_eval && python -m scripts.claude.t2_counterfactual` (from the repo root, venv active). The extraction cache lives in `/tmp/t2-extract-cache-v1.pkl` and holds derived fields only. No HTML is in the repo.
