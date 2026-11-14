/* Searches: every search the site tracks (your position, PREDICTED position, intent, where the search came from,
   the best page) and searches Ranksim found but does not track yet. A row opens a sheet with the real top 10 and the
   content gaps for that search. Extra columns come from registerSearchColumn. */

import { S, api, h, icon, clear, main, pathOf, go, enc, plural, page, footer, noSearchData, serpList, openSheet, closeSheet, toast, reloadSite } from '../core.js';
import { registerScreen, searchColumns, renderEntry } from '../hooks.js';

export const SOURCE_LABEL = {
  added: 'Added', title: 'Title', h1: 'Heading', h2: 'Subheading', slug: 'Page address', related: 'Related search',
  paa: 'People ask', competitor: 'Competitor titles', gsc: 'Search Console', plugin: 'Plugin',
};
export const INTENT_LABEL = { commercial: 'Buying', transactional: 'Ready to buy', informational: 'Learning', navigational: 'Brand' };

export const sourceLabel = (key) => SOURCE_LABEL[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : '');

/* What discovery knows about each search, by search text. */
export function discoveryIndex(report) {
  const found = new Map();
  const data = report && report.discovery;
  for (const item of (data && data.tracked) || []) found.set(item.q, item);
  for (const item of (data && data.searches) || []) {
    found.set(item.q, item);
    if (!found.has(item.q) || !(found.get(item.q).sources || []).length) found.set(item.q, item);
    for (const variant of item.variants || []) if (!found.has(variant)) found.set(variant, item);
  }
  return found;
}

function intentChip(item) {
  if (!item || !item.intent) return h('span', { class: 'none', text: '' });
  return h('span', { class: `chip intent-${item.intent}`, title: item.intent_rule || '', text: INTENT_LABEL[item.intent] || item.intent });
}

function sourceText(item) {
  if (!item || !item.sources || !item.sources.length) return '';
  const names = item.sources.map((source) => sourceLabel(source.source));
  return names.length > 1 ? `${names[0]} +${names.length - 1}` : names[0];
}

function columnCell(column, row) {
  const out = renderEntry({ key: column.key, render: column.cell }, row);
  return h('span', { class: 'qcol', role: 'cell', 'data-label': column.header || '' }, out);
}

function openInEditor(row) {
  closeSheet(true);
  go('editor', row.url ? { q: row.q, page: row.url } : { q: row.q });
}

export function renderSearches() {
  const R = S.report;
  const known = discoveryIndex(R);
  const columns = searchColumns().filter((column) => { // when(report): show the column only when it has data
    try {
      return typeof column.when !== 'function' || column.when(R);
    } catch (err) {
      console.error(`Ranksim: ${column.key || 'unnamed'} column when failed`, err);
      return false;
    }
  });
  const table = h('div', { class: 'stable', role: 'table', 'aria-label': 'Your searches' });
  // repeat(0, auto) is invalid CSS, so the template is built here with exactly the columns there are.
  table.style.gridTemplateColumns = ['minmax(0, 2fr)', 'auto', ...columns.map(() => 'auto'), 'auto', 'auto', 'auto', 'minmax(0, 1.3fr)', 'auto'].join(' ');
  table.append(h('div', { class: 'srow shead', role: 'row' },
    h('span', { role: 'columnheader', text: 'Search' }),
    h('span', { class: 'c-num', role: 'columnheader', text: 'You' }),
    columns.map((column) => h('span', { class: 'qcol', role: 'columnheader', text: column.header || '' })),
    h('span', { class: 'c-num', role: 'columnheader', text: 'PREDICTED' }),
    h('span', { class: 'c-intent', role: 'columnheader', text: 'Intent' }),
    h('span', { class: 'c-source', role: 'columnheader', text: 'Source' }),
    h('span', { class: 'c-page', role: 'columnheader', text: 'Best page' }),
    h('span', { class: 'c-open', role: 'columnheader' }, h('span', { class: 'sr-only', text: 'Open' }))));
  const rows = R.rankings.slice().sort((a, b) => (a.position || 99) - (b.position || 99) || a.q.localeCompare(b.q));
  for (const row of rows) {
    const item = known.get(row.q);
    const predicted = row.predicted && row.predicted.position != null ? `#${row.predicted.position}` : '';
    table.append(h('div', { class: 'srow', role: 'row' },
      h('button', { class: 'c-q qrow', type: 'button', role: 'cell', onClick: () => openSearch(row) }, h('span', { class: 'q', text: row.q })),
      h('span', { class: `c-num p${row.position ? '' : ' none'}`, role: 'cell', text: row.position ? `#${row.position}` : row.checked ? 'Not top 10' : 'Not checked' }),
      columns.map((column) => columnCell(column, row)),
      h('span', { class: `c-num pred${predicted ? '' : ' none'}`, role: 'cell', title: predicted ? `PREDICTED of ${row.predicted.of}` : '', text: predicted || '' }),
      h('span', { class: 'c-intent', role: 'cell' }, intentChip(item)),
      h('span', { class: 'c-source', role: 'cell', text: sourceText(item) }),
      h('span', { class: 'c-page one-line', role: 'cell', title: row.url || '', text: row.url ? pathOf(row.url) : 'No page yet' }),
      h('span', { class: 'c-open', role: 'cell' }, h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Open ${row.q} in the editor`, title: 'Open in editor', onClick: () => openInEditor(row) }, icon('pencil')))));
  }
  if (!rows.length) table.append(h('div', { class: 'empty-note', text: 'No searches yet.' }));

  const tracked = h('section', { class: 'tile span-4' },
    h('div', { class: 'tile-head' }, h('h2', { text: 'Your searches' }), h('span', { class: 'aside', text: `${R.kpis.top10} in top 10` })), table);
  const callout = noSearchData();
  clear(main).append(page('Searches', 'Found from your pages, Google and your competitors.', null,
    h('div', { class: 'bento' }, callout, tracked, suggestions(R)), footer()));
}

/* Searches discovery found that the site does not track yet, with Add. */
function suggestions(R) {
  const data = R.discovery;
  const fresh = ((data && data.searches) || []).filter((item) => !item.tracked && !R.rankings.some((row) => row.q === item.q));
  if (!fresh.length) return null;
  const list = h('div', { class: 'rows' });
  const setBusy = (busy) => list.querySelectorAll('button.add').forEach((button) => { button.disabled = busy; });
  for (const item of fresh.slice(0, 12)) {
    const add = S.publicDemo ? null : h('button', {
      class: 'btn add',
      type: 'button',
      onClick: async () => {
        setBusy(true);  // one change at a time, so a second Add never sends a list without the first
        try {
          const current = S.project && Array.isArray(S.project.queries) ? S.project.queries.map((entry) => entry.q) : S.report.rankings.map((row) => row.q);
          if (!current.includes(item.q)) await api.post(`/api/v1/projects/${enc(S.slug)}/settings`, { queries: current.concat([item.q]) });
          await reloadSite();
          toast('Added. Refresh in Settings to get its Google results.');
          renderSearches();
        } catch (err) {
          toast(err.message);
          setBusy(false);
        }
      },
    }, icon('plus'), 'Add');
    list.append(h('div', { class: 'rowcard' },
      h('div', { class: 'body' },
        h('div', { class: 't', text: item.q }),
        h('div', { class: 'm' }, intentChip(item), h('span', { text: sourceText(item) }))),
      add ? h('div', { class: 'right' }, add) : null));
  }
  return h('section', { class: 'tile span-4' },
    h('div', { class: 'tile-head' }, h('h2', { text: 'Found, not tracked' }), h('span', { class: 'aside', text: plural(fresh.length, 'search', 'searches') })), list);
}

/* The sheet for one search: where you are, the real top 10, and what the pages above you have that yours lacks. */
export function openSearch(row) {
  const R = S.report;
  const item = discoveryIndex(R).get(row.q);
  const gap = (R.gaps || []).find((entry) => entry.q === row.q);
  openSheet((sheet, closeBtn) => {
    const sub = [item ? INTENT_LABEL[item.intent] : null, item ? sourceText(item) : null].filter(Boolean).join(' · ');
    sheet.append(h('div', { class: 'sheet-head' }, h('div', null, h('h2', { text: row.q }), sub ? h('div', { class: 'sub', text: sub }) : null), closeBtn));
    const body = h('div', { class: 'sheet-body' });
    const predicted = row.predicted && row.predicted.position != null ? row.predicted : null;
    body.append(h('div', { class: 'sgrid' },
      h('div', { class: 'tile white' }, h('div', { class: 'tile-k', text: 'Google' }), h('div', { class: 'num', text: row.position ? `#${row.position}` : '-' }), h('div', { class: 'tile-sub', text: row.position ? 'Your position' : row.checked ? 'Not in the top 10' : 'Not checked yet' })),
      h('div', { class: 'tile white' }, h('div', { class: 'tile-k', text: 'PREDICTED' }), h('div', { class: 'num', text: predicted ? `#${predicted.position}` : '-' }), h('div', { class: 'tile-sub', text: predicted ? `of ${predicted.of} stored pages` : 'Needs a trained model' }))));
    if (row.url) body.append(h('p', { class: 'reason', text: `Best page: ${pathOf(row.url)}` }));
    if (row.top && row.top.length) body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Top 10 on Google' })), serpList(row.top)));
    else body.append(h('div', { class: 'empty-note', text: 'No Google results for this search yet.' }));
    if (gap) body.append(gapTile(gap));
    if (item && item.sources && item.sources.length) body.append(sourcesTile(item));
    body.append(h('div', { class: 'form-actions' }, h('button', { class: 'btn btn-primary btn-large', type: 'button', onClick: () => openInEditor(row) }, icon('pencil'), row.url ? 'Open in editor' : 'Write a page for it')));
    sheet.append(body);
  });
}

function sourcesTile(item) {
  return h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Where it came from' })),
    h('div', { class: 'rows' }, item.sources.map((source) => h('div', { class: 'srcline' },
      h('span', { class: 'chip chip-gray', text: sourceLabel(source.source) }),
      h('span', { class: 'one-line', text: (source.detail || []).join(', ') })))));
}

/* Friendly names for structured data types. */
const SCHEMA_NAME = {
  FAQPage: 'FAQ markup', HowTo: 'how-to markup', Product: 'product markup', SoftwareApplication: 'software markup',
  Review: 'review markup', AggregateRating: 'star rating markup', BreadcrumbList: 'breadcrumb markup', VideoObject: 'video markup',
  Article: 'article markup', BlogPosting: 'article markup', Organization: 'organization markup', LocalBusiness: 'local business markup',
  ItemList: 'list markup', WebPage: 'page markup', WebSite: 'site markup',
};

function pagesAbove(item) {
  return item.pages === item.of ? `All ${item.of} pages above` : `${item.pages} of the ${item.of} pages above`;
}

/* One gap as a sentence, the pages that have it, and Open in editor. */
function gapLine(sentence, urls, gap, extra) {
  return h('div', { class: 'gapline' },
    h('div', { class: 'gaprow' },
      h('div', { class: 'gaptext' }, h('div', { class: 't', text: sentence }), extra || null,
        h('div', { class: 'evidence' }, urls.slice(0, 4).map((url) => h('a', { class: 'one-line', href: url, target: '_blank', rel: 'noopener noreferrer', title: url, text: hostPath(url) })))),
      gap.q ? h('button', { class: 'btn btn-sm', type: 'button', onClick: () => openInEditor({ q: gap.q, url: gap.page }) }, icon('pencil'), gap.page ? 'Add it' : 'Write it') : null));
}

/* What the pages above you share and your page lacks, said plainly, with the pages that have each one. */
export function gapTile(gap) {
  const tile = h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: 'What the pages above have' }), h('span', { class: 'aside', text: gap.compared && gap.compared.length ? plural(gap.compared.length, 'page') : '' })));
  if (gap.note) tile.append(h('p', { class: 'reason gap-bottom', text: gap.note }));
  const yours = gap.checked === false ? '' : ' Yours doesn\'t.';
  const lines = h('div', { class: 'rows' });
  for (const item of gap.elements || []) lines.append(gapLine(`${pagesAbove(item)} have ${item.label}.${yours}`, item.evidence, gap));
  for (const topic of gap.topics || []) {
    lines.append(gapLine(`${pagesAbove(topic)} have a section on "${topic.topic}".${topic.in_your_text ? ' You mention it, but without a heading.' : yours}`,
      topic.evidence.map((entry) => entry.url), gap,
      topic.evidence.length ? h('div', { class: 'quote' }, topic.evidence.slice(0, 2).map((entry) => h('span', { class: 'one-line', text: `"${entry.heading}"` }))) : null));
  }
  for (const item of gap.schema || []) lines.append(gapLine(`${pagesAbove(item)} use ${SCHEMA_NAME[item.type] || `${item.type} markup`}.${yours}`, item.evidence, gap));
  if (gap.terms && gap.terms.length) {
    const top = gap.terms.slice(0, 8);
    lines.append(h('div', { class: 'gapline' },
      h('div', { class: 't', text: gap.checked === false ? 'Words the pages above use:' : 'Words the pages above use that yours doesn\'t:' }),
      h('div', { class: 'chips' }, top.map((term) => h('span', {
        class: `chip${term.in_search ? '' : ' chip-gray'}`,
        title: `${pagesAbove(term)}: ${term.evidence.map(hostPath).join(', ')}`,
        text: `${term.term} (${term.pages} of ${term.of})`,
      })))));
  }
  if (gap.words && gap.words.median_above && gap.words.yours != null && gap.words.yours < gap.words.median_above * 0.7) {
    lines.append(gapLine(`The pages above have about ${gap.words.median_above} words. Yours has ${gap.words.yours}.`, [], gap));
  }
  if (lines.childNodes.length) tile.append(lines);
  else if (!gap.note) tile.append(h('p', { class: 'reason', text: 'Nothing the pages above share is missing from yours.' }));
  return tile;
}

export function hostPath(url) {
  try {
    const parsed = new URL(url);
    return `${parsed.hostname.replace(/^www\./, '')}${parsed.pathname === '/' ? '' : parsed.pathname}`;
  } catch {
    return url;
  }
}

registerScreen('searches', { label: 'Searches', order: 30, render: () => renderSearches() });
