# Publish workstream plan (session 7, branch `claude/rs-publish`)

James's spec: anyone can edit any page; you only need to own a site to push a change live.

## Pieces

| Piece | File | State |
| --- | --- | --- |
| Page parts (title, description, h1, body region) and field diff | `src/aeo/ranksim/publishing/htmlparts.py` | Phase A |
| Ownership proof: meta tag, file, DNS TXT over DNS-over-HTTPS | `publishing/ownership.py` | Phase A |
| Per-project state, sealed credentials, history | `publishing/store.py` | Phase A |
| Connectors: WordPress, Webflow, Shopify, GitHub, signed webhook | `publishing/connectors/*.py` | Phase A |
| Flow: plan, publish, live check, rollback | `publishing/service.py` | Phase A |
| Routes and the `after_pull` re-check hook | `plugins/publish.py` | Phase A (registered by the P0 loader) |
| Tests, all HTTP mocked | `tests/test_publish.py` | Phase A |
| Publish sheet | `static/js/editor/publish.js`, `static/css/plugins/publish.css` | Phase B, after P0 |

`publishing/` is a new package owned by this workstream (the plugin loader imports every module in `plugins/`, so
the connector code lives outside it and `plugins/publish.py` stays a thin wiring module).

## Ownership

- One record per domain (www stripped) per project: random token (`rs-` + 24 url-safe chars), `verified`, `method`,
  `scope`, `verified_at`, `checked_at`, `last_check` with every attempt.
- Meta: `<meta name="ranksim-verification" content="TOKEN">` on `https://domain/` (then `https://www.domain/`).
- File: `https://domain/.well-known/ranksim-verification.txt` holding the token (or `ranksim-verification=TOKEN`).
- DNS: TXT `ranksim-verification=TOKEN` on the domain, via Cloudflare DoH then Google DoH (`application/dns-json`).
  Split TXT strings are joined. NXDOMAIN is a real "not found". A DNS proof covers subdomains; meta and file cover
  only that host.
- Proof fetches use the same robots-respecting, private-address-refusing Crawler as `ui.preview_fetch`, but keep the final
  URL: a proof served after a redirect to another host does not count (apex to www does). The meta tag counts only
  inside `<head>`.
- Re-check: before a publish or rollback when the proof is older than 24 hours, and after every pull (`after_pull`).
  A proof that is gone revokes ownership. A check that could not run (resolver down, fetch blocked) keeps a success
  younger than 7 days, never longer.

## Connections and secrets

- Credentials are sealed with `aeo.ranksim.secretbox.seal(plaintext, purpose=...)`; the purpose is
  `ranksim.publish:<project slug>:<connection id>`, so a sealed value copied to another project or connection does not
  open. Until secretbox lands, saving a connection answers 503 "encryption is not set up"; tests use a fake module.
- A connection is tested against the platform before it is saved. No route returns `sealed` or any secret field.
- User-supplied hosts are resolved once, every address must be public, and the request dials that address with the
  original Host header and TLS name (no DNS rebinding between check and connect). WordPress must use https so the
  application password is never sent in the clear.
- Platform hosts are fixed (api.webflow.com, api.github.com, `*.myshopify.com`). User-supplied hosts (WordPress site,
  webhook URL) must be public addresses; redirects are not followed.

## What each platform changes

The editor sends the whole edited page. The server fetches the live page, diffs four fields (title tag, meta
description, main heading, body content region) and each connector says per field whether and how it applies.

- WordPress (REST, application password): h1 to post title; title and description to Rank Math (`rankmath/v1/updateMeta`)
  or Yoast meta when the site exposes it; otherwise title via "post title + site suffix", description to excerpt; body to
  post content from `.entry-content` and similar, only when that region's text matches the post's rendered content.
  Blocks and shortcodes need an explicit confirm because the body is saved as plain HTML. A title change made through the
  post title also changes the heading on most themes, so it needs a confirm. Rank Math does not expose stored values
  over REST by default, so rollback of Rank Math fields clears them (its template applies again) unless the site exposes them.
- Webflow (Data API v2): static page SEO title and description (`PUT /pages/{id}`). Static page text cannot change through
  the API; the UI says so. Page changes stay drafts unless the owner ticks "Publish the site", which also publishes
  anything else staged. CMS items: name, SEO title and description fields when present, rich text body; the item is
  published right after.
- Shopify (Admin GraphQL `2025-10`): pages and blog articles: title, body, SEO title and description metafields.
  Products and collections are not covered yet.
- GitHub: commit on a new `ranksim/...` branch and open a pull request. When the repo file is the served page the whole
  edited page is committed; when it is a template only title, description and h1 are patched where the old value
  appears exactly once. Path mapping setting: `/url=path/file.html` lines, plus a `root` folder.
- Webhook: POST JSON with `X-Ranksim-Signature: t=<unix>,v1=hex(HMAC-SHA256(secret, "<t>." + body))`. 2xx accepted.

Every connector refuses to publish a body that lost scripts, iframes or forms without an explicit confirm.

## Publish flow

1. `POST .../publish/plan` returns ownership, connections, the field diff, and per-connection rows and warnings.
2. `POST .../publish` requires verified ownership covering the URL and a connection for that domain, re-fetches the live
   page as "previous", applies, stores history, then fetches the live page with a cache-busting query up to three times
   and records `confirmed`, `partial`, `pending` (caches) or `not_live_yet` (pull request open, Webflow draft).
3. `POST .../publish/history/{id}/verify` re-checks later (and for GitHub, checks the PR merged first).
4. `POST .../publish/history/{id}/rollback` writes the previous values back (GitHub: close PR and delete branch, or a revert
   PR once merged; webhook: sends `ranksim.rollback` with the previous page).

History entry: who, when, URL, connection, ownership method, rows (before and after per field), the platform result, the
previous values, and the live check. The browser gets it without the stored previous content.

Robustness: history is written before the platform call (a half-applied publish still has its previous values and can be
rolled back); a rollback is refused while a newer publish of the same page is live; a rollback can use a reconnected
connection of the same platform and site; unexpected platform answers become clean errors.

Known gap outside this workstream: the shared Crawler (`aeo/crawl/fetch.py`) resolves names twice like the old connector
code did, so it has the same DNS rebinding window. Infra owns that file.

## Routes

`GET /api/v1/publish/platforms`, and under `/api/v1/projects/{slug}/publish`: `status`, `ownership`,
`ownership/verify`, `ownership/reset`, `connections` (GET, POST, DELETE `/{id}`), `plan`, the publish POST itself,
`history`, `history/{id}/verify`, `history/{id}/rollback`. Every write checks `x-ranksim-csrf`. The public demo is refused.

## Phase B (done)

- Wired to P0: routes use `aeo.ranksim.api.common`; state lives in `plugins.data_dir(project, "publish")`. Secretbox is
  imported lazily: when it is missing or `RANKSIM_SECRET` is unset, the plugin still loads, saving a connection answers
  503, and `status` reports `encryption_ready: false` so the sheet says so.
- Publish sheet (`js/editor/publish.js`, `css/plugins/publish.css`): What changes (the server diff, with the editor's
  `ctx.diff()` summary when the editor offers it, and per field whether it will publish and why not), Ownership (three
  methods with copy buttons and Check, last check result), Where it is hosted (platform forms with their honest limits,
  saved connections, confirm boxes for warnings, the Webflow "publish the site" option, Publish), History (live check
  status, Check again, pull request link, Roll back with a second click to confirm), and Take it with you (Download and
  Copy, always). It publishes `ctx.exportHtml()` when the editor offers it (the original page with edits, scripts
  intact), else the draft HTML.
- Verified in Chromium at 1280 and 390 wide against a local server with a mocked web and WordPress site (no network):
  prove ownership, connect, publish, live check confirmed, roll back; no horizontal scroll; the password never appears
  in the page.

## Owner actions

None for the code to run. To publish for real: the GSC session's secretbox key must be set on the server, then each
client site needs its proof added and a platform credential (WordPress application password, Webflow site token,
Shopify custom app token with `write_online_store_pages` and `write_content`, GitHub fine-grained token with contents and
pull request write, or a webhook secret).
