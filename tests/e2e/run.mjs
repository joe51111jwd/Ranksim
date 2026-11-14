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
// --group main | jobs | all. pytest runs main and jobs at the same time against separate servers to keep the run short.
const GROUP = args.group || 'all';
// Three groups run at the same time against three servers: main (the core flow), more (plugin features on a project
// it seeds through the API), jobs (analysis jobs on slow fake sites).
const GROUPS = {
  jobs: new Set(['first-load-kept', 'parallel-analysis', 'reattach-after-refresh', 'paused-resume']),
  more: new Set(['seed', 'discover-searches', 'discover-competitors', 'audit-issues', 'audit-details', 'audit-fix-top', 'audit-apply',
    'track', 'publish', 'phone-issue-sheet', 'phone-test-changes', 'phone-publish']),
};
const groupOf = (name) => (GROUPS.jobs.has(name) ? 'jobs' : GROUPS.more.has(name) ? 'more' : 'main');
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
  if (name !== 'login' && (GROUP === 'all' ? name === 'seed' : groupOf(name) !== GROUP)) return;
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

/* A domain that is not a site yet asks before it analyzes. Say Analyze. */
async function analyze(p, timeout = SLOW * 3) {
  await p.locator(SEL.openAnalyze).click({ timeout });
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

  await step('seed', async () => {
    // The same site as the main flow, built through the API: analyze, set the searches, pull Google again.
    slug = await page.evaluate(async ([site, searches]) => {
      const csrf = document.getElementById('csrf').value;
      const post = (path, body) => fetch(path, { method: 'POST', headers: { 'content-type': 'application/json', 'x-ranksim-csrf': csrf }, body: JSON.stringify(body) });
      const lines = (await (await post('/api/v1/sites', { site })).text()).trim().split('\n').map((line) => JSON.parse(line));
      const done = lines.find((line) => line.type === 'done');
      if (!done) throw new Error(`analysis did not finish: ${JSON.stringify(lines.slice(-2))}`);
      await post(`/api/v1/projects/${encodeURIComponent(done.slug)}/settings`, { queries: searches.join('\n') });
      const pull = (await (await post(`/api/v1/projects/${encodeURIComponent(done.slug)}/pull`, { google: true })).text()).trim().split('\n').map((line) => JSON.parse(line));
      if (!pull.some((line) => line.type === 'done')) throw new Error(`pull did not finish: ${JSON.stringify(pull.slice(-2))}`);
      return done.slug;
    }, [SITE, SEARCHES]);
    expect(slug, 'a seeded project');
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
    await page.waitForSelector(SEL.openAsk, { timeout: SLOW });
    expect(/Analyze .* as a client site\?/.test(await page.innerText(SEL.openAsk)), 'a new domain asks before it analyzes');
    await audit(page, 'desktop-03a-ask');
    await analyze(page);
    await page.waitForSelector(SEL.progressStep);
    await audit(page, 'desktop-03-analyzing');
    await page.waitForURL(/\/app\//, { timeout: SLOW });
    slug = decodeURIComponent(new URL(page.url()).pathname.split('/')[2]);
    expect(slug, 'a project slug after analysis');
    await page.waitForSelector(SEL.editorFrame, { timeout: SLOW });
    await page.waitForFunction((sel) => /Report ready/.test(document.querySelector(sel).textContent), SEL.toast);
    expect(await page.isVisible(SEL.nav), 'the section nav appears once the report exists');
    await audit(page, 'desktop-04-opened');
  });

  await step('just-edit', async () => {
    // A competitor's page opens in the editor; Just edit never creates a site and never analyzes.
    const before = await page.evaluate(async () => (await (await fetch('/api/v1/projects')).json()).projects.length);
    const posts = [];
    const onRequest = (r) => { if (/\/api\/v1\/sites$/.test(r.url()) && r.method() === 'POST') posts.push(r.url()); };
    page.on('request', onRequest);
    await goto(page, '/');
    await page.fill(SEL.homeInput, 'alpha.example/dirt-work-software');
    await page.click(SEL.homeSubmit);
    await page.waitForSelector(SEL.openAsk, { timeout: SLOW });
    await page.frameLocator(SEL.editorFrame).locator('h1').first().waitFor({ timeout: SLOW });
    await page.click(SEL.openJustEdit);
    await page.waitForSelector(SEL.openEditing);
    await audit(page, 'desktop-04b-just-edit');
    await page.waitForTimeout(500);
    page.off('request', onRequest);
    const after = await page.evaluate(async () => (await (await fetch('/api/v1/projects')).json()).projects.length);
    expect(after === before, `Just edit adds no site (${before} before, ${after} after)`);
    expect(!posts.length, 'Just edit starts no analysis');
    expect(/\/edit/.test(page.url()), 'the competitor page stays in the editor');
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
    const backtest = await page.locator('.backtest-quiet').innerText();
    expect(/Not accuracy/.test(backtest) && !(await page.locator('.backtest-quiet .ring').count()), `the backtest is quiet text: ${backtest}`);
    expect(!(await page.locator('main .ring').count()), 'no backtest ring on the Overview');
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
    await page.keyboard.press('Escape');  // the search detail is a sheet (discover)
    await page.waitForSelector(SEL.sheet, { state: 'detached' });
  });

  await step('competitors', async () => {
    await page.click(SEL.navLink('Competitors'));
    await page.waitForURL(/\/competitors/);
    await page.waitForSelector(SEL.competitorRow);
    expect(/alpha\.example/.test(await page.innerText('main')), 'alpha.example is listed');
    await audit(page, 'desktop-10-competitors');
  });

  await step('discover-searches', async () => {
    await goto(page, `/app/${encodeURIComponent(slug)}/searches`);
    await page.waitForSelector(SEL.searchTable);
    const headers = (await page.$$eval(SEL.searchHeader, (list) => list.map((el) => el.textContent.trim()))).join('|');
    for (const want of ['Search', 'You', 'PREDICTED', 'Intent', 'Source', 'Best page']) expect(headers.includes(want), `Searches has a ${want} column: ${headers}`);
    // gsc's column has when(report) false until Search Console is connected, so it must not show.
    expect(!/Search Console|Clicks|Impr/i.test(headers), `the Search Console column is hidden while not connected: ${headers}`);
    await page.locator(SEL.searchRow).filter({ hasText: SEARCHES[0] }).click();
    const sheet = page.locator(SEL.searchSheet);
    await sheet.waitFor();
    await sheet.locator(':text("Top 10 on Google")').waitFor();
    expect((await sheet.locator(SEL.serpRow).count()) >= 5, 'the search sheet lists the real top 10');
    expect((await sheet.locator(SEL.serpYou).count()) === 1, 'your page is marked in the search sheet');
    const edit = await sheet.locator(SEL.serpEdit).first().getAttribute('href');
    expect(new URL(edit, BASE).searchParams.get('q') === SEARCHES[0], `the pencil opens the editor on this search: ${edit}`);
    const gaps = await sheet.locator(':text("What the pages above have")').count();
    expect(gaps >= 1, 'the search sheet shows the gaps against the pages above');
    await audit(page, 'desktop-09b-search-sheet');
    await page.keyboard.press('Escape');
    await page.waitForSelector(SEL.sheet, { state: 'detached' });
    const found = page.locator(SEL.foundNotTracked);
    if (await found.count()) {
      const before = await page.locator(SEL.searchRow).count();
      const add = found.locator('button:has-text("Add")').first();
      await add.click();
      await page.waitForFunction((sel) => /Added/.test(document.querySelector(sel).textContent), SEL.toast);
      await page.waitForFunction(([sel, n]) => document.querySelectorAll(sel).length > n, [SEL.searchRow, before]);
    }
  });

  await step('discover-competitors', async () => {
    await goto(page, `/app/${encodeURIComponent(slug)}/competitors`);
    await page.waitForSelector(SEL.competitorCard);
    const card = page.locator(SEL.competitorCard).first();
    const button = card.locator('button.track');
    const before = (await button.innerText()).trim();
    await button.click();
    await page.waitForFunction(([sel, was]) => { const b = document.querySelector(sel); return b && !b.disabled && b.textContent.trim() !== was; }, [SEL.trackToggle, before], { timeout: 20000 });
    await page.locator(SEL.trackToggle).first().click();
    await page.waitForFunction(([sel, was]) => { const b = document.querySelector(sel); return b && !b.disabled && b.textContent.trim() === was; }, [SEL.trackToggle, before], { timeout: 20000 });
    await audit(page, 'desktop-10b-competitors');
    await page.locator(SEL.competitorCard).first().locator('button.body').click();
    await page.waitForSelector(SEL.sheet);
    await page.keyboard.press('Escape');
    await page.waitForSelector(SEL.sheet, { state: 'detached' });
  });

  await step('audit-issues', async () => {
    await goto(page, `/app/${encodeURIComponent(slug)}/issues`);
    await page.waitForSelector(SEL.issueRow);
    const all = await page.locator(SEL.issueRow).count();
    await page.locator(SEL.issueFilter).filter({ hasText: 'High' }).click();
    await page.waitForSelector(`${SEL.issueFilter}[aria-pressed="true"]:has-text("High")`);
    expect((await page.locator(SEL.issueRow).count()) <= all, 'the High filter narrows the list');
    await page.locator(SEL.issueFilter).filter({ hasText: 'All' }).click();
    await page.locator(SEL.issueGroup).filter({ hasText: 'By search' }).click();
    await page.waitForSelector(`${SEL.issueGroup}[aria-pressed="true"]:has-text("By search")`);
    await audit(page, 'desktop-07b-issues-by-search');
    await page.locator(SEL.issueGroup).filter({ hasText: 'By page' }).click();
    const withFix = page.locator(SEL.issueRow).filter({ has: page.locator('.issue-has-fix') }).first();
    await withFix.click();
    await page.waitForSelector(SEL.sheet);
    await page.locator('.sheet button:has-text("Copy")').first().click();
    await page.waitForFunction((sel) => /Copied|Copy did not work/.test(document.querySelector(sel).textContent), SEL.toast);
    await page.keyboard.press('Escape');
    await page.waitForSelector(SEL.sheet, { state: 'detached' });
    // Fix in editor focuses elements in the page body (head tags such as <title> have nothing to point at).
    await page.locator(SEL.issueRow).filter({ has: page.locator('.issue-tag', { hasText: '<h1>' }) }).first().click();
    await page.waitForSelector(SEL.sheet);
    await page.click(SEL.fixInEditor);
    await page.waitForURL(/\/editor/);
    await page.waitForSelector(SEL.commentsPanel, { timeout: SLOW });
    await page.waitForSelector(`${SEL.commentsPanel} [aria-expanded="true"]`, { timeout: SLOW });
    const frame = page.frameLocator(SEL.editorFrame);
    await frame.locator('[data-rs-annotations="active"]').first().waitFor({ timeout: SLOW });
    await audit(page, 'desktop-11b-fix-in-editor');
  });

  await step('audit-details', async () => {
    const issues = `/app/${encodeURIComponent(slug)}/issues`;
    await goto(page, issues);
    await page.waitForSelector('.issue-controls');
    await page.locator('.issue-controls .seg button').filter({ hasText: 'High' }).click();
    const dots = await page.$$eval('.rowcard .dot', (list) => list.map((d) => d.className));
    expect(dots.length && dots.every((c) => /high/.test(c)), `High shows only high issues: ${dots.join(',')}`);
    await page.locator('.issue-controls .seg button').filter({ hasText: 'All' }).click();
    await page.locator('.issue-controls button').filter({ hasText: 'By search' }).click();
    const heads = await page.$$eval('.bento section.tile h2', (list) => list.map((el) => el.textContent.trim()));
    expect(heads.length && heads.every((t) => /^["\u201c]/.test(t) || /Not tied to a search/.test(t)), `By search headings: ${heads.join(' | ')}`);
    await page.locator('.issue-controls button').filter({ hasText: 'By page' }).click();
    // 2. the sheet for an issue with an element
    const h1Row = () => page.locator(SEL.issueRow).filter({ has: page.locator('.issue-tag', { hasText: '<h1>' }) }).first();
    await h1Row().click();
    await page.waitForSelector('.sheet.open .issue-element code.issue-selector');
    const sheetText = await page.innerText('.sheet.open');
    expect(/What to do/.test(sheetText), 'the issue sheet says what to do');
    const title = (await page.innerText('.sheet.open .sheet-head h2')).trim();
    // 3 and 4. Fix in editor, open another comment, come back and Fix in editor again
    for (let round = 0; round < 2; round += 1) {
      await page.click(SEL.fixInEditor);
      await page.waitForURL(/focus=/);
      await page.waitForSelector('.annot-item.open', { timeout: SLOW });
      const frame = page.frameLocator(SEL.editorFrame);
      await frame.locator('[data-rs-pick]').first().waitFor({ state: 'attached', timeout: SLOW });
      expect((await frame.locator('[data-rs-annotations="active"]').count()) === 1, 'exactly one active element');
      expect((await frame.locator('[data-rs-pin]').count()) >= 1, 'pins are drawn');
      expect((await page.innerText('.annot-item.open .annot-title')).trim() === title, 'the open comment is the issue');
      if (round === 0) {
        await page.locator('.annot-item:not(.open) .annot-head').first().click();
        await page.click(SEL.navLink('Issues'));
        await page.waitForSelector(SEL.issueRow);
        await h1Row().click();
        await page.waitForSelector(SEL.fixInEditor);
      }
    }
    // 5. mode toggle closes comments and draws pins again
    await page.locator('.annot-item.open .annot-head').click();
    await page.locator('button:has-text("HTML")').first().click();
    // 7. nothing leaks into the code view
    const code = await page.locator('textarea.code').first().inputValue();
    expect(!/data-rs/.test(code), 'the HTML view has no data-rs');
    await page.locator('button:has-text("Visual")').first().click();
    await page.frameLocator(SEL.editorFrame).locator('[data-rs-pin]').first().waitFor({ timeout: SLOW });
    expect((await page.locator('.annot-item.open').count()) === 0, 'no comment is open after a mode change');
    await audit(page, 'desktop-11d-audit-editor');
  });

  await step('audit-fix-top', async () => {
    await goto(page, `/app/${encodeURIComponent(slug)}/issues`);
    const button = page.locator('button.fix-top').first();
    await button.waitFor();
    const n = Number(((await button.innerText()).match(/\d+/) || ['0'])[0]);
    await button.click();
    await page.waitForSelector('.fix-list');
    const rows = await page.locator('.fix-row').count();
    expect(n && rows === n, `the review lists ${n} changes, got ${rows}`);
    if (rows > 1) {
      await page.locator('.fix-row input[type=checkbox]').last().uncheck();
      const label = (await page.innerText('.fix-actions button')).trim();
      expect(new RegExp(`${rows - 1} change`).test(label), `the apply button counts the checked rows: ${label}`);
    }
    await audit(page, 'desktop-07c-fix-top-review');
    const tests = [];
    const onRequest = (r) => { if (/\/api\/v1\/projects\/[^/]+\/test$/.test(r.url()) && r.method() === 'POST') tests.push(r.postData() || ''); };
    page.on('request', onRequest);
    await page.locator('.fix-actions button').click();
    await page.waitForSelector('.sheet', { state: 'detached', timeout: SLOW });
    await page.waitForSelector('.result, .verdict', { timeout: SLOW });
    page.off('request', onRequest);
    const done = await page.locator('.annot-done').count();
    expect(done === Math.max(1, rows - 1), `the applied comments are marked Applied (${done} of ${Math.max(1, rows - 1)}; ${await page.locator('.annot-item').count()} comments)`);
    expect(tests.length && tests.every((body) => !/data-rs/.test(body)), 'the Test changes draft has no data-rs');
    await audit(page, 'desktop-11e-fix-top-result');
  });

  await step('audit-apply', async () => {
    const pageUrl = `https://${SITE}/`; // a page Fix the top issues did not touch
    await goto(page, `/app/${encodeURIComponent(slug)}/editor?page=${encodeURIComponent(pageUrl)}&q=${encodeURIComponent(SEARCHES[0])}`);
    await page.waitForSelector(SEL.commentsPanel, { timeout: SLOW });
    const frame = page.frameLocator(SEL.editorFrame);
    await frame.locator('h1').first().waitFor();
    const heads = page.locator(`${SEL.commentsPanel} .annot-head`);
    let applied = null;
    for (let i = 0; i < await heads.count() && !applied; i += 1) {
      const head = heads.nth(i);
      if ((await head.getAttribute('aria-expanded')) !== 'true') await head.click();
      const item = page.locator(`${SEL.commentsPanel} li`).nth(i);
      const tag = await item.locator('.annot-tag').first().textContent().catch(() => '');
      const apply = item.locator('button:has-text("Apply suggestion")');
      if (await apply.count() && /h1/i.test(tag || '')) applied = { apply, tag };
    }
    if (!applied) {
      const apply = page.locator(SEL.applySuggestion).first();
      if (await apply.count()) applied = { apply, tag: 'other' };
    }
    expect(applied, 'a comment offers Apply suggestion');
    const before = await page.evaluate(async (url) => (await import('/studio/js/editor/editor.js')).draftFor(url).text, pageUrl);
    const h1Before = await frame.locator('h1').first().innerText();
    await applied.apply.click();
    await page.waitForFunction((url) => import('/studio/js/editor/editor.js').then((m) => m.draftFor(url).touched), pageUrl);
    const after = await page.evaluate(async (url) => (await import('/studio/js/editor/editor.js')).draftFor(url).text, pageUrl);
    expect(after !== before, 'Apply suggestion changed the draft');
    if (applied.tag !== 'other') expect((await frame.locator('h1').first().innerText()) !== h1Before, 'the H1 changed');
    expect(!/data-rs/.test(after), 'the saved HTML has no data-rs marks');
    await audit(page, 'desktop-11c-applied');
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
    const answers = [];
    const onAnswer = (r) => { if (/\/api\/v1\/projects\/[^/]+\/(test|predict)/.test(r.url()) && r.request().method() === 'POST') answers.push(`${r.status()} ${r.url().replace(BASE, '')}`); };
    page.on('response', onAnswer);
    await page.click(SEL.testButton);
    await page.waitForSelector(SEL.verdict, { timeout: SLOW });
    await page.waitForTimeout(500);
    page.off('response', onAnswer);
    expect(answers.length && answers.every((line) => line.startsWith('200')), `Test changes answers 200: ${answers.join(', ')}`);
    const body = await page.innerText('main');
    expect(/predict/i.test(body), 'the result says it is a prediction');
    await checkPredictTiles(page);
    await audit(page, 'desktop-11-editor-result');
  });

  await step('track', async () => {
    await goto(page, `/app/${encodeURIComponent(slug)}`);
    const tile = page.locator(SEL.rankChanges);
    await tile.waitFor();
    const text = await tile.innerText();
    expect(/Up|Down|New|Lost|Changes show|Positions show/.test(text), `Rank changes tile has content: ${text.slice(0, 120)}`);
    await goto(page, `/app/${encodeURIComponent(slug)}/searches`);
    await page.waitForSelector(SEL.searchRow);
    expect(/Since last visit|Trend/.test(await page.innerText('main')), 'the Searches list has the rank history column');
    expect((await page.locator('.history-cell').count()) >= 1, 'a sparkline cell per checked search');
    await goto(page, `/app/${encodeURIComponent(slug)}/track-history`);
    await page.waitForSelector(SEL.historyRow);
    await audit(page, 'desktop-14-history');
    await page.locator(SEL.historyRow).first().click();
    await page.waitForSelector(SEL.sheet);
    await audit(page, 'desktop-15-history-sheet');
    await page.keyboard.press('Escape');
    await page.waitForSelector(SEL.sheet, { state: 'detached' });
    await goto(page, `/app/${encodeURIComponent(slug)}/settings`);
    await page.waitForSelector(SEL.schedule);
    const off = page.locator(`${SEL.schedule}[aria-pressed="false"]`).first();
    const label = (await off.innerText()).trim();
    await off.click();
    await page.waitForFunction(([sel, want]) => [...document.querySelectorAll(sel)].some((b) => b.getAttribute('aria-pressed') === 'true' && b.textContent.trim() === want), [SEL.schedule, label]);
    await audit(page, 'desktop-16-settings-tracking');
  });

  await step('publish', async () => {
    page.on('dialog', (dialog) => dialog.accept());
    const wp = 'https://www.dirtcrew.example/wp-about/';
    await goto(page, `/app/${encodeURIComponent(slug)}/editor?page=${encodeURIComponent(wp)}&q=${encodeURIComponent(SEARCHES[0])}`);
    const heading = page.frameLocator(SEL.editorFrame).locator('h1').first();
    await heading.waitFor({ timeout: SLOW });
    await heading.click();
    await page.keyboard.press('End');
    await page.keyboard.type(' and our crews');
    await page.waitForTimeout(500);
    await page.locator(SEL.publishOpen).first().click();
    await page.waitForSelector(SEL.publishSheet);
    await page.waitForSelector(SEL.proofCode, { timeout: SLOW });
    await audit(page, 'desktop-17-publish-sheet');
    const tag = (await page.innerText(SEL.proofCode)).trim();
    expect(/ranksim/i.test(tag), `a proof tag: ${tag}`);
    // The owner adds the tag to their home page (the fake live web), then checks.
    const html = `<!doctype html><html><head><title>DirtCrew</title>${tag}</head><body><h1>DirtCrew</h1></body></html>`;
    await page.evaluate(async ([html]) => {
      const csrf = document.getElementById('csrf').value;
      for (const url of ['https://dirtcrew.example/', 'https://www.dirtcrew.example/']) {
        await fetch('/__e2e/web', { method: 'POST', headers: { 'content-type': 'application/json', 'x-ranksim-csrf': csrf }, body: JSON.stringify({ url, html }) });
      }
    }, [html]);
    await page.click(SEL.proofCheck);
    await page.waitForSelector(`${SEL.publishSheet} :text("Proven")`, { timeout: 20000 });
    await page.click(SEL.platform('wordpress'));
    await page.fill('.sheet input[name="username"]', 'e2e');
    await page.fill('.sheet input[name="app_password"]', 'abcd efgh ijkl mnop');
    await page.locator('.sheet button:text-is("Check")').last().click();
    await page.waitForSelector(`${SEL.connectSave}:not([disabled])`, { timeout: 20000 });
    await page.click(SEL.connectSave);
    await page.waitForSelector(`${SEL.publishGo}:not([disabled])`, { timeout: SLOW });
    await audit(page, 'desktop-18-publish-ready');
    await page.click(SEL.publishGo);
    await page.waitForSelector(SEL.rollBack, { timeout: SLOW });
    const sheetText = await page.innerText(SEL.publishSheet);
    expect(/live|confirmed|published/i.test(sheetText), `the live check reports: ${sheetText.slice(0, 200)}`);
    await audit(page, 'desktop-19-published');
    await page.click(SEL.rollBack);
    await page.click('.sheet button:text-is("Roll back for real?")');
    await page.locator(SEL.publishSheet).filter({ hasText: /rolled back|restored/i }).waitFor({ timeout: SLOW });
    await audit(page, 'desktop-20-rolled-back');
    await page.keyboard.press('Escape');
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
    const demoText = await page.innerText('main');
    expect(!/Google position|first page of Google|on Google/.test(demoText), 'demo positions never say Google');
    const chartText = (await page.$$eval('svg.rc title, svg.rc desc, svg.rc text', (l) => l.map((n) => n.textContent))).join(' ');
    expect(!/on Google|Google position/.test(chartText), `demo charts never say Google: ${chartText.match(/.{30}Google.{20}/)}`);
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

  await step('first-load-kept', async () => {
    // A brand new site on a slow host: the page shows first, the person edits while the analysis runs, and the
    // report arriving must neither empty the draft, load it again, nor drop those edits.
    const site = 'https://www.slow-first.example/';
    const tab = await desk.newPage();
    watch(tab, 'first-load');
    await tab.goto(`${BASE}/edit?url=${encodeURIComponent(site)}`);
    const h1 = tab.frameLocator(SEL.editorFrame).locator('h1').first();
    await h1.waitFor({ timeout: SLOW * 3 });
    await analyze(tab);
    const base = await tab.evaluate(async (u) => (await import('/studio/js/editor/editor.js')).draftFor(u).base, site);
    expect(base && base.length > 100, 'the first loaded draft has the page');
    // Watch the draft from inside the page: it must never go empty or get a different base once loaded.
    await tab.evaluate(async (u) => {
      const m = await import('/studio/js/editor/editor.js');
      const first = m.draftFor(u).base;
      window.__draftProblems = [];
      window.__draftWatch = setInterval(() => {
        const d = m.draftFor(u);
        if (!d.text) window.__draftProblems.push('empty');
        if (d.base !== first) window.__draftProblems.push('reloaded');
      }, 20);
    }, site);
    await h1.click();
    await tab.keyboard.press('End');
    let typed = 0;
    while (!/\/app\//.test(tab.url()) && typed < 400) {
      await tab.keyboard.type('Q');
      typed += 1;
      await tab.waitForTimeout(60);
    }
    expect(/\/app\//.test(tab.url()), 'the analysis finished while typing');
    await tab.waitForSelector(SEL.editorFrame);
    await tab.waitForTimeout(800);
    const result = await tab.evaluate(async (u) => {
      clearInterval(window.__draftWatch);
      const m = await import('/studio/js/editor/editor.js');
      const frame = document.querySelector('iframe[title="Editable page"]');
      return { problems: [...new Set(window.__draftProblems)], draft: (m.draftFor(u).text.match(/Q+/) || [''])[0].length, frame: ((frame.contentDocument.body.textContent || '').match(/Q+/) || [''])[0].length };
    }, site);
    expect(!result.problems.length, `the first loaded draft was never emptied or replaced: ${result.problems.join(', ')}`);
    expect(result.draft >= typed - 1 && result.frame >= typed - 1, `edits made during the analysis are kept (typed ${typed}, draft ${result.draft}, preview ${result.frame})`);
    await tab.close();
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
    await Promise.all([analyze(one), analyze(two)]);
    // Each analysis is timed by its own done signal (the project shows as built), not by a fixed URL timeout, so a
    // busy machine only makes it slower, never flaky. Generous headroom: six minutes per site.
    const HEADROOM = 360000;
    const timeline = async (p, host) => {
      await p.waitForSelector('.progress-tile .step.active, .progress-tile .step.done', { timeout: HEADROOM });
      const start = Date.now();
      for (;;) {
        const built = await p.evaluate(async (want) => {
          const list = await (await fetch('/api/v1/projects')).json();
          return (list.projects || []).some((x) => String(x.site).includes(want) && x.built);
        }, host).catch(() => false);
        if (built) break;
        if (Date.now() - start > HEADROOM) throw new Error(`${host} did not finish in ${HEADROOM / 1000} s`);
        await p.waitForTimeout(250);
      }
      const end = Date.now();
      await p.waitForURL(/\/app\//, { timeout: HEADROOM });
      return { start, end };
    };
    const [a, b] = await Promise.all([timeline(one, 'slow-a.example'), timeline(two, 'slow-b.example')]);
    expect(Math.max(a.start, b.start) < Math.min(a.end, b.end), `the two analyses overlap in time: ${JSON.stringify({ a, b })}`);
    expect(!refused.length, `no 409 while two sites are analyzed: ${refused.join(', ')}`);
    await one.close();
    await two.close();
  });

  await step('reattach-after-refresh', async () => {
    const tab = await desk.newPage();
    watch(tab, 'reattach');
    await tab.goto(`${BASE}/edit?url=${encodeURIComponent('https://www.slow-c.example/')}`);
    await analyze(tab);
    await tab.waitForSelector('.progress-tile .step.done', { timeout: SLOW * 3 });
    expect(!/\/app\//.test(tab.url()), 'still analyzing when the page reloads');
    await tab.reload();
    await tab.waitForSelector(SEL.progress, { timeout: SLOW * 3 });
    await tab.waitForURL(/\/app\//, { timeout: SLOW * 3 });
    await tab.waitForSelector(SEL.editorFrame, { timeout: SLOW * 3 });
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
      const response = await route.fetch({ timeout: SLOW * 3 });
      const body = await response.text();
      if (/"type":\s*"paused"/.test(body)) paused += 1;
      await route.fulfill({ response, body });
    });
    await tab.fill(SEL.homeInput, 'www.slow-p.example');
    await tab.click(SEL.homeSubmit);
    await analyze(tab);
    await tab.waitForURL(/\/app\//, { timeout: SLOW * 3 });
    await tab.waitForSelector(SEL.editorFrame, { timeout: SLOW * 3 });
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

  await step('phone-issue-sheet', async () => {
    for (const width of [390, 360]) {
      await mobile.setViewportSize({ width, height: 844 });
      await goto(mobile, `/app/${encodeURIComponent(slug)}/issues`);
      await mobile.waitForSelector(SEL.issueRow);
      await mobile.locator(SEL.issueRow).first().click();
      await mobile.waitForSelector('.sheet.open');
      await audit(mobile, `phone-${width}-issue-sheet`);
      await mobile.keyboard.press('Escape');
    }
    await mobile.setViewportSize({ width: 390, height: 844 });
  });

  await step('phone-test-changes', async () => {
    const pageUrl = `https://${SITE}/dirt-work-software/`;
    await goto(mobile, `/app/${encodeURIComponent(slug)}/editor?page=${encodeURIComponent(pageUrl)}&q=${encodeURIComponent(SEARCHES[0])}`);
    await mobile.waitForSelector(`${SEL.testButton}:not([disabled])`, { timeout: SLOW });
    await mobile.click(SEL.testButton);
    await mobile.waitForSelector(SEL.verdict, { timeout: SLOW });
    await checkPredictTiles(mobile);
    await audit(mobile, 'phone-editor-result');
  });

  await step('phone-publish', async () => {
    const wp = 'https://www.dirtcrew.example/wp-about/';
    await goto(mobile, `/app/${encodeURIComponent(slug)}/editor?page=${encodeURIComponent(wp)}`);
    const heading = mobile.frameLocator(SEL.editorFrame).locator('h1').first();
    await heading.waitFor({ timeout: SLOW });
    await heading.click();
    await mobile.keyboard.press('End');
    await mobile.keyboard.type(' today');
    await mobile.waitForTimeout(500);
    await mobile.locator(SEL.publishOpen).first().click();
    await mobile.waitForSelector(SEL.publishSheet);
    await mobile.locator(SEL.publishSheet).filter({ hasText: 'Proven' }).waitFor({ timeout: SLOW });
    await mobile.waitForSelector(`${SEL.publishGo}:not([disabled])`, { timeout: SLOW });
    const wide = await mobile.$$eval('.sheet .tile, .sheet button, .sheet code', (list) => list.filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1).map((el) => el.className || el.tagName));
    expect(!wide.length, `the publish sheet fits 390px: ${wide.join(', ')}`);
    await audit(mobile, 'phone-publish-sheet');
    await mobile.keyboard.press('Escape');
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
