// Ranksim end-to-end run. Drives a real Chromium against a local server with fakes (tests/e2e/server.py).
//   node tests/e2e/run.mjs --base http://127.0.0.1:8799 --out tests/e2e/screenshots
// Prints one JSON line {ok, steps: [...], failures: [...]} at the end and exits 1 on any failure.
// Selectors live in selectors.mjs. Screenshots are taken at deviceScaleFactor 2 into --out (git-ignored).
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { SEL } from './selectors.mjs';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, index, all) => {
  if (value.startsWith('--')) pairs.push([value.slice(2), all[index + 1]]);
  return pairs;
}, []));
const BASE = (args.base || 'http://127.0.0.1:8799').replace(/\/$/, '');
// Optional second server whose job budget is so short that analyses pause and the app has to post again.
const PAUSED_BASE = args['paused-base'] ? args['paused-base'].replace(/\/$/, '') : '';
const OUT = args.out || 'tests/e2e/screenshots';
const PASSWORD = process.env.E2E_PASSWORD || 'e2e-pass';
const SITE = process.env.E2E_SITE || 'www.dirtcrew.example';
const SEARCHES = JSON.parse(process.env.E2E_SEARCHES || '["dirt work software","excavation crew app","grading job costing","paving crew scheduling"]');
const SLOW = Number(process.env.E2E_TIMEOUT || 120000);
const DASHES = /[–—]/;

mkdirSync(OUT, { recursive: true });

const steps = [];
const failures = [];
const consoleErrors = [];
let phase = 'start';
const allowed = []; // [{phase, pattern}] console errors expected in one phase only

async function step(name, fn) {
  phase = name;
  const started = Date.now();
  try {
    await fn();
    steps.push({ name, ok: true, ms: Date.now() - started });
  } catch (err) {
    steps.push({ name, ok: false, ms: Date.now() - started });
    failures.push({ step: name, error: String(err && err.stack ? err.stack.split('\n').slice(0, 3).join(' | ') : err) });
  }
}

function expect(cond, message) {
  if (!cond) throw new Error(message);
}

function watch(page, label) {
  page.on('console', (msg) => {
    const text = msg.text();
    if (msg.type() === 'warning' && /replaces the built-in/.test(text)) {
      consoleErrors.push({ page: label, phase, text: `plugin key collision: ${text}` });
      return;
    }
    if (msg.type() !== 'error') return;
    if (allowed.some((a) => a.phase === phase && a.pattern.test(text))) return;
    consoleErrors.push({ page: label, phase, text });
  });
  page.on('pageerror', (err) => consoleErrors.push({ page: label, phase, text: `pageerror: ${err.message}` }));
}

/* Checks every screen gets: no horizontal scroll, no em or en dashes in rendered copy, a screenshot. */
async function audit(page, name) {
  await page.waitForTimeout(250);
  const facts = await page.evaluate(() => {
    const doc = document.documentElement;
    const texts = [document.title, document.body.innerText];
    for (const node of document.querySelectorAll('[aria-label],[title],[placeholder],svg title,svg desc,svg text')) {
      texts.push(node.getAttribute('aria-label') || '', node.getAttribute('title') || '', node.getAttribute('placeholder') || '', node.tagName.toLowerCase() !== 'svg' && node.namespaceURI === 'http://www.w3.org/2000/svg' ? node.textContent : '');
    }
    const wide = [];
    for (const node of document.querySelectorAll('body *')) {
      const box = node.getBoundingClientRect();
      if (box.width && box.right > window.innerWidth + 1 && getComputedStyle(node).position !== 'fixed') {
        let clipped = false;
        for (let up = node.parentElement; up && up !== document.body; up = up.parentElement) {
          const style = getComputedStyle(up);
          if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) { clipped = true; break; }
        }
        if (!clipped) wide.push(`${node.tagName.toLowerCase()}.${String(node.className && node.className.baseVal != null ? node.className.baseVal : node.className).split(' ')[0]}`);
      }
    }
    return { scroll: doc.scrollWidth, inner: window.innerWidth, text: texts.join('\n'), wide: wide.slice(0, 5) };
  });
  expect(facts.scroll <= facts.inner + 1, `${name}: horizontal scroll (${facts.scroll} > ${facts.inner}) from ${facts.wide.join(', ')}`);
  const dash = facts.text.match(new RegExp(`.{0,30}${DASHES.source}.{0,30}`));
  expect(!dash, `${name}: em or en dash in copy: "${dash && dash[0]}"`);
  await page.screenshot({ path: join(OUT, `${name}.png`), fullPage: true });
}

/* The Test changes tiles from the predict session: Every search (PREDICTED) and What would move you up (Guidance). */
async function checkPredictTiles(page) {
  const all = page.locator(SEL.predictAll);
  const move = page.locator(SEL.predictMove);
  await all.waitFor({ timeout: SLOW });
  await move.waitFor({ timeout: SLOW });
  expect(/PREDICTED/.test(await all.innerText()), 'Every search is labeled PREDICTED');
  expect(/Guidance/.test(await move.innerText()), 'What would move you up is labeled Guidance');
  const width = page.viewportSize().width;
  for (const tile of [all, move]) {
    const box = await tile.boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width + 1, `tile fits the ${width}px screen: ${JSON.stringify(box)}`);
  }
}

async function goto(page, path) {
  await page.goto(BASE + path, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction((sel) => !document.querySelector(sel), SEL.loading, { timeout: SLOW });
}

const browser = await chromium.launch();
let slug = null;
let state = null;

try {
  const desk = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
  const page = await desk.newPage();
  page.setDefaultTimeout(15000);
  watch(page, 'desktop');

  await step('login', async () => {
    await goto(page, '/');
    await page.waitForSelector(SEL.password);
    await audit(page, 'desktop-01-login');
    allowed.push({ phase: 'login', pattern: /status of 401/ });
    await page.fill(SEL.password, 'wrong password');
    await page.click(SEL.loginSubmit);
    await page.waitForSelector(`${SEL.loginError}:not([hidden])`);
    await page.fill(SEL.password, PASSWORD);
    await page.click(SEL.loginSubmit);
    await page.waitForSelector(SEL.homeInput);
  });

  await step('home', async () => {
    expect(await page.isVisible(SEL.homeInput), 'address box is visible');
    await page.waitForFunction((sel) => document.activeElement === document.querySelector(sel), SEL.homeInput, { timeout: 3000 })
      .catch(() => { throw new Error('address box has focus'); });
    await audit(page, 'desktop-02-home');
  });

  await step('keyboard', async () => {
    await page.evaluate(() => {
      document.body.setAttribute('tabindex', '-1');
      document.body.focus();
      document.body.removeAttribute('tabindex');
    });
    await page.keyboard.press('Tab');
    const first = await page.evaluate(() => {
      const el = document.activeElement;
      const style = getComputedStyle(el);
      return { tag: el.tagName, cls: el.className, outline: style.outlineStyle, shadow: style.boxShadow, text: el.textContent };
    });
    expect(first.tag === 'A' && /skip/.test(first.cls), `first Tab reaches the skip link, got ${first.tag}.${first.cls}`);
    expect(first.outline !== 'none' || first.shadow !== 'none', 'focused element shows a focus ring');
  });

  await step('home-validation', async () => {
    expect(await page.isVisible(SEL.homeDemo), 'a demo entry shows when there is no demo yet');
    await page.fill(SEL.homeInput, 'not an address');
    await page.click(SEL.homeSubmit);
    await page.waitForSelector(SEL.homeError);
    expect(new URL(page.url()).pathname === '/', 'a bad address stays on home');
  });

  await step('open-url', async () => {
    await page.fill(SEL.homeInput, SITE);
    await page.click(SEL.homeSubmit);
    await page.waitForSelector(SEL.progress, { timeout: SLOW });
    await page.waitForSelector(SEL.progressStep);
    await audit(page, 'desktop-03-analyzing');
    await page.waitForURL(/\/app\//, { timeout: SLOW });
    slug = decodeURIComponent(new URL(page.url()).pathname.split('/')[2]);
    expect(slug, 'a project slug after analysis');
    await page.waitForSelector(SEL.editorFrame, { timeout: SLOW });
    await audit(page, 'desktop-04-opened');
  });

  await step('settings-and-refresh', async () => {
    expect(slug, 'needs a project');
    await goto(page, `/app/${encodeURIComponent(slug)}/settings`);
    await page.waitForSelector(SEL.settingsSearches);
    await audit(page, 'desktop-05-settings');
    await page.fill(SEL.settingsSearches, SEARCHES.join('\n'));
    await page.click(SEL.saveSearches);
    await page.waitForFunction((sel) => /Saved/.test(document.querySelector(sel).textContent), SEL.toast);
    await page.click(SEL.refreshNow);
    await page.waitForFunction((sel) => /Refreshed/.test(document.querySelector(sel).textContent), SEL.toast, { timeout: SLOW });
  });

  await step('overview', async () => {
    await goto(page, `/app/${encodeURIComponent(slug)}`);
    await page.waitForSelector(SEL.tile);
    expect((await page.locator(SEL.tile).count()) >= 4, 'overview shows its tiles');
    const body = await page.innerText('main');
    expect(/#4/.test(body), 'overview shows the #4 ranking from the fake Google');
    const charts = await page.$$eval(SEL.chart, (list) => list.map((svg) => ({
      role: svg.getAttribute('role'), title: (svg.querySelector('title') || {}).textContent, desc: (svg.querySelector('desc') || {}).textContent, w: svg.getBoundingClientRect().width,
    })));
    expect(charts.length >= 3, `overview draws its charts, got ${charts.length}`);
    for (const c of charts) expect(c.role === 'img' && c.title && c.desc && c.w > 100, `chart is labeled and sized: ${JSON.stringify(c)}`);
    const titles = charts.map((c) => c.title).join('|');
    expect(/Where you rank/.test(titles) && /Who takes the top 10/.test(titles) && /Position distribution/.test(titles), `chart titles: ${titles}`);
    await audit(page, 'desktop-06-overview');
    await page.locator(SEL.opportunity).filter({ hasText: SEARCHES[0] }).click();
    await page.waitForSelector(SEL.sheet);
    await page.waitForSelector(`${SEL.sheet} ${SEL.serpYou}`);
    expect((await page.locator(`${SEL.sheet} ${SEL.serpEdit}`).count()) >= 5, 'every result opens in the editor');
    await audit(page, 'desktop-06b-serp-sheet');
    await page.keyboard.press('Escape');
    await page.waitForSelector(SEL.sheet, { state: 'detached' });
  });

  await step('gsc-not-set-up', async () => {
    const tile = page.locator(SEL.gscTile);
    await tile.waitFor();
    expect(/not set up yet/i.test(await tile.innerText()), 'the Overview tile says Search Console is not set up');
    await goto(page, `/app/${encodeURIComponent(slug)}/settings`);
    const section = page.locator(SEL.gscSection);
    await section.waitFor();
    await section.filter({ hasText: 'Not set up yet' }).waitFor();
    expect((await page.locator(SEL.gscSection).count()) === 1, 'one Search Console section: the plugin replaced the placeholder');
    await audit(page, 'desktop-06c-settings-gsc');
  });

  await step('client-share-link', async () => {
    await page.waitForSelector(SEL.shareSection);
    await page.fill(SEL.shareLabel, 'E2E client');
    await page.click(SEL.shareCreate);
    await page.waitForSelector(SEL.shareUrl);
    const url = (await page.innerText(SEL.shareUrl)).trim();
    expect(/\/r\//.test(url), `a share URL: ${url}`);
    const sharePath = new URL(url, BASE).pathname;
    const guest = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    const reader = await guest.newPage();
    watch(reader, 'share-reader');
    await reader.goto(BASE + sharePath, { waitUntil: 'networkidle' });
    const text = await reader.innerText('body');
    expect(/DirtCrew|dirtcrew/i.test(text), 'the signed-out phone sees the client report');
    expect(!(await reader.$(SEL.password)), 'no login on a share link');
    await audit(reader, 'phone-share-report');
    page.once('dialog', (dialog) => dialog.accept());
    await page.click(SEL.shareOff);
    await page.waitForFunction(() => /Turned off/.test(document.querySelector('.share-row').innerText));
    allowed.push({ phase: 'client-share-link', pattern: /status of 410|status of 404/ });
    const gone = await reader.goto(BASE + sharePath, { waitUntil: 'networkidle' });
    const after = await reader.innerText('body');
    expect(gone.status() >= 400 || /expired or was turned off/.test(after), `a turned off link stops working (status ${gone.status()})`);
    expect(!/dirt work software/i.test(after), 'no report data after turning the link off');
    await guest.close();
    await goto(page, `/app/${encodeURIComponent(slug)}`);
  });

  await step('skeleton', async () => {
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    await page.route('**/api/v1/projects/*/report', async (route) => { await held; await route.continue(); });
    await page.goto(`${BASE}/app/${encodeURIComponent(slug)}/issues`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector(SEL.skeleton('issues'));
    await audit(page, 'desktop-06d-skeleton-issues');
    release();
    await page.waitForSelector(SEL.issueRow);
    await page.unroute('**/api/v1/projects/*/report');
  });

  await step('issues', async () => {
    await page.click(SEL.navLink('Issues'));
    await page.waitForURL(/\/issues/);
    await page.waitForSelector(SEL.issueRow);
    await audit(page, 'desktop-07-issues');
    await page.locator(SEL.issueRow).first().click();
    await page.waitForSelector(SEL.sheet);
    await audit(page, 'desktop-08-issue-sheet');
    await page.keyboard.press('Escape');
    await page.waitForSelector(SEL.sheet, { state: 'detached' });
  });

  await step('searches', async () => {
    await page.click(SEL.navLink('Searches'));
    await page.waitForURL(/\/searches/);
    await page.waitForSelector(SEL.searchRow);
    expect((await page.locator(SEL.searchRow).count()) === SEARCHES.length, 'one row per search');
    await page.locator(SEL.searchRow).filter({ hasText: SEARCHES[0] }).click();
    await page.waitForSelector(SEL.serpRow);
    expect((await page.locator(SEL.serpRow).count()) >= 5, 'the top 10 is listed');
    expect((await page.locator(SEL.serpYou).count()) === 1, 'your page is highlighted in the top 10');
    await audit(page, 'desktop-09-searches');
  });

  await step('competitors', async () => {
    await page.click(SEL.navLink('Competitors'));
    await page.waitForURL(/\/competitors/);
    await page.waitForSelector(SEL.competitorRow);
    expect(/alpha\.example/.test(await page.innerText('main')), 'alpha.example is listed');
    await audit(page, 'desktop-10-competitors');
  });

  await step('editor-test', async () => {
    const pageUrl = `https://${SITE}/dirt-work-software/`;
    await goto(page, `/app/${encodeURIComponent(slug)}/editor?page=${encodeURIComponent(pageUrl)}&q=${encodeURIComponent(SEARCHES[0])}`);
    await page.waitForSelector(SEL.editorFrame);
    const frame = page.frameLocator(SEL.editorFrame);
    const heading = frame.locator('h1').first();
    await heading.waitFor();
    await heading.click();
    await page.keyboard.press('End');
    await page.keyboard.type(' for dirt work crews');
    await page.waitForTimeout(400);
    await page.waitForSelector(`${SEL.testButton}:not([disabled])`, { timeout: SLOW });
    await page.click(SEL.testButton);
    await page.waitForSelector(SEL.verdict, { timeout: SLOW });
    const body = await page.innerText('main');
    expect(/predict/i.test(body), 'the result says it is a prediction');
    await checkPredictTiles(page);
    await audit(page, 'desktop-11-editor-result');
  });

  await step('offline', async () => {
    await desk.setOffline(true);
    await page.evaluate(() => window.dispatchEvent(new Event('offline')));
    await page.waitForSelector(SEL.offline);
    await audit(page, 'desktop-12-offline');
    await desk.setOffline(false);
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await page.waitForSelector(SEL.offline, { state: 'detached' });
  });

  await step('demo', async () => {
    await goto(page, '/');
    await page.click(SEL.homeDemo);
    await page.waitForURL(/\/app\//, { timeout: SLOW });
    await page.waitForSelector(SEL.chart, { timeout: SLOW });
    await audit(page, 'desktop-13-demo-overview');
    await goto(page, '/');
    expect((await page.locator(SEL.siteCard).count()) >= 2, 'recent sites lists both sites');
    expect(!(await page.isVisible(SEL.homeDemo)), 'the demo entry is a site card once it exists');
  });

  await step('tablet', async () => {
    await page.setViewportSize({ width: 820, height: 1180 });
    await goto(page, `/app/${encodeURIComponent(slug)}`);
    await page.waitForSelector(SEL.chart);
    await audit(page, 'tablet-overview');
    await page.setViewportSize({ width: 1280, height: 900 });
  });

  await step('parallel-analysis', async () => {
    const one = await desk.newPage();
    const two = await desk.newPage();
    watch(one, 'site-a');
    watch(two, 'site-b');
    const refused = [];
    for (const p of [one, two]) p.on('response', (r) => { if (r.url().includes('/api/v1/sites') && r.status() === 409) refused.push(r.url()); });
    await Promise.all([
      one.goto(`${BASE}/edit?url=${encodeURIComponent('https://www.slow-a.example/')}`),
      two.goto(`${BASE}/edit?url=${encodeURIComponent('https://www.slow-b.example/')}`),
    ]);
    const timeline = (p) => p.waitForSelector('.progress-tile .step.active', { timeout: SLOW }).then(() => Date.now())
      .then(async (start) => { await p.waitForURL(/\/app\//, { timeout: SLOW }); return { start, end: Date.now() }; });
    const [a, b] = await Promise.all([timeline(one), timeline(two)]);
    expect(Math.max(a.start, b.start) < Math.min(a.end, b.end), `the two analyses overlap in time: ${JSON.stringify({ a, b })}`);
    expect(!refused.length, `no 409 while two sites are analyzed: ${refused.join(', ')}`);
    await one.close();
    await two.close();
  });

  await step('reattach-after-refresh', async () => {
    const tab = await desk.newPage();
    watch(tab, 'reattach');
    await tab.goto(`${BASE}/edit?url=${encodeURIComponent('https://www.slow-c.example/')}`);
    await tab.waitForSelector('.progress-tile .step.done', { timeout: SLOW });
    expect(!/\/app\//.test(tab.url()), 'still analyzing when the page reloads');
    await tab.reload();
    await tab.waitForSelector(SEL.progress, { timeout: SLOW });
    await tab.waitForURL(/\/app\//, { timeout: SLOW });
    await tab.waitForSelector(SEL.editorFrame, { timeout: SLOW });
    const projects = await tab.evaluate(async () => (await (await fetch('/api/v1/projects')).json()).projects.map((p) => p.site));
    const copies = projects.filter((site) => /slow-c/.test(site));
    expect(copies.length === 1, `one project for the reattached site, got ${JSON.stringify(projects)}`);
    const jobs = await tab.evaluate(async () => (await fetch('/api/v1/jobs')).json());
    const live = (jobs.jobs || []).filter((job) => /slow-c/.test(JSON.stringify(job))); // the list holds running or paused jobs only
    expect(!live.length, `no job left running: ${JSON.stringify(live)}`);
    await tab.close();
  });

  await step('paused-resume', async () => {
    if (!PAUSED_BASE) throw new Error('no --paused-base server given');
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
    const tab = await ctx.newPage();
    watch(tab, 'paused');
    await tab.goto(PAUSED_BASE + '/');
    await tab.fill(SEL.password, PASSWORD);
    await tab.click(SEL.loginSubmit);
    await tab.waitForSelector(SEL.homeInput);
    let posts = 0;
    let paused = 0;
    // Buffer each analysis answer through a route so its NDJSON lines can be read (this test does not need streaming).
    await tab.route('**/api/v1/sites', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      posts += 1;
      const response = await route.fetch({ timeout: SLOW });
      const body = await response.text();
      if (/"type":\s*"paused"/.test(body)) paused += 1;
      await route.fulfill({ response, body });
    });
    await tab.fill(SEL.homeInput, 'www.slow-p.example');
    await tab.click(SEL.homeSubmit);
    await tab.waitForURL(/\/app\//, { timeout: SLOW * 2 });
    await tab.waitForSelector(SEL.editorFrame, { timeout: SLOW });
    expect(posts >= 2, `the app posted again after a pause (${posts} posts, ${paused} paused answers)`);
    expect(paused >= 1, `the server paused at least once (${paused})`);
    await ctx.close();
  });

  state = await desk.storageState();

  await step('signed-out', async () => {
    const other = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
    const fresh = await other.newPage();
    watch(fresh, 'signed-out');
    await goto(fresh, `/app/${encodeURIComponent(slug || 'x')}`);
    await fresh.waitForSelector(SEL.password);
    await other.close();
  });

  await desk.close();

  const phone = await browser.newContext({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, reducedMotion: 'reduce', storageState: state,
  });
  const mobile = await phone.newPage();
  mobile.setDefaultTimeout(15000);
  watch(mobile, 'phone');

  const screens = [['home', '/'], ['overview', ''], ['issues', '/issues'], ['searches', '/searches'], ['competitors', '/competitors'], ['settings', '/settings'], ['editor', '/editor']];
  for (const [name, path] of screens) {
    await step(`phone-${name}`, async () => {
      await goto(mobile, name === 'home' ? '/' : `/app/${encodeURIComponent(slug)}${path}`);
      await mobile.waitForSelector(name === 'home' ? SEL.homeInput : 'main h1, main h2');
      await audit(mobile, `phone-${name}`);
      if (name !== 'home') {
        const nav = await mobile.$eval(SEL.nav, (el) => ({ sw: el.scrollWidth, cw: el.clientWidth }));
        expect(nav.sw <= nav.cw + 1, `phone nav fits without sideways scrolling (${nav.sw} > ${nav.cw})`);
      }
    });
  }

  await step('phone-test-changes', async () => {
    const pageUrl = `https://${SITE}/dirt-work-software/`;
    await goto(mobile, `/app/${encodeURIComponent(slug)}/editor?page=${encodeURIComponent(pageUrl)}&q=${encodeURIComponent(SEARCHES[0])}`);
    await mobile.waitForSelector(`${SEL.testButton}:not([disabled])`, { timeout: SLOW });
    await mobile.click(SEL.testButton);
    await mobile.waitForSelector(SEL.verdict, { timeout: SLOW });
    await checkPredictTiles(mobile);
    await audit(mobile, 'phone-editor-result');
  });

  await step('sign-out', async () => {
    await goto(mobile, '/');
    await mobile.click(SEL.signOut);
    await mobile.waitForSelector(SEL.password);
    await audit(mobile, 'phone-signed-out');
  });

  await phone.close();
} finally {
  await browser.close();
}

if (consoleErrors.length) failures.push({ step: 'console', error: JSON.stringify(consoleErrors.slice(0, 10)) });
const ok = failures.length === 0;
console.log(JSON.stringify({ ok, slug, steps, failures }));
process.exit(ok ? 0 : 1);
