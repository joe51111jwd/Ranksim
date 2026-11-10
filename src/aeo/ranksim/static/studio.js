/* Ranksim Studio. One page: edit a page, preview it, and see where it is PREDICTED to land before it is published.
   Every number shown comes from /api/v1. Model positions are predictions among the stored candidate pages, never a Google rank. */
(() => {
  'use strict';

  const CSRF = (document.getElementById('csrf') || {}).value || '';
  const MODE = document.documentElement.dataset.mode || 'local';
  const SVGNS = 'http://www.w3.org/2000/svg';
  const DESKTOP_WIDTH = 1024;
  const PHONE_WIDTH = 390;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  /* ------------------------------------------------------------------ dom helpers */

  const ICONS = {
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.5h.01"/>',
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
    list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    x: '<path d="M7 7l10 10M17 7 7 17"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/>',
    upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    db: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
    out: '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="M10 16l-4-4 4-4M6 12h10"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
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

  function plural(n, one, many) {
    return `${n} ${n === 1 ? one : many || one + 's'}`;
  }

  function pct(value) {
    return value == null ? null : `${Math.round(value * 100)}%`;
  }

  function hostOf(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, '');
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

  const api = {
    get: (path) => request('GET', path),
    post: (path, body) => request('POST', path, body),
  };

  /* ------------------------------------------------------------------ state */

  const S = {
    session: null,
    setup: null,
    projects: [],
    publicDemo: MODE === 'public',
    demoSlug: 'synthetic-demo',
    slug: null,
    project: null,
    screen: 'studio',
    q: '',
    compare: new Map(),
    pages: new Map(),
    drafts: new Map(),
    results: new Map(),
    previewMode: 'page',
    device: 'desktop',
    sort: 'observed',
    sheet: null,
  };

  const main = document.getElementById('main');
  const bar = $('#bar .bar-inner');

  function queryRow(q) {
    return ((S.project && S.project.queries) || []).find((row) => row.q === q) || null;
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

  async function getPage(url) {
    if (!S.pages.has(url)) {
      S.pages.set(url, api.get(`/api/v1/projects/${enc(S.slug)}/page?url=${enc(url)}`).catch((err) => {
        S.pages.delete(url);
        throw err;
      }));
    }
    return S.pages.get(url);
  }

  /* ------------------------------------------------------------------ routing */

  const SCREENS = ['studio', 'rankings', 'data'];

  function parseLocation() {
    const parts = location.pathname.split('/').filter(Boolean);
    const params = new URLSearchParams(location.search);
    if (parts[0] === 'start') return { slug: null, screen: 'new', q: '' };
    if (parts[0] === 'app' && parts[1]) {
      return { slug: decodeURIComponent(parts[1]), screen: SCREENS.includes(parts[2]) ? parts[2] : 'studio', q: params.get('q') || '' };
    }
    return { slug: null, screen: 'home', q: params.get('q') || '' };
  }

  function navigate(path) {
    if (path !== location.pathname + location.search) history.pushState({}, '', path);
    closeSheet(true);
    window.scrollTo(0, 0);
    route();
  }

  function pathFor(screen, q) {
    return `/app/${enc(S.slug)}/${screen}${q ? `?q=${enc(q)}` : ''}`;
  }

  function go(screen, q, { replace = false, scroll = true } = {}) {
    S.screen = screen;
    if (q != null) S.q = q;
    const url = pathFor(S.screen, S.q);
    if (url !== location.pathname + location.search) history[replace ? 'replaceState' : 'pushState']({}, '', url);
    renderBar();
    render();
    if (scroll) window.scrollTo(0, 0);
  }

  function link(screen, q, label, props = {}) {
    return h('a', {
      href: pathFor(screen, q),
      ...props,
      onClick: (event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1) return;
        event.preventDefault();
        go(screen, q);
      },
    }, label);
  }

  window.addEventListener('popstate', () => {
    closeSheet(true);
    const where = parseLocation();
    if (where.slug && where.slug === S.slug && S.project) {
      S.screen = where.screen;
      if (where.q && queryRow(where.q)) S.q = where.q;
      renderBar();
      render();
      return;
    }
    route();
  });

  /* ------------------------------------------------------------------ toast */

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
      renderBar();
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
    if (where.screen === 'new' && !S.publicDemo) {
      S.project = null;
      S.slug = null;
      renderBar();
      renderNewProject();
      return;
    }
    if (where.slug && S.projects.some((p) => p.slug === where.slug)) {
      if (where.slug === S.slug && S.project) {
        S.screen = where.screen;
        if (where.q && queryRow(where.q)) S.q = where.q;
        renderBar();
        render();
        return;
      }
      await openProject(where.slug, where);
      return;
    }
    if (S.publicDemo) {
      if (!S.projects.length) {
        await buildDemo(true);
        return;
      }
      await openProject(S.demoSlug, { slug: S.demoSlug, screen: where.screen === 'home' ? 'studio' : where.screen, q: where.q });
      return;
    }
    S.project = null;
    S.slug = null;
    renderBar();
    renderHome();
  }

  async function openProject(slug, route) {
    S.slug = slug;
    S.compare.clear();
    S.pages.clear();
    S.drafts.clear();
    S.results.clear();
    S.project = null;
    renderBar();
    clear(main).append(h('div', { class: 'boot' }, h('span', { class: 'spinner', 'aria-hidden': 'true' })));
    try {
      S.project = await api.get(`/api/v1/projects/${enc(slug)}`);
    } catch (err) {
      renderProblem('This project could not be opened', err.message);
      return;
    }
    const qs = S.project.queries.map((row) => row.q);
    S.q = qs.includes(route.q) ? route.q : qs[0] || '';
    S.screen = SCREENS.includes(route.screen) ? route.screen : 'studio';
    if (!S.project.engine.ready && S.screen !== 'data' && !S.publicDemo) S.screen = 'data';
    renderBar();
    render();
  }

  async function reloadProject() {
    S.compare.clear();
    S.pages.clear();
    S.project = await api.get(`/api/v1/projects/${enc(S.slug)}`);
    const qs = S.project.queries.map((row) => row.q);
    if (!qs.includes(S.q)) S.q = qs[0] || '';
    const list = await api.get('/api/v1/projects');
    S.projects = list.projects || [];
  }

  async function buildDemo(auto) {
    clear(main).append(
      h('div', { class: 'wrap screen' },
        h('section', { class: 'hero' },
          h('div', { class: 'boot', style: null }, h('span', { class: 'spinner', 'aria-hidden': 'true' })),
          h('p', { class: 'lede', text: 'Building the demo engine. This takes a few seconds.' }))));
    try {
      await api.post('/api/v1/demo', {});
      await loadProjects();
      const where = auto ? parseLocation() : { screen: 'studio', q: '' };
      await openProject(S.demoSlug, { slug: S.demoSlug, screen: SCREENS.includes(where.screen) ? where.screen : 'studio', q: where.q });
      if (!auto) history.pushState({}, '', pathFor('studio', S.q));
    } catch (err) {
      renderProblem('The demo could not be built', err.message);
    }
  }

  /* ------------------------------------------------------------------ bar */

  function renderBar() {
    clear(bar);
    bar.append(
      h('a', {
        class: 'brand',
        href: '/',
        'aria-label': 'Ranksim home',
        onClick: (event) => {
          if (event.metaKey || event.ctrlKey || S.publicDemo) return;
          event.preventDefault();
          navigate('/');
        },
      },
      h('span', { class: 'logo', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
      h('span', { class: 'brand-name', text: 'Ranksim' })),
    );
    if (S.project) {
      const nav = h('nav', { class: 'seg nav-seg', 'aria-label': 'Screens' });
      const screens = [['studio', 'Studio'], ['rankings', 'Rankings']];
      if (!S.publicDemo) screens.push(['data', 'Data']);
      for (const [key, label] of screens) nav.append(link(key, S.q, label, { 'aria-current': S.screen === key ? 'page' : null }));
      bar.append(nav);
    } else {
      bar.append(h('span'));
    }
    const right = h('div', { class: 'bar-right' });
    if (S.project) {
      if (S.project.data && S.project.data.kind === 'synthetic') right.append(h('span', { class: 'tag tag-demo', text: 'Demo data' }));
      if (S.projects.length > 1) {
        const select = h('select', { 'aria-label': 'Project' }, S.projects.map((p) => h('option', { value: p.slug, selected: p.slug === S.slug, text: p.name })));
        select.addEventListener('change', () => navigate(`/app/${enc(select.value)}/studio`));
        right.append(h('label', { class: 'select-pill' }, select, icon('chevron', 'chev')));
      } else {
        right.append(h('span', { class: 'bar-note', text: S.project.name }));
      }
    }
    if (S.session && S.session.login_required && S.session.signed_in) {
      right.append(h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Sign out', title: 'Sign out', onClick: signOut }, icon('out')));
    }
    if (!(S.session && S.session.login_required && !S.session.signed_in)) {
      right.append(h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'About Ranksim', title: 'About', onClick: openAbout }, icon('info')));
    }
    bar.append(right);
  }

  /* ------------------------------------------------------------------ render */

  function render() {
    closeSheet(true);
    if (!S.project) return;
    if (S.screen === 'data' && !S.publicDemo) {
      renderData();
      document.title = `Data · ${S.project.name} · Ranksim`;
      return;
    }
    if (!S.project.engine.ready || S.project.error) {
      renderNotBuilt();
      return;
    }
    if (!S.project.queries.length) {
      renderProblem('No queries yet', 'Add the queries you want to rank for on the classic Setup screen.');
      return;
    }
    if (S.screen === 'rankings') renderRankings();
    else renderStudio();
    document.title = `${S.screen === 'rankings' ? 'Rankings' : 'Studio'} · ${S.q} · Ranksim`;
  }

  function footer() {
    const left = h('div', { class: 'fl' });
    const data = (S.project && S.project.data) || { kind: 'none', short: '' };
    if (S.publicDemo) {
      left.append(h('span', { text: 'Demo data · Not Google results' }), h('span', { text: "Drafts aren't saved" }));
    } else {
      if (S.project) left.append(h('span', { text: data.kind === 'synthetic' ? 'Demo data · Not Google results' : data.short }));
      left.append(h('span', { text: S.session && S.session.storage === 'cloud' ? 'Saved to your private cloud storage' : 'Draft tests are saved in this project' }));
    }
    const right = h('div', { class: 'fl' }, h('span', { text: 'Predicted positions are model estimates among the pages shown, never a Google rank.' }));
    if (S.session && S.session.mode === 'local') right.append(h('a', { href: S.slug ? `/p/${enc(S.slug)}` : '/projects', text: 'Classic screens' }));
    return h('footer', { class: 'foot' }, left, right);
  }

  function renderProblem(title, text) {
    clear(main).append(
      h('div', { class: 'wrap screen' },
        h('section', { class: 'hero' }),
        h('div', { class: 'card center-card' },
          h('h2', { text: title }),
          h('p', { text: text || 'Something went wrong.' }),
          h('button', { class: 'btn btn-gray', type: 'button', onClick: () => location.reload() }, 'Try again'))),
    );
  }

  function renderEmpty() {
    clear(main).append(
      h('div', { class: 'wrap screen' },
        h('section', { class: 'hero' },
          h('h1', { text: 'Test your next edit.' }),
          h('p', { class: 'lede', text: 'Preview a page and see where it would rank among the pages that rank today, before you publish.' })),
        h('div', { class: 'card center-card' },
          h('h2', { text: 'No projects yet' }),
          h('p', { text: 'Load the demo to try it, or create a project with your own queries.' }),
          h('div', { class: 'result-actions' },
            h('button', { class: 'btn btn-primary btn-large', type: 'button', onClick: () => buildDemo(false) }, 'Load the demo'),
            h('a', { class: 'btn btn-large', href: '/start', onClick: (event) => { event.preventDefault(); navigate('/start'); } }, 'Create a project')))),
      footer(),
    );
  }

  function renderNotBuilt() {
    const P = S.project;
    clear(main).append(
      h('div', { class: 'wrap screen' },
        h('section', { class: 'hero' },
          h('h1', { text: P.name }),
          h('p', { class: 'lede', text: P.site })),
        h('div', { class: 'card center-card' },
          h('h2', { text: 'Pull data first' }),
          h('p', { text: P.error || 'Ranksim needs the pages that rank for your queries before it can predict anything. Pull them on the Data screen.' }),
          S.publicDemo ? null : link('data', S.q, 'Open Data', { class: 'btn btn-primary btn-large' }))),
      footer(),
    );
  }

  /* ------------------------------------------------------------------ studio */

  function draftFor(q) {
    let draft = S.drafts.get(q);
    if (!draft) {
      const row = queryRow(q);
      draft = {
        text: '',
        url: (row && row.your_url) || `https://${S.project.site}/new-page/`,
        base: null,
        baseUrl: null,
        loading: false,
        touched: false,
      };
      S.drafts.set(q, draft);
      if (row && row.your_url) loadCurrent(q, true);
    }
    return draft;
  }

  async function loadCurrent(q, auto) {
    const row = queryRow(q);
    const draft = draftFor(q);
    if (!row || !row.your_url) {
      toast("You don't have a page for this query yet. Start a new one.");
      return;
    }
    draft.loading = true;
    refreshStudio();
    try {
      const page = await getPage(row.your_url);
      if (auto && draft.touched) return;
      draft.text = tidyHtml(page.html);
      draft.base = draft.text;
      draft.baseUrl = page.url;
      draft.url = page.url;
      draft.touched = false;
    } catch (err) {
      if (!auto) toast(err.message);
    } finally {
      draft.loading = false;
      if (S.q === q && S.screen === 'studio') refreshStudio(true);
    }
  }

  let studio = null; // live references into the rendered Studio

  function renderStudio() {
    const P = S.project;
    const draft = draftFor(S.q);
    const row = queryRow(S.q);

    const select = h('select', { 'aria-label': 'Search query' },
      P.queries.map((r) => h('option', { value: r.q, selected: r.q === S.q, text: r.q })));
    select.addEventListener('change', () => go('studio', select.value, { scroll: false }));

    const editor = h('textarea', {
      class: 'editor',
      spellcheck: 'false',
      autocomplete: 'off',
      'aria-label': 'Draft HTML or text',
      placeholder: 'Paste the page HTML, or just start writing. The first line becomes the title.',
      value: draft.text,
    });
    const onEdit = debounce(() => {
      paintPreview();
    }, 260);
    editor.addEventListener('input', () => {
      draft.text = editor.value;
      draft.touched = true;
      paintStats();
      onEdit();
    });
    editor.addEventListener('keydown', (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault();
        runTest();
      }
    });

    const testBtn = h('button', { class: 'btn btn-primary btn-large', type: 'button', onClick: runTest }, 'Test draft');
    const stats = h('div', { class: 'stats', 'aria-live': 'polite' });
    const editorCard = h('section', { class: 'card editor-card', 'aria-label': 'Draft editor' },
      h('div', { class: 'card-head' },
        h('h2', null, 'Draft', h('span', { class: 'hint', text: 'HTML or plain text' })),
        h('div', { class: 'tools' },
          row && row.your_url ? h('button', { class: 'btn btn-plain', type: 'button', onClick: () => loadCurrent(S.q, false) }, 'Load current page') : null,
          S.publicDemo ? null : h('button', { class: 'btn btn-plain', type: 'button', onClick: () => toggleUrlBar() }, 'Load from URL'),
          h('button', {
            class: 'btn btn-plain',
            type: 'button',
            onClick: () => {
              draft.text = '';
              draft.touched = true;
              editor.value = '';
              paintStats();
              paintPreview();
              editor.focus();
            },
          }, 'Clear'))),
      urlBar(draft, editor),
      editor,
      h('div', { class: 'card-foot' }, stats, h('div', { class: 'foot-actions' }, h('span', { class: 'kbd', text: navigator.platform.includes('Mac') ? '⌘ Return' : 'Ctrl Enter' }), testBtn)),
    );

    const previewCard = h('section', { class: 'card preview-card', 'aria-label': 'Preview' });
    const resultHost = h('div', { class: 'result', id: 'result', tabindex: '-1' });

    const wrap = h('div', { class: 'wrap screen' },
      h('section', { class: 'hero' },
        h('h1', { text: 'Test your next edit.' }),
        h('p', { class: 'lede', text: 'Edit a page, preview it, and see where it would rank before you publish.' }),
        h('div', { class: 'query-row' },
          h('label', { class: 'query-pill' }, icon('search', 'lead'), select, icon('chevron', 'chev')),
          standing(row))),
      h('div', { class: 'bench' }, editorCard, previewCard),
      resultHost,
    );
    clear(main).append(wrap, footer());
    studio = { editor, stats, testBtn, previewCard, resultHost, frame: null, observer: null };
    paintStats();
    paintPreviewCard();
    const saved = S.results.get(S.q);
    if (saved) renderResult(saved, false);
    getCompare(S.q).catch(() => {});
  }

  function urlBar(draft, editor) {
    const input = h('input', { class: 'field', type: 'url', placeholder: 'https://staging.example.com/new-page/', 'aria-label': 'Page URL to load', spellcheck: 'false' });
    const go = h('button', { class: 'btn btn-primary', type: 'submit' }, 'Load');
    const form = h('form', { class: 'url-bar', hidden: true }, input, go);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!input.value.trim()) return;
      go.disabled = true;
      clear(go).append(h('span', { class: 'spinner', 'aria-hidden': 'true' }));
      try {
        const page = await api.post(`/api/v1/projects/${enc(S.slug)}/fetch`, { url: input.value.trim() });
        draft.text = tidyHtml(page.html);
        draft.touched = true;
        draft.base = null;
        editor.value = draft.text;
        form.hidden = true;
        paintStats();
        paintPreviewCard();
        toast(`Loaded ${hostOf(page.url)}. It is tested as a new page at the address in the preview.`);
      } catch (err) {
        toast(err.message);
      } finally {
        go.disabled = false;
        clear(go).append('Load');
      }
    });
    return form;
  }

  function toggleUrlBar() {
    const form = main.querySelector('.url-bar');
    if (!form) return;
    form.hidden = !form.hidden;
    if (!form.hidden) form.querySelector('input').focus();
  }

  function refreshStudio(fromLoad) {
    if (!studio || S.screen !== 'studio') return;
    const draft = draftFor(S.q);
    if (fromLoad && studio.editor.value !== draft.text) studio.editor.value = draft.text;
    studio.editor.disabled = draft.loading;
    studio.editor.placeholder = draft.loading ? 'Loading your current page' : 'Paste the page HTML, or just start writing. The first line becomes the title.';
    paintStats();
    paintPreviewCard();
  }

  function standing(row) {
    const box = h('div', { class: 'standing' });
    if (!row) return box;
    if (row.observed == null) {
      box.append(h('span', { text: "You don't rank for this query yet. Write a new page and test it." }));
      return box;
    }
    box.append(h('span', null, 'Your page: observed ', h('b', { text: `#${row.observed}` })));
    box.append(h('span', { class: 'sep', 'aria-hidden': 'true' }));
    if (row.predicted != null) box.append(h('span', null, 'predicted ', h('b', { text: `#${row.predicted}` }), ` of ${row.of}`));
    else box.append(h('span', { text: 'not analyzed' }));
    return box;
  }

  function parseDoc(text) {
    return new DOMParser().parseFromString(asHtml(text || ''), 'text/html');
  }

  function docFacts(text) {
    const doc = parseDoc(text);
    const body = doc.body ? doc.body.cloneNode(true) : null;
    if (body) body.querySelectorAll('script,style,noscript,template').forEach((node) => node.remove());
    const words = body ? (body.textContent.match(/[A-Za-z0-9À-ɏ'’]+/g) || []).length : 0;
    const title = ((doc.querySelector('title') || {}).textContent || '').trim();
    const h1 = ((doc.querySelector('h1') || {}).textContent || '').trim();
    const metaDesc = doc.querySelector('meta[name="description" i]');
    let description = metaDesc ? (metaDesc.getAttribute('content') || '').trim() : '';
    let descSource = 'meta';
    if (!description) {
      const para = Array.from(doc.querySelectorAll('p')).map((p) => p.textContent.trim()).find((t) => t.split(/\s+/).length > 6);
      description = para || '';
      descSource = 'text';
    }
    return {
      doc,
      words,
      title,
      h1,
      description,
      descSource,
      headings: doc.querySelectorAll('h2,h3,h4,h5,h6').length,
      tables: doc.querySelectorAll('table').length,
    };
  }

  function paintStats() {
    if (!studio) return;
    const draft = draftFor(S.q);
    const facts = docFacts(draft.text);
    clear(studio.stats);
    if (!draft.text.trim()) {
      studio.stats.append(h('span', { text: draft.loading ? 'Loading your current page' : 'Empty draft' }));
    } else {
      studio.stats.append(
        h('span', null, h('b', { text: facts.words.toLocaleString() }), ' words'),
        h('span', null, h('b', { text: String(facts.headings) }), ' sections'),
        facts.title ? h('span', null, 'title ', h('b', { text: String(facts.title.length) }), ' characters') : h('span', { text: 'no title' }),
      );
    }
    studio.testBtn.disabled = !draft.text.trim() || draft.loading;
  }

  /* ------------------------------------------------------------------ preview */

  const READER_CSS = `
:root{color-scheme:light}
html{background:#fff}
body{margin:0 auto;max-width:760px;padding:56px 36px 72px;font:18px/1.62 -apple-system,BlinkMacSystemFont,"Helvetica Neue",Helvetica,Arial,sans-serif;color:#1d1d1f;letter-spacing:-.01em;-webkit-font-smoothing:antialiased}
h1{font-size:42px;line-height:1.1;letter-spacing:-.03em;margin:0 0 14px;font-weight:700}
h2{font-size:27px;line-height:1.2;letter-spacing:-.02em;margin:44px 0 10px;font-weight:700}
h3{font-size:20px;margin:30px 0 6px;font-weight:600}
p{margin:0 0 18px;color:#333336}
time{display:inline-block;color:#86868b;font-size:15px;margin-bottom:8px}
a{color:#0066cc;text-decoration:none}
ul,ol{padding-left:24px;margin:0 0 18px}
li{margin:4px 0}
table{width:100%;border-collapse:collapse;margin:28px 0;font-size:16px}
th,td{text-align:left;padding:11px 14px;border-bottom:1px solid #e5e5ea}
th{font-weight:600;background:#f5f5f7}
nav{margin-top:44px;padding-top:22px;border-top:1px solid #e5e5ea;font-size:15px}
nav a{display:inline-block;margin:0 16px 8px 0}
img,video{max-width:100%;height:auto;border-radius:12px}
blockquote{margin:24px 0;padding:4px 20px;border-left:3px solid #d2d2d7;color:#515154}
code,pre{font-family:ui-monospace,Menlo,monospace;font-size:15px}
`;
  const GUARD_CSS = 'a,button,input,select,textarea,label{pointer-events:none!important}html{scrollbar-width:thin}';

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

  /* One block element per line, so a stored page is readable in the editor. Only whitespace between block tags
     changes, which a browser does not render. */
  function tidyHtml(html) {
    if (!html || /\n.*\n.*\n/.test(html.slice(0, 4000))) return html;
    const block = '(?:html|head|body|title|meta|link|style|header|footer|main|article|section|nav|aside|div|h[1-6]|p|ul|ol|li|table|thead|tbody|tr|figure|blockquote|time|form)';
    return html
      .replace(new RegExp(`>\\s*<(${block}\\b)`, 'gi'), '>\n<$1')
      .replace(new RegExp(`(</${block}>)\\s*<`, 'gi'), '$1\n<')
      .replace(/\n{2,}/g, '\n')
      .trim();
  }

  function previewSource(text) {
    const doc = parseDoc(text);
    doc.querySelectorAll('script,noscript,iframe,frame,frameset,object,embed,base,meta[http-equiv],link:not([rel~="stylesheet"])').forEach((node) => node.remove());
    for (const el of doc.querySelectorAll('*')) {
      for (const attr of Array.from(el.attributes)) {
        const name = attr.name.toLowerCase();
        if (name.startsWith('on') || (/^(href|src|action|formaction|xlink:href|srcdoc)$/.test(name) && /^\s*(javascript|data:text\/html)/i.test(attr.value))) {
          el.removeAttribute(attr.name);
        }
      }
    }
    const styled = doc.querySelector('style, link[rel~="stylesheet"]');
    const style = doc.createElement('style');
    style.textContent = (styled ? '' : READER_CSS) + GUARD_CSS;
    (doc.head || doc.documentElement).append(style);
    return '<!doctype html>' + doc.documentElement.outerHTML;
  }

  function paintPreviewCard() {
    if (!studio) return;
    const card = studio.previewCard;
    const draft = draftFor(S.q);
    if (studio.observer) studio.observer.disconnect();
    studio.frame = null;
    clear(card);

    const modeSeg = h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': 'Preview type' });
    for (const [key, label] of [['page', 'Page'], ['search', 'In search']]) {
      modeSeg.append(h('button', {
        type: 'button',
        'aria-pressed': String(S.previewMode === key),
        onClick: () => {
          S.previewMode = key;
          paintPreviewCard();
        },
      }, label));
    }
    const tools = h('div', { class: 'tools' }, modeSeg);
    if (S.previewMode === 'page') {
      const deviceSeg = h('div', { class: 'seg seg-sm seg-icon', role: 'group', 'aria-label': 'Device' });
      for (const [key, label] of [['desktop', 'Desktop'], ['mobile', 'Phone']]) {
        deviceSeg.append(h('button', {
          type: 'button',
          title: label,
          'aria-label': label,
          'aria-pressed': String(S.device === key),
          onClick: () => {
            S.device = key;
            paintPreviewCard();
          },
        }, icon(key)));
      }
      tools.append(deviceSeg);
    }
    card.append(h('div', { class: 'card-head' }, h('h2', { text: 'Preview' }), tools));

    const body = h('div', { class: 'preview-body' });
    card.append(body);
    if (S.previewMode === 'search') {
      body.append(serpView(draft));
      return;
    }
    const address = h('input', { type: 'text', value: draft.url, 'aria-label': 'Page URL', spellcheck: 'false', autocomplete: 'off' });
    address.addEventListener('change', () => {
      let value = address.value.trim();
      if (value && !/^https?:\/\//i.test(value)) value = `https://${value}`;
      draft.url = value || `https://${S.project.site}/new-page/`;
      address.value = draft.url;
    });
    address.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') address.blur();
    });
    body.append(h('div', { class: 'chrome' },
      h('span', { class: 'lights', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
      h('label', { class: 'address' }, icon('lock'), address),
      h('span', { 'aria-hidden': 'true', style: null })));
    const viewport = h('div', { class: `viewport${S.device === 'mobile' ? ' mobile' : ''}` });
    body.append(viewport);
    studio.viewport = viewport;
    paintPreview();
  }

  function frameFor(viewport, srcdoc, device) {
    const frame = h('iframe', { sandbox: '', referrerpolicy: 'no-referrer', title: 'Page preview', tabindex: '-1' });
    frame.srcdoc = srcdoc;
    if (device === 'mobile') {
      const screen = h('div', { class: 'phone-screen' }, frame);
      viewport.append(h('div', { class: 'phone' }, screen));
      const fit = () => {
        const width = screen.clientWidth;
        const scale = width / PHONE_WIDTH;
        frame.style.width = `${PHONE_WIDTH}px`;
        frame.style.height = `${screen.clientHeight / scale}px`;
        frame.style.transform = `scale(${scale})`;
      };
      fit();
      return { frame, fit, target: screen };
    }
    viewport.append(frame);
    const fit = () => {
      const width = viewport.clientWidth || 1;
      const scale = Math.min(1, width / DESKTOP_WIDTH);
      const virtual = Math.max(width, DESKTOP_WIDTH);
      frame.style.width = `${scale < 1 ? DESKTOP_WIDTH : virtual}px`;
      frame.style.height = `${viewport.clientHeight / scale}px`;
      frame.style.transform = scale < 1 ? `scale(${scale})` : 'none';
      if (scale >= 1) frame.style.width = `${width}px`;
    };
    fit();
    return { frame, fit, target: viewport };
  }

  function paintPreview() {
    if (!studio || !studio.viewport || S.previewMode !== 'page') return;
    const draft = draftFor(S.q);
    const viewport = studio.viewport;
    if (studio.observer) studio.observer.disconnect();
    clear(viewport);
    if (!draft.text.trim()) {
      viewport.append(h('div', { class: 'empty' },
        icon('doc'),
        h('strong', { text: draft.loading ? 'Loading your current page' : 'Your page appears here' }),
        h('span', { text: draft.loading ? 'One moment.' : 'Paste HTML or start writing on the left.' })));
      return;
    }
    const made = frameFor(viewport, previewSource(draft.text), S.device);
    studio.frame = made.frame;
    if ('ResizeObserver' in window) {
      studio.observer = new ResizeObserver(() => made.fit());
      studio.observer.observe(made.target);
    }
  }

  function serpView(draft) {
    const facts = docFacts(draft.text);
    const P = S.project;
    const ours = looseKey(draft.url).startsWith(P.domain.replace(/^www\./, ''));
    let path = '';
    try {
      const u = new URL(draft.url);
      path = u.pathname.split('/').filter(Boolean).map((part) => ` › ${decodeURIComponent(part)}`).join('');
      path = `${u.protocol}//${u.hostname}${path}`;
    } catch (_err) {
      path = draft.url;
    }
    const siteName = ours ? P.name.replace(/\s*\(.*\)\s*$/, '') : hostOf(draft.url);
    const title = facts.title || facts.h1 || 'Untitled page';
    let desc = facts.description || 'No description yet. Search engines pick text from the page when there is no meta description.';
    if (desc.length > 160) desc = `${desc.slice(0, 157).replace(/\s+\S*$/, '')} ...`;
    const view = h('div', { class: 'serp-view' },
      h('div', { class: 'snippet' },
        h('div', { class: 'snippet-site' },
          h('span', { class: 'fav', text: initial(siteName) }),
          h('div', null, h('div', { class: 'snippet-name', text: siteName }), h('div', { class: 'snippet-url', text: path }))),
        h('span', { class: 'snippet-title', text: title.length > 62 ? `${title.slice(0, 59).replace(/\s+\S*$/, '')} ...` : title }),
        h('div', { class: 'snippet-desc', text: desc })),
    );
    if (!draft.text.trim()) return view;
    const words = S.q.toLowerCase().split(/\s+/).filter((w) => w.length > 2);
    const inTitle = words.filter((w) => title.toLowerCase().includes(w)).length;
    const checks = [
      check('Title length', `${facts.title.length} characters`, !facts.title ? 'bad' : facts.title.length > 70 ? 'bad' : facts.title.length > 60 || facts.title.length < 20 ? 'warn' : 'ok'),
      check('Query words in title', `${inTitle} of ${words.length}`, inTitle === words.length ? 'ok' : inTitle ? 'warn' : 'bad'),
      check('Meta description', facts.descSource === 'meta' ? `${facts.description.length} characters` : 'missing', facts.descSource !== 'meta' ? 'warn' : facts.description.length > 160 || facts.description.length < 70 ? 'warn' : 'ok'),
      check('Main heading (H1)', facts.h1 ? 'present' : 'missing', facts.h1 ? 'ok' : 'bad'),
    ];
    view.append(h('div', { class: 'checks' }, checks));
    return view;
  }

  function check(label, value, level) {
    return h('div', { class: `check ${level === 'ok' ? '' : level}` },
      h('span', { class: 'dot', 'aria-hidden': 'true' }),
      h('span', { class: 'label', text: label }),
      h('span', { class: 'value', text: value + (level === 'ok' ? '' : level === 'warn' ? ', check' : ', fix') }));
  }

  /* ------------------------------------------------------------------ test */

  let testing = false;

  async function runTest() {
    if (testing || !studio) return;
    const q = S.q;
    const draft = draftFor(q);
    if (!draft.text.trim()) {
      toast('Write or paste a draft first.');
      studio.editor.focus();
      return;
    }
    const row = queryRow(q);
    const sameAsCurrent = !!(row && row.your_url && draft.base != null && looseKey(draft.url) === looseKey(row.your_url));
    testing = true;
    const btn = studio.testBtn;
    clear(btn).append(h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Testing');
    btn.disabled = true;
    try {
      const [run] = await Promise.all([
        api.post(`/api/v1/projects/${enc(S.slug)}/test`, {
          q,
          draft: draft.text,
          url: draft.url,
          original: sameAsCurrent ? 'current' : 'none',
          original_html: sameAsCurrent ? draft.base : '',
        }),
        getCompare(q).catch(() => null),
      ]);
      S.results.set(q, run);
      if (S.q === q && S.screen === 'studio') {
        renderResult(run, true);
        studio.resultHost.scrollIntoView({ behavior: reduceMotion.matches ? 'auto' : 'smooth', block: 'start' });
      }
    } catch (err) {
      toast(err.message);
    } finally {
      testing = false;
      if (studio && studio.testBtn === btn) {
        clear(btn).append('Test draft');
        paintStats();
      }
    }
  }

  function confidenceTag(label) {
    if (!label || label === 'n/a') return null;
    if (label === 'CLEARER') return h('span', { class: 'tag tag-clear tag-dot', text: 'Clearer call' });
    return h('span', { class: 'tag tag-unsure tag-dot', text: 'Uncertain' });
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
    if (after.position == null) return after.unplaced || 'The draft did not pass the on-topic check, so it was not placed.';
    const fa = run.facts_after || {};
    const fb = run.facts_before;
    const parts = [];
    if (fb) {
      const diff = fa.word_count - fb.word_count;
      if (Math.abs(diff) >= Math.max(25, fb.word_count * 0.08)) parts.push(diff > 0 ? `is ${plural(diff, 'word')} longer` : `is ${plural(-diff, 'word')} shorter`);
      const hd = fa.headings - fb.headings;
      if (hd > 0) parts.push(`adds ${plural(hd, 'section')}`);
      else if (hd < 0) parts.push(`drops ${plural(-hd, 'section')}`);
      if (fa.table_count > fb.table_count) parts.push(fb.table_count ? 'adds a table' : 'adds a table');
      else if (fa.table_count < fb.table_count) parts.push('drops a table');
      if (fa.has_faq !== fb.has_faq) parts.push(fa.has_faq === 'yes' ? 'adds an FAQ' : 'drops the FAQ');
      if (fa.updated !== fb.updated && fa.updated && fa.updated !== 'none') parts.push('shows a different date');
      if (fa.title !== fb.title) parts.push('changes the title');
      if (!parts.length) return 'Your draft reads much like the current page.';
    } else {
      parts.push(`is scored as a new page against ${plural(run.candidates, 'page')}`);
      if (fa.word_count != null) parts.push(`has ${plural(fa.word_count, 'word')}`);
    }
    const shown = parts.slice(0, 3);
    const sentence = shown.length > 1 ? `${shown.slice(0, -1).join(', ')} and ${shown[shown.length - 1]}` : shown[0];
    return `Your draft ${sentence}.`;
  }

  function renderResult(run, animate) {
    const host = studio.resultHost;
    clear(host);
    const verdict = verdictOf(run);
    const after = run.after || {};
    const before = run.before;
    const card = h('section', { class: 'card result-card', 'aria-live': 'polite' },
      confidenceTag(after.confidence),
      h('h2', { class: `verdict ${verdict.cls}`, text: verdict.text }));
    if (after.position != null) {
      const ranks = h('div', { class: 'ranks' });
      if (before && before.position != null) {
        ranks.append(
          h('div', { class: 'rank-box before' }, h('span', { class: 'k', text: 'Current page' }), h('span', { class: 'n', text: `#${before.position}` }), h('span', { class: 'of', text: `of ${before.of}` })),
          icon('arrow', 'arrow'),
        );
      }
      ranks.append(h('div', { class: 'rank-box after' }, h('span', { class: 'k', text: 'Draft' }), h('span', { class: 'n', text: `#${after.position}` }), h('span', { class: 'of', text: `of ${after.of}` })));
      card.append(ranks);
    }
    card.append(
      h('p', { class: 'reason', text: reasonOf(run) }),
      h('p', { class: 'caveat', text: 'Among these candidate pages, not a Google ranking.' }),
      h('div', { class: 'result-actions' },
        h('button', {
          class: 'btn btn-gray',
          type: 'button',
          onClick: () => {
            studio.editor.scrollIntoView({ behavior: reduceMotion.matches ? 'auto' : 'smooth', block: 'center' });
            setTimeout(() => studio.editor.focus({ preventScroll: true }), 350);
          },
        }, icon('pencil'), 'Edit draft'),
        link('rankings', S.q, [icon('list'), 'See rankings'], { class: 'btn btn-gray' })),
    );
    host.append(card);
    if (after.order && after.order.length) host.append(orderCard(run, animate));
    host.append(detailsBlock(run));
  }

  function orderCard(run, animate) {
    const after = run.after;
    const before = run.before && run.before.order && run.before.order.length ? run.before : null;
    const draft = draftFor(run.search);
    const facts = docFacts(draft.text);
    const keyOf = (item) => (item.page ? '__page__' : item.url);
    const titles = new Map();
    const list = h('ol', { class: 'order-list', 'aria-label': 'Predicted order' });
    const rows = new Map();

    function makeRow(item) {
      const isPage = !!item.page;
      const title = isPage ? facts.title || facts.h1 || 'Your draft' : item.domain;
      const li = h('li', { class: `order-row${isPage ? ' is-draft' : ''}` },
        h('span', { class: 'pos', text: String(item.predicted) }),
        h('span', { class: 'fav', text: isPage ? '' : initial(item.domain) }),
        h('div', { class: 'main' },
          h('div', { class: 't', text: title }),
          h('div', { class: 'd', text: isPage ? `Your draft · ${hostOf(run.url)}` : item.domain })),
        h('div', { class: 'side' }));
      if (isPage) li.querySelector('.fav').append(icon('pencil'));
      rows.set(keyOf(item), li);
      if (!isPage) titles.set(item.url, li.querySelector('.t'));
      return li;
    }

    function side(li, item) {
      const box = clear(li.querySelector('.side'));
      if (item.page) {
        const verdict = verdictOf(run);
        if (before && verdict.change != null) {
          box.append(moveChip(verdict.change), ' ', h('span', { text: ` was #${run.before.position}` }));
        } else box.append(h('span', { text: 'New page' }));
      } else {
        box.append(h('span', { text: `Observed #${item.observed}` }));
      }
    }

    const start = before || after;
    for (const item of start.order) list.append(makeRow(item));
    for (const item of after.order) if (!rows.has(keyOf(item))) list.append(makeRow(item));

    const card = h('section', { class: 'card order-card' },
      h('div', { class: 'card-head' },
        h('div', null,
          h('h2', { text: 'Predicted order' }),
          h('div', { class: 'table-head-meta', text: `${plural(after.of, 'page')} for "${run.search}"${run.which === 'competitors' ? ', named competitors only' : ''}` }))),
      list);

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
        if (item.page) clear(li.querySelector('.side')).append(h('span', { text: 'Current page' }));
        else side(li, item);
      }
      setTimeout(settle, 900);
    } else {
      settle();
    }

    getCompare(run.search).then((view) => {
      for (const cand of view.candidates || []) {
        const el = titles.get(cand.url);
        if (el && cand.title) el.textContent = cand.title;
      }
    }).catch(() => {});
    return card;
  }

  /* A pull label is a date when one was recorded, or a phrase such as "pull date not recorded". Show dates only. */
  function seenText(pulled, prefix = '', suffix = '') {
    return pulled && /\d{4}/.test(pulled) ? `${prefix}${pulled}${suffix}` : '';
  }

  function moveChip(change) {
    if (!change) return h('span', { class: 'move same', text: 'same' });
    return h('span', { class: `move ${change > 0 ? 'up' : 'down'}` }, icon(change > 0 ? 'up' : 'down'), String(Math.abs(change)));
  }

  function detailsBlock(run) {
    const fa = run.facts_after || {};
    const fb = run.facts_before;
    const factRows = [
      ['Words', 'word_count'],
      ['Sections', 'headings'],
      ['Tables', 'table_count'],
      ['FAQ', 'has_faq'],
      ['Visible date', 'updated'],
      ['Links', 'link_count'],
      ['Title', 'title'],
    ];
    const table = h('table', { class: 'kv' });
    if (fb) table.append(h('thead', null, h('tr', null, h('th', { text: '' }), h('th', { text: 'Current' }), h('th', { text: 'Draft' }))));
    const tbody = h('tbody');
    for (const [label, key] of factRows) {
      const a = fa[key];
      const b = fb ? fb[key] : undefined;
      const fmt = (v) => (v == null || v === '' ? 'none' : typeof v === 'number' ? v.toLocaleString() : String(v));
      tbody.append(h('tr', null,
        h('th', { text: label }),
        fb ? h('td', { text: fmt(b) }) : null,
        h('td', { class: fb && fmt(a) !== fmt(b) ? 'changed' : null, text: fmt(a) })));
    }
    table.append(tbody);

    const panels = [h('section', { class: 'card panel' }, h('h3', { text: fb ? 'What changed' : 'The draft' }), table)];

    const topic = run.topic_after;
    if (topic && topic.text) {
      const panel = h('section', { class: 'card panel' }, h('h3', { text: 'On topic' }), h('p', { text: topic.text }));
      if (topic.missing && topic.missing.length) {
        panel.append(h('p', { text: 'Words the ranking pages use that the draft does not:' }), h('div', { class: 'words' }, topic.missing.map((w) => h('span', { text: w }))));
      }
      panels.push(panel);
    }

    const after = run.after || {};
    const sure = h('section', { class: 'card panel' }, h('h3', { text: 'How sure is this' }));
    if (run.query_pairwise != null) sure.append(h('p', { text: `On this query, the model ordered ${pct(run.query_pairwise)} of ${plural(run.query_pairs || 0, 'page pair')} correctly when it was held out. 50% is a coin flip.` }));
    if (run.overall_pairwise != null) sure.append(h('p', { text: `Across all queries: ${pct(run.overall_pairwise)}.` }));
    if (after.confidence_note) sure.append(h('p', { text: after.confidence_note }));
    panels.push(sure);

    const data = h('section', { class: 'card panel' }, h('h3', { text: 'Data' }));
    const ds = S.project.data || {};
    data.append(h('p', { text: ds.long || '' }));
    data.append(h('p', { text: `Compared against ${plural(run.candidates, 'stored candidate page')} (${run.set_label}).${seenText(run.pulled, ' Positions seen ', '.')}` }));
    if (run.has_original) data.append(h('p', { text: `Current page: ${run.original_label}.` }));
    for (const note of run.notes || []) data.append(h('p', { text: note }));
    data.append(h('p', { text: run.saved ? 'This test is saved in the project.' : "This test isn't saved. Nothing you paste is stored." }));
    panels.push(data);

    if (run.ai && run.ai.status && run.ai.status !== 'off' && run.ai.status !== 'unavailable') {
      panels.push(h('section', { class: 'card panel' }, h('h3', { text: 'AI notes' }), h('div', { class: 'ai-text', text: run.ai.text || '' })));
    }

    return h('details', { class: 'more' },
      h('summary', null, 'Details', icon('chevron')),
      h('div', { class: 'more-grid' }, panels));
  }

  /* ------------------------------------------------------------------ rankings */

  function renderRankings() {
    const P = S.project;
    const grid = h('section', { class: 'qgrid', 'aria-label': 'Queries' });
    for (const row of P.queries) {
      grid.append(h('button', {
        class: 'qcard',
        type: 'button',
        'aria-pressed': String(row.q === S.q),
        onClick: () => {
          if (row.q === S.q) return;
          S.q = row.q;
          history.replaceState({}, '', pathFor('rankings', row.q));
          for (const el of grid.querySelectorAll('.qcard')) el.setAttribute('aria-pressed', String(el.dataset.q === row.q));
          paintTable(tableHost);
          document.title = `Rankings · ${S.q} · Ranksim`;
        },
        dataset: { q: row.q },
      },
      h('span', { class: 'q', text: row.q }),
      h('span', { class: 'nums' },
        h('span', { class: 'num' }, h('span', { class: `v${row.observed == null ? ' none' : ''}`, text: row.observed == null ? 'Not listed' : `#${row.observed}` }), h('span', { class: 'k', text: 'Observed' })),
        h('span', { class: 'num pred' },
          row.predicted == null
            ? h('span', { class: 'v none', text: 'n/a' })
            : h('span', { class: 'v' }, `#${row.predicted}`, h('small', { text: `of ${row.of}` })),
          h('span', { class: 'k', text: 'Predicted' })))));
    }
    const tableHost = h('section', { class: 'card table-card', 'aria-live': 'polite' });
    clear(main).append(
      h('div', { class: 'wrap screen' },
        h('section', { class: 'hero hero-compact' },
          h('h1', { text: 'Rankings' }),
          h('p', { class: 'lede', text: 'Every page that ranks for your queries, next to the order the model predicts.' })),
        grid,
        tableHost),
      footer(),
    );
    paintTable(tableHost);
  }

  async function paintTable(host) {
    const q = S.q;
    clear(host).append(
      h('div', { class: 'card-head' }, h('h2', { text: q })),
      h('div', null, [1, 2, 3, 4, 5].map(() => h('div', { class: 'skeleton sk-row' }))));
    let view;
    try {
      view = await getCompare(q);
    } catch (err) {
      clear(host).append(h('div', { class: 'card-head' }, h('h2', { text: q })), h('div', { class: 'notes' }, h('p', { text: err.message })));
      return;
    }
    if (S.q !== q) return;
    const sortSeg = h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': 'Order by' });
    for (const [key, label] of [['observed', 'Observed'], ['predicted', 'Predicted']]) {
      sortSeg.append(h('button', {
        type: 'button',
        'aria-pressed': String(S.sort === key),
        onClick: () => {
          S.sort = key;
          paintTable(host);
        },
      }, label));
    }
    const meta = h('div', { class: 'table-head-meta' },
      h('span', { text: `${plural(view.total, 'page')} · ${view.scored} analyzed` }),
      view.query_pairwise != null ? h('span', { text: `Held out accuracy ${pct(view.query_pairwise)} on this query` }) : null,
      seenText(view.pulled) ? h('span', { text: seenText(view.pulled, 'Seen ') }) : null);
    clear(host).append(h('div', { class: 'card-head' }, h('div', null, h('h2', { text: q }), meta), h('div', { class: 'tools' }, sortSeg)));
    if (view.error) {
      host.append(h('div', { class: 'notes' }, h('p', { text: view.error })));
    }
    const cands = view.candidates.slice();
    if (S.sort === 'predicted') cands.sort((a, b) => (a.predicted == null) - (b.predicted == null) || (a.predicted || 0) - (b.predicted || 0) || a.observed - b.observed);
    const list = h('div', { class: 'rows', role: 'list' });
    host.append(h('div', { class: 'rows-head', 'aria-hidden': 'true' },
      h('span', { text: 'Observed' }), h('span', { text: 'Predicted' }), h('span', { text: 'Page' }), h('span', { text: 'Shift' }), h('span')));
    for (const cand of cands) {
      const you = cand.role === 'you';
      const tags = [];
      if (you) tags.push(h('span', { class: 'tag tag-you', text: 'You' }));
      else if (cand.role === 'competitor') tags.push(h('span', { class: 'tag tag-comp', text: 'Competitor' }));
      list.append(h('button', {
        class: `rank-row${you ? ' you' : ''}${cand.stored ? '' : ' missing'}`,
        type: 'button',
        role: 'listitem',
        onClick: (event) => openPageSheet(cand, view, event.currentTarget),
      },
      h('span', { class: 'cell-n', text: String(cand.observed) }),
      cand.predicted != null ? h('span', { class: 'cell-n pred', text: String(cand.predicted) }) : h('span', { class: 'cell-n na', text: 'n/a' }),
      h('span', { class: 'page' },
        h('span', { class: 'fav', text: initial(cand.domain) }),
        h('span', { class: 'txt' },
          h('span', { class: 't', text: cand.title || cand.domain }),
          h('span', { class: 'd' }, h('span', { class: 'host', text: cand.stored ? cand.domain : `${cand.domain} · no stored copy` }), tags))),
      h('span', { class: 'cell-move' }, cand.shift == null ? '' : moveChip(cand.shift)),
      icon('next', 'go')));
    }
    host.append(list);
    const notes = (view.notes || []).filter(Boolean);
    host.append(h('div', { class: 'notes' },
      h('p', { text: 'Observed is the position in the stored results. Predicted is the order the model gives the analyzed pages. Shift compares the two.' }),
      notes.map((note) => h('p', { text: note }))));
  }

  /* ------------------------------------------------------------------ sheets */

  let lastFocus = null;

  function openSheet(build) {
    closeSheet(true);
    lastFocus = document.activeElement;
    const scrim = h('div', { class: 'scrim', onClick: () => closeSheet() });
    const closeBtn = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Close', onClick: () => closeSheet() }, icon('close'));
    const sheet = h('aside', { class: 'sheet', role: 'dialog', 'aria-modal': 'true' });
    build(sheet, closeBtn);
    document.body.append(scrim, sheet);
    S.sheet = { scrim, sheet, observer: null };
    requestAnimationFrame(() => {
      scrim.classList.add('open');
      sheet.classList.add('open');
    });
    closeBtn.focus({ preventScroll: true });
    return S.sheet;
  }

  function closeSheet(instant) {
    const current = S.sheet;
    if (!current) return;
    S.sheet = null;
    if (current.observer) current.observer.disconnect();
    const done = () => {
      current.scrim.remove();
      current.sheet.remove();
    };
    if (instant || reduceMotion.matches) done();
    else {
      current.scrim.classList.remove('open');
      current.sheet.classList.remove('open');
      setTimeout(done, 420);
    }
    if (!instant && lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
  }

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && S.sheet) closeSheet();
    if (event.key === 'Tab' && S.sheet) {
      const focusable = Array.from(S.sheet.sheet.querySelectorAll('button,a[href],input,select,textarea,summary')).filter((el) => !el.disabled && el.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  function openPageSheet(cand, view) {
    const you = cand.role === 'you';
    const sheetState = openSheet((sheet, closeBtn) => {
      const titleId = 'sheet-title';
      sheet.setAttribute('aria-labelledby', titleId);
      sheet.append(h('div', { class: 'sheet-head' },
        h('div', { style: null }, h('h2', { id: titleId, text: cand.title || cand.domain }), h('div', { class: 'sub', text: cand.url })),
        closeBtn));
      const body = h('div', { class: 'sheet-body' });
      sheet.append(body);

      const pair = h('div', { class: 'bigpair' },
        h('div', { class: 'rank-box' }, h('span', { class: 'k', text: 'Observed' }), h('span', { class: 'n', text: `#${cand.observed}` }), h('span', { class: 'of', text: `of ${view.total}` })),
        h('div', { class: 'rank-box pred' }, h('span', { class: 'k', text: 'Predicted' }), h('span', { class: 'n', text: cand.predicted == null ? 'n/a' : `#${cand.predicted}` }), h('span', { class: 'of', text: cand.predicted == null ? 'not analyzed' : `of ${view.scored}` })));
      body.append(pair);
      if (you && view.confidence && view.confidence !== 'n/a') body.append(h('div', { class: 'sheet-section' }, confidenceTag(view.confidence)));

      const previewSection = h('div', { class: 'sheet-section' }, h('h3', { text: 'Preview' }));
      const viewport = h('div', { class: 'viewport' });
      previewSection.append(h('div', { class: 'mini-browser' },
        h('div', { class: 'chrome' },
          h('span', { class: 'lights', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
          h('span', { class: 'address' }, icon('lock'), h('input', { type: 'text', value: cand.url, readonly: true, 'aria-label': 'Page URL', tabindex: '-1' })),
          h('span')),
        viewport));
      body.append(previewSection);
      if (cand.stored) {
        viewport.append(h('div', { class: 'empty' }, h('span', { class: 'spinner', 'aria-hidden': 'true' })));
        getPage(cand.url).then((page) => {
          if (!S.sheet || S.sheet.sheet !== sheet) return;
          clear(viewport);
          const made = frameFor(viewport, previewSource(page.html), 'desktop');
          if ('ResizeObserver' in window) {
            S.sheet.observer = new ResizeObserver(() => made.fit());
            S.sheet.observer.observe(viewport);
          }
        }).catch(() => {
          clear(viewport).append(h('div', { class: 'empty' }, icon('doc'), h('strong', { text: 'No preview' }), h('span', { text: 'This page has no stored copy.' })));
        });
      } else {
        viewport.append(h('div', { class: 'empty' }, icon('doc'), h('strong', { text: 'No stored copy' }), h('span', { text: "It keeps its observed position, but the model can't read it, so it isn't placed." })));
      }

      if (cand.stored) {
        const chips = h('div', { class: 'chips' },
          cand.word_count != null ? h('span', null, h('b', { text: cand.word_count.toLocaleString() }), ' words') : null,
          h('span', { text: cand.has_table ? 'Has a table' : 'No table' }),
          h('span', { text: cand.has_faq ? 'Has an FAQ' : 'No FAQ' }),
          h('span', { text: cand.updated ? `Updated ${cand.updated}` : 'No visible date' }),
          cand.stored_at ? h('span', { text: `Stored ${cand.stored_at}` }) : null);
        body.append(h('div', { class: 'sheet-section' }, h('h3', { text: 'Page' }), chips));
      }

      if (cand.evidence && cand.evidence.length) {
        const ev = h('div', { class: 'evidence' });
        for (const item of cand.evidence) {
          const width = item.of > 1 ? Math.max(8, Math.round(((item.of - item.rank + 1) / item.of) * 100)) : 100;
          const meter = h('span', { class: `meter${item.rank === 1 ? ' top' : item.rank === item.of && item.of > 2 ? ' low' : ''}`, title: `${item.rank} of ${item.of}` }, h('i'));
          meter.firstChild.style.width = `${width}%`;
          ev.append(h('div', { class: 'ev' }, h('span', { class: 'l', text: item.label }), h('span', { class: 'v', text: item.value }), meter));
        }
        body.append(h('div', { class: 'sheet-section' },
          h('h3', { text: 'What the model reads' }),
          ev,
          h('p', { class: 'ev-legend', text: `Bars show where this page stands among the ${view.scored} analyzed pages. A full bar is the best of them.` })));
      }

      const actions = h('div', { class: 'sheet-actions' });
      if (you) {
        actions.append(h('button', {
          class: 'btn btn-primary',
          type: 'button',
          onClick: () => {
            closeSheet(true);
            const draft = draftFor(view.search);
            if (!draft.text.trim() || !draft.touched) {
              draft.url = cand.url;
              loadCurrent(view.search, false);
            }
            go('studio', view.search);
          },
        }, icon('pencil'), 'Edit in Studio'));
      } else {
        actions.append(h('button', {
          class: 'btn btn-primary',
          type: 'button',
          onClick: () => {
            closeSheet(true);
            go('studio', view.search);
          },
        }, 'Test a draft for this query'));
      }
      body.append(actions);
    });
    return sheetState;
  }

  function openAbout() {
    const P = S.project;
    openSheet((sheet, closeBtn) => {
      sheet.setAttribute('aria-labelledby', 'about-title');
      sheet.append(h('div', { class: 'sheet-head' },
        h('div', null, h('h2', { id: 'about-title', text: 'About Ranksim' }), h('div', { class: 'sub', text: P ? `${P.name} · ${P.site}` : '' })),
        closeBtn));
      const body = h('div', { class: 'sheet-body' });
      sheet.append(body);
      body.append(h('p', { class: 'sheet-lede', text: 'Ranksim builds a small search engine for your queries and predicts where a page would land among the pages that rank today, so you can test an edit before you publish it.' }));
      body.append(h('div', { class: 'sheet-section' },
        h('h3', { text: 'How it works' }),
        h('ol', { class: 'steps' },
          h('li', { text: 'Collect the pages that rank for each query.' }),
          h('li', { text: 'Train a ranking model on them. Each query is tested on a model that never saw it.' }),
          h('li', { text: 'Score your draft against the same pages, with the same model.' }))));
      if (P && P.model && P.model.pairwise != null) {
        const acc = h('div', { class: 'sheet-section' },
          h('h3', { text: 'Accuracy' }),
          h('div', { class: 'big-stat' }, pct(P.model.pairwise), h('small', { text: 'of page pairs in the right order' })),
          h('p', { class: 'sheet-text', text: `Held out across ${plural(P.model.queries, 'query', 'queries')} and ${plural(P.model.pairs, 'pair')}. 50% is a coin flip.` }));
        if (P.data && P.data.kind === 'synthetic') acc.append(h('p', { class: 'sheet-text', text: 'This is demo data, so the number only shows the pipeline runs. It says nothing about accuracy on real Google results.' }));
        body.append(acc);
      }
      if (P && P.data) body.append(h('div', { class: 'sheet-section' }, h('h3', { text: 'Data' }), h('p', { class: 'sheet-text', text: P.data.long })));
      if (S.session && S.session.mode === 'private') {
        body.append(h('div', { class: 'sheet-section' },
          h('h3', { text: 'This server' }),
          h('p', { class: 'sheet-text' }, googleLine()),
          h('p', { class: 'sheet-text' }, storageLine()),
          h('div', { class: 'sheet-actions' }, h('button', { class: 'btn', type: 'button', onClick: () => { closeSheet(true); signOut(); } }, icon('out'), 'Sign out'))));
      } else if (S.publicDemo) {
        body.append(h('div', { class: 'sheet-section' },
          h('h3', { text: 'Use your own pages' }),
          h('p', { class: 'sheet-text', text: 'This public demo runs on invented sites and keeps nothing you paste. Run Ranksim on your own computer to use real queries and pages:' }),
          h('pre', { class: 'cmd', text: 'pip install -e .\npython -m aeo.ranksim serve' })));
      } else {
        body.append(h('div', { class: 'sheet-section' },
          h('h3', { text: 'Manage' }),
          h('div', { class: 'sheet-actions' },
            P ? h('a', { class: 'btn btn-gray', href: `/p/${enc(P.slug)}` }, 'Project setup') : null,
            h('a', { class: 'btn btn-gray', href: '/projects' }, 'All projects'))));
      }
    });
  }

  /* ------------------------------------------------------------------ sign in */

  function renderLogin() {
    S.project = null;
    S.slug = null;
    renderBar();
    const field = h('input', { class: 'field', type: 'password', id: 'pw', autocomplete: 'current-password', required: true, 'aria-label': 'Password' });
    const error = h('p', { class: 'form-error', role: 'alert', hidden: true });
    const button = h('button', { class: 'btn btn-primary btn-large', type: 'submit' }, 'Sign in');
    const form = h('form', { class: 'form', novalidate: true },
      h('div', { class: 'form-row' }, h('label', { for: 'pw', text: 'Password' }), field),
      error,
      button);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!field.value) {
        field.focus();
        return;
      }
      error.hidden = true;
      button.disabled = true;
      clear(button).append(h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Signing in');
      try {
        await api.post('/api/v1/login', { password: field.value });
        await boot();
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        button.disabled = false;
        clear(button).append('Sign in');
        field.select();
      }
    });
    clear(main).append(
      h('div', { class: 'wrap screen' },
        h('section', { class: 'hero' }),
        h('div', { class: 'card login-card' },
          h('span', { class: 'logo', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
          h('h1', { text: 'Ranksim' }),
          h('p', { text: 'Sign in to test pages against the searches you want to win.' }),
          form)));
    setTimeout(() => field.focus(), 60);
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

  /* ------------------------------------------------------------------ projects home */

  function googleLine() {
    const google = (S.session && S.session.google) || {};
    if (google.ready) return h('span', null, 'Google results: ', h('b', { text: `${google.provider} connected` }));
    return h('span', null, 'Google results: ', h('b', { text: 'no key yet' }), ' (import positions instead, or add SERPER_API_KEY)');
  }

  function storageLine() {
    const kind = S.session && S.session.storage;
    const text = kind === 'cloud' ? 'Private cloud storage' : kind === 'disk' ? 'This computer' : 'Temporary, not saved';
    return h('span', null, 'Storage: ', h('b', { text }));
  }

  function renderHome() {
    document.title = 'Projects · Ranksim';
    const grid = h('section', { class: 'pgrid', 'aria-label': 'Projects' });
    for (const p of S.projects) {
      const target = `/app/${enc(p.slug)}/${p.built ? 'studio' : 'data'}`;
      grid.append(h('a', {
        class: 'card pcard',
        href: target,
        onClick: (event) => {
          if (event.metaKey || event.ctrlKey) return;
          event.preventDefault();
          navigate(target);
        },
      },
      h('div', null, h('h2', { text: p.name }), h('div', { class: 'site', text: p.site })),
      h('div', { class: 'meta' },
        h('span', { text: plural(p.queries, 'query', 'queries') }),
        p.built ? h('span', { class: 'tag tag-ok', text: 'Ready' }) : h('span', { class: 'tag tag-warn', text: 'Needs a pull' }),
        p.dataset === 'synthetic' ? h('span', { class: 'tag tag-demo', text: 'Demo data' }) : null)));
    }
    grid.append(h('a', {
      class: 'card pcard pcard-new',
      href: '/start',
      onClick: (event) => {
        event.preventDefault();
        navigate('/start');
      },
    }, h('span', { class: 'plus' }, icon('plus')), h('span', { text: 'New project' })));

    const head = h('section', { class: 'hero-left' },
      h('div', null,
        h('h1', { text: 'Projects' }),
        h('p', { class: 'lede', text: 'Each project is a site and the searches it wants to win.' })),
      h('a', { class: 'btn btn-primary', href: '/start', onClick: (event) => { event.preventDefault(); navigate('/start'); } }, icon('plus'), 'New project'));
    const status = h('div', { class: 'status-row' }, googleLine(), storageLine());
    const body = [head, h('div', { class: 'card status-card' }, h('div', { class: 'card-body' }, status)), grid];
    if (!S.projects.length) {
      body.push(h('div', { class: 'card center-card stack-gap' },
        h('h2', { text: 'Start with the demo' }),
        h('p', { text: 'The demo is a made up fencing company with six queries. It takes a few seconds and needs no keys.' }),
        h('button', { class: 'btn', type: 'button', onClick: () => buildDemo(false) }, 'Load the demo')));
    }
    clear(main).append(h('div', { class: 'wrap screen' }, body), footer());
  }

  /* ------------------------------------------------------------------ new project */

  function renderNewProject() {
    document.title = 'New project · Ranksim';
    const name = h('input', { class: 'field', id: 'np-name', placeholder: 'SmartCrew', autocomplete: 'off' });
    const site = h('input', { class: 'field', id: 'np-site', placeholder: 'www.example.com', autocomplete: 'off', spellcheck: 'false' });
    const queries = h('textarea', { class: 'field', id: 'np-queries', rows: '7', placeholder: 'all in one dirt work software\nhcss heavyjob alternative\nexcavation estimating software' });
    const rivals = h('textarea', { class: 'field', id: 'np-rivals', rows: '3', placeholder: 'competitor.com\nanother-rival.com', spellcheck: 'false' });
    const error = h('div', { class: 'form-error', role: 'alert', hidden: true });
    const google = (S.session && S.session.google) || {};
    const submit = h('button', { class: 'btn btn-primary btn-large', type: 'submit' }, google.ready ? 'Create and pull from Google' : 'Create project');
    const count = h('span', { class: 'form-help' });
    const recount = () => {
      const n = queries.value.split('\n').map((line) => line.trim()).filter(Boolean).length;
      count.textContent = n ? `${plural(n, 'query', 'queries')}${google.ready ? `, about ${n} Google credits per pull` : ''}` : 'One per line.';
    };
    queries.addEventListener('input', recount);
    recount();
    const form = h('form', { class: 'form', novalidate: true },
      h('div', { class: 'form-row' }, h('label', { for: 'np-name', text: 'Project name' }), name),
      h('div', { class: 'form-row' }, h('label', { for: 'np-site', text: 'Your site' }), site, h('span', { class: 'form-help', text: 'The domain your pages live on. Pages on it are marked as yours.' })),
      h('div', { class: 'form-row' }, h('label', { for: 'np-queries', text: 'Searches you want to win' }), queries, count),
      h('div', { class: 'form-row' }, h('label', { for: 'np-rivals', text: 'Competitors (optional)' }), rivals, h('span', { class: 'form-help', text: 'Domains, one per line. They are highlighted in rankings.' })),
      error,
      h('div', { class: 'form-actions' }, submit, h('a', { class: 'btn btn-plain', href: '/', onClick: (event) => { event.preventDefault(); navigate('/'); } }, 'Cancel')));
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      error.hidden = true;
      submit.disabled = true;
      const label = submit.textContent;
      clear(submit).append(h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Creating');
      try {
        const made = await api.post('/api/v1/projects', { name: name.value, site: site.value, queries: queries.value, competitors: rivals.value });
        await loadProjects();
        history.pushState({}, '', `/app/${enc(made.slug)}/data`);
        await openProject(made.slug, { slug: made.slug, screen: 'data', q: '' });
        if (google.ready) startPull(true);
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        submit.disabled = false;
        clear(submit).append(label);
      }
    });
    clear(main).append(
      h('div', { class: 'wrap narrow screen' },
        h('section', { class: 'hero-left' },
          h('div', null,
            h('h1', { text: 'New project' }),
            h('p', { class: 'lede', text: 'Ranksim pulls the live top 10 for each search, stores every ranking page, and trains a model on them.' }))),
        h('section', { class: 'card form-card' }, h('div', { class: 'card-body' }, form))),
      footer());
    setTimeout(() => name.focus(), 60);
  }

  /* ------------------------------------------------------------------ data */

  const PULL_STEPS = [
    ['google', 'Google results', 'The live top 10 for each search'],
    ['crawl', 'Pages', 'Every ranking page, robots.txt respected'],
    ['train', 'Model', 'One held out model per search'],
    ['store', 'Save', 'Project, pages and model'],
  ];
  let pulling = null;

  async function renderData() {
    const P = S.project;
    const wrap = h('div', { class: 'wrap screen' },
      h('section', { class: 'hero-left' },
        h('div', null,
          h('p', { class: 'eyebrow', text: 'Data' }),
          h('h1', { text: P.name }),
          h('p', { class: 'lede', text: P.site })),
        h('div', { class: 'status-row' }, googleLine(), storageLine())),
      h('div', { class: 'boot' }, h('span', { class: 'spinner', 'aria-hidden': 'true' })));
    clear(main).append(wrap, footer());
    try {
      S.setup = await api.get(`/api/v1/projects/${enc(S.slug)}/setup`);
    } catch (err) {
      wrap.lastChild.replaceWith(h('div', { class: 'callout bad' }, icon('warn'), h('span', { text: err.message })));
      return;
    }
    if (S.screen !== 'data') return;
    wrap.lastChild.replaceWith(dataGrid(S.setup));
    if (pulling && pulling.slug === S.slug) paintPull();
  }

  function dataGrid(setup) {
    const left = h('div', { class: 'col' }, pullCard(setup), queriesCard(setup));
    const right = h('div', { class: 'col' }, importCard(setup), competitorsCard(setup), dangerCard(setup));
    return h('div', { class: 'data-grid' }, left, right);
  }

  function pullCard(setup) {
    const google = setup.google || {};
    const imported = setup.queries.some((row) => row.candidates > 0);
    const canPull = google.ready || imported;
    const button = h('button', { class: 'btn btn-primary btn-large', type: 'button', id: 'pull-btn', disabled: !canPull || !!pulling, onClick: () => startPull(false) },
      google.ready ? 'Pull from Google' : 'Build from imported positions');
    const intro = google.ready
      ? `Gets the live top 10 for ${plural(setup.queries.length, 'search', 'searches')} with ${google.provider}, fetches every page that is missing or older than a week, and trains the model.`
      : 'No Google key on this server, so Ranksim builds from the positions you import. Add SERPER_API_KEY to the server to pull live results.';
    const body = h('div', { class: 'card-body' },
      h('p', { class: 'sheet-text', text: intro }),
      !canPull ? h('div', { class: 'callout' }, icon('warn'), h('span', { text: 'Import a CSV of positions first (on the right), or add a Google key.' })) : null,
      h('ol', { class: 'pull-steps', id: 'pull-steps' }, PULL_STEPS.map(([key, label, help]) => h('li', { class: 'pull-step', dataset: { step: key } },
        h('span', { class: 'ico' }, icon('check')),
        h('span', { class: 'what' }, h('b', { text: label }), h('span', { class: 'msg', text: help }), h('span', { class: 'bar-track', hidden: true }, h('i'))),
        h('span', { class: 'when' })))),
      h('div', { class: 'form-actions' }, button, h('span', { class: 'form-help', id: 'pull-note', text: lastPullText(setup) })));
    const card = h('section', { class: 'card pull-card' },
      h('div', { class: 'card-head' }, h('h2', null, 'Pull', h('span', { class: 'hint', text: setup.engine.ready ? 'Model ready' : 'Not built yet' }))),
      body);
    if (setup.last_pull && !pulling) {
      const lp = setup.last_pull;
      for (const step of lp.steps || []) {
        const key = step.step === 'save' ? 'store' : step.step;
        markStep(card, key, 'done', stepText(key, lp), step.seconds);
      }
    }
    return card;
  }

  /* Plain sentences for each finished step, from the pull's counts. The engine's own log stays on the server. */
  function stepText(key, report) {
    const c = report.counts || {};
    if (key === 'google') {
      return report.google_kind === 'api'
        ? `Asked Google for ${plural(report.searches, 'search', 'searches')}. ${report.searches_with_results} came back with results.`
        : 'Used the positions already saved. Google was not asked.';
    }
    if (key === 'crawl') {
      const failed = (c.http_error || 0) + (c.error || 0) + (c.unsafe || 0);
      const parts = [`${c.fetched_now || 0} fetched`, `${(c.stored || 0) + (c.kept_stored || 0)} already stored`];
      if (c.blocked_robots) parts.push(`${c.blocked_robots} blocked by robots.txt`);
      if (failed) parts.push(`${failed} could not be fetched`);
      return `${parts.join(', ')}.`;
    }
    if (key === 'train') {
      const detail = ((report.steps || []).find((step) => step.step === 'train') || {}).detail || '';
      if (/^Reused/.test(detail)) return 'Nothing it learns from changed, so the last model was kept.';
      return report.pairwise != null
        ? `Held out accuracy ${pct(report.pairwise)} on ${plural(report.pairs || 0, 'page pair')} across ${plural(report.queries_scored || 0, 'search', 'searches')}.`
        : 'Trained.';
    }
    return S.session && S.session.storage === 'cloud' ? 'Saved to private cloud storage.' : 'Saved.';
  }

  function lastPullText(setup) {
    const lp = setup.last_pull;
    if (!lp) return 'Nothing pulled yet.';
    const acc = lp.pairwise != null ? `, held out accuracy ${pct(lp.pairwise)}` : '';
    return `Last pull ${shortWhen((setup.pulls[0] || {}).when)} in ${Math.round(lp.seconds || 0)} s${acc}.`;
  }

  function shortWhen(stamp) {
    if (!stamp) return '';
    const d = new Date(stamp);
    if (Number.isNaN(d.getTime())) return stamp;
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  function markStep(root, key, state, message, seconds, done, total) {
    const li = (root || document).querySelector(`.pull-step[data-step="${key}"]`);
    if (!li) return;
    li.classList.remove('active', 'done', 'failed');
    if (state) li.classList.add(state);
    const ico = clear(li.querySelector('.ico'));
    if (state === 'active') ico.append(h('span', { class: 'spinner', 'aria-hidden': 'true' }));
    else ico.append(icon(state === 'failed' ? 'x' : 'check'));
    if (message) li.querySelector('.msg').textContent = message;
    const track = li.querySelector('.bar-track');
    if (state === 'active' && total) {
      track.hidden = false;
      track.firstChild.style.width = `${Math.min(100, Math.round((done / total) * 100))}%`;
    } else track.hidden = true;
    if (seconds != null && state === 'done') li.querySelector('.when').textContent = `${Number(seconds).toFixed(1)} s`;
  }

  function paintPull() {
    if (!pulling || S.screen !== 'data' || pulling.slug !== S.slug) return;
    const btn = document.getElementById('pull-btn');
    if (btn) {
      btn.disabled = true;
      clear(btn).append(h('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Pulling');
    }
    for (const [key, info] of Object.entries(pulling.steps)) markStep(document, key, info.state, info.message, info.seconds, info.done, info.total);
    const note = document.getElementById('pull-note');
    if (note) note.textContent = `Running for ${Math.round((Date.now() - pulling.started) / 1000)} s. You can leave this page; the pull keeps going.`;
  }

  async function startPull(auto) {
    if (pulling) return;
    const slug = S.slug;
    pulling = { slug, started: Date.now(), steps: {}, current: null };
    const advance = (step, info) => {
      if (pulling.current && pulling.current !== step && pulling.steps[pulling.current] && pulling.steps[pulling.current].state === 'active') {
        pulling.steps[pulling.current].state = 'done';
      }
      pulling.current = step;
      pulling.steps[step] = { ...(pulling.steps[step] || {}), ...info };
      paintPull();
    };
    for (const [key] of PULL_STEPS) pulling.steps[key] = { state: '', message: '' };
    paintPull();
    const timer = setInterval(paintPull, 1000);
    let failed = '';
    let report = null;
    try {
      const response = await fetch(`/api/v1/projects/${enc(slug)}/pull`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'content-type': 'application/json', 'x-ranksim-csrf': CSRF, accept: 'application/x-ndjson' },
        body: JSON.stringify({ google: true }),
      });
      if (!response.ok) {
        let data = null;
        try {
          data = await response.json();
        } catch (_err) {
          data = null;
        }
        throw new Error((data && data.error) || `Ranksim answered ${response.status}.`);
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
          if (!line) continue;
          const event = JSON.parse(line);
          if (event.type === 'start' && !event.google) {
            pulling.steps.google = { state: 'done', message: 'Using the positions you imported. Google was not asked.' };
          } else if (event.type === 'progress') {
            const step = event.step === 'save' ? 'store' : event.step;
            if (!PULL_STEPS.some(([key]) => key === step)) continue;
            advance(step, { state: 'active', message: event.message, done: event.done, total: event.total });
          } else if (event.type === 'done') {
            report = event.report;
          } else if (event.type === 'error') {
            failed = event.error;
          }
        }
      }
    } catch (err) {
      failed = err.message;
    }
    clearInterval(timer);
    if (report) {
      for (const step of report.steps || []) {
        const key = step.step === 'save' ? 'store' : step.step;
        if (pulling.steps[key]) pulling.steps[key] = { state: 'done', message: stepText(key, report), seconds: step.seconds };
      }
      pulling.steps.store = { state: 'done', message: stepText('store', report), seconds: (pulling.steps.store || {}).seconds };
    } else if (pulling.current) {
      pulling.steps[pulling.current].state = 'failed';
      pulling.steps[pulling.current].message = failed || 'Stopped.';
    }
    paintPull();
    const finishedSlug = pulling.slug;
    pulling = null;
    if (failed && !report) {
      toast(failed);
      if (S.slug === finishedSlug && S.screen === 'data') {
        const btn = document.getElementById('pull-btn');
        if (btn) {
          btn.disabled = false;
          clear(btn).append('Try again');
        }
        const note = document.getElementById('pull-note');
        if (note) note.textContent = failed;
      }
      return;
    }
    toast(`Pull finished in ${Math.round(report.seconds || 0)} s. ${report.pairwise != null ? `Held out accuracy ${pct(report.pairwise)}.` : ''}`);
    if (S.slug === finishedSlug) {
      await reloadProject();
      renderBar();
      if (S.screen === 'data') renderData();
    }
  }

  function queriesCard(setup) {
    const card = h('section', { class: 'card' });
    const paint = (editing) => {
      clear(card);
      const head = h('div', { class: 'card-head' },
        h('h2', null, 'Searches', h('span', { class: 'hint', text: String(setup.queries.length) })),
        h('div', { class: 'tools' }, editing ? null : h('button', { class: 'btn btn-plain', type: 'button', onClick: () => paint(true) }, 'Edit')));
      card.append(head);
      if (editing) {
        const area = h('textarea', { class: 'field', rows: String(Math.min(16, setup.queries.length + 3)), value: setup.queries.map((row) => row.q).join('\n') });
        const err = h('div', { class: 'form-error', hidden: true });
        const save = h('button', { class: 'btn btn-primary', type: 'button' }, 'Save searches');
        save.addEventListener('click', async () => {
          save.disabled = true;
          try {
            await api.post(`/api/v1/projects/${enc(S.slug)}/settings`, { queries: area.value });
            await reloadProject();
            toast('Searches saved. Pull again to get results for new ones.');
            renderData();
          } catch (e) {
            err.textContent = e.message;
            err.hidden = false;
            save.disabled = false;
          }
        });
        card.append(h('div', { class: 'card-body form' }, area, h('span', { class: 'form-help', text: 'One per line. New searches have no results until the next pull.' }), err,
          h('div', { class: 'form-actions' }, save, h('button', { class: 'btn btn-plain', type: 'button', onClick: () => paint(false) }, 'Cancel'))));
        area.focus();
        return;
      }
      const table = h('table', { class: 'qtable' },
        h('thead', null, h('tr', null, h('th', { text: 'Search' }), h('th', { class: 'num', text: 'Pages stored' }), h('th', { class: 'num hide-sm', text: 'You' }))),
        h('tbody', null, setup.queries.map((row) => {
          const share = row.candidates ? row.stored / row.candidates : 0;
          const meter = h('span', { class: 'meter-inline' }, h('i'));
          meter.firstChild.style.width = `${Math.round(share * 100)}%`;
          return h('tr', null,
            h('td', { class: 'q' }, row.q, row.error ? h('span', { class: 'sub', text: row.error }) : null),
            h('td', { class: 'num' }, row.candidates ? `${row.stored} of ${row.candidates}` : 'none', row.candidates ? meter : null),
            h('td', { class: 'num hide-sm', text: row.yours ? `#${row.yours}` : 'not listed' }));
        })));
      card.append(h('div', { class: 'card-body' }, table));
    };
    paint(false);
    return card;
  }

  function importCard(setup) {
    const file = h('input', { type: 'file', accept: '.csv,text/csv', hidden: true });
    const date = h('input', { class: 'field', type: 'date', 'aria-label': 'Date the positions were seen' });
    const label = h('span', { text: 'Choose a CSV' });
    const drop = h('label', { class: 'drop' }, icon('upload'), h('b', null, label), h('span', { text: 'query, url, position. Search Console exports work too.' }), file);
    const err = h('div', { class: 'form-error', hidden: true });
    const go = h('button', { class: 'btn', type: 'button', disabled: true }, 'Import');
    let text = '';
    const take = async (picked) => {
      if (!picked) return;
      if (picked.size > 5 * 1024 * 1024) {
        err.textContent = 'That CSV is over 5 MB.';
        err.hidden = false;
        return;
      }
      text = await picked.text();
      label.textContent = picked.name;
      go.disabled = false;
      err.hidden = true;
    };
    file.addEventListener('change', () => take(file.files[0]));
    drop.addEventListener('dragover', (event) => {
      event.preventDefault();
      drop.classList.add('over');
    });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', (event) => {
      event.preventDefault();
      drop.classList.remove('over');
      take(event.dataTransfer.files[0]);
    });
    go.addEventListener('click', async () => {
      go.disabled = true;
      err.hidden = true;
      try {
        const done = await api.post(`/api/v1/projects/${enc(S.slug)}/import`, { csv: text, date: date.value });
        toast(`Imported ${done.rows} positions. Now pull to fetch the pages and build the model.`);
        await reloadProject();
        renderData();
      } catch (e) {
        err.textContent = [e.message].concat(e.problems || []).join(' ');
        err.hidden = false;
        go.disabled = false;
      }
    });
    return h('section', { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', { text: 'Import positions' })),
      h('div', { class: 'card-body form' }, drop,
        h('div', { class: 'form-row' }, h('label', { text: 'Seen on (optional)' }), date),
        err, h('div', { class: 'form-actions' }, go)));
  }

  function competitorsCard(setup) {
    const area = h('textarea', { class: 'field', rows: '4', spellcheck: 'false', value: (setup.competitors || []).join('\n'), placeholder: 'competitor.com' });
    const save = h('button', { class: 'btn', type: 'button' }, 'Save');
    save.addEventListener('click', async () => {
      save.disabled = true;
      try {
        const done = await api.post(`/api/v1/projects/${enc(S.slug)}/settings`, { competitors: area.value });
        toast(done.problems && done.problems.length ? `Saved. Skipped: ${done.problems.join(' ')}` : 'Competitors saved.');
        S.compare.clear();
      } catch (e) {
        toast(e.message);
      }
      save.disabled = false;
    });
    return h('section', { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', { text: 'Competitors' })),
      h('div', { class: 'card-body form' }, area, h('div', { class: 'form-actions' }, save)));
  }

  function dangerCard(setup) {
    const field = h('input', { class: 'field', placeholder: setup.slug, autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Type the project name to confirm' });
    const del = h('button', { class: 'btn btn-danger', type: 'button', disabled: true }, 'Delete project');
    field.addEventListener('input', () => {
      del.disabled = field.value.trim() !== setup.slug;
    });
    del.addEventListener('click', async () => {
      del.disabled = true;
      try {
        await api.post(`/api/v1/projects/${enc(S.slug)}/delete`, { confirm: field.value.trim() });
        await loadProjects();
        toast('Project deleted.');
        navigate('/');
      } catch (e) {
        toast(e.message);
        del.disabled = false;
      }
    });
    return h('section', { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', { text: 'Delete' })),
      h('div', { class: 'card-body form' }, h('span', { class: 'form-help', text: `Type ${setup.slug} to delete this project and its tests. Stored pages stay for other projects.` }), field, h('div', { class: 'form-actions' }, del)));
  }

  boot();
})();
