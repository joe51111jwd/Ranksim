/* Google Search Console: the on/off switch and property picker in Settings, a Search Console column on Searches,
   and an Overview tile. Every number shown is Google Search Console data for the stated date range.
   Server side: src/aeo/ranksim/plugins/gsc.py (routes under /api/v1/gsc). */

import { registerSettingsSection, registerSearchColumn, registerOverviewTile, registerScreen } from '../hooks.js';
import { S, h, icon, api, enc, toast, link, clear, go, page, pathOf, reloadSite } from '../core.js';

const LABEL = 'Google Search Console data';
const fmt = (n) => (n == null ? '' : Number(n).toLocaleString('en-US'));
const pctText = (ctr) => (ctr == null ? '' : `${(ctr * 100).toFixed(ctr >= 0.1 ? 0 : 1)}%`);
const posText = (p) => (p ? Number(p).toFixed(1) : '');

const RESULTS = {
  connected: 'Search Console is connected.',
  needs_property: 'Connected. Pick the property for this site.',
  denied: 'Google sign in was cancelled. Nothing changed.',
  scope_missing: 'Search Console access was not granted. Try again and leave the Search Console box ticked.',
  no_refresh_token: 'Google did not give lasting access. Remove Ranksim under Third party access in your Google account, then connect again.',
  invalid_grant: 'Google did not accept the sign in. Try again.',
  bad_state: 'That sign in link was not valid. Start again here.',
  expired_state: 'That sign in took too long. Start again here.',
  bad_nonce: 'That sign in was started in another browser or tab. Start again here.',
  bad_session: 'That sign in belongs to another session. Start again here.',
  not_configured: 'Search Console is not set up on this server yet.',
  google_error: 'Google answered with an error. Try again.',
  storage: 'Storage did not answer. Try again in a moment.',
  network: 'Google did not answer. Try again in a moment.',
  token_error: 'Google refused the token request. Try again in a moment.',
  client_config: 'Google refused the OAuth client. Check the client ID and secret on the server.',
  no_project: 'That site no longer exists.',
};

function gscOf(report) {
  return (report && report.gsc) || null;
}

function ready(g) {
  return !!g && (g.state === 'ready' || g.state === 'error') && !!g.totals;
}

function rangeText(g) {
  return g && g.range ? g.range.label || `${g.range.start} to ${g.range.end}` : '';
}

/* After Google sends the browser back to /app/<slug>/settings?gsc=<result>: say what happened once, then clean the address. */
async function announceResult() {
  let result = S.params && S.params.get('gsc');
  if (!result) return false;
  S.params.delete('gsc');
  const rest = S.params.toString();
  history.replaceState({}, '', `${location.pathname}${rest ? `?${rest}` : ''}`);
  if (result === 'signin') {
    // Google sent the browser back while signed out. Now signed in: finish the connection it parked.
    try {
      result = (await api.post('/api/v1/gsc/resume', {})).result;
    } catch (err) {
      toast(err.message);
      return true;
    }
  }
  toast(RESULTS[result] || 'Search Console sign in did not finish. Try again.');
  return result === 'connected' || result === 'needs_property';
}

/* ------------------------------------------------------------------ Settings */

async function startConnect(button) {
  if (button) button.disabled = true;
  try {
    const answer = await api.post(`/api/v1/gsc/projects/${enc(S.slug)}/connect`, {});
    location.assign(answer.url);
  } catch (err) {
    toast(err.message);
    if (button) button.disabled = false;
  }
}

function setupCard(status) {
  const setup = status.setup || {};
  const uri = status.redirect_uri || setup.redirect_uri || '';
  const copy = h('button', { class: 'btn gsc-copy', type: 'button' }, icon('doc'), 'Copy');
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(uri);
      toast('Redirect URI copied.');
    } catch (_err) {
      toast(uri);
    }
  });
  return h('div', { class: 'gsc-setup' },
    h('div', { class: 'callout' }, icon('warn'), h('span', null, h('b', { text: 'Not set up yet. ' }), 'The server owner does this once. Then every site can turn Search Console on here.')),
    h('ol', { class: 'gsc-steps' }, (setup.steps || []).map((step) => h('li', { text: step }))),
    uri ? h('div', { class: 'gsc-uri' }, h('span', { class: 'form-help', text: 'Redirect URI for this server' }), h('div', { class: 'gsc-uri-row' }, h('code', { text: uri }), copy)) : null,
    (setup.missing || []).length ? h('div', { class: 'form-help', text: `Missing on the server: ${setup.missing.join(', ')}.` }) : null);
}

async function propertyPicker(status, rerender) {
  const box = h('div', { class: 'gsc-prop' }, h('span', { class: 'form-help', text: 'Loading your Search Console properties' }));
  let listed;
  try {
    listed = await api.get(`/api/v1/gsc/projects/${enc(S.slug)}/properties`);
  } catch (err) {
    clear(box).append(h('div', { class: 'callout' }, icon('warn'), h('span', { text: err.message })));
    return box;
  }
  const usable = (listed.properties || []).filter((item) => item.usable);
  if (!usable.length) {
    clear(box).append(h('div', { class: 'callout' }, icon('warn'), h('span', { text: 'This Google account has no Search Console properties. Add the site in Search Console, or ask the site owner to add this account as a user.' })));
    return box;
  }
  const current = status.property || '';
  const select = h('select', { 'aria-label': 'Search Console property' },
    current ? null : h('option', { value: '', text: 'Pick a property', selected: true }),
    usable.map((item) => h('option', { value: item.url, text: item.kind === 'domain' ? `${item.url.slice(10)} (domain)` : item.url, selected: item.url === current })));
  select.addEventListener('change', async () => {
    if (!select.value) return;
    select.disabled = true;
    try {
      const next = await api.post(`/api/v1/gsc/projects/${enc(S.slug)}`, { property: select.value });
      toast(next.state === 'ready' ? 'Property saved and data loaded.' : next.message || 'Property saved.');
      await rerender(true);
    } catch (err) {
      toast(err.message);
      select.disabled = false;
    }
  });
  const hint = !current && listed.suggested ? h('span', { class: 'form-help', text: `Suggested: ${listed.suggested}` }) : null;
  clear(box).append(...[h('span', { class: 'form-help', text: 'Property' }), h('label', { class: 'select' }, select, icon('chevron', 'chev')), hint].filter(Boolean));
  return box;
}

function totalsRow(g) {
  const t = g.totals || {};
  return h('div', { class: 'gsc-stats' },
    stat(fmt(t.clicks), 'clicks'),
    stat(fmt(t.impressions), 'impressions'),
    stat(pctText(t.ctr), 'CTR'),
    stat(posText(t.position), 'avg position'));
}

function stat(value, label) {
  return h('div', { class: 'gsc-stat' }, h('b', { text: value || '0' }), h('span', { text: label }));
}

function gscSection(ctx) {
  const tile = h('section', { class: 'tile span-2 gsc-tile', 'data-gsc': 'settings' });
  const toggle = h('button', { class: 'toggle', type: 'button', role: 'switch', 'aria-checked': 'false', 'aria-label': 'Google Search Console' });
  const head = h('div', { class: 'tile-head' }, h('h2', { text: 'Google Search Console' }), toggle);
  const body = h('div', { class: 'form gsc-body' }, h('span', { class: 'form-help', text: 'Checking' }));
  tile.append(head, body);

  async function paint(reloadReport) {
    let status;
    try {
      status = await api.get(`/api/v1/gsc/projects/${enc(S.slug)}`);
    } catch (err) {
      clear(body).append(h('div', { class: 'callout' }, icon('warn'), h('span', { text: err.message })));
      return;
    }
    if (reloadReport) await ctx.reload();
    fill(status);
  }

  function fill(status) {
    const st = status.state;
    toggle.setAttribute('aria-checked', String(!!status.enabled && st !== 'not_configured'));
    toggle.disabled = st === 'not_configured' || st === 'not_available';
    toggle.dataset.state = st;
    clear(body);
    if (st === 'not_available') {
      body.append(h('p', { class: 'tile-sub', text: 'Search Console is not available on the public demo.' }));
      return;
    }
    if (st === 'not_configured') {
      body.append(setupCard(status));
      return;
    }
    if (st === 'off') {
      body.append(h('p', { class: 'tile-sub', text: 'Turn it on to add your real clicks, impressions and positions from Google for the last 28 days.' }));
      if (status.connected) body.append(h('span', { class: 'form-help', text: 'Your Google account is already connected.' }));
      return;
    }
    if (st === 'not_connected') {
      const connect = h('button', { class: 'btn btn-dark', type: 'button' }, 'Connect Google');
      connect.addEventListener('click', () => startConnect(connect));
      body.append(h('p', { class: 'tile-sub', text: 'Sign in with the Google account that can see this site in Search Console. Ranksim only reads.' }), h('div', { class: 'form-actions' }, connect));
      return;
    }
    if (st === 'error') body.append(h('div', { class: 'callout' }, icon('warn'), h('span', { text: status.message })));
    if (ready(status)) {
      body.append(h('div', { class: 'gsc-range form-help', text: `${LABEL}, ${rangeText(status)}` }), totalsRow(status));
    } else if (st === 'no_data') {
      body.append(h('p', { class: 'tile-sub', text: 'Connected. Load the data to see clicks and impressions.' }));
    } else if (st === 'needs_property') {
      body.append(h('p', { class: 'tile-sub', text: 'Connected. Pick the Search Console property for this site.' }));
    }
    const pickerSlot = h('div');
    body.append(pickerSlot);
    propertyPicker(status, paint).then((node) => pickerSlot.replaceWith(node));
    const actions = h('div', { class: 'form-actions gsc-actions' });
    if (status.property) {
      const refresh = h('button', { class: 'btn', type: 'button' }, icon('refresh'), 'Refresh now');
      refresh.addEventListener('click', async () => {
        refresh.disabled = true;
        try {
          await api.post(`/api/v1/gsc/projects/${enc(S.slug)}/refresh`, {});
          toast('Search Console data refreshed.');
          await paint(true);
        } catch (err) {
          toast(err.message);
          refresh.disabled = false;
        }
      });
      actions.append(refresh);
    }
    const disconnect = h('button', { class: 'btn', type: 'button' }, 'Disconnect Google');
    disconnect.addEventListener('click', async () => {
      if (!window.confirm('Disconnect Google? Search Console stops for every site until you connect again.')) return;
      disconnect.disabled = true;
      try {
        await api.post('/api/v1/gsc/disconnect', {});
        toast('Google disconnected.');
        await paint(true);
      } catch (err) {
        toast(err.message);
        disconnect.disabled = false;
      }
    });
    actions.append(disconnect);
    body.append(actions);
    if (status.fetched_at) body.append(h('span', { class: 'form-help', text: `Updated ${new Date(status.fetched_at).toLocaleString()}` }));
  }

  toggle.addEventListener('click', async () => {
    const turnOn = toggle.getAttribute('aria-checked') !== 'true';
    toggle.disabled = true;
    try {
      const next = await api.post(`/api/v1/gsc/projects/${enc(S.slug)}`, { enabled: turnOn });
      if (turnOn && next.needs_connect) {
        await startConnect(null);
        return;
      }
      await ctx.reload();
      fill(next);
    } catch (err) {
      toast(err.message);
    }
    toggle.disabled = false;
  });

  announceResult().then((changed) => paint(changed));
  return tile;
}

registerSettingsSection({ key: 'gsc', order: 20, render: (ctx) => gscSection(ctx) });

/* ------------------------------------------------------------------ Searches */

registerSearchColumn({
  key: 'gsc',
  order: 50,
  header: 'Search Console',
  when: (report) => ready(gscOf(report)),
  cell(row) {
    const g = gscOf(S.report);
    if (!ready(g)) return null;
    const hit = (g.searches || {})[row.q];
    const tip = `${LABEL}, ${rangeText(g)}`;
    if (!hit) return h('span', { class: 'gsc-cell none', title: `${tip}. No impressions for this search.`, text: 'No data' });
    const near = nearFor(g, row.q);
    return h('span', { class: 'gsc-cell', title: `${tip}. CTR ${pctText(hit.ctr)}, average position ${posText(hit.position)}.` },
      h('b', { text: `${fmt(hit.clicks)} clicks` }),
      h('small', { text: `${fmt(hit.impressions)} impr.` }),
      near ? h('span', { class: 'gsc-chip', text: 'Near page one' }) : null);
  },
});

/* ------------------------------------------------------------------ Overview */

registerOverviewTile({
  key: 'gsc',
  order: 15,
  render(R) {
    const g = gscOf(R);
    if (!g || g.state === 'not_available') return null;
    if (!ready(g)) {
      const text = {
        not_configured: 'Search Console is not set up yet.',
        off: 'Turn on Search Console to see your real clicks and impressions.',
        not_connected: 'Connect Google to see your real clicks and impressions.',
        needs_property: 'Pick your Search Console property to see clicks and impressions.',
        no_data: 'Search Console data has not loaded yet.',
      }[g.state] || g.message || 'Search Console is not ready.';
      return h('section', { class: 'tile gsc-tile gsc-off', 'data-gsc': 'overview' },
        h('div', { class: 'tile-k', text: 'Google Search Console' }),
        h('div', { class: 'tile-sub', text }),
        h('div', { class: 'gsc-link' }, link('settings', g.state === 'not_configured' ? 'How to set it up' : 'Open Settings')));
    }
    const t = g.totals || {};
    const top = (g.queries || []).slice(0, 5);
    return h('section', { class: 'tile white span-2 gsc-tile', 'data-gsc': 'overview' },
      h('div', { class: 'tile-k', text: `${LABEL}, ${rangeText(g)}` }),
      h('div', { class: 'tile-big' }, fmt(t.clicks), h('small', { text: 'clicks' })),
      h('div', { class: 'gsc-mini' },
        h('span', null, h('b', { text: fmt(t.impressions) }), ' impressions'),
        h('span', null, h('b', { text: pctText(t.ctr) }), ' CTR'),
        h('span', null, h('b', { text: posText(t.position) }), ' avg position')),
      g.state === 'error' ? h('div', { class: 'callout gsc-gap' }, icon('warn'), h('span', { text: g.message })) : null,
      top.length ? h('ol', { class: 'gsc-top' }, top.map((row) => h('li', null,
        h('span', { class: 'gsc-q', text: row.q }),
        h('span', { class: 'gsc-n', text: `${fmt(row.clicks)} clicks, ${fmt(row.impressions)} impr.` })))) : null);
  },
});

/* ------------------------------------------------------------------ Opportunities */

function opps(g) {
  return (g && g.opportunities) || { near_page_one: [], low_ctr: [], rules: {} };
}

function nearFor(g, q) {
  const key = String(q || '').toLowerCase().trim();
  return opps(g).near_page_one.find((row) => row.q.toLowerCase().trim() === key) || null;
}

function editorButton(q, pageUrl, label) {
  const params = {};
  if (q) params.q = q;
  if (pageUrl) params.page = pageUrl;
  return h('button', { class: 'btn gsc-edit', type: 'button', onClick: () => go('editor', params) }, icon('pencil'), label || 'Open in editor');
}

function tracked(q) {
  const key = String(q || '').toLowerCase().trim();
  return ((S.report && S.report.rankings) || []).some((row) => String(row.q).toLowerCase().trim() === key);
}

function trackButton(q) {
  if (tracked(q)) return null;
  const button = h('button', { class: 'btn gsc-track', type: 'button' }, icon('plus'), 'Track');
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const queries = ((S.report && S.report.rankings) || []).map((row) => row.q).concat([q]);
      await api.post(`/api/v1/projects/${enc(S.slug)}/settings`, { queries });
      await reloadSite();
      toast(`Tracking "${q}". Refresh to get its Google rankings.`);
      button.replaceWith(h('span', { class: 'label label-plain', text: 'Tracked' }));
    } catch (err) {
      toast(err.message);
      button.disabled = false;
    }
  });
  return button;
}

function nearRow(row, compact) {
  return h('div', { class: 'rowcard gsc-opp' }, h('div', { class: 'body' },
    h('div', { class: 't', text: row.q }),
    h('div', { class: 'm' },
      h('span', { text: `#${posText(row.position)} on average` }),
      h('span', { text: `${fmt(row.impressions)} impressions` }),
      h('span', { text: `${fmt(row.clicks)} clicks` }),
      row.page ? h('span', { class: 'gsc-path', text: pathOf(row.page) }) : null),
    compact ? null : h('div', { class: 'gsc-opp-actions' }, editorButton(row.q, row.page), trackButton(row.q))),
    compact ? h('div', { class: 'right' }, editorButton(row.q, row.page, 'Edit')) : null);
}

function lowRow(row, compact) {
  return h('div', { class: 'rowcard gsc-opp' }, h('div', { class: 'body' },
    h('div', { class: 't gsc-path', text: pathOf(row.page) }),
    h('div', { class: 'm' },
      h('span', { text: `${pctText(row.ctr)} CTR at #${posText(row.position)}` }),
      h('span', { text: `typical about ${pctText(row.typical_ctr)}` }),
      h('span', { text: `${fmt(row.impressions)} impressions` }),
      row.q ? h('span', { text: `top search: ${row.q}` }) : null),
    compact ? null : h('div', { class: 'gsc-opp-actions' }, editorButton(row.q, row.page, 'Improve title and description'))),
    compact ? h('div', { class: 'right' }, editorButton(row.q, row.page, 'Edit')) : null);
}

registerOverviewTile({
  key: 'gsc-opportunities',
  order: 16,
  render(R) {
    const g = gscOf(R);
    if (!ready(g)) return null;
    const o = opps(g);
    if (!o.near_page_one.length && !o.low_ctr.length) return null;
    return h('section', { class: 'tile span-2 gsc-tile', 'data-gsc': 'opportunities' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Search Console opportunities' }), link('gsc', 'All')),
      h('div', { class: 'tile-k', text: `${LABEL}, ${rangeText(g)}` }),
      o.near_page_one.length ? h('div', { class: 'gsc-group' }, h('div', { class: 'gsc-group-k', text: 'Near page one' }), o.near_page_one.slice(0, 3).map((row) => nearRow(row, true))) : null,
      o.low_ctr.length ? h('div', { class: 'gsc-group' }, h('div', { class: 'gsc-group-k', text: 'Seen but not clicked' }), o.low_ctr.slice(0, 2).map((row) => lowRow(row, true))) : null);
  },
});

registerScreen('gsc', {
  label: 'Search Console',
  nav: false,
  order: 95,
  render() {
    const g = gscOf(S.report);
    if (!ready(g)) {
      return [page('Search Console opportunities', 'Turn on Search Console in Settings to see these.', null,
        h('div', { class: 'bento' }, h('section', { class: 'tile span-4' }, h('p', { class: 'tile-sub', text: (g && g.message) || 'Search Console is not ready for this site.' }), h('div', { class: 'gsc-link' }, link('settings', 'Open Settings')))))];
    }
    const o = opps(g);
    const rules = o.rules || {};
    const near = h('section', { class: 'tile span-2', 'data-gsc': 'near' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Near page one' }), h('span', { class: 'aside', text: String(o.near_page_one.length) })),
      h('p', { class: 'tile-sub gsc-rule', text: rules.near_page_one || '' }),
      o.near_page_one.length ? h('div', { class: 'form' }, o.near_page_one.map((row) => nearRow(row, false))) : h('div', { class: 'empty-note', text: 'None right now.' }));
    const low = h('section', { class: 'tile span-2', 'data-gsc': 'low' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Seen but not clicked' }), h('span', { class: 'aside', text: String(o.low_ctr.length) })),
      h('p', { class: 'tile-sub gsc-rule', text: rules.low_ctr || '' }),
      o.low_ctr.length ? h('div', { class: 'form' }, o.low_ctr.map((row) => lowRow(row, false))) : h('div', { class: 'empty-note', text: 'None right now.' }));
    return [page('Search Console opportunities', `${LABEL}, ${rangeText(g)}`, null, h('div', { class: 'bento' }, near, low))];
  },
});
