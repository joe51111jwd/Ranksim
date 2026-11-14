/* Ranksim. Type a site; Ranksim finds its searches, pulls Google, finds competitors, and reports what to fix.
   Then edit a page right in the preview and test where it is PREDICTED to land before you publish.
   Every number comes from /api/v1. Predicted positions are model estimates among stored pages, never a Google rank. */
(() => {
  'use strict';

  const CSRF = (document.getElementById('csrf') || {}).value || '';
  const MODE = document.documentElement.dataset.mode || 'local';
  const SVGNS = 'http://www.w3.org/2000/svg';
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  /* ------------------------------------------------------------------ helpers */

  const ICONS = {
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

  function icon(name, cls) {
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

  function h(tag, props, ...kids) {
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

  function put(el, kids) {
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  function clear(el) {
    while (el.firstChild) el.firstChild.remove();
    return el;
  }

  const $ = (sel, root = document) => root.querySelector(sel);
  const enc = encodeURIComponent;

  function debounce(fn, ms) {
    let timer = 0;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  }

  function esc(text) {
    return String(text).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
  }

  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + 's'}`;
  const pct = (value) => (value == null ? null : `${Math.round(value * 100)}%`);

  function hostOf(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, '');
    } catch (_err) {
      return url || '';
    }
  }

  function pathOf(url) {
    try {
      return new URL(url).pathname || '/';
    } catch (_err) {
      return url || '';
    }
  }

  function looseKey(url) {
    try {
      const u = new URL(url);
      return (u.hostname.replace(/^www\./, '') + u.pathname.replace(/\/+$/, '')).toLowerCase();
    } catch (_err) {
      return String(url || '').toLowerCase();
    }
  }

  function initial(text) {
    const match = String(text || '').replace(/^www\./, '').match(/[a-z0-9]/i);
    return match ? match[0] : '?';
  }

  function bar(fraction, cls) {
    const fill = h('i', { class: cls || null });
    const track = h('div', { class: 'track' }, fill);
    requestAnimationFrame(() => {
      fill.style.width = `${Math.max(0, Math.min(100, Math.round(fraction * 100)))}%`;
    });
    return track;
  }

  /* ------------------------------------------------------------------ api */

  async function request(method, path, body) {
    const options = { method, headers: { accept: 'application/json' }, credentials: 'same-origin' };
    if (body !== undefined) {
      options.headers['content-type'] = 'application/json';
      options.headers['x-ranksim-csrf'] = CSRF;
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
      renderLogin();
      throw new Error('Sign in to continue.');
    }
    if (!response.ok) {
      const error = new Error((data && data.error) || `Ranksim answered ${response.status}. Try again.`);
      error.problems = (data && data.problems) || [];
      throw error;
    }
    return data;
  }

  const api = { get: (path) => request('GET', path), post: (path, body) => request('POST', path, body) };

  /* Lines of JSON from a streaming POST. onEvent gets each object as it arrives. */
  async function stream(path, body, onEvent) {
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
      if (response.status === 401 && data && data.login) renderLogin();
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

  /* ------------------------------------------------------------------ state and routing */

  const S = {
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
  };

  const main = document.getElementById('main');
  const barInner = $('#bar .bar-inner');
  const SCREENS = ['overview', 'issues', 'searches', 'competitors', 'editor', 'settings'];
  const ALIASES = { studio: 'editor', rankings: 'searches', data: 'settings' };
  const NAV = [['overview', 'Overview'], ['issues', 'Issues'], ['searches', 'Searches'], ['competitors', 'Competitors'], ['editor', 'Editor']];

  function parseLocation() {
    const parts = location.pathname.split('/').filter(Boolean);
    const params = new URLSearchParams(location.search);
    if (parts[0] === 'edit') return { slug: null, screen: 'open', params };
    if (parts[0] === 'app' && parts[1]) {
      const raw = ALIASES[parts[2]] || parts[2];
      return { slug: decodeURIComponent(parts[1]), screen: SCREENS.includes(raw) ? raw : 'overview', params };
    }
    return { slug: null, screen: 'home', params };
  }

  function pathFor(screen, params) {
    const query = params ? new URLSearchParams(params).toString() : '';
    return `/app/${enc(S.slug)}${screen && screen !== 'overview' ? `/${screen}` : ''}${query ? `?${query}` : ''}`;
  }

  function navigate(path, { replace = false } = {}) {
    if (path !== location.pathname + location.search) history[replace ? 'replaceState' : 'pushState']({}, '', path);
    closeSheet(true);
    window.scrollTo(0, 0);
    route();
  }

  function go(screen, params) {
    navigate(pathFor(screen, params));
  }

  function link(screen, label, props = {}, params) {
    return h('a', {
      href: pathFor(screen, params),
      ...props,
      onClick: (event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1) return;
        event.preventDefault();
        go(screen, params);
      },
    }, label);
  }

  window.addEventListener('popstate', () => {
    closeSheet(true);
    route();
  });

  let toastTimer = 0;
  function toast(message) {
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

  async function loadProjects() {
    const list = await api.get('/api/v1/projects');
    S.projects = list.projects || [];
    S.demoSlug = list.demo_slug || S.demoSlug;
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

  async function openSite(slug, { keepDrafts = false } = {}) {
    S.slug = slug;
    S.compare.clear();
    S.pages.clear();
    if (!keepDrafts) S.drafts.clear();
    S.results.clear();
    S.report = null;
    S.project = null;
    renderBar();
    if (!keepDrafts) clear(main).append(h('div', { class: 'boot' }, h('span', { class: 'spinner', 'aria-hidden': 'true' })));
    const [report, project] = await Promise.all([api.get(`/api/v1/projects/${enc(slug)}/report`), api.get(`/api/v1/projects/${enc(slug)}`)]);
    S.report = report;
    S.project = project;
  }

  async function reloadSite() {
    if (!S.slug) return;
    S.compare.clear();
    S.pages.clear();
    const [report, project] = await Promise.all([api.get(`/api/v1/projects/${enc(S.slug)}/report`), api.get(`/api/v1/projects/${enc(S.slug)}`)]);
    S.report = report;
    S.project = project;
    await loadProjects();
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
      for (const [key, label] of NAV) nav.append(link(key, label, { 'aria-current': S.screen === key ? 'page' : null }));
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

  /* A pull label is a date when one was recorded, or a phrase such as "pull date not recorded". Show dates only. */
  function pulledText(prefix) {
    const pulled = S.report && S.report.pulled;
    return pulled && /\d{4}/.test(pulled) ? `${prefix}${pulled}` : '';
  }

  function footer() {
    const left = h('div', { class: 'fl' });
    if (S.report && S.report.data && S.report.data.kind === 'synthetic') left.append(h('span', { text: 'Demo data, not Google results' }));
    else if (pulledText('')) left.append(h('span', { text: pulledText('Google results from ') }));
    left.append(h('span', { text: 'Predicted positions are model estimates among the pages shown, never a Google rank.' }));
    return h('footer', { class: 'foot' }, left, h('div', { class: 'fl' }, h('span', { text: 'Ranksim' })));
  }

  function page(title, sub, actions, ...body) {
    return h('div', { class: 'wrap screen' },
      h('div', { class: 'head' },
        h('div', null, h('h1', { text: title }), sub ? h('p', { class: 'sub', text: sub }) : null),
        actions ? h('div', { class: 'actions' }, actions) : null),
      body);
  }

  function renderProblem(title, text) {
    renderBar();
    clear(main).append(h('div', { class: 'wrap screen' }, h('div', { class: 'tile white login' }, h('h1', { text: title }), h('p', { text: text || 'Something went wrong.' }),
      h('div', { class: 'form' }, h('button', { class: 'btn btn-dark btn-large', type: 'button', onClick: () => location.reload() }, 'Try again')))));
  }

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

  function renderHome() {
    document.title = 'Ranksim';
    const input = h('input', { type: 'text', inputmode: 'url', autocomplete: 'off', spellcheck: 'false', placeholder: 'yoursite.com or any page address', 'aria-label': 'Page address' });
    const submit = h('button', { class: 'btn btn-primary', type: 'submit' }, 'Open', icon('arrow'));
    const form = h('form', { class: 'composer' }, icon('globe', 'lead'), input, submit);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const site = input.value.trim();
      if (!site) {
        input.focus();
        return;
      }
      navigate(`/edit?url=${enc(/^https?:\/\//i.test(site) ? site : `https://${site}`)}`);
    });
    const hero = h('section', { class: 'hero' },
      h('h1', { text: 'Which page should we look at?' }),
      h('p', { class: 'sub', text: 'Paste any address, yours or a competitor\'s. You get the live page to edit right away, while Ranksim finds its searches, who it competes with, and what to fix.' }),
      S.publicDemo ? h('p', { class: 'hints', text: 'This demo is read only.' }) : form,
      S.publicDemo ? null : h('div', { class: 'hints' }, h('span', { text: 'Edit any page. Publishing needs you to own the site.' })));
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
        h('div', { class: 'top' }, h('span', { class: 'fav dark', text: initial(p.site) }), h('div', { class: 'who' }, h('div', { class: 'name', text: p.name }), h('div', { class: 'host', text: p.site }))),
        h('div', { class: 'meta' }, h('span', { text: plural(p.queries, 'search', 'searches') }), p.built ? h('span', { class: 'label label-ok', text: 'Report ready' }) : h('span', { class: 'label label-medium', text: 'Rankings pending' }), p.dataset === 'synthetic' ? h('span', { class: 'label label-demo', text: 'Demo' }) : null)));
      }
      body.push(h('section', { class: 'sites' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Your sites' })), grid));
    } else if (!S.publicDemo) {
      body.push(h('div', { class: 'hints' }, h('button', { class: 'btn btn-plain', type: 'button', onClick: loadDemo }, 'Or open the demo')));
    }
    clear(main).append(h('div', { class: 'wrap screen' }, body), footer());
    if (!S.publicDemo) setTimeout(() => input.focus(), 50);
  }

  async function loadDemo() {
    clear(main).append(h('div', { class: 'boot' }, h('span', { class: 'spinner', 'aria-hidden': 'true' })));
    try {
      await api.post('/api/v1/demo', {});
      await loadProjects();
      navigate(`/app/${enc(S.demoSlug)}`);
    } catch (err) {
      toast(err.message);
      renderHome();
    }
  }

  /* ------------------------------------------------------------------ analysis: live steps */

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
          await new Promise((resolve) => setTimeout(resolve, 6000));
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
    return tile;
  }

  function paintProgress() {
    const old = main.querySelector('.progress-tile');
    if (old && S.open) old.replaceWith(progressTile());
  }

  setInterval(() => {
    if (S.open && S.open.state === 'analyzing') {
      const aside = main.querySelector('.progress-tile .aside');
      if (aside) aside.textContent = `${Math.round((Date.now() - S.open.started) / 1000)} s`;
    }
  }, 1000);

  /* ------------------------------------------------------------------ render dispatch */

  function render() {
    closeSheet(true);
    if (!S.report) {
      if (S.open) renderEditor();
      return;
    }
    const titles = { overview: 'Overview', issues: 'Issues', searches: 'Searches', competitors: 'Competitors', editor: 'Editor', settings: 'Settings' };
    document.title = `${titles[S.screen]} · ${S.report.name} · Ranksim`;
    if (S.screen === 'issues') renderIssues();
    else if (S.screen === 'searches') renderSearches();
    else if (S.screen === 'competitors') renderCompetitors();
    else if (S.screen === 'editor') renderEditor();
    else if (S.screen === 'settings') renderSettings();
    else renderOverview();
  }

  function noSearchData() {
    const R = S.report;
    if (R.search_data) return null;
    return h('div', { class: 'callout span-4' }, icon('warn'), h('span', null,
      h('b', { text: 'Rankings need search data. ' }),
      R.google_ready ? 'Refresh this site in Settings to pull Google results.' : 'Search data isn\'t connected on this server yet, so this report covers your pages only. Add SERPER_API_KEY to the server once and every site gets rankings and competitors.'));
  }

  /* ------------------------------------------------------------------ overview */

  function renderOverview() {
    const R = S.report;
    const P = S.project || {};
    const K = R.kpis;
    const grid = h('div', { class: 'bento' });
    const callout = noSearchData();
    if (callout) grid.append(callout);

    const squares = h('div', { class: 'squares', 'aria-hidden': 'true' }, R.rankings.map((row) => h('i', { class: row.position ? (row.position <= 3 ? 'top' : 'on') : null, title: `${row.q}: ${row.position ? `#${row.position}` : 'not in top 10'}` })));
    grid.append(h('section', { class: 'tile white span-2' },
      h('div', { class: 'tile-k', text: 'On the first page of Google' }),
      h('div', { class: 'tile-big' }, R.search_data ? String(K.top10) : '?', h('small', { text: `of ${plural(K.searches, 'search', 'searches')}` })),
      squares,
      h('div', { class: 'legend' }, h('span', { class: 'l-top', text: 'Top 3' }), h('span', { class: 'l-on', text: 'Top 10' }), h('span', { text: 'Not in top 10' }))));

    grid.append(h('section', { class: 'tile black' },
      h('div', { class: 'tile-k', text: 'Average position' }),
      h('div', { class: 'tile-big' }, K.average != null ? `#${K.average}` : 'None'),
      h('div', { class: 'tile-sub', text: K.top10 ? `Across the ${plural(K.top10, 'search', 'searches')} you rank for` : 'You are not in the top 10 yet' })));

    const acc = P.model && P.model.pairwise != null ? P.model.pairwise : null;
    grid.append(h('section', { class: 'tile' },
      h('div', { class: 'tile-k', text: 'Same-day backtest' }),
      ring(acc, acc != null ? pct(acc) : 'n/a', 'pairs right'),
      h('div', { class: 'tile-sub', text: acc != null ? 'Each search left out of training, same Google snapshot. 50% is a coin flip. Not forward accuracy.' : 'Appears once rankings are pulled.' })));

    const issues = h('div', { class: 'rows' });
    for (const issue of R.issues.slice(0, 6)) issues.append(issueRow(issue, true));
    if (!R.issues.length) issues.append(h('div', { class: 'empty-note', text: 'No issues found on the pages we read.' }));
    grid.append(h('section', { class: 'tile span-2 row-2' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Top issues' }), link('issues', `All ${R.issues.length}`, { class: 'btn btn-plain' })),
      issues));

    const ranks = h('div', { class: 'bars' });
    const sorted = R.rankings.slice().sort((a, b) => (a.position || 99) - (b.position || 99));
    for (const row of sorted.slice(0, 8)) {
      ranks.append(h('div', { class: `barrow${row.position ? ' you' : ''}` },
        h('div', { class: 'top-line' }, h('span', { class: 'name', text: row.q }), h('span', { class: 'val', text: row.position ? `#${row.position}` : row.checked ? 'Not in top 10' : 'Not checked' })),
        bar(row.position ? (11 - row.position) / 10 : 0.02, row.position && row.position <= 3 ? 'dark' : null)));
    }
    grid.append(h('section', { class: 'tile span-2' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Where you rank' }), link('searches', 'All searches', { class: 'btn btn-plain' })),
      ranks));

    const comp = h('div', { class: 'bars' });
    const rivals = R.share.filter((row) => !row.you).slice(0, 6);
    const most = Math.max(1, ...R.share.map((row) => row.slots));
    const mine = R.share.find((row) => row.you);
    if (mine) comp.append(h('div', { class: 'barrow you' }, h('div', { class: 'top-line' }, h('span', { class: 'name', text: `${mine.domain} (you)` }), h('span', { class: 'val', text: `${mine.slots} spots, avg #${mine.average}` })), bar(mine.slots / most)));
    for (const row of rivals) {
      comp.append(h('div', { class: 'barrow' },
        h('div', { class: 'top-line' }, h('span', { class: 'name', text: row.domain }), h('span', { class: 'val', text: `${row.slots} spots, avg #${row.average}` })),
        bar(row.slots / most, row.competitor ? 'dark' : 'gray')));
    }
    grid.append(h('section', { class: 'tile span-2' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Who takes the top 10' }), link('competitors', 'Competitors', { class: 'btn btn-plain' })),
      rivals.length || mine ? comp : h('div', { class: 'empty-note', text: 'Competitors appear once rankings are pulled.' })));

    const sev = { high: 0, medium: 0, low: 0 };
    for (const issue of R.issues) sev[issue.severity] += 1;
    const worst = Math.max(1, sev.high, sev.medium, sev.low);
    grid.append(h('section', { class: 'tile' },
      h('div', { class: 'tile-k', text: 'Issues by priority' }),
      h('div', { class: 'bars', style: null },
        [['high', 'High', 'dark'], ['medium', 'Medium', null], ['low', 'Low', 'gray']].map(([key, label, cls]) => h('div', { class: 'barrow' },
          h('div', { class: 'top-line' }, h('span', { class: 'name', text: label }), h('span', { class: 'val', text: String(sev[key]) })),
          bar(sev[key] / worst, cls))))));
    const tracked = (R.competitors || []).length;
    grid.append(h('section', { class: 'tile' },
      h('div', { class: 'tile-k', text: 'Competitors tracked' }),
      h('div', { class: 'tile-big' }, String(tracked)),
      h('div', { class: 'tile-sub', text: tracked ? R.competitors.slice(0, 3).join(', ') : 'Found automatically once rankings are pulled' })));
    grid.append(h('section', { class: 'tile white' },
      h('div', { class: 'tile-k', text: 'Pages read' }),
      h('div', { class: 'tile-big' }, String(R.pages_read)),
      h('div', { class: 'tile-sub', text: R.pages_failed ? `${R.pages_failed} couldn't be read` : 'All readable' })));
    grid.append(h('section', { class: 'tile black' },
      h('div', { class: 'tile-k', text: 'Try a change' }),
      h('div', { class: 'tile-sub', text: 'Edit any page right in the preview and see where it would land before you publish.' }),
      h('div', { class: 'result-actions' }, link('editor', ['Open editor', icon('arrow')], { class: 'btn btn-primary' }))));

    clear(main).append(page(R.name, `${R.site} · ${plural(K.searches, 'search', 'searches')} tracked${pulledText(' · Google results from ')}`, null, grid), footer());
  }

  function ring(value, big, small) {
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

  /* ------------------------------------------------------------------ issues */

  const SEVERITY = { high: 'High', medium: 'Medium', low: 'Low' };

  function issueRow(issue, compact) {
    const meta = compact ? null : h('div', { class: 'm' },
      h('span', { class: `label label-${issue.severity}`, text: SEVERITY[issue.severity] }),
      issue.search ? h('span', { text: `"${issue.search}"` }) : null);
    return h('button', { class: 'rowcard', type: 'button', onClick: () => openIssue(issue) },
      h('span', { class: `dot ${issue.severity}` }),
      h('div', { class: 'body' }, h('div', { class: 't', text: issue.title }), h('div', { class: `c${compact ? ' one-line' : ''}`, text: issue.comment }), meta),
      icon('next', 'go'));
  }

  function renderIssues() {
    const R = S.report;
    const filters = h('div', { class: 'seg', role: 'group', 'aria-label': 'Filter issues' });
    const counts = { all: R.issues.length, high: 0, medium: 0, low: 0 };
    for (const issue of R.issues) counts[issue.severity] += 1;
    for (const [key, label] of [['all', 'All'], ['high', 'High'], ['medium', 'Medium'], ['low', 'Low']]) {
      filters.append(h('button', {
        type: 'button',
        'aria-pressed': String(S.issueFilter === key),
        onClick: () => {
          S.issueFilter = key;
          renderIssues();
        },
      }, `${label} ${counts[key]}`));
    }
    const shown = R.issues.filter((issue) => S.issueFilter === 'all' || issue.severity === S.issueFilter);
    const groups = new Map();
    for (const issue of shown) {
      const key = issue.page ? pathOf(issue.page) : 'Searches without a page';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(issue);
    }
    const body = h('div', { class: 'bento' });
    const callout = noSearchData();
    if (callout) body.append(callout);
    if (!shown.length) body.append(h('div', { class: 'empty-note span-4', text: 'Nothing here.' }));
    for (const [where, list] of groups) {
      body.append(h('section', { class: 'tile span-2' },
        h('div', { class: 'tile-head' }, h('h2', { class: 'one-line', text: where }), h('span', { class: 'aside', text: plural(list.length, 'issue') })),
        h('div', { class: 'rows' }, list.map((issue) => issueRow(issue, false)))));
    }
    clear(main).append(page('Issues', `${counts.high} high, ${counts.medium} medium and ${counts.low} low on ${R.site}`, filters, body), footer());
  }

  function openIssue(issue) {
    openSheet((sheet, closeBtn) => {
      sheet.append(h('div', { class: 'sheet-head' }, h('div', null, h('h2', { text: issue.title }), h('div', { class: 'sub', text: [issue.page ? pathOf(issue.page) : null, issue.search ? `"${issue.search}"` : null].filter(Boolean).join(' · ') })), closeBtn));
      const body = h('div', { class: 'sheet-body' });
      body.append(h('div', { class: 'tile white' }, h('span', { class: `label label-${issue.severity}`, text: `${SEVERITY[issue.severity]} priority` }), h('p', { class: 'reason', text: issue.comment })));
      if (issue.fix) body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-k', text: 'What to do' }), h('p', { class: 'reason', text: issue.fix })));
      if (issue.search) {
        const row = S.report.rankings.find((item) => item.q === issue.search);
        if (row && row.top.length) body.append(h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: `Top results for "${issue.search}"` })), serpList(row.top.slice(0, 5))));
      }
      if (issue.page || issue.search) {
        body.append(h('div', { class: 'form-actions' }, h('button', {
          class: 'btn btn-primary btn-large',
          type: 'button',
          onClick: () => {
            closeSheet(true);
            const params = {};
            if (issue.search) params.q = issue.search;
            if (issue.page) params.page = issue.page;
            go('editor', params);
          },
        }, icon('pencil'), issue.page ? 'Fix it in the editor' : 'Write this page in the editor')));
      }
      sheet.append(body);
    });
  }

  function serpList(items) {
    return h('div', { class: 'serp' }, items.map((item) => h('div', { class: `rowcard${item.you ? ' you' : ''}` },
      h('span', { class: `pos${item.you ? ' you' : ''}`, text: String(item.position) }),
      h('span', { class: `fav${item.you ? ' you' : ''}`, text: initial(item.domain) }),
      h('div', { class: 'body' }, h('div', { class: 't', text: item.title || item.domain }),
        h('div', { class: 'm' }, h('span', { text: item.domain }), item.you ? h('span', { class: 'label label-you', text: 'You' }) : item.competitor ? h('span', { class: 'label label-low', text: 'Competitor' }) : null)))));
  }

  /* ------------------------------------------------------------------ searches */

  function renderSearches() {
    const R = S.report;
    const want = S.params.get('q');
    const selected = R.rankings.find((row) => row.q === want) || R.rankings.slice().sort((a, b) => (a.position || 99) - (b.position || 99))[0];
    const list = h('div', { class: 'qlist' });
    for (const row of R.rankings) {
      list.append(h('button', {
        class: 'qrow',
        type: 'button',
        'aria-pressed': String(!!selected && row.q === selected.q),
        onClick: () => {
          history.replaceState({}, '', pathFor('searches', { q: row.q }));
          S.params = new URLSearchParams({ q: row.q });
          renderSearches();
        },
      },
      h('span', { class: 'q', text: row.q }),
      h('span', { class: `p${row.position ? '' : ' none'}`, text: row.position ? `#${row.position}` : row.checked ? 'Not in top 10' : 'Not checked' }),
      bar(row.position ? (11 - row.position) / 10 : 0.02, row.position && row.position <= 3 ? 'dark' : null)));
    }
    const detail = h('section', { class: 'tile' });
    if (selected) {
      const predicted = selected.predicted && selected.predicted.position != null ? `Model puts you #${selected.predicted.position} of ${selected.predicted.of}` : '';
      detail.append(h('div', { class: 'tile-head' }, h('h2', { class: 'one-line', text: selected.q }), h('span', { class: 'aside', text: predicted })));
      if (!selected.checked) detail.append(h('div', { class: 'empty-note', text: 'No Google results for this search yet.' }));
      else {
        if (!selected.position) detail.append(h('div', { class: 'callout gap-bottom' }, icon('warn'), h('span', { text: selected.url ? `${pathOf(selected.url)} is about this search but isn't in the top 10.` : 'No page on your site targets this search.' })));
        detail.append(serpList(selected.top));
      }
      detail.append(h('div', { class: 'result-actions' },
        h('button', { class: 'btn btn-dark', type: 'button', onClick: () => go('editor', selected.url ? { q: selected.q, page: selected.url } : { q: selected.q }) }, icon('pencil'), selected.position ? 'Improve your page' : 'Write a page for it')));
    }
    const callout = noSearchData();
    const body = h('div', { class: 'split' }, h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Your searches' }), h('span', { class: 'aside', text: `${R.kpis.top10} in top 10` })), list), detail);
    clear(main).append(page('Searches', 'The searches your pages are written for, and who holds the first page for each.', null, callout ? h('div', { class: 'bento gap-bottom' }, callout) : null, body), footer());
  }

  /* ------------------------------------------------------------------ competitors */

  function renderCompetitors() {
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

  /* ------------------------------------------------------------------ editor */

  const READER_CSS = `
:root{color-scheme:light}
html{background:#fff}
body{margin:0 auto;max-width:760px;padding:48px 32px 64px;font:17px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;color:#1b1b19;-webkit-font-smoothing:antialiased}
h1{font-size:38px;line-height:1.12;letter-spacing:-.025em;margin:0 0 14px;font-weight:700}
h2{font-size:25px;line-height:1.25;letter-spacing:-.015em;margin:40px 0 10px;font-weight:700}
h3{font-size:19px;margin:28px 0 6px;font-weight:650}
p{margin:0 0 16px;color:#333330}
time{display:inline-block;color:#8a8a84;font-size:14px;margin-bottom:6px}
a{color:#3358eb;text-decoration:none}
ul,ol{padding-left:24px;margin:0 0 16px}
table{width:100%;border-collapse:collapse;margin:24px 0;font-size:15px}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #e6e6e0}
th{font-weight:600;background:#f6f6f3}
nav{margin-top:40px;padding-top:20px;border-top:1px solid #e6e6e0;font-size:14px}
nav a{display:inline-block;margin:0 14px 8px 0}
img,video{max-width:100%;height:auto;border-radius:10px}
`;
  const EDIT_CSS = 'body{outline:none;caret-color:#3358eb}h1:hover,h2:hover,h3:hover,p:hover,li:hover,td:hover,th:hover{outline:1px dashed rgba(51,88,235,.4);outline-offset:3px;border-radius:3px}html{scrollbar-width:thin}';

  function asHtml(text) {
    if (/<\s*(html|body|p|h1|h2|div|title|article|section)\b/i.test(text)) return text;
    const blocks = text.split(/\n\s*\n/).map((block) => block.trim()).filter(Boolean);
    if (!blocks.length) return text;
    const lines = blocks[0].split('\n');
    const first = lines[0].trim().slice(0, 160);
    const restLine = lines.slice(1).join(' ').trim();
    const rest = lines.length > 1 && restLine ? [restLine] : [];
    const paras = rest.concat(blocks.slice(1)).map((block) => `<p>${esc(block.split(/\s+/).join(' '))}</p>`);
    return `<html><head><title>${esc(first)}</title></head><body><h1>${esc(first)}</h1>${paras.join('')}</body></html>`;
  }

  /* A page as the editor holds it: parsed once, unsafe parts removed, serialized the same way every time, so an
     unchanged page tests exactly like the original. */
  function cleanDoc(text) {
    const doc = new DOMParser().parseFromString(asHtml(text || ''), 'text/html');
    doc.querySelectorAll('script,noscript,iframe,frame,frameset,object,embed,base,meta[http-equiv]').forEach((node) => node.remove());
    settleStreamedContent(doc);
    for (const el of doc.querySelectorAll('*')) {
      for (const attr of Array.from(el.attributes)) {
        const name = attr.name.toLowerCase();
        if (name.startsWith('on') || name === 'contenteditable' || (/^(href|src|action|formaction|xlink:href|srcdoc)$/.test(name) && /^\s*(javascript|data:text\/html)/i.test(attr.value))) el.removeAttribute(attr.name);
      }
    }
    return doc;
  }

  /* React streaming (Next.js and others) sends a loading placeholder first and the real content later inside
     <div hidden id="S:n">, then a script swaps it into the <template id="B:n"> boundary. Scripts never run here, so
     do the swap: the page then reads the way a browser shows it. */
  function settleStreamedContent(doc) {
    for (const hidden of Array.from(doc.querySelectorAll('div[hidden][id^="S:"]'))) {
      const slot = doc.getElementById(`P:${hidden.id.slice(2)}`); // a segment: replaces its placeholder
      if (slot && slot.parentNode) {
        while (hidden.firstChild) slot.parentNode.insertBefore(hidden.firstChild, slot);
        slot.remove();
        hidden.remove();
        continue;
      }
      const boundary = doc.getElementById(`B:${hidden.id.slice(2)}`); // a boundary: replaces its fallback
      if (!boundary || !boundary.parentNode) continue;
      const parent = boundary.parentNode;
      let node = boundary.nextSibling;
      let depth = 0;
      while (node) {
        const next = node.nextSibling;
        if (node.nodeType === 8) {
          if (node.data === '/$') {
            if (depth === 0) break;
            depth -= 1;
          } else if (node.data.startsWith('$')) {
            depth += 1;
          }
        }
        node.remove(); // the loading placeholder
        node = next;
      }
      while (hidden.firstChild) parent.insertBefore(hidden.firstChild, node);
      boundary.remove();
      hidden.remove();
    }
  }

  function serialize(doc) {
    const root = doc.documentElement.cloneNode(true);
    root.querySelectorAll('[data-rs]').forEach((node) => node.remove());
    root.querySelectorAll('[data-rs-reveal]').forEach((node) => node.removeAttribute('data-rs-reveal'));
    return `<!doctype html>\n${root.outerHTML}`;
  }

  const normalize = (text) => serialize(cleanDoc(text));

  /* Pages built for scripts often hide content until a script reveals it. The preview shows it anyway. */
  const REVEAL_CSS = 'html,body{opacity:1!important;visibility:visible!important}[style*="opacity:0"],[style*="opacity: 0"]{opacity:1!important;transform:none!important}[style*="visibility:hidden"],[style*="visibility: hidden"]{visibility:visible!important}';

  /* Pages drawn by scripts send search engines a hidden copy of their text. When the preview comes out empty, that
     copy is shown as a plain page: it is exactly what Google and Ranksim read. */
  const HIDDEN_CSS = `[data-rs-reveal]{position:static!important;clip:auto!important;clip-path:none!important;width:auto!important;height:auto!important;max-width:760px!important;margin:0 auto!important;padding:48px 32px 64px!important;overflow:visible!important;white-space:normal!important;display:block!important;opacity:1!important;visibility:visible!important;transform:none!important;background:#fff!important;font:17px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif!important;color:#1b1b19!important;text-align:left!important}
[data-rs-reveal] *{white-space:normal!important}
[data-rs-reveal] h1{font-size:38px!important;line-height:1.12!important;letter-spacing:-.025em;margin:0 0 14px!important;font-weight:700!important}
[data-rs-reveal] h2{font-size:25px!important;line-height:1.25!important;margin:40px 0 10px!important;font-weight:700!important}
[data-rs-reveal] h3{font-size:19px!important;margin:28px 0 6px!important;font-weight:650!important}
[data-rs-reveal] p{margin:0 0 16px!important;color:#333330!important}
[data-rs-reveal] a{color:#3358eb!important;text-decoration:none!important}
[data-rs-reveal] ul,[data-rs-reveal] ol{padding-left:24px!important;margin:0 0 16px!important;list-style:revert!important}
[data-rs-reveal] summary{cursor:pointer;color:#555550;margin:24px 0 8px}
html:has([data-rs-reveal]),body:has([data-rs-reveal]){background:#fff!important;height:auto!important;overflow:auto!important}`;

  function revealHidden(doc) {
    const win = doc.defaultView;
    if (!win || !doc.body) return false;
    const text = (doc.body.textContent || '').trim().length;
    if (text < 120 || doc.body.scrollHeight > 160) return false; // it shows something already
    let best = null;
    let most = 0;
    for (const el of doc.body.querySelectorAll('article,main,section,div')) {
      const size = (el.textContent || '').trim().length;
      if (size < most || size < 80) continue;
      const box = el.getBoundingClientRect();
      const cs = win.getComputedStyle(el);
      const hidden = box.width <= 2 || box.height <= 2 || (cs.clip && cs.clip !== 'auto') || (cs.clipPath && cs.clipPath !== 'none') || cs.display === 'none' || cs.visibility === 'hidden';
      if (hidden && size >= most) {
        best = el;
        most = size;
      }
    }
    if (!best) return false;
    best.setAttribute('data-rs-reveal', '');
    for (let up = best.parentElement; up && up !== doc.body; up = up.parentElement) {
      const cs = win.getComputedStyle(up);
      if (cs.display === 'none') up.style.setProperty('display', 'block', 'important');
    }
    const style = doc.createElement('style');
    style.setAttribute('data-rs', '1');
    style.textContent = HIDDEN_CSS;
    doc.head.append(style);
    return true;
  }

  function frameSource(text, url) {
    const doc = cleanDoc(text);
    const head = doc.head || doc.documentElement;
    if (url && /^https?:\/\//i.test(url)) {
      const base = doc.createElement('base');
      base.setAttribute('href', url);
      base.setAttribute('data-rs', '1');
      head.prepend(base); // the page's own CSS, images and fonts load from its own site
    }
    for (const link of doc.querySelectorAll('link')) {
      const rel = (link.getAttribute('rel') || '').toLowerCase();
      const as = (link.getAttribute('as') || '').toLowerCase();
      if (/modulepreload|prefetch|prerender/.test(rel) || (rel.includes('preload') && as !== 'style')) {
        link.remove(); // scripts never run in the preview, so there is nothing to preload
        continue;
      }
      if (rel.includes('preload') && as === 'style') link.setAttribute('rel', 'stylesheet'); // the onload swap was removed
      link.removeAttribute('crossorigin'); // a CORS fetch fails when the site sends no CORS header; a plain load works
      link.removeAttribute('integrity');
    }
    for (const img of doc.querySelectorAll('img[data-src], img[data-lazy-src], source[data-srcset], img[data-srcset]')) {
      const src = img.getAttribute('data-src') || img.getAttribute('data-lazy-src');
      if (src && !img.getAttribute('src')) img.setAttribute('src', src);
      const set = img.getAttribute('data-srcset');
      if (set && !img.getAttribute('srcset')) img.setAttribute('srcset', set);
    }
    const styled = doc.querySelector('style, link[rel~="stylesheet"]');
    const style = doc.createElement('style');
    style.setAttribute('data-rs', '1');
    style.textContent = (styled ? '' : READER_CSS) + REVEAL_CSS + EDIT_CSS;
    head.append(style);
    return `<!doctype html>${doc.documentElement.outerHTML}`;
  }

  function facts(text) {
    const doc = cleanDoc(text);
    const body = doc.body ? doc.body.cloneNode(true) : null;
    if (body) body.querySelectorAll('style,template').forEach((node) => node.remove());
    const words = body ? (body.textContent.match(/[A-Za-z0-9À-ɏ'’]+/g) || []).length : 0;
    const meta = doc.querySelector('meta[name="description" i]');
    return {
      title: ((doc.querySelector('title') || {}).textContent || '').trim(),
      h1: ((doc.querySelector('h1') || {}).textContent || '').trim(),
      meta: meta ? (meta.getAttribute('content') || '').trim() : '',
      words,
      headings: doc.querySelectorAll('h2,h3,h4').length,
    };
  }

  /* One draft per page address. Switching the search keeps your edits. */
  function draftFor(url) {
    if (!S.drafts.has(url)) S.drafts.set(url, { text: '', url, base: null, loaded: '', touched: false, loading: false, failed: '' });
    return S.drafts.get(url);
  }

  async function getPage(url) {
    if (!S.pages.has(url)) {
      S.pages.set(url, api.get(`/api/v1/projects/${enc(S.slug)}/page?url=${enc(url)}`).catch((err) => {
        S.pages.delete(url);
        throw err;
      }));
    }
    return S.pages.get(url);
  }

  async function getCompare(q) {
    if (!S.compare.has(q)) {
      S.compare.set(q, api.get(`/api/v1/projects/${enc(S.slug)}/compare?q=${enc(q)}`).catch((err) => {
        S.compare.delete(q);
        throw err;
      }));
    }
    return S.compare.get(q);
  }

  const ED = { mode: 'visual', device: 'desktop', frame: null, code: null, refs: null };

  function tokens(text) {
    return new Set(String(text || '').toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').split(/\s+/).filter((w) => w.length > 2 && !['and', 'the', 'for', 'with', 'your', 'you'].includes(w)));
  }

  function bestSearchFor(text) {
    const R = S.report;
    const have = tokens(text);
    let best = null;
    let score = 0;
    for (const row of R.rankings) {
      const want = tokens(row.q);
      const overlap = want.size ? [...want].filter((w) => have.has(w)).length / want.size : 0;
      if (overlap > score) {
        best = row.q;
        score = overlap;
      }
    }
    return score >= 0.5 ? best : null;
  }

  function currentPage() {
    if (!S.report) return S.open ? S.open.url : '';
    const R = S.report;
    const want = S.params.get('page');
    if (want) return want;
    const q = S.params.get('q');
    const row = R.rankings.find((item) => item.q === q) || R.rankings.find((item) => item.position) || null;
    if (row && row.url) return row.url;
    const first = R.pages.find((item) => item.status === 'fetched');
    return first ? first.url : `https://${R.site}/`;
  }

  function currentQuery(draft) {
    const R = S.report;
    if (!R || !R.rankings.length) return '';
    const want = S.params.get('q');
    if (want && R.rankings.some((row) => row.q === want)) return want;
    const mine = R.rankings.find((row) => row.url && looseKey(row.url) === looseKey(draft.url));
    if (mine) return mine.q;
    const f = facts(draft.text);
    return bestSearchFor(`${f.title} ${f.h1}`) || (R.rankings.find((row) => row.position) || R.rankings[0]).q;
  }

  async function loadPage(url) {
    const draft = draftFor(url);
    if (draft.loading) return;
    draft.loading = true;
    draft.failed = '';
    try {
      const stored = S.report && S.report.pages.some((item) => item.url === url && item.status === 'fetched');
      const got = stored ? await getPage(url) : await api.post('/api/v1/fetch', { url });
      draft.text = normalize(got.html);
      draft.base = draft.text;
      draft.url = got.url;
      draft.loaded = got.url;
      draft.touched = false;
    } catch (err) {
      draft.failed = err.message;
    } finally {
      draft.loading = false;
      if (ED.refs && ED.refs.page === url) {
        ED.refs.address.value = draft.url;
        paintFrame();
        paintSide();
      }
    }
  }

  function renderEditor() {
    const R = S.report;
    const pageUrl = currentPage();
    const draft = draftFor(pageUrl);
    if (!draft.text && !draft.loading && !draft.failed && !S.open) loadPage(pageUrl);
    if (S.open && !draft.text && !draft.failed) draft.loading = true;

    const toolbar = h('div', { class: 'toolbar' });
    const left = h('div', { class: 'left' });
    if (R) {
      const options = [];
      const seen = new Set();
      const add = (url, label) => {
        if (seen.has(looseKey(url))) return;
        seen.add(looseKey(url));
        options.push(h('option', { value: url, selected: looseKey(url) === looseKey(pageUrl), text: label }));
      };
      if (!R.pages.some((item) => looseKey(item.url) === looseKey(pageUrl))) add(pageUrl, hostOf(pageUrl) === R.site.replace(/^www\./, '') ? pathOf(pageUrl) : `${hostOf(pageUrl)}${pathOf(pageUrl)}`);
      for (const item of R.pages.filter((p) => p.status === 'fetched')) add(item.url, item.path);
      options.push(h('option', { value: '__new', text: 'Start a new page' }));
      const pSelect = h('select', { 'aria-label': 'Page to edit' }, options);
      pSelect.addEventListener('change', () => {
        syncFromFrame();
        if (pSelect.value === '__new') {
          const url = `https://${R.site}/new-page/`;
          const fresh = draftFor(url);
          if (!fresh.text) Object.assign(fresh, { text: normalize('New page title\n\nWrite the page here. Click any line to change it.'), url, base: null, loaded: '', touched: true, failed: '' });
          go('editor', { page: url });
          return;
        }
        go('editor', { page: pSelect.value });
      });
      left.append(h('label', { class: 'select' }, pSelect, icon('chevron', 'chev')));
    } else {
      left.append(h('span', { class: 'chip chip-gray', text: S.open ? S.open.domain : '' }));
    }
    const modeSeg = h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': 'Edit mode' });
    for (const [key, label, ic] of [['visual', 'Visual', 'eye'], ['html', 'HTML', 'code']]) {
      modeSeg.append(h('button', { type: 'button', 'aria-pressed': String(ED.mode === key), onClick: () => { syncFromFrame(); ED.mode = key; renderEditor(); } }, icon(ic), label));
    }
    const deviceSeg = h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': 'Device' });
    for (const [key, label] of [['desktop', 'Desktop'], ['mobile', 'Phone']]) {
      deviceSeg.append(h('button', { type: 'button', title: label, 'aria-label': label, 'aria-pressed': String(ED.device === key), onClick: () => { syncFromFrame(); ED.device = key; renderEditor(); } }, icon(key)));
    }
    toolbar.append(left, h('div', { class: 'right' }, modeSeg, deviceSeg));

    const address = h('input', { type: 'text', value: draft.url || pageUrl, 'aria-label': 'Page address', spellcheck: 'false', title: 'Press Return to open another page' });
    address.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      let value = address.value.trim();
      if (!value) return;
      if (!/^https?:\/\//i.test(value)) value = `https://${value}`;
      syncFromFrame();
      if (R && hostOf(value) === hostOf(`https://${R.site}`)) go('editor', { page: value });
      else navigate(`/edit?url=${enc(value)}`);
    });
    const viewport = h('div', { class: `viewport${ED.device === 'mobile' ? ' phone' : ''}` });
    const hint = h('span', { class: 'edit-hint', text: ED.mode === 'visual' ? 'Click any text to edit it' : 'Editing HTML' });
    const browser = h('section', { class: 'browser' },
      h('div', { class: 'chrome' }, h('span', { class: 'lights', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')), h('label', { class: 'address' }, icon('lock'), address), hint),
      viewport);
    const side = h('div', { class: 'side-col' });
    const result = h('div', { class: 'result', id: 'result', tabindex: '-1' });
    const title = R ? 'Editor' : (S.open ? S.open.domain : 'Editor');
    const sub = R ? 'Change the page, then test where it would land among the pages that rank today.' : 'Your page is ready to edit. Ranksim is reading the site and the pages that rank against it.';
    clear(main).append(page(title, sub, null, toolbar, h('div', { class: 'editor-grid' }, browser, side), result), footer());
    ED.refs = { viewport, side, result, address, hint, page: pageUrl, q: R ? currentQuery(draft) : '' };
    paintFrame();
    paintSide();
    if (R && ED.refs.q) {
      const saved = S.results.get(`${ED.refs.q}|${looseKey(pageUrl)}`);
      if (saved) renderResult(saved, false);
      getCompare(ED.refs.q).catch(() => {});
    }
  }

  function syncFromFrame() {
    if (!ED.refs) return;
    const draft = draftFor(ED.refs.page);
    if (ED.mode === 'visual' && ED.frame && ED.frame.contentDocument && ED.frame.contentDocument.body && draft.touched) {
      draft.text = serialize(ED.frame.contentDocument);
    } else if (ED.mode === 'html' && ED.code) {
      draft.text = ED.code.value;
    }
  }

  function paintFrame() {
    if (!ED.refs) return;
    const { viewport, page: pageUrl } = ED.refs;
    const draft = draftFor(pageUrl);
    clear(viewport);
    ED.frame = null;
    ED.code = null;
    if (!draft.text) {
      const note = draft.failed
        ? [icon('warn'), h('strong', { text: 'This page could not be opened' }), h('span', { text: draft.failed }), h('button', { class: 'btn', type: 'button', onClick: () => { draft.failed = ''; loadPage(pageUrl); paintFrame(); } }, 'Try again')]
        : [h('span', { class: 'spinner', 'aria-hidden': 'true' }), h('strong', { text: 'Opening the page' }), h('span', { text: hostOf(pageUrl) })];
      viewport.append(h('div', { class: 'empty' }, note));
      return;
    }
    if (ED.mode === 'html') {
      const code = h('textarea', { class: 'code', spellcheck: 'false', 'aria-label': 'Page HTML', value: tidy(draft.text) });
      code.addEventListener('input', () => {
        draft.text = code.value;
        draft.touched = true;
        debouncedSide();
      });
      viewport.append(code);
      ED.code = code;
      return;
    }
    const frame = h('iframe', { sandbox: 'allow-same-origin', title: 'Editable page', referrerpolicy: 'no-referrer' });
    frame.srcdoc = frameSource(draft.text, draft.url);
    frame.addEventListener('load', () => {
      const doc = frame.contentDocument;
      if (!doc || !doc.body) return;
      const revealed = revealHidden(doc);
      if (ED.refs && ED.refs.hint) {
        ED.refs.hint.textContent = revealed ? 'Showing what Google reads' : 'Click any text to edit it';
        ED.refs.hint.title = revealed ? 'The live site builds its look with scripts, which never run here. This is the HTML Google and Ranksim score. Click any text to edit it.' : '';
      }
      doc.designMode = 'on';
      doc.addEventListener('input', debounce(() => {
        draft.text = serialize(doc);
        draft.touched = true;
        paintSide();
      }, 250));
      doc.addEventListener('keydown', (event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
          event.preventDefault();
          runTest();
        }
      });
    });
    if (ED.device === 'mobile') viewport.append(h('div', { class: 'device' }, frame));
    else viewport.append(frame);
    ED.frame = frame;
  }

  function tidy(html) {
    if (!html || /\n.*\n.*\n/.test(html.slice(0, 4000))) return html;
    const block = '(?:html|head|body|title|meta|link|style|header|footer|main|article|section|nav|aside|div|h[1-6]|p|ul|ol|li|table|thead|tbody|tr|figure|blockquote|form)';
    return html.replace(new RegExp(`>\\s*<(${block}\\b)`, 'gi'), '>\n<$1').replace(new RegExp(`(</${block}>)\\s*<`, 'gi'), '$1\n<').replace(/\n{2,}/g, '\n').trim();
  }

  const debouncedSide = debounce(() => paintSide(), 300);

  function setHead(field, value) {
    const draft = draftFor(ED.refs.page);
    if (draft.touched) syncFromFrame();
    const live = ED.mode === 'visual' && ED.frame && ED.frame.contentDocument && ED.frame.contentDocument.head;
    const target = live ? ED.frame.contentDocument : cleanDoc(draft.text);
    if (field === 'title') {
      let el = target.querySelector('title');
      if (!el) {
        el = target.createElement('title');
        target.head.prepend(el);
      }
      el.textContent = value;
    } else {
      let el = target.querySelector('meta[name="description" i]');
      if (!el) {
        el = target.createElement('meta');
        el.setAttribute('name', 'description');
        target.head.append(el);
      }
      el.setAttribute('content', value);
    }
    draft.text = serialize(target);
    draft.touched = true;
    if (ED.mode === 'html' && ED.code) ED.code.value = tidy(draft.text);
  }

  function testTile(draft) {
    const R = S.report;
    if (!R) {
      const box = h('div', { class: 'side-stack' }, progressTile());
      box.append(h('div', { class: 'result-actions' }, publishButton(draft)));
      return box;
    }
    const q = ED.refs.q;
    const tile = h('section', { class: 'tile' });
    if (!R.rankings.length || !S.project || !S.project.engine || !S.project.engine.ready) {
      tile.append(h('div', { class: 'tile-k', text: 'Testing' }),
        h('p', { class: 'tile-sub', text: R.google_ready ? 'Rankings for this site are still being pulled. Refresh it in Settings.' : `Predictions need Google results for ${R.site}. Search data isn't connected on the server yet, so you can edit and download, but not test.` }));
      tile.append(h('div', { class: 'result-actions' }, h('button', { class: 'btn btn-primary btn-large btn-block', type: 'button', disabled: true }, 'Test changes'), publishButton(draft)));
      return tile;
    }
    const qSelect = h('select', { 'aria-label': 'Search to test against' }, R.rankings.map((item) => h('option', { value: item.q, selected: item.q === q, text: item.q })));
    qSelect.addEventListener('change', () => {
      syncFromFrame();
      S.params = new URLSearchParams({ page: ED.refs.page, q: qSelect.value });
      history.replaceState({}, '', pathFor('editor', { page: ED.refs.page, q: qSelect.value }));
      renderEditor();
    });
    const row = R.rankings.find((item) => item.q === q) || {};
    const mine = row.url && looseKey(row.url) === looseKey(draft.url);
    const standing = !row.checked ? 'No Google results for this search yet.'
      : mine && row.position ? `This page is #${row.position} on Google${row.predicted && row.predicted.position ? `; the model puts it #${row.predicted.position} of ${row.predicted.of}` : ''}.`
        : row.position ? `${R.name} is #${row.position} with ${pathOf(row.url)}. This page is tested as a new entry.` : 'Not in the top 10 yet. This page is tested against the pages that are.';
    const testBtn = h('button', { class: 'btn btn-primary btn-large btn-block', type: 'button', id: 'test-btn', disabled: !draft.text || draft.loading, onClick: runTest }, 'Test changes');
    tile.append(h('div', { class: 'tile-k', text: 'Test against the search' }), h('label', { class: 'select select-block' }, qSelect, icon('chevron', 'chev')), h('p', { class: 'tile-sub', text: standing }),
      h('div', { class: 'result-actions' }, testBtn, publishButton(draft)));
    return tile;
  }

  function publishButton(draft) {
    return h('button', { class: 'btn btn-block', type: 'button', disabled: !draft.text, onClick: () => openPublish(draft) }, 'Publish');
  }

  function openPublish(draft) {
    syncFromFrame();
    const domain = hostOf(draft.url);
    const owned = S.report && hostOf(`https://${S.report.site}`) === domain;
    openSheet((sheet, closeBtn) => {
      sheet.append(h('div', { class: 'sheet-head' }, h('div', null, h('h2', { text: `Publish to ${domain}` }), h('div', { class: 'sub', text: draft.url })), closeBtn));
      const body = h('div', { class: 'sheet-body' });
      body.append(h('div', { class: 'tile white' },
        h('span', { class: 'label label-medium', text: 'Needs ownership' }),
        h('p', { class: 'reason', text: owned ? `Pushing a change live needs you to prove you own ${domain} and connect where it's hosted. Connecting a site is coming next.` : `Anyone can edit and test this page. Pushing it live needs you to own ${domain}.` }),
        h('p', { class: 'caveat', text: 'Until then, take the edited page with you. The file is the page HTML with your edits and without its scripts, so copy your changes into the real page rather than replacing it.' })));
      const download = h('button', { class: 'btn btn-dark btn-large', type: 'button' }, 'Download HTML');
      download.addEventListener('click', () => {
        const blob = new Blob([draftFor(ED.refs.page).text], { type: 'text/html' });
        const a = h('a', { href: URL.createObjectURL(blob), download: `${(pathOf(draft.url).replace(/^\/|\/$/g, '').replace(/\//g, '-') || 'index')}.html` });
        document.body.append(a);
        a.click();
        setTimeout(() => {
          URL.revokeObjectURL(a.href);
          a.remove();
        }, 500);
      });
      const copy = h('button', { class: 'btn btn-large', type: 'button' }, 'Copy HTML');
      copy.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(draftFor(ED.refs.page).text);
          toast('Copied the edited HTML.');
        } catch (_err) {
          toast('Copy is blocked here. Use Download instead.');
        }
      });
      body.append(h('div', { class: 'form-actions' }, download, copy));
      sheet.append(body);
    });
  }

  function paintSide() {
    if (!ED.refs) return;
    const { side, page: pageUrl } = ED.refs;
    const draft = draftFor(pageUrl);
    const f = facts(draft.text);
    if (document.activeElement && side.contains(document.activeElement) && document.activeElement.dataset.field) {
      const snippet = side.querySelector('.snippet');
      if (snippet) snippet.replaceWith(snippetCard(f, draft));
      const stats = side.querySelector('.stats-card');
      if (stats) stats.replaceWith(statsCard(f));
      return;
    }
    clear(side);
    const titleCount = h('span', { class: `counter${f.title.length > 60 ? ' over' : ''}`, text: `${f.title.length}/60` });
    const titleField = h('input', { class: 'field', value: f.title, 'aria-label': 'Title', dataset: { field: 'title' }, disabled: !draft.text });
    titleField.addEventListener('input', () => {
      setHead('title', titleField.value);
      titleCount.textContent = `${titleField.value.length}/60`;
      titleCount.classList.toggle('over', titleField.value.length > 60);
      debouncedSide();
    });
    const metaCount = h('span', { class: `counter${f.meta.length > 160 ? ' over' : ''}`, text: `${f.meta.length}/160` });
    const metaField = h('textarea', { class: 'field', rows: '3', value: f.meta, 'aria-label': 'Meta description', dataset: { field: 'meta' }, disabled: !draft.text });
    metaField.addEventListener('input', () => {
      setHead('meta', metaField.value);
      metaCount.textContent = `${metaField.value.length}/160`;
      metaCount.classList.toggle('over', metaField.value.length > 160);
      debouncedSide();
    });
    side.append(
      testTile(draft),
      snippetCard(f, draft),
      h('section', { class: 'tile' },
        h('div', { class: 'form' },
          h('div', { class: 'form-row' }, h('label', { class: 'row-label' }, h('span', { text: 'Title' }), titleCount), titleField),
          h('div', { class: 'form-row' }, h('label', { class: 'row-label' }, h('span', { text: 'Description' }), metaCount), metaField))),
      statsCard(f));
  }

  function snippetCard(f, draft) {
    let crumbs = draft.url;
    try {
      const u = new URL(draft.url);
      crumbs = `${u.hostname}${u.pathname.split('/').filter(Boolean).map((part) => ` › ${decodeURIComponent(part)}`).join('')}`;
    } catch (_err) {
      crumbs = draft.url;
    }
    const title = f.title || f.h1 || 'Untitled page';
    const desc = f.meta || 'No description. Google will pick a line from the page.';
    const name = S.report && hostOf(`https://${S.report.site}`) === hostOf(draft.url) ? S.report.name : hostOf(draft.url);
    return h('section', { class: 'snippet' },
      h('div', { class: 's-site' }, h('span', { class: 'fav', text: initial(hostOf(draft.url)) }), h('div', null, h('div', { class: 's-name', text: name }), h('div', { class: 's-url', text: crumbs }))),
      h('div', { class: 's-title', text: title.length > 62 ? `${title.slice(0, 59).replace(/\s+\S*$/, '')} ...` : title }),
      h('div', { class: 's-desc', text: desc.length > 160 ? `${desc.slice(0, 157).replace(/\s+\S*$/, '')} ...` : desc }));
  }

  function statsCard(f) {
    return h('section', { class: 'tile stats-card' },
      h('div', { class: 'stat-line' }, h('span', { class: 'k', text: 'Words' }), h('span', { class: 'v', text: f.words.toLocaleString() })),
      h('div', { class: 'stat-line' }, h('span', { class: 'k', text: 'Sections' }), h('span', { class: 'v', text: String(f.headings) })),
      h('div', { class: 'stat-line' }, h('span', { class: 'k', text: 'Main heading' }), h('span', { class: 'v one-line', text: f.h1 || 'missing' })));
  }

  /* ------------------------------------------------------------------ test and result */

  let testing = false;

  async function runTest() {
    if (testing || !ED.refs || !S.report || !ED.refs.q) return;
    syncFromFrame();
    const q = ED.refs.q;
    const pageUrl = ED.refs.page;
    const draft = draftFor(pageUrl);
    if (!draft.text) {
      toast('Open or write a page first.');
      return;
    }
    const sameAsLoaded = draft.base != null && !!draft.loaded && looseKey(draft.url) === looseKey(draft.loaded);
    testing = true;
    const btn = $('#test-btn');
    if (btn) {
      btn.disabled = true;
      clear(btn).append(h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Testing');
    }
    try {
      const [run] = await Promise.all([
        api.post(`/api/v1/projects/${enc(S.slug)}/test`, { q, draft: draft.text, url: draft.url, original: sameAsLoaded ? 'current' : 'none', original_html: sameAsLoaded ? draft.base : '' }),
        getCompare(q).catch(() => null),
      ]);
      S.results.set(`${q}|${looseKey(pageUrl)}`, run);
      if (ED.refs && ED.refs.q === q && ED.refs.page === pageUrl && S.screen === 'editor') {
        renderResult(run, true);
        ED.refs.result.scrollIntoView({ behavior: reduceMotion.matches ? 'auto' : 'smooth', block: 'start' });
      }
    } catch (err) {
      toast(err.message);
    } finally {
      testing = false;
      const again = $('#test-btn');
      if (again) {
        again.disabled = false;
        clear(again).append('Test changes');
      }
    }
  }

  function verdictOf(run) {
    const after = run.after || {};
    const before = run.before;
    if (after.position == null) return { text: 'Not placed', cls: '' };
    if (before && before.position != null) {
      const change = before.position - after.position;
      if (change > 0) return { text: `Predicted ${plural(change, 'place')} higher`, cls: 'up', change };
      if (change < 0) return { text: `Predicted ${plural(-change, 'place')} lower`, cls: 'down', change };
      return { text: 'Predicted no change', cls: '', change: 0 };
    }
    return { text: `Predicted #${after.position} of ${after.of}`, cls: '' };
  }

  function reasonOf(run) {
    const after = run.after || {};
    if (after.position == null) return after.unplaced || 'The page did not pass the on-topic check for this search, so it was not placed.';
    const fa = run.facts_after || {};
    const fb = run.facts_before;
    const parts = [];
    if (fb) {
      const diff = fa.word_count - fb.word_count;
      if (Math.abs(diff) >= Math.max(25, fb.word_count * 0.08)) parts.push(diff > 0 ? `is ${plural(diff, 'word')} longer` : `is ${plural(-diff, 'word')} shorter`);
      const hd = fa.headings - fb.headings;
      if (hd > 0) parts.push(`adds ${plural(hd, 'section')}`);
      else if (hd < 0) parts.push(`drops ${plural(-hd, 'section')}`);
      if (fa.table_count > fb.table_count) parts.push('adds a table');
      else if (fa.table_count < fb.table_count) parts.push('drops a table');
      if (fa.has_faq !== fb.has_faq) parts.push(fa.has_faq === 'yes' ? 'adds an FAQ' : 'drops the FAQ');
      if (fa.title !== fb.title) parts.push('changes the title');
      if (!parts.length) return 'Your edit reads much like the current page.';
    } else {
      parts.push(`is scored as a new page against ${plural(run.candidates, 'page')}`);
      if (fa.word_count != null) parts.push(`has ${plural(fa.word_count, 'word')}`);
    }
    const shown = parts.slice(0, 3);
    return `Your page ${shown.length > 1 ? `${shown.slice(0, -1).join(', ')} and ${shown[shown.length - 1]}` : shown[0]}.`;
  }

  function moveChip(change) {
    if (!change) return h('span', { class: 'move same', text: 'same' });
    return h('span', { class: `move ${change > 0 ? 'up' : 'down'}` }, icon(change > 0 ? 'up' : 'down'), String(Math.abs(change)));
  }

  function renderResult(run, animate) {
    const host = clear(ED.refs.result);
    const verdict = verdictOf(run);
    const after = run.after || {};
    const before = run.before;
    const head = h('section', { class: 'tile white span-2' },
      after.confidence && after.confidence !== 'n/a' ? h('span', { class: `label ${after.confidence === 'CLEARER' ? 'label-ok' : 'label-medium'}`, text: after.confidence === 'CLEARER' ? 'Clearer call' : 'Uncertain' }) : null,
      h('h2', { class: `verdict ${verdict.cls}`, text: verdict.text }));
    if (after.position != null) {
      const ranks = h('div', { class: 'ranks' });
      if (before && before.position != null) ranks.append(h('div', { class: 'rank-box before' }, h('span', { class: 'k', text: 'Now' }), h('span', { class: 'n', text: `#${before.position}` }), h('span', { class: 'of', text: `of ${before.of}` })), icon('arrow', 'arrow'));
      ranks.append(h('div', { class: 'rank-box after' }, h('span', { class: 'k', text: 'With your edit' }), h('span', { class: 'n', text: `#${after.position}` }), h('span', { class: 'of', text: `of ${after.of}` })));
      head.append(ranks);
    }
    head.append(h('p', { class: 'reason', text: reasonOf(run) }), h('p', { class: 'caveat', text: 'Among the pages that rank for this search today. A prediction, not a Google ranking.' }));
    const grid = h('div', { class: 'bento' }, head);
    if (after.order && after.order.length) grid.append(orderTile(run, animate));
    grid.append(detailsTile(run));
    host.append(grid);
  }

  function orderTile(run, animate) {
    const after = run.after;
    const before = run.before && run.before.order && run.before.order.length ? run.before : null;
    const f = facts(draftFor(ED.refs ? ED.refs.page : run.url).text);
    const keyOf = (item) => (item.page ? '__page__' : item.url);
    const titles = new Map();
    const rows = new Map();
    const list = h('ol', { class: 'order', 'aria-label': 'Predicted order' });
    const make = (item) => {
      const isPage = !!item.page;
      const li = h('li', { class: isPage ? 'is-draft' : null },
        h('span', { class: `pos${isPage ? ' you' : ''}`, text: String(item.predicted) }),
        h('span', { class: `fav${isPage ? ' you' : ''}`, text: isPage ? '' : initial(item.domain) }),
        h('div', { class: 'main' }, h('div', { class: 't', text: isPage ? f.title || 'Your page' : item.domain }), h('div', { class: 'd', text: isPage ? `Your page · ${hostOf(run.url)}` : item.domain })),
        h('span', { class: 'side' }));
      if (isPage) li.querySelector('.fav').append(icon('pencil'));
      rows.set(keyOf(item), li);
      if (!isPage) titles.set(item.url, li.querySelector('.t'));
      return li;
    };
    const side = (li, item) => {
      const box = clear(li.querySelector('.side'));
      if (item.page) {
        const v = verdictOf(run);
        if (before && v.change != null) box.append(moveChip(v.change), ` was #${run.before.position}`);
        else box.append('New page');
      } else box.append(`Google #${item.observed}`);
    };
    const start = before || after;
    for (const item of start.order) list.append(make(item));
    for (const item of after.order) if (!rows.has(keyOf(item))) list.append(make(item));
    const settle = () => {
      const first = new Map();
      for (const [key, li] of rows) first.set(key, li.getBoundingClientRect().top);
      for (const item of after.order) {
        const li = rows.get(keyOf(item));
        li.querySelector('.pos').textContent = String(item.predicted);
        side(li, item);
        list.append(li);
      }
      if (reduceMotion.matches || !animate || !before) return;
      for (const [key, li] of rows) {
        const delta = first.get(key) - li.getBoundingClientRect().top;
        if (!delta) continue;
        li.style.transition = 'none';
        li.style.transform = `translateY(${delta}px)`;
        void li.offsetWidth;
        li.style.transition = 'transform 0.9s cubic-bezier(0.32, 0.72, 0, 1)';
        li.style.transform = '';
      }
    };
    if (before && animate && !reduceMotion.matches) {
      for (const item of before.order) {
        const li = rows.get(keyOf(item));
        if (!li) continue;
        if (item.page) clear(li.querySelector('.side')).append('Now');
        else side(li, item);
      }
      setTimeout(settle, 900);
    } else settle();
    getCompare(run.search).then((view) => {
      for (const cand of view.candidates || []) {
        const el = titles.get(cand.url);
        if (el && cand.title) el.textContent = cand.title;
      }
    }).catch(() => {});
    return h('section', { class: 'tile span-2' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Predicted order' }), h('span', { class: 'aside', text: plural(after.of, 'page') })), list);
  }

  function detailsTile(run) {
    const fa = run.facts_after || {};
    const fb = run.facts_before;
    const rows = [['Words', 'word_count'], ['Sections', 'headings'], ['Tables', 'table_count'], ['FAQ', 'has_faq'], ['Visible date', 'updated'], ['Title', 'title']];
    const fmt = (v) => (v == null || v === '' ? 'none' : typeof v === 'number' ? v.toLocaleString() : String(v));
    const table = h('table', { class: 'kv' },
      fb ? h('thead', null, h('tr', null, h('th', { text: '' }), h('th', { text: 'Now' }), h('th', { text: 'Edit' }))) : null,
      h('tbody', null, rows.map(([label, key]) => h('tr', null, h('th', { text: label }), fb ? h('td', { text: fmt(fb[key]) }) : null, h('td', { class: fb && fmt(fa[key]) !== fmt(fb[key]) ? 'changed' : null, text: fmt(fa[key]) })))));
    const notes = h('div', { class: 'note-list' });
    if (run.query_pairwise != null) notes.append(h('p', { text: `On this search the model ordered ${pct(run.query_pairwise)} of ${plural(run.query_pairs || 0, 'page pair')} right when held out. 50% is a coin flip.` }));
    if (run.topic_after && run.topic_after.missing && run.topic_after.missing.length) notes.append(h('p', { text: `Words the ranking pages use that yours doesn't: ${run.topic_after.missing.join(', ')}.` }));
    for (const note of run.notes || []) notes.append(h('p', { text: note }));
    notes.append(h('p', { text: run.saved ? 'Saved to this site\'s test history.' : 'Not saved.' }));
    return h('section', { class: 'tile span-4' }, h('div', { class: 'bento' },
      h('div', { class: 'span-2' }, h('div', { class: 'tile-head' }, h('h2', { text: fb ? 'What changed' : 'Your page' })), table),
      h('div', { class: 'span-2' }, h('div', { class: 'tile-head' }, h('h2', { text: 'How sure is this' })), notes)));
  }

  /* ------------------------------------------------------------------ settings */

  function renderSettings() {
    const R = S.report;
    const grid = h('div', { class: 'bento' });

    const steps = h('div', { class: 'steps' });
    const refreshBtn = h('button', { class: 'btn btn-dark', type: 'button' }, icon('refresh'), 'Refresh now');
    refreshBtn.addEventListener('click', () => refresh(refreshBtn, steps));
    grid.append(h('section', { class: 'tile span-2' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Refresh data' }), h('span', { class: 'aside', text: pulledText('Last pull ') || (R.search_data ? '' : 'Not pulled yet') })),
      h('p', { class: 'tile-sub', text: R.google_ready ? 'Pulls the live top 10 for every search, reads new or changed pages, and retrains the model.' : 'Search data isn\'t connected on this server yet. Add SERPER_API_KEY once and this pulls the live top 10 for every search.' }),
      h('div', { class: 'result-actions' }, refreshBtn), steps));

    grid.append(h('section', { class: 'tile span-2' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Google Search Console' }), h('button', { class: 'toggle', type: 'button', role: 'switch', 'aria-checked': 'false', disabled: true, 'aria-label': 'Connect Google Search Console' })),
      h('p', { class: 'tile-sub', text: 'Adds your real clicks, impressions and positions. Coming next.' })));

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
    grid.append(h('section', { class: 'tile span-2' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Searches' }), h('span', { class: 'aside', text: 'Found automatically' })), h('div', { class: 'form' }, qArea, h('span', { class: 'form-help', text: 'One per line. Ranksim found these from your pages; add or remove any.' }), h('div', { class: 'form-actions' }, qSave))));

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
    grid.append(h('section', { class: 'tile span-2' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Competitors' }), h('span', { class: 'aside', text: 'Found automatically' })), h('div', { class: 'form' }, cArea, h('span', { class: 'form-help', text: 'Picked from who takes the most top 10 spots. Edit if we got one wrong.' }), h('div', { class: 'form-actions' }, cSave))));

    const confirmField = h('input', { class: 'field', placeholder: S.slug, autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Type the site name to confirm' });
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
    grid.append(h('section', { class: 'tile span-4' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Delete' })), h('div', { class: 'form' }, h('span', { class: 'form-help', text: `Type ${S.slug} to delete this site and its tests.` }), confirmField, h('div', { class: 'form-actions' }, del))));

    clear(main).append(page('Settings', R.site, null, grid), footer());
  }

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
    renderBar();
  }

  /* ------------------------------------------------------------------ sheet */

  let lastFocus = null;

  function openSheet(build) {
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

  function closeSheet(instant) {
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
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && S.screen === 'editor' && S.slug) {
      event.preventDefault();
      runTest();
    }
  });

  boot();
})();
