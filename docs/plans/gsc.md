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

## Owner setup guide (for James, about 15 minutes, once)

You need: the Google account that can see your clients in Search Console, and access to the ranksim project on Vercel.
Google renames menu items now and then; if a name below differs, look for the closest match.

**A. Google Cloud: create the project and turn on the API**

1. Open https://console.cloud.google.com and sign in with that Google account.
2. In the project picker at the top, click **New project**. Name it `Ranksim`, click **Create**, then select it.
3. Open the menu (top left), **APIs and services**, **Library**. Search for **Google Search Console API**, open it,
   click **Enable**.

**B. Google Cloud: the sign in screen (OAuth consent)**

4. Menu, **APIs and services**, **OAuth consent screen** (newer consoles call it **Google Auth Platform**; click
   **Get started** if asked).
5. **App name** `Ranksim`, **User support email** your email. **Audience**: **External**. **Contact email** your
   email. Agree and click **Create** (or **Save and continue** on older screens).
6. **Data access** (older: **Scopes**): **Add or remove scopes**, paste
   `https://www.googleapis.com/auth/webmasters.readonly` in the manual box, **Add to table**, **Update**, **Save**.
7. **Audience** (older: **OAuth consent screen** summary): click **Publish app**, then **Confirm**, so the status is
   **In production**. Leave it in production: in Testing, Google ends the connection after 7 days.
   Google may say the app needs verification. You do not need it for your own account: when you connect, Google shows
   "Google hasn't verified this app"; click **Advanced**, then **Go to Ranksim**.

**C. Google Cloud: the OAuth client**

8. **Clients** (older: **Credentials**, then **Create credentials**, **OAuth client ID**): click **Create client**.
9. **Application type**: **Web application**. **Name**: `Ranksim`.
10. Under **Authorized redirect URIs** click **Add URI** and paste exactly
    `https://ranksim.vercel.app/api/v1/gsc/callback`. Leave **Authorized JavaScript origins** empty.
11. Click **Create**. Copy the **Client ID** and the **Client secret** from the dialog (or download the JSON). Keep
    the secret private.

**D. Vercel: the settings**

12. Open https://vercel.com, the **ranksim** project, **Settings**, **Environment Variables**.
13. Add each of these (Key, Value, environment **Production**, **Save**):
    - `GOOGLE_OAUTH_CLIENT_ID` = the Client ID
    - `GOOGLE_OAUTH_CLIENT_SECRET` = the Client secret (mark it **Sensitive** if offered)
    - `RANKSIM_PUBLIC_URL` = `https://ranksim.vercel.app`
    - `RANKSIM_SECRET` only if it is not set yet or is shorter than 32 characters: a long random value, for example
      the output of `openssl rand -base64 32`. Changing it signs you out and you connect Google again.
14. **Deployments**, the latest production deployment, the **...** menu, **Redeploy**. Settings apply to new
    deployments only.

**E. Ranksim: connect**

15. Open a client site in Ranksim, the gear (**Settings**), **Google Search Console**, turn it **on**.
16. Pick the Google account, accept the read only Search Console access (keep the box ticked), and you come back to
    Settings. The property is picked for you when your account has one for the site (`sc-domain:client.com` or
    `https://www.client.com/`); otherwise pick it from the list.
17. Every other client site turns on the same way, without signing in again. New sites you analyze turn on by
    themselves when your account has their property.

**If something goes wrong**

- "Not set up yet" in Settings: a Vercel variable is missing or the redeploy has not finished. The missing names are
  listed under **Setup steps**.
- Google says `redirect_uri_mismatch`: the URI in step 10 does not match exactly (https, no trailing slash).
- Google says "Access blocked" or the connection stops after a week: the app is still in Testing (step 7).
- "This Google account cannot read <property>": add your account as a user on that property in Search Console
  (Settings, Users and permissions), then **Refresh now**.
- To disconnect: Settings, **Disconnect Google** (stops Search Console for every site). You can also remove Ranksim
  at https://myaccount.google.com/permissions.

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
  The weight is on discovery's scale (a title phrase scores 3.0, 2.5 is the bar): `2.5 + 2.5 * impressions / top`,
  so every query sent qualifies (2.5 to 5.0) and the most seen rank first. Only queries with at least
  max(10, 1% of the top query's) impressions are sent. Refreshes stale data first
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

## Phase C additions

- **Signed out at the callback.** `/api/v1/gsc/callback` is `login_exempt`. It still checks the sealed state and the
  nonce cookie first (the session binding only when signed in). Signed out, it parks `{code, state}` sealed in the
  HttpOnly cookie `rs_gsc_pending` (path `/api/v1/gsc`, expires with the state) and redirects to
  `/app/<slug>/settings?gsc=signin`. After sign in, Settings calls `POST /api/v1/gsc/resume` (login and CSRF
  required), which rechecks the state and nonce and finishes. Single use.
- **First analysis.** `discovery_sources` with `project=None` (a new site) matches the site's host against the
  account's properties, loads the snapshot, returns its queries, and parks it under `_plugins/gsc/pending/` for an
  hour. `after_pull` then turns GSC on for a project that has no GSC settings yet when Google is connected (adopting
  the parked data, or matching the property now). A project the owner turned off stays off.
- **Searches column** is hidden until GSC has data: columns may define `when(report)`, honored by one line in
  `js/screens/searches.js` (shared file).
- **Opportunities** in `report["gsc"]["opportunities"]`: `near_page_one` (searches with at least max(20, 1% of total
  impressions) impressions at average position 8 to 20, with the page that gets most of their impressions) and
  `low_ctr` (pages on page one with that many impressions and under half a typical CTR for their position; the
  typical curve is a rough benchmark and labeled as not Google data). Shown on an Overview tile (order 16), a
  `gsc` screen (`/app/<slug>/gsc`, linked from the tile) with Open in editor and Track buttons, and a "Positions 8 to 20
  one" mark in the Searches column. Labels: "Positions 8 to 20" and "Seen but not clicked" (benchmark CTR labeled not
  Google data). Automatic matching (first analysis, new projects) never picks a parent domain property; a project
  with no match is rechecked once a day. Parked first-analysis data older than an hour is deleted.

- **Editor panel** (`registerEditorPanel`, key `gsc-page`, order 35): clicks, impressions, CTR, average position and
  the top 10 searches for the page being edited, from `GET /api/v1/gsc/projects/{slug}/page?url=` (stored snapshot,
  no network; pages matched without scheme, www, query or trailing slash). Labeled Google Search Console data with
  the date range. Hidden when GSC is not ready.

- **PKCE (S256).** `make_state` adds a random verifier to the sealed state; the authorization URL carries its
  S256 `code_challenge`, and the exchange sends `code_verifier`. The verifier never appears in a URL.
- **`RANKSIM_PUBLIC_URL`** (optional) pins the redirect URI's base, for example `https://ranksim.vercel.app`.
  `GOOGLE_OAUTH_REDIRECT_URI` still overrides the whole URI. With neither, the request origin is used.

## Search Console as a free rank source

Without a search data key, new sites have no Google positions. When GSC is on:
- `report.gsc.positions` holds `{q: {position, clicks, impressions, ctr, page}}` for every tracked search whose ranking
  row is not checked (no Google results), with `positions_label` "Search Console average position, <range>" and a
  note. Ranking rows are never changed; the two numbers are never mixed.
- UI: the Searches column shows "GSC avg #N" for those rows (title says it is not a Google top 10 position); an
  Overview tile (`gsc-positions`, order 14) lists them with their pages; the client report gets a "Google Search
  Console" section with totals and a "Search Console average position" table (FLAGGED shared files:
  `plugins/share.py`, one key plus a helper that calls `gsc.client_section`, and `static/share/share.js`, one block).
- `after_pull` reads and stores up to 15 of the site's own Search Console pages that the site read did not store
  (best page per tracked search, then most clicked), with the app's crawler, so Issues covers them.
- Audit check `gsc-title-search`: for those searches, when the best page's title does not use the search's words,
  a medium issue on the title, quoting the Search Console average position and date range.

## Known gaps

- One Google account per Ranksim. A client whose property lives under a different Google account must add James's
  account as a user in Search Console (the normal agency setup). Reconnecting with another account re-matches each
  site's property on that site's next connect or toggle; other sites show "cannot read <property>" until then.
- Only web search type, final data, 1000 rows per dimension set. Capped flags say when more rows exist.
