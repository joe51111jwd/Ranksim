# Track workstream plan (session 2, branch `claude/rs-track`)

Owner files: `src/aeo/ranksim/plugins/tracking.py`, `src/aeo/ranksim/static/js/plugins/history.js`,
`src/aeo/ranksim/static/css/plugins/history.css`, `tests/test_tracking.py`, this file.

Shared files touched (smallest possible edits, flagged in the final report):

- `src/aeo/ranksim/serp.py`: providers gain `search_with_features(query, count) -> (hits, features)` and a pure
  `parse_features(provider, payload)`. `search()` is unchanged and still returns only organic hits.
- `src/aeo/ranksim/project.py`: `SearchResults.features` (dict, default empty, old files read as `{}`), and an
  optional `budget=None` argument on `pull()` that is threaded to the Google step. With `budget=None` the pull
  behaves exactly as before.
- `vercel.json`: only the `crons` key (owned by infra).

## 1. How Google results are pulled today

`project.pull()` runs `_google_step`, which resolves a provider with `serp.resolve_serp(settings, "auto")`
(SerpAPI, then DataForSEO, then Serper; Brave is never used as Google) unless a test passes `serp_provider`.
`_pull_google` calls `provider.search(search, depth)` for every search in 4 threads and writes
`pulls/<pull_id>/google.json` (`GoogleSnapshot`). The Studio calls `pull()` through `_run_pull(ui, ...)` with
`**ui.pull_options`. `studio.google_status(ui)` reports which provider is configured (keys never leave the server).

Serper is preferred for this product (`SERPER_API_KEY`). The status route reports the provider `resolve_serp` would
actually pick, so a server with several keys shows the truth.

## 2. Cost control

`tracking.Budget(root)` is the `budget` object passed to `pull()`. The plugin puts one in `ui.pull_options["budget"]`
when it registers, so every Studio pull and the classic screens use it with no edit to the pull routes.

- Counter file: `<projects root>/.tracking/usage.json`. The projects folder is what cloud storage mirrors, so the
  counter persists on Vercel (it is saved with the next `cloud.save()`, which every pull already does).
  Shape: `{"months": {"2026-11": {"searches": 12, "reused": 30, "capped": 0, "errors": 1}},
  "last_ok_at": iso, "last_error": "HTTP 403", "last_error_at": iso}`. Writes are atomic (temp file plus rename)
  under a process lock. Two servers pulling at the same moment can undercount (last cloud save wins); the cap is a
  guard, not an invoice.
- Month: UTC calendar month. Every paid request attempt counts as one search, success or failure.
- Cap: `RANKSIM_MONTHLY_SEARCH_CAP`, default 500 (Serper's free plan is 2,500 one-time credits). `0` blocks paid
  searches entirely.
- Freshness reuse: a search whose earlier REAL results were pulled less than `RANKSIM_SEARCH_FRESH_HOURS` ago
  (default 6) is copied from the previous pull with its original `pulled_at`, not paid for again. The window is
  shorter than a day so a daily re-check always pulls new data.
- Over the cap: before a pull starts, if any search needs a paid request and nothing is left, the Google step is
  not run. With earlier results the pull carries them (the note says the monthly cap was reached). With none it
  refuses with `SearchCapError` (a `ProviderConfigError`): "Monthly search cap reached: 500 of 500 searches used in
  November 2026. Raise RANKSIM_MONTHLY_SEARCH_CAP or wait until December 1." If the cap is hit in the middle of a pull,
  the remaining searches keep their earlier results with an error saying so.
- Provider health: every paid request records success or the HTTP error code (never the URL, which can hold a key).

`GET /api/v1/tracking/status` returns
`{provider, configured, used, cap, remaining, reused, month, resets, fresh_hours, last_ok_at, last_error, last_error_at,
cron: {secret_set}}`.

## 3. Scheduled re-checks

Per project setting in `<project>/plugins/tracking/settings.json`: `{"schedule": "off"|"daily"|"weekly"}`, default
weekly. `GET|POST /api/v1/projects/{slug}/tracking` reads and changes it (POST needs the CSRF header).

`GET /api/v1/cron/recheck` with `Authorization: Bearer <CRON_SECRET>` (Vercel's documented scheme, checked
2026-11-14 at vercel.com/docs/cron-jobs/manage-cron-jobs). No secret set, or a wrong header: 401, nothing runs. The
route is exempt from the owner login (Vercel cannot sign in) and only does this one job.

Due selection: projects that are not synthetic or historical, schedule not off, last REAL Google check (newest
`pulled_at` in any pull, or never) older than the interval minus a 2 hour slack (daily 22 h, weekly 166 h), and no
re-check attempt in the last 20 hours. Oldest check first. Each project runs only if the searches it would pay for fit
in the remaining cap and the estimated time (its last pull's seconds, at least 30 s) fits in
`RANKSIM_CRON_SECONDS` (default 240, under the 300 s `maxDuration`). The result lists every project with ran, skipped
and why. Attempts are stored in `<project>/plugins/tracking/state.json`.

`vercel.json` gets `"crons": [{"path": "/api/v1/cron/recheck", "schedule": "17 9 * * *"}]`: once a day, which the
Hobby plan allows. Vercel now calls crons a legacy feature that will migrate to `schedules`; the route works with both.

## 4. Rank history

Computed from every stored `google.json` of the project (api and import pulls; carried copies are skipped because
they repeat older data). A check is one distinct `pulled_at` per search (import rows without a date use the pull's
`written_at`). Position is the best position of any of our URLs; `null` means not in the results we hold (top 10).

`GET /api/v1/projects/{slug}/history` returns:

```json
{
  "site": "example.com",
  "competitors": ["rival.com"],
  "checks": 3, "first_at": "...", "last_at": "...",
  "changes": {"up": 1, "down": 0, "new": 0, "lost": 1, "same": 2, "none": 4, "first": 0},
  "searches": [
    {
      "search": "dirt work software",
      "position": 4, "previous": 6, "change": "up", "delta": 2, "url": "https://example.com/x",
      "points": [{"at": "2026-11-06T10:00:00+00:00", "position": 6, "url": "..."}],
      "competitors": {"rival.com": [{"at": "...", "position": 1}]},
      "features": {"people_also_ask": [...], "related_searches": [...]}
    }
  ],
  "schedule": "weekly", "last_check_at": "...", "next_check_at": "..."
}
```

`change` is `up`, `down`, `same`, `new` (not ranked before, ranked now), `lost` (ranked before, not now), `none`
(not ranked either time, or never checked) or `first` (only one check so far). `delta` is positive when it moved up.
Positions deeper than 10 count as not ranked, so a pull at depth 20 never turns into a false `lost` at depth 10.
`trend` has one row per pull: `{at, pull_id, searches, ranked, top3, average}` for the Overview chart. Competitors are the project's chosen competitors (Settings), else the 5
domains with the most top 10 slots in the latest pull.

`report["history"]` (through `extend_report`) is the compact form: the same top level fields, and per search only
`search, position, previous, change, delta, spark` (last 12 positions) and `features` (list of feature names present).

## 5. SERP features (storage shape, read by the discovery session)

Stored per search in `google.json` as `searches[i].features`. Keys appear only when the provider returned something.
Old files and providers without features give `{}`.

```json
{
  "people_also_ask": [{"question": "...", "snippet": "...", "url": "..."}],
  "related_searches": ["..."],
  "answer_box": {"title": "...", "snippet": "...", "url": "..."},
  "knowledge_panel": {"title": "...", "type": "...", "description": "...", "url": "..."},
  "top_stories": [{"title": "...", "url": "...", "source": "...", "date": "..."}],
  "videos": [{"title": "...", "url": "...", "source": "..."}],
  "images": [{"title": "...", "url": "..."}],
  "sitelinks": [{"position": 1, "url": "...", "links": [{"title": "...", "url": "..."}]}]
}
```

Lists hold at most 10 items, strings at most 300 characters. Parsed for Serper, SerpAPI and DataForSEO. Read them with
`tracking.serp_features(project)` (latest snapshot, `{search: features}`) or straight from
`latest_google(project).searches[i].features`.

## 6. UI (phase B, after P0)

- Overview tile "Rank changes": up, down, new, lost counts and a small line chart of the average tracked position
  per check (inline SVG).
- Searches column via `registerSearchColumn`: sparkline of our position (inline SVG, lower is better, drawn top).
- Per search history chart (sheet opened from the sparkline): our line plus top competitors, with PAA and related
  searches listed under it.
- Settings section: re-check schedule (off, daily, weekly) and the usage meter (used of cap, provider, health).
- Charts are hand-written SVG, bento tokens only, no dashes in copy.

## 7. Tests (`tests/test_tracking.py`, fakes only)

Counter and cap, month rollover, freshness reuse keeps the original date, refusal message, mid-pull cap, cron auth
(missing secret, wrong header, right header), due selection and ordering and cap/time limits, history math (up, down,
new, lost, same, none, dedupe of carried pulls), SERP feature parsing for all three providers and storage round trip,
routes through a bare FastAPI app.

## 8. Owner actions

Set `SERPER_API_KEY` and `CRON_SECRET` (any long random string) on Vercel. Optionally `RANKSIM_MONTHLY_SEARCH_CAP`.
