# Ranksim mission and handoff (as of Nov 9, 2026)

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
- Integration PR: https://github.com/joe51111jwd/Ranksim/pull/5 (branch cursor/integration-review-175f, base claude/ranksim-mvp-eod-ifjitw). The forward-eval commit is f76f7f1. CI on that commit was green (run 42635653671, six pytest jobs on Python 3.11, 3.12, and 3.13, keys unset and fake keys). Docs added after f76f7f1 do not change the ranker or those scores.
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

## The main gap and next work
Ranksim's core use case, predicting where a new or draft page lands, is still open. On pages that have text, draft versus draft is 12/22 and draft versus a page that already ranked is 87/167 (0.521). Next work:
1. Take the next snapshot of the same 30 queries. Judge a new-page rule on pages that have text. Twenty-two text-versus-text pairs are too few to change the ranker.
2. Keep the no-HTML pairs visible in the report. They are what pulls 34/116 under half, and they are not the pasted-draft path.
3. Use persistence for pages already ranking, and keep the 90% target measured on unseen data with coverage stated.
4. Apple mockup: blocked on a Superdesign browser sign-in. Resume when that is done, then wait for James's approval.

## Coordination
- SEO Tracker (Grok Bot teammate) runs SmartCrew site work and sends real SERP data.
- SEO Tool Builder (Grok Bot) owns ranksim and dispatches Cursor agents. A weekday 9am ET routine keeps pushing this work.
