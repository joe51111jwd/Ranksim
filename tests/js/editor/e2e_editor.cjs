/* Playwright run of the editor against a running Ranksim (BASE, default http://127.0.0.1:8765). Offline: the page is
   served from fixture-page.html by intercepting the app's fetch call, and the site analysis is answered with an
   error, so the server never touches the network. Run by tests/test_editor_e2e.py. Exit code 1 on any failure. */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const PASSWORD = process.env.RANKSIM_PASSWORD || '';
const URL_ = 'https://fixture.test/';
const SOURCE = fs.readFileSync(path.join(__dirname, 'fixture-page.html'), 'utf8');
let page;
let failed = 0;
const check = (ok, msg) => {
  console.log(`${ok ? 'PASS' : 'FAIL'} ${msg}`);
  if (!ok) failed += 1;
};
const editor = (fn) => page.evaluate(fn);

async function settled() {
  for (let k = 0; k < 2; k += 1) {
    await page.waitForFunction(async () => {
      const ed = await import('/studio/js/editor/editor.js');
      const c = ed.editorCtx();
      return !!ed.ED.ready && !!c && !!c.doc() && !!ed.modelOf(c.draft);
    }, null, { timeout: 30000 });
    await page.waitForTimeout(600);
  }
}

(async () => {
  const browser = await chromium.launch();
  page = await browser.newPage({ viewport: { width: 1400, height: 1000 }, acceptDownloads: true });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/api/v1/fetch', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ url: URL_, html: SOURCE }) }));
  await page.route('**/api/v1/sites', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'Offline test.' }) }));
  await page.goto(`${BASE}/`);
  if (PASSWORD) {
    await page.evaluate(async (password) => {
      const csrf = document.getElementById('csrf').value;
      await fetch('/api/v1/login', { method: 'POST', headers: { 'content-type': 'application/json', 'x-ranksim-csrf': csrf }, body: JSON.stringify({ password }) });
    }, PASSWORD);
  }
  await page.goto(`${BASE}/edit?url=${encodeURIComponent(URL_)}`);
  await settled();
  const frame = page.frameLocator('.viewport iframe');

  // the preview never runs scripts
  const sandbox = await page.locator('.viewport iframe').getAttribute('sandbox');
  check(sandbox === 'allow-same-origin', `the preview sandbox has no allow-scripts (${sandbox})`);
  check(await editor(async () => { const ed = await import('/studio/js/editor/editor.js'); return ed.editorCtx().doc().querySelectorAll('script:not([type="application/ld+json"])').length === 0; }), 'no runnable script is in the preview');

  // an untouched export is the page byte for byte
  const untouched = await editor(async () => (await import('/studio/js/editor/editor.js')).editorCtx().exportHtml());
  check(untouched === SOURCE, 'an untouched page exports byte for byte');

  // edit the heading, undo, redo
  const h1 = frame.locator('h1');
  await h1.click();
  await page.keyboard.press('End');
  await page.keyboard.type(' and yards');
  await page.waitForTimeout(900);
  check((await h1.textContent()).includes('and yards'), 'typing edits the heading');
  await page.keyboard.press('Control+z');
  await page.waitForTimeout(300);
  check((await frame.locator('h1').textContent()) === 'Dirt work software for crews', 'Ctrl+Z undoes');
  await page.keyboard.press('Control+Shift+z');
  await page.waitForTimeout(300);
  check((await frame.locator('h1').textContent()).includes('and yards'), 'Ctrl+Shift+Z redoes');

  // insert an FAQ with structured data after the first paragraph
  await frame.locator('main p').first().click();
  await page.getByRole('button', { name: 'Insert' }).click();
  await page.locator('.ed-menu [data-block="faq"]').click();
  await page.getByRole('button', { name: 'Add FAQ' }).click();
  await page.waitForTimeout(600);
  check(await frame.locator('section[data-rs-faq] h3').count() === 3, 'the FAQ is inserted');

  // download keeps every script, comment and style
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 15000 }),
    page.locator('.ed-changes').getByRole('button', { name: 'Download page' }).click(),
  ]);
  const out = fs.readFileSync(await download.path(), 'utf8');
  const scripts = (t) => (t.match(/<script\b[\s\S]*?<\/script>/gi) || []);
  check(scripts(SOURCE).every((s) => out.includes(s)), 'every original script is in the download, unchanged');
  check(scripts(out).length === scripts(SOURCE).length + 1, 'plus one FAQPage JSON-LD');
  check(out.includes('<!-- Google Tag Manager -->') && out.includes('<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-TEST"'), 'comments and noscript stay');
  check(out.includes('<link rel="preload" href="/app.js" as="script" crossorigin>') && out.includes('crossorigin="anonymous"'), 'preview-only changes to links are not exported');
  check(out.includes(`<a href="/" onclick="track('logo')">`), 'event handlers stay');
  check(out.includes("<h1 class='hero'>Dirt work software for crews and yards</h1>"), 'the heading edit is exported in place');
  check(/"@type": "FAQPage"/.test(out), 'the FAQ structured data is exported');
  check(!/data-rs/.test(out), 'no editor attributes are exported');
  check(out.includes('Track crews, trucks &amp; haul tickets.<!-- --> Built for contractors&nbsp;&copy; 2026.'), 'untouched text keeps its entities');

  console.log(errors.length ? `PAGE ERRORS ${errors.join(' | ')}` : 'no page errors');
  if (errors.length) failed += 1;
  await browser.close();
  process.exit(failed ? 1 : 0);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
