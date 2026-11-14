/* Ranksim Studio extension points. Every feature plugs in here instead of editing another module.

   A plugin is one file, static/js/plugins/<feature>.js. The shell loads every file there as a module (sorted by
   name) after app.js and before the first render, so register at the top level of the module. Import shared code
   with these exact relative paths, never with a query string (a module is keyed by its URL):
     import { registerOverviewTile, on } from '../hooks.js';
     import { h, S, api, icon } from '../core.js';

   Every registry is sorted by `order` (ascending; ties keep registration order). Every entry may carry a `key`:
   registering a key that already exists replaces that entry, so a plugin can replace a built-in one (built-in keys
   are listed below). A render or listener that throws is logged to the console and skipped, never fatal.
   A render returns a Node, an array of Nodes, or null for nothing.

   ---------------------------------------------------------------- screens

   registerScreen(key, { label, nav = true, order = 100, render(ctx) })
     Adds a screen at /app/<slug>/<key>. `label` names it in the top bar and the tab title. `nav: false` keeps it out
     of the top bar (Settings is reached by the gear button). render(ctx) either paints ctx.main itself (return
     nothing) or returns Nodes, which are shown followed by the footer. Use page(title, sub, actions, ...body) from
     core.js for the standard heading.
     ctx = { key, slug, report, project, params (URLSearchParams of the address), main (the <main> element) }
     Built-in keys and orders: overview 10, issues 20, searches 30, competitors 40, editor 50, settings 90 (nav false).
     Note: the server only serves the shell for the built-in screen names, so a new screen opens from the top bar or
     from go('<key>'); reloading its address needs the server's screen list to include the key.

   ---------------------------------------------------------------- overview

   registerOverviewTile({ key, order, render(report, ctx) })
     Adds a tile to the Overview bento. Return a <section class="tile ..."> (span-2, span-4, white, black...).
     ctx = { report, project, slug }
     Built-in keys and orders: search-data 0, first-page 10, average 20, backtest 30, issues 40, rankings 50,
     share 60, priority 70, competitors 80, pages 90, try 100.

   ---------------------------------------------------------------- editor

   registerEditorPanel({ key, order, render(ctx) })
     Adds a tile to the editor's side column. Panels repaint whenever the draft changes, except the panel that holds
     keyboard focus. Built-in keys and orders: open-progress 5 (while a new site is analyzed), test 10, snippet 20,
     fields 30, stats 40.

   registerEditorTool({ key, order, render(ctx) })
     Adds a control to the editor toolbar (right side). Built-in keys and orders: mode 50, device 60.

   The editor ctx (panels, tools, and the editor events below):
     url           the address of the page being edited (the draft's own URL once loaded)
     page          the address the editor opened (the key for this draft)
     search        the current search, or '' before the site's report exists
     report, project, slug
     mode          'visual' or 'html';  device: 'desktop' or 'mobile'
     draft         { text, url, base, loaded, touched, loading, failed }: base is the HTML as loaded
     facts         { title, h1, meta, words, headings } of the draft
     getHtml()     the current draft HTML (reads the live preview first)
     setHtml(html) replaces the draft (cleaned like any page), repaints, fires 'draft-changed'
     doc()         the live preview document in Visual mode, or null (HTML mode, or still loading)
     select(sel, { scroll = true })  outlines and selects the element matching the CSS selector in the preview and
                   scrolls it into view. Returns the element, or null (no match, HTML mode, not loaded).
                   select(null) clears the outline.
     scrollTo(sel) scrolls the element into view without selecting it. Returns the element or null.
     replaceText(sel, text)  sets the text of the element matching the selector in the draft (either mode), marks
                   the draft changed, fires 'draft-changed'. Returns true when an element matched.
     repaint()     repaints the side column.
     test()        runs Test changes for the current search.

   ---------------------------------------------------------------- settings and searches

   registerSettingsSection({ key, order, render(ctx) })
     Adds a section to Settings. Return a <section class="tile span-2"> (or span-4).
     ctx = { report, project, slug, reload() (reloads the report and project), rerender() }
     Built-in keys and orders: refresh 10, gsc 20, searches 30, competitors 40, delete 90.

   registerSearchColumn({ key, order, header, cell(row) })
     Adds a column to the Searches list. `header` is its title; cell(row) returns text or a Node for one ranking row
     ({ q, position, checked, url, top, predicted, ... } from the report). Columns appear after the position.

   ---------------------------------------------------------------- events

   on(name, fn) listens and returns a function that stops listening. call(name, ctx) runs every listener and returns
   their results. collect(name, ctx) does the same and flattens the results into one list, dropping nulls.
     'report-loaded'  { report, project, slug }  after a site's report and project load or reload.
     'editor-ready'   editor ctx  each time the editor shows a draft: in Visual mode once the preview document has
                      loaded (ctx.doc() is live), in HTML mode once the code view is shown (ctx.doc() is null).
     'draft-changed'  editor ctx plus { source: 'visual' | 'html' | 'title' | 'meta' | 'api' }  after the draft
                      HTML changes. Visual edits are debounced; HTML typing fires per change, so debounce heavy work.
     'test-finished'  { run, search, url, page, html, editor }  after Test changes returns. run is the API answer;
                      editor is the editor ctx, or null if the person already left the editor.
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

function registry(kind) {
  const entries = [];
  return {
    kind,
    add(spec) {
      const entry = { order: 100, ...spec, seq: seq += 1 };
      if (entry.key != null) {
        const at = entries.findIndex((item) => item.key === entry.key);
        if (at >= 0) entries.splice(at, 1);
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

export function registerScreen(key, { label, nav = true, order = 100, render }) {
  return SCREENS.add({ key, label: label || key, nav, order, render });
}

export function registerOverviewTile(spec) {
  return TILES.add(spec);
}

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

export const screens = () => SCREENS.list();
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
