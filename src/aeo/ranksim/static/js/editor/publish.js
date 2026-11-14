/* Publish: anyone can edit and test a page; pushing it live needs proof that you own the site and a connection to
   where it is hosted. The sheet shows what will change, walks through ownership and the connection, publishes,
   checks the live page, and keeps a history with rollback. Download and Copy are always there.

   Server routes: /api/v1/publish/platforms and /api/v1/projects/{slug}/publish/... (plugins/publish.py). Secrets go
   to the server once and never come back. */

import { S, h, api, enc, hostOf, pathOf, toast, openSheet, clear, icon, put } from '../core.js';
import { collect } from '../hooks.js';
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
    // exportHtml before the page has loaded is the plain draft, not the original with scripts
    roundTrip: !!exported && !!(ctx && ctx.draft && ctx.draft.loaded),
    diff: ctx && typeof ctx.diff === 'function' ? ctx.diff() : null,
    fixes: ctx ? appliedFixes(ctx) : [],
  };
}

/* Issues whose suggestion was applied to this draft. The audit plugin answers the 'publish-fixes' event with
   [{ id, title }] for ctx.page and ctx.draft (on('publish-fixes', (ctx) => [...])). A live publish marks them Done in
   the client report; a rollback opens them again. Without a listener the list is empty. */
function appliedFixes(ctx) {
  const seen = new Set();
  return collect('publish-fixes', ctx).filter((fix) => {
    const ok = fix && typeof fix.id === 'string' && /^[a-z0-9][a-z0-9-]{0,119}$/.test(fix.id) && !seen.has(fix.id);
    if (ok) seen.add(fix.id);
    return ok;
  }).map((fix) => ({ id: fix.id, title: String(fix.title || '') }));
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

/* Every plan is tied to the connection it was made for. A newer plan() call makes older answers stale, and while a
   plan is on its way the Publish button is off, so a click never sends a plan made for another connection. */
async function plan(view) {
  const seq = (view.planSeq = (view.planSeq || 0) + 1);
  const connection = view.status.owned ? view.connection : null;
  view.plan = null;
  view.planFor = null;
  paintChanges(view, null);
  if (!view.formOpen) paintConnect(view);
  try {
    const answer = await api.post(`${base()}/plan`, { url: view.url, html: view.page.html, connection_id: connection });
    if (seq !== view.planSeq) return;
    view.plan = answer.plan;
    view.planFor = connection;
    paintChanges(view, answer.changes);
  } catch (err) {
    if (seq !== view.planSeq) return;
    paintChanges(view, null, err.message);
  }
  if (!view.formOpen) paintConnect(view);
}

/* ------------------------------------------------------------------ what changes */

function paintChanges(view, changes, error) {
  const tile = view.changes;
  clear(tile);
  const diff = view.page.diff;
  add(tile, h('div', { class: 'tile-head' }, h('h2', { text: 'What changes' })));
  if (diff && diff.summary) add(tile, h('p', { class: 'publish-summary', text: diff.summary }));
  if (error) {
    add(tile, h('p', { class: 'caveat', text: error }));
    return;
  }
  if (!changes) {
    add(tile, h('p', { class: 'caveat', text: 'Comparing with the live page...' }));
    return;
  }
  if (view.page.fixes.length) {
    add(tile, h('div', { class: 'publish-fixes' },
      h('b', { text: view.page.fixes.length === 1 ? 'Fixes 1 issue' : `Fixes ${view.page.fixes.length} issues` }),
      h('ul', { class: 'publish-steps' }, view.page.fixes.map((fix) => h('li', { class: 'ok' }, icon('check'), h('span', { text: fix.title || fix.id })))),
      h('p', { class: 'caveat', text: 'Once the change is live, these show as Done in the client report.' })));
  }
  if (!changes.length) {
    add(tile, h('p', { class: 'caveat', text: 'The live page already matches this page. Edit it to publish a change.' }));
    return;
  }
  const rows = view.plan ? view.plan.rows : changes.map((c) => ({ ...c, apply: null }));
  const blocks = bodyChanges(diff);
  add(tile, h('div', { class: 'publish-rows' }, rows.map((row) => h('div', { class: 'publish-row' },
    h('div', { class: 'publish-row-head' },
      h('b', { text: row.label }),
      row.apply === true ? h('span', { class: 'chip chip-green', text: 'Will publish' }) : row.apply === false ? h('span', { class: 'chip chip-gray', text: 'Not through this connection' }) : null),
    row.field === 'body' && blocks.length ? bodyBlocks(blocks) : [
      h('div', { class: 'publish-before', text: row.before || 'Empty' }),
      h('div', { class: 'publish-after' }, icon('arrow'), h('span', { text: row.after || 'Empty' })),
      row.places ? h('p', { class: 'caveat', text: `Changed in ${row.places} places. The first ones are shown.` }) : null,
    ],
    row.note ? h('p', { class: 'caveat', text: row.note }) : null))));
}

/* The body row, from the editor's own diff (ctx.diff): each changed block with its words marked, unchanged stretches
   trimmed, so the edit is visible however long the page is. Title, description and main heading have their own rows. */
const SHOWN_EDITS = 8;
const OWN_ROWS = new Set(['title', 'description', 'h1']);
const bodyChanges = (diff) => ((diff && diff.changes) || []).filter((c) => c.type !== 'same' && !OWN_ROWS.has(c.tag));

function trimmed(text, where) {
  const limit = 70;
  if (text.length <= limit) return text;
  if (where === 'first') return `...${text.slice(-limit / 2)}`;
  if (where === 'last') return `${text.slice(0, limit / 2)}...`;
  return `${text.slice(0, limit / 2)} ... ${text.slice(-limit / 2)}`;
}

function wordLine(words) {
  return h('p', { class: 'publish-words' }, words.map((w, i) => {
    if (w.op === '+') return h('ins', { text: w.text });
    if (w.op === '-') return h('del', { text: w.text });
    return h('span', { text: trimmed(w.text, i === 0 ? 'first' : i === words.length - 1 ? 'last' : 'middle') });
  }));
}

function bodyBlocks(changes) {
  const line = (c) => h('li', null,
    h('b', { text: c.label || c.kind || c.type }),
    c.words && c.words.length ? wordLine(c.words)
      : c.type === 'removed' ? h('p', { class: 'publish-words' }, h('del', { text: trimmed(c.before || '', 'last') }))
        : c.after ? h('p', { class: 'publish-words' }, c.type === 'added' ? h('ins', { text: trimmed(c.after, 'last') }) : h('span', { text: trimmed(c.after, 'last') })) : null);
  return h('div', { class: 'publish-edits' },
    h('ul', null, changes.slice(0, SHOWN_EDITS).map(line)),
    changes.length > SHOWN_EDITS ? h('p', { class: 'caveat', text: `And ${changes.length - SHOWN_EDITS} more.` }) : null);
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
    const copyRow = (value) => h('div', { class: 'publish-code' }, h('code', { text: value }), h('button', { class: 'btn btn-plain', type: 'button', onClick: () => copyText(value, 'Copied.') }, 'Copy'));
    add(detail, h('p', { class: 'publish-step', text: step.steps }));
    if (step.fields) {
      add(detail, step.fields.map(([name, value]) => h('div', { class: 'publish-kv' }, h('span', { text: name }),
        name === 'Name' || name === 'Type' ? h('code', { text: value }) : copyRow(value))));
    } else {
      add(detail, copyRow(step.value));
    }
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
  add(tile, seg, detail, h('div', { class: 'form-actions' }, check), foundList(record.last_check));
}

/* What the last Check found, one line per method it tried, in plain words. */
function foundList(last) {
  if (!last || last.ok || !(last.attempts || []).length) return null;
  return h('div', { class: 'publish-found' },
    h('b', { text: `What Check found, ${when(last.at)}` }),
    h('ul', { class: 'publish-steps' }, last.attempts.map((a) => h('li', { class: a.found ? 'ok' : a.definitive ? 'miss' : 'unsure' },
      icon(a.found ? 'check' : a.definitive ? 'x' : 'warn'),
      h('span', null, h('b', { text: `${(METHOD_NAMES[a.method] || a.method).replace(/^./, (c) => c.toUpperCase())}: ` }), a.detail,
        a.definitive ? '' : ' This check could not run, so try again in a minute.')))));
}

/* ------------------------------------------------------------------ connection and publishing */

function paintConnect(view) {
  const tile = view.connect;
  view.formOpen = false;
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
  const planning = !view.plan || view.planFor !== view.connection;
  const seg = h('div', { class: 'seg seg-sm publish-conns', role: 'group', 'aria-label': 'Connection' },
    connections.map((c) => h('button', { type: 'button', 'aria-pressed': String(c.id === view.connection), onClick: () => { view.connection = c.id; view.confirm.clear(); plan(view); } }, c.label)));
  add(tile, seg);
  const current = connections.find((c) => c.id === view.connection);
  const platform = (view.platforms || []).find((p) => current && p.kind === current.kind);
  if (platform && platform.limits.length) add(tile, h('ul', { class: 'publish-limits' }, platform.limits.map((text) => h('li', { text }))));
  if (planning) {
    add(tile, h('button', { class: 'btn btn-primary btn-large btn-block', type: 'button', disabled: true }, 'Comparing with the live page...'));
    return;
  }
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
  const planned = view.planFor;
  publishBtn.addEventListener('click', async () => {
    busy(publishBtn, 'Publishing...');
    view.plan = null;  // this plan is used up: no second click while the sheet reloads
    try {
      const entry = await api.post(base(), { url: view.url, html: view.page.html, connection_id: planned, confirm: [...view.confirm], options: view.options, fixes: view.page.fixes });
      toast(publishedText(entry));
    } catch (err) {
      toast(err.message);
    }
    view.confirm.clear();
    await load(view);
  });
  add(tile, publishBtn,
    view.page.roundTrip || !current || !['github', 'webhook'].includes(current.kind) ? null
      : h('p', { class: 'caveat', text: 'This sends the whole page as the editor shows it, without its scripts. Ranksim asks you to confirm when the live page has scripts or forms this copy lacks.' }),
    view.plan.live_after_apply ? null : h('p', { class: 'caveat', text: current && current.kind === 'github' ? 'This opens a pull request. It goes live when you merge it.' : 'This saves the change on the platform.' }));
}

function paintForm(view) {
  const tile = view.connect;
  clear(tile);
  view.formOpen = true;
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
    if (GUIDES[kind]) {
      guidedConnect(view, form, platform, GUIDES[kind]);
      return;
    }
    add(form, h('p', { class: 'caveat', text: platform.summary }));
    if (platform.limits.length) add(form, h('ul', { class: 'publish-limits' }, platform.limits.map((text) => h('li', { text }))));
    const field = (key, secret) => {
      const [label, help] = FIELD_HELP[key] || [key, ''];
      const required = secret || (REQUIRED[kind] || []).includes(key);
      const input = LONG.has(key)
        ? h('textarea', { class: 'field mono', name: key, rows: 3, placeholder: help })
        : h('input', { class: 'field', name: key, type: secret ? 'password' : 'text', placeholder: help, required, autocomplete: secret ? 'new-password' : 'off', spellcheck: 'false' });
      if (key === 'base_url' || key === 'url') input.value = key === 'base_url' ? new URL(view.url).origin : '';
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

/* ------------------------------------------------------------------ guided connect

   WordPress, Shopify and Webflow get numbered steps in plain words: where to make the credential, what to paste, a
   live Check that says what it found (sign in, permissions, this page, what the platform's API can and cannot
   change), then Save. Nothing is stored until Save. Each platform is a short spec below. */

const GUIDES = {
  wordpress: {
    intro: 'Ranksim uses an application password, a separate password you can remove at any time. It never needs your normal password.',
    steps: [
      { title: 'Your WordPress address', fields: [['base_url', 'Site address', 'url', (view) => new URL(view.url).origin]] },
      {
        title: 'Make an application password',
        text: 'In WordPress, open Users, then Profile. Scroll to Application Passwords, type Ranksim as the name, and click Add New Application Password. WordPress shows it once, so copy it.',
        link: ['Open your WordPress profile', (v) => `${new URL(v.base_url).origin}/wp-admin/profile.php#application-passwords-section`],
      },
      { title: 'Paste it here', fields: [['username', 'WordPress username', 'text', null, 'Your WordPress username, not your email'], ['app_password', 'Application password', 'secret', null, 'xxxx xxxx xxxx xxxx xxxx xxxx']] },
    ],
    check: 'Check signs in, looks for an SEO plugin and finds this page. Nothing is saved yet.',
  },
  shopify: {
    intro: 'Ranksim uses a custom app token for your store. You can remove the app at any time.',
    steps: [
      { title: 'Your store address', fields: [['shop', 'Store address', 'text', null, 'your-store.myshopify.com']], text: 'Find it in Shopify admin under Settings, Domains. It ends in myshopify.com.' },
      {
        title: 'Make a custom app',
        text: 'In Shopify admin, open Settings, then Apps and sales channels, then Develop apps. Create an app named Ranksim. Under Configuration, turn on the Admin API scopes write_online_store_pages and write_content. Install the app, then reveal the Admin API access token. Shopify shows it once.',
        link: ['Open Develop apps', (v) => (/^[a-z0-9-]+\.myshopify\.com$/.test(v.shop || '') ? `https://${v.shop}/admin/settings/apps/development` : null)],
      },
      { title: 'Paste it here', fields: [['access_token', 'Admin API access token', 'secret', null, 'shpat_...']] },
    ],
    check: 'Check connects to your store, reads the app permissions and finds this page. Nothing is saved yet.',
  },
  webflow: {
    intro: 'Ranksim uses a site API token. You can delete it in Webflow at any time.',
    steps: [
      { title: 'Your site ID', fields: [['site_id', 'Site ID', 'text', null, '24 letters and digits']], text: 'In Webflow, open the site, then Settings, General. Copy the Site ID.' },
      {
        title: 'Make a site API token',
        text: 'In the same Settings, open Apps and integrations, then API access, and generate a token. Give it read and write access to Pages and CMS, and Sites publish if Ranksim should publish the site.',
      },
      { title: 'Paste it here', fields: [['api_token', 'Site API token', 'secret', null, '']] },
    ],
    check: 'Check connects to the site, reads its pages and CMS, and says what Webflow lets Ranksim change on this page. Nothing is saved yet.',
  },
};

function guidedConnect(view, form, platform, guide) {
  const inputs = {};
  const links = [];
  const results = h('ul', { class: 'publish-steps' });
  const checkBtn = h('button', { class: 'btn btn-dark', type: 'button' }, 'Check');
  const save = h('button', { class: 'btn btn-primary', type: 'submit', disabled: true }, 'Save connection');
  const values = () => {
    const settings = {};
    const secret = {};
    for (const [key, input] of Object.entries(inputs)) {
      if (platform.secrets.includes(key)) secret[key] = input.value;
      else if (input.value.trim()) settings[key] = input.value.trim();
    }
    return { settings, secret };
  };
  const plain = () => Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, input.value.trim()]));
  const relink = () => {
    for (const [a, make] of links) {
      let href = null;
      try {
        href = make(plain());
      } catch (_err) {
        href = null;
      }
      if (href) a.href = href;
      else a.removeAttribute('href');
    }
  };
  const reset = () => {
    save.disabled = true;
    clear(results);
    relink();
  };
  const stepBox = (n, title, ...kids) => h('div', { class: 'publish-guide-step' }, h('span', { class: 'publish-num', text: String(n) }), h('div', null, h('b', { text: title }), ...kids));
  add(form, h('p', { class: 'caveat', text: guide.intro }));
  guide.steps.forEach((step, index) => {
    const kids = [];
    if (step.text) kids.push(h('p', { class: 'publish-step', text: step.text }));
    for (const [key, label, type, initial, placeholder] of step.fields || []) {
      const secret = type === 'secret';
      const input = h('input', {
        class: `field${secret ? ' mono' : ''}`, name: key, type: secret ? 'password' : type, required: true, spellcheck: 'false',
        autocomplete: secret ? 'new-password' : 'off', placeholder: placeholder || '', value: initial ? initial(view) : '',
      });
      input.addEventListener('input', reset);
      inputs[key] = input;
      kids.push(h('label', { class: 'publish-field' }, h('span', { text: label }), input));
    }
    if (step.link) {
      const a = h('a', { class: 'btn', target: '_blank', rel: 'noopener' }, step.link[0]);
      links.push([a, step.link[1]]);
      kids.push(h('div', { class: 'form-actions' }, a));
    }
    add(form, stepBox(index + 1, step.title, ...kids));
  });
  relink();
  checkBtn.addEventListener('click', async () => {
    if (!form.reportValidity()) return;
    const done = busy(checkBtn, 'Checking...');
    clear(results);
    try {
      const answer = await api.post(`${base()}/connections/check`, { kind: platform.kind, url: view.url, ...values() });
      add(results, answer.steps.map((step) => h('li', { class: step.ok ? (step.key === 'limits' ? 'info' : 'ok') : 'miss' },
        icon(step.ok ? (step.key === 'limits' ? 'doc' : 'check') : 'x'), h('span', null, h('b', { text: `${step.label}: ` }), step.detail))));
      const canSave = answer.can_save !== undefined ? answer.can_save : answer.ok;
      save.disabled = !canSave;
      if (!canSave) add(results, h('li', { class: 'unsure' }, icon('warn'), h('span', { text: 'Fix the first item marked with a cross, then Check again.' })));
      else if (!answer.ok) add(results, h('li', { class: 'unsure' }, icon('warn'), h('span', { text: 'The connection works. This page cannot be published through it, but other pages can.' })));
    } catch (err) {
      add(results, h('li', { class: 'miss' }, icon('x'), h('span', { text: err.message })));
    }
    done();
  });
  add(form,
    stepBox(guide.steps.length + 1, 'Check it works', h('p', { class: 'publish-step', text: guide.check }), h('div', { class: 'form-actions' }, checkBtn), results),
    h('div', { class: 'publish-can' }, h('b', { text: 'What it can and cannot change' }), h('p', { class: 'caveat', text: platform.summary }),
      h('ul', { class: 'publish-limits' }, platform.limits.map((text) => h('li', { text })))),
    h('div', { class: 'form-actions' }, save));
  form.onsubmit = async (event) => {
    event.preventDefault();
    const done = busy(save, 'Saving...');
    try {
      const made = await api.post(`${base()}/connections`, { kind: platform.kind, site: view.domain, ...values() });
      form.reset();
      toast(`Connected ${made.label}.`);
      view.connection = made.id;
      await load(view);
    } catch (err) {
      done();
      toast(err.message);
    }
  };
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
  // The server rolls back the newest change first, so only the newest entry that is still in effect offers it.
  const rollbackable = entries.find((e) => !e.rolled_back_at && e.status !== 'failed' && e.status !== 'closed');
  entries.forEach((entry) => {
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
    if (entry.can_rollback && entry === rollbackable) {
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
          const result = await api.post(`${base()}/history/${enc(entry.id)}/rollback`, rollbackBody(view, entry));
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

/* The connection to roll back with: the one used to publish, or, after a reconnect, one to the same platform. */
function rollbackBody(view, entry) {
  const connections = view.status.connections || [];
  if (!entry.connection || connections.some((c) => c.id === entry.connection.id)) return undefined;
  const same = connections.find((c) => c.kind === entry.connection.kind);
  return same ? { connection_id: same.id } : undefined;
}

function publishedText(entry) {
  const status = (entry.verify && entry.verify.status) || entry.status;
  if (entry.status === 'pull_request') return 'Pull request opened. It goes live when you merge it.';
  if (entry.status === 'saved') return 'Saved on the platform. Not live yet.';
  if (status === 'confirmed') return 'Published. The live page shows it.';
  return 'Published. The live page does not show it yet.';
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
