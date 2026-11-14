/* Settings: refresh the data, Search Console, the searches and competitors lists, server plugins, and delete. Every
   section comes from registerSettingsSection, so a plugin can add one or replace a built-in one by its key. */

import { S, api, stream, h, icon, clear, main, enc, bar, toast, navigate, page, footer, pulledText, loadProjects, reloadSite, shell, plural } from '../core.js';
import { registerScreen, registerSettingsSection, settingsSections, renderAll } from '../hooks.js';

export function renderSettings() {
  const R = S.report;
  const ctx = { report: R, project: S.project, slug: S.slug, reload: reloadSite, rerender: () => renderSettings() };
  const grid = h('div', { class: 'bento' }, renderAll(settingsSections(), ctx));
  clear(main).append(page('Settings', R.site, null, grid), footer());
}

registerScreen('settings', { label: 'Settings', nav: false, order: 90, render: () => renderSettings() });

registerSettingsSection({
  key: 'refresh',
  order: 10,
  render({ report: R }) {
    const steps = h('div', { class: 'steps' });
    const refreshBtn = h('button', { class: 'btn btn-dark', type: 'button' }, icon('refresh'), 'Refresh now');
    refreshBtn.addEventListener('click', () => refresh(refreshBtn, steps));
    return h('section', { class: 'tile span-2' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Refresh data' }), h('span', { class: 'aside', text: pulledText('Last pull ') || (R.search_data ? '' : 'Not pulled yet') })),
      h('p', { class: 'tile-sub', text: R.google_ready ? 'Pulls the live top 10 for every search, reads new or changed pages, and retrains the model.' : 'Search data isn\'t connected on this server yet. Add SERPER_API_KEY once and this pulls the live top 10 for every search.' }),
      h('div', { class: 'result-actions' }, refreshBtn), steps);
  },
});

registerSettingsSection({
  key: 'gsc',
  order: 20,
  render() {
    return h('section', { class: 'tile span-2' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Google Search Console' }), h('button', { class: 'toggle', type: 'button', role: 'switch', 'aria-checked': 'false', disabled: true, 'aria-label': 'Connect Google Search Console' })),
      h('p', { class: 'tile-sub', text: 'Adds your real clicks, impressions and positions. Coming next.' }));
  },
});

registerSettingsSection({
  key: 'searches',
  order: 30,
  render({ report: R }) {
    const qArea = h('textarea', { class: 'field', rows: '8', value: R.rankings.map((row) => row.q).join('\n') });
    const qSave = h('button', { class: 'btn', type: 'button' }, 'Save searches');
    qSave.addEventListener('click', async () => {
      qSave.disabled = true;
      try {
        await api.post(`/api/v1/projects/${enc(S.slug)}/settings`, { queries: qArea.value });
        await reloadSite();
        toast('Saved. Refresh to get rankings for new searches.');
      } catch (err) {
        toast(err.message);
      }
      qSave.disabled = false;
    });
    return h('section', { class: 'tile span-2' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Searches' }), h('span', { class: 'aside', text: 'Found automatically' })), h('div', { class: 'form' }, qArea, h('span', { class: 'form-help', text: 'One per line. Ranksim found these from your pages; add or remove any.' }), h('div', { class: 'form-actions' }, qSave)));
  },
});

registerSettingsSection({
  key: 'competitors',
  order: 40,
  render({ report: R }) {
    const cArea = h('textarea', { class: 'field', rows: '8', spellcheck: 'false', value: (R.competitors || []).join('\n'), placeholder: 'competitor.com' });
    const cSave = h('button', { class: 'btn', type: 'button' }, 'Save competitors');
    cSave.addEventListener('click', async () => {
      cSave.disabled = true;
      try {
        await api.post(`/api/v1/projects/${enc(S.slug)}/settings`, { competitors: cArea.value });
        await reloadSite();
        toast('Competitors saved.');
      } catch (err) {
        toast(err.message);
      }
      cSave.disabled = false;
    });
    return h('section', { class: 'tile span-2' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Competitors' }), h('span', { class: 'aside', text: 'Found automatically' })), h('div', { class: 'form' }, cArea, h('span', { class: 'form-help', text: 'Picked from who takes the most top 10 spots. Edit if we got one wrong.' }), h('div', { class: 'form-actions' }, cSave)));
  },
});

/* Server plugins: GET /api/v1/plugins lists every plugin with the dict its settings_schema() returns, and the plugins
   that failed to load. hooks.js documents the schema fields shown here. Asked once per page load; the list changes
   only with a deploy. A server without the route (404) shows nothing. */
let pluginList = null;

function loadPlugins() {
  if (!pluginList) {
    pluginList = api.get('/api/v1/plugins').catch((err) => {
      if (err.status !== 404) pluginList = null; // try again next time
      return null;
    });
  }
  return pluginList;
}

function schemaValue(value) {
  if (value == null || value === '') return 'Not set';
  if (typeof value === 'boolean') return value ? 'On' : 'Off';
  if (Array.isArray(value)) return value.map(String).join(', ') || 'None';
  if (typeof value === 'object') return null;
  return String(value);
}

/* The rows a schema describes: schema.fields ([{ key, label, value, help }]) or JSON Schema properties. */
function schemaFields(schema) {
  const text = (value) => (typeof value === 'string' ? value : '');
  if (Array.isArray(schema.fields)) {
    return schema.fields.filter((field) => field && typeof field === 'object').map((field) => ({
      label: text(field.label) || text(field.title) || text(field.key),
      value: schemaValue('value' in field ? field.value : field.default),
      help: text(field.help) || text(field.description),
    }));
  }
  if (schema.properties && typeof schema.properties === 'object') {
    return Object.entries(schema.properties).filter(([, prop]) => prop && typeof prop === 'object').map(([key, prop]) => ({
      label: text(prop.title) || key,
      value: schemaValue('value' in prop ? prop.value : prop.default),
      help: text(prop.description),
    }));
  }
  return [];
}

function pluginRow(item) {
  const schema = item.schema;
  const fields = schemaFields(schema).filter((field) => field.label && field.value != null);
  return h('div', { class: 'rowcard' }, h('div', { class: 'body' },
    h('div', { class: 't', text: (typeof schema.title === 'string' && schema.title) || item.name }),
    typeof schema.description === 'string' && schema.description ? h('div', { class: 'c', text: schema.description }) : null,
    fields.map((field) => h('div', { class: 'm' }, h('span', { text: field.label }), h('span', { class: 'label label-plain', text: field.value }), field.help ? h('span', { text: field.help }) : null))));
}

registerSettingsSection({
  key: 'plugins',
  order: 80,
  render() {
    const tile = h('section', { class: 'tile span-4', hidden: true });
    loadPlugins().then((info) => {
      if (!info) return;
      const own = new Set(settingsSections().map((entry) => entry.key));
      const shown = (info.plugins || []).filter((item) => item && item.schema && typeof item.schema === 'object' && !own.has(item.name));
      const failed = (info.failed || []).filter((item) => item && item.name);
      if (!shown.length && !failed.length) return;
      const list = h('div', { class: 'form' }, shown.map(pluginRow), failed.map((item) => h('div', { class: 'rowcard' }, h('div', { class: 'body' },
        h('div', { class: 't', text: item.name }),
        h('div', { class: 'm' }, h('span', { class: 'label label-high', text: 'Did not load' })),
        item.error ? h('div', { class: 'c', text: String(item.error) }) : null))));
      tile.append(h('div', { class: 'tile-head' }, h('h2', { text: 'Plugins' }), h('span', { class: 'aside', text: plural(shown.length + failed.length, 'plugin') })), list);
      tile.hidden = false;
    });
    return tile;
  },
});

registerSettingsSection({
  key: 'delete',
  order: 90,
  render({ slug }) {
    const confirmField = h('input', { class: 'field', placeholder: slug, autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Type the site name to confirm' });
    const del = h('button', { class: 'btn btn-danger', type: 'button', disabled: true }, 'Delete this site');
    confirmField.addEventListener('input', () => {
      del.disabled = confirmField.value.trim() !== S.slug;
    });
    del.addEventListener('click', async () => {
      del.disabled = true;
      try {
        await api.post(`/api/v1/projects/${enc(S.slug)}/delete`, { confirm: confirmField.value.trim() });
        await loadProjects();
        toast('Site deleted.');
        navigate('/');
      } catch (err) {
        toast(err.message);
        del.disabled = false;
      }
    });
    return h('section', { class: 'tile span-4' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Delete' })), h('div', { class: 'form' }, h('span', { class: 'form-help', text: `Type ${slug} to delete this site and its tests.` }), confirmField, h('div', { class: 'form-actions' }, del)));
  },
});

async function refresh(button, steps) {
  button.disabled = true;
  clear(button).append(h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Refreshing');
  clear(steps);
  const rows = new Map();
  const labels = { google: 'Checking Google', crawl: 'Reading the pages that rank', train: 'Training your model', store: 'Saving' };
  let current = null;
  let report = null;
  let failed = '';
  const show = (key, message, done, total) => {
    if (!labels[key]) return;
    if (current && current !== key) {
      const prev = rows.get(current);
      if (prev) {
        prev.className = 'step done';
        clear(prev.querySelector('.box')).append(icon('check'));
      }
    }
    current = key;
    if (!rows.has(key)) {
      const row = h('div', { class: 'step active' }, h('span', { class: 'box' }, h('span', { class: 'spinner', 'aria-hidden': 'true' })), h('div', { class: 'body' }, h('div', { class: 't', text: labels[key] }), h('div', { class: 'c' }), h('div', { class: 'extra' })));
      rows.set(key, row);
      steps.append(row);
    }
    const row = rows.get(key);
    row.querySelector('.c').textContent = message || '';
    const extra = clear(row.querySelector('.extra'));
    if (total) extra.append(bar(done / total));
  };
  try {
    await stream(`/api/v1/projects/${enc(S.slug)}/pull`, { google: true }, (event) => {
      if (event.type === 'progress') show(event.step === 'save' ? 'store' : event.step, event.message, event.done, event.total);
      else if (event.type === 'done') report = event.report;
      else if (event.type === 'error') failed = event.error;
    });
  } catch (err) {
    failed = err.message;
  }
  for (const row of rows.values()) {
    row.className = `step ${report ? 'done' : 'failed'}`;
    clear(row.querySelector('.box')).append(icon(report ? 'check' : 'x'));
  }
  button.disabled = false;
  clear(button).append(icon('refresh'), 'Refresh now');
  if (!report) {
    toast(failed || 'The refresh stopped.');
    return;
  }
  toast(`Refreshed in ${Math.round(report.seconds || 0)} s.`);
  await reloadSite();
  shell.renderBar();
}
