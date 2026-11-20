# Ranksim master plan: from same-site content order to an approximate Google rank

Written 2026-11-20 by the lead (Claude goal run), branch `claude/goal-ranksim-works`. This is a plan and a theory. No production code was written and no Serper credit was spent to make it. Every model number in it is labeled PREDICTED, SIMULATED or DERIVED. Every REAL number names its source. James reviews this file before any coding agent runs a stage.

Product goal (James): "make a meaningful change to a page, run the predictor, and get an approximate predicted Google rank position. Don't worry about tiny day-to-day edits or knowing exactly what to fix yet."

Research behind this plan (seven memos, each claim graded and linked, all in `evals/claude/research/`):

| Memo | Topic |
| --- | --- |
| `r4-rank-aggregation.md` | Bradley-Terry, Plackett-Luce, pairs to positions, calibration, selection |
| `r4-site-strength-signals.md` | Every free site-strength signal, with live reachability tests from this box |
| `r4-cross-site-evidence.md` | Published cross-site prediction results, how Google mixes relevance and site standing |
| `r4-statistical-design.md` | Derivations and simulations: variance split, ceilings, placement error, power, intervals (code in its appendix) |
| `r4-data-inventory.md` | Real counts of everything stored on this box, and the code that already exists |
| `r4-free-label-sources.md` | Credit mechanics, free label and page sources, label efficiency |
| `r4-edit-simulator-design.md` | Prior art, the anchored design, validation ladder, ledger statistics, output format |

---

## 0. Summary for James

### 0.1 The five conclusions that shape the plan

1. **The cross-site gap is mostly solved by anchoring, not by modelling.** A page that already ranks carries its site's strength, links and clicks inside its current position. For that page the simulator only has to predict the change from the edit. That needs the content scorer and one scale number. It does not need a site-strength model at all. Site strength is only needed for a page with no current rank.
2. **Site strength decides who reaches page one. Content decides little inside page one.** REAL, design day Nov 16, 100 searches: a free link count (Majestic referring subnets) orders 0.633 [0.607, 0.659] of pairs with one page in the top 10 and the other at 11 to 30, but only 0.517 of pairs inside the top 10 (`r4-free-label-sources.md` section 6). Published work agrees: Moz Domain Authority correlates 0.26 with position over a top 50 and 0.071 inside a top 10.
3. **The earlier "global authority points the wrong way" result was a counting artifact.** The 0.377 to 0.420 figures counted score ties and missing values as wrong (`src/aeo/ranksim/snapshot_protocol.py`, `pair_counts`). A recount on decided pairs gives 0.528 to 0.548 (`r4-statistical-design.md` section 0). So global authority is weakly right, not harmful. The plan treats it as a weak gate signal.
4. **The realistic ceiling is far below 90% pair accuracy.** Two pulls of the same search agree on 0.87 to 0.93 of pairs. The best transparent published cross-site result is 62.5% to 67.5% (Bifet 2005), and 65% to 66% with domains held out (Critchlow 2017). SIMULATED from our own measured numbers: inside a top 10, content plus a good site estimate reaches 0.58 to 0.60, and even a perfect site value plus our content scorer reaches 0.66 to 0.71. Section 2.8 has the full table.
5. **So the honest product is a band, a tier and a direction, not an exact position.** For a page that ranks: the chance the edit beats leaving the page alone, the expected change, and an 80% band. For a new page: tier probabilities (top 3, 4 to 10, 11 to 30, beyond 30). An explicit "cannot tell" state is part of the product.

### 0.2 The stages at a glance

| Stage | What it does | Credits | Gate to advance (short form, exact form in the stage) | PREDICTED chance of passing |
| --- | --- | ---: | --- | --- |
| 0 | Pre-flight: push, freeze the gate file, credit guard | 0 | Branch pushed, gates frozen, balance at least 468 | near 1 if D1 is settled |
| 1 | Freeze and score the same-site content scorer on unseen sites (Checkpoint 1) | 208 | Goal target: two-way lower bound above 0.65. Advance rule: point at least 0.65, lower bound at least 0.60, beats BM25F | goal 0.45, advance 0.85 |
| 2 | Site strength from free signals, tested on stored lists (Checkpoint 2) | 0 | Site-only model on page-one boundary pairs: lower bound above 0.60 and at least 0.02 above the best single free signal | 0.6 |
| 3 | Content plus site strength, pairs to positions, sealed test on fresh searches (Checkpoints 3a and 3b) | 90 to 180 | Placement error beats the middle guess and the domain prior with intervals above zero, bands calibrated, boundary pairs lower bound above 0.58 | 0.35 for all gates |
| 4 | The edit simulator: anchored prediction, sanity suite, band backtest, live ledgers (Checkpoints 4 and 5) | 0 | Sanity suite, band coverage and virtual-edit gates. Live ledgers never block | 0.7 |

Stage 4's anchored modes do not depend on stage 3 passing. If stage 3 fails, the simulator ships for pages that already rank and says "cannot tell" for pages with no anchor.

### 0.3 Decisions needed from James

| # | Decision | Why it matters | Default if no answer |
| --- | --- | --- | --- |
| D1 | Cap or pause the other team's daily collector (about 90 credits a day) until the stage 1 and stage 3 pulls are done, and set `RANKSIM_CREDIT_RESERVE=250` in its caller | Balance is 617 (read 2026-11-20 09:04 ET). 367 can be spent. Stage 1 needs 208. After two more collector days stage 1 no longer fits. The collector's own default floor is 200, not 250 (`/workspace/ranksim-data/collect_daily.sh` line 13) | Run stage 1 at once. Stage 3 falls back to stored data only (Checkpoint 3a, provisional) |
| D2 | Connect Google Search Console for the client's site (free, read only) | The only free source of the client's positions past page one and of before and after views for PR 41 and PR 45. Exports through mid November are already on disk | Use the exports on disk only |
| D3 | Allow one free signup for extra Google lists (for example the DataForSEO $1 trial) | Only needed if a larger stage 3 test is wanted than the credits allow | No signup |
| D4 | Allow the client's domain to be sent to free public lookups (Google autocomplete, Wikidata, RDAP) | Brand-demand and age signals for the client's site | Client domain is never sent. Its values come from local data only |
| D5 | Swap one of the collector's three daily page-one pulls for a page-two pull (same 90 credits) | Free daily labels for positions 11 to 20, which is where site strength shows | No swap |
| D6 | Commit `scripts/claude/t1b_postmortem.py` (untracked, but its outputs were committed in `f0da90c`) | The repo rule says every number comes from a committed script | Left untracked and flagged |
| D7 | Allow paid LLM calls for an LLM-judge content scorer | Only a fallback if stage 1 fails. Costs real money | Not used |

---

## 1. Where we stand (measured, with sources)

| Fact | Value | Source |
| --- | --- | --- |
| Two ordinary pulls of one search, 2 to 3 days apart, same pair order | 0.870 of all pairs | `RESULTS-claude.md` 5.1 |
| Same site-restricted list pulled twice, 40 minutes apart | 0.877 of pairs. Adjacent 0.839, five or more apart 0.932 | `RESULTS-claude.md` 5.1 |
| Label ceiling against one pull | 0.91 to 0.93 | `r4-statistical-design.md` 1.2 |
| Site-restricted order against ordinary order | 62 of 74 pairs (0.838) | `RESULTS-claude.md` 5.1 |
| Same-site content scorer `page-blend`, design cross-validation | 0.709 site held out, 0.704 site and search group held out, two-way [0.668, 0.736]. BM25F 0.614. Unfitted cross-encoder 0.694 | `evals/claude/theory.md` R3.3 |
| Same scorer by gap | adjacent pairs 0.580, five or more apart 0.832 | theory R3.3 |
| The scorer's state | Fitted. Not frozen (no `manifest.json`). Held-out lists never pulled | `r4-data-inventory.md` 10 |
| BM25F on same-site pairs in ordinary top-30 lists | 277 of 464 (0.597) against 0.522 across sites | `RESULTS-claude.md` 4.1 |
| BM25 across sites, forward tests | 0.444 to 0.559 | `docs/MISSION.md` |
| Persistence for pages that stay | 0.86 to 0.89 of pairs | `docs/MISSION.md` |
| Current product ranker | 94% of gain from history, 3.3% from content | `RESULTS-claude.md` 3.5 |
| Short-window edit effects | Changed pages moved 0.517 of the time against 0.481 for matched unchanged pages, +0.036 [-0.065, +0.140] | `RESULTS-claude.md` 4.3 |
| Global authority inside top 10s, decided pairs | Tranco 0.528 to 0.529, Common Crawl PageRank 0.535 to 0.544, harmonic centrality 0.546 to 0.548 | `r4-statistical-design.md` 0 |
| Majestic referring subnets, Nov 16 top-30 lists | boundary pairs 0.633, inside top 10 0.517, inside 11 to 30 0.506, all pairs 0.571 | `r4-free-label-sources.md` 6 |
| One-day movement of an unedited page | within 1 place: 0.72 at positions 4 to 10, 0.86 at 1 to 3. Within 2 places: 0.82 at 4 to 10 | `r4-edit-simulator-design.md` |
| Ten-day movement inside the core update (Nov 6 to 16) | 50% of pages at 4 to 10 within 2 places. Half left the top 10 | same |
| Stored ordinary lists | 100 construction searches (c100), plus 35, 4 and 15 other searches. 428 domains in c100 top 10s. 256 of them on one search only, 31 on more than ten | `r4-data-inventory.md` 2 |
| Deep lists | One day only: Nov 16, 100 searches, positions 1 to 30 (70 reach 30). 16,264 cross-site pairs with both pages readable | `r4-data-inventory.md` 3 |
| Platforms in c100 top-10 rows | 22.3% user-content platforms, 10.0% review aggregators. Readable 22.7% and 44.4%, against 88.9% for ordinary sites | `r4-data-inventory.md` 2 |
| Global lists and the client | Majestic Million and Tranco each cover about 243 of 428 c100 domains. The client's domain is in neither | `r4-data-inventory.md` 7 |
| Serper balance | 617. Floor 250. Stage 1 needs 208 | read 2026-11-20 09:04 ET |
| A Google core update | Started Nov 10. Not marked complete on Nov 20 | `r4-edit-simulator-design.md` 5 |
| PR 45 ledger | Not written. Blocked on the stage 1 freeze. 6 of 9 target searches have a before rank | `r4-data-inventory.md` 9 |
| PR 41 ledger | 147 rows, 143 frozen, no outcome yet. Score dates Dec 2 (plus 14) and Dec 16 (plus 28) | `r4-data-inventory.md` 9 |

---

## 2. The theory: how content plus site strength gives an approximate rank

### 2.1 The model

For search q and page p on domain d, Google orders pages by a total score. Its own call notes say top-level signals are "a linear combination of log of individual raw signals", and that quality is "largely static and largely related to the site rather than the query" (trial exhibits PXR0357 and PXR0356). So write

    u(q, p) = kappa * m(q, p) + S(q, d) + r(q, p) + noise

- `m(q, p)`: the content score of the page for the search. This is the frozen same-site scorer.
- `S(q, d)`: the **site part**. Everything that is the same for all pages of one site on one search: site quality, site-level links and clicks, topical focus of the site, and brand affinity (the search names the site's brand).
- `r(q, p)`: the page's own unseen part: its clicks on this search, its outside links, its age.
- `noise`: pull-to-pull order noise.

Inside one site, `S` cancels. That is why the same-site scorer could be built and tested without any site data. Across sites, `S` does not cancel, and that is the gap.

### 2.2 What the measured numbers imply

**How much of the within-site order the content scorer explains.** For two normal variables with correlation rho, pair accuracy is `1/2 + arcsin(rho)/pi` (Greiner's relation, used by Bifet 2005). Inverse: `rho = sin(pi * (a - 1/2))`.

- Observed 0.709 gives rho 0.61.
- The label is right about 0.91 to 0.93 of the time. Corrected, the scorer is right on 0.72 to 0.74 of pairs against Google's settled order, rho 0.63 to 0.69.
- So the content score explains **40% to 47%** of the within-site variance of pair differences. The rest is the page's unseen part.

**How large the site part is.** Let R be the variance of the site part divided by the within-site variance, among the candidate pages of a search. Inside a list that Google selected on the total score, the right estimate is the linear form (derived and checked by simulation in `r4-statistical-design.md` 1.3):

    1 + R  is about  (a_same - 1/2) / (a_cross - 1/2)

With BM25F at 0.597 on same-site pairs and 0.522 on cross-site pairs of the same ordinary top-30 lists: `R is about 3` (plausible range 1 to 10). So among candidate pages the site part holds about 75% of the score variance, the content score about 10% to 12%, and the page's unseen part the rest. This matches Google's own description of quality as mostly a site property.

### 2.3 Why content alone is near coin across sites, and why authority looks weak inside a top 10

Google shows the top 10 of many candidates, chosen on the total score. Inside that selected set every part of the score has a narrow range. Three consequences, each derived and simulated in the memos:

1. **Range restriction.** A signal that holds half of the score orders 0.72 of pairs across the whole candidate pool and only 0.58 inside the top 10 (`r4-cross-site-evidence.md` 5.2).
2. **Trade-off inside the list.** Content and site strength are unrelated among candidates but correlate at about -0.19 inside the top 10: weak sites are there because their page fits very well (`r4-rank-aggregation.md`).
3. **No sign flip.** Selecting on the total score shrinks a true signal but cannot reverse it (proof in `r4-statistical-design.md` 2.1). The recount in section 1 confirms this: global authority is at 0.53 to 0.55 inside top 10s, not below one half.

With R near 3, the content score alone is PREDICTED to order 0.53 to 0.54 of cross-site pairs inside a top 10. The measured BM25 range was 0.444 to 0.559. So nothing is broken in the content scorer. It is simply the small part of a cross-site comparison.

Where site strength does show is the page-one boundary: 0.633 for a single free link count on pairs that straddle position 10. So the cross-site model must be trained and tested on lists deeper than 10.

### 2.4 Two-step identification

Content weights must never be learned from cross-site pairs without site effects. In simulation, a page feature that only tracks site strength then gets the same weight as real content, and the simulator would tell a user that changing it moves rank (`r4-statistical-design.md` 3.3, cosine to the true weights 0.74 against 0.996 for the two-step fit).

**Step 1, within site (done in round 3).** On same-site pairs, `P(i above j) = Lambda(beta' (x_i - x_j))`. The site part cancels exactly, as in a conditional logit with fixed effects (Chamberlain 1980). This gives `m = beta' x`.

**Step 2, across sites (stage 2 and 3).** On ordinary lists down to position 30, with `m` frozen:

    P(i above j) = Lambda( kappa (m_i - m_j)
                         + gamma' (zbar_i - zbar_j)      site means of the content score (topical focus)
                         + w' (x_d(i) - x_d(j))          free site features
                         + g' (a_i - a_j)                query-to-domain affinity flags
                         + (b_d(i) - b_d(j)) )           per-domain effects, ridge penalised

`kappa` is one free scale, because the scale of a coefficient changes with the label design (3.6 times between top-10 pairs and lists with known losers in simulation). The domain effects make the model honest: with them, the content weights stay clean.

### 2.5 The site-strength estimator

Each domain's effect is shrunk toward what its free features predict (empirical Bayes, Morris 1983):

    prior:      S_d ~ Normal(w' x_d, tau^2)
    estimate:   S_hat_d = B_d * (w' x_d) + (1 - B_d) * r_d,     B_d = v_d / (v_d + tau^2)
    variance:   v_d * tau^2 / (v_d + tau^2)

`r_d` is the domain's own evidence from the lists it appears in and `v_d` its sampling variance. A site seen on many searches is judged by its own results. A site seen once, or never, falls back on its features, and its wide variance is carried into the output band. SIMULATED: a domain needs about 5 to 10 listed appearances to pin its effect to plus or minus 0.3 to 0.4 logits. In c100, 58 of 428 domains appear on six or more searches and 256 on one only, so features matter for most small sites.

The main feature is the site's **niche footprint**: how often and how high the site ranks on the niche's other search groups. It is always computed with the target search group left out, so it cannot restate the label.

### 2.6 From pairwise wins to a position

- Expected rank is exact: `E[rank] = 1 + sum over rivals of P(rival beats page)`.
- A raw count of wins is biased toward the middle. With constant pair accuracy a, `E[1 + wins | true position P] = 1 + (n - 1)(1 - a) + (P - 1)(2a - 1)`. A page truly at 1 is placed at 3.7 on average (n = 10, a = 0.7).
- So place by the **posterior median**, not by sorting scores. In practice: regress the rivals' known positions (on the latent scale) on their model scores inside the same results page, and place the target at the fitted value. That calibrates and shrinks in one step.
- For a fully ordered list, Diaconis and Graham give `K <= D <= 2K` (discordant pairs against total displacement). Mean absolute displacement per page lies between `(1 - a)(n - 1)/2` and `(1 - a)(n - 1)`.

SIMULATED placement of one page among rivals whose order is known (`r4-statistical-design.md` 4.2). "a" is pair accuracy over all scored pairs of the list.

| List | a | Unshrunk: mean error, within 3 | Posterior median: mean error, within 3 | Middle guess: mean error, within 3 |
| --- | --- | --- | --- | --- |
| 10 | 0.60 | 3.26, 0.580 | 2.08, 0.790 | 2.50, 0.700 |
| 10 | 0.65 | 2.70, 0.667 | 1.96, 0.807 | 2.50, 0.700 |
| 10 | 0.70 | 2.19, 0.751 | 1.74, 0.845 | 2.50, 0.700 |
| 10 | 0.80 | 1.37, 0.891 | 1.22, 0.919 | 2.50, 0.700 |
| 30 | 0.60 | 10.47, 0.239 | 6.75, 0.298 | 7.50, 0.233 |
| 30 | 0.65 | 8.75, 0.295 | 6.26, 0.341 | 7.50, 0.233 |
| 30 | 0.70 | 7.18, 0.355 | 5.63, 0.393 | 7.50, 0.233 |

Two lessons. First, at our accuracy an unshrunk placement is worse than guessing the middle, so every gate includes the middle guess as a baseline. Second, the pair accuracy of a target against its rivals is not a skill measure: the middle guess scores 0.72 to 0.75 on it.

### 2.7 The anchored edit model

For a page that ranks at position `r0` today, the anchor already contains `S` and `r`. An edit changes only the content score, from `m0` to `m1`:

    u_new = u(r0) + lambda * kappa * (m1 - m0)

If the upper tail of candidate scores is roughly exponential, the expected score at rank r is `a - b * log(r)`, so a score change becomes a change in log rank. The simulations confirm this: one standard deviation of content score moved a page to 0.47 to 0.61 times its position number, from any start (from 5 to about 2.8, from 9 to about 5.1, from 15 to about 9, for R = 3; `r4-statistical-design.md` 5.3). So the working formula is

    r1 = r0 * exp( - lambda * kappa_A * dm )

- `dm`: the change in content score, in within-site standard deviations, on a scale that does not depend on which other pages are in the list.
- `kappa_A`: log-rank change per standard deviation. SIMULATED range 0.5 to 0.75. It is estimated in stage 4 from same-site pairs in ordinary top-30 lists (two pages of one site, known positions, known scores).
- `lambda`: the **transfer factor**, between 0 and 1. The slope between two different settled pages mixes the text effect with clicks and links that follow good pages (Robinson 1950, Mundlak 1978). An edit moves the text at once and the rest slowly. No cross-sectional test can estimate lambda. It starts with a wide prior and is learned from natural experiments and live ledgers.

Error of the prediction: `Var = Var(anchor) + (transfer and kappa_A uncertainty) + (normal movement over the horizon)`. The anchor must be the median of at least 3 pulls on at least 2 days: about 10% of pages in a pull land at a random place (jump mixture fit, `r4-statistical-design.md` 5.2), and a single high pull is followed by a drop of 2.1 places on average.

### 2.8 Realistic accuracy ceilings

| Quantity | Value | Kind |
| --- | --- | --- |
| Pair agreement of two pulls (the label ceiling) | 0.87 to 0.93 | REAL |
| Best transparent published cross-site pair accuracy | 0.625 to 0.675 over top 100 with Google's own link data. 0.65 to 0.66 on top-10 pairs with domains held out | published |
| Same-site content scorer against Google's settled order | 0.72 to 0.74 | DERIVED from REAL |
| Cross-site, inside a top 10, content alone | 0.53 to 0.54 | SIMULATED, R = 3 |
| Cross-site, inside a top 10, content plus a site estimate correlated 0.7 with the truth | 0.58 to 0.60 | SIMULATED |
| Cross-site, inside a top 10, content plus a perfect site value | 0.66 to 0.71 | SIMULATED |
| Any model, inside a top 10, if hidden click signals hold 20% of the score | at most about 0.69 | SIMULATED |
| Cross-site, all pairs of a top 30, combined model | 0.58 to 0.64 | PREDICTED |
| Cross-site, page-one boundary pairs, combined model | 0.64 to 0.70 | PREDICTED |
| New page with no anchor, placed inside a top 30 | mean error about 6.3 to 6.8 places against 7.5 for the middle guess | SIMULATED at a = 0.60 to 0.65 |
| Page that ranks, no edit, next day | within 1 place 0.72, within 2 places 0.82 (positions 4 to 10) | REAL |
| Page that ranks, large content change (one standard deviation), before the transfer discount | about 2 to 3 places from position 5, 4 to 6 from position 9, 6 to 10 from position 15 | SIMULATED upper figure |

The 90% pairwise target in `docs/MISSION.md` cannot be met by any history-free method on unseen results. It is above the label ceiling of most pairs and far above every published result.

### 2.9 What the product can honestly claim

- **Page that ranks (anchored).** "This edit has a 64% chance to beat leaving the page as it is. Expected change: about 1 place better. 80% band at 28 days: positions 4 to 13." The paired effect is the claim. The absolute band is mostly normal movement.
- **New page on a site that already ranks for the search (sibling anchor).** Placed against the site's own ranking page, with a wider band.
- **New page with no anchor.** Tier probabilities and a "low confidence" label.
- **Cannot tell.** Shown when the paired effect is inside the noise, the edit is not material, a sanity guard fires, or the page cannot be read.

The simulator will not say: an exact position, a guarantee, or that an edit "will" move rank. No edit has been scored yet.

---

## 3. Rules for every stage

1. **Freeze before scoring.** Each test's split, metric, threshold, code and model go into `evals/preregistered/claude-<name>/` with a sha256 manifest (`scripts/claude/freeze.py`), committed and pushed before any held-out data is pulled or read. A test is scored once. A result file is never overwritten.
2. **Design seal for cross-site work.** Stages 2 to 4 fit and design only on data dated 2026-11-19 or earlier. Until the PR 45 simulator ledger is frozen (stage 4), no script may read any row dated after 2026-11-19 for the PR 45 target searches, or any row of the client's domain dated after 2026-11-19.
3. **Splits.** Time first, then search group, then domain. Near-duplicate wordings stay in one search group (the frozen linker in `scripts/claude/r3_dataset.py`). The round 3 site folds stay: test1 is the stage 1 test, test2 sites contribute no training pairs and no domain effects in stages 2 and 3.
4. **Leave-group-out features.** Any feature built from result lists (footprint, niche link counts) for a pair or a page in search group g uses only lists of other groups, dated on or before the label day. For a target page, its own rows on other searches are also left out in the primary reading.
5. **Baselines on the same rows, always.** Middle guess, raw domain prior, content only, site only, global authority only, persistence where it is defined, and the current ranker where it can be run. A gate needs an absolute bar and a paired difference against the best baseline.
6. **Baseline dominance check.** Before a gate is frozen, every baseline and a within-search label shuffle are run through it on design data. If any of them passes, the gate is changed before the freeze.
7. **Pair counts.** Every pairwise report gives correct, wrong and ties separately. Accuracy is on decided pairs, with ties as one half reported next to it. A missing value is missing, never zero.
8. **Intervals.** Pair metrics use the dyadic-robust variance (pairs that share a search or a domain are correlated; `r4-statistical-design.md` 6.1), and the two-way bootstrap is reported next to it. Row metrics (placement error, coverage) use a two-way cluster bootstrap over search groups and domains, 2000 draws, seed 20261120. Before a gate is frozen, the interval's coverage is simulated on the gate's own layout and must be 0.93 to 0.97. If not, the more conservative interval is used and that is recorded.
9. **Credits.** Never below 250. A script refuses to pull if the balance after the pull would be under 260. Every pull is chunked, each chunk is committed and pushed before the next, and a rerun imports chunks already in git (the box can roll back, credits cannot). No scrape credits.
10. **Free only.** No paid service, no paid signup. Outside lookups use bulk downloads where they exist, so no project detail leaves the box. The client's domain is never sent to an outside service unless D4 is approved. No API key is printed, logged or committed.
11. **Never touched.** The PR 41 ledger, earlier preregistrations, the product ranker, the client's site, main, and production.
12. **Labels.** Every model output is PREDICTED. Site-restricted order is called site-restricted order. Results on stored data that earlier work has read are called PROVISIONAL.
13. **Loops.** If a gate fails, the fallback in the stage is followed. At most one redesign loop per stage, and the loop must change the theory, not tune a knob. The failed test's data becomes design data.
14. **Log.** After every checkpoint, the numbers and the pass or fail go into `RESULTS-claude.md`, and the theory with its predictions made before scoring goes into `evals/claude/theory.md` (new part "Round 4").

**Known exposures of the stored cross-site data (it is not pristine):**

| What was read | By whom |
| --- | --- |
| Nov 16 top-30 order of every search | round 2 same-site study |
| Tranco and Common Crawl ranks against the Nov 6 top 10s of 20 searches | earlier team, Nov 7 |
| Tranco and Common Crawl ranks against the Nov 6 and Nov 9 top 10s of 30 searches | `r4-statistical-design.md` recount |
| Majestic against the Nov 16 top-30 lists of all 100 searches, by pair type | `r4-free-label-sources.md` |
| Mean position of platforms by search type, all searches from Nov 16 | `r4-cross-site-evidence.md` |
| One-day and ten-day movement, Nov 6 to 17 | `r4-edit-simulator-design.md` |

No cross-site model has ever been fitted on any of it. Because of these reads, a test on stored lists (Checkpoint 3a) is PROVISIONAL, and only the fresh pull (Checkpoint 3b) counts as unseen.

---

## 4. Stage 0: pre-flight (no credits, half a day)

**Inputs:** this file, the branch, the Serper balance.

**Tasks:**

| # | Task | Output |
| --- | --- | --- |
| 0.1 | Confirm the branch is level with origin (`git status -sb` shows no "ahead") | pushed branch |
| 0.2 | Copy every numeric gate of this file (Appendix A) into `evals/preregistered/claude-r4-gates/gates.json`, build the manifest with `freeze.py`, commit, push. This happens before the stage 1 pulls, so the advance rules cannot drift | frozen gate file |
| 0.3 | Build `scripts/claude/r4_gate.py <checkpoint>`: reads a result file and `gates.json`, prints each gate with its value and PASS or FAIL. It contains no threshold of its own | gate script |
| 0.4 | Read the balance. Stage 1 may start only if balance is at least 468 (250 floor, 208 pulls, 10 safety). If not, stop and ask James (D1) | balance note in `RESULTS-claude.md` |
| 0.5 | Run `python -m pytest` and record the counts. Expected: the six known DNS failures in `tests/test_layered_fetch.py` and no other failure | baseline test record |
| 0.6 | Housekeeping from the inventory: use `index.sqlite`, not the parquet files (no `pyarrow` in the venv). Note that `r3_pages.load_pages` writes a cache under `/workspace/ranksim-data`. Fix the dead citation in theory R3.8 (`t1-postmortem/notes.md` does not exist; point to `results.md`). D6 decides the untracked script | notes |

**Tests:** `tests/test_claude_r4_gate.py`: the gate script fails closed on a missing field, reads thresholds only from `gates.json`, and refuses an unverified manifest.

**Gate G0 (all must hold):** branch pushed. `freeze.py verify evals/preregistered/claude-r4-gates` passes. Balance at least 468. No new test failure.

**If G0 fails:** on balance, stop and raise D1. On tests, fix the cause before any freeze.

---

## 5. Stage 1: freeze and verify the same-site content scorer on unseen sites (Checkpoint 1)

**Purpose.** James's first priority. The scorer is fitted but not frozen and not scored. Everything is designed and reviewed (`evals/preregistered/claude-r3-samesite/preregistration.md`). This stage only executes it. The preregistration is not changed.

**Inputs:** the three fitted scorers in `evals/claude/r3/model/`, the plan `evals/claude/r3/plans/heldout1.jsonl` (104 lists, 93 test1 sites, 46 searches, 35 search groups), `test.json`, the pinned text models, 208 credits.

**Outputs:** `evals/preregistered/claude-r3-samesite/manifest.json`, `evals/claude/r3/pr45/ledger.json`, `evals/claude/r3/heldout/claude-r3-samesite/{results.json, pairs.csv, report.md}`, a section in `RESULTS-claude.md`.

### Task groups

**Group 1A. Freeze.** Build the manifest over `r3_score.py` and every file it imports, `r3_pull.py`, `r3_fetch.py`, the three model folders, `models.json`, the universe and heldout1 plan files, `model-next.json`, `offpage.tsv`, `test.json` and `preregistration.md`. The command is `python scripts/claude/freeze.py closure evals/preregistered/claude-r3-samesite <modules> -- <files>` (the full argument list is in `r4-data-inventory.md` section 10; check it against `freeze.py --help` first). Then `freeze.py verify`. Commit and push.

**Group 1B. PR 45 direction ledger.** Right after the freeze: `python scripts/claude/r3_pr45.py predict`, commit, push. It reads only the before and after HTML saved on Nov 19. It must run before any script reads a result of the PR 45 target searches dated after the merge. This ledger is version 1 (direction calls from the same-site scorer).

**Group 1C. Pulls and fetch.**
1. Pull A: `python scripts/claude/r3_pull.py --label heldout1-a --plan /workspace/ranksim-claude/evals/claude/r3/plans/heldout1.jsonl --max-credits 104`.
2. Fetch pass, once, plain HTTP only: `python scripts/claude/r3_fetch.py --label heldout1-a`.
3. Pull B: same command with `--label heldout1-b`, at least 30 minutes and at most 6 hours after pull A for every list.

**Group 1D. Score once.** `python scripts/claude/r3_score.py --prereg evals/preregistered/claude-r3-samesite`. Commit and push the results whatever they show. Then `python scripts/claude/r4_gate.py c1`.

**Group 1E. Descriptive add-ons after scoring (not part of the preregistration).** New script `scripts/claude/r4_pair_calibration.py`: from `pairs.csv`, a reliability curve of the pair probabilities with one temperature fitted on training lists only, and accuracy by position gap with the label-noise correction of section 2.2. These numbers are descriptive.

**Tests:** `python -m pytest tests/test_claude_r3_core.py tests/test_claude_r3_semantic.py tests/test_claude_heldout.py`. The scoring script's own guards (full manifest, freeze commit is an ancestor of every chunk commit, no third pull, one fetch pass, pull gap) must all pass or it refuses to run.

### Gates

**G1-GOAL (the frozen round 3 target, unchanged).** On consensus pairs, primary scorer `page-blend`, two-way interval: at least 40 sites and 800 consensus pairs. Lower bound above 0.65. Primary minus BM25F lower bound above 0. Primary minus each variant of the current ranker's content part lower bound above 0. Stretch: lower bound above 0.75.

**G1-ADV (the rule to advance to stage 2, declared here before any held-out list exists).** All of:
1. The run is valid: every guard of `r3_score.py` held.
2. At least 40 sites and 800 consensus pairs.
3. Primary point estimate on consensus pairs at least 0.65, and two-way lower bound at least 0.60.
4. Primary minus BM25F: two-way lower bound above 0.

Why the advance rule is weaker than the goal: stages 2 to 4 need a content score with a real signal on unseen sites. They do not need the lower bound to clear 0.65, and each later stage has its own sealed gate. G1-GOAL is still reported as passed or failed exactly as frozen.

**PREDICTED (made in the preregistration before scoring):** 0.69 on consensus pairs (range 0.64 to 0.73), lower bound about 0.65. G1-GOAL passes with probability about 0.45. G1-ADV passes with probability about 0.85 (lead's estimate).

### Fallbacks, declared now

| Case | Rule | Next step |
| --- | --- | --- |
| F1-A | `page-blend` fails G1-ADV, but the unfitted cross-encoder on title, H1 and meta (nothing fitted, already a baseline in the same run) meets conditions 2 to 4 | The unfitted cross-encoder becomes the content score for stages 2 to 4. Nothing was fitted, and the rule is written here first, so this is not a post-hoc pick |
| F1-B | Neither meets G1-ADV on all lists, but `page-blend` meets conditions 3 and 4 on the construction-software lists alone, with at least 25 sites and 500 consensus pairs (the topic slice is already frozen in `heldout1.slices.json`) | Advance with the claim limited to niches that have training lists. Stage 3's kappa check (G3.5) must then confirm the content signal |
| F1-C | None of the above | The content track stops. Stage 2 groups A to C (site signals, no content score) may still run. One new theory loop from theory R3.9: crawl each site's internal links, add training lists in other topic areas, then a new preregistered test on the test2 sites when credits allow. D7 (LLM judge) is offered to James as an option |

In every case the held-out lists become design data after scoring.

---

## 6. Stage 2: site strength from free signals (Checkpoint 2)

**Purpose.** Estimate, for every domain in the niche, a site-strength value with an honest uncertainty, from free data, and prove on stored lists that it carries real information where site strength matters: at the page-one boundary, and for small sites that no global list covers. Zero credits. No waiting on Google.

**Inputs:**
- Stored ordinary lists through 2026-11-19 for the 140 construction searches (c100, the 35 PR 41 baseline searches, the 4 ClockShark searches), via `scripts/claude/rsdata.py` with `max_date="2026-11-19"`. The 15 revenue searches are a different niche: never used for fitting, reported as a separate slice.
- The Nov 16 deep lists (positions 1 to 30, source `smartcrew-index-deep:2026-11-16:abspos`). Trap: never take deep positions from the index `candidates` table.
- The frozen content scorer from stage 1 (or its F1-A replacement).
- The page store and the local index (`/workspace/ranksim-data/index/index.sqlite`).
- Free outside files, all reachable from this box on Nov 20: Common Crawl domain graph release `cc-main-2026-aug-sep-oct` (domain edges 7.1 GiB, vertices 942.6 MiB, ranks 2.54 GB), Majestic Million and Tranco (already on disk in `/workspace/kw-sample-2026-11-13/`), Cisco Umbrella, the public CrUX top list, Wikidata through QLever, Wikipedia APIs, Google autocomplete, RDAP.

**Outputs:**
- `evals/claude/r4/plans/xs_split.json` (frozen split) and `evals/claude/r4/site/site_types.tsv` (frozen site types).
- `evals/claude/r4/site/signals.parquet` or `.csv.gz`: one row per (domain, left-out search group) with every signal, its source and its as-of date.
- `evals/claude/r4/site/site_strength.csv.gz`: per (domain, left-out group): estimate, standard deviation, and how much came from the domain's own results against its features.
- `evals/claude/r4/stage2/{tables.md, results.json}` and a `RESULTS-claude.md` section.
- Large downloads go to `/workspace/ranksim-data/external/` (new folder, append only, not in git), with a sha256 list in git.

### The candidate signals (exact list, frozen before fitting)

| Id | Signal | Unit | Source | Notes |
| --- | --- | --- | --- | --- |
| S1a | Share of other search groups where the site is in the top 10 | domain, left-out group | stored lists | leave-group-out |
| S1b | Share of other search groups where the site is in the top 30 | same | Nov 16 deep lists | deep day only |
| S1c | Mean of `1 / log2(1 + best position)` over other groups, 0 where absent | same | stored lists | the visibility index, SISTRIX style |
| S2a | Referring domains (in-degree) on the whole web | registrable domain | Common Crawl domain graph | bulk download, no per-domain call |
| S2b | Referring domains among niche domains (leave-group-out niche set) | same | same pass | the free stand-in for topical authority |
| S2c | Harmonic centrality rank and PageRank rank | same | Common Crawl ranks file | weak gate signal |
| S3a | Site topical focus: similarity of the site's page centroid to the search | domain, search | local index, pinned `bge-small` | no labels |
| S3b | Site radius: spread of the site's pages around its centroid | domain | local index | |
| S3c | `zbar`: mean and maximum frozen content score of up to 30 of the site's readable indexed pages for the search (frozen pick rule: most recently captured) | domain, search | local index, frozen scorer | this is the Mundlak term of section 2.4 |
| S3d | Count of the site's indexed pages on the topic, and sitemap size where a sitemap answers | domain | local index, plain HTTP | |
| S4a | Brand demand: autocomplete completes the brand, number of suggestions | brand | Google autocomplete | client excluded unless D4 |
| S4b | Wikidata official-website match and sitelink count, Wikipedia pageviews | domain | QLever, Wikimedia APIs | |
| S5 | Majestic referring subnets, Tranco rank, Umbrella rank, CrUX rank bucket, each with a "not listed" flag | domain | downloads | gate signals, never assumed monotone |
| S6 | Registration age, with an "under two years" flag | domain | RDAP | missing for .io, .co, .us, .de |
| S7 | Site type: user-content platform, review aggregator, marketplace or app store, vendor, publisher, government or education, other | domain | frozen rule table | platforms from the inventory's list, the rest by rule |
| S8 | Affinity flags: the search names this site's brand, the search names another brand, exact-match domain words, the page is the home page | search, domain or page | search text and URL | kept apart from site strength |

Not used, with reasons (`r4-site-strength-signals.md`): Open PageRank (needs a signup, built from the same Common Crawl graph), Moz (50 rows a month), Ahrefs and Semrush (paid or terms forbid automation), Cloudflare Radar (token, non-commercial license), Google Trends (rate limited on the first call), Bing (own sites only).

### Task groups

**Group 2A. Data layer and split (before any model code exists).**
1. `scripts/claude/r4_split.py`: assign the construction search groups to two folds by seeded hash, seed 20261120: **XS-design** 65%, **XS-test-stored** 35%. The largest group (35 dirt work searches, about a third of all pairs) is placed in XS-design by rule. Size check: XS-test-stored must hold at least 9 groups and at least 18 c100 searches with a deep list. If the check fails, use seed 20261121, then 20261122, and so on. Record every seed tried. No other criterion may be used. Record for each test group whether the content scorer saw it in training (round 3 train group) or not.
2. `scripts/claude/r4_sitetype.py`: the site-type table, frozen.
3. `scripts/claude/r4_xsdata.py`: tables of rows and pairs from `rsdata`, date sealed. One list per search for fitting: the Nov 16 deep list where it exists, else the search's single stored pull. Pair types: inside top 10, boundary (one page in the top 10, the other at 11 to 30), inside 11 to 30. Pair weights make each list's total weight equal to its number of scorable pages. Page text for a row is the stored fetch closest to the label day within 14 days, else the row is unreadable.
4. `scripts/claude/r4_content_scores.py`: the frozen scorer applied to ordinary-list pages. **List invariance is required:** the same page with the same text for the same search must get the same score whatever other pages are in the list. The frozen `page-blend` standardises inside a list, so this script replaces in-list standardisation with frozen pooled scales computed from the training lists (no weight is refitted). Check: on the training lists the list-invariant score must agree with the frozen scorer (pair accuracy within 0.005 in site-held-out cross-validation, within-list rank correlation at least 0.98). If the check fails, use the logistic part alone (`text-logit` style), which was preregistered as a secondary scorer.
5. Free text fill: one plain-HTTP pass over Nov 19 URLs without text, and Common Crawl captures through `data.commoncrawl.org` (`cluster.idx` plus range reads; `index.commoncrawl.org` does not answer from this box). Record source and capture date.

**Group 2B. Signals.** One script per family, each with a loader that keeps missing as missing:
`r4_signals_local.py` (S1, S3d, S7, S8), `r4_signals_cc.py` (S2, with resumable downloads, `curl -C -`), `r4_signals_focus.py` (S3a to S3c), `r4_signals_lists.py` (S5, reusing `src/aeo/ranksim/offpage.py`), `r4_signals_brand.py` (S4, S6, polite rate, results cached, the client's domain filtered out unless a D4 flag file exists).

**Group 2C. Single-signal table (design, XS-design only).** `scripts/claude/r4_stage2_eval.py signals`: each signal alone, as correct, wrong and ties, by pair type, with and without platforms, and on small-site pairs. This is the baseline table for the gate. It also reruns the recount of section 1 so the old 0.38 to 0.42 figures are settled in a committed script.

**Group 2D. The site-strength model.** `scripts/claude/r4_site_model.py`: the step 2 model of section 2.4 without the page-level content term (the site-only model: S1 to S8, `zbar`, domain effects). Fixed choices: logistic link, all signals enter (no selection), each signal standardised on XS-design, ridge on domain effects with `tau^2` chosen by nested leave-group-out log-likelihood, a small fixed ridge on the other weights. Statistics helpers in `scripts/claude/r4_stats.py` (dyadic variance, two-way bootstrap, coverage simulation, power), ported from the appendix of `r4-statistical-design.md`.

**Group 2E. Checkpoint 2.** `r4_stage2_eval.py gate`: nested leave-group-out cross-validation over XS-design. Every prediction for group g comes from a model that never saw g, with features that leave g out. Then `r4_gate.py c2`. XS-test-stored is not opened in stage 2.

### Tests

- `tests/test_claude_r4_split.py`: same seed gives the same folds, no group in two folds, the largest group is in XS-design, test2 sites carry no training weight.
- `tests/test_claude_r4_xsdata.py`: a row after the seal date raises. Pair types are right at positions 10 and 11. `correct + wrong + ties = pairs`, and a tie is never counted as right or wrong. Deep positions never come from the index table.
- `tests/test_claude_r4_signals.py`: planted-leak test (a synthetic domain that ranks only in group g has footprint 0 for g). As-of date test. Target-URL exclusion. A domain missing from a global list stays missing and gets its flag. The outbound request list never contains the client's domain without the D4 flag.
- `tests/test_claude_r4_content_scores.py`: list invariance (score unchanged when other pages are added or removed), and the agreement check of task 2A.4 on a fixture.
- `tests/test_claude_r4_site_model.py`: on the synthetic design of `r4-statistical-design.md` simulation 3, the two-step fit keeps content weights with cosine at least 0.95 and beats content only. A domain with only wins or only losses gets a finite effect. The shrinkage formula matches the closed form. Dyadic interval coverage is 0.93 to 0.97 on a synthetic layout.
- All tests run with no network and with keys unset.

### Gate G2 (Checkpoint 2), on nested leave-group-out predictions over XS-design

| Id | Metric | Bar |
| --- | --- | --- |
| G2.0 | List-invariant content score agrees with the frozen scorer | pair accuracy within 0.005, rank correlation at least 0.98 |
| G2.1 | Coverage: share of non-platform top-30 domains with a site-strength estimate and a standard deviation. The client's domain has one | at least 0.95 |
| G2.2 | Site-only model, cross-site boundary pairs, decided pairs, Nov 16 deep lists | dyadic 95% lower bound above 0.60, and point estimate at least 0.02 above the best single signal of group 2C on the same pairs |
| G2.3 | Same, on small-site boundary pairs (both domains absent from Majestic Million and Tranco) | point at least 0.58, dyadic lower bound above 0.52 |
| G2.4 | Leak tests of the test list above | all pass |
| G2.5 | Baseline dominance: every single signal and a within-search label shuffle run through G2.2 | none passes |

Reported next to the gate, not gated: the same model inside the top 10 (PREDICTED 0.53 to 0.58, an honesty check on section 2.3), inside 11 to 30, with and without platforms, by search type, and on the revenue niche.

**PREDICTED:** G2.2 point 0.66 to 0.72 (the best single signal is expected near 0.63). G2.3 point 0.58 to 0.66. Chance that all of G2 pass: about 0.6. The main risk is that the niche footprint adds less than 0.02 over the best single signal.

### Fallbacks

| Case | Next step |
| --- | --- |
| G2.2 fails only on the "0.02 above the best single signal" part | The best single signal becomes the site-strength value, with the same shrinkage. Stage 3 runs with it. Say plainly that the model added nothing over it |
| G2.3 fails (small sites cannot be told apart) | For a small site with no footprint, the no-anchor mode says "cannot tell: site not established for this topic". The product leans on the anchored modes. Raise D2 and D5 with James |
| G2.2 lower bound at or under 0.60 with every signal | One loop allowed, and it must change the theory: move the test to "does the site reach the top 10 at all" as a per-domain classification, add the page-two labels from D5 if approved. If that also fails, stage 3 runs content plus the raw domain prior only, and the no-anchor mode is tiers only |
| G2.0 fails | Use the logistic part alone as the content score, as task 2A.4 says |

---

## 7. Stage 3: content plus site strength, full-SERP pairs, and positions (Checkpoints 3a and 3b)

**Purpose.** Combine the frozen content score with the stage 2 site strength into one model that orders pages of different sites, turn the pairwise wins into a position with an honest band, and test it once on searches and sites nothing has seen.

**Inputs:** stage 2 outputs, XS-design lists, the frozen content score, the multi-pull days (for the noise model), 90 to 180 credits for Checkpoint 3b.

**Outputs:**
- `evals/claude/r4/model/rank-v1/` (the frozen combined model: kappa, weights, domain effects, tau, the position curve, the band quantiles, with a save and load API like `r3_model.FrozenScorer`).
- `evals/preregistered/claude-r4-xs/` (preregistration, `test.json`, manifest).
- `evals/claude/r4/plans/xs_fresh.jsonl` (the fresh-search plan) and its meta file.
- `evals/claude/r4/stage3/{stored, fresh}/{results.json, rows.csv, pairs.csv, report.md}`.

### The three tasks the model is scored on

- **T-A, placement.** For each test search and each readable, non-platform page in its top 30: hide that page's position, keep the other pages at their known positions, and predict the hidden page's position from its content score and its site strength. The site strength uses no list of this search group and none of the page's own rows. Output: posterior median position, 50% and 80% bands, and probabilities for the tiers 1 to 3, 4 to 10, 11 to 20, 21 to 30.
- **T-B, reach.** For each test search and each niche domain that has a readable indexed page: the probability that the domain appears in the search's top 10, and in its top 30. The candidate page is the domain's indexed page with the highest content score for the search, the same rule for every model. This task covers the "beyond 30" outcome that T-A cannot see.
- **T-C, order from scratch.** All cross-site pairs of the top 30: which page is higher. Reported for all pairs, boundary pairs, inside the top 10 and inside 11 to 30.

### Task groups

**Group 3A. The combined model.** `scripts/claude/r4_rank_model.py`: the full step 2 model of section 2.4, fitted once on XS-design (one list per search). Fixed choices as in 2D. Report `kappa` with a search-group bootstrap interval. Also fit `kappa` a second way, from same-site pairs of ordinary lists only (a conditional logit on the content score), and report both. If they differ by more than a factor of 2, say so: content and site are then not on one scale.

**Group 3B. Positions.** `scripts/claude/r4_position.py`:
1. Position curve and pull noise: fit the jump-mixture noise model (a page lands at a random place with probability q, otherwise gets Gaussian jitter) to pair agreement by position gap, from the multi-pull days of XS-design searches. This gives the latent spacing of positions 1 to 10. Positions 11 to 30 are extrapolated on the log-rank form of section 2.7 and flagged as extrapolated until repeat deep pulls exist.
2. Calibrated placement: inside each search, regress the rivals' latent positions on their model scores (a pooled slope with a per-search intercept), place the target at its fitted value, and take the posterior median.
3. Bands: split-conformal error quantiles from leave-group-out placements on XS-design, by tier of the predicted position. The noise model alone never sets a band.
4. Expected rank `1 + sum P(rival beats page)` is computed as a cross-check. Reuse `src/aeo/ranksim/position_aggregate.py` where it fits.

**Group 3C. Checkpoint 3a, stored test (0 credits, PROVISIONAL).** Freeze the model and the preregistration. Then score XS-test-stored once with `scripts/claude/r4_stage3_score.py --cell stored`. It is a dress rehearsal and the only test if credits run out. If the model is refitted afterwards, the stored cell is design data from then on.

**Group 3D. Checkpoint 3b, fresh test (the one that counts as unseen).**
1. `scripts/claude/r4_stage3_plan.py`: candidate searches are the related searches and "people also ask" strings in stored raw responses dated on or before Nov 19 (1,028 and 542 distinct strings). A candidate is eligible when it is not one of the 155 stored searches, the frozen linker puts it in no existing search group, and it contains a niche term from a frozen list (written to the plan's meta file before sampling). Draw by seeded hash, at most 2 searches per new group. The eligible list and its count are written before the draw.
2. Size, fixed by the balance on the day of the freeze: `N = min(60, floor((balance - 250 - 15) / 3))`. If N is under 30, Checkpoint 3b is not run, and that is reported as "not tested on unseen searches".
3. Freeze and push. Then pull pages 1, 2 and 3 of each search once, all three within minutes (`r3_pull.py` extended with a `page` field, or a small `r4_stage3_pull.py`; chunked, each chunk committed and pushed). 3 credits per search.
4. One plain-HTTP fetch pass, then the Common Crawl fill. No browser pass, no second pass, no scrape.
5. Score once: `r4_stage3_score.py --cell fresh`. Guards as in round 3: full manifest, the freeze commit is an ancestor of every chunk commit, one pull, one fetch pass.
6. Before the freeze, compute the power of each gate at the PREDICTED effect by simulation from the XS-design errors. If power is under 0.8, the gate stays as written and the report says a fail is uninformative.

### Tests

- `tests/test_claude_r4_position.py`: on synthetic data at pair accuracy 0.65 the posterior median beats the middle guess and matches the table of section 2.6 within 0.15 places. The raw win count shows its known bias. Bands cover at their stated rate on synthetic data. The expected-rank identity holds.
- `tests/test_claude_r4_rank_model.py`: save and load round trip gives identical scores. Scores are list invariant. A test2 site has no domain effect.
- `tests/test_claude_r4_score_guards.py`: scoring refuses without a manifest, with an unpushed freeze, with a second pull, with a second fetch pass, or when a result file exists.
- `tests/test_claude_r4_plan.py`: no fresh search is in a stored search group, the draw is deterministic, N follows the formula.

### Gate G3 (applied to Checkpoint 3b; applied to 3a as PROVISIONAL)

Rows: readable, non-platform target pages. Validity: at least 30 searches and 300 target rows.

| Id | Metric | Bar |
| --- | --- | --- |
| G3.1 | T-A mean absolute error of the posterior median | at least 8% below the middle guess at the point estimate, and the paired difference (baseline minus model) has a two-way 95% lower bound above 0 against the middle guess and against the raw domain prior |
| G3.2 | T-A band coverage | the 80% band covers 0.72 to 0.88 of rows, the 50% band 0.42 to 0.58 |
| G3.3 | T-C pair accuracy, decided cross-site pairs | all pairs of the top 30: dyadic lower bound above 0.55. Boundary pairs: dyadic lower bound above 0.58 |
| G3.4 | T-A tier forecast | ranked probability skill score against the tier frequencies of XS-design is above 0 with a two-way lower bound above 0 |
| G3.5 | Content matters across sites | `kappa` from the XS-design fit has a search-group bootstrap 95% lower bound above 0, and on the test rows the combined model's T-A error is not worse than the site-only model's by more than 0.10 places |
| G3.6 | No harm on cold sites (domains in no stored list) | T-A error at most the middle guess's at the point estimate. Reported with its interval |
| G3.7 | No harm on reach | T-B log loss at most the footprint-only baseline's at the point estimate |

Always reported next to the gate: correct, wrong and ties; pair accuracy inside the top 10 (PREDICTED 0.56 to 0.62, not gated, see section 2.8); results with platforms as targets; by search type; by whether the content scorer saw the search group; content only, site only, global authority only and the current ranker on the same rows.

**PREDICTED:** T-C all pairs 0.58 to 0.64, boundary pairs 0.64 to 0.70. T-A error 10% to 18% below the middle guess and 5% to 10% below the raw domain prior. Chance that G3.1 to G3.5 all pass on the fresh test: about 0.35. The most likely failure is G3.1 against the raw domain prior, or a lower bound in G3.3 just under its bar with N near 30.

### What each outcome means, and the fallbacks

| Outcome | Meaning | Next step |
| --- | --- | --- |
| All of G3.1 to G3.5 pass | The no-anchor mode is supported on unseen searches | Stage 4 enables it with tiers and bands |
| G3.3 and G3.4 pass, G3.1 fails | The model orders pages but its position estimate is no better than simple guesses | The no-anchor mode ships tiers only, with no position |
| G3.5 fails (kappa not above 0) | The content score adds nothing across sites once site strength is in | The no-anchor mode uses site strength only. The anchored mode then rests on the same-site kappa of group 3A and on stage 4's own gates |
| G3.2 fails | Bands are wrong | Widen from the fresh errors, and treat the widened bands as untested until the next fresh data |
| 3a fails badly (model worse than a baseline at the point estimate) | Do not spend the fresh credits yet | One loop that changes the theory: the top-k Plackett-Luce likelihood with a candidate pool (`r4-rank-aggregation.md` section 7), which corrects the selection bias of top-k lists. Then 3a again as design data, then 3b |
| Everything fails | Cross-site placement is not reachable with free data today | The product is the anchored modes only. The no-anchor mode says "cannot tell". The report says which data would change that: D2, D3, D5 |

---

## 8. Stage 4: the edit simulator and live confirmation (Checkpoints 4 and 5)

**Purpose.** The end product: give it a search, a page and a changed version, get an approximate rank with honest uncertainty. Validate it with tests that do not wait on Google. Confirm it later on real edits without ever blocking on them.

**Inputs:** the frozen content scorer, the list-invariant score (stage 2), the position curve and noise model (stage 3), the rank model for pages with no anchor (stage 3, if it passed), stored lists, the PR 41 and PR 45 material.

### The three modes

| Mode | When | Method |
| --- | --- | --- |
| A, anchored | The page is in the stored top 30 of the search (at least 3 pulls on at least 2 days for the anchor; fewer pulls gives a flagged, wider band) | `r1 = r0 * exp(-lambda * kappa_A * dm)` on the latent anchor, then the horizon's movement band |
| B, sibling anchor | The page has no position, but another page of the same site ranks for the search or a near-duplicate search | Mode A against the sibling's anchor, plus a residual-reset term that widens the band (a new URL does not inherit the sibling's clicks and links) |
| C, no anchor | Neither | The stage 3 model: tier probabilities, low confidence. If stage 3 failed: "cannot tell" |

If the site already has two pages in the list, the output flags host crowding: the edited page may swap with its sibling instead of adding a slot.

### Interface

Command: `python -m aeo.ranksim simulate-edit --project NAME --search "..." --url URL --after-html FILE [--before-html FILE] [--horizon-days 28]`. Also a function `simulate_edit(...)` returning the same record. With two `--after-html` files it compares two candidate edits.

Output record (JSON, every field labeled PREDICTED, rendered as text for the report):

    search, url, mode, anchor {median position, pulls, days, latent}, horizon_days
    effect  {p_edit_better, word, expected_change_places, range80, dm, lambda_prior}
    landing {tier_probabilities: top3, 4to10, 11to20, beyond20, band80, most_likely_tier}
    confidence {level, reasons}, cannot_tell {flag, reasons}, flags [host_crowding, extrapolated_band, ...]
    noise_floor {places_per_day at the start tier}, track_record {past band coverage, pooled ledger hit rate}
    provenance {model sha256, data through date, core update status}

Rules: probability words follow the IPCC scale ("likely" only at 66% or more). The 80% band comes from conformal error quantiles by start tier and horizon. "Cannot tell" is set when `p_edit_better` is between 0.40 and 0.60, the paired effect is under the noise floor, the edit is under the frozen material-change rule (title or H1 changed, or at least 250 tokens and 2% of the body), a sanity guard fires, the page cannot be read, or mode C is not supported. Every prediction is appended to the hash-chained ledger (`src/aeo/ranksim/ledger.py`).

### Task groups

**Group 4A. Package.** `src/aeo/ranksim/editsim/`: `content.py` (loads the frozen scorer and checks its sha256, never refits, byte-identical scores to the round 3 output on a fixture), `anchor.py`, `effect.py` (modes A, B, C), `guards.py`, `output.py`, and the CLI hook in `cli.py`. Nothing is merged to main or deployed.

**Group 4B. Anchor and movement bands.** The latent anchor from all pulls of the last days, shrunk toward the position curve (this handles regression to the mean). Movement bands by start tier and horizon, from stored transitions. Horizons beyond the stored history (13 days today) are extrapolated and flagged until the collector's data covers them.

**Group 4C. The edit effect.**
1. `kappa_A`: regress the log-rank gap of same-site pairs in ordinary top-30 lists on their content-score gap (XS-design, clustered by site). About 465 such pairs exist, 43% from one site, so report the estimate with and without that site.
2. `lambda`: prior uniform on 0 to 1. The paired effect at `lambda = 1` is shown as "upper figure". The band integrates over the prior. The prior is updated only by rungs 4 and 5 below, under a rule frozen before those rungs are read.
3. Mode B residual reset: measured on stored same-site swaps (a new URL replaced a sibling). There are few (27 on the held-out days of round 2), so it stays a wide term.

**Group 4D. Validation ladder (cheap first).**

| Rung | Script | What it shows | Cost |
| --- | --- | --- | --- |
| 0 | `scripts/claude/r4_sanity_suite.py` | The simulator does not reward edits that should not matter (appended irrelevant text, synonym swaps, duplication, date-only edits, keyword stuffing) and moves the right way on clear cases (on-topic title replaces off-topic title). CheckList style: invariance, direction, minimum function | 0 credits |
| 1 | stage 1 | Content order on unseen sites | done in stage 1 |
| 2 | `scripts/claude/r4_band_backtest.py` | With no edit, the stated 50%, 80% and 90% bands cover at those rates. Widths set on transitions ending on or before Nov 17, tested on transitions ending Nov 18 and 19 and on later collector days (PR 45 searches and client rows excluded, rule 2) | 0 credits |
| 3 | `scripts/claude/r4_virtual_edits.py` | Virtual edits: page A of a site given page B's title, then title and H1, then all text. The score must move toward B step by step. Swap pairs: one site, two pages, opposite order under two searches. Position backtest: for same-site pairs in ordinary lists of held-out search groups, predict B's position from A's anchor and the score gap | 0 credits |
| 4 | `scripts/claude/r4_cc_versions.py` | Common Crawl natural experiments: for training-site pages whose text changed materially between monthly captures, does today's site-restricted order agree more with the after version than the before version, and more for older changes? Unchanged pages are the control. This is the only free estimate of lambda | 0 credits, 1 to 2 weeks |
| 5 | `scripts/claude/r4_ledger_score.py` | Live edits: PR 45, PR 41, later edits | 0 extra credits, weeks |

**Group 4E. Live confirmation, never blocking.**
- **PR 41 ledger** (the earlier team's file, never edited): read only. Outcomes are appended to a new file `evals/claude/r4/ledgers/pr41-outcomes.jsonl`.
- **PR 45 ledger version 1** (stage 1, group 1B): direction calls from the same-site scorer.
- **PR 45 ledger version 2**: the full simulator's predictions for the 6 target searches that have a before rank, frozen as soon as the simulator passes G4. It counts as blind only if rule 2 of section 3 held for every script. Otherwise it is labeled "not blind" and kept out of the pooled rate.
- **Score dates:** edit plus 28 days and plus 56 days (PR 41: Dec 16 and Jan 13. PR 45: Dec 17 and Jan 14), and never earlier than 7 days after Google marks the core update complete. Any earlier read is a diagnostic.
- **Scoring rule (frozen before the first read):** truth is the consensus of at least 3 pulls on the score date. Each edited (page, search) row is compared with matched controls: the same site's unedited pages on the same searches, and the unedited rivals of the same results page. A hit is a called direction that matches the sign of the edited-minus-control change. Rows with no call are reported with their share and left out of the rate.
- **Pooling across ledgers:** a Beta-binomial hit rate with a flat prior and with a skeptical Beta(10, 10) prior, and an anytime-valid e-value against 0.5 with design alternative 0.65. Both are reported after every ledger. No ledger is stopped or extended because of them.
- **Why it cannot block:** PR 45 has at most 24 in-play rows on 8 searches, worth about 12 to 15 independent rows. At a true hit rate of 0.70 the sign test has power 0.30. A miss proves nothing and a hit proves little. About 37 to 69 independent rows are needed for 80% power at 0.70 to 0.65.

### Tests

- `tests/test_editsim_content.py`: frozen scorer hash check, byte-identical scores on a fixture, list invariance.
- `tests/test_editsim_effect.py`: mode selection, the log-rank formula on known inputs, zero edit gives zero paired effect, antisymmetry (A to B is the reverse of B to A), monotone in `dm`, host-crowding flag.
- `tests/test_editsim_guards.py`: stuffing, duplication, irrelevant text and date-only edits trigger their guards. The material-change rule matches `scripts/claude/t2_changes.py`.
- `tests/test_editsim_output.py`: schema, the IPCC words at their cut points, every "cannot tell" reason, the PREDICTED label on every number, deterministic output for a fixed seed.
- `tests/test_editsim_ledger.py`: every prediction is appended, the hash chain verifies, the PR 41 file is never opened for writing.
- `tests/test_claude_r4_ledger_score.py`: the scorer refuses a score date inside the core-update window unless a "diagnostic" flag is set, and refuses to run before the scoring rule is frozen.
- The whole suite passes with keys unset and with fake keys, with no network.

### Gate G4 (Checkpoint 4, the release gate for the simulator on this branch)

| Id | Metric | Bar |
| --- | --- | --- |
| G4.1 | Rung 0 sanity suite on held-out pages | Invariance cases: median absolute paired effect under 0.25 places, and "no change" or "cannot tell" in at least 90%. Direction cases: right sign or "cannot tell" in at least 90%, wrong sign in under 5%. Stuffing capped and flagged in 100% |
| G4.2 | Rung 2 band coverage with no edit, on days not used to set the widths | For each start tier (1 to 3, 4 to 10, 11 to 30) and each horizon with at least 150 rows: realized coverage of the 50%, 80% and 90% bands within 7 points of stated. Cells with fewer rows are merged before scoring. Median band width is reported next to it |
| G4.3 | Rung 3 | Monotone dose response in at least 90% of virtual edits. Swap-pair accuracy with a by-site lower bound above 0.5. Position backtest on held-out groups: direction accuracy at least 0.60 with a by-site lower bound above 0.5, and mean log-rank error not above the "no change" baseline |
| G4.4 | `kappa_A` | by-site bootstrap 95% lower bound above 0 |
| G4.5 | Software | All tests pass (the six known DNS failures aside). Output is deterministic. One prediction takes under 60 seconds on this box |

Not gated, always reported: rung 4 (after-minus-before agreement, controls, the time split, and the lambda estimate it implies) and rung 5 (the pooled hit rate, the e-value, band coverage on real edits).

**PREDICTED:** G4.2 passes for horizons of 1 to 3 days and is untested beyond 13 days. G4.3 direction accuracy 0.60 to 0.68. `kappa_A` 0.3 to 0.75 (lower than the simulated range, because lists cut at 30 pull the estimate down). Chance that all of G4 pass: about 0.7.

### Fallbacks

| Case | Next step |
| --- | --- |
| G4.1 invariance under 90% | Tighten the guards. If still under 90%, the simulator accepts only material edits and says "cannot tell" for the rest |
| G4.2 fails | Widen the bands by conformal quantiles and retest on later collector days. No release before coverage holds on days the widths did not see |
| G4.4 fails (`kappa_A` not above 0) | Mode A gives direction only (up, down, cannot tell) and no position change |
| G4.3 position backtest fails | Same as G4.4. The paired effect is shown as a direction with its probability |
| Rung 4 shows no agreement gain for after versions | Lambda's prior moves toward 0, bands widen, and the report says the edit effect is unconfirmed. This does not block release of the band and tier outputs |

### Checkpoint 5 (live reads, on the calendar, never a gate)

| Date (not before 7 days after the core update completes) | Read |
| --- | --- |
| Dec 16 and 17 | PR 41 plus 28, PR 45 plus 28 |
| Jan 13 and 14 | PR 41 plus 56, PR 45 plus 56 |
| Each later client edit | A new ledger frozen before the edit goes live, read at plus 28 and plus 56 |

---

## 9. Credits and order of work

| Step | Credits | Balance above the 250 floor after it (if the collector is paused, D1) |
| --- | ---: | ---: |
| Now | | 367 |
| Stage 1, two pulls of 104 lists | 208 | 159 |
| Stage 2 | 0 | 159 |
| Stage 3b, fresh test, N = 48 by the formula (3 credits a search) | 144 | 15 |
| Stage 4 | 0 | 15 |

- With the collector running at about 90 a day: stage 1 must be pulled before the collector's second run from now, or it no longer fits. After stage 1, one more collector run leaves 69 above the floor, which is under the 90 needed for the smallest fresh test (N = 30). So without D1, Checkpoint 3b cannot happen and stage 3 ends at the PROVISIONAL stored test.
- The collector's default floor is 200. Unless its caller sets 250, it can take the shared balance below James's floor on its own.
- Not affordable now and left out: a repeat pull of pages 2 and 3 to measure noise at positions 11 to 30 (30 to 60 credits). D5 would give that data for free.

**Order.** Stage 0 and stage 1 at once (one day). Stage 2 next (about four working days, no credits). Stage 3 (about three days plus the pull). Stage 4 (about one to two weeks to the release gate). Ledger reads on the calendar. Stage 2 groups A to C may start while stage 1's second pull waits out its gap, because they do not use the held-out result.

---

## 10. What this plan does not do

- It does not wait on Google for any gate.
- It does not spend money, and it does not sign up for anything unless James approves D3.
- It does not claim an exact position, or that an edit will move rank.
- It does not change the round 3 preregistration, the PR 41 ledger, the product ranker, main, or production.
- It does not use rank history as a model feature of the content scorer. The anchored mode uses the page's current position as a starting point, which is known at prediction time, and every test anchors only on data from before the prediction date.

---

## Appendix A. Every gate in one table (source for `gates.json`)

| Gate | Metric | Bar |
| --- | --- | --- |
| G0 | branch pushed; gate manifest verifies; balance; tests | level with origin; verify passes; at least 468; no new failure |
| G1-GOAL | `page-blend`, consensus pairs, two-way interval | at least 40 sites and 800 pairs; lower bound above 0.65; minus BM25F lower bound above 0; minus each ranker variant lower bound above 0 |
| G1-ADV | same run | valid run; at least 40 sites and 800 pairs; point at least 0.65; lower bound at least 0.60; minus BM25F lower bound above 0 |
| F1-A | unfitted cross-encoder, same run | conditions 2 to 4 of G1-ADV |
| F1-B | `page-blend`, construction lists only | at least 25 sites and 500 pairs; point at least 0.65; lower bound at least 0.60; minus BM25F lower bound above 0 |
| G2.0 | list-invariant score against the frozen scorer | accuracy within 0.005; rank correlation at least 0.98 |
| G2.1 | coverage of site-strength estimates | at least 0.95; client's domain covered |
| G2.2 | site-only, boundary pairs, leave-group-out | dyadic lower bound above 0.60; point at least best single signal plus 0.02 |
| G2.3 | site-only, small-site boundary pairs | point at least 0.58; dyadic lower bound above 0.52 |
| G2.4 | leak tests | all pass |
| G2.5 | baseline dominance | no baseline and no shuffle passes G2.2 |
| G3 validity | fresh test size | at least 30 searches and 300 target rows |
| G3.1 | T-A mean absolute error | at least 8% below the middle guess; paired lower bound above 0 against the middle guess and the raw domain prior |
| G3.2 | T-A band coverage | 80% band 0.72 to 0.88; 50% band 0.42 to 0.58 |
| G3.3 | T-C pair accuracy | all top-30 pairs dyadic lower bound above 0.55; boundary pairs above 0.58 |
| G3.4 | T-A tier forecast | ranked probability skill above 0, lower bound above 0 |
| G3.5 | kappa | bootstrap lower bound above 0; combined not worse than site-only by more than 0.10 places |
| G3.6 | cold sites | T-A error at most the middle guess's |
| G3.7 | reach | T-B log loss at most the footprint-only baseline's |
| G4.1 | sanity suite | invariance: median under 0.25 places and at least 90% no change or cannot tell; direction: at least 90% right or cannot tell, under 5% wrong; stuffing flagged 100% |
| G4.2 | band coverage, no edit | within 7 points of stated, per start tier and horizon with at least 150 rows |
| G4.3 | virtual edits, swap pairs, position backtest | monotone at least 90%; swap lower bound above 0.5; direction at least 0.60 with lower bound above 0.5; log-rank error not above no change |
| G4.4 | `kappa_A` | by-site lower bound above 0 |
| G4.5 | software | tests pass; deterministic; under 60 seconds |

## Appendix B. New code, by stage

| Stage | Files |
| --- | --- |
| 0 | `scripts/claude/r4_gate.py`, `evals/preregistered/claude-r4-gates/`, `tests/test_claude_r4_gate.py` |
| 1 | `scripts/claude/r4_pair_calibration.py` (everything else exists) |
| 2 | `scripts/claude/r4_split.py`, `r4_sitetype.py`, `r4_xsdata.py`, `r4_content_scores.py`, `r4_signals_local.py`, `r4_signals_cc.py`, `r4_signals_focus.py`, `r4_signals_lists.py`, `r4_signals_brand.py`, `r4_site_model.py`, `r4_stats.py`, `r4_stage2_eval.py`, and their tests |
| 3 | `scripts/claude/r4_rank_model.py`, `r4_position.py`, `r4_stage3_plan.py`, `r4_stage3_pull.py`, `r4_stage3_score.py`, `evals/preregistered/claude-r4-xs/`, and their tests |
| 4 | `src/aeo/ranksim/editsim/{content, anchor, effect, guards, output}.py`, the CLI hook, `scripts/claude/r4_sanity_suite.py`, `r4_band_backtest.py`, `r4_virtual_edits.py`, `r4_cc_versions.py`, `r4_ledger_score.py`, and their tests |

Existing code to reuse: `scripts/claude/rsdata.py`, `r3_model.py` (`FrozenScorer`, `twoway_interval`), `r3_features.py`, `r3_semantic.py`, `r3_dataset.py`, `r3_pull.py`, `r3_fetch.py`, `freeze.py`, `metrics.py`; `src/aeo/ranksim/position_aggregate.py`, `site_history.py`, `offpage.py`, `bands.py`, `ledger.py`, `draft_ledger.py`. Do not use `src/aeo/ranksim/authority.py` (a paid service client).

## Appendix C. Words used

- **Site-restricted order**: the order Google returns for `site:<site> <search>`.
- **Consensus**: the median position over a day's pulls, as defined in `RESULTS-claude.md` section 0.
- **Boundary pair**: one page in the top 10, the other at 11 to 30.
- **Middle guess**: always predicting the middle position of the list.
- **Raw domain prior**: the unshrunk mean position of the page's site on other search groups, the middle guess where the site was never seen.
- **Decided pair**: a pair where the scorer gives two different, non-missing scores.
- **Anchor**: the page's current latent position, from at least 3 pulls on at least 2 days.
- **Transfer factor (lambda)**: how much of the settled content effect an edit actually delivers, between 0 and 1.

## Appendix D. Adversarial review log

Pending. The review of this file and the list of changes it forced are added here before the plan is handed to James.
