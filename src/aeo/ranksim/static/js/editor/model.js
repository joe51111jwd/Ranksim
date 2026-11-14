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

export function serializeDoc(doc) {
  return `<!doctype html>\n${doc.documentElement.outerHTML}`;
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
  if (!(name in saved)) saved[name] = el.hasAttribute(name) ? el.getAttribute(name) : null;
  el.setAttribute(STASH, JSON.stringify(saved));
  if (value === null) el.removeAttribute(name);
  else el.setAttribute(name, value);
}

export function unstash(root) {
  for (const el of root.querySelectorAll(`[${STASH}]`)) {
    const saved = JSON.parse(el.getAttribute(STASH) || '{}');
    for (const [name, value] of Object.entries(saved)) {
      if (value === null) el.removeAttribute(name);
      else el.setAttribute(name, value);
    }
    el.removeAttribute(STASH);
  }
}

/* The draft held by a live preview document: preview-only nodes removed, stashed attributes restored. */
export function serializeDraft(doc) {
  const root = doc.documentElement.cloneNode(true);
  root.querySelectorAll('[data-rs]').forEach((node) => node.remove());
  root.querySelectorAll('[data-rs-reveal]').forEach((node) => node.removeAttribute('data-rs-reveal'));
  unstash(root);
  return `<!doctype html>\n${root.outerHTML}`;
}

/* HTML mode: the user edits the draft without ids. Parse what they typed, then put the ids back. */
export function draftFromHtml(previousDraft, typed, clean) {
  const doc = new DOMParser().parseFromString(typed, 'text/html');
  if (clean) clean(doc);
  return reattachIds(previousDraft, serializeDoc(doc));
}

export { exportHtml, stripRs };
