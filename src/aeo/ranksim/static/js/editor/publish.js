/* Publish: anyone can edit and test a page; pushing it live needs proof that you own the site and a connection to
   where it is hosted. The sheet shows what will change, walks through ownership and the connection, publishes,
   checks the live page, and keeps a history with rollback. Download and Copy are always there.

   Server routes: /api/v1/publish/platforms and /api/v1/projects/{slug}/publish/... (plugins/publish.py). Secrets go
   to the server once and never come back. */

import { S, h, api, enc, hostOf, pathOf, toast, openSheet, clear, icon, put } from '../core.js';
import { ED, draftFor, syncFromFrame, editorCtx } from './editor.js';

const METHOD_NAMES = { meta: 'meta tag', file: 'file', dns: 'DNS record' };
const VERIFY_TEXT = {
  confirmed: ['label-ok', 'Live. The page shows the change.'],
  partial: ['label-medium', 'Partly live. Some changes do not show on the page yet.'],
  pending: ['label-medium', 'Sent. The live page does not show it yet, often a cache. Check again in a few minutes.'],
  not_live_yet: ['label-low', 'Saved. Not live yet.'],
  unknown: ['label-medium', 'Sent. The live page could not be read to confirm it.'],
  failed: ['label-high', 'Failed.'],
};
const FIELD_HELP = {
  base_url: ['Site address', 'https://example.com'],
  username: ['WordPress username', ''],
  app_password: ['Application password', 'Users, Profile, Application passwords'],
  site_id: ['Site ID', 'Site settings, General'],
  api_token: ['Site API token', 'Site settings, Apps and integrations'],
  shop: ['Store address', 'your-store.myshopify.com'],
  access_token: ['Admin API access token', 'From a custom app with page and content write access'],
  repo: ['Repository', 'owner/name'],
  branch: ['Base branch', 'main'],
  root: ['Site folder', 'Optional, for example public'],
  paths: ['Path mapping', 'Optional, one per line: /pricing=src/pricing.html'],
  token: ['Access token', 'Fine-grained token with contents and pull request write'],
  url: ['Webhook address', 'https://your-server.example/ranksim'],
  signing_secret: ['Signing secret', 'At least 16 characters. Your endpoint checks the signature with it.'],
  content_selector: ['Content area', 'Optional CSS selector for the body, for example .post-body'],
  title_field: ['SEO title field', 'Optional field slug'],
  description_field: ['Description field', 'Optional field slug'],
  body_field: ['Body field', 'Optional field slug'],
};
const REQUIRED = { wordpress: ['base_url', 'username'], webflow: ['site_id'], shopify: ['shop'], github: ['repo'], webhook: ['url'] };
const LONG = new Set(['paths']);

export function publishButton(draft) {
  return h('button', { class: 'btn btn-block', type: 'button', disabled: !draft.text, onClick: () => openPublish(draft) }, 'Publish');
}

/* The edited page to publish: the editor's round trip export (original page plus edits, scripts intact) when the
   editor offers it, else the draft HTML. */
function editedPage(draft) {
  syncFromFrame();
  const ctx = editorCtx();
  const exported = ctx && typeof ctx.exportHtml === 'function' ? ctx.exportHtml() : null;
  return {
    html: exported || (ctx ? ctx.getHtml() : draftFor(ED.refs ? ED.refs.page : draft.url).text) || draft.text,
    roundTrip: !!exported,
    diff: ctx && typeof ctx.diff === 'function' ? ctx.diff() : null,
  };
}

/* Element.append writes null as the text "null"; put() skips it. */
const add = (el, ...kids) => put(el, kids);
const when = (seconds) => (seconds ? new Date(seconds * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '');
const base = () => `/api/v1/projects/${enc(S.slug)}/publish`;

function busy(button, text) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = text;
  return () => {
    button.disabled = false;
    button.textContent = label;
  };
}

async function copyText(text, done) {
  try {
    await navigator.clipboard.writeText(text);
    toast(done);
  } catch (_err) {
    toast('Copy is blocked here. Select the text and copy it.');
  }
}

export function openPublish(draft) {
  const page = editedPage(draft);
  const url = draft.url || (ED.refs && ED.refs.page) || '';
  const domain = hostOf(url);
  openSheet((sheet, closeBtn) => {
    sheet.classList.add('publish-sheet');
    add(sheet, h('div', { class: 'sheet-head' }, h('div', null, h('h2', { text: `Publish to ${domain}` }), h('div', { class: 'sub', text: url })), closeBtn));
    const body = h('div', { class: 'sheet-body' });
    add(sheet, body);
    const view = { url, domain, page, body, status: null, platforms: null, connection: null, plan: null, confirm: new Set(), options: {} };
    view.changes = h('section', { class: 'tile white publish-changes' });
    view.owner = h('section', { class: 'tile publish-owner' });
    view.connect = h('section', { class: 'tile publish-connect' });
    view.history = h('section', { class: 'tile publish-history' });
    add(body, view.changes, view.owner, view.connect, view.history, takeAway(view));
    if (S.publicDemo || !S.slug) {
      view.changes.remove();
      view.connect.remove();
      view.history.remove();
      add(view.owner, 
        h('span', { class: 'label label-medium', text: 'Needs ownership' }),
        h('p', { class: 'reason', text: S.publicDemo ? 'The demo cannot publish. Take the edited page with you below.' : `Add ${domain} as a site to prove you own it and publish from here.` }));
      return;
    }
    load(view);
  });
}

/* ------------------------------------------------------------------ loading */

async function load(view) {
  add(view.owner, h('p', { class: 'caveat', text: 'Checking ownership...' }));
  paintChanges(view, null);
  try {
    const [status, platforms] = await Promise.all([api.get(`${base()}/status?url=${enc(view.url)}`), view.platforms ? null : api.get('/api/v1/publish/platforms')]);
    view.status = status;
    if (platforms) view.platforms = platforms.platforms;
  } catch (err) {
    clear(view.owner);
    add(view.owner, h('span', { class: 'label label-high', text: 'Could not load' }), h('p', { class: 'reason', text: err.message }));
    return;
  }
  if (!view.status.ownership) {
    try {
      view.status.ownership = await api.post(`${base()}/ownership`, { site: view.domain });
    } catch (err) {
      toast(err.message);
    }
  }
  const usable = view.status.connections || [];
  if (!view.connection || !usable.some((c) => c.id === view.connection)) view.connection = usable.length ? usable[0].id : null;
  paintOwner(view);
  paintConnect(view);
  paintHistory(view);
  plan(view);
}

async function plan(view) {
  paintChanges(view, null);
  try {
    const answer = await api.post(`${base()}/plan`, { url: view.url, html: view.page.html, connection_id: view.status.owned ? view.connection : null });
    view.plan = answer.plan;
    paintChanges(view, answer.changes);
    paintConnect(view);
  } catch (err) {
    view.plan = null;
    paintChanges(view, null, err.message);
  }
}

/* ------------------------------------------------------------------ what changes */

function paintChanges(view, changes, error) {
  const tile = view.changes;
  clear(tile);
  add(tile, h('div', { class: 'tile-head' }, h('h2', { text: 'What changes' }), view.page.diff && view.page.diff.summary ? h('span', { class: 'aside', text: view.page.diff.summary }) : null));
  if (error) {
    add(tile, h('p', { class: 'caveat', text: error }));
    return;
  }
  if (!changes) {
    add(tile, h('p', { class: 'caveat', text: 'Comparing with the live page...' }));
    return;
  }
  if (!changes.length) {
    add(tile, h('p', { class: 'caveat', text: 'The live page already matches this page. Edit it to publish a change.' }));
    return;
  }
  const rows = view.plan ? view.plan.rows : changes.map((c) => ({ ...c, apply: null }));
  add(tile, h('div', { class: 'publish-rows' }, rows.map((row) => h('div', { class: 'publish-row' },
    h('div', { class: 'publish-row-head' },
      h('b', { text: row.label }),
      row.apply === true ? h('span', { class: 'chip chip-green', text: 'Will publish' }) : row.apply === false ? h('span', { class: 'chip chip-gray', text: 'Not through this connection' }) : null),
    h('div', { class: 'publish-before', text: row.before || 'Empty' }),
    h('div', { class: 'publish-after' }, icon('arrow'), h('span', { text: row.after || 'Empty' })),
    row.note ? h('p', { class: 'caveat', text: row.note }) : null))));
}

/* ------------------------------------------------------------------ ownership */

function paintOwner(view) {
  const tile = view.owner;
  clear(tile);
  const record = view.status.ownership;
  if (view.status.owned) {
    tile.classList.add('white');
    add(tile, 
      h('div', { class: 'tile-head' }, h('h2', { text: 'Ownership' }), h('span', { class: 'label label-ok', text: 'Proven' })),
      h('p', { class: 'caveat', text: `Proven with the ${METHOD_NAMES[record.method] || record.method} on ${when(record.verified_at)}. Checked again before publishing.` }));
    return;
  }
  tile.classList.remove('white');
  add(tile, 
    h('div', { class: 'tile-head' }, h('h2', { text: `Prove you own ${record ? record.domain : view.domain}` }), h('span', { class: 'label label-medium', text: 'Needs ownership' })),
    h('p', { class: 'caveat', text: 'Anyone can edit and test this page. Publishing it needs one of these on your site. Add one, then Check.' }));
  if (!record) return;
  let method = view.method || 'meta';
  const seg = h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': 'Proof method' });
  const detail = h('div', { class: 'publish-proof' });
  const paintMethod = () => {
    for (const b of seg.children) b.setAttribute('aria-pressed', String(b.dataset.method === method));
    clear(detail);
    const step = record.instructions[method];
    add(detail, 
      h('p', { class: 'publish-step', text: step.steps }),
      h('div', { class: 'publish-code' }, h('code', { text: step.value }), h('button', { class: 'btn btn-plain', type: 'button', onClick: () => copyText(step.value, 'Copied.') }, 'Copy')));
  };
  for (const key of ['meta', 'file', 'dns']) {
    seg.append(h('button', { type: 'button', dataset: { method: key }, onClick: () => { method = key; view.method = key; paintMethod(); } }, record.instructions[key].label));
  }
  paintMethod();
  const check = h('button', { class: 'btn btn-dark', type: 'button' }, 'Check');
  check.addEventListener('click', async () => {
    const done = busy(check, 'Checking...');
    try {
      const result = await api.post(`${base()}/ownership/verify`, { site: record.domain });
      done();
      if (result.verified) {
        toast(`Ownership of ${result.domain} is proven.`);
        await load(view);
      } else {
        view.status.ownership = result;
        paintOwner(view);
      }
    } catch (err) {
      done();
      toast(err.message);
    }
  });
  const last = record.last_check;
  add(tile, seg, detail, h('div', { class: 'form-actions' }, check),
    last && !last.ok ? h('p', { class: 'caveat publish-miss', text: `Last check, ${when(last.at)}: ${last.detail}` }) : null);
}

/* ------------------------------------------------------------------ connection and publishing */

function paintConnect(view) {
  const tile = view.connect;
  clear(tile);
  if (!view.status || !view.status.owned) {
    tile.hidden = true;
    return;
  }
  tile.hidden = false;
  tile.classList.add('white');
  const connections = view.status.connections || [];
  add(tile, h('div', { class: 'tile-head' }, h('h2', { text: 'Where it is hosted' }),
    connections.length ? h('button', { class: 'btn btn-plain', type: 'button', onClick: () => paintForm(view) }, 'Add a connection') : null));
  if (!connections.length) {
    paintForm(view);
    return;
  }
  const seg = h('div', { class: 'seg seg-sm publish-conns', role: 'group', 'aria-label': 'Connection' },
    connections.map((c) => h('button', { type: 'button', 'aria-pressed': String(c.id === view.connection), onClick: () => { view.connection = c.id; view.confirm.clear(); plan(view); } }, c.label)));
  add(tile, seg);
  const current = connections.find((c) => c.id === view.connection);
  const platform = (view.platforms || []).find((p) => current && p.kind === current.kind);
  if (platform && platform.limits.length) add(tile, h('ul', { class: 'publish-limits' }, platform.limits.map((text) => h('li', { text }))));
  if (!view.plan) return;
  for (const warning of view.plan.warnings || []) {
    const key = warning.option || warning.code;
    const box = h('input', { type: 'checkbox', checked: warning.option ? !!view.options[key] : view.confirm.has(key) });
    box.addEventListener('change', () => {
      if (warning.option) view.options[key] = box.checked;
      else if (box.checked) view.confirm.add(key);
      else view.confirm.delete(key);
      publishBtn.disabled = !ready();
    });
    add(tile, h('label', { class: 'publish-warn' }, box, h('span', { text: warning.text })));
  }
  const applying = view.plan.rows.filter((r) => r.apply).length;
  const ready = () => applying > 0 && (view.plan.warnings || []).every((w) => !w.confirm || view.confirm.has(w.code));
  const publishBtn = h('button', { class: 'btn btn-primary btn-large btn-block', type: 'button', disabled: !ready() },
    applying ? `Publish ${applying === 1 ? '1 change' : `${applying} changes`}` : 'Nothing to publish here');
  publishBtn.addEventListener('click', async () => {
    const done = busy(publishBtn, 'Publishing...');
    try {
      const entry = await api.post(base(), { url: view.url, html: view.page.html, connection_id: view.connection, confirm: [...view.confirm], options: view.options });
      toast(entry.verify && entry.verify.status === 'confirmed' ? 'Published. The live page shows it.' : 'Published.');
      view.confirm.clear();
      await load(view);
    } catch (err) {
      done();
      toast(err.message);
    }
  });
  add(tile, publishBtn, view.plan.live_after_apply ? null : h('p', { class: 'caveat', text: current && current.kind === 'github' ? 'This opens a pull request. It goes live when you merge it.' : 'This saves the change on the platform.' }));
}

function paintForm(view) {
  const tile = view.connect;
  clear(tile);
  add(tile, h('div', { class: 'tile-head' }, h('h2', { text: 'Connect where it is hosted' }),
    (view.status.connections || []).length ? h('button', { class: 'btn btn-plain', type: 'button', onClick: () => paintConnect(view) }, 'Cancel') : null));
  if (view.status.encryption_ready === false) {
    add(tile, h('p', { class: 'caveat', text: 'Saving a connection needs the server encryption key, which is not set up yet. The owner of this Ranksim sets it.' }));
  }
  const platforms = view.platforms || [];
  let kind = view.kind || (platforms[0] && platforms[0].kind);
  const seg = h('div', { class: 'seg seg-sm publish-kinds', role: 'group', 'aria-label': 'Platform' });
  const form = h('form', { class: 'publish-form', autocomplete: 'off' });
  const paint = () => {
    for (const b of seg.children) b.setAttribute('aria-pressed', String(b.dataset.kind === kind));
    clear(form);
    const platform = platforms.find((p) => p.kind === kind);
    if (!platform) return;
    add(form, h('p', { class: 'caveat', text: platform.summary }));
    if (platform.limits.length) add(form, h('ul', { class: 'publish-limits' }, platform.limits.map((text) => h('li', { text }))));
    const field = (key, secret) => {
      const [label, help] = FIELD_HELP[key] || [key, ''];
      const required = secret || (REQUIRED[kind] || []).includes(key);
      const input = LONG.has(key)
        ? h('textarea', { class: 'field mono', name: key, rows: 3, placeholder: help })
        : h('input', { class: 'field', name: key, type: secret ? 'password' : 'text', placeholder: help, required, autocomplete: secret ? 'new-password' : 'off', spellcheck: 'false' });
      if (key === 'base_url' || key === 'url') input.value = key === 'base_url' ? `https://${view.domain}` : '';
      return h('label', { class: 'publish-field' }, h('span', { text: required ? label : `${label} (optional)` }), input);
    };
    for (const key of platform.settings) add(form, field(key, false));
    for (const key of platform.secrets) add(form, field(key, true));
    const save = h('button', { class: 'btn btn-dark', type: 'submit' }, 'Connect');
    add(form, h('p', { class: 'caveat', text: 'Ranksim tests the connection, then stores the credentials encrypted. They are never shown again.' }), h('div', { class: 'form-actions' }, save));
    form.onsubmit = async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const settings = {};
      const secret = {};
      for (const key of platform.settings) if (String(data.get(key) || '').trim()) settings[key] = String(data.get(key)).trim();
      for (const key of platform.secrets) secret[key] = String(data.get(key) || '');
      const done = busy(save, 'Testing...');
      try {
        const made = await api.post(`${base()}/connections`, { kind, site: view.domain, settings, secret });
        form.reset();
        toast(`Connected ${made.label}.`);
        view.connection = made.id;
        await load(view);
      } catch (err) {
        done();
        toast(err.message);
      }
    };
  };
  for (const p of platforms) seg.append(h('button', { type: 'button', dataset: { kind: p.kind }, onClick: () => { kind = p.kind; view.kind = kind; paint(); } }, p.label));
  paint();
  add(tile, seg, form);
}

/* ------------------------------------------------------------------ history */

function paintHistory(view) {
  const tile = view.history;
  clear(tile);
  const entries = (view.status && view.status.history) || [];
  if (!entries.length) {
    tile.hidden = true;
    return;
  }
  tile.hidden = false;
  add(tile, h('div', { class: 'tile-head' }, h('h2', { text: 'History' }), h('span', { class: 'aside', text: `${entries.length}` })));
  const list = h('div', { class: 'rows' });
  entries.forEach((entry, index) => {
    const [cls, text] = VERIFY_TEXT[(entry.verify && entry.verify.status) || entry.status] || ['label-low', entry.status];
    const fields = (entry.rows || []).filter((r) => r.apply).map((r) => r.label).join(', ');
    const actions = h('div', { class: 'form-actions' });
    if (entry.remote && entry.remote.pr_url) actions.append(h('a', { class: 'btn btn-plain', href: entry.remote.pr_url, target: '_blank', rel: 'noopener' }, 'Pull request'));
    if (!entry.rolled_back_at && entry.status !== 'failed') {
      const again = h('button', { class: 'btn btn-plain', type: 'button' }, 'Check again');
      again.addEventListener('click', async () => {
        const done = busy(again, 'Checking...');
        try {
          await api.post(`${base()}/history/${enc(entry.id)}/verify`);
          await load(view);
        } catch (err) {
          done();
          toast(err.message);
        }
      });
      actions.append(again);
    }
    if (entry.can_rollback && index === entries.findIndex((e) => e.can_rollback)) {
      const undo = h('button', { class: 'btn btn-plain btn-danger', type: 'button' }, 'Roll back');
      let armed = false;
      undo.addEventListener('click', async () => {
        if (!armed) {
          armed = true;
          undo.textContent = 'Roll back for real?';
          return;
        }
        const done = busy(undo, 'Rolling back...');
        try {
          const result = await api.post(`${base()}/history/${enc(entry.id)}/rollback`);
          toast((result.rollback && result.rollback.note) || 'Rolled back.');
          await load(view);
        } catch (err) {
          done();
          toast(err.message);
        }
      });
      actions.append(undo);
    }
    list.append(h('div', { class: 'rowcard publish-entry' }, h('div', { class: 'body' },
      h('div', { class: 'publish-row-head' }, h('b', { text: when(entry.at) }), h('span', { class: 'chip chip-gray', text: entry.connection ? entry.connection.label : '' })),
      h('span', { class: `label ${cls}`, text: entry.rolled_back_at ? `Rolled back ${when(entry.rolled_back_at)}` : text }),
      fields ? h('p', { class: 'caveat', text: fields }) : null,
      entry.note ? h('p', { class: 'caveat', text: entry.note }) : null,
      actions)));
  });
  add(tile, list);
}

/* ------------------------------------------------------------------ take it with you */

function takeAway(view) {
  const html = view.page.html;
  const download = h('button', { class: 'btn', type: 'button' }, 'Download HTML');
  download.addEventListener('click', () => {
    const blob = new Blob([html], { type: 'text/html' });
    const a = h('a', { href: URL.createObjectURL(blob), download: `${(pathOf(view.url).replace(/^\/|\/$/g, '').replace(/\//g, '-') || 'index')}.html` });
    document.body.append(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(a.href);
      a.remove();
    }, 500);
  });
  const copy = h('button', { class: 'btn', type: 'button', onClick: () => copyText(html, 'Copied the edited HTML.') }, 'Copy HTML');
  return h('section', { class: 'tile publish-take' },
    h('div', { class: 'tile-head' }, h('h2', { text: 'Take it with you' })),
    h('p', { class: 'caveat', text: view.page.roundTrip
      ? 'The file is the original page with your edits, scripts included.'
      : 'The file is the page HTML with your edits and without its scripts, so copy your changes into the real page rather than replacing it.' }),
    h('div', { class: 'form-actions' }, download, copy));
}
