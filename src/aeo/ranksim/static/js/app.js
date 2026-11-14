/* Ranksim. Type a site; Ranksim finds its searches, pulls Google, finds competitors, and reports what to fix.
   Then edit a page right in the preview and test where it is PREDICTED to land before you publish.
   This module boots the app: routing, the top bar, sign in, home, and the open-any-URL flow. Screens live in
   screens/ and editor/, and register themselves through hooks.js; plugins in plugins/ do the same. */

import {
  S, main, shell, api, stream, h, icon, clear, enc, hostOf, plural, initial, bar, toast, navigate, link, pathFor,
  parseLocation, loadProjects, openSite, closeSheet, footer, bootSpinner, sleep, emptyState,
} from './core.js';
import { registerEditorPanel, screen, screens, renderEntry, sealBuiltIns } from './hooks.js';
import './screens/overview.js';
import './screens/issues.js';
import './screens/searches.js';
import './screens/competitors.js';
import './screens/settings.js';
import { draftFor, normalize, paintFrame, paintSide, renderEditor } from './editor/editor.js';
import './editor/test.js';
import { publishButton } from './editor/publish.js';

const barInner = document.querySelector('#bar .bar-inner');

/* ------------------------------------------------------------------ boot */

async function boot() {
  renderBar();
  try {
    S.session = await api.get('/api/v1/session');
    S.publicDemo = S.session.mode === 'public';
    if (S.session.login_required && !S.session.signed_in) {
      renderLogin();
      return;
    }
    await loadProjects();
    await route();
  } catch (err) {
    if (S.session && S.session.login_required && !S.session.signed_in) return;
    renderProblem('Ranksim could not load', err.message);
  }
}

async function route() {
  const where = parseLocation();
  S.params = where.params;
  if (where.screen === 'open' && !S.publicDemo) {
    openUrl(where.params.get('url') || '');
    return;
  }
  try {
    if (where.slug && S.projects.some((p) => p.slug === where.slug)) {
      // the server answers any screen-shaped address; one no screen registered shows the Overview at its own address
      if (where.unknown) history.replaceState({}, '', `/app/${enc(where.slug)}${location.search}`);
      if (where.slug !== S.slug || !S.report) await openSite(where.slug);
      S.screen = where.screen;
      renderBar();
      render();
      return;
    }
    if (S.publicDemo && S.projects.length) {
      history.replaceState({}, '', `/app/${enc(S.demoSlug)}`);
      await openSite(S.demoSlug);
      S.screen = 'overview';
      renderBar();
      render();
      return;
    }
  } catch (err) {
    renderProblem('This site could not be opened', err.message);
    return;
  }
  S.slug = null;
  S.report = null;
  S.project = null;
  renderBar();
  renderHome();
}

/* ------------------------------------------------------------------ render dispatch */

function render() {
  closeSheet(true);
  if (!S.report) {
    if (S.open) renderEditor();
    return;
  }
  const spec = screen(S.screen) || screen('overview');
  document.title = `${spec.label} · ${S.report.name} · Ranksim`;
  const out = renderEntry(spec, { key: spec.key, slug: S.slug, report: S.report, project: S.project, params: S.params, main });
  if (out.length) clear(main).append(...out, footer());
}

/* ------------------------------------------------------------------ top bar */

function renderBar() {
  clear(barInner);
  barInner.append(h('a', {
    class: 'brand',
    href: '/',
    'aria-label': 'Ranksim home',
    onClick: (event) => {
      if (event.metaKey || event.ctrlKey) return;
      event.preventDefault();
      navigate('/');
    },
  }, h('span', { class: 'logo', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')), h('span', { class: 'brand-name', text: 'Ranksim' })));
  if (S.slug && S.report) {
    const nav = h('nav', { class: 'seg nav-seg', 'aria-label': 'Sections' });
    for (const spec of screens()) {
      if (spec.nav === false) continue;
      nav.append(link(spec.key, spec.label, { 'aria-current': S.screen === spec.key ? 'page' : null }));
    }
    barInner.append(nav);
  } else {
    barInner.append(h('span'));
  }
  const right = h('div', { class: 'bar-right' });
  if (S.slug && S.projects.length > 1) {
    const select = h('select', { 'aria-label': 'Site' }, S.projects.map((p) => h('option', { value: p.slug, selected: p.slug === S.slug, text: p.name })));
    select.addEventListener('change', () => navigate(`/app/${enc(select.value)}`));
    right.append(h('label', { class: 'select' }, select, icon('chevron', 'chev')));
  }
  if (S.slug && S.report && !S.publicDemo) right.append(link('settings', icon('gear'), { class: 'icon-btn', title: 'Settings', 'aria-label': 'Settings', 'aria-current': S.screen === 'settings' ? 'page' : null }));
  if (!S.publicDemo && S.session && (S.session.signed_in || !S.session.login_required)) {
    right.append(h('button', { class: 'icon-btn', type: 'button', title: 'Analyze a new site', 'aria-label': 'Analyze a new site', onClick: () => navigate('/') }, icon('plus')));
  }
  if (S.session && S.session.login_required && S.session.signed_in) {
    right.append(h('button', { class: 'icon-btn', type: 'button', title: 'Sign out', 'aria-label': 'Sign out', onClick: signOut }, icon('out')));
  }
  barInner.append(right);
}

function renderProblem(title, text) {
  renderBar();
  const offline = navigator.onLine === false;
  clear(main).append(h('div', { class: 'wrap screen', role: 'alert' }, emptyState(title, offline ? 'You are offline. Reconnect, then try again.' : (text || 'Something went wrong.'),
    h('button', { class: 'btn btn-dark', type: 'button', onClick: () => location.reload() }, icon('refresh'), 'Try again'),
    h('button', { class: 'btn', type: 'button', onClick: () => navigate('/') }, 'Go home'))), footer());
}

/* ------------------------------------------------------------------ offline */

function paintOffline() {
  const old = document.querySelector('.offline');
  if (navigator.onLine !== false) {
    if (old) {
      old.remove();
      toast('Back online.');
    }
    return;
  }
  if (old) return;
  document.getElementById('bar').after(h('div', { class: 'offline', role: 'status' }, icon('warn'), 'You are offline. Changes will not save until you reconnect.'));
}

window.addEventListener('online', paintOffline);
window.addEventListener('offline', paintOffline);

/* ------------------------------------------------------------------ sign in */

function renderLogin() {
  S.slug = null;
  S.report = null;
  renderBar();
  const field = h('input', { class: 'field', type: 'password', id: 'pw', autocomplete: 'current-password', 'aria-label': 'Password', placeholder: 'Password' });
  const error = h('p', { class: 'form-error', role: 'alert', hidden: true });
  const button = h('button', { class: 'btn btn-dark btn-large btn-block', type: 'submit' }, 'Continue');
  const form = h('form', { class: 'form', novalidate: true }, field, error, button);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!field.value) {
      field.focus();
      return;
    }
    error.hidden = true;
    button.disabled = true;
    clear(button).append(h('span', { class: 'spinner', 'aria-hidden': 'true' }));
    try {
      await api.post('/api/v1/login', { password: field.value });
      await boot();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
      button.disabled = false;
      clear(button).append('Continue');
      field.select();
    }
  });
  clear(main).append(h('div', { class: 'wrap screen' }, h('div', { class: 'tile white login' },
    h('span', { class: 'logo', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
    h('h1', { text: 'Welcome back' }),
    h('p', { text: 'Sign in to Ranksim.' }),
    form)));
  setTimeout(() => field.focus(), 50);
}

async function signOut() {
  try {
    await api.post('/api/v1/logout', {});
  } catch (_err) {
    /* signing out never fails visibly */
  }
  S.session.signed_in = false;
  history.pushState({}, '', '/');
  renderLogin();
}

/* ------------------------------------------------------------------ home: one box, type a site */

function looksLikeAddress(text) {
  return /^(https?:\/\/)?(localhost|[^\s/.]+(\.[^\s/.]+)+)(:\d+)?(\/\S*)?$/i.test(text);
}

function renderHome() {
  document.title = 'Ranksim';
  const input = h('input', { type: 'text', inputmode: 'url', autocomplete: 'off', spellcheck: 'false', placeholder: 'yoursite.com or any page address', 'aria-label': 'Page address', 'aria-describedby': 'home-error' });
  const submit = h('button', { class: 'btn btn-primary', type: 'submit' }, 'Open', icon('arrow'));
  const error = h('p', { class: 'form-error home-error', id: 'home-error', role: 'alert', hidden: true });
  const form = h('form', { class: 'composer', novalidate: true }, icon('globe', 'lead'), input, submit);
  input.addEventListener('input', () => { error.hidden = true; });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const site = input.value.trim();
    if (!site) {
      input.focus();
      return;
    }
    if (!looksLikeAddress(site)) {
      error.textContent = 'Enter an address like yoursite.com or yoursite.com/page.';
      error.hidden = false;
      input.focus();
      return;
    }
    submit.disabled = true;
    clear(submit).append(h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Opening');
    navigate(`/edit?url=${enc(/^https?:\/\//i.test(site) ? site : `https://${site}`)}`);
  });
  const demo = S.projects.find((p) => p.dataset === 'synthetic' || p.slug === S.demoSlug);
  const hero = h('section', { class: 'hero' },
    h('h1', { text: 'Which page should we look at?' }),
    h('p', { class: 'sub', text: 'Paste any address, yours or a competitor\'s. The live page opens right away while Ranksim finds its searches, competitors and issues.' }),
    S.publicDemo ? h('p', { class: 'hints', text: 'This demo is read only.' }) : form,
    S.publicDemo ? null : error,
    S.publicDemo ? null : h('div', { class: 'hints' },
      h('span', { text: 'Edit any page. Publishing needs you to own the site.' }),
      demo ? null : h('button', { class: 'btn btn-plain demo-entry', type: 'button', onClick: (event) => loadDemo(event.currentTarget) }, 'Or try the demo')));
  const body = [hero];
  if (S.projects.length) {
    const grid = h('div', { class: 'sites-grid' });
    for (const p of S.projects) {
      grid.append(h('a', {
        class: 'site-card',
        href: `/app/${enc(p.slug)}`,
        onClick: (event) => {
          if (event.metaKey || event.ctrlKey) return;
          event.preventDefault();
          navigate(`/app/${enc(p.slug)}`);
        },
      },
      h('div', { class: 'top' }, h('span', { class: `fav ${p.dataset === 'synthetic' ? '' : 'dark'}`, text: initial(p.site), 'aria-hidden': 'true' }), h('div', { class: 'who' }, h('div', { class: 'name', text: p.name }), h('div', { class: 'host', text: p.site }))),
      h('div', { class: 'meta' }, h('span', { text: plural(p.queries, 'search', 'searches') }), p.built ? h('span', { class: 'label label-ok', text: 'Report ready' }) : h('span', { class: 'label label-medium', text: 'Rankings pending' }), p.dataset === 'synthetic' ? h('span', { class: 'label label-demo', text: 'Demo' }) : null)));
    }
    body.push(h('section', { class: 'sites', 'aria-labelledby': 'recent-sites' }, h('div', { class: 'tile-head' }, h('h2', { id: 'recent-sites', text: 'Recent sites' })), grid));
  }
  clear(main).append(h('div', { class: 'wrap screen' }, body), footer());
  if (!S.publicDemo) setTimeout(() => input.focus(), 50);
}

async function loadDemo(button) {
  if (button) {
    button.disabled = true;
    clear(button).append(h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Building the demo');
  } else clear(main).append(bootSpinner());
  try {
    await api.post('/api/v1/demo', {});
    await loadProjects();
    navigate(`/app/${enc(S.demoSlug)}`);
  } catch (err) {
    toast(err.message);
    renderHome();
  }
}

/* ------------------------------------------------------------------ open any page: live steps */

const STEPS = [
  ['site', 'Reading your site'],
  ['searches', 'Finding your searches'],
  ['google', 'Checking Google'],
  ['pages', 'Reading the pages that rank'],
  ['model', 'Training your model'],
  ['competitors', 'Finding competitors'],
  ['report', 'Writing your report'],
];

/* Open any page: the live page is editable at once; the site's analysis runs beside it. */
async function openUrl(raw) {
  let url = (raw || '').trim();
  if (!url) {
    navigate('/', { replace: true });
    return;
  }
  if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
  const domain = hostOf(url);
  S.slug = null;
  S.report = null;
  S.project = null;
  S.screen = 'editor';
  S.params = new URLSearchParams({ page: url });
  S.open = { url, domain, steps: new Map(), current: null, state: 'reading', error: '', started: Date.now() };
  document.title = `${domain} · Ranksim`;
  renderBar();
  renderEditor();
  let found = null;
  try {
    found = await api.post('/api/v1/fetch', { url });
    const draft = draftFor(url);
    draft.text = normalize(found.html);
    draft.base = draft.text;
    draft.url = found.url;
    draft.loaded = found.url;
    draft.loading = false;
    draft.failed = '';
  } catch (err) {
    const draft = draftFor(url);
    draft.loading = false;
    draft.failed = err.message;
  }
  if (S.open && S.open.url === url && !S.report) {
    paintFrame();
    paintSide();
  }
  const known = found && found.project ? S.projects.find((p) => p.slug === found.project) : null;
  if (known && known.built) {
    await finishOpen(known.slug, url);
    return;
  }
  S.open.state = 'analyzing';
  paintSide();
  let result = null;
  for (let wait = 0; wait < 40; wait += 1) {
    try {
      await analyzeOnce(url, (done) => { result = done; });
      break;
    } catch (err) {
      if (!S.open || S.open.url !== url) return;
      if (err.status === 409) {
        S.open.steps.set('site', { state: 'active', message: 'Another analysis is running. Starting as soon as it finishes.' });
        paintProgress();
        await sleep(6000);
        continue;
      }
      S.open.error = err.message;
      break;
    }
  }
  if (!S.open || S.open.url !== url) return;
  if (!result) {
    S.open.state = 'failed';
    paintSide();
    return;
  }
  await loadProjects();
  await finishOpen(result.slug, url);
}

async function analyzeOnce(url, onDone) {
  await stream('/api/v1/sites', { site: url }, (event) => {
    if (!S.open || S.open.url !== url) return;
    if (event.type === 'step') {
      const key = event.step === 'save' ? 'model' : event.step;
      if (S.open.current && S.open.current !== key) {
        const prev = S.open.steps.get(S.open.current);
        if (prev && prev.state === 'active') prev.state = 'done';
      }
      if (event.state === 'active') S.open.current = key;
      S.open.steps.set(key, { state: event.state, message: event.message, done: event.done, total: event.total, searches: event.searches, competitors: event.competitors });
      if (event.state === 'skipped' && key === 'google') {
        for (const name of ['pages', 'model', 'competitors']) S.open.steps.set(name, { state: 'skipped', message: 'Needs Google results for this site.' });
      }
      paintProgress();
    } else if (event.type === 'project') {
      S.open.slug = event.slug;
    } else if (event.type === 'done') {
      onDone(event);
    } else if (event.type === 'error') {
      S.open.error = event.error;
    }
  });
}

async function finishOpen(slug, url) {
  try {
    await openSite(slug, { keepDrafts: true });
  } catch (err) {
    S.open.state = 'failed';
    S.open.error = err.message;
    paintSide();
    return;
  }
  S.open = null;
  S.screen = 'editor';
  S.params = new URLSearchParams({ page: url });
  history.replaceState({}, '', pathFor('editor', { page: url }));
  renderBar();
  render();
}

function progressTile() {
  const O = S.open;
  const tile = h('section', { class: 'tile progress-tile' });
  const timer = Math.round((Date.now() - O.started) / 1000);
  tile.append(h('div', { class: 'tile-head' }, h('h2', { text: O.state === 'failed' ? `Couldn't finish ${O.domain}` : `Analyzing ${O.domain}` }), h('span', { class: 'aside num', text: O.state === 'failed' ? '' : `${timer} s` })));
  if (O.state === 'failed') {
    tile.append(h('p', { class: 'tile-sub', text: O.error || 'The analysis stopped.' }), h('div', { class: 'result-actions' }, h('button', { class: 'btn btn-dark', type: 'button', onClick: () => openUrl(O.url) }, icon('refresh'), 'Try again')));
    return tile;
  }
  const list = h('div', { class: 'steps compact' });
  for (const [key, label] of STEPS) {
    const info = O.steps.get(key) || { state: 'pending' };
    const box = h('span', { class: 'box' });
    if (info.state === 'active') box.append(h('span', { class: 'spinner', 'aria-hidden': 'true' }));
    else if (info.state === 'done') box.append(icon('check'));
    const row = h('div', { class: `step ${info.state || 'pending'}` }, box, h('div', { class: 'body' }, h('div', { class: 't', text: label }), info.message && info.state !== 'pending' ? h('div', { class: 'c', text: info.message }) : null));
    if (info.state === 'active' && info.total) row.querySelector('.body').append(bar(info.done / info.total));
    if (info.searches && info.state === 'done') row.querySelector('.body').append(h('div', { class: 'found' }, info.searches.slice(0, 8).map((q) => h('span', { class: 'chip chip-gray', text: q }))));
    list.append(row);
  }
  tile.append(h('p', { class: 'tile-sub', text: 'Edit the page while this runs. Testing unlocks when it finishes.' }), list);
  if (timer >= 60) tile.append(h('p', { class: 'slow-note', role: 'status', text: 'This site is taking a while. It keeps running, and your edits are safe.' }));
  return tile;
}

function paintProgress() {
  const old = main.querySelector('.progress-tile');
  if (old && S.open) old.replaceWith(progressTile());
}

setInterval(() => {
  if (S.open && S.open.state === 'analyzing') {
    const aside = main.querySelector('.progress-tile .aside');
    const seconds = Math.round((Date.now() - S.open.started) / 1000);
    if (aside) aside.textContent = `${seconds} s`;
    if (seconds === 60) paintProgress();
  }
}, 1000);

/* While a new site is analyzed the editor's side column leads with the live steps, and Publish stays available. */
registerEditorPanel({
  key: 'open-progress',
  order: 5,
  render(ctx) {
    if (S.report || !S.open) return null;
    const box = h('div', { class: 'side-stack' }, progressTile());
    box.append(h('div', { class: 'result-actions' }, publishButton(ctx.draft)));
    return box;
  },
});

/* ------------------------------------------------------------------ start */

Object.assign(shell, { route, render, renderBar, renderLogin, renderProblem });
/* Everything registered up to here is built in; plugin modules run next, and a plugin key that matches a built-in
   one logs a warning (see hooks.js). */
sealBuiltIns();
paintOffline();

/* Module scripts run in page order before DOMContentLoaded, and the shell lists plugins after this file, so every
   plugin has registered by the time the first screen renders. */
if (document.readyState === 'complete') boot();
else document.addEventListener('DOMContentLoaded', () => boot(), { once: true });
