/* Browser side of the round trip: builds the base from the fetched source, serializes drafts, and keeps preview-only
   changes out of the draft. Needs DOMParser; the pure parts live in roundtrip.js. */

import { annotate, exportHtml, reattachIds, stripRs, ID } from './roundtrip.js';

/* Every element the parser made up (an implied tbody, a copied formatting element) gets an x id, so the export knows
   its children belong to the parent. Template contents are included. */
export function markSeeThrough(doc) {
  let k = 0;
  const visit = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (!el.hasAttribute(ID)) el.setAttribute(ID, `x${(k += 1)}`);
      if (el.content && el.content.querySelectorAll) visit(el.content);
    }
  };
  visit(doc);
}

/* The page's own doctype, or none: a quirks mode page has to stay in quirks mode when the draft is read again. */
export function doctypeOf(doc) {
  const dt = doc.doctype;
  if (!dt) return '';
  const pub = dt.publicId ? ` PUBLIC "${dt.publicId}"` : '';
  const sys = dt.systemId ? `${dt.publicId ? '' : ' SYSTEM'} "${dt.systemId}"` : '';
  return `<!DOCTYPE ${dt.name}${pub}${sys}>\n`;
}

export function serializeDoc(doc) {
  return `${doctypeOf(doc)}${doc.documentElement.outerHTML}`;
}

/* source -> base. `clean(doc)` removes what the editor never shows (scripts, iframes) and settles streamed content. */
export function makeBase(source, clean) {
  const doc = new DOMParser().parseFromString(annotate(source), 'text/html');
  if (clean) clean(doc);
  markSeeThrough(doc);
  return serializeDoc(doc);
}

/* Preview-only changes (a base href, a preload link turned off, a lazy image given its src) are stashed so the draft
   never counts them as edits. stash() records the attribute's original value; unstash() puts it back. */
const STASH = 'data-rs-a';

export function stash(el, name, value) {
  const saved = JSON.parse(el.getAttribute(STASH) || '{}');
  if (!saved['\u0000order']) saved['\u0000order'] = Array.from(el.attributes).map((a) => a.name);
  if (!(name in saved)) saved[name] = el.hasAttribute(name) ? el.getAttribute(name) : null;
  el.setAttribute(STASH, JSON.stringify(saved));
  if (value === null) el.removeAttribute(name);
  else el.setAttribute(name, value);
}

/* Puts stashed attributes back, in their original order, so an untouched element serializes exactly as before. */
export function unstash(root) {
  for (const el of root.querySelectorAll(`[${STASH}]`)) {
    const saved = JSON.parse(el.getAttribute(STASH) || '{}');
    const order = saved['\u0000order'] || [];
    delete saved['\u0000order'];
    el.removeAttribute(STASH);
    for (const [name, value] of Object.entries(saved)) {
      if (value === null) el.removeAttribute(name);
      else el.setAttribute(name, value);
    }
    const now = Array.from(el.attributes).map((a) => [a.name, a.value]);
    const rank = new Map(order.map((name, k) => [name, k]));
    const sorted = now.slice().sort((p, q) => (rank.has(p[0]) ? rank.get(p[0]) : 1e6) - (rank.has(q[0]) ? rank.get(q[0]) : 1e6));
    if (sorted.some((pair, k) => pair[0] !== now[k][0])) {
      for (const [name] of now) el.removeAttribute(name);
      for (const [name, value] of sorted) el.setAttribute(name, value);
    }
  }
}

/* Attributes the draft keeps: the source ids and the inserted FAQ markers. Every other data-rs-* attribute belongs to
   the preview or a plugin and is dropped. */
const KEEP = new Set([ID, 'data-rs-faq', 'data-rs-faq-ld']);

/* The draft held by a live preview document (or any parsed copy): preview-only nodes removed, stashed attributes
   restored, plugin attributes dropped, ids kept. */
export function serializeDraft(doc) {
  const root = doc.documentElement.cloneNode(true);
  // Nodes the preview or a plugin added carry data-rs and no id; a page's own element that happens to use a data-rs
  // attribute has an id, and stays.
  root.querySelectorAll(`[data-rs]:not([${ID}])`).forEach((node) => node.remove());
  unstash(root);
  for (const el of [root, ...root.querySelectorAll('*')]) {
    for (const attr of Array.from(el.attributes)) if (attr.name.startsWith('data-rs-') && !KEEP.has(attr.name)) el.removeAttribute(attr.name);
  }
  return `${doctypeOf(doc)}${root.outerHTML}`;
}

/* HTML mode: the user edits the draft without ids. Parse what they typed, then put the ids back. */
export function draftFromHtml(previousDraft, typed, clean) {
  const doc = new DOMParser().parseFromString(typed, 'text/html');
  if (clean) clean(doc);
  return reattachIds(previousDraft, serializeDoc(doc));
}

export { exportHtml, stripRs };
