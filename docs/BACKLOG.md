# Ranksim backlog (2026-11-05)

Written after an audit of `main` at `ae6012b` and the work on branch
`claude/ranksim-audit-8nshyt`. Evidence for every number is in `docs/EXPERIMENTS.md`.
Each item lists the problem, the change, acceptance criteria, dependencies, effort
(S under a day, M a few days, L a week or more) and any cost or credential needed.

## Where the product stands

- **What works:** crawling labeled URLs politely; joining Google labels to stored pages;
  a LambdaMART reranker; the demo server; CLI commands. 140 tests that run without network or keys.
- **What is not established:** that the model predicts Google order better than BM25F alone or
  better than luck once near-duplicate queries and pages are held out (16 queries, 458 pairs,
  every interval includes 0). No confidence threshold survives a held-out check. The AI relevance
  experiment never had enough data to say anything.
- **What the product can honestly say today:** "Here is how your draft compares with the stored
  competitor pages on measurable on-page signals, how many competitors we could actually score, and
  how old the Google results are. We record every call and score it later." It cannot say "your
  page will rank above X" or "this change will improve rankings".

## P0: required before any accuracy claim or paid pilot

### P0-1 Collect a real, dated, diverse labeled set (300+ queries)
- Problem: 16 queries from one site, 11 independent clusters, no collection date or locale.
- Change: run `collect` (Serper or DataForSEO) on 300+ queries across at least 5 sites and 3 niches,
  with informational, commercial, comparison and transactional intent; store `observed_on`, country,
  language, device and provider; crawl with `index-serp`.
- Acceptance: label files load with an empty drop report; at least 90% of labeled rows covered with
  reasons for the rest; at least 150 query clusters; per-intent and per-site counts reported by
  `benchmark`.
- Depends on: a SERP key. Cost: one SERP call per query per snapshot (check the provider's current
  price before buying; nothing was purchased here).

### P0-2 Freeze the benchmark and pre-register a final test set
- Problem: every design choice so far (monotone constraints, the 150-word gate, the confidence rule)
  was made by looking at the same 16-query backtest that was then reported.
- Change: `benchmark freeze` writes a dataset manifest (label sha256, page HTML hashes, corpus
  fingerprint, as_of) and an optional compressed page snapshot so the benchmark reruns without a
  recrawl. Split clusters and sites into dev and final; `benchmark --final` runs once per model id and
  appends to a results log it never rewrites.
- Acceptance: two machines produce identical numbers from the same snapshot; a second `--final` run
  for the same model id is refused. A `--final` number is a same-snapshot holdout (held-out queries
  from the same day as the training queries), so it is reported as that and never as accuracy
  (`docs/MISSION.md`). Accuracy claims come only from forward evaluation on a later snapshot.
- Depends on: P0-1. Effort M.
- Status 2026-11-05: built (`benchmark-freeze`, `benchmark --snapshot`, `--final`; E12). The split
  is by query cluster only; splitting by site as well needs more labeled sites (P0-1).

### P0-3 Repeated snapshots for volatility and for the ledger
- Problem: one snapshot cannot say how much Google itself moves, and the prediction ledger has no
  outcomes to score.
- Change: collect the same queries on at least 3 later dates (same locale and device); run `noise`
  on each pair of dates; record outcomes with `ledger observe --observed-on --depth`.
- Acceptance: `noise` reports agreement with churn on at least 2 date pairs; `ledger score` reports
  resolved calls separately for UNSURE and CLEARER.
- Depends on: P0-1 and a SERP key. Effort S once collection works.

### P0-4 Close the remaining SSRF gaps before exposing anything beyond one machine
- Done on this branch: streamed fetches with a 2 MB cap and a 30 s total deadline; global-only
  addresses; robots.txt fail-closed; demo Host allowlist, CSRF token and security headers; local
  bind by default and a refusal to bind publicly without named hosts.
- Done 2026-11-10 (B3): the crawler's direct connections go to an address it vetted at connect
  time (a local rebinding demo fetched an internal page before and is refused now); the 30 s
  deadline covers connect, headers and body; 429/503 set a per-host backoff from Retry-After or a
  doubling wait, and a wait over 2 minutes is reported, not slept. Proxied requests are resolved
  by the proxy and keep the pre-check plus the deadline.
- Still open: the demo has no user accounts or rate limit.
- Change: connect to the vetted IP (Host header plus SNI); honour 429/503 Retry-After per host;
  add authentication and a rate limit before any shared deployment.
- Acceptance: local tests for rebinding and a 429 storm pass; a shared deployment requires a token.
- Effort M.

### P0-5 Decide what a draft is compared against
- Problem: training uses only pages Google already ranked top 10, so a new draft is outside the
  training distribution, and "not in the top 10" is not a possible prediction.
- Change: add non-ranking candidates (positions 11-30 via deeper collection, or relevant site pages
  that do not rank) as lower-relevance rows; evaluate a "would this page enter the top 10" question
  separately from ordering within the top 10.
- Acceptance: the benchmark reports both tasks with baselines; the UI says which one a check answers.
- Depends on: P0-1 (deeper collection). Effort L.
- 2026-11-10: this is MISSION next work item 1 ("a draft/new-page predictor ... evaluated on URLs
  that newly entered the top 10 between snapshots"). The integration branch (PR #5) now holds two
  dated 30-query snapshots (Nov 6 and Nov 9) with 97 entrant rows, but their page HTML is private
  and not in git, and `forward_eval.py` belongs to the SEO Tool Builder. Persistence (keep the
  previous order) covers pages already ranking (MISSION next work 2) and needs no content model;
  see E14.

## P1: quality and trust

| id | problem (evidence) | change | acceptance | effort |
| --- | --- | --- | --- | --- |
| P1-1 | Space view: "PREDICTED top 3 of 3" in 2-4 page sets; "REAL top 10" counts any position; head-to-head rows show the client's record under the competitor's name; business and SERP competitors mixed (UI audit CSU-03/08/09/11) | top-k only when the set has more than k pages; count position <= 10; label perspective; `kind` column (business or serp) | tests on 2-page sets, position 35, a #1 vs #35 pair, and a mixed competitor file | M |
| P1-2 (done, B10) | Competitor import silently drops bad lines, corrupts quoted CSV, accepts ports and junk hosts (CSU-10) | csv.reader, IDNA, reject IPs/localhost, report rejected lines | round-trip test for quoted CSV; rejected list printed | S |
| P1-3 (done, B16; not adopted) | LambdaMART can memorize ~30 rows (min_data_in_leaf=1, 15 leaves) and its exponential label gain does not match the pairwise metric (EVAL-08) | regularize; linear gain; choose only by inner CV on the dev split | in-sample vs held-out gap reported; no use of the final split | S |
| P1-4 (done, B5) | URL matching ignores www/http/tracking params; redirects and canonicals are dropped (ING-07, CRAWL-14) | a match key for joining only; persist final_url and canonical; flag labels whose URL redirects | tests for each variant; labeled redirect shown in coverage | M |
| P1-5 (done, B6) | Client-rendered pages score as empty with no warning (CRAWL-15) | detect JS shells; flag in coverage and in the check | a Next.js-style shell is flagged, not scored as a thin page | S |
| P1-6 (done, B4) | Crawler: robots wildcard rules differ on Python 3.11/3.12 (CRAWL-06); placeholder user agent (CRAWL-07) | RFC 9309 matcher; require a real contact UA before crawling third parties | robots cases pass on 3.11-3.13; crawl refuses `example.com` UA | S |
| P1-7 (done, B11) | SERP history is overwritten and has no locale/device; collected SERPs never reach `evaluate` or `noise` (ING-04, ING-09, ING-10) | append-only snapshots with params and raw payload; `export-serp`; `--keywords` file input | two collections of one query both retrievable; `noise --from-db` works | M |
| P1-8 (done, B12) | Attribution scores before and after in one candidate set, so originality and BM25 IDF change because the copy is present (EVAL-12) | score each version against the same peers separately | a no-op edit produces no feature changes | S |
| P1-9 (done, B7) | Coverage reasons are not persisted per label; thin and empty pages count as covered; saved HTML is found only from the repo root (ING-08, ING-12) | per-label coverage table (`--coverage-csv`), thin/empty as their own reason, paths from repo root | `evaluate` prints counts by reason from any cwd | S |
| P1-10 | `test --bundle` adds every bundle page to every query; report.html is stale and mis-renders bundle runs (CSU-06/07/19) | insert only matching pages; render bundle runs; compute report accuracy from the label file | tests for a 0-competitor query and a bundle report | M |
| P1-11 | LLM explanations are not checked against the numbers given to the model; one model id is sent to every provider (AI-11, AI-12) | reject replies with numbers outside the brief; provider selector and model-format check | tests with a fake reply containing an invented rank | S |
| P1-12 (done, B15) | `site_history.py` on the unmerged branch is a content-blind site prior (MAE 1.985 vs 2.119 for the middle guess on 135 rows, in its audit) | port it as one more benchmark baseline row next to the domain prior, not as a product answer | it runs on identical folds in `benchmark` | S |
| P1-13 (done, E13) | Confidence is calibrated on all labeled pairs, but a check is labeled from the gap to the adjacent page; thin-page gate offsets count as confident gaps (E11) | calibrate on adjacent-page gaps from pre-gate scores, exclude gated pairs | the calibration statistic matches the one the check uses; a test with a gated pair | S |
| P1-14 (done, B8) | 9 of 16 labeled queries have no intent and default to "commercial"; `build_space` hard-codes "commercial" (E11, CSU-21) | read intent from the label file or keywords.csv; one intents source for training, check and space | every labeled query has an explicit intent or is reported as unknown | S |

## P2: later, only with evidence

- Domain, niche and time-based holdouts (needs P0-1 data).
- Per-intent and per-niche results (needs P0-1 data; report n with every number).
- Backlink or authority features from DataForSEO (costs money; must beat the domain prior on `--final`).
- AI relevance judge rerun on at least a few hundred labeled pairs, with the paired interval; keep it
  out of the default score unless the interval excludes 0 on the final split.
- Multi-user deployment: tenant isolation, retention and deletion, audit of every route.

## Smallest useful product demonstration

One site, 30 to 50 dated queries collected with Serper, a polite crawl, and three screens:
1. **Benchmark:** the model next to BM25F, a domain prior and random scores, with intervals,
   coverage and data age. It shows when the model does not beat the baselines.
2. **Draft check:** which of the stored competitors the draft is predicted ahead of or behind,
   each marked UNSURE unless a held-out threshold supports it, with coverage and data age next to
   the verdict and the draft echoed back for editing.
3. **Track record:** the ledger after two to four weeks of later snapshots: resolved calls, right
   and wrong, unresolved counts, never edited.

Everything for screens 2 and 3 exists on this branch. Screen 1 exists as a CLI. The missing input is
dated SERP data (P0-1).

## Commercial validation (assumptions are labeled)

- **First buyer:** an in-house SEO or content lead at a B2B software company in a narrow niche
  (SmartCrew's position), or an agency strategist serving several such clients. Their repeated
  decision is "which page do we write or refresh next, and is this draft competitive with the
  specific pages ranking now". Assumption, not validated with interviews.
- **Difference from a checklist or AI writer:** it compares a draft with the actual ranking
  competitor pages for that query, says how many it could score and how old the results are, keeps
  a ledger of its calls and scores them against later Google results, and says UNSURE when the
  evidence is weak. A checklist does not measure itself; a writing assistant does not compare with
  the live competitor set.
- **Promises the evidence supports now:** coverage, data age and on-page differences against named
  competitors; an honest UNSURE; a record of calls. **Not supported:** predicted Google positions,
  "beats competitor X", "this edit will improve rankings", any accuracy percentage.
- **Pilot:** 3 to 5 sites, 50 queries each, 6 weeks. Pre-register: ledger accuracy on resolved
  calls against a coin flip and against the domain prior; how many drafts users changed or did not
  publish because of a check; time per decision. Report every call, including wrong ones.
- **Unit economics (measured here):** a polite crawl of about 120 labeled URLs took 31 seconds and
  a 50-fetch link crawl 44 seconds, bandwidth only. One historical AI evaluation made 37
  gpt-4o-mini calls estimated at $0.003368 (an estimate, not a bill). SERP cost is one provider call
  per query per snapshot; check the provider's current price list before estimating margins.
  Storage: a 163-page corpus database was 51 MB.
- **Pricing experiments:** do not set a price from guesses. Start with a free concierge pilot, then
  test per-tracked-query pricing against per-seat pricing with real buyers and record conversion.
  No willingness-to-pay figure has been measured.

## Coordination

- The unmerged branch `claude/bold-einstein-oi5qoj` (about 18k lines: project tester, AI engines,
  optimize loop, market and revenue plans) overlaps `serve.py`, `check.py`, `cli.py`, `fetch.py` and
  `urls.py`. Rebasing either branch onto the other will conflict in those files. AI-search
  optimization is parked, so its AI-engine work should not be merged into the Google product. See
  the branch inventory notes in the pull request.
- All implementation here was done by Claude Code in a cloud session, not by Cursor. Each commit is
  one reviewable chunk with its own tests; Cursor can cherry-pick or rewrite any of them.
