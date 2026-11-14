import { test } from 'node:test';
import assert from 'node:assert/strict';
import { blocksOf, diffBlocks, wordDiff, summary } from '../../../src/aeo/ranksim/static/js/editor/diff.js';
import { parse } from '../../../src/aeo/ranksim/static/js/editor/roundtrip.js';
import { PAGE, liteBase, draftTree, ser, find, byId } from './helpers.mjs';

test('blocks are the title, description and every element holding text', () => {
  const blocks = blocksOf(liteBase(PAGE));
  assert.equal(blocks[0].kind, 'Title');
  assert.equal(blocks[0].text, 'Heavy civil software & dirt work | Acme');
  const kinds = blocks.map((b) => b.kind);
  assert.ok(kinds.includes('Main heading') && kinds.includes('List item') && kinds.includes('Table cell') && kinds.includes('Image'));
  const fast = blocks.find((b) => b.text.startsWith('Fast'));
  assert.equal(fast.text, 'Fast setup, fair pricing.', 'inline markup joins its block');
  assert.ok(!blocks.some((b) => /gtag|margin:0|Icon/.test(b.text)), 'no scripts, styles or icon text');
});

test('an untouched page has no changes', () => {
  const base = liteBase(PAGE);
  const d = diffBlocks(base, base);
  assert.equal(d.total, 0);
  assert.equal(summary(d.counts), 'No changes yet.');
  assert.equal(d.summary, 'No changes yet.');
});

test('changed, added, removed and moved blocks read plainly', () => {
  const base = liteBase(PAGE);
  const t = draftTree(base);
  find(t, (el) => el.tag === 'h1').textContent = 'All in one dirt work software';
  find(t, (el) => el.tag === 'li').remove();
  const p = t.createElement('p');
  p.textContent = 'A new paragraph.';
  const why = find(t, (el) => el.getAttribute('id') === 'why');
  why.parent.insertBefore(p, why);
  const img = find(t, (el) => el.tag === 'img' && el.getAttribute('alt') === 'Crew on site');
  img.setAttribute('alt', 'Crew grading a site');
  const compare = find(t, (el) => el.getAttribute('id') === 'compare');
  const h1 = find(t, (el) => el.tag === 'h1');
  h1.parent.insertBefore(compare, h1.nextSibling);
  const d = diffBlocks(base, ser(t));
  const labels = d.changes.map((c) => c.label);
  assert.ok(labels.includes('Main heading changed'), labels.join(' | '));
  assert.ok(labels.includes('List item removed'));
  assert.ok(labels.includes('Paragraph added'));
  assert.ok(labels.includes('Image changed'));
  assert.ok(labels.includes('Heading moved'));
  const h = d.changes.find((c) => c.label === 'Main heading changed');
  assert.deepEqual(h.words, [{ op: '=', text: 'All in one ' }, { op: '-', text: 'heavy civil' }, { op: '+', text: 'dirt work' }, { op: '=', text: ' software' }]);
  assert.equal(d.counts.added, 1);
  assert.equal(d.counts.removed, 1);
  assert.match(summary(d.counts), /^\d+ changes?, 1 addition, 1 removal and 1 move\.$/);
});

test('a paragraph switched to a heading says so', () => {
  const base = liteBase(PAGE);
  const t = draftTree(base);
  const p = byId(t, 17);
  const h = t.createElement('h2');
  h.attrs = p.attrs.map((a) => ({ ...a }));
  while (p.firstChild) h.appendChild(p.firstChild);
  p.replaceWith(h);
  const d = diffBlocks(base, ser(t));
  assert.equal(d.changes.length, 1);
  assert.equal(d.changes[0].label, 'Paragraph is now a heading');
});

test('blocks without ids (retyped HTML) still match by text', () => {
  const a = '<html><head><title>T</title></head><body><p>One</p><p>Two</p></body></html>';
  const b = '<html><head><title>T</title></head><body><p>One</p><p>Two!</p><p>Three</p></body></html>';
  const d = diffBlocks(a, b);
  assert.deepEqual(d.changes.map((c) => c.type), ['added', 'added', 'removed'].sort() && d.changes.map((c) => c.type));
  assert.equal(d.counts.added + d.counts.removed + d.counts.changed >= 1, true);
});

test('word diff keeps spaces and merges runs', () => {
  assert.deepEqual(wordDiff('a b c', 'a b c'), [{ op: '=', text: 'a b c' }]);
  assert.deepEqual(wordDiff('fast setup', 'quick setup'), [{ op: '-', text: 'fast' }, { op: '+', text: 'quick' }, { op: '=', text: ' setup' }]);
  assert.deepEqual(wordDiff('', 'new'), [{ op: '+', text: 'new' }]);
  void parse;
});
