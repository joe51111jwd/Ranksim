// Test helpers: a lite stand-in for the browser half of the round trip (parse, clean, serialize).
import { annotate, parse, serializeLite } from '../../../src/aeo/ranksim/static/js/editor/roundtrip.js';

export const PAGE = `<!DOCTYPE html>
<html lang='en'>
<head>
  <meta charset=utf-8>
  <title>Heavy civil software &amp; dirt work | Acme</title>
  <!-- analytics: keep -->
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-1"></script>
  <script>window.dataLayer=window.dataLayer||[];if(a<b&&c>d){gtag('js',new Date())}</script>
  <link rel=stylesheet href="/app.css" crossorigin>
  <style>body{margin:0} p>a{color:red}</style>
  <script type="application/ld+json">{"@type":"Organization","name":"Acme"}</script>
</head>
<body class="home"   data-x=1>
  <header><nav><a href="/" onclick="track('logo')">Acme</a> <a href=/pricing>Pricing</a></nav></header>
  <main id=main>
    <h1 class='hero'>All in one heavy civil software</h1>
    <p>Run crews, equipment &amp; dirt work in one place.&nbsp;Built for contractors &copy; 2026.</p>
    <script>document.write('<p>widget</p>')</script>
    <h2 id="why">Why Acme</h2>
    <p>Fast <strong>setup</strong>, fair <em>pricing</em>.</p>
    <ul>
      <li>Daily logs</li>
      <li>Time cards</li>
    </ul>
    <h2 id="compare">Compare</h2>
    <table class=grid>
      <tbody>
        <tr><th>Tool</th><th>Price</th></tr>
        <tr><td>Acme</td><td>$99</td></tr>
      </tbody>
    </table>
    <noscript><img src="/pixel.gif" alt=""></noscript>
    <pre>
  indented code &lt;here&gt;
</pre>
    <svg viewBox="0 0 10 10" class=icon><path d="M0 0L10 10"/><title>Icon</title></svg>
    <img src="/hero.png" alt="Crew on site" width=600>
  </main>
  <footer><p>Contact <a href="mailto:hi@acme.test">us</a></p></footer>
  <script src="/app.js" defer></script>
  <iframe src="https://example.com/embed"></iframe>
</body>
</html>
`;

const HIDE = new Set(['script', 'noscript', 'iframe', 'object', 'embed']);

/* What the browser side does: annotate, parse, drop scripts (but not JSON-LD) and event handlers, serialize.
   Valid for well formed pages where the browser invents no elements. */
export function liteBase(source) {
  const tree = parse(annotate(source), { strict: true });
  dropHidden(tree);
  return serializeLite(tree);
}

function dropHidden(node) {
  for (const child of [...node.children]) {
    if (child.type !== 'element') continue;
    if (HIDE.has(child.tag) && !(child.tag === 'script' && /ld\+json/.test(child.getAttribute('type') || ''))) {
      child.remove();
      continue;
    }
    for (const a of [...(child.attrs || [])]) if (a.name.startsWith('on')) child.removeAttribute(a.name);
    dropHidden(child);
  }
}

/* A draft tree to edit, and its serialization. */
export const draftTree = (base) => parse(base, { strict: true });
export const ser = (tree) => serializeLite(tree);
export const byId = (tree, id) => tree.elements.find((el) => el.rsid === String(id));
export const find = (tree, test) => tree.elements.find(test);

/* Content of a page as the editor sees it, for comparing an export with a draft: tags, attributes without data-rs,
   text with whitespace collapsed. */
export function content(html) {
  const tree = parse(html, { strict: true });
  dropHidden(tree);
  const out = [];
  const walk = (node) => {
    for (const c of node.children) {
      if (c.type === 'text') {
        const t = c.textContent.replace(/\s+/g, ' ').trim();
        if (!t) continue;
        if (out.length && out[out.length - 1].startsWith('T:')) out[out.length - 1] += ` ${t}`;
        else out.push(`T:${t}`);
      } else if (c.type === 'element') {
        const attrs = c.attrs.filter((a) => !a.name.startsWith('data-rs')).map((a) => `${a.name}=${a.value}`).sort().join(' ');
        out.push(`<${c.tag} ${attrs}>`);
        walk(c);
        out.push(`</${c.tag}>`);
      }
    }
  };
  walk(tree);
  return out;
}
