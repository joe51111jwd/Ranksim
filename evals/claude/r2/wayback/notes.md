# Wayback backfill of tracked pages (round 2, Nov 19 2026)

Script: `scripts/claude/r2_wayback.py`. Tests: `tests/test_claude_r2_wayback.py` (offline, no network). Everything here uses design days only (SERP rows and own fetches dated 2026-11-17 or earlier, Wayback captures up to 20261117235959 UTC).

## Result in one paragraph

The Wayback Machine (web.archive.org) cannot be reached from this box, so no capture was downloaded and no `pages` row was inserted. Every connection to web.archive.org times out inside the box's egress proxy, while archive.org itself answers. The script stopped cleanly after 6 refused connections in a row. As a fallback, the official availability API on archive.org was probed for every URL to size what a working run would find. It shows that few of these pages are archived inside the Oct 1 to Nov 17 window. Material changes found: 0. New usable rows for the edit-direction dataset: 0. The cross-source noise floor could not be measured because no Wayback copy shares a day with one of our own fetches.

PROBE_RESULTS_PLACEHOLDER

## URL list

`build_url_list()` takes every distinct URL in a top-10 row of `rsdata.load_rows("2026-11-17")` whose query is in group o30 or a70 (`rsdata.query_groups`). There are 764 URLs on 299 sites: 379 from the original 30 searches (listed first) and 385 that appear only in the 70 added searches. Order inside a group is first day seen, then best position, then URL. `urls.json` lists sha1(url), group and order. No URL is printed or written in plain text by the script, except the archived URL that the CDX API returns in its `original` field, which is a design-day URL.

## What happened on the network

- 2026-11-19T20:09:26Z to 20:17:00Z, `r2_wayback.py run`: 6 requests to the CDX API, all `httpx.ConnectError`. Backoff was 15 s, 30 s, then 120 s and 240 s on the next URL, and the run stopped with `service_refused: 6 refusals in a row`. The first URL has a `transient:error:ConnectError` record in `cdx.jsonl`, which a later run retries. Run summary: `runs.jsonl`.
- Diagnosis with curl, one request at a time: web.archive.org resolves to the proxy at 198.18.0.1 and every TLS connect ends after exactly 8.01 s with an unexpected EOF (seen 6 times between 19:54Z and 20:27Z). Plain http to web.archive.org returns an empty 500 from the proxy. A host that does not exist fails in 0.05 s, so the 8 s is the proxy trying and failing to open a connection upstream, not a policy refusal. archive.org and `https://archive.org/wayback/available` answer in about 0.3 s, and the availability API returned a 2026-11-11 capture for a test URL, so the Internet Archive is up and indexing current.
- I did not try other host names for the Wayback Machine to get around this.

## Fallback: availability API probe (archive.org)

`r2_wayback.py probe` calls `https://archive.org/wayback/available?url=<url>&timestamp=<t>` for t = 20261117235959, 20261106000000 and 20261001000000. Each answer is the single closest capture to t, in either direction. A capture inside the window counts if its timestamp is between 20261001000000 and 20261117235959 and its status is 200. Three probes can find at most three distinct captures per URL, so these counts are lower bounds. The API returns no digest, so it cannot tell distinct versions apart. A closest capture after the cutoff is recorded only as `closest_after_cutoff: true`, without its timestamp. Same politeness as the CDX path (one request at a time, at least 1.5 s apart, backoff). Records: `availability.jsonl`.

## Measurement (`r2_wayback.py measure`, design days only)

MEASURE_PLACEHOLDER

## Where the stored rows go

- `pages.fetched_at_utc` holds the Wayback capture time in UTC, converted from the 14-digit capture timestamp (for example `20261105123456` becomes `2026-11-05T12:34:56.000Z`). This matches the meaning of the 138 older `fetch_method = 'wayback'` rows, which hold the capture date at midnight.
- `pages.fetch_method` is `wayback:<14-digit capture timestamp>`, so the capture time can be read without parsing HTML or dates. It starts with `wayback`, so `rsdata.load_page_index` and `t2_common.fetch_table` keep excluding these rows.
- The download time is not in `pages`. It is `downloaded_at_utc` in `downloads.jsonl`, joined on `page_id` (or on url_sha1 plus timestamp).
- `http_status` is the status of the replay response, `content_type` its header, `sha256` the hash of the raw bytes, and `html` the bytes decoded as UTF-8 with replacement, the same as `refetch_html.py`.
- Each row is written through `lib.db.insert_page` inside `refetch_html.write_batch` (BEGIN IMMEDIATE, 60 s busy timeout, up to 12 retries on a locked or busy database). One row per transaction. Nothing is updated, deleted or vacuumed.

## Resuming and extending

- Resume: run `python scripts/claude/r2_wayback.py run` again from the repo with the venv active. It takes a lock (`/tmp/r2_wayback.lock`), so two copies cannot run at once. Before each CDX request it skips URLs with a final record in `cdx.jsonl`. Before each download it checks `downloads.jsonl` and then queries `pages` for a row with the same URL and `fetch_method`, so a sidecar line lost to a snapshot restore never causes a second download. Transient failures (429, 5xx, connection errors) are retried on the next run. 404, redirects to another capture time, non-HTML and over-5-MB bodies are final. Sidecar lines are fsynced one at a time, and a torn last line is ignored.
- If web.archive.org stays unreachable here, the run has to happen from a network that can reach it. The script writes straight into the local raw store, so a run elsewhere would need a small import step for the downloaded bytes (not built).
- Extend to later dates once those days are unsealed: `python scripts/claude/r2_wayback.py run --from 20261118 --to 20261130`. CDX records are keyed by window, so a new window is queried fresh. Downloads are keyed by capture timestamp, so nothing already stored is fetched again. `measure` stays sealed at 2026-11-17; scoring later days needs its own preregistration.
- `probe` is resumable the same way (key: url_sha1 plus probe timestamp).

## Files

- `urls.json`: ordered URL list as sha1 plus group.
- `cdx.jsonl`: one line per CDX attempt: url_sha1, window, query time, status, outcome, and on success the raw CDX JSON rows.
- `downloads.jsonl`: one line per capture attempt: url_sha1, capture timestamp, digest, attempted and downloaded times, status, bytes, sha256, page_id, outcome. Empty this run.
- `availability.jsonl`: fallback probe records.
- `runs.jsonl`: one summary line per run (requests, refusals, stop reason).
- `measure.json`, `pairs.csv`, `changes.csv`, `usable_rows.csv`: measurement output.
