# From pairwise comparisons to a predicted rank position: rank aggregation and learning to rank

Research memo for the round 4 master plan, written 2026-11-20. Research and theory only. No Serper credit was spent, no held-out list was read, no project or client detail was sent to any outside service. Earlier memos (`ranking-mechanics.md`, `edit-effects.md`, `r3-matched-pairs.md`) and the round 3 theory are not repeated here.

Grades: STRONG (primary source read in full or in the relevant part), MEDIUM (credible secondary source, or a primary abstract or documentation page), WEAK (single informal source), SNIPPET ONLY, UNVERIFIED. Numbers marked DERIVED are my own algebra. Numbers marked SIMULATED come from small numpy simulations I ran in memory for this memo (no file written, seeds fixed, code described in the text). They are illustrations of mechanisms, not evidence about Google.

## What this means for the master plan

1. **Use one likelihood family for everything: Plackett-Luce over top-k lists (the rank-ordered logit), with a candidate pool.** It is the only standard model that (a) handles a top-10 or top-30 list correctly, (b) cancels the site effect automatically in a site-restricted list, (c) takes covariates and per-domain effects, and (d) gives exact pairwise probabilities (Bradley-Terry) and rank distributions for the conversion to a position. ListMLE is this likelihood (Xia et al. 2008, STRONG). RankNet with a linear score is structured Bradley-Terry (Burges 2010, STRONG).
2. **Do not fit cross-site weights on pairs taken only from inside a top 10.** Inside a list that Google selected on the total score, every component of the score loses most of its visible effect and components become negatively correlated (Berkson). SIMULATED: a content signal that orders 0.648 of pairs in a 50-page pool orders 0.557 inside the top 10, and logistic or subset Plackett-Luce fits on top-10 pairs recover only 0.18 to 0.37 of the true coefficients. The top-k Plackett-Luce likelihood with the rest of the pool in the denominators recovers them (0.98 to 1.01). This alone can explain why content looks near coin inside ordinary top 10s. Selection attenuates but cannot flip the sign of a true score component (DERIVED), so the wrong-way sign of Tranco and Common Crawl authority means those proxies do not measure the site standing Google uses on these searches.
3. **Build candidate pools with known non-ranking pages, and weight sampled negatives.** Every page we know is a candidate but is absent from the observed list (the Nov 16 positions 11 to 30, each universe site's best page from its site-restricted list, the site's other pages in the local index) goes into the denominators. If the pool is a random sample of the non-listed candidates, add log(1/sampling fraction) to each sampled item's utility (McFadden's sampling-of-alternatives correction, MEDIUM). SIMULATED: sampling 10 of 40 non-listed pages gives coefficients 0.75 unweighted and 1.03 weighted. The same applies to site-restricted lists: they are a top 10 of the site's indexed pages, so the site's other crawled pages should enter as weighted negatives, or beta comes out on a shrunken scale that does not match the site effects.
4. **Identify content and site separately, then join them on one scale.** Stage 1: beta from site-restricted lists, where the site effect cancels exactly (conditional logit with a (search, site) stratum). Stage 2: per-domain effects from ordinary result lists, with beta times content entered as an offset multiplied by one free scale gamma, plus a few query-domain terms. Shrink each domain effect toward a regression on site-level covariates with a ridge (Gaussian) penalty chosen by held-out log-likelihood. Report gamma: if it is far from 1, the two stages are not on one scale and the edit effect is mis-sized.
5. **For an edit, keep the page's own residual.** Rivals do not change when the client edits a page, and their current order is at the noise ceiling. So infer each rival's latent utility from its observed positions (all pulls), infer the edited page's residual from its current position, and move only the edited page by gamma times beta times the change in content score. For a brand new URL, draw the residual from its prior. This is the only design where a content model of about 0.70 pair accuracy can still produce a usable position.
6. **Convert to a position by simulation, and report a distribution, not a sorted score.** Expected rank is 1 + sum over rivals of P(rival beats page). The full rank distribution of one page under Plackett-Luce is an exact one-dimensional integral of a Poisson-binomial (DERIVED, checked by Monte Carlo). Do not treat the pairwise events as independent: SIMULATED, that understates the spread by almost half (sd 1.38 against 2.53 in a 10-page example). SIMULATED: with page-against-rival accuracy near 0.70, placing a page by sorting point scores is worse than a flat "middle of the page" guess (mean absolute error 2.73 against 2.50 on 10 places, 8.80 against 7.50 on 30), while the posterior median of the calibrated rank distribution beats it (1.95 and 6.32).
7. **Calibrate with one or two parameters, not isotonic regression.** Fit a temperature (Platt-style) on held-out search groups. Isotonic regression loses to Platt scaling below about 200 to 1000 calibration cases (Niculescu-Mizil and Caruana 2005, STRONG), and our cases are clustered by search.
8. **Score position forecasts with proper rules.** Primary: ranked probability score over position bands (1 to 3, 4 to 10, 11 to 20, 21 to 30, beyond 30) and log loss of the observed band. Intervals: coverage plus the interval score of Gneiting and Raftery (2007, STRONG). Point error: mean absolute error of the posterior median against the domain prior, a flat position, and persistence where it exists. Bootstrap by search group.
9. **Keep the model linear and small.** With a few hundred searches no published comparison can separate learning-to-rank algorithms: Chapelle and Chang (2011, STRONG) say LETOR's 50 to 106 queries "preclude any statistically significant difference"; the tree win they report (NDCG 0.790 against 0.759 for linear RankSVM) used 19,944 training queries. Our own round 3 data agree: the fitted blend adds +0.010 over an unfitted cross-encoder. Spend effort on which pages enter the training lists, not on the learner: in a document-selection study, 44% of the variance in effectiveness on small training sets came from document selection and 31% from the algorithm (Aslam et al. 2009, slides, MEDIUM).
10. **Pairwise accuracy targets must be set with the label ceiling and the list length in view.** DERIVED from Diaconis and Graham: mean absolute position error of a full list of n pages lies between (1 - a)(n - 1)/2 and (1 - a)(n - 1). With a = 0.70 that is 1.35 to 2.7 places on 10 pages and 4.35 to 8.7 on 30. Against a single pull, observed accuracy is a*p + (1 - a)(1 - p). With p of about 0.93, the round 3 single-pull 0.709 implies about 0.74 against Google's expected order (adjacent pairs about 0.60, five or more apart about 0.86).

## 1. The models, their conditions, and how they behave on sparse graphs

**What it means for the plan:** all the standard models are one random-utility family. Use Plackett-Luce with Gumbel noise. Counting methods and Rank Centrality cannot take covariates and assume a comparison design we do not have.

### 1.1 Bradley-Terry and Luce

P(i beats j) = w_i / (w_i + w_j) = 1 / (1 + exp(-(lambda_i - lambda_j))), lambda = log w. Bradley-Terry 1952, Luce 1959. In logit form it is a logistic regression on item indicator differences (Turner and Firth 2012, STRONG, https://www.jstatsoft.org/article/view/v048i09).

Identifiability (Ford 1957, as stated by Yan, https://arxiv.org/abs/1411.1168, MEDIUM for Ford's original): the maximum likelihood estimate exists and is unique if and only if the directed win-loss graph is strongly connected, that is, for every split of the items into two non-empty sets, some item in each set has beaten some item in the other. Yan notes it fails "whenever there are undefeated objects or objects with no wins" and that the same failure holds for Thurstone. For us: a site that appears once at position 1, or only ever at the bottom, has an infinite estimate. Fixes in the literature: add pseudo-comparisons with a "ghost" item (PlackettLuce package default, interpreted as an exchangeable prior, STRONG, https://arxiv.org/abs/1810.12068), Firth bias reduction (BradleyTerry2, STRONG), or a ridge penalty. The ridge-penalised MLE, minimise L(theta) + (lambda/2)||theta||^2, is minimax optimal for top-K recovery under a random comparison design (Chen, Fan, Ma and Wang, Annals of Statistics 2019, STRONG for the estimator and claim, https://arxiv.org/abs/1707.09971).

Error and graph topology (Shah, Balakrishnan, Bradley, Parekh, Ramchandran and Wainwright, JMLR 2016, https://arxiv.org/abs/1505.01462, STRONG): with d items, N comparisons and comparison-graph Laplacian L (edge weights = share of comparisons on that pair), the MLE satisfies E||w_hat - w*||^2 <= c sigma^2 d / (lambda_2(L) N). Graphs with a small second eigenvalue (chains, two clusters joined by few edges) estimate scores badly. The paper also shows that error in the Laplacian semi-norm, which is "closely related to the prediction risk", has a topology-independent rate. Meaning for us: predicting the outcome of comparisons like those already observed (sites that meet each other in the same kind of search) is easier than pinning every site's absolute effect. A small vendor site that only meets incumbents in a few searches will have a poorly determined effect, and the model should say so.

### 1.2 Thurstone and Mosteller

P(i beats j) = Phi((mu_i - mu_j) / (sigma sqrt 2)), Gaussian utilities (Thurstone 1927, case V by Mosteller 1951; not re-read, standard). Shah et al. 2016 cover Thurstone and Bradley-Terry with the same rates (STRONG). With small data the logit and probit links are not distinguishable in practice. Thurstone needs multivariate normal integrals for whole rankings, so it is inconvenient for lists. TrueSkill is an approximate Bayesian Thurstone model for rankings of any number of entities, fitted by message passing (Herbrich, Minka and Graepel, NIPS 2006, STRONG, https://proceedings.neurips.cc/paper/2006/hash/f44ee263952e65b3610b8ba51229d1f9-Abstract.html). It can "infer individual skills from team results", which is the additive structure we want (a page as a team of site standing and content), but a batch fit with a Laplace approximation gives the same answer with less machinery.

### 1.3 Plackett-Luce and the top-k rank-ordered logit

A ranking is a sequence of choices. For a full ranking of a set A: P(i_1 > ... > i_J) = prod_j w_{i_j} / sum_{i in A_j} w_i, where A_j is the set of items not yet chosen (Turner, van Etten, Firth and Kosmidis 2020, STRONG). Two partial forms matter and give different likelihoods (same source):

- **Subset ranking**: a complete order of a subset; denominators use only the subset. The PlackettLuce R package and choix fit this form.
- **Top-n ranking**: the top n are ordered and every other item is "implicitly ranked lower"; denominators use "the full set of items minus the items that have been selected so far". ROlogit (Stata) fits this with covariates.

Our ordinary result lists and our site-restricted lists are top-n rankings from a larger candidate set, not subset rankings. Log-likelihood of one top-k list with utilities eta and candidate pool C:

    log L = sum_{j=1..k} [ eta_{i_j} - log( sum_{m in C minus {i_1..i_{j-1}}} exp(eta_m) ) ]

This is the exploded or rank-ordered logit of Beggs, Cardell and Hausman (Journal of Econometrics 17:1-19, 1981; bibliographic record and secondary descriptions only, MEDIUM, https://ideas.repec.org/a/eee/econom/v17y1981i1p1-19.html). With Gumbel errors, sorting eta + Gumbel noise produces exactly this distribution (Yellott 1977; the Gumbel-race property, verified numerically below), so sampling is trivial. Pairwise marginals of Plackett-Luce are Bradley-Terry: P(i above j) = w_i / (w_i + w_j) (DERIVED from the Gumbel race; SIMULATED check on 400,000 draws: 0.8185 against formula 0.8176). Error rates: the MLE from partial rankings is minimax optimal up to a log factor and its error depends inversely on the spectral gap of a comparison graph (Hajek, Oh and Xu, NIPS 2014, abstract read, MEDIUM, https://arxiv.org/abs/1406.5638).

Rank breaking. Our current round 3 scorer fits a logistic regression on all pairs inside each list as if independent. That is "full rank-breaking". It ranks well but "naive rank-breaking approaches can result in inconsistent estimates"; for partial orders "the full-rank breaking estimator is inconsistent ... the error is strictly bounded away from zero even with infinite samples" (Khetan and Oh, JMLR 2016, STRONG, https://jmlr.org/papers/v17/16-209.html; characterisation of consistent breakings in Azari Soufiani, Parkes and Xia, ICML 2014, abstract level, MEDIUM, https://proceedings.mlr.press/v32/soufiani14.html). Pairs from top-k lists also give "a biased view of the pairwise preferences" (Ahmadi Fahandar, Huellermeier and Couso, ICML 2017, STRONG, https://arxiv.org/abs/1712.01158). For ordering pages this bias often does not matter (the paper notes the order is still recovered when estimates stay co-monotonic). For a position predictor it does: the scale of beta and the calibration of probabilities are what turn a score into places.

### 1.4 Elo, Glicko

Elo is stochastic gradient ascent on the Bradley-Terry likelihood; "the constant K ... will turn out to be the learning rate in a gradient update" (Kiraly and Qian 2017, STRONG, https://arxiv.org/abs/1701.08055). Glicko adds a per-player rating deviation for uncertainty (Glickman, Applied Statistics 48:377-394, 1999, bibliographic and secondary only, MEDIUM, https://ideas.repec.org/a/bla/jorssc/v48y1999i3p377-394.html). Both are order-dependent online updates. Our data fit in memory, so a batch penalised fit dominates. Not recommended except as a later device for daily updating of site effects.

### 1.5 Rank Centrality

Stationary distribution of a random walk on the comparison graph. Theorem 1 (Negahban, Oh and Shah, Operations Research 2017, STRONG, https://arxiv.org/abs/1209.1688): with k comparisons per edge, ||pi - pi_true|| / ||pi_true|| <= C b^(5/2) kappa / xi * sqrt(log n / (k d_max)), where xi is the spectral gap of the random-walk Laplacian, kappa = d_max/d_min and b = max w_i/w_j is the dynamic range. On an Erdos-Renyi design it needs on the order of n log n comparisons, which is nearly optimal. For us the b^(5/2) term is the problem: large incumbents against tiny vendors is exactly a wide dynamic range, and the method takes no covariates and no new items. Use only as a sanity baseline.

### 1.6 Copeland and Borda counting

Copeland ranks by number of pairwise wins, a special case of Borda (Shah and Wainwright, "Simple, robust and optimal ranking from pairwise comparisons", arXiv 1512.08949, JMLR 18(199):1-38, 2018, where the same counting rule is called Borda counting; arXiv version read, STRONG for content, https://arxiv.org/abs/1512.08949, https://jmlr.org/papers/v18/16-206.html). Under their random design (each pair observed Binomial(r, p) times, independently), the top-k set is recovered with error probability at most 1/n^14 when the separation Delta_k = tau_(k) - tau_(k+1) >= 8 sqrt(log n / (n p r)), where tau_i is the probability that i beats a uniformly random other item, and when the separation is only 1/7 of that scale, every estimator fails with probability at least 1/7 in the worst case (their Theorem 1). No parametric or transitivity assumption is needed. The catch for us is the design: the guarantee needs opponents drawn at random. In our data big sites meet each other on head searches and small vendors meet each other on niche searches, so win counts confound strength with schedule. Counting also cannot score a page that has never been compared. Useful as a robustness check of a fitted model, not as the estimator.

### 1.7 Recommendation for our setting

Plackett-Luce top-k with covariates, per-domain ridge effects and a candidate pool. Its pairwise marginals are Bradley-Terry, so the existing pair-accuracy metrics still apply. Sparse graph handling: the ridge penalty plays the role of the ghost item and guarantees finite effects; report each domain's posterior standard deviation and the number of lists it appears in, so that "unknown site" is visible in the output.

## 2. Covariates, random effects, fixed effects, and what to implement

**What it means for the plan:** the model we want is a structured Plackett-Luce with an offset and a penalised per-domain effect. No light Python package does exactly this. It is about 150 lines of numpy and scipy.

### 2.1 Structured models

- Springall (1973, Applied Statistics 22:59-68, "Response surface fitting using a generalization of the Bradley-Terry paired comparison model"; bibliographic only, UNVERIFIED content) is the usual citation for lambda_i = sum_r beta_r x_ir with no item effect. A logistic regression on feature differences is exactly this model with full rank-breaking.
- BradleyTerry2 (Turner and Firth, JSS 48(9), 2012, STRONG) fits lambda_i = sum_r beta_r x_ir + U_i, with U_i independent errors, by penalised quasi-likelihood. U_i "allows for variability between players with equal covariate values and induces correlation between comparisons with a common player". It also allows contest-specific predictors, which are "necessarily interactions" between the contest and the players. That is the slot for query-domain terms. R only.
- PlackettLuce (Turner et al. 2020, STRONG): ties, subset rankings, ghost-item shrinkage, quasi standard errors, and trees on ranking-level covariates. Its comparison table lists item covariates as "No" for PlackettLuce and "Yes" only for ROlogit. R only.
- Conditional logit with fixed effects: grouping by stratum and conditioning out the group intercepts gives consistent slopes even when the intercepts are correlated with the regressors, while joint MLE with many group intercepts is generally inconsistent (Chamberlain, Review of Economic Studies 47:225-238, 1980; abstract and secondary sources, MEDIUM, https://www.nber.org/papers/w0325). In a site-restricted list every page shares the site, so the site effect is a stratum intercept that cancels from the Plackett-Luce likelihood by construction. That is the formal reason beta is identified there and nowhere else without assumptions.
- Shrinking item effects toward a regression on group-level predictors: alpha_d ~ N(theta' w_d, tau^2). This is the BradleyTerry2 random-effect model with a mean structure, and it is what a ridge penalty centred on theta' w_d computes at the posterior mode. If the site effect may correlate with the content features, add the site mean of the content score to w_d (Mundlak 1978, Econometrica 46:69-85; bibliographic only, the device is standard but I did not re-read it, UNVERIFIED).
- Uncertainty: Glicko and TrueSkill keep a variance per player. The batch equivalent is the inverse Hessian of the penalised negative log-likelihood (a Laplace approximation), or a bootstrap over search groups.

### 2.2 Python options without heavy dependencies

| Option | What it fits | Covariates | Top-k with pool | Installed in `.venv` |
| --- | --- | --- | --- | --- |
| choix (https://choix.lum.li/en/latest/api.html, docs read, MEDIUM) | BT pairwise, PL subset rankings, top-1 choices, Rank Centrality, EP posterior; `alpha` regularisation | No | Yes, by exploding a top-k list into k top-1 choices | No |
| statsmodels `ConditionalLogit` (https://www.statsmodels.org/stable/generated/statsmodels.discrete.conditional_models.ConditionalLogit.html, docs read, MEDIUM) | Binary outcome within groups, group intercepts conditioned out; one success per group is McFadden's choice logit | Yes | Yes, one group per stage; no offsets for sampled negatives without a workaround; penalty form of `fit_regularized` not verified | No |
| scikit-learn logistic regression on pair differences | Structured BT with full rank-breaking (our current scorer) | Yes | No | Yes |
| LightGBM `lambdarank` | Tree ranker, NDCG-weighted pairwise gradients | Yes | No; outputs scores, not calibrated probabilities | Yes (4.7.0) |
| numpy and scipy | Anything below | Yes | Yes | Yes |

### 2.3 What a plain numpy or scipy fit needs

Utility of candidate m in list l (a (search, pull) or a (search, site, pull)):

    eta_m = gamma * o_m + X_m beta + alpha_{d(m)} + Z_m delta + s_m

- o_m: offset, the frozen content score (or beta_hat' x from stage 1). gamma is free in stage 2.
- alpha_d: per-domain effect. Penalty (1/(2 tau^2)) sum_d (alpha_d - theta' w_d)^2. Small ridge on beta and delta.
- s_m: sampling offset, 0 for listed pages and for pages included with certainty, log(1/pi_m) for a negative included with probability pi_m.

For each list with chosen sequence i_1..i_k and pool C_l, stage j has choice set S_j = C_l minus {i_1..i_{j-1}}, with probabilities p_m = exp(eta_m) / sum_{S_j} exp(eta). Then:

- Negative log-likelihood: sum over lists and stages of [logsumexp(eta over S_j) - eta_{i_j}], plus the penalties.
- Gradient with respect to eta_m: sum over stages where m is in S_j of (p_m - 1{m = i_j}). Chain rule to (gamma, beta, alpha, delta, theta) through the linear predictor.
- Hessian: sum over stages of F_S' (diag(p) - p p') F_S plus the penalty precisions, F the design rows. Invert at the optimum for a Laplace covariance.
- Optimiser: `scipy.optimize.minimize(method="L-BFGS-B", jac=True)`. Standardise features. Use logsumexp for stability.
- Replicate pulls of the same search enter as separate lists. That is how order noise is handled without any special label rule.
- Multiple pages of one site: the model treats each URL as an item with the shared alpha_d. Host crowding (at most about two results per site in ordinary lists, `r3-matched-pairs.md`) is a display rule, not part of the score; a later version can drop third and lower same-site pages from the pool of ordinary lists.
- Identification checks before trusting any fit: no complete separation in the design (finite estimates without the penalty on beta), each alpha_d reported with its posterior sd and appearance count, and site-constant features never entered in stage 1 (they cancel and are not identified there).

SIMULATED size of the information on one domain effect: a page in a fully ordered list of 10 equal pages carries about 0.71 units of Fisher information on its log-worth (0.87 for 30). With a prior sd of 1 logit, a domain needs about 5 to 10 appearances for a posterior sd of 0.3 to 0.4. In a top 10 drawn from a pool of 50, a listed page carries only about 0.12 and an unlisted pool member about 0.22, so pools spread information thinly and more searches beat deeper lists for site effects.

## 3. RankNet, LambdaRank, LambdaMART, ListNet, ListMLE with few queries

**What it means for the plan:** these are losses on top of the same Bradley-Terry or Plackett-Luce probability. With a few hundred searches, pick the likelihood that matches the data (ListMLE on top-k with pools) and a linear score. Trees are a later option once there are thousands of searches.

- RankNet: P_ij = 1 / (1 + exp(-sigma (s_i - s_j))) with cross-entropy, and it accepts soft targets ("P-bar_ij could be estimated by showing the pair to several judges") (Burges 2010, MSR-TR-2010-82, STRONG, https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/MSR-TR-2010-82.pdf). With a linear s it is structured Bradley-Terry. Soft targets are a direct way to use two-pull agreement.
- LambdaRank weights each pair's gradient by |Delta NDCG| from swapping the two; LambdaMART is LambdaRank with boosted trees (same source, STRONG). It optimises top-heavy ordering, not calibrated probabilities, so it is a poor fit for an expected-rank conversion without a separate calibration step.
- ListNet uses the top-one probability cross-entropy (Cao et al. 2007, described in Xia et al. 2008). ListMLE is the negative log of the Plackett-Luce probability of the observed permutation, "a Plackett-Luce model" (Xia, Liu, Wang, Zhang and Li, ICML 2008, STRONG, https://icml.cc/Conferences/2008/papers/167.pdf).
- Evidence on size: LETOR query counts of 50 to 106 "preclude any statistically significant difference between the algorithms" (Chapelle and Chang 2011, STRONG, https://proceedings.mlr.press/v14/chapelle11a/chapelle11a.pdf). On Yahoo set 1 (19,944 training queries, 519 features) GBDT reached test NDCG 0.790 against 0.759 for linear RankSVM and 0.732 for BM25F-SD alone (same source). A meta-comparison of 87 methods on mostly small LETOR sets found listwise and linear methods (ListNet, SmoothRank, FenchelRank) among the Pareto-optimal ones and LambdaMART best on MSLR-WEB10K (Tax, Bockting and Hiemstra 2015, preprint read, STRONG for what it reports, https://ris.utwente.nl/ws/portalfiles/portal/6420086/ipm2015-preprint.pdf). On large benchmarks, tuned GBDT still matched or beat most neural rankers (Qin et al., ICLR 2021, abstract level, MEDIUM, https://research.google/pubs/are-neural-rankers-still-outperformed-by-gradient-boosted-decision-trees/).
- For a fixed labelling budget, "better to train over many queries with few judged documents" (Yilmaz and Robertson 2009, quoted in Aslam et al. slides, MEDIUM, https://khoury.northeastern.edu/home/vip/teach/IRcourse/6_ML/slides/sigir09.slides.pdf). Meaning: spend pulls on more searches, not deeper lists, once a moderate pool exists.
- When a linear pairwise logistic model beats trees: when queries are few (hundreds), when one or two strong features carry most of the signal (our pretrained cross-encoder), when the test topics shift from the training topics (round 3: 37 of 104 held-out lists are in a topic with no training list), and when probabilities must be calibrated and extrapolated to an edited page outside the training range. Trees are piecewise constant, so a content change that moves a feature past the last split produces no change in score. Our round 3 data already show the plateau (blend 0.704 against 0.685 for logistic regression text only, both with site and search group held out).

## 4. From pairwise probabilities to a position

**What it means for the plan:** compute the whole rank distribution of the page from the fitted model, report its median and bands, and evaluate it with proper scores. Never sort point scores.

### 4.1 Expected rank and the exact rank distribution

- Expected rank of page i among n - 1 rivals: E[R_i] = 1 + sum_{j != i} P(j beats i). Linearity of expectation makes this exact for any joint model whose pairwise marginals are P(j beats i). The same "1 + count of items ahead" definition of rank is used by Mogstad et al. (below). Under Plackett-Luce, E[R_i] = 1 + sum_j w_j / (w_i + w_j). SIMULATED check (10 pages): Monte Carlo 5.719, formula 5.714.
- Full distribution, DERIVED: write u_m = eta_m + g_m with Gumbel g. Given the page's own draw u_i = x, the events "u_j > x" are independent with p_j(x) = 1 - exp(-exp(-(x - eta_j))). So P(R_i = r) = integral over x of PoissonBinomial(r - 1; p_1(x), ..., p_{n-1}(x)) times the density of u_i at x. A one-dimensional quadrature of an O(n^2) recursion. SIMULATED check on 10 pages: the quadrature matches 400,000 Monte Carlo draws to within 0.001 at every position.
- The naive Poisson-binomial that plugs in the unconditional pairwise probabilities and assumes independence is wrong, because all n - 1 events share the page's own noise. SIMULATED: sd 1.38 places against the true 2.53 in the same example. It produces intervals that are far too narrow.
- In practice simulate: draw parameters from the Laplace or bootstrap distribution, draw rivals' utilities from their posterior given the observed list, draw Gumbel noise, sort, record the page's position. A few thousand draws per prediction is instant at 30 pages. This also folds in parameter and rival uncertainty, which the closed forms ignore.

### 4.2 Why the conversion matters (SIMULATED)

Rivals' true utilities fixed, one page's utility seen through Gaussian noise of sd s, true utilities standard normal. "Accuracy" is the share of page-against-rival pairs ordered correctly.

| Pages | Page-vs-rival accuracy | MAE, sort by point score | MAE, posterior median rank | MAE, flat middle guess | Within 3, sort / median |
| --- | --- | --- | --- | --- | --- |
| 10 | 0.80 | 1.79 | 1.53 | 2.50 | 0.83 / 0.88 |
| 10 | 0.74 | 2.31 | 1.78 | 2.50 | 0.73 / 0.84 |
| 10 | 0.70 | 2.73 | 1.95 | 2.50 | 0.66 / 0.81 |
| 30 | 0.81 | 5.64 | 4.79 | 7.50 | 0.44 / 0.47 |
| 30 | 0.75 | 7.41 | 5.71 | 7.50 | 0.34 / 0.39 |
| 30 | 0.70 | 8.80 | 6.32 | 7.50 | 0.29 / 0.34 |

At the accuracy we have, sorting by score loses to a constant. A calibrated distribution that shrinks toward the middle when the evidence is weak wins. This is optimistic because the rivals are known exactly here; with rival uncertainty the gaps shrink.

### 4.3 Intervals for ranks

- Hall and Miller (Annals of Statistics 37(6B):3929-3959, 2009, STRONG, https://arxiv.org/abs/0911.3749): "the conventional bootstrap can produce inconsistency" for ranks, the n-out-of-n bootstrap distribution of an empirical rank "may not converge in the usual sense", and the m-out-of-n bootstrap restores consistency with an empirical choice of m.
- Xie, Singh and Zhang (JASA 104:775-788, 2009, secondary descriptions only, MEDIUM, https://ideas.repec.org/a/bes/jnlasa/v104i486y2009p775-788.html): bootstrap and Bayesian rank intervals can under-cover with ties or near ties; they propose a smoothed rank and widened intervals.
- Mogstad, Romano, Shaikh and Wilhelm (the brief's "Wilke" is Wilhelm), "Inference for Ranks with Applications to Mobility across Neighborhoods and Academic Achievement across Countries", Review of Economic Studies 91(1):476-518, 2024 (NBER version read, STRONG, https://www.nber.org/papers/w26883). Rank r_j = 1 + sum_k 1{theta_k > theta_j}. Given simultaneous confidence intervals for the differences theta_j - theta_k over all rivals k, let N_j^- be the rivals whose interval lies entirely below zero (clearly ahead) and N_j^+ those entirely above (clearly behind). Then {|N_j^-| + 1, ..., p - |N_j^+|} covers the rank with at least the nominal probability, with no separation assumption.
- Meaning for us: these are confidence sets for a latent order, not prediction intervals for tomorrow's noisy list. The product needs the predictive distribution of 4.1, which also includes Google's pull noise. The Mogstad construction is still useful as a conservative "rivals clearly ahead, rivals clearly behind" display, and the Hall and Miller result is a warning against a plain bootstrap of fitted ranks.

### 4.4 Calibration

Fit one temperature T (and optionally an intercept) on held-out search groups so that P(j beats i) = sigmoid((eta_j - eta_i)/T) matches observed pair frequencies, or equivalently rescale all utilities before the Plackett-Luce simulation. Platt scaling beats isotonic regression "when the calibration set is small (less than about 200-1000 cases)" for all nine learners tested (Niculescu-Mizil and Caruana, ICML 2005, STRONG, https://www.cs.cornell.edu/~alexn/papers/calibration.icml05.crc.rev3.pdf). Our cases are pairs clustered in lists, so the effective count is the number of lists, which is in that range.

### 4.5 Proper scores for position forecasts

- Ranked probability score over ordered bands: RPS = sum_k (F_k - O_k)^2, cumulative forecast against cumulative observed indicator. It is the Brier score summed over thresholds, proper and sensitive to distance; Gneiting and Raftery show it as a special case of their threshold construction (JASA 102:359-378, 2007, STRONG, https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf; Epstein 1969 cited there).
- Log loss of the observed band: proper and local; harsh on zero probabilities, so keep a floor on every band.
- Interval score for a central (1 - alpha) interval [l, u]: (u - l) + (2/alpha)(l - x)1{x < l} + (2/alpha)(x - u)1{x > u} (same source, STRONG). Report coverage next to it.
- Truth for scoring: the consensus position over the day's pulls, with "beyond the deepest pulled position" as its own band.

## 5. Relations between the metrics

**What it means for the plan:** a pair accuracy near 0.70 means a position error of about 2 places on a 10-result page and 5 to 7 on a 30-result list, before label noise. Targets should be stated in places and bands, with the label ceiling subtracted.

- Diaconis and Graham (JRSS B 39:262-268, 1977): for any permutation, I + T <= D <= 2I, where I is the number of inversions (Kendall distance K), T is the minimum number of transpositions and D is Spearman's footrule; D = 2I exactly when there is no reversed triple (Cornwell and McNew 2024, statement read, MEDIUM for the original, https://arxiv.org/abs/2404.16755). So K <= D <= 2K.
- DERIVED: if a share a of all n(n - 1)/2 pairs is ordered correctly, K = (1 - a) n(n - 1)/2, and mean absolute position error D/n lies in [(1 - a)(n - 1)/2, (1 - a)(n - 1)].

| a | n = 10 | n = 30 |
| --- | --- | --- |
| 0.70 | 1.35 to 2.70 | 4.35 to 8.70 |
| 0.75 | 1.13 to 2.25 | 3.63 to 7.25 |
| 0.80 | 0.90 to 1.80 | 2.90 to 5.80 |
| 0.90 | 0.45 to 0.90 | 1.45 to 2.90 |

- SIMULATED (iid normal utilities, Gaussian score noise, whole list sorted): D/K is 1.6 to 1.8 on 10 pages and 1.45 to 1.6 on 30, because errors concentrate on close pairs. Pair accuracy 0.686 gave a list MAE of 2.22 on 10 pages and 6.59 on 30. Accuracy 0.80 gave 1.50 and 4.43.
- Kendall tau and pair accuracy: tau = 2a - 1 without ties (Bifet et al.'s conversion, already in `r3-matched-pairs.md`).
- Label noise, DERIVED: if a single pull shows the true order of a pair with probability p and the scorer is right against the true order with probability a, independently, the observed accuracy is a p + (1 - a)(1 - p), so a = (observed - (1 - p)) / (2p - 1). Two pulls agree with probability r = p^2 + (1 - p)^2, so p = (1 + sqrt(2r - 1))/2.
  - All pairs: r = 0.877 gives p = 0.934; observed 0.709 gives a of about 0.741.
  - Adjacent pairs: r = 0.839, p = 0.912; observed 0.580 gives a of about 0.597.
  - Five or more apart: r = 0.932, p = 0.965; observed 0.832 gives a of about 0.857.
  - Caveat: label noise and scorer error both concentrate on close pairs, so the independence assumption fails across pairs. Do the correction within distance strata, as above, not on the pooled number. And the two inputs come from different list sets (the 40-minute repeat and the training lists), so these are approximate.

## 6. Selection and truncation

**What it means for the plan:** every list we have is selected on the very score we try to learn. Fit it with a likelihood that knows what was left out, or the cross-site weights will be shrunk and distorted.

### 6.1 What happens inside a selected list

- Berkson (1946, Biometrics Bulletin 2(3):47-53; PubMed record and secondary descriptions, MEDIUM, https://pubmed.ncbi.nlm.nih.gov/21001024/): selecting a sample on a combination of two independent factors makes them negatively associated inside the sample.
- DERIVED: if selection is on the total score u itself and (a, u) are jointly normal, Cov(a, u | selected) = (Cov(a, u)/Var(u)) Var(u | selected), which is positive. Selection on the outcome attenuates a component's visible effect but cannot reverse it. A reversed sign needs selection on something other than u, or a proxy that is not the component.
- DERIVED, why top-k order says so little: Gumbel exceedances over a high threshold are close to exponential with the same scale whatever the location, so among items that all cleared a high bar, the order inside depends only weakly on their locations. In a three-item Plackett-Luce example with S = {a, b} on top and c below, P(a > b > c) / P(b > a > c) = (w_a + w_c)/(w_b + w_c), which is pulled toward 1 from w_a/w_b whenever the excluded item has weight.
- SIMULATED (300 searches, 50 candidates each, u = site + beta*content + Gumbel, top 10 observed, site and content independent in the pool):

| Scenario | Content alone, pool / top 10 | Site alone, pool / top 10 | corr(site, content), pool / top 10 | Fitted (site, content) coefs, truth (1, beta) |
| --- | --- | --- | --- | --- |
| sd(site) 1.5, beta 1 | 0.648 / 0.557 | 0.745 / 0.584 | 0.00 / -0.19 | top-10 pairs (0.30, 0.30); subset PL (0.29, 0.30); top-k PL with pool (1.01, 1.00) |
| sd(site) 1.0, beta 1 | 0.678 / 0.564 | 0.683 / 0.530 | 0.00 / -0.14 | (0.18, 0.27); (0.17, 0.26); (1.00, 0.98) |
| sd(site) 2.0, beta 0.7 | 0.589 / 0.537 | 0.807 / 0.631 | -0.01 / -0.18 | (0.37, 0.25); (0.35, 0.25); (0.99, 0.68) |

  Inside the top 10 a real content effect looks like 0.54 to 0.56, which is what the earlier team measured across sites (BM25 0.444 to 0.559). The ratio of the two fitted coefficients is not preserved either (0.67 against a true 1.0 in the second row), so even "relative weights from top-10 pairs" is unsafe.
- Partial pools, SIMULATED (first scenario): subset PL on the top-30 order alone (0.67, 0.67); top 10 with positions 11 to 30 as the only known rest (0.86, 0.83); top 30 with 10 random pages from below 30 (0.91, 0.89); top 10 with a random 20 of the 40 below (0.89, 0.87); a random 10 of the 40 below, unweighted (0.75, 0.73) and weighted by the inverse sampling fraction (1.03, 0.99). A free per-search "outside option" weight does not fix the bias: it is never chosen, so its maximum likelihood value is zero.

### 6.2 What the literature does about it

- Model the selection: Ovaisi, Ahsan, Zhang, Vasilaky and Zheleva, "Correcting for Selection Bias in Learning-to-rank Systems", WWW 2020 (STRONG, https://arxiv.org/abs/2001.11358) adapt Heckman's two-stage method: a probit for being shown, an inverse Mills ratio, then the outcome model. "A crucial assumption here is that we will use both seen and unseen documents for a given query". Our selection and our outcome are the same latent score, so the natural version is the top-k likelihood itself, which models selection and order jointly. Either way the unseen candidates' features are needed.
- Deeper or sampled negatives: McFadden (1978) showed that a logit estimated on a sampled subset of alternatives is consistent when each alternative's utility gets the correction ln pi(D | j), the probability of drawing that set given j was chosen; under uniform conditioning the correction cancels (secondary sources, MEDIUM, https://arxiv.org/abs/2101.06211; original at https://www.its.caltech.edu/~mshum/gradio/McFadden1978.pdf, not read). In our top-k setting the listed pages are in every stage's set with certainty and the sampled rest is not, so the correction does not cancel and must be added (the 0.75 against 1.03 result above).
- Document selection for training sets: which documents enter the training lists matters as much as the learner on small sets (Aslam et al. 2009, slides, MEDIUM).
- Truncated likelihoods: the truncated-regression line of econometrics (Hausman and Wise, Econometrica 45:919-938, 1977; bibliographic record only, content UNVERIFIED, https://ideas.repec.org/a/ecm/emetrp/v45y1977i4p919-38.html) is the continuous analogue of the top-k likelihood.
- Coarsening: top-k observation is "a special (degenerate) case of rank-dependent coarsening" and pairwise statistics from it give "a biased view" (Ahmadi Fahandar et al. 2017, STRONG).

### 6.3 Where our pools come from (no credits needed)

1. Ordinary lists: positions 11 to 30 from the Nov 16 deep list for the 100 searches, sure members of the rest.
2. For each search, every universe site whose site-restricted list exists for that search but which is absent from the ordinary top 30: its first site-restricted page is that site's best candidate (42 of 70 checked lists put the site's best ordinary page first, round 3 study 1). This is a candidate that lost.
3. Site-restricted lists: the site's other crawled pages from the local index that did not appear in the site-restricted top 10, sampled and weighted.
4. Unknown remainder: unavoidable. Report sensitivity of gamma and the site effects to the assumed pool size (for example a pool weight multiplier of 1, 2 and 4) and pick it by held-out log-likelihood of the observed positions, not by fit on training lists.

## 7. Recommended estimator

Utility of page p for search q, with d the page's registrable domain:

    u(q, p) = gamma * c(q, p) + alpha_d + delta' z(q, d) + r(q, p) + g,   g ~ Gumbel(0, T)

- c(q, p): the frozen page content score (the round 3 page-blend, or beta_hat' x from stage 1). One scalar keeps the stage 2 parameter count small.
- alpha_d: domain effect, alpha_d ~ N(theta' w_d, tau^2). w_d: site-level covariates (log Tranco rank, Common Crawl harmonic centrality, the site mean of c over its pages for the search group). Their signs are learned under the pool likelihood, not assumed; the top-10 evidence that they point the wrong way is not evidence about their sign in the pool.
- z(q, d): a handful of query-domain terms: the search names the site's brand, the site's topical focus on the search group (share or mean score of its pages on that topic), home page indicator. More only if held-out log-likelihood improves.
- r(q, p): page-by-search residual (clicks, outside links, everything invisible). Random, mean zero, variance sigma_r^2 estimated from held-out lists. For a page that already ranks, its posterior given the observed positions carries over through an edit.
- g: pull and model noise. Replicate pulls are separate observations, so T absorbs pull noise; calibrate T on held-out search groups.

**Likelihood.** Plackett-Luce top-k with candidate pools and sampling offsets, on two data sets. Stage 1: site-restricted lists, strata (search, site, pull), pool = the listed pages plus the site's other indexed pages as weighted negatives, parameters beta of the content model or a single scale on c. alpha_d and z cancel. Stage 2: ordinary lists (all pulls, top 10, plus the top 30 where pulled), pool as in 6.3, offset beta_hat' x, parameters gamma, alpha, delta, theta. Why: it is the correct likelihood for top-k data, it cancels the site in stage 1 exactly as a fixed-effects conditional logit, it corrects the selection that makes top-10 pairs near coin, and its pairwise marginals are Bradley-Terry so it stays comparable with the round 3 pair metrics.

**Regularisation.** Gaussian (ridge) on alpha_d centred on theta' w_d, tau^2 chosen by held-out log-likelihood over search groups (or marginal likelihood by Laplace); small ridge on beta, delta and theta; no free per-URL parameters in the fit. Why: ridge gives finite effects when Ford's condition fails, it is the minimax-optimal regulariser studied for top-K recovery, it shrinks a small vendor with few appearances toward what its covariates predict, and its posterior sd tells the user when the site is unknown.

**Conversion to a position.** Monte Carlo over parameter draws (Laplace or search-group bootstrap), rival utilities drawn from their posterior given the current observed list (persistence for rivals), the page's own residual (carried over for an edit, drawn from the prior for a new URL), and Gumbel noise. Output: posterior median position, 50% and 80% bands, P(top 3), P(top 10), P(beyond 30), and the expected rank 1 + sum P(rival beats page) as a check. Why: sorting point scores loses to a constant at our accuracy (section 4.2), independence across pairs halves the spread (section 4.1), and a distribution is what the proper scores in 4.5 can check.

**How to test it without new credits and without heldout1.** Hold out search groups from the 100 ordinary searches (not the round 3 heldout1 lists). Fit stages 1 and 2 on the rest. For each held-out search, hide one ranking page at a time, predict its position from content, domain and the other pages, and score RPS, log loss, interval coverage, interval score and MAE of the median against the domain prior, a flat position, and the round 1 rule. Report gamma with its interval. Then, for pages with two fetched versions, apply the edit form and compare with what the earlier "persistence" numbers allow.

## 8. Risks and things I could not verify

- Every simulation here assumes the Plackett-Luce form, independent site and content in the pool, and Gumbel noise. Real Google order also has host crowding, result-type slots and core-update drift. The simulations show mechanisms and sizes, not our numbers.
- The candidate pool is never fully known. The pool-size sensitivity in 6.3 is a real modelling choice with no ground truth; it should be chosen on held-out likelihood and reported.
- Site-restricted lists are top 10s of a site's indexed pages, and a page missing from one may be unindexed, crowded out or retrieval-limited rather than outranked. Using crawled pages as negatives assumes they were candidates.
- The two-stage scale gamma could absorb mis-specification. If it is far from 1, content and site are not on one scale and edit effects are mis-sized.
- Read only through abstracts, records or secondary sources: Beggs, Cardell and Hausman 1981; Springall 1973 (content unverified); Chamberlain 1980; Mundlak 1978 (device unverified); Xie, Singh and Zhang 2009; Glickman 1999; Hajek, Oh and Xu 2014; Azari Soufiani et al. 2014; McFadden 1978; Hausman and Wise 1977 (content unverified); Berkson 1946; Qin et al. 2021; Yilmaz and Robertson 2009 (one-line quote in slides). Diaconis and Graham read through a 2024 restatement.
- Not checked: Python packages trueskill, openskill, pylogit, xlogit. Serper's credit cost for deeper lists was not checked and must be before any plan that buys deep pulls.

## Sources

| Source | Used for | Grade |
| --- | --- | --- |
| Shah and Wainwright, Simple, robust and optimal ranking from pairwise comparisons, https://arxiv.org/abs/1512.08949 | Copeland threshold, random design | STRONG |
| Negahban, Oh and Shah, Rank Centrality, https://arxiv.org/abs/1209.1688 | Error bound, dynamic range | STRONG |
| Shah, Balakrishnan, Bradley, Parekh, Ramchandran and Wainwright, https://arxiv.org/abs/1505.01462 | Laplacian dependence of MLE error | STRONG |
| Yan, Ranking in the generalized Bradley-Terry models when the strong connection condition fails, https://arxiv.org/abs/1411.1168 | Ford's condition | MEDIUM |
| Turner, van Etten, Firth and Kosmidis, PlackettLuce, https://arxiv.org/abs/1810.12068 | PL likelihood, subset vs top-n, ghost item, software table | STRONG |
| Turner and Firth, BradleyTerry2, https://www.jstatsoft.org/article/view/v048i09 | Covariates, random effects, contest-specific terms | STRONG |
| Chen, Fan, Ma and Wang, https://arxiv.org/abs/1707.09971 | Ridge-penalised MLE optimality | STRONG |
| Hajek, Oh and Xu, https://arxiv.org/abs/1406.5638 | PL partial rankings, spectral gap | MEDIUM |
| Khetan and Oh, https://jmlr.org/papers/v17/16-209.html | Full rank-breaking inconsistent | STRONG |
| Azari Soufiani, Parkes and Xia, https://proceedings.mlr.press/v32/soufiani14.html | Consistent breakings | MEDIUM |
| Ahmadi Fahandar, Huellermeier and Couso, https://arxiv.org/abs/1712.01158 | Top-k coarsening bias | STRONG |
| Beggs, Cardell and Hausman 1981, https://ideas.repec.org/a/eee/econom/v17y1981i1p1-19.html | Rank-ordered logit | MEDIUM |
| Kiraly and Qian, https://arxiv.org/abs/1701.08055 | Elo as gradient step on BT | STRONG |
| Glickman 1999, https://ideas.repec.org/a/bla/jorssc/v48y1999i3p377-394.html | Glicko | MEDIUM |
| Herbrich, Minka and Graepel, TrueSkill, https://proceedings.neurips.cc/paper/2006/hash/f44ee263952e65b3610b8ba51229d1f9-Abstract.html | Bayesian Thurstone for rankings | STRONG |
| Burges, MSR-TR-2010-82, https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/MSR-TR-2010-82.pdf | RankNet, LambdaRank, LambdaMART | STRONG |
| Xia et al., ListMLE, https://icml.cc/Conferences/2008/papers/167.pdf | ListMLE is PL likelihood | STRONG |
| Chapelle and Chang, https://proceedings.mlr.press/v14/chapelle11a/chapelle11a.pdf | Query counts, GBDT vs linear | STRONG |
| Tax, Bockting and Hiemstra, https://ris.utwente.nl/ws/portalfiles/portal/6420086/ipm2015-preprint.pdf | 87-method comparison | STRONG |
| Qin et al., ICLR 2021, https://research.google/pubs/are-neural-rankers-still-outperformed-by-gradient-boosted-decision-trees/ | GBDT vs neural | MEDIUM |
| Aslam et al. slides, https://khoury.northeastern.edu/home/vip/teach/IRcourse/6_ML/slides/sigir09.slides.pdf | Document selection, many shallow queries | MEDIUM |
| Hall and Miller, https://arxiv.org/abs/0911.3749 | Bootstrap inconsistency for ranks | STRONG |
| Xie, Singh and Zhang, https://ideas.repec.org/a/bes/jnlasa/v104i486y2009p775-788.html | Rank intervals with near ties | MEDIUM |
| Mogstad, Romano, Shaikh and Wilhelm, https://www.nber.org/papers/w26883 | Confidence sets for ranks | STRONG |
| Niculescu-Mizil and Caruana, https://www.cs.cornell.edu/~alexn/papers/calibration.icml05.crc.rev3.pdf | Platt vs isotonic | STRONG |
| Gneiting and Raftery, https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf | RPS, interval score | STRONG |
| Cornwell and McNew, https://arxiv.org/abs/2404.16755 | Diaconis-Graham inequality | MEDIUM |
| Ovaisi et al., https://arxiv.org/abs/2001.11358 | Heckman-style correction in LTR | STRONG |
| McFadden 1978 via Dekker et al., https://arxiv.org/abs/2101.06211 | Sampling of alternatives | MEDIUM |
| Chamberlain 1980, https://www.nber.org/papers/w0325 | Conditional logit with fixed effects | MEDIUM |
| Berkson 1946, https://pubmed.ncbi.nlm.nih.gov/21001024/ | Selection-induced association | MEDIUM |
| Hausman and Wise 1977, https://ideas.repec.org/a/ecm/emetrp/v45y1977i4p919-38.html | Truncated estimation | UNVERIFIED content |
| Mundlak 1978, https://www.econometricsociety.org/publications/econometrica/1978/01/01/pooling-time-series-and-cross-section-data | Group-mean device | UNVERIFIED content |
| choix docs, https://choix.lum.li/en/latest/api.html | Python BT and PL | MEDIUM |
| statsmodels ConditionalLogit docs, https://www.statsmodels.org/stable/generated/statsmodels.discrete.conditional_models.ConditionalLogit.html | Python conditional logit | MEDIUM |
