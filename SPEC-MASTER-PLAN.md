# Ranksim master plan: from same-site content order to an approximate Google rank

> REVIEW IN PROGRESS (Nov 20, 15:50 ET). Sections 3 to 8 carry the review fixes. Sections 0, 1, 2.8, 2.9, 9 and the appendices are still the pre-review text and are being rewritten. Do not act on this file until this note is gone.

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
- Two pulls agree on 0.870 to 0.877 of all pairs (REAL). So one pull is right against the settled order about 0.91 to 0.94 of the time (DERIVED). Corrected for that, the scorer is right on 0.72 to 0.74 of pairs against Google's settled order, rho 0.63 to 0.69. The range comes from two label-noise models, and the 20 twice-pulled lists cannot tell them apart.
- So the content score explains **40% to 47%** of the within-site variance of pair differences. The rest is the page's unseen part.

**How large the site part is.** Let R be the variance of the site part divided by the within-site variance, among the candidate pages of a search. Inside a list that Google selected on the total score, the right estimate is the linear form (derived and checked by simulation in `r4-statistical-design.md` 1.3):

    1 + R  is about  (a_same - 1/2) / (a_cross - 1/2)

With BM25F at 0.597 on same-site pairs and 0.522 on cross-site pairs of the same ordinary top-30 lists: `R is about 3`. This rests on one pair of numbers. From sampling alone its 95% range is about 0.8 to 17, and "1 to 10" holds with a chance of about 0.88 (DERIVED). At R = 3 the site part holds about 75% of the score variance among candidate pages, the content score about 10% to 12%, and the page's unseen part the rest. Over the 95% range the site share runs from about 45% to 95%. This matches Google's own description of quality as mostly a site property.

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

`r_d` is the domain's own evidence from the lists it appears in and `v_d` its sampling variance. A site seen on many searches is judged by its own results. A site seen once, or never, falls back on its features, and its wide variance is carried into the output band. SIMULATED: in lists that Google selected from a larger pool, one listed appearance carries little information (about 0.12 units), so a domain needs about 40 or more appearances for a posterior standard deviation of 0.4 logits. In fully ordered lists it would need about 8 to 14. In c100 only 31 of 428 domains appear on more than ten searches and 256 on one only, so almost every domain's estimate rests mainly on its features.

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

If the upper tail of candidate scores is roughly exponential, the expected score at rank r is `a - b * log(r)`, so a score change becomes a change in log rank. SIMULATED at R = 3 (`r4-statistical-design.md` 5.3): one standard deviation of content score moved a page to 0.46 to 0.60 times its position number for starts of 5, 9 and 15 (from 5 to about 2.8, from 9 to about 5.1, from 15 to about 9, with 100 candidates). Over R = 1 to 10 the factor runs from 0.34 to 0.73. This does not hold from any start: position 1 cannot improve, starts of 2 and 3 move less (factor about 0.6 to 0.7), and past 20 the move depends on the size of the candidate pool. So the edit is applied on the latent scale and mapped back through the fitted position curve, clipped at position 1. The formula

    r1 = r0 * exp( - lambda * kappa_A * dm )

is only a cross-check for starts of 4 to 20.

- `dm`: the change in content score, in within-site standard deviations, on a scale that does not depend on which other pages are in the list.
- `kappa_A`: log-rank change per standard deviation. SIMULATED range 0.3 to 1.1 over R = 1 to 10 and 100 to 1000 candidates (0.5 to 0.8 at R = 3). Stage 4 fits a slope on same-site pairs in ordinary top-30 lists, but that slope is a **lower figure**: both pages were selected into the list, which pulls it toward zero. SIMULATED: the fitted slope stays near 0.33 to 0.43 while the truth runs from 0.34 to 1.09. So the simulator carries a range for `kappa_A`, not one number (group 4C).
- `lambda`: the **transfer factor**, between 0 and 1. The slope between two different settled pages mixes the text effect with clicks and links that follow good pages (Robinson 1950, Mundlak 1978). An edit moves the text at once and the rest slowly. No cross-sectional test can estimate lambda. It starts with a wide prior and is learned from natural experiments and live ledgers.

Error of the prediction: `Var = Var(anchor) + (transfer and kappa_A uncertainty) + (normal movement over the anchor's age plus the horizon)`. The anchor should be the median of at least 3 pulls on at least 2 days: about 10% of pages in a pull land at a random place (jump mixture fit, `r4-statistical-design.md` 5.2), and a single high pull is followed by a drop of 2.1 places on average.

**How big is a real edit?** One standard deviation is the spread between different pages of one site. DERIVED from the logistic half of the scorer (the boosted half was not split): about 17% of that spread comes from the URL, about 63% from the title, H1, headings and meta description, and about 20% from the body. So a rewrite that keeps the URL, title and H1 moves the score by about 0.1 to 0.3 standard deviations, and only a retargeted title and H1 approaches one.

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

1. **Freeze before scoring.** Each test's split, metric, threshold, code and model go into `evals/preregistered/claude-<name>/` with a sha256 manifest (`scripts/claude/freeze.py`), committed and pushed before any held-out data is pulled or read. A test is scored once. A result file is never overwritten. **Gates on design data are frozen too** (G2, G4.1 to G4.4). Before the first gate run of stage 2, freeze `evals/preregistered/claude-r4-site/`. Before the first gate run of stage 4, freeze `evals/preregistered/claude-r4-editsim/`. Each folder holds the code and every choice a gate depends on, written as a number or a rule: the pair set, missing-value encoding, signal directions, ridge values, cells, horizons, the case generator and its seed, and which estimate the product uses. A gate detail that is in neither `gates.json` nor the stage's frozen folder makes that gate's result VOID.
2. **Design seal and PR 45 seal.** Stages 2 to 4 fit and design only on data dated 2026-11-19 or earlier. Every reader of list rows, page fetches or Search Console data dated after that goes through one sealed loader, `scripts/claude/r4_sealed.py` (a new wrapper around `rsdata`; see rule 16). Until `evals/claude/r4/ledgers/pr45-v2/manifest.json` exists and verifies, the loader drops every row on the client's domain, every row of a PR 45 target search, every row of a search whose folded tokens (`r2_samesite_pairs._fold_tokens`) contain a PR 45 brand token (raken, busybusy, clockshark, b2w) or equal or contain the folded tokens of a PR 45 target, and every fetch of a client URL. For the client's domain and the PR 45 searches the seal is a time, not a date: `fetched_at_utc` before 2026-11-20T01:00:00Z (the merge). Site-restricted lists of other sites are not sealed.
3. **Splits.** Time first, then search group, then domain. Near-duplicate wordings stay in one search group. The 71 groups in `evals/claude/r3/plans/universe.json` are fixed and never recomputed (`r3_dataset.search_groups` only looks them up, so it cannot screen a new search; group 3D has the screen for new searches). The round 3 site folds stay: test1 is the stage 1 test, test2 sites contribute no training pairs and no domain effects in stages 2 and 3.
4. **Leave-group-out features.** Any feature built from result lists (footprint, niche link counts) for a pair or a page in search group g uses only lists of other groups, dated on or before the label day. **URL-new rule:** gated placement rows and gated site-strength pairs use only URLs that appear in no stored list of another search group dated on or before the label day, and the footprint of their domains is computed without those URLs' rows. This is what a new page looks like. Rows with URLs seen elsewhere are a secondary reading.
5. **Baselines on the same rows, always.** Middle guess, shrunk domain prior, best single signal, content only, site only, global authority only, persistence where it is defined, and the current ranker where it can be run. A gate needs an absolute bar and a paired difference against the best baseline.
6. **Baseline dominance check.** Before a gate is frozen, every baseline and a within-search label shuffle are run through its absolute bar on design data. The shuffle must fail. A baseline that clears the absolute bar is reported, and the gate must then carry a paired difference against that baseline.
7. **Pair counts.** Every gated cross-site pair metric is computed on one fixed pair set, each pair scored 1, 0 or one half (a tie or a missing score), the same way for the model and for every baseline. Correct, wrong, ties and missing are reported separately, and decided-pair accuracy is reported next to the gated value. A missing value is missing, never zero. A signal with a "not listed" flag may also enter as a second variant with unlisted ranked below every listed value.
8. **Intervals.** Pair metrics use the dyadic-robust variance (pairs that share a search or a domain are correlated; `r4-statistical-design.md` 6.1), and the two-way bootstrap is reported next to it. Row metrics (placement error, coverage) use a two-way cluster bootstrap over search groups and domains, 2000 draws, seed 20261120. Before a gate is frozen, the interval's coverage is simulated on the gate's own layout and must be 0.93 to 0.97. If not, the more conservative interval is used and that is recorded.
9. **Credits.** Never below 250. A script refuses to pull if the balance after the pull would be under 260, and it reads the balance again before every chunk (`r3_pull.py` does both). No pull starts while another user of the shared key is due to spend inside the pull's window (task 0.4). Every pull is chunked, each chunk is committed and pushed before the next, and a rerun imports chunks already in git (the box can roll back, credits cannot). No scrape credits.
10. **Free only.** No paid service, no paid signup. Outside lookups use bulk downloads where they exist, so no project detail leaves the box. The client's domain is never sent to an outside service unless D4 is approved. No API key is printed, logged or committed.
11. **Never touched.** The PR 41 ledger, earlier preregistrations, the product ranker, the client's site, main, and production.
12. **Labels.** Every model output is PREDICTED. Site-restricted order is called site-restricted order. Results on stored data that earlier work has read are called PROVISIONAL. A cross-validation on design data (Checkpoint 2 and most of Checkpoint 4) is called design cross-validation and PROVISIONAL, never unseen.
13. **Loops and the run log.** If a gate fails, the fallback in the stage is followed. At most one redesign loop per stage, and the loop must change the theory, not tune a knob. The failed test's data becomes design data. Enforcement: every gate run appends one line (checkpoint, commit, UTC time, sha256 of the result) to `evals/claude/r4/gate_runs.jsonl`, committed and pushed before the result is read. `r4_gate.py` reads that log from the pushed branch. A second run of a checkpoint prints VOID unless its line carries `redesign_loop=1` and names a theory note committed before the run. A third run is refused. Development uses a dev mode limited to two leave-group-out folds, which cannot write a gate result.
14. **Log.** After every checkpoint, an entry goes into `RESULTS-claude.md`, and the theory with its predictions made before scoring goes into `evals/claude/theory.md` (new part "Round 4"). Every entry opens with a table of every `gates.json` id at that checkpoint: value, interval, bar, and PASS, FAIL, NOT RUN or VOID. Stage 1 opens with G1-GOAL. A fallback is written "FAIL, fallback F taken", never "passed" or "advanced". No point estimate appears without its interval. Every number from stored lists that earlier work read carries PROVISIONAL in the same sentence. The entry links its audit file (rule 15).
15. **Independent audit.** No PASS is logged until a reviewer subagent that wrote none of the stage's code reruns `r4_gate.py` on a fresh clone of the pushed commit, checks rules 2 to 4 for leakage, and commits `evals/claude/r4/audits/<checkpoint>.md` with the rerun output. Until then the checkpoint reads "PASS pending audit". GOAL.md requires this.
16. **Frozen files are wrapped, never edited.** Until `evals/claude/r3/heldout/claude-r3-samesite/results.json` is committed, no commit may change any file listed in `evals/preregistered/claude-r3-samesite/manifest.json` (about 47 files, among them `rsdata.py`, `r3_model.py`, `r3_dataset.py`, `r3_features.py`, `r3_semantic.py`, `metrics.py`, `t2_changes.py`). Stages 2 to 4 add new modules that import them.

**Known exposures of the stored cross-site data (it is not pristine):**

| What was read | By whom |
| --- | --- |
| Nov 16 top-30 order of every search | round 2 same-site study |
| Tranco and Common Crawl ranks against the Nov 6 top 10s of 20 searches | earlier team, Nov 7 |
| Tranco and Common Crawl ranks against the Nov 6 and Nov 9 top 10s of 30 searches | `r4-statistical-design.md` recount |
| Majestic against the Nov 16 top-30 lists of all 100 searches, by pair type | `r4-free-label-sources.md` |
| Mean position of platforms by search type, all searches from Nov 16 | `r4-cross-site-evidence.md` |
| One-day and ten-day movement, Nov 6 to 17 | `r4-edit-simulator-design.md` |
| About 50 cross-site scorers, some fitted (logistic and small trees), on the original 30 searches, Nov 16 to 17 | round 1, `RESULTS-claude.md` 3.2 |
| The product ranker, fitted on cross-site lists | earlier team |
| A site's rank on sibling searches as a placement signal, pulls through Nov 19 | round 2, `evals/claude/r2/t1-mechanisms/notes.md` |
| Nov 18 and Nov 19 scored once for placement and movers | rounds 1 and 2, `evals/claude/heldout/REPORT.md` |
| Common Crawl capture counts against the Nov 16 lists (138 domains) | `r4-free-label-sources.md` 6 |
| Counts only on Nov 20 collector rows (rows, client rows, URLs new to a top 10). No position was scored | this review, Appendix D |

No model with the stage 2 site features has been fitted on any of it. History-based cross-site models and footprint-type transfers have been read. Because of these reads, every test on stored lists dated Nov 19 or earlier (Checkpoints 2, 3a and 4) is PROVISIONAL. Only data dated Nov 20 or later counts as unseen: the fresh pull (Checkpoint 3b, not affordable today, see section 9) and the collector days of Checkpoint 3c.

---

## 4. Stage 0: pre-flight (no credits, half a day)

**Inputs:** this file, the branch, the Serper balance.

**Tasks:**

| # | Task | Output |
| --- | --- | --- |
| 0.1 | Confirm the branch is level with origin (`git status -sb` shows no "ahead") | pushed branch |
| 0.2 | Write every gate of Appendix A into `evals/preregistered/claude-r4-gates/gates.json` in machine form: gate id, metric key, comparison, value, row-level file, interval type. Build the manifest with `freeze.py`, commit, push, and list the commit in `VERSIONS.json` in the same folder (append only). This happens before the stage 1 pulls, so the advance rules cannot drift. A later version of `gates.json` is allowed only for a rule 6 or rule 8 check, before that stage's first gate run, and it must name the check that forced it. `r4_gate.py` refuses a version unless every bar is equal or stricter and every interval equal or wider than in version 1 | frozen gate file |
| 0.3 | Build `scripts/claude/r4_gate.py <checkpoint>`. It contains no threshold of its own. Each checkpoint has one named producer script. The producer's result file holds a provenance block: HEAD commit, clean-tree flag, command line, sha256 of the inputs, of the row-level file (`pairs.csv` or `rows.csv`) and of the manifest. The gate script recomputes every gated number from the row-level file with the frozen `r4_stats.py`, checks the provenance against git, and never trusts a summary field. Every commit that touches `gates.json` or its manifest must be listed in `VERSIONS.json`, or the script prints REFUSED | gate script |
| 0.4 | Credit check (section 9). Read the balance B and the other users' run times from `serp_raw` (on Nov 17 to 20: a 15-credit tracker at about 09:10 ET, then three 30-credit collector pulls between about 09:30 and 11:00 ET). Stage 1 may start only if all three hold. (a) D1 is answered. (b) B is at least 478: the 260 reserve inside `r3_pull.py`, 208 for the pulls, 10 for charges with no stored response. (c) Pull A starts after the other users' last run of the day and pull B ends before their first run of the next day, so no other spend can fall between the two pulls. If pull B is refused or comes more than 6 hours after pull A, `r3_score.py` can never score the test, the held-out sites are spent, and 104 credits are lost | balance note in `RESULTS-claude.md` |
| 0.5 | Run `python -m pytest` and record the counts and the ids of the failing tests in `evals/claude/r4/baseline_tests.json`. Expected: the six known DNS failures in `tests/test_layered_fetch.py` and no other failure | baseline test record |
| 0.6 | Housekeeping from the inventory: use `index.sqlite`, not the parquet files (no `pyarrow` in the venv). Note that `r3_pages.load_pages` writes a cache under `/workspace/ranksim-data`. Fix the dead citation in theory R3.8 (`t1-postmortem/notes.md` does not exist; point to `results.md`). D6 decides the untracked script | notes |
| 0.7 | Write `scripts/claude/r4_c1_fallbacks.py` before the stage 1 pulls and freeze it with `gates.json`. It reads only `heldout/claude-r3-samesite/pairs.csv` and `heldout1.slices.json`, and uses `r3_model.accuracy` and `r3_model.difference` with the frozen seed and draws. It writes `c1_fallbacks.json`: the unfitted cross-encoder's accuracy, sites and pairs; cross-encoder minus BM25F; and, on the construction consensus pairs, `page-blend` accuracy, sites, pairs and `page-blend` minus BM25F, all with two-way intervals. The frozen `r3_score.py` writes none of these, so without this script the fallbacks of stage 1 would be computed by code written after the result is open | frozen fallback script |

**Tests:** `tests/test_claude_r4_gate.py`: the gate script fails closed on a missing field, reads thresholds only from `gates.json`, refuses an unverified manifest, refuses a hand-edited summary field, refuses a commit to `gates.json` that `VERSIONS.json` does not list, refuses a weaker later version, and prints VOID on a second run that the run log does not allow. `tests/test_claude_r4_c1_fallbacks.py`: right numbers on a synthetic `pairs.csv`.

**Gate G0 (all must hold):** branch pushed. `freeze.py verify evals/preregistered/claude-r4-gates` passes and its commit is in `VERSIONS.json`. The three conditions of task 0.4. No test fails that is not in `baseline_tests.json`.

**If G0 fails:** on credits, stop and raise D1. Stage 1 does not start on a guess. On tests, fix the cause before any freeze.

---

## 5. Stage 1: freeze and verify the same-site content scorer on unseen sites (Checkpoint 1)

**Purpose.** James's first priority. The scorer is fitted but not frozen and not scored. Everything is designed and reviewed (`evals/preregistered/claude-r3-samesite/preregistration.md`). This stage only executes it. The preregistration is not changed.

**Inputs:** the three fitted scorers in `evals/claude/r3/model/`, the plan `evals/claude/r3/plans/heldout1.jsonl` (104 lists, 93 test1 sites, 46 searches, 35 search groups), `test.json`, the pinned text models, 208 credits.

**Outputs:** `evals/preregistered/claude-r3-samesite/manifest.json`, `evals/claude/r3/pr45/ledger.json`, `evals/claude/r3/heldout/claude-r3-samesite/{results.json, pairs.csv, report.md}`, a section in `RESULTS-claude.md`.

### Task groups

**Group 1A. Freeze.** Build the manifest over `r3_score.py` and every file it imports, `r3_pull.py`, `r3_fetch.py`, the three model folders, `models.json`, the universe and heldout1 plan files, `model-next.json`, `offpage.tsv`, `test.json` and `preregistration.md`. The command is `python scripts/claude/freeze.py closure evals/preregistered/claude-r3-samesite <modules> -- <files>` (the full argument list is in `r4-data-inventory.md` section 10). Pass the model files one by one: `freeze.py` hashes files, not folders. Then `freeze.py verify`. Commit and push. The gates commit of stage 0 comes before this freeze and the PR 45 ledger commit after it. The scorer's guards accept both.

**Group 1B. PR 45 direction ledger.** Right after the freeze: `python scripts/claude/r3_pr45.py predict`, commit, push. It reads only the before and after HTML saved on Nov 19. It must run before any script reads a result of the PR 45 target searches dated after the merge. This ledger is version 1 (direction calls from the same-site scorer). The collector has already stored rows of these searches fetched after the merge (Nov 20). No script of this plan has read their positions, and rule 2 keeps it that way.

**Group 1C. Pulls and fetch.** Both pulls fall in one window with no other user of the shared key running inside it (task 0.4). Never pass `--reserve` to `r3_pull.py`. If pull B is refused, do not change the reserve or the labels. Tell James at once: the test can be saved only if credits arrive within 6 hours of pull A.
1. Pull A: `python scripts/claude/r3_pull.py --label heldout1-a --plan /workspace/ranksim-claude/evals/claude/r3/plans/heldout1.jsonl --max-credits 104`.
2. Fetch pass, once, plain HTTP only. First `python scripts/claude/r3_fetch.py --label heldout1-a --dry-run`. If it reports nothing to fetch, stop and ask: the scorer needs exactly one fetch summary, and the script writes none in that case. Then run it without `--dry-run`. Commit and push the one `summary-heldout1-a-*.json` and its sidecar once, before pull B. The script does not commit them itself, and the scorer refuses an uncommitted or changed summary.
3. Pull B: same command with `--label heldout1-b`, at least 30 minutes and at most 6 hours after pull A for every list. Read the balance first: pull B needs at least 364.

**Group 1D. Score once.** `python scripts/claude/r3_score.py --prereg evals/preregistered/claude-r3-samesite`. Commit and push the results whatever they show. Then `python scripts/claude/r4_c1_fallbacks.py` once, then `python scripts/claude/r4_gate.py c1`.

**Group 1E. Descriptive add-ons after scoring (not part of the preregistration).** New script `scripts/claude/r4_pair_calibration.py`: from `pairs.csv`, a reliability curve of the pair probabilities with one temperature fitted on training lists only, and accuracy by position gap with the label-noise correction of section 2.2. These numbers are descriptive.

**Tests:** `python -m pytest tests/test_claude_r3_core.py tests/test_claude_r3_semantic.py tests/test_claude_heldout.py tests/test_claude_r4_c1_fallbacks.py`. The scoring script's own guards (full manifest, freeze commit is an ancestor of every chunk commit, no third pull, one fetch pass, pull gap) must all pass or it refuses to run.

### Gates

**G1-GOAL (the frozen round 3 target, unchanged).** On consensus pairs, primary scorer `page-blend`, two-way interval: at least 40 sites and 800 consensus pairs. Lower bound above 0.65. Primary minus BM25F lower bound above 0. Primary minus each variant of the current ranker's content part lower bound above 0. Stretch: lower bound above 0.75.

**G1-ADV (the rule to advance to stage 2, declared here before any held-out list exists).** All of:
1. The run is valid: every guard of `r3_score.py` held.
2. At least 40 sites and 800 consensus pairs.
3. Primary point estimate on consensus pairs at least 0.65, and two-way lower bound at least 0.60.
4. Primary minus BM25F: two-way lower bound above 0.

Why the advance rule is weaker than the goal: stages 2 to 4 need a content score with a real signal on unseen sites. They do not need the lower bound to clear 0.65, and each later stage has its own gate. G1-GOAL is still reported as passed or failed exactly as frozen.

The rate in G1-ADV, F1-A and F1-B is the frozen scorer's own rate (a tie or a missing score counts one half), the same footing as G1-GOAL. Decided-pair counts are reported next to it. The fallback numbers come only from `c1_fallbacks.json` (task 0.7).

One test here has three routes to advance. So `RESULTS-claude.md` and every later provenance record name the route: G1-GOAL passed, or G1-GOAL failed and advanced by G1-ADV, by F1-A or by F1-B. A scorer that advanced by F1-A or F1-B was picked after the result was seen, so its accuracy is reported as selected and biased upward. Rung 1 of the validation ladder counts as passed only for G1-GOAL.

**PREDICTED (made in the preregistration before scoring):** 0.69 on consensus pairs (range 0.64 to 0.73), lower bound about 0.65. G1-GOAL passes with probability about 0.45. G1-ADV passes with probability about 0.85 (lead's estimate).

### Fallbacks, declared now

| Case | Rule | Next step |
| --- | --- | --- |
| F1-A | `page-blend` fails G1-ADV, but the unfitted cross-encoder on title, H1 and meta (nothing fitted, already a baseline in the same run) meets conditions 2 to 4 | The unfitted cross-encoder becomes the content score for stages 2 to 4. Nothing was fitted, and the rule is written here first, so this is not a post-hoc pick |
| F1-B | Neither meets G1-ADV on all lists, but `page-blend` meets conditions 3 and 4 on the construction-software lists alone, with at least 25 sites and 500 consensus pairs (the topic slice is already frozen in `heldout1.slices.json`) | Advance with the claim limited to niches that have training lists. A claim that content matters across sites is made only if G3.5a passes |
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
- `evals/claude/r4/site/content_oof.csv.gz`: out-of-fold content scores for stored rows (task 2A.4).
- `evals/claude/r4/site/signal_spec.json`: every signal's formula, direction and missing-value encoding, committed before group 2C runs.
- `evals/preregistered/claude-r4-site/` (rule 1): frozen before the one gate run.
- `evals/claude/r4/stage2/{tables.md, results.json, pairs.csv}` and a `RESULTS-claude.md` section.
- Large downloads go to `/workspace/ranksim-data/external/` (new folder, append only, not in git), with a sha256 list in git. Read the large files as streams: the box has about 6 GB of free memory.

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
1. `scripts/claude/r4_split.py`: assign the construction search groups to two folds by seeded hash, seed 20261120: **XS-design** 50%, **XS-test-stored** 50%. The largest group (35 dirt work searches, about a third of all pairs) is placed in XS-design by rule. Size check, counts only: XS-test-stored must hold at least 12 groups and between 30 and 40 c100 searches with a Nov 16 deep list. If the check fails, use seed 20261121, then 20261122, and so on. Record every seed tried. No other criterion may be used. Record for each test group whether the content scorer saw it in training (round 3 train group) or not. Why 50%: the stored test is now the main cross-site test (section 9), and at 35% it would usually hold about 23 deep-list searches, under its own validity bar. XS-design still keeps the 35-search group and 25 to 35 other deep-list searches.
2. `scripts/claude/r4_sitetype.py`: the site-type table, frozen.
3. `scripts/claude/r4_xsdata.py`: tables of rows and pairs through `r4_sealed.py`. One list per search for fitting: the Nov 16 deep list where it exists (used to its own depth: 66 lists reach position 30, 30 stop near 20), else the search's single stored pull. Pair types: inside top 10, boundary (one page in the top 10, the other at 11 to 30), inside 11 to 30. Pair weights make each list's total weight equal to its number of scorable pages. Page text for a row is the stored fetch closest to the label day within 14 days, else the row is unreadable. Each row also gets a `text_mismatch` flag when no 8-word run of Google's stored snippet appears in the stored main text. The share of flagged rows is reported by day and site type, and every stage 2 and 3 table has a line with flagged rows dropped.
4. `scripts/claude/r4_content_scores.py`: the frozen scorer applied to ordinary-list pages. **List invariance is required:** the same page with the same text for the same search must get the same score whatever other pages are in the list. The frozen `page-blend` standardises inside a list, so this script replaces in-list standardisation with frozen pooled scales computed from the training lists (no weight is refitted). **Out-of-fold scores are required on stored rows.** The frozen scorer was fitted once on all training lists, and 85 of the 100 deep-list searches are in its training groups. So the script also writes `content_oof.csv.gz`: for every ordinary-list (search, page) whose site is a round 3 train site or whose search is in a round 3 train group, a score from `r3_model.fit_predict`, refitted with the frozen spec, seed and site folds (`r3_model.site_folds`) on the training table minus that site's fold and minus that search group, with the pooled scales recomputed the same way. The frozen weights used to score new pages do not change. Every fit and every gate that uses a content score on stored rows uses `content_oof` there: `zbar` (S3c), `kappa` both ways (3A), `kappa_A` (4C), the bands of 3B, G3.5, G4.3 and G4.4. Each is also reported on sites and groups the scorer never trained on. Check (G2.0): on the training lists, with site-held-out fits on both sides, the list-invariant score must agree with the in-list score (pair accuracy within 0.005, within-list rank correlation at least 0.98). If the check fails, use the logistic part alone (`text-logit` style), which was preregistered as a secondary scorer.
5. Free text fill: one plain-HTTP pass over Nov 19 URLs without text, and Common Crawl captures through `data.commoncrawl.org` (`cluster.idx` plus range reads; `index.commoncrawl.org` does not answer from this box). Record source and capture date.

**Group 2B. Signals.** One script per family, each with a loader that keeps missing as missing:
`r4_signals_local.py` (S1, S3d, S7, S8), `r4_signals_cc.py` (S2, with resumable downloads, `curl -C -`), `r4_signals_focus.py` (S3a to S3c), `r4_signals_lists.py` (S5, reusing `src/aeo/ranksim/offpage.py`), `r4_signals_brand.py` (S4, S6, polite rate, results cached, the client's domain filtered out unless a D4 flag file exists).

**Group 2C. Single-signal table (design, XS-design only).** Commit `signal_spec.json` first, so no direction or encoding is chosen after seeing this table. Then `scripts/claude/r4_stage2_eval.py signals`: each signal alone on the fixed pair sets of rule 7 (1, 0 or one half), with correct, wrong, ties and missing shown, by pair type, with and without platforms, on small-site pairs, and on URL-new pairs (rule 4). A signal with a "not listed" flag appears twice: missing as missing, and unlisted ranked last. This is the baseline table for the gate, and the best single signal is the best row of it on the gate's pair set. It also reruns the recount of section 1 so the old 0.38 to 0.42 figures are settled in a committed script.

**Group 2D. The site-strength model.** `scripts/claude/r4_site_model.py`: the step 2 model of section 2.4 without the page-level content term (the site-only model: S1 to S8, `zbar`, domain effects). Fixed choices: logistic link, all signals enter (no selection), each signal standardised on XS-design, ridge on domain effects with `tau^2` chosen by nested leave-group-out log-likelihood over a grid written in the freeze, one ridge value on the other weights written in the freeze. Statistics helpers in `scripts/claude/r4_stats.py` (dyadic variance, two-way bootstrap, coverage simulation, power), ported from the appendix of `r4-statistical-design.md`.

**Group 2E. Checkpoint 2.** Before the first gate run, freeze `evals/preregistered/claude-r4-site/`: `r4_site_model.py`, `r4_stage2_eval.py`, `r4_stats.py`, `xs_split.json`, `site_types.tsv`, `signal_spec.json`, the signals file, and a config that fixes every open choice (the gate's pair set, ridge value, tau grid, pair weights, missing handling, list choice). Commit and push. Then `r4_stage2_eval.py gate`, once: nested leave-group-out cross-validation over XS-design. Every prediction for group g comes from a model that never saw g, with features that leave g out. The gate refuses to run if the manifest fails or `stage2/results.json` exists, locally or on the pushed branch. Then `r4_gate.py c2`. XS-test-stored is not opened in stage 2. Checkpoint 2 is a design cross-validation on lists that earlier work has read. It is reported as PROVISIONAL and never as accuracy on unseen data.

### Tests

- `tests/test_claude_r4_split.py`: same seed gives the same folds, no group in two folds, the largest group is in XS-design, test2 sites carry no training weight.
- `tests/test_claude_r4_xsdata.py`: a row after the seal date raises. Pair types are right at positions 10 and 11. `correct + wrong + ties = pairs`, and a tie is never counted as right or wrong. Deep positions never come from the index table.
- `tests/test_claude_r4_sealed.py`: a planted client row and a planted PR 45 row, both fetched after 2026-11-20T01:00Z, are dropped by every loader that stages 2 to 4 use. A row of a search that only contains a PR 45 brand token is dropped too. The drop ends only when the ledger manifest verifies.
- `tests/test_claude_r4_signals.py`: planted-leak test (a synthetic domain that ranks only in group g has footprint 0 for g). As-of date test. URL-new rule: a URL listed in another group is not a gated row, and its rows do not count in its domain's footprint. A domain missing from a global list stays missing and gets its flag. The outbound request list never contains the client's domain without the D4 flag.
- `tests/test_claude_r4_content_scores.py`: list invariance (score unchanged when other pages are added or removed), the agreement check of task 2A.4 on a fixture, and an out-of-fold check: no row in `content_oof` was scored by a fit that saw its site or its search group.
- `tests/test_claude_r4_site_model.py`: on the synthetic design of `r4-statistical-design.md` simulation 3, the two-step fit keeps content weights with cosine at least 0.95 and beats content only. A domain with only wins or only losses gets a finite effect. The shrinkage formula matches the closed form. Dyadic interval coverage is 0.93 to 0.97 on a synthetic layout.
- All tests run with no network and with keys unset.

### Gate G2 (Checkpoint 2), on nested leave-group-out predictions over XS-design

| Id | Metric | Bar |
| --- | --- | --- |
| G2.0 | List-invariant content score agrees with the in-list score, both from site-held-out fits | pair accuracy within 0.005, rank correlation at least 0.98 |
| G2.1 | Real evidence behind the estimates: share of non-platform top-30 domains with at least one non-missing outside signal or evidence from their own lists. The client's domain has an estimate. The share of prior-only estimates is reported | at least 0.80 |
| G2.2 | Site-only model on the gate's pair set: cross-site boundary pairs of the Nov 16 deep lists in XS-design, both pages readable, no platform, both URLs new under rule 4, scored per rule 7 | dyadic 95% lower bound above 0.60, and model minus the best single signal of group 2C on the same pairs: paired dyadic 95% lower bound above 0 |
| G2.3 | Same, on small-site boundary pairs (both domains absent from Majestic Million and Tranco) | point at least 0.58, dyadic lower bound above 0.52, and point estimate above the footprint-only score (S1) on the same pairs, with the paired interval reported |
| G2.4 | Leak tests of the test list above | all pass |
| G2.5 | Baseline dominance: a within-search label shuffle run through the absolute bar of G2.2. Each single signal's result on that bar is reported | the shuffle fails |

Why G2.2 changed: one free signal already clears 0.60 on the full Nov 16 data (Majestic 0.633 [0.607, 0.659], unlisted counted as zero). So the absolute bar proves little, and the gate now rests on a paired difference with an interval. G2.1 used to ask for an estimate for 95% of domains, which the prior gives to every domain by construction.

Reported next to the gate, not gated: the same model on all boundary pairs (URLs seen elsewhere included), inside the top 10 (PREDICTED 0.53 to 0.58, an honesty check on section 2.3), inside 11 to 30, with and without platforms, by search type, and on the revenue niche.

**PREDICTED (lead's estimates, not computed):** G2.2 point 0.62 to 0.68 on URL-new pairs. The best single signal is expected at 0.62 to 0.66 and is likely the footprint itself. G2.3 point 0.56 to 0.64. Chance that all of G2 pass: about 0.4. The main risk is that the model adds nothing measurable over the best single signal.

### Fallbacks

| Case | Next step |
| --- | --- |
| G2.2 fails only on the paired difference against the best single signal | FAIL, fallback taken: the best single signal becomes the site-strength value, with the same shrinkage. Stage 3 runs with it. Say plainly that the model added nothing over it |
| G2.3 fails (small sites cannot be told apart) | For a small site with no footprint, the no-anchor mode says "cannot tell: site not established for this topic". The product leans on the anchored modes. Raise D2 and D5 with James |
| G2.2 lower bound at or under 0.60 with every signal | One loop allowed, and it must change the theory: move the test to "does the site reach the top 10 at all" as a per-domain classification, add the page-two labels from D5 if approved. If that also fails, stage 3 runs content plus the raw domain prior only, and the no-anchor mode is tiers only |
| G2.0 fails | Use the logistic part alone as the content score, as task 2A.4 says |

---

## 7. Stage 3: content plus site strength, full-SERP pairs, and positions (Checkpoints 3a and 3b)

**Purpose.** Combine the frozen content score with the stage 2 site strength into one model that orders pages of different sites, turn the pairwise wins into a position with an honest band, and test it on search groups the model never saw. The sites in those searches are mostly known from other searches. Sites with no stored history are a separate slice (G3.6), and rows of test2 sites and of cold domains are always reported apart.

**Three tests, in order of how much they prove:**

| Checkpoint | Data | Credits | Status of the result |
| --- | --- | ---: | --- |
| 3a, stored test | XS-test-stored, the Nov 16 deep lists of search groups never used in fitting | 0 | PROVISIONAL: earlier work has read these lists |
| 3c, forward test | collector days dated Nov 20 or later that are already in the store, pages new to a top 10 | 0 | unseen in time, same searches. This is GOAL Target 1 |
| 3b, fresh test | new searches from new search groups, pages 1 to 3 | 90 to 180 | unseen. **Not affordable from today's balance** (section 9). Runs only if credits appear (D3) |

**Inputs:** stage 2 outputs, XS-design lists, the frozen content score with `content_oof` on stored rows, the multi-pull days (for the noise model).

**Outputs:**
- `evals/claude/r4/model/rank-v1/` (the frozen combined model: kappa, weights, domain effects, tau, the reach model, the position curve, the band quantiles, with a save and load API like `r3_model.FrozenScorer`).
- `evals/preregistered/claude-r4-xs/` (preregistration, `test.json`, manifest).
- `evals/claude/r4/plans/xs_fresh.jsonl` (the fresh-search plan) and its meta file, only if 3b runs.
- `evals/claude/r4/stage3/{stored, forward, fresh}/{results.json, rows.csv, pairs.csv, report.md}`.

### The three tasks the model is scored on

Tiers are the same everywhere in this plan: 1 to 3, 4 to 10, 11 to 20, 21 to 30, beyond 30.

- **T-A, placement.** For each test search and each readable, non-platform page in its list: hide that page, and predict its position from its content score and its site strength. The model receives the rivals as an order (ranks 1 to n-1 among themselves). It never receives their absolute positions or the list length with the gap, because the one missing number would be the answer. The site strength uses no list of this search group. Output: posterior median position, 50% and 80% bands, and probabilities for the tiers 1 to 3, 4 to 10, 11 to 20, 21 to 30. **Gated rows** are targets with no other page of their domain in the list and with a URL that is new under rule 4. That is what a new page with no anchor looks like. Rows with a same-site page in the list are scored as mode B (sibling anchor) and reported apart. Rows with URLs seen elsewhere are a secondary reading.
- **T-B, reach.** For each test search and each niche domain that has a readable indexed page: the probability that the domain appears in the search's top 10, and in its top 30. The candidate page is the domain's top page in a stored site-restricted list for the search where one exists, else its indexed page with the highest content score, the same rule for every model. The log of the domain's indexed page count is a covariate in every model and baseline, because the best score of many pages is higher by chance. This task covers the "beyond 30" outcome that T-A cannot see: T-A only has rows for pages that did reach the list.
- **T-C, order from scratch.** All cross-site pairs of the list: which page is higher. Reported for all pairs, boundary pairs, inside the top 10 and inside 11 to 30, scored per rule 7.

### Task groups

**Group 3A. The combined model.** `scripts/claude/r4_rank_model.py`: the full step 2 model of section 2.4, fitted once on XS-design (one list per search), with `content_oof` scores on stored rows. Fixed choices as in 2D. Report `kappa` with a search-group bootstrap interval. Also fit `kappa` a second way, from same-site pairs of ordinary lists only (a conditional logit on the content score), and report both. If they differ by more than a factor of 2, say so: content and site are then not on one scale. The same script fits the **reach model** for T-B, because the pairwise model has no page that failed to rank and so cannot learn reach: a logistic classifier on XS-design (domain, search) rows. Positives are domains in the top 10 (and, in a second fit, the top 30) of the Nov 16 deep list. Negatives are niche domains with a readable indexed page that are absent from that list. Features: the combined model's score for the candidate page, the stage 2 site strength, and the log page count, all leave-group-out. It is frozen with `rank-v1`.

**Group 3B. Positions.** `scripts/claude/r4_position.py`:
1. Position curve and pull noise: fit the jump-mixture noise model (a page lands at a random place with probability q, otherwise gets Gaussian jitter) to pair agreement by position gap, from the multi-pull days of XS-design searches. This gives the latent spacing of positions 1 to 10. Positions 11 to 30 are extrapolated on the log-rank form of section 2.7 and flagged as extrapolated until repeat deep pulls exist.
2. Calibrated placement: inside each search, regress the rivals' latent positions on their model scores (a pooled slope with a per-search intercept), place the target at its fitted value, and take the posterior median. The rivals' positions here are their ranks among themselves, as T-A requires.
3. Bands: split-conformal error quantiles from leave-group-out placements on XS-design, by tier of the predicted position. The noise model alone never sets a band.
4. Expected rank `1 + sum P(rival beats page)` is computed as a cross-check. Reuse `src/aeo/ranksim/position_aggregate.py` where it fits.

**Group 3C. Checkpoint 3a, stored test (0 credits, PROVISIONAL).** Freeze the model and the preregistration `evals/preregistered/claude-r4-xs/`. Before the freeze, compute the power of each gate at the PREDICTED effect for the real size of XS-test-stored, by simulation from the XS-design errors. If power is under 0.8, the gate stays as written and the report says a fail is uninformative. Then score XS-test-stored once with `scripts/claude/r4_stage3_score.py --cell stored`. T-A and T-C use the deep-list searches only. The top-10-only searches of the fold (PR 41 baseline and ClockShark searches) are a separate reported slice. If the model is refitted afterwards, the stored cell is design data from then on. With today's balance this is the main test of the cross-site model.

**Group 3D. Checkpoint 3b, fresh test. Not affordable today.** By the size rule below, the balance after stage 1 gives N = 13, under the minimum of 30 (section 9). The group stays in the plan so that it can run unchanged if credits appear.
1. `scripts/claude/r4_stage3_plan.py`: candidate searches are the related searches and "people also ask" strings in stored raw responses dated on or before Nov 19 (1,028 and 542 distinct strings). Eligibility is computed for each candidate against the stored searches only, never through other candidates. With t = `r2_samesite_pairs._fold_tokens`, a candidate c is excluded if, for any of the 155 stored searches s, the token sets of t(c) and t(s) are equal, they share a token trigram, or one token set contains the other (any size, including one-token stored searches). It is also excluded if it contains a brand token from a frozen list (every brand named in a stored search, the client's brand, and every PR 45 brand token) or a navigational word from a frozen list (login, sign in, app, download, careers, glassdoor, support, reddit, complaints). It must contain a niche term from a frozen list. All three lists are written to the plan's meta file before sampling. Fresh groups come from `assign_folds` over the eligible candidates alone. The 71 universe groups are never recomputed. Draw by seeded hash, at most 2 searches per new group. The eligible list and its count are written before the draw.
2. Size, fixed by the balance on the day of the freeze: `N = min(60, floor((balance - 250 - 15) / 3))`. If N is under 30, Checkpoint 3b is not run, and that is reported as "not tested on unseen searches".
3. Freeze and push. Then pull pages 1, 2 and 3 of each search once, all three within minutes (a small `r4_stage3_pull.py`; chunked, each chunk committed and pushed). 3 credits per search.
4. One plain-HTTP fetch pass, then the Common Crawl fill. No browser pass, no second pass, no scrape. Client URLs are not fetched.
5. Score once: `r4_stage3_score.py --cell fresh`, reading through `r4_sealed.py`. The client's domain is never a T-A target, a T-B domain or a T-C pair member in this cell, and its rows are removed before the rivals' order is formed. Guards as in round 3: full manifest, the freeze commit is an ancestor of every chunk commit, one pull, one fetch pass.
6. Every fresh result is also reported on the fresh searches whose top 10 shares fewer than 3 URLs with any stored top 10.

**Group 3E. Checkpoint 3c, forward test on collector days (0 credits). This is GOAL Target 1.**
1. Days: the collector days dated 2026-11-20 or later that are in the store when `claude-r4-xs` is frozen. Nov 20 is there (two to three pulls of the original 30 searches). Later days exist only while the other team's collector still has credits. This plan buys none. The days are named in the preregistration. No script reads their positions before the freeze, and nothing is ever fitted or widened on them.
2. Rows: a (search, URL) whose URL is in the day's consensus top 10 and in no stored pull of that search in the 7 days before. Read through `r4_sealed.py`, so client rows and PR 45 searches are out.
3. Prediction: the frozen stage 3 model places the URL among the previous day's consensus list of that search, as mode B when its site already has a page there and as mode C when not. Page text is the collector's own fetch of the new URL.
4. Score once, `r4_stage3_score.py --cell forward`, with the GOAL metric: share within 3 places of the consensus position, against the best of the domain prior, position 7 and position 11, with a 95% interval of the difference by search. GOAL Target 1 passes if that interval is above 0 on at least two days. With one day it is reported as "Target 1 not decided: one day". Rows are few (PREDICTED 20 to 50 a day), so the interval will be wide.

### Tests

- `tests/test_claude_r4_position.py`: on synthetic data at pair accuracy 0.65 the posterior median beats the middle guess and matches the table of section 2.6 within 0.15 places. The raw win count shows its known bias. Bands cover at their stated rate on synthetic data. The expected-rank identity holds. The T-A prediction does not change when the hidden page's true slot moves and the rivals' order is kept.
- `tests/test_claude_r4_rank_model.py`: save and load round trip gives identical scores. Scores are list invariant. A test2 site has no domain effect.
- `tests/test_claude_r4_score_guards.py`: scoring refuses without a manifest, with an unpushed freeze, with a second pull, with a second fetch pass, or when a result file exists locally or on the pushed branch. A forward-cell run refuses a day that the preregistration does not name.
- `tests/test_claude_r4_plan.py`: `raken alternative free`, `raken pricing per month`, `raken time tracking` and `busybusy login` are not eligible. `universe.json` is unchanged. The draw is deterministic. N follows the formula.

### Gate G3 (applied to Checkpoint 3a as PROVISIONAL, and to 3b if it ever runs)

Rows: the gated T-A rows defined above (readable, no platform, no same-site page in the list, URL new under rule 4). Validity for 3a: at least 30 searches with a deep list and at least 200 gated T-A rows. Validity for 3b: at least 30 searches and 300 gated rows. A test under its validity bar is reported as "not tested: too few rows", with descriptive numbers only, and is never scored against the bars. G3 passes only if G3.1 to G3.7 all pass, where G3.5a may instead be "not testable".

| Id | Metric | Bar |
| --- | --- | --- |
| G3.1 | T-A mean absolute error of the posterior median, combined model | at least 8% below the middle guess at the point estimate, and the paired difference (baseline minus model) has a two-way 95% lower bound above 0 against the middle guess and against the domain prior (the shrunk one, Appendix C). The site-only model's error on the same rows and the paired difference (site-only minus combined) are reported with a two-way interval |
| G3.2 | T-A band coverage and width | the 80% band covers 0.72 to 0.88 of rows, the 50% band 0.42 to 0.58, and the median width of the 80% band is at most 0.85 of the width of the 80% band of the middle guess on the same rows |
| G3.3 | T-C pair accuracy, cross-site pairs scored per rule 7 | all pairs of the list: dyadic lower bound above 0.55. Boundary pairs: dyadic lower bound above 0.58. On both pair sets, model minus the best single signal of group 2C: paired dyadic 95% lower bound above 0 |
| G3.4 | T-A tier forecast (tiers 1 to 3, 4 to 10, 11 to 20, 21 to 30) | ranked probability skill score against the tier frequencies of XS-design is above 0 with a two-way lower bound above 0 |
| G3.5a | Content matters across sites | on the test rows, T-C cross-site pairs, combined minus site-only: paired dyadic 95% lower bound above 0. Before the freeze its power at the PREDICTED effect is simulated. If that is under 0.8, G3.5a is reported as "not testable" and no stage says that content matters across sites |
| G3.5b | No harm from content | T-A mean absolute error, combined minus site-only: two-way 95% upper bound under 0.10 places |
| G3.6 | No harm on cold sites (domains in no stored list) | T-A error at most the middle guess's at the point estimate. Reported with its interval |
| G3.7 | Reach | T-B log loss below the base-rate forecast (the XS-design frequency of a niche domain reaching the top 10, and the top 30) with a two-way 95% lower bound of the difference above 0, and not above the footprint-only baseline's at the point estimate |

`kappa` is reported with its interval and is not gated: it comes from the design fit. Rows of test2 sites and of cold domains are reported apart for G3.1 to G3.3.

Always reported next to the gate: correct, wrong, ties and missing; pair accuracy inside the top 10 (PREDICTED 0.56 to 0.62, not gated, see section 2.8); the secondary T-A readings (same-site page in the list, URL seen elsewhere); results with platforms as targets; by search type; by whether the content scorer saw the search group; content only, site only, global authority only and the current ranker on the same rows.

**PREDICTED (lead's estimates, not computed):** T-C all pairs 0.58 to 0.64, boundary pairs 0.62 to 0.68. T-A error about 7% to 14% below the middle guess (from T-C 0.58 to 0.64 through simulation 4, a point or two less against one pull), so the 8% bar of G3.1 is near the low end of that range. Chance that G3 passes on the stored test: about 0.15 (0.10 to 0.20). The most likely failures are the 8% bar of G3.1 and a lower bound in G3.3 just under its bar.

### What each outcome means, and the fallbacks

| Outcome | Meaning | Next step |
| --- | --- | --- |
| G3 passes on 3b, or on 3a together with a Target 1 pass on 3c | The no-anchor mode is supported on unseen data | Stage 4 enables it with tiers and bands |
| G3 passes on 3a only | The no-anchor mode is supported on stored lists that earlier work has read, and on nothing unseen | Stage 4 enables it with tiers only, and every output carries the label "PROVISIONAL: not tested on unseen searches" |
| G3.7 fails, the rest pass | The model places a page that reaches the list, but cannot say whether a page reaches it | The no-anchor mode shows tiers only as "if it reaches the top 30" and says "cannot tell" for the chance of reaching it |
| G3.3 and G3.4 pass, G3.1 fails | The model orders pages but its position estimate is no better than simple guesses | FAIL, fallback taken: the no-anchor mode gives tiers only, with no position, and the PROVISIONAL label |
| G3.5a fails or is not testable | Content is not shown to add anything across sites once site strength is in | The no-anchor mode uses site strength only. No output says content changes placement across sites. The anchored mode rests on stage 4's own gates |
| G3.2 fails | Bands are wrong | FAIL, fallback taken: widen from the test errors, and mark the widened bands "untested" until new data exists |
| 3a fails badly (model worse than a baseline at the point estimate) | The model is wrong, not just weak | One loop that changes the theory: the top-k Plackett-Luce likelihood with a candidate pool of known pages (`r4-rank-aggregation.md` section 7), which corrects the selection bias of top-k lists. Then 3a again as design data. After that only 3c or 3b can test it |
| 3a is under its validity bar, or everything fails | Cross-site placement is not shown with free data today | The product is the anchored modes only. The no-anchor mode says "cannot tell". The report says which data would change that: D2, D3 |

---

## 8. Stage 4: the edit simulator and live confirmation (Checkpoints 4 and 5)

**Purpose.** The end product: give it a search, a page and a changed version, get an approximate rank with honest uncertainty. Validate it with tests that do not wait on Google. Confirm it later on real edits without ever blocking on them.

**Inputs:** the frozen content scorer (stage 1 must have advanced by G1-ADV or a fallback; under F1-C stage 4 does not start), the list-invariant score and `content_oof` (stage 2, group 2A), the position curve and noise model (stage 3, group 3B), the rank model for pages with no anchor (stage 3, if it passed), stored lists, the PR 41 and PR 45 material, the Search Console exports on disk.

### The three modes

| Mode | When | Method |
| --- | --- | --- |
| A, anchored | The page is in a stored list of the search | The edit is applied on the latent scale and mapped back through the stage 3B position curve, clipped at position 1. Then the movement band for the anchor's age plus the horizon |
| B, sibling anchor | The page has no position, but another page of the same site ranks for the search or a near-duplicate search | Mode A against the sibling's anchor, plus a residual-reset term that widens the band (a new URL does not inherit the sibling's clicks and links) |
| C, no anchor | Neither | The stage 3 model, with the labels its outcome table sets. If stage 3 failed or was not valid: "cannot tell" |

**Anchor.** The median of at least 3 page-one pulls in total over at least 2 distinct days. Fewer pulls, or a page at 11 to 30 (only one deep pull exists, Nov 16), gives mode A with the flag "thin anchor". The output reports the anchor's age in days. A fresh anchor costs 3 credits per search per day, and section 9 does not fund it. For a ledger, `anchor.py` takes an explicit `as_of` UTC time and reads only pulls fetched before it (PR 45: 2026-11-20T01:00Z, the merge).

**What is covered today (REAL counts).** A full anchor exists for pages in the top 10 of the 100 construction searches. It is refreshed daily only for the 30 searches the other team's collector pulls, and only while that collector has credits. At 11 to 30 the anchor is one pull and the band is untested. 401 of the client's 429 Search Console queries have no stored list at all. `simulate-edit` never pulls: for a search with no stored list it returns "cannot tell", reason `no_list`, unless a Search Console anchor exists (task 4B.2).

If the site already has two pages in the list, the output flags host crowding: the edited page may swap with its sibling instead of adding a slot.

### Interface

Command: `python -m aeo.ranksim simulate-edit --project NAME --search "..." --url URL --after-html FILE [--before-html FILE] [--horizon-days 28]`. Also a function `simulate_edit(...)` returning the same record. With two `--after-html` files it compares two candidate edits.

Output record (JSON, every field labeled PREDICTED, rendered as text for the report):

    search, url, mode, anchor {median position, pulls, days, age_days, source, latent}, horizon_days
    effect  {p_edit_better, word, expected_change_places {lower, upper}, dm, edit_kind, lambda_prior, kappa_A_range, scale_label}
    landing {tier_probabilities: 1to3, 4to10, 11to20, 21to30, beyond30, band80, most_likely_tier}
    confidence {level, reasons}, cannot_tell {flag, reasons}
    flags [host_crowding, thin_anchor, untested_horizon, scale_simulated, provisional_no_anchor, ...]
    noise_floor {band at the longest tested horizon}, track_record {past band coverage, pooled ledger hit rate}
    provenance {model sha256, stage 1 route, data through date, core update status}

Rules:
- Probability words follow the IPCC scale ("likely" only at 66% or more).
- `p_edit_better` is the calibrated chance that Google orders the after text above the before text at score gap `dm`, read from the stage 1E reliability curve (group 4C). It is not a count of simulation draws: with shared noise and a positive scale every draw would favour the edit, which would hide the scorer's own error rate of about one pair in four.
- "Cannot tell" is set when `p_edit_better` is between 0.34 and 0.66 (the IPCC "about as likely as not" range), the edit is under the frozen material-change rule (title or H1 changed, or at least 250 tokens and 2% of the body), a sanity guard fires, the page cannot be read, the search has no stored list, or mode C is not supported.
- A band carries a coverage percentage only for a start tier and horizon that passed G4.2. For any other horizon or start tier the output sets `untested_horizon`, shows no percentage band, and shows the band of the longest tested horizon as the noise floor. The default horizon stays 28 days, so until a 28-day cell has passed, the default output has no percentage band.
- `expected_change_places` is a range, from the lower scale figure to the upper one (group 4C), and `scale_label` says whether the scale is measured or SIMULATED.
- Every prediction is appended to the hash-chained ledger (`src/aeo/ranksim/ledger.py`).

### Task groups

**Group 4A. Package.** `src/aeo/ranksim/editsim/`: `content.py` (loads the frozen scorer and checks its sha256, never refits, byte-identical scores to the round 3 output on a fixture), `anchor.py`, `effect.py` (modes A, B, C), `guards.py`, `output.py`, and the CLI hook in `cli.py`. Nothing is merged to main or deployed.

**Group 4B. Anchor and movement bands.**
1. The latent anchor from the pulls before the `as_of` time, shrunk toward the position curve (this handles regression to the mean). Movement bands by start tier and horizon, from stored transitions. Bands are whole positions. Each band is the narrowest whole-position band around the latent anchor, clipped at 1, whose coverage on the width-setting transitions is at least its stated rate. A page that left the top 10 has an unknown position: it is never given position 11 (`rsdata.ABSENT`), and a band covers it only if the band's upper end is "beyond 10". The band covers movement over the anchor's age plus the horizon. Tiers and horizons with no passed backtest are marked untested (interface rules).
2. `scripts/claude/r4_gsc.py`: a read-only loader for the Search Console exports on disk (`/workspace/seo-tracker/smartcrew/gsc-export/` and `gsc-compare-2026-11-16/`), with each file's date range checked to end on or before 2026-11-19. Uses: (a) for a client query with no stored list, a flagged anchor (`anchor.source = gsc`, the band widened by the measured window-to-window movement of these averages), never used in any gate; (b) the window-to-window movement of client queries at 11 to 30 and at 31 to 100 (59 and 101 queries have a position in both windows), reported next to G4.2 as a descriptive check of the untested tiers; (c) the client's share of niche queries with a position, reported as a client-only value and never mixed into the cross-site features, because no other site has it. These are impression-weighted averages, not list positions, and the output says so.

**Group 4C. The edit effect.**
1. `kappa_A`, the lower figure: an ordinary regression of the log-rank gap on the content-score gap over same-site pairs of non-platform sites in the deep lists of XS-design, with `content_oof` scores, clustered by site. This slope is biased toward zero, because both pages were selected into the list on the outcome. SIMULATED: it stays near 0.33 to 0.43 while the true value runs from 0.34 to 1.09. One site, hcss.com, holds about 60% of the non-platform same-site pairs, and only about five other sites have five or more pairs, so report pairs and sites with and without it.
2. `kappa_A`, the upper figure: a synthetic recovery test (`tests/test_editsim_effect.py`, the simulation 5b worlds: 100 and 1000 candidates, R of 1, 3 and 10, 5 pages per site, top-30 selection, pair counts matched to the real layout) reports the attenuation factor A = fitted slope divided by the true mean log-rank change over starts 4 to 30. The upper figure is the lower figure divided by the smallest A (SIMULATED smallest A about 0.37, so about 2.7 times the lower figure). If G4.4 is untested or fails, the range is instead the SIMULATED 0.3 to 1.1 and every output carries `scale_simulated`.
3. `lambda`: prior uniform on 0 to 1. The band integrates over that prior and over the `kappa_A` range. The "upper figure" of an edit is `lambda = 1` at the upper `kappa_A`. The prior is updated only by rungs 4 and 5 below, under a rule frozen before those rungs are read.
4. `p_edit_better`: one temperature fitted on training lists, the reliability curve checked on the stage 1 held-out pairs (`r4_pair_calibration.py`, group 1E). The output labels it as carried over from pairs of different pages to two versions of one page. Position ties count as no change and are reported as their own share.
5. Edit kinds, reported before G4 is frozen: on design pages, the distribution of `dm` for virtual edits that change the body only, the title and H1 only, or both, and the share of each kind that ends in "cannot tell".
6. Mode B residual reset: measured on stored same-site swaps (a new URL replaced a sibling). There are few (27 on the held-out days of round 2), so it stays a wide term.

**Group 4D. Validation ladder (cheap first).**

| Rung | Script | What it shows | Cost |
| --- | --- | --- | --- |
| 0 | `scripts/claude/r4_sanity_suite.py` | The simulator does not reward edits that should not matter (appended irrelevant text, synonym swaps, duplication, date-only edits, keyword stuffing) and moves the right way on clear cases (on-topic title replaces off-topic title). CheckList style: invariance, direction, minimum function. A frozen, seeded generator builds the cases from 200 readable pages drawn by hash from XS-test-stored search groups, before any guard is written. Half the pages are a development set. The other half is sealed and scored once. Invariance edits are sized at or above the material-change rule, so that rule cannot absorb them | 0 credits |
| 1 | stage 1 | Content order on unseen sites | done in stage 1 |
| 2 | `scripts/claude/r4_band_backtest.py` | With no edit, the stated 80% band covers at least near its rate. Widths are set on transitions ending on or before Nov 17. They are tested on transitions ending Nov 18 and 19 (PROVISIONAL: those days were scored once before for other targets) and on the collector days dated Nov 20 or later that are in the store, read through `r4_sealed.py`. No later collector day is assumed: the collector runs out of credits within days | 0 credits |
| 3 | `scripts/claude/r4_virtual_edits.py` | Virtual edits: page A of a site given page B's title, then title and H1, then all text. The score must move strictly toward B at each step. Swap pairs: one site, two pages, opposite order under two searches. Position backtest: for same-site pairs of non-platform sites in the deep lists of XS-test-stored groups, read once and only after Checkpoint 3a, predict B's position from A's anchor and the score gap. This backtest checks gaps between different settled pages. It does not test the size of an edit's effect | 0 credits |
| 4 | `scripts/claude/r4_cc_versions.py` | Common Crawl natural experiments: for training-site pages whose text changed materially between monthly captures, does today's site-restricted order agree more with the after version than the before version, and more for older changes? Unchanged pages are the control. This is the only free estimate of lambda | 0 credits, 1 to 2 weeks |
| 5 | `scripts/claude/r4_ledger_score.py` | Live edits: PR 45, PR 41, later edits | 18 credits per PR 45 read, 222 per PR 41 read (section 9). Weeks |

**Group 4E. Live confirmation, never blocking.**
- **PR 41 ledger** (the earlier team's file, never edited): read only. Outcomes are appended to a new file `evals/claude/r4/ledgers/pr41-outcomes.jsonl`.
- **PR 45 ledger version 1** (stage 1, group 1B): direction calls from the same-site scorer.
- **PR 45 ledger version 2**: the full simulator's predictions for the 6 target searches that have a before rank (raken alternative, raken pricing, busybusy alternative, clockshark alternative, b2w alternative, earthwork software), frozen in `evals/claude/r4/ledgers/pr45-v2/` as soon as the simulator passes G4. Its anchors use `as_of` 2026-11-20T01:00Z, so no pull from after the merge enters them. One of the six (raken pricing) has a single pull before the merge, so its anchor is thin. The ledger counts as blind only if rule 2 of section 3 held for every script. Otherwise it is labeled "not blind" and kept out of the pooled rate. The three other PR 45 searches were never pulled before the merge, so they have no before rank and no direction can be scored on them.
- **Score dates:** edit plus 28 days and plus 56 days (PR 41: Dec 16 and Jan 13. PR 45: Dec 17 and Jan 14), and never earlier than 7 days after Google marks the core update complete. Any earlier read is a diagnostic.
- **What a read costs.** The truth rule needs 3 pulls of every ledger search on the score date, and the other team's collector will not be running in December (section 9). A PR 45 read of its 6 searches costs 18 credits. A PR 41 read of its 74 searches costs 222 (the 6 PR 45 searches are among them). The balance left after stage 1 pays for the two PR 45 reads and nothing else. The PR 41 reads are not funded: without new credits (D3) that ledger stays unscored and the report says so.
- **Scoring rule (frozen before the first read):** truth is the consensus of at least 3 pulls on the score date. Each edited (page, search) row is compared with matched controls: the same site's unedited pages on the same searches, and the unedited rivals of the same results page. A hit is a called direction that matches the sign of the edited-minus-control change. Rows with no call are reported with their share and left out of the rate. Each edited row also gets a reprocessing marker: whether the title or snippet Google shows on the score date contains text found only in the after version. Rows Google has not yet reprocessed are reported apart and left out of the hit rate.
- **Pooling across ledgers:** a Beta-binomial hit rate with a flat prior and with a skeptical Beta(10, 10) prior, and an anytime-valid e-value against 0.5 with design alternative 0.65. Both are reported after every ledger. No ledger is stopped or extended because of them.
- **Why it cannot block:** PR 45 has at most about 18 in-play rows on its 6 scorable searches, worth about 9 to 11 independent rows (DERIVED from the memo's 24 rows on 8 searches). At a true hit rate of 0.70 the sign test has power of about 0.25 to 0.30. A miss proves nothing and a hit proves little. About 37 to 69 independent rows are needed for 80% power at 0.70 to 0.65.

### Tests

- `tests/test_editsim_content.py`: frozen scorer hash check, byte-identical scores on a fixture, list invariance.
- `tests/test_editsim_effect.py`: mode selection, the latent-scale edit on known inputs, the output never goes above position 1, zero edit gives zero paired effect, antisymmetry (A to B is the reverse of B to A), monotone in `dm`, host-crowding flag, and the synthetic recovery test of task 4C.2 with its attenuation factors.
- `tests/test_editsim_bands.py`: each band is the narrowest whole-position band that reaches its stated rate on the width-setting rows, so a band cannot be widened by hand. A page that left the top 10 is covered only by a band that ends "beyond 10". No percentage band is shown for an untested tier or horizon.
- `tests/test_editsim_guards.py`: stuffing, duplication, irrelevant text and date-only edits trigger their guards. The material-change rule matches `scripts/claude/t2_changes.py`.
- `tests/test_editsim_output.py`: schema, the IPCC words at their cut points, "cannot tell" from 0.34 to 0.66, every "cannot tell" reason, the PREDICTED label on every number, byte-identical JSON on two runs of a fixture.
- `tests/test_editsim_ledger.py`: every prediction is appended, the hash chain verifies, the PR 41 file is never opened for writing, and a pull planted after the `as_of` time leaves the ledger record byte-identical.
- `tests/test_claude_r4_ledger_score.py`: the scorer refuses a score date inside the core-update window unless a "diagnostic" flag is set, and refuses to run before the scoring rule is frozen.
- The whole suite passes with keys unset and with fake keys, with no network.

### Gate G4 (Checkpoint 4, the release gate for the simulator on this branch)

| Id | Metric | Bar |
| --- | --- | --- |
| G4.1 | Rung 0 sanity suite, sealed half of the cases, scored once | Invariance cases: median absolute paired effect under 0.25 places, and "no change" or "cannot tell" in at least 90%. Direction cases: a call (not "cannot tell") on at least 70%, the right sign on at least 85% of the calls, the wrong sign on under 5% of all cases. Stuffing flagged in 100%. Each guard fires on at most 5% of the sealed clean direction cases and on at most 5% of real material changes between stored fetches of one URL (dated Nov 19 or earlier). The "cannot tell" share is reported by reason |
| G4.2 | Rung 2 coverage of the 80% band with no edit, on days not used to set the widths | Cells are fixed now: start tier (1 to 3, 4 to 10) by horizon (1, 2, 3 days). A cell counts when it holds at least 30 searches and 3 outcome days. In every such cell the realized coverage is at least 0.72. At least 4 cells must count. Tier 11 to 30 and every longer horizon are reported as untested and never merged into a cell. Reported, not gated: the 50% and 90% bands, median widths, a "conservative" note for any 80% band that covers more than 0.90, and the same numbers on the days dated Nov 20 or later alone |
| G4.3 | Rung 3 | Virtual edits: after the full-text step the score has moved toward B in at least 90% of cases (a tie fails), and the median share of the gap to B that it closes is at least 0.5. Step-by-step movement is reported. Swap pairs: accuracy with a by-site lower bound above 0.5. Position backtest, valid only with at least 5 non-platform sites and 100 pairs, else reported as untested: direction accuracy at least 0.60 and at least 0.03 above BM25F on the same pairs at the point estimate, and mean log-rank error below the "no change" baseline at the point estimate. Its by-site intervals are reported with and without the largest site |
| G4.4 | `kappa_A`, lower figure | by-site bootstrap 95% lower bound above 0, with all sites and with hcss.com left out. Valid only if XS-design holds at least 8 non-platform sites with at least 5 readable same-site pairs each after hcss.com is removed. Otherwise it is reported as untested. Reported: the effective number of sites, the estimate without the client's domain, and separate estimates for pairs with both pages at 1 to 10 and both at 11 to 30 |
| G4.5 | Software | All tests pass except the ids in `baseline_tests.json`. The JSON output is byte-identical on two runs of a fixture. One prediction takes under 60 seconds on this box |

Why G4.2 changed: positions are whole numbers, and a page at 1 to 3 stays in place about 0.70 of the time over one day, so no band can cover "50% within 7 points". The old rule also failed a perfectly calibrated band about half the time by chance at 150 clustered rows. Before the freeze, a band that is exactly calibrated and one that is 10 points too narrow are run through the new rule on the gate's own layout of searches and days. The first must pass with probability at least 0.85 and the second at most 0.10. If the layout cannot give that, horizons 2 and 3 are pooled into one cell, the check is repeated, and the change is recorded.

G4.4 tests the sign of the scale, not its size. **No gate of this plan compares a predicted edit effect with what Google did.** That check first comes from rung 4 and from the ledgers.

A gate that is reported as untested is not a pass. The simulator is then released on this branch "with fallbacks", and each fallback shows in the output flags and in `RESULTS-claude.md`.

Not gated, always reported: rung 4 (after-minus-before agreement, controls, the time split, and the lambda estimate it implies) and rung 5 (the pooled hit rate, the e-value, band coverage on real edits).

**PREDICTED (lead's estimates, not computed):** G4.2 passes with probability about 0.8 if the bands are calibrated. G4.3 direction accuracy 0.60 to 0.68, and its position backtest is likely untested for lack of sites. G4.4 is likely untested: fewer than 8 sites have enough same-site pairs. So the simulator most likely ships with a SIMULATED scale for the size of an edit's effect. Chance that G4.1, G4.2 and G4.5 pass: about 0.6. Chance that all five gates pass: about 0.1.

### Fallbacks

| Case | Next step |
| --- | --- |
| G4.1 fails | The guards may be changed once, on the development half only, and that is the stage's one loop. The sealed half was scored once and is not scored again: the simulator is released accepting only the edit kinds whose sealed cases passed, and says "cannot tell" for the rest |
| G4.2 fails | FAIL, fallback taken: widen the bands by conformal quantiles of the test errors and release them marked "widened, not yet tested on unseen days". They are retested only if new pulls are funded |
| G4.4 fails or is untested | Mode A shows the direction and its probability. The change in places is shown only as a range from the SIMULATED scale (`kappa_A` 0.3 to 1.1), with the flag `scale_simulated` and the words "size not measured" |
| G4.4 estimate for pairs at 11 to 30 is not above 0 | Mode A gives direction only for a start at 11 to 30 |
| G4.3 position backtest fails | Same as a G4.4 fail. The paired effect is shown as a direction with its probability |
| Rung 4 shows no agreement gain for after versions | Lambda's prior moves toward 0, bands widen, and the report says the edit effect is unconfirmed. This does not block release of the band and tier outputs |

### Checkpoint 5 (live reads, on the calendar, never a gate)

| Date (not before 7 days after the core update completes) | Read | Credits | Funded today |
| --- | --- | ---: | --- |
| Dec 17 | PR 45 plus 28, 6 searches, 3 pulls | 18 | yes, from the balance left after stage 1 |
| Jan 14 | PR 45 plus 56, 6 searches, 3 pulls | 18 | yes |
| Dec 16 and Jan 13 | PR 41 plus 28 and plus 56, 74 searches, 3 pulls | 222 each | no. Needs new credits (D3) |
| Each later client edit | A new ledger frozen before the edit goes live, read at plus 28 and plus 56 | 3 per search per read | no |

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
