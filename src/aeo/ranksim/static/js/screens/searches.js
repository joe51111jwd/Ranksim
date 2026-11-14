/* Searches: every search the site tracks with its position, and the top 10 for the one selected.
   Extra columns come from registerSearchColumn. */

import { S, h, icon, clear, main, pathOf, bar, go, pathFor, page, footer, noSearchData, serpList } from '../core.js';
import { registerScreen, searchColumns, renderEntry } from '../hooks.js';

function columnCell(column, row) {
  const out = renderEntry({ key: column.key, render: column.cell }, row);
  return h('span', { class: 'qcol' }, out);
}

export function renderSearches() {
  const R = S.report;
  const want = S.params.get('q');
  const selected = R.rankings.find((row) => row.q === want) || R.rankings.slice().sort((a, b) => (a.position || 99) - (b.position || 99))[0];
  const columns = searchColumns().filter((column) => typeof column.when !== 'function' || column.when(R)); // when(report): show the column only when it has data
  const list = h('div', { class: `qlist${columns.length ? ' has-cols' : ''}` });
  if (columns.length) {
    list.style.setProperty('--qcols', String(columns.length));
    list.append(h('div', { class: 'qhead', 'aria-hidden': 'true' }, h('span', { text: 'Search' }), h('span', { text: 'Position' }), columns.map((column) => h('span', { class: 'qcol', text: column.header || '' }))));
  }
  for (const row of R.rankings) {
    list.append(h('button', {
      class: 'qrow',
      type: 'button',
      'aria-pressed': String(!!selected && row.q === selected.q),
      onClick: () => {
        history.replaceState({}, '', pathFor('searches', { q: row.q }));
        S.params = new URLSearchParams({ q: row.q });
        renderSearches();
      },
    },
    h('span', { class: 'q', text: row.q }),
    h('span', { class: `p${row.position ? '' : ' none'}`, text: row.position ? `#${row.position}` : row.checked ? 'Not in top 10' : 'Not checked' }),
    columns.map((column) => columnCell(column, row)),
    bar(row.position ? (11 - row.position) / 10 : 0.02, row.position && row.position <= 3 ? 'dark' : null)));
  }
  const detail = h('section', { class: 'tile' });
  if (selected) {
    const predicted = selected.predicted && selected.predicted.position != null ? `Model puts you #${selected.predicted.position} of ${selected.predicted.of}` : '';
    detail.append(h('div', { class: 'tile-head' }, h('h2', { class: 'one-line', text: selected.q }), h('span', { class: 'aside', text: predicted })));
    if (!selected.checked) detail.append(h('div', { class: 'empty-note', text: 'No Google results for this search yet.' }));
    else {
      if (!selected.position) detail.append(h('div', { class: 'callout gap-bottom' }, icon('warn'), h('span', { text: selected.url ? `${pathOf(selected.url)} is about this search but isn't in the top 10.` : 'No page on your site targets this search.' })));
      detail.append(serpList(selected.top));
    }
    detail.append(h('div', { class: 'result-actions' },
      h('button', { class: 'btn btn-dark', type: 'button', onClick: () => go('editor', selected.url ? { q: selected.q, page: selected.url } : { q: selected.q }) }, icon('pencil'), selected.position ? 'Improve your page' : 'Write a page for it')));
  }
  const callout = noSearchData();
  const body = h('div', { class: 'split' }, h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Your searches' }), h('span', { class: 'aside', text: `${R.kpis.top10} in top 10` })), list), detail);
  clear(main).append(page('Searches', 'The searches your pages are written for, and who holds the first page for each.', null, callout ? h('div', { class: 'bento gap-bottom' }, callout) : null, body), footer());
}

registerScreen('searches', { label: 'Searches', order: 30, render: () => renderSearches() });
