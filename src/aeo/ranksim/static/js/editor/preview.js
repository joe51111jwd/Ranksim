/* The editable preview: how a page is cleaned, shown, and turned back into HTML. Pure functions, no app state.
   A page is parsed once, unsafe parts removed, and serialized the same way every time, so an unchanged page tests
   exactly like the original. Scripts never run in the preview. */

import { esc } from '../core.js';
import { doctypeOf, stash, unstash } from './model.js';

export const READER_CSS = `
:root{color-scheme:light}
html{background:#fff}
body{margin:0 auto;max-width:760px;padding:48px 32px 64px;font:17px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;color:#1b1b19;-webkit-font-smoothing:antialiased}
h1{font-size:38px;line-height:1.12;letter-spacing:-.025em;margin:0 0 14px;font-weight:700}
h2{font-size:25px;line-height:1.25;letter-spacing:-.015em;margin:40px 0 10px;font-weight:700}
h3{font-size:19px;margin:28px 0 6px;font-weight:650}
p{margin:0 0 16px;color:#333330}
time{display:inline-block;color:#8a8a84;font-size:14px;margin-bottom:6px}
a{color:#3358eb;text-decoration:none}
ul,ol{padding-left:24px;margin:0 0 16px}
table{width:100%;border-collapse:collapse;margin:24px 0;font-size:15px}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #e6e6e0}
th{font-weight:600;background:#f6f6f3}
nav{margin-top:40px;padding-top:20px;border-top:1px solid #e6e6e0;font-size:14px}
nav a{display:inline-block;margin:0 14px 8px 0}
img,video{max-width:100%;height:auto;border-radius:10px}
`;
export const EDIT_CSS = 'body{outline:none;caret-color:#3358eb}h1:hover,h2:hover,h3:hover,p:hover,li:hover,td:hover,th:hover{outline:1px dashed rgba(51,88,235,.4);outline-offset:3px;border-radius:3px}html{scrollbar-width:thin}';

export function asHtml(text) {
  if (/<\s*(html|body|p|h1|h2|div|title|article|section)\b/i.test(text)) return text;
  const blocks = text.split(/\n\s*\n/).map((block) => block.trim()).filter(Boolean);
  if (!blocks.length) return text;
  const lines = blocks[0].split('\n');
  const first = lines[0].trim().slice(0, 160);
  const restLine = lines.slice(1).join(' ').trim();
  const rest = lines.length > 1 && restLine ? [restLine] : [];
  const paras = rest.concat(blocks.slice(1)).map((block) => `<p>${esc(block.split(/\s+/).join(' '))}</p>`);
  return `<html><head><title>${esc(first)}</title></head><body><h1>${esc(first)}</h1>${paras.join('')}</body></html>`;
}

/* A page as the editor holds it: parsed once, unsafe parts removed, serialized the same way every time, so an
   unchanged page tests exactly like the original. */
export function cleanDoc(text) {
  return cleanParsed(new DOMParser().parseFromString(asHtml(text || ''), 'text/html'));
}

/* The cleaning itself, on a parsed document. Structured data (JSON-LD) stays: it is data, never run, and it is part
   of what Google reads. The preview's sandbox has no allow-scripts, so nothing in it ever runs. */
export function cleanParsed(doc) {
  doc.querySelectorAll('script:not([type="application/ld+json" i]),noscript,iframe,frame,frameset,object,embed,base,meta[http-equiv]').forEach((node) => node.remove());
  settleStreamedContent(doc);
  for (const el of doc.querySelectorAll('*')) {
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      if (name.startsWith('on') || name === 'contenteditable' || (/^(href|src|action|formaction|xlink:href|srcdoc)$/.test(name) && /^\s*(javascript|data:text\/html)/i.test(attr.value))) el.removeAttribute(attr.name);
    }
  }
  return doc;
}

/* React streaming (Next.js and others) sends a loading placeholder first and the real content later inside
   <div hidden id="S:n">, then a script swaps it into the <template id="B:n"> boundary. Scripts never run here, so
   do the swap: the page then reads the way a browser shows it. */
export function settleStreamedContent(doc) {
  for (const hidden of Array.from(doc.querySelectorAll('div[hidden][id^="S:"]'))) {
    const slot = doc.getElementById(`P:${hidden.id.slice(2)}`); // a segment: replaces its placeholder
    if (slot && slot.parentNode) {
      while (hidden.firstChild) slot.parentNode.insertBefore(hidden.firstChild, slot);
      slot.remove();
      hidden.remove();
      continue;
    }
    const boundary = doc.getElementById(`B:${hidden.id.slice(2)}`); // a boundary: replaces its fallback
    if (!boundary || !boundary.parentNode) continue;
    const parent = boundary.parentNode;
    let node = boundary.nextSibling;
    let depth = 0;
    while (node) {
      const next = node.nextSibling;
      if (node.nodeType === 8) {
        if (node.data === '/$') {
          if (depth === 0) break;
          depth -= 1;
        } else if (node.data.startsWith('$')) {
          depth += 1;
        }
      }
      node.remove(); // the loading placeholder
      node = next;
    }
    while (hidden.firstChild) parent.insertBefore(hidden.firstChild, node);
    boundary.remove();
    hidden.remove();
  }
}

/* The page as HTML. Everything the editor or a plugin adds to the preview is left out: nodes marked data-rs and
   every data-rs-* attribute. */
export function serialize(doc) {
  const root = doc.documentElement.cloneNode(true);
  root.querySelectorAll('[data-rs]').forEach((node) => node.remove());
  unstash(root);
  for (const el of [root, ...root.querySelectorAll('*')]) {
    for (const attr of Array.from(el.attributes)) {
      if (attr.name.startsWith('data-rs-')) el.removeAttribute(attr.name);
    }
  }
  return `<!doctype html>\n${root.outerHTML}`;
}

export const normalize = (text) => serialize(cleanDoc(text));

/* Pages built for scripts often hide content until a script reveals it. The preview shows it anyway. */
export const REVEAL_CSS = 'html,body{opacity:1!important;visibility:visible!important}[style*="opacity:0"],[style*="opacity: 0"]{opacity:1!important;transform:none!important}[style*="visibility:hidden"],[style*="visibility: hidden"]{visibility:visible!important}';

/* Pages drawn by scripts send search engines a hidden copy of their text. When the preview comes out empty, that
   copy is shown as a plain page: it is exactly what Google and Ranksim read. */
export const HIDDEN_CSS = `[data-rs-reveal]{position:static!important;clip:auto!important;clip-path:none!important;width:auto!important;height:auto!important;max-width:760px!important;margin:0 auto!important;padding:48px 32px 64px!important;overflow:visible!important;white-space:normal!important;display:block!important;opacity:1!important;visibility:visible!important;transform:none!important;background:#fff!important;font:17px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif!important;color:#1b1b19!important;text-align:left!important}
[data-rs-reveal] *{white-space:normal!important}
[data-rs-reveal] h1{font-size:38px!important;line-height:1.12!important;letter-spacing:-.025em;margin:0 0 14px!important;font-weight:700!important}
[data-rs-reveal] h2{font-size:25px!important;line-height:1.25!important;margin:40px 0 10px!important;font-weight:700!important}
[data-rs-reveal] h3{font-size:19px!important;margin:28px 0 6px!important;font-weight:650!important}
[data-rs-reveal] p{margin:0 0 16px!important;color:#333330!important}
[data-rs-reveal] a{color:#3358eb!important;text-decoration:none!important}
[data-rs-reveal] ul,[data-rs-reveal] ol{padding-left:24px!important;margin:0 0 16px!important;list-style:revert!important}
[data-rs-reveal] summary{cursor:pointer;color:#555550;margin:24px 0 8px}
html:has([data-rs-reveal]),body:has([data-rs-reveal]){background:#fff!important;height:auto!important;overflow:auto!important}`;

export function revealHidden(doc) {
  const win = doc.defaultView;
  if (!win || !doc.body) return false;
  const text = (doc.body.textContent || '').trim().length;
  if (text < 120 || doc.body.scrollHeight > 160) return false; // it shows something already
  let best = null;
  let most = 0;
  for (const el of doc.body.querySelectorAll('article,main,section,div')) {
    const size = (el.textContent || '').trim().length;
    if (size < most || size < 80) continue;
    const box = el.getBoundingClientRect();
    const cs = win.getComputedStyle(el);
    const hidden = box.width <= 2 || box.height <= 2 || (cs.clip && cs.clip !== 'auto') || (cs.clipPath && cs.clipPath !== 'none') || cs.display === 'none' || cs.visibility === 'hidden';
    if (hidden && size >= most) {
      best = el;
      most = size;
    }
  }
  if (!best) return false;
  best.setAttribute('data-rs-reveal', '');
  for (let up = best.parentElement; up && up !== doc.body; up = up.parentElement) {
    const cs = win.getComputedStyle(up);
    if (cs.display === 'none') up.style.setProperty('display', 'block', 'important');
  }
  const style = doc.createElement('style');
  style.setAttribute('data-rs', '1');
  style.textContent = HIDDEN_CSS;
  doc.head.append(style);
  return true;
}

export function frameSource(text, url) {
  const doc = cleanDoc(text);
  const head = doc.head || doc.documentElement;
  if (url && /^https?:\/\//i.test(url)) {
    const base = doc.createElement('base');
    base.setAttribute('href', url);
    base.setAttribute('data-rs', '1');
    head.prepend(base); // the page's own CSS, images and fonts load from its own site
  }
  for (const link of doc.querySelectorAll('link')) {
    const rel = (link.getAttribute('rel') || '').toLowerCase();
    const as = (link.getAttribute('as') || '').toLowerCase();
    // Preview-only changes are stashed (model.js), so the draft puts the page's own values back when it is saved.
    if (/modulepreload|prefetch|prerender/.test(rel) || (rel.includes('preload') && as !== 'style')) {
      stash(link, 'rel', 'rs-off'); // scripts never run in the preview, so there is nothing to preload
      continue;
    }
    if (rel.includes('preload') && as === 'style') stash(link, 'rel', 'stylesheet'); // the onload swap was removed
    if (link.hasAttribute('crossorigin')) stash(link, 'crossorigin', null); // a CORS fetch fails without CORS headers
    if (link.hasAttribute('integrity')) stash(link, 'integrity', null);
  }
  for (const img of doc.querySelectorAll('img[data-src], img[data-lazy-src], source[data-srcset], img[data-srcset]')) {
    const src = img.getAttribute('data-src') || img.getAttribute('data-lazy-src');
    if (src && !img.getAttribute('src')) stash(img, 'src', src);
    const set = img.getAttribute('data-srcset');
    if (set && !img.getAttribute('srcset')) stash(img, 'srcset', set);
  }
  const styled = doc.querySelector('style, link[rel~="stylesheet"]');
  const style = doc.createElement('style');
  style.setAttribute('data-rs', '1');
  style.textContent = (styled ? '' : READER_CSS) + REVEAL_CSS + EDIT_CSS;
  head.append(style);
  return `${doctypeOf(doc) || '<!doctype html>'}${doc.documentElement.outerHTML}`;
}

export function facts(text) {
  const doc = cleanDoc(text);
  const body = doc.body ? doc.body.cloneNode(true) : null;
  if (body) body.querySelectorAll('style,template').forEach((node) => node.remove());
  const words = body ? (body.textContent.match(/[A-Za-z0-9À-ɏ'’]+/g) || []).length : 0;
  const meta = doc.querySelector('meta[name="description" i]');
  return {
    title: ((doc.querySelector('title') || {}).textContent || '').trim(),
    h1: ((doc.querySelector('h1') || {}).textContent || '').trim(),
    meta: meta ? (meta.getAttribute('content') || '').trim() : '',
    words,
    headings: doc.querySelectorAll('h2,h3,h4').length,
  };
}

/* The outline on an element picked from outside the preview (an issue, an outline row). Never saved: serialize()
   drops the attribute and the style. */
export const PICK_CSS = '[data-rs-pick]{outline:2px solid #3358eb!important;outline-offset:3px;border-radius:3px}';

export function pick(doc, el) {
  if (!doc) return;
  doc.querySelectorAll('[data-rs-pick]').forEach((node) => node.removeAttribute('data-rs-pick'));
  if (!el) return;
  if (!doc.querySelector('style[data-rs-pick-style]')) {
    const style = doc.createElement('style');
    style.setAttribute('data-rs', '1');
    style.setAttribute('data-rs-pick-style', '');
    style.textContent = PICK_CSS;
    (doc.head || doc.documentElement).append(style);
  }
  el.setAttribute('data-rs-pick', '');
}
