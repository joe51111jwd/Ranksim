/* Blocks the user can insert, the heading outline, moving a section, and switching a block between paragraph and
   headings. Works on the DOM, and on roundtrip.js lite nodes in tests (only the shared subset of the DOM API is used).
   Inserted markup is plain and unstyled, so it takes the page's own styles. */

import { parse } from './roundtrip.js';

const esc = (text) => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export const BLOCKS = [
  { key: 'section', label: 'Section', note: 'A heading and a paragraph' },
  { key: 'paragraph', label: 'Paragraph', note: 'A line of text' },
  { key: 'list', label: 'List', note: 'Bullet points' },
  { key: 'table', label: 'Table', note: 'Rows and columns' },
  { key: 'faq', label: 'FAQ', note: 'Questions and answers' },
  { key: 'image', label: 'Image', note: 'A picture with alt text' },
];

export const DEFAULT_FAQ = [
  { q: 'What is the first question people ask?', a: 'Answer it in one or two plain sentences.' },
  { q: 'What is the second question?', a: 'Keep each answer short and specific.' },
  { q: 'What is the third question?', a: 'Use the words people search for.' },
];

let faqSeq = 0;

/* Markup for one block. Options: table {rows, cols}; faq {pairs, jsonld}; image {src, alt, caption}. */
export function blockHtml(key, opts = {}) {
  if (key === 'section') return `<h2>${esc(opts.heading || 'New section')}</h2><p>${esc(opts.text || 'Write this section here.')}</p>`;
  if (key === 'paragraph') return `<p>${esc(opts.text || 'New paragraph. Click to edit it.')}</p>`;
  if (key === 'list') {
    const items = opts.items && opts.items.length ? opts.items : ['First point', 'Second point', 'Third point'];
    return `<ul>${items.map((item) => `<li>${esc(item)}</li>`).join('')}</ul>`;
  }
  if (key === 'table') {
    const rows = Math.max(1, Math.min(50, Number(opts.rows) || 3));
    const cols = Math.max(1, Math.min(12, Number(opts.cols) || 3));
    const head = Array.from({ length: cols }, (_, c) => `<th>Column ${c + 1}</th>`).join('');
    const body = Array.from({ length: rows }, () => `<tr>${Array.from({ length: cols }, () => '<td>Value</td>').join('')}</tr>`).join('');
    return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  }
  if (key === 'faq') {
    const pairs = opts.pairs && opts.pairs.length ? opts.pairs : DEFAULT_FAQ;
    const tag = opts.key || `f${Date.now().toString(36)}${(faqSeq += 1)}`;
    const items = pairs.map((pair) => `<h3>${esc(pair.q)}</h3><p>${esc(pair.a)}</p>`).join('');
    const html = `<section data-rs-faq="${tag}"><h2>${esc(opts.heading || 'Frequently asked questions')}</h2>${items}</section>`;
    return opts.jsonld ? `${html}<script type="application/ld+json" data-rs-faq-ld="${tag}">${faqJsonLd(pairs)}</script>` : html;
  }
  if (key === 'image') {
    const src = String(opts.src || '').trim();
    const img = `<img src="${esc(src)}" alt="${esc(opts.alt || '')}"${opts.width ? ` width="${Number(opts.width) | 0}"` : ''}${opts.height ? ` height="${Number(opts.height) | 0}"` : ''} loading="lazy">`;
    return opts.caption ? `<figure>${img}<figcaption>${esc(opts.caption)}</figcaption></figure>` : `<figure>${img}</figure>`;
  }
  throw new Error(`Unknown block: ${key}`);
}

/* An image address the editor accepts: http or https only, so a pasted data: or javascript: URL never lands. */
export function imageUrlOk(url) {
  return /^https?:\/\/[^\s"'<>]+$/i.test(String(url || '').trim());
}

/* FAQPage structured data for Google. `<` is escaped so the text can never close the script element. */
export function faqJsonLd(pairs) {
  const data = {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: pairs.filter((pair) => pair.q && pair.a).map((pair) => ({
      '@type': 'Question',
      name: pair.q,
      acceptedAnswer: { '@type': 'Answer', text: pair.a },
    })),
  };
  return JSON.stringify(data, null, 2).replace(/</g, '\\u003c');
}

const QUESTION = new Set(['H3', 'H4', 'H5', 'DT', 'SUMMARY']);
const squash = (text) => String(text || '').replace(/\s+/g, ' ').trim();

/* Question and answer pairs in an FAQ block: each h3 (or h4, dt, summary) is a question; the text after it, up to the
   next question, is the answer. */
export function faqPairs(section) {
  const pairs = [];
  let current = null;
  const visit = (node) => {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType !== 1) continue;
      if (QUESTION.has(child.tagName)) {
        current = { q: squash(child.textContent), a: '' };
        pairs.push(current);
      } else if (child.tagName === 'DETAILS' || child.tagName === 'DL' || child.tagName === 'DIV') {
        visit(child);
      } else if (current && !/^H[12]$/.test(child.tagName) && child.tagName !== 'SCRIPT') {
        current.a = squash(`${current.a} ${child.textContent}`);
      }
    }
  };
  visit(section);
  return pairs.filter((pair) => pair.q);
}

/* Keeps every inserted FAQ's JSON-LD in step with its questions. Call before the draft is serialized. Returns true
   when a script changed. */
export function syncFaq(doc) {
  let changed = false;
  for (const script of all(doc, (el) => el.tagName === 'SCRIPT' && el.getAttribute('data-rs-faq-ld') !== null)) {
    const key = script.getAttribute('data-rs-faq-ld');
    const section = first(doc, (el) => el.getAttribute('data-rs-faq') === key);
    if (!section) {
      script.remove(); // the FAQ was deleted; its structured data goes with it
      changed = true;
      continue;
    }
    const text = faqJsonLd(faqPairs(section));
    if (script.textContent !== text) {
      script.textContent = text;
      changed = true;
    }
  }
  return changed;
}

/* ------------------------------------------------------------------ tree helpers */

function* elements(root) {
  const stack = [...Array.from(root.childNodes)].reverse();
  while (stack.length) {
    const node = stack.pop();
    if (node.nodeType !== 1) continue;
    yield node;
    const kids = Array.from(node.childNodes);
    for (let k = kids.length - 1; k >= 0; k -= 1) stack.push(kids[k]);
  }
}

function all(root, test) {
  const out = [];
  for (const el of elements(root)) if (test(el)) out.push(el);
  return out;
}

function first(root, test) {
  for (const el of elements(root)) if (test(el)) return el;
  return null;
}

const levelOf = (el) => (el && el.nodeType === 1 && /^H[1-6]$/.test(el.tagName) ? Number(el.tagName[1]) : 0);
const SKIP_OUTLINE = new Set(['SVG', 'TEMPLATE', 'SCRIPT', 'STYLE', 'NOSCRIPT']);

function bodyOf(doc) {
  if (doc.body) return doc.body;
  return first(doc, (el) => el.tagName === 'BODY') || doc;
}

/* The heading tree: [{el, level, text, depth, children}]. Empty headings and headings in hidden parts (svg, template)
   are left out. */
export function outline(doc) {
  const flat = [];
  const visit = (node) => {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType !== 1 || SKIP_OUTLINE.has(child.tagName)) continue;
      const level = levelOf(child);
      if (level) {
        const text = squash(child.textContent);
        if (text) flat.push({ el: child, level, text, depth: 0, children: [] });
        continue;
      }
      visit(child);
    }
  };
  visit(bodyOf(doc));
  const roots = [];
  const stack = [];
  for (const item of flat) {
    while (stack.length && stack[stack.length - 1].level >= item.level) stack.pop();
    item.depth = stack.length;
    if (stack.length) stack[stack.length - 1].children.push(item);
    else roots.push(item);
    stack.push(item);
  }
  return { roots, flat };
}

function hasHeadingAtMost(el, level) {
  if (levelOf(el) && levelOf(el) <= level) return true;
  return first(el, (node) => levelOf(node) > 0 && levelOf(node) <= level) !== null;
}

/* The nodes that make up a heading's section: the heading and what follows it up to the next heading of the same or
   a higher level. When the heading opens its own <section> or <article> wrapper, the wrapper is the section. */
export function sectionNodes(heading) {
  const level = levelOf(heading);
  const parent = heading.parentNode;
  if (parent && parent.nodeType === 1 && /^(SECTION|ARTICLE)$/.test(parent.tagName)) {
    const firstEl = Array.from(parent.childNodes).find((node) => node.nodeType === 1);
    const others = all(parent, (node) => node !== heading && levelOf(node) > 0 && levelOf(node) <= level);
    if (firstEl === heading && !others.length) return [parent];
  }
  const nodes = [heading];
  for (let node = heading.nextSibling; node; node = node.nextSibling) {
    if (node.nodeType === 1 && hasHeadingAtMost(node, level)) break;
    nodes.push(node);
  }
  while (nodes.length > 1 && nodes[nodes.length - 1].nodeType === 3 && !nodes[nodes.length - 1].data.trim()) nodes.pop();
  return nodes;
}

/* Moves the section of `heading` before (or after) the section of `target`. Returns false when that makes no sense
   (moving a section into itself). */
export function moveSection(heading, target, place = 'before') {
  if (!heading || !target || heading === target) return false;
  const nodes = sectionNodes(heading);
  const goal = sectionNodes(target);
  if (nodes.some((node) => node === goal[0] || node.contains(goal[0]))) return false;
  const anchor = place === 'after' ? goal[goal.length - 1] : goal[0];
  const parent = anchor.parentNode;
  const ref = place === 'after' ? anchor.nextSibling : anchor;
  for (const node of nodes) parent.insertBefore(node, ref);
  return true;
}

export const SWITCHABLE = ['p', 'h2', 'h3'];
const TEXT_BLOCK = new Set(['P', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'DIV']);

/* The block the element toolbar acts on: the nearest paragraph or heading around a node. */
export function textBlockOf(node) {
  for (let el = node && node.nodeType === 1 ? node : node && node.parentNode; el && el.nodeType === 1; el = el.parentNode) {
    if (el.tagName === 'BODY' || el.tagName === 'HTML') return null;
    if (TEXT_BLOCK.has(el.tagName) && (el.tagName !== 'DIV' || !first(el, (inner) => /^(P|H[1-6]|DIV|UL|OL|TABLE|SECTION)$/.test(inner.tagName)))) return el;
  }
  return null;
}

/* Switches a block to another tag (p, h2, h3), keeping its attributes (and so its place in the original page) and
   its contents. Returns the new element. */
export function convertBlock(el, tag) {
  const name = String(tag).toLowerCase();
  if (!SWITCHABLE.includes(name) || el.tagName.toLowerCase() === name) return el;
  const next = el.ownerDocument.createElement(name);
  for (const attr of Array.from(el.attributes)) next.setAttribute(attr.name, attr.value);
  while (el.firstChild) next.appendChild(el.firstChild);
  el.replaceWith(next);
  return next;
}

/* Where an inserted block goes: after the block around the selection, never inside a list item, table or heading. */
export function insertionPoint(node) {
  let el = node && node.nodeType === 1 ? node : node && node.parentNode;
  let best = null;
  for (; el && el.nodeType === 1; el = el.parentNode) {
    if (el.tagName === 'BODY' || el.tagName === 'HTML') break;
    if (/^(P|H[1-6]|UL|OL|DL|TABLE|FIGURE|BLOCKQUOTE|PRE|SECTION|ARTICLE|DETAILS)$/.test(el.tagName)) best = el;
    if (/^(SECTION|ARTICLE|MAIN)$/.test(el.tagName)) break;
  }
  return best;
}

/* Parses block markup into nodes owned by `doc` (a DOM document or a lite root). */
export function fragmentNodes(doc, html) {
  if (doc && typeof doc.createElement === 'function' && doc.nodeType === 9 && doc.defaultView !== undefined) {
    const holder = doc.createElement('template');
    holder.innerHTML = html;
    return Array.from(holder.content.childNodes).map((node) => doc.importNode ? doc.importNode(node, true) : node);
  }
  return [...parse(html).children];
}

/* Inserts block markup after `ref` (or at the end of the body). Returns the inserted nodes. */
export function insertBlock(doc, html, ref) {
  const nodes = fragmentNodes(doc, html);
  const parent = ref ? ref.parentNode : bodyOf(doc);
  const before = ref ? ref.nextSibling : null;
  for (const node of nodes) parent.insertBefore(node, before);
  return nodes;
}
