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
    if (msg.type() !== 'error') return;
    const text = msg.text();
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

async function goto(page, path) {
  await page.goto(BASE + path, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !document.querySelector('main .boot'), null, { timeout: SLOW });
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
    await audit(page, 'desktop-06-overview');
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
    await audit(page, 'desktop-11-editor-result');
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
    });
  }

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
