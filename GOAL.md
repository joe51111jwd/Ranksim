# Ranksim goal brief (owner: James Camarota, set Nov 18 2026)

## Mission
Make ranksim actually work: a private per-niche simulator that predicts Google rankings well enough that an SEO agency can make decisions with it. The decision we care about most: given an existing indexed page and two candidate edits (or a new/changed page), tell which version will rank better and roughly where it will land. Beat honest baselines on held-out data. James does not care what previous reports said; find a way.

## Team
You are the lead (Fable 5.2, ultracode). Split work across subagents (they run on Opus 5.5, xhigh effort). Use parallel workers for independent experiments, a separate reviewer subagent to audit every claimed win for leakage before you accept it.

## Repo and data (all local, already on this machine)
- Repo: this folder, a clone of github.com/joe51111jwd/Ranksim on branch cursor/pairwise-heldout-experiments-678f. Create and work on branch `claude/goal-ranksim-works`. Commit often. You may push that branch and open a draft PR into cursor/pairwise-heldout-experiments-678f. NEVER merge, never push to main, never deploy.
- Read first: AGENTS.md, ACCURACY_PLAN.md, COORDINATION.md, evals/preregistered/*, evals/draft-ledger/smartcrew-pr41.md, scripts/*.
- Raw data, append-only, NEVER delete or rewrite: /workspace/ranksim-data/ranksim_raw.sqlite (Serper SERPs + fetched page HTML) and /workspace/ranksim-data/snapshots/ (dated pulls: 2026-11-16 q100/deep/noise/pr41-baseline, 2026-11-17 pull-a/b/c + clockshark, 2026-11-18 pull-a/b (pull-c incomplete)). Older snapshots: /workspace/ranksim-live-snapshot-2026-11-{09,11,13,16}, /workspace/ranksim_saved_html. Real SERP CSVs also under /workspace/seo-tracker/.
- More data: Serper credits are free. Key loads with: export SERPER_API_KEY="$(sed '1s/^\xEF\xBB\xBF//' /workspace/ranksim-secrets/serper.env | grep '^SERPER_API_KEY=' | cut -d= -f2- | tr -d '\r\n ')". Collector: /workspace/ranksim-data/collect_daily.sh (use absolute query file paths, cap with RANKSIM_DAILY_CREDIT_CAP). Always keep at least 250 credits unspent. Never print, log, or commit any key. Never spend real money or sign up for anything paid.

## What is already known (do not re-prove)
- Same-day Google noise is large. Inter-pull ceiling on consensus truth: exact ~0.31-0.37, 3-wide ~0.52-0.61, top3 recall ~0.74-0.80, #1 ~0.66-0.83.
- "Persistence" (yesterday's consensus position) is at that ceiling for URLs already ranking. The model lost to persistence on the Nov 17 consensus gate (exact 0.228 vs 0.363, #1 0.667 vs 0.811). Bands also lost. Hybrid (persistence for seen URLs, model for new/changed) failed its Nov 17 diagnostic: 6/22 vs domain prior 9/22.
- So the value has to come from what persistence cannot know: new pages, changed pages, and comparing edits.

## Targets (clear, measurable, held out). Freeze each test's split, metric and threshold in evals/preregistered/claude-<name>/ with a sha256 BEFORE you score it. Never tune on a test set. Report 95% bootstrap CIs (resample by query).
1. NEW/CHANGED URL PLACEMENT: on rows where a URL is new to the query's top 30 or whose page content changed materially, predict its consensus position. Pass = within-3 accuracy beats the best of {domain prior, position 7, position 11} with the CI of the difference above 0, on at least two held-out days not used for any design choice (use Nov 19 and later pulls as they arrive, plus any earlier days you reserve now and never look at).
2. EDIT DIRECTION (the "better of two edits" proxy): build a dataset of real (query, URL) pairs where the page HTML changed between snapshots and the consensus rank moved. Predict up vs down from the content diff plus features. Pass = accuracy on held-out pages/queries/time beats the majority-class baseline and a "no information" coin with CI lower bound above 0.5 + margin, n >= 40. If there are not enough real changed pages, say so and build the collection pipeline that will get them (scheduled re-fetch of HTML + SERP), plus a counterfactual pairwise test: for held-out queries, take two pages that both rank, and test whether the model orders them correctly when the higher-ranked page's key on-page signals are swapped in (report as diagnostic only, not proof).
3. PAIRWISE ORDER OF MOVERS: among URL pairs whose relative order flipped between consecutive days, predict the new order better than persistence (which gets 0 by construction) and better than 50% with CI lower bound above 0.5.
4. Do not break anything: existing tests pass (`pytest`), existing preregistered gates and the SmartCrew PR41 draft ledger are untouched.

## Rules
- Never fake or inflate results. Every number from a real run, reproducible from a committed script. Label all forecasts PREDICTED. If a target is not reachable with the data available, prove it with numbers and build what would make it reachable.
- No leakage: split by time first, then by query group and by domain. Features must only use data available before the prediction date.
- Keep everything you pull: raw responses go append-only into the SQLite DB.
- Write progress to RESULTS-claude.md in the repo after every meaningful experiment: what was tried, the real numbers vs baselines, pass/fail.

## Done when
All of targets 1, 2 and 3 pass on held-out data and a reviewer subagent has signed off that there is no leakage, OR you have exhausted the reasonable approaches and RESULTS-claude.md contains an honest final report: what passes, what fails with numbers, why, and the concrete next data needed. Finish with a short plain-English summary (no em or en dashes) at the end of RESULTS-claude.md and as your final message.

## Round 2 (James, Nov 19 2026): find a way to hit the numbers
Round 1 stopped before freezing and scoring. Read RESULTS-claude.md first; do not redo finished work. The Targets above are still the numbers to hit. Round 1 showed the current approach cannot hit them: the ranker is 94% history and 3% content, so it cannot tell which version of a page is better, and changed-page data is thin. Do not respond by building more variants and hoping one crosses the line. Work like a researcher:
1. Research first. Study how real ranking works (Google leak attributes, published learning-to-rank and click model work, content quality and relevance signals, how other rank simulators and SEO tools model edits) and what the data says about why pages move.
2. Write a theory before building. In `evals/claude/theory.md`, for each target write the mechanism you believe would hit the number, why it should work, which data it needs, and a back-of-envelope estimate (from real counts) showing the target is reachable if the theory holds. Have a reviewer subagent attack the theory before you build.
3. Build only what the theory says, then test it honestly on held-out data under the freeze rules above.
4. If it fails, write down why, go back to step 1 with what you learned, and form a new theory. Repeat. Each loop must change the theory, not just tune knobs.
5. If the bottleneck is data, get it: you may build and run free data collection (daily HTML re-fetch of all tracked URLs, more SERP pulls within the credit rules, controlled edits on test pages you own are NOT allowed without James). Never touch client sites.
Freeze the round 1 preregistrations that are ready and score them on held-out days as they arrive; those results go in the report whatever they show. Keep going until the Done-when condition is met. Do not stop just because subagents returned; stopping without the final report is a failure.
