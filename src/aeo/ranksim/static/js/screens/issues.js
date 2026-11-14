/* Issues: every issue on the site's pages and searches. Filter by priority, page and search, group by page or by
   search, and open one to see the exact element, the comment, the fix, a ready-to-paste suggestion and a button
   that opens the editor on that element. */

import { S, h, icon, clear, main, plural, pathOf, looseKey, go, page, footer, noSearchData, openSheet, closeSheet, serpList, toast, SEVERITY } from '../core.js';
import { registerScreen } from '../hooks.js';

const VIEW = { page: '', search: '', group: 'page' };

const pagesOf = (issue) => (issue.pages && issue.pages.length ? issue.pages : issue.page ? [issue.page] : []);
const onPage = (issue, url) => pagesOf(issue).some((item) => looseKey(item) === looseKey(url));

/* Where "Fix in editor" goes: the issue's page, its search, and the element to focus. */
export function editorParams(issue, url) {
  const params = {};
  const target = url || (VIEW.page && onPage(issue, VIEW.page) ? VIEW.page : pagesOf(issue)[0]);
  if (target) params.page = target;
  if (issue.search) params.q = issue.search;
  if (target && issue.element && issue.element.selector && !/^(head\b|title\b|meta\b|link\b)/.test(issue.element.selector)) params.focus = issue.element.selector;
  return params;
}

/* Opens the editor on the issue's element. The token tells the annotations plugin this is a fresh click, so the
   element is selected again even when the same address was opened before. */
export function fixInEditor(issue, url) {
  const params = editorParams(issue, url);
  try {
    if (params.focus) sessionStorage.setItem('rs-audit-focus', JSON.stringify({ selector: params.focus, page: params.page, at: Date.now() }));
  } catch (_err) {
    // storage can be off; the focus param still works the first time
  }
  closeSheet(true);
  go('editor', params);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Copied.');
  } catch (_err) {
    toast('Copy did not work here. Select the text and copy it.');
  }
}

/* One issue as a row button that opens its sheet. compact: one line of comment and no labels (Overview). */
export function issueRow(issue, compact) {
  const many = pagesOf(issue).length > 1;
  const meta = compact ? null : h('div', { class: 'm' },
    h('span', { class: `label label-${issue.severity}`, text: SEVERITY[issue.severity] }),
    issue.search ? h('span', { text: `"${issue.search}"` }) : null,
    many ? h('span', { text: plural(pagesOf(issue).length, 'page') }) : null,
    issue.element && issue.element.tag ? h('span', { class: 'issue-tag', text: `<${issue.element.tag}>` }) : null,
    issue.suggestion ? h('span', { class: 'issue-has-fix', text: 'Suggestion ready' }) : null);
  return h('button', { class: 'rowcard', type: 'button', onClick: () => openIssue(issue) },
    h('span', { class: `dot ${issue.severity}` }),
    h('div', { class: 'body' }, h('div', { class: 't', text: issue.title }), h('div', { class: `c${compact ? ' one-line' : ''}`, text: issue.comment }), meta),
    icon('next', 'go'));
}

function select(label, value, options, onChange) {
  const el = h('select', { 'aria-label': label },
    options.map(([key, text]) => h('option', { value: key, text, selected: key === value })));
  el.addEventListener('change', () => onChange(el.value));
  return h('label', { class: 'select issue-select' }, el, icon('chevron', 'chev'));
}

function seg(label, items, value, onPick) {
  const box = h('div', { class: 'seg', role: 'group', 'aria-label': label });
  for (const [key, text] of items) {
    box.append(h('button', { type: 'button', 'aria-pressed': String(value === key), onClick: () => onPick(key) }, text));
  }
  return box;
}

export function renderIssues() {
  const R = S.report;
  const all = R.issues || [];
  const linked = S.params.get('page') || '';
  if (linked !== (VIEW.linked || '')) {
    VIEW.linked = linked; // a new ?page= in the address sets the page filter once
    if (linked) VIEW.page = linked;
  }
  const counts = { all: all.length, high: 0, medium: 0, low: 0 };
  for (const issue of all) counts[issue.severity] += 1;

  const pageUrls = [];
  for (const issue of all) for (const url of pagesOf(issue)) if (!pageUrls.some((item) => looseKey(item) === looseKey(url))) pageUrls.push(url);
  pageUrls.sort((a, b) => pathOf(a).localeCompare(pathOf(b)));
  const searches = [...new Set(all.map((issue) => issue.search).filter(Boolean))].sort();
  if (VIEW.page && !pageUrls.some((url) => looseKey(url) === looseKey(VIEW.page))) VIEW.page = '';
  if (VIEW.search && !searches.includes(VIEW.search)) VIEW.search = '';

  const shown = all.filter((issue) => (S.issueFilter === 'all' || issue.severity === S.issueFilter)
    && (!VIEW.page || onPage(issue, VIEW.page))
    && (!VIEW.search || issue.search === VIEW.search));

  const controls = h('div', { class: 'issue-controls' },
    seg('Filter by priority', [['all', `All ${counts.all}`], ['high', `High ${counts.high}`], ['medium', `Medium ${counts.medium}`], ['low', `Low ${counts.low}`]], S.issueFilter, (key) => { S.issueFilter = key; renderIssues(); }),
    pageUrls.length > 1 ? select('Page', VIEW.page, [['', 'Every page'], ...pageUrls.map((url) => [url, pathOf(url)])], (value) => { VIEW.page = value; renderIssues(); }) : null,
    searches.length ? select('Search', VIEW.search, [['', 'Every search'], ...searches.map((q) => [q, q])], (value) => { VIEW.search = value; renderIssues(); }) : null,
    seg('Group issues', [['page', 'By page'], ['search', 'By search']], VIEW.group, (key) => { VIEW.group = key; renderIssues(); }));

  const groups = new Map();
  for (const issue of shown) {
    let keys;
    if (VIEW.group === 'search') keys = [issue.search ? `"${issue.search}"` : 'Not tied to a search'];
    else keys = issue.pages && issue.pages.length > 1 ? ['On several pages'] : [issue.page ? pathOf(issue.page) : 'Searches without a page'];
    for (const key of keys) {
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(issue);
    }
  }
  const rank = (list) => list.reduce((sum, issue) => sum + (issue.severity === 'high' ? 1000 : 0) + (issue.score || 0), 0);
  const ordered = [...groups.entries()].sort((a, b) => rank(b[1]) - rank(a[1]));

  const body = h('div', { class: 'bento' });
  const callout = noSearchData();
  if (callout) body.append(callout);
  if (!shown.length) body.append(h('div', { class: 'empty-note span-4', text: all.length ? 'No issues match these filters.' : 'No issues found.' }));
  for (const [where, list] of ordered) {
    const high = list.filter((issue) => issue.severity === 'high').length;
    body.append(h('section', { class: 'tile span-2' },
      h('div', { class: 'tile-head' }, h('h2', { class: 'one-line', text: where }), h('span', { class: 'aside', text: high ? `${plural(list.length, 'issue')}, ${high} high` : plural(list.length, 'issue') })),
      h('div', { class: 'rows' }, list.map((issue) => issueRow(issue, false)))));
  }
  clear(main).append(page('Issues', `${counts.high} high, ${counts.medium} medium and ${counts.low} low on ${R.site}`, controls, body), footer());
}

function elementBox(issue) {
  const el = issue.element;
  if (!el) return null;
  const text = el.attr ? `${el.attr}="${el.text}"` : el.text;
  return h('div', { class: 'tile white issue-element' },
    h('div', { class: 'tile-k', text: 'The element' }),
    h('div', { class: 'issue-snippet' },
      h('span', { class: 'issue-tag', text: `<${el.tag || 'element'}>` }),
      text ? h('span', { class: 'issue-snippet-text', text }) : h('span', { class: 'issue-snippet-text muted', text: 'Whole section' })),
    h('code', { class: 'issue-selector', text: el.selector }));
}

const ACTION_HELP = {
  text: 'Replaces the element\'s text.',
  attr: 'Sets the attribute shown above.',
  retag: 'Changes the heading level and keeps its text.',
  append: 'Goes at the end of the element.',
  prepend: 'Goes at the start of the element.',
  after: 'Goes right after the element.',
  copy: 'Copy it and use it where it belongs.',
};

export function suggestionBox(issue) {
  if (!issue.suggestion) return null;
  const shown = issue.action === 'retag' ? `<${issue.suggestion}>` : issue.suggestion;
  return h('div', { class: 'tile issue-suggestion' },
    h('div', { class: 'tile-head' }, h('div', { class: 'tile-k', text: 'Suggestion' }), h('button', { class: 'btn btn-plain', type: 'button', onClick: () => copyText(shown) }, icon('doc'), 'Copy')),
    h('pre', { class: 'issue-suggestion-text', text: shown }),
    ACTION_HELP[issue.action] ? h('p', { class: 'tile-sub', text: ACTION_HELP[issue.action] }) : null);
}

export function openIssue(issue) {
  const pages = pagesOf(issue);
  openSheet((sheet, closeBtn) => {
    sheet.append(h('div', { class: 'sheet-head' }, h('div', null, h('h2', { text: issue.title }), h('div', { class: 'sub', text: [pages.length > 1 ? plural(pages.length, 'page') : pages[0] ? pathOf(pages[0]) : null, issue.search ? `"${issue.search}"` : null].filter(Boolean).join(' · ') })), closeBtn));
    const body = h('div', { class: 'sheet-body' });
    body.append(h('div', { class: 'tile white' }, h('span', { class: `label label-${issue.severity}`, text: `${SEVERITY[issue.severity]} priority` }), h('p', { class: 'reason', text: issue.comment })));
    const box = elementBox(issue);
    if (box) body.append(box);
    if (issue.fix) body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-k', text: 'What to do' }), h('p', { class: 'reason', text: issue.fix })));
    const suggestion = suggestionBox(issue);
    if (suggestion) body.append(suggestion);
    if (issue.evidence && issue.evidence.length) {
      body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-k', text: 'Details' }), h('ul', { class: 'issue-evidence' }, issue.evidence.slice(0, 8).map((line) => h('li', { text: line })))));
    }
    if (pages.length > 1) {
      body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-k', text: 'Pages' }), h('div', { class: 'rows' }, pages.slice(0, 12).map((url) => h('button', {
        class: 'rowcard',
        type: 'button',
        onClick: () => fixInEditor(issue, url),
      }, h('div', { class: 'body' }, h('div', { class: 't one-line', text: pathOf(url) })), icon('pencil', 'go'))))));
    }
    if (issue.search) {
      const row = (S.report.rankings || []).find((item) => item.q === issue.search);
      if (row && row.top.length) body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: `Top results for "${issue.search}"` })), serpList(row.top.slice(0, 5))));
    }
    if (pages.length || issue.search) {
      body.append(h('div', { class: 'form-actions' }, h('button', {
        class: 'btn btn-primary btn-large',
        type: 'button',
        onClick: () => fixInEditor(issue),
      }, icon('pencil'), pages.length ? 'Fix in editor' : 'Write this page in the editor')));
    }
    sheet.append(body);
  });
}

registerScreen('issues', { label: 'Issues', order: 20, render: () => renderIssues() });
