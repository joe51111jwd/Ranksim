# Local HTML coverage

Measured on 2026-11-06 from files already in this repo. No crawl and no paid call.

## Files

- `smartcrew/serp.csv`: 135 labeled rows, 16 queries. The file has no pull date.
- `smartcrew/saved_html/manifest.csv`: 19 HTML files, all present.
- `smartcrew/pr8/manifest.csv`, `smartcrew/pr8_v2/manifest.csv`, `smartcrew/pr8_v3/manifest.csv`: 6 HTML files each.
- `smartcrew/pr8/home.html` is not listed in a manifest, so the join does not use it.

## What the join does

`aeo.ranksim.snapshot_join` matches a label URL to a manifest URL with `normalize_url`. That folds the trailing slash and the default port. It does not drop `www`, and it does not change `http` to `https`. Empty files are excluded. Nothing is written into a missing page.

When the same URL is in more than one manifest, the later directory wins. The order is `saved_html`, `pr8`, `pr8_v2`, `pr8_v3`. On this tree that keeps the 19 saved pages and uses `pr8_v3` for the 6 draft URLs that also appear in the older bundles.

## Counts

| Join | Labeled rows used | Queries with a pair | Pairs |
| --- | --- | --- | --- |
| Exact manifest URL | 35 | 12 | 33 |
| After normalization | 35 | 12 | 33 |

Normalization recovered 0 rows. 100 labeled rows have no file in those manifests. 29 of the 35 used rows come from `saved_html`. 6 come from `pr8_v3`. Dataset id for that set of file bytes is `ffb8bfab9e9244ce`.

Leave-one-query-out on those 35 rows, with no link-graph features and no judge scores, is pairwise 0.545 on 33 pairs and 12 queries. The same row count with `pr8` instead of `pr8_v3` is pairwise 0.515. That difference is different draft HTML, not more coverage. An older checkout reported about 0.485 on 33 pairs. This run does not reproduce that figure. It is not a broad accuracy claim. `llm_relevance` was not scored, because no judge cache was supplied, and it is not a default feature.

## What would add rows

The other 100 labeled URLs need HTML that is not in the repo. A saved file listed in a manifest would be picked up by this join. A live crawl is a separate step and was not run. `index-serp` on the audit branch still looks up saved HTML by the exact label string. This branch does not change that file.
