/* Issues: every issue on the site's pages, grouped by page, with a sheet that says what to do. */

import { S, h, icon, clear, main, plural, pathOf, go, page, footer, noSearchData, openSheet, closeSheet, serpList, SEVERITY } from '../core.js';
import { registerScreen } from '../hooks.js';

/* One issue as a row button that opens its sheet. compact: one line of comment and no labels (Overview). */
export function issueRow(issue, compact) {
  const meta = compact ? null : h('div', { class: 'm' },
    h('span', { class: `label label-${issue.severity}`, text: SEVERITY[issue.severity] }),
    issue.search ? h('span', { text: `"${issue.search}"` }) : null);
  return h('button', { class: 'rowcard', type: 'button', onClick: () => openIssue(issue) },
    h('span', { class: `dot ${issue.severity}` }),
    h('div', { class: 'body' }, h('div', { class: 't', text: issue.title }), h('div', { class: `c${compact ? ' one-line' : ''}`, text: issue.comment }), meta),
    icon('next', 'go'));
}

export function renderIssues() {
  const R = S.report;
  const filters = h('div', { class: 'seg', role: 'group', 'aria-label': 'Filter issues' });
  const counts = { all: R.issues.length, high: 0, medium: 0, low: 0 };
  for (const issue of R.issues) counts[issue.severity] += 1;
  for (const [key, label] of [['all', 'All'], ['high', 'High'], ['medium', 'Medium'], ['low', 'Low']]) {
    filters.append(h('button', {
      type: 'button',
      'aria-pressed': String(S.issueFilter === key),
      onClick: () => {
        S.issueFilter = key;
        renderIssues();
      },
    }, `${label} ${counts[key]}`));
  }
  const shown = R.issues.filter((issue) => S.issueFilter === 'all' || issue.severity === S.issueFilter);
  const groups = new Map();
  for (const issue of shown) {
    const key = issue.page ? pathOf(issue.page) : 'Searches without a page';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(issue);
  }
  const body = h('div', { class: 'bento' });
  const callout = noSearchData();
  if (callout) body.append(callout);
  if (!shown.length) body.append(h('div', { class: 'empty-note span-4', text: 'Nothing here.' }));
  for (const [where, list] of groups) {
    body.append(h('section', { class: 'tile span-2' },
      h('div', { class: 'tile-head' }, h('h2', { class: 'one-line', text: where }), h('span', { class: 'aside', text: plural(list.length, 'issue') })),
      h('div', { class: 'rows' }, list.map((issue) => issueRow(issue, false)))));
  }
  clear(main).append(page('Issues', `${counts.high} high, ${counts.medium} medium and ${counts.low} low on ${R.site}`, filters, body), footer());
}

export function openIssue(issue) {
  openSheet((sheet, closeBtn) => {
    sheet.append(h('div', { class: 'sheet-head' }, h('div', null, h('h2', { text: issue.title }), h('div', { class: 'sub', text: [issue.page ? pathOf(issue.page) : null, issue.search ? `"${issue.search}"` : null].filter(Boolean).join(' · ') })), closeBtn));
    const body = h('div', { class: 'sheet-body' });
    body.append(h('div', { class: 'tile white' }, h('span', { class: `label label-${issue.severity}`, text: `${SEVERITY[issue.severity]} priority` }), h('p', { class: 'reason', text: issue.comment })));
    if (issue.fix) body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-k', text: 'What to do' }), h('p', { class: 'reason', text: issue.fix })));
    if (issue.search) {
      const row = S.report.rankings.find((item) => item.q === issue.search);
      if (row && row.top.length) body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: `Top results for "${issue.search}"` })), serpList(row.top.slice(0, 5))));
    }
    if (issue.page || issue.search) {
      body.append(h('div', { class: 'form-actions' }, h('button', {
        class: 'btn btn-primary btn-large',
        type: 'button',
        onClick: () => {
          closeSheet(true);
          const params = {};
          if (issue.search) params.q = issue.search;
          if (issue.page) params.page = issue.page;
          go('editor', params);
        },
      }, icon('pencil'), issue.page ? 'Fix it in the editor' : 'Write this page in the editor')));
    }
    sheet.append(body);
  });
}

registerScreen('issues', { label: 'Issues', order: 20, render: () => renderIssues() });
