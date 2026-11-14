/* Competitors: every site in the top 10 across the searches, with Track and Untrack. */

import { S, api, h, clear, main, enc, plural, initial, bar, toast, page, footer, noSearchData, reloadSite } from '../core.js';
import { registerScreen } from '../hooks.js';

export function renderCompetitors() {
  const R = S.report;
  const most = Math.max(1, ...R.share.map((row) => row.slots));
  const rows = h('div', { class: 'rows' });
  for (const row of R.share) {
    const toggle = row.you || row.platform || S.publicDemo ? null : h('button', {
      class: 'btn',
      type: 'button',
      onClick: async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        const list = row.competitor ? R.competitors.filter((d) => d !== row.domain) : R.competitors.concat([row.domain]);
        try {
          await api.post(`/api/v1/projects/${enc(S.slug)}/settings`, { competitors: list });
          await reloadSite();
          renderCompetitors();
        } catch (err) {
          toast(err.message);
          button.disabled = false;
        }
      },
    }, row.competitor ? 'Untrack' : 'Track');
    rows.append(h('div', { class: 'rowcard' },
      h('span', { class: `fav${row.you ? ' you' : row.competitor ? ' dark' : ''}`, text: initial(row.domain) }),
      h('div', { class: 'body' },
        h('div', { class: 't', text: row.you ? `${row.domain} (you)` : row.domain }),
        h('div', { class: 'm' }, h('span', { text: `${row.slots} top 10 spots` }), h('span', { text: plural(row.searches, 'search', 'searches') }), h('span', { text: `best #${row.best}` }), h('span', { text: `avg #${row.average}` }),
          row.platform ? h('span', { class: 'label label-low', text: 'Platform' }) : row.competitor ? h('span', { class: 'label label-you', text: 'Tracked' }) : null),
        h('div', { class: 'barwrap' }, bar(row.slots / most, row.you ? null : row.competitor ? 'dark' : 'gray'))),
      toggle ? h('div', { class: 'right' }, toggle) : null));
  }
  if (!R.share.length) rows.append(h('div', { class: 'empty-note', text: 'Competitors appear once rankings are pulled.' }));
  const callout = noSearchData();
  clear(main).append(page('Competitors', 'Every site in the top 10 across your searches. Ranksim tracks the ones that compete with you; platforms like YouTube are listed but not tracked.', null,
    callout ? h('div', { class: 'bento gap-bottom' }, callout) : null,
    h('section', { class: 'tile' }, rows)), footer());
}

registerScreen('competitors', { label: 'Competitors', order: 40, render: () => renderCompetitors() });
