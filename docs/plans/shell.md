# Shell plan (workstream 10)

Owner: build session `shell`, branch `claude/rs-shell`. Owns `js/app.js`, `js/core.js`, `js/hooks.js`,
`js/charts.js`, `js/screens/overview.js`, `js/screens/settings.js`, `css/base.css`, `tests/e2e/`.

Goal from James: "You can see where you're ranking, you can see charts of everything that's ranking."
Look is fixed by BUILD_PLAN section 2. Every number on screen comes from the report; nothing is invented.

## Overview bento (desktop, 4 columns; tablet 2; phone 1)

```
+---------------------------+-------------+-------------+
| First page of Google  W   | Avg pos  B  | Top 3    T  |   W white card, T gray tile, B black tile
| 3 of 12   squares         | #6.2        | 2           |
+---------------------------+-------------+-------------+
| Where you rank, per search  (rankDots)  W  | Position |
| one row per search, #1..#10 + Out,          | spread T |
| solid dot real, open ring PREDICTED         | columns  |
+---------------------------------------------+----------+
| Who takes the top 10 (shareBars)  T | PREDICTED vs   W  |
| you blue, tracked dark, others gray | real (scatter)    |
+-------------------------------------+-------------------+
| Biggest opportunities   W           | Top issues   T    |
| 4 to 20 that could move, no page    | 5 rows, All link  |
+-------------------------------------+-------------------+
| [open slot: registerOverviewTile, e.g. rank changes]    |
+---------------------------------------------------------+
| Try a change  B  (Open editor)                          |
+---------------------------------------------------------+
```

The same-day backtest ring moves to Settings (model section). It is not forward accuracy and the Overview
should not lead with it. Plugin tiles from `registerOverviewTile({order, render(report)})` are placed by
`order` after the core tiles; a tile that throws is skipped with a console warning, never a broken page.

## Charts (`js/charts.js`, done in phase A)

Pure functions return a node tree; `toSvg` makes a string (tests), `mount` makes SVG elements (no
innerHTML), `fit(host, draw)` redraws at the real pixel width so text is 1:1 and 1px lines snap to pixels.
Colors are classes (`rc-you`, `rc-rival`, `rc-other`, `rc-grid`, `rc-track`, `rc-pred`, `rc-text`,
`rc-muted`, `rc-value`), mapped to tokens in `css/base.css`. No inline styles (CSP). Each chart has
`role="img"`, a `<title>` and a `<desc>` that states the data, and a `<title>` per mark.

| Function | Shows | Empty state |
| --- | --- | --- |
| `rankDots(rows)` | position per search, real dot and PREDICTED ring | "No searches yet" |
| `positionColumns(rows)` | #1, #2 to 3, #4 to 10, Not in top 10, Not checked | "No searches yet" |
| `shareBars(share)` | top 10 spots by domain, you always shown | "No rankings pulled yet" |
| `predictedVsReal(rows)` | scatter where both exist, diagonal = agreement | "No PREDICTED positions yet" |
| `opportunities(rows)` | data only: 4 to 20 near misses, then searches with no page | list says "Nothing to move yet" |
| `sparkline(values)` | for plugin tiles such as rank history | "Not enough history yet" |

Unit tests: `node --test tests/e2e/charts.test.mjs` (also run by `tests/e2e/test_e2e.py`).

## Per-search SERP view

Searches screen detail tile: the real top 10, the user's row in blue with a "You" label, tracked
competitors marked, PREDICTED position beside the real one when the model scored the page. Each row has
Open in editor (any URL opens in the editor, BUILD_PLAN 1.8). A search with no page gets "Write a page".

## Home

The address box stays the hero. Under it: recent sites (cards, last pulled, top 10 count), a clear
"Open the demo" entry, and immediate feedback on submit (button spinner, then the editor with the live
page while analysis runs beside it).

## States

| State | Treatment |
| --- | --- |
| Loading | skeleton tiles in the bento shape (gray blocks, no spinner wall); reduced motion: no shimmer |
| Empty | one sentence and one button that does the next step (Refresh, Add searches, Open the demo) |
| Error | what failed in plain words plus a next step button (Try again, Settings) |
| Offline | top bar notice from `online`/`offline` events; writes disabled until back |
| Signed out | login card; any API 401 returns there and keeps the path to come back to |
| Slow analysis | live steps with timer; after 60 s a line says it keeps running and the page stays editable |
| No search data | callout with the owner action (SERPER_API_KEY) or Refresh |

## Mobile and accessibility

390px: one column, 16px gutters, nav becomes a horizontally scrolling segment inside its own box (the page
never scrolls sideways), charts redraw at width. Tablet: two columns. Keyboard: skip link first, visible
focus rings (2px blue, offset), sheets trap focus and close on Escape, charts are focusable only through
their tile heading. ARIA: `aria-current` on nav, `role=status` toast, live region for analysis steps.
`prefers-reduced-motion`: no transitions or bar animations.

## Consistency

One set of components in `css/base.css`: `.tile` (gray), `.tile.white`, `.tile.black`, `.tile-head`,
`.tile-k`, `.tile-big`, `.tile-sub`, `.rowcard`, `.label-*`, `.btn-*`, `.seg`, `.callout`, `.skeleton`,
`.empty-state`, and the chart classes. Area files only lay out; they do not restyle these.

## End-to-end suite (`tests/e2e/`, done in phase A)

- `server.py`: private mode (password `e2e-pass`), fake Google (`FakeGoogle` from `tests/test_hosted.py`,
  answering unknown searches too), fake crawler and preview fetch. No network.
- `selectors.mjs`: every selector in one place.
- `run.mjs`: login (wrong then right password), home focus, skip link, open a URL and watch analysis,
  Settings save searches and Refresh, Overview, Issues and the issue sheet, Searches and the top 10,
  Competitors, editor edit and Test changes, signed out, then every screen at 390px with reduced motion,
  then sign out. Every screen: no horizontal scroll, no em or en dashes in rendered copy, aria labels or
  SVG text, no console errors. Screenshots at deviceScaleFactor 2 to `tests/e2e/screenshots/` (ignored).
- `test_e2e.py`: starts the server on a free port, runs the script, skips without node or Playwright.

Phase B adds: Overview charts present and labeled, SERP view highlight, offline banner, skeletons, demo
entry, plugin tile slot, tablet width.
