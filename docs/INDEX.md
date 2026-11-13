# Local page index

Ranksim needs the text of every page it ranks, the way a search index does, so a rerank reads the page instead of guessing from a title. The index is a SQLite database plus parquet files under `data/index/`. That directory is gitignored. Nothing in it is committed, and the Serper key is never written there.

The frozen scorer from `3ad2613` is unchanged. `evals/preregistered/` is unchanged. This index does not promote a ranker.

## Commands

From the repo root:

```bash
python -m aeo.ranksim index build --project "Dirt Crew"
python -m aeo.ranksim index status --project "Dirt Crew"
python -m aeo.ranksim index refresh --project "Dirt Crew"
```

`--project` is the normal way to run it. The index is stored in that project's folder (`data/projects/<slug>/index/`), and the build uses only that project's searches. Candidates are the URLs in its saved pulls, its own site, and the competitor domains saved on Setup. A snapshot directory is optional. Pass `--snapshot` when you have a SERP export that is not already a pull. Rows for other queries are left out.

```bash
python -m aeo.ranksim index build --snapshot /path/to/snapshot
```

That form, with no project, writes `data/index/` and is what the Nov 13 box command below uses.

`build` gathers candidates, fetches pages that have no usable text, writes corpus signals, and prints coverage. `refresh` does the same and also refetches full text older than `--stale-days` (default 14). `status` prints the coverage already on disk.

Useful flags:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--project` | none | Project name or slug. Index goes in that project's folder. Searches, pulls, and competitors come from the project |
| `--index-dir` | project index, or `data/index` | Where `index.sqlite`, the parquet files, and `coverage.json` go |
| `--snapshot` | none | Repeatable. A directory with a SERP CSV (`serp_serper_live.csv`, `serp.csv`, or `slim.csv`) and optional `html/` plus `html_manifest.csv` |
| `--queries` | none | Text file, one tracked query per line. Queries in the snapshots are always included |
| `--num` | 100 | Serper organic results per query, capped at 100. Positions 11-100 are labels |
| `--depth` | 1 | Internal-link hops from pages that rank. `0` keeps SERP URLs only |
| `--max-internal` | 50 | Relevant internal URLs kept per ranking domain |
| `--credit-cap` | 300 | Hard Serper credit ceiling for this run |
| `--delay` | 1 | Seconds between fetches to the same host |
| `--offline` | off | Snapshot HTML only. No Serper, HTTP, browser, or archive call |
| `--estimate-only` | off | Print the credit estimate and write the local seed. Spend nothing |
| `--skip-search` | off | Do not call Serper search. Still fetches URLs already in the pool |
| `--no-browser` | off | Skip the headless browser rung |

`SERPER_API_KEY` is read from the environment, the same place as the rest of Ranksim. Do not pass it as an argument. The commands never print it. If it is missing, the build says so and continues. Serper search and Serper scrape are skipped. HTTP, the browser, Common Crawl, and Wayback still run, using the project's saved URLs and competitor homepages.

## What goes in the pool

With `--project`, the tracked queries are that project's searches. The pool starts from the URLs in its pulls, the project site, and each saved competitor's homepage. Other queries in those pulls are ignored.

For each tracked query the pool is the union of:

1. A Serper search with `num` up to 100 (`gl=us`, `hl=en`), when `SERPER_API_KEY` is set. Positions 11-100 are labels, same as the top 10.
2. Every URL already saved for those queries (project pulls, or a snapshot you pass with `--snapshot`).
3. Internal links of ranking domains and of named competitors. A link is kept when it stays on that domain, the path is not login, cart, privacy, or a file asset, and the URL or anchor shares a query term of 4 characters or more. `--depth` is the hop count.

## Fetch ladder

For each URL that does not already have usable text, in order:

1. Plain HTTP, with the crawler user agent. `robots.txt` for that user agent is obeyed. A disallow is not fetched by our crawler.
2. Headless Chromium, when Playwright is installed. `robots.txt` is checked again for the browser user agent. A disallow is not bypassed. Install with `pip install playwright` and `playwright install chromium` if you want this rung. Without it, the rung is recorded as not installed and the ladder continues.
3. Serper scrape: `POST https://scrape.serper.dev` with JSON `{"url": "<url>"}` and header `X-API-KEY`. This runs only when `SERPER_API_KEY` is set and the credit cap can still cover a 10-credit page. Our crawler stops at a robots disallow. The scrape rung still runs, because that is a separate source.
4. Common Crawl and the Wayback Machine. The newer capture is fetched. Its capture date is stored. This is the last resort.

Each stored page has the final URL, canonical, title, meta description, headings, main text, word count, schema types, outlinks, inlinks inside the corpus, anchor text received, published and updated dates, content hash, source, and capture date. Sources are `snapshot_html`, `http`, `browser`, `serper_scrape`, `common_crawl`, and `wayback`.

After the fetches, the index computes corpus PageRank, aggregated anchor text, and per-domain page counts. PageRank here is the corpus graph, not web-wide PageRank.

Files:

- `data/index/index.sqlite`
- `data/index/pages.parquet`
- `data/index/candidates.parquet`
- `data/index/edges.parquet`
- `data/index/domain_stats.parquet`
- `data/index/coverage.json`
- `data/index/credit_estimate.txt`

## Credits

Serper charges 1 credit for a search that asks for up to 10 results, and 2 credits for one request that asks for 11 to 100. If that response is short, each extra page of 10 is another 1-credit call. A scrape is 2 credits on a typical page and up to 10 on a hard page. Before a scrape call the build reserves 10, then records the `credits` field Serper returns. A call that would pass the cap is not sent.

The first search request for each query is the deep request (`num` 100, 2 credits). Extra pages stop once they would leave fewer than `2 x missing top-10 URLs` credits for scrapes. Top-10 URLs are fetched, and scraped if the free rungs fail, before positions 11-100 and before internal links.

The build prints this estimate before the first paid call.

## Coverage

`coverage.json` and `index status` report:

- percent of labeled rows with usable text, by query and by rank band
- the same percent for top-10 rows, against a 95% target
- every URL that still has no usable text, with the reason

Usable text is real main text, not a challenge page and not a thin file. A saved HTML file that is only a challenge or a few words stays missing.

## Features

When `data/index/index.sqlite` exists, or `RANKSIM_INDEX_DIR` points at an index, corpus features for retrieval read that index. BM25F scores title, URL, headings, full text, and anchor text from the stored pages. `content_missing` stays 1 when the index has no usable text.

```python
from aeo.ranksim.localindex import feature_rows, retrieve_bm25f

ranked = retrieve_bm25f("dirt work software", "data/index")
rows = feature_rows("dirt work software", urls, "data/index")
```

The frozen full-coverage scorer does not read this index. Coefficients are not retuned. No ranker is promoted.

## Offline seed, 13 Nov 2026

The Nov 13 snapshot (`serp_serper_live.csv`, `html/`, `html_manifest.csv`) was seeded with:

```bash
python -m aeo.ranksim index build \
  --offline \
  --index-dir data/index \
  --snapshot /tmp/ranksim-live-snapshot-2026-11-13 \
  --num 100 \
  --depth 1 \
  --max-internal 50 \
  --credit-cap 300
```

That run spent 0 credits. Result on the 298 top-10 rows:

- 210 of 298 rows (70.5%) have usable main text. 93 unique URLs.
- The snapshot had a saved HTML file on 216 rows (96 unique URLs). 6 of those rows, 3 unique URLs, were too thin to use. 82 rows had no file. Together that is 88 rows and 59 unique URLs with no usable text.
- Reasons on those 59 URLs: 33 `robots_disallow`, 11 `challenge_page`, 9 `http_403`, 3 `thin_or_empty`, 2 `error:SSLError`, 1 `http_429`.
- Depth 1 also recorded 726 internal URLs. They were not fetched. Their reason is `offline`. Unique candidates: 878. Unique text coverage including those unfetched links: 93 of 878 (10.6%). The 95% target is on top-10 rows, not on unfetched internal links.
- The top-10 target of 95% is not met by the seed. The live build below is what fills the gap.

## From the app

On Setup, **Build page index** starts the same build for that project. Progress is the job panel on the project page, and `GET /api/jobs/{id}` returns the steps (`index_estimate`, `index_fetch`, `index_coverage`). When it finishes, `GET /p/{slug}/index` is the coverage page and `GET /p/{slug}/index?format=json` is the coverage report. The button is a `POST /p/{slug}/index` with the page's CSRF token.

The public demo does not start this job.

## Box run

Unpack the Nov 13 snapshot, export `SERPER_API_KEY` in the shell (do not echo it), and from the repo root run:

```bash
tar -xzf ranksim-live-snapshot-2026-11-13.tgz -C /tmp
python -m aeo.ranksim index build \
  --index-dir data/index \
  --snapshot /tmp/ranksim-live-snapshot-2026-11-13 \
  --num 100 \
  --depth 1 \
  --max-internal 50 \
  --credit-cap 300 \
  --delay 1
```

Pass older snapshot directories with more `--snapshot` flags when you have them. Every URL in those files joins the pool.

Credit estimate for this candidate list, printed by the offline seed before any spend:

- 30 queries. One `num=100` search each is 2 credits: **60 credits**.
- If a search returns only 10 results, extra pages are 1 credit each and would be 300 credits for 10 pages across 30 queries. Extra pages stop once fewer than **118 credits** would remain for scrapes. The most the search side can spend under that rule is 60 plus 122 extra-page credits, which is 182, leaving 118.
- 59 top-10 URLs have no usable text. Scraping all of them is **118 credits** at 2 credits each, and **590** at the 10-credit worst case.
- The hard cap is **300**. A run whose searches cost 60 and whose scrapes cost 2 spends **178** if only those 59 URLs need a scrape. If every scrape costs 10, the cap covers the 60 search credits plus 24 scrapes (240), and the rest of the 59 wait for `index refresh` after free rungs and after you raise the cap.
- HTTP, the browser, Common Crawl, and Wayback cost no Serper credits. They run before scrape, so the scrape bill can come out lower than 118. The 726 internal URLs are fetched after the labeled URLs. Scrape credits are spent on missing top-10 URLs first.

Then:

```bash
python -m aeo.ranksim index status --index-dir data/index
```

The status block is the coverage check. Top-10 text at or above 95% is the target for that live run.
