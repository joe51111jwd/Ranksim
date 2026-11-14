/* Competitors: the businesses that compete for the site's searches (profiles with Track and Untrack), the directories
   and review sites whose lists rank, other sites in the top 10, and the content gaps per search. */

import { S, api, h, icon, clear, main, enc, plural, initial, bar, toast, page, footer, noSearchData, reloadSite, openSheet, pathOf } from '../core.js';
import { registerScreen } from '../hooks.js';
import { hostPath, openSearch } from './searches.js';

const SECOND_LEVEL = new Set(['co', 'com', 'org', 'net', 'ac', 'gov', 'edu', 'ne', 'or', 'go', 'gob']);
const KIND_LABEL = { directory: 'Directory', marketplace: 'Marketplace', forum: 'Forum', social: 'Social', video: 'Video', platform: 'Platform', publisher: 'Publisher' };

/* The registrable domain, as the server groups it: help.hcss.com gives hcss.com. */
export function baseDomain(domain) {
  const parts = String(domain || '').toLowerCase().replace(/^www\./, '').split('.');
  if (parts.length >= 3 && SECOND_LEVEL.has(parts[parts.length - 2]) && parts[parts.length - 1].length === 2) return parts.slice(-3).join('.');
  return parts.slice(-2).join('.');
}

function isTracked(domain) {
  const want = baseDomain(domain);
  return (S.report.competitors || []).some((item) => baseDomain(item) === want);
}

function trackButton(domain) {
  if (S.publicDemo) return null;
  const tracked = isTracked(domain);
  return h('button', {
    class: 'btn',
    type: 'button',
    onClick: async (event) => {
      event.stopPropagation();
      const button = event.currentTarget;
      button.disabled = true;
      const want = baseDomain(domain);
      const list = tracked ? S.report.competitors.filter((item) => baseDomain(item) !== want) : S.report.competitors.concat([domain]);
      try {
        await api.post(`/api/v1/projects/${enc(S.slug)}/settings`, { competitors: list });
        await reloadSite();
        renderCompetitors();
      } catch (err) {
        toast(err.message);
        button.disabled = false;
      }
    },
  }, tracked ? 'Untrack' : 'Track');
}

function stats(row) {
  return h('div', { class: 'm' },
    h('span', { text: plural(row.searches, 'search', 'searches') }),
    h('span', { text: `avg #${row.average}` }),
    h('span', { text: `best #${row.best}` }));
}

function profileCard(row, most) {
  const tracked = isTracked(row.domain);
  return h('div', { class: 'rowcard comp' },
    h('span', { class: `fav${tracked ? ' dark' : ''}`, text: initial(row.domain) }),
    h('button', { class: 'body linkish', type: 'button', onClick: () => openProfile(row) },
      h('div', { class: 't' }, h('span', { text: row.domain }), tracked ? h('span', { class: 'label label-you', text: 'Tracked' }) : null),
      stats(row),
      h('div', { class: 'barwrap' }, bar(row.visibility / most, tracked ? 'dark' : 'gray'))),
    h('div', { class: 'right' }, trackButton(row.domain)));
}

function siteRow(row) {
  return h('button', { class: 'rowcard site', type: 'button', onClick: () => openProfile(row) },
    h('span', { class: 'fav', text: initial(row.domain) }),
    h('div', { class: 'body' },
      h('div', { class: 't' }, h('span', { text: row.domain }), h('span', { class: 'label label-low', text: KIND_LABEL[row.kind] || row.kind })),
      stats(row)),
    icon('next', 'go'));
}

export function renderCompetitors() {
  const R = S.report;
  const profiles = R.competitor_profiles;
  const callout = noSearchData();
  if (!profiles) return renderShare(R, callout);
  const comps = profiles.competitors || [];
  const most = Math.max(1, ...comps.map((row) => row.visibility || 0));
  const you = (R.share || []).find((row) => row.you);
  const tiles = [callout];
  tiles.push(h('section', { class: 'tile span-4' },
    h('div', { class: 'tile-head' }, h('h2', { text: 'Competitors' }), h('span', { class: 'aside', text: you ? `You: ${plural(you.searches, 'search', 'searches')}, avg #${you.average}` : '' })),
    comps.length ? h('div', { class: 'rows' }, comps.map((row) => profileCard(row, most))) : h('div', { class: 'empty-note', text: 'Competitors appear once rankings are pulled.' })));
  const directories = profiles.directories || [];
  if (directories.length) {
    tiles.push(h('section', { class: 'tile span-2' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Directories and review sites' })),
      h('p', { class: 'tile-sub gap-bottom', text: 'Their lists rank for your searches. Get listed.' }),
      h('div', { class: 'rows' }, directories.map(siteRow))));
  }
  const others = (profiles.platforms || []).concat(profiles.publishers || []);
  if (others.length) {
    tiles.push(h('section', { class: `tile ${directories.length ? 'span-2' : 'span-4'}` },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Other sites in the top 10' })),
      h('div', { class: 'rows' }, others.map(siteRow))));
  }
  const gaps = (R.gaps || []).filter((gap) => (gap.topics || []).length || (gap.terms || []).length || (gap.schema || []).length);
  if (gaps.length) {
    tiles.push(h('section', { class: 'tile span-4' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Content gaps' }), h('span', { class: 'aside', text: 'What the pages above you have' })),
      h('div', { class: 'rows' }, gaps.map((gap) => {
        const row = R.rankings.find((item) => item.q === gap.q);
        const parts = [gap.topics.length ? plural(gap.topics.length, 'section') : null, gap.terms.length ? plural(gap.terms.length, 'word') : null, gap.schema.length ? plural(gap.schema.length, 'data type') : null].filter(Boolean);
        return h('button', { class: 'rowcard', type: 'button', onClick: () => (row ? openSearch(row) : null) },
          h('div', { class: 'body' }, h('div', { class: 't', text: gap.q }), h('div', { class: 'm' }, h('span', { text: parts.join(', ') }), gap.page ? h('span', { text: pathOf(gap.page) }) : null)),
          icon('next', 'go'));
      }))));
  }
  clear(main).append(page('Competitors', 'Who takes the top 10 for your searches.', null, h('div', { class: 'bento' }, tiles)), footer());
}

/* Reports from before profiles existed: the share list. */
function renderShare(R, callout) {
  const most = Math.max(1, ...R.share.map((row) => row.slots));
  const rows = h('div', { class: 'rows' }, R.share.map((row) => h('div', { class: 'rowcard' },
    h('span', { class: `fav${row.you ? ' you' : ''}`, text: initial(row.domain) }),
    h('div', { class: 'body' }, h('div', { class: 't', text: row.you ? `${row.domain} (you)` : row.domain }), stats(row), h('div', { class: 'barwrap' }, bar(row.slots / most, 'gray'))),
    row.you || row.platform ? null : h('div', { class: 'right' }, trackButton(row.domain)))));
  if (!R.share.length) rows.append(h('div', { class: 'empty-note', text: 'Competitors appear once rankings are pulled.' }));
  clear(main).append(page('Competitors', 'Who takes the top 10 for your searches.', null, h('div', { class: 'bento' }, callout, h('section', { class: 'tile span-4' }, rows))), footer());
}

function pct(value) {
  return `${Math.round((value || 0) * 100)}%`;
}

/* The sheet for one site: what it ranks for, its best pages and how it writes them. */
export function openProfile(row) {
  openSheet((sheet, closeBtn) => {
    sheet.append(h('div', { class: 'sheet-head' }, h('div', null, h('h2', { text: row.domain }), h('div', { class: 'sub', text: row.reason || '' })), closeBtn));
    const body = h('div', { class: 'sheet-body' });
    body.append(h('div', { class: 'sgrid three' },
      h('div', { class: 'tile white' }, h('div', { class: 'tile-k', text: 'Searches' }), h('div', { class: 'num', text: String(row.searches) })),
      h('div', { class: 'tile white' }, h('div', { class: 'tile-k', text: 'Average' }), h('div', { class: 'num', text: `#${row.average}` })),
      h('div', { class: 'tile white' }, h('div', { class: 'tile-k', text: 'Best' }), h('div', { class: 'num', text: `#${row.best}` }))));
    body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Ranks for' })),
      h('div', { class: 'rows' }, (row.rankings || []).map((hit) => h('div', { class: 'rankline' },
        h('span', { class: 'pos', text: String(hit.position) }),
        h('div', { class: 'body' }, h('div', { class: 't', text: hit.q }), h('div', { class: 'm one-line', title: hit.url, text: hit.title || hostPath(hit.url) })))))));
    if ((row.best_pages || []).length) {
      body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Best pages' })),
        h('div', { class: 'rows' }, row.best_pages.map((item) => h('a', { class: 'rowcard', href: item.url, target: '_blank', rel: 'noopener noreferrer' },
          h('div', { class: 'body' }, h('div', { class: 't one-line', text: item.title || hostPath(item.url) }),
            h('div', { class: 'm' }, h('span', { text: hostPath(item.url) }), h('span', { text: plural(item.searches.length, 'search', 'searches') }), item.words ? h('span', { text: `${item.words} words` }) : null)),
          icon('out', 'go'))))));
    }
    const titles = row.titles || {};
    const words = row.words;
    const facts = [];
    if (titles.count) facts.push(['Title length', `${titles.average_length} characters`], ['Name in title', pct(titles.brand_in_title)], ['Year in title', pct(titles.with_year)], ['List titles', pct(titles.listicle)]);
    if (titles.separator) facts.push(['Separator', titles.separator]);
    if (words) facts.push(['Words', words.pages > 1 ? `${words.median} median (${words.min} to ${words.max})` : String(words.median)]);
    if (row.h2_median != null) facts.push(['Sections', `${row.h2_median} per page`]);
    if (facts.length) {
      body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: 'How they write' }), h('span', { class: 'aside', text: row.pages_stored ? `${row.pages_stored} of ${row.pages_ranking} pages read` : 'Pages not read yet' })),
        h('dl', { class: 'facts' }, facts.map(([k, v]) => [h('dt', { text: k }), h('dd', { text: v })])),
        (titles.common_words || []).length ? h('div', { class: 'chips' }, titles.common_words.map((word) => h('span', { class: 'chip chip-gray', text: word }))) : null));
    }
    if ((row.schema || []).length) {
      body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Structured data' })),
        h('div', { class: 'chips' }, row.schema.map((item) => h('span', { class: 'chip chip-gray', text: `${item.type} ${item.pages}` })))));
    }
    if (row.kind === 'competitor') body.append(h('div', { class: 'form-actions' }, trackButton(row.domain)));
    sheet.append(body);
  });
}

registerScreen('competitors', { label: 'Competitors', order: 40, render: () => renderCompetitors() });

