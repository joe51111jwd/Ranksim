/* What changed, block by block, in words a client can read: "Heading changed", "Paragraph added". Compares the base
   (the page as fetched, cleaned) with the draft. Blocks keep the data-rs-id of their element, so an edited paragraph
   is matched to itself even when its text changed completely. Pure. */

import { parse, lcs, longestIncreasing, decode, isSpace } from './roundtrip.js';

const INLINE = new Set(['a', 'abbr', 'b', 'bdi', 'bdo', 'br', 'cite', 'code', 'data', 'dfn', 'em', 'font', 'i', 'kbd', 'label', 'mark', 'q', 'rp', 'rt', 'ruby', 's', 'samp', 'small', 'span', 'strong', 'sub', 'sup', 'time', 'u', 'var', 'wbr', 'del', 'ins', 'big', 'tt', 'nobr', 'output']);
const SKIP = new Set(['script', 'style', 'template', 'noscript', 'svg', 'math', 'head', 'iframe', 'object', 'select', 'textarea', 'canvas']);

const KIND = {
  h1: 'Main heading', h2: 'Heading', h3: 'Subheading', h4: 'Subheading', h5: 'Subheading', h6: 'Subheading',
  p: 'Paragraph', li: 'List item', td: 'Table cell', th: 'Table heading', blockquote: 'Quote', pre: 'Code block',
  dt: 'Term', dd: 'Definition', figcaption: 'Caption', summary: 'Question', caption: 'Table caption', button: 'Button',
  img: 'Image', title: 'Title', description: 'Description', legend: 'Label', address: 'Address',
};

export function kindLabel(tag) {
  return KIND[tag] || 'Text';
}

const squash = (text) => text.replace(/[ \t\n\f\r]+/g, ' ').trim();

/* The readable blocks of a page, in order: title and description first, then every element that directly holds
   text (its nearest non-inline ancestor), and images. */
export function blocksOf(html) {
  const tree = parse(html, { strict: true });
  const blocks = [];
  const byEl = new Map();
  let title = null;
  let description = null;
  for (const el of tree.elements) {
    if (el.tag === 'title' && !el.foreign && title === null) title = el;
    if (el.tag === 'meta' && (el.getAttribute('name') || '').toLowerCase() === 'description' && description === null) description = el;
  }
  if (title) blocks.push({ key: 'title', tag: 'title', kind: KIND.title, text: squash(decode(title.textContent)) });
  if (description) blocks.push({ key: 'description', tag: 'description', kind: KIND.description, text: squash(description.getAttribute('content') || '') });
  const body = tree.elements.find((el) => el.tag === 'body') || tree;
  const visit = (node, block) => {
    for (const child of node.children) {
      if (child.type === 'text') {
        if (!block || isSpace(child.text)) continue;
        let entry = byEl.get(block);
        if (!entry) {
          entry = { key: keyOf(block), tag: block.tag, kind: kindLabel(block.tag), parts: [] };
          byEl.set(block, entry);
          blocks.push(entry);
        }
        entry.parts.push(decode(child.text));
      } else if (child.type === 'element') {
        if (SKIP.has(child.tag)) continue;
        if (child.tag === 'img') {
          const alt = child.getAttribute('alt');
          const src = child.getAttribute('src') || '';
          blocks.push({ key: keyOf(child), tag: 'img', kind: KIND.img, text: alt ? squash(alt) : '', src, alt: alt === null ? null : alt });
          continue;
        }
        if (child.tag === 'br' && block && byEl.has(block)) byEl.get(block).parts.push(' ');
        visit(child, INLINE.has(child.tag) && block ? block : child);
      }
    }
  };
  visit(body, body);
  return blocks
    .map((b) => (b.parts ? { key: b.key, tag: b.tag, kind: b.kind, text: squash(b.parts.join('')) } : b))
    .filter((b) => b.text || b.tag === 'img' || b.key === 'title' || b.key === 'description');
}

function keyOf(el) {
  const id = el.rsid;
  return id && id[0] !== 'x' ? `#${id}` : null;
}

/* Word level changes inside one block: [{op: '=', text}, {op: '-', text}, {op: '+', text}]. */
export function wordDiff(before, after) {
  const a = before.split(/(\s+)/).filter(Boolean);
  const b = after.split(/(\s+)/).filter(Boolean);
  if (a.length * b.length > 1_000_000) return [{ op: '-', text: before }, { op: '+', text: after }].filter((s) => s.text);
  const pairs = lcs(a, b);
  const out = [];
  const push = (op, text) => {
    if (!text) return;
    const last = out[out.length - 1];
    if (last && last.op === op) last.text += text;
    else out.push({ op, text });
  };
  let x = 0;
  let y = 0;
  for (const [px, py] of [...pairs, [a.length, b.length]]) {
    if (x < px) push('-', a.slice(x, px).join(''));
    if (y < py) push('+', b.slice(y, py).join(''));
    if (px < a.length) push('=', a[px]);
    x = px + 1;
    y = py + 1;
  }
  // a lone space between two changes reads better as part of them
  for (let k = 1; k + 1 < out.length; k += 1) {
    if (out[k].op === '=' && /^\s+$/.test(out[k].text) && out[k - 1].op !== '=' && out[k + 1].op !== '=') {
      out[k] = { op: '-', text: out[k].text };
      out.splice(k + 1, 0, { op: '+', text: out[k].text });
    }
  }
  const merged = [];
  for (let k = 0; k < out.length;) {
    if (out[k].op === '=') { merged.push({ ...out[k] }); k += 1; continue; }
    let gone = '';
    let added = '';
    for (; k < out.length && out[k].op !== '='; k += 1) {
      if (out[k].op === '-') gone += out[k].text;
      else added += out[k].text;
    }
    if (gone) merged.push({ op: '-', text: gone });
    if (added) merged.push({ op: '+', text: added });
  }
  return merged;
}

/* Block changes between two drafts. Returns {changes, counts} where each change is
   {type: 'changed' | 'added' | 'removed' | 'moved', kind, label, before, after, words?}. Unchanged blocks are left out
   unless `all` is set (then they come back as type 'same'). */
export function diffBlocks(baseHtml, draftHtml, { all = false } = {}) {
  const A = blocksOf(baseHtml);
  const B = blocksOf(draftHtml);
  const match = new Map(); // index in B -> index in A
  const usedA = new Set();
  const indexA = new Map();
  A.forEach((block, i) => { if (block.key && !indexA.has(block.key)) indexA.set(block.key, i); });
  B.forEach((block, j) => {
    const i = block.key ? indexA.get(block.key) : undefined;
    if (i !== undefined && !usedA.has(i)) { match.set(j, i); usedA.add(i); }
  });
  // blocks with no key on one side (new wrappers, retyped HTML): match equal text in order
  const restA = A.map((block, i) => i).filter((i) => !usedA.has(i));
  const restB = B.map((block, j) => j).filter((j) => !match.has(j));
  for (const [x, y] of lcs(restA.map((i) => `${A[i].tag}|${A[i].text}`), restB.map((j) => `${B[j].tag}|${B[j].text}`))) {
    match.set(restB[y], restA[x]);
    usedA.add(restA[x]);
  }
  const ordered = [...match.entries()].sort((p, q) => p[0] - q[0]).map(([j, i]) => [i, j]).sort((p, q) => p[0] - q[0]);
  const stay = new Set(longestIncreasing(ordered.map(([i, j]) => [i, j])).map(([i]) => i));
  const changes = [];
  const counts = { changed: 0, added: 0, removed: 0, moved: 0 };
  const removedAfter = new Map(); // removed blocks are listed after the block that preceded them
  let lastMatchedA = -1;
  A.forEach((block, i) => {
    if (usedA.has(i)) { lastMatchedA = i; return; }
    if (!removedAfter.has(lastMatchedA)) removedAfter.set(lastMatchedA, []);
    removedAfter.get(lastMatchedA).push(i);
  });
  const emitRemoved = (anchor) => {
    for (const i of removedAfter.get(anchor) || []) {
      const block = A[i];
      counts.removed += 1;
      changes.push({ type: 'removed', kind: block.kind, tag: block.tag, label: `${block.kind} removed`, before: block.text, after: '' });
    }
    removedAfter.delete(anchor);
  };
  emitRemoved(-1);
  B.forEach((block, j) => {
    const i = match.get(j);
    if (i === undefined) {
      counts.added += 1;
      changes.push({ type: 'added', kind: block.kind, tag: block.tag, label: `${block.kind} added`, before: '', after: block.text });
      return;
    }
    const old = A[i];
    const moved = !stay.has(i);
    const textChanged = old.text !== block.text || old.tag !== block.tag || (block.tag === 'img' && old.src !== block.src);
    if (textChanged) {
      counts.changed += 1;
      const label = old.tag !== block.tag ? `${old.kind} is now a ${block.kind.toLowerCase()}` : `${block.kind} changed`;
      changes.push({ type: 'changed', kind: block.kind, tag: block.tag, label: moved ? `${label} and moved` : label, before: old.text, after: block.text, moved, words: wordDiff(old.text, block.text) });
    } else if (moved) {
      counts.moved += 1;
      changes.push({ type: 'moved', kind: block.kind, tag: block.tag, label: `${block.kind} moved`, before: old.text, after: block.text });
    } else if (all) {
      changes.push({ type: 'same', kind: block.kind, tag: block.tag, label: block.kind, before: old.text, after: block.text });
    }
    emitRemoved(i);
  });
  for (const anchor of [...removedAfter.keys()]) emitRemoved(anchor);
  return { changes, counts, total: counts.changed + counts.added + counts.removed + counts.moved };
}

/* One plain sentence for the top of the diff view. */
export function summary(counts) {
  const parts = [];
  const say = (n, one, many) => { if (n) parts.push(`${n} ${n === 1 ? one : many}`); };
  say(counts.changed, 'change', 'changes');
  say(counts.added, 'addition', 'additions');
  say(counts.removed, 'removal', 'removals');
  say(counts.moved, 'move', 'moves');
  if (!parts.length) return 'No changes yet.';
  const last = parts.pop();
  return `${parts.length ? `${parts.join(', ')} and ${last}` : last}.`;
}
