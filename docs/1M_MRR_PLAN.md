# Ranksim: the $1M+ MRR version

*Plan date: 2026-11-02. Revised 2026-11-03 after the founder's correction: the product is the tester. "You update, it indexes in minutes, you see what works, and a rapid iteration version that runs until you're top N on your top searches." Audience: the founder. Labels: **REAL** means pulled from Google or an AI engine. **PREDICTED** means a model output. **UNVERIFIED** means a single unconfirmed or secondary source. Funnel rates, churn and CAC are assumptions to test with design partners; none of them are sourced.*

---

## 1. TL;DR

- **The sandbox tester is the product and the hook.** You edit a page. The tester puts that version into the stored competitor set, re-indexes it, and re-scores it in seconds. You see the PREDICTED rank on your top searches with UNSURE/CLEARER, an independent linear check, how much of each Google result set is stored, and the backtest, printed on every board. Every version is saved, and CI can fail a preview that is PREDICTED worse. Built as v1 (`tester.py`, `ranksim tester`).
- **`optimize` is the rapid iteration version.** It rewrites the page, re-scores each version, and keeps the best one until every target query is PREDICTED top N, or a stop rule ends the run. Guards reject stuffing, hidden text, shrinkage, new claims, any target query getting worse, and any version the independent linear scorer likes less. Built as v1 (`optimize.py`, `ranksim optimize`). "Goal reached" is a PREDICTED sandbox result. It is marketed only with that label until the accuracy gates in section 6 pass.
- **The Change Ledger is the verification loop, not the headline.** After a version ships, Ranksim tracks the REAL rank, grades the frozen tester call with difference-in-differences against a noise band, and retrains on the result. The ledger is how the tester earns trust, and it is the training-data flywheel. It needs no model, so it is also the floor of the product if prediction never passes its gates.
- **Honest status today.** On the pages stored in this repo, the tester's backtest is pairwise 0.485 on 12 queries, a coin flip (0.534 on a larger crawl that is not in the repo). Every SmartCrew target query set tried on 2026-11-03 was THIN: fewer than half of its labeled Google results have stored HTML. Five rules-only optimize runs that day tried 8 rewrites and accepted none. Until G1 passes, the tester is sold as a fast, guarded workbench that prints its own grade, not as a rank forecast.
- **Base case: $1.00M recurring MRR at month 33 (Aug 2029).** That is 1,205 logos at about $833 ARPA (930 Team, 185 Growth, 60 Agency, 30 Enterprise), plus about $70K a month of migration project revenue that is not counted as MRR. M24 is about $626K. Reaching $1M at M24 is a stretch case only. Tester checks and optimize runs are metered limits inside each tier. Gross margin is about 71% on data and infrastructure, and about 66% once support staff are counted.
- **The moat is the record of tester calls that were frozen and graded afterwards.** Each published version's PREDICTED ranks are frozen with a model version before the outcome is known. They are graded with difference-in-differences on change events, both backfilled history and live changes. Optimize outputs are graded as their own slice. The results, misses included, are published weekly. None of this exists yet, so the moat is thin in year 1.
- **First 90 days:** store the labeled pages so target sets stop being THIN, make SmartCrew tenant #1 by removing the hardcodes, wire DataForSEO with cost metering, label 300 queries (about 25K page crawls), measure the noise ceiling, put the tester board in a web UI, and put the tester plus the ledger in front of 10 paying design partners. **Stop marketing PREDICTED ranks and "goal reached" at M9** if CLEARER change-direction precision is below 0.68.

---

## 2. What you actually have today

About 7,550 committed lines in `src/aeo/ranksim`, plus about 2,900 uncommitted lines for the tester and the optimize loop (10,468 lines in the working tree on 2026-11-03), and about 4,580 lines in the AEO track. Most of the infrastructure needed for SaaS already exists. The core loop now exists as a CLI. The rest of the product does not. Line numbers below are for the working tree on 2026-11-03.

| Component | Files | Status | Evidence |
|---|---|---|---|
| **Sandbox tester** | `tester.py` `load_sim_context` :113, `pick_targets` :250, `score_page` :356, `TesterHistory` :546, `board_dict` :639, `watch` :849; `cli.py` `_tester` :502 | Built v1, uncommitted, CLI only | Scores one page version on its target queries against the stored set, in memory. Measured on 2026-11-03: model load 2.4 s, scoring 42 to 196 ms for 2 queries. Saves v1, v2, ... in `data/tester/`. `--watch` re-scores when a file or preview URL changes. `--json --fail-on-regression` exits 1 when a target query is PREDICTED worse than the stored copy, or than v1. Every SmartCrew target set tried was THIN. |
| **Optimize loop** | `optimize.py` `Guards` :108, `check_guards` :225, `judge_candidate` :438, `LlmRewriter` :498, `RulesRewriter` :545, `optimize` :762; `cli.py` `_optimize` :614 | Built v1, uncommitted. The LLM path has run only against test fakes | Keeps the best version, not the last. Stops at the goal, at `--max-rounds` (8), after `--patience` (3) rejected rounds, at `--budget-usd`, or when the rewriter has nothing new to try. On 2026-11-03 the rules rewriter ran 5 times on SmartCrew pages: 8 rounds, 0 accepted. None of the 8 improved the PREDICTED objective. One also failed the stuffing guard and two also made the linear check worse. No LLM key is set in this environment. |
| Bundle what-if (live vs PR HTML) | `whatif.py` `score_many` :150, `worse_than_live` :846, `render_regression` :945 | Works, text only | `pr8-v2-compare.txt` flags 3 ship blockers. `pr8-v3` has 0 BLOCK lines but 3 "clearly worse than live" CLEAR lines, so a grep for BLOCK would pass v3. `test --regression` still exits non-zero only on bad input (`SystemExit(2)` at `cli.py:252` and `:255`), so a bundle cannot fail CI yet. One page can, through `tester --fail-on-regression`. |
| Draft check | `check.py` `check_draft` :160, `assemble_query_docs` :129, `suggest_links` :254, `_top_gaps` :411 | Works. It is the scoring engine under the tester | `serve.py:143` still refuses any query not in the stored export ("No rank was invented"). |
| Competitor share view | `space.py` `build_space` :129 | Works | REAL top-3/top-10 columns (hcss.com holds 8 of 16 top-3 spots). PREDICTED columns are mostly n/a because HTML is missing. |
| SERP ingestion | `serp.py` :41-201, `resolve_serp` :277-300 | Partial | SerpAPI, DataForSEO and Serper are wired; no key is configured. No cost metering. DataForSEO depth is capped at 20 (`serp.py:144`). No AI Overview or AI Mode parsing. |
| Rank tracker | `loop.py` `track_ranks` :386 | Partial | Stores only our best organic position at depth 10, through a SmartCrew-bound `is_our_url`. No link to a page version, so a tester call cannot be graded yet. |
| Model | `lambdamart.py`, `features.py` (34 features), `groundtruth.py` | Works, weak | LightGBM lambdarank with `min_data_in_leaf=1` on about 135 rows. `hash_embed` is a hashing bag of words. Authority is 0.0 when missing (`features.py:276`). |
| Linear validator | `score.py` `HAND_WEIGHTS`, `score_pages` | Works | An independent hand-weighted scorer. The tester shows it next to every PREDICTED rank and the optimize loop rejects any version it likes less. On the PR8 v3 compare page it puts SmartCrew 1 of 5 for "hcss alternative" where LambdaMART puts it 5 of 5. |
| Confidence and noise | `confidence.py`, `noise.py` | Present, not audited | The noise ceiling has never been measured on real repeat pulls. |
| LLM layer | `llm.py` `CachingLlm` :167-243 | Works | Content-hash cache plus a JSONL cost log. `LIST_RATES` prices only gpt-4o-mini and claude-3-5-haiku; any other model logs as unpriced, and `optimize --budget-usd` then stops. The Anthropic client caps replies at 1,200 tokens (`llm.py:145`), which may cut off a rewrite of a long page opening. Cost per check and per optimize run has never been measured. |
| Crawler | `crawl/fetch.py`, `urls.py`, `robots.py`, `extract.py` | Works | SSRF-safe and re-validates every redirect hop. One gap remains: DNS rebinding (`urls.py:88`). `jsrender.py` is a 38-line optional stub. The tester fetches preview URLs through it. |
| Queue and store | `jobs.py`, `worker.py`, `db.py` | Works | Postgres `SKIP LOCKED`, tsvector and pgvector. No ANN index. |
| AEO sandbox | `pipeline.py`, `sim/fanout.py`, `providers/llm.py` :28 | Works in mock mode only | `citation_share=0.8` is a mock artifact. It measures our own RAG pipeline, not any real engine. |
| AI-answer parsing and calibration | `sim/citations.py`, `sim/calibrate.py` | Partial | Calibration takes manual POSTs only, and covers no Gemini and no AI Mode (`calibrate.py:14-20`). |
| Demo server | `serve.py` | Works, unsafe to expose | Binds 0.0.0.0 with no auth. The check form (`serve.py:146-148`) calls `_fetch_html` on any URL a visitor submits. It does not show the tester board. |

**Tests and reproducibility**
- 2026-11-02, fresh clone of the committed code: `pip install -e '.[dev]' && pytest -q` passes all **63 tests**.
- 2026-11-03, working tree with the uncommitted tester and optimize work: **124 tests pass**, 61 of them in `tests/test_tester_optimize.py`.
- `python -m aeo.ranksim evaluate smartcrew/serp.csv` gives pairwise **0.485**, NDCG@10 0.814, Spearman 0.032, top-3 0.917, on 12 queries and 33 pairs. The tester and optimize print the same backtest.
- In a fresh clone only 35 of 135 labeled URLs have stored HTML; the other 100 are uncovered. The documented 0.534 needs the larger stored crawl (122 rows), which is not in the repo. Today, nobody else can reproduce the headline number.

**Accuracy** (`docs/accuracy.md`, `ACCURACY_PLAN.md`):

| Measure | Value |
|---|---|
| Leave-one-query-out pairwise (16 queries, 414 pairs) | **0.534** (0.5 is a coin flip) |
| NDCG@10 / Spearman / top-3 hit | 0.695 / 0.075 / 0.500 |
| Pairs with score gap ≥ 2.0 | 0.716, on 33% of pairs |
| Fresh-clone `evaluate`, demo startup and tester backtest (12 queries, 33 pairs) | Pairwise **0.485**, worse than a coin flip. 100 of 135 labeled rows have no stored HTML. The tester says a PREDICTED move here "is a smoke test, not evidence of a Google move". |

**Single-tenant hardcodes.** All of these must move to tenant config:

- `__init__.py:6`
- `check.py:260`
- `space.py:135`
- `serve.py:27-29, 59-67`
- `lambdamart.py:369-370`, plus its uses in `whatif.py:20, 113, 333-438`
- `whatif.py:258, 470-557, 796-838, 870-939`
- `cli.py:36, 62-189, 379, 417, 425, 718, 853, 896`
- `tester.py:113` (default `smartcrew/linkgraph.json`), `:493` (`seed_origin` reads `smartcrew/pr8_v3`), `:938` (targets from SmartCrew's `keywords.csv`)
- `llm.py:121-122`
- `bootstrap.py:16`
- `scripts/ai_eval.sh`
- `loop.py` `is_our_url`

**Bottom line:** the honesty labeling, now including the tester board, the crawler, the queue and `space.py` are product-grade. The tester and optimize are a working v1 CLI, not a product yet. Prediction is not product-grade, and the optimizer makes that weakness easier to exploit (section 6).

---

## 3. The product

**Name:** Ranksim.

**One-liner:** Ranksim is a rank tester for B2B SaaS pages. Edit a page and see in seconds how it is PREDICTED to rank on your top searches against the pages that rank there now, or let it rewrite the page until it is PREDICTED top N. Then it tracks what REAL Google did and publishes how often the tester was right.

**Core loop**

1. **Edit, or let `optimize` rewrite.** A person edits a draft or a preview deploy. Or `optimize` proposes versions: the rules rewriter edits the title, H1, one H2 and a visible date, and the LLM rewriter edits the title and the page opening. Guards reject stuffing, hidden text, shrinkage, new claims and model instructions.
2. **Sandbox re-index in seconds.** The tester puts that version into the stored competitor set for each target query, in place of the stored copy of the URL, or added when the URL is not in that set, and re-scores it. Measured on SmartCrew: 2.4 s to load the models, then 42 to 196 ms per version for 2 target queries. Google's own crawl and indexing are not in our control. Step 5 records what Google did once it did.
3. **PREDICTED rank on your top searches.** One row per target query: stored or live copy, previous version, this version, the change, UNSURE/CLEARER, the linear check, the stored set size (THIN when under half), and the biggest gap as a fix. The backtest is printed on every board. A PREDICTED regression can fail CI.
4. **Publish the best version.** A person reviews `best.html` for accuracy and wording. Nothing publishes itself.
5. **REAL rank tracked.** Daily Google pulls and GSC record what the published version actually did. Competitor pages that change on the same SERP are re-crawled, so the stored set stays current.
6. **Graded.** The tester call for the published version was frozen with its model version before any outcome existed. It is graded at +7, +14, +28 and +56 days:
   - Google: difference-in-differences against untouched pages on the same site and against competitors on the same SERP, with a published noise band.
   - AI: topic level only, never attributed to a single page.
7. **Retrained.** The model retrains on the graded events. The tester's backtest and the calibration ledger are published, misses included. The next board is scored by the retrained model.

Two numbers stay separate and are never blended: **Google share** (REAL top-3 and top-10 share across tracked queries, which `space.py` already computes) and **AI citation share** (the fraction of sampled answers that cite the domain, per topic, with at least 20 prompts, 3 repeats, a 4-week window and a 95% binomial interval).

Provable facts (noindex, a canonical moved, a 404 without a redirect, an inlink collapse on a page that ranks) can fail a merge by default. The PREDICTED regression gate (`tester --fail-on-regression`) is opt-in in hosted CI and posts a warning, not a failed status, until G3 passes.

**Who uses it and when**

| Who | When | What they see |
|---|---|---|
| SEO/content lead (main user) | Every draft; when a page sits below its goal; Monday ledger email | The tester board on each version; optimize runs; what last month's published versions did |
| VP Marketing (buyer) | Monthly, QBR, board prep | Google and AI share by topic vs competitors; the tester's published hit rate |
| Marketing-site engineer (Growth tier) | Every PR or preview deploy | `tester --json` on the changed pages, provable facts, blast radius; the PREDICTED gate when the team opts in |
| Agency account manager | Client work; monthly report; migration pitch | Tester and optimize per client workspace, white-label report, Snapshot, Migration Sign-off |

**What the tester shows** (example output from `ranksim tester` on SmartCrew's stored data on 2026-11-03. All ranks are PREDICTED. `draft.html` is the PR8 v3 page with its title cut to "SmartCrew" and a new H1, a deliberately bad edit made for this example. Paths are shortened and a few lines are left out.)

```
$ python -m aeo.ranksim tester draft.html --url https://www.smartcrewhq.com/all-in-one-dirt-work-software/ --fail-on-regression
Sandbox index. Ranks are PREDICTED on the stored competitor set. They are not live Google ranks.
Page: https://www.smartcrewhq.com/all-in-one-dirt-work-software/
This version: v1, draft from file:draft.html, 2195 words, sha1 91d09c147c2b, scored 2026-11-03T18:31:15.
The stored copy of this URL is rank database row https://www.smartcrewhq.com/all-in-one-dirt-work-software/, seeded from smartcrew/pr8_v3/all-in-one-dirt-work-software.html, not crawled from the live site. It is not the live site. The stored column and the change are measured against that copy.
Change is measured against the stored copy.
Backtest on the pages stored now, leave-one-query-out, PREDICTED: pairwise 0.485, NDCG at 10 0.814, Spearman 0.032, top-3 hit rate 0.917. 12 queries, 33 pairs, 100 labeled rows had no stored HTML. Pairwise 0.500 is a coin flip. This backtest is no better than a coin flip, so a PREDICTED move here is a smoke test, not evidence of a Google move. Store more labeled pages to make it useful.

query                          stored  prev  now  change  flag   confidence  linear  stored set   biggest gap
all in one dirt work software  2/3     n/a   2/3  same    same   UNSURE      2/3     2 of 8 THIN  Pages PREDICTED ahead share less text with other pages. Say what is specific to this product.
dirt work contractor software  2/3     n/a   3/3  down 1  WORSE  UNSURE      2/3     2 of 9 THIN  Put the query in the title, near the start.

Better on 0, worse on 1, same on 1, of 2 target queries.
WORSE: dirt work contractor software: PREDICTED 3 of 3, worse than the stored copy at PREDICTED 2 of 3.
THIN: all in one dirt work software: 2 of 8 labeled Google results are stored and scored, 6 have no stored HTML and were left out. A PREDICTED rank on this set says little.
THIN: dirt work contractor software: 2 of 9 labeled Google results are stored and scored, 7 have no stored HTML and were left out. A PREDICTED rank on this set says little.
Saved as v1 in data/tester/<url hash>.jsonl. Model load 2.4 s. Scoring 50 ms.
Failing: 1 target query is PREDICTED worse than the stored copy.
(exit code 1)
```

**What an optimize run shows** (example output from `ranksim optimize` on the committed PR8 v1 build of the same page, 2026-11-03. All ranks are PREDICTED. Some round lines are shortened and a few lines are left out.)

```
$ python -m aeo.ranksim optimize smartcrew/pr8/all-in-one-dirt-work-software.html --url https://www.smartcrewhq.com/all-in-one-dirt-work-software/ --rewriter rules --goal 1
Rapid iteration in the sandbox. Ranks are PREDICTED on the stored competitor set. They are not live Google ranks.
Rewriter: rules rewriter, not an LLM.
Goal: PREDICTED rank 1 or better on every one of 2 target queries, and never last in its stored set.
Start: all in one dirt work software PREDICTED 2/3, dirt work contractor software PREDICTED 2/3. Linear check: all in one dirt work software 1/3, dirt work contractor software 2/3.
The starting page already has "all in one dirt work software" 1.6 times per 100 words.

Round 1: rejected. [...] title set to "Dirt Work Contractor Software | SmartCrew".
  Why rejected: Not better on the objective [...]. The linear check disagrees: its rank sum went from 3 to 4.
Round 2: rejected. [...] put the query at the start of the H1: "Dirt Work Contractor Software: All-in-one dirt work software".
  Why rejected: Not better on the objective: 0 at goal with a PREDICTED rank sum of 4, the best has 0 at goal and 4.
Round 3: rejected. [...] added an H2 "Dirt Work Contractor Software" after the H1.
  Why rejected: Guard: Keyword stuffing: the exact phrase "all in one dirt work software" appears 17 times, 1.7 per 100 words. The limit is 1.5. [...]

query                          start  best  change  linear start  linear best  stored set
all in one dirt work software  2/3    2/3   same    1/3           1/3          2 of 8 THIN
dirt work contractor software  2/3    2/3   same    2/3           2/3          2 of 9 THIN

Stop: 3 rejected rounds in a row, the patience limit.
PREDICTED at the goal on 0 of 2 target queries.
Accepted 0 of 3 rounds. The best version is the starting page, unchanged.
Estimated rewriter cost: $0.0000.
No accepted change. best.html is the starting page. Nothing new to publish.
Reaching the goal here is a PREDICTED rank in the sandbox, not a Google result.
```

What these examples show:
- The loop is fast enough to use on every edit: 2.4 s to load, 50 ms to score.
- The regression gate fires on a bad edit and exits 1, so CI can use it.
- The guards and the linear check do their job: one rewrite was stuffing, one was a version the linear scorer liked less.
- The numbers themselves are weak today. Every set is THIN, the backtest is a coin flip, and at the start the two scorers disagree on "all in one dirt work software" (2/3 vs 1/3). That is why the board prints all of it.
- No SmartCrew run has reached a goal yet, and the LLM rewriter has not run on a real page, because no LLM key is set here. Neither result is shown because neither exists.

**What a ledger entry will look like** (format only. No SmartCrew version has been published and graded yet, so there are no numbers.)

```
Change Ledger: /all-in-one-dirt-work-software/   v[n] published [date]   graded at +28 days
  Frozen tester call (model [version], [date]): dirt work contractor software, PREDICTED [rank] of [n], UNSURE, up
  REAL GSC 28-day avg position: [before] -> [after]
  Untouched pages, same site (median): [delta]   DiD: [delta]   Noise band: ±[band]   -> UP / FLAT / DOWN
  Grade: direction correct / wrong. Source: human edit / optimize (rules) / optimize (LLM)
```

---

## 4. Why now and who it beats

**Why now**
- About 68% of US Google searches were zero-click in early 2026 (SparkToro, via Search Engine Land).
- A randomized study found AI Overviews cut organic clicks by 39.8% (Agarwal & Sen, SSRN, Apr 2026).
- A forced-AI-Mode RCT found an 18.8-point drop in external click-through (Penn/Northeastern, Aug 2026).
- Buyers now have to explain what their content work did across two surfaces.
- The Ahrefs "58%" AI Overview figure is left out because the research sources conflict on what it measures.

**Market map**

| Player | Sells | Price | Scale | Confidence |
|---|---|---|---|---|
| Semrush | Suite + AI Toolkit; owns SplitSignal | Guru $249.95; AI Toolkit ~$99/domain | FY25 revenue $443.6M; AI ARR $4M→$38M; Enterprise ARR $37M across 579 customers (~$64K each); NRR 104%. Adobe deal at $1.9B announced; **close UNVERIFIED** | Sourced |
| Ahrefs | Suite + Brand Radar | Brand Radar $199-699 (sources conflict) | ~$149M 2024 revenue | Secondhand |
| Peec AI | AI visibility monitoring | Starter ~$95 | $10M+ ARR at 16 months, 2,500+ customers (company claim). ~$380/mo ARPA is **derived**, not reported | Sourced / derived |
| Profound | Enterprise AI visibility | Quote | $96M Series C at ~$1B (Feb 2026). $180M Series D at $1.8B **UNVERIFIED** | Sourced / unverified |
| seoClarity / BrightEdge / Botify | Enterprise SEO | $2.5-4.5K/mo; ~$1-12.5K/mo; $75-150K/yr | Not found | Secondhand |
| **SEOTesting.com** | GSC before/after tests | $50/site | Not found | Secondhand. **The direct ledger competitor** |
| SearchPilot / SplitSignal | Split tests | Custom; needs traffic | n/a | Secondhand / unverified |
| Surfer / Clearscope / Frase | Content scores, never validated | Clearscope $129/$399; Frase $39-299 | Surfer ~$15M ARR | Secondhand. **The closest to the tester in the buyer's mind** |
| iQuanti ALPS, Behamics, MarketBrew | Predictive rank claims, no published accuracy | n/a | n/a | MarketBrew UNVERIFIED. **The closest to the tester in method** |
| DIY Actions, InfiniSynapse, Lumar Protect | Rule lint in CI | Free to quote | n/a | Lumar Protect UNVERIFIED |
| ContentKing (Conductor) | Post-deploy alerts | Quote | n/a | UNVERIFIED |
| Otterly | Low-cost AI monitoring | ~$29 | n/a | UNVERIFIED |

**Whitespace Ranksim can own**
1. **A rank tester that prints its own accuracy and is graded in public.** Content scorers and predictive-rank vendors show a score. We found no competitor that publishes held-out accuracy for pre-publish prediction. Ranksim prints the backtest on every board and grades every published call against REAL ranks.
2. **Rapid iteration with guards.** An optimizer that stops at PREDICTED top N, rejects stuffing, hidden text and new claims, and keeps the best version, not the last.
3. **Attribution of changes beyond GSC.** SEOTesting measures one site's GSC history. Ranksim adds:
   - SERP and AI-panel outcomes
   - competitor change events
   - difference-in-differences against untouched pages
   - a published noise band
4. **A ranking-aware pre-merge check.** Rule lint does not know which URLs hold REAL top-10 positions. Ranksim does, and the tester runs on the preview.
5. **Sites too small to split test.** SearchPilot and SplitSignal need traffic; most B2B and vertical SaaS sites do not have enough. The tester needs none.

We do not claim "nobody sells Google + AI together". Semrush and Ahrefs already sell both.

---

## 5. Pricing and the $1M MRR math

**Units.** Three metered units sit inside each tier.
- **Tester check:** one page version re-scored against the stored competitor set. No new SERP pull. A `--watch` re-score counts as one check.
- **Optimize run:** one `optimize` call, up to 8 rewrite rounds (the default `--max-rounds`), each version re-scored by the tester.
- **Live check:** one PR, preview or draft check that also pulls fresh SERPs for the touched queries (the old "draft check").

Prices and logo counts are unchanged from the 2026-11-02 plan, so the MRR table is unchanged. Only limits and cost to serve moved.

**Tiers**

| Tier | Price | Who | Limits |
|---|---|---|---|
| Free | $0 | Any site with GSC connected | 1 domain, 25 queries weekly (Google only). 20 tester checks and 2 optimize runs a month, rules rewriter only. Ledger on its own pages. GSC required; pooled training is opt-in |
| Team | $499/mo | In-house content team at a B2B SaaS | 1 domain, 250 queries daily (top 20 + AIO once parsed), AI panel of 150 prompts, 1,000 tester checks and 40 optimize runs a month, Change Ledger, competitor change feed, 50 live checks, 5 seats |
| Growth | $1,800/mo | Mid-market team shipping 20+ pages a month | 3 domains, 1,000 queries, 500 prompts, 5,000 tester checks and 200 optimize runs a month, PR/preview tester in CI (GitHub, Vercel, Netlify), CMS hooks, API, 300 live checks a month |
| Agency | $1,200/mo | SEO and content agencies | 10 client workspaces (+$35 each), 150 queries each, 200 tester checks and 10 optimize runs a month per workspace, white-label reports, branded Snapshot, Migration Sign-off at partner price, 20% revenue share on referred direct accounts |
| Enterprise | $4,500/mo avg ($54K ACV; floor $3,500) | Multi-brand, multi-locale | 5,000 query-locales, 10 domains, 10,000 tester checks and 500 optimize runs a month, SSO, audit log, warehouse export, calibration report on their own changes, opt-out of pooled training |
| Migration Sign-off | $2,500-5,000 one-time (avg $3,500) | Any site with a redesign or replatform | Staging vs live deterministic diff, blast radius, the tester on every changed page, graded at +7/+14/+28 days |

Extra optimize runs are sold in packs. Pack revenue is not counted in the MRR table below.

$72K enterprise ACV is upside, available only once the ledger shows measured lift.

**MRR at M33 (Aug 2029), base case**

| Tier | Logos | Price | MRR |
|---|---|---|---|
| Team | 930 | $499 | $464,070 |
| Growth | 185 | $1,800 | $333,000 |
| Agency | 60 | $1,200 | $72,000 |
| Enterprise | 30 | $4,500 | $135,000 |
| **Total recurring** | **1,205** | **ARPA ~$833** | **$1,004,070** |
| Migration Sign-off (not MRR) | ~20 a month | $3,500 | ~$70,000 a month |

**Ramp**

| Month | Team / Growth / Agency / Ent | MRR |
|---|---|---|
| M3 (Feb 2027) | 10 design partners at ~$750 | ~$7.5K |
| M6 (May 2027) | 60 / 8 / 3 / 0 | ~$48K |
| M9 (Aug 2027) | 130 / 18 / 8 / 2 | ~$116K |
| M12 (Nov 2027) | 210 / 32 / 12 / 5 | ~$199K |
| M18 (May 2028) | 380 / 65 / 25 / 11 | ~$386K |
| M24 (Nov 2028) | 600 / 110 / 40 / 18 | ~$626K |
| M30 (May 2029) | 820 / 160 / 52 / 26 | ~$877K |
| M33 (Aug 2029) | 930 / 185 / 60 / 30 | **~$1.00M** |

**Other scenarios**
- **Stretch:** $1M at M24. This needs Team churn at 3% and about 1.6x the funnel volume.
- **Downside:** a measurement-only product, where the tester keeps its guards and history but drops PREDICTED ranks from marketing, priced near Peec at $300-400 ARPA, would need 2,500-3,300 logos. It probably stays below $1M MRR within 36 months.

**Funnel at steady state, M24-M33, per month** (all rates are assumptions to test)
- 15,000 free Snapshot runs, each with one tester board on the domain's top page, then 8% create an account (1,200), then 60% connect GSC (720), then 5% convert to Team within 90 days: **36 Team**.
- About 32 more Team from other sources: agency referrals, the GitHub Action, direct trials, outbound.
- **About 68 Team gross adds a month.** At 4% monthly churn on about 770 accounts (about 31 lost), that nets about 37 a month.
- Growth: about 5 Team upgrades plus about 6 sales-assisted deals, so about 11 gross. At 2% churn that nets about 8.
- Agency: about 3.5 gross, 2.5% churn. Enterprise: about 1.6 gross, 1% churn.

**Sales capacity**
- **Enterprise:** 2 AEs hired at M6 (ramped by M10), plus 1 at M15 (ramped by M19). That gives about 63 ramped AE-months by M33, or 5.25 AE-years. At 10 deals per AE-year and 65% attainment, that is about 34 gross wins, or about 30 net.
- **Growth:** 1 sales-assist hire at M9 and 1 at M18. That is about 3.1 AE-years at 40 deals per year and 65% attainment, about 80 deals, plus founder-led deals and Team upgrades.

**CAC payback targets** (gross profit uses the cost to serve below)

| Tier | Gross profit per month | Target CAC | Payback |
|---|---|---|---|
| Team | ~$389 | ≤ $2,000 | ≤ 5.1 months |
| Growth | ~$1,400 | ≤ $13,000 | ≤ 9.3 months |
| Agency | ~$1,047 | ≤ $6,400 | ≤ 6.1 months |
| Enterprise | ~$3,297 | ≤ $40,000 | ≤ 12.1 months |

**Unit costs used.** Billing is budgeted as if DataForSEO charges per 10-result page; per-depth billing is unverified.

| Item | Rate |
|---|---|
| Google SERP with AI Overview, top 20 | 2 pages × $1.20/1K = **$2.40 per 1K queries** |
| Top-100 training pull | 10 pages × $0.60/1K = **$6.00 per 1K queries** |
| AI answer (DataForSEO LLM Scraper floor $0.0012) | **$0.0025**, a 2x buffer |
| Page crawl, including storage | **$1.00 per 1K pages**. This is the largest cost line |
| Tester check | **$0.005**, a budget: one preview fetch ($0.001) plus scoring compute. Not measured in production |
| Optimize run | **$0.10**, a budget: LLM rewrite plus 8 re-scores. A rules-only run is about $0.01 |

**Per optimize run, LLM rewrite cost** (from `llm.py` `LIST_RATES`, USD per 1M tokens, list rates, not an invoice)
- Each round sends at most 12,000 characters of the page opening plus instructions. Assume about 5,000 input tokens and up to 3,000 output tokens per call. These token counts are assumptions, not measurements.
- gpt-4o-mini (the default, direct or through OpenRouter), $0.15 in and $0.60 out: 5,000 × $0.15/1M + 3,000 × $0.60/1M = $0.00255 a call, **about $0.02 for 8 rounds**.
- claude-3-5-haiku, $0.80 in and $4.00 out, with replies capped at 1,200 tokens by `llm.py`: 5,000 × $0.80/1M + 1,200 × $4.00/1M = $0.0088 a call, **about $0.07 for 8 rounds**.
- Add about $0.01 of compute for the re-scores. The hosted product passes `--budget-usd` on every run, so a run stops before a call that would likely pass its cap. A model without a published rate stops the run instead of spending blind.

**Per live check cost** (one PR, preview or draft check with fresh SERPs)
- 10 live SERPs for the touched queries: 10 × 2 pages × $2.00/1K = $0.040
- 40 page fetches (preview, live and uncached competitors): $0.040
- LLM explanation: about $0.010. This is an audit estimate; it has not been measured.
- Scoring compute: $0.005
- **Total: about $0.10 per check.**

**Cost to serve, Team ($499)**
- Daily Google, 7,500 pulls: $18.00
- Weekly top-100: $6.45
- AI panel, 150 prompts × 4 engines × 3 repeats × 4.3 weeks = 7,740 answers: $19.35
- Crawl, about 22K pages: $22.00
- 50 live checks: $5.00
- 1,000 tester checks: $5.00
- 40 optimize runs: $4.00
- LLM narration: $5.00
- Infrastructure: $25.00
- **Total: about $110 (78% GM)**

**Cost to serve, other tiers**
- **Growth: about $400 (78% GM).** Google $72, top-100 $25.80, AI panel $64.50, crawl $73, live checks $30, tester checks $25, optimize runs $20, LLM $20, infrastructure $70.
- **Agency: about $153 (87% GM).** That is $13.75 per workspace × 10, plus $15 shared. Each workspace refreshes 30 queries daily, the rest weekly, runs a biweekly AI panel, and uses $1.00 of tester checks and $1.00 of optimize runs.
- **Enterprise: about $1,203 (73.3% GM).** Google $175, top-100 $64.50, AI panel $193.50, crawl $240, live checks $100, tester checks $50, optimize runs $50, LLM $80, infrastructure $250.
- **Free: about $2.37.** The earlier $2.00, plus 20 tester checks ($0.10), 2 rules-only optimize runs ($0.02), and a monthly crawl of about 250 competitor pages so its tester has a stored set ($0.25).
- **Free Snapshot with a tester board:** about $0.37 a run (30 queries × $2.40/1K plus about 300 page crawls × $1.00/1K), about $5.6K a month at 15,000 runs. This is a marketing cost and is not in the table below.

**Blended gross margin at M33**

| Line | Cost per month |
|---|---|
| Team (930 × $110) | $102.3K |
| Growth (185 × $400) | $74.0K |
| Agency (60 × $153) | $9.2K |
| Enterprise (30 × $1,203) | $36.1K |
| Free (~8,000 active × $2.37) | $19.0K |
| Platform (pooled training, warehouse, $100/mo DataForSEO Backlinks minimum) | $25.0K |
| Payment fees (3%) | $30.1K |
| **Total, data and infrastructure** | **~$296K, so about 71% GM** |
| Add 4 support/CS staff (~$48K) | ~66% GM, fully loaded |

**Path to 75%+ margin:** share one SERP and prompt cache across tenants in the same vertical, share one stored competitor set per SERP across tenants, and lean on the `llm.py` content-hash cache. **Caveat:** LLM cost per check and per optimize run, DataForSEO depth billing and real AI-answer token costs are all unmeasured. Meter them in month 1.

**Funding and team** (assumption: a $3.5M seed; the repo says nothing about funding)

| Quarter | Headcount | Gross burn per month | MRR at end |
|---|---|---|---|
| Q1 (M0-3) | 3: founder, ML/ranking engineer, platform engineer | ~$75K | ~$7.5K |
| Q2 (M3-6) | 5: + integrations engineer, content/GTM | ~$120K | ~$48K |
| Q3 (M6-9) | 8: + 2 enterprise AEs, 1 CS | ~$190K | ~$116K |
| Q4 (M9-12) | 10: + Growth sales-assist, + engineer | ~$240K | ~$199K |

- Net cash used is about $0.8M by the M9 kill gate and about $1.0M by M12, which leaves about $2.5M.
- A Series A is optional at M15-18, at about $4.6M ARR run rate.

---

## 6. The accuracy problem and the moat

**What the price buys at 0.534.** The tester is the product, but its PREDICTED number is not yet worth paying for on its own. Until G1 passes, the price buys things that do not depend on the model being right:
- Re-index and re-score in seconds, with every version saved
- The guards: stuffing, hidden text, shrinkage, new claims, model instructions
- The gap list, the stored set size, and the linear check beside every rank
- Provable-fact checks and blast radius
- The Change Ledger (REAL, difference-in-differences, noise band), which grades every published call
- Google share and AI citation share as two separate numbers
- The competitor change feed, the Snapshot and Migration Sign-off

The PREDICTED rank is always labeled PREDICTED, shown as UNSURE unless a gate below has passed, and printed next to its own backtest. It is never sold as a Google forecast.

**The Goodhart risk.** An optimizer pointed at a model with about 0.5 pairwise accuracy will game it. Each round keeps whatever raises the model's score, so `optimize` searches for the model's blind spots, not for what Google rewards. Once the PREDICTED rank is the target, it stops measuring rank. Three things keep the loop honest:
1. **Guards** (`optimize.py` `check_guards` :225) reject a version before its score counts: term share and exact-phrase rate over the whole body, over the first 8,000 characters the model reads, and over every run of 200 words; repeated blocks and headings; headings inside nav, footer or form; hidden text; link farms and empty anchors; a body that shrinks below 80% of the start; a page without exactly one title and one H1; and new numbers, prices, ratings, review markup or model instructions.
2. **Acceptance rules** (`judge_candidate` :438): no target query may get worse, the independent linear validator (`score.py` `HAND_WEIGHTS`) may not get worse, last place in a stored set never counts as the goal, and the best version is kept, not the last. The validator is a separate scorer, not ground truth. It only makes a version that games one model's quirks harder to accept.
3. **The accuracy gates below.** "Goal reached" is marketed only as a PREDICTED sandbox result until the gates pass: "PREDICTED top 3 in the sandbox", never "top 3 on Google". After G2, the measured precision of such calls may be shown next to it. It is never a promise of a Google rank.

Optimizer outputs also bias the grade. A version chosen because the model scored it highest will look better to that model than it is (winner's curse). So the ledger grades optimize outputs as their own slice, apart from human edits, and no gate counts optimize precision until that slice has its own events.

**Fixing the label cost.**
- 300 queries at top-100 depth costs $0.18 to $6 in SERP fees, depending on billing mode.
- It also requires about 20-25K unique competitor pages to be crawled, because `lambdamart.py:92-97` drops any URL without HTML. That is about $10-30 in fees plus 1-2 engineer-weeks for fetch failures and JS rendering.
- The depth cap at `serp.py:144` must be raised first.
- The same crawl fixes THIN: the tester can only score pages that are stored.

**How the label flywheel is designed**
1. **Backfill on day 1.** GSC holds 16 months of position history per page and query. Pair it with git history, CMS revisions or Wayback snapshots to reconstruct historical change events at onboarding.
   - These events train the model and feed gate G2.
   - They never count toward the BLOCK gate, because no prediction was frozen before their outcome.
2. **Every published tester version is a frozen prediction.** Its PREDICTED ranks, model version and stored set are saved at publish time, before any outcome exists.
3. **Difference-in-differences labels.** The changed URL's delta, minus the median delta of untouched URLs on the same site and of competitors on the same SERP. Exclude ±14 days around confirmed Google core updates.
4. **Bucketed direction labels:** up beyond the noise band, flat, or down beyond the noise band. The band comes from `noise.py` on 28-day GSC average position.
5. **Unbiased evaluation slice.** Once the tester warns, bad versions mostly do not ship, so precision measured on shipped versions is inflated. Overridden and shipped-anyway versions form the only unbiased slice; report it separately. Optimize outputs are a third slice.
6. **Realistic volume.** Expect 1-3 gradeable tenant events per site per month, plus competitor-change events. Plan on about 15-40K events a month at about 600 paying sites, not 100K+. Tester and optimize runs that are never published produce no labels.

**Pitfalls to design around**
- **Label delay:** 28 days, which the backfill offsets.
- **Confounders:** core updates, competitor moves and seasonality.
- **Noise ceiling:** never measured, so no target can be published above it.
- **THIN sets:** a PREDICTED rank among 2 or 3 stored pages says little. The board marks it and the grade should report THIN calls apart.
- **AI-side power:** 3 repeats per prompt gives a standard error of about ±0.29. Even 20 prompts × 3 repeats × 4 weeks gives roughly ±6 points at best. Hence topic-level reporting only.
- **API vs UI:** API answers missed brands about 8% of the time vs UI answers (Surfer's measurement).
- **GSC terms:** whether GSC data may be used for pooled training is **UNVERIFIED**, so training is explicit opt-in. Competitor-change labels do not need GSC.

**Accuracy gates.** Each gate unlocks a claim; nothing is promised before its gate passes.

| Gate | Target date | Bar | Unlocks |
|---|---|---|---|
| G0 | M1 | Make `evaluate` reproduce from a fresh clone (commit the stored crawl or a rebuild script); measure the noise ceiling with repeated pulls; tester target sets for design partners not THIN | Publishing any target at all |
| G1 | M3 | Static pairwise ≥ 0.60 on 300+ queries, top-50 candidates, bucketed labels | CLEARER tags in the tester, with coverage disclosed |
| G2 | M6-M9 | Change-direction precision ≥ 0.70 on CLEARER calls, ≥ 25% coverage, ≥ 1,000 backfilled + live events, leave-one-site-out | Measured precision shown next to "goal reached", which stays labeled PREDICTED; PREDICTED column in paid reports |
| G3 | ~M15 | ≥ 0.80 precision, ≥ 30% coverage, on ≥ 500 held-out **live** frozen events, with the optimize slice reported apart | Opt-in predictive BLOCK in CI (`--fail-on-regression` as a failed status); the predicted half of Migration Sign-off |
| G4 | ~M21 | ≥ 0.80 at ≥ 40% coverage on ≥ 1,000 live events; override slice ≥ 0.75; optimize slice ≥ 0.70 | Predictive BLOCK eligible to be on by default |

No plan number depends on reaching 0.70 overall or 0.85 precision, and neither is promised.

**The moat, stated honestly**
- **Frozen, versioned tester calls with graded outcomes.** A company that starts in 2028 cannot backfill 2027 predictions.
- **A public ledger that includes misses,** including the optimizer's. Incumbents have little incentive to publish theirs.
- **Lock-in from sitting in the edit and deploy path,** through the tester in CI, CMS hooks and the version history.
- **Cost:** per-vertical cache sharing makes each new tenant cheaper to serve.

In year 1 the moat is workflow plus trust. The data advantage only exists if G2 and G3 pass.

---

## 7. Build plan from the existing code

| Action | Files | Change |
|---|---|---|
| **Built (v1, uncommitted)** | `ranksim/tester.py`; `cli.py` `_tester` :502; `check.py` `assemble_query_docs` :129; `tests/test_tester_optimize.py` | Sandbox tester: target picking and pinning, in-memory scoring on the stored set, stored or live baseline, THIN coverage, linear check, gap fixes, JSONL version history, `--watch`, `--json`, `--fail-on-regression` |
| **Built (v1, uncommitted)** | `ranksim/optimize.py`; `cli.py` `_optimize` :614 | Optimize loop: rules and LLM rewriters, guards, acceptance rules, best-not-last, goal, patience, max rounds, `--budget-usd`, `best.html`, `round-NN.html`, `log.json`, `report.txt` |
| **Next** | New web app over `tester.py` | Web UI for the tester board: paste HTML or a preview URL, see the board, compare versions, start an optimize run. Behind auth; replaces `serve.py` as the surface |
| **Next** | GitHub App, Vercel/Netlify webhooks, CMS hooks | Call `tester --json --fail-on-regression` on every preview of a changed page. Post the board as a comment. A warning, not a failed status, until G3 |
| **Next** | `loop.py`, the page-version store, `tester.py` `TesterHistory` | Real-rank grading of optimize outputs and human edits: freeze the published version's tester call, link it to `page_version_id`, grade at +7/14/28/56 days, report the optimize slice apart |
| **Next** | `index_serp.py`, `lambdamart.py` | Store the labeled pages so target sets stop being THIN, then re-measure the backtest the tester prints |
| **Keep, fix** | `llm.py` | Add more models to `LIST_RATES` so `--budget-usd` works beyond two. Size the Anthropic reply cap (1,200 tokens, `llm.py:145`) to the 12,000-character rewrite window. Measure real tokens per optimize round |
| **Keep** | `ranksim/serp.py` | Add per-call cost metering. Raise the depth cap at :144 from 20 to 100. Add AI Overview and AI Mode item parsing |
| **Keep** | `crawl/*`, `extract.py`, `sim/diagnostics.py` | Page-version store with content-hash change detection. Fix DNS rebinding (`urls.py:88`) before the tester fetches customer previews in a hosted service. Add response size caps and an egress proxy. New rules: noindex/robots, sitemap removal, redirect chains, inlink diff |
| **Keep** | `whatif.py`, `check.py`, `space.py` | One verdict enum (FAIL/WARN/PASS × CLEARER/UNSURE). JSON output and exit codes for bundles too. Remove the `our_domain` defaults |
| **Keep, fix** | `lambdamart.py`, `features.py`, `groundtruth.py` | Candidates beyond the ranked set (:92-97); bucketed labels (:65-70); snapshot-date freshness (:87); `min_data_in_leaf` above 1 (:253); inferred intent (:126,134); real embedding (`features.py:9,176`); Common Crawl and backlink authority (:276); close the partial-leak hole (`groundtruth.py:64`) |
| **Keep** | `confidence.py`, `noise.py`, `score.py` | Drive UNSURE/CLEARER and the published noise band. Keep `score.py` as the independent validator |
| **Rebuild** | `loop.py` `track_ranks` :386 | Store full SERPs keyed to `page_version_id`; tenant-configurable ownership; +7/14/28/56-day grader |
| **Keep, extend** | `sim/calibrate.py`, `sim/citations.py`, `sim/diff.py` | Automated, versioned predictions; Gemini and AI Mode; parse real panel answers |
| **Keep** | `jobs.py`, `worker.py`, `db.py` | Per-tenant cost metering; run optimize as a queued job; keep each tenant's models loaded so a tester check does not pay the load time; ANN index later |
| **Kill** | `sim/fanout.py`, `providers/llm.py:28`, `pipeline.py` synthetic share, `providers/search.py`, `examples/run_local.py` | No longer sold as a product claim; the code stays parked |
| **Kill** | `serve.py` as a product surface; `scripts/ai_eval.sh`; the 19-subcommand CLI as the main interface outside CI; any bare "Predicted #N of M" headline without confidence, stored set size and backtest; `hash_embed`; `check.py` `_fix_loop` :485, which discards the HTML it writes and is replaced by `optimize` | |
| **Kill** | Every SmartCrew hardcode (section 2) | Move to tenant config; SmartCrew becomes tenant #1 |
| **Build** | New | Tenancy (tenant, site, page_versions, queries, observations, predictions) with RLS and auth; `ranksim check --base --head` for bundles with JSON/SARIF output and exit codes 0, 1 and 78; GSC OAuth plus backfill; AI panel through DataForSEO; ledger UI and email; Snapshot with a tester board; preview auto-manifest; white-label reports; Stripe with metered tester checks and optimize runs |

**30 / 60 / 90 / 180 days**

**Days 0-30 (to Dec 2, 2026)**
- Commit the tester and optimize v1.
- Make `evaluate` reproducible: commit the stored crawl, or a script that rebuilds it, so a fresh clone gets the documented number instead of 0.485.
- Run `index-serp` on the labeled file so SmartCrew's target sets stop being THIN, and re-measure the tester's backtest.
- Move hardcodes into tenant config, including `tester.py`.
- Wire DataForSEO with cost metering and raise the depth cap.
- Label 300 queries and crawl about 25K pages.
- Apply the `lambdamart` fixes.
- Measure the noise ceiling.
- Run the LLM rewriter on SmartCrew pages with a key and a budget, and measure tokens and cost per run.

**Days 31-60 (to Jan 1, 2027)**
- Tenancy, auth and RLS.
- Web UI for the tester board and optimize runs.
- Page-version store and change detection; tester history moves into it.
- GSC OAuth plus the 16-month backfill.
- Rebuild `loop.py` so each published version is tracked.
- Ledger v1 on SmartCrew.
- DNS-rebinding fix.
- Free Snapshot with one tester board (Google only).

**Days 61-90 (to Jan 31, 2027)**
- 10 paying design partners using the tester weekly.
- Preview hooks that call `tester --json --fail-on-regression`, as warnings.
- Weekly ledger email.
- Competitor change feed.
- AI Overview parsing.
- Publish the G1 result and the first public ledger.

**Days 91-180 (to May 1, 2027)**
- Real-rank grading of the first published optimize outputs, reported as their own slice.
- Topic-level AI panel with confidence intervals.
- GitHub App and Vercel integration (deterministic FAIL, blast radius, tester board).
- Migration Sign-off SKU.
- Stripe with metered tester checks and optimize runs.
- Agency white-label.
- Pooled difference-in-differences model v1.
- Measure G2.
- Hire 2 AEs at M6.

---

## 8. Go-to-market

**First 10 customers (M0-M3)**
- SmartCrew converts to paid.
- Add 9 B2B and vertical SaaS content teams at $500-1,000 a month, chosen because they ship 15+ pages a month or have a migration on the calendar.
- **Do not sign SmartCrew's direct competitors** (hcss.com, stackct.com and others in its SERP) without SmartCrew's agreement.
- Every design partner agrees to let us publish anonymized ledger accuracy, including the tester's misses.

**First 100 (M3-M12)**
- **Free Snapshot with a tester board:** REAL Google share for any domain, 30 queries against 5 competitors, plus one PREDICTED board on the domain's top page, with its backtest shown.
- **Weekly public calibration ledger,** with human edits and optimize outputs reported apart.
- **Monthly "Construction Software Search Share Index."** Stay in one vertical until it has a published ledger.
- **Migration Sign-off,** sold directly and through agencies.
- **Agency program:** a 20% revenue share and a free workspace for the agency's own site.
- **A free GitHub Action with commodity rules only.** The tester, the ranking-aware rules and the blast radius need SERP data and stay hosted and paid.
- **SDR signals:** AI Overviews showing on a prospect's head terms, a migration announcement, a GEO hire.

**Positioning**
- **Against Surfer, Clearscope and Frase:** "they give a content score; we show a PREDICTED rank against the pages that rank now, print our accuracy on every board, and grade it against REAL Google."
- **Against MarketBrew and other predictive-rank claims:** "we publish held-out accuracy and every miss."
- **Against Profound and Peec:** "they show where you are; we let you test the next version and show what each change did."
- **Against SEOTesting:** a tester before publishing, plus SERP and AI outcomes, competitor changes, difference-in-differences and frozen predictions after, vs before/after on GSC alone.

**Channels**

| Channel | Starts | Role |
|---|---|---|
| Founder-led sales | M0 | First 10 customers, first enterprise pipeline |
| Snapshot with tester board + ledger content | M2 | Main source of self-serve Team signups |
| Agencies | M4 | Workspaces, migrations, referrals |
| GitHub/Vercel marketplaces | M5 | Growth tier and developer installs |
| AEs (enterprise, then Growth) | M6 / M9 | Sales-assisted pipeline |

**SmartCrew case study** (publish only with permission)
- Accuracy going from 16 to 300 queries, re-measured, misses included.
- Tester calls on SmartCrew pages vs what REAL ranks did at +28 days.
- If PR8 ships: what the tester said about v2 and v3 vs what REAL ranks did at +28 days.

---

## 9. Milestones and kill criteria

| Month | MRR | Logos | Team churn | Data / accuracy |
|---|---|---|---|---|
| M3 | ~$7.5K | 10 | n/a | G1 measured; ≥1,000 backfilled events; design-partner target sets not THIN |
| M6 | ~$48K | 71 | ≤5% | Public ledger live, with tester calls graded |
| M9 | ~$116K | 158 | ≤5% | **G2 decision** |
| M12 | ~$199K | 259 | ≤4.5% | Live events ≥ 2K/month |
| M18 | ~$386K | 481 | ≤4% | G3 measured, optimize slice reported apart |
| M24 | ~$626K | 768 | ≤4% | 15-40K events/month |
| M33 | ~$1.00M | 1,205 | ≤4% | G4 measured |

**Kill or pivot triggers**

| When | Trigger | Action |
|---|---|---|
| M6 | Fewer than 6 of 10 design partners renew at list price, or Free→Team below 2% of GSC-connected free accounts | Stop pushing self-serve Team; sell Growth and Migration Sign-off |
| M6 | Fewer than half of design partners score 3+ versions a week in the tester | The tester is not the hook. Lead with the ledger and the deploy check |
| M9 | CLEARER change-direction precision below 0.68 on ≥500 events, or pooled static pairwise below 0.62 | Remove PREDICTED ranks and "goal reached" from all pricing and marketing. The tester stays as a guarded edit workbench with version history and provable-fact checks, plus the ledger (the downside case) |
| M9 | Fewer than 25% of Git installs run 3+ checks within 14 days | Demote the PR gate to migration-only |
| M12 | MRR below $150K, or Team churn above 5% a month | Go Growth/Enterprise plus migrations only; cut burn |
| M12 | Optimize outputs grade worse than human edits on the same pages for 2 months | Stop selling optimize as a rank lever; keep it as a draft assistant behind the same guards |
| Any month | Agency workspace cost above $15; any tier's cost above 30% of price for 2 months; measured AI answer cost above $0.005; measured optimize run cost above $0.10 | Cut refresh frequency, repeats or rounds, switch model, or reprice |
| Any vertical | CLEARER hit rate below 0.65 for 2 consecutive months | Pull forecasts for that vertical |

---

## 10. Biggest risks

1. **Prediction never earns its gates.**
   - This matters more now that the tester is the product.
   - *Mitigation:* every paid tier is also justified by speed, guards, version history, the ledger and the deterministic gate.
   - The M9 kill rule is public, and the downside case is priced in.
2. **The optimizer games the model (Goodhart).**
   - An optimizer finds what the model rewards, which at about 0.5 accuracy is not what Google rewards.
   - *Mitigation:* the guards, the no-regression rule, the independent linear validator, the gates on "goal reached", and grading optimize outputs as their own slice (section 6). A person reviews every `best.html` before it ships.
3. **Data access and terms of service.**
   - We depend on SERP and AI-answer suppliers.
   - Google legal action against scrapers is **UNVERIFIED**, as is whether GSC terms allow pooled training.
   - API answers differ from what users see in the UI.
   - *Mitigation:* buy through DataForSEO or Bright Data so the scraping risk sits with the supplier, and keep 2 providers live (`serp.py` already abstracts 3).
   - GSC training is opt-in; competitor-diff labels need no GSC.
4. **Incumbent response.**
   - Semrush has $38M AI ARR and the Adobe close is UNVERIFIED. Ahrefs Brand Radar and Profound's capital are both threats. SEOTesting could add SERP data. Surfer or Clearscope could add a rank estimate.
   - *Mitigation:* a published ledger with misses; frozen-prediction history that a late entrant cannot backfill; edit-path and deploy-path lock-in; pricing below the enterprise suites.
5. **The value is episodic, so churn could be high.**
   - Teams may buy for one migration or one rewrite push and leave.
   - *Mitigation:* charge for migrations as projects; make the tester part of every edit, and the weekly ledger and competitor feed the recurring value; model churn at 4%; the M12 trigger above.
6. **Click erosion lowers the value of a ranking.**
   - *Mitigation:* AI citation share is a first-class number from M4. If AI share comes to dominate, shift the product's weight toward it.

**Also:** fetching customer preview URLs is an SSRF surface, and the tester does it on every check. Fix DNS rebinding (`urls.py:88`) and add an egress proxy before any public launch. Never expose `serve.py`. Page text and model text are data, not instructions: the LLM rewriter fences the page and the guards reject new model instructions, and that must stay true in the hosted product.

---

## 11. Sources

**Repo:** `docs/accuracy.md`, `ACCURACY_PLAN.md`, `smartcrew/pr8-v2-compare.txt`, `smartcrew/pr8-v3-compare.txt`, the code audits cited by file and line above, and `tester` and `optimize` runs on the stored SmartCrew data on 2026-11-03.

**Semrush and Adobe**
- https://www.businesswire.com/news/home/20260302904076/en/Semrush-Announces-Fourth-Quarter-and-Full-Year-2025-Financial-Results
- https://searchengineland.com/adobe-semrush-464905
- https://www.eesel.ai/blog/semrush-seo-pricing

**Ahrefs**
- https://getlatka.com/companies/ahrefs
- https://www.get-ryze.ai/blog/ahrefs-brand-radar-pricing-2026

**Peec AI**
- https://sacra.com/c/peec/
- https://enterprisedna.co/resources/ai-pulse/ai-pulse-2026-07-22-berlin-s-peec-ai-hit-10m-arr-sixteen-months-after-launching
- https://trakkr.ai/reviews/peec-ai-review/pricing

**Profound**
- https://www.fortune.com/2026/02/24/exclusive-as-ai-threatens-search-profound-raises-96-million-to-help-brands-stay-visible
- https://aiweekly.co/alerts/ai-search-marketing-startup-profound-raises-180m-series-d-at-18b-led-by-sequoia (UNVERIFIED)

**Enterprise SEO platforms**
- https://costbench.com/software/ai-seo-tools/seoclarity/
- https://www.pepper.inc/blog/enterprise-seo-tools/
- https://www.therankmasters.com/blog/best-enterprise-seo-tools

**Testing tools**
- https://seotesting.com/blog/semrush-alternatives/
- https://searchpilot.com/pricing
- https://subscribed.fyi/splitsignal/reviews/

**Content tools and predictive claims**
- https://costbench.com/compare/clearscope-vs-frase/
- https://costbench.com/changelog/surfer-seo-price-decrease-2026-03/
- https://costbench.com/compare/semrush-ai-vs-ahrefs-ai
- https://searchengineland.com/the-secret-behind-seo-success-estimate-and-optimize-your-contents-rank-as-you-create-it-345719
- https://behamics.com/?p=8116
- https://www.frase.io/blog/best-geo-tools-2026

**SEO in CI**
- https://infinisynapse.com/en/tool/seo-ci-cd
- https://dev.to/datawinder/how-a-successful-deploy-silently-ruined-our-seo-and-how-we-solved-it-in-cicd-1ch0

**Clicks and AI answers**
- https://searchengineland.com/google-zero-click-searches-reach-68-in-early-2026-study-479717
- https://surferseo.com/blog/llm-scraped-ai-answers-vs-api-results
- https://authoritytech.io/curated/measure-geo-like-a-distribution-not-a-ranking-2026
- https://pixis.ai/blog/why-your-geo-dashboard-isnt-moving-the-needleand-what-to-build-instead/

**Data suppliers**
- https://dataforseo.com/apis/serp-api/pricing
- https://dataforseo.com/llm-mention-visibility-api
- https://dataforseo.com/help-center/backlinks-api-pricing-explained
- https://brightdata.com/use-cases/seo-ai-visibility
- https://commoncrawl.org/blog/host-and-domain-level-web-graphs-nov-dec-2018-jan-2019
- https://coldiq.com/blog/serper-pricing
- https://apiserpent.com/blog/serpapi-pricing-explained

**Cited by name; URL not captured in the research pass:** Agarwal & Sen, AI Overviews RCT (SSRN, Apr 2026); Penn/Northeastern AI Mode field experiment (Aug 2026).
