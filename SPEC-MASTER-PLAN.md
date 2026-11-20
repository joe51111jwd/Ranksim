# Ranksim master plan: where a new client site will rank across its key searches, and what an edit does to a page that ranks

Written 2026-11-20 by the lead (Claude goal run), branch `claude/goal-ranksim-works`. Revised the same day after an adversarial review (Appendix D) and after James's two directions of 2:52 and 2:53 PM ET. This is a plan and a theory. No production code was written and no Serper credit was spent to make it. Every model number in it is labeled PREDICTED, SIMULATED or DERIVED. Every REAL number names its source. James reviews this file before any coding agent runs a stage.

Product goals (James):
- Nov 20, the headline product: "If I made a website for a client and I can run it and know where I rank for hundreds of key searches. That's what matters."
- Nov 20, on what counts: "No one would pay for 71%." Pair accuracy is not the product metric.
- Nov 19, now the secondary product: "make a meaningful change to a page, run the predictor, and get an approximate predicted Google rank position. Don't worry about tiny day-to-day edits or knowing exactly what to fix yet."

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

### 0.1 What you asked for, and the short honest answer

You gave two directions on Nov 20. First: "No one would pay for 71%." Second: "If I made a website for a client and I can run it and know where I rank for hundreds of key searches. That's what matters."

So the plan now has one headline product and one secondary product, and each has a product gate measured on real sites or real edits against real Google. Pair accuracy stays only as a check on parts. It is not a product gate anywhere.

| | Product 1 (headline): rank bands for a new site | Product 2 (secondary): effect of an edit |
| --- | --- | --- |
| Input | A client site with no rank history, and its list of key searches | A page that already ranks, and an edited version |
| Output | One band per search: top 3, 4 to 10, page 2, not ranking, or "cannot tell". Also the expected number of searches in each band | Up, down or stay, a predicted new position, or "cannot tell" |
| Product gate | Checkpoint P, on real sites the model never saw (0.2) | Gate E, on real edits (0.3) |
| Can free data reach a sellable number? | Probably not. PREDICTED chance about 0.1 to 0.15 (lead's estimate). A lower "worth continuing" bar: about 0.5 | Probably not. Direction is capped near 0.67 by what we measured (DERIVED) |
| When will we know? | About two weeks of work, 0 credits | Not before Dec 17, and it needs more real edits than exist today |

The short honest answer: free data will most likely give a triage tool ("these are the searches where this site has a real chance of page one"), not "know where I rank". Sections 0.2 and 0.3 give the number that would change that, and the point at which to stop.

### 0.2 Product 1: where a new site will rank across its key searches

**How a site with no history is scored from free signals.** Four inputs:
1. Its pages. For each search the content scorer rates the site's best page against the pages that rank now.
2. Free site signals: referring domains from the Common Crawl link graph (from all sites, and from sites of the niche), listing in the Majestic, Tranco and Chrome top lists, brand demand, domain age, site type, and how focused the site is on the topic.
3. The competition on each search, read from its current result list: how strong the sites on pages 1 and 2 are, and how many small sites hold a slot there.
4. What happened to similar sites: the model is fitted on real sites of the niche, each treated as if it were new.

A weak site is placed on page one only where weak sites already hold page-one slots and its page fits the search well. A brand-new domain has almost no links, so most of its searches will come out "not ranking". That is probably right for its first months. The tool predicts the settled rank. It cannot say how long a new site takes to get there, and no stored data covers brand-new domains.

**The product test (Checkpoint P, stage 3).** Take a real site of the niche. Hide every trace of its rankings. Score it as new across the stored searches. Compare with where Google really has it. Two readings, both at 0 credits:
- Every eligible site in turn: about 400 sites, 100 searches with a top-20 list and 40 more with page one only. The model's form is chosen on this data, so the reading is PROVISIONAL.
- The 145 sites that no fit of this project has ever used, scored once. This one is small. REAL counts: they hold 67 page-one placements on 35 sites and 84 page-two placements.

All bars are measured on the searches a site targets (a rule fixed on the design sites and frozen before any held-out site is scored, section 7), so easy "this site has nothing on that topic" cases cannot inflate them.

| Measure | Sellable bar (lead's proposal, D8) | Continue bar. Below it: stop | PREDICTED from free data (lead's estimate) |
| --- | --- | --- | --- |
| P1, page one or not. Of the tool's "page 1" calls, the share that are right, and the share of real page-one rankings it finds | 0.70 right while finding 0.40 | 0.35 right while finding 0.30, and better than a guess from the search's difficulty alone, with the interval above zero | 0.25 to 0.50 right while finding 0.30 |
| P2, right band. On searches where the site really is in the top 20, the share placed in the right band | 0.60 | 0.25 | 0.15 to 0.35 |
| P3, spread. Per site, the predicted number of page-one searches against the real number | within 25% or 2 searches for 7 sites in 10 | rank correlation across sites at least 0.4 | correlation 0.4 to 0.7 |
| Coverage. Share of targeted searches with a confident call, the rest "cannot tell" | at least 0.80 | reported | 0.85 or more, mostly confident "not ranking" |

The plain share of searches placed in the correct band is always shown too, next to the same number for a tool that answers "not ranking" every time. That empty tool will already score about 0.70 to 0.90, which is why no bar is written on it.

**What someone would pay for.** Agencies pay today for keyword difficulty scores that publish no accuracy at all. A tool that is right on 7 of 10 page-one calls for a site it has never seen would stand out. That needs the model to separate rankers from non-rankers far better than any published free-data method has (the best published cross-site results order 62% to 67% of pairs). So the sellable bar is a long shot on free data, and the plan says so before any work starts.

**Kill or pivot.** If P1 or P2 is under the continue bar on the first reading, the no-history product stops on free data. No further tuning. The pivots, in order of cost:
1. Search Console (D2). For a site that is live, Google already reports its real position on every search with impressions, for free. Then the product is real tracking plus the edit effect, and the forecast is needed only before launch.
2. Sell the triage list (searches ranked by chance of page one) and not the bands.
3. Paid link data, which is your call. Site strength is about 75% of the score among candidate pages (DERIVED at R = 3) and free signals see it only weakly.

If the continue bars hold but the sellable bars do not, the tool is a triage tool and is described as that. If the sellable bars hold on both readings, the claim still needs unseen result lists before anyone hears it: 200 credits (section 0.5).

**What a client run costs.** The tool needs the current result list of every search: 2 credits per search for pages 1 and 2. A run of 300 searches is 600 credits. The free balance cannot pay for one run. Until credits exist the product can only be shown on the 140 stored searches.

### 0.3 Product 2: what an edit does to a page that already ranks (secondary)

**Gate E, on real edits.** For each edited page and search: (a) direction, called as up, down or stay, where up or down means a move of 2 or more places beyond what unedited control pages did; (b) whether the predicted new position is within 2 places of the real one; (c) coverage, the share of edits that get a confident call and not "cannot tell".

| Measure | Sellable bar (lead's proposal, D8) | What we can honestly expect |
| --- | --- | --- |
| Direction right, on confident calls | 0.70, with confident calls on at least half of the edits | PREDICTED 0.55 to 0.65. REAL: over days, changed pages moved no more than unchanged pages (+0.036 [-0.065, +0.140]). At most a third of moves carry any edit signal, which caps direction near 0.67 (DERIVED) |
| Within 2 places, on confident calls | 0.70, and at least 10 points above "predict no change" on the same edits | Mostly rank stability, not model skill. REAL: an unedited page at 4 to 10 stays within 2 places 0.82 of the time over one day and 0.50 over ten days across the core update |

**Can it even be measured?** It needs about 40 to 70 independent real edits. PR 45 gives about 10 and reads at 18 credits a date. PR 41 gives more but costs 222 credits a date, which is not funded. Search Console (D2) would give those reads for free.

**Kill or pivot.** After 40 independent scored edits: if direction on confident calls is under 0.60, or "within 2" is not above "predict no change", stop selling position changes. Keep only the comparison of two versions ("this version fits the search better"), with no rank claim. Until that count exists, every edit prediction is labeled "unconfirmed against Google".

### 0.4 The stages at a glance

| Stage | What it does | Credits | Gate | PREDICTED chance (lead's estimate) |
| --- | --- | ---: | --- | --- |
| 0 | Pre-flight: push, freeze the gate file, credit check | 0 | G0 | near 1 |
| 1 | A part, not a product: the content scorer on unseen sites (same-site pairs). The freeze and the PR 45 ledger run in any case. The test itself runs only if D1 says so | 208 | G1-GOAL: lower bound above 0.65. Advance rule: point at least 0.65 | goal 0.45, advance 0.85 |
| 2 | Site strength from free signals. Label-blind crawl of the niche sites | 0 | G2, a cross-validation on design data, PROVISIONAL | 0.4 |
| 3 | The band model and **Checkpoint P, the Product 1 test**. Also part checks 3a and 3c | 0. Unseen confirmation: 200 | P continue bars, P sellable bars, G3 for the parts | continue 0.5, sellable 0.1 to 0.15, G3 0.15 |
| 4 | The edit simulator (Product 2): sanity suite, honest bands, then Gate E on real edits | 36 for two PR 45 reads | G4 now. Gate E not before Dec 17 | G4 core gates 0.6, Gate E sellable: unlikely |

### 0.5 Credits, and the decisions needed from James

REAL, read 2026-11-20 15:45 ET: the shared free Serper balance is 513. This plan's own pulls stop at 260, so it can spend 253. Two other users draw on the same key every morning between about 09:10 and 11:00 ET: the other team's collector (about 90) and a 15-credit tracker for another client. Together about 105 a day. The collector's own floor is 200, not 250, and the tracker has none.

| # | Decision | Why it matters | Default if no answer |
| --- | --- | --- | --- |
| **D1, today** | What to do with the last free credits. **(C, recommended) Pause both other users of the key today.** Then nothing is urgent and the 253 credits go to whichever test Checkpoint P shows is worth it. If they cannot be paused, the credits are gone by about Nov 23 and only one of these fits, tonight, before 09:00 ET Nov 21: **(A)** the stage 1 test, 208 credits, a part and not a product. **(B)** fresh result lists for Product 1: 100 new searches, pages 1 and 2, 200 credits, sealed until the model is frozen. This is the only unseen data for the product you named, and it needs a small pull script first. Lead's pick if no pause: B | After tomorrow morning's runs the balance is about 408. A then never fits again without new credits, and B shrinks to 51 searches and to none a day later. With no pause, the other users also take the balance under your 250 floor by Nov 21 to 23, whatever this plan does | No stage runs on its own. If an agent is started without an answer, it spends nothing and runs the 0-credit stages |
| D2 | Connect Google Search Console for the client's site (free, read only) | Real positions for hundreds of the client's searches at no cost. It is pivot 1 for Product 1, and the only free way to read the PR 41 and PR 45 edits for Gate E | Use the exports on disk only (through mid November) |
| D3 | More credits: one free signup for extra Google lists, or lifting the no-money rule for Serper credits | Without them there is no unseen test of Product 1 beyond D1 (B), no PR 41 read, and no client run of 300 searches | None |
| D4 | Allow the client's domain to be sent to free public lookups (Google autocomplete, Wikidata, RDAP) | Brand-demand and age signals for the client's site | Never sent. Its values come from local data only |
| D6 | Commit `scripts/claude/t1b_postmortem.py` (untracked, but its outputs were committed in `f0da90c`) | The repo rule says every number comes from a committed script | Left untracked and flagged |
| D7 | Allow paid LLM calls for an LLM-judge content scorer | Only a fallback if the content scorer fails. Costs real money | Not used |
| D8 | Confirm or change the sellable bars and continue bars of 0.2 and 0.3 | They decide when to stop. They are the lead's proposal, not a measured market fact | Used as written |
| D9 | Confirm the target map: GOAL Target 1 is scored as Checkpoint 3c, Target 2 becomes Gate E, Target 3 (one-day movers) is retired under your Nov 19 note | GOAL.md still names all three | Used as written |

The adversarial review is logged in Appendix D: five Opus reviewers and five skeptic passes, 66 distinct objections, 61 accepted and 5 rejected. The largest changes: the credit numbers were stale and the fresh test was no longer affordable, several gates could be passed by an empty or a silent tool, the content score was being tested on sites it was trained on, one gate could not be passed even by a perfect tool, and no gate compared anything with a real product outcome. The last point is what your Nov 20 directions fix.

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
| Deep lists | One day only: Nov 16, 100 searches. All reach about position 20, 70 have a third page and 66 reach position 30. 16,264 cross-site pairs with both pages readable | `r4-data-inventory.md` 3, recount in this review |
| Platforms in c100 top-10 rows | 22.3% user-content platforms, 10.0% review aggregators. Readable 22.7% and 44.4%, against 88.9% for ordinary sites | `r4-data-inventory.md` 2 |
| Global lists and the client | Majestic Million and Tranco each cover about 243 of 428 c100 domains. The client's domain is in neither | `r4-data-inventory.md` 7 |
| Serper balance | 513. Floor 250. This plan's pulls stop at 260 | read 2026-11-20 15:45 ET (617 at 09:04, 538 at 10:40) |
| Other users of the same key | A collector of the other team: three pulls of the original 30 searches each morning, 84 to 90 credits, own floor 200. A tracker for another client: 15 a day, no floor. About 6 more a day charged with no stored response. Runs between about 09:10 and 11:00 ET | `serp_raw` Nov 17 to 20, `/workspace/ranksim-data/collect_daily.sh` |
| Sites never used by any fit (test2, 145 sites) in the Nov 16 deep lists | 67 page-one placements on 35 sites, 84 page-two placements, 79 sites with at least one top-20 placement, 13 sites with three or more | counts only, this review |
| All universe sites in the Nov 16 deep lists | page one: 571 placements. Top 20: 1,135 placements on 328 sites | counts only, this review |
| Client's Search Console export | 429 searches. 343 average beyond position 10, 231 beyond 30. 401 of them have no stored result list | `/workspace/seo-tracker/smartcrew/gsc-export/`, review |
| Same-site pairs of non-platform sites in the deep lists | hcss.com holds about 60%. About five other sites have five or more pairs | review, counts only |
| Content scorer on its own training lists | 0.771 in sample, against 0.704 with site and search group held out. 85 of the 100 deep-list searches are in its training groups | `evals/claude/r3/model/page-blend/fit_info.json`, review |
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

Every SIMULATED row is against the true order. Against a single pull, subtract 0.01 to 0.03.

| Quantity | Value | Kind |
| --- | --- | --- |
| Two pulls of one search agree on a pair | 0.870 to 0.877 of all pairs (0.932 for pairs five or more places apart) | REAL |
| One pull against the settled order (the label ceiling) | 0.91 to 0.94 | DERIVED |
| Best transparent published cross-site pair accuracy | 0.625 to 0.675 over top 100 with Google's own link data. 0.65 to 0.66 on top-10 pairs with domains held out | published |
| Same-site content scorer against Google's settled order | 0.72 to 0.74 | DERIVED from REAL |
| Cross-site, inside a top 10, content alone | 0.53 to 0.54 | SIMULATED, R = 3 |
| Cross-site, inside a top 10, content plus a site estimate correlated 0.7 with the truth | 0.58 to 0.60 | SIMULATED, R = 1 to 10 |
| Cross-site, inside a top 10, content plus a perfect site value | 0.62 to 0.78 depending on R and pool size (0.66 to 0.71 at R = 3) | SIMULATED |
| Any model, inside a top 10, if hidden click signals hold 20% of the score | at most about 0.67 to 0.75, depending on the candidate pool (0.69 at 300 candidates) | SIMULATED |
| Cross-site, all pairs of a top 30, combined model | 0.58 to 0.64 | PREDICTED |
| Cross-site, page-one boundary pairs, combined model | 0.62 to 0.68 | PREDICTED |
| A signal that holds half the score, over the whole candidate pool and not only the top 10 | orders 0.72 of pairs, against 0.58 inside the top 10 | SIMULATED |
| New page on a known site, placed inside a top 30 | mean error about 6.3 to 6.8 places against 7.5 for the middle guess | SIMULATED at a = 0.60 to 0.65 |
| New site with no history, page one or not, on the searches it targets | 0.25 to 0.50 of "page 1" calls right while finding 0.30 of the real ones | PREDICTED, lead's estimate (see 2.9) |
| Page that ranks, no edit, next day | within 1 place 0.72, within 2 places 0.82 (positions 4 to 10) | REAL |
| Page that ranks, title and H1 retargeted (about one standard deviation), before the transfer discount | about 2 to 3 places from position 5, 4 to 6 from position 9, 6 to 10 from position 15 | SIMULATED upper figure, R = 1 to 10 |
| Page that ranks, body-only rewrite with the title and H1 kept (about 0.1 to 0.3 standard deviations) | about 0.2 to 2 places from position 14 and under 1 place from position 5, at the middle of the transfer prior. Usually inside the one-day noise of about 1 place, so reported as "cannot tell" | SIMULATED from the DERIVED edit size |

The 90% pairwise target in `docs/MISSION.md` sits only 0.01 to 0.04 under the single-pull ceiling. Reaching it would need near-perfect knowledge of Google's settled order, which no history-free method has shown, and it is far above every published result.

### 2.9 The new-site model, and why reach is easier than order

Product 1 asks a different question from the one sections 2.3 to 2.6 answer. It does not ask "which of two pages that both made page one is higher". It asks "does this site make page one, page two, or neither, on this search".

- **Less range restriction.** Inside a top 10 every signal is squeezed (section 2.3). Between pages that rank and candidates that do not, the gaps are wide. SIMULATED: a signal that holds half the score orders 0.72 of pairs over the whole candidate pool and 0.58 inside the top 10. A page-one boundary already shows it in our data: one free link count orders 0.633 of pairs that straddle position 10 and 0.517 of pairs inside the top 10 (REAL, design day).
- **Topical fit does real work.** Which of a site's searches it ranks for at all depends first on whether it has a page about the search. That is what the content scorer measures, and across sites and searches it is not range restricted.
- **The site part still dominates who gets in.** At R = 3 it holds about 75% of the score among candidates (DERIVED). For a site with no history the model sees that part only through free outside signals. That is the weak point of Product 1, and no theory removes it.

**The band model.** For site s and search q, with the site treated as new:

    P(band of s on q) = OrderedLogit( a * m_best(q, s)          best content score among the site's pages
                                    + c' cold(s)                site strength from outside signals only
                                    + d' comp(q)                strength of the sites now on pages 1 and 2
                                    + e * E[rank among the listed rivals]   from the pairwise model of 2.4
                                    + g' flags(q, s) )          brand in the search, site type, page count

It is fitted on (site, search) cells of real sites, including the many cells where the site does not rank. Those cells are what a pairwise model fitted inside result lists never sees, and they are why the pairwise coefficients cannot be reused as they are (the scale of a coefficient changes 3.6 times between designs, section 2.4).

**Where the PREDICTED numbers of section 0.2 come from.** They are the lead's arithmetic, not a measurement. Assume the model separates page-one cells from the rest on targeted searches with an area under the curve of 0.70 to 0.82 (the published cross-site results and our boundary number suggest that range), and that about 1 targeted cell in 9 is a page-one cell. For two normal score distributions that gives 0.26 to 0.50 of "page 1" calls right when 0.30 of the real ones are found. The sellable bar (0.70 right while finding 0.40) needs an area of about 0.90. Nothing published on free data comes close to that.

### 2.10 What each product can honestly claim

**Product 1, a site with no history.**
- Per search: a band (top 3, 4 to 10, page 2, not ranking) or "cannot tell", and per site the expected number of searches in each band.
- It is a forecast of the settled rank of a site like this one. It says nothing about how many months that takes.
- If Checkpoint P only reaches the continue bars, the output is a triage list ("searches ranked by chance of page one") and is named that. Bands are shown only if the sellable bars hold.
- A site that is live and has Search Console connected does not need the forecast for searches where Google already shows it: there the tool reports the real position.

**Product 2, a page that ranks (anchored).**
- Illustrative only, nothing was run: "This edit is likely (70%) to beat leaving the page as it is. Expected change: 0 to 2 places better. Normal movement at this position: within 2 places on 8 days in 10."
- Most body-only rewrites will get "cannot tell" (section 2.7).
- Today a full anchor exists only for pages in the top 10 of the 100 stored construction searches. At 11 to 30 the anchor is one pull and the band is untested. 401 of the client's 429 Search Console searches have no stored list at all.
- **New page on a site that already ranks for the search (sibling anchor).** Placed against the site's own ranking page, with a wider band.
- **Cannot tell.** Shown when the chance that the edit helps is between 0.34 and 0.66, the edit is not material, a sanity guard fires, the page cannot be read, or the search has no stored list.

Neither product will say: an exact position, a guarantee, or that an edit "will" move rank. No edit has been scored against Google yet, and no gate of this plan can do that before Dec 17.

---

## 3. Rules for every stage

1. **Freeze before scoring.** Each test's split, metric, threshold, code and model go into `evals/preregistered/claude-<name>/` with a sha256 manifest (`scripts/claude/freeze.py`), committed and pushed before any held-out data is pulled or read. A test is scored once. A result file is never overwritten. One exception to the order is written down in group 3D: fresh lists pulled before the models exist stay sealed until the models are frozen. **Gates on design data are frozen too** (G2, G4.1 to G4.4). Before the first gate run of stage 2, freeze `evals/preregistered/claude-r4-site/`. Before the first gate run of stage 4, freeze `evals/preregistered/claude-r4-editsim/`. Each folder holds the code and every choice a gate depends on, written as a number or a rule: the pair set, missing-value encoding, signal directions, ridge values, cells, horizons, the case generator and its seed, and which estimate the product uses. A gate detail that is in neither `gates.json` nor the stage's frozen folder makes that gate's result VOID.
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
17. **New-site rule (Product 1).** When a site is scored as new, nothing derived from its own rankings may reach the model: no footprint (S1), no domain effect, no row of the site in any list on any day. Its rows are removed from every list before the rivals' order and the competition features are formed, so no gap marks where it was. Its pages come only from the label-blind crawl of task 2A.6, never from the list of pages that ranked. Rivals keep their own histories: at prediction time they are known sites. The sites of the round 3 test2 fold are scored as new exactly once, in Checkpoint P, and contribute nothing to any fit before that.
18. **Product gates outrank part gates.** Checkpoint P (section 7) and Gate E (section 8) are the only gates that say whether something can be sold. G1 to G4 check parts. A report never presents a pass of a part gate as a product result.

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
| Counts only of the test2 sites' placements by band in the Nov 16 deep lists (67 on page one, 84 on page two). No model or signal was scored on them | this review, section 1 |

No model with the stage 2 site features has been fitted on any of it. History-based cross-site models and footprint-type transfers have been read. Because of these reads, every test on stored lists dated Nov 19 or earlier (Checkpoints 2, 3a and 4) is PROVISIONAL. Only data dated Nov 20 or later counts as unseen: the fresh lists of group 3D (which fit the free balance only under D1 option B) and the collector days of Checkpoint 3c. Checkpoint P-sealed scores sites that no fit has seen, but on these same stored lists.

---

## 4. Stage 0: pre-flight (no credits, half a day)

**Inputs:** this file, the branch, the Serper balance.

**Tasks:**

| # | Task | Output |
| --- | --- | --- |
| 0.1 | Confirm the branch is level with origin (`git status -sb` shows no "ahead") | pushed branch |
| 0.2 | Write every gate of Appendix A into `evals/preregistered/claude-r4-gates/gates.json` in machine form: gate id, metric key, comparison, value, row-level file, interval type. Build the manifest with `freeze.py`, commit, push, and list the commit in `VERSIONS.json` in the same folder (append only). This happens before the stage 1 pulls, so the advance rules cannot drift. A later version of `gates.json` is allowed only for a rule 6 or rule 8 check, before that stage's first gate run, and it must name the check that forced it. `r4_gate.py` refuses a version unless every bar is equal or stricter and every interval equal or wider than in version 1 | frozen gate file |
| 0.3 | Build `scripts/claude/r4_gate.py <checkpoint>`. It contains no threshold of its own. Each checkpoint has one named producer script. The producer's result file holds a provenance block: HEAD commit, clean-tree flag, command line, sha256 of the inputs, of the row-level file (`pairs.csv` or `rows.csv`) and of the manifest. The gate script recomputes every gated number from the row-level file with the frozen `r4_stats.py`, checks the provenance against git, and never trusts a summary field. Every commit that touches `gates.json` or its manifest must be listed in `VERSIONS.json`, or the script prints REFUSED | gate script |
| 0.4 | Credit check (section 9). Read the balance B and the other users' run times from `serp_raw` (on Nov 17 to 20: a 15-credit tracker at about 09:10 ET, then three 30-credit collector pulls between about 09:30 and 11:00 ET). A pull may start only if all three hold. (a) D1 is answered and names this pull. (b) For the stage 1 pulls B is at least 478: the 260 reserve inside `r3_pull.py`, 208 for the pulls, 10 for charges with no stored response. For the fresh lists of group 3D, B is at least 260 + 2N + 46. (c) The whole pull falls in one clear window: it starts after the other users' last run of the day and ends before their first run of the next day, so for stage 1 no other spend can fall between pull A and pull B. If pull B is refused or comes more than 6 hours after pull A, `r3_score.py` can never score the test, the held-out sites are spent, and 104 credits are lost | balance note in `RESULTS-claude.md` |
| 0.5 | Run `python -m pytest` and record the counts and the ids of the failing tests in `evals/claude/r4/baseline_tests.json`. Expected: the six known DNS failures in `tests/test_layered_fetch.py` and no other failure | baseline test record |
| 0.6 | Housekeeping from the inventory: use `index.sqlite`, not the parquet files (no `pyarrow` in the venv). Note that `r3_pages.load_pages` writes a cache under `/workspace/ranksim-data`. Fix the dead citation in theory R3.8 (`t1-postmortem/notes.md` does not exist; point to `results.md`). D6 decides the untracked script | notes |
| 0.7 | Write `scripts/claude/r4_c1_fallbacks.py` before the stage 1 pulls and freeze it with `gates.json`. It reads only `heldout/claude-r3-samesite/pairs.csv` and `heldout1.slices.json`, and uses `r3_model.accuracy` and `r3_model.difference` with the frozen seed and draws. It writes `c1_fallbacks.json`: the unfitted cross-encoder's accuracy, sites and pairs; cross-encoder minus BM25F; and, on the construction consensus pairs, `page-blend` accuracy, sites, pairs and `page-blend` minus BM25F, all with two-way intervals. The frozen `r3_score.py` writes none of these, so without this script the fallbacks of stage 1 would be computed by code written after the result is open | frozen fallback script |

**Tests:** `tests/test_claude_r4_gate.py`: the gate script fails closed on a missing field, reads thresholds only from `gates.json`, refuses an unverified manifest, refuses a hand-edited summary field, refuses a commit to `gates.json` that `VERSIONS.json` does not list, refuses a weaker later version, and prints VOID on a second run that the run log does not allow. `tests/test_claude_r4_c1_fallbacks.py`: right numbers on a synthetic `pairs.csv`.

**Gate G0 (all must hold):** branch pushed. `freeze.py verify evals/preregistered/claude-r4-gates` passes and its commit is in `VERSIONS.json`. The three conditions of task 0.4. No test fails that is not in `baseline_tests.json`.

**If G0 fails:** on credits, stop and raise D1. Stage 1 does not start on a guess. On tests, fix the cause before any freeze.

---

## 5. Stage 1: freeze the same-site content scorer and, if D1 gives it the credits, score it on unseen sites (Checkpoint 1, a part check)

**Purpose.** A check of one part, not a product gate. It was James's first priority on Nov 19. On Nov 20 he said that pair accuracy is not what anyone pays for, so D1 now decides whether this test gets the last free credits. The scorer is fitted but not frozen and not scored. Everything is designed and reviewed (`evals/preregistered/claude-r3-samesite/preregistration.md`). This stage only executes it. The preregistration is not changed.

**What runs in any case, at 0 credits:** group 1A (the freeze) and group 1B (the PR 45 ledger). **What needs D1:** groups 1C to 1E (the pulls and the scoring). If they do not run, G1 is reported as NOT RUN, the frozen scorer is used as it is with the label "not scored on unseen sites", and stages 2 to 4 proceed. Checkpoint P then tests the scorer end to end on sites it never saw.

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

**Purpose.** Estimate, for every domain in the niche, a site-strength value with an honest uncertainty, from free data, and prove on stored lists that it carries real information where site strength matters: at the page-one boundary, and for small sites that no global list covers. For Product 1 it must also give a value for a site with no rank history at all: the **cold estimate**, built from outside signals only. Zero credits. No waiting on Google.

**Inputs:**
- Stored ordinary lists through 2026-11-19 for the 140 construction searches (c100, the 35 PR 41 baseline searches, the 4 ClockShark searches), via `scripts/claude/rsdata.py` with `max_date="2026-11-19"`. The 15 revenue searches are a different niche: never used for fitting, reported as a separate slice.
- The Nov 16 deep lists (positions 1 to 30, source `smartcrew-index-deep:2026-11-16:abspos`). Trap: never take deep positions from the index `candidates` table.
- The frozen content scorer from stage 1 (or its F1-A replacement).
- The page store and the local index (`/workspace/ranksim-data/index/index.sqlite`).
- Free outside files, all reachable from this box on Nov 20: Common Crawl domain graph release `cc-main-2026-aug-sep-oct` (domain edges 7.1 GiB, vertices 942.6 MiB, ranks 2.54 GB), Majestic Million and Tranco (already on disk in `/workspace/kw-sample-2026-11-13/`), Cisco Umbrella, the public CrUX top list, Wikidata through QLever, Wikipedia APIs, Google autocomplete, RDAP.

**Outputs:**
- `evals/claude/r4/plans/xs_split.json` (frozen split) and `evals/claude/r4/site/site_types.tsv` (frozen site types).
- `evals/claude/r4/site/signals.parquet` or `.csv.gz`: one row per (domain, left-out search group) with every signal, its source and its as-of date.
- `evals/claude/r4/site/site_strength_cold.csv.gz`: per domain, the cold estimate and its standard deviation, each from a fit that never saw that domain (rule 17).
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
6. `scripts/claude/r4_site_crawl.py`: the **label-blind site crawl**. Product 1 scores a site from its own pages, and the local index holds mostly pages that were collected because they ranked, which would leak the answer. So every non-platform site of the niche is crawled the way a client site would be: the home page, the sitemap (all URLs if there are at most 300, else a seeded sample of 300 plus, for each stored search, up to 3 URLs whose path shares the most folded tokens with the search), and the pages the home page links to. Plain HTTP, one request a second per site, no page of the client's site (rule 2), results stored append only under `/workspace/ranksim-data/external/site_crawl/` with a sha256 list in git. The URL list of each site is fixed by this rule alone. No ranked URL is added to it. Free, no credits.

**Group 2B. Signals.** One script per family, each with a loader that keeps missing as missing:
`r4_signals_local.py` (S1, S3d, S7, S8), `r4_signals_cc.py` (S2, with resumable downloads, `curl -C -`), `r4_signals_focus.py` (S3a to S3c), `r4_signals_lists.py` (S5, reusing `src/aeo/ranksim/offpage.py`), `r4_signals_brand.py` (S4, S6, polite rate, results cached, the client's domain filtered out unless a D4 flag file exists).

**Group 2C. Single-signal table (design, XS-design only).** Commit `signal_spec.json` first, so no direction or encoding is chosen after seeing this table. Then `scripts/claude/r4_stage2_eval.py signals`: each signal alone on the fixed pair sets of rule 7 (1, 0 or one half), with correct, wrong, ties and missing shown, by pair type, with and without platforms, on small-site pairs, and on URL-new pairs (rule 4). A signal with a "not listed" flag appears twice: missing as missing, and unlisted ranked last. This is the baseline table for the gate, and the best single signal is the best row of it on the gate's pair set. It also reruns the recount of section 1 so the old 0.38 to 0.42 figures are settled in a committed script.

**Group 2D. The site-strength model.** `scripts/claude/r4_site_model.py`: the step 2 model of section 2.4 without the page-level content term (the site-only model: S1 to S8, `zbar`, domain effects). Fixed choices: logistic link, all signals enter (no selection), each signal standardised on XS-design, ridge on domain effects with `tau^2` chosen by nested leave-group-out log-likelihood over a grid written in the freeze, one ridge value on the other weights written in the freeze. Statistics helpers in `scripts/claude/r4_stats.py` (dyadic variance, two-way bootstrap, coverage simulation, power), ported from the appendix of `r4-statistical-design.md`.

**Group 2D-cold. The cold estimate.** The same script fits a second model for sites scored as new: signals S2 to S8 only, no footprint (S1), no domain effect for the target. For a site scored as new, the page-based signals (S3a to S3d) are computed from its label-blind crawl (task 2A.6), never from the local index, which holds mostly pages that ranked. It is fitted by leave-site-out folds over the design sites (round 3 train and test1 sites), so each site's cold estimate comes from a fit that never saw it. The test2 sites get theirs from the fit on all design sites, and no test2 label is read. A brand-new domain has near-zero link counts and no listing, and the estimate must say so with a wide standard deviation.

**Group 2E. Checkpoint 2.** Before the first gate run, freeze `evals/preregistered/claude-r4-site/`: `r4_site_model.py`, `r4_stage2_eval.py`, `r4_stats.py`, `xs_split.json`, `site_types.tsv`, `signal_spec.json`, the signals file, and a config that fixes every open choice (the gate's pair set, ridge value, tau grid, pair weights, missing handling, list choice). Commit and push. Then `r4_stage2_eval.py gate`, once: nested leave-group-out cross-validation over XS-design. Every prediction for group g comes from a model that never saw g, with features that leave g out. The gate refuses to run if the manifest fails or `stage2/results.json` exists, locally or on the pushed branch. Then `r4_gate.py c2`. XS-test-stored is not opened in stage 2. Checkpoint 2 is a design cross-validation on lists that earlier work has read. It is reported as PROVISIONAL and never as accuracy on unseen data.

### Tests

- `tests/test_claude_r4_split.py`: same seed gives the same folds, no group in two folds, the largest group is in XS-design, test2 sites carry no training weight.
- `tests/test_claude_r4_xsdata.py`: a row after the seal date raises. Pair types are right at positions 10 and 11. `correct + wrong + ties = pairs`, and a tie is never counted as right or wrong. Deep positions never come from the index table.
- `tests/test_claude_r4_sealed.py`: a planted client row and a planted PR 45 row, both fetched after 2026-11-20T01:00Z, are dropped by every loader that stages 2 to 4 use. A row of a search that only contains a PR 45 brand token is dropped too. The drop ends only when the ledger manifest verifies.
- `tests/test_claude_r4_signals.py`: planted-leak test (a synthetic domain that ranks only in group g has footprint 0 for g). As-of date test. URL-new rule: a URL listed in another group is not a gated row, and its rows do not count in its domain's footprint. A domain missing from a global list stays missing and gets its flag. The outbound request list never contains the client's domain without the D4 flag.
- `tests/test_claude_r4_content_scores.py`: list invariance (score unchanged when other pages are added or removed), the agreement check of task 2A.4 on a fixture, and an out-of-fold check: no row in `content_oof` was scored by a fit that saw its site or its search group.
- `tests/test_claude_r4_newsite.py`: the new-site rule. For a site scored as new, the feature row is identical whether or not the site's rows are in the lists. Removing the site leaves the rivals' ranks consecutive. The crawl list of a site is the same with the stored lists deleted. No test2 site appears in any training table.
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

Reported next to the gate, not gated: the cold estimate on the same boundary pairs with every design site scored as new (PREDICTED 0.55 to 0.62, lead's estimate: this is the number that matters for Product 1, and Checkpoint P gates it); the same model on all boundary pairs (URLs seen elsewhere included), inside the top 10 (PREDICTED 0.53 to 0.58, an honesty check on section 2.3), inside 11 to 30, with and without platforms, by search type, and on the revenue niche.

**PREDICTED (lead's estimates, not computed):** G2.2 point 0.62 to 0.68 on URL-new pairs. The best single signal is expected at 0.62 to 0.66 and is likely the footprint itself. G2.3 point 0.56 to 0.64. Chance that all of G2 pass: about 0.4. The main risk is that the model adds nothing measurable over the best single signal.

### Fallbacks

| Case | Next step |
| --- | --- |
| G2.2 fails only on the paired difference against the best single signal | FAIL, fallback taken: the best single signal becomes the site-strength value, with the same shrinkage. Stage 3 runs with it. Say plainly that the model added nothing over it |
| G2.3 fails (small sites cannot be told apart) | For a small site with no footprint, the no-anchor mode says "cannot tell: site not established for this topic". Product 1 will then very likely miss its continue bars, and the stage 2 report says so before stage 3 starts. Raise D2 with James |
| G2.2 lower bound at or under 0.60 with every signal | One loop allowed, and it must change the theory: move the test to "does the site reach the top 10 at all" as a per-domain classification, which is the form Checkpoint P uses anyway. If that also fails, stage 3 runs content plus the domain prior only, and the no-anchor mode is tiers only |
| G2.0 fails | Use the logistic part alone as the content score, as task 2A.4 says |

---

## 7. Stage 3: the band model for a new site, and the cross-site parts behind it (Checkpoints P, 3a, 3c, 3b)

**Purpose.** This stage holds the test of Product 1. It combines the frozen content score with the stage 2 site strength, turns them into a rank band for a site with no history on each of its searches, and measures that on real sites the model never saw (Checkpoint P). It also checks the parts: pairwise order across sites and the placement of one page among known rivals (Checkpoints 3a, 3c and 3b).

**The tests, most important first:**

| Checkpoint | What it tests | Data | Credits | Status of the result |
| --- | --- | --- | ---: | --- |
| **P, the Product 1 test** | Bands for a whole site scored as new, across all stored searches | P-cv: every design site in turn. P-sealed: the 145 test2 sites, once | 0 | P-cv is a design cross-validation, PROVISIONAL. P-sealed uses sites no fit has seen, on lists earlier work has read |
| P-fresh and 3b | The same on unseen result lists | 100 new searches, pages 1 and 2 | 200 | unseen. Runs only under D1 option B, or with new credits (D3) |
| 3a, stored part check | One page placed among known rivals, on search groups never used in fitting | XS-test-stored, Nov 16 deep lists | 0 | PROVISIONAL |
| 3c, forward part check | Pages new to a top 10 on later days. This is GOAL Target 1 | collector days dated Nov 20 or later that are already in the store | 0 | unseen in time, same searches |

**Inputs:** stage 2 outputs, XS-design lists, the frozen content score with `content_oof` on stored rows, the multi-pull days (for the noise model).

**Outputs:**
- `evals/claude/r4/model/rank-v1/` (the frozen combined model: kappa, weights, domain effects, tau, the reach model, the position curve, the band quantiles, with a save and load API like `r3_model.FrozenScorer`).
- `evals/preregistered/claude-r4-xs/` (preregistration, `test.json`, manifest) for the part checks, and `evals/preregistered/claude-r4-newsite/` for Checkpoint P.
- `evals/claude/r4/stage3/newsite/{cv, sealed, fresh}/{results.json, cells.csv, report.md}`: one row per (site, search) cell with the true band, the four band probabilities, the targeted flag and the fold.
- `evals/claude/r4/plans/xs_fresh.jsonl` (the fresh-search plan) and its meta file, only if 3b runs.
- `evals/claude/r4/stage3/{stored, forward, fresh}/{results.json, rows.csv, pairs.csv, report.md}`.

### The three tasks of the part checks

Tiers are the same everywhere in this plan: 1 to 3, 4 to 10, 11 to 20, 21 to 30, beyond 30.

- **T-A, placement.** For each test search and each readable, non-platform page in its list: hide that page, and predict its position from its content score and its site strength. The model receives the rivals as an order (ranks 1 to n-1 among themselves). It never receives their absolute positions or the list length with the gap, because the one missing number would be the answer. The site strength uses no list of this search group. Output: posterior median position, 50% and 80% bands, and probabilities for the tiers 1 to 3, 4 to 10, 11 to 20, 21 to 30. **Gated rows** are targets with no other page of their domain in the list and with a URL that is new under rule 4. That is what a new page with no anchor looks like. Rows with a same-site page in the list are scored as mode B (sibling anchor) and reported apart. Rows with URLs seen elsewhere are a secondary reading.
- **T-B, reach.** For each test search and each niche domain that has a readable indexed page: the probability that the domain appears in the search's top 10, and in its top 30. The candidate page is the domain's top page in a stored site-restricted list for the search where one exists, else its indexed page with the highest content score, the same rule for every model. The log of the domain's indexed page count is a covariate in every model and baseline, because the best score of many pages is higher by chance. This task covers the "beyond 30" outcome that T-A cannot see: T-A only has rows for pages that did reach the list.
- **T-C, order from scratch.** All cross-site pairs of the list: which page is higher. Reported for all pairs, boundary pairs, inside the top 10 and inside 11 to 30, scored per rule 7.

### Task groups

**Group 3P. The band model and Checkpoint P (the Product 1 test).** `scripts/claude/r4_newsite.py`.
1. **Cells.** A cell is one (site, search): a non-platform site of the niche that has a crawl (task 2A.6), and a stored construction search. The truth is the site's best position in the stored list of that search, as a band: top 3, 4 to 10, page 2 (11 to 20), not ranking (not in the top 20). The 100 searches with a deep list give all four bands (label day Nov 16). The 40 searches with page one only give "page one or not" and enter P1 only. The client's site and the PR 45 searches follow rule 2.
2. **New-site rule.** In every cell the target site is scored as new (rule 17): its rows are removed from the list, it has no footprint and no domain effect, and its pages are the crawled ones.
3. **Features.** (a) The best list-invariant content score among the site's crawled pages for the search, and the number of its pages above a frozen score level. Sites the scorer trained on get out-of-fold scores (task 2A.4). (b) The cold site strength and its standard deviation (group 2D-cold). (c) The competition on the search, from the list without the target: the median and the lowest site strength on page 1 and on page 2 (rivals are known sites, so their full stage 2 estimates are used), and the share of small sites and of platforms on page 1. (d) The target's expected rank among the 20 listed rivals from the pairwise model of group 3A, using the cold strength. (e) Flags: the search names the site's brand, site type, log page count.
4. **Model.** One ordered logit over the four bands, with a page-one-or-not term for the searches that have page one only. One ridge value, written in the freeze. One temperature fitted on left-out predictions. Fitted on the design sites (round 3 train and test1 sites) and the XS-design searches.
5. **Targeted cells.** A cell is targeted when the best content score is at or above a frozen level. The level is set on design sites so that targeted cells keep 90% of their real top-20 cells. In the cross-validation it is set inside each training fold. Every bar is on targeted cells. The same numbers on all cells are reported next to them.
6. **Calls.** The called band is the most probable one. The call is confident when that probability is at least 0.5, else the output is "cannot tell". Coverage is the share of targeted cells with a confident call.
7. **Readings.** P-cv: 5 folds by site (seeded), every design site scored by a fit that never saw it, XS-design searches. P-sealed: one fit on all design sites, frozen, then the 145 test2 sites scored once on all 140 searches, reported apart for XS-design searches (unseen sites) and XS-test-stored searches (unseen sites and unseen searches). P-fresh, only if the fresh lists of group 3D exist: every site scored on the fresh searches by a fit that never saw it. Before P-cv runs, freeze `evals/preregistered/claude-r4-newsite/` with the code, the crawl list, the content-score level rule, the ridge value, the fold seed and the call threshold. One gate run each, logged (rule 13).
8. **Baselines on the same cells.** B0: always "not ranking". B1: difficulty only, the same model with features (c) and site type alone, so every site gets the same answer on a search. B2: B1 plus the cold strength. B3: B1 plus the content score.
9. **Interface, built only if the decision rule says continue.** `python scripts/claude/r4_newsite.py score-site --site URL --searches FILE`: crawls the site, reads the stored list of each search, and writes one record per search (the four band chances, the called band or "cannot tell", the reasons) and one per site (the expected number of searches in each band), every number labeled PREDICTED. It never pulls. A search with no stored list returns `no_list`.
10. **Slices, each with its count.** Small sites (in neither Majestic nor Tranco). Young domains (registered under two years ago, where RDAP gives a date): the closest stored stand-in for a brand-new site. Site type. Search type. A slice with fewer than 20 sites is descriptive only. If the young-domain slice is that small, the report says plainly that brand-new sites are untested.

A known bias, on the safe side: a held-out site's crawl is capped, so it can miss the page that ranks. A client's own site is crawled in full. The test therefore finds fewer real rankings than the product would.

**Group 3A. The combined model.** `scripts/claude/r4_rank_model.py`: the full step 2 model of section 2.4, fitted once on XS-design (one list per search), with `content_oof` scores on stored rows. Fixed choices as in 2D. Report `kappa` with a search-group bootstrap interval. Also fit `kappa` a second way, from same-site pairs of ordinary lists only (a conditional logit on the content score), and report both. If they differ by more than a factor of 2, say so: content and site are then not on one scale. The same script fits the **reach model** for T-B, because the pairwise model has no page that failed to rank and so cannot learn reach: a logistic classifier on XS-design (domain, search) rows. Positives are domains in the top 10 (and, in a second fit, the top 30) of the Nov 16 deep list. Negatives are niche domains with a readable indexed page that are absent from that list. Features: the combined model's score for the candidate page, the stage 2 site strength, and the log page count, all leave-group-out. It is frozen with `rank-v1`. This reach model is the known-site reading of the cells of group 3P: the same cells and code with the site's footprint switched on. Sites of the test2 fold are never targets in group 3A, 3C or 3E, so Checkpoint P-sealed stays unread.

**Group 3B. Positions.** `scripts/claude/r4_position.py`:
1. Position curve and pull noise: fit the jump-mixture noise model (a page lands at a random place with probability q, otherwise gets Gaussian jitter) to pair agreement by position gap, from the multi-pull days of XS-design searches. This gives the latent spacing of positions 1 to 10. Positions 11 to 30 are extrapolated on the log-rank form of section 2.7 and flagged as extrapolated until repeat deep pulls exist.
2. Calibrated placement: inside each search, regress the rivals' latent positions on their model scores (a pooled slope with a per-search intercept), place the target at its fitted value, and take the posterior median. The rivals' positions here are their ranks among themselves, as T-A requires.
3. Bands: split-conformal error quantiles from leave-group-out placements on XS-design, by tier of the predicted position. The noise model alone never sets a band.
4. Expected rank `1 + sum P(rival beats page)` is computed as a cross-check. Reuse `src/aeo/ranksim/position_aggregate.py` where it fits.

**Group 3C. Checkpoint 3a, stored test (0 credits, PROVISIONAL).** Freeze the model and the preregistration `evals/preregistered/claude-r4-xs/`. Before the freeze, compute the power of each gate at the PREDICTED effect for the real size of XS-test-stored, by simulation from the XS-design errors. If power is under 0.8, the gate stays as written and the report says a fail is uninformative. Then score XS-test-stored once with `scripts/claude/r4_stage3_score.py --cell stored`. T-A and T-C use the deep-list searches only. The top-10-only searches of the fold (PR 41 baseline and ClockShark searches) are a separate reported slice. If the model is refitted afterwards, the stored cell is design data from then on. This is the main part check of the cross-site model.

**Group 3D. Fresh lists, for P-fresh and Checkpoint 3b.** These are the only unseen result lists this plan can get. They fit the free balance only in place of the stage 1 test (D1 option B), or with new credits (D3). After a stage 1 run the size rule below gives a negative N and the group does not run.
1. `scripts/claude/r4_stage3_plan.py`: candidate searches are the related searches and "people also ask" strings in stored raw responses dated on or before Nov 19 (1,028 and 542 distinct strings). Eligibility is computed for each candidate against the stored searches only, never through other candidates. With t = `r2_samesite_pairs._fold_tokens`, a candidate c is excluded if, for any of the 155 stored searches s, the token sets of t(c) and t(s) are equal, they share a token trigram, or one token set contains the other (any size, including one-token stored searches). It is also excluded if it contains a brand token from a frozen list (every brand named in a stored search, the client's brand, and every PR 45 brand token) or a navigational word from a frozen list (login, sign in, app, download, careers, glassdoor, support, reddit, complaints). It must contain a niche term from a frozen list. All three lists are written to the plan's meta file before sampling. Fresh groups come from `assign_folds` over the eligible candidates alone. The 71 universe groups are never recomputed. Draw by seeded hash, at most 2 searches per new group. The eligible list and its count are written before the draw.
2. Size, fixed by the balance B on the day of the pull: `N = min(100, floor((B - 260 - 46) / 2))`. The 46 keeps 36 credits for the two PR 45 reads and 10 for charges with no stored response. If N is under 30, the group is not run, and that is reported as "not tested on unseen lists".
3. Freeze the query list and the pull script and push. Then pull pages 1 and 2 of each search once, both within minutes (a small `r4_stage3_pull.py`; chunked, each chunk committed and pushed, the balance read before every chunk). 2 credits per search. Page 3 is not pulled: the bands of Product 1 end at position 20.
3b. **Pulling before the models exist.** Under D1 option B the pull happens tonight, before stages 2 and 3 are built, because the credits will not be there later. That breaks the usual order of rule 1, so the lists are sealed instead: the pull script stores raw responses only and prints counts, and `r4_sealed.py` refuses every read of them until the manifests of `claude-r4-xs` and `claude-r4-newsite` verify. The audit of rule 15 checks that no script read them earlier.
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
- `tests/test_claude_r4_newsite_gate.py`: on a synthetic world with a known answer the band model beats baseline B1 and its P1, P2 and P3 match hand-computed values. Shuffling site labels inside each search brings P1 to the base rate. No cell of a site is scored by a fit that saw that site. No test2 site is in any training table. A second run of a reading is refused.
- `tests/test_claude_r4_plan.py`: `raken alternative free`, `raken pricing per month`, `raken time tracking` and `busybusy login` are not eligible. `universe.json` is unchanged. The draw is deterministic. N follows the formula.

### Gate P (Checkpoint P, the Product 1 gate)

All numbers are on targeted cells, with a bootstrap over sites for every interval.

| Id | Measure, exactly | Continue bar | Sellable bar |
| --- | --- | --- | --- |
| P1 | Page one or not. Rank the cells by the predicted chance of page one and take cells from the top until the stated share of the real page-one cells is found. The measure is the share of taken cells that really are on page one | at least 0.35 when 0.30 are found, and log loss of the page-one chance below baseline B1 with a 95% lower bound of the difference above 0 | at least 0.70 when 0.40 are found |
| P2 | Right band. Over cells whose true band is top 3, 4 to 10 or page 2: the share whose called band is the true band. "Cannot tell" and "not ranking" count as wrong | at least 0.25 | at least 0.60 |
| P3 | Spread. Over sites with at least 5 targeted cells: the predicted number of page-one cells (the sum of the chances) against the real number | rank correlation across sites at least 0.4 | within 25% or 2 searches of the real number for at least 0.70 of sites |
| P4 | Coverage: share of targeted cells with a confident call | reported | at least 0.80 |
| P5 | Guard tests: the new-site tests of stage 2, and a shuffle of site labels inside each search, which must bring P1 down to the base rate | all pass | all pass |

Always reported next to the gate: the plain share of cells placed in the correct band, with the same number for B0, B1, B2 and B3; the area under the curve for page one; the all-cells versions; the slices of task 10; the count of cells and sites behind every number.

**Decision rule, fixed now.**
- **Kill.** P1 or P2 misses its continue bar on P-cv. Product 1 stops on free data. No second model is tried on the same data beyond the one loop of rule 13. The pivots are in section 0.2.
- **Continue as a triage tool.** The continue bars hold on P-cv, and P-sealed agrees (its point estimates clear the continue bars, or the bars lie inside its 95% intervals). The output is a list of searches ranked by chance of page one. No bands are shown.
- **Sellable, pending unseen lists.** The sellable bars hold on P-cv and at the point estimate on P-sealed. Then the fresh lists are requested (200 credits). Bands are shown, and the numbers are quoted to anyone outside, only after P-fresh meets the sellable bars at the point estimate with its lower bounds above the continue bars.

P-sealed is small: 67 page-one placements on 35 sites and 84 page-two placements (REAL). It can contradict P-cv. It cannot carry a sellable claim alone.

**PREDICTED (lead's estimates from the arithmetic of section 2.9, not computed):** P1 0.25 to 0.50 at the continue point. P2 0.15 to 0.35. P3 correlation 0.4 to 0.7. Chance that the continue bars hold: about 0.5. Chance that the sellable bars hold: about 0.1 to 0.15.

### Gate G3 (the part checks: applied to Checkpoint 3a as PROVISIONAL, and to 3b if it ever runs)

Rows: the gated T-A rows defined above (readable, no platform, no same-site page in the list, URL new under rule 4). Validity for 3a: at least 30 searches with a deep list and at least 200 gated T-A rows. Validity for 3b: at least 30 searches and 200 gated rows (its lists end at position 20). A test under its validity bar is reported as "not tested: too few rows", with descriptive numbers only, and is never scored against the bars. G3 passes only if G3.1 to G3.7 all pass, where G3.5a may instead be "not testable".

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

### What each outcome of the part checks means, and the fallbacks

This table is about G3 and about the no-anchor mode for a new page on a site that is already known. Product 1 is decided by Gate P alone.

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

## 8. Stage 4: the edit simulator, Product 2 (Checkpoints 4 and 5, Gate E)

**Purpose.** The secondary product: give it a search, a page that already ranks and a changed version, get the direction and an approximate new position with honest uncertainty. Validate its parts with tests that do not wait on Google (G4). Measure it on real edits when they exist (Gate E), without ever blocking on them.

**Inputs:** the frozen content scorer (stage 1 advanced by G1-ADV or a fallback, or was NOT RUN under D1 and the scorer carries the label "not scored on unseen sites"; under F1-C stage 4 does not start), the list-invariant score and `content_oof` (stage 2, group 2A), the position curve and noise model (stage 3, group 3B), the rank model for pages with no anchor (stage 3, if it passed), stored lists, the PR 41 and PR 45 material, the Search Console exports on disk.

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
| 3 | `scripts/claude/r4_virtual_edits.py` | Virtual edits: page A of a site given page B's title, then title and H1, then all text. After the full-text step the score must have moved toward B, and the movement at each step is reported. Swap pairs: one site, two pages, opposite order under two searches. Position backtest: for same-site pairs of non-platform sites in the deep lists of XS-test-stored groups, read once and only after Checkpoint 3a, predict B's position from A's anchor and the score gap. This backtest checks gaps between different settled pages. It does not test the size of an edit's effect | 0 credits |
| 4 | `scripts/claude/r4_cc_versions.py` | Common Crawl natural experiments: for training-site pages whose text changed materially between monthly captures, does today's site-restricted order agree more with the after version than the before version, and more for older changes? Unchanged pages are the control. This is the only free estimate of lambda | 0 credits, 1 to 2 weeks |
| 5 | `scripts/claude/r4_ledger_score.py` | Live edits: PR 45, PR 41, later edits | 18 credits per PR 45 read, 222 per PR 41 read (section 9). Weeks |

**Group 4E. Live confirmation, never blocking.**
- **PR 41 ledger** (the earlier team's file, never edited): read only. Outcomes are appended to a new file `evals/claude/r4/ledgers/pr41-outcomes.jsonl`.
- **PR 45 ledger version 1** (stage 1, group 1B): direction calls from the same-site scorer.
- **PR 45 ledger version 2**: the full simulator's predictions for the 6 target searches that have a before rank (raken alternative, raken pricing, busybusy alternative, clockshark alternative, b2w alternative, earthwork software), frozen in `evals/claude/r4/ledgers/pr45-v2/` as soon as the simulator passes G4. Its anchors use `as_of` 2026-11-20T01:00Z, so no pull from after the merge enters them. One of the six (raken pricing) has a single pull before the merge, so its anchor is thin. The ledger counts as blind only if rule 2 of section 3 held for every script. Otherwise it is labeled "not blind" and kept out of the pooled rate. The three other PR 45 searches were never pulled before the merge, so they have no before rank and no direction can be scored on them.
- **Score dates:** edit plus 28 days and plus 56 days (PR 41: Dec 16 and Jan 13. PR 45: Dec 17 and Jan 14), and never earlier than 7 days after Google marks the core update complete. Any earlier read is a diagnostic.
- **What a read costs.** The truth rule needs 3 pulls of every ledger search on the score date, and the other team's collector will not be running in December (section 9). A PR 45 read of its 6 searches costs 18 credits. A PR 41 read of its 74 searches costs 222 (the 6 PR 45 searches are among them). The balance left after option A or option B of D1 pays for the two PR 45 reads and nothing else. The PR 41 reads are not funded: without new credits (D3) that ledger stays unscored and the report says so.
- **Scoring rule (frozen before the first read):** truth is the consensus of at least 3 pulls on the score date. Each edited (page, search) row is compared with matched controls: the same site's unedited pages on the same searches, and the unedited rivals of the same results page. Calls, truth and hits follow Gate E below (up, down or stay, with a 2-place threshold). Rows with no call are reported with their share and left out of the rate. Each edited row also gets a reprocessing marker: whether the title or snippet Google shows on the score date contains text found only in the after version. Rows Google has not yet reprocessed are reported apart and left out of the hit rate.
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

### Checkpoint 5 (live reads, on the calendar, never a release gate)

| Date (not before 7 days after the core update completes) | Read | Credits | Funded today |
| --- | --- | ---: | --- |
| Dec 17 | PR 45 plus 28, 6 searches, 3 pulls | 18 | yes, from the balance left after stage 1 |
| Jan 14 | PR 45 plus 56, 6 searches, 3 pulls | 18 | yes |
| Dec 16 and Jan 13 | PR 41 plus 28 and plus 56, 74 searches, 3 pulls | 222 each | no. Needs new credits (D3) |
| Each later client edit | A new ledger frozen before the edit goes live, read at plus 28 and plus 56 | 3 per search per read | no |

### Gate E (the Product 2 gate, on real edits)

G4 says the simulator is sane. Only Gate E says whether its answers match what Google did. It cannot block the release on this branch, because it waits on Google. Until it is measured, every edit prediction carries the label "unconfirmed against Google".

- **Rows.** Every edited (page, search) row of a ledger frozen before the edit went live, with a before anchor from pulls fetched before the edit, and with Google's reprocessing marker set by the score date. Rows of one search are not independent: the count of independent rows is the row count divided by 1 + (m - 1) x 0.3, with m the mean rows per search (the 0.3 is assumed, not measured).
- **Truth.** The change in consensus position (at least 3 pulls) from the before anchor to the score date, minus the median change of the matched controls. Up: a net gain of 2 places or more. Down: a net loss of 2 places or more. Stay: anything else.
- **Calls.** Up or down: the chance that the edit helps is at least 0.66 or at most 0.34, and the lower figure of the expected change is at least 2 places. Stay: the upper figure of the expected change is under 2 places. Otherwise "cannot tell".

| Id | Measure | Sellable bar | Kill bar, read after 40 independent rows |
| --- | --- | --- | --- |
| E1 | Direction: share of confident calls (up, down, stay) that match the truth | at least 0.70, and above "always stay" on the same rows | under 0.60 |
| E2 | Within 2: share of confident calls whose predicted position is within 2 places of the real consensus position | at least 0.70, and at least 10 points above "predict no change" on the same rows | not above "predict no change" |
| E3 | Coverage: share of rows with a confident call | at least 0.50 | reported |

Also reported: accuracy on the up and down calls alone, because a tool that always says "stay" would look good on E1 and E2 without knowing anything. That is why both bars are tied to the empty baselines.

The rule, the baselines and the row filter are frozen in `evals/preregistered/claude-r4-gate-e/` before the first ledger read. If the kill bar is hit, the product stops quoting position changes and keeps only the comparison of two versions. Rows come from PR 45 (about 10 independent rows, 18 credits a read), PR 41 (222 credits a read, not funded) and later client edits. With Search Console connected (D2), the same rows can be read for free from Google's own average positions, reported as a second truth source and labeled as that.

**PREDICTED (lead's estimate):** E1 0.55 to 0.65 on up and down calls. E2 close to the "no change" baseline. The 40 independent rows will not exist before February unless D2 or D3 is approved.

---

## 9. Credits and order of work

**The balance (REAL, read 2026-11-20 15:45 ET): 513.** James's floor is 250. This plan's pull scripts stop at 260, so the plan can spend 253.

**Who else spends it.** Two other users draw on the same free key every morning: the other team's collector (three pulls of the original 30 searches, 84 to 90 credits, between about 09:30 and 11:00 ET) and a tracker for another client (15 credits at about 09:10 ET). About 6 more a day are charged with no stored response. Together about 105 to 111 a day. The collector stops itself at 200, not 250. The tracker never stops.

| If nothing changes | Balance after that morning (DERIVED) |
| --- | ---: |
| Nov 21 | about 408 |
| Nov 22 | about 303 |
| Nov 23 | about 200, under the 250 floor. The tracker then keeps taking 15 a day |

**What each use costs, and what fits.**

| Use | Credits | Fits today (253 to spend) | Fits after the Nov 21 morning runs (about 148 to spend) |
| --- | ---: | --- | --- |
| A. Stage 1 test: 104 site-restricted lists, two pulls | 208 | yes, alone | no |
| B. Fresh lists for Product 1 and Checkpoint 3b: 100 new searches, pages 1 and 2 | 200 | yes, alone | in part: 51 searches. None a day later |
| A and B together | 408 | no | no |
| Two PR 45 reads (Dec 17, Jan 14): 6 searches, 3 pulls | 36 | yes, next to A or next to B | yes |
| One PR 41 read: 74 searches, 3 pulls | 222 | only in place of A and B | no |
| One client run of Product 1 on 300 searches, pages 1 and 2 | 600 | no | no |
| Stages 0, 2, 3 (Checkpoint P, 3a, 3c) and 4 up to G4 | 0 | yes | yes |

So the free balance pays for exactly one of A or B, plus the two PR 45 reads, and only if it is spent before about 09:00 ET on Nov 21 or the other two users are paused today (D1). Everything that decides whether Product 1 is worth continuing costs nothing.

**Rules for any pull (they restate task 0.4).** Read the balance just before the pull. A pull of A needs at least 478 (260 + 208 + 10). A pull of B needs at least 260 + 2N + 46. Both pulls of A, or the whole of B, must fall in a window with no run of the other users inside it: start after their last run of the day, end before 09:00 ET the next morning. Never pass `--reserve`.

**The collector is not a data source after Nov 21 to 23.** It runs out of credits by then, or sooner if it is paused under D1. No gate, fallback or ledger read in this plan depends on a collector day that does not already exist. The days that exist (through Nov 20, and Nov 21 if it runs) are used by Checkpoint 3c and by the band backtest.

**Not affordable and left out:** a repeat pull of pages 2 and 3 to measure noise at positions 11 to 30, any PR 41 read, any client run of Product 1, a fresh anchor for an edit prediction (3 credits per search per day).

**Order.**
1. Today: D1. Stage 0. Stage 1 groups 1A and 1B (freeze and PR 45 ledger, 0 credits). Then A or B tonight if D1 says so.
2. Stage 2 (about five working days, 0 credits): data layer, the label-blind crawl, signals, the site model and the cold estimate.
3. Stage 3 (about four working days, 0 credits): the band model and **Checkpoint P first**, then the part checks 3a and 3c. The kill, continue or sellable decision for Product 1 falls here, about two weeks from the start.
4. Stage 4 (about one to two weeks to G4, 0 credits): the edit simulator. Gate E waits for real edits: first read Dec 17.

If Checkpoint P hits its kill rule, stage 4 still runs (Product 2 does not depend on it), and the pivots of section 0.2 go to James.

---

## 10. What this plan does not do

- It does not make any gate wait on Google, except Gate E, which is the only honest measure of the edit product and never blocks other work.
- It does not spend money, and it does not sign up for anything unless James approves D3.
- It does not claim an exact position, or that an edit will move rank, or how fast a new site reaches its settled rank.
- It does not treat pair accuracy as a product result. The Round 3 target (G1-GOAL) is still scored as frozen if the credits go to it, and it is reported as a part.
- It does not change the round 3 preregistration, the PR 41 ledger, the product ranker, main, or production.
- It does not use rank history as a feature of the content scorer. Product 1 uses no history of the scored site at all (rule 17). Product 2 uses the page's current position as a starting point, which is known at prediction time, and every test anchors only on data from before the prediction time.

**GOAL.md targets.** Target 1 (place new or changed URLs, at least two held-out days) is scored as Checkpoint 3c with the GOAL metric. Target 2 (edit direction) is Gate E. Target 3 (one-day movers) is retired: it measured pull noise (RESULTS-claude.md 4.3), and James's Nov 19 note sets day-to-day movement aside. The Round 3 target (same-site pairs, lower bound above 0.65) is G1-GOAL. D9 asks James to confirm this map.

---

## Appendix A. Every gate in one table (source for `gates.json`)

Product gates come first. They decide what can be sold. The G gates check parts.

| Gate | Metric | Bar |
| --- | --- | --- |
| **P1** (Product 1) | page one or not, on targeted cells: share right among the top-ranked cells that find the stated share of real page-one cells | continue: at least 0.35 when 0.30 are found, and log loss below baseline B1 with a lower bound of the difference above 0. Sellable: at least 0.70 when 0.40 are found |
| **P2** | right band, over cells truly in the top 20 | continue: at least 0.25. Sellable: at least 0.60 |
| **P3** | per site, predicted against real number of page-one searches, sites with at least 5 targeted cells | continue: rank correlation at least 0.4. Sellable: within 25% or 2 searches for at least 0.70 of sites |
| **P4** | coverage: share of targeted cells with a confident call | sellable: at least 0.80 |
| **P5** | new-site tests and the site-label shuffle | all pass |
| P decision | kill, continue or sellable | kill if P1 or P2 misses its continue bar on P-cv. Continue if the continue bars hold on P-cv and P-sealed agrees. Sellable only after P-fresh |
| **E1** (Product 2) | direction on real edits: share of confident up, down and stay calls that match | sellable: at least 0.70 and above "always stay". Kill after 40 independent rows: under 0.60 |
| **E2** | predicted position within 2 places of the real one, confident calls | sellable: at least 0.70 and at least 10 points above "predict no change". Kill: not above "predict no change" |
| **E3** | coverage of confident calls on real edits | sellable: at least 0.50 |
| G0 | branch pushed; gate manifest verifies and is listed in `VERSIONS.json`; credits; tests | level with origin; verify passes; the three conditions of task 0.4 before any pull; no failing test outside `baseline_tests.json` |
| G1-GOAL | `page-blend`, consensus pairs, two-way interval | at least 40 sites and 800 pairs; lower bound above 0.65; minus BM25F lower bound above 0; minus each ranker variant lower bound above 0. NOT RUN if D1 gives the credits elsewhere |
| G1-ADV | same run, the scorer's own rate (ties and missing count one half) | valid run; at least 40 sites and 800 pairs; point at least 0.65; lower bound at least 0.60; minus BM25F lower bound above 0 |
| F1-A | unfitted cross-encoder, from `c1_fallbacks.json` | conditions 2 to 4 of G1-ADV |
| F1-B | `page-blend`, construction lists only, from `c1_fallbacks.json` | at least 25 sites and 500 pairs; point at least 0.65; lower bound at least 0.60; minus BM25F lower bound above 0 |
| G2.0 | list-invariant score against the in-list score, site-held-out fits | accuracy within 0.005; rank correlation at least 0.98 |
| G2.1 | share of non-platform top-30 domains with a real signal or own-list evidence | at least 0.80; client's domain has an estimate |
| G2.2 | site-only model, URL-new boundary pairs, fixed pair set, design cross-validation | dyadic lower bound above 0.60; minus best single signal: paired dyadic lower bound above 0 |
| G2.3 | same, small-site boundary pairs | point at least 0.58; dyadic lower bound above 0.52; point above the footprint-only score |
| G2.4 | leak tests | all pass |
| G2.5 | within-search label shuffle through the absolute bar of G2.2 | fails |
| G3 validity | size | 3a: at least 30 deep-list searches and 200 gated T-A rows. 3b: at least 30 searches and 200 gated rows. Under it: not tested |
| G3.1 | T-A mean absolute error, gated rows | at least 8% below the middle guess; paired lower bound above 0 against the middle guess and the shrunk domain prior |
| G3.2 | T-A band coverage and width | 80% band 0.72 to 0.88; 50% band 0.42 to 0.58; median 80% width at most 0.85 of the middle guess's |
| G3.3 | T-C pair accuracy per rule 7 | all pairs dyadic lower bound above 0.55; boundary pairs above 0.58; on both, minus best single signal: paired lower bound above 0 |
| G3.4 | T-A tier forecast | ranked probability skill above 0, lower bound above 0 |
| G3.5a | content matters across sites: T-C, combined minus site-only | paired dyadic lower bound above 0, or "not testable" if its power is under 0.8 |
| G3.5b | no harm from content: T-A error, combined minus site-only | two-way upper bound under 0.10 places |
| G3.6 | cold sites | T-A error at most the middle guess's |
| G3.7 | reach, known-site reading | log loss below the base-rate forecast with a lower bound above 0; not above footprint-only |
| 3c (GOAL Target 1) | pages new to a top 10 on collector days: share within 3 places, against the best of domain prior, position 7 and position 11 | interval of the difference above 0 on at least two days. One day: not decided |
| G4.1 | sanity suite, sealed half | invariance: median under 0.25 places and at least 90% no change or cannot tell. Direction: a call on at least 70%, right on at least 85% of calls, wrong on under 5% of all. Stuffing flagged 100%. Each guard fires on at most 5% of clean cases and of real material changes |
| G4.2 | coverage of the 80% band, no edit, start tier (1 to 3, 4 to 10) by horizon (1, 2, 3 days) | at least 0.72 in every cell with at least 30 searches and 3 outcome days; at least 4 such cells; narrowest-band rule unit tested |
| G4.3 | virtual edits, swap pairs, position backtest | full-text step moves toward B in at least 90% and closes a median of at least half the gap; swap lower bound above 0.5; backtest (valid with at least 5 sites and 100 pairs) direction at least 0.60 and at least 0.03 above BM25F, log-rank error below no change |
| G4.4 | `kappa_A`, lower figure | by-site lower bound above 0 with all sites and without hcss.com; valid only with at least 8 other sites of at least 5 pairs, else untested |
| G4.5 | software | tests pass except the baseline ids; byte-identical JSON on two runs; under 60 seconds |

## Appendix B. New code, by stage

| Stage | Files |
| --- | --- |
| 0 | `scripts/claude/r4_gate.py`, `r4_c1_fallbacks.py`, `evals/preregistered/claude-r4-gates/` (`gates.json`, `VERSIONS.json`, manifest), `evals/claude/r4/gate_runs.jsonl`, `evals/claude/r4/baseline_tests.json`, `tests/test_claude_r4_gate.py`, `tests/test_claude_r4_c1_fallbacks.py` |
| 1 | `scripts/claude/r4_pair_calibration.py` (everything else exists) |
| 2 | `scripts/claude/r4_sealed.py`, `r4_split.py`, `r4_sitetype.py`, `r4_xsdata.py`, `r4_content_scores.py`, `r4_site_crawl.py`, `r4_signals_local.py`, `r4_signals_cc.py`, `r4_signals_focus.py`, `r4_signals_lists.py`, `r4_signals_brand.py`, `r4_site_model.py`, `r4_stats.py`, `r4_stage2_eval.py`, `evals/preregistered/claude-r4-site/`, and their tests |
| 3 | `scripts/claude/r4_newsite.py`, `r4_rank_model.py`, `r4_position.py`, `r4_stage3_plan.py`, `r4_stage3_pull.py`, `r4_stage3_score.py`, `evals/preregistered/claude-r4-newsite/`, `evals/preregistered/claude-r4-xs/`, and their tests |
| 4 | `src/aeo/ranksim/editsim/{content, anchor, effect, guards, output}.py`, the CLI hook, `scripts/claude/r4_gsc.py`, `r4_sanity_suite.py`, `r4_band_backtest.py`, `r4_virtual_edits.py`, `r4_cc_versions.py`, `r4_ledger_score.py`, `evals/preregistered/claude-r4-editsim/`, `evals/preregistered/claude-r4-gate-e/`, and their tests |
| every checkpoint | `evals/claude/r4/audits/<checkpoint>.md`, written by a reviewer that wrote none of the stage's code |

Existing code to reuse by import, never by editing while it is frozen (rule 16): `scripts/claude/rsdata.py`, `r3_model.py` (`FrozenScorer`, `fit_predict`, `site_folds`, `twoway_interval`), `r3_features.py`, `r3_semantic.py`, `r3_dataset.py`, `r3_pull.py`, `r3_fetch.py`, `r2_samesite_pairs.py` (`_fold_tokens`, `assign_folds`), `t2_changes.py`, `freeze.py`, `metrics.py`; `src/aeo/ranksim/position_aggregate.py`, `site_history.py`, `offpage.py`, `bands.py`, `ledger.py`, `draft_ledger.py`. Do not use `src/aeo/ranksim/authority.py` (a paid service client).

## Appendix C. Words used

- **Band**: top 3, 4 to 10, page 2 (11 to 20), not ranking (not in the top 20). In the part checks that go to depth 30 the tiers are 1 to 3, 4 to 10, 11 to 20, 21 to 30, beyond 30.
- **Cell**: one (site, search). **Targeted cell**: a cell where the site has a page whose content score is at or above the frozen level.
- **Scored as new**: scored under rule 17, with no trace of the site's own rankings.
- **Cold estimate**: site strength from outside signals only, with no footprint.
- **Design sites**: the round 3 train and test1 sites. **Held-out sites**: the 145 test2 sites.
- **Site-restricted order**: the order Google returns for `site:<site> <search>`.
- **Consensus**: the median position over a day's pulls, as defined in `RESULTS-claude.md` section 0.
- **Boundary pair**: one page in the top 10, the other at 11 to 30.
- **Middle guess**: always predicting the middle position of the list.
- **Domain prior**: the site's mean position on other search groups, shrunk toward the middle position with weight n / (n + k), where n is the number of other groups the site appears in and k is chosen by leave-group-out error on XS-design and frozen. The middle guess where the site was never seen.
- **Best single signal**: the best row of the frozen single-signal table of group 2C on the gate's own pair set.
- **Decided pair**: a pair where the scorer gives two different, non-missing scores.
- **Anchor**: the page's current latent position, from at least 3 page-one pulls in total over at least 2 days. With less it is a thin anchor.
- **Transfer factor (lambda)**: how much of the settled content effect an edit actually delivers, between 0 and 1.
- **Independent rows**: rows divided by the design effect of rows that share a search.

## Appendix D. Adversarial review log

**How the review ran (Nov 20).** Five reviewer subagents on Opus attacked the draft of commit `9acc643`, one lens each: leakage, arithmetic and gate strength, credits and feasibility, hidden assumptions and missing free signals, and ways to fake a pass. Each wrote a report, kept in `evals/claude/r4/reviews/`. Five skeptic subagents on Opus then tried to refute every serious finding and the lead's own 38 candidate flaws. A flaw was accepted only if a skeptic or the lead confirmed its evidence independently. The reviewers read code and counted rows. They fitted and scored nothing on real ranking data, spent no credits and read no held-out list. The table below merges duplicates: 66 distinct objections, 61 accepted in full or in part, 5 rejected.

Ids: L leakage, B, M, m math, C credits, A assumptions, FP fake pass, LL, LM, LC, LA, LF the lead's candidates, "missed" an item a skeptic added.

### Credits and feasibility

| # | Objection (ids) | Verdict | What changed |
| --- | --- | --- | --- |
| 1 | The balance in the draft (617) was stale and the fresh test was no longer affordable after stage 1 (B1, A1, FP10, C3, LC1) | accepted | Sections 0.5, 1 and 9 rebuilt on the measured 513. The fresh pull is re-sized and fits only in place of stage 1 |
| 2 | Stage 1 fits only until the other users' next run, and a run between pull A and pull B would spoil the test for good (C1, LC2, LC3) | accepted | Task 0.4, group 1C and rule 9: bar of 478, one clear window, balance read before each pull |
| 3 | The default "run stage 1 at once" lets the shared balance fall under 250 (C2, A1) | accepted, different fix | A default of "do not run" would have lost the test for good, so D1 is now an explicit choice with a recommendation, and no stage starts on its own |
| 4 | A second user (a 15-credit daily tracker) and unmatched charges: about 105 to 111 a day, not 90 (LC5, missed) | accepted | Sections 1 and 9 |
| 5 | The live ledger reads were listed at 0 credits but need 18 (PR 45) and 222 (PR 41) a date (C4, LC4) | accepted | Group 4E, the Checkpoint 5 table, section 9. PR 41 reads are marked not funded |
| 6 | The plan asked to pause the collector and also relied on its later days (C5, LC6) | accepted | No gate depends on a collector day that does not exist. D5 (swap a collector pull) is dropped: the collector has no page option |
| 7 | The anchor rule cannot be met at 11 to 30 or for searches pulled once, and anchor age was ignored (C6, A4) | accepted | Anchor definition, thin-anchor flag, age in the band |
| 8 | A page that left the top 10 was stored as position 11 and could count as covered. Tier 11 to 30 has no rows. Cells could be merged at will (C7, FP6) | accepted | G4.2 and group 4B |
| 9 | The stored test would usually miss its own validity bar (C8, M1, FP11, LM8) | accepted, different fix | Test share raised from 35% to 50% of search groups with a count-only size check, and 3a has its own validity bar |
| 10 | `kappa_A` rests on about five sites, one of them with 60% of the pairs (C9, A9) | accepted | G4.4 validity rule. PREDICTED: untested, so the scale ships as SIMULATED |
| 11 | The fetch summary is not committed by the script, and none is written when nothing needs fetching (C10) | accepted | Group 1C step 2, group 1A |
| 12 | Stage 2 could edit files inside the round 3 manifest and block the scorer (C11) | accepted | Rule 16 |
| 13 | An agent could lower `--reserve` to force pull B through (missed) | accepted | Group 1C, section 9 |
| 14 | 66 deep lists reach position 30, not 70 (missed) | accepted | Section 1, task 2A.3 |
| 15 | Best use of the credits left after stage 1 (missed) | accepted | The two PR 45 reads of the 6 searches with a before rank |
| 16 | The collector does not finish every run and skips its floor check when the balance call fails (missed) | accepted | Balance read before every chunk. Section 9 plans on the worst case |

### Leakage

| # | Objection (ids) | Verdict | What changed |
| --- | --- | --- | --- |
| 17 | The fresh test would read client rows and PR 45 searches before ledger version 2 is frozen (L1, LL4) | accepted | Rule 2: one sealed loader, with a planted-row test |
| 18 | The "frozen linker" is a lookup table, so near-duplicate searches would enter the unseen test (L2, C12, LL5) | accepted | Group 3D eligibility rule, candidate against stored only. The result-page overlap slice is reporting only |
| 19 | The content score is in sample on most stored rows: 85 of 100 deep-list searches are in its training groups (L3, LL1) | accepted | Task 2A.4: out-of-fold scores wherever a stored row enters a fit or a gate |
| 20 | G3.5 "content matters" could not fail on held-out data (L4, M5, missed) | accepted | Split into G3.5a (a real test, or "not testable") and G3.5b (no harm) |
| 21 | Placement rows gave the model answers a new page lacks: the empty slot, a same-site page in the list, the URL's rank elsewhere (L5, LL2, LL3) | accepted | T-A: rivals as an order only, gated rows defined, rule 4 |
| 22 | The site-strength gate counted a page's own rankings in its site's footprint (LL7) | accepted | G2.2 and G2.3 on URL-new pairs |
| 23 | The stage 1 fallbacks needed numbers the frozen scorer does not write, and gave three routes to advance (L6, FP9) | accepted | Task 0.7, and the route is named in every record |
| 24 | Checkpoint 2 is a cross-validation on design data and could be rerun until it passed (L7, LL6, FP2) | accepted | Rule 1 (design gates are frozen too), group 2E, PROVISIONAL label |
| 25 | The table of earlier reads was incomplete (L8) | accepted | Section 3 |
| 26 | The seal used a date, but the PR 45 merge fell inside that date (L9) | accepted | Rule 2: a time |
| 27 | The PR 45 ledger's anchor could include pulls from after the merge (missed) | accepted | Anchor `as_of`, group 4E |
| 28 | Stage 3 claimed "sites nothing has seen" when only searches were held out (missed) | accepted | Stage 3 purpose. Then made real by Checkpoint P, which holds sites out |

### Arithmetic, ceilings and gate strength

| # | Objection (ids) | Verdict | What changed |
| --- | --- | --- | --- |
| 29 | G4.2 could not be passed by a perfectly calibrated band, and failed by chance about half the time (B2, missed) | accepted | One-sided rule on the 80% band, narrowest-band rule, fixed cells, a check of the rule before the freeze |
| 30 | The `kappa_A` fit is biased toward zero and barely tracks the truth. The log-rank ratio is not constant near the top or past 20 (M2) | accepted, skeptic's fix | The fitted slope is a lower figure and the tool carries a range. The edit is applied on the latent curve. The reviewer's own estimator was rejected: it is not identified |
| 31 | One free signal already clears G2.2's absolute bar, the rest was a point margin, and missing values had two rules (M3, FP4, FP5, LM4) | accepted | G2.2, rule 7 |
| 32 | G2.1 was met by construction (LM5, FP4) | accepted | G2.1 |
| 33 | G2.3 had no baseline (m4) | accepted | G2.3 |
| 34 | G2.5 could not fail for a single signal (L7, M3) | accepted | G2.5, rule 6 |
| 35 | A tool that always says "cannot tell" passed G4.1, guards could fire on everything, and the cases were tuned on (M4, FP3, LM6, LF4, LF5) | accepted | G4.1, rung 0 |
| 36 | G4.3's direction bar sat 0.003 above BM25F, its error clause passed at zero effect, and "monotone" passed for a constant (M4, FP8, LM6) | accepted, with a change | G4.3. A strict step-by-step rule looked impossible for real pages, so the gate is on the full-text step |
| 37 | G3.1 compared against an unshrunk prior that almost always loses (M5) | accepted | Domain prior in Appendix C, G3.1 |
| 38 | G3.2 and G3.3 had no width bar and no paired baseline (FP4) | accepted | G3.2, G3.3. The content comparison lives in G3.5a |
| 39 | Ceilings were quoted for R = 3 only, R itself is loosely known, and "at most 0.69" depends on the pool (M6, LM2) | accepted | Sections 2.2 and 2.8 |
| 40 | Two-pull agreement and the label ceiling were mixed up, and "90% is above the ceiling" was false (m1, LM1, LM3) | accepted in part | Sections 2.2 and 2.8. The claim that only 0.72 is supported was rejected: the data cannot separate the two label models |
| 41 | The stage 3 placement prediction did not follow from its own pair prediction (m2) | accepted | Stage 3 PREDICTED line |
| 42 | "5 to 10 appearances" used the wrong information figure (m3) | accepted | Section 2.5 |
| 43 | A 0.35 chance for all stage 3 gates was too high (LM9) | accepted | About 0.15 |
| 44 | Placement rows exist only for pages that reached the list, and reach had no real gate (LM10, missed) | accepted | G3.7, a defined reach model, and then Checkpoint P |
| 45 | Tier sets differed in four places (LM11, FP7) | accepted | One set everywhere, Appendix C |
| 46 | G4.2 could pass at 1 to 3 days while the default horizon is 28 (LM7) | accepted, with a change | No percentage band is shown for an untested tier or horizon. The default stays 28 days so the paired effect is still shown |
| 47 | Google puts a site's second result next to its first, which would shrink same-site gaps (LM12) | rejected | Counts on the deep lists show no excess of adjacent same-site pairs |
| 48 | The 0.85 chance for the stage 1 advance rule is optimistic (reviewer suspicion) | rejected | The twice-pulled lists are harder on the same label, so they do not move the predicted centre |

### Ways to fake or drift into a pass

| # | Objection (ids) | Verdict | What changed |
| --- | --- | --- | --- |
| 49 | The gate script trusted a result file its own author wrote, and nothing required an independent audit (FP1, LF1, LF2) | accepted | Tasks 0.2 and 0.3, rule 15 |
| 50 | `gates.json` could not be built mechanically and could be re-frozen weaker (FP7, LF3) | accepted | Machine form, `VERSIONS.json`, a stricter-only rule |
| 51 | Nothing enforced "scored once" or "one loop" (LF6, FP2) | accepted | Rule 13: a pushed run log, VOID on a second run |
| 52 | A report could read as a win while the gate that counts failed (FP12) | accepted, with a change | Rule 14. A stored-test pass enables the no-anchor mode only with a PROVISIONAL label on every output |
| 53 | The reach task had no model (missed) | accepted | Group 3A, then group 3P |
| 54 | The outcome table left out G3.6 and G3.7 (missed, FP7) | accepted | G3 passes only with G3.1 to G3.7 |
| 55 | The position backtest cannot see a shrunken scale (missed) | accepted | G4.3 text, group 4C |

### Hidden assumptions and missing free signals

| # | Objection (ids) | Verdict | What changed |
| --- | --- | --- | --- |
| 56 | One standard deviation of content score is not a realistic edit. A rewrite that keeps the title and H1 is 0.1 to 0.3 (A2) | accepted | Sections 2.7 and 2.8, task 4C.5 |
| 57 | The "chance the edit helps" had no source of sign error, and its "cannot tell" range clashed with the word scale (A3) | accepted | Task 4C.4, 0.34 to 0.66 |
| 58 | A page at 14, or outside the top 30, would get a flagged or empty answer (A4) | accepted | Stage 4 "what is covered today", `no_list` |
| 59 | The client's Search Console exports are on disk and no task read them (A5, LA1) | accepted | Task 4B.2. D2 is now the first pivot |
| 60 | GOAL Targets 1 to 3 were never mapped, and a free forward test was unused (A6, LA4, LA7) | accepted | Checkpoint 3c, section 10, D9 |
| 61 | Build the anchored simulator before the site model (A7, LA5) | rejected | It goes against James's stated order and would open the stored test fold early |
| 62 | Add a rule to re-measure everything after the core update ends (A8) | rejected | The outputs already carry the update status, and bands measured during it err on the wide side |
| 63 | Section 0 overstated the evidence and hid that no gate compares an edit prediction with Google (A10, LA6) | accepted in part | Section 0 rewritten. The proposed decision to accept anchoring as the main product was rejected, and James's Nov 20 directions then settled the question the other way |
| 64 | Fields in the stored responses go unused (A11, LA3) | accepted in part | The snippet check (`text_mismatch`) and the reprocessing marker are in. Sitelinks and the knowledge graph are out: too rare, and they repeat the footprint |
| 65 | Train on known losers (LA2, A12) | rejected for the pairwise model | Mixing label designs changes the coefficient scale. The band model of Product 1 does train on cells where the site does not rank |
| 66 | The best-scoring indexed page is taken as Google's pick, and more pages means a higher best score by chance (A12) | accepted | Page-count covariate, and for Product 1 the label-blind crawl |

### Changes that came from James's Nov 20 directions, not from the reviewers

| Direction | What changed |
| --- | --- |
| "No one would pay for 71%." | Pair accuracy is a part check only (rule 18). Two product gates were added, each with a sellable bar, an honest forecast and a kill rule: Gate P and Gate E |
| The product is a new client site scored across hundreds of key searches | Section 0 and stage 3 are rebuilt around it: rule 17 (scored as new), the label-blind crawl (2A.6), the cold estimate (2D-cold), the band model and Checkpoint P (group 3P), the fresh-list option B in D1, sections 2.9 and 2.10 |
| Keep the edit effect as a secondary product | Stage 4 is Product 2. Gate E is its product gate, on real edits |
