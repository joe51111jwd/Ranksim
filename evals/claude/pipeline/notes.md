# Free HTML re-fetch pass (Nov 19 2026)

Script: `scripts/claude/refetch_html.py`. Daily wrapper: `scripts/claude/collect_day.sh`. Test: `tests/test_claude_refetch.py`.

## What one pass does

- URL list: every distinct URL in `serp_results` with position <= 10 and snapshot_date >= today (America/New_York) minus N days (default 5). It skips sources that start with `keyword-sample:` and the page-relative deep source `smartcrew-index-deep:2026-11-16`. The `:abspos` deep rows with position <= 10 are kept. The list includes held-out days, so nothing prints, logs, or saves a URL. Output is counts only.
- Fetch: one plain-HTTP attempt per URL through `lib/collect_snapshot.py` `http_fetch_one`. That means the bot UA, robots.txt (403 or 401 on robots means disallow all), the public-host check by hostname (so the 198.18.0.0/15 DNS proxy works), a 3 s per-host delay, a 10 s connect and 20 s read timeout, and a 5 MB body cap. One worker per host queue, 10 workers, longest host first. robots.txt is fetched with the same function under a per-origin lock, so one slow robots.txt does not stall every host.
- Store: one new `pages` row per URL via `lib.db.insert_page`, in IMMEDIATE transactions of 20 rows, with the 60 s busy timeout and up to 12 retries with backoff on locked or busy. Success is `fetch_method='http'` with html. A failure is `http:<reason>` with html NULL, and status and hash kept as in the collector. `insert_page` only skips an exact (url, fetched_at_utc, sha256, fetch_method) repeat, so every pass inserts new dated rows, including for byte-identical pages.
- Sidecar: `refetch-<UTCstamp>.jsonl` has one line per URL: url_sha1, fetched_at_utc (same value as the DB row), body sha256, http_status, fetch_method, bytes, page_id, same_sha256_as_prior_http. `refetch-<UTCstamp>.summary.json` holds the aggregate.

## Nov 19 passes

Smoke test (`--limit 20`), 08:27:42Z to 08:28:09Z: 20 URLs, 18 ok, 2 `http_500`, 20 rows inserted. These 20 URLs were fetched again in the full pass 30 s later, so they have two rows dated today.

Full pass (`--since-days 5`, cutoff 2026-11-14), sidecar `refetch-20261119T082812Z.jsonl`:

| Count | Value |
| --- | --- |
| UTC window | 2026-11-19T08:28:12.894Z to 08:59:59.457Z |
| Active fetch time | 303 s (monotonic clock) |
| URLs selected | 1,208 on 506 hosts |
| Fetched ok (HTML stored) | 785 |
| Failed | 423 |
| Rows inserted into `pages` | 1,208 (0 skipped as duplicates) |
| `pages` total rows | 9,879 before smoke, 11,107 after |
| HTML bytes stored | 333.5 MB |
| Ok, same body sha256 as the last earlier http fetch | 123 |
| Ok, different sha256 | 453 |
| Ok, no earlier plain-HTTP fetch | 209 |

The UTC window is longer than the fetch time. The box was suspended from about 08:30Z to 08:56Z, and no rows are stamped in that gap. Some of the 6 ReadTimeout and 3 ConnectError failures may be requests cut by the suspend.

A different sha256 does not mean the content changed. Many pages carry nonces, timestamps, or rotating ads. Change detection should compare extracted text, not raw bytes.

Failure reasons:

| Reason | URLs |
| --- | --- |
| robots_disallow | 209 |
| http_403 | 125 |
| challenge_page | 57 |
| http_202 | 10 |
| error:ReadTimeout | 6 |
| error:ConnectError | 3 |
| non_html | 3 |
| http_429 | 3 |
| http_404 | 2 |
| http_500 | 2 |
| http_402 | 1 |
| http_454 | 1 |
| http_503 | 1 |

## DB growth

- File size before the smoke test: 3,253,936,128 bytes, with an empty WAL. After the full pass and checkpoint: 3,594,469,376 bytes. That is +340.5 MB for 1,228 rows (smoke plus full), about 277 KB per row.
- One full pass is about 335 MB. One pass a day is about 10 GB a month. `/workspace` had 50 GB free after the pass.
- Nothing is vacuumed, rewritten, or deleted. The store is append-only.

## Scheduling once a day

This box has no `crontab` or `cron` binary. On a machine with cron, run at 06:15 New York time:

```
15 6 * * * /workspace/ranksim-claude/scripts/claude/collect_day.sh >> /workspace/ranksim-claude/evals/claude/pipeline/cron.log 2>&1
```

Plain loop alternative. It wakes every 10 minutes and runs once per New York date after 06:00, so a suspended box catches up on wake instead of drifting the way `sleep 86400` does:

```
nohup bash -c 'S=/workspace/ranksim-claude/evals/claude/pipeline/last-run-date; while true; do d=$(TZ=America/New_York date +%F); h=$(TZ=America/New_York date +%H); if [ "$d" != "$(cat $S 2>/dev/null)" ] && [ "$h" -ge 6 ]; then /workspace/ranksim-claude/scripts/claude/collect_day.sh >> /workspace/ranksim-claude/evals/claude/pipeline/cron.log 2>&1 && echo "$d" > $S; fi; sleep 600; done' >/dev/null 2>&1 &
```

`collect_day.sh` runs the re-fetch, then prints (does not run) the three `scripts/claude/pull_serps.py` commands (pull-a, pull-b, pull-c). The query file defaults to `smartcrew-30-original.txt` and can be overridden with `RANKSIM_PULL_QUERIES`. The lead should confirm the query file.

## Known limits

- `http_fetch_one` reads the whole response, then trims it to 5 MB. The cap limits what is stored, not what is downloaded. This is the existing collector behaviour and was kept as is.
- For rows with no response (robots, errors), `fetched_at_utc` is the time the row was written, not the time of a fetch. This matches the collector.
- The 209 robots-blocked URLs never get content from this path. A disallow is not bypassed.
