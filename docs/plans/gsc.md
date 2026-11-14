# Workstream gsc: Google Search Console on/off

Owner session: build session 1, branch `claude/rs-gsc`. Owns `src/aeo/ranksim/plugins/gsc.py`,
`src/aeo/ranksim/secretbox.py` (shared interface, see below), `js/plugins/gsc.js`, `css/plugins/gsc.css`,
`tests/test_gsc.py`.

## Goal

The only thing a user connects is Google Search Console, and it is a simple on/off per project. When on, Ranksim
reads the last 28 days of Search Console data for the project's property, shows clicks, impressions, CTR and
average position (labeled as Google Search Console data with the date range), and feeds the top queries into
search discovery. When the owner has not created the Google OAuth client yet, every screen shows a clear
"Not set up yet" state with the exact steps.

## Owner setup (once)

1. Google Cloud Console: enable the **Google Search Console API**.
2. OAuth consent screen: External, scope `https://www.googleapis.com/auth/webmasters.readonly`, publishing status
   **In production** (in Testing, Google ends refresh tokens after 7 days; an unverified app warning is shown when
   connecting, click Advanced, then continue).
3. Credentials: OAuth client ID of type **Web application**, authorized redirect URI
   `https://ranksim.vercel.app/api/v1/gsc/callback` (plus `http://127.0.0.1:8765/api/v1/gsc/callback` for local use).
4. Vercel env vars: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`. `RANKSIM_SECRET` must already be set
   (it keys the token encryption). Optional `GOOGLE_OAUTH_REDIRECT_URI` overrides the computed redirect URI.

## secretbox (shared helper)

`src/aeo/ranksim/secretbox.py`, also used by the publish workstream:

- `seal(plaintext: str, *, purpose: str) -> str` and `unseal(token: str, *, purpose: str) -> str`.
- Key: HKDF-SHA256 over `RANKSIM_SECRET` (salt `ranksim-secretbox-v1`, info includes the purpose), so each purpose
  has its own 256-bit key. Cipher: AES-256-GCM, random 96-bit nonce, purpose also bound as associated data.
- Token format `v1.<base64url(nonce || ciphertext || tag)>`.
- `RANKSIM_SECRET` must be at least 32 characters (HKDF is fast, so the secret must be random, not a password).
- Any failure (tamper, wrong purpose, wrong key, bad format, missing or short secret) raises `SecretBoxError`
  whose message never holds key material or plaintext. `configured()` says whether `RANKSIM_SECRET` is usable.
- Library: `cryptography` (already installed transitively, now declared in `pyproject.toml` as `cryptography>=42`
  so Vercel installs it).

## OAuth flow

1. Settings toggle on. If no Google connection exists, the UI calls
   `POST /api/v1/gsc/projects/{slug}/connect` (CSRF header). The server returns the Google authorization URL with
   `scope=webmasters.readonly`, `access_type=offline`, `prompt=consent`, `include_granted_scopes=true`,
   `redirect_uri=<origin>/api/v1/gsc/callback`, and `state`.
2. `state` is `seal(json, purpose="gsc-oauth-state")` of `{slug, nonce, exp (10 minutes), sess, redirect_uri}`.
   `sess` is a hash of the owner session cookie (empty in local mode with no password). The `nonce` is also set as
   the cookie `rs_gsc_nonce` (HttpOnly, SameSite=Lax, path `/api/v1/gsc`, 10 minutes, Secure on https).
3. `GET /api/v1/gsc/callback` (top-level redirect from Google, so no CSRF header; the login middleware still
   requires the signed-in owner). It checks: state unseals, not expired, nonce equals the cookie, session hash
   equals the current session. Then it exchanges the code (same redirect URI), checks that the scope was granted,
   requires a refresh token, seals and stores the tokens (an older refresh token is not revoked: Google's revoke ends
   the whole grant, including the new token), turns the project on,
   auto-matches the property, fetches data, saves to cloud storage, clears the nonce cookie, and redirects to
   `/app/{slug}/settings?gsc=<result>` (`connected`, `needs_property`, `denied`, `scope_missing`, `bad_state`,
   `expired_state`, `bad_nonce`, `bad_session`, `no_refresh_token`, `invalid_grant`, `not_configured`).
4. Token refresh: the access token is reused until 60 seconds before expiry, then refreshed with the refresh
   token. A 401 from the API triggers one forced refresh and retry. `invalid_grant` on refresh (revoked or expired)
   deletes the stored connection, so the state becomes `not_connected`. `invalid_client` (a wrong client secret)
   keeps the stored grant and shows an error until the env vars are fixed.
5. Disconnect: `POST /api/v1/gsc/disconnect` revokes the refresh token at Google (best effort) and deletes the
   stored connection. Project settings are kept.

## Data model

One Google connection per Ranksim (the owner's account), shared by all projects:

```
<projects root>/_plugins/gsc/account.json
  {refresh_token: sealed, access_token: sealed, expires_at, scope, connected_at, refreshed_at}
```

`_plugins` cannot clash with a project slug (slugs start with a letter or digit) and is mirrored by cloud storage
with the rest of the projects folder.

Per project, under `project.dir / "plugins" / "gsc"`:

```
settings.json   {enabled, property, last_error, error_at, updated}
latest.json     the newest snapshot
snapshots/<YYYYMMDDTHHMMSSZ>.json   last 8 snapshots
```

Snapshot: `{source, label, property, fetched_at, start, end, days: 28, search_type: "web", row_limit: 1000,
capped: {queries, pages, query_pages}, totals: {clicks, impressions, ctr, position}, queries: [{q, ...metrics}],
pages: [{page, ...metrics}], query_pages: [{q, page, ...metrics}]}`.

Date range: the 28 days ending two days before today (UTC), since Search Console's final data trails by about two
days. Four `searchAnalytics.query` calls: no dimension (totals, which include anonymized queries), `query`, `page`,
and `query,page`, each with `rowLimit=1000`. Data is refetched when older than 12 hours, on property change, or on
demand.

## States

`report["gsc"].state` and `GET /api/v1/gsc/projects/{slug}`:

| State | Meaning |
| --- | --- |
| `not_available` | public demo |
| `not_configured` | env vars missing; includes `setup.steps` and `setup.missing` |
| `off` | the project's toggle is off |
| `not_connected` | on, but no Google connection |
| `needs_property` | connected, no matching property; the owner picks one |
| `no_data` | ready to fetch, nothing stored yet |
| `ready` | data available |
| `error` | the last fetch failed; `message` says why; older data is still shown |

## Routes

All under `/api/v1/gsc`, all require the signed-in owner. Writes need `x-ranksim-csrf` and are refused on the
public demo.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/status` | configured, connected, redirect URI for this host, setup steps |
| GET | `/projects/{slug}` | project state (above), range, totals |
| GET | `/projects/{slug}/properties` | `sites.list` with usable flags and the suggested match |
| POST | `/projects/{slug}` | `{enabled?: bool, property?: str}`; property must be a usable listed site |
| POST | `/projects/{slug}/connect` | returns `{url}` for Google, sets the nonce cookie |
| GET | `/callback` | Google's redirect (validates `state`) |
| POST | `/projects/{slug}/refresh` | fetch now |
| POST | `/disconnect` | revoke and forget |

Property auto-match order: `sc-domain:<domain>`, `sc-domain:<parent domains>`, then URL-prefix properties for
the site host, `www.` and bare host, https before http. Only `siteOwner`, `siteFullUser` and `siteRestrictedUser`
count.

## Hooks

- `register(app, ui)`: the routes above.
- `extend_report`: `report["gsc"] = {state, message, label, configured, connected, enabled, property, range
  {start, end, days, label}, fetched_at, totals, queries (top 100 by impressions), pages (top 50 by clicks),
  query_count, page_count, searches: {<project search>: {clicks, impressions, ctr, position, page}}}`. Reads files
  only, never the network.
- `discovery_sources`: when on, up to 30 top queries `{q, weight, source: "gsc", impressions, clicks, position}`.
  The weight is on discovery's scale (a title phrase scores 3.0 to 4.5, 2.5 is the bar): `1.5 + 3.5 * sqrt(share)`
  where share is impressions over the top query's, so the top query scores 5.0, a query with a tenth of its
  impressions about 2.6, and a rare query only counts when the site's pages also use it. Refreshes stale data first
  (best effort). Not called on a site's first analysis (no project yet), so GSC queries join from the next analysis.
- `after_pull`: refresh stale data (best effort, errors recorded in settings).
- `settings_schema`: toggle and property.

## UI (built in phase B, js/plugins/gsc.js and css/plugins/gsc.css)

- **Settings section** (`registerSettingsSection`): "Google Search Console" with an on/off switch, the property
  picker (select from `/properties`, suggested one first), last updated time, "Refresh now" and "Disconnect".
  When not configured: a "Not set up yet" card with the numbered owner steps and the exact redirect URI.
- **Searches column** (`registerSearchColumn`, key `gsc`): "Search Console" with clicks and impressions per search
  from `report.gsc.searches`; the cell title gives CTR, average position and "Google Search Console data, <range>".
  The column header is always there (the registry is static); its cells are empty until GSC is ready.
- **Overview tile** (`registerOverviewTile`): totals (clicks, impressions, CTR, average position) labeled
  "Google Search Console data" and the date range, plus the top 5 queries. Off or not set up: a small tile that
  links to Settings.
- Copy has no em dashes or en dashes. Phone width works.

## Tests (`tests/test_gsc.py`)

Fake Google behind `httpx.MockTransport` (token, revoke, sites.list, searchAnalytics.query), a fake clock, no
network. Covered: secretbox round trip, tamper, wrong purpose, wrong key, missing secret; not configured steps;
full flow (toggle, auth URL params, callback, sealed storage, auto-match, request bodies and date range, report
section, discovery weights, freshness, refresh grant, disconnect and revoke); 401 retry; revoked grant; manual
property pick and validation; forbidden property error state and recovery; bad, forged, expired state; nonce
cookie; session binding; login and CSRF required; Google denial, missing scope, missing refresh token, bad code;
rotated secret; local mode single use; public demo; hooks with no context; redirect URI override.

## Browser check (phase B)

Playwright against two local servers with the fake Google (`ui.gsc_transport`) and Google's authorization page
intercepted: not set up state with steps and redirect URI, toggle on, consent redirect with the right scope,
offline access and consent prompt, callback, Settings totals and property picker, Overview tile, Searches column,
phone width (390 px, no horizontal scroll), disconnect. No console errors.

## Known gaps

- One Google account per Ranksim. A client whose property lives under a different Google account must add James's
  account as a user in Search Console (the normal agency setup).
- The callback answers a JSON 401 when the owner is signed out at the moment Google redirects back (the login
  middleware handles `/api/` paths that way). Rare; signing in and starting again works.
- Only web search type, final data, 1000 rows per dimension set. Capped flags say when more rows exist.
