/* Pages where the browser and a simple parser disagree about the tree. Each base in browser-cases.json was made by
   Chromium (annotate, DOMParser, the app cleaner), so these tests hold the export to what a real browser built. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { exportHtml, parse, serializeLite, reattachIds, stripRs } from '../../../src/aeo/ranksim/static/js/editor/roundtrip.js';
import { liteBase } from './helpers.mjs';

const C = JSON.parse(fs.readFileSync(new URL('./browser-cases.json', import.meta.url), 'utf8'));

function edit(name, fn) {
  const { src, base } = C[name];
  assert.equal(exportHtml(src, base, base), src, `${name}: untouched export is the source`);
  const t = parse(base, { strict: true });
  const byId = (id) => t.elements.find((e) => e.rsid === String(id));
  const find = (f) => t.elements.find(f);
  fn(t, byId, find);
  return exportHtml(src, base, serializeLite(t));
}

test('an old document.write script with a nested script tag stays whole', () => {
  const out = edit('escapedScript', (t, byId) => byId(2).remove());
  assert.equal(out, C.escapedScript.src.replace('<p>one</p>', ''));
});

test('a tracking iframe in a head noscript keeps the head in the head', () => {
  const out = edit('headNoscript', (t, byId) => byId(9).remove());
  assert.equal(out, C.headNoscript.src.replace('<section id=a>A</section>', ''));
});

test('table text the browser moves out of the table is neither lost nor doubled', () => {
  const row = edit('foster', (t, byId, find) => find((e) => e.tag === 'tr' && e.textContent === 'b').remove());
  assert.equal(row, '<div id=w><p id=p1>x</p><table id=t><tr><td>a</td></tr>Note:</table></div>');
  const before = edit('foster', (t, byId) => byId(2).remove());
  assert.equal(before, '<div id=w><table id=t><tr><td>a</td></tr>Note:<tr><td>b</td></tr></table></div>');
});

test('text after </body> is not doubled by a body edit', () => {
  const out = edit('afterBody', (t, byId, find) => find((e) => e.getAttribute('id') === 'a').remove());
  assert.equal(out, C.afterBody.src.replace('<div id=a>A</div>', ''));
});

test('a button inside a button closes the first, as in a browser', () => {
  const out = edit('button', (t, byId) => { byId(2).firstChild.data = 'a EDIT'; });
  assert.equal(out, '<div><button id=b1>a EDIT<button id=b2>b</button>c</button></div>');
});

test('typing into an empty cell whose end tag is implied', () => {
  const out = edit('emptyTd', (t, byId, find) => { find((e) => e.tag === 'td').textContent = 'FILLED'; });
  assert.equal(out, '<table><tr><td>FILLED<td>x</table>');
});

test('setting an attribute the cleaner hid replaces it instead of adding a second one', () => {
  const out = edit('jsHref', (t, byId, find) => find((e) => e.tag === 'a').setAttribute('href', '/buy'));
  assert.equal(out, '<a href="/buy" onclick="go()">Buy</a>');
});

test('attributes written without a space between them stay apart', () => {
  const out = edit('glued', (t, byId, find) => find((e) => e.tag === 'a').removeAttribute('class'));
  assert.equal(out, '<a id=one href="/x">L</a>');
});

test('loose text in streamed content is edited where the source holds it', () => {
  const out = edit('streamed', (t, byId, find) => {
    find((e) => e.tag === 'main').children.find((n) => n.type === 'text' && n.textContent.includes('Loose')).data = 'Changed ';
  });
  assert.equal(out, '<main><h1>H</h1>Changed <template id="P:1"></template><p>after</p></main><div hidden id="S:1"><b>text</b></div>');
  assert.ok(!out.includes('Loose'));
});

test('a paragraph the browser made from a stray </p> keeps its wrapper once filled', () => {
  const out = edit('strayP', (t) => { t.elements.find((e) => e.rsid && e.rsid.startsWith('x') && e.tag === 'p').append('New para'); });
  assert.ok(out.includes('<p>New para</p>'));
});

test('misnested formatting: an edit after a copied bold is not doubled', () => {
  const out = edit('misnested', (t, byId) => { byId(3).lastChild.data = '3 more'; });
  assert.equal((out.match(/3/g) || []).length, 1, out);
  assert.ok(out.includes('3 more'));
});

test('a pasted copy before its original leaves the original its handlers', () => {
  const src = '<!doctype html><html><head></head><body><ul><li onclick="go()">A</li><li>B</li></ul></body></html>';
  const base = liteBase(src);
  const t = parse(base, { strict: true });
  const li = t.elements.find((e) => e.tag === 'li');
  const copy = parse(serializeLite(li), { strict: true }).children[0];
  copy.textContent = 'COPY';
  li.parent.insertBefore(copy, li);
  assert.equal(exportHtml(src, base, serializeLite(t)), src.replace('<li onclick', '<li>COPY</li><li onclick'));
});

test('HTML mode: swapping two blocks keeps the scripts inside them', () => {
  const src = '<!doctype html><html><head><title>t</title></head><body><main><div class="card"><h3>Logs</h3><p>Daily logs.</p><script>card1()</script></div><div class="card"><h3>Time</h3><p>Time cards.</p></div></main></body></html>';
  const base = liteBase(src);
  const c1 = '<div class="card"><h3>Logs</h3><p>Daily logs.</p></div>';
  const c2 = '<div class="card"><h3>Time</h3><p>Time cards.</p></div>';
  const typed = stripRs(base).replace(c1 + c2, c2 + c1);
  const out = exportHtml(src, base, reattachIds(base, typed));
  assert.ok(out.includes('card1()'));
  assert.ok(out.indexOf('Time cards') < out.indexOf('Daily logs'));
});

test('HTML mode: a deleted block does not lend its id (and its embeds) to a new one', () => {
  const src = '<!doctype html><html><head><title>t</title></head><body><aside class="side"><div class="promo"><p>Book a demo</p><script src="https://cal.example/embed.js"></script></div></aside><main class="main"><h1>Pricing</h1><p>Plans start at $99.</p></main></body></html>';
  const base = liteBase(src);
  const typed = stripRs(base).replace('<div class="promo"><p>Book a demo</p></div>', '').replace('<main class="main">', '<main class="main"><div class="faq"><p>Do you offer refunds? Yes.</p></div>');
  const out = exportHtml(src, base, reattachIds(base, typed));
  assert.ok(!out.includes('Book a demo'));
  assert.ok(!out.includes('<div class="faq"><p>Do you offer refunds? Yes.</p><script'), out);
});
