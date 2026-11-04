# Is Ranksim distinctive enough to make money? (as of 2026-11-04)

**How this was put together.** This answer draws on five research sweeps, a fact check and a skeptical investor review. The web-search budget ran out partway through, so the later checks were done by opening source pages directly on 2026-11-04. Reddit, G2 and Capterra could not be reached, so there is little direct evidence of what buyers say they want. "My arithmetic" marks numbers I worked out myself; they are not quoted from a source.

---

## 1. The answer in 3 sentences

Yes, the idea is distinctive. No one sells a self-serve web app that takes a page before it goes live and shows where it would land on every search you care about, who would beat you and why, and then publicly tracks how often those predictions came true. The trouble is in two places:
- **The parts that work today are cheap elsewhere.** Pulling Google's top 10, comparing your page and showing what ChatGPT cites are sold by dozens of tools, starting at about $23 a month.
- **The prediction doesn't work yet.** It is the only part nobody else sells, and right now it gets page order right 52.4% of the time on 16 searches, which can't be told apart from a coin flip.

It can make money if you:
- sell it as "check any change before you ship it" for any industry (new pages, page refreshes, redesigns, site moves);
- prove its accuracy with public numbers;
- start pricing near $149 to $249 a month plus $2.5K to $5K per launch check, not $499 and up.

---

## 2. What is already out there

| Group | What it does | Main names and prices | Overlap with Ranksim |
|---|---|---|---|
| **AI-visibility trackers** (the "things popping up right now") | Ask ChatGPT, Perplexity, Gemini and others your questions over and over, and chart whether they mention or link to you | **Peec:** $95 / $245 / $495.<br>**AthenaHQ:** free / $295 / Enterprise.<br>**Scrunch:** $250 to $500.<br>**Profound:** free trial, then custom Enterprise | "What AI cites now." **Very crowded:** one count says 100+ tools, and these startups have raised about $576M |
| **Big SEO suites adding AI** (SEO means getting found on Google) | All-in-one SEO platforms with AI tracking built in | **Semrush (owned by Adobe):** $139 to $299+.<br>**Ahrefs:** $29 to $1,499, with AI "Brand Radar" from $199.<br>**HubSpot AEO:** $50.<br>**SE Ranking:** $129 to $279, AI prompts included | They can bundle any feature you build |
| **Content graders** | Score a draft from 0 to 100 against the current top results for one keyword | **Surfer:** $49 to $299 (billed yearly).<br>**Clearscope:** $129 / $399.<br>**Frase:** $39 to $239 (yearly).<br>**NeuronWriter:** $23 to $117 | "Who is above you and what they have," one keyword at a time |
| **Pre-launch simulators** | Estimate whether a draft or change will rank or get cited before it goes live | **Market Brew:** quote only.<br>**Bodhium:** AI only; free starter, then custom.<br>**AthenaHQ ACE:** Enterprise only.<br>**AEOsim:** no price listed | **Ranksim's core.** Few players, mostly enterprise or AI-only |
| **Testing on live traffic** | Change some pages, then measure the difference over weeks | **SearchPilot:** from $37,500 a year.<br>**SEOTesting:** $50 to $375 a month | Sells proof, but only works for big sites |
| **Site move and technical checks** | Compare a new site with the old one and catch broken redirects and tags | **Screaming Frog:** £199 a year.<br>**Lumar Protect:** blocks a release that fails 350+ checks; no price listed.<br>**Agencies:** $3K to $15K for migration SEO | Ranksim's "migration sign-off." **None of these predicts ranking changes** |
| **Free from Google** | Search Console now reports how often you appear in AI Overviews and AI Mode (appearances only, no clicks) | Free; worldwide since 2026-08-31 | Undercuts paid AI tracking. Useful as a free data source for a scorecard |

Prices were checked on vendor pages on 2026-11-04 unless the sources list says otherwise.

**What "popping up" really means.** The money is going into AI-visibility tracking:
- **Profound** raised $180M at a $1.8B valuation in September 2026, seven months after raising $96M at $1B. It has raised about $335M in total.
- **Buyers are consolidating the market:**
  - Adobe bought Semrush for $1.9B (closed 2026-04-28).
  - Positive Group bought Surfer, which had about 12,000 customers.
  - HubSpot bought XFunnel for $30M.
  - Sitecore bought Scrunch, at a reported ~$225M that neither company confirmed.

That corner is crowded and well funded, so don't make it your pitch.

---

## 3. Who is closest, and how Ranksim is different

| Competitor | What it does | Proof it publishes | How Ranksim would be different to a buyer |
|---|---|---|---|
| **Market Brew** (closest) | A model of a search engine, tuned to match real results. Release notes from July to September 2026 say it can test changes on a staging site, analyze "proposed page content before it is published," and preview drafts against Google's AI Overviews | **None with numbers.** It claims "full ranking correlation" without figures (2026-07-20) and says "the goal is not to promise an exact ranking outcome." It is quote-only and enterprise; reviewers mention heavy onboarding and drift. It has been around since 2007 and shows zero reviews on Slashdot | Self-serve: paste a page and get answers in minutes. A predicted position for each search, the pages above you named, and a public hit rate |
| **Bodhium Labs** | AI only. Recreates the AI answer for a draft and keeps only edits that raise its 0 to 100 score | Calls its outputs "modeled estimates," with no accuracy number. Raised $3.5M in 2026 and says it has $1M+ in yearly revenue (its own claim) | Ranksim also predicts Google, which Bodhium doesn't. On the AI side Bodhium is ahead and funded |
| **AthenaHQ ACE** | Scores content from 0 to 1 for how likely AI is to cite it. Enterprise only | 87.0% of its top-scored tenth were cited, against 38.6% of the bottom tenth (1,761 articles). But it scored articles that had already been live for 90+ days, so this looks back; it is **not a forecast made before launch.** The authors call it "a correlation, not a controlled experiment" | ACE gives one score. Ranksim would give a result for each search, against named competitors |
| **Profound** | An enterprise "AEO Content Score." Since 2026-10-19 it also flags gaps between your Google rankings and your AI citations and drafts fixes | Its score is described only as "statistically significant" | The biggest copy threat: 1,000+ enterprise brands and $335M raised |
| **Surfer's AI SEO Content Score** (May 2026) | Grades one draft for Google and AI side by side, from $99 | Its own study: a weak link (0.28) between its score and rank across 10,000 searches | Surfer gives a score, not a predicted position, for one keyword at a time and with no track record |
| **Others** | **AEOsim:** online-store product pages across 4 AI engines; vendor page only.<br>**Amplitude:** "simulate changes," included in every plan; not clear it works on unpublished drafts.<br>**Ozone Labs:** free, research-stage AI simulator for publishers.<br>**MarketMuse:** "Personalized Difficulty," how hard it is for your site to rank, scored 1 to 100 | Little or none | Ranksim is broader than AEOsim and Ozone, covers Google, and makes a forecast where MarketMuse gives a difficulty score |

**What a buyer would actually notice:**
1. **One upload, every search.** A predicted position on each search you care about, with the pages above you named and what they have that you lack.
2. **Answers in minutes.** SearchPilot tests take 2 to 4 weeks. One grader, POP, says results "can often take between 10 and 21 days."
3. **It works for small sites.** SearchPilot needs hundreds of pages on the same template and 30,000+ organic visits a month.
4. **A public record of right and wrong calls.** No vendor publishes one.

---

## 4. What is worth paying for, and what is not

**Distinctive (only if it works):**
- **One page, every search.** One upload shows a predicted position range on each search, who beats you, and why.
- **A public track record.** Predictions checked against real Google results and published, misses included.
- **A launch check that predicts which rankings you would gain or lose.** Today's tools check for technical errors only.
- **Answers for sites too small to split-test.**

**Distinctive, but already contested:**
- **Simulating AI citations before launch:** Bodhium, AthenaHQ ACE, Profound, AEOsim and Ozone already do this.
- **Simulating Google:** Market Brew.
- **"Google and AI in one test":** Market Brew, Surfer's new score and Profound's October feature.

**Not distinctive (include these, don't charge for them):**
- **"What AI cites now":** $29 to $99 elsewhere, and partly free in Search Console.
- **"Pull the top 10 and compare":** 15+ tools, from $23 a month.
- **Auto-rewrite:** Surfer Auto-Optimize (May 2026), AirOps Quill, and Search Atlas OTTO, which comes with every plan.
- **Connecting to AI assistants and other tools:** Surfer's connector launched 2026-10-05, and Frase includes one on every plan.
- **Standard extras:** unlimited seats, white-label reports and a free tier.

**The hard truth: the only distinctive part doesn't work yet.**
- **52.4% is within coin-flip range.** It means: pick any two pages, and the model puts them in Google's order 52.4% of the time. On 16 searches that is about 720 pairs. A fair coin would score 52.4% or better about 1 time in 10 (my arithmetic). Pairs from the same search aren't independent, so the real uncertainty is even larger.
- **It may be overstated.** Check whether it was measured on the same 16 searches the model learned from. If so, the true number is probably lower.
- **New pages are the hardest case.**
  - Only 1.74% of newly published pages reached Google's top 10 within a year (a second sample gave 6.11%).
  - The average #1 page is 5 years old. This is Ahrefs data collected in 2023, so it may be dated.
- **Google uses signals a pasted draft doesn't have.** It names links, site-wide signals and freshness. Leaked documents suggest click data matters a lot, though Rand Fishkin calls this "not quite proof."
- **So for a brand-new page, the honest answer is a range with a time attached,** for example: "likely page 2 to 3 for the first 6 months."
- **Changes to existing pages are easier to predict.** They already have history, and "nothing changes" is a strong starting guess to beat.

---

## 5. How to stay broad but still stand out

**Positioning: "Check every change before you ship it."**
- **The hook:** "Before you publish a page, refresh one or launch a redesign, see which of your searches you'd win or lose, to whom, and why."
- **Broad by moment, not by industry.** It covers a moment that every industry goes through. That answers "we don't want it so focused."
- **Lead with Google, where traffic and sales still come from.** Show AI citation as a likelihood, never a yes or no.

| Who buys first | What it costs them | Why they buy |
|---|---|---|
| **1. Agencies and in-house SEO or web leads running a redesign or site move** (any industry) | **Launch sign-off:** $2.5K to $5K per project, up to about $10K for sites with 10,000+ pages | **The fear is concrete and the budget already exists.**<br>• Agencies charge $3K to $15K for migration SEO; the most common agency project fee is $2,501 to $5,000 (2024 survey, may be dated).<br>• After a domain move, traffic took 523 days on average to recover, and 17% of sites had not recovered after 1,000 days (2024 data).<br>• Results show up within weeks, which builds your track record fast.<br>**Caveat:** many failures are technical, and a £199-a-year crawler catches those. Ranksim has to catch ranking losses a crawl misses |
| **2. SEO and content agencies serving many industries** (your sales channel) | **$599 to $799 a month**, priced by number of clients, with white-label reports | **This is where breadth comes from:** each agency brings many industries.<br>• Peec's agency plans run $245 to $795.<br>• Sacra estimates agencies are about half of Peec's customers (UNVERIFIED).<br>• The typical agency retainer is $501 to $1,000 a month per client, which caps what agencies will pay.<br>**Risk:** agencies can build rough versions themselves |
| **3. In-house marketers at small and mid-sized sites with 20 to 200 searches that bring in revenue** | **Starter:** $149 to $199.<br>**Team:** $399 to $499 | **They can't split-test, and nothing else gives them an answer before launch.** Surfer costs $49 to $299 and Clearscope $129 to $399. Charging 1.5 to 2 times Surfer's middle tier for a real prediction is believable; 5 times without proof is not |

**Hold off on enterprise ($1,800 to $4,500 a month)** until the track record is public. That segment belongs to Profound and other well-funded players.

**Broad pitch, staged proof.** The skeptic argues for sticking to one or two industries for the first year. That point holds in one sense: accuracy has to be proven separately in each industry, and right now it has only been measured on construction software. The middle path:
1. Market to everyone.
2. Publish accuracy separately for each industry.
3. Only advertise an industry once its numbers pass.
4. Let agencies pull you into new ones.

**Your current prices against the market:**

| Your plan | Price | Market comparison | Verdict |
|---|---|---|---|
| Team | $499 | Peec Advanced $495, Clearscope Business $399, Surfer top tier $299 | At the ceiling. Too high as the entry point |
| Agency | $1,200 | Peec agency Scale $795, Search Atlas Agency from $999 | Too high, and confusing because it sits below Growth |
| Growth | $1,800 | All of Ahrefs Enterprise is $1,499 | Not justified for a tool that does one job |
| Enterprise | $4,500 | SearchPilot ~$3.1K a month; Semrush Enterprise averages ~$5.3K a month (my arithmetic) | Plausible only once the track record is proven |
| Migration sign-off | $2.5K to $5K | Agency migration fees of $3K to $15K | **The best-priced item in the plan** |

**The $1M monthly revenue goal (my arithmetic):**
- **What the plan needs:** 1,205 customers means about $830 per customer per month.
- **What the fastest comparable earns:** Peec makes roughly $333 to $641 per customer.
- **What it takes at realistic prices:** at $250 to $450 per customer, $1M a month needs about 2,200 to 4,000 customers.
- **One mix that works:** about 1,800 subscribers at $400 ($720K) plus about 80 sign-offs a month at $3.5K ($280K). The sign-offs are one-off income, not recurring revenue.

---

## 6. Biggest risks, and the proof that it's working

**Risks, biggest first:**
1. **Accuracy.** This is the one that can kill the business. A model that only reads page content can't see links or clicks, and new pages rarely rank.
2. **The track record can backfire, or miss the point.**
   - A public hit rate near 50% confirms what critics already say: scores don't predict rankings.
   - Only one version of a page ever gets published, so a hit rate can't show that a particular edit helped. Proving that needs comparison pages.
   - People publish the pages the tool liked, which makes the rate look better than it is.
3. **Copying.** Profound, Surfer, Ahrefs and Semrush could all add a "predicted position" label. So far they have chosen not to; Surfer's changelog through 2026-10-05 has no forecast feature. That is either an opening or a sign they think it's unwise. Your defense is the recorded track record, not the screen.
4. **The AI side is noisy and its value is doubted.**
   - In one small study, 67% of cited links changed from one day to the next, and different engines almost never cited the same links.
   - A typical page lost half its citation share within 11 days of its peak (Profound's own data).
   - One study found content scores barely predict which pages AI cites.
   - Fishkin cites data showing AI sent 0.73% of traffic across 100 sites, while Google drove 97% of conversions.
5. **Data access and legal risk.**
   - Google sued SerpApi, a company that collects and resells Google results (2025-12-19).
   - Semrush is dropping Copilot tracking on 2026-11-09 after Microsoft changed its access rules.
   - Your "one button" depends on the same kind of access, so use a licensed data source.
6. **Bursty use leads to cancellations.** Launches happen a few times a year, and a $499-a-month tool used only in bursts gets cancelled in between.
7. **Earlier attempts drifted away from software.**
   - Market Brew went quote-only and enterprise.
   - RankScience started at Y Combinator as automated SEO software and now sells agency services. Why it changed is UNVERIFIED.
   - CanIRank asks the same question Ranksim does and now pitches action recommendations instead (weak evidence).

**Proof points (these are the skeptic's thresholds, which I think are fair):**

| What to prove | Number to hit | Why it matters |
|---|---|---|
| Accuracy on searches the model never saw | 150+ searches across 8+ industries, tested on searches and dates not used to build it. Gets page order right at least 60% of the time, and the cautious low end of the estimate is still above 55% | Shows real signal rather than luck, beyond construction software |
| Content adds something beyond "big site wins" | Beats a simple rule that ranks pages by site authority (the strength of a site's links) by 3+ points | Otherwise it's an authority lookup with a nicer screen |
| New-page forecasts | 200+ pages predicted before publishing, on 20+ sites. At 90 days, its "top 10, yes or no" calls beat simple guessing, and "70% likely" turns out right about 70% of the time. Median miss of 5 positions or less for pages that reach the top 20 | This is the claim buyers hear |
| Edits actually help | 50+ live pages edited, compared against similar unedited pages. Calls the direction of change (up or down) right at least 65% of the time, beating "nothing changes" | The only way to back up "edit and re-test" |
| Launch check | 5 to 10 paid sign-offs at $2.5K+, with predicted and actual changes published at 30 and 90 days. At least one loss flagged that a crawler missed | Proves the strongest wedge |
| Demand beyond SmartCrew | 10+ paying customers; 3+ agencies on monthly plans; fewer than 4% cancelling per month; at least 2 tests per account per month in months 2 and 3 | Shows it isn't a one-customer, one-off tool |
| AI layer | Run each question many times per engine and publish likelihoods that hold up | A yes or no can't survive daily churn |
| Data cost | A licensed source of search results, costing under ~10% of the price per test | Legal safety and profit margin |

**Signs it isn't working:**
- The 52.4% turns out to be measured on the training searches.
- The model can't beat the authority-only rule.
- The first 5 launch checks can't be sold at $2.5K.

---

## 7. What to build next, in order

1. **This week: check whether 52.4% was measured on the same 16 searches the model learned from.**
2. **An honest test at scale.**
   - Grow to 150+ searches across 8 to 10 industries, and keep some searches and dates aside for testing.
   - Add link and authority data.
   - Compare against simple rules: random order, "rank stays the same," authority only, and keyword coverage.
   - Start with easier questions: "do you beat competitor X," "page 1, yes or no," and position ranges.
   - Report results per industry.
3. **Switch to a licensed source of Google results** and know the cost per test.
4. **Build the one screen that sets you apart.** Upload once and get a table: every search, a predicted range, the pages above you, and the 3 specific things each has that you don't. Keep "what AI cites now" as an included extra.
5. **A private track record before a public one.**
   - Log every prediction with a date.
   - Check it against Search Console at 2, 4 and 8 weeks, and at 90 days for new pages.
   - Use unedited comparison pages when testing edits.
   - Publish only once the numbers clear the bar, and include the misses.
6. **Hand-sell 5 to 10 launch sign-offs at $2.5K to $5K** through SmartCrew-like partners and a few agencies, partly done by hand. Run 15 to 20 buyer interviews alongside. The demand evidence so far is indirect.
7. **Reprice and open self-serve.**
   - Free: one page, 5 searches, Google only.
   - Starter: $149 to $199.
   - Team: $399 to $499.
   - Agency: $599 to $799.
   - Enterprise: quoted only after the track record is public.
8. **The AI layer as likelihoods, and the auto-optimizer last.** Building the optimizer before steps 2 and 5 pass would make it optimize toward noise. It would also fall into the known trap of stuffing pages with terms to push a score up.

---

## Sources

Dates are the publication date or, where marked, the date the page was fetched. UNVERIFIED means only one weak source supports the claim.

**Funding, deals and market size**
- Profound Series D: https://www.tryprofound.com/newsroom/profound-raises-usd180m-series-d-at-usd1-8b-valuation-to-build-the-ai-platform-for-marketing-teams (2026-09-15)
- AI-search startup funding totals (about $576M across dated rounds, ~$619M on latest totals; Profound's ~$335M total and $96M Series C): https://valueaddvc.com/blog/ai-search-optimization-startups-2026-geo-aeo-funding-investors (2026-10-16)
- AI-visibility tool count, "100+" (author owns a competing vendor): https://guptadeepak.com/geo-compass/guides/geo-tooling-market-2026/ (2026-10-14)
- Adobe completes Semrush acquisition: https://news.adobe.com/news/2026/04/adobe-completes-semrush-acquisition (2026-04-28)
- Adobe to buy Semrush for $1.9B: https://www.cnbc.com/2025/11/19/adobe-ai-semrush-stock-deal.html (2025-11-19)
- Positive Group acquires Surfer: https://positivegroup.com/news/positive-acquires-surfer (2025-10-28)
- HubSpot to acquire XFunnel for $30M: https://www.marketscreener.com/news/hubspot-inc-entered-into-an-agreement-to-acquire-xfunnel-inc-for-30-million-ce7d5cdddf81f625 (2025-11-04; deal signed 2025-10-30)
- Sitecore acquires Scrunch (price reported, not confirmed): https://www.techtarget.com/enterprise-software/news/366643973/Sitecore-acquires-Scrunch-for-answer-engine-optimization (2026-06-04)
- Peec Series A: https://peec.ai/blog/we-raised-21m-series-a-to-help-brands-win-in-ai-search (2025-11-18)
- Peec $10M ARR (headline only seen): https://www.globenewswire.com/news-release/2026/05/28/3303009/0/en/peec-ai-hits-10m-arr-16-months-after-launch-as-brands-race-to-optimize-for-ai-search.html (2026-05-28)
- Sacra estimates on Peec (estimates, UNVERIFIED): https://sacra.com/c/peec/ (2026)
- Semrush full-year 2025 results: https://www.stocktitan.net/news/SEMR/semrush-announces-fourth-quarter-and-full-year-2025-financial-ji2vmj49se7r.html (2026-03-02)
- 2026 Gartner Market Guide for Answer Engine Visibility Tools (exact date UNVERIFIED): https://digital-release.wboy.com/business/press-releases/cision/20260512SF57497/iqrush-named-in-the-2026-gartner-market-guide-for-answer-engine-visibility-tools (2026-05-12)

**Pricing pages (all fetched 2026-11-04)**
- Peec: https://peec.ai/pricing
- Profound: https://www.tryprofound.com/pricing
- AthenaHQ: https://www.athenahq.ai/pricing
- Scrunch: https://scrunch.com/pricing
- Semrush: https://www.semrush.com/prices/
- Ahrefs: https://ahrefs.com/pricing
- HubSpot AEO: https://www.hubspot.com/aeo-grader
- SE Ranking: https://seranking.com/subscription.html
- Surfer: https://surfer.ai/pricing/
- Clearscope: https://www.clearscope.io/pricing
- Frase: https://www.frase.io/pricing
- NeuronWriter: https://neuronwriter.com/pricing
- Rankability: https://www.rankability.com/pricing
- Search Atlas: https://searchatlas.com/pricing
- POP: https://www.pageoptimizer.pro/pricing
- SearchPilot: https://www.searchpilot.com/pricing
- SEOTesting: https://seotesting.com/pricing/
- Screaming Frog: https://www.screamingfrog.co.uk/seo-spider/pricing/
- Market Brew: https://marketbrew.ai/price-and-licensing
- MarketMuse: https://www.marketmuse.com/pricing/

**Peec agency pricing** (written by a Peec rival; also the source of the "cleaner for analytics than for prescriptive recommendations" line): https://trakkr.ai/reviews/peec-ai-review/pricing (2026-10-01)

**Closest competitors**
- Market Brew:
  - Release notes, staging and pre-publish tests: https://community.marketbrew.ai/release-notes (2026-07 to 2026-09)
  - "Not to promise an exact ranking outcome" (section undated): https://marketbrew.ai/resources/search-modeling-forecasting (fetched 2026-11-04)
  - Listing site, not user reviews: https://martechradar.ai/tools/marketbrew/ (2026-10-15)
  - Slashdot, zero reviews and founded 2007; G2 not checked: https://slashdot.org/software/p/Market-Brew/ (fetched 2026-11-04)
- Bodhium:
  - Product page: https://www.bodhiumlabs.com/geo (fetched 2026-11-04)
  - "Modeled estimates": https://www.bodhiumlabs.com/blog/best-geo-tools (2026-11-04)
  - Funding: https://app.fundz.net/fundings/bodhium-labs-funding-round-seed-36c522 (2026-10-02)
  - $1M+ ARR (company claim, UNVERIFIED): https://dealroom.co/news/157981-bodhium-labs-raises-3-5m-to-test-marketing-ai-before-launch/ (2026-09-30)
- AthenaHQ ACE study (looks back, correlational): https://athenahq.ai/blog/the-science-of-ai-citation/ (2026-05-28)
- Profound:
  - AEO Content Score: https://www.tryprofound.com/blog/upgrading-content-optimization-with-the-aeo-content-score (2025-10-27)
  - Blog index, "Search Visibility in Pages" dated 2026-10-19: https://www.tryprofound.com/blog (fetched 2026-11-04)
- Surfer:
  - Updates, including AI SEO Content Score, Auto-Optimize and the 2026-10-05 connector launch: https://www.surfer.ai/updates/ (fetched 2026-11-04)
  - Content score study: https://surfer.ai/blog/surfer-content-score-study (updated 2026-09-09)
- AEOsim (vendor page only): https://www.aeosim.com/ (fetched 2026-11-04)
- Amplitude AI Visibility FAQ: https://amplitude.com/docs/api/content/amplitude-ai/ai-visibility/faq (2026-10-09)
- Ozone simulation platform; also the source of the ~0.3% AI click-through figure (one unnamed source): https://digiday.com/media/ozones-simulation-platform-tries-to-predict-how-publisher-content-appears-in-ai-answers/ (2026-04-07)
- Lumar Protect: https://www.lumar.io/protect/ (fetched 2026-11-04)
- AirOps Quill: https://siliconangle.com/2026/05/13/exclusive-airops-targets-emerging-ai-search-market-autonomous-content-optimization-agent/ (2026-05-13)

**Testing, migrations and agency pricing**
- SearchPilot:
  - Split-testing requirements: https://www.searchpilot.com/resources/blog/what-is-seo-split-testing (updated 2026-10-28)
  - Best practices backfiring: https://www.searchpilot.com/resources/blog/when-seo-best-practices-backfire (2026-08-28)
  - Lose Google, lose AI search: https://www.searchpilot.com/resources/blog/lose-google-and-you-lose-ai-search (2026-07-31)
- Migration SEO cost: https://projectcostestimator.com/blog/seo-services-cost-2026 (2026-06-16)
- Large-site migration cost: https://www.wearetenet.com/blog/website-migration-cost (2026-03-10)
- Ahrefs SEO pricing survey (may be out of date): https://ahrefs.com/blog/seo-pricing/ (2024-08-15)
- Migration recovery times (data collected October 2024): https://www.searchenginejournal.com/how-long-should-an-seo-migration-take/531219/ (2025-01-08)

**Why prediction is hard**
- How long new pages take to rank (2023 data): https://ahrefs.com/blog/how-long-does-it-take-to-rank/ (2025-05-15)
- Google ranking systems guide (undated): https://developers.google.com/search/docs/appearance/ranking-systems-guide (fetched 2026-11-04)
- Google leak analysis: https://sparktoro.com/blog/an-anonymous-source-shared-thousands-of-leaked-google-search-api-documents-with-me-everyone-in-seo-should-see-them/ (2024-05-28)
- Daily churn and cross-engine overlap of AI citations (small study): https://arxiv.org/abs/2609.22655 (2026-09-19)
- Content scores barely predict AI citations: https://arxiv.org/abs/2609.07559 (2026-09-07)
- Citation half-life (vendor data): https://www.tryprofound.com/blog/the-half-life-of-an-ai-citation-is-11-days (2026-09-30)
- Fishkin on AI-visibility value: https://sparktoro.com/blog/is-ai-visibility-a-marketing-boondoggle/ (2026-10-22)
- Marketers' doubts about AI-visibility tools: https://digiday.com/marketing/marketers-question-expensive-ai-visibility-tools-as-inconsistent-results-fuel-skepticism/ (2026-05-01)
- Content-score study, including keyword stuffing (20 keywords): https://ahrefs.com/blog/seo-content-score-study/ (2025-05-21)
- Surfer user review: https://www.trustpilot.com/review/surferseo.com (2026-10-18)

**Data access and legal**
- Google sues SerpApi: https://blog.google/technology/safety-security/serpapi-lawsuit/ (2025-12-19)
- Semrush drops Copilot tracking: https://ppc.land/semrush-drops-copilot-from-ai-visibility-tracking-on-november-9 (2026-10-29)
- Search Console AI reports announced: https://developers.google.com/search/blog/2026/06/gen-ai-performance-reports (2026-06-03)
- Search Console AI reports rolled out worldwide: https://www.searchenginejournal.com/google-search-console-ai-reports-rolled-out-worldwide/587836/ (2026-08-31)

**Earlier attempts** (weak evidence; reasons for any change of course are UNVERIFIED)
- RankScience: https://www.rankscience.com/ (fetched 2026-11-04)
- RankScience YC listing: https://www.ycombinator.com/companies/rankscience (fetched 2026-11-04)
- CanIRank: https://www.canirank.com/ (fetched 2026-11-04)

**Corrections from the fact check, applied above:**
- Market Brew is treated as a direct competitor that tests pages before launch.
- The claim that Surfer says its score "is not a forecast" was dropped; it was wrong.
- AthenaHQ's study is described as looking back, not as a forecast made before launch.
- Startup funding is given as about $576M.
- The "broad tools get squeezed" quote attributed to guptadeepak was removed.
- The "monitoring without diagnosis" quote attributed to Trakkr was removed.
- Semrush's Copilot change is attributed to unspecified access changes, not a sign-in requirement.
- Search Console's AI report is described as showing appearances only, with no clicks.
