/* Ranksim Studio extension points. Every feature plugs in here instead of editing another module.

   A plugin is one file, static/js/plugins/<feature>.js. The shell loads every file there as a module (sorted by
   name) after app.js and before the first render, so register at the top level of the module. Import shared code
   with these exact relative paths, never with a query string (a module is keyed by its URL):
     import { registerOverviewTile, on } from '../hooks.js';
     import { h, S, api, icon } from '../core.js';

   Every registry is sorted by `order` (ascending; ties keep registration order). Every entry may carry a `key`:
   registering a key that already exists replaces that entry, so a plugin can replace a built-in one (built-in keys
   are listed below). Replacing a built-in by accident is easy (a 'share' tile once hid Who takes the top 10), so
   after app.js has registered the built-ins it calls sealBuiltIns(), and from then on a key that matches a built-in
   logs a console warning unless the built-in was registered with replaceable: true (the gsc Settings placeholder)
   or the new spec says replace: true to show the replacement is meant. A render or listener that throws is logged to the console and skipped, never fatal.
   A render returns a Node, an array of Nodes, or null for nothing.

   ---------------------------------------------------------------- screens

   registerScreen(key, { label, nav = true, order = 100, render(ctx) })
     Adds a screen at /app/<slug>/<key>. `label` names it in the top bar and the tab title. `nav: false` keeps it out
     of the top bar (Settings is reached by the gear button). render(ctx) either paints ctx.main itself (return
     nothing) or returns Nodes, which are shown followed by the footer. Use page(title, sub, actions, ...body) from
     core.js for the standard heading.
     ctx = { key, slug, report, project, params (URLSearchParams of the address), main (the <main> element) }
     Built-in keys and orders: overview 10, issues 20, searches 30, competitors 40, editor 50, settings 90 (nav false).
     A key is 1 to 40 lowercase letters, digits, - and _, starting with a letter or digit. The server answers
     /app/<slug>/<key> with the app for any key of that shape, so a screen's address also works on a reload, in a new
     tab and from a bookmark. An address whose key no screen registered opens the site's Overview and the address bar
     is corrected to /app/<slug>.

   ---------------------------------------------------------------- overview

   registerOverviewTile({ key, order, render(report, ctx) })   (registerTile is the same function)
     Adds a tile to the Overview bento. Return a <section class="tile ..."> (span-2, span-4, white, black...).
     ctx = { report, project, slug }
     Built-in keys and orders: search-data 0, first-page 10, average 20, backtest 30, issues 40, opportunities 45,
     rankings 50, share 60, predicted 65 (only when PREDICTED and real positions both exist), priority 70,
     competitors 80, pages 90, try 100. Order 55 is left for rank history. The bento packs densely, so a one-column
     tile fills the first gap that fits. Charts: import { fit, sparkline, ... } from '../charts.js'.

   ---------------------------------------------------------------- editor

   registerEditorPanel({ key, order, render(ctx) })
     Adds a tile to the editor's side column. Panels repaint whenever the draft changes, except the panel that holds
     keyboard focus: it stays as it is, and stays still on screen while the panels around it change height. Built-in
     keys and orders: open-progress 5 (while a new site is analyzed), test 10, editor-changes 15, snippet 20,
     editor-outline 25, fields 30, stats 40, editor-versions 50.

   registerEditorTool({ key, order, render(ctx) })
     Adds a control to the editor toolbar (right side). Built-in keys and orders: editor-history 20 (undo, redo), editor-block 30
     (Text, H2, H3), editor-insert 40, mode 50, device 60.

   The editor ctx (panels, tools, and the editor events below):
     url           the address of the page being edited (the draft's own URL once loaded)
     page          the address the editor opened (the key for this draft)
     search        the current search, or '' before the site's report exists
     report, project, slug
     mode          'visual' or 'html';  device: 'desktop' or 'mobile'
     draft         { text, url, base, loaded, touched, loading, failed }: base is the HTML as loaded, touched turns
                   true at the first edit
     facts         { title, h1, meta, words, headings } of draft.text, worked out again whenever draft.text changes
     getHtml()     the draft HTML. Once the draft is touched it first reads back the live preview (Visual mode) or the
                   code view (HTML mode). Before the first edit it is the page as loaded.
     setHtml(html) replaces the draft (cleaned like any page), repaints, fires 'draft-changed'
     edit(fn)      changes the page: calls fn(doc) with the live preview document, or with a parsed copy of the draft
                   in HTML mode or while the preview loads, then saves the result as the draft, marks it touched,
                   repaints the side column and fires 'draft-changed'. fn returns false to change nothing. Returns
                   true when the draft changed.
     doc()         the live preview document in Visual mode, or null (HTML mode, or still loading). Use it to read the
                   page, find elements and draw on top of them, never to change content: the draft does not see a
                   change made here until the person edits the page, and then saves it with their edit. Change content
                   with edit(fn), replaceText() or setHtml().
                   Everything a plugin adds to doc() must be marked so it is never saved, tested or published: put the
                   data-rs attribute on every element it inserts (a pin, a style, an overlay) and name every attribute
                   it sets on the page's own elements data-rs-<feature>. Serializing the draft drops [data-rs] elements
                   and every data-rs-* attribute.
     select(sel, { scroll = true })  outlines and selects the element matching the CSS selector in the preview and
                   scrolls it into view. Returns the element, or null (no match, HTML mode, not loaded).
                   select(null) clears the outline.
     scrollTo(sel) scrolls the element into view without selecting it. Returns the element or null.
     replaceText(sel, text)  sets the text of the element matching the selector in the draft (either mode), marks
                   the draft changed, fires 'draft-changed'. Returns true when an element matched.
     repaint()     repaints the side column.
     test()        runs Test changes for the current search.
     exportHtml()  the full page to publish or download: the original HTML as fetched (scripts, styles, comments
                   intact) with the draft's edits applied, and no data-rs attributes. Untouched parts are byte for byte
                   the original. Before the page has loaded it is draft.text.
     diff()        what changed against the page as loaded, block by block in plain words:
                   { changes: [{ type: 'changed' | 'added' | 'removed' | 'moved', kind, label, before, after,
                   words? }], counts: { changed, added, removed, moved }, total, summary }. summary is one sentence such
                   as "3 parts of the page changed: 1 edited
                   and 2 added." or "No changes yet.". label reads like "Main heading
                   changed"; words is a word diff [{ op: '=' | '+' | '-', text }] for changed blocks.
     undo(), redo()  step through the draft's history (visual, HTML, inserted blocks, title and description share one
                   history). Return true when something changed. canUndo, canRedo: booleans.
     The preview marks every element with data-rs-id (the round trip uses it). serialize-time rules above still
     hold: getHtml() and draft.text never contain data-rs attributes.

   ---------------------------------------------------------------- settings and searches

   registerSettingsSection({ key, order, render(ctx) })
     Adds a section to Settings. Return a <section class="tile span-2"> (or span-4).
     ctx = { report, project, slug, reload() (reloads the report and project), rerender() }
     Built-in keys and orders: refresh 10, gsc 20, searches 30, competitors 40, plugins 80, delete 90.
     The plugins section shows what GET /api/v1/plugins returns: every server plugin whose settings_schema() gave a
     dict, and every plugin that failed to load. It is hidden when there is nothing to show. A schema is shown read
     only as { title, description, fields: [{ key, label, value, help }] } (or JSON Schema `properties`, each with
     title, description and value or default). A plugin that registers its own settings section with the plugin's
     name as the key (for example 'gsc') is left out of this list, so settings that people change belong in such a
     section with the plugin's own routes.

   registerSearchColumn({ key, order, header, cell(row) })
     Adds a column to the Searches list. `header` is its title; cell(row) returns text or a Node for one ranking row
     ({ q, position, checked, url, top, predicted, ... } from the report). Columns appear after the position.
     Optional when(report): the column shows only when it returns true. A false or throwing when() hides the column
     (use it for data that may not exist yet, for example Search Console before it is connected).

   registerScreenNotice({ key, order, screen, render(ctx) })
     Adds a short notice at the top of a screen, under its heading (for example "2 searches moved up since your last
     visit" on Searches). `screen` is a screen key ('searches', 'overview', a plugin screen...), or '*' for every
     screen. render(ctx) returns a Node (use <div class="callout info">), an array, or null for nothing.
     ctx = { screen, report, project, slug }. Notices show on every screen built with page() from core.js.

   ---------------------------------------------------------------- events

   on(name, fn) listens and returns a function that stops listening. call(name, ctx) runs every listener and returns
   their results. collect(name, ctx) does the same and flattens the results into one list, dropping nulls.
     'report-loaded'  { report, project, slug }  after a site's report and project load or reload.
     'editor-ready'   editor ctx  each time the editor shows a draft: in Visual mode once the preview document has
                      loaded (ctx.doc() is live), in HTML mode once the code view is shown (ctx.doc() is null).
     'draft-changed'  editor ctx plus { source: 'visual' | 'html' | 'title' | 'meta' | 'api' | 'undo' | 'redo' |
                      'restore' }  after the draft HTML changes ('restore': edits brought back from this browser's
                      autosave). Visual edits are debounced; HTML typing is debounced too.
     'editor-caret'   editor ctx  when the cursor moves in the preview (the block toolbar follows it).
     'test-finished'  { run, search, url, page, html, editor }  after Test changes returns. run is the API answer;
                      editor is the editor ctx, or null if the person already left the editor.
     'publish-fixes'  collect('publish-fixes', editorCtx) -> [{ id, title }], the issues fixed in this draft,
                      answered by the audit plugin and read by the Publish sheet.

   ---------------------------------------------------------------- frozen module exports

   Core modules owned by different build sessions import these names from each other. They are part of this
   contract: keep the names and signatures, and change one only together with every module that imports it. app.js
   imports the modules statically, so a missing export stops the whole app from loading. Plugins use the hooks above.
     editor/editor.js   ED  the editor's view state { mode, device, frame, code, refs, ready }
                        draftFor(url) -> draft        normalize(html) -> html      paintFrame()      paintSide()
                        renderEditor()                syncFromFrame()              editorCtx(extra?) -> ctx | null
                        getCompare(q) -> Promise of the compare answer for a search
     editor/preview.js  facts(html) -> { title, h1, meta, words, headings }
     editor/test.js     runTest() -> Promise          renderResult(run, animate)
     editor/publish.js  publishButton(draft) -> Node
     screens/issues.js  issueRow(issue, compact) -> Node
     Importers: app.js (draftFor, normalize, paintFrame, paintSide, renderEditor, syncFromFrame, publishButton), screens/overview.js
     (issueRow), editor/test.js (ED, draftFor, editorCtx, getCompare, renderEditor, syncFromFrame, facts,
     publishButton), editor/editor.js (renderResult, runTest), editor/publish.js (ED, draftFor, syncFromFrame, editorCtx).
     tests/test_studio_modules.py checks this list against the imports.
*/

const listeners = new Map();

function report(where, err) {
  console.error(`Ranksim: ${where} failed`, err);
}

export function on(name, fn) {
  if (!listeners.has(name)) listeners.set(name, []);
  listeners.get(name).push(fn);
  return () => {
    const list = listeners.get(name) || [];
    const at = list.indexOf(fn);
    if (at >= 0) list.splice(at, 1);
  };
}

export function call(name, ctx) {
  const out = [];
  for (const fn of (listeners.get(name) || []).slice()) {
    try {
      out.push(fn(ctx));
    } catch (err) {
      report(`'${name}' listener`, err);
    }
  }
  return out;
}

export function collect(name, ctx) {
  return flat(call(name, ctx));
}

function flat(items) {
  return items.flat(Infinity).filter((item) => item != null && item !== false);
}

/* ------------------------------------------------------------------ registries */

let seq = 0;
let sealed = false;

/* Marks every entry registered so far as built in. app.js calls it once, after its own imports and registrations
   and before any plugin module runs. Returns the number of entries marked. */
export function sealBuiltIns() {
  sealed = true;
  let count = 0;
  for (const reg of REGISTRIES) {
    for (const entry of reg.list()) {
      entry.builtIn = true;
      count += 1;
    }
  }
  return count;
}

function registry(kind) {
  const entries = [];
  return {
    kind,
    add(spec) {
      const entry = { order: 100, ...spec, seq: seq += 1 };
      if (entry.key != null) {
        const at = entries.findIndex((item) => item.key === entry.key);
        if (at >= 0) {
          const old = entries[at];
          if (sealed && old.builtIn && !old.replaceable && !entry.replace) {
            console.warn(`Ranksim: a plugin registered the ${kind} key '${entry.key}', which replaces the built-in ${kind} with that key. Pick a new key, or pass replace: true if replacing it is intended.`);
          }
          if (old.builtIn) entry.replaced = true;
          entries.splice(at, 1);
        }
      }
      entries.push(entry);
      return entry;
    },
    list() {
      return entries.slice().sort((a, b) => (a.order - b.order) || (a.seq - b.seq));
    },
    get(key) {
      return entries.find((item) => item.key === key) || null;
    },
  };
}

const SCREENS = registry('screen');
const TILES = registry('overview tile');
const PANELS = registry('editor panel');
const TOOLS = registry('editor tool');
const SECTIONS = registry('settings section');
const COLUMNS = registry('search column');
const NOTICES = registry('screen notice');
const REGISTRIES = [SCREENS, TILES, PANELS, TOOLS, SECTIONS, COLUMNS, NOTICES];

export function registerScreen(key, { label, nav = true, order = 100, render }) {
  if (!/^[a-z0-9][a-z0-9_-]{0,39}$/.test(String(key))) {
    console.error(`Ranksim: screen key '${key}' should be 1 to 40 lowercase letters, digits, - and _. Its address will not open on a reload.`);
  }
  return SCREENS.add({ key, label: label || key, nav, order, render });
}

export function registerOverviewTile(spec) {
  return TILES.add(spec);
}

/* The short name the build plan uses for registerOverviewTile. */
export const registerTile = registerOverviewTile;

export function registerEditorPanel(spec) {
  return PANELS.add(spec);
}

export function registerEditorTool(spec) {
  return TOOLS.add(spec);
}

export function registerSettingsSection(spec) {
  return SECTIONS.add(spec);
}

export function registerSearchColumn(spec) {
  return COLUMNS.add(spec);
}

export function registerScreenNotice(spec) {
  return NOTICES.add({ screen: '*', ...spec });
}

export const screens = () => SCREENS.list();
/* The notices for one screen key, in order: those registered for that screen and those for every screen ('*'). */
export const screenNotices = (key) => NOTICES.list().filter((entry) => entry.screen === '*' || entry.screen === key);
export const screen = (key) => SCREENS.get(key);
export const overviewTiles = () => TILES.list();
export const editorPanels = () => PANELS.list();
export const editorTools = () => TOOLS.list();
export const settingsSections = () => SECTIONS.list();
export const searchColumns = () => COLUMNS.list();

/* Runs one entry's render, logging and skipping it when it throws. Returns a flat list of Nodes. */
export function renderEntry(entry, ...args) {
  try {
    const out = (entry.render || entry.cell).apply(entry, args);
    return flat([out]);
  } catch (err) {
    report(`${entry.key || 'unnamed'} render`, err);
    return [];
  }
}

/* Every entry of a registry list rendered in order, as one flat list of Nodes. */
export function renderAll(entries, ...args) {
  return entries.flatMap((entry) => renderEntry(entry, ...args));
}
