/* The editor screen: the live page in an editable preview, a toolbar, and a side column of panels.
   Panels and toolbar controls come from registerEditorPanel and registerEditorTool (hooks.js documents the editor
   ctx they receive). Test changes lives in test.js and Publish in publish.js.
   editor.js and test.js import each other: keep top-level code in both free of calls into the other module. */

import {
  S, api, h, icon, clear, main, enc, debounce, hostOf, pathOf, looseKey, initial, go, navigate, page, footer,
  reduceMotion,
} from '../core.js';
import { call, editorPanels, editorTools, registerEditorPanel, registerEditorTool, registerScreen, renderAll, renderEntry } from '../hooks.js';
import { cleanDoc, facts, frameSource, normalize, pick, revealHidden, serialize } from './preview.js';
import { renderResult, runTest } from './test.js';

export { normalize };

/* ------------------------------------------------------------------ drafts and page data */

/* One draft per page address. Switching the search keeps your edits. */
export function draftFor(url) {
  if (!S.drafts.has(url)) S.drafts.set(url, { text: '', url, base: null, loaded: '', touched: false, loading: false, failed: '' });
  return S.drafts.get(url);
}

export async function getPage(url) {
  if (!S.pages.has(url)) {
    S.pages.set(url, api.get(`/api/v1/projects/${enc(S.slug)}/page?url=${enc(url)}`).catch((err) => {
      S.pages.delete(url);
      throw err;
    }));
  }
  return S.pages.get(url);
}

export async function getCompare(q) {
  if (!S.compare.has(q)) {
    S.compare.set(q, api.get(`/api/v1/projects/${enc(S.slug)}/compare?q=${enc(q)}`).catch((err) => {
      S.compare.delete(q);
      throw err;
    }));
  }
  return S.compare.get(q);
}

/* The editor's view state. refs holds the painted screen: { viewport, side, result, address, hint, page, q }. */
export const ED = { mode: 'visual', device: 'desktop', frame: null, code: null, refs: null, ready: null };

function tokens(text) {
  return new Set(String(text || '').toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').split(/\s+/).filter((w) => w.length > 2 && !['and', 'the', 'for', 'with', 'your', 'you'].includes(w)));
}

function bestSearchFor(text) {
  const R = S.report;
  const have = tokens(text);
  let best = null;
  let score = 0;
  for (const row of R.rankings) {
    const want = tokens(row.q);
    const overlap = want.size ? [...want].filter((w) => have.has(w)).length / want.size : 0;
    if (overlap > score) {
      best = row.q;
      score = overlap;
    }
  }
  return score >= 0.5 ? best : null;
}

export function currentPage() {
  if (!S.report) return S.open ? S.open.url : '';
  const R = S.report;
  const want = S.params.get('page');
  if (want) return want;
  const q = S.params.get('q');
  const row = R.rankings.find((item) => item.q === q) || R.rankings.find((item) => item.position) || null;
  if (row && row.url) return row.url;
  const first = R.pages.find((item) => item.status === 'fetched');
  return first ? first.url : `https://${R.site}/`;
}

export function currentQuery(draft) {
  const R = S.report;
  if (!R || !R.rankings.length) return '';
  const want = S.params.get('q');
  if (want && R.rankings.some((row) => row.q === want)) return want;
  const mine = R.rankings.find((row) => row.url && looseKey(row.url) === looseKey(draft.url));
  if (mine) return mine.q;
  const f = facts(draft.text);
  return bestSearchFor(`${f.title} ${f.h1}`) || (R.rankings.find((row) => row.position) || R.rankings[0]).q;
}

export async function loadPage(url) {
  const draft = draftFor(url);
  if (draft.loading) return;
  draft.loading = true;
  draft.failed = '';
  try {
    const stored = S.report && S.report.pages.some((item) => item.url === url && item.status === 'fetched');
    const got = stored ? await getPage(url) : await api.post('/api/v1/fetch', { url });
    draft.text = normalize(got.html);
    draft.base = draft.text;
    draft.url = got.url;
    draft.loaded = got.url;
    draft.touched = false;
  } catch (err) {
    draft.failed = err.message;
  } finally {
    draft.loading = false;
    if (ED.refs && ED.refs.page === url) {
      ED.refs.address.value = draft.url;
      paintFrame();
      paintSide();
    }
  }
}

/* ------------------------------------------------------------------ the editor ctx (see hooks.js) */

function liveDoc() {
  const frame = ED.frame;
  if (ED.mode !== 'visual' || !frame || ED.ready !== frame) return null;
  const doc = frame.contentDocument;
  return doc && doc.body ? doc : null;
}

function find(root, selector) {
  if (!root || !selector) return null;
  try {
    return root.querySelector(selector);
  } catch (_err) {
    return null;
  }
}

function changed(source) {
  call('draft-changed', editorCtx({ source }));
}

export function editorCtx(extra) {
  if (!ED.refs) return null;
  const pageUrl = ED.refs.page;
  const draft = draftFor(pageUrl);
  const here = () => !!ED.refs && ED.refs.page === pageUrl;
  let known = null;
  let knownText = null;
  /* Runs fn(doc) on the live preview, or on a parsed copy of the draft in HTML mode or while the preview loads, then
     saves the result as the draft. fn returning false means it changed nothing. */
  const edit = (fn) => {
    if (!here()) return false;
    const live = liveDoc();
    if (!live) syncFromFrame();
    const doc = live || cleanDoc(draft.text);
    if (fn(doc) === false) return false;
    draft.text = serialize(doc);
    if (!live) {
      if (ED.mode === 'html' && ED.code) ED.code.value = tidy(draft.text);
      else paintFrame();
    }
    draft.touched = true;
    paintSide();
    changed('api');
    return true;
  };
  const ctx = {
    page: pageUrl,
    report: S.report,
    project: S.project,
    slug: S.slug,
    draft,
    get url() {
      return draft.url || pageUrl;
    },
    get search() {
      return here() ? ED.refs.q || '' : '';
    },
    get mode() {
      return ED.mode;
    },
    get device() {
      return ED.device;
    },
    get facts() {
      if (!known || knownText !== draft.text) {
        known = facts(draft.text);
        knownText = draft.text;
      }
      return known;
    },
    getHtml() {
      if (here()) syncFromFrame();
      return draft.text;
    },
    setHtml(html) {
      if (!here()) return false;
      draft.text = normalize(String(html || ''));
      draft.touched = true;
      if (ED.mode === 'html' && ED.code) ED.code.value = tidy(draft.text);
      else paintFrame();
      paintSide();
      changed('api');
      return true;
    },
    doc: () => (here() ? liveDoc() : null),
    select(selector, { scroll = true } = {}) {
      const doc = here() ? liveDoc() : null;
      if (!doc) return null;
      if (selector == null) {
        pick(doc, null);
        return null;
      }
      const el = find(doc, selector);
      if (!el) return null;
      pick(doc, el);
      if (scroll) el.scrollIntoView({ block: 'center', behavior: reduceMotion.matches ? 'auto' : 'smooth' });
      const selection = doc.getSelection();
      if (selection) {
        const range = doc.createRange();
        range.selectNodeContents(el);
        selection.removeAllRanges();
        selection.addRange(range);
      }
      return el;
    },
    scrollTo(selector) {
      const el = find(here() ? liveDoc() : null, selector);
      if (el) el.scrollIntoView({ block: 'center', behavior: reduceMotion.matches ? 'auto' : 'smooth' });
      return el;
    },
    edit,
    replaceText(selector, text) {
      return edit((doc) => {
        const el = find(doc, selector);
        if (!el) return false;
        el.textContent = String(text);
        return true;
      });
    },
    repaint: () => paintSide(),
    test: () => runTest(),
  };
  return extra ? Object.assign(ctx, extra) : ctx;
}

function ready() {
  call('editor-ready', editorCtx());
}

/* ------------------------------------------------------------------ the screen */

export function renderEditor() {
  const R = S.report;
  const pageUrl = currentPage();
  const draft = draftFor(pageUrl);
  if (!draft.text && !draft.loading && !draft.failed && !S.open) loadPage(pageUrl);
  if (S.open && !draft.text && !draft.failed) draft.loading = true;

  const toolbar = h('div', { class: 'toolbar' });
  const left = h('div', { class: 'left' });
  if (R) {
    const options = [];
    const seen = new Set();
    const add = (url, label) => {
      if (seen.has(looseKey(url))) return;
      seen.add(looseKey(url));
      options.push(h('option', { value: url, selected: looseKey(url) === looseKey(pageUrl), text: label }));
    };
    if (!R.pages.some((item) => looseKey(item.url) === looseKey(pageUrl))) add(pageUrl, hostOf(pageUrl) === R.site.replace(/^www\./, '') ? pathOf(pageUrl) : `${hostOf(pageUrl)}${pathOf(pageUrl)}`);
    for (const item of R.pages.filter((p) => p.status === 'fetched')) add(item.url, item.path);
    options.push(h('option', { value: '__new', text: 'Start a new page' }));
    const pSelect = h('select', { 'aria-label': 'Page to edit' }, options);
    pSelect.addEventListener('change', () => {
      syncFromFrame();
      if (pSelect.value === '__new') {
        const url = `https://${R.site}/new-page/`;
        const fresh = draftFor(url);
        if (!fresh.text) Object.assign(fresh, { text: normalize('New page title\n\nWrite the page here. Click any line to change it.'), url, base: null, loaded: '', touched: true, failed: '' });
        go('editor', { page: url });
        return;
      }
      go('editor', { page: pSelect.value });
    });
    left.append(h('label', { class: 'select' }, pSelect, icon('chevron', 'chev')));
  } else {
    left.append(h('span', { class: 'chip chip-gray', text: S.open ? S.open.domain : '' }));
  }
  const right = h('div', { class: 'right' });
  toolbar.append(left, right);

  const address = h('input', { type: 'text', value: draft.url || pageUrl, 'aria-label': 'Page address', spellcheck: 'false', title: 'Press Return to open another page' });
  address.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    let value = address.value.trim();
    if (!value) return;
    if (!/^https?:\/\//i.test(value)) value = `https://${value}`;
    syncFromFrame();
    if (R && hostOf(value) === hostOf(`https://${R.site}`)) go('editor', { page: value });
    else navigate(`/edit?url=${enc(value)}`);
  });
  const viewport = h('div', { class: `viewport${ED.device === 'mobile' ? ' phone' : ''}` });
  const hint = h('span', { class: 'edit-hint', text: ED.mode === 'visual' ? 'Click any text to edit it' : 'Editing HTML' });
  const browser = h('section', { class: 'browser' },
    h('div', { class: 'chrome' }, h('span', { class: 'lights', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')), h('label', { class: 'address' }, icon('lock'), address), hint),
    viewport);
  const side = h('div', { class: 'side-col' });
  const result = h('div', { class: 'result', id: 'result', tabindex: '-1' });
  const title = R ? 'Editor' : (S.open ? S.open.domain : 'Editor');
  const sub = R ? 'Change the page, then test where it would land among the pages that rank today.' : 'Your page is ready to edit. Ranksim is reading the site and the pages that rank against it.';
  clear(main).append(page(title, sub, null, toolbar, h('div', { class: 'editor-grid' }, browser, side), result), footer());
  ED.refs = { viewport, side, result, address, hint, page: pageUrl, q: R ? currentQuery(draft) : '' };
  right.append(...renderAll(editorTools(), editorCtx()));
  paintFrame();
  paintSide();
  if (R && ED.refs.q) {
    const saved = S.results.get(`${ED.refs.q}|${looseKey(pageUrl)}`);
    if (saved) renderResult(saved, false);
    getCompare(ED.refs.q).catch(() => {});
  }
}

registerScreen('editor', { label: 'Editor', order: 50, render: () => renderEditor() });

/* Reads the live preview (or the HTML view) back into the draft. */
export function syncFromFrame() {
  if (!ED.refs) return;
  const draft = draftFor(ED.refs.page);
  if (ED.mode === 'visual' && ED.frame && ED.ready === ED.frame && ED.frame.contentDocument && ED.frame.contentDocument.body && draft.touched) {
    draft.text = serialize(ED.frame.contentDocument);
  } else if (ED.mode === 'html' && ED.code) {
    draft.text = ED.code.value;
  }
}

export function paintFrame() {
  if (!ED.refs) return;
  const { viewport, page: pageUrl } = ED.refs;
  const draft = draftFor(pageUrl);
  clear(viewport);
  ED.frame = null;
  ED.code = null;
  ED.ready = null;
  if (!draft.text) {
    const note = draft.failed
      ? [icon('warn'), h('strong', { text: 'This page could not be opened' }), h('span', { text: draft.failed }), h('button', { class: 'btn', type: 'button', onClick: () => { draft.failed = ''; loadPage(pageUrl); paintFrame(); } }, 'Try again')]
      : [h('span', { class: 'spinner', 'aria-hidden': 'true' }), h('strong', { text: 'Opening the page' }), h('span', { text: hostOf(pageUrl) })];
    viewport.append(h('div', { class: 'empty' }, note));
    return;
  }
  if (ED.mode === 'html') {
    const code = h('textarea', { class: 'code', spellcheck: 'false', 'aria-label': 'Page HTML', value: tidy(draft.text) });
    code.addEventListener('input', () => {
      draft.text = code.value;
      draft.touched = true;
      debouncedSide();
      changed('html');
    });
    viewport.append(code);
    ED.code = code;
    queueMicrotask(() => {
      if (ED.code === code) ready();
    });
    return;
  }
  const frame = h('iframe', { sandbox: 'allow-same-origin', title: 'Editable page', referrerpolicy: 'no-referrer' });
  frame.srcdoc = frameSource(draft.text, draft.url);
  frame.addEventListener('load', () => {
    const doc = frame.contentDocument;
    if (!doc || !doc.body) return;
    const revealed = revealHidden(doc);
    if (ED.refs && ED.refs.hint) {
      ED.refs.hint.textContent = revealed ? 'Showing what Google reads' : 'Click any text to edit it';
      ED.refs.hint.title = revealed ? 'The live site builds its look with scripts, which never run here. This is the HTML Google and Ranksim score. Click any text to edit it.' : '';
    }
    doc.designMode = 'on';
    doc.addEventListener('input', debounce(() => {
      draft.text = serialize(doc);
      draft.touched = true;
      paintSide();
      changed('visual');
    }, 250));
    doc.addEventListener('keydown', (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault();
        runTest();
      }
    });
    if (ED.frame === frame) {
      ED.ready = frame;
      ready();
    }
  });
  if (ED.device === 'mobile') viewport.append(h('div', { class: 'device' }, frame));
  else viewport.append(frame);
  ED.frame = frame;
}

export function tidy(html) {
  if (!html || /\n.*\n.*\n/.test(html.slice(0, 4000))) return html;
  const block = '(?:html|head|body|title|meta|link|style|header|footer|main|article|section|nav|aside|div|h[1-6]|p|ul|ol|li|table|thead|tbody|tr|figure|blockquote|form)';
  return html.replace(new RegExp(`>\\s*<(${block}\\b)`, 'gi'), '>\n<$1').replace(new RegExp(`(</${block}>)\\s*<`, 'gi'), '$1\n<').replace(/\n{2,}/g, '\n').trim();
}

const debouncedSide = debounce(() => paintSide(), 300);

function setHead(field, value) {
  const draft = draftFor(ED.refs.page);
  if (draft.touched) syncFromFrame();
  const live = ED.mode === 'visual' && ED.frame && ED.ready === ED.frame && ED.frame.contentDocument && ED.frame.contentDocument.head;
  const target = live ? ED.frame.contentDocument : cleanDoc(draft.text);
  if (field === 'title') {
    let el = target.querySelector('title');
    if (!el) {
      el = target.createElement('title');
      target.head.prepend(el);
    }
    el.textContent = value;
  } else {
    let el = target.querySelector('meta[name="description" i]');
    if (!el) {
      el = target.createElement('meta');
      el.setAttribute('name', 'description');
      target.head.append(el);
    }
    el.setAttribute('content', value);
  }
  draft.text = serialize(target);
  draft.touched = true;
  if (ED.mode === 'html' && ED.code) ED.code.value = tidy(draft.text);
  changed(field);
}

/* ------------------------------------------------------------------ side column */

const TYPING = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

function slotOf(entry, ctx) {
  return h('div', { class: 'panel-slot', dataset: { panel: entry.key || `panel-${entry.seq}` } }, renderEntry(entry, ctx));
}

/* Repaints every side panel. A panel that holds a focused field is left alone so typing is never interrupted, and it
   is the node the browser keeps still on screen while the panels around it change height (at phone width the page
   scrolls by that change instead of moving the field under the caret). */
export function paintSide() {
  if (!ED.refs) return;
  const { side } = ED.refs;
  const ctx = editorCtx();
  const active = document.activeElement;
  const keep = active && side.contains(active) && (TYPING.has(active.tagName) || active.isContentEditable) ? active.closest('.panel-slot') : null;
  const fresh = editorPanels().map((entry) => (keep && keep.dataset.panel === (entry.key || `panel-${entry.seq}`) ? keep : slotOf(entry, ctx)));
  for (const child of Array.from(side.children)) if (child !== keep) child.remove();
  let before = !!keep && fresh.includes(keep);
  for (const node of fresh) {
    if (node === keep) {
      before = false;
      continue;
    }
    if (before) side.insertBefore(node, keep);
    else side.append(node);
  }
}

export function snippetCard(f, draft) {
  let crumbs = draft.url;
  try {
    const u = new URL(draft.url);
    crumbs = `${u.hostname}${u.pathname.split('/').filter(Boolean).map((part) => ` › ${decodeURIComponent(part)}`).join('')}`;
  } catch (_err) {
    crumbs = draft.url;
  }
  const title = f.title || f.h1 || 'Untitled page';
  const desc = f.meta || 'No description. Google will pick a line from the page.';
  const name = S.report && hostOf(`https://${S.report.site}`) === hostOf(draft.url) ? S.report.name : hostOf(draft.url);
  return h('section', { class: 'snippet' },
    h('div', { class: 's-site' }, h('span', { class: 'fav', text: initial(hostOf(draft.url)) }), h('div', null, h('div', { class: 's-name', text: name }), h('div', { class: 's-url', text: crumbs }))),
    h('div', { class: 's-title', text: title.length > 62 ? `${title.slice(0, 59).replace(/\s+\S*$/, '')} ...` : title }),
    h('div', { class: 's-desc', text: desc.length > 160 ? `${desc.slice(0, 157).replace(/\s+\S*$/, '')} ...` : desc }));
}

function fieldsCard(f, draft) {
  const titleCount = h('span', { class: `counter${f.title.length > 60 ? ' over' : ''}`, text: `${f.title.length}/60` });
  const titleField = h('input', { class: 'field', value: f.title, 'aria-label': 'Title', dataset: { field: 'title' }, disabled: !draft.text });
  titleField.addEventListener('input', () => {
    setHead('title', titleField.value);
    titleCount.textContent = `${titleField.value.length}/60`;
    titleCount.classList.toggle('over', titleField.value.length > 60);
    debouncedSide();
  });
  const metaCount = h('span', { class: `counter${f.meta.length > 160 ? ' over' : ''}`, text: `${f.meta.length}/160` });
  const metaField = h('textarea', { class: 'field', rows: '3', value: f.meta, 'aria-label': 'Meta description', dataset: { field: 'meta' }, disabled: !draft.text });
  metaField.addEventListener('input', () => {
    setHead('meta', metaField.value);
    metaCount.textContent = `${metaField.value.length}/160`;
    metaCount.classList.toggle('over', metaField.value.length > 160);
    debouncedSide();
  });
  return h('section', { class: 'tile' },
    h('div', { class: 'form' },
      h('div', { class: 'form-row' }, h('label', { class: 'row-label' }, h('span', { text: 'Title' }), titleCount), titleField),
      h('div', { class: 'form-row' }, h('label', { class: 'row-label' }, h('span', { text: 'Description' }), metaCount), metaField)));
}

export function statsCard(f) {
  return h('section', { class: 'tile stats-card' },
    h('div', { class: 'stat-line' }, h('span', { class: 'k', text: 'Words' }), h('span', { class: 'v', text: f.words.toLocaleString() })),
    h('div', { class: 'stat-line' }, h('span', { class: 'k', text: 'Sections' }), h('span', { class: 'v', text: String(f.headings) })),
    h('div', { class: 'stat-line' }, h('span', { class: 'k', text: 'Main heading' }), h('span', { class: 'v one-line', text: f.h1 || 'missing' })));
}

registerEditorPanel({ key: 'snippet', order: 20, render: (ctx) => snippetCard(ctx.facts, ctx.draft) });
registerEditorPanel({ key: 'fields', order: 30, render: (ctx) => fieldsCard(ctx.facts, ctx.draft) });
registerEditorPanel({ key: 'stats', order: 40, render: (ctx) => statsCard(ctx.facts) });

/* ------------------------------------------------------------------ toolbar */

registerEditorTool({
  key: 'mode',
  order: 50,
  render() {
    const modeSeg = h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': 'Edit mode' });
    for (const [key, label, ic] of [['visual', 'Visual', 'eye'], ['html', 'HTML', 'code']]) {
      modeSeg.append(h('button', { type: 'button', 'aria-pressed': String(ED.mode === key), onClick: () => { syncFromFrame(); ED.mode = key; renderEditor(); } }, icon(ic), label));
    }
    return modeSeg;
  },
});

registerEditorTool({
  key: 'device',
  order: 60,
  render() {
    const deviceSeg = h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': 'Device' });
    for (const [key, label] of [['desktop', 'Desktop'], ['mobile', 'Phone']]) {
      deviceSeg.append(h('button', { type: 'button', title: label, 'aria-label': label, 'aria-pressed': String(ED.device === key), onClick: () => { syncFromFrame(); ED.device = key; renderEditor(); } }, icon(key)));
    }
    return deviceSeg;
  },
});
