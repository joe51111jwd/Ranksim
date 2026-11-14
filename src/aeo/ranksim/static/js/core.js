/* Ranksim Studio core: DOM helpers, icons, the API client, shared state, the router, toasts, sheets and the shared
   building blocks every screen uses. No screen code lives here. Every number on screen comes from /api/v1.
   Predicted positions are model estimates among stored pages, never a Google rank. */

import { call, screen } from './hooks.js';

export const CSRF = (document.getElementById('csrf') || {}).value || '';
export const MODE = document.documentElement.dataset.mode || 'local';
export const SVGNS = 'http://www.w3.org/2000/svg';
export const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
export const main = document.getElementById('main');

/* App-level functions that live in app.js. app.js fills these in when it loads; plugins may call them at run time.
   shell.render() repaints the current screen, shell.route() re-reads the address, shell.renderBar() the top bar. */
export const shell = {
  route: async () => {},
  render: () => {},
  renderBar: () => {},
  renderLogin: () => {},
  renderProblem: () => {},
};

export const rerender = () => shell.render();

/* ------------------------------------------------------------------ helpers */

export const ICONS = {
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  close: '<path d="M17 7 7 17M7 7l10 10"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
  desktop: '<rect x="3" y="4.5" width="18" height="12" rx="2"/><path d="M8.5 20h7M12 16.5V20"/>',
  mobile: '<rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M11 18.5h2"/>',
  doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
  next: '<path d="m9 6 6 6-6 6"/>',
  warn: '<path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  pencil: '<path d="M4 20h4L18.5 9.5a2.1 2.1 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  x: '<path d="M7 7l10 10M17 7 7 17"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/>',
  out: '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="M10 16l-4-4 4-4M6 12h10"/>',
  code: '<path d="m9 8-4 4 4 4M15 8l4 4-4 4"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
};

/* An inline SVG icon from ICONS (plugins may add their own: ICONS.name = '<path .../>'). */
export function icon(name, cls) {
  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  if (cls) svg.setAttribute('class', cls);
  svg.innerHTML = ICONS[name] || '';
  return svg;
}

const PROPS = new Set(['value', 'checked', 'disabled', 'hidden', 'open', 'selected', 'srcdoc']);

/* h('div', { class, text, dataset, onClick, ...attributes }, ...children). Children may be nested arrays, strings,
   Nodes, or null/false (skipped). Text is always set as text, never parsed as HTML. */
export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value == null || value === false) continue;
      if (key === 'class') el.className = value;
      else if (key === 'text') el.textContent = value;
      else if (key === 'dataset') Object.assign(el.dataset, value);
      else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
      else if (PROPS.has(key)) el[key] = value;
      else el.setAttribute(key, value === true ? '' : String(value));
    }
  }
  put(el, kids);
  return el;
}

export function put(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const enc = encodeURIComponent;

export function debounce(fn, ms) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export function esc(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
}

export const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + 's'}`;
export const pct = (value) => (value == null ? null : `${Math.round(value * 100)}%`);
export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch (_err) {
    return url || '';
  }
}

export function pathOf(url) {
  try {
    return new URL(url).pathname || '/';
  } catch (_err) {
    return url || '';
  }
}

export function looseKey(url) {
  try {
    const u = new URL(url);
    return (u.hostname.replace(/^www\./, '') + u.pathname.replace(/\/+$/, '')).toLowerCase();
  } catch (_err) {
    return String(url || '').toLowerCase();
  }
}

export function initial(text) {
  const match = String(text || '').replace(/^www\./, '').match(/[a-z0-9]/i);
  return match ? match[0] : '?';
}

/* A thin progress track filled to `fraction` (0 to 1). cls: null (blue), 'dark' or 'gray'. */
export function bar(fraction, cls) {
  const fill = h('i', { class: cls || null });
  const track = h('div', { class: 'track' }, fill);
  requestAnimationFrame(() => {
    fill.style.width = `${Math.max(0, Math.min(100, Math.round(fraction * 100)))}%`;
  });
  return track;
}

/* A ring meter: value 0 to 1, a big label in the middle and a small one under it. */
export function ring(value, big, small) {
  const r = 56;
  const c = 2 * Math.PI * r;
  const make = (cls) => {
    const circle = document.createElementNS(SVGNS, 'circle');
    circle.setAttribute('class', cls);
    circle.setAttribute('cx', '66');
    circle.setAttribute('cy', '66');
    circle.setAttribute('r', String(r));
    return circle;
  };
  const track = make('track');
  const fill = make('fill');
  fill.setAttribute('stroke-dasharray', String(c));
  fill.setAttribute('stroke-dashoffset', String(c));
  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('viewBox', '0 0 132 132');
  svg.append(track, fill);
  requestAnimationFrame(() => fill.setAttribute('stroke-dashoffset', String(c * (1 - (value || 0)))));
  return h('div', { class: 'ring' }, svg, h('div', { class: 'mid' }, h('b', { text: big }), h('span', { text: small })));
}

/* ------------------------------------------------------------------ api */

/* JSON request. Every method but GET and HEAD sends the x-ranksim-csrf header, with or without a body. Errors carry
   .status and .problems. A 401 that asks for a login shows the sign-in screen. */
export async function request(method, path, body) {
  const options = { method, headers: { accept: 'application/json' }, credentials: 'same-origin' };
  if (!/^(GET|HEAD)$/i.test(method)) options.headers['x-ranksim-csrf'] = CSRF;
  if (body !== undefined) {
    options.headers['content-type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (_err) {
    throw new Error('Could not reach Ranksim. Check the connection and try again.');
  }
  let data = null;
  try {
    data = await response.json();
  } catch (_err) {
    data = null;
  }
  if (response.status === 401 && data && data.login) {
    if (S.session) S.session.signed_in = false;
    shell.renderLogin();
    const error = new Error('Sign in to continue.');
    error.status = 401;
    throw error;
  }
  if (!response.ok) {
    const error = new Error((data && data.error) || `Ranksim answered ${response.status}. Try again.`);
    error.status = response.status;
    error.problems = (data && data.problems) || [];
    throw error;
  }
  return data;
}

export const get = (path) => request('GET', path);
export const post = (path, body) => request('POST', path, body);
/* The API client: api.get(path), api.post(path, body), api.request(method, path, body), api.stream(path, body, onEvent). */
export const api = { request, get, post, stream };

/* Lines of JSON from a streaming POST. onEvent gets each object as it arrives. Errors carry .status. */
export async function stream(path, body, onEvent) {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'content-type': 'application/json', 'x-ranksim-csrf': CSRF, accept: 'application/x-ndjson' },
    body: JSON.stringify(body || {}),
  });
  if (!response.ok) {
    let data = null;
    try {
      data = await response.json();
    } catch (_err) {
      data = null;
    }
    if (response.status === 401 && data && data.login) shell.renderLogin();
    const err = new Error((data && data.error) || `Ranksim answered ${response.status}.`);
    err.status = response.status;
    throw err;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, cut).trim();
      buffer = buffer.slice(cut + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer.trim()));
}

/* ------------------------------------------------------------------ state */

export const S = {
  session: null,
  projects: [],
  demoSlug: 'synthetic-demo',
  publicDemo: MODE === 'public',
  slug: null,
  screen: 'overview',
  params: new URLSearchParams(),
  report: null,
  project: null,
  compare: new Map(),
  pages: new Map(),
  drafts: new Map(),
  results: new Map(),
  issueFilter: 'all',
  sheet: null,
  open: null,
};

export async function loadProjects() {
  const list = await api.get('/api/v1/projects');
  S.projects = list.projects || [];
  S.demoSlug = list.demo_slug || S.demoSlug;
}

function loaded() {
  call('report-loaded', { report: S.report, project: S.project, slug: S.slug });
}

export async function openSite(slug, { keepDrafts = false } = {}) {
  S.slug = slug;
  S.compare.clear();
  S.pages.clear();
  if (!keepDrafts) S.drafts.clear();
  S.results.clear();
  S.report = null;
  S.project = null;
  shell.renderBar();
  if (!keepDrafts) clear(main).append(skeleton());
  const [report, project] = await Promise.all([api.get(`/api/v1/projects/${enc(slug)}/report`), api.get(`/api/v1/projects/${enc(slug)}`)]);
  S.report = report;
  S.project = project;
  loaded();
}

export async function reloadSite() {
  if (!S.slug) return;
  S.compare.clear();
  S.pages.clear();
  const [report, project] = await Promise.all([api.get(`/api/v1/projects/${enc(S.slug)}/report`), api.get(`/api/v1/projects/${enc(S.slug)}`)]);
  S.report = report;
  S.project = project;
  await loadProjects();
  loaded();
}

/* ------------------------------------------------------------------ routing */

const ALIASES = { studio: 'editor', rankings: 'searches', data: 'settings' };

/* Where the address points: { slug, screen, params, unknown }. screen is 'home', 'open' (/edit?url=) or a registered
   key. A key no screen registered gives 'overview' with unknown: true. */
export function parseLocation() {
  const parts = location.pathname.split('/').filter(Boolean);
  const params = new URLSearchParams(location.search);
  if (parts[0] === 'edit') return { slug: null, screen: 'open', params };
  if (parts[0] === 'app' && parts[1]) {
    const raw = ALIASES[parts[2]] || parts[2];
    const known = !raw || !!screen(raw);
    return { slug: decodeURIComponent(parts[1]), screen: raw && known ? raw : 'overview', params, unknown: !known };
  }
  return { slug: null, screen: 'home', params };
}

export function pathFor(key, params) {
  const query = params ? new URLSearchParams(params).toString() : '';
  return `/app/${enc(S.slug)}${key && key !== 'overview' ? `/${key}` : ''}${query ? `?${query}` : ''}`;
}

export function navigate(path, { replace = false } = {}) {
  if (path !== location.pathname + location.search) history[replace ? 'replaceState' : 'pushState']({}, '', path);
  closeSheet(true);
  window.scrollTo(0, 0);
  shell.route();
}

export function go(key, params) {
  navigate(pathFor(key, params));
}

/* An <a> to a screen of the open site that navigates in place (modified clicks open a new tab as usual). */
export function link(key, label, props = {}, params) {
  return h('a', {
    href: pathFor(key, params),
    ...props,
    onClick: (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1) return;
      event.preventDefault();
      go(key, params);
    },
  }, label);
}

window.addEventListener('popstate', () => {
  closeSheet(true);
  shell.route();
});

/* ------------------------------------------------------------------ toast */

let toastTimer = 0;
export function toast(message) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.hidden = false;
  el.style.animation = 'none';
  void el.offsetWidth;
  el.style.animation = '';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 5200);
}

/* ------------------------------------------------------------------ sheet */

let lastFocus = null;

/* A side sheet (a bottom sheet on phones). build(sheet, closeBtn) fills it; put closeBtn in the sheet head. */
export function openSheet(build) {
  closeSheet(true);
  lastFocus = document.activeElement;
  const scrim = h('div', { class: 'scrim', onClick: () => closeSheet() });
  const closeBtn = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Close', onClick: () => closeSheet() }, icon('close'));
  const sheet = h('aside', { class: 'sheet', role: 'dialog', 'aria-modal': 'true' });
  build(sheet, closeBtn);
  document.body.append(scrim, sheet);
  S.sheet = { scrim, sheet };
  requestAnimationFrame(() => {
    scrim.classList.add('open');
    sheet.classList.add('open');
  });
  closeBtn.focus({ preventScroll: true });
}

export function closeSheet(instant) {
  const current = S.sheet;
  if (!current) return;
  S.sheet = null;
  const done = () => {
    current.scrim.remove();
    current.sheet.remove();
  };
  if (instant || reduceMotion.matches) done();
  else {
    current.scrim.classList.remove('open');
    current.sheet.classList.remove('open');
    setTimeout(done, 400);
  }
  if (!instant && lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && S.sheet) closeSheet();
});

/* ------------------------------------------------------------------ page parts */

/* A pull label is a date when one was recorded, or a phrase such as "pull date not recorded". Show dates only. */
export function pulledText(prefix) {
  const pulled = S.report && S.report.pulled;
  return pulled && /\d{4}/.test(pulled) ? `${prefix}${pulled}` : '';
}

export function footer() {
  const left = h('div', { class: 'fl' });
  if (S.report && S.report.data && S.report.data.kind === 'synthetic') left.append(h('span', { text: 'Demo data, not Google results' }));
  else if (pulledText('')) left.append(h('span', { text: pulledText('Google results from ') }));
  left.append(h('span', { text: 'Predicted positions are model estimates among the pages shown, never a Google rank.' }));
  return h('footer', { class: 'foot' }, left, h('div', { class: 'fl' }, h('span', { text: 'Ranksim' })));
}

/* The standard screen: a heading, an optional line under it, optional actions on the right, then the body. */
export function page(title, sub, actions, ...body) {
  return h('div', { class: 'wrap screen' },
    h('div', { class: 'head' },
      h('div', null, h('h1', { text: title }), sub ? h('p', { class: 'sub', text: sub }) : null),
      actions ? h('div', { class: 'actions' }, actions) : null),
    body);
}

/* Replaces the main area with these nodes followed by the footer. */
export function paint(...nodes) {
  clear(main).append(...nodes.flat(Infinity).filter(Boolean), footer());
}

/* The note shown when the server has no search data for this site. */
export function noSearchData() {
  const R = S.report;
  if (R.search_data) return null;
  return h('div', { class: 'callout span-4' }, icon('warn'), h('span', null,
    h('b', { text: 'Rankings need search data. ' }),
    R.google_ready ? 'Refresh this site in Settings to pull Google results.' : 'Search data isn\'t connected on this server yet, so this report covers your pages only. Add SERPER_API_KEY to the server once and every site gets rankings and competitors.'));
}

export const SEVERITY = { high: 'High', medium: 'Medium', low: 'Low' };

/* A list of Google results: position, site initial, title and domain. Your page is highlighted, tracked competitors
   are marked, and every result opens in the editor (any page can be edited; publishing needs ownership).
   opts.q is the search the editor should test against (defaults to ?q= in the address). */
export function serpList(items, opts = {}) {
  const q = opts.q || (S.params && S.params.get('q')) || '';
  return h('ol', { class: 'serp', 'aria-label': q ? `Top results for ${q}` : 'Top results' }, (items || []).map((item) => h('li', { class: `rowcard${item.you ? ' you' : item.competitor ? ' rival' : ''}` },
    h('span', { class: `pos${item.you ? ' you' : ''}`, text: String(item.position) }),
    h('span', { class: `fav${item.you ? ' you' : item.competitor ? ' dark' : ''}`, text: initial(item.domain), 'aria-hidden': 'true' }),
    h('div', { class: 'body' }, h('div', { class: 't', text: item.title || item.domain }),
      h('div', { class: 'm' }, h('span', { text: item.domain }),
        item.you ? h('span', { class: 'label label-you', text: 'Your page' }) : item.competitor ? h('span', { class: 'label label-plain label-rival', text: 'Competitor' }) : null)),
    item.url && S.slug ? h('div', { class: 'right' }, link('editor', icon('pencil'), {
      class: 'icon-btn',
      title: 'Open in editor',
      'aria-label': `Open ${item.domain} in the editor`,
    }, q ? { page: item.url, q } : { page: item.url })) : null)));
}

/* A placeholder in the bento's shape while a site loads: gray blocks, no spinner wall. Still under reduced motion. */
export function skeleton() {
  const tile = (cls) => h('div', { class: `tile ${cls || ''}` }, h('span', { class: 'sk sk-line' }), h('span', { class: 'sk sk-big' }), h('span', { class: 'sk sk-block' }));
  return h('div', { class: 'wrap screen skeleton', 'aria-busy': 'true', 'aria-label': 'Loading' },
    h('div', { class: 'head' }, h('div', null, h('span', { class: 'sk sk-big' }), h('span', { class: 'sk sk-line' }))),
    h('div', { class: 'bento' }, tile('white span-2'), tile('black'), tile(), tile('span-2 tall'), tile('white span-2 tall')),
    h('span', { class: 'sr-only', role: 'status', text: 'Loading' }));
}

/* A centered empty or error state: a short title, one sentence, and the buttons for the next step. */
export function emptyState(title, text, ...actions) {
  return h('div', { class: 'empty-state' }, h('h2', { text: title }), text ? h('p', { text }) : null,
    actions.length ? h('div', { class: 'form-actions' }, actions) : null);
}

/* The spinner shown while a screen loads. */
export function bootSpinner() {
  return h('div', { class: 'boot' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }));
}
