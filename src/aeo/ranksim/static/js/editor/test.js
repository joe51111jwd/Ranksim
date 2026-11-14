/* Test changes: score the draft against the pages that rank for the search and show where it is PREDICTED to land.
   A prediction among stored pages, never a Google rank. */

import { S, api, h, icon, clear, enc, $, plural, pct, hostOf, pathOf, initial, looseKey, pathFor, toast, reduceMotion } from '../core.js';
import { call, registerEditorPanel } from '../hooks.js';
import { facts } from './preview.js';
import { ED, draftFor, editorCtx, getCompare, renderEditor, syncFromFrame } from './editor.js';
import { publishButton } from './publish.js';

let testing = false;

/* The side panel that runs a test: pick the search, see where the page stands, Test changes, Publish. */
function testTile(ctx) {
  const R = S.report;
  if (!R) return null;
  const draft = ctx.draft;
  const q = ctx.search;
  const tile = h('section', { class: 'tile' });
  if (!R.rankings.length || !S.project || !S.project.engine || !S.project.engine.ready) {
    tile.append(h('div', { class: 'tile-k', text: 'Testing' }),
      h('p', { class: 'tile-sub', text: R.google_ready ? 'Rankings for this site are still being pulled. Refresh it in Settings.' : `Predictions need Google results for ${R.site}. Search data isn't connected on the server yet, so you can edit and download, but not test.` }));
    tile.append(h('div', { class: 'result-actions' }, h('button', { class: 'btn btn-primary btn-large btn-block', type: 'button', disabled: true }, 'Test changes'), publishButton(draft)));
    return tile;
  }
  const qSelect = h('select', { 'aria-label': 'Search to test against' }, R.rankings.map((item) => h('option', { value: item.q, selected: item.q === q, text: item.q })));
  qSelect.addEventListener('change', () => {
    syncFromFrame();
    S.params = new URLSearchParams({ page: ED.refs.page, q: qSelect.value });
    history.replaceState({}, '', pathFor('editor', { page: ED.refs.page, q: qSelect.value }));
    renderEditor();
  });
  const row = R.rankings.find((item) => item.q === q) || {};
  const mine = row.url && looseKey(row.url) === looseKey(draft.url);
  const standing = !row.checked ? 'No Google results for this search yet.'
    : mine && row.position ? `This page is #${row.position} on Google${row.predicted && row.predicted.position ? `; the model puts it #${row.predicted.position} of ${row.predicted.of}` : ''}.`
      : row.position ? `${R.name} is #${row.position} with ${pathOf(row.url)}. This page is tested as a new entry.` : 'Not in the top 10 yet. This page is tested against the pages that are.';
  const testBtn = h('button', { class: 'btn btn-primary btn-large btn-block', type: 'button', id: 'test-btn', disabled: testing || !draft.text || draft.loading, onClick: runTest }, testing ? [h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Testing'] : 'Test changes');
  tile.append(h('div', { class: 'tile-k', text: 'Test against the search' }), h('label', { class: 'select select-block' }, qSelect, icon('chevron', 'chev')), h('p', { class: 'tile-sub', text: standing }),
    h('div', { class: 'result-actions' }, testBtn, publishButton(draft)));
  return tile;
}

registerEditorPanel({ key: 'test', order: 10, render: testTile });

export async function runTest() {
  if (testing || !ED.refs || !S.report || !ED.refs.q) return;
  syncFromFrame();
  const q = ED.refs.q;
  const pageUrl = ED.refs.page;
  const draft = draftFor(pageUrl);
  if (!draft.text) {
    toast('Open or write a page first.');
    return;
  }
  const sameAsLoaded = draft.base != null && !!draft.loaded && looseKey(draft.url) === looseKey(draft.loaded);
  testing = true;
  const btn = $('#test-btn');
  if (btn) {
    btn.disabled = true;
    clear(btn).append(h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Testing');
  }
  try {
    const [run] = await Promise.all([
      api.post(`/api/v1/projects/${enc(S.slug)}/test`, { q, draft: draft.text, url: draft.url, original: sameAsLoaded ? 'current' : 'none', original_html: sameAsLoaded ? draft.base : '' }),
      getCompare(q).catch(() => null),
    ]);
    S.results.set(`${q}|${looseKey(pageUrl)}`, run);
    const here = !!ED.refs && ED.refs.q === q && ED.refs.page === pageUrl && S.screen === 'editor';
    if (here) {
      renderResult(run, true);
      ED.refs.result.scrollIntoView({ behavior: reduceMotion.matches ? 'auto' : 'smooth', block: 'start' });
    }
    call('test-finished', { run, search: q, url: draft.url, page: pageUrl, html: draft.text, editor: here ? editorCtx() : null });
  } catch (err) {
    toast(err.message);
  } finally {
    testing = false;
    const again = $('#test-btn');
    if (again) {
      again.disabled = false;
      clear(again).append('Test changes');
    }
  }
}

document.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && S.screen === 'editor' && S.slug) {
    event.preventDefault();
    runTest();
  }
});

export function verdictOf(run) {
  const after = run.after || {};
  const before = run.before;
  if (after.position == null) return { text: 'Not placed', cls: '' };
  if (before && before.position != null) {
    const change = before.position - after.position;
    if (change > 0) return { text: `Predicted ${plural(change, 'place')} higher`, cls: 'up', change };
    if (change < 0) return { text: `Predicted ${plural(-change, 'place')} lower`, cls: 'down', change };
    return { text: 'Predicted no change', cls: '', change: 0 };
  }
  return { text: `Predicted #${after.position} of ${after.of}`, cls: '' };
}

export function reasonOf(run) {
  const after = run.after || {};
  if (after.position == null) return after.unplaced || 'The page did not pass the on-topic check for this search, so it was not placed.';
  const fa = run.facts_after || {};
  const fb = run.facts_before;
  const parts = [];
  if (fb) {
    const diff = fa.word_count - fb.word_count;
    if (Math.abs(diff) >= Math.max(25, fb.word_count * 0.08)) parts.push(diff > 0 ? `is ${plural(diff, 'word')} longer` : `is ${plural(-diff, 'word')} shorter`);
    const hd = fa.headings - fb.headings;
    if (hd > 0) parts.push(`adds ${plural(hd, 'section')}`);
    else if (hd < 0) parts.push(`drops ${plural(-hd, 'section')}`);
    if (fa.table_count > fb.table_count) parts.push('adds a table');
    else if (fa.table_count < fb.table_count) parts.push('drops a table');
    if (fa.has_faq !== fb.has_faq) parts.push(fa.has_faq === 'yes' ? 'adds an FAQ' : 'drops the FAQ');
    if (fa.title !== fb.title) parts.push('changes the title');
    if (!parts.length) return 'Your edit reads much like the current page.';
  } else {
    parts.push(`is scored as a new page against ${plural(run.candidates, 'page')}`);
    if (fa.word_count != null) parts.push(`has ${plural(fa.word_count, 'word')}`);
  }
  const shown = parts.slice(0, 3);
  return `Your page ${shown.length > 1 ? `${shown.slice(0, -1).join(', ')} and ${shown[shown.length - 1]}` : shown[0]}.`;
}

function moveChip(change) {
  if (!change) return h('span', { class: 'move same', text: 'same' });
  return h('span', { class: `move ${change > 0 ? 'up' : 'down'}` }, icon(change > 0 ? 'up' : 'down'), String(Math.abs(change)));
}

/* Paints a test answer under the editor: the verdict, the predicted order, and what changed. */
export function renderResult(run, animate) {
  const host = clear(ED.refs.result);
  const verdict = verdictOf(run);
  const after = run.after || {};
  const before = run.before;
  const head = h('section', { class: 'tile white span-2' },
    after.confidence && after.confidence !== 'n/a' ? h('span', { class: `label ${after.confidence === 'CLEARER' ? 'label-ok' : 'label-medium'}`, text: after.confidence === 'CLEARER' ? 'Clearer call' : 'Uncertain' }) : null,
    h('h2', { class: `verdict ${verdict.cls}`, text: verdict.text }));
  if (after.position != null) {
    const ranks = h('div', { class: 'ranks' });
    if (before && before.position != null) ranks.append(h('div', { class: 'rank-box before' }, h('span', { class: 'k', text: 'Now' }), h('span', { class: 'n', text: `#${before.position}` }), h('span', { class: 'of', text: `of ${before.of}` })), icon('arrow', 'arrow'));
    ranks.append(h('div', { class: 'rank-box after' }, h('span', { class: 'k', text: 'With your edit' }), h('span', { class: 'n', text: `#${after.position}` }), h('span', { class: 'of', text: `of ${after.of}` })));
    head.append(ranks);
  }
  head.append(h('p', { class: 'reason', text: reasonOf(run) }), h('p', { class: 'caveat', text: 'Among the pages that rank for this search today. A prediction, not a Google ranking.' }));
  const grid = h('div', { class: 'bento' }, head);
  if (after.order && after.order.length) grid.append(orderTile(run, animate));
  const predict = run.predict;
  if (predict && predict.searches && predict.searches.length) grid.append(searchesTile(run), guidanceTile(run));
  else if (predict && predict.error) grid.append(h('section', { class: 'tile span-2' }, h('div', { class: 'tile-k', text: 'Every search' }), h('p', { class: 'tile-sub', text: predict.error })));
  grid.append(detailsTile(run));
  host.append(grid);
}

function orderTile(run, animate) {
  const after = run.after;
  const before = run.before && run.before.order && run.before.order.length ? run.before : null;
  const f = facts(draftFor(ED.refs ? ED.refs.page : run.url).text);
  const keyOf = (item) => (item.page ? '__page__' : item.url);
  const titles = new Map();
  const rows = new Map();
  const list = h('ol', { class: 'order', 'aria-label': 'Predicted order' });
  const make = (item) => {
    const isPage = !!item.page;
    const li = h('li', { class: isPage ? 'is-draft' : null },
      h('span', { class: `pos${isPage ? ' you' : ''}`, text: String(item.predicted) }),
      h('span', { class: `fav${isPage ? ' you' : ''}`, text: isPage ? '' : initial(item.domain) }),
      h('div', { class: 'main' }, h('div', { class: 't', text: isPage ? f.title || 'Your page' : item.domain }), h('div', { class: 'd', text: isPage ? `Your page · ${hostOf(run.url)}` : item.domain })),
      h('span', { class: 'side' }));
    if (isPage) li.querySelector('.fav').append(icon('pencil'));
    rows.set(keyOf(item), li);
    if (!isPage) titles.set(item.url, li.querySelector('.t'));
    return li;
  };
  const side = (li, item) => {
    const box = clear(li.querySelector('.side'));
    if (item.page) {
      const v = verdictOf(run);
      if (before && v.change != null) box.append(moveChip(v.change), ` was #${run.before.position}`);
      else box.append('New page');
    } else box.append(`Google #${item.observed}`);
  };
  const start = before || after;
  for (const item of start.order) list.append(make(item));
  for (const item of after.order) if (!rows.has(keyOf(item))) list.append(make(item));
  const settle = () => {
    const first = new Map();
    for (const [key, li] of rows) first.set(key, li.getBoundingClientRect().top);
    for (const item of after.order) {
      const li = rows.get(keyOf(item));
      li.querySelector('.pos').textContent = String(item.predicted);
      side(li, item);
      list.append(li);
    }
    if (reduceMotion.matches || !animate || !before) return;
    for (const [key, li] of rows) {
      const delta = first.get(key) - li.getBoundingClientRect().top;
      if (!delta) continue;
      li.style.transition = 'none';
      li.style.transform = `translateY(${delta}px)`;
      void li.offsetWidth;
      li.style.transition = 'transform 0.9s cubic-bezier(0.32, 0.72, 0, 1)';
      li.style.transform = '';
    }
  };
  if (before && animate && !reduceMotion.matches) {
    for (const item of before.order) {
      const li = rows.get(keyOf(item));
      if (!li) continue;
      if (item.page) clear(li.querySelector('.side')).append('Now');
      else side(li, item);
    }
    setTimeout(settle, 900);
  } else settle();
  getCompare(run.search).then((view) => {
    for (const cand of view.candidates || []) {
      const el = titles.get(cand.url);
      if (el && cand.title) el.textContent = cand.title;
    }
  }).catch(() => {});
  return h('section', { class: 'tile span-2' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Predicted order' }), h('span', { class: 'aside', text: plural(after.of, 'page') })), list);
}

function detailsTile(run) {
  const fa = run.facts_after || {};
  const fb = run.facts_before;
  const rows = [['Words', 'word_count'], ['Sections', 'headings'], ['Tables', 'table_count'], ['FAQ', 'has_faq'], ['Visible date', 'updated'], ['Title', 'title']];
  const fmt = (v) => (v == null || v === '' ? 'none' : typeof v === 'number' ? v.toLocaleString() : String(v));
  const table = h('table', { class: 'kv' },
    fb ? h('thead', null, h('tr', null, h('th', { text: '' }), h('th', { text: 'Now' }), h('th', { text: 'Edit' }))) : null,
    h('tbody', null, rows.map(([label, key]) => h('tr', null, h('th', { text: label }), fb ? h('td', { text: fmt(fb[key]) }) : null, h('td', { class: fb && fmt(fa[key]) !== fmt(fb[key]) ? 'changed' : null, text: fmt(fa[key]) })))));
  const notes = h('div', { class: 'note-list' });
  if (run.query_pairwise != null) notes.append(h('p', { text: `On this search the model ordered ${pct(run.query_pairwise)} of ${plural(run.query_pairs || 0, 'page pair')} right when held out. 50% is a coin flip.` }));
  if (run.topic_after && run.topic_after.missing && run.topic_after.missing.length) notes.append(h('p', { text: `Words the ranking pages use that yours doesn't: ${run.topic_after.missing.join(', ')}.` }));
  for (const note of run.notes || []) notes.append(h('p', { text: note }));
  notes.append(h('p', { text: run.saved ? 'Saved to this site\'s test history.' : 'Not saved.' }));
  return h('section', { class: 'tile span-4' }, h('div', { class: 'bento' },
    h('div', { class: 'span-2' }, h('div', { class: 'tile-head' }, h('h2', { text: fb ? 'What changed' : 'Your page' })), table),
    h('div', { class: 'span-2' }, h('div', { class: 'tile-head' }, h('h2', { text: 'How sure is this' })), notes)));
}

/* -- every search the page targets, and what would move it up (predict workstream) -------------------------- */

const posText = (value) => (value == null ? 'none' : `#${Math.max(1, Math.round(value))}`);

function moveCell(row) {
  if (row.move == null) return h('td', { class: 'predict-move', text: row.method === 'new page' ? 'new' : '' });
  const steps = Math.round(row.move);
  if (!steps) return h('td', { class: 'predict-move' }, h('span', { class: 'move same', text: 'same' }));
  return h('td', { class: 'predict-move' }, moveChip(steps));
}

function confidenceChip(conf) {
  const sure = conf && conf.label === 'CLEARER';
  return h('span', { class: `label ${sure ? 'label-ok' : 'label-medium'} predict-conf`, text: sure ? 'CLEARER' : 'UNSURE' });
}

function backtestText(conf) {
  if (!conf || conf.backtest_pairwise == null) return 'no backtest yet';
  return `backtest ${pct(conf.backtest_pairwise)} of ${plural(conf.backtest_pairs || 0, 'pair')}${conf.near_chance ? ', near chance' : ''}`;
}

function searchesTile(run) {
  const rows = run.predict.searches;
  const body = rows.map((row) => h('tr', { class: row.search === run.search ? 'is-current' : null },
    h('th', { scope: 'row', class: 'predict-q' }, h('span', { text: row.search }), h('span', { class: 'predict-method', text: row.method === 'anchored' ? `ranks #${Math.round(row.observed_position)} today` : row.method === 'new page' ? 'new page' : 'not placed' })),
    h('td', { text: row.now == null ? 'not ranked' : posText(row.now) }),
    h('td', { class: 'predict-after', text: row.with_edit == null ? 'not placed' : posText(row.with_edit) }),
    moveCell(row),
    h('td', { class: 'predict-sure' }, confidenceChip(row.confidence), h('span', { class: 'predict-bt', text: backtestText(row.confidence) }))));
  const table = h('div', { class: 'predict-scroll' }, h('table', { class: 'predict-table', 'aria-label': 'PREDICTED position for every search' },
    h('thead', null, h('tr', null, h('th', { scope: 'col', text: 'Search' }), h('th', { scope: 'col', text: 'Now' }), h('th', { scope: 'col', text: 'With edit' }), h('th', { scope: 'col', text: 'Move' }), h('th', { scope: 'col', text: 'How sure' }))),
    h('tbody', null, body)));
  return h('section', { class: 'tile white span-4 predict-tile' },
    h('div', { class: 'tile-head' }, h('h2', { text: 'Every search' }), h('span', { class: 'label predict-label', text: 'PREDICTED' })),
    h('p', { class: 'tile-sub', text: 'Pages that rank today start from their real position. New pages are placed by the new page model.' }),
    table,
    h('p', { class: 'caveat', text: run.predict.backtest_note || '' }),
    run.predict.forward_check ? h('p', { class: 'caveat', text: run.predict.forward_check }) : null);
}

function guideDetail(item) {
  if (['words', 'headings', 'links'].includes(item.feature)) return null; // the sentence already gives both numbers
  const show = (v) => (typeof v === 'number' ? pct(v) : String(v));
  return h('span', { class: 'predict-guide-d', text: `You: ${show(item.yours)}. Pages above: ${show(item.above)}.` });
}

function guidanceTile(run) {
  const rows = run.predict.searches;
  const row = rows.find((item) => item.search === run.search) || rows[0];
  const items = (row && row.guidance) || [];
  const list = items.length
    ? h('ol', { class: 'predict-guide' }, items.map((item) => h('li', null, h('span', { class: 'predict-guide-t', text: item.text }), guideDetail(item))))
    : h('p', { class: 'tile-sub', text: row && row.with_edit != null && row.with_edit <= 1.5 ? 'Nothing is PREDICTED above this page here.' : 'No clear gap on the features this model trusts.' });
  const above = (row && row.above) || [];
  return h('section', { class: 'tile span-2 predict-tile' },
    h('div', { class: 'tile-head' }, h('h2', { text: 'What would move you up' }), h('span', { class: 'label predict-label', text: 'Guidance' })),
    h('p', { class: 'tile-sub', text: above.length ? `Compared with ${plural(above.length, 'page')} just above you for ${row.search}: ${above.map((item) => hostOf(item.url)).join(', ')}.` : `For ${row ? row.search : 'this search'}.` }),
    list,
    h('p', { class: 'caveat', text: run.predict.guidance_note || 'Guidance, not a guarantee.' }));
}

