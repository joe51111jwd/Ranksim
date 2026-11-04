# Ranksim: the $1M+ MRR version

*Plan date: 2026-11-02. Revised 2026-11-03 after the founder's correction that the product is the tester. Revised 2026-11-04 after the project tester, the AI engines, the local web UI and the accuracy lab were built: sections 1, 2, 3, 6 and 7 are rewritten, section 5 gains a cost check, and section 9's accuracy triggers use the new gate metric. Pricing and the MRR math are unchanged. Audience: the founder. Labels: **REAL** means pulled from Google or an AI engine. **PREDICTED** means a model output. **UNVERIFIED** means a single unconfirmed or secondary source. Funnel rates, churn and CAC are assumptions to test with design partners; none of them are sourced.*

---

## 1. TL;DR

- **The product is the project tester, and it is built.** The founder's version: "these are my key searches. It runs the searches through Google, pulls the top 10 pages for all those searches ... now you upload your page and you can see how it fares on all those searches." That is what exists. You add key searches. One button (Pull now) records Google's REAL top 10 for each, stores every page on them and trains a mini search engine. You upload or paste a page and see, on every search, whether it is on topic, where it is PREDICTED to land among the REAL results, which pages and sites sit above it and why, and what ChatGPT, Claude, Perplexity and Gemini cite. Versions are saved and compared, and CI can fail a version that stops being on topic. It runs as a CLI and a local web UI (`project.py`, `serve.py`). On SmartCrew's 16 searches a test takes about 3 s, a repeat pull about 10 s and a cold pull 37.7 s, well inside the founder's five-minute goal.
- **Honest accuracy: it predicts and proves its hit rate. It does not know.** No tool can know Google's ranking before Google ranks a page, because Google uses links, site reputation and clicks that are not on the page. The accuracy lab's insertion backtest hides a REAL result, predicts its slot, and never trains on that search. On SmartCrew the headline slot (site history) misses by 1.83 places on average [1.60, 2.02] and lands within 2 places 68% of the time. Always guessing the middle misses by 2.02 (62%), and the old content-model slot by 2.57. For brand-new sites: 1.43 against 2.39 for the middle. For a new page on an established site, which is SmartCrew's case: 1.88 against 1.92, no better than the middle. No on-page model beats guessing the middle, so the tester cannot yet say whether an edit moves you up. The board prints all of this.
- **The scorecard is how it earns the right to say it knows.** Publish, then track: freeze each tested version's PREDICTED slots, pull again after publishing, and grade each call against the REAL position and against the middle guess, misses included. This is the Change Ledger of earlier drafts, now graded with the insertion metric. It is not built yet; it is the next build, together with a SERP key and deeper results.
- **Not run yet.** No Google, AI engine or LLM key exists in this environment. The live Google pull, the four engines and the simulation have run only against fakes. Bing Copilot has no public citation API and is not covered. The web UI runs on one computer with no login.
- **Base case unchanged: $1.00M recurring MRR at month 33 (Aug 2029).** That is 1,205 logos at about $833 ARPA (930 Team, 185 Growth, 60 Agency, 30 Enterprise), plus about $70K a month of migration project revenue that is not counted as MRR. M24 is about $626K. Reaching $1M at M24 is a stretch case only. Tester checks and optimize runs are metered limits inside each tier. Gross margin is about 71% on data and infrastructure, and about 66% once support staff are counted. **One cost decision is open (section 5):** the cost lines assume DataForSEO's LLM scraper for AI answers. The engine APIs built now cost at least 3.65 times as much per answer in search fees alone, which would take the M33 blended margin from about 71% to about 61% before token costs.
- **The moat is the record of frozen slots graded afterwards.** Each published version's PREDICTED slots are frozen with the engine version before the outcome is known, then graded against REAL positions and published weekly, misses included. None of this exists yet, so the moat is thin in year 1.
- **First 90 days:** buy a SERP key and run the first live pull; pull positions 11 to 100 and repeat pulls to measure the noise floor; build the scorecard; commit the accuracy lab as a script that reruns on every project; host the web UI with tenancy and auth; put it in front of 10 paying design partners. **Stop marketing PREDICTED slots at M9** if the pooled insertion backtest (G1) or the live scorecard (G2) does not beat always guessing the middle beyond noise.

---

## 2. What you actually have today

On 2026-11-04 `src/aeo/ranksim` is 20,196 lines. All of it is committed on the branch `claude/bold-einstein-oi5qoj`. Line numbers below are as of 2026-11-04.

| Component | Files | Status | Evidence |
|---|---|---|---|
| **Project tester (the product)** | `project.py` `create_project` :847, `import_serp_csv` :1052, `pull` :1176, `borrow_real_results` :1461, `_crawl_step` :1628, `load_project_context` :2044, `test_page` :2217, `_score_search` :2521, `_summary` :3003; `cli.py` `_add_project_parser` :284, `_project` :364 | Built and committed. CLI and local web UI | A pull runs timed steps (google, engines, crawl, train, save) and saves `google.json`, `pull.json` and the trained engine per pull. On SmartCrew: repeat pull 10.1 s, cold pull 37.7 s, `project test` 3.0 to 3.4 s wall for all 16 searches. REAL results keep their source and pull time; the committed file is labeled "pull date not recorded". Without a Google key, a pull reuses saved, imported or another project's REAL results and says Google was not asked. |
| **On-topic check** | `relevance.py` `TopicModel` :152, `todo_list` :383 | Built | Calibrated per project from the REAL top 10 pages: search words used, TF-IDF topic match, repetition. Leave-one-out on SmartCrew: 119 of 120 readable REAL pages pass. A cake recipe is off topic on 16 of 16 searches, SmartCrew's homepage on 3 of 16. Never tested on pages that failed to rank, because the data holds none. |
| **Site-history slot (the headline)** | `site_history.py` `fit_site_history` :128, `site_history_position` :200, `site_history_backtest` :320; `project.py` `_site_slot` :2674 | Built | A two-parameter pairwise logistic on how many of the project's other searches list each site in their REAL top 10. Needs only REAL result lists, so results with no stored HTML still count. Content blind: editing the page does not move it. Reproduces the lab on the 128 stored pages: miss 1.836 vs 2.023 for the middle guess. |
| **Content-model slot** | `project.py` `insertion_position` :2206, `content_insertion_backtest` :2132; `confidence.py` `loo_fit` :49; `lambdamart.py`; `features.py` (34 features) | Kept, labeled "content model (unvalidated)" | Insertion miss 2.5 places vs 2.0 for the middle guess (128 stored results). Order backtest pairwise 0.528 on 458 pairs. Every call UNSURE. Drives version-to-version changes and `--fail-on-content-regression` only. |
| **AI engines, REAL** | `engines.py` `ENGINE_SPECS` :107, rate tables :149 to :163, `ask_engine` :909, `ask_engines` :1150; `project_engines.py` `run_engines_step` :111, `latest_engines` :242 | Built. Tested only against mocks | ChatGPT (OpenAI Responses API, web search), Claude (Messages API, web search tool), Perplexity (Agent API, Sonar), Gemini (Interactions API, Google Search). Without a key: `not run: needs <ENV VAR>` and no call. Empty or refused answers are errors, never "not cited". Bing Copilot is not covered. |
| **AI simulation, PREDICTED** | `engines.py` `simulate_citations` :1865, `agreement_with_real` :1792; `project_engines.py` `attach_ai` :361 | Built. Fake LLM only | Our LLM answers from the stored Google top 10 plus your page, all page text fenced as data, labeled as our model. 96 LLM calls per SmartCrew test at the default 3 runs. Ungraded until REAL engine answers exist; `agreement_with_real` is the grading hook. |
| **Web UI** | `serve.py` `create_app` :624, routes :656 to :903, `GuardMiddleware` :502, `JobManager` :282 | Built. One computer, no login | Pull now with a progress bar per step; Test a page (upload, paste, or preview URL); results, versions, compare, AI engines, CSV import. Binds 127.0.0.1 with a Host allowlist, CSRF on every POST, 5 MB cap, strict CSP. Measured: Pull now 10.8 s, Test a page 3.6 s from click to results. |
| Older single-page tester | `tester.py` `load_sim_context` :116, `pick_targets` :255, `score_page` :361, `TesterHistory` :551, `board_dict` :670, `watch` :887; `cli.py` `_tester` :748 | Works. Superseded by `project test` | Up to 5 target queries per page. Backtest pairwise 0.528 on 458 pairs. |
| Optimize loop | `optimize.py` `Guards` :108, `check_guards` :225, `judge_candidate` :438, `LlmRewriter` :498, `RulesRewriter` :545, `optimize` :762; `cli.py` `_optimize` :860 | Works. Parked as a product claim | It rewrites toward the content model, which the insertion backtest puts behind the middle guess. Not wired into projects. The LLM rewriter has run only against fakes. |
| Bundle what-if | `whatif.py` `score_many` :150, `worse_than_live` :846, `render_regression` :945 | Works, text only | Unchanged since 2026-11-03. |
| SERP ingestion | `serp.py` `resolve_serp` :277; depth cap of 20 at :122, :144 and :198 | Wired, never run live | SerpAPI, then DataForSEO, then Serper. Brave is refused for projects because it is not Google. No cost metering. No AI Overview parsing. Positions 11 to 100 need the cap raised. |
| Rank tracker | `loop.py` `track_ranks` :386, `is_our_url` :65 | Partial | Stores only our best position, through a SmartCrew-bound `is_our_url`. Not linked to tested versions, so the scorecard does not exist yet. |
| LLM layer | `llm.py` `LIST_RATES` :21, Anthropic reply cap :145, `CachingLlm` :167, `resolve_llm` :273 | Works | `LIST_RATES` prices only gpt-4o-mini and claude-3-5-haiku; any other model logs as unpriced. |
| Crawler | `crawl/fetch.py` `_get_capped` :245; `crawl/urls.py` `assert_safe_url` :63 | Works | Respects robots.txt, refuses private, CGNAT and IPv4-mapped addresses, streams with a 2 MB cap. One gap remains: the host is resolved and checked at `urls.py:94`, then httpx resolves it again to connect, so DNS rebinding is still possible. |
| Queue and store | `jobs.py`, `worker.py`, `db.py` | Works | Postgres `SKIP LOCKED`, tsvector and pgvector. Used by the older AEO API, not by projects yet. |
| AEO sandbox (older API) | `pipeline.py`, `sim/fanout.py`, `providers/llm.py` `mock_include_passage` :28 | Parked | `auto` no longer falls back to mock output; a missing key gives `not run: needs ...`. |
| AI-answer calibration (older API) | `sim/calibrate.py` `ENGINE_ALIASES` :13 | Partial | Manual POSTs only; no Gemini engine. Superseded by the REAL engine rows above. |

**Tests and reproducibility**
- 2026-11-04: `python -m pytest -q .` passes **265 tests** in 11.5 s, with no network.
- `python -m aeo.ranksim evaluate smartcrew/serp.csv` gives pairwise **0.524** on 458 pairs, NDCG at 10 0.725, Spearman 0.080, top-3 0.562, on 128 of 135 labeled rows. Seven rows are uncovered (two g2.com HTTP 403, capterra HTTP 404, thedigitalprojectmanager HTTP 403, facebook blocked by robots.txt).
- `data/` is gitignored, so a fresh clone has no stored pages and no projects. The README quickstart rebuilds the SmartCrew project from the committed file in about a minute. A cold crawl today stores fewer pages than the shared database holds (30 of 137 REAL results had no stored HTML in the cold run, and its content backtest was pairwise 0.516 on 306 pairs instead of 0.528 on 458), so a fresh clone does not reproduce the numbers above exactly.
- The accuracy lab's scripts live outside the repo. The simple versions of its two insertion backtests run in the product at every engine load.

**Accuracy** (SmartCrew, 16 searches, REAL file with no pull date; full protocol in section 6):

| Measure | Value |
|---|---|
| Headline slot (site history), lab, new URL, 128 stored pages | Miss **1.83** places [1.60, 2.02]; within 2 places 68% |
| Always guessing the middle | Miss 2.02; within 2 places 62% |
| Old content-model slot | Miss 2.57 [2.19, 2.90]; within 2 places 54% |
| Headline slot on brand-new sites (28 pages) | Miss 1.43 [1.03, 1.81] vs 2.39 for the middle |
| Headline slot on a new page of an established site | 1.88 vs 1.92 for the middle: noise |
| What the board prints today (all 135 REAL results) | Miss 2.0 vs 2.1 for the middle; within 2 places 64% vs 59% |
| Content-model order, `evaluate` | Pairwise 0.524 on 458 pairs (0.5 is a coin flip) |
| On-topic check | 119 of 120 readable REAL pages pass; untested on pages that failed to rank |

**Single-tenant hardcodes.** The project flow has none: `project.py`, `site_history.py`, `relevance.py`, `project_engines.py` and the project routes in `serve.py` take any site and any searches. The only SmartCrew string there is the source label for the committed file (`project.py:79` and `:1166`). Hardcodes remain in the older commands:

- `__init__.py:6` (`OUR_DOMAIN`, used by `loop.py:65`)
- `check.py:262`, `space.py:135`
- `lambdamart.py:346` and `:369-370`
- `llm.py:121-122`, `bootstrap.py:16`, `tester.py:120`
- `engines.py:1335` (the default file for `load_incumbents`)
- `serve.py:67-70` and `:147-155` (the old demo only)
- `whatif.py` (36 mentions), `keywords.py` (62), and the older subcommands in `cli.py` (39 mentions, none in the project parser at :284 to :502)
- `scripts/ai_eval.sh`

**Bottom line:** the project flow, the honesty labeling, the crawler and the local web UI are product-grade for one user on one computer. Prediction is not: the headline slot beats guessing the middle only for sites that are new to the searches, and nothing yet tells a better page from a worse one. Hosting, tenancy, auth, the scorecard and any live data pull do not exist.

---

## 3. The product

**Name:** Ranksim.

**One-liner:** Ranksim tests a page against the searches you want to win before you publish it. Add your key searches; one click pulls Google's REAL top 10 for each and stores every page on them; upload a draft and see, per search, whether it is on topic, where it is PREDICTED to land among the pages there now, which pages and sites sit above it and why, and what ChatGPT, Claude, Perplexity and Gemini cite. After you publish, it tracks what REAL Google did and publishes how often its call was right.

**Core loop.** Steps 1 to 4 are built. Steps 5 to 7 are the scorecard, which is next.

1. **Project.** A site plus its key searches, from the web form or `project create`. Searches can be added or removed later.
2. **Pull now.** One button. The REAL Google top 10 for every search (SerpAPI, DataForSEO or Serper), a crawl of every result page and of your own site's pages, the mini search engine trained on exactly those pages, and the AI engines asked every search. Each step is timed and shown with a progress bar. Measured on SmartCrew: about 10 s for a repeat pull, 37.7 s for a cold one; the Google API step itself has not been timed because no key is set.
3. **Test a page.** Upload, paste or give a preview URL, plus the URL the page will live at. About 3 s for 16 searches. Per search: the on-topic check, the PREDICTED slot among the REAL results with a likely range, the REAL top 10 with your page marked, the pages above you and why, the content model's slot (labeled unvalidated), the REAL AI citations per engine, and an optional PREDICTED AI answer simulation. A to-do list ranks the fixes from the on-topic check.
4. **Iterate.** Every version is saved (v1, v2, and so on) and rescored against the stored copy of the URL and the previous version on the same engine. The compare page puts two versions side by side. `--fail-on-regression` fails a CI build when a version stops being on topic for a search.
5. **Publish.** A person ships the version. Nothing publishes itself.
6. **Scorecard (to build).** The published version's PREDICTED slots are frozen with the engine that made them, before any outcome exists. Later pulls record the REAL position at +7, +14, +28 and +56 days. Each call is graded against always guessing the middle and against the noise band measured from repeated pulls. Results are published, misses included.
7. **Retrain.** Graded launches become training data, and the backtest printed on every board is recomputed.

**What the tester can say today, and what it cannot**

| Question | Today | Basis |
|---|---|---|
| Is my page about this search, the way the ranked pages are? | Yes or no per search, PREDICTED | 119 of 120 REAL top 10 pages pass the check. Never tested on pages that failed to rank |
| Where will it land among the REAL top 10? | A PREDICTED slot, plus or minus 3 places | Miss 1.83 vs 2.02 for the middle guess. For a new page on an established site, no better than the middle |
| Who is above me, and why? | The REAL pages, how many of your searches their site appears in vs yours, and the PREDICTED odds they outrank you | Site history, the only signal with measured lift |
| Will this edit move me up? | No | No on-page model beats guessing the middle (section 6) |
| Will it make page one at all? | Only "off topic means no" | All labeled pages are pages that ranked |
| What do the AI engines cite? | REAL, per engine and search, with a key | Not run here: no keys |
| Would an AI answer cite my page? | A PREDICTED simulation from the Google top 10 | Ungraded until REAL engine answers exist |

**Why the scorecard is how it earns the right to say it knows.** No tool can know Google's ranking before Google ranks the page, because Google uses signals that are not on the page: links, the site's reputation, and clicks. So Ranksim does not claim to know. It claims a measured hit rate, printed on every board. Today that rate comes from backtests on pages that already rank. The scorecard turns it into a rate measured on real launches: the slot frozen before publishing, the REAL position afterwards, both on the record. Only when the scorecard passes its gates (section 6) may the product say it knows where a page will land, and then only with the measured rate next to the claim. Until then the PREDICTED slot is sold as what it is: the best measured guess, with its miss rate.

Two numbers stay separate and are never blended: **Google share** (REAL top-3 and top-10 share across tracked searches, which `space.py` already computes) and **AI citation share** (the fraction of sampled answers that cite the domain, per topic, with at least 20 prompts, 3 repeats, a 4-week window and a 95% binomial interval).

Provable facts (noindex, a canonical moved, a 404 without a redirect, an inlink collapse on a page that ranks) can fail a merge by default. So can `project test --fail-on-regression`, which fires when a version stops being on topic for a search. The content-model gate (`--fail-on-content-regression`) is opt-in in hosted CI and posts a warning, not a failed status, until G3 passes.

**Who uses it and when**

| Who | When | What they see |
|---|---|---|
| SEO/content lead (main user) | Every draft; when a page sits below its goal; Monday scorecard email | The project board for each version; the pages and sites in the way; what last month's published versions did |
| VP Marketing (buyer) | Monthly, QBR, board prep | Google and AI share by topic vs competitors; the tester's published hit rate |
| Marketing-site engineer (Growth tier) | Every PR or preview deploy | `project test --json --fail-on-regression` on the changed pages, provable facts, blast radius |
| Agency account manager | Client work; monthly report; migration pitch | One project per client workspace, white-label report, Snapshot, Migration Sign-off |

**What the tester shows** (a `project test` run on SmartCrew's stored data on 2026-11-04, `--no-save --no-sim`. Everything below is real output. Lines are shortened with [...] and some lines are left out.)

```
$ python -m aeo.ranksim project test smartcrew smartcrew/pr8_v3/compare-hcss-heavyjob-alternative.html --url https://www.smartcrewhq.com/compare/hcss-heavyjob-alternative/ --no-save --no-sim
PREDICTED: your page is on topic for 16 of your 16 key searches. Where it is on topic, your site's track record puts it in Google's top 3 on 0 (that slot comes from your site, not the page text). REAL (pull date not recorded): this exact URL is in the top 10 for 1 of them; some page of your site is for 5.

PREDICTED top 3 on 0 of 16 searches, top 10 on 16 of 16. REAL (pull date not recorded): top 10 on 1 of 16. [...]
The tester places a page among pages that already made Google's top 10, so it cannot say whether a page makes page one at all. An off-topic page is PREDICTED not to.

How accurate it is: In backtests this predictor put a hidden page within 2 places of its REAL Google position 64% of the time (135 REAL results over 16 searches, each search held out). It was off by 2.0 places on average and exactly right 15% of the time. Always guessing the middle is off by 2.1 and within 2 places 59% of the time. [...]

4/16 "hcss heavyjob alternative"
   On topic (PREDICTED): your page uses 3 of the 3 search words (hcss, heavyjob, alternative); pages Google ranks here use at least 1. [...]
   PREDICTED: your page slots in at #4 of 9 (site history), between REAL #3 truein.com [...] and REAL #4 constructionbids.ai [...]. Likely #1 to #7.
   Content model (unvalidated) PREDICTED #5 of 9 [...]. Confidence UNSURE. Linear check PREDICTED #1.
   REAL (pull date not recorded): this URL is #7.
   Check (site history): PREDICTED #4 but REAL #7 (pull date not recorded), 3 places optimistic here.
   REAL top 9 (smartcrew/serp.csv (committed 2026-10-29, pull date not recorded)):
      #1   getonecrew.com  10 HCSS HeavyJob Alternatives: Top Picks for 2026 - OneCrew  (stored)
      #2   reddit.com  Estimating software options? : r/estimators  (stored)
      #3   truein.com  7 Best Heavy Civil Construction Time Tracking Software (2026  (stored)
      >>  YOUR PAGE (PREDICTED #4)
      #4   constructionbids.ai  HCSS Alternatives for Heavy Civil Contractors (2026)  (stored)
      [...]
      #7   smartcrewhq.com [this URL]  HCSS Alternative for Dirtwork Contractors | SmartCrew  (stored)
      [...]
   Why these pages sit above your slot (site history, REAL counts; editing the page does not change it):
      REAL #2 reddit.com: Site history: its site is in the REAL top 10 of 13 other searches, yours in 4, so it gets a 58% PREDICTED chance of outranking you. [...]
         REAL: Search words in the title: theirs 0 of 3, yours (title or main heading) 2 of 3.
```

What this example shows:
- The loop is fast enough to use on every edit: about 3 s for 16 searches.
- The board names the pages in the way and says why, in REAL counts.
- It grades itself where it can. This URL already ranks REAL #7 for this search, and the board says the slot is 3 places optimistic here. That is the scorecard in miniature: a frozen guess next to a REAL outcome.
- The slot comes from the site, not the text. The board says so, and says the content model's own slot is unvalidated.

**What a scorecard entry will look like** (format only. No version has been published and graded yet, so there are no numbers.)

```
Scorecard: /all-in-one-dirt-work-software/   v[n] published [date]   graded at +28 days
  Frozen call (engine [pull id], [date]): all in one dirt work software, PREDICTED slot #[s] of [n], likely #[low] to #[high], on topic
  REAL position at +28 days: #[p] ([source], pulled [date and time])
  Miss: [|s - p|] places   Middle guess miss: [m] places   Noise band: [band] places   -> BEAT MIDDLE / TIED / MISSED
  Source of the version: human edit / optimize (rules) / optimize (LLM)
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
- **Tester check:** one page version tested on all of a project's key searches against the stored REAL top 10, with the AI answer simulation off. No new SERP pull. Scoring took 0.6 s for 16 searches on 2026-11-04; the production cost is unmeasured.
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

**Cost check on what was built (2026-11-04).** The tiers, the MRR table and the cost lines in this section are unchanged. Two things built since then could change what a unit costs, and neither is in the lines above:

- **AI answers.** The AI panel lines assume DataForSEO's LLM scraper at $0.0025 per answer. The engines built in `engines.py` call each vendor's own API instead. Their published per-search fees alone (`SEARCH_FEES`, `engines.py:158`) are $0.010 for ChatGPT, $0.010 for Claude, $0.0025 for Perplexity and $0.014 for Gemini. That averages $0.009125 per answer across the four, 3.65 times the budget, before any token cost (the default ChatGPT model is listed at $10 in and $50 out per million tokens, and search results count as input). Gemini's first 5,000 searches a month are free, which changes the totals below by at most $70 a month. If the hosted AI panel used these APIs, search fees alone would make:
  - Team: AI panel $70.63 instead of $19.35 (7,740 answers), cost to serve about $161 instead of about $110, 68% GM instead of 78%.
  - Growth: $235.43 instead of $64.50 (25,800 answers), cost to serve about $571 instead of about $400, 68% GM instead of 78%.
  - Enterprise: $706.28 instead of $193.50 (77,400 answers), cost to serve about $1,716 instead of about $1,203, 62% GM instead of 73.3%.
  - Blended at M33: about $94.7K a month more (Team, Growth and Enterprise; the Agency panel is not broken out), about $390K in total, so about 61% GM instead of about 71%, before token costs.
  - **Founder decision:** run the hosted panel through DataForSEO (keeps this plan's math, but its answers must first be checked against the engine APIs), or use the engine APIs with cheaper models and fewer prompts or repeats, or reprice. Until that is decided, the plan keeps the DataForSEO line.
- **The PREDICTED AI answer simulation.** One SmartCrew test with the simulation on is 96 LLM calls (16 searches × 3 runs × with and without your page). At gpt-4o-mini's list price and the token sizes the web UI assumes (3,000 in and 300 out per call, `serve.py:1509-1511`), that is about $0.06 per test, against a $0.005 budget per tester check. So the simulation is off by default, as built, and is not part of a tester check. It would be sold as its own metered unit.

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

**What no tool can do.** No tool can know where Google will rank a page before Google ranks it. Google uses signals that are not on the page: links from other sites, the reputation of the whole site, and how searchers click (DOJ trial testimony describes Navboost as a table of about 13 months of clicks; see `docs/accuracy.md`). Ranksim sees none of those directly. So the product's claim is narrower and checkable: it predicts, and it proves its hit rate, on every board.

**What the price buys before any gate passes.** Nothing below depends on the model being right:
- One click for the REAL Google top 10 of every key search, every page stored and dated
- The on-topic check and its to-do list
- The pages and sites above you on each search, with REAL counts and REAL facts
- REAL AI citations per engine and search (with keys)
- Version history, compare, and the on-topic CI gate
- Once built: the scorecard, Google share and AI citation share as separate numbers, the competitor change feed, the Snapshot and Migration Sign-off

The PREDICTED slot is always labeled PREDICTED and printed next to its own backtest. It is never sold as a Google forecast.

**Why no on-page model works yet.** Google's top 10 is already filtered for relevance, so relevance features cannot order pages inside it. Pairwise agreement with Google's order over the 458 SmartCrew pairs, where 0.5 is a coin flip:
- Content signals: title BM25 0.465, embedding similarity 0.463, hand content weights 0.445, LambdaMART 0.524
- Site and link signals: how many other searches list the site 0.684 [0.622, 0.744], corpus PageRank 0.585, corpus domain rank 0.586, Tranco popularity 0.562

More training searches do not help the content model: its pairwise was 0.531 with 4 training searches and 0.524 with 15. The site signal did improve with more searches, from 0.627 to 0.687.

**The accuracy protocol. This is the metric for every gate below.**
1. **Unit: an insertion.** In a held-out search, hide one REAL top 10 result and predict its slot among the search's other REAL results, whose order is known. The true slot is its rank among them. This is exactly what a page test asks.
2. **Never train on the held-out search** (leave-one-search-out). Every tuned choice (model settings, insertion rule, shrinkage) is picked in inner folds over the training searches only.
3. **Settings.**
   - **New URL (S1)** is the headline: every row of the hidden URL is removed from training and from the priors.
   - **New site:** the hidden page's site appears in no other search. This is the honest number for a new customer.
   - **Known site:** a new page on a site that ranks for other searches. This is the honest number for SmartCrew.
   - **S2:** every row of the hidden page's domain removed. This is a stress test for content models.
4. **Metrics per hidden page:** miss in places (MAE), exact, within 1, within 2, and top-3 correct. Always saying "not top 3" already scores 62% on top-3, so that metric is reported but never gated alone.
5. **Baselines:** always the middle slot, (n + 1) // 2, and a random slot.
6. **Intervals:** 4,000 bootstrap resamples of searches. Differences between methods use a paired bootstrap on the same resamples. A method beats a baseline only when the whole 95% interval of the difference is below 0.
7. **Live version (the scorecard):** the same metrics on frozen slots, graded against the REAL position at +28 days, leave-one-site-out, next to the noise band from repeated pulls.
8. **In the product today:** `site_history_backtest` (`site_history.py:320`) and `content_insertion_backtest` (`project.py:2132`) recompute the simple version (no nesting, no intervals) for each project at engine load and print it on every board. The lab's nested, bootstrapped version is a set of scripts outside the repo; committing it is a G0 item.

**Where it stands** (accuracy lab, 2026-11-04, SmartCrew's 16 searches, 128 stored REAL pages, setting S1):

| Method | Miss in places [95% CI] | Within 2 places | Difference vs middle [95% CI] |
|---|---|---|---|
| Site history, the headline slot | 1.83 [1.60, 2.02] | 68% | -0.20 [-0.32, -0.09] |
| Stacker with every component | 1.87 [1.68, 2.04] | 67% | -0.16 [-0.25, -0.07] |
| Always the middle | 2.02 [1.88, 2.17] | 62% | 0 |
| Ridge on page features, nested | 2.05 [1.85, 2.25] | 62% | +0.02 [-0.06, +0.10] |
| LambdaMART, 10 configurations, nested | 2.24 [1.92, 2.51] | 59% | +0.22 [-0.02, +0.40] |
| Content model with isotonic calibration (the best content rescue) | 2.30 [2.02, 2.53] | 58% | +0.28 [+0.07, +0.47] |
| Content model, old default slot | 2.57 [2.19, 2.90] | 54% | +0.55 [+0.25, +0.83] |
| Random | 2.68 [2.48, 2.86] | 53% | +0.65 [+0.61, +0.70] |

By group, for site history:
- **New site** (28 pages): 1.43 [1.03, 1.81] vs 2.39 for the middle, difference -0.96 [-1.38, -0.63]; within 2 places 86%.
- **Known site** (a new page on an established site): 1.88 vs 1.92, difference -0.04 [-0.18, +0.08]. Noise.
- **S2:** 1.97 vs 2.02, difference -0.05 [-0.11, -0.01]. Marginal.
- **The board's own backtest** (all 135 REAL results, including the 7 with no stored copy): 2.0 vs 2.1 for the middle; within 2 places 64% vs 59%; new sites 1.9 vs 2.4 (27 pages); known sites 2.1 vs 2.2 (70 pages).

The stacker's LambdaMART weight came out slightly negative, so the headline is the one-feature site-history model.

**What grows it, in order**
1. **Positions 11 to 100 per search.** About 10 times more pages per search, real negatives, and the only way to test "will it make the top 10" and the on-topic check's false-pass rate.
2. **Before and after observations of the same URL.** Repeated pulls of the same searches, and Search Console positions for the customer's own pages. This is the only way to validate content edits and to measure the noise floor, which is unknown today: with 16 searches, differences under about 0.2 places cannot be detected.
3. **Hundreds of searches across distinct topics.** SmartCrew's 16 are near duplicates of one topic, which is why the site signal's learning curve flattens.
4. **Backlink and authority data per URL and domain.** Link and site signals are the only family with measured signal, and the corpus link graph is sparse (PageRank takes only 18 distinct values).

**The Goodhart risk.** An optimizer pointed at a model that is worse than guessing the middle will game it. That describes today's content model, so `optimize` stays out of the project flow and out of marketing until G1c passes. The headline slot cannot be gamed by page edits, because it ignores the page. The on-topic check can be gamed (a page can repeat search words), which is why it has a repetition floor and why the guards in `optimize.py` (`check_guards` :225, `judge_candidate` :438) stay in force for any automated rewrite:
- stuffing, measured as term share and exact-phrase rate
- hidden text, link farms and empty anchors
- a body that shrinks
- new numbers, prices, ratings, review markup or model instructions
- any version the independent linear scorer likes less

A version chosen because a model scored it highest looks better to that model than it is (winner's curse). So the scorecard grades optimize outputs as their own slice, apart from human edits.

**Fixing the label cost.**
- 300 searches at top-100 depth costs $0.18 to $6 in SERP fees, depending on billing mode.
- The depth cap of 20 in `serp.py` (:122, :144, :198) must be raised first.
- The content model can only learn from pages with stored HTML (`lambdamart.py:92-96` skips the rest), so deeper results also mean about 20 to 25K page crawls for 300 searches. That is about $10 to $30 in fees, plus 1 to 2 engineer-weeks for fetch failures and JS rendering.
- Site history needs no HTML, so it benefits from deeper results at SERP cost alone.

**How the label flywheel is designed**
1. **Backfill on day 1.** Search Console holds 16 months of position history per page and query. Paired with git history, CMS revisions or Wayback snapshots, that reconstructs before and after events at onboarding. These events train and calibrate. They never count toward a gate's live slice, because no slot was frozen before their outcome.
2. **Every published version is a frozen prediction.** Its PREDICTED slots, engine version and REAL result lists are saved at publish time, before any outcome exists.
3. **Grading.** The miss of the frozen slot vs the REAL position, next to the middle-guess miss and the noise band. For edit direction: the changed URL's move, minus the median move of untouched URLs on the same site and of competitors on the same results page. Exclude 14 days either side of confirmed Google core updates.
4. **Unbiased evaluation slice.** Once the tester warns, bad versions mostly do not ship, so precision measured on shipped versions is inflated. Overridden and shipped-anyway versions form the only unbiased slice; report it separately. Optimize outputs are a third slice.
5. **Realistic volume.** Expect 1 to 3 gradeable launches per site per month, plus competitor-change events. Plan on about 15 to 40K events a month at about 600 paying sites, not 100K or more. Versions that are never published produce no labels.

**Pitfalls to design around**
- **Survivorship:** every labeled page today made the top 10. Nothing measured so far says anything about pages that did not.
- **Label delay:** 28 days, which the backfill offsets.
- **Confounders:** core updates, competitor moves and seasonality.
- **Noise floor:** never measured, so no target can be published above it.
- **Content blindness:** a scorecard grade of the site-history slot says nothing about edits. Edit claims need G1c and G3.
- **AI-side power:** 3 repeats per prompt gives a standard error of about ±0.29. Even 20 prompts × 3 repeats × 4 weeks gives roughly ±6 points at best. Hence topic-level reporting only.
- **API vs UI:** API answers missed brands about 8% of the time vs UI answers (Surfer's measurement). Every REAL engine row says so.
- **GSC terms:** whether Search Console data may be used for pooled training is **UNVERIFIED**, so training is explicit opt-in. Competitor-change labels do not need it.

**Accuracy gates.** Each gate unlocks a claim. Nothing is promised before its gate passes. Every bar uses the insertion protocol above.

| Gate | Target date | Bar | Unlocks | Status on 2026-11-04 |
|---|---|---|---|---|
| G0 | M1 | The lab is a committed script that reruns on any project. `evaluate` and the site-history backtest reproduce from a fresh clone (committed snapshot or rebuild script). Two pulls of the same searches at least 7 days apart measure the noise floor. Positions 11 to 100 are pulled for every SmartCrew search | Publishing any accuracy number outside the product | Not met: no SERP key, no repeat pull, lab scripts outside the repo, `data/` gitignored |
| G1 | M3 | Pooled over at least 300 searches from at least 10 projects in different topics, setting S1: the headline slot's miss is below always-middle (whole paired 95% interval below 0) overall **and** in the known-site group; within 2 places at least 70% | The measured hit rate, with its interval and the middle baseline, may be quoted outside the product; the PREDICTED slot appears in paid reports | SmartCrew alone (16 searches, one topic): overall passes at -0.20 [-0.32, -0.09], within 2 places 68%; known-site group fails at -0.04 [-0.18, +0.08] |
| G1c | M6 | On the G1 data, a page-dependent predictor (its slot moves when the text changes) beats the site-history slot beyond noise in the known-site group and beats always-middle in S2 | "Edit the page and see the slot move"; CLEARER tags on content calls; optimize marketed as a PREDICTED rank lever | Not met: no tested content model beats the middle (best: ridge, +0.02 [-0.06, +0.10]) |
| G2 | M6 to M9 | Live scorecard: at least 300 published pages with frozen slots, graded at +28 days, leave-one-site-out. The frozen slot's miss is below always-middle beyond noise on pages that reached the REAL top 10. The share of PREDICTED on-topic pages that reach the REAL top 10, and of PREDICTED off-topic pages that do (from positions 11 to 100), are measured and published | The measured live hit rate is printed next to every PREDICTED slot | Scorecard not built |
| G3 | ~M15 | Edit direction: on at least 500 live frozen before and after pairs of the same URL, the PREDICTED change in slot has the right sign beyond the noise band at least 80% of the time on CLEARER calls, at coverage of at least 30%, with the optimize slice reported apart | Opt-in predictive BLOCK in CI (`--fail-on-content-regression` as a failed status); the predicted half of Migration Sign-off | Not started |
| G4 | ~M21 | At least 80% at coverage of at least 40% on at least 1,000 live pairs; override slice at least 75%; optimize slice at least 70% | Predictive BLOCK eligible to be on by default | Not started |

No plan number depends on G1c, G3 or G4 passing, and none of them is promised.

**The moat, stated honestly**
- **Frozen, versioned slots with graded outcomes.** A company that starts in 2028 cannot backfill 2027 predictions.
- **A public scorecard that includes misses,** including the optimizer's. Incumbents have little incentive to publish theirs.
- **Lock-in from sitting in the edit and deploy path,** through the page test in CI, CMS hooks and the version history.
- **Cost:** per-vertical sharing of pulled results and stored pages makes each new tenant cheaper to serve. `pull` already reuses REAL results another project holds for the same searches.

In year 1 the moat is workflow plus trust. The data advantage only exists if G1 and G2 pass.

---

## 7. Build plan from the existing code

| Action | Files | Change |
|---|---|---|
| **Built** | `ranksim/project.py`; `cli.py` project subcommands (:284 to :502); `tests/test_project.py` | Projects; REAL Google pull and CSV import; borrowing REAL results across projects; crawl with a status per URL; an engine saved per pull; test on every search; versions, compare, JSON output, the on-topic CI gate |
| **Built** | `ranksim/relevance.py`, `ranksim/site_history.py`; `tests/test_site_history.py`, `tests/test_review_fixes.py` | On-topic check with its to-do list; the site-history headline slot with its backtest printed on every board |
| **Built** | `ranksim/engines.py`, `ranksim/project_engines.py`; `tests/test_engines.py`, `tests/test_project_ai.py` | REAL citations from ChatGPT, Claude, Perplexity and Gemini; the PREDICTED simulation; `not run: needs <ENV VAR>` without keys |
| **Built** | `ranksim/serve.py`; `tests/test_serve_project.py` | Local web UI: Pull now, Test a page, results, versions, compare, AI engines, CSV import |
| **Next 1** | A SERP key; `serp.py` :122, :144, :198 | The first live pull. Raise the depth cap from 20 to 100. Meter cost per call. Time the Google step against the five-minute goal at 16 and at 250 searches |
| **Next 2** | New `ranksim/scorecard.py`; `project.py` version history; `loop.py` `track_ranks` :386 | The scorecard: mark a version published, freeze its slots with the engine's pull id, grade against later pulls at +7, +14, +28 and +56 days against the middle guess and the noise band, show it per project, publish it. Grade optimize outputs as their own slice |
| **Next 3** | New `scripts/accuracy_lab.py`, from the lab scripts | Commit the lab. Run it per project and pooled across projects. Report G0 and G1 |
| **Next 4** | `project.py` `pull`; a scheduler | Weekly repeat pulls of every project's searches, for the noise floor and the scorecard |
| **Next 5** | Tenancy, auth, RLS; `jobs.py`, `worker.py` | Run pulls and tests as queued jobs per tenant; keep each tenant's engine loaded; replace the per-process CSRF token and in-memory jobs |
| **Next** | Search Console OAuth plus backfill | Before and after events for training, and the edit-direction data for G3 |
| **Next** | `authority.py` (`RANKSIM_AUTHORITY=dataforseo`) | Backlink and authority data per URL and domain, the only signal family with measured lift |
| **Next** | AI panel data path | The founder decision in section 5: engine APIs as built, or DataForSEO's LLM scraper |
| **Keep, fix** | `crawl/urls.py:94`, `crawl/fetch.py` | Close DNS rebinding (pin the checked address for the connection) before a hosted service fetches customer preview URLs. Add an egress proxy |
| **Keep, fix** | `llm.py` | Add more models to `LIST_RATES` (:21) so costs and `--budget-usd` work beyond two models. Measure real tokens per simulation call and per optimize round |
| **Keep, fix** | `lambdamart.py`, `features.py`, `groundtruth.py` | Only once deep results and backlinks exist: candidates beyond the ranked set (`lambdamart.py:92-96`); fill `orphan`, `link_depth` and `crawl_inlinks` when `build_examples` rebuilds each page (`lambdamart.py:104-124` leaves them out, so they are unset in training); `min_data_in_leaf` above 1 (:253); a real embedding instead of `hash_embed` (`features.py:176`); backlink authority instead of 0.0 (`features.py:276`) |
| **Keep** | `tester.py`, `optimize.py`, `whatif.py`, `check.py`, `space.py` | Older single-page tools. `optimize` stays parked until G1c. `space.py` feeds Google share |
| **Keep** | `confidence.py`, `noise.py`, `score.py` | UNSURE and CLEARER for content calls; the noise band once repeated pulls exist; `score.py` as the independent linear check |
| **Kill** | `sim/fanout.py`, `pipeline.py` synthetic share, `providers/search.py`, `examples/run_local.py` | No longer sold as a product claim; the code stays parked |
| **Kill** | `scripts/ai_eval.sh`; `check.py` `_fix_loop` :481, which discards the HTML it writes; any PREDICTED slot shown without its backtest | |
| **Kill** | The SmartCrew hardcodes in the older commands (section 2) | Projects already take any site; SmartCrew is project #1 |
| **Build** | New | Tenancy (tenant, project, page versions, pulls, frozen slots, observations) with RLS and auth; GitHub App and Vercel/Netlify hooks that run `project test --json --fail-on-regression` on changed pages; a free Snapshot with one project test on the domain's top page; white-label reports; Stripe with metered tester checks |

**What is built now (2026-11-04).** Steps 1 to 4 of the core loop in section 3, end to end, on one computer: project, Pull now, Test a page, versions. 265 tests pass. Nothing has run against a live Google, AI engine or LLM API.

**30 / 60 / 90 / 180 days**

**Days 0-30 (to Dec 2, 2026)**
- Commit the project tester, the web UI and the review fixes.
- Buy a SERP key, run the first live pull on SmartCrew, raise the depth cap and pull positions 11 to 100.
- Pull the same searches again a week later and measure the noise floor.
- Commit the accuracy lab as a script, and make `evaluate` reproducible from a fresh clone (G0).
- Put a key on one AI engine and the simulation, and measure cost per answer and per test.
- Decide the AI panel data path (section 5).

**Days 31-60 (to Jan 1, 2027)**
- Scorecard v1 on SmartCrew: mark published versions, freeze slots, grade on later pulls.
- Tenancy, auth and RLS; host the web UI.
- Close DNS rebinding in the crawler.
- Search Console OAuth plus the 16-month backfill.
- Projects for 10 design partners, in different topics, to start the pooled G1 data.

**Days 61-90 (to Jan 31, 2027)**
- 10 paying design partners testing pages weekly.
- Preview hooks that call `project test --json --fail-on-regression`.
- Weekly scorecard email.
- Competitor change feed from repeated pulls.
- Publish the first G1 measurement and the first public scorecard.

**Days 91-180 (to May 1, 2027)**
- Topic-level AI panel with confidence intervals, and the first agreement numbers between the PREDICTED simulation and REAL engine answers.
- GitHub App and Vercel integration.
- Migration Sign-off SKU.
- Stripe with metered tester checks.
- Agency white-label.
- Backlink data in the model.
- Measure G1c and G2.
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
| M3 | ~$7.5K | 10 | n/a | G0 met and G1 measured; ≥1,000 backfilled events; design-partner projects not THIN |
| M6 | ~$48K | 71 | ≤5% | Public scorecard live, with frozen slots graded; G1c measured |
| M9 | ~$116K | 158 | ≤5% | **G2 decision** |
| M12 | ~$199K | 259 | ≤4.5% | Live events ≥ 2K/month |
| M18 | ~$386K | 481 | ≤4% | G3 measured, optimize slice reported apart |
| M24 | ~$626K | 768 | ≤4% | 15-40K events/month |
| M33 | ~$1.00M | 1,205 | ≤4% | G4 measured |

**Kill or pivot triggers**

| When | Trigger | Action |
|---|---|---|
| M6 | Fewer than 6 of 10 design partners renew at list price, or Free→Team below 2% of GSC-connected free accounts | Stop pushing self-serve Team; sell Growth and Migration Sign-off |
| M6 | Fewer than half of design partners test 3+ versions a week | The tester is not the hook. Lead with the scorecard and the deploy check |
| M9 | G1 fails on the pooled data (the headline slot does not beat always guessing the middle beyond noise in setting S1), or the G2 live scorecard's frozen slots miss by as much as the middle guess or more on ≥300 graded pages | Remove PREDICTED slots and "goal reached" from all pricing and marketing. The tester stays as a REAL top 10 workbench: on-topic check, the pages in your way, version history, REAL AI citations and provable-fact checks, plus the scorecard (the downside case) |
| M9 | Fewer than 25% of Git installs run 3+ checks within 14 days | Demote the PR gate to migration-only |
| M12 | MRR below $150K, or Team churn above 5% a month | Go Growth/Enterprise plus migrations only; cut burn |
| M12 | Optimize outputs grade worse than human edits on the same pages for 2 months | Stop selling optimize as a rank lever; keep it as a draft assistant behind the same guards |
| Any month | Agency workspace cost above $15; any tier's cost above 30% of price for 2 months; measured AI answer cost above $0.005; measured optimize run cost above $0.10 | Cut refresh frequency, repeats or rounds, switch model, or reprice |
| Any vertical | Live scorecard: frozen slots miss by more than the middle guess for 2 consecutive months | Pull PREDICTED slots for that vertical |

---

## 10. Biggest risks

1. **Prediction never earns its gates.**
   - This matters more now that the tester is the product.
   - *Mitigation:* every paid tier is also justified by the one-click REAL top 10, the on-topic check, the pages in your way, version history, REAL AI citations, the scorecard and the deterministic gate.
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

**Also:** fetching customer preview URLs is an SSRF surface, and the tester does it on every check. Fix DNS rebinding (`crawl/urls.py:94`) and add an egress proxy before any public launch. Do not expose `serve.py` beyond one computer until tenancy and auth exist: it has no login. Page text and model text are data, not instructions: the LLM rewriter fences the page and the guards reject new model instructions, and that must stay true in the hosted product.

---

## 11. Sources

**Repo:** `docs/accuracy.md`, `ACCURACY_PLAN.md`, `smartcrew/pr8-v2-compare.txt`, `smartcrew/pr8-v3-compare.txt`, the code audits cited by file and line above, and `tester` and `optimize` runs on the stored SmartCrew data on 2026-11-03.

**2026-11-04 revision:** `project create`, `import-serp`, `pull`, `test`, `show` and `evaluate` runs on SmartCrew's stored data and on scratch copies of it on 2026-11-04; `python -m pytest -q .` (265 passed); the accuracy lab of 2026-11-04 (leave-one-search-out insertion backtest with nested tuning and a 4,000-resample search bootstrap; its scripts are outside the repo, and `site_history.py` reproduces its headline numbers on the stored pages); the engine API shapes and rate tables recorded in `engines.py` (checked against each vendor's documentation on 2026-11-04, never called live).

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
