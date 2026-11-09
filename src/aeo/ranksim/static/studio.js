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
    if (!response.ok) throw new Error((data && data.error) || `Ranksim answered ${response.status}. Try again.`);
    return data;
  }

  const api = {
    get: (path) => request('GET', path),
    post: (path, body) => request('POST', path, body),
  };

  /* ------------------------------------------------------------------ state */

  const S = {
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

  function parseLocation() {
    const parts = location.pathname.split('/').filter(Boolean);
    const params = new URLSearchParams(location.search);
    if (parts[0] === 'app' && parts[1]) {
      return { slug: decodeURIComponent(parts[1]), screen: parts[2] === 'rankings' ? 'rankings' : 'studio', q: params.get('q') || '' };
    }
    return { slug: null, screen: 'studio', q: params.get('q') || '' };
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

  window.addEventListener('popstate', async () => {
    const route = parseLocation();
    closeSheet(true);
    if (route.slug && route.slug !== S.slug) {
      await openProject(route.slug, route);
      return;
    }
    S.screen = route.screen;
    if (route.q && queryRow(route.q)) S.q = route.q;
    renderBar();
    render();
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
      const list = await api.get('/api/v1/projects');
      S.projects = list.projects || [];
      S.publicDemo = !!list.public_demo;
      S.demoSlug = list.demo_slug || S.demoSlug;
      const route = parseLocation();
      let slug = route.slug && S.projects.some((p) => p.slug === route.slug) ? route.slug : null;
      if (!slug) {
        const pick = S.projects.find((p) => p.slug === S.demoSlug && S.publicDemo) || S.projects.find((p) => p.built) || S.projects[0];
        slug = pick ? pick.slug : null;
      }
      if (!slug) {
        if (S.publicDemo) {
          await buildDemo(true);
          return;
        }
        renderBar();
        renderEmpty();
        return;
      }
      await openProject(slug, route.slug === slug ? route : { slug, screen: route.screen, q: route.q });
    } catch (err) {
      renderBar();
      renderProblem('Ranksim could not load', err.message);
    }
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
    S.screen = route.screen || 'studio';
    renderBar();
    render();
  }

  async function buildDemo(auto) {
    clear(main).append(
      h('div', { class: 'wrap screen' },
        h('section', { class: 'hero' },
          h('div', { class: 'boot', style: null }, h('span', { class: 'spinner', 'aria-hidden': 'true' })),
          h('p', { class: 'lede', text: 'Building the demo engine. This takes a few seconds.' }))));
    try {
      await api.post('/api/v1/demo', {});
      const list = await api.get('/api/v1/projects');
      S.projects = list.projects || [];
      const route = auto ? parseLocation() : { screen: 'studio', q: '' };
      await openProject(S.demoSlug, { slug: S.demoSlug, screen: route.screen, q: route.q });
      if (!auto) history.pushState({}, '', pathFor('studio', S.q));
    } catch (err) {
      renderProblem('The demo could not be built', err.message);
    }
  }

  /* ------------------------------------------------------------------ bar */

  function renderBar() {
    clear(bar);
    bar.append(
      h('a', { class: 'brand', href: '/', 'aria-label': 'Ranksim home' },
        h('span', { class: 'logo', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
        h('span', { class: 'brand-name', text: 'Ranksim' })),
    );
    if (S.project && S.project.engine && S.project.engine.ready) {
      const nav = h('nav', { class: 'seg nav-seg', 'aria-label': 'Screens' });
      for (const [key, label] of [['studio', 'Studio'], ['rankings', 'Rankings']]) {
        nav.append(link(key, S.q, label, { 'aria-current': S.screen === key ? 'page' : null }));
      }
      bar.append(nav);
    } else {
      bar.append(h('span'));
    }
    const right = h('div', { class: 'bar-right' });
    if (S.project) {
      if (S.project.data && S.project.data.kind === 'synthetic') right.append(h('span', { class: 'tag tag-demo', text: 'Demo data' }));
      if (S.projects.length > 1) {
        const select = h('select', { 'aria-label': 'Project' },
          S.projects.map((p) => h('option', { value: p.slug, selected: p.slug === S.slug, text: p.name })));
        select.addEventListener('change', async () => {
          const slug = select.value;
          history.pushState({}, '', `/app/${enc(slug)}/studio`);
          await openProject(slug, { slug, screen: 'studio', q: '' });
        });
        right.append(h('label', { class: 'select-pill' }, select, icon('chevron', 'chev')));
      } else {
        right.append(h('span', { class: 'tag', text: S.project.name }));
      }
    }
    right.append(h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'About Ranksim', title: 'About', onClick: openAbout }, icon('info')));
    bar.append(right);
  }

  /* ------------------------------------------------------------------ render */

  function render() {
    closeSheet(true);
    if (!S.project) return;
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
      left.append(h('span', { text: data.kind === 'synthetic' ? 'Demo data · Not Google results' : data.short }));
      left.append(h('span', { text: 'Draft tests are saved in this project' }));
    }
    const right = h('div', { class: 'fl' }, h('span', { text: 'Predicted positions are model estimates among the pages shown, never a Google rank.' }));
    if (!S.publicDemo) right.append(h('a', { href: S.slug ? `/p/${enc(S.slug)}` : '/projects', text: 'Classic screens' }));
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
            h('a', { class: 'btn btn-gray btn-large', href: '/projects' }, 'Create a project')))),
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
          h('h2', { text: "This project isn't built yet" }),
          h('p', { text: P.error || `The engine is ${P.engine.reason}. Fetch the pages and build it on the Setup screen, then come back.` }),
          h('a', { class: 'btn btn-primary btn-large', href: `/p/${enc(P.slug)}` }, 'Open setup'))),
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
      if (S.publicDemo) {
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

  boot();
})();
