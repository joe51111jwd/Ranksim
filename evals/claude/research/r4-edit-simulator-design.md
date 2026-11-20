# Edit simulator: prior art, the anchored design, and validation that does not wait on Google (round 4 research)

Research memo, 2026-11-20. Research and theory only. No Serper call was made (0 credits spent). No held-out list of the round 3 test (heldout1) was pulled or read. Web searches used generic topical words only. Nothing about the client, the project or any person was sent to an outside service.

Grades: STRONG (primary source read in the relevant part), MEDIUM (credible secondary source read, or a primary source relayed by a page-reading tool), WEAK (one informal source), SNIPPET ONLY (seen only in a search summary), UNVERIFIED. "LOCAL" means computed on this box from stored design-day data (Nov 17 or earlier), with the method in the last section. LOCAL numbers are diagnostics, not tests.

This memo does not repeat `edit-effects.md` (split-test effect sizes, tool correlations, recrawl times), `ranking-mechanics.md`, `r3-matched-pairs.md` or `r4-free-label-sources.md`. It points to them.

## What this means for the master plan

1. **Build the simulator as an anchored model, not a from-scratch ranker.** For a page that already ranks, predict the new position from its current latent standing plus the change in content score. The anchor already holds everything we cannot see: site authority, links, clicks, age. So the cross-site gap (small vendor against incumbents) does not have to be modelled for these pages. Only the edit's content change is modelled, and that is exactly what the same-site scorer measures. Google's own description supports an additive update: "Top-level signals are a linear combination of log of individual raw signals" (PXR0357, in `ranking-mechanics.md` 1.1).
2. **For a new page, or a page outside the top 30, anchor on a sibling.** If the site already ranks for the search (or a near-identical search) with another page, place the new page relative to that sibling using the same-site scorer's content difference. Round 2 found that 84 of 101 new rows whose site was already on page one were same-site swaps (`RESULTS-claude.md` 4.1). Caution: the round 1 rule "site's own slot, else the bottom" only tied a flat 7 on 135 held-out rows. The content comparison against the sibling is the new part, and it is untested. Only when the site has nothing near page 1 is a site-strength model needed. There the honest output is mostly class probabilities ("most likely beyond page 1").
3. **Output two separate things for every prediction.** (a) The paired effect: the position with the edit against the position without it, simulated with the same noise draws. Day-to-day drift cancels here. This is the product claim. (b) The absolute forecast: a position band and class probabilities. Drift dominates this one. LOCAL: over the 10 days from Nov 6 (before the core update) to Nov 16 (inside it), only 50% of pages at positions 4 to 10 were within 2 places, and half had left the consensus top 10.
4. **Treat the transfer factor as the biggest unknown.** Cross-sectional accuracy measures settled pages, which carry months of clicks and links. An edit changes text at once and the rest slowly. Write the edit effect as lambda times the settled content effect, with lambda between 0 and 1. No cross-sectional test can estimate lambda. Until natural experiments or ledgers estimate it, integrate over a wide prior on lambda. That widens bands and keeps claims directional.
5. **Anchor on a latent score from at least 3 pulls on at least 2 days, never on one observed rank.** Regression to the mean is real in our data. LOCAL, Nov 16 to 17: when one pull showed a page higher than its 3-pull median (21 rows), the next day's consensus was on average 2.1 places below that pull. Even pages whose first pull equalled the median were 0.75 places lower the next day (224 rows), because being in today's top 10 is itself a selection.
6. **Calibrate band widths from past errors, the way the hurricane cone is built.** The National Hurricane Center sizes each circle "so that two-thirds of historical official forecast errors over a 5-year sample fall within the circle". Do the same with split-conformal quantiles by start position and horizon, and publish the realized coverage. LOCAL one-day floor for an unedited page: ±1 place covers 0.72 and ±2 covers 0.82 at positions 4 to 10, and ±1 covers 0.86 at positions 1 to 3.
7. **Gate every release on a behavioral sanity suite (free, minutes).** Neural rankers misbehave in ways Google may not. Appending non-relevant sentences "often increases the ranking score of contextualized models" (ABNIRML, TACL 2022). Synonym swaps on about 4% of words promoted a BERT ranker's target document in 96.7% of attacks (PRADA). A duplicated document always lost score under DistilBERT (0.00 on the LNC2 axiom). The simulator must say "no change" or "cannot tell" for such edits. Use CheckList's three test types: invariance, directional expectation and minimum functionality.
8. **Validate in this order: sanity suite, round 3 held-out pairs, band coverage backtest on stored lists, virtual edits and swap pairs, Common Crawl natural experiments, then live ledgers.** Section 7 gives the ladder with cost and what each rung proves.
9. **Run a Common Crawl "which version does Google agree with" test.** For pages whose text changed materially between monthly captures, compare how well the before version and the after version order the page against its same-site neighbours in today's site-restricted lists. If Google has absorbed the change, the after version should agree more. If agreement grows with time since the change, that is a timing estimate. Use training sites and site-held-out scorer fits only. The scorer was fitted on current versions of the training pages, so a scorer that saw the site would favour the after version.
10. **Never let a live ledger gate the plan.** PR 45 has at most 24 in-play rows clustered on 8 searches. Even at a true hit rate of 0.70, a one-sided sign test on 15 effective rows has power 0.30. Score ledgers at edit plus 28 and plus 56 days, and only at least 7 days after Google marks the core update complete. Today's dashboard (fetched Nov 20) shows only the Nov 10 release entry and no completion. Pool ledgers with a pre-registered Beta-binomial hit rate and an anytime-valid e-value.
11. **Adopt one output format (section 8).** Classes (top 3, 4 to 10, 11 to 20, beyond 20) with probabilities, an 80% band, P(edit beats current) with a calibrated word, an explicit "cannot tell" state with its reason, the noise floor, and provenance with the PREDICTED label. The current UI brief shows a single point ("Predicted 3 places lower"). That should become a band plus "cannot tell" whenever the paired effect is inside the noise.
12. **Do not borrow vendor numbers as targets.** No vendor or paper found publishes the accuracy of edit-level rank predictions against real Google moves. SearchPilot reports group-level click tests with credible intervals and says Search Console rank data "is not reliable enough to generate statistical confidence correctly on most SEO tests". The rank simulators found (Market Brew, and patents from iQuanti and T-Mobile) describe score-to-rank simulation with no published validation. A backtested, coverage-calibrated band would be new. That is an opportunity and a warning: there is no benchmark to compare against.

## 1. How existing tools model an edit, and what they publish

What it means: every tool falls into one of three types. Split testers measure an edit after the fact, on groups of pages, in clicks. Content scorers grade a draft against today's top pages with no edit validation. Rank simulators map a score to a rank with no published accuracy. None predicts the rank of one page after one edit with a published error rate. Our design borrows the counterfactual forecast from the split testers and the score-to-rank simulation from the patents, and adds the calibration step none of them publish.

| Tool or work | What it predicts | How it is validated | Published accuracy | Grade, source |
| --- | --- | --- | --- | --- |
| SearchPilot | Counterfactual clicks for the changed group of pages: "estimate what would have happened if you hadn't made the SEO change", from control pages plus "the historical relationship between the control and variant pages before the test starts" | Neural network trained "on a large number of random "shuffles" of the data". Credible intervals by how far an uplift "persists under random shuffling of the pages". A separate confidence measure for the model's own fit. No fixed threshold; the customer chooses | Sensitivity "doubled" when it left CausalImpact. Better shuffling gave "a 16 percentage point increase in conclusive tests". Search Console rank data "is not reliable enough to generate statistical confidence correctly on most SEO tests" | STRONG (vendor, read): https://searchpilot.com/resources/blog/how-searchpilot-analyses-test-data , https://www.searchpilot.com/resources/blog/the-math-behind-searchpilot-how-seo-a/b-testing-actually-works |
| SearchPilot quiz, 2025 | Which of two pages ranks higher for a search | Gemini scored 75%. A 2016 model scored "about two-thirds". Pair count and Gemini's inputs are not given | 75%, no method | MEDIUM: https://www.searchpilot.com/resources/blog/ai-outperforms-humans-in-our-seo-quiz (already in `r3-matched-pairs.md`) |
| SplitSignal (Semrush) | Click change of a variant group against a control group | Groups split using "100+ days of historical traffic data". Needs 500K monthly sessions on the site, 100K to the test group, 300+ template pages. Googlebot "visits about 60% of the Variant pages in week 1, and the remaining 40% of Variant pages in week 2". No significance level stated on that page | None | STRONG for the FAQ (read): https://www.semrush.com/blog/top-faqs-for-seo-split-testing-with-splitsignal/ . That it uses Google's CausalImpact: SNIPPET ONLY (Semrush product and help pages redirect) |
| seoClarity Split Tester | Same design: test and control groupings with a "correlation percentage" tied to CausalImpact | Positive or negative, significant or not | None found | SNIPPET ONLY: https://www.seoclarity.net/blog/seo-tests-with-seoclarity , https://kb.seoclarity.net/portal/en/kb/articles/split-test-analysis |
| Market Brew | A search engine model fitted to a target result page, used to test changes before launch | Its own page on its patent says the system "was not presented as an exact reproduction of every signal a live search engine might use". A third-party listing claims an "average Pearson Score of 70% or higher" | None published | MEDIUM for the patent page (read): https://marketbrew.ai/resources/the-patent-behind-the-matrix-in-search . Pearson claim: SNIPPET ONLY. Rest in `edit-effects.md` section 4 |
| iQuanti patent US 10,146,876 B2 (priority 2015) | "a simulation interface that enables a user to make changes to the one or more variables and to determine a real-time change in organic search ranking" from changes in an overall score and a competition score. Its example moves a page from rank 32 to 30 | Overall score "correlated with the actual organic search ranking" | None | STRONG as a design (read): https://patents.google.com/patent/US10146876B2/en |
| T-Mobile patent US 10,878,058 B2 (priority 2017) | A search engine response model and a ranking-factor response model. "As attributes of a webpage are adjusted, the operations herein can simulate an expected webpage ranking and traffic volume". Calibrated to "current rank" and current traffic. Spearman correlation with a Fisher transformation | Model-to-measured calibration only | None | STRONG as a design (read): https://patents.google.com/patent/US10878058B2/en |
| SEOmonitor Goal Chance | Whether a target rank is reachable in a time frame | Heuristic: "1 month to climb every 10 positions outside top 10 and 1 month to climb each position within top 10". Labels "Realistic", "Ambitious", "Too Ambitious" | No backtest reported | STRONG (help page read): https://help.seomonitor.com/en/articles/6723561-the-goal-chance-explained |
| Surfer Content Score | A draft's grade against current top pages | Spearman 0.28 with position over "10,000 search queries". The page does not say whether this is within each search or pooled, and has no study of pages that changed score | 0.28 correlation | STRONG for what the page says (read): https://surfer.ai/blog/surfer-content-score-study |
| Clearscope Content Grade | "Clearscope's measure of content relevance and comprehensiveness", from term importance in competitors' pages | The page makes no ranking claim | None | STRONG (read): https://clearscope.io/support/articles/how-does-clearscope-grade-your-content |
| MarketMuse | Content Score (subtopic coverage) and Personalized Difficulty (keyword difficulty minus the site's topic authority) | Vendor claims only | None found | SNIPPET ONLY: https://help.marketmuse.com/support/solutions/articles/80001167736 |
| Originality.ai optimizer | Gap analysis against the top 10 | The page has no study, no sample and no metric | None | STRONG for the absence (read): https://originality.ai/blog/predictive-seo-content-optimization-accuracy |
| GEO (Aggarwal et al., KDD 2024) | Visibility of a source in a generative engine's answer after a content edit, measured in a simulated engine and then on Perplexity | Simulated engine on GEO-bench (10,000 queries), then a live check. Keyword stuffing scored 17.8 against 19.5 for no change in the simulated engine, and "performs 10% worse than the baseline" live. The best methods gave "up to 40%" | The closest published "simulate an edit, then confirm on the live engine" design. Not Google rank | STRONG (paper read): https://arxiv.org/abs/2311.09735 |
| C-SEO Bench (2025) | Same question for conversational engines with several competing adopters | "most current C-SEO methods are not only largely ineffective but also frequently have a negative impact on document ranking", and gains shrink as more actors adopt | Negative result | STRONG (abstract): https://arxiv.org/abs/2506.11097 |
| LLM product search manipulation (CORE, 2026) | Appended content steers LLM product rankings | "Promotion Success Rate of 91.4% @Top-5" across four LLMs | Shows LLM rankers over-respond to edits | STRONG (abstract): https://arxiv.org/abs/2602.03608 . Related: https://arxiv.org/abs/2406.03589 , https://arxiv.org/abs/2406.18382 |
| LLM-based Google rank predictors, 2024 to 2026 | Vendor and blog claims of "predicting rankings before publishing" (SEOZoom, Alli AI and others) | None found with a method | Not found | SNIPPET ONLY. A Feb 2026 claim of an ML model estimating "ranking probability" before content is written could not be traced to a primary source. A Terakeet patent (US 12,353,492) forecasts SERP positions from position history: SNIPPET ONLY |

Reading for the plan:
- The split testers' core idea is the right one for us: a forecast of what would have happened without the change, built from the unit's own history plus untouched controls. That is the anchored design, applied to one page instead of a page group.
- Their minimum sizes (hundreds of pages, 100K sessions a month) tell us how far a 15-page ledger is from a decisive test (section 5).
- LLM-as-judge rank prediction is risky as an edit simulator. LLM rankers are moved by appended text in 80% to 91% of attacks, and GEO and C-SEO show that what moves an LLM ranker often does nothing or harm on a live engine.

## 2. Counterfactual and what-if work on rankers, and sanity tests with no ground truth

What it means: academic what-if work always assumes the ranker is known. It cannot tell us how Google responds. It does tell us how our own scorer can fail: tiny or irrelevant edits can move a cross-encoder a lot. So the simulator needs guards, and a behavioral test suite is the cheapest validation we have.

**Counterfactual explanations for document rankers.**
- CREDENCE (ICDE 2023 demo) gives counterfactual documents, counterfactual queries and "build and test" perturbations for a known neural ranker. STRONG (abstract): https://arxiv.org/abs/2302.04983
- Chandna and Sen ask "which terms should be added to a document to improve its ranking", on BM25, DRMM, DSSM, ColBERT and MonoT5. The abstract gives no success rate. STRONG (abstract): https://arxiv.org/abs/2409.00860 . The "about 60%" figure in `edit-effects.md` came from a summary and is not in the abstract.
- Ranking competitions: authors who copy content from past top documents improve their rank, on the live web too (Vasilisky, Tennenholtz and Kurland, SIGIR 2020, Wayback snapshots of ClueWeb09 pages). STRONG (abstract): https://arxiv.org/abs/2005.13810 . An LLM-author simulator for such competitions exists: https://arxiv.org/abs/2502.11197 (abstract).

**How far a small text change can move a neural ranker (adversarial rank attacks).**
- PRADA (Wu et al., ACM TOIS 2023) attacks a BERT ranker fine-tuned on MS MARCO, using documents from ranks 11 to 100. Replacing words with synonyms gives a success rate of 96.7% with a perturbation of 4.0% of words on MS MARCO documents. Plain query-term spam (TS_rep) reaches 93.1%, but a term-spam detector catches it 81% to 99.4% of the time, against 7% to 19% for PRADA. Documents ranked 11 to 20 were the hardest to promote (0.845). STRONG (paper read): https://arxiv.org/abs/2204.01321
- Order-Disorder (2022) and topic-oriented attacks (2023) promote documents with "very few tokens" or for a whole group of queries. STRONG (abstracts): https://arxiv.org/abs/2209.06506 , https://arxiv.org/abs/2304.14867
- Reading: if the simulator's delta is a raw cross-encoder difference, a copywriter can raise it with edits Google would ignore. The PR 45 rule (both scorer parts must agree, delta at least 0.10 list standard deviations) is a start, not a defence.

**Diagnostic probes of neural rankers.**
- ABNIRML (MacAvaney et al., TACL 2022). Verbatim: "adding non-relevant sentences to the end of relevant documents often increases the ranking score of contextualized models. This is in contrast with models like BM25". Also: "the contextualized models often respond negatively" to added DocT5Query expansion terms. STRONG (read): https://aclanthology.org/2022.tacl-1.13 . Probe scores relayed by a page-reading tool and spot-checked in the table: word shuffling about -0.38 (BERT) to -0.76 (T5-large) against 0.00 for BM25. Typos -0.23 for BM25 and -0.09 to -0.37 for the neural models. MEDIUM for the exact values.
- Câmara and Hauff (ECIR 2020): DistilBERT beat query likelihood by 40% in nDCG but did not satisfy the classic retrieval axioms. Share of diagnostic instances ranked as the axiom says, query likelihood against DistilBERT: TFC1 (more query-term occurrences is better) 0.99 against 0.61, TFC2 0.70 against 0.39, LNC2 (a duplicated document should not score lower) 1.00 against 0.00, TP (query terms closer together is better) 0.39 against 0.41. STRONG (paper read): https://arxiv.org/abs/2201.04458
- Reading: the axioms are not Google's rules either, so failing one is not proof of error. But a scorer that drops when a page is duplicated, or rises when an off-topic paragraph is appended, will mislead users on exactly the edits they try.

**Behavioral and metamorphic testing.**
- CheckList (Ribeiro et al., ACL 2020) defines three test types. A Minimum Functionality test is "a collection of simple examples (and labels) to check a behavior". An Invariance test applies "label-preserving perturbations to inputs and expect[s] the model prediction to remain the same". A Directional Expectation test is the same "except that the label is expected to change in a certain way". STRONG (paper read): https://arxiv.org/abs/2005.04118
- Contrast sets (Gardner et al., 2020): small expert edits that flip the label dropped model accuracy "up to 25%" below the ordinary test set. STRONG (abstract): https://arxiv.org/abs/2004.02709
- Metamorphic testing has been applied to Google, Bing and Baidu, which have no test oracle (Zhou, Xiang and Chen, IEEE TSE 2016). SNIPPET ONLY: https://ro.uow.edu.au/eispapers/5699

**A sanity suite for our simulator.** None of these tests needs Google. Each runs on held-out pages in minutes. Each pass rule should be frozen before it is run.

| Test | Type | Input change | Expected simulator output |
| --- | --- | --- | --- |
| Irrelevant append | INV | Add one off-topic paragraph | No change, or "cannot tell" (ABNIRML says cross-encoders rise here) |
| Synonym swap | INV | Replace 3% to 5% of body words with synonyms | No change (PRADA shows a BERT ranker moves) |
| Cosmetic | INV | Change footer year, whitespace, HTML formatting, reorder unrelated sections | No change |
| Date only | INV | Change the visible date or dateModified only | No change. Google says re-dating without real change does not help; split tests are mixed (`edit-effects.md` section 6) |
| Duplication | INV | Repeat the body twice | No rise; flag as low quality (DistilBERT fails LNC2) |
| Determinism and order | INV | Re-run; shuffle the order of competitor inputs | Identical output |
| Other pages fixed | INV | Edit page A only | Relative order of other pages unchanged (independence of irrelevant alternatives under the choice model) |
| Antisymmetry | metamorphic | Score A to B and B to A | Paired effects equal and opposite |
| Declare the topic | DIR | Add the search phrase to a title and H1 that lack it | Non-negative paired effect on that search; non-positive or zero on unrelated searches |
| Remove the topic | DIR | Remove the search topic from title and H1 | Non-positive |
| Delete the body | DIR plus size | Leave only title and byline | Down, but a modest drop for an established page. Ahrefs deleted the body of two ranking posts for 12 days, and "Most of the rankings only lost a few positions" (MEDIUM, n of 2: https://ahrefs.com/blog/impact-removing-content/ ) |
| Dose response | DIR | Move a page's text step by step toward a better sibling's (title, then H1, then body) | Monotone movement toward the sibling's predicted position |
| Keyword stuffing | MFT | Repeat the search phrase 20 times | Gain capped at about one natural mention, plus a stuffing flag. Google: keyword stuffing is "filling a web page with keywords or numbers" "in an attempt to manipulate rankings" (STRONG: https://developers.google.com/search/docs/essentials/spam-policies ). GEO found stuffing worse than no change |
| Saturation | MFT | Add the same supporting section 1, 2, 3 times | Concave or flat after the first addition |

What the suite proves: the simulator behaves sensibly where we know the answer. What it cannot prove: that Google responds the same way. Several expectations (date edits, title keyword edits) have mixed signs in real split tests, so their rule should be "no confident call", not a fixed sign.

## 3. The anchored design: analogues, regression to the mean and daily noise

What it means: predicting the change from where a page already stands is the standard way to beat noise in before-and-after measurement. It removes the need to model what we cannot see. Its main risks are a noisy starting point (regression to the mean) and a drifting starting point (core updates, competitors). Both can be measured from stored lists for free.

**Closest published analogues.**
- *Baseline-adjusted analysis in trials.* For a before-and-after design, analysis of covariance on the follow-up with the baseline as a covariate beats both raw follow-up and change scores. Change scores do not remove baseline imbalance "because of regression to the mean", and the gain from analysis of covariance is small when the baseline-to-follow-up correlation is above about 0.8 (Vickers and Altman, BMJ 2001;323:1123). SNIPPET ONLY (quotes seen in search summaries; the BMJ and PubMed Central pages were blocked): https://doi.org/10.1136/bmj.323.7321.1123
- *Regression to the mean.* "It happens when unusually large or small measurements tend to be followed by measurements that are closer to the mean" (Barnett, van der Pols and Dobson, Int J Epidemiol 2005). STRONG for the definition (abstract): https://academic.oup.com/ije/article/34/1/215/638499 . The usual remedies are several baseline measurements, a control group, and modelling the baseline instead of subtracting it.
- *Counterfactual forecasts in SEO testing.* SearchPilot and CausalImpact forecast the treated series from its own past and from untouched controls, then read the effect as actual minus forecast (section 1; Brodersen et al., Annals of Applied Statistics 2015, STRONG abstract: https://arxiv.org/abs/1506.00356 ). This is anchoring with a control group.
- *Score-to-rank simulators.* The iQuanti and T-Mobile patents calibrate a score to the current rank, then move the score and read off a new rank (section 1). They are anchored designs with no published error rate.
- *Ratings with uncertainty.* TrueSkill "tracks the uncertainty about player skills" and updates them from new results (Herbrich, Minka and Graepel, NIPS 2007). STRONG (abstract): https://www.microsoft.com/en-us/research/publication/trueskilltm-a-bayesian-skill-rating-system/ . The analogy: a page's standing is a mean with an uncertainty, not a fixed number.
- *Ranking choice models.* Plackett-Luce and Thurstone models turn latent scores into probabilities over whole orders. A Bayesian Plackett-Luce fit is in Guiver and Snelson (ICML 2009). SNIPPET ONLY (bibliographic records and the PDF link were found, the PDF not read): https://icml.cc/imls/conferences/2009/papers/347.pdf

**The design, step by step (theory, not built).**
1. *Anchor.* For search q, fit a latent score a_i for every page in the current candidate set (top 30 where pulled) from the last K pulls over at least 2 days. Use a Thurstone-type model: in each pull the order follows a_i plus noise, and a page missing from a pull sits below every page shown. Set the noise scale so that simulated pull-to-pull agreement matches the measured 0.87 of pairs (0.839 adjacent, 0.932 for pairs five or more apart).
2. *Edit.* Let d be the frozen same-site scorer's change in pair log-odds from the before text to the after text (its logistic part is already in pair log-odds units). Set the edited page's score to a_e plus lambda times k times d. Here k converts logit to the Thurstone scale (roughly 1 over 1.7) and lambda (0 to 1) is the share of the settled content effect that appears within the scoring window.
3. *Forecast.* Draw M simulated orders at horizon h, with drift noise for h calibrated from historical anchor errors by start band. Apply the host-crowding rule ("generally won't show more than two web page listings from the same site", `ranking-mechanics.md` section 3).
4. *Two outputs.* The paired effect uses the same random draws with and without the edit: P(edited position better than unedited position), and the expected difference. The absolute forecast gives bands and class probabilities.
5. *Large rewrites.* The anchor's residual (where the page ranks minus what its content predicts) holds links, clicks and site standing, which persist after an edit. It also holds the scorer's error on the old text, which a full rewrite resets. So blend the anchored forecast with a from-scratch forecast (sibling anchor plus content) by edit size, for example by one minus text similarity. Freeze the weight rule before scoring.
6. *No anchor.* If the site has another page in the candidate set for q, or for a search whose top 10 shares at least 3 URLs with q, anchor on that sibling and add the same-site content difference between the new page and the sibling. If the site has nothing near page 1, fall back to the site-standing boundary model in `r4-free-label-sources.md` (point 4) and report class probabilities only.

Why additivity is a reasonable first assumption: Google describes top-level signals as "a linear combination of log of individual raw signals", and the signals as "monotonic" (PXR0357, `ranking-mechanics.md` 1.1, STRONG as call notes). Why it can fail: twiddlers re-rank after scoring, host crowding caps a site at about two listings, and the content effect seen in settled same-site pairs includes clicks and links that content attracted over months. That last part is lambda.

**Regression to the mean and noise in the starting position.** LOCAL, design days, consensus position = median of the day's pulls, a page missing from the next day's consensus top 10 counted as 11 (so drops are understated):

| Anchor day to outcome day | Start band | Rows | Same place | Within 1 | Within 2 | Within 3 | Left the top 10 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Nov 16 to Nov 17 (45 searches) | 1 to 3 | 125 | 0.70 | 0.86 | 0.88 | 0.88 | 0.10 |
| Nov 16 to Nov 17 | 4 to 10 | 290 | 0.45 | 0.72 | 0.82 | 0.89 | 0.24 |
| Nov 13 to Nov 16 (30 searches) | 1 to 3 | 90 | 0.43 | 0.62 | 0.71 | 0.71 | 0.24 |
| Nov 13 to Nov 16 | 4 to 10 | 208 | 0.23 | 0.60 | 0.75 | 0.84 | 0.32 |
| Nov 6 to Nov 16 (30 searches, Nov 6 one pull, before the update) | 1 to 3 | 90 | 0.36 | 0.54 | 0.62 | 0.69 | 0.21 |
| Nov 6 to Nov 16 | 4 to 10 | 204 | 0.08 | 0.31 | 0.50 | 0.66 | 0.50 |

- One day ahead, a 5-place band (±2) is the narrowest band that covers about 80% for an unedited page at 4 to 10. Ten days ahead, across the start of the core update, ±3 covers only two thirds and half the pages left the top 10. Every window here is inside or across the November 2026 core update, so these are worst-case drift numbers. Re-measure after it completes.
- Regression to the mean, Nov 16 to 17, 3-pull days: when the first pull showed a page higher than its 3-pull median (21 rows), the next day's consensus was 2.1 places below that first pull on average. When the first pull equalled the median (224 rows), it was 0.75 below. When it was lower (6 rows), 0.5 above. Small n, but the direction is the textbook one.
- Two consequences. First, use the multi-pull latent score, not the last observed rank. Second, condition on how the anchor was selected: a page is in today's top 10 partly because of good luck today, so its expected position tomorrow is a bit worse even with no edit. The latent-score fit handles this if absent pages are treated as censored, not dropped.
- An edit effect of 0.1 to 0.6 places (`edit-effects.md` section 2) is far inside the drift for any horizon over a day. This is why the paired output, with drift cancelled, is the product claim, and the absolute band is context.

## 4. Validation that does not wait on Google

What it means: four free tests, each proving a different link in the chain from "content score" to "edit moves rank". None of them proves the last link (an edit moves this page by this much on Google within weeks). Together they bound it. The order matters: cheap and leak-free first.

| Test | What it proves | What it cannot prove | Main confounds | Size and cost |
| --- | --- | --- | --- | --- |
| (a) Cross-sectional held-out pairs (round 3 test1: 104 site-restricted lists, 93 sites, two pulls) | The scorer orders unseen pages of unseen sites in Google's settled same-site order, at X% against BM25F and the ranker's content part | Edit response (settled pages carry clicks and links). Cross-site order. Positions | URL and template cues; internal links (hub beats leaf); per-search click memory; topic shift (37 of 104 held-out lists are in a topic area with no training list); label noise (ceiling about 0.93) | 208 credits, already planned. Expected 1,500 to 2,100 consensus pairs |
| (b) Common Crawl natural experiments ("which version does Google agree with") | Whether Google's current order agrees more with a page's new text than its old text, and how that grows with months since the change. This is the only free test of transfer (lambda) | Size of the effect in positions. Causation | Selection (pages are rewritten because they were slipping; pages that died are missing); other changes made at the same time (links, redesigns, internal linking); capture timing (monthly, so the change date is only known to within a month); scorer leakage (it was fitted on current versions of training pages); byte changes that are not content changes | 0 credits. `data.commoncrawl.org` works from this box. LOCAL from the earlier memo: 32% of small-site ranking URLs have 2 or more captures in 6 crawls. Expect a few dozen to about 200 changed pages in the training lists |
| (c) Swap pairs (one site's two pages in opposite orders under two searches) | The scorer reads the search against the page, with site and page identity fixed. A search-blind scorer gets exactly 0.500 | Edit response; positions | Anchor text and clicks differ by search; the two searches must be parallel (share 3 or more top-10 URLs, a WEAK practitioner rule) | Training: 286 rows on 37 sites (blend 0.612, unfitted vote 0.645, BM25F 0.563). Held-out: only 11 sites have two lists. More lists per site would grow it (R3.9 item 3) |
| (d) Virtual edits (page A of a site given the text of same-template page B) | Internal consistency: how much of the predicted A-to-B gap comes from text and how much from URL and page shape; monotone dose response; antisymmetry | What Google would do with A carrying B's text. B's position also reflects B's URL, links and clicks | Same as (a), plus the hybrid page never existed | 0 credits; uses training lists. Hundreds of same-template pairs |

**More detail on (b), because it is the only free route to lambda.**
- Universe: pages in the training site-restricted lists (train sites, train search groups) with at least two Common Crawl captures between about May and October 2026 whose main text changed under the frozen material-change rule (title or H1 changed, or a body edit of at least 250 tokens and 2%). The digest field screens for byte changes without downloading pages (`r4-free-label-sources.md` section 5).
- Statistic per changed page: pair accuracy of the before version against its same-site neighbours (current versions) in today's site-restricted list, minus the same with the after version. Score with a site-held-out fit of the scorer, so neither version was seen in training. The test is a sign test or bootstrap by site over pages.
- Dose response in time: split by capture month of the change. If changes from May to July show a larger after-minus-before gain than changes from September to October, Google absorbs content over months, and the gap estimates the time course. The rank transition patent describes about 70 days to target in its example (`edit-effects.md` section 3; a design, not proof of use).
- Controls: pages in the same lists with two or more captures and no material change. Their before-minus-after difference should be near zero; a non-zero value measures drift in the scorer itself.
- Do not use heldout1 sites. Test2 sites (never used) could give a clean replication later, but that spends them.
- Interpretation: a positive result shows that Google's settled same-site order tracks the scorer's notion of content change. A null result is ambiguous (changes too small, too recent, or the scorer's axis is not Google's).

**More detail on (d).** Build hybrids that keep A's URL and layout and take B's title, then B's title and H1, then B's title, H1 and body. The sanity rules: the predicted score moves monotonically toward B's; the full hybrid's predicted pair outcome against A should agree with B's real order against A more often than the title-only hybrid. The share of the scorer's held-out accuracy kept by the full hybrid is the "content share" of the scorer. The round 1 version of this diagnostic showed the old ranker's score went up for only 0.312 to 0.496 of pairs (`RESULTS-claude.md` 3.5). The new scorer should be checked the same way.

## 5. Live confirmation for small pre-registered ledgers

What it means: a 15-page ledger cannot confirm or refute the simulator on its own. It can only add evidence to a pool. Freeze the scoring rule, wait long enough, compare against matched untouched pages, and pool across ledgers.

**Power of the sign test.** LOCAL arithmetic, exact binomial, one-sided 5%, independent rows:

| Rows | Hits needed | Power at a true hit rate of 0.60 | 0.65 | 0.70 | 0.75 | 0.80 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 7 | 0.11 | 0.17 | 0.26 | 0.37 | 0.50 |
| 15 | 12 | 0.09 | 0.17 | 0.30 | 0.46 | 0.65 |
| 24 | 17 | 0.19 | 0.36 | 0.56 | 0.77 | 0.91 |
| 30 | 20 | 0.29 | 0.51 | 0.73 | 0.89 | 0.97 |
| 40 | 26 | 0.32 | 0.57 | 0.81 | 0.95 | 0.99 |
| 60 | 37 | 0.45 | 0.75 | 0.94 | 0.99 | 1.00 |
| 100 | 59 | 0.62 | 0.91 | 0.99 | 1.00 | 1.00 |

- Rows for 80% power: 158 at 0.60, 69 at 0.65, 37 at 0.70, 23 at 0.75, 18 at 0.80.
- Realistic ceiling: if only a share f of moved rows carries a real, correctly called edit effect and the rest move by chance, accuracy is f plus half of (1 minus f). Round 2 bounded f at about 0.335 (`RESULTS-claude.md` 4.1), so the ceiling is about 0.667, which needs about 53 independent rows. At f of 0.25 it is 0.625 and about 102 rows.
- Clustering: rows share searches and pages. With a design effect of 1 plus (m minus 1) times rho, 24 in-play rows of PR 45 in 8 searches (m of 3) are worth about 15 independent rows at rho of 0.3, and 12 at rho of 0.5. 120 rows with 8 rows per search at rho of 0.3 are worth about 39.
- So PR 45 alone has power of roughly 0.17 to 0.36 at plausible hit rates. A miss proves nothing and a hit proves little. This is why the ledger cannot block the plan.

**Matched controls and difference in differences.** Score each edited (page, search) row against controls that faced the same Google: the same site's unedited pages on the same searches (absorbs site-level shifts, including much of a core update), and the same SERP's unedited competitors. The outcome is the change in consensus position (or latent score) from a multi-pull before window to a multi-pull after window. The edited-minus-control difference is the estimate. This is the analysis-of-covariance logic of section 3 with a control group. Report the direction hit rate on rows where the simulator made a call, the "no call" rows separately, and the edited-minus-control difference with a cluster bootstrap by search.

**Synthetic control and CausalImpact.** CausalImpact predicts the treated series from control series and assumes "the relationship between the treatment and the control series that existed prior to the intervention" continues, "as long as the control series received no intervention themselves" (SNIPPET ONLY for the quote; abstract STRONG: https://arxiv.org/abs/1506.00356 ). For one page's rank that needs a daily pre-period of several weeks and controls untouched by the treatment. A core update touches every control. With few controls, placebo inference "suffers from low resolution since the null distribution is constructed from only N reference estimates" (Lei and Sudijono 2024, STRONG abstract: https://arxiv.org/abs/2401.07152 ), and close pre-period fit does not guarantee a good counterfactual (Abadie and Vives-i-Bastida 2022: https://arxiv.org/abs/2203.06279 , abstract read). Use it as a per-page picture, not as the test.

**Accumulating evidence across many small ledgers.**
- Pooled hit rate with a pre-registered prior. LOCAL: with a flat Beta(1,1) prior, 10 of 15 gives P(rate above 0.5) = 0.895 and a 90% interval of 0.45 to 0.82. 12 of 15 gives 0.989. With a skeptical Beta(10,10), 12 of 15 gives 0.939 and 36 of 60 gives 0.912. Report the posterior after each ledger; never stop or continue a ledger based on it.
- Anytime-valid test. A likelihood-ratio e-value against a hit rate of 0.5 can be checked after every ledger without inflating error. Johari, Pekelis and Walsh define "always valid p-values and confidence intervals" for continuously monitored A/B tests (STRONG abstract: https://arxiv.org/abs/1512.04922 ). LOCAL: against a design alternative of 0.65, 10 of 15 gives an e-value of 2.3 and 12 of 15 gives 8.0. Reaching 20 (a 5% level) takes about 66 rows on average if the true rate is 0.65, and about 36 rows if it is 0.70.
- A classical alternative is group-sequential alpha spending with one look per ledger (Lan and DeMets, Biometrika 1983; O'Brien-Fleming-type spending). SNIPPET ONLY: https://support.sas.com/documentation/cdl/en/statug/68162/HTML/default/statug_seqdesign_details37.htm
- Weight rows by in-play status, and keep "no call" rows out of the hit rate but in the report, so the simulator cannot win by calling only easy rows. Report the share of rows called.

**How long to wait.** From the earlier memos: refresh of a known URL is about 30 days typical (Illyes, relayed), title updates show 1 to 2 days after reprocessing, Google says "a few days to a few weeks", SplitSignal sees 60% of pages visited in week 1 and 40% in week 2, and split tests run 2 to 6 weeks (`ranking-mechanics.md` 4, `edit-effects.md` 3). For a low-traffic B2B site, plan the primary score at edit plus 28 days and a second at plus 56. Record a per-page reprocessing marker (the result-page title or snippet switching to the new text) and report rows before and after their marker separately. Search Console's crawl data would be better but needs owner approval (`r4-free-label-sources.md` point 6).

**A core update in the window.** As fetched on Nov 20, Google's status page for the November 2026 core update shows only "Released the November 2026 core update ... The rollout may take up to 2 weeks to complete" (10 Nov) and no completion entry (STRONG: https://status.search.google.com/incidents/WLxcJyC6jMWMfryxsKMc ). Google advises waiting "at least a full week after a core update completes" before analysing (`ranking-mechanics.md` 3). PR 41 (deployed Nov 17) and PR 45 (live Nov 19) both sit inside it. Pre-register: any score date before completion plus 7 days is diagnostic only. The same-site control difference absorbs a site-wide shift but not a page-specific one.

## 6. How other fields present wide uncertainty honestly

What it means: the best-tested formats give a probability for a well-defined event, size the range from past errors, and use calibrated words. Showing a range does not cost much trust.

- Probability of a defined event. The US National Weather Service defines a 40% chance of rain as "a 40 percent probability for at least 0.01" of rain at the specific forecast point". STRONG: https://www.weather.gov/ffc/pop . For us: "a 55% chance of being in positions 4 to 10 on the consensus of three pulls on the score date".
- Ranges sized from historical errors. The hurricane cone's circles are sized "so that two-thirds of historical official forecast errors over a 5-year sample fall within the circle", and are recomputed each year from the last five years. STRONG: https://www.nhc.noaa.gov/aboutcone.shtml . For us: band widths from our own past prediction errors, recomputed as data grows, with realized coverage published. Split-conformal prediction gives the same guarantee for any model: sets "guaranteed to contain the ground truth with a user-specified probability" (Angelopoulos and Bates, STRONG abstract: https://arxiv.org/abs/2107.07511 ).
- Calibrated words. IPCC: "Virtually certain 99-100%", "Very likely 90-100%", "Likely 66-100%", "About as likely as not 33 to 66%", "Unlikely 0-33%". STRONG: https://www.ipcc.ch/site/assets/uploads/2018/05/uncertainty-guidance-note.pdf . It also keeps "confidence" (quality of evidence) separate from likelihood. SearchPilot does the same: a credible interval plus a separate confidence measure for the model's own fit (section 1).
- Ranges and trust. Five experiments with 5,780 people found that stating uncertainty lowered trust only slightly, mostly when it was verbal, and that a numeric range worked best (van der Bles et al., PNAS 2020). SNIPPET ONLY: https://www.repository.cam.ac.uk/handle/1810/304699
- Discrete displays. Quantile dotplots (for example 20 dots, each a 5% outcome) made people's probability estimates less variable than density plots and led to better decisions (Kay et al., CHI 2016; Fernandes et al., CHI 2018). SNIPPET ONLY: https://idl.uw.edu/papers/when-ish-is-my-bus
- Judge by "sharpness subject to calibration": among calibrated forecasts, the narrower is better (Gneiting, Balabdaoui and Raftery, JRSS-B 2007). SNIPPET ONLY: https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jrssb.pdf . For ordered classes (top 3, 4 to 10, 11 to 20, beyond 20), the ranked probability score (Epstein 1969) penalizes probability placed far from the true class. SNIPPET ONLY: https://arxiv.org/abs/2309.08701
- An explicit abstain state. Selective prediction lets a model reject inputs to keep a promised error rate; the cost is coverage (Geifman and El-Yaniv 2017: an error guarantee "with probability 99.9%, and almost 60% test coverage" on ImageNet top-5). STRONG abstract: https://arxiv.org/abs/1705.08500 . For us: "cannot tell" is a designed output with its own reported share, not a failure.

## 7. Recommended validation ladder for the edit simulator

Cheap and immediate first, live confirmation last. Freeze each rung's rule and threshold before running it, as the round 3 rules require.

| Rung | What | Cost and time | What a pass shows | Pass rule to freeze (proposal) |
| --- | --- | --- | --- | --- |
| 0 | Behavioral sanity suite (section 2 table) on held-out pages | 0 credits, minutes | The simulator does not reward edits we know should not matter, and moves the right way on clear cases | Invariance tests: median absolute paired effect under 0.25 places and "cannot tell" or "no change" in at least 90% of cases. Directional tests: right sign or "cannot tell" in at least 90%, wrong sign in under 5%. Stuffing capped and flagged in 100% |
| 1 | Round 3 held-out same-site pairs (already planned), plus calibration of the pair probabilities (reliability curve, Brier score) | 208 credits, one day | Content ordering on unseen sites; pair probabilities mean what they say | As frozen in round 3. Calibration: predicted-probability bins within 0.05 of observed rates |
| 2 | Band coverage backtest on stored lists, with the edit set to zero | 0 credits, a day | The noise part of the simulator: stated 50%, 80% and 90% bands cover at those rates by start band and horizon | Realized coverage within 5 points of stated, in each start band, on days not used to set the widths. Report sharpness (median width) next to it |
| 3 | Virtual edits (hybrid transplants) and swap pairs on training lists | 0 credits, a day | Internal consistency, dose response, search sensitivity with the page fixed | Monotone dose response in at least 90% of hybrids; swap-pair accuracy above 0.5 with the lower bound by site above 0.5 |
| 4 | Common Crawl version-agreement test, with untouched-page controls and a time-since-change split | 0 credits, 1 to 2 weeks to build | Google's settled order moves toward new content; a first estimate of lambda and its time course | After-minus-before agreement above 0 with the lower bound by site above 0; controls near 0 |
| 5 | Live ledgers (PR 45, PR 41, future edits), scored at plus 28 and plus 56 days and at least 7 days after a core update completes, against same-site and same-SERP controls, pooled across ledgers | 0 credits beyond the collector's pulls, weeks | Edit direction on real edits; band coverage on real edits | No single-ledger pass rule. Pooled: posterior P(hit rate above 0.5) and the e-value, both reported after each ledger. Band coverage reported, never used to retune the bands it scores |

Not on the ladder: waiting for Google before rungs 0 to 4 are done.

## 8. Recommended output format

One block per (page version, search). Every number is labeled PREDICTED. The numbers in the example are illustrative only; nothing was run.

```
Search: <search>                    Score date: <edit date + 28 days>   PREDICTED
Page: <url>  Version: <draft id>    Anchor: consensus of 6 pulls, Nov 18-20 (latent rank 7)

Effect of this edit (same day, same competitors, noise cancelled)
  Better than leaving the page as is: 64%  -> "about as likely as not"
  Expected change: 0.4 places better (80% range: 1 place worse to 2 better)

Where it should land (includes normal movement over 28 days)
  Top 3: 9%   Positions 4-10: 56%   Positions 11-20: 24%   Beyond 20: 11%
  80% band: positions 4 to 13        Most likely class: 4-10
  Normal movement for a page at 4-10: about 1 place a day; half of such
  pages left the top 10 within 10 days during the Nov 2026 core update.

Confidence in the model for this case: Medium
  (site has a ranking sibling; search topic is in training; edit size: large)
Cannot tell: no
Flags: none
Model <name> <sha256>, data through <date>, core update status: <complete/in progress>
```

Rules for the fields:
- Probabilities come from the simulation and are recalibrated on rungs 1 and 2. Words follow the IPCC scale: "likely" only at 66% or more, "about as likely as not" from 33% to 66%, "unlikely" under 33%.
- The 80% band comes from conformal error quantiles by start band and horizon (rung 2), never from the model's own spread alone.
- "Cannot tell" is shown, with its reason, when any of these holds: P(edit better) between 0.40 and 0.60; the paired effect is below the noise floor for that start band; the edit is below the frozen material-change rule; the scorer's parts disagree in sign; a sanity guard fires (stuffing, duplication, irrelevant text, date only); the page has no anchor and its site has no page near page 1 for the search or a parallel search; the search's topic area has no training lists; the page cannot be read.
- For two candidate edits, show P(B beats A) and the expected place difference, with the same "cannot tell" rule.
- For a new page with no sibling anchor, show only the class probabilities and the band, and say "placement estimate, low confidence".
- Keep the current UI brief's caveat, "Among these candidate pages, not a Google ranking", for simulated competitor sets. Replace its single "Predicted 3 places lower" with the effect block above.
- Show the realized coverage of past bands on the details page ("our 80% bands contained the real position N% of the time on M past predictions").

## Sources not opened, and things not found

- Not opened or blocked: the BMJ and PubMed Central copies of Vickers and Altman (bot check), Barnett et al. full text (paywall), the Glicko paper (bot check), the Zhou et al. TSE paper (403), Semrush's SplitSignal product and help pages (redirect), the Market Brew glossary (an HTML page, not a PDF). Guiver and Snelson, Gneiting et al., van der Bles et al., Kay et al. and the ranked probability score were seen only through search summaries or abstracts and are graded that way.
- Not found: any published accuracy of edit-level Google rank predictions; any vendor backtest of a rank simulator; any study that validates a content model cross-sectionally and then on real edits (repeats the finding in `r3-matched-pairs.md`); a primary source for the Feb 2026 "ranking probability" model claim.

## Method notes for the LOCAL numbers

- Anchor drift table: `scripts/claude/rsdata.py` loaders with `max_date` 2026-11-17 (design days only), `consensus_table` for each day. For each search present on both days, every URL with consensus position 1 to 10 on the anchor day was matched by key to the outcome day's consensus; a URL missing from the outcome day's consensus table counted as 11. Rows are (search, URL). 45 searches had both Nov 16 and Nov 17; 30 had the earlier pairs. Nov 6 and Nov 9 were single pulls.
- Regression to the mean: Nov 16 rows from 3-pull sessions only; the "first pull" is the first entry of the session's positions tuple, compared with that day's 3-pull median and with the Nov 17 consensus.
- Power, Beta-binomial and e-values: exact binomial and Beta distributions from SciPy 1.18.1 in the repo's virtual environment. Design effects use 1 plus (m minus 1) times rho with assumed rho of 0.1, 0.3 and 0.5 (not measured).
- The scripts were run from `/tmp` and not saved in the repo, since this run may only create this memo. Each step can be rebuilt from the description above.
