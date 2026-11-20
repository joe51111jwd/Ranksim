# Round 4 research: statistical design for separating content from site strength, and honest ceilings

Research and theory memo, written 2026-11-20. No production code. No existing file edited. Zero Serper credits spent; no call to any Serper host. No held-out list of the pending round 3 test (heldout1) was pulled or read. Outside requests used generic statistics queries only.

What the numbers below rest on:

- **DERIVED**: algebra in this memo, checked by simulation.
- **SIMULATED**: synthetic Monte Carlo, numpy only, no project data. Code and seeds are in Appendix A, so every number can be rerun.
- **RECOUNT**: one side check (section 0) that re-reads two committed design-day files (Nov 6 and Nov 9 ordinary top 10s, already in git) to test how an earlier number was computed. It is not a simulation and it uses no held-out data. Its code is in Appendix A too.
- Outside sources carry the grades STRONG, MEDIUM, WEAK, SNIPPET ONLY or UNVERIFIED, as defined in the task. All are listed in Appendix B.

Notation. For search q and page p on domain d: u = beta*c(q,p) + s(d) + g*a(q,d) + e. In one search, s(d) + g*a(q,d) is constant across a site's pages. I call that sum the **site part**. I call w = beta*c + e the **within-site part**. R = Var(site part) / Var(w) over the candidate pages of a search, before Google picks its top 10. "Pair accuracy" means the share of pairs a score puts in the same order as the label.

## What this means for the master plan

1. **Drop the claim that global authority "points the wrong way".** The 0.392, 0.420 and 0.377 figures were computed with score ties counted as wrong and kept in the denominator (`src/aeo/ranksim/snapshot_protocol.py`, `pair_counts` and `summarize_queries`). Missing Common Crawl values are NaN and fall into ties too. A recount on the committed Nov 6 and Nov 9 top 10s (30 searches) gives 0.528 to 0.548 on decided pairs, with intervals touching 0.5. That is weakly positive, as range restriction predicts. A proof in section 2.1 shows that selecting on the total score cannot reverse the sign of a signal. So treat these features as weak site features, not as harmful ones.
2. **Do not gate cross-site pair accuracy inside an ordinary top 10 at 0.65.** Inside a top 10, even perfect knowledge of every site's strength orders only 0.62 to 0.78 of cross-site pairs. A site estimate correlated 0.7 with the truth, plus the current content scorer, reaches only 0.58 to 0.60 (section 2.3, simulation 1b). Against a single pull, subtract about 0.01 to 0.03 more. Gate on placement error instead (within 3 and mean absolute error) against the middle-position guess. Measure pair accuracy on wider lists (top 30 or more, or with known non-ranked losers), where pairs are further apart.
3. **The measured numbers imply that the site part holds about 3 times the within-site variance (plausible range 1 to 10).** They do not imply 20 to 125 times. Inside a selected list, use the linear excess ratio 1 + R ≈ (a_same - 0.5) / (a_cross - 0.5), not the arcsine formula (section 1.3). With R near 3, the site part carries about 75% of the variance of the total score among candidate pages. That fits Google's own description of quality as mostly a site property.
4. **Build the site-strength model in two steps.** Step 1: learn the content weights only from same-site pairs (the site part cancels). Step 2: fit cross-site pairs with four terms: the frozen content score with one free scale, site averages of the page features (Mundlak terms), outside site features, and domain effects shrunk toward that regression (ridge, which is the same as a random effect). Never learn content weights from cross-site pairs without domain effects. In simulation 3, that gave a page feature that only tracks site strength the same weight as real content (cosine to the true weights 0.74). That model would tell a user that changing a site-correlated page trait moves rank.
5. **Get more widely separated pairs. The fix is efficiency, not bias.** Fitting a pairwise logit on top-10-only lists recovers the content-to-site coefficient ratio up to scale (Li and Duan's result, confirmed in simulation 2: 1.04 to 1.09 against a truth of 1.00). But the ratio's spread is 0.21 with top-10 pairs only, 0.09 with top-30 lists, and 0.05 when non-ranked candidates are added as losers. Free losers already exist: pages in the site-restricted lists or the Nov 16 deep list that sit outside the ordinary top 30 lose to every top-30 page. The coefficient scale changes 3.6 times between these designs. So the scale must be refitted for each label design and never carried across.
6. **Place a page with the shrunk (posterior median) estimate, never with raw win counts or unshrunk scores.** At realistic accuracy, unshrunk placement loses to guessing the middle. For n = 10 and a pair accuracy of 0.60 on scored pairs: unshrunk mean error 3.26, middle guess 2.50, shrunk 2.08 (section 4 table). Never report the target-against-rivals pair accuracy of a placement as skill: guessing the middle scores 0.72 to 0.75 on it.
7. **For pages that already rank, predict edits anchored on the current position.** New utility = anchored utility + kappa * beta' * (c1 - c0). This needs the content coefficient on the ordinary-results scale and does not need the site strength at all. Site strength is only needed for pages that do not yet rank. Anchor on the median of at least 3 pulls. Pull noise fits "about 10% of pages per pull land at a random place" much better than Gaussian jitter (section 5.2). With 3 pulls, the median is a jump about 3% of the time.
8. **Label every edit prediction as cross-sectional until real edits calibrate it.** The slope between different pages mixes the content effect with clicks and links that track content (Robinson, Mundlak, Neuhaus and Kalbfleisch). One crude sign: swap pairs, which remove page-level signals that do not depend on the search, score 0.61 to 0.65, against 0.71 for all same-site pairs. Estimate a transfer factor on the PR 45 ledger and on any Wayback cases before quoting moves in positions.
9. **Intervals.** For cross-site pairs, use the dyadic-robust variance: add residual products over every two pairs that share a search or a domain. It kept 95% coverage in simulation 6c. A bootstrap with search weight times domain weight times domain weight covered 100% at twice the width. A search-only bootstrap fell to 0.895 at 75 searches. On the same-site design, the frozen round 3 two-way bootstrap is conservative (coverage 0.983 against 0.95 nominal). Keep it for round 3, because conservative is safe. For any new gate, check the interval's coverage by simulation on the gate's own cluster layout before freezing.
10. **Power.** To show a cross-site lower bound above 0.60, true accuracy must be at least 0.65 and the test needs about 75 to 150 searches. True 0.62 never clears 0.60 at any affordable size (power at most 0.32 with 200 searches). A bar of 0.55 needs about 50 to 75 searches at true 0.62. Each search needs at least 3 pulls for a consensus label, so 75 searches cost about 225 credits. Unless the test reuses future daily-collector pulls, that competes directly with the 208 credits reserved for round 3.
11. **Write every gate so that no baseline can pass it.** Score every baseline on the same rows: middle guess, domain prior, persistence where defined, content only, site only, and the current ranker. Require both an absolute bar and a paired difference against the best baseline, with the lower bound above 0. Run each baseline through the frozen gate on design data first. If any baseline passes, the gate is invalid (checklist in section 6.5).

## 0. Side check: the authority numbers were computed with ties counted as wrong (RECOUNT)

What it means: the evidence that global authority hurts inside top 10s does not hold. The signals are weakly right, which is the attenuation section 2 predicts.

- Code read: `pair_counts` in `src/aeo/ranksim/snapshot_protocol.py` says "A tie in the score is not counted as a correct order", and `summarize_queries` puts ties in the denominator. `cc_features` in `src/aeo/ranksim/snapshot_signals.py` returns NaN for a domain the crawl file lacks, and NaN comparisons land in the tie branch. `tranco_features` gives 0 to unlisted domains, so two unlisted domains tie. Same-site pairs always tie on a domain signal. The Nov 7 numbers in `COORDINATION.md` (Tranco 0.392 of 469 pairs, PageRank 0.420, harmonic centrality 0.377) came from this code (commit `21723d1`).
- Recount. Files: `evals/forward-2026-11-09/baseline_serp.csv` (Nov 6), `forward_serp.csv` (Nov 9), all 30 searches, top 10, with the frozen domain table `evals/preregistered/forward-pair-2026-11-16/offpage.tsv`. These are not the same rows as the Nov 7 check (20 training queries with stored HTML), so this does not reproduce the 469-pair numbers. It tests the reading of them.

| Day | Signal | Pairs | Correct | Wrong | Ties | Ties counted wrong | Decided pairs [95% by search] |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| Nov 6 | Tranco popularity | 1,296 | 598 | 535 | 163 | 0.461 | 0.528 [0.468, 0.584] |
| Nov 6 | CC PageRank | 1,296 | 645 | 560 | 91 | 0.498 | 0.535 [0.483, 0.584] |
| Nov 6 | CC harmonic centrality | 1,296 | 658 | 547 | 91 | 0.508 | 0.546 [0.487, 0.600] |
| Nov 9 | Tranco popularity | 1,341 | 640 | 570 | 131 | 0.477 | 0.529 [0.474, 0.583] |
| Nov 9 | CC PageRank | 1,341 | 692 | 580 | 69 | 0.516 | 0.544 [0.498, 0.588] |
| Nov 9 | CC harmonic centrality | 1,341 | 697 | 575 | 69 | 0.520 | 0.548 [0.506, 0.590] |

Majestic is mostly missing (672 to 685 ties) and is at 0.496 to 0.498 on decided pairs. Every pairwise report should give correct, wrong and ties separately.

## 1. Pair accuracy and explained variance

What it means: the page-blend explains about 40% to 47% of the within-site variance in pair differences once label noise is removed. The two BM25F numbers imply a site part about 3 times the within-site part, not 20 or more. Content alone will stay near 0.51 to 0.58 across sites inside a top 10.

### 1.1 The formula (DERIVED; sources STRONG)

Let D = m_i - m_j be the score difference and T = u_i - u_j the true difference. If (D, T) is bivariate normal with mean 0 and correlation rho, then

    P(sign D = sign T) = 1/2 + arcsin(rho) / pi.

Two published facts give this. First, pair precision = 50% + 50% * Kendall's tau (Bifet et al. 2005, section on measures, STRONG). Second, tau = (2/pi) arcsin(rho) under bivariate normality (Greiner's relation; Lindskog, McNeil and Schmock 2001 trace it to Stieltjes 1889 and Sheppard 1898, STRONG). If pages are independent draws, the correlation of differences equals the correlation of the page-level values.

Inverse: rho = sin(pi * (a - 1/2)). Explained variance of pair differences = rho^2.

### 1.2 Correcting the 0.709 for label noise

The same site-restricted list pulled twice agrees on 0.877 of pairs. Two label models:

- **A. Independent flips.** Each pull shows a pair's true order with probability p, independent of everything else. Then p^2 + (1 - p)^2 = 0.877, so p = 0.934, and a_true = (a_obs - (1 - p)) / (2p - 1).
- **B. Gaussian pull noise.** A pull is u + eps. Two pulls agree with probability 1/2 + arcsin(r_L^2)/pi, where r_L = corr(T, T + delta eps). So r_L^2 = 0.926, r_L = 0.962, single-pull correctness p = 0.913, and rho_true = rho_obs / r_L.

| Observed | rho observed | B: rho, accuracy, rho^2 | A: accuracy, rho, rho^2 |
| --- | --- | --- | --- |
| 0.709 (site held out) | 0.610 | 0.634, 0.719, 0.402 | 0.741, 0.686, 0.471 |
| 0.704 (site and search group held out) | 0.598 | 0.621, 0.713, 0.386 | 0.735, 0.673, 0.453 |

Model B corrects less because its flips fall on close pairs, where the scorer is near coin anyway. The pull-noise fit in section 5.2 (a jump mixture) lies in between: 0.713 against one pull becomes 0.737 against the truth. **Use 0.72 to 0.74 as the noise-free within-site accuracy, rho about 0.63 to 0.69, and 40% to 47% explained.** The label ceiling against one pull is 0.91 to 0.93.

Assumptions, and how fragile each is:

1. Bivariate normal differences. Fragile for binary features and ties, mild for a blended score.
2. Independent pairs. Pairs in a list share pages. This does not bias the point estimate but does widen the interval (section 6).
3. Label errors independent of scorer errors, given the true order. Model B relaxes this. Both bracket the answer.
4. No selection. Wrong: a site-restricted list is the top 10 of the site's candidates. So rho inside the list is smaller than rho over all of the site's candidate pages, and 40% to 47% is a lower bound for the whole candidate pool.

### 1.3 What BM25F within and across sites implies about R

Without selection (random pairs of candidates), with content independent of the site part, rho_cross = rho_within * sqrt(1 / (1 + R)), so

    1 + R = (rho_within / rho_cross)^2.            (arcsine form, unselected pairs)

Inside a selected list, both pair types have a narrow spread of T. Because selection depends only on u, the regression of content on u keeps its slope within the selected set (section 2.1). So the pair accuracy is a = E[Phi(k * T)], and to first order a - 1/2 is proportional to k. The ratio of slopes is

    k_same / k_cross = (1 + R) * sqrt((1 - rho_within^2 / (1 + R)) / (1 - rho_within^2)),

and therefore

    1 + R ≈ (a_same - 1/2) / (a_cross - 1/2).        (linear form, inside a selected list)

The square-root factor is 1.00 to 1.05 for BM25F-size rho, so the linear form slightly overstates R.

Simulation 1 checks both forms. Data: 4,000 searches per cell, N candidates, 5 pages per site, top 10 kept, true R known.

| True R | N | Content quality rho | Same-site accuracy | Cross-site accuracy | R, arcsine form | R, linear form |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 100 | 0.30 | 0.555 | 0.524 | 4.2 | 1.3 |
| 1 | 1000 | 0.30 | 0.559 | 0.522 | 6.0 | 1.7 |
| 3 | 100 | 0.30 | 0.568 | 0.517 | 14.3 | 2.9 |
| 3 | 1000 | 0.30 | 0.560 | 0.519 | 9.1 | 2.2 |
| 10 | 100 | 0.30 | 0.582 | 0.510 | 68.4 | 7.4 |
| 3 | 100 | 0.63 | 0.663 | 0.537 | 16.4 | 3.4 |
| 3 | 1000 | 0.63 | 0.647 | 0.532 | 18.5 | 3.6 |
| 10 | 1000 | 0.63 | 0.670 | 0.514 | 125.4 | 10.8 |

Inside a top 10, the arcsine form overstates R by 3 to 13 times. The linear form lands within about a factor of 2 for R up to 3 and gets noisy above that.

Applied to the measurements:

| Numbers | Same lists? | Arcsine R | Linear R |
| --- | --- | --- | --- |
| Round 2, ordinary top-30 lists: same-site BM25F 0.597 [0.548, 0.647] vs cross-site 0.522 | yes | 17.9 | 3.4 (1.2 to 5.7 over the same-site interval alone) |
| Site-restricted 0.614 vs ordinary cross-site 0.51 to 0.52 | no (different selection) | 22 to 124 | 3.8 to 10.4, not valid |
| Held-out ordinary top 10s: 0.529 (51 pairs) vs 0.513 to 0.516 | yes | not informative | about 1, interval too wide |

**Reading: R is about 3, plausibly 1 to 10.** R includes query-to-domain affinity (brand searches), because that term is also constant within a site for a search. With R = 3 and the blend's quality, content alone orders 0.53 to 0.54 of cross-site pairs inside a top 10 (simulation 1). That matches the measured BM25 range of 0.444 to 0.559.

## 2. Selection on the total score

What it means: Google showing only its top 10 creates a strong negative correlation between content and site strength among the shown pages (Berkson). It shrinks every signal's pair accuracy toward 0.5 (range restriction). It cannot reverse a signal's sign. A pairwise logit on top-k pairs still recovers the coefficient ratio, but only up to an unknown scale and with high variance.

### 2.1 Selection on u cannot flip a signal's sign (DERIVED)

Claim. Let x and u be jointly normal with cov(x, u) > 0. Let the shown set depend only on the u values (top k of N, any k/N). Then for any two shown pages with u_i > u_j,

    P(x_i > x_j | shown, u_i > u_j) = E[ Phi( b * (u_i - u_j) / (sigma_{x|u} * sqrt 2) ) ] > 1/2,

where b = cov(x, u)/Var(u) and sigma_{x|u}^2 = Var(x)(1 - rho_xu^2).

Proof. Given the vector of u values, each x_i depends only on its own u_i, as N(a + b u_i, sigma_{x|u}^2). The shown set is a function of u alone. So conditioning on it changes the distribution of the u values but not x given u.

Consequences:

- A site signal ordering fewer than half of the decided pairs inside a top 10 cannot be a Berkson artifact. It needs one of three things: (a) a negative association in the population between the signal and some part of u, such as big generic domains having less topical pages; (b) a reranking that does not follow u (diversity rules, host crowding); or (c) a measurement artifact such as ties (section 0).
- Simulation 1, a site proxy x = s/sd + conf * w + noise: with conf = 0, pair accuracy is 0.562. With conf = -0.5, -1 and -2 it is 0.522, 0.494 and 0.463. Only population confounding pushes it under 0.5.
- The same holds if the label is a noisy pull and selection is on that pull: replace u by u + eps.

### 2.2 Berkson inside the top 10 (DERIVED, SIMULATED)

The Pearson and Lawley selection formulas apply to selection on a linear combination. Lawley (1943) is graded SNIPPET ONLY, because I found only its citation and secondary uses; the algebra below is self-contained and was checked by simulation. Let kappa = Var(u | shown) / Var(u) and let w and s be independent before selection. Then

    Cov(w, s | shown) = - Var(w) Var(s) (1 - kappa) / Var(u)
    Corr(x, u | shown) = rho0 * sqrt(kappa) / sqrt(1 - rho0^2 (1 - kappa))    (attenuated, never reversed)

These are exact for truncation and close for top k. Simulation 1 gives corr(w, s) inside the top 10 from -0.30 to -0.79. It is most negative when N is large and R is small.

### 2.3 How little any signal can do inside a top 10 (SIMULATED)

Simulation 1, cross-site pairs inside the top 10, against the true order:

| R | N | Perfect site value alone | Site proxy (corr 0.71 with s) | Content alone (rho 0.63) |
| --- | --- | --- | --- | --- |
| 1 | 100 | 0.606 | 0.560 | 0.565 |
| 3 | 100 | 0.664 | 0.579 | 0.537 |
| 3 | 1000 | 0.628 | 0.560 | 0.532 |
| 10 | 100 | 0.748 | 0.598 | 0.514 |
| 10 | 1000 | 0.697 | 0.564 | 0.514 |

Simulation 1b: content (rho 0.63) plus a site estimate with correlation r_s to the true site part, combined with the population-optimal weights:

| R | N | r_s = 0 | 0.3 | 0.5 | 0.7 | 0.9 | 1.0 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 100 | 0.563 | 0.569 | 0.575 | 0.593 | 0.628 | 0.657 |
| 1 | 1000 | 0.547 | 0.552 | 0.564 | 0.579 | 0.600 | 0.622 |
| 3 | 100 | 0.539 | 0.555 | 0.571 | 0.600 | 0.654 | 0.707 |
| 3 | 1000 | 0.535 | 0.539 | 0.555 | 0.576 | 0.616 | 0.664 |
| 10 | 100 | 0.524 | 0.536 | 0.564 | 0.589 | 0.662 | 0.775 |
| 10 | 1000 | 0.520 | 0.530 | 0.544 | 0.577 | 0.628 | 0.732 |

Against a single pull, a true 0.60, 0.65 or 0.70 shows as about 0.59 to 0.60, 0.63 to 0.64, or 0.67 to 0.69 (the two label models of section 1.2). Deeper lists help little when N is large. For R = 3 and N = 1,000, content-alone cross-site accuracy is 0.535 in the top 10, 0.533 in the top 30 and 0.540 in the top 100. The perfect site value goes 0.627, 0.652, 0.680. Pairs gain mostly when one page is far below the other, for example non-ranked candidates.

### 2.4 Logistic regression on pair differences fitted only on top-k lists (DERIVED, SIMULATED)

Theory. The label and the selection depend on the features only through the single index beta*c + gamma*x. If the features have linear conditional means given that index (true for Gaussian or elliptical features), then the misspecified pairwise logit is consistent for (beta, gamma) up to one scale factor. This is Li and Duan (1989) (MEDIUM: read through Park and Weisberg's 1995 technical report, which states the condition E(X | X'beta) linear in X'beta "satisfied by X normally distributed, and more generally by elliptically contoured X"). The key step: selection that depends on the features only through the index plus noise leaves E(x | index) unchanged.

Simulation 2. 100 searches per fit, 40 fits; 200 candidates per search; content c and site feature x each with variance 1; an unobserved site part (sd 1) and page part (sd 1.3); true ratio beta/gamma = 1.

| Pairs used | Ratio mean | Ratio sd | beta hat (scale) | Held-out top-10 accuracy (oracle 0.589) |
| --- | --- | --- | --- | --- |
| Top-10 pairs only | 1.09 | 0.21 | 0.31 | 0.588 |
| Top-30 deep list | 1.06 | 0.09 | 0.37 | 0.589 |
| Top 10 against ranks 11 to 30, plus top-10 pairs | 1.06 | 0.12 | 0.53 | 0.589 |
| Top 10 plus 20 non-ranked losers per search | 1.04 | 0.05 | 1.12 | 0.589 |
| Same four, heavy-tailed site feature (t, 3 df) | 0.93 to 1.09 | 0.06 to 0.17 | 0.33 to 1.12 | 0.588 to 0.589 |
| No selection (random pairs) | 1.02 | | 0.72 | |

The direction is recovered to within about 10%. The scale is not portable: it moves 3.6 times between designs. With 100 searches, estimation error does not limit accuracy in this setting; the invisible part does.

When the theory fails: binary or heavy-tailed features (a small bias, seen above), host crowding (at most about two results per site, which is selection on something besides u), and population confounding. Selection does not cause the confounding, but it does amplify omitted parts through the negative correlation inside the top 10.

Fixes, in order of value per credit:

1. **Known losers at no cost.** Pages from the site-restricted lists and the Nov 16 deep list that are not in an ordinary top 30 rank below every top-30 page of that search. Add those pairs. They are the most informative pairs about the site part.
2. **Deeper lists** (top 30 to 100) for a subset of searches.
3. **A top-k Plackett-Luce likelihood with a per-search "rest of the field" term.** Under Gumbel noise, the probability of the shown top-k order is

       prod_{r=1..k} exp(v_{pi_r}) / ( sum_{j shown, rank >= r} exp(v_j) + exp(theta_q) ),

   where theta_q is the log of the total worth of all unshown candidates. It is one free parameter per search. This is exact under the Plackett-Luce model. Fit it with MM (Hunter 2004, SNIPPET ONLY) or with gradient steps.
4. Heckman-style corrections for learning to rank exist (Ovaisi et al. 2020, MEDIUM: abstract read). They need a model of which items get shown. Not needed here.

## 3. Two-step identification and shrinkage of site effects

What it means: learn the content weights where the site cancels, then learn site strength around them with shrinkage. With a median of about 3 appearances per domain, domain effects help a lot for domains seen in training. For unseen domains, everything rests on site features and the coverage of the comparison graph.

### 3.1 The model

**Step 1, within site.** For same-site pairs (i, j) in a list for search q and domain d:

    P(i above j) = Lambda( beta' (c_i - c_j) )

s(d) + g*a(q,d) cancels. This is the conditional logit of Chamberlain (1980) (MEDIUM: abstract read). It says the joint maximum likelihood estimator is "not consistent" as the number of groups grows with a fixed number per group, while the conditional likelihood removes the incidental parameters. It is consistent however few searches a domain appears in.

**Step 2, across sites.** Use ordinary lists plus known losers. Let m = beta_hat' c, frozen from step 1.

    P(i above j) = Lambda( kappa (m_i - m_j) + gamma' (zbar_{d(i)} - zbar_{d(j)}) + w' (x_{d(i)} - x_{d(j)}) + g (a_{q,d(i)} - a_{q,d(j)}) + (b_{d(i)} - b_{d(j)}) )

- kappa: a free scale, because the scale is not portable (section 2.4).
- zbar_d: site means of the page features (the Mundlak device). Mundlak (1978), MEDIUM via the abstract: once the dependence between effects and regressors is modelled, random and fixed effects give "the same estimate for the slopes, the 'within' estimate".
- x_d: outside site features (Tranco, Common Crawl harmonic and PageRank, crawl size, internal link structure, domain age).
- a_{q,d}: affinity flags, for example the domain's brand in the search.
- b_d ~ N(0, tau^2): domain effects, fitted with a ridge penalty b^2 / (2 tau^2).

Identification: domain effects sit on one common scale only if the graph of domains linked by shared lists is connected. This is the usual Bradley-Terry condition; see Hunter 2004. A target site that shares no list with its rivals has no estimable domain effect beyond its features.

### 3.2 Empirical Bayes formulas

Prior: s_d ~ N(w' x_d, tau^2). The domain's own evidence r_d has sampling variance v_d. Posterior mean and variance:

    s_hat_d = B_d * w' x_d + (1 - B_d) * r_d,     B_d = v_d / (v_d + tau^2)
    Var(s_d | data) = v_d * tau^2 / (v_d + tau^2)    (plus the uncertainty in w)

This is Morris 1983, eq. 1.11 and 1.15 (STRONG: the PDF was read): theta_i* = (1 - B) Y_i + B z_i' beta, B = V/(V + A). Fay and Herriot 1979 is the area-level version (SNIPPET ONLY). In the pairwise logit, one Newton step from the fit without domain effects gives

    r_d = sum over pairs with d of ±(y - p_hat) / sum p_hat (1 - p_hat),    v_d = 1 / sum p_hat (1 - p_hat)
    tau^2 hat = max(0, weighted mean over d of (r_d^2 - v_d))

Morris's equal-variance estimate is B_hat = (k - r - 2) V / S, with S the residual sum of squares around the regression.

### 3.3 Simulation 3: two-step against joint fits

Setup: 600 domains with Zipf reuse; 100 training searches, 30 domains times 8 pages each; median 3 appearances per seen domain; about 250 site-restricted lists; 300 test searches. Content weights (1, 0.5, 0, 0). Feature 3 is a decoy that tracks site strength across sites and has no effect within a site. The outside site features correlate 0.5 with the site part. Labels are the true order, with no pull noise.

| Model | R = 1, all / both seen / any unseen | R = 3, all / both seen / any unseen | Content weights (R = 3) | Cosine to the true weights |
| --- | --- | --- | --- | --- |
| Oracle | 0.663 / 0.654 / 0.685 | 0.710 / 0.711 / 0.707 | 1, 0.5, 0, 0 | 1.000 |
| Content only, true weights | 0.556 / 0.565 / 0.534 | 0.527 / 0.531 / 0.515 | 1, 0.5, 0, 0 | 1.000 |
| Joint: content + site features | 0.591 / 0.587 / 0.600 | 0.604 / 0.602 / 0.609 | 1, 0.90, **1.06**, 0.02 | 0.735 to 0.757 |
| Joint + domain effects (ridge) | 0.602 / 0.602 / 0.601 | 0.661 / 0.679 / 0.603 | 1, 0.62, -0.03, -0.01 | 0.963 to 0.995 |
| Joint Mundlak + domain effects | 0.617 / 0.606 / 0.644 | 0.675 / 0.681 / 0.654 | 1, 0.59, -0.22, -0.01 | 0.980 to 0.982 |
| Two-step A: offset + site features + one-step EB | 0.572 / 0.571 / 0.575 | 0.619 / 0.638 / 0.557 | 1, 0.47, 0.09, 0.03 | 0.996 to 0.998 |
| **Two-step B: offset + site means + site features + ridge domain effects** | **0.617 / 0.608 / 0.640** | **0.677 / 0.683 / 0.657** | 1, 0.47, 0.09, 0.03 | **0.996 to 0.998** |

Lessons:

- Fitted across sites without domain effects, the decoy gets the same weight as real content. That is wrong for edits.
- Two-step B ties the best cross-site accuracy and keeps the cleanest content weights.
- The one-step EB approximation (A) did worse than a full ridge refit. Use a full penalized fit (or a mixed model). Use the EB formulas to read the result and to set tau^2.
- Unseen domains depend on site features. Here the site means of page features carried the signal by design. In real data that must be measured.

## 4. Position error from pair accuracy

What it means: placement quality is not a monotone function of target-against-rival pair accuracy. Shrinkage is required. The middle guess must be a baseline in every gate.

### 4.1 Exact relations (DERIVED)

- **One target placed by a scalar estimate against rivals whose utilities are known.** Every misordered target-rival pair lies on the same side, so |position error| = number of misordered pairs, exactly. Mean absolute error = (n - 1)(1 - a_target).
- **The middle guess** g = (n + 1)/2 rounded has target-rival accuracy 1 - E|P - g|/(n - 1): 0.722 for n = 10, 0.741 for n = 30, 0.745 for n = 50. So target-rival pair accuracy is not a skill measure.
- **Independent pair calls** with constant accuracy a, where the win count W gives the position 1 + W: E[1 + W | P] = 1 + (n - 1)(1 - a) + (P - 1)(2a - 1). The slope 2a - 1 < 1 pulls every estimate toward the middle. A page truly at 1 is placed at 3.70 on average (n = 10, a = 0.7), at 9.78 (n = 30), and at 15.89 (n = 50). Linear debiasing: P_hat = 1 + (W - (n - 1)(1 - a)) / (2a - 1), clipped. Better still: the posterior median of P given W, with P(W | P) = Binomial(P - 1, a) convolved with Binomial(n - P, 1 - a).
- **Shrunk scalar placement.** If m = u + eta with Var(u) = 1 and Var(eta) = sigma^2, then E[u | m] = m / (1 + sigma^2). Position is monotone in u, so placing E[u | m] among the rivals gives the **posterior median position**, which minimizes expected absolute error. In practice: regress the rivals' latent utilities (from their positions) on their model scores in the same results page, and place the target at the fitted value. That calibrates and shrinks in one step.
- **Diaconis and Graham** (1977; as Proposition 1 of Dwork et al. 2001, STRONG): for full lists, K <= F <= 2K, where K is the number of discordant pairs and F the footrule. So the mean absolute displacement of a fully ordered list lies between (1 - a)(n - 1)/2 and (1 - a)(n - 1). This held in every simulated trial.

### 4.2 Simulation 4 table

20,000 trials per cell. True positions are uniform on 1 to n. The rivals' utilities are known. "a" is the pair accuracy on pairs where both pages are scored, which is the number an evaluation reports: sigma^2 = 1/sin^2(pi(a - 1/2)) - 1. The order is the true order; against a single pull, everything is worse.

| n | a (scored pairs) | Target-rival accuracy | Unshrunk: MAE, within 1, 2, 3 | Shrunk (posterior median): MAE, within 1, 2, 3 | Full list: MAE per page (bound K/n to 2K/n) |
| --- | --- | --- | --- | --- | --- |
| 10 | middle guess | 0.722 | 2.50, 0.300, 0.500, 0.700 | | |
| 10 | 0.55 | 0.572 | 3.85, 0.269, 0.381, 0.493 | 2.22, 0.407, 0.595, 0.758 | 3.03 (2.03 to 4.06) |
| 10 | 0.60 | 0.638 | 3.26, 0.329, 0.458, 0.580 | 2.08, 0.441, 0.635, 0.790 | 2.73 (1.79 to 3.58) |
| 10 | 0.65 | 0.700 | 2.70, 0.399, 0.545, 0.667 | 1.96, 0.476, 0.664, 0.807 | 2.44 |
| 10 | 0.70 | 0.756 | 2.19, 0.471, 0.629, 0.751 | 1.74, 0.532, 0.717, 0.845 | 2.14 (1.35 to 2.70) |
| 10 | 0.75 | 0.804 | 1.77, 0.550, 0.711, 0.822 | 1.51, 0.589, 0.770, 0.878 | 1.81 |
| 10 | 0.80 | 0.848 | 1.37, 0.636, 0.798, 0.891 | 1.22, 0.671, 0.827, 0.919 | 1.49 |
| 10 | 0.90 | 0.928 | 0.65, 0.843, 0.945, 0.984 | 0.63, 0.847, 0.949, 0.985 | 0.80 |
| 30 | middle guess | 0.741 | 7.50, 0.100, 0.167, 0.233 | | |
| 30 | 0.60 | 0.639 | 10.47, 0.128, 0.185, 0.239 | 6.75, 0.147, 0.224, 0.298 | 8.22 |
| 30 | 0.65 | 0.698 | 8.75, 0.167, 0.235, 0.295 | 6.26, 0.173, 0.258, 0.341 | 7.30 |
| 30 | 0.70 | 0.752 | 7.18, 0.203, 0.283, 0.355 | 5.63, 0.202, 0.301, 0.393 | 6.35 |
| 30 | 0.80 | 0.849 | 4.38, 0.310, 0.423, 0.523 | 3.95, 0.311, 0.437, 0.543 | 4.35 |
| 30 | 0.90 | 0.928 | 2.09, 0.520, 0.670, 0.778 | 2.03, 0.525, 0.680, 0.788 | 2.29 |
| 50 | middle guess | 0.745 | 12.50, 0.060, 0.100, 0.140 | | |
| 50 | 0.60 | 0.635 | 17.88, 0.080, 0.117, 0.151 | 11.61, 0.087, 0.129, 0.173 | 13.70 |
| 50 | 0.65 | 0.702 | 14.62, 0.104, 0.149, 0.193 | 10.57, 0.104, 0.157, 0.207 | 12.10 |
| 50 | 0.70 | 0.752 | 12.13, 0.131, 0.187, 0.237 | 9.57, 0.120, 0.182, 0.241 | 10.50 |
| 50 | 0.80 | 0.849 | 7.41, 0.208, 0.293, 0.367 | 6.69, 0.202, 0.294, 0.378 | 7.18 |
| 50 | 0.90 | 0.928 | 3.53, 0.372, 0.505, 0.608 | 3.43, 0.378, 0.511, 0.618 | 3.73 |

Independent pair calls (win counts), MAE and within 3:

| n | a | Raw wins | Linear debiased | Posterior median |
| --- | --- | --- | --- | --- |
| 10 | 0.60 | 2.22, 0.790 | 3.00, 0.627 | 2.22, 0.794 |
| 10 | 0.70 | 1.76, 0.896 | 1.96, 0.827 | 1.73, 0.869 |
| 10 | 0.80 | 1.28, 0.966 | 1.34, 0.937 | 1.24, 0.951 |
| 30 | 0.70 | 4.84, 0.390 | 4.16, 0.513 | 3.83, 0.553 |
| 30 | 0.80 | 3.34, 0.566 | 2.60, 0.712 | 2.48, 0.739 |
| 50 | 0.70 | 7.83, 0.237 | 5.68, 0.394 | 5.33, 0.415 |
| 50 | 0.80 | 5.40, 0.347 | 3.50, 0.582 | 3.38, 0.605 |

Where raw wins go wrong: for small n with weak calls, the pull toward the middle happens to help within-k, so raw wins and the posterior median tie, and linear debiasing hurts. For n >= 30, raw wins are badly biased, and debiasing or the posterior median gains 0.12 to 0.25 in within-3.

Caveat: real targets are often outside the shown list. A product placement needs a "below n" outcome. The top-k Plackett-Luce term of section 2.4 provides it.

## 5. Anchored prediction for an edit

What it means: for a page that already ranks, the anchor absorbs its site strength, clicks and links. Only the content coefficient on the ordinary-results scale is needed, and it is worth roughly 2 to 3 positions per within-site standard deviation at mid-page before any transfer discount. Pull noise is mostly random re-placement, so a single pull is a poor anchor.

### 5.1 Formulas

    u_hat_1 = u_hat(r0) + kappa * beta' (c1 - c0)
    predicted position = 1 + #{rivals r : u_hat_r > u_hat_1}    (rivals anchored at their own positions)
    Var(error) = Var(anchor) + kappa^2 (c1 - c0)' Var(beta_hat) (c1 - c0) + (transfer error)^2

To put positions on a latent scale, use Thurstone case V: P(i above j in one pull) = Phi((u_i - u_j) / (sigma_p sqrt 2)), and two pulls agree on a pair with probability p^2 + (1 - p)^2. Thurstone 1927 is MEDIUM: read through Wikipedia, which gives x_ij = (S_i - S_j)/sigma with "discriminal dispersions ... uniform and uncorrelated"; the primary text returned HTTP 403. To fit per-search utilities from many pulls, fit a Thurstone or Plackett-Luce model to the pull orders of one day (MM: Hunter 2004).

### 5.2 Fitting the flip rates (SIMULATED, simulation 5)

- **Pure case V, equal spacing.** Adjacent agreement 0.839 gives p = 0.912 and spacing delta = 1.91 sigma_p per position. It predicts 1.000 agreement for pairs five or more places apart, against 0.932 measured. Rejected.
- **Jump mixture.** Each page in each pull lands at a uniform random place in the list with probability q, and otherwise gets Gaussian jitter. Fit to 0.839 and 0.932: delta = 3.8 sigma_p, q = 0.10. Fitted 0.839 and 0.929. It predicts 0.886 over all pairs, against 0.877 measured, a number not used in the fit. Single pull against the truth: 0.938, which matches the ceiling of about 0.93.
- **The scorer in the same units.** Fitted to the blend's 0.580, 0.688 and 0.832 by gap, its noise sd is about 3.7 positions of a one-site list. It reproduces 0.713 against one pull (measured 0.709) and 0.737 against the truth. Correlation with true utility inside the list: 0.62. So **one within-list sd of the content score is worth about 1.8 positions of expected movement inside a one-site list of 10.**
- With q = 0.10, the median of 3 pulls is a jump with probability 3q^2(1 - q) + q^3 = 0.028.

These two numbers do not pin down the shape. Fit the same model to agreement at every gap from 1 to 9, using the 20 twice-pulled training lists and the three-pulls-a-day ordinary data (no credits).

### 5.3 Moving to ordinary results-page positions (SIMULATED, simulation 5b)

A latent change of 0.62 within-site sd (1 sd of a content score whose population correlation is 0.62). Positions gained, by starting position:

| N candidates | R | From 5 | From 9 | From 15 (share landing in the top 10) |
| --- | --- | --- | --- | --- |
| 100 | 1 | +2.62 | +4.79 | +7.62 (0.94) |
| 100 | 3 | +2.23 | +3.88 | +6.00 (0.75) |
| 100 | 10 | +1.78 | +2.83 | +4.12 (0.40) |
| 1000 | 1 | +3.01 | +5.81 | +9.84 (1.00) |
| 1000 | 3 | +2.60 | +4.86 | +8.06 (0.96) |
| 1000 | 10 | +2.08 | +3.63 | +5.79 (0.71) |

For a one-site list: +2.5 to +3.2 from position 5. More site variance means fewer positions per unit of content. The same change moves a page further when it starts lower, because positions are denser there. These are cross-sectional upper figures.

### 5.4 The main threat: the slope between pages is not the effect of an edit

- Robinson 1950 (STRONG, relevant pages read): the correlation between race and illiteracy was .946 across areas and .203 across individuals. Correlations at two levels "need not" agree.
- Mundlak 1978 (MEDIUM): the slope from within units is the one that survives correlation between unit effects and regressors.
- Neuhaus and Kalbfleisch 1998 (SNIPPET ONLY): conditional likelihood estimates within-cluster effects, while mixed models estimate a weighted average of between and within effects.
- Bell and Jones 2015 (SNIPPET ONLY): fit within and between effects separately.

Here, between-page slopes mix the content effect with clicks, anchors and internal links that a site gives its main page for a topic. An edit changes the text at once, the rest slowly or never. The swap-pair gap (0.61 to 0.65 against 0.71) hints that a large share of the within-site signal is page traits that do not depend on the search. It is not an estimate: the swap pairs have a different difficulty mix.

The published split tests in `edit-effects.md` (75% of edits with no detectable effect; detected effects 0.1 to 0.6 positions) concern mostly smaller edits.

Plan: show cross-sectional deltas as PREDICTED upper figures, and fit a transfer factor on PR 45 and on Wayback cases run from another network.

## 6. Intervals, power and gate design

What it means: pick the interval method by simulated coverage on the test's own layout. Cross-site gates need a dyadic variance. Bars above 0.60 need true accuracy of at least 0.65 and 75 or more searches.

### 6.1 Variance structures

- **Pigeonhole bootstrap** (Owen 2007, STRONG, PDF read). True Var = (nu_A sigma_A^2 + nu_B sigma_B^2 + sigma_E^2)/N. The bootstrap gives about ((nu_A + 2) sigma_A^2 + (nu_B + 2) sigma_B^2 + 3 sigma_E^2)/N: "mildly conservative". The pair-level Bernoulli term is large here, so the 3 times overcount matters.
- **Two-way analytic** (Cameron, Gelbach and Miller 2011, STRONG, working paper read): V = V_A + V_B - V_{A∩B}.
- **Dyadic** (Aronow, Samii and Assenova 2015, MEDIUM: abstract read; the form below is my adaptation to a mean): with e = hit - mean,

      Var = (1/P^2) * sum_{i,j} e_i e_j * 1[i and j share a search or a domain].

### 6.2 Calibration to the training design (simulation 6a)

The synthetic design has 5,568 pairs, 87 sites, 19 groups (one holding 33% of pairs, effective groups 7.2) and 182 lists. Logit sds: site 0.3, group 0.1, list 0.4. That gives one-way half-widths of 0.0208, 0.0202 and 0.0186, against 0.020, 0.0195 and 0.018 measured. Two-way bootstrap half-width: 0.0366, against 0.0365 measured. Coverage over 300 replicates: two-way bootstrap 0.983, analytic two-way 0.907, site-only 0.917.

### 6.3 Cross-site layout (simulation 6c)

28 scorable pairs and 9 domains per search; a Zipf domain pool of 5 per search; acc 0.65; 200 replicates.

| Searches | Search weight × domain weight × domain weight | Search-only bootstrap | Dyadic analytic |
| --- | --- | --- | --- |
| 30 | coverage 1.000, half-width 0.095 | 0.930, 0.043 | 0.950, 0.048 |
| 75 | 1.000, 0.063 | 0.895, 0.029 | 0.955, 0.034 |

### 6.4 Power table (simulation 6d)

Share of 200 tests whose dyadic 95% lower bound clears the bar. Search-level logit sd 0.41 (calibrated) / 0.60 (more varied searches).

| True accuracy | Bar | 20 searches | 30 | 50 | 75 | 100 | 150 | 200 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.62 | 0.55 | 0.57 / 0.53 | 0.76 / 0.61 | 0.83 / 0.76 | 0.95 / 0.92 | 0.97 / 0.95 | 0.99 / 0.98 | 0.99 / 0.99 |
| 0.62 | 0.60 | 0.14 / 0.12 | 0.18 / 0.14 | 0.17 / 0.14 | 0.22 / 0.21 | 0.24 / 0.26 | 0.25 / 0.29 | 0.32 / 0.28 |
| 0.65 | 0.55 | 0.91 / 0.80 | 0.93 / 0.92 | 0.99 / 0.99 | 1.00 / 0.99 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| 0.65 | 0.60 | 0.46 / 0.34 | 0.53 / 0.41 | 0.71 / 0.54 | 0.80 / 0.71 | 0.86 / 0.79 | 0.91 / 0.91 | 0.96 / 0.92 |
| 0.70 | 0.55 | 0.99 / 0.97 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| 0.70 | 0.60 | 0.90 / 0.81 | 0.96 / 0.94 | 0.99 / 0.99 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |

"True accuracy" is against the label used. Inside a top 10 and against one pull, section 2.3 puts realistic values at 0.55 to 0.62. Paired differences against a baseline use the same dyadic variance on d = hit_model - hit_base.

### 6.5 Checklist for a gate no baseline can pass

1. Choose the primary metric and the rows before pulling anything: within 3 and MAE of placement against a consensus truth (median of at least 3 same-session pulls).
2. Run every baseline on the same rows: the middle guess; the domain prior (the site's shrunk mean position in other searches); persistence where defined (score new or changed rows separately); content only; site only; the current ranker.
3. The pass needs both an absolute bar and a paired difference against the best design-day baseline, with the lower bound above 0.
4. Baseline dominance check: run every baseline through the frozen gate on design data, and on labels shuffled within searches. If any baseline passes, change the gate before freezing.
5. Report pair accuracy over all pairs in the scored lists, never target-against-rival accuracy (the middle guess scores 0.72 to 0.75 on it). Give correct, wrong and ties separately.
6. Check the interval's coverage on the gate's own layout. Require 0.93 to 0.97.
7. Compute power at the predicted accuracy. If it is below 0.8, enlarge the test or lower the claim, before pulling.
8. Budget: 3 pulls for each of 75 searches is about 225 credits. Sealing searches now and scoring on future daily-collector pulls costs no extra credits.

## Appendix A. Code and seeds

Python 3 with numpy. Seeds: simulation 1: 20261120; 1b: 20261128; 2: 20261121; 3: 20261122; 4: 20261123; 5: 20261124; 5b: 20261125; 6a: 20261126; 6b to 6d: 20261127 (6c and 6d run the top of 6b, which sets the seed, so save 6b as `sim6b.py` first). The recount runs from the repo root and uses seed 20261120.

### recount.py

```python
# Side check: global authority signals inside ordinary top 10s, ties shown separately. Run from the repo root. Reads committed files only.
import csv, numpy as np
from urllib.parse import urlparse
off = {r['domain']: r for r in csv.DictReader(open('evals/preregistered/forward-pair-2026-11-16/offpage.tsv'), delimiter='\t')}
def sk(u):
    l = [p for p in urlparse(u).netloc.lower().split(':')[0].strip('.').split('.') if p]; return '.'.join(l[-2:])
def val(d, feat):
    x = off.get(d, {}).get(feat, '')
    return float(x) if x != '' else (0.0 if feat == 'tranco_popularity' else np.nan)   # same missing-value rules as snapshot_signals.py
rng = np.random.default_rng(20261120)
for fn in ('baseline_serp.csv', 'forward_serp.csv'):
    byq = {}
    for r in csv.DictReader(open('evals/forward-2026-11-09/' + fn)):
        if int(r['position']) <= 10: byq.setdefault(r['query'], []).append((int(r['position']), sk(r['link'])))
    for feat in ('tranco_popularity', 'cc_pagerank', 'cc_harmonic', 'majestic_log'):
        per = []
        for L in byq.values():
            c = w = t = 0
            for (pi, di) in L:
                for (pj, dj) in L:
                    if pi >= pj: continue
                    a, b = val(di, feat), val(dj, feat)
                    if di != dj and a > b: c += 1
                    elif di != dj and a < b: w += 1
                    else: t += 1                                   # same site, equal value or missing value
            per.append((c, w, t))
        per = np.array(per); C, W, T = per.sum(0)
        bs = [(lambda s: s[:, 0].sum() / s[:, :2].sum())(per[rng.integers(0, len(per), len(per))]) for _ in range(2000)]
        print("%s %-17s pairs %d correct %d wrong %d ties %d | ties in denominator %.3f | decided %.3f [%.3f, %.3f]" %
              (fn, feat, C + W + T, C, W, T, C / (C + W + T), C / (C + W), *np.percentile(bs, [2.5, 97.5])))
```

### sim1.py

```python
# Sim 1: range restriction and Berkson inside a top k. numpy only, synthetic.
import numpy as np
rng = np.random.default_rng(20261120)
def run(R, N, k, rho_f, Q=4000, per_site=5, conf=0.0):
    M = N // per_site
    s = rng.normal(0, np.sqrt(R), (Q, M)).repeat(per_site, 1)       # site effect, var R
    w = rng.normal(0, 1, (Q, N))                                       # within-site utility, var 1
    f = rho_f * w + np.sqrt(1 - rho_f**2) * rng.normal(0, 1, (Q, N))   # content feature, corr rho_f with w
    x = s / np.sqrt(R) + conf * w + rng.normal(0, 1, (Q, N))           # noisy site proxy (conf<0: confounded)
    site = np.arange(M).repeat(per_site)[None, :].repeat(Q, 0)
    u = w + s
    top = np.argsort(-u, 1)[:, :k]
    take = lambda a: np.take_along_axis(a, top, 1)
    U, F, S, X, W, SI = map(take, (u, f, s, x, w, site))
    i, j = np.triu_indices(k, 1)                                       # i is above j (sorted)
    same = SI[:, i] == SI[:, j]
    acc = lambda Z, m: np.mean((Z[:, i] > Z[:, j])[m])
    cr = lambda A, B: np.corrcoef(A.ravel(), B.ravel())[0, 1]
    return dict(R=R, N=N, k=k, rf=rho_f, n_same=int(same.sum()),
                f_same=acc(F, same) if same.sum() > 200 else np.nan, f_cross=acc(F, ~same),
                s_cross=acc(S, ~same), x_cross=acc(X, ~same), corr_ws=cr(W, S))
def implied(a_same, a_cross):
    rho = lambda a: np.sin(np.pi * (a - 0.5))
    return (rho(a_same) / rho(a_cross))**2 - 1, (a_same - .5) / (a_cross - .5) - 1
print("Unselected check: random pairs, R=3, rho_f=0.3 -> same-site acc should be 0.5+asin(.3)/pi=%.3f" % (0.5+np.arcsin(.3)/np.pi))
print("cross-site expected 0.5+asin(.3/2)/pi=%.3f" % (0.5+np.arcsin(.3/np.sqrt(1+3))/np.pi))
print("R    N     k  rho_f n_same f_same f_cross s_cross x_cross corr(w,s) impliedR_arcsin impliedR_linear")
for rf in (0.30, 0.63):
    for R in (0.33, 1, 3, 10):
        for N in (30, 100, 1000):
            r = run(R, N, 10, rf)
            ia, il = implied(r['f_same'], r['f_cross']) if r['f_same'] == r['f_same'] else (np.nan, np.nan)
            print("%-4s %-5d %-2d %.2f %6d %.3f  %.3f   %.3f   %.3f   %+.3f    %7.1f %7.1f" % (R, N, 10, rf, r['n_same'], r['f_same'], r['f_cross'], r['s_cross'], r['x_cross'], r['corr_ws'], ia, il))
print("Deeper lists, R=3, rho_f=0.63, N=1000: k=10,30,100")
for k in (10, 30, 100):
    r = run(3, 1000, k, 0.63, Q=1500)
    print(k, "f_cross %.3f s_cross %.3f corr(w,s) %+.3f" % (r['f_cross'], r['s_cross'], r['corr_ws']))
print("Confounded proxy: x = s/sd + conf*w + noise, R=1, N=100, k=10")
for conf in (0.0, -0.5, -1.0, -2.0):
    r = run(1, 100, 10, 0.63, conf=conf)
    print("conf %+.1f x_cross %.3f" % (conf, r['x_cross']))
```

### sim1b.py

```python
# Sim 1b: cross-site pair accuracy inside a top 10 for content (corr 0.63 with within-site utility) plus a site estimate of quality r_s.
import numpy as np
rng = np.random.default_rng(20261128)
def run(R, N, r_s, rho_f=0.63, k=10, Q=3000, per_site=5):
    M = N // per_site; sd = np.sqrt(R)
    zs = rng.normal(0, 1, (Q, M)); s = (sd * zs).repeat(per_site, 1)
    shat = (r_s * zs + np.sqrt(1 - r_s ** 2) * rng.normal(0, 1, (Q, M))).repeat(per_site, 1)   # site estimate, corr r_s with s
    w = rng.normal(0, 1, (Q, N)); f = rho_f * w + np.sqrt(1 - rho_f ** 2) * rng.normal(0, 1, (Q, N))
    site = np.arange(M).repeat(per_site)[None, :].repeat(Q, 0)
    u = w + s; score = rho_f * f + r_s * sd * shat                      # population-optimal linear weights
    top = np.argsort(-u, 1)[:, :k]; t = lambda a: np.take_along_axis(a, top, 1)
    S, SI = t(score), t(site); i, j = np.triu_indices(k, 1); cross = SI[:, i] != SI[:, j]
    return np.mean((S[:, i] > S[:, j])[cross])
print("cross-site pair accuracy inside the top 10 against the true order (no pull noise)")
print("R   N    | r_s=0.0  0.3   0.5   0.7   0.9   1.0")
for R in (1, 3, 10):
    for N in (100, 1000):
        print("%-3s %-5d| " % (R, N) + "  ".join("%.3f" % run(R, N, r) for r in (0.0, 0.3, 0.5, 0.7, 0.9, 1.0)))
```

### sim2.py

```python
# Sim 2: pairwise logit on pair differences fitted only on selected lists. numpy only, synthetic.
import numpy as np
rng = np.random.default_rng(20261121)
def logit_fit(X, y, lam=1e-3, it=30):
    b = np.zeros(X.shape[1])
    for _ in range(it):
        p = 1 / (1 + np.exp(-X @ b)); W = p * (1 - p)
        H = X.T @ (X * W[:, None]) + lam * np.eye(len(b)); b += np.linalg.solve(H, X.T @ (y - p) - lam * b)
    return b
def world(Q, N=200, per_site=5, beta=1.0, gam=1.0, sz=1.0, heavy=False):
    M = N // per_site
    xs = rng.standard_t(3, (Q, M)) / np.sqrt(3) if heavy else rng.normal(0, 1, (Q, M))
    x = xs.repeat(per_site, 1)                                   # observed site feature
    zs = rng.normal(0, sz, (Q, M)).repeat(per_site, 1)           # unobserved site part
    c = rng.normal(0, 1, (Q, N))                                 # observed content score
    u = beta * c + gam * x + zs + rng.normal(0, 1.3, (Q, N))     # unobserved page part sd 1.3
    order = np.argsort(-u, 1)
    t = lambda a: np.take_along_axis(a, order, 1)
    return t(c), t(x), t(u)
def pairs(C, X, k_hi, k_lo, n_lose=0):
    I, J = np.triu_indices(k_lo, 1); keep = I < k_hi              # i above j, i within top k_hi
    I, J = I[keep], J[keep]
    if n_lose:                                                    # add sampled non-ranked losers vs top k_hi
        L = rng.integers(30, C.shape[1], n_lose)
        I = np.concatenate([I, np.repeat(np.arange(k_hi), n_lose)]); J = np.concatenate([J, np.tile(L, k_hi)])
    D = np.stack([C[:, I] - C[:, J], X[:, I] - X[:, J]], -1).reshape(-1, 2)
    flip = rng.random(len(D)) < 0.5; D[flip] *= -1                # random orientation
    return D, (~flip).astype(float)
def acc_top10(b, C, X, U):
    m = b[0] * C[:, :10] + b[1] * X[:, :10]; I, J = np.triu_indices(10, 1)
    return np.mean(m[:, I] > m[:, J])
Ct, Xt, Ut = world(3000)                                          # held-out evaluation world
designs = {"top10 pairs only": (10, 10, 0), "top30 deep list": (30, 30, 0),
           "top10 + 20 non-ranked losers": (10, 10, 20), "top10 vs 11-30 + top10 pairs": (10, 30, 0)}
print("truth ratio beta/gamma = 1.00; oracle held-out top-10 acc %.3f" % acc_top10(np.array([1., 1.]), Ct, Xt, Ut))
for heavy in (False, True):
    for name, (kh, kl, nl) in designs.items():
        R, A, B = [], [], []
        for rep in range(40):
            C, X, U = world(100, heavy=heavy)
            D, y = pairs(C, X, kh, kl, nl); b = logit_fit(D, y)
            R.append(b[0] / b[1]); B.append(b); A.append(acc_top10(b, Ct, Xt, Ut))
        R = np.array(R); B = np.array(B)
        print("%-30s heavy=%-5s ratio mean %.2f sd %.2f | beta_hat %.2f gamma_hat %.2f | held-out top10 acc %.3f" %
              (name, heavy, R.mean(), R.std(), B[:, 0].mean(), B[:, 1].mean(), np.mean(A)))
# no selection reference: random pairs from all N candidates
C, X, U = world(100); I, J = rng.integers(0, 200, (2, 4500)); same = I != J
D = np.stack([C[:, I] - C[:, J], X[:, I] - X[:, J]], -1).reshape(-1, 2); y = (U[:, I] > U[:, J]).ravel().astype(float)
b = logit_fit(D, y); print("no selection, random pairs: ratio %.2f beta_hat %.2f gamma_hat %.2f" % (b[0] / b[1], b[0], b[1]))
```

### sim3.py

```python
# Sim 3: two-step (within-site content, then site effects with offset + EB) vs joint fit. numpy only, synthetic.
import numpy as np
rng = np.random.default_rng(20261122)
def fit(X, y, lam, it=25):
    b = np.zeros(X.shape[1])
    for _ in range(it):
        p = 1 / (1 + np.exp(-X @ b)); H = X.T @ (X * (p * (1 - p))[:, None]) + np.diag(lam)
        b += np.linalg.solve(H, X.T @ (y - p) - lam * b)
    return b
D, Pg, M, BETA = 600, 8, 30, np.array([1.0, 0.5, 0.0, 0.0])   # true content weights; f3 is a site-correlated decoy
pop = 1 / np.arange(1, D + 1) ** 0.9; pop /= pop.sum()
def make_world(R):
    xd = rng.normal(0, 1, (D, 2)); sd = np.sqrt(R * 2.25)       # within-site var of beta.c + e is 1.25 + 1.0
    s = sd * (0.5 * (xd @ [0.8, 0.6]) + np.sqrt(0.75) * rng.normal(0, 1, D))   # corr(w.x, s) = 0.5
    return xd, s, sd
def serps(Q, xd, s, sd):
    rows = []
    for q in range(Q):
        doms = rng.choice(D, M, replace=False, p=pop).repeat(Pg)
        F = rng.normal(0, 1, (M * Pg, 4)); F[:, 2] = 0.7 * s[doms] / sd + 0.7 * F[:, 2]
        Fm = F.reshape(M, Pg, 4).mean(1).repeat(Pg, 0)           # Mundlak: site mean of each page feature
        rows.append((doms, np.hstack([F, Fm]), F @ BETA + s[doms] + rng.normal(0, 1, M * Pg)))
    return rows
def pair_data(rows, same_site_lists=0):
    Xc, dd = [], []
    for doms, F, u in rows:
        if same_site_lists:                                      # site-restricted lists: all pages of a few domains
            for d in rng.choice(np.unique(doms), same_site_lists, replace=False):
                idx = np.where(doms == d)[0]; idx = idx[np.argsort(-u[idx])]; I, J = np.triu_indices(len(idx), 1)
                Xc.append(F[idx[I]] - F[idx[J]]); dd.append(np.full((len(I), 2), d))
            continue
        idx = np.argsort(-u)[:10]; I, J = np.triu_indices(10, 1); k = doms[idx[I]] != doms[idx[J]]
        Xc.append(F[idx[I[k]]] - F[idx[J[k]]]); dd.append(np.stack([doms[idx[I[k]]], doms[idx[J[k]]]], 1))
    Xc, dd = np.concatenate(Xc), np.concatenate(dd); y = np.ones(len(Xc))
    fl = rng.random(len(y)) < 0.5; Xc[fl] *= -1; dd[fl] = dd[fl][:, ::-1]; y[fl] = 0
    return Xc, dd, y
def evaluate(score_fn, rows):
    hit, sm = [], []
    for doms, F, u in rows:
        idx = np.argsort(-u)[:10]; m = score_fn(F[idx], doms[idx]); I, J = np.triu_indices(10, 1); k = doms[idx[I]] != doms[idx[J]]
        hit.append((m[I] > m[J])[k]); sm.append((seen[doms[idx[I]]] & seen[doms[idx[J]]])[k])
    h, sm = np.concatenate(hit), np.concatenate(sm); return h.mean(), h[sm].mean(), h[~sm].mean()
cos = lambda b: b[:4] @ BETA / np.linalg.norm(b[:4]) / np.linalg.norm(BETA)
for R in (1.0, 3.0):
    xd, s, sd = make_world(R); train, test = serps(100, xd, s, sd), serps(300, xd, s, sd)
    Xc, dd, y = pair_data(train); seen = np.zeros(D, bool); seen[np.unique(dd)] = True
    apps = np.bincount(np.concatenate([np.unique(r[0]) for r in train]), minlength=D)
    Xw, _, yw = pair_data(train[:31], same_site_lists=8)       # about 250 site-restricted lists
    Xx = xd[dd[:, 0]] - xd[dd[:, 1]]; E = np.zeros((len(y), D)); E[np.arange(len(y)), dd[:, 0]] += 1; E[np.arange(len(y)), dd[:, 1]] -= 1
    small, rid = lambda n: np.full(n, 1e-3), np.full(D, 2.0)
    res = {"oracle": (lambda F, d: F[:, :4] @ BETA + s[d], BETA), "content only, true weights": (lambda F, d: F[:, :4] @ BETA, BETA)}
    b = fit(np.hstack([Xc[:, :4], Xx]), y, small(6)); res["joint: content + site feats"] = (lambda F, d, b=b: F[:, :4] @ b[:4] + xd[d] @ b[4:], b)
    b = fit(np.hstack([Xc[:, :4], Xx, E]), y, np.r_[small(6), rid]); res["joint: + domain effects (ridge)"] = (lambda F, d, b=b: F[:, :4] @ b[:4] + xd[d] @ b[4:6] + b[6:][d], b)
    b = fit(np.hstack([Xc, Xx, E]), y, np.r_[small(10), rid]); res["joint Mundlak: + site means + domain eff."] = (lambda F, d, b=b: F @ b[:8] + xd[d] @ b[8:10] + b[10:][d], b)
    bw = fit(Xw[:, :4], yw, small(4)); O = (Xc[:, :4] @ bw)[:, None]
    b2 = fit(np.hstack([O, Xx]), y, small(3)); p = 1 / (1 + np.exp(-np.hstack([O, Xx]) @ b2))
    info = ((p * (1 - p))[:, None] * E ** 2).sum(0); r = ((y - p)[:, None] * E).sum(0) / np.maximum(info, 1e-9); v = 1 / np.maximum(info, 1e-9); ok = info > 0
    tau2 = max(1e-3, np.average(r[ok] ** 2 - v[ok], weights=1 / (1 + v[ok]))); bd = np.where(ok, tau2 / (tau2 + v) * r, 0.0)
    res["two-step A: offset + site feats + one-step EB"] = (lambda F, d, b=b2, bw=bw, bd=bd: b[0] * (F[:, :4] @ bw) + xd[d] @ b[1:] + bd[d], bw)
    b3 = fit(np.hstack([O, Xc[:, 4:], Xx, E]), y, np.r_[small(7), rid])
    res["two-step B: offset + site means + feats + ridge"] = (lambda F, d, b=b3, bw=bw: b[0] * (F[:, :4] @ bw) + F[:, 4:] @ b[1:5] + xd[d] @ b[5:7] + b[7:][d], bw)
    print("R=%.0f: domains seen %d, appearances of seen domains median %.0f, EB tau2 %.2f" % (R, seen.sum(), np.median(apps[apps > 0]), tau2))
    for name, (fn, bc) in res.items():
        a = evaluate(fn, test); print("   %-48s acc all %.3f | both seen %.3f | any unseen %.3f | content weights %s cos %.3f" % (name, *a, np.round(bc[:4] / bc[0], 2), cos(bc)))
```

### sim4.py

```python
# Sim 4: position error of a placed target as a function of pair accuracy. numpy only, synthetic.
import numpy as np
from math import comb
rng = np.random.default_rng(20261123)
T = 20000
def metrics(pred, true):
    e = np.abs(pred - true); return e.mean(), (e <= 1).mean(), (e <= 2).mean(), (e <= 3).mean()
def scalar_case(n, a_eval):
    sig2 = 1 / np.sin(np.pi * (a_eval - 0.5)) ** 2 - 1            # model noise var so that eval pair acc = a_eval
    u = rng.normal(0, 1, (T, n)); ut, ur = u[:, 0], u[:, 1:]
    true = 1 + (ur > ut[:, None]).sum(1)
    m = ut + rng.normal(0, np.sqrt(sig2), T)
    a_t = np.mean((m[:, None] > ur) == (ut[:, None] > ur))          # target-vs-known-rival accuracy (naive)
    naive = 1 + (ur > m[:, None]).sum(1)
    shrunk = 1 + (ur > (m / (1 + sig2))[:, None]).sum(1)              # posterior median of u_t -> posterior median position
    full = u + rng.normal(0, np.sqrt(sig2), (T, n))                   # full-list ordering for Diaconis-Graham
    rk_true, rk_pred = u.argsort(1).argsort(1), full.argsort(1).argsort(1)
    I, J = np.triu_indices(n, 1); K = ((u[:, I] > u[:, J]) != (full[:, I] > full[:, J])).sum(1)
    Dft = np.abs(rk_true - rk_pred).sum(1)
    return a_t, metrics(naive, true), metrics(shrunk, true), (Dft / n).mean(), (K / n).mean(), np.mean((K <= Dft) & (Dft <= 2 * K))
def wins_case(n, a):                                                  # independent pair errors at constant accuracy a
    true = rng.integers(1, n + 1, T)
    W = rng.binomial(true - 1, a) + rng.binomial(n - true, 1 - a)
    naive = 1 + W
    debiased = np.clip(np.rint(1 + (W - (n - 1) * (1 - a)) / (2 * a - 1)), 1, n)
    L = np.zeros((n, n))                                              # L[k-1, w] = P(W=w | true=k)
    for k in range(1, n + 1):
        p1 = [comb(k - 1, i) * a ** i * (1 - a) ** (k - 1 - i) for i in range(k)]
        p2 = [comb(n - k, i) * (1 - a) ** i * a ** (n - k - i) for i in range(n - k + 1)]
        L[k - 1] = np.convolve(p1, p2)
    post = L / L.sum(0); med = 1 + (post.cumsum(0) < 0.5).sum(0)      # posterior median under a uniform prior
    return metrics(naive, true), metrics(debiased, true), metrics(med[W], true), np.mean(naive[true == 1])
for n in (10, 30, 50):
    mid = (n + 1) // 2; tr = np.arange(1, n + 1); e = np.abs(mid - tr)
    print("\nn=%d  middle guess %d: MAE %.2f within1 %.3f within2 %.3f within3 %.3f" % (n, mid, e.mean(), (e <= 1).mean(), (e <= 2).mean(), (e <= 3).mean()))
    print(" a_eval a_target | naive scalar: MAE w1 w2 w3 | shrunk scalar (posterior median): MAE w1 w2 w3 | full-list MAE, K/n, DG bound holds")
    for a in (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.90):
        at, nv, sh, dfn, kn, ok = scalar_case(n, a)
        print(" %.2f   %.3f   | %5.2f %.3f %.3f %.3f | %5.2f %.3f %.3f %.3f | %5.2f %5.2f %.3f" % (a, at, *nv, *sh, dfn, kn, ok))
    print(" independent pair errors, accuracy a | naive wins: MAE w1 w2 w3 | linear debiased | posterior median | mean naive pred when true=1")
    for a in (0.60, 0.70, 0.80, 0.90):
        nv, db, pm, top = wins_case(n, a)
        print(" %.2f | %5.2f %.3f %.3f %.3f | %5.2f %.3f %.3f %.3f | %5.2f %.3f %.3f %.3f | %.2f" % (a, *nv, *db, *pm, top))
```

### sim5.py

```python
# Sim 5: Thurstone case V fit of pull-to-pull agreement with a jump mixture; model noise in position units. numpy only.
import numpy as np
rng = np.random.default_rng(20261124)
n, T = 10, 20000
I, J = np.triu_indices(n, 1)
def pulls(delta, q, Jsd=8.0):
    u = -delta * np.arange(n)[None, :].repeat(T, 0)                    # equally spaced true utilities, sd of pull noise = 1
    def one():
        e = rng.normal(0, 1, (T, n)); jump = rng.random((T, n)) < q
        return np.where(jump, rng.uniform(-delta * (n - 1) - 1, 1, (T, n)), u + e)   # a jumping page lands at a uniform random place in the list
    return u, one(), one()
def by_gap(score, label):                                               # agreement of score with label order, by label gap
    rank = (-label).argsort(1).argsort(1)                               # observed position in the label pull
    gap = np.abs(rank[:, I] - rank[:, J]); agree = (score[:, I] > score[:, J]) == (label[:, I] > label[:, J])
    return agree[gap == 1].mean(), agree[(gap >= 2) & (gap <= 4)].mean(), agree[gap >= 5].mean(), agree.mean()
print("Pure Gaussian Thurstone (q=0): delta -> adjacent, gap>=5, all")
for d in (1.5, 1.9, 2.2):
    u, p1, p2 = pulls(d, 0.0); a = by_gap(p2, p1); print("  delta %.1f: adj %.3f gap2-4 %.3f gap>=5 %.3f all %.3f" % (d, a[0], a[1], a[2], a[3]))
best = None
for d in np.arange(1.0, 6.01, 0.25):
    for q in np.arange(0.0, 0.301, 0.01):
        u, p1, p2 = pulls(d, q); a = by_gap(p2, p1)
        loss = (a[0] - 0.839) ** 2 + (a[2] - 0.932) ** 2
        if best is None or loss < best[0]: best = (loss, d, q, a)
_, d, q, a = best
print("Jump-mixture fit to adjacent 0.839 and gap>=5 0.932: delta %.1f sd-of-pull-noise per position, jump prob per page per pull %.3f -> adj %.3f gap2-4 %.3f gap>=5 %.3f all %.3f (measured all 0.877)" % (d, q, *a))
u, p1, p2 = pulls(d, q)
print("label (one pull) vs truth: all pairs %.3f" % np.mean((p1[:, I] > p1[:, J]) == (u[:, I] > u[:, J])))
target = np.array([0.580, 0.688, 0.832])
best = None
for s in np.arange(0.5, 30.01, 0.5):                                    # model noise sd in pull-noise units
    m = u + rng.normal(0, s, (T, n)); a = np.array(by_gap(m, p1)[:3])
    loss = ((a - target) ** 2).sum()
    if best is None or loss < best[0]: best = (loss, s, a)
_, s, a = best
m = u + rng.normal(0, s, (T, n)); rho = np.mean([np.corrcoef(m[t], u[t])[0, 1] for t in range(2000)])
acc_truth = np.mean((m[:, I] > m[:, J]) == (u[:, I] > u[:, J]))
print("Model noise fit to 0.580/0.688/0.832 vs one pull: sd %.2f pull units = %.2f positions -> fitted %.3f %.3f %.3f" % (s, s / d, *a))
print("  model vs truth, all pairs: %.3f ; vs one pull all pairs: %.3f (measured 0.709)" % (acc_truth, by_gap(m, p1)[3]))
print("  within-list corr(model, true utility) %.3f ; 1 within-list sd of model score moves expected utility by %.2f positions" %
      (rho, rho * np.std(np.arange(n))))
```

### sim5b.py

```python
# Sim 5b: positions gained in a SERP top 10 (and in a one-site list) for a content edit worth rho within-site sd. numpy only.
import numpy as np
rng = np.random.default_rng(20261125)
T, rho = 20000, 0.62                       # rho: corr(model score, within-site utility) from the fit in sim 5
def gain(N, R, r0, per_site=5):
    M = N // per_site
    u = rng.normal(0, 1, (T, N)) + np.sqrt(R) * rng.normal(0, 1, (T, M)).repeat(per_site, 1)
    srt = -np.sort(-u, 1)
    target = srt[:, r0 - 1]; rivals = np.delete(srt, r0 - 1, 1)
    new = target + rho * 1.0                # +1 within-site sd of model score -> +rho within-site sd of expected utility
    newpos = 1 + (rivals > new[:, None]).sum(1)
    return np.mean(r0 - newpos), np.mean(np.minimum(newpos, 11) <= 10)
print("One-site list (R=0): top 10 of N pages, target at r0")
for N in (20, 50, 200):
    print("  N=%4d  r0=5: +%.2f positions | r0=9: +%.2f" % (N, gain(N, 0, 5, per_site=N)[0], gain(N, 0, 9, per_site=N)[0]))
print("Ordinary SERP: N candidates, 5 pages per site, between/within variance ratio R, target at r0 (positive = moves up)")
for N in (100, 1000):
    for R in (1, 3, 10):
        g5, _ = gain(N, R, 5); g9, _ = gain(N, R, 9); g15, in10 = gain(N, R, 15)
        print("  N=%4d R=%2d  r0=5: +%.2f | r0=9: +%.2f | r0=15: +%.2f (lands in top 10: %.2f)" % (N, R, g5, g9, g15, in10))
```

### sim6a.py

```python
# Sim 6a: calibrate site/group/list variance components to the training half-widths; coverage of the product-weight bootstrap.
import numpy as np
rng = np.random.default_rng(20261126)
def design():
    g_of_list = np.r_[np.zeros(60, int), rng.integers(1, 19, 122)]         # one group holds about a third of the lists
    s_of_list = np.r_[rng.permutation(87), rng.integers(0, 87, 95)]          # every site has at least one list
    npairs = np.clip(rng.poisson(31, 182), 1, 45); npairs = np.round(npairs * 5623 / npairs.sum()).astype(int)
    lst = np.repeat(np.arange(182), npairs)
    return lst, s_of_list[lst], g_of_list[lst]
def outcomes(lst, site, grp, sa, sg, sl, acc=0.709):
    eta = np.log(acc / (1 - acc)) + sa * rng.normal(0, 1, 87)[site] + sg * rng.normal(0, 1, 19)[grp] + sl * rng.normal(0, 1, 182)[lst]
    return (rng.random(len(lst)) < 1 / (1 + np.exp(-eta))).astype(float)
def cr_hw(y, cl):                                                           # one-way cluster-robust half-width
    r = np.bincount(cl, y - y.mean()); return 1.96 * np.sqrt((r ** 2).sum()) / len(y)
def pigeon(y, site, grp, B=400):                                            # product-weight two-way bootstrap
    Ws = rng.multinomial(87, np.full(87, 1 / 87), B); Wg = rng.multinomial(19, np.full(19, 1 / 19), B)
    w = Ws[:, site] * Wg[:, grp]; st = (w * y).sum(1) / w.sum(1); return np.percentile(st, [2.5, 97.5])
def cgm_hw(y, lst, site, grp):                                              # Cameron-Gelbach-Miller two-way: V_s + V_g - V_list
    v = lambda cl: (np.bincount(cl, y - y.mean()) ** 2).sum() / len(y) ** 2
    return 1.96 * np.sqrt(max(v(site) + v(grp) - v(lst), 1e-12))
lst, site, grp = design()
print("design: pairs %d, group share max %.2f, effective groups %.1f, effective sites %.1f" % (len(lst), np.bincount(grp).max() / len(grp),
      1 / ((np.bincount(grp) / len(grp)) ** 2).sum(), 1 / ((np.bincount(site) / len(site)) ** 2).sum()))
best = None
for sa in (0.0, 0.2, 0.3, 0.4):
    for sg in (0.0, 0.1, 0.2, 0.3):
        for sl in (0.0, 0.2, 0.3, 0.4):
            h = np.mean([[cr_hw(y, site), cr_hw(y, grp), cr_hw(y, lst)] for y in (outcomes(lst, site, grp, sa, sg, sl) for _ in range(20))], 0)
            loss = ((h - [0.020, 0.0195, 0.018]) ** 2).sum()
            if best is None or loss < best[0]: best = (loss, sa, sg, sl, h)
_, sa, sg, sl, h = best
print("calibrated logit sds: site %.1f group %.1f list %.1f -> one-way half-widths site %.4f group %.4f list %.4f (targets 0.020 0.0195 0.018)" % (sa, sg, sl, *h))
eta_pop = np.log(0.709 / 0.291) + sa * rng.normal(0, 1, 10 ** 6) + sg * rng.normal(0, 1, 10 ** 6) + sl * rng.normal(0, 1, 10 ** 6)
truth = np.mean(1 / (1 + np.exp(-eta_pop)))
cov_p, cov_c, cov_s, hw_p, hw_c = 0, 0, 0, [], []
R = 300
for _ in range(R):
    y = outcomes(lst, site, grp, sa, sg, sl); lo, hi = pigeon(y, site, grp); c = cgm_hw(y, lst, site, grp)
    cov_p += lo <= truth <= hi; cov_c += abs(y.mean() - truth) <= c; cov_s += abs(y.mean() - truth) <= cr_hw(y, site)
    hw_p.append((hi - lo) / 2); hw_c.append(c)
print("population accuracy %.3f | product-weight bootstrap: mean half-width %.4f coverage %.3f | CGM two-way: half-width %.4f coverage %.3f | site-only coverage %.3f" %
      (truth, np.mean(hw_p), cov_p / R, np.mean(hw_c), cov_c / R, cov_s / R))
```

### sim6b.py

```python
# Sim 6b: power of a cross-site pair-accuracy gate (two-way product-weight bootstrap lower bound > bar). numpy only.
import numpy as np
rng = np.random.default_rng(20261127)
SD_DOM = 0.3                                               # calibrated site sd (sim 6a), logit scale
PAIRS, DOMS = 28, 9                                        # scorable cross-site pairs and domains per search top 10
def center_for(acc, sd_tot):                               # logit center whose population mean accuracy equals acc
    z = rng.normal(0, sd_tot, 200000); lo, hi = -3.0, 3.0
    for _ in range(50):
        mid = (lo + hi) / 2; lo, hi = (mid, hi) if np.mean(1 / (1 + np.exp(-(mid + z)))) < acc else (lo, mid)
    return mid
def one_dataset(G, center, sd_search):
    npool = 5 * G; pop = 1 / np.arange(1, npool + 1) ** 0.9; pop /= pop.sum()
    d1, d2, srch = [], [], []
    for g in range(G):
        doms = rng.choice(npool, DOMS, replace=False, p=pop); I, J = np.triu_indices(DOMS, 1); pick = rng.choice(len(I), PAIRS, replace=False)
        d1.append(doms[I[pick]]); d2.append(doms[J[pick]]); srch.append(np.full(PAIRS, g))
    d1, d2, srch = map(np.concatenate, (d1, d2, srch))
    a = rng.normal(0, SD_DOM, npool); b = rng.normal(0, sd_search, G)
    eta = center + (a[d1] + a[d2]) / np.sqrt(2) + b[srch]
    y = (rng.random(len(eta)) < 1 / (1 + np.exp(-eta))).astype(float)
    return y, d1, d2, srch, npool
def lower_bounds(y, d1, d2, srch, npool, G, B=300):
    Wd = rng.multinomial(npool, np.full(npool, 1 / npool), B).astype(float); Wq = rng.multinomial(G, np.full(G, 1 / G), B).astype(float)
    w2 = Wq[:, srch] * Wd[:, d1] * Wd[:, d2]; w1 = Wq[:, srch]
    est = lambda w: np.nanpercentile((w * y).sum(1) / w.sum(1), 2.5)
    return est(w2), est(w1)
print("Power = share of 150 simulated tests whose 95%% lower bound clears the bar. Pairs per search %d, domains per search %d, domain pool 5 per search." % (PAIRS, DOMS))
for sd_search in (0.41, 0.6):
    print("\nsearch-level logit sd %.2f (0.41 = calibrated group+list from training; 0.60 = more heterogeneous searches)" % sd_search)
    print(" true acc | searches | power bar 0.55 (two-way, search-only) | power bar 0.60 (two-way, search-only) | mean two-way LB")
    for acc in (0.62, 0.65, 0.70):
        c = center_for(acc, np.sqrt(SD_DOM ** 2 + sd_search ** 2))
        for G in (20, 30, 50, 75, 100, 150):
            L = np.array([lower_bounds(*one_dataset(G, c, sd_search), G) for _ in range(150)])
            print("  %.2f    | %4d     |      %.2f , %.2f                     |      %.2f , %.2f                     | %.3f" %
                  (acc, G, np.mean(L[:, 0] > 0.55), np.mean(L[:, 1] > 0.55), np.mean(L[:, 0] > 0.60), np.mean(L[:, 1] > 0.60), L[:, 0].mean()))
```

### sim6c.py

```python
# Sim 6c: coverage of intervals for cross-site pair accuracy with dyadic domain clustering. numpy only.
import numpy as np
exec(open('sim6b.py').read().split('print("Power')[0])     # reuse the design functions of sim 6b (same file, same seed)
def intervals(y, d1, d2, srch, npool, G, B=400):
    Wd = rng.multinomial(npool, np.full(npool, 1 / npool), B).astype(float); Wq = rng.multinomial(G, np.full(G, 1 / G), B).astype(float)
    out = []
    for w in (Wq[:, srch] * Wd[:, d1] * Wd[:, d2], Wq[:, srch]):
        st = (w * y).sum(1) / w.sum(1); out.append(np.nanpercentile(st, [2.5, 97.5]))
    e = y - y.mean()                                        # dyadic-robust analytic variance: pairs that share a search or a domain
    share = (srch[:, None] == srch[None, :]) | (d1[:, None] == d1[None, :]) | (d1[:, None] == d2[None, :]) | (d2[:, None] == d1[None, :]) | (d2[:, None] == d2[None, :])
    hw = 1.96 * np.sqrt(e @ share @ e) / len(y)
    out.append(np.array([y.mean() - hw, y.mean() + hw]))
    return out
for G in (30, 75):
    acc, sd_search = 0.65, 0.41
    c = center_for(acc, np.sqrt(SD_DOM ** 2 + sd_search ** 2))
    res = np.array([[[lo <= acc <= hi, (hi - lo) / 2] for lo, hi in intervals(*one_dataset(G, c, sd_search), G)] for _ in range(200)])
    for k, name in enumerate(("product weights search x domain x domain", "search-only bootstrap", "dyadic-robust analytic")):
        print("G=%3d %-42s coverage %.3f mean half-width %.4f" % (G, name, res[:, k, 0].mean(), res[:, k, 1].mean()))
```

### sim6d.py

```python
# Sim 6d: power of a cross-site gate using the dyadic-robust analytic lower bound. numpy only.
import numpy as np
exec(open('sim6b.py').read().split('print("Power')[0])     # same design functions and seed as sim 6b
def dyadic_lb(y, d1, d2, srch, npool, G):
    e = y - y.mean()
    share = (srch[:, None] == srch[None, :]) | (d1[:, None] == d1[None, :]) | (d1[:, None] == d2[None, :]) | (d2[:, None] == d1[None, :]) | (d2[:, None] == d2[None, :])
    return y.mean() - 1.96 * np.sqrt(e @ share @ e) / len(y)
print("searches needed: power = share of 200 simulated tests with dyadic-robust 95% lower bound above the bar")
for sd_search in (0.41, 0.6):
    print("search-level logit sd %.2f" % sd_search)
    print("  true acc | " + " | ".join("G=%d" % G for G in (20, 30, 50, 75, 100, 150, 200)))
    for acc in (0.62, 0.65, 0.70):
        c = center_for(acc, np.sqrt(SD_DOM ** 2 + sd_search ** 2)); row55, row60 = [], []
        for G in (20, 30, 50, 75, 100, 150, 200):
            L = np.array([dyadic_lb(*one_dataset(G, c, sd_search), G) for _ in range(200)])
            row55.append(np.mean(L > 0.55)); row60.append(np.mean(L > 0.60))
        print("  %.2f bar 0.55 | " % acc + " | ".join("%.2f" % p for p in row55))
        print("  %.2f bar 0.60 | " % acc + " | ".join("%.2f" % p for p in row60))
```

## Appendix B. Sources

| Source | Used for | Grade |
| --- | --- | --- |
| Lindskog, McNeil, Schmock (2001), "Kendall's tau for elliptical distributions", https://staff.math.su.se/lindskog/KendallsTau.pdf | tau = (2/pi) arcsin(rho), traced to Stieltjes 1889 and Sheppard 1898 | STRONG |
| Bifet, Castillo, Chirita, Weber (2005), https://ingmarweber.de/wp-content/uploads/2013/07/An-Analysis-of-Factors-Used-in-Search-Engine-Ranking.pdf | pair precision = 50% + 50% * tau; held-out pair accuracy 62.5% to 67.5% (Table 4) | STRONG |
| Li and Duan (1989), Annals of Statistics 17(3):1009-1052, https://projecteuclid.org/journals/annals-of-statistics/volume-17/issue-3/Regression-Analysis-Under-Link-Violation/10.1214/aos/1176347254.short, read through Park and Weisberg (1995) UMN Tech. Report 602, https://conservancy.umn.edu/server/api/core/bitstreams/e1cff89e-45c1-4292-acf3-ac19d9a7e85f/content | consistency up to scale under a linear conditional mean | MEDIUM |
| Diaconis and Graham (1977), JRSS B 39(2):262-268, as Proposition 1 in Dwork, Kumar, Naor, Sivakumar (2001), https://archives.iw3c2.org/www10/cdrom/papers/577/ | K <= F <= 2K | STRONG (secondary statement of a proved result) |
| Morris (1983), JASA 78:47-55, https://errorstatistics.com/wp-content/uploads/2015/11/morris.pdf | B = V/(V + A); estimator (1 - B) Y_i + B z_i' b | STRONG |
| Fay and Herriot (1979), JASA 74:269-277, https://scilit.net/publications/6714384cbd8122347ad2be41930b50e9 | area-level shrinkage toward a regression | SNIPPET ONLY |
| Chamberlain (1980), Review of Economic Studies 47:225-238, https://ideas.repec.org/a/oup/restud/v47y1980i1p225-238..html | conditional logit removes incidental parameters | MEDIUM |
| Mundlak (1978), Econometrica 46(1):69-85, https://www.econometricsociety.org/publications/econometrica/1978/01/01/pooling-time-series-and-cross-section-data | within estimator, effects correlated with regressors | MEDIUM |
| Neuhaus and Kalbfleisch (1998), Biometrics 54(2):638-645 (abstract seen only in search results) | conditional likelihood = within; mixed model = weighted between and within | SNIPPET ONLY |
| Bell and Jones (2015), PSRM 3(1):133-153, https://eprints.whiterose.ac.uk/id/eprint/89757 | within-between random effects | SNIPPET ONLY |
| Robinson (1950), ASR 15(3):351-357, https://www.stats.uwo.ca/faculty/aim/2015/9938/articles/Robinson1950AmericanSociologicalReview.pdf | ecological .946 against individual .203 | STRONG |
| Berkson (1946), Biometrics Bulletin 2(3):47-53, https://pubmed.ncbi.nlm.nih.gov/21001024/ | name and origin of selection-induced association | SNIPPET ONLY |
| Lawley (1943), Proc. Royal Society of Edinburgh A 62:28-30 | multivariate selection formulas | SNIPPET ONLY (algebra here is self-derived and simulated) |
| Thurstone (1927), Psychological Review 34:273-286, via https://en.wikipedia.org/wiki/Law_of_comparative_judgment | case V | MEDIUM |
| Hunter (2004), Annals of Statistics 32(1):384-406, https://projecteuclid.org/journals/annals-of-statistics/volume-32/issue-1/MM-algorithms-for-generalized-Bradley-Terry-models/10.1214/aos/1079120141.full | MM fitting for Plackett-Luce and Bradley-Terry | SNIPPET ONLY |
| Shah and Wainwright (2018), JMLR 18(199), https://jmlr.org/papers/v18/16-206.html | win counting (Copeland or Borda) as a ranking estimator | SNIPPET ONLY |
| Ovaisi et al. (2020), WWW, https://arxiv.org/abs/2001.11358v2 | Heckman-style selection correction in learning to rank | MEDIUM |
| Owen (2007), Annals of Applied Statistics 1(2):386-411, https://arxiv.org/pdf/0712.1111 | pigeonhole bootstrap variance and its conservatism | STRONG |
| Cameron, Gelbach, Miller (2011), JBES 29(2):238-249, https://cameron.econ.ucdavis.edu/research/CGM_twoway_ALL_13may2008.pdf | V_A + V_B - V_{A∩B} | STRONG (working paper) |
| Aronow, Samii, Assenova (2015), Political Analysis 23(4):564-577, https://arxiv.org/abs/1312.3398 | dyadic cluster-robust variance | MEDIUM |
| DOJ trial exhibit PXR0356 (already in `r3-matched-pairs.md`) | quality mostly a site property | STRONG (from the earlier memo) |
