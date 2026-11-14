/* Overview: one bento of tiles. Every tile, built in or from a plugin, comes from registerOverviewTile.
   Charts are inline SVG from ../charts.js, redrawn at the tile's real width. Every number comes from the report.

   Built-in keys and orders (hooks.js lists them for plugins): search-data 0, first-page 10, average 20,
   backtest 30, issues 40, opportunities 45, rankings 50, (55 is left for rank history), share 60, predicted 65,
   priority 70, competitors 80, pages 90, try 100. */

import { S, h, icon, clear, main, plural, pct, bar, ring, link, go, page, footer, pulledText, noSearchData, openSheet, closeSheet, serpList, pathOf } from '../core.js';
import { registerOverviewTile, registerScreen, overviewTiles, renderAll } from '../hooks.js';
import { fit, rankDots, positionColumns, shareBars, predictedVsReal, opportunities } from '../charts.js';
import { issueRow } from './issues.js';

export function renderOverview() {
  const R = S.report;
  const K = R.kpis;
  const grid = h('div', { class: 'bento bento-dense' }, renderAll(overviewTiles(), R, { report: R, project: S.project, slug: S.slug }));
  clear(main).append(page(R.name, `${R.site} · ${plural(K.searches, 'search', 'searches')}${pulledText(' · Google results from ')}`, null, grid), footer());
}

registerScreen('overview', { label: 'Overview', order: 10, render: () => renderOverview() });

/* A host that redraws one chart at its own width. */
function chart(draw, cls) {
  const host = h('div', { class: `chart${cls ? ` ${cls}` : ''}` });
  fit(host, draw);
  return host;
}

function head(title, action) {
  return h('div', { class: 'tile-head' }, h('h2', { text: title }), action || null);
}

/* The top 10 for one search, in a sheet, with your page marked and an editor link on every result. */
export function openSerp(row) {
  openSheet((sheet, closeBtn) => {
    const pred = row.predicted && row.predicted.position != null ? row.predicted : null;
    const status = row.position ? `You are #${row.position} on Google` : row.checked ? 'You are not in the top 10' : 'Not checked yet';
    sheet.append(h('div', { class: 'sheet-head' }, h('div', null, h('h2', { text: row.q }), h('div', { class: 'sub', text: pred ? `${status} · PREDICTED #${pred.position} of ${pred.of}` : status })), closeBtn));
    const body = h('div', { class: 'sheet-body' });
    if (row.top && row.top.length) body.append(serpList(row.top, { q: row.q }));
    else body.append(h('div', { class: 'empty-note', text: row.checked ? 'Google returned no results for this search.' : 'Refresh the site to pull Google results for this search.' }));
    body.append(h('div', { class: 'form-actions' }, h('button', {
      class: 'btn btn-primary btn-large',
      type: 'button',
      onClick: () => {
        closeSheet(true);
        go('editor', row.url ? { q: row.q, page: row.url } : { q: row.q });
      },
    }, icon('pencil'), row.url ? 'Improve your page' : 'Write a page for it')));
    sheet.append(body);
  });
}

registerOverviewTile({ key: 'search-data', order: 0, render: () => noSearchData() });

registerOverviewTile({
  key: 'first-page',
  order: 10,
  render(R) {
    const K = R.kpis;
    return h('section', { class: 'tile white span-2', 'aria-label': 'On the first page of Google' },
      h('div', { class: 'tile-k', text: 'On the first page of Google' }),
      h('div', { class: 'tile-big' }, R.search_data ? String(K.top10) : '0', h('small', { text: `of ${plural(K.searches, 'search', 'searches')}` })),
      R.search_data ? chart((width) => positionColumns(R.rankings, { width, height: 150 }), 'chart-cols') : h('div', { class: 'tile-sub', text: 'Positions appear once Google results are pulled.' }));
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
      h('div', { class: 'tile-sub', text: K.top10 ? `Across ${plural(K.top10, 'search', 'searches')} in the top 10.${K.top3 ? ` ${K.top3} in the top 3.` : ''}` : 'Not in the top 10 yet.' }));
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
      ring(acc, acc != null ? pct(acc) : 'None', 'pairs right'),
      h('div', { class: 'tile-sub', text: acc != null ? 'Searches left out of training, same snapshot. 50% is a coin flip. Not forward accuracy.' : 'Appears once rankings are pulled.' }));
  },
});

registerOverviewTile({
  key: 'issues',
  order: 40,
  render(R) {
    const issues = h('div', { class: 'rows' });
    for (const issue of R.issues.slice(0, 5)) issues.append(issueRow(issue, true));
    if (!R.issues.length) issues.append(h('div', { class: 'empty-note', text: 'No issues on the pages we read.' }));
    return h('section', { class: 'tile span-2' },
      head('Top issues', R.issues.length ? link('issues', `All ${R.issues.length}`, { class: 'btn btn-plain' }) : null),
      issues);
  },
});

registerOverviewTile({
  key: 'opportunities',
  order: 45,
  render(R) {
    const list = opportunities(R.rankings, { limit: 5 });
    const rows = h('div', { class: 'rows' });
    for (const item of list) {
      const row = R.rankings.find((r) => r.q === item.q) || item;
      rows.append(h('button', { class: 'rowcard opp', type: 'button', onClick: () => openSerp(row) },
        h('span', { class: `opp-pos${item.position ? '' : ' none'}`, text: item.position ? `#${item.position}` : 'New' }),
        h('div', { class: 'body' },
          h('div', { class: 't one-line', text: item.q }),
          h('div', { class: 'c', text: item.kind === 'move' ? (item.gap === 1 ? 'One place from the top 3' : `${item.gap} places from the top 3`) : 'No page targets this search' })),
        icon('next', 'go')));
    }
    if (!list.length) rows.append(h('div', { class: 'empty-note', text: R.search_data ? 'Nothing close to moving right now.' : 'Appears once Google results are pulled.' }));
    return h('section', { class: 'tile white span-2' }, head('Biggest opportunities', link('searches', 'All searches', { class: 'btn btn-plain' })), rows);
  },
});

registerOverviewTile({
  key: 'rankings',
  order: 50,
  render(R) {
    const hasPred = R.rankings.some((row) => row.predicted && row.predicted.position != null);
    return h('section', { class: 'tile white span-2' },
      head('Where you rank', link('searches', 'All searches', { class: 'btn btn-plain' })),
      R.rankings.length ? chart((width) => rankDots(R.rankings, { width }), 'chart-dots') : h('div', { class: 'empty-note', text: 'No searches yet. Add some in Settings.' }),
      R.rankings.length ? h('div', { class: 'legend' },
        h('span', { class: 'l-on', text: 'Google position' }),
        hasPred ? h('span', { class: 'l-ring', text: 'PREDICTED' }) : null,
        h('span', { text: 'Out of the top 10' })) : null);
  },
});

registerOverviewTile({
  key: 'share',
  order: 60,
  render(R) {
    const has = R.share.some((row) => row.slots > 0);
    return h('section', { class: 'tile span-2' },
      head('Who takes the top 10', link('competitors', 'Competitors', { class: 'btn btn-plain' })),
      has ? chart((width) => shareBars(R.share, { width, limit: 8 }), 'chart-share') : h('div', { class: 'empty-note', text: 'Appears once Google results are pulled.' }),
      has ? h('div', { class: 'legend' }, h('span', { class: 'l-on', text: 'You' }), h('span', { class: 'l-top', text: 'Tracked' }), h('span', { text: 'Other sites' })) : null);
  },
});

registerOverviewTile({
  key: 'predicted',
  order: 65,
  render(R) {
    const both = R.rankings.filter((row) => row.position && row.predicted && row.predicted.position != null);
    if (!both.length) return null;
    return h('section', { class: 'tile white' },
      h('div', { class: 'tile-k', text: 'PREDICTED versus real' }),
      chart((width) => predictedVsReal(R.rankings, { width: Math.min(width, 280), height: 220 }), 'chart-scatter'),
      h('p', { class: 'tile-sub', text: 'On the line means the model agrees with Google.' }));
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
      h('div', { class: 'bars' },
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
      h('div', { class: 'tile-sub', text: tracked ? R.competitors.slice(0, 3).join(', ') : 'Found once rankings are pulled.' }));
  },
});

registerOverviewTile({
  key: 'pages',
  order: 90,
  render(R) {
    const failed = (R.pages || []).filter((p) => p.status !== 'fetched');
    return h('section', { class: 'tile white' },
      h('div', { class: 'tile-k', text: 'Pages read' }),
      h('div', { class: 'tile-big' }, String(R.pages_read)),
      h('div', { class: 'tile-sub', text: R.pages_failed ? `${R.pages_failed} could not be read${failed[0] ? `, like ${pathOf(failed[0].url)}` : ''}.` : 'All readable.' }));
  },
});

registerOverviewTile({
  key: 'try',
  order: 100,
  render() {
    return h('section', { class: 'tile black' },
      h('div', { class: 'tile-k', text: 'Try a change' }),
      h('div', { class: 'tile-sub', text: 'Edit any page and see where it would land before you publish.' }),
      h('div', { class: 'result-actions' }, link('editor', ['Open editor', icon('arrow')], { class: 'btn btn-primary' })));
  },
});
