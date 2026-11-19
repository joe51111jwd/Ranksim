# SmartCrew own-site edits as a Target 2 source: scout, 2026-11-19

Read-only inventory. SERP store queried only for snapshot_date <= 2026-11-17. No network calls.

## Sources

- Site repo with full history: `/workspace/tmp/Smart-Crew-Website` (origin joe51111jwd/Smart-Crew-Website; HEAD 99c2ece = main, Nov 18 17:22 UTC). Page copy is TS source in `src/seo/pages/**`, rendered by `src/seo/render.ts`, with redirects in `vercel.json`. Every main commit is a production deploy (Vercel). PR merge times are in `/workspace/sc-kb/prtable.md`.
- `/workspace/sc-docs` is a different repo (Website-selling). Its last commit is Oct 22, so it is not useful here.
- GSC: `seo-tracker/smartcrew/gsc-export/*` covers Oct 25 to about Nov 8 as one cumulative window. `gsc-compare-2026-11-16/` compares Nov 7-10 with Nov 12-15. Both have a Pages.csv and a Queries.csv but no query x page breakdown and no per-day page data. The position figure is an impression-weighted average.
- Serper top-10 in `ranksim_raw.sqlite`: the same 30 core queries were pulled on Nov 6, 9, 11 and 13 (one pull each), Nov 16 (3 pulls) and Nov 17 (3 pulls). The 100-query and deep top-100 set exists only on Nov 16 within the open window.
- Older Google scrapes (Oct 29/30/31, Nov 2, Nov 4) were signed-out, captcha-truncated (7-10 cards), and mostly logged breadcrumbs ("smartcrewhq.com > Guides") instead of page URLs. Nov 13 `live-buyer` is 18 queries, signed in with pws=0. `rank-baseline/postfix-2026-11-10` are OpenRouter AI-answer ranks, not Google.

## Edit events (main, UTC)

68 commits touched `src/seo/pages` between Oct 25 and Nov 18, across 105 page files. Edits between the first and last open Serper pulls:

- #35, Nov 7 03:14: 9 files, retired five guides behind redirects.
- #38, Nov 10 16:33: 2 files.
- #39, Nov 10 18:33: 52 files.
- #40, Nov 11 23:27: 10 files.
- b8f590d, Nov 13 23:02: 74 files. Changes title case only; 0 words differ when compared case-insensitively.
- busybusy review, Nov 16 16:56: new page.
- Package 2/6/7/8 commits, Nov 17 12:25-13:39: 1.5-3h before the Nov 17 pulls.
- More batches on Nov 17 18:41-19:33 and Nov 18.

PR #41 merged and deployed to production on Nov 17 at 22:05 ET (994deb1; see `seo-tracker/smartcrew/pr41-live-verify-2026-11-17.md`). 15 of its 17 ledger pages were edited again on Nov 18.

## Counts (30 core queries, SmartCrew URL in the top 10 on at least one side)

- No word-material edit (≥20 words changed, case-insensitive) is followed by at least about 20h of lag before the next pull, except #38/#39 (before Nov 9, after Nov 11) and #40 (before Nov 11, after Nov 13).
- Strict rows: 12 (query, page) rows on 4 pages from 3 PRs (#38/#39 on Nov 10, #40 on Nov 11). Of those 12, 10 are entries into or exits from the top 10 rather than measured moves, and only 2 have a position on both sides. The pages are /all-in-one-dirt-work-software/, /blog/best-software-small-dirt-work-contractors/, /blog/best-excavation-software-20-50-employees/ and /compare/hcss-heavyjob-alternative/.
- Comparing Nov 9 with the Nov 16 three-pull median: 15 edited rows on 5 pages, of which 11 moved (10 up, 1 down).
- Noise base rate: 77 of 91 (85%) consecutive-pull transitions with no edit also moved. For edited rows it was 37 of 44 (84%).
- 11 of the 23 SmartCrew URLs Google showed for the core queries are redirect sources in vercel.json.
- GSC page level (Nov 7-10 vs Nov 12-15): 20 pages edited by #38-#40, 17 better and 3 worse. Pages not edited: 53 better and 14 worse. Only 7 edited pages have at least 20 impressions in each window.
- PR #41 ledger: 147 rows (115 rewrite, 32 new). Only 25 rewrite rows on 9 pages have a numeric before-position, and 13 of those are inside the top 10. The outcome is sealed (Nov 18 and later), falls inside the core update, and the pages were re-edited Nov 18.

## Verdict

Not enough for a held-out up/down test: there are about 12 strict rows, 4 pages and 2 edit days against a 40-row target. Movement in that window is dominated by a sitewide rise during the Nov 10-24 core update, URL rotation among SmartCrew's own pages, and redirects. A model that always predicts "up" would win.
