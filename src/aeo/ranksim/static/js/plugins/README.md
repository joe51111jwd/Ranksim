# Studio plugins (JavaScript)

Every `*.js` file in this folder is loaded automatically by the Studio shell as a `<script type="module">`, sorted by
file name, after `/studio/js/app.js` and before the first screen renders. Adding a file is enough: no edit to
`studio.html`, `studio.py` or any other module.

## Contract

- One feature per file, named after the feature: `gsc.js`, `history.js`, `share.js`.
- File names use letters, digits, `.`, `_` and `-` only, and must not start with a dot. Other names are not served.
- Register at the top level of the module (synchronously, no top-level `await`), so the hooks exist before the
  first render.
- Import shared code with these exact relative paths and never add a query string. A module is keyed by its URL,
  so `../core.js?v=2` would load a second copy of the app state.

```js
import { on, registerOverviewTile, registerEditorPanel } from '../hooks.js';
import { S, h, api, icon, enc } from '../core.js';

registerOverviewTile({
  key: 'history',
  order: 55,
  render(report) {
    return h('section', { class: 'tile span-2' }, h('div', { class: 'tile-k', text: 'Rank history' }));
  },
});

on('report-loaded', ({ slug }) => { /* fetch your own data for this site */ });
```

`../hooks.js` documents every hook, its `ctx` object, and the keys of the built-in entries (a plugin can replace a
built-in entry by registering the same `key`). `../core.js` exports the DOM helper `h`, `icon`, the API client
(`api.get`, `api.post`, `api.request`, `api.stream`), the state object `S`, the router (`go`, `link`, `navigate`),
`toast`, `openSheet`, `page`, `footer` and the other shared helpers.

## Rules

- Build DOM with `h()` and `textContent`. Never put outside HTML into the app page with `innerHTML`.
- No inline scripts and no scripts from other hosts: the page policy is `script-src 'self'`.
- Every write to the API sends the `x-ranksim-csrf` header. `api.post`, `api.request` (any method but GET and HEAD,
  with or without a body) and `api.stream` do it for you.
- A screen key is 1 to 40 lowercase letters, digits, `-` and `_`, as in `registerScreen('history', {...})`. The
  server answers `/app/<slug>/<key>` with the app for any key of that shape, so your screen's address also opens on a
  reload, in a new tab or from a bookmark.
- The editor preview (`ctx.doc()`) is for reading the page and drawing on top of it. Put `data-rs` on every element
  you insert there and name every attribute you set on the page's own elements `data-rs-<feature>`; the editor drops
  both when it saves, tests or downloads the page. Change the page itself only with `ctx.edit(fn)`,
  `ctx.replaceText()` or `ctx.setHtml()`.
- Server plugin settings: a dict from `settings_schema()` in `plugins/<feature>.py` is listed read only in Settings
  (see `registerSettingsSection` in `../hooks.js`). For settings people change, register a settings section with your
  feature name as its key and save through your own routes.
- UI copy: short plain sentences, no em dashes or en dashes. Label predictions PREDICTED.
- A hook that throws is logged and skipped, so one broken plugin never takes the app down. Keep it that way: do not
  patch core modules at run time.
- Styles go in `../../css/plugins/<feature>.css` (loaded automatically too).
