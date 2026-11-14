// node --test tests/e2e/charts.test.mjs
// Pure tests for static/js/charts.js: no DOM, no browser.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { readFileSync } from 'node:fs';

// Loaded as a data URL so it runs as an ES module on any node version, without a package.json "type".
const SOURCE = readFileSync(new URL('../../src/aeo/ranksim/static/js/charts.js', import.meta.url), 'utf8');
const {
  clip, distribution, el, esc, opportunities, positionColumns, predictedVsReal, rankDots, shareBars, sparkline, toSvg,
} = await import(`data:text/javascript;base64,${Buffer.from(SOURCE).toString('base64')}`);

const ROWS = [
  { q: 'dirt work software', position: 4, checked: true, url: 'https://a.example/dirt', predicted: { position: 3, of: 8 } },
  { q: 'excavation crew app', position: 1, checked: true, url: 'https://a.example/', predicted: { position: 6, of: 8 } },
  { q: 'grading job costing', position: null, checked: true, url: null, predicted: null },
  { q: 'paving crew scheduling', position: null, checked: true, url: 'https://a.example/paving', predicted: null },
  { q: 'never pulled', position: null, checked: false, url: null, predicted: null },
  { q: 'site prep bids', position: 12, checked: true, url: 'https://a.example/bids', predicted: null },
];

const SHARE = [
  { domain: 'alpha.example', slots: 5, searches: 4, average: 1.2, you: false, competitor: true, platform: false },
  { domain: 'youtube.com', slots: 3, searches: 3, average: 6, you: false, competitor: false, platform: true },
  { domain: 'a.example', slots: 2, searches: 2, average: 2.5, you: true, competitor: false, platform: false },
];

const DASHES = /[–—]/;

function walk(node, fn) {
  fn(node);
  for (const kid of node.kids || []) walk(kid, fn);
}

function find(node, pred) {
  const out = [];
  walk(node, (n) => { if (pred(n)) out.push(n); });
  return out;
}

function assertAccessible(tree) {
  assert.equal(tree.tag, 'svg');
  assert.equal(tree.attrs.role, 'img');
  const [titleId, descId] = tree.attrs['aria-labelledby'].split(' ');
  const title = find(tree, (n) => n.tag === 'title' && n.attrs.id === titleId);
  const desc = find(tree, (n) => n.tag === 'desc' && n.attrs.id === descId);
  assert.equal(title.length, 1, 'chart has a title');
  assert.equal(desc.length, 1, 'chart has a description');
  assert.ok(desc[0].kids[0].text.length > 5);
  const svg = toSvg(tree);
  assert.ok(!DASHES.test(svg), 'no em or en dashes');
  assert.ok(!/style=/.test(svg), 'no inline styles, the CSP forbids them');
  assert.ok(!/NaN|undefined|Infinity/.test(svg), `no broken numbers: ${svg.match(/.{40}(NaN|undefined|Infinity).{20}/)}`);
  assert.ok(svg.startsWith('<svg xmlns="http://www.w3.org/2000/svg"'));
}

test('esc and toSvg escape text and attributes', () => {
  const svg = toSvg(el('svg', { 'data-x': '"<>&' }, el('text', null, { text: '<script>&' })));
  assert.equal(svg, '<svg xmlns="http://www.w3.org/2000/svg" data-x="&quot;&lt;&gt;&amp;"><text>&lt;script&gt;&amp;</text></svg>');
  assert.equal(esc("'"), '&#39;');
});

test('clip keeps short labels and ends long ones with an ellipsis', () => {
  assert.equal(clip('short', 200), 'short');
  const cut = clip('a very long search phrase that will not fit', 60);
  assert.ok(cut.endsWith('…') && cut.length < 12);
  assert.equal(clip(null, 50), '');
});

test('rankDots draws one row per search with real dots and PREDICTED rings', () => {
  const tree = rankDots(ROWS, { width: 600 });
  assertAccessible(tree);
  assert.equal(find(tree, (n) => n.attrs && n.attrs.class === 'rc-row').length, ROWS.length);
  assert.equal(find(tree, (n) => n.tag === 'circle' && n.attrs.class === 'rc-pred').length, 2);
  assert.equal(find(tree, (n) => n.tag === 'circle' && /rc-you/.test(n.attrs.class)).length, 2);
  assert.equal(find(tree, (n) => n.tag === 'circle' && n.attrs.class === 'rc-none').length, 1, 'unchecked search is marked apart');
  const svg = toSvg(tree);
  assert.match(svg, /PREDICTED #3 of 8/);
  assert.match(svg, /You rank in the top 10 for 2\./);
  // Best position first.
  const labels = find(tree, (n) => n.tag === 'text' && n.attrs['text-anchor'] === 'end').map((n) => n.kids[0].text);
  assert.equal(labels[0], 'excavation crew app');
  // Every x stays inside the chart.
  for (const c of find(tree, (n) => n.tag === 'circle')) assert.ok(c.attrs.cx > 0 && c.attrs.cx < 600, `cx ${c.attrs.cx}`);
});

test('rankDots fits a phone width without overflowing', () => {
  const tree = rankDots(ROWS, { width: 300 });
  for (const c of find(tree, (n) => n.tag === 'circle')) assert.ok(c.attrs.cx + c.attrs.r <= 300);
  assert.equal(tree.attrs.width, 300);
});

test('charts with no data say so instead of drawing nothing', () => {
  for (const tree of [rankDots([]), positionColumns([]), shareBars([]), predictedVsReal(ROWS.map((r) => ({ ...r, predicted: null }))), sparkline([3])]) {
    assertAccessible(tree);
    assert.ok(tree.attrs.class.includes('rc-empty'));
    assert.equal(find(tree, (n) => n.tag === 'text').length, 1);
  }
});

test('distribution buckets positions and keeps unchecked apart', () => {
  const counts = Object.fromEntries(distribution(ROWS).map((b) => [b.key, b.count]));
  assert.deepEqual(counts, { p1: 1, p2: 0, p4: 1, out: 3, unk: 1 });
});

test('positionColumns labels every bucket with its count', () => {
  const tree = positionColumns(ROWS, { width: 360 });
  assertAccessible(tree);
  const svg = toSvg(tree);
  assert.match(svg, /#1: 1\. #2 to 3: 0\. #4 to 10: 1\. Not in top 10: 3\. Not checked: 1\./);
  assert.equal(find(tree, (n) => n.tag === 'rect').length, 4, 'a zero bucket draws no bar');
  const noUnk = positionColumns(ROWS.filter((r) => r.checked !== false));
  assert.ok(!/Not checked/.test(toSvg(noUnk)));
});

test('shareBars highlights you and tracked competitors and always shows you', () => {
  const tree = shareBars(SHARE, { width: 500 });
  assertAccessible(tree);
  const rects = find(tree, (n) => n.tag === 'rect');
  assert.deepEqual(rects.map((r) => r.attrs.class), ['rc-rival', 'rc-other', 'rc-you']);
  assert.match(toSvg(tree), /a\.example \(you\)/);
  assert.match(toSvg(tree), /5 · 50%/);
  const many = Array.from({ length: 12 }, (_, i) => ({ domain: `d${i}.example`, slots: 20 - i }));
  const cut = shareBars(many.concat([{ domain: 'me.example', slots: 1, you: true }]), { limit: 5 });
  const names = find(cut, (n) => n.tag === 'text' && n.attrs['text-anchor'] === 'end').map((n) => n.kids[0].text);
  assert.equal(names.length, 5);
  assert.equal(names[4], 'me.example', 'you are marked by color, so a long domain is not cut by a suffix');
  // Bars stay inside the width.
  for (const r of find(cut, (n) => n.tag === 'rect')) assert.ok(r.attrs.x + r.attrs.width <= 560);
});

test('predictedVsReal only plots searches with both, and never reads as accuracy', () => {
  const tree = predictedVsReal(ROWS);
  assertAccessible(tree);
  assert.equal(find(tree, (n) => n.tag === 'circle').length, 2);
  assert.match(toSvg(tree), /2 searches with both a Google position and a PREDICTED one\. Same Google snapshot the model learned from, so this is not accuracy\./);
  assert.doesNotMatch(toSvg(tree), /within one place/);
  assert.match(toSvg(tree), /PREDICTED versus real/);
});

test('opportunities lists near misses first, then searches with no page', () => {
  const out = opportunities(ROWS);
  assert.deepEqual(out.map((o) => [o.kind, o.q]), [['move', 'dirt work software'], ['move', 'site prep bids'], ['page', 'grading job costing']]);
  assert.equal(out[0].gap, 1);
  assert.equal(opportunities(ROWS, { limit: 1 }).length, 1);
  assert.deepEqual(opportunities(null), []);
});

test('sparkline breaks on gaps and inverts positions so up is better', () => {
  const tree = sparkline([8, 6, null, 3], { width: 100, height: 40 });
  assertAccessible(tree);
  const path = find(tree, (n) => n.tag === 'path')[0].attrs.d;
  assert.equal((path.match(/M/g) || []).length, 2);
  const ys = [...path.matchAll(/[ML][\d.]+ ([\d.]+)/g)].map((m) => Number(m[1]));
  assert.ok(ys[0] > ys[ys.length - 1], 'position 3 is drawn higher than position 8');
});

test('ids are unique across charts on one page', () => {
  const a = rankDots(ROWS);
  const b = rankDots(ROWS);
  assert.notEqual(a.attrs['aria-labelledby'], b.attrs['aria-labelledby']);
});
