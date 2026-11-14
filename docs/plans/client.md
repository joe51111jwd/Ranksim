# Client delivery plan (workstream 8, `client`)

Branch `claude/rs-client`. Owns `plugins/share.py`, `js/plugins/share.js`, `static/share/`, `css/print.css`,
`tests/test_share.py`. Everything else is touched only through the P0 hooks.

## What the client gets

A read-only report at `/r/<token>`. No login, no editor, no settings, no other projects, no internal notes.

- Header: agency name and logo, the site, the report date, the Google data date.
- Overview numbers: searches tracked, searches checked, in the top 10, in the top 3, average position, issues.
- Where the site ranks per search: Google position (labeled with the Google data date) and, when an engine is
  built, the PREDICTED position among the stored candidates (always labeled PREDICTED).
- Issues and what is being done: severity, title, comment, the fix, and the owner's status and plan
  (Not started, Planned, In progress, Done).
- Competitors: the competitor list and share of the top 10.
- Change log, short form: date, kind and a one line summary of public entries only.
- Print and CSV buttons. Demo data is labeled as demo data. No accuracy number is shown at all
  (the pull's same-snapshot pairwise score is never sent).

## Links

- Token `<id>.<sig>`: `id` is 18 random bytes (base64url, 24 chars), `sig` is HMAC-SHA256 over
  `share|<slug>|<id>` (22 chars). The key mixes `RANKSIM_SECRET` with a random key stored at
  `<projects root>/_plugins/share/secret.key` so links survive restarts and cloud round trips.
- Stored per project in `<project>/plugins/share/links.json`: id, label, created, expires, revoked, last used.
  The slug never appears in the URL. A token resolves only if the format, the signature, the stored link,
  the expiry and the revocation all check out. Every failure gets the same answer.
- Revoke marks the link revoked (kept for the owner's record). Optional expiry in days (1 to 365).

## Safety

- Public paths are `/r/...` only. Phase B adds `/r/` to the login middleware's open list deliberately, and a
  `csp_for` entry: `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self';
  connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'`.
  Until then the plugin adds that policy as a second header (both are enforced).
- `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet`, `<meta name="robots">`, `Referrer-Policy: no-referrer`,
  `Cache-Control: no-store`.
- Rate limit per client address: 120 requests a minute on `/r/`, 20 bad tokens a minute, and a global cap on
  bad tokens. Answers 429.
- The client JSON is built from a whitelist, never by filtering the owner report. No page HTML, no element
  selectors, no evidence, no test runs, no slugs, no paths, no keys, no CSRF token, no pull metrics.
- Logo: PNG, JPEG or WebP only (no SVG), at most 200 KB, magic bytes must match the declared type, stored as
  `logo.<ext>` under `_plugins/share/`, served with its fixed type and `nosniff`.
- CSV cells that start with `= + - @` or a tab or carriage return get a leading quote (formula injection).

## Change log

`log_change(project, kind, summary, *, detail="", public=True, key=None)` appends to
`<project>/plugins/share/changelog.jsonl` (capped). Kinds: analysis, google, test, publish, issue, share, note.
`key` makes an entry idempotent. `after_pull` logs every pull. Reading the log also folds in finished pulls and
draft tests already on disk (keys `pull:<id>`, `test:<id>`), so old projects have a history. Other plugins call
`log_change` (publish logs "edits published"). Marking an issue Done logs "issue resolved". The owner sees
detail and private entries; the client sees public summaries only.

## Routes

Owner (signed in, writes need `x-ranksim-csrf`, blocked on the public demo):
`GET|POST /api/v1/projects/{slug}/share`, `POST .../share/{id}/revoke`, `GET|POST .../changelog`,
`GET .../client-report`, `GET .../issue-plans`, `POST .../issues/{issue_id}/plan`,
`GET .../export/rankings.csv`, `GET .../export/issues.csv`, `GET|POST /api/v1/branding`,
`GET|POST /api/v1/branding/logo`, `POST /api/v1/branding/logo/delete`, `GET /report/{slug}` (print preview).

Public: `GET /r/assets/{name}`, `GET /r/{token}`, `.../data`, `.../logo`, `.../rankings.csv`, `.../issues.csv`.

## Frontend

- `static/share/index.html`, `share.js`, `share.css`: one small page, no inline script or style, DOM built with
  `textContent`. Reuses the bento tokens (Geist, `--bg`, `--tile`, `--card`, `--blue`, `--dark`). Works at phone width.
  Print styles in the same CSS: light colors, no buttons, rows never split, a header with agency, dates and data dates.
- Phase B: `js/plugins/share.js` registers a Settings section (agency name, logo) and a Share screen
  (create, copy, revoke links, open the print view, CSV downloads, the change log, issue plans).
  `css/print.css` hides the Studio chrome when the owner prints a Studio screen.

## Tests (`tests/test_share.py`)

Create, use, revoke, expiry, forged and tampered tokens, cross-project isolation, owner routes stay closed with a
password, the payload has no keys, HTML, paths, slugs, test internals or private notes, headers (CSP, noindex),
rate limit, logo validation (type, size, magic, SVG refused), CSV content and formula escaping, change log merge.
