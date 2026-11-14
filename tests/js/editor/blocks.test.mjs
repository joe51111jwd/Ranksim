import { test } from 'node:test';
import assert from 'node:assert/strict';
import { blockHtml, faqJsonLd, faqPairs, syncFaq, outline, sectionNodes, moveSection, convertBlock, insertBlock, insertionPoint, textBlockOf, imageUrlOk, BLOCKS } from '../../../src/aeo/ranksim/static/js/editor/blocks.js';
import { parse, exportHtml } from '../../../src/aeo/ranksim/static/js/editor/roundtrip.js';
import { PAGE, liteBase, draftTree, ser, find } from './helpers.mjs';

const doc = (html) => parse(html, { strict: true });

test('every block renders plain markup', () => {
  for (const block of BLOCKS) {
    const html = blockHtml(block.key, { src: 'https://acme.test/a.png', alt: 'A crew' });
    assert.ok(html.length > 10, block.key);
    assert.ok(!/style=|class=/.test(html), `${block.key} takes the page's own styles`);
  }
  assert.equal(blockHtml('table', { rows: 2, cols: 2 }), '<table><thead><tr><th>Column 1</th><th>Column 2</th></tr></thead><tbody><tr><td>Value</td><td>Value</td></tr><tr><td>Value</td><td>Value</td></tr></tbody></table>');
  assert.equal(blockHtml('image', { src: 'https://a.test/x.png', alt: 'A "big" <crew>' }), '<figure><img src="https://a.test/x.png" alt="A &quot;big&quot; &lt;crew&gt;" loading="lazy"></figure>');
  assert.throws(() => blockHtml('nope'));
});

test('image addresses must be http or https', () => {
  assert.ok(imageUrlOk('https://a.test/x.png'));
  assert.ok(!imageUrlOk('javascript:alert(1)'));
  assert.ok(!imageUrlOk('data:image/png;base64,AAA'));
  assert.ok(!imageUrlOk('https://a.test/x".png'));
});

test('FAQ JSON-LD follows schema.org and cannot break out of its script', () => {
  const json = faqJsonLd([{ q: 'Is it </script> safe?', a: 'Yes.' }, { q: 'Empty', a: '' }]);
  assert.ok(!json.includes('</script>'));
  const data = JSON.parse(json);
  assert.equal(data['@type'], 'FAQPage');
  assert.equal(data.mainEntity.length, 1);
  assert.equal(data.mainEntity[0].name, 'Is it </script> safe?');
  assert.equal(data.mainEntity[0].acceptedAnswer.text, 'Yes.');
});

test('an FAQ block reads back its pairs and keeps its JSON-LD in step', () => {
  const html = blockHtml('faq', { key: 'k1', jsonld: true, pairs: [{ q: 'What is it?', a: 'A tool.' }, { q: 'Cost?', a: 'Free & fair.' }] });
  const t = doc(`<html><head></head><body><p>x</p>${html}</body></html>`);
  const section = find(t, (el) => el.getAttribute('data-rs-faq') === 'k1');
  assert.deepEqual(faqPairs(section), [{ q: 'What is it?', a: 'A tool.' }, { q: 'Cost?', a: 'Free & fair.' }]);
  assert.equal(syncFaq(t), false, 'already in step');
  const q = section.children.find((el) => el.tag === 'h3');
  q.textContent = 'What is Acme?';
  assert.equal(syncFaq(t), true);
  const script = find(t, (el) => el.tag === 'script');
  assert.equal(JSON.parse(script.textContent).mainEntity[0].name, 'What is Acme?');
  section.remove();
  assert.equal(syncFaq(t), true);
  assert.equal(t.elements.filter((el) => el.tag === 'script' && el.parent).length, 0, 'deleting the FAQ drops its JSON-LD');
});

test('outline is the heading tree in order', () => {
  const t = doc('<html><body><h1>A</h1><h2>B</h2><h3>C</h3><h2>D</h2><svg><title>x</title></svg><h3> </h3></body></html>');
  const { roots, flat } = outline(t);
  assert.deepEqual(flat.map((h) => `${h.depth}${h.text}`), ['0A', '1B', '2C', '1D']);
  assert.equal(roots.length, 1);
  assert.deepEqual(roots[0].children.map((h) => h.text), ['B', 'D']);
});

test('a section runs to the next heading of the same level, or is its wrapper', () => {
  const t = doc('<body><h2>A</h2><p>a1</p><h3>A.1</h3><p>a2</p><h2>B</h2><p>b</p><section><h2>C</h2><p>c</p></section></body>');
  const [a, , b, c] = outline(t).flat.map((h) => h.el);
  assert.deepEqual(sectionNodes(a).map((n) => n.textContent), ['A', 'a1', 'A.1', 'a2']);
  assert.deepEqual(sectionNodes(b).map((n) => n.textContent), ['B', 'b']);
  assert.equal(sectionNodes(c)[0].tag, 'section');
});

test('moving a section reorders the page, and the export carries it', () => {
  const base = liteBase(PAGE);
  const t = draftTree(base);
  const flat = outline(t).flat;
  const why = flat.find((h) => h.text === 'Why Acme').el;
  const compare = flat.find((h) => h.text === 'Compare').el;
  assert.equal(moveSection(compare, why, 'before'), true);
  assert.deepEqual(outline(t).flat.map((h) => h.text), ['All in one heavy civil software', 'Compare', 'Why Acme']);
  assert.equal(moveSection(why, why), false);
  const out = exportHtml(PAGE, base, ser(t));
  assert.ok(out.indexOf('Compare') < out.indexOf('Why Acme'));
  assert.ok(out.includes('<noscript><img src="/pixel.gif" alt=""></noscript>'), 'hidden parts stay');
});

test('switching a block keeps its id, attributes and contents', () => {
  const t = doc('<body><p data-rs-id="5" class="lead">Hello <b>there</b></p></body>');
  const p = t.elements[1];
  const h = convertBlock(p, 'h2');
  assert.equal(ser(t), '<body><h2 data-rs-id="5" class="lead">Hello <b>there</b></h2></body>');
  assert.equal(convertBlock(h, 'h2'), h);
  assert.equal(convertBlock(h, 'div'), h, 'only p, h2 and h3');
  assert.equal(textBlockOf(h.children[1].children[0]), h);
});

test('inserted blocks go after the block around the cursor', () => {
  const t = doc('<html><body><ul><li>one</li></ul><p>x <b>y</b></p></body></html>');
  const li = find(t, (el) => el.tag === 'li');
  const b = find(t, (el) => el.tag === 'b');
  assert.equal(insertionPoint(li.firstChild).tag, 'ul');
  assert.equal(insertionPoint(b.firstChild).tag, 'p');
  insertBlock(t, blockHtml('paragraph', { text: 'New' }), insertionPoint(li));
  assert.equal(ser(t), '<html><body><ul><li>one</li></ul><p>New</p><p>x <b>y</b></p></body></html>');
  insertBlock(t, blockHtml('list'), null);
  assert.ok(ser(t).endsWith('<li>Third point</li></ul></body></html>'));
});
