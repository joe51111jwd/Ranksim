/* Overview: one bento of tiles. Every tile, built in or from a plugin, comes from registerOverviewTile. */

import { S, h, icon, clear, main, plural, pct, bar, ring, link, page, footer, pulledText, noSearchData } from '../core.js';
import { registerOverviewTile, registerScreen, overviewTiles, renderAll } from '../hooks.js';
import { issueRow } from './issues.js';

export function renderOverview() {
  const R = S.report;
  const K = R.kpis;
  const grid = h('div', { class: 'bento' }, renderAll(overviewTiles(), R, { report: R, project: S.project, slug: S.slug }));
  clear(main).append(page(R.name, `${R.site} · ${plural(K.searches, 'search', 'searches')} tracked${pulledText(' · Google results from ')}`, null, grid), footer());
}

registerScreen('overview', { label: 'Overview', order: 10, render: () => renderOverview() });

registerOverviewTile({ key: 'search-data', order: 0, render: () => noSearchData() });

registerOverviewTile({
  key: 'first-page',
  order: 10,
  render(R) {
    const K = R.kpis;
    const squares = h('div', { class: 'squares', 'aria-hidden': 'true' }, R.rankings.map((row) => h('i', { class: row.position ? (row.position <= 3 ? 'top' : 'on') : null, title: `${row.q}: ${row.position ? `#${row.position}` : 'not in top 10'}` })));
    return h('section', { class: 'tile white span-2' },
      h('div', { class: 'tile-k', text: 'On the first page of Google' }),
      h('div', { class: 'tile-big' }, R.search_data ? String(K.top10) : '?', h('small', { text: `of ${plural(K.searches, 'search', 'searches')}` })),
      squares,
      h('div', { class: 'legend' }, h('span', { class: 'l-top', text: 'Top 3' }), h('span', { class: 'l-on', text: 'Top 10' }), h('span', { text: 'Not in top 10' })));
  },
});

registerOverviewTile({
  key: 'average',
  order: 20,
  render(R) {
    const K = R.kpis;
    return h('section', { class: 'tile black' },
      h('div', { class: 'tile-k', text: 'Average position' }),
      h('div', { class: 'tile-big' }, K.average != null ? `#${K.average}` : 'None'),
      h('div', { class: 'tile-sub', text: K.top10 ? `Across the ${plural(K.top10, 'search', 'searches')} you rank for` : 'You are not in the top 10 yet' }));
  },
});

registerOverviewTile({
  key: 'backtest',
  order: 30,
  render(_R, ctx) {
    const P = ctx.project || {};
    const acc = P.model && P.model.pairwise != null ? P.model.pairwise : null;
    return h('section', { class: 'tile' },
      h('div', { class: 'tile-k', text: 'Same-day backtest' }),
      ring(acc, acc != null ? pct(acc) : 'n/a', 'pairs right'),
      h('div', { class: 'tile-sub', text: acc != null ? 'Each search left out of training, same Google snapshot. 50% is a coin flip. Not forward accuracy.' : 'Appears once rankings are pulled.' }));
  },
});

registerOverviewTile({
  key: 'issues',
  order: 40,
  render(R) {
    const issues = h('div', { class: 'rows' });
    for (const issue of R.issues.slice(0, 6)) issues.append(issueRow(issue, true));
    if (!R.issues.length) issues.append(h('div', { class: 'empty-note', text: 'No issues found on the pages we read.' }));
    return h('section', { class: 'tile span-2 row-2' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Top issues' }), link('issues', `All ${R.issues.length}`, { class: 'btn btn-plain' })),
      issues);
  },
});

registerOverviewTile({
  key: 'rankings',
  order: 50,
  render(R) {
    const ranks = h('div', { class: 'bars' });
    const sorted = R.rankings.slice().sort((a, b) => (a.position || 99) - (b.position || 99));
    for (const row of sorted.slice(0, 8)) {
      ranks.append(h('div', { class: `barrow${row.position ? ' you' : ''}` },
        h('div', { class: 'top-line' }, h('span', { class: 'name', text: row.q }), h('span', { class: 'val', text: row.position ? `#${row.position}` : row.checked ? 'Not in top 10' : 'Not checked' })),
        bar(row.position ? (11 - row.position) / 10 : 0.02, row.position && row.position <= 3 ? 'dark' : null)));
    }
    return h('section', { class: 'tile span-2' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Where you rank' }), link('searches', 'All searches', { class: 'btn btn-plain' })),
      ranks);
  },
});

registerOverviewTile({
  key: 'share',
  order: 60,
  render(R) {
    const comp = h('div', { class: 'bars' });
    const rivals = R.share.filter((row) => !row.you).slice(0, 6);
    const most = Math.max(1, ...R.share.map((row) => row.slots));
    const mine = R.share.find((row) => row.you);
    if (mine) comp.append(h('div', { class: 'barrow you' }, h('div', { class: 'top-line' }, h('span', { class: 'name', text: `${mine.domain} (you)` }), h('span', { class: 'val', text: `${mine.slots} spots, avg #${mine.average}` })), bar(mine.slots / most)));
    for (const row of rivals) {
      comp.append(h('div', { class: 'barrow' },
        h('div', { class: 'top-line' }, h('span', { class: 'name', text: row.domain }), h('span', { class: 'val', text: `${row.slots} spots, avg #${row.average}` })),
        bar(row.slots / most, row.competitor ? 'dark' : 'gray')));
    }
    return h('section', { class: 'tile span-2' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Who takes the top 10' }), link('competitors', 'Competitors', { class: 'btn btn-plain' })),
      rivals.length || mine ? comp : h('div', { class: 'empty-note', text: 'Competitors appear once rankings are pulled.' }));
  },
});

registerOverviewTile({
  key: 'priority',
  order: 70,
  render(R) {
    const sev = { high: 0, medium: 0, low: 0 };
    for (const issue of R.issues) sev[issue.severity] += 1;
    const worst = Math.max(1, sev.high, sev.medium, sev.low);
    return h('section', { class: 'tile' },
      h('div', { class: 'tile-k', text: 'Issues by priority' }),
      h('div', { class: 'bars', style: null },
        [['high', 'High', 'dark'], ['medium', 'Medium', null], ['low', 'Low', 'gray']].map(([key, label, cls]) => h('div', { class: 'barrow' },
          h('div', { class: 'top-line' }, h('span', { class: 'name', text: label }), h('span', { class: 'val', text: String(sev[key]) })),
          bar(sev[key] / worst, cls)))));
  },
});

registerOverviewTile({
  key: 'competitors',
  order: 80,
  render(R) {
    const tracked = (R.competitors || []).length;
    return h('section', { class: 'tile' },
      h('div', { class: 'tile-k', text: 'Competitors tracked' }),
      h('div', { class: 'tile-big' }, String(tracked)),
      h('div', { class: 'tile-sub', text: tracked ? R.competitors.slice(0, 3).join(', ') : 'Found automatically once rankings are pulled' }));
  },
});

registerOverviewTile({
  key: 'pages',
  order: 90,
  render(R) {
    return h('section', { class: 'tile white' },
      h('div', { class: 'tile-k', text: 'Pages read' }),
      h('div', { class: 'tile-big' }, String(R.pages_read)),
      h('div', { class: 'tile-sub', text: R.pages_failed ? `${R.pages_failed} couldn't be read` : 'All readable' }));
  },
});

registerOverviewTile({
  key: 'try',
  order: 100,
  render() {
    return h('section', { class: 'tile black' },
      h('div', { class: 'tile-k', text: 'Try a change' }),
      h('div', { class: 'tile-sub', text: 'Edit any page right in the preview and see where it would land before you publish.' }),
      h('div', { class: 'result-actions' }, link('editor', ['Open editor', icon('arrow')], { class: 'btn btn-primary' })));
  },
});
