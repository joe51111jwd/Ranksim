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
  await page.route('**/api/v1/fetch', (route) => {
    const asked = JSON.parse(route.request().postData() || '{}').url || '';
    const html = asked.includes('spa.test') ? '<!doctype html><html><head><title>App</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>' : SOURCE;
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ url: asked || URL_, html }) });
  });
  await page.route('**/slow.png', () => {}); // never answers: the page must still be editable
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

  // content a site fades in with its scripts is shown; the marker never reaches the draft
  const faded = await editor(async () => {
    const doc = (await import('/studio/js/editor/editor.js')).editorCtx().doc();
    return doc.defaultView.getComputedStyle(doc.querySelector('.fade-in')).opacity;
  });
  check(faded === '1', `a block hidden until a script fades it in is shown (opacity ${faded})`);
  check(await editor(async () => !(await import('/studio/js/editor/editor.js')).editorCtx().getHtml().includes('data-rs-shown')), 'the reveal marker is not in the draft');

  // the preview never runs scripts
  const sandbox = await page.locator('.viewport iframe').getAttribute('sandbox');
  check(sandbox === 'allow-same-origin', `the preview sandbox has no allow-scripts (${sandbox})`);
  check(await editor(async () => { const ed = await import('/studio/js/editor/editor.js'); return ed.editorCtx().doc().querySelectorAll('script:not([type="application/ld+json"])').length === 0; }), 'no runnable script is in the preview');

  // an untouched export is the page byte for byte
  const untouched = await editor(async () => (await import('/studio/js/editor/editor.js')).editorCtx().exportHtml());
  check(untouched === SOURCE, 'an untouched page exports byte for byte');

  // the first edit survives an immediate switch to HTML mode
  await frame.locator('footer p').click();
  await page.keyboard.press('End');
  await page.keyboard.type(' today');
  await page.getByRole('button', { name: 'HTML' }).click();
  await page.waitForSelector('textarea.code');
  check((await page.locator('textarea.code').inputValue()).includes('Contact us today'), 'a first edit is kept by an immediate switch to HTML');
  await page.getByRole('button', { name: 'Visual' }).click();
  await settled();
  check((await frame.locator('footer p').textContent()) === 'Contact us today', 'and is still there back in Visual');

  // edit(fn) that changes nothing reports false and adds no step
  const noop = await editor(async () => {
    const ctx = (await import('/studio/js/editor/editor.js')).editorCtx();
    const before = ctx.canUndo;
    const result = ctx.edit(() => {});
    return { result, same: ctx.canUndo === before };
  });
  check(noop.result === false && noop.same, 'edit(fn) that changes nothing returns false');

  // the insert menu works from the keyboard
  await page.getByRole('button', { name: 'Insert' }).focus();
  await page.keyboard.press('Enter');
  await page.waitForSelector('.ed-menu');
  await page.keyboard.press('ArrowDown');
  check(await page.evaluate(() => document.activeElement && document.activeElement.dataset.block === 'paragraph'), 'arrow keys move through the insert menu');
  await page.keyboard.press('Escape');
  check(await page.locator('.ed-menu').count() === 0, 'Escape closes the insert menu');

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
  check(out.includes('<footer><p>Contact us today</p></footer>'), 'the first edit is exported');
  check(/"@type": "FAQPage"/.test(out), 'the FAQ structured data is exported');
  check(!/data-rs-(id|faq|a|reveal|pick)/.test(out), 'no editor attributes are exported');
  check(out.includes('<div data-rs-slide="2" data-rs="hero">Slide two</div>'), "the page's own data-rs attributes are exported");
  const clean = await editor(async () => (await import('/studio/js/editor/editor.js')).editorCtx().getHtml());
  check(clean.includes('data-rs-slide="2"') && clean.includes('data-rs="hero"') && !clean.includes('data-rs-id'), "getHtml keeps the page's own data-rs attributes and none of the editor's");
  check(out.includes('Track crews, trucks &amp; haul tickets.<!-- --> Built for contractors&nbsp;&copy; 2026.'), 'untouched text keeps its entities');

  check(out.includes('<div class="fade-in"><p>'), 'the faded block is exported as the page wrote it');

  // a page drawn entirely by scripts says so
  await page.goto(`${BASE}/edit?url=${encodeURIComponent('https://spa.test/')}`);
  await settled();
  check((await page.locator('.edit-hint').textContent()) === 'Built by scripts', 'a script-only page is labeled Built by scripts');
  await page.goto(`${BASE}/edit?url=${encodeURIComponent(URL_)}`);
  await settled();

  // at phone width the insert menu opens on screen
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(400);
  await page.getByRole('button', { name: 'Insert' }).click();
  const box = await page.locator('.ed-menu').boundingBox();
  check(!!box && box.y >= 0 && box.y + box.height <= 844 && box.x >= 0 && box.x + box.width <= 390, 'the insert menu is on screen at phone width');
  check(await page.evaluate(() => document.documentElement.scrollWidth <= 390), 'no sideways scroll at phone width');
  await page.keyboard.press('Escape');

  console.log(errors.length ? `PAGE ERRORS ${errors.join(' | ')}` : 'no page errors');
  if (errors.length) failed += 1;
  await browser.close();
  process.exit(failed ? 1 : 0);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
