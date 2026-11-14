// Browser check for the audit UI (tests/test_audit.py runs it against tests/e2e/server.py, fakes only).
//   node tests/audit_browser/check.mjs --base http://127.0.0.1:8799
// Prints one JSON line {ok, failures, notes} and exits 1 on any failure.
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, index, all) => {
  if (value.startsWith('--')) pairs.push([value.slice(2), all[index + 1]]);
  return pairs;
}, []));
const BASE = (args.base || 'http://127.0.0.1:8799').replace(/\/$/, '');
const failures = [];
const notes = {};
const check = (ok, what) => { if (!ok) failures.push(what); };

const browser = await chromium.launch();
try {
  const page = await (await browser.newContext({ viewport: { width: 1360, height: 900 } })).newPage();
  const errors = [];
  page.on('pageerror', (err) => errors.push(String(err)));
  page.on('console', (msg) => { if (msg.type() === 'error' && !/Failed to load resource/.test(msg.text())) errors.push(msg.text()); });
  const bad = [];
  page.on('response', async (res) => { if (res.status() >= 400) bad.push(`${res.status()} ${res.request().method()} ${res.url()} ${(await res.text().catch(() => '')).slice(0, 200)}`); });
  await page.goto(`${BASE}/`);
  const csrf = await page.evaluate(() => document.getElementById('csrf').value);
  const headers = { 'x-ranksim-csrf': csrf, 'content-type': 'application/json' };
  await page.request.post(`${BASE}/api/v1/login`, { headers, data: { password: 'e2e-pass' } });
  const analyzed = await page.request.post(`${BASE}/api/v1/sites`, { headers, data: { site: 'www.dirtcrew.example' }, timeout: 300000 });
  const lines = (await analyzed.text()).trim().split('\n').map((line) => JSON.parse(line));
  const slug = lines[lines.length - 1].slug;
  check(!!slug, 'analysis gave no slug');
  const report = await (await page.request.get(`${BASE}/api/v1/projects/${slug}/report`)).json();
  const ids = new Set(report.issues.map((issue) => issue.id));

  await page.goto(`${BASE}/app/${slug}/issues`);
  await page.waitForSelector('.issue-controls', { timeout: 60000 });
  const buttons = page.locator('button.fix-top');
  notes.fixButtons = await buttons.count();
  check(notes.fixButtons >= 1, 'no Fix the top N button on the Issues screen');
  await buttons.first().click();
  await page.waitForSelector('.fix-list', { timeout: 60000 });
  notes.rows = await page.locator('.fix-row').count();
  check(notes.rows >= 2, `review sheet has ${notes.rows} rows`);
  await page.click('.fix-actions button');
  await page.waitForTimeout(1500);
  notes.applied = await page.locator('.annot-done').count();
  check(notes.applied === notes.rows, `applied ${notes.applied} of ${notes.rows}`);

  const fixes = await page.evaluate(async () => {
    const hooks = await import('/studio/js/hooks.js');
    const editor = await import('/studio/js/editor/editor.js');
    return hooks.collect('publish-fixes', editor.editorCtx());
  });
  notes.fixes = fixes.length;
  check(fixes.length === notes.applied, `publish-fixes gave ${fixes.length}, applied ${notes.applied}`);
  check(fixes.every((fix) => ids.has(fix.id) && fix.title), 'a publish-fixes id is not a report issue id');

  const html = await page.evaluate(async () => {
    const editor = await import('/studio/js/editor/editor.js');
    return editor.editorCtx().getHtml();
  });
  check(!/data-rs/.test(html), 'the draft carries data-rs');

  const undone = await page.evaluate(async () => {
    const hooks = await import('/studio/js/hooks.js');
    const editor = await import('/studio/js/editor/editor.js');
    const ctx = editor.editorCtx();
    ctx.setHtml(ctx.draft.base);
    return hooks.collect('publish-fixes', editor.editorCtx()).length;
  });
  check(undone === 0, `publish-fixes still lists ${undone} after the changes were undone`);
  check(!errors.length, `console errors: ${errors.join(' | ')}`);
  notes.failedRequests = bad;
} catch (err) {
  failures.push(`crashed: ${err && err.stack ? err.stack : err}`);
} finally {
  await browser.close();
}
console.log(JSON.stringify({ ok: !failures.length, failures, notes }));
process.exit(failures.length ? 1 : 0);
