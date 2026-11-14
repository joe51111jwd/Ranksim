/* Searches: every search the site tracks (your position, PREDICTED position, intent, where the search came from,
   the best page) and searches Ranksim found but does not track yet. A row opens a sheet with the real top 10 and the
   content gaps for that search. Extra columns come from registerSearchColumn. */

import { S, api, h, icon, clear, main, pathOf, go, enc, esc, looseKey, plural, page, footer, noSearchData, serpList, openSheet, closeSheet, toast, reloadSite } from '../core.js';
import { registerScreen, searchColumns, renderEntry } from '../hooks.js';
import { draftFor, normalize } from '../editor/editor.js';

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

/* True when the site's positions are the synthetic demo's, never Google's. */
export function isDemo() {
  return Boolean((S.report && S.report.data && S.report.data.kind === 'synthetic') || (S.slug && S.slug === S.demoSlug) || (S.project && S.project.dataset === 'synthetic'));
}

const engine = () => (isDemo() ? 'Demo' : 'Google');

/* The page for a search: the one Google ranks or that targets it, else the page Search Console reports for it. */
export function bestPage(row, report) {
  if (row.url) return { url: row.url, fromGsc: false };
  const gsc = report && report.gsc && report.gsc.positions && report.gsc.positions[row.q];
  if (gsc && typeof gsc.page === 'string' && gsc.page) return { url: gsc.page, fromGsc: true };
  return { url: null, fromGsc: false };
}

function pageCell(row, report) {
  const best = bestPage(row, report);
  if (!best.url) return h('span', { class: 'c-page one-line', role: 'cell', text: 'No page yet' });
  return h('span', { class: 'c-page one-line', role: 'cell', title: best.fromGsc ? `${best.url} (from Search Console)` : best.url, text: pathOf(best.url) });
}

/* How to show a row's PREDICTED position honestly. A prediction marked anything but CLEARER shows as UNSURE in gray;
   the backtest pair count goes in the tooltip; a missing prediction (null) shows nothing. Works with and without the
   confidence fields. */
export function predictionView(row) {
  const p = row && row.predicted;
  if (!p || p.position == null) {
    const reason = p && typeof p.reason === 'string' && p.reason ? p.reason : '';
    const sub = reason || (row && row.position ? 'Already ranks: no prediction needed' : 'Needs a trained model');
    return { text: p || (row && row.position) ? '-' : '', unsure: true, tip: sub, sub };
  }
  const graded = typeof p.confidence === 'string';
  const unsure = graded && p.confidence.toUpperCase() !== 'CLEARER';
  const pairs = Number.isFinite(p.query_pairs) ? p.query_pairs : null;
  const acc = Number.isFinite(p.query_pairwise) ? `${Math.round(p.query_pairwise * 100)}%` : null;
  const backtest = pairs != null ? `Backtest: ${acc ? `${acc} of ` : ''}${plural(pairs, 'pair')} ordered right` : '';
  const tip = [`PREDICTED #${p.position}${p.of ? ` of ${p.of}` : ''}`, unsure ? 'UNSURE' : null, backtest || null, p.method ? `Method: ${p.method}` : null].filter(Boolean).join('. ');
  return {
    text: unsure ? 'UNSURE' : `#${p.position}`,
    unsure,
    tip,
    sub: unsure ? `Guess #${p.position}${p.of ? ` of ${p.of}` : ''}, not reliable yet` : (p.of ? `of ${p.of} stored pages` : 'PREDICTED'),
  };
}

function openInEditor(row) {
  closeSheet(true);
  const best = bestPage(row, S.report);
  go('editor', best.url ? { q: row.q, page: best.url } : { q: row.q });
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
    const pv = predictionView(row);
    table.append(h('div', { class: 'srow', role: 'row' },
      h('button', { class: 'c-q qrow', type: 'button', role: 'cell', onClick: () => openSearch(row) }, h('span', { class: 'q', text: row.q })),
      h('span', { class: `c-num p${row.position ? '' : ' none'}`, role: 'cell', text: row.position ? `#${row.position}` : row.checked ? 'Not top 10' : 'Not checked' }),
      columns.map((column) => columnCell(column, row)),
      h('span', { class: `c-num pred${pv.text ? '' : ' none'}${pv.unsure ? ' unsure' : ''}`, role: 'cell', title: pv.tip, text: pv.text }),
      h('span', { class: 'c-intent', role: 'cell' }, intentChip(item)),
      h('span', { class: 'c-source', role: 'cell', text: sourceText(item) }),
      pageCell(row, R),
      h('span', { class: 'c-open', role: 'cell' }, h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Open ${row.q} in the editor`, title: 'Open in editor', onClick: () => openInEditor(row) }, icon('pencil')))));
  }
  if (!rows.length) table.append(h('div', { class: 'empty-note', text: 'No searches yet.' }));

  const tracked = h('section', { class: 'tile span-4' },
    h('div', { class: 'tile-head' }, h('h2', { text: 'Your searches' }), h('span', { class: 'aside', text: `${R.kpis.top10} in top 10` })), table);
  const callout = noSearchData();
  clear(main).append(page('Searches', isDemo() ? 'Demo data, not Google results.' : 'Found from your pages, Google and your competitors.', null,
    h('div', { class: 'bento' }, callout, tracked, suggestions(R)), footer()));
}

/* Searches discovery found that the site does not track yet, with Add. Adds queue up: the clicked button says
   Adding, later clicks say Queued, and they are saved together. */
const SHOWN = 12;
const adding = { queue: [], busy: false, showAll: false };

function addButton(item) {
  const state = adding.busy && adding.queue.includes(item.q) ? 'queued' : adding.current === item.q || (adding.inFlight || []).includes(item.q) ? 'adding' : 'idle';
  const button = h('button', { class: 'btn add', type: 'button', disabled: state !== 'idle' || null, 'aria-live': 'polite' },
    state === 'adding' ? [h('span', { class: 'spinner spinner-sm', 'aria-hidden': 'true' }), 'Adding'] : state === 'queued' ? 'Queued' : [icon('plus'), 'Add']);
  button.addEventListener('click', () => queueAdd(item.q));
  return button;
}

async function queueAdd(q) {
  if (!adding.queue.includes(q)) adding.queue.push(q);
  if (adding.busy) {
    renderSearches();
    return;
  }
  adding.busy = true;
  let added = 0;
  try {
    while (adding.queue.length) {
      const batch = adding.queue.splice(0);
      adding.inFlight = batch;
      renderSearches();
      const current = S.project && Array.isArray(S.project.queries) ? S.project.queries.map((entry) => entry.q) : S.report.rankings.map((row) => row.q);
      const fresh = batch.filter((item) => !current.includes(item));
      if (fresh.length) await api.post(`/api/v1/projects/${enc(S.slug)}/settings`, { queries: current.concat(fresh) });
      await reloadSite();
      added += fresh.length;
    }
    toast(added ? `Added ${plural(added, 'search', 'searches')}. Refresh in Settings to get ${isDemo() ? 'their results' : 'their Google results'}.` : 'Already tracked.');
  } catch (err) {
    toast(err.message);
  } finally {
    adding.busy = false;
    adding.inFlight = [];
    adding.queue = [];
    renderSearches();
  }
}

function suggestions(R) {
  const data = R.discovery;
  const fresh = ((data && data.searches) || []).filter((item) => !item.tracked && !R.rankings.some((row) => row.q === item.q));
  if (!fresh.length) return null;
  const list = h('div', { class: 'rows' });
  const shown = adding.showAll ? fresh : fresh.slice(0, SHOWN);
  for (const item of shown) {
    list.append(h('div', { class: 'rowcard' },
      h('div', { class: 'body' },
        h('div', { class: 't', text: item.q }),
        h('div', { class: 'm' }, intentChip(item), h('span', { text: sourceText(item) }))),
      S.publicDemo ? null : h('div', { class: 'right' }, addButton(item))));
  }
  if (fresh.length > shown.length) {
    list.append(h('button', { class: 'btn btn-plain', type: 'button', onClick: () => { adding.showAll = true; renderSearches(); } }, `Show ${fresh.length - shown.length} more`));
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
    const pv = predictionView(row);
    body.append(h('div', { class: 'sgrid' },
      h('div', { class: 'tile white' }, h('div', { class: 'tile-k', text: engine() }), h('div', { class: 'num', text: row.position ? `#${row.position}` : '-' }), h('div', { class: 'tile-sub', text: row.position ? 'Your position' : row.checked ? 'Not in the top 10' : 'Not checked yet' })),
      h('div', { class: 'tile white' }, h('div', { class: 'tile-k', text: 'PREDICTED' }), h('div', { class: `num${pv.unsure ? ' unsure' : ''}`, text: pv.text || '-' }), h('div', { class: 'tile-sub', text: pv.sub }))));
    const best = bestPage(row, S.report);
    if (best.url) body.append(h('p', { class: 'reason', text: best.fromGsc ? `Best page: ${pathOf(best.url)} (from Search Console)` : `Best page: ${pathOf(best.url)}` }));
    if (row.top && row.top.length) body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: isDemo() ? 'Demo top 10' : 'Top 10 on Google' })), serpList(row.top)));
    else body.append(h('div', { class: 'empty-note', text: isDemo() ? 'No demo results for this search.' : 'No Google results for this search yet.' }));
    const brief = R.briefs && R.briefs[row.q];
    if (brief && !brief.note && (brief.sections.length || brief.questions.length)) body.append(briefTile(brief, row));
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

/* The HTML a brief starts a new page with: title, H1, the sections the pages that rank share, questions, and a
   table when most of them have one. Every line is a prompt to replace. */
export function briefHtml(brief) {
  const parts = [`<h1>${esc(brief.h1)}</h1>`, `<p>${esc(`Answer "${brief.q}" in the first two sentences.`)}</p>`];
  for (const section of brief.sections) {
    parts.push(`<h2>${esc(section.heading)}</h2>`, `<p>${esc(`Write this section. ${section.pages} of the ${section.of} pages that rank cover it.`)}</p>`);
  }
  if (brief.elements.some((item) => item.element === 'table')) {
    parts.push('<h2>Comparison</h2>', '<table><thead><tr><th>Option</th><th>Best for</th><th>Price</th></tr></thead><tbody><tr><td>Option one</td><td>Who it suits</td><td>From</td></tr><tr><td>Option two</td><td>Who it suits</td><td>From</td></tr></tbody></table>');
  }
  if (brief.questions.length) {
    parts.push('<h2>Frequently asked questions</h2>');
    for (const question of brief.questions) parts.push(`<h3>${esc(question)}</h3>`, '<p>Answer in two or three sentences.</p>');
  }
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${esc(brief.title)}</title><meta name="description" content="${esc(`${brief.h1}. Replace this with a 140 to 160 character summary.`)}"></head><body><main>${parts.join('')}</main></body></html>`;
}

function slugOf(text) {
  return String(text || 'new-page').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'new-page';
}

/* Opens the editor on a new page filled from the brief, the way the editor's own "Start a new page" does. */
export function startFromBrief(brief) {
  const R = S.report;
  const taken = new Set((R.pages || []).map((item) => looseKey(item.url)));
  let url = `https://${R.site}/${slugOf(brief.q)}/`;
  if (taken.has(looseKey(url))) url = `https://${R.site}/new-${slugOf(brief.q)}/`;
  const draft = draftFor(url);
  if (!draft.touched) Object.assign(draft, { text: normalize(briefHtml(brief)), url, base: null, loaded: '', touched: true, failed: '' });
  closeSheet(true);
  go('editor', { page: url, q: brief.q });
}

/* The brief tile in the search sheet. */
function briefTile(brief, row) {
  const tile = h('div', { class: 'tile' },
    h('div', { class: 'tile-head' }, h('h2', { text: row.url ? 'Brief for a better page' : 'Brief for a new page' }), h('span', { class: 'aside', text: plural(brief.based_on.length, 'page') })),
    h('p', { class: 'reason gap-bottom', text: `Built from the ${plural(brief.based_on.length, 'page')} that rank for this search. A starting point, not a guarantee.` }));
  const facts = [['Title', brief.title], ['H1', brief.h1]];
  if (brief.words) facts.push(['Length', `${brief.words.low} to ${brief.words.high} words`]);
  if (brief.elements.length) facts.push(['Has', brief.elements.map((item) => `${item.label} (${item.pages} of ${item.of})`).join(', ')]);
  tile.append(h('dl', { class: 'facts' }, facts.map(([k, v]) => [h('dt', { text: k }), h('dd', { text: v })])));
  if (brief.sections.length) {
    tile.append(h('div', { class: 'tile-k', text: 'Sections' }), h('div', { class: 'rows gap-bottom' }, brief.sections.map((section) => h('div', { class: 'srcline' },
      h('span', { class: 'one-line', text: section.heading }), h('span', { class: 'count', text: `${section.pages} of ${section.of}` })))));
  }
  if (brief.questions.length) tile.append(h('div', { class: 'tile-k', text: 'Questions people ask' }), h('div', { class: 'rows gap-bottom' }, brief.questions.map((question) => h('div', { class: 'srcline', text: question }))));
  if (brief.terms.length) tile.append(h('div', { class: 'chips gap-bottom' }, brief.terms.map((term) => h('span', { class: 'chip chip-gray', text: term.term }))));
  if (!S.publicDemo) tile.append(h('button', { class: 'btn btn-dark', type: 'button', onClick: () => startFromBrief(brief) }, icon('plus'), 'Start a new page from this brief'));
  return tile;
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
