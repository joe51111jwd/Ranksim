# Ranksim: the $1M+ MRR version

*Plan date: 2026-11-02. Audience: the founder. Labels: **REAL** means pulled from Google or an AI engine. **PREDICTED** means a model output. **UNVERIFIED** means a single unconfirmed or secondary source. Funnel rates, churn and CAC are assumptions to test with design partners; none of them are sourced.*

---

## 1. TL;DR

- **Sell the Change Ledger, not the predictor.** B2B SaaS content and SEO teams get a REAL before/after measurement of every page they and their competitors ship, on Google and in AI answers. Each measurement is graded against a noise band and against untouched pages. The ledger needs no model, so it can be sold today at 0.534 pairwise accuracy. It also produces the training data the model lacks.
- **Add the pre-merge check as a Growth-tier feature, plus a one-time Migration Sign-off ($2.5–5K).** Only provable facts can fail a check: noindex, a canonical moved, a 404 without a redirect, an inlink collapse on a page that ranks. Predictions stay advisory and labeled PREDICTED until published accuracy gates pass.
- **Base case: $1.00M recurring MRR at month 33 (Aug 2029).** That is 1,205 logos at about $833 ARPA (930 Team, 185 Growth, 60 Agency, 30 Enterprise), plus about $70K a month of migration project revenue that is not counted as MRR. M24 is about $626K. Reaching $1M at M24 is a stretch case only. Gross margin is about 73% on data and infrastructure, and about 68% once support staff are counted.
- **The moat is the record of predictions that were frozen and graded afterwards.** Predictions are frozen with a model version before the outcome is known. They are graded with difference-in-differences on change events, both backfilled history and live changes. The results, misses included, are published weekly. None of this exists yet, so the moat is thin in year 1.
- **First 90 days:** make SmartCrew tenant #1 by removing the hardcodes, wire DataForSEO with cost metering, label 300 queries (about 25K page crawls), measure the noise ceiling, and put the ledger in front of 10 paying design partners. **Stop marketing prediction at M9** if CLEARER change-direction precision is below 0.68.

---

## 2. What you actually have today

About 7,550 lines in `src/aeo/ranksim` and about 4,580 lines in the AEO track. Most of the infrastructure needed for SaaS already exists. Most of the product does not.

| Component | Files | Status | Evidence |
|---|---|---|---|
| Bundle what-if (live vs PR HTML) | `whatif.py` `score_many` :150, `worse_than_live` :846, `render_regression` :945 | Works, text only | `pr8-v2-compare.txt` flags 3 ship blockers. `pr8-v3` has 0 BLOCK lines but 3 "clearly worse than live" CLEAR lines, so a grep for BLOCK would pass v3. `cli.py` exits non-zero only on bad input (`SystemExit(2)` at :197 and :200). No exit code reports a regression, so CI cannot fail on it yet. |
| Draft check | `check.py` `check_draft` :116, `suggest_links` :228, `_top_gaps` :378 | Works, web demo only | No `check` CLI command. `serve.py:143` refuses any query not in the stored export ("No rank was invented"). |
| Competitor share view | `space.py` `build_space` :129 | Works | REAL top-3/top-10 columns (hcss.com holds 8 of 16 top-3 spots). PREDICTED columns are mostly n/a because HTML is missing. |
| SERP ingestion | `serp.py` :41-201, `resolve_serp` :277-300 | Partial | SerpAPI, DataForSEO and Serper are wired; no key is configured. No cost metering. DataForSEO depth is capped at 20 (`serp.py:144`). No AI Overview or AI Mode parsing. |
| Rank tracker | `loop.py` `track_ranks` :386 | Partial | Stores only our best organic position at depth 10, through a SmartCrew-bound `is_our_url`. No link to a page version. |
| Model | `lambdamart.py`, `features.py` (34 features), `groundtruth.py` | Works, weak | LightGBM lambdarank with `min_data_in_leaf=1` on about 135 rows. `hash_embed` is a hashing bag of words. Authority is 0.0 when missing (`features.py:276`). |
| Confidence and noise | `confidence.py`, `noise.py` | Present, not audited | The noise ceiling has never been measured on real repeat pulls. |
| LLM layer | `llm.py` `CachingLlm` :167-243 | Works | Content-hash cache plus a JSONL cost log. `LIST_RATES` prices only gpt-4o-mini and claude-3-5-haiku; any other model logs as unpriced. Cost per check has never been measured. |
| Crawler | `crawl/fetch.py`, `urls.py`, `robots.py`, `extract.py` | Works | SSRF-safe and re-validates every redirect hop. One gap remains: DNS rebinding (`urls.py:88`). `jsrender.py` is a 38-line optional stub. |
| Queue and store | `jobs.py`, `worker.py`, `db.py` | Works | Postgres `SKIP LOCKED`, tsvector and pgvector. No ANN index. |
| AEO sandbox | `pipeline.py`, `sim/fanout.py`, `providers/llm.py` :28 | Works in mock mode only | `citation_share=0.8` is a mock artifact. It measures our own RAG pipeline, not any real engine. |
| AI-answer parsing and calibration | `sim/citations.py`, `sim/calibrate.py` | Partial | Calibration takes manual POSTs only, and covers no Gemini and no AI Mode (`calibrate.py:14-20`). |
| Demo server | `serve.py` | Works, unsafe to expose | Binds 0.0.0.0 with no auth. The check form (`serve.py:146-148`) calls `_fetch_html` on any URL a visitor submits. |

**Tests and reproducibility** (checked 2026-11-02 in a fresh clone):
- `pip install -e '.[dev]' && pytest -q`: all **63 tests pass**.
- `python -m aeo.ranksim evaluate smartcrew/serp.csv` gives pairwise **0.485**, NDCG@10 0.814, Spearman 0.032, top-3 0.917, on 12 queries and 33 pairs.
- In a fresh clone only 35 of 135 labeled URLs have stored HTML; the other 100 are uncovered. The documented 0.534 needs the larger stored crawl (122 rows), which is not in the repo. Today, nobody else can reproduce the headline number.

**Accuracy** (`docs/accuracy.md`, `ACCURACY_PLAN.md`):

| Measure | Value |
|---|---|
| Leave-one-query-out pairwise (16 queries, 414 pairs) | **0.534** (0.5 is a coin flip) |
| NDCG@10 / Spearman / top-3 hit | 0.695 / 0.075 / 0.500 |
| Pairs with score gap ≥ 2.0 | 0.716, on 33% of pairs |
| Fresh-clone `evaluate` and demo startup backtest (12 queries, 33 pairs) | Pairwise **0.485**, worse than a coin flip. 100 of 135 labeled rows have no stored HTML. The demo prints "Calls stay UNSURE". |

**Single-tenant hardcodes.** All of these must move to tenant config:

- `__init__.py:6`
- `check.py:234`
- `space.py:135`
- `serve.py:27-29, 59-67`
- `lambdamart.py:369-370`, plus its uses in `whatif.py:20, 113, 333-438`
- `whatif.py:258, 470-557, 796-838, 870-939`
- `cli.py:34, 60-142, 324, 362, 370, 464, 599, 642`
- `llm.py:121-122`
- `bootstrap.py:16`
- `scripts/ai_eval.sh`
- `loop.py` `is_our_url`

**Bottom line:** the honesty labeling, the crawler, the queue and `space.py` are product-grade. Prediction is not.

---

## 3. The product

**Name:** Ranksim.

**One-liner:** Ranksim shows a B2B SaaS team what every page change, theirs and their competitors', actually did to their Google rank and AI-answer citations. It catches provable regressions before they ship, and it only makes predictions it has a published track record for.

**Core loop**

1. **Measure.** Two published numbers, never blended into one:
   - **Google share:** REAL top-3 and top-10 share across tracked queries, which is what `space.py` already computes.
   - **AI citation share:** the fraction of sampled answers that cite the domain, per topic. Each topic needs at least 20 prompts, at least 3 repeats, and a 4-week window, and the number is shown with a 95% binomial interval.
2. **Detect changes.** Content-hash diffs on crawled page versions find changes on the tenant's own pages and on competitor URLs that rank. Sitemap `lastmod` is unreliable, so it is not used as the trigger.
3. **Freeze a prediction** (direction plus UNSURE/CLEARER) with the model version, before any outcome exists.
4. **Grade** at +7, +14, +28 and +56 days:
   - Google: difference-in-differences against untouched pages on the same site and against competitors on the same SERP.
   - AI: topic level only, never attributed to a single page.
5. **Retrain** on the graded events, then **publish** the calibration ledger, misses included.

**Who uses it and when**

| Who | When | What they see |
|---|---|---|
| SEO/content lead (main user) | Monday ledger email; before publishing | What last month's changes did; draft check |
| VP Marketing (buyer) | Monthly, QBR, board prep | Google and AI share by topic vs competitors; ledger summary |
| Marketing-site engineer (Growth tier) | Every PR or preview deploy | Check comment: facts, blast radius, advisory prediction |
| Agency account manager | Monthly client report; migration pitch | White-label report, Snapshot, Migration Sign-off |

**What a check looks like** (illustrative mock-up; URLs and numbers are invented for layout)

```
Ranksim check: FAIL (1 provable regression)   base: live   head: preview, PR #142

FACTS FROM THE DIFF (only these can fail the check)
  FAIL  /compare/vendor-x     canonical changed: self -> /compare
  WARN  /guides/estimating    internal inlinks 14 -> 5 (-64%)

BLAST RADIUS (REAL: Google US top 20 via DataForSEO, pulled 2027-03-02)
  3 changed URLs currently hold 7 top-10 positions on 6 tracked queries

PREDICTED (model v0.4, advisory, cannot fail this check)
  "vendor x alternative"   direction: worse   CLEARER
  "estimating software"    direction: worse   UNSURE
  CLEARER track record: precision [published weekly], coverage [..], n [..]

Graded against REAL ranks at +7, +14 and +28 days after merge.
```

```
Change Ledger, week of 2027-03-08 (illustrative)
/guides/estimating   edited 2027-02-06 (+28 days)
  GSC 28-day avg position: 14.2 -> 9.8  (REAL, +4.4)
  Untouched pages, same site (median): +0.3   DiD: +4.1   Noise band: ±2.1  -> UP
  Frozen prediction (2027-02-06, model v0.2): UP, UNSURE -> graded correct
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
| Ahrefs | Suite + Brand Radar | Brand Radar $199–699 (sources conflict) | ~$149M 2024 revenue | Secondhand |
| Peec AI | AI visibility monitoring | Starter ~$95 | $10M+ ARR at 16 months, 2,500+ customers (company claim). ~$380/mo ARPA is **derived**, not reported | Sourced / derived |
| Profound | Enterprise AI visibility | Quote | $96M Series C at ~$1B (Feb 2026). $180M Series D at $1.8B **UNVERIFIED** | Sourced / unverified |
| seoClarity / BrightEdge / Botify | Enterprise SEO | $2.5–4.5K/mo; ~$1–12.5K/mo; $75–150K/yr | Not found | Secondhand |
| **SEOTesting.com** | GSC before/after tests | $50/site | Not found | Secondhand. **The direct ledger competitor** |
| SearchPilot / SplitSignal | Split tests | Custom; needs traffic | n/a | Secondhand / unverified |
| Surfer / Clearscope / Frase | Content scores, never validated | Clearscope $129/$399; Frase $39–299 | Surfer ~$15M ARR | Secondhand |
| iQuanti ALPS, Behamics, MarketBrew | Predictive rank claims, no published accuracy | n/a | n/a | MarketBrew UNVERIFIED |
| DIY Actions, InfiniSynapse, Lumar Protect | Rule lint in CI | Free to quote | n/a | Lumar Protect UNVERIFIED |
| ContentKing (Conductor) | Post-deploy alerts | Quote | n/a | UNVERIFIED |
| Otterly | Low-cost AI monitoring | ~$29 | n/a | UNVERIFIED |

**Whitespace Ranksim can own**
1. **Attribution of changes beyond GSC.** SEOTesting measures one site's GSC history. Ranksim adds:
   - SERP and AI-panel outcomes
   - competitor change events
   - difference-in-differences against untouched pages
   - a published noise band
2. **Published, pre-registered accuracy.** We found no competitor that publishes held-out accuracy for pre-publish prediction.
3. **A ranking-aware pre-merge check.** Rule lint does not know which URLs hold REAL top-10 positions. Ranksim does.
4. **Sites too small to split test.** SearchPilot and SplitSignal need traffic; most B2B and vertical SaaS sites do not have enough.

We do not claim "nobody sells Google + AI together". Semrush and Ahrefs already sell both.

---

## 5. Pricing and the $1M MRR math

**Tiers**

| Tier | Price | Who | Limits |
|---|---|---|---|
| Free | $0 | Any site with GSC connected | 1 domain, 25 queries weekly (Google only). Ledger on its own pages. GSC required; pooled training is opt-in |
| Team | $499/mo | In-house content team at a B2B SaaS | 1 domain, 250 queries daily (top 20 + AIO once parsed), AI panel of 150 prompts, Change Ledger, competitor change feed, 50 draft checks, 5 seats |
| Growth | $1,800/mo | Mid-market team shipping 20+ pages a month | 3 domains, 1,000 queries, 500 prompts, PR/preview check (GitHub, Vercel, Netlify), CMS hooks, API, 300 checks a month |
| Agency | $1,200/mo | SEO and content agencies | 10 client workspaces (+$35 each), 150 queries each, white-label reports, branded Snapshot, Migration Sign-off at partner price, 20% revenue share on referred direct accounts |
| Enterprise | $4,500/mo avg ($54K ACV; floor $3,500) | Multi-brand, multi-locale | 5,000 query-locales, 10 domains, SSO, audit log, warehouse export, calibration report on their own changes, opt-out of pooled training |
| Migration Sign-off | $2,500–5,000 one-time (avg $3,500) | Any site with a redesign or replatform | Staging vs live deterministic diff, blast radius, graded at +7/+14/+28 days |

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
- **Downside:** a measurement-only product with no prediction, priced near Peec at $300–400 ARPA, would need 2,500–3,300 logos. It probably stays below $1M MRR within 36 months.

**Funnel at steady state, M24–M33, per month** (all rates are assumptions to test)
- 15,000 free Snapshot runs, then 8% create an account (1,200), then 60% connect GSC (720), then 5% convert to Team within 90 days: **36 Team**.
- About 32 more Team from other sources: agency referrals, the GitHub Action, direct trials, outbound.
- **About 68 Team gross adds a month.** At 4% monthly churn on about 770 accounts (about 31 lost), that nets about 37 a month.
- Growth: about 5 Team upgrades plus about 6 sales-assisted deals, so about 11 gross. At 2% churn that nets about 8.
- Agency: about 3.5 gross, 2.5% churn. Enterprise: about 1.6 gross, 1% churn.

**Sales capacity**
- **Enterprise:** 2 AEs hired at M6 (ramped by M10), plus 1 at M15 (ramped by M19). That gives about 63 ramped AE-months by M33, or 5.25 AE-years. At 10 deals per AE-year and 65% attainment, that is about 34 gross wins, or about 30 net.
- **Growth:** 1 sales-assist hire at M9 and 1 at M18. That is about 3.1 AE-years at 40 deals per year and 65% attainment, about 80 deals, plus founder-led deals and Team upgrades.

**CAC payback targets**

| Tier | Gross profit per month | Target CAC | Payback |
|---|---|---|---|
| Team | ~$398 | ≤ $2,000 | ≤ 5 months |
| Growth | ~$1,445 | ≤ $13,000 | ≤ 9 months |
| Agency | ~$1,067 | ≤ $6,400 | ≤ 6 months |
| Enterprise | ~$3,400 | ≤ $40,000 | ≤ 12 months |

**Unit costs used.** Billing is budgeted as if DataForSEO charges per 10-result page; per-depth billing is unverified.

| Item | Rate |
|---|---|
| Google SERP with AI Overview, top 20 | 2 pages × $1.20/1K = **$2.40 per 1K queries** |
| Top-100 training pull | 10 pages × $0.60/1K = **$6.00 per 1K queries** |
| AI answer (DataForSEO LLM Scraper floor $0.0012) | **$0.0025**, a 2x buffer |
| Page crawl, including storage | **$1.00 per 1K pages**. This is the largest cost line |

**Per-check cost** (one PR, preview or draft check)
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
- 50 checks: $5.00
- LLM narration: $5.00
- Infrastructure: $25.00
- **Total: about $101 (80% GM)**

**Cost to serve, other tiers**
- **Growth: about $355 (80% GM).** Google $72, top-100 $25.80, AI panel $64.50, crawl $73, checks $30, LLM $20, infrastructure $70.
- **Agency: about $133 (89% GM).** That is $11.75 per workspace × 10, plus $15 shared. Each workspace refreshes 30 queries daily, the rest weekly, and runs a biweekly AI panel.
- **Enterprise: about $1,103 (75.5% GM).** Google $175, top-100 $64.50, AI panel $193.50, crawl $240, checks $100, LLM $80, infrastructure $250.
- **Free: about $2.**

**Blended gross margin at M33**

| Line | Cost per month |
|---|---|
| Team | $93.9K |
| Growth | $65.7K |
| Agency | $8.0K |
| Enterprise | $33.1K |
| Free (~8,000 active) | $16.0K |
| Platform (pooled training, warehouse, $100/mo DataForSEO Backlinks minimum) | $25.0K |
| Payment fees (3%) | $30.1K |
| **Total, data and infrastructure** | **~$272K, so about 73% GM** |
| Add 4 support/CS staff (~$48K) | ~68% GM, fully loaded |

**Path to 75%+ margin:** share one SERP and prompt cache across tenants in the same vertical, and lean on the `llm.py` content-hash cache. **Caveat:** LLM cost per check, DataForSEO depth billing and real AI-answer token costs are all unmeasured. Meter them in month 1.

**Funding and team** (assumption: a $3.5M seed; the repo says nothing about funding)

| Quarter | Headcount | Gross burn per month | MRR at end |
|---|---|---|---|
| Q1 (M0–3) | 3: founder, ML/ranking engineer, platform engineer | ~$75K | ~$7.5K |
| Q2 (M3–6) | 5: + integrations engineer, content/GTM | ~$120K | ~$48K |
| Q3 (M6–9) | 8: + 2 enterprise AEs, 1 CS | ~$190K | ~$116K |
| Q4 (M9–12) | 10: + Growth sales-assist, + engineer | ~$240K | ~$199K |

- Net cash used is about $0.8M by the M9 kill gate and about $1.0M by M12, which leaves about $2.5M.
- A Series A is optional at M15–18, at about $4.6M ARR run rate.

---

## 6. The accuracy problem and the moat

**Sellable at 0.534.** None of the following uses the model:
- The Change Ledger (REAL, difference-in-differences, noise band)
- Google share and AI citation share as two separate numbers
- The competitor change feed
- Deterministic check rules plus blast radius
- The Snapshot
- Migration Sign-off

Predictions are free, labeled PREDICTED, and shown as UNSURE unless a gate below has passed. They never carry a price.

**Fixing the label cost.**
- 300 queries at top-100 depth costs $0.18 to $6 in SERP fees, depending on billing mode.
- It also requires about 20–25K unique competitor pages to be crawled, because `lambdamart.py:92-97` drops any URL without HTML. That is about $10–30 in fees plus 1–2 engineer-weeks for fetch failures and JS rendering.
- The depth cap at `serp.py:144` must be raised first.

**How the label flywheel is designed**
1. **Backfill on day 1.** GSC holds 16 months of position history per page and query. Pair it with git history, CMS revisions or Wayback snapshots to reconstruct historical change events at onboarding.
   - These events train the model and feed gate G2.
   - They never count toward the BLOCK gate, because no prediction was frozen before their outcome.
2. **Difference-in-differences labels.** The changed URL's delta, minus the median delta of untouched URLs on the same site and of competitors on the same SERP. Exclude ±14 days around confirmed Google core updates.
3. **Bucketed direction labels:** up beyond the noise band, flat, or down beyond the noise band. The band comes from `noise.py` on 28-day GSC average position.
4. **Unbiased evaluation slice.** Once the check warns, bad versions mostly do not ship, so precision measured on shipped versions is inflated. Overridden and shipped-anyway versions form the only unbiased slice; report it separately.
5. **Realistic volume.** Expect 1–3 gradeable tenant events per site per month, plus competitor-change events. Plan on about 15–40K events a month at about 600 paying sites, not 100K+.

**Pitfalls to design around**
- **Label delay:** 28 days, which the backfill offsets.
- **Confounders:** core updates, competitor moves and seasonality.
- **Noise ceiling:** never measured, so no target can be published above it.
- **AI-side power:** 3 repeats per prompt gives a standard error of about ±0.29. Even 20 prompts × 3 repeats × 4 weeks gives roughly ±6 points at best. Hence topic-level reporting only.
- **API vs UI:** API answers missed brands about 8% of the time vs UI answers (Surfer's measurement).
- **GSC terms:** whether GSC data may be used for pooled training is **UNVERIFIED**, so training is explicit opt-in. Competitor-change labels do not need GSC.

**Accuracy gates.** Each gate unlocks a claim; nothing is promised before its gate passes.

| Gate | Target date | Bar | Unlocks |
|---|---|---|---|
| G0 | M1 | Make `evaluate` reproduce from a fresh clone (commit the stored crawl or a rebuild script); measure the noise ceiling with repeated pulls | Publishing any target at all |
| G1 | M3 | Static pairwise ≥ 0.60 on 300+ queries, top-50 candidates, bucketed labels | CLEARER tags in the UI, with coverage disclosed |
| G2 | M6–M9 | Change-direction precision ≥ 0.70 on CLEARER calls, ≥ 25% coverage, ≥ 1,000 backfilled + live events, leave-one-site-out | PREDICTED column in paid reports (beta, still free) |
| G3 | ~M15 | ≥ 0.80 precision, ≥ 30% coverage, on ≥ 500 held-out **live** frozen events | Opt-in predictive BLOCK; the predicted half of Migration Sign-off |
| G4 | ~M21 | ≥ 0.80 at ≥ 40% coverage on ≥ 1,000 live events; override slice ≥ 0.75 | Predictive BLOCK eligible to be on by default |

No plan number depends on reaching 0.70 overall or 0.85 precision, and neither is promised.

**The moat, stated honestly**
- **Frozen, versioned predictions with graded outcomes.** A company that starts in 2028 cannot backfill 2027 predictions.
- **A public ledger that includes misses.** Incumbents have little incentive to publish theirs.
- **Lock-in from sitting in the deploy path,** through a required status check and CMS hooks.
- **Cost:** per-vertical cache sharing makes each new tenant cheaper to serve.

In year 1 the moat is workflow plus trust. The data advantage only exists if G2 and G3 pass.

---

## 7. Build plan from the existing code

| Action | Files | Change |
|---|---|---|
| **Keep** | `ranksim/serp.py` | Add per-call cost metering. Raise the depth cap at :144 from 20 to 100. Add AI Overview and AI Mode item parsing |
| **Keep** | `crawl/*`, `extract.py`, `sim/diagnostics.py` | Page-version store with content-hash change detection. Fix DNS rebinding (`urls.py:88`). Add response size caps and an egress proxy. New rules: noindex/robots, sitemap removal, redirect chains, inlink diff |
| **Keep** | `whatif.py`, `check.py`, `space.py` | One verdict enum (FAIL/WARN/PASS × CLEARER/UNSURE). JSON output. Remove the `our_domain` defaults |
| **Keep, fix** | `lambdamart.py`, `features.py`, `groundtruth.py` | Candidates beyond the ranked set (:92-97); bucketed labels (:65-70); snapshot-date freshness (:87); `min_data_in_leaf` above 1 (:253); inferred intent (:126,134); real embedding (`features.py:9,176`); Common Crawl and backlink authority (:276); close the partial-leak hole (`groundtruth.py:64`) |
| **Keep** | `confidence.py`, `noise.py` | Drive UNSURE/CLEARER and the published noise band |
| **Rebuild** | `loop.py` `track_ranks` :386 | Store full SERPs keyed to `page_version_id`; tenant-configurable ownership; +7/14/28/56-day grader |
| **Keep, extend** | `sim/calibrate.py`, `sim/citations.py`, `sim/diff.py` | Automated, versioned predictions; Gemini and AI Mode; parse real panel answers |
| **Keep** | `llm.py`, `jobs.py`, `worker.py`, `db.py` | Per-tenant cost metering; update `LIST_RATES`; ANN index later |
| **Kill** | `sim/fanout.py`, `providers/llm.py:28`, `pipeline.py` synthetic share, `providers/search.py`, `examples/run_local.py` | No longer sold as a product claim; the code stays parked |
| **Kill** | `serve.py` as a product surface; `scripts/ai_eval.sh`; the 17-subcommand CLI as the main interface; "Predicted #N of M" headlines; `hash_embed`; the AI fix loop (`check.py:452`) as a headline feature | |
| **Kill** | Every SmartCrew hardcode (section 2) | Move to tenant config; SmartCrew becomes tenant #1 |
| **Build** | New | Tenancy (tenant, site, page_versions, queries, observations, predictions) with RLS and auth; `ranksim check --base --head` with JSON/SARIF output and exit codes 0, 1 and 78; GSC OAuth plus backfill; AI panel through DataForSEO; ledger UI and email; Snapshot; GitHub App and Vercel/Netlify integration; preview auto-manifest; white-label reports; Stripe |

**30 / 60 / 90 / 180 days**

**Days 0–30 (to Dec 2, 2026)**
- Make `evaluate` reproducible: commit the stored crawl, or a script that rebuilds it, so a fresh clone gets the documented number instead of 0.485.
- Move hardcodes into tenant config.
- Wire DataForSEO with cost metering and raise the depth cap.
- Label 300 queries and crawl about 25K pages.
- Apply the `lambdamart` fixes.
- Measure the noise ceiling.
- Ship the verdict enum and `ranksim check` with exit codes.

**Days 31–60 (to Jan 1, 2027)**
- Tenancy, auth and RLS.
- Page-version store and change detection.
- GSC OAuth plus the 16-month backfill.
- Rebuild `loop.py`.
- Ledger v1 on SmartCrew.
- DNS-rebinding fix.
- Free Snapshot (Google only).

**Days 61–90 (to Jan 31, 2027)**
- 10 paying design partners.
- Weekly ledger email.
- Competitor change feed.
- AI Overview parsing.
- Publish the G1 result and the first public ledger.

**Days 91–180 (to May 1, 2027)**
- Topic-level AI panel with confidence intervals.
- GitHub App and Vercel integration (deterministic FAIL plus blast radius).
- Migration Sign-off SKU.
- Stripe.
- Agency white-label.
- Pooled difference-in-differences model v1.
- Measure G2.
- Hire 2 AEs at M6.

---

## 8. Go-to-market

**First 10 customers (M0–M3)**
- SmartCrew converts to paid.
- Add 9 B2B and vertical SaaS content teams at $500–1,000 a month, chosen because they ship 15+ pages a month or have a migration on the calendar.
- **Do not sign SmartCrew's direct competitors** (hcss.com, stackct.com and others in its SERP) without SmartCrew's agreement.
- Every design partner agrees to let us publish anonymized ledger accuracy.

**First 100 (M3–M12)**
- **Free Snapshot:** REAL Google share for any domain, 30 queries against 5 competitors, for a few cents of SERP data.
- **Weekly public calibration ledger.**
- **Monthly "Construction Software Search Share Index."** Stay in one vertical until it has a published ledger.
- **Migration Sign-off,** sold directly and through agencies.
- **Agency program:** a 20% revenue share and a free workspace for the agency's own site.
- **A free GitHub Action with commodity rules only.** The ranking-aware rules and the blast radius need SERP data and stay hosted and paid.
- **SDR signals:** AI Overviews showing on a prospect's head terms, a migration announcement, a GEO hire.

**Positioning**
- **Against Profound and Peec:** "they show where you are; we show what each change did, and publish how often we're right."
- **Against SEOTesting:** SERP and AI outcomes, competitor changes, difference-in-differences and frozen predictions, vs before/after on GSC alone.

**Channels**

| Channel | Starts | Role |
|---|---|---|
| Founder-led sales | M0 | First 10 customers, first enterprise pipeline |
| Snapshot + ledger content | M2 | Main source of self-serve Team signups |
| Agencies | M4 | Workspaces, migrations, referrals |
| GitHub/Vercel marketplaces | M5 | Growth tier and developer installs |
| AEs (enterprise, then Growth) | M6 / M9 | Sales-assisted pipeline |

**SmartCrew case study** (publish only with permission)
- Accuracy going from 16 to 300 queries, re-measured, misses included.
- A ledger of SmartCrew pages shipped since the GSC backfill window.
- If PR8 ships: what the check said about v2 and v3 vs what REAL ranks did at +28 days.

---

## 9. Milestones and kill criteria

| Month | MRR | Logos | Team churn | Data / accuracy |
|---|---|---|---|---|
| M3 | ~$7.5K | 10 | n/a | G1 measured; ≥1,000 backfilled events |
| M6 | ~$48K | 71 | ≤5% | Public ledger live |
| M9 | ~$116K | 158 | ≤5% | **G2 decision** |
| M12 | ~$199K | 259 | ≤4.5% | Live events ≥ 2K/month |
| M18 | ~$386K | 481 | ≤4% | G3 measured |
| M24 | ~$626K | 768 | ≤4% | 15–40K events/month |
| M33 | ~$1.00M | 1,205 | ≤4% | G4 measured |

**Kill or pivot triggers**

| When | Trigger | Action |
|---|---|---|
| M6 | Fewer than 6 of 10 design partners renew at list price, or Free→Team below 2% of GSC-connected free accounts | Stop pushing self-serve Team; sell Growth and Migration Sign-off |
| M9 | CLEARER change-direction precision below 0.68 on ≥500 events, or pooled static pairwise below 0.62 | Remove prediction from all pricing and marketing; run as measurement + ledger + gate (the downside case) |
| M9 | Fewer than 25% of Git installs run 3+ checks within 14 days | Demote the PR gate to migration-only |
| M12 | MRR below $150K, or Team churn above 5% a month | Go Growth/Enterprise plus migrations only; cut burn |
| Any month | Agency workspace cost above $15; any tier's cost above 30% of price for 2 months; measured AI answer cost above $0.005 | Cut refresh frequency and repeats, or reprice |
| Any vertical | CLEARER hit rate below 0.65 for 2 consecutive months | Pull forecasts for that vertical |

---

## 10. Biggest risks

1. **Prediction never earns its gates.**
   - *Mitigation:* every paid tier is justified by the ledger and the deterministic gate.
   - The M9 kill rule is public, and the downside case is priced in.
2. **Data access and terms of service.**
   - We depend on SERP and AI-answer suppliers.
   - Google legal action against scrapers is **UNVERIFIED**, as is whether GSC terms allow pooled training.
   - API answers differ from what users see in the UI.
   - *Mitigation:* buy through DataForSEO or Bright Data so the scraping risk sits with the supplier, and keep 2 providers live (`serp.py` already abstracts 3).
   - GSC training is opt-in; competitor-diff labels need no GSC.
3. **Incumbent response.**
   - Semrush has $38M AI ARR and the Adobe close is UNVERIFIED. Ahrefs Brand Radar and Profound's capital are both threats. SEOTesting could add SERP data.
   - *Mitigation:* a published ledger with misses; frozen-prediction history that a late entrant cannot backfill; deploy-path lock-in; pricing below the enterprise suites.
4. **The value is episodic, so churn could be high.**
   - Teams may buy for one migration and leave.
   - *Mitigation:* charge for migrations as projects; make the weekly ledger and competitor feed the recurring value; model churn at 4%; the M12 trigger above.
5. **Click erosion lowers the value of a ranking.**
   - *Mitigation:* AI citation share is a first-class number from M4. If AI share comes to dominate, shift the product's weight toward it.

**Also:** fetching customer preview URLs is an SSRF surface. Fix DNS rebinding (`urls.py:88`) and add an egress proxy before any public launch. Never expose `serve.py`.

---

## 11. Sources

**Repo:** `docs/accuracy.md`, `ACCURACY_PLAN.md`, `smartcrew/pr8-v2-compare.txt`, `smartcrew/pr8-v3-compare.txt`, and the code audits cited by file and line above.

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