import { test } from 'node:test';
import assert from 'node:assert/strict';
import { annotate, parse, exportHtml, reattachIds, stripRs, decode, lcs, serializeLite, LNode } from '../../../src/aeo/ranksim/static/js/editor/roundtrip.js';
import { PAGE, liteBase, draftTree, ser, byId, find, content } from './helpers.mjs';

const scripts = (html) => (html.match(/<script\b[^>]*>[\s\S]*?<\/script>/gi) || []);
const noRs = (html) => assert.ok(!/data-rs/.test(html), 'no data-rs attributes in the export');
const h1Of = (t) => find(t, (el) => el.tag === 'h1');

/* Where two strings stop agreeing, from each end: the export should differ from the source only in a small window. */
function changedWindow(a, b) {
  let s = 0;
  while (s < a.length && s < b.length && a[s] === b[s]) s += 1;
  let e = 0;
  while (e < a.length - s && e < b.length - s && a[a.length - 1 - e] === b[b.length - 1 - e]) e += 1;
  return { start: s, removed: a.slice(s, a.length - e), added: b.slice(s, b.length - e) };
}

test('every byte of the source belongs to the tree', () => {
  for (const html of [PAGE, '<p>a<p>b<li>c<table><tr><td>x</table>', 'text only &amp; <b>bold', '<!-- c --><!--><!---><div', '<a href="x>y</a>', '</p></br><?php x ?><![CDATA[x]]>', '<script>if(a</script>b</script>', '<title>a<b>c</title><textarea></div></textarea>']) {
    const base = html;
    assert.equal(exportHtml(html, base, base) !== undefined, true);
    const tree = parse(html);
    const pieces = [];
    const emit = (node) => {
      if (node.type !== 'element' && node.type !== 'root') { pieces.push(html.slice(node.start, node.end)); return; }
      if (node.type === 'element') pieces.push(html.slice(node.start, node.startEnd));
      node.children.forEach(emit);
      if (node.type === 'element' && node.hasEnd) pieces.push(html.slice(node.endStart, node.end));
    };
    emit(tree);
    assert.equal(pieces.join(''), html);
  }
});

test('annotate numbers start tags in order and leaves raw text alone', () => {
  const out = annotate('<div><script>var s = "<p>";</script><p class=a>x</p></div>');
  assert.equal(out, '<div data-rs-id="1"><script data-rs-id="2">var s = "<p>";</script><p data-rs-id="3" class=a>x</p></div>');
});

test('an untouched page exports byte for byte', () => {
  const base = liteBase(PAGE);
  assert.match(base, /data-rs-id="1"/);
  assert.ok(!/gtag\('js'/.test(base), 'the base has no scripts');
  assert.equal(exportHtml(PAGE, base, base), PAGE);
});

test('a text edit changes only that text and keeps every script', () => {
  const base = liteBase(PAGE);
  const t = draftTree(base);
  h1Of(t).textContent = 'Heavy civil software for crews & yards';
  const out = exportHtml(PAGE, base, ser(t));
  const w = changedWindow(PAGE, out);
  assert.equal(w.removed, 'All in one heavy civil software');
  assert.equal(w.added, 'Heavy civil software for crews &amp; yards');
  assert.ok(out.includes("<h1 class='hero'>Heavy civil software for crews &amp; yards</h1>"));
  assert.deepEqual(scripts(out), scripts(PAGE));
  assert.ok(out.includes('<!-- analytics: keep -->'));
  noRs(out);
});

test('an edit next to entities keeps the untouched entities as written', () => {
  const base = liteBase(PAGE);
  const t = draftTree(base);
  const p = byId(t, 17);
  p.textContent = `${p.textContent} Try it.`;
  const out = exportHtml(PAGE, base, ser(t));
  assert.ok(out.includes('<p>Run crews, equipment &amp; dirt work in one place.&nbsp;Built for contractors © 2026. Try it.</p>'));
  const w = changedWindow(PAGE, out);
  assert.ok(w.removed.length < 120 && w.added.length < 120);
});

test('attribute edits keep the rest of the tag as written, including hidden handlers', () => {
  const base = liteBase(PAGE);
  const t = draftTree(base);
  const logo = find(t, (el) => el.tag === 'a' && el.getAttribute('href') === '/');
  logo.setAttribute('href', '/home');
  logo.setAttribute('title', 'Home');
  const h1 = h1Of(t);
  h1.setAttribute('class', 'hero big');
  const out = exportHtml(PAGE, base, ser(t));
  assert.ok(out.includes(`<a href="/home" onclick="track('logo')" title="Home">Acme</a>`), out.slice(out.indexOf('<nav>'), out.indexOf('</nav>')));
  assert.ok(out.includes('<h1 class="hero big">'));
  assert.ok(out.includes("<html lang='en'>"), 'untouched tags keep their quotes');
  noRs(out);
});

test('removing a block keeps the script next to it', () => {
  const base = liteBase(PAGE);
  const t = draftTree(base);
  byId(t, 17).remove();
  const out = exportHtml(PAGE, base, ser(t));
  assert.ok(!out.includes('Run crews'));
  assert.ok(out.includes("<script>document.write('<p>widget</p>')</script>"));
  assert.deepEqual(scripts(out), scripts(PAGE));
});

test('moving a section carries its markup, and scripts stay put', () => {
  const base = liteBase(PAGE);
  const t = draftTree(base);
  const why = find(t, (el) => el.getAttribute('id') === 'why');
  const compare = find(t, (el) => el.getAttribute('id') === 'compare');
  const table = find(t, (el) => el.tag === 'table');
  const main = why.parent;
  main.insertBefore(compare, why);
  main.insertBefore(table, why);
  const out = exportHtml(PAGE, base, ser(t));
  assert.ok(out.indexOf('id="compare"') < out.indexOf('id="why"'));
  assert.ok(out.includes('<table class=grid>\n      <tbody>'), 'the moved table is copied from the source as written');
  assert.deepEqual(scripts(out), scripts(PAGE));
  assert.deepEqual(content(out), content(ser(t)));
});

test('switching a paragraph to a heading keeps attributes and inner markup', () => {
  const base = liteBase(PAGE);
  const t = draftTree(base);
  const p = find(t, (el) => el.tag === 'p' && el.textContent.startsWith('Fast'));
  const h = t.createElement('h3');
  h.attrs = p.attrs.map((a) => ({ ...a }));
  while (p.firstChild) h.appendChild(p.firstChild);
  p.replaceWith(h);
  const out = exportHtml(PAGE, base, ser(t));
  assert.ok(out.includes('<h3>Fast <strong>setup</strong>, fair <em>pricing</em>.</h3>'));
});

test('inserted blocks come out clean, with JSON-LD, and no editor attributes', () => {
  const base = liteBase(PAGE);
  const t = draftTree(base);
  const ul = find(t, (el) => el.tag === 'ul');
  const html = '<section data-rs-faq="f1"><h2>Questions</h2><h3>Is it fast?</h3><p>Yes &amp; cheap.</p></section><script type="application/ld+json" data-rs-faq-ld="f1">{"@type":"FAQPage"}</script>';
  const frag = parse(html, { strict: true });
  for (const node of [...frag.children].reverse()) ul.parent.insertBefore(node, ul.nextSibling);
  const out = exportHtml(PAGE, base, ser(t));
  assert.ok(out.includes('</ul><section><h2>Questions</h2><h3>Is it fast?</h3><p>Yes &amp; cheap.</p></section><script type="application/ld+json">{"@type":"FAQPage"}</script>'), out.slice(out.indexOf('</ul>') - 10, out.indexOf('</ul>') + 260));
  noRs(out);
  assert.equal(scripts(out).length, scripts(PAGE).length + 1);
});

test('title and description: changed in place, or added when missing', () => {
  const base = liteBase(PAGE);
  const t = draftTree(base);
  find(t, (el) => el.tag === 'title').textContent = 'Dirt work software | Acme';
  const meta = t.createElement('meta');
  meta.setAttribute('name', 'description');
  meta.setAttribute('content', 'Plan "dirt" work & crews.');
  const head = find(t, (el) => el.tag === 'head');
  head.appendChild(meta);
  const out = exportHtml(PAGE, base, ser(t));
  assert.ok(out.includes('<title>Dirt work software | Acme</title>'));
  assert.ok(out.includes('<meta name="description" content="Plan &quot;dirt&quot; work &amp; crews.">'));
  assert.ok(out.indexOf('name="description"') < out.indexOf('</head>'));
});

test('elements the browser invents are see-through', () => {
  const source = '<!doctype html><html><head><title>T</title></head><body><table><tr><td>a</td><td>b</td></tr></table><p>x</p></body></html>';
  // the browser adds a tbody: it gets an x id
  const base = liteBase(source).replace('<tr data-rs-id="6">', '<tbody data-rs-id="x1"><tr data-rs-id="6">').replace('</tr></table>', '</tr></tbody></table>');
  assert.equal(exportHtml(source, base, base), source);
  const t = draftTree(base);
  byId(t, 8).textContent = 'B';
  const row = byId(t, 6);
  const extra = parse('<tr><td>c</td><td>d</td></tr>', { strict: true }).children[0];
  row.parent.appendChild(extra);
  const out = exportHtml(source, base, ser(t));
  assert.equal(out, '<!doctype html><html><head><title>T</title></head><body><table><tr><td>a</td><td>B</td></tr><tr><td>c</td><td>d</td></tr></table><p>x</p></body></html>');
});

test('a page without html, head or body tags', () => {
  const source = '<title>Bare</title>\n<h1>Hello</h1>\n<p>One</p>\n<p>Two</p>\n';
  const base = '<!doctype html>\n<html data-rs-id="x1"><head data-rs-id="x2"><title data-rs-id="1">Bare</title>\n</head><body data-rs-id="x3"><h1 data-rs-id="2">Hello</h1>\n<p data-rs-id="3">One</p>\n<p data-rs-id="4">Two</p>\n</body></html>';
  assert.equal(exportHtml(source, base, base), source);
  const t = draftTree(base);
  byId(t, 3).remove();
  const p = t.createElement('p');
  p.textContent = 'Three';
  byId(t, 4).parent.appendChild(p);
  assert.equal(exportHtml(source, base, ser(t)), '<title>Bare</title>\n<h1>Hello</h1>\n\n<p>Two</p><p>Three</p>\n');
});

test('streamed content the cleaner moved still takes edits where the source holds it', () => {
  const source = '<html><head></head><body><main><!--$?--><template id="B:0"></template><p>Loading</p><!--/$--></main><div hidden id="S:0"><h2>Real</h2><p>Body</p></div><script>$RC("B:0","S:0")</script></body></html>';
  // what the browser side produces after settling: the hidden div's children replace the fallback
  const base = '<html data-rs-id="1"><head data-rs-id="2"></head><body data-rs-id="3"><main data-rs-id="4"><!--$?--><h2 data-rs-id="8">Real</h2><p data-rs-id="9">Body</p><!--/$--></main></body></html>';
  assert.equal(exportHtml(source, base, base), source);
  const t = draftTree(base);
  byId(t, 9).textContent = 'Better body';
  const out = exportHtml(source, base, ser(t));
  assert.equal(out, source.replace('<p>Body</p>', '<p>Better body</p>'));
});

test('a pasted copy of an element is new content, not a second original', () => {
  const base = liteBase(PAGE);
  const t = draftTree(base);
  const li = find(t, (el) => el.tag === 'li');
  const copy = parse(ser(li), { strict: true }).children[0];
  copy.textContent = 'Daily logs copy';
  li.parent.insertBefore(copy, li.nextSibling);
  const out = exportHtml(PAGE, base, ser(t));
  assert.ok(out.includes('<li>Daily logs</li><li>Daily logs copy</li>'), out.slice(out.indexOf('<ul>'), out.indexOf('</ul>')));
  noRs(out);
});

test('stripRs removes every editor attribute and nothing else', () => {
  const base = liteBase(PAGE);
  const clean = stripRs(base);
  noRs(clean);
  assert.equal(clean.length, base.replace(/ data-rs-id="[^"]*"/g, '').length);
});

test('HTML mode: ids come back after the user retypes the draft', () => {
  const base = liteBase(PAGE);
  const typed = stripRs(base).replace('Why Acme', 'Why crews pick Acme').replace('<li>Time cards</li>', '<li>Time cards</li><li>Equipment</li>');
  const draft = reattachIds(base, typed);
  const t = draftTree(draft);
  assert.equal(find(t, (el) => el.getAttribute('id') === 'why').rsid, '19');
  const out = exportHtml(PAGE, base, draft);
  assert.ok(out.includes('<h2 id="why">Why crews pick Acme</h2>'));
  assert.ok(out.includes('<li>Time cards</li><li>Equipment</li>'));
  assert.deepEqual(scripts(out), scripts(PAGE));
  const w = changedWindow(PAGE, out);
  assert.ok(w.removed.length < 200, `the export only differs around the edits: ${JSON.stringify(w.removed)}`);
});

test('entities decode like a browser for the common cases', () => {
  assert.equal(decode('a &amp; b &lt;c&gt; &quot;d&quot; &#39;e&#x27; &nbsp;&copy;&hellip;'), 'a & b <c> "d" \'e\'  ©…');
  assert.equal(decode('&ampx &notanentity; &#0; &#x110000; &#128;'), '&x &notanentity; � � €');
  assert.equal(decode('a\r\nb\rc'), 'a\nb\nc');
});

test('lcs finds the longest common run and handles big inputs', () => {
  assert.deepEqual(lcs(['a', 'b', 'c', 'd'], ['a', 'c', 'd', 'e']), [[0, 0], [2, 1], [3, 2]]);
  const big = Array.from({ length: 3000 }, (_, i) => `k${i}`);
  const shuffled = [...big.slice(0, 1000), 'new', ...big.slice(1000, 2999).reverse()];
  const pairs = lcs(big, shuffled);
  assert.ok(pairs.length >= 1000);
  for (let k = 1; k < pairs.length; k += 1) assert.ok(pairs[k][0] > pairs[k - 1][0] && pairs[k][1] > pairs[k - 1][1]);
});

/* Random edits on the fixture: the export, read back, must show exactly the draft's content, and keep every script
   that was not inside a removed element. */
test('fuzz: random edits round trip to the same content', () => {
  let seed = 42;
  const rnd = (n) => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed % n;
  };
  const base = liteBase(PAGE);
  for (let trial = 0; trial < 300; trial += 1) {
    const t = draftTree(base);
    const body = find(t, (el) => el.tag === 'body');
    for (let k = 0; k < 4; k += 1) {
      const list = [...body.walk()].filter((n) => n.type === 'element' && !['svg', 'path', 'title', 'pre', 'table', 'tbody', 'tr', 'td', 'th', 'style', 'script'].includes(n.tag));
      if (!list.length) break;
      const el = list[rnd(list.length)];
      const op = rnd(6);
      if (op === 0) {
        const text = el.children.find((n) => n.type === 'text' && n.textContent.trim());
        if (text) {
          text.data = `${text.textContent} edit${k} & <ok>`;
        }
      } else if (op === 1) el.setAttribute('class', `c${k}`);
      else if (op === 2 && el.tag !== 'main') el.remove();
      else if (op === 3) {
        const p = t.createElement('p');
        p.textContent = `new ${k}`;
        el.parent.insertBefore(p, el.nextSibling);
      } else if (op === 4) {
        const other = list[rnd(list.length)];
        if (!el.contains(other) && !other.contains(el) && other.tag !== 'li' && el.tag !== 'li') other.parent.insertBefore(el, other);
      } else if (el.tag === 'p') {
        const h = t.createElement('h2');
        h.attrs = el.attrs.map((a) => ({ ...a }));
        while (el.firstChild) h.appendChild(el.firstChild);
        el.replaceWith(h);
      }
    }
    const draft = ser(t);
    const out = exportHtml(PAGE, base, draft);
    noRs(out);
    assert.deepEqual(content(out), content(draft), `trial ${trial}`);
    assert.ok(out.includes('googletagmanager') && out.includes("gtag('js'") && out.includes('/app.js'), 'head and body scripts survive');
  }
});

test('lite nodes cover the DOM subset the blocks code uses', () => {
  const t = parse('<body><h2>a</h2><p>b</p></body>', { strict: true });
  const body = t.elements[0];
  assert.equal(body.firstElementChild.tagName, 'H2');
  assert.equal(body.firstElementChild.nextElementSibling.textContent, 'b');
  assert.ok(body.contains(body.lastChild));
  assert.ok(new LNode('text', { text: 'x' }).nodeType === 3);
  assert.equal(serializeLite(t), '<body><h2>a</h2><p>b</p></body>');
});
