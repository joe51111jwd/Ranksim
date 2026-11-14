// Client delivery for the owner: share links, the print view, CSV files, agency branding, issue plans, change log.
// Server side: plugins/share.py. The client page itself lives in static/share/ and never loads this file.
import { registerSettingsSection, registerOverviewTile } from '../hooks.js';
import { h, api, enc, toast, link, CSRF, MODE } from '../core.js';

const STATUSES = [['open', 'Not started'], ['planned', 'Planned'], ['doing', 'In progress'], ['done', 'Done']];
const EXPIRY = [['', 'Never expires'], ['7', 'Expires in 7 days'], ['30', 'Expires in 30 days'], ['90', 'Expires in 90 days']];

function day(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? String(iso).slice(0, 10) : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function fullUrl(path) {
  return location.origin + path;
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Link copied.');
  } catch (_err) {
    window.prompt('Copy this link', text);
  }
}

function busy(button, run) {
  return async (event) => {
    if (event) event.preventDefault();
    button.disabled = true;
    try {
      await run();
    } catch (err) {
      toast(err.message || 'That did not work. Try again.');
    } finally {
      button.disabled = false;
    }
  };
}

function statusChip(status) {
  if (status === 'active') return h('span', { class: 'chip chip-green', text: 'Active' });
  if (status === 'expired') return h('span', { class: 'chip chip-gray', text: 'Expired' });
  return h('span', { class: 'chip chip-gray', text: 'Turned off' });
}

/* ------------------------------------------------------------------ weekly update email */

// Copies the weekly update as rich text (HTML) and plain text, so it pastes cleanly into Gmail. The HTML is only
// placed on the clipboard; it is never put into this page.
export async function copyWeeklyUpdate(slug) {
  const data = await api.get(`/api/v1/projects/${enc(slug)}/weekly-update`);
  const full = `Subject: ${data.subject}\n\n${data.text}`;
  try {
    if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
      await navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([data.html], { type: 'text/html' }),
        'text/plain': new Blob([data.text], { type: 'text/plain' }),
      })]);
    } else {
      await navigator.clipboard.writeText(full);
    }
    toast(data.has_link ? 'Weekly update copied. Paste it into your email.' : 'Weekly update copied. Make a link to include the full report.');
  } catch (_err) {
    window.prompt('Copy the weekly update', full);
  }
  return data;
}

/* ------------------------------------------------------------------ client report links */

function weeklyButton(slug) {
  const button = h('button', { class: 'btn btn-primary', type: 'button' }, 'Copy weekly update');
  button.addEventListener('click', busy(button, () => copyWeeklyUpdate(slug)));
  return button;
}

function linksSection({ slug }) {
  const list = h('div', { class: 'share-list' }, h('p', { class: 'tile-sub', text: 'Loading links.' }));
  const label = h('input', { class: 'field', type: 'text', maxlength: '80', placeholder: 'Who is it for (only you see this)', 'aria-label': 'Link label' });
  const expiry = h('select', { class: 'field share-select', 'aria-label': 'Link expiry' }, EXPIRY.map(([value, text]) => h('option', { value, text })));
  const create = h('button', { class: 'btn btn-dark', type: 'button' }, 'Create link');
  const readOnly = MODE === 'public';

  function paintLinks(links) {
    list.replaceChildren();
    if (!links.length) {
      list.append(h('p', { class: 'tile-sub', text: 'No links yet. A link opens a read only report with no login.' }));
      return;
    }
    for (const link of links) {
      const url = fullUrl(link.path);
      const off = h('button', { class: 'btn btn-plain btn-danger', type: 'button' }, 'Turn off');
      off.addEventListener('click', busy(off, async () => {
        if (!window.confirm('Turn off this link? Anyone who has it loses access.')) return;
        const data = await api.post(`/api/v1/projects/${enc(slug)}/share/${enc(link.id)}/revoke`);
        paintLinks(data.links);
        toast('Link turned off.');
      }));
      const active = link.status === 'active';
      const meta = [`Made ${day(link.created)}`, link.expires ? `${active ? 'expires' : 'expired'} ${day(link.expires)}` : 'no expiry', link.last_used ? `last opened ${day(link.last_used)}` : 'not opened yet'];
      list.append(h('div', { class: 'share-row' + (active ? '' : ' is-off') },
        h('div', { class: 'share-row-main' },
          h('div', { class: 'share-row-top' }, h('strong', { text: link.label || 'Client link' }), statusChip(link.status)),
          active ? h('code', { class: 'share-url', text: url }) : null,
          h('span', { class: 'tile-sub', text: meta.join(', ') })),
        active ? h('div', { class: 'share-row-actions' },
          h('button', { class: 'btn', type: 'button', onclick: () => copy(url) }, 'Copy'),
          h('a', { class: 'btn btn-plain', href: link.path, target: '_blank', rel: 'noopener noreferrer' }, 'Open'),
          off) : null));
    }
  }

  async function load() {
    try {
      const data = await api.get(`/api/v1/projects/${enc(slug)}/share`);
      paintLinks(data.links || []);
    } catch (err) {
      list.replaceChildren(h('p', { class: 'form-error', text: err.message }));
    }
  }

  create.addEventListener('click', busy(create, async () => {
    const body = { label: label.value.trim() };
    if (expiry.value) body.expires_days = Number(expiry.value);
    const link = await api.post(`/api/v1/projects/${enc(slug)}/share`, body);
    label.value = '';
    await load();
    copy(fullUrl(link.path));
  }));

  load();
  return h('section', { class: 'tile span-4 share-tile' },
    h('div', { class: 'tile-head' }, h('h2', { text: 'Client report' }),
      h('div', { class: 'share-head-actions' },
        weeklyButton(slug),
        h('a', { class: 'btn', href: `/report/${enc(slug)}`, target: '_blank', rel: 'noopener' }, 'Preview and print'),
        h('a', { class: 'btn btn-plain', href: `/api/v1/projects/${enc(slug)}/export/rankings.csv`, download: '' }, 'Rankings CSV'),
        h('a', { class: 'btn btn-plain', href: `/api/v1/projects/${enc(slug)}/export/issues.csv`, download: '' }, 'Issues CSV'))),
    h('p', { class: 'tile-sub', text: 'Share a read only report: positions with their Google data date, issues and your plan, competitors and recent work. No editor, settings or other sites.' }),
    readOnly ? h('p', { class: 'tile-sub', text: 'The public demo cannot make links.' }) : h('div', { class: 'share-create' }, label, expiry, create),
    list);
}

/* ------------------------------------------------------------------ agency branding */

function brandingSection() {
  const name = h('input', { class: 'field', type: 'text', maxlength: '80', placeholder: 'Agency name', 'aria-label': 'Agency name' });
  const save = h('button', { class: 'btn', type: 'button' }, 'Save name');
  const preview = h('div', { class: 'share-logo' });
  const file = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp', class: 'sr-only', id: 'share-logo-file' });
  const pick = h('label', { class: 'btn', for: 'share-logo-file' }, 'Upload logo');
  const remove = h('button', { class: 'btn btn-plain btn-danger', type: 'button' }, 'Remove logo');

  function paint(brand) {
    name.value = brand.agency || '';
    preview.replaceChildren(brand.logo
      ? h('img', { src: `/api/v1/branding/logo?t=${Date.now()}`, alt: 'Agency logo' })
      : h('span', { class: 'tile-sub', text: 'No logo yet' }));
    remove.hidden = !brand.logo;
  }

  save.addEventListener('click', busy(save, async () => {
    paint(await api.post('/api/v1/branding', { agency: name.value }));
    toast('Agency name saved.');
  }));
  remove.addEventListener('click', busy(remove, async () => {
    paint(await api.post('/api/v1/branding/logo/delete'));
    toast('Logo removed.');
  }));
  file.addEventListener('change', async () => {
    const chosen = file.files && file.files[0];
    file.value = '';
    if (!chosen) return;
    if (chosen.size > 200 * 1024) { toast('Keep the logo under 200 KB.'); return; }
    try {
      const response = await fetch('/api/v1/branding/logo', {
        method: 'POST', credentials: 'same-origin', body: chosen,
        headers: { 'content-type': chosen.type || 'application/octet-stream', 'x-ranksim-csrf': CSRF, accept: 'application/json' },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'That logo could not be saved.');
      paint(data);
      toast('Logo saved.');
    } catch (err) {
      toast(err.message);
    }
  });

  api.get('/api/v1/branding').then(paint).catch(() => {});
  return h('section', { class: 'tile span-2' },
    h('div', { class: 'tile-head' }, h('h2', { text: 'Agency branding' })),
    h('p', { class: 'tile-sub', text: 'Shown on client reports and printouts. PNG, JPEG or WebP, up to 200 KB.' }),
    h('div', { class: 'share-create' }, name, save),
    h('div', { class: 'share-brand' }, preview, h('div', { class: 'share-row-actions' }, pick, file, remove)));
}

/* ------------------------------------------------------------------ issue plans */

function plansSection({ slug, report }) {
  const issues = (report && report.issues) || [];
  const body = h('div', { class: 'share-list' });

  async function load() {
    let plans = {};
    try {
      plans = (await api.get(`/api/v1/projects/${enc(slug)}/issue-plans`)).plans || {};
    } catch (_err) {
      plans = {};
    }
    body.replaceChildren();
    if (!issues.length) {
      body.append(h('p', { class: 'tile-sub', text: 'No issues found.' }));
      return;
    }
    for (const issue of issues.slice(0, 40)) {
      const plan = plans[issue.id] || {};
      const status = h('select', { class: 'field share-select', 'aria-label': `Status for ${issue.title}` },
        STATUSES.map(([value, text]) => h('option', { value, text, selected: (plan.status || 'open') === value })));
      const note = h('input', { class: 'field', type: 'text', maxlength: '400', value: plan.plan || '', placeholder: 'What you are doing (the client sees this)', 'aria-label': `Plan for ${issue.title}` });
      const save = h('button', { class: 'btn', type: 'button' }, 'Save');
      save.addEventListener('click', busy(save, async () => {
        await api.post(`/api/v1/projects/${enc(slug)}/issues/${enc(issue.id)}/plan`, { status: status.value, plan: note.value, title: issue.title });
        toast(status.value === 'done' ? 'Marked done and added to the change log.' : 'Plan saved.');
      }));
      body.append(h('div', { class: 'share-row share-plan' },
        h('div', { class: 'share-row-main' },
          h('div', { class: 'share-row-top' }, h('span', { class: `label label-${issue.severity}`, text: issue.severity }), h('strong', { text: issue.title }))),
        h('div', { class: 'share-plan-edit' }, status, note, save)));
    }
  }

  load();
  return h('section', { class: 'tile span-4' },
    h('div', { class: 'tile-head' }, h('h2', { text: 'What we are doing' })),
    h('p', { class: 'tile-sub', text: 'Set a status and a short plan per issue. Clients see both. Marking one Done adds it to the change log.' }),
    body);
}

/* ------------------------------------------------------------------ change log */

function logSection({ slug }) {
  const list = h('ol', { class: 'share-log' });
  const text = h('input', { class: 'field', type: 'text', maxlength: '200', placeholder: 'Add a note, for example "Rewrote the pricing page"', 'aria-label': 'Change log note' });
  const shown = h('input', { type: 'checkbox', id: 'share-note-public' });
  const add = h('button', { class: 'btn', type: 'button' }, 'Add');

  function paint(entries) {
    list.replaceChildren();
    if (!entries.length) {
      list.append(h('li', { class: 'tile-sub', text: 'Nothing logged yet. Analyses, Google data, tests, published edits and resolved issues appear here.' }));
      return;
    }
    for (const entry of entries.slice(0, 50)) {
      list.append(h('li', { class: 'share-log-row' },
        h('span', { class: 'tile-sub', text: day(entry.at) }),
        h('span', { class: 'chip chip-gray', text: entry.label }),
        h('span', { class: 'share-log-text' }, h('span', { text: entry.summary }), entry.detail ? h('span', { class: 'tile-sub', text: entry.detail }) : null),
        h('span', { class: entry.public ? 'chip' : 'chip chip-gray', text: entry.public ? 'Client sees' : 'Only you' })));
    }
  }

  add.addEventListener('click', busy(add, async () => {
    if (!text.value.trim()) { toast('Write a short note first.'); return; }
    const data = await api.post(`/api/v1/projects/${enc(slug)}/changelog`, { summary: text.value, public: shown.checked });
    text.value = '';
    paint(data.entries || []);
  }));

  api.get(`/api/v1/projects/${enc(slug)}/changelog`).then((data) => paint(data.entries || [])).catch((err) => list.replaceChildren(h('li', { class: 'form-error', text: err.message })));
  return h('section', { class: 'tile span-4' },
    h('div', { class: 'tile-head' }, h('h2', { text: 'Change log' })),
    h('p', { class: 'tile-sub', text: 'Clients see the entries marked Client sees, in short form.' }),
    MODE === 'public' ? null : h('div', { class: 'share-create' }, text, h('label', { class: 'share-check', for: 'share-note-public' }, shown, 'Show to client'), add),
    list);
}

registerSettingsSection({ key: 'share', order: 50, render: linksSection });
registerSettingsSection({ key: 'share-plans', order: 52, render: plansSection });
registerSettingsSection({ key: 'share-log', order: 54, render: logSection });
registerSettingsSection({ key: 'share-brand', order: 56, render: brandingSection });

registerOverviewTile({
  key: 'client-report', // not 'share': that key is the built-in Who takes the top 10 tile
  order: 95,
  render(_report, { slug }) {
    return h('section', { class: 'tile span-2 no-print' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Client report' }), link('settings', 'Share', { class: 'btn btn-plain' })),
      h('p', { class: 'tile-sub', text: 'Share a read only report with your client, or open it to print a PDF.' }),
      h('p', { class: 'tile-sub' }, h('a', { href: `/report/${enc(slug)}`, target: '_blank', rel: 'noopener', text: 'Open the printable report' })));
  },
});
