# Ranksim mission and handoff (as of Nov 13, 2026)

Owner: James Camarota (GitHub joe51111jwd). Any agent picking this up should read this file first.

Fact check against this repo on 2026-11-09: there is no `onboard` command. The Nov 6 query list spells the two product queries without hyphens (`all in one heavy civil software`, `all in one dirt work software`). The UI brief in git is `docs/ui-mockup-brief.md`. Forward-eval figures below are the rounded values in `evals/forward-2026-11-09/report.json`. The Serper credit balance is not stored in the repo.

## Mission
Ranksim is a private Google rank simulator. It builds a small search engine per niche and predicts where a page (including an unpublished draft) will rank in real Google, so SEO changes can be tested in minutes instead of waiting for Google to recrawl.
First client: SmartCrew (www.smartcrewhq.com). Top target queries, spelled as in the Nov 6 snapshot (`evals/forward-2026-11-09/baseline_serp.csv`): "all in one heavy civil software", "all in one dirt work software", "hcss alternative".
Scope: Google SEO only. The AI search (AEO) track is parked.

## Priorities (in order)
1. Make ranksim actually work. This is the number one priority.
2. Target: 90%+ pairwise page-ordering accuracy on genuinely unseen real Google results, reported with honest coverage (how many pairs and rows were scorable).
3. Apple-style UI redesign is secondary. James rejected the current UI as too complicated and too text-heavy. He wants simple interactions, minimal copy, generous spacing, clear type. A visual mockup must be approved by James before any redesign is built. Brief: `docs/ui-mockup-brief.md` (the Grok Bot copy was `/workspace/ranksim-apple-mockup/BRIEF.md`).

## Hard rules
- Never fake results. Label predictions as PREDICTED. Never present a training score, a same-snapshot score, or a test-tuned score as accuracy.
- Select features and models on training data only. Held-out and forward data are scored once.
- No merge to main and no production deploy without James's explicit authorization. Production is the old PR2 build at https://ranksim-demo.vercel.app.
- No paid purchases without James's approval. Never commit or print API keys.
- Heavy work runs in Cursor cloud agents to save Grok Bot usage.
- Reports to James: short plain sentences, lead with what changed, no em dashes or en dashes.

## Repo state
- Repo: https://github.com/joe51111jwd/Ranksim. Main is unchanged at ae6012b.
- Integration PR: https://github.com/joe51111jwd/Ranksim/pull/5 (branch cursor/integration-review-175f, base claude/ranksim-mvp-eod-ifjitw). The first forward-eval commit is f76f7f1. The fetch fallback and the unknown-content gate are commit 6a0910a, written before the recovered Nov 9 scores were read. The browser wait cap is 4391bb5. CI on f76f7f1 was green (run 42635653671, six pytest jobs on Python 3.11, 3.12, and 3.13, keys unset and fake keys).
- Nov 13 score: `evals/forward-2026-11-13/report.md`. Ledger `evals/preregistered/next-snapshot/track-record.jsonl`. Freeze commit `3ad2613`. The product ranker is unchanged.
- Stack: Python package aeo.ranksim. BM25F retrieval plus LightGBM LambdaMART reranker with Google-leak-informed features. Entry point: `python -m aeo.ranksim` (console script `ranksim`). There is no onboard command. Commands include collect, evaluate, train, test, track, report, forward-eval, serve, and `project create`.
- Ledger: append-only, hash-chained. Records PREDICTED rows and later outcomes. Calls stay UNSURE until held-out calibration passes.
- LLM layer: OpenRouter with a cheap model (gpt-4o-mini). The key is an env var, never in git.

## Data
- Real SERPs come from the Serper API (POST https://google.serper.dev/search, gl=us, hl=en, num=10). Free account, 2,500 credits, about 61 used by Nov 9. That balance is not in git. The key lives outside git.
- Snapshot 1, Nov 6: 30 fixed US-English queries, 294 organic rows, 143 unique URLs, 97 stored HTML files (`files.baseline_html_saved`), 215 training rows with that HTML (`coverage.training_examples`).
- Snapshot 2, Nov 9: same 30 queries, 299 rows, 155 unique URLs in `forward_serp.csv`, 103 HTML files (`files.forward_html_saved`).
- Take a fresh snapshot of the same 30 queries every few days so forward evals keep growing.

## Results so far (all honest, none promoted)
- Same-snapshot holdout (frozen 20 train / 10 holdout queries, 222 pairs): LambdaMART 0.545 pairwise vs BM25 0.559. Not forward accuracy. Recorded in COORDINATION.md at heads 6f48474 and 21723d1.
- Round 2 (head 6f48474): family-out validation found no ranker gain.
- Round 3 (head 21723d1): rejected page type, Tranco popularity, Common Crawl link rank (family-out 0.50 to 0.52).
- First forward eval, Nov 6 models frozen, scored once against Nov 9 (head f76f7f1, output in evals/forward-2026-11-09/):

| Scorer | Pairwise | NDCG@10 | Spearman |
| --- | --- | --- | --- |
| LambdaMART | 0.507 | 0.715 | 0.096 |
| BM25 | 0.444 | 0.604 | -0.045 |
| Persistence (keep Nov 6 order) | 0.694 | 0.902 | 0.649 |
| Equal-weight blend | 0.702 | 0.841 | 0.555 |
| History-aware rule (not adopted, no clean split) | 0.751 | 0.885 | 0.631 |

  Persistence on URLs present both days: 594/667 pairs (0.891), mean Spearman 0.816. Ledger: 1,495 PREDICTED rows, all UNSURE; persistence right on 931/1,146 resolved calls (0.812).
- Drafts: LambdaMART 134/281 (0.477) against pages already ranking, 34/116 (0.293) draft vs draft. Persistence cannot order drafts at all.
- Nov 10 new-page check (family-out, Nov 6 only, scored once on Nov 9; details in `evals/forward-2026-11-09/new_page.md`). The 34/116 figure is 28 ties of pages with no HTML, 22 right and 44 wrong when one page has text and the other does not, and 12/22 when both have text. The full list is 680 right and 557 wrong, so the comparator is not flipped. Dropping the largest query leaves 24/71. Three cheap rules (domain prior, URL overlap plus BM25, and a blend whose weight was fit on Nov 6) all failed the pre-set gate: beat LambdaMART by 0.05 on both draft slices, stay at least at BM25, and keep the bootstrap gain interval above zero. The ranker is unchanged.
- Nov 10 fetch and recovered forward look (gate commit 6a0910a, scores in `evals/forward-2026-11-09/recovered.md`). Label: PREDICTED. The ranker is still not promoted.
- Nov 10 Serper scrape fill (gate commit bb18150, same rules as 6a0910a, scores in `evals/forward-2026-11-09/scrape.md`). 61 of 64 scraped URLs had more than 500 characters. The text was captured on Nov 10, after both SERP days. Label: PREDICTED. The ranker is still not promoted.

## Fetch design
The collector tries three methods, in order, and records which one worked and why the others did not. The delay is 1 second per host.
1. The existing polite HTTP fetch. It still obeys robots.txt for the bot user agent and still refuses private addresses.
2. Headless Chromium (Playwright), with a current Chrome user agent and a 1366x768 viewport. It waits for the DOM, then for network idle, and reads the rendered HTML, visible text, title, headings, links, and schema. Robots are checked again for that browser user agent. A disallow is not bypassed. A 40 second alarm closes the browser if a page never goes idle.
3. Serper scrape (`POST https://scrape.serper.dev`) only when `SERPER_API_KEY` is set and `serper_scrape_credit_cap` is above zero. The cap is a hard ceiling: a call that might cost more than the credits left is not sent. The default cap is 0, so a live scrape is off. A folder of scrape JSON that was already captured can be imported with no key: text, title, and description, source `serper_scrape`, plus the capture date. Text of 500 characters or fewer is not used as page content.

A page with no HTML is unknown content, not a shared low score. The feature `content_missing` is 1 for that row. Its ordering score is the mean of the Nov 6 LambdaMART training scores, shifted by the average within-query z-score of the off-page parts that exist. A missing part adds nothing. The domain prior uses Nov 6 rows from the other query families only. Recovered HTML is used at score time only. It is not a Nov 6 training row.

Off-page sources that work with no signup:
- Majestic Million CSV (referring subnets), free download, CC BY 3.0: https://downloads.majestic.com/majestic_million.csv. On this list, 52 of 84 domains had a value.
- Tranco top list (popularity, not a backlink count): https://tranco-list.eu/top-1m.csv.zip. 50 of 84 domains were listed.
- Common Crawl domain PageRank is free and keyless, and the file is multiple gigabytes. The loader is wired for a local file. This run did not download it, so that part added nothing.

Needs a free signup key. Not requested:
- Open PageRank: https://www.domcop.com/openpagerank/. The client is wired and stays off until `OPEN_PAGERANK_API_KEY` is set.
- Moz Link API: https://moz.com/products/api. Not wired.
- Majestic API beyond the Million CSV: https://developer.majestic.com/. Not wired.

Clicks, impressions, and on-site behavior are not available. Nothing in this run invents them.

## Recovered forward eval (PREDICTED, ranker not promoted)
Missing HTML on the two snapshots: 46 URLs on Nov 6, 52 on Nov 9, 65 unique. HTTP recovered 3 unique URLs. The browser recovered 2 more. 5 of 65 unique pages came back. Nov 6: 3 of 46 (HTTP 2, browser 1). Nov 9: 4 of 52 (HTTP 2, browser 2). The same page can sit in both snapshots, so the per-day counts share some URLs. 60 unique pages stayed missing: 36 blocked by robots for both the bot and the browser, 12 HTTP challenge pages the browser still could not pass, and 12 HTTP 403s that were still challenges in the browser. The five recovered pages are thedigitalprojectmanager.com, sage.com heavy civil, topconsolutions.com/dirt-logic, meanjin.com.au/fiction/dirt-work, and zoho.com/crm/customers/dirt-rocks.html.

The model was still fit only on the original 215 Nov 6 HTML rows. At score time, 10 Nov 9 rows used recovered HTML, 71 rows still had no HTML, 183 used Nov 6 HTML, and 35 used the original Nov 9 fetch. Pairwise still has 1,341 pairs. Intervals are a query bootstrap, 2000 draws, seed 20261110. NDCG@10 and Spearman intervals use the same draw count, seeds 20261110 and 20261111.

| Scorer | All pairs | NDCG@10 | Spearman |
| --- | --- | --- | --- |
| Published LambdaMART (no recovery) | 680/1341 (0.507) | 0.715 | 0.096 |
| LambdaMART, recovered text, unknown content | 885/1341 (0.660) [0.624, 0.691] | 0.777 [0.724, 0.828] | 0.413 [0.328, 0.488] |
| BM25, same pages | 678/1341 (0.506) [0.466, 0.547] | 0.626 [0.577, 0.677] | 0.029 [-0.079, 0.139] |
| Persistence | 931/1341 (0.694) [0.605, 0.773] | 0.902 [0.848, 0.946] | 0.649 [0.544, 0.739] |
| Equal-weight blend | 934/1341 (0.696) [0.655, 0.738] | 0.836 [0.782, 0.885] | 0.535 [0.440, 0.627] |
| History-aware rule, not adopted | 1009/1341 (0.752) [0.701, 0.800] | 0.879 [0.828, 0.925] | 0.625 [0.518, 0.724] |

Persistence on the full list is the same 931/1341 as the published run. The candidate set did not change.

| Slice | Published LambdaMART | This run, LambdaMART | This run, BM25 |
| --- | --- | --- | --- |
| Draft vs draft | 34/116 (0.293) | 76/116 (0.655) [0.393, 0.760] | 59/116 (0.509) |
| Draft vs draft, both have text | 12/22 (0.545) | 18/31 (0.581) [0.353, 0.727] | 19/31 (0.613) |
| Draft vs already ranking | 134/281 (0.477) | 182/281 (0.648) [0.578, 0.713] | 149/281 (0.530) |
| Draft vs already ranking, draft has text | 87/167 (0.521) | 111/176 (0.631) [0.556, 0.697] | 84/176 (0.477) |

The adoption gate was fixed in 6a0910a before these scores. Promote only if every draft slice, including both text slices, beats the published LambdaMART rate by 0.05, is at least BM25 on that slice, and the bootstrap interval of the gain over the published rate sits above zero. Draft versus draft with text is 18/31 (0.581). The bar is 0.595. It is also below BM25 at 19/31 (0.613). The gain interval is [-0.212, 0.182]. The other three slices cleared their bars. The rule needs every slice. `ranker_change` is `none`.

The all-pairs lift is mostly the 71 rows that still have no HTML. They used to share one floor under every fetched page. They now take an off-page score, so they are not tied at the bottom. That is not a claim that the pasted-draft path got better. On drafts that have text, the count moved from 12/22 to 18/31 because a few recovered pages entered that slice.

## Serper scrape fill (PREDICTED, ranker not promoted)
James supplied a Nov 10 Serper scrape of 64 unique URLs that had no stored HTML. 61 had more than 500 characters of text. Three did not: a help.hcss.com article (456 characters), a Facebook post (74), and a Quora URL that returned a scrape error. The importer does not call Serper and does not need a key. Each usable page is source `serper_scrape`, captured on 2026-11-10. The gate for this look was committed in bb18150 before the scores, with the same rule as 6a0910a: every draft slice must clear.

Caveat: the words were captured on Nov 10. That is three to four days after the Nov 6 SERP and one day after the Nov 9 SERP. The text can differ from the page Google ranked.

The model was still fit only on the original 215 Nov 6 HTML rows. None of the scrape URLs entered training. At score time, 78 Nov 9 rows used scrape text (a URL can appear on more than one query), 35 used the original Nov 9 fetch, 183 used Nov 6 HTML, and 3 still had no usable text. Pairwise is still 1,341 pairs. Persistence is still 931/1341.

| Scorer | All pairs | NDCG@10 | Spearman |
| --- | --- | --- | --- |
| Published LambdaMART | 680/1341 (0.507) | 0.715 | 0.096 |
| LambdaMART, scrape text filled in | 909/1341 (0.678) [0.633, 0.721] | 0.798 [0.736, 0.857] | 0.432 [0.317, 0.545] |
| BM25, same pages | 734/1341 (0.547) [0.510, 0.587] | 0.665 [0.615, 0.717] | 0.142 [0.035, 0.253] |
| Persistence | 931/1341 (0.694) [0.605, 0.773] | 0.902 [0.848, 0.946] | 0.649 [0.544, 0.739] |
| Equal-weight blend | 923/1341 (0.688) [0.649, 0.729] | 0.846 [0.798, 0.890] | 0.495 [0.399, 0.589] |
| History-aware rule, not adopted | 1004/1341 (0.749) [0.704, 0.789] | 0.890 [0.843, 0.930] | 0.614 [0.510, 0.711] |

| Slice | Published LambdaMART | This run, LambdaMART | This run, BM25 |
| --- | --- | --- | --- |
| Draft vs draft | 34/116 (0.293) | 68/116 (0.586) [0.508, 0.717] | 57/116 (0.491) |
| Draft vs draft, both have text | 12/22 (0.545) | 68/116 (0.586) [0.519, 0.725] | 57/116 (0.491) |
| Draft vs already ranking | 134/281 (0.477) | 184/281 (0.655) [0.569, 0.736] | 143/281 (0.509) |
| Draft vs already ranking, draft has text | 87/167 (0.521) | 184/281 (0.655) [0.568, 0.735] | 143/281 (0.509) |

Draft versus draft and the text-only version are the same 68/116. The scrape gave text to the drafts that used to have none, so the text slice is no longer 22 pairs. It is the full 116. The rate 0.586 does not clear the published text rate 0.545 by 0.05 (the bar is 0.595). The gain interval is [-0.038, 0.172]. The other three slices cleared their bars, including staying above BM25. The rule needs every slice. `ranker_change` is `none`.

## Nov 11 fresh forward test (PREDICTED, ranker not changed)
Predictions were frozen in `8365d68` before the Nov 11 file was opened. Details are in `evals/forward-2026-11-11/report.md`. The primary metric is overlap pairwise accuracy: ordered pairs whose URLs were in the candidate list and in the Nov 11 top 10. A tie is not correct. URLs new on Nov 11 are unpredicted. The Nov 10 scrape was not used. `ranker_change` is `none`.

The supplied check is reproduced: Nov 9 order matches Nov 11 on 625/724 (0.863260), and 208 of 299 Nov 9 rows persisted. The table below is rounded to three decimals. The stored rates are in the report.

| Window | Scorer | Overlap pairs | NDCG@10 | Spearman |
| --- | --- | --- | --- | --- |
| Nov 9 to Nov 11 | LambdaMART | 508/724 (0.702) | 0.860 | 0.486 |
| Nov 9 to Nov 11 | BM25 | 321/724 (0.443) | 0.625 | -0.057 |
| Nov 9 to Nov 11 | Persistence | 625/724 (0.863) | 0.935 | 0.714 |
| Nov 9 to Nov 11 | Blend | 545/724 (0.753) | 0.851 | 0.542 |
| Nov 9 to Nov 11 | History-aware, not eligible | 609/724 (0.841) | 0.933 | 0.692 |
| Nov 6 to Nov 11 | LambdaMART | 396/583 (0.679) | 0.852 | 0.420 |
| Nov 6 to Nov 11 | BM25 | 252/583 (0.432) | 0.666 | -0.025 |
| Nov 6 to Nov 11 | Persistence | 483/583 (0.828) | 0.919 | 0.622 |
| Nov 6 to Nov 11 | Blend | 417/583 (0.715) | 0.847 | 0.473 |
| Nov 6 to Nov 11 | History-aware, not eligible | 461/583 (0.791) | 0.907 | 0.595 |

Nov 9 to Nov 11 coverage: 88 of 296 Nov 11 rows were unpredicted, and 724 of 1314 pairs were scored. Nov 6 to Nov 11 coverage: 104 of 296 rows were unpredicted, and 583 of 1314 pairs were scored. Exact rates are in the report. These overlap rates are not the old 1,341-pair Nov 9 figure.

Ledger: 2,965 PREDICTED rows, hashes unchanged from `8365d68`, plus 300 REAL outcome rows appended. The Nov 9 ledger file was not edited.

## Nov 13 full-coverage gate (PREDICTED, ranker not changed)
The frozen full-coverage line from `3ad2613` was scored once on a REAL Serper snapshot taken 2026-11-13, 15:45 to 15:46 ET (`gl=us`, `hl=en`, `num=10`, 30 queries, 298 organic rows). HTML from that same capture was passed, so BM25F is on for new URLs at the frozen weight of 1. Details, the pair table, and the ledger chain head are in `evals/forward-2026-11-13/report.md`. One snapshot of these 30 queries is not a general forecast of Google.

The gate passed on all three frozen checks. Combined all-pairs accuracy is 992/1332 (0.744745), above persistence-ranked-last at 862/1332 (0.647147). New versus surviving is 354/522 (0.678161), above 0.5, with 522 pairs. `ranker_change` is `none`. A pass does not change the product ranker, the coefficients, or the frozen files.

Surviving versus surviving is 518/605 (0.856198) for both the combined line and persistence-ranked-last. The survivor score is the Nov 11 position. 194 of 296 Nov 11 rows stayed in the Nov 13 top 10. 102 exited. 104 of 298 Nov 13 rows are new. The all-pairs gap is 120 new-versus-new pairs plus 10 new-versus-surviving pairs. New versus new is 120/205 (0.585366), with 3 ties. Persistence-ranked-last ties all 205 of those pairs. Frozen LambdaMART was not scored. The booster is not in git. BM25F alone is 543/1332 (0.407658).

216 of 298 rows had HTML (96 unique URLs). 82 rows had no text. Missing text adds no push. The Nov 11 ledger file was not edited.

## The main gap and next work
Ranksim's core use case, predicting where a new or draft page lands, is still short of the 90% target. On this one Nov 13 snapshot, new versus surviving is 354/522 (0.678161) and new versus new is 120/205 (0.585366). The earlier draft slices are unchanged: with scrape text, draft versus draft is 68/116 (0.586) and draft versus a page that already ranked is 184/281 (0.655). Persistence still orders the URLs that stayed. Next work:
1. Leave the `3ad2613` freeze untouched. Do not retune the full-coverage coefficients, LambdaMART, the blend, or the history-aware scale on the Nov 11 or Nov 13 labels.
2. Keep the 90% target on unseen data, with coverage stated. This gate was a point comparison on 30 queries, and a pass can be noise.
3. Use the Nov 11 position for pages that already ranked. The combined survivor score on Nov 13 is that position.
4. Apple mockup: blocked on a Superdesign browser sign-in. Resume when that is done, then wait for James's approval.

## Coordination
- SEO Tracker (Grok Bot teammate) runs SmartCrew site work and sends real SERP data.
- SEO Tool Builder (Grok Bot) owns ranksim and dispatches Cursor agents. A weekday 9am ET routine keeps pushing this work.
