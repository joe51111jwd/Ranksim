/* The editor's own tools and panels: undo and redo, insert a block, switch a block between paragraph and headings,
   the heading outline (click to scroll, drag to move a section), what changed (block by block, with a download of
   the full page), and saved versions. Everything goes through the editor ctx (hooks.js), like a plugin.
   editor.js imports this module for its registrations: keep top-level code free of calls into editor.js. */

import { S, SVGNS, api, enc, h, icon, toast, openSheet, closeSheet, pathOf, reduceMotion } from '../core.js';
import { on, registerEditorPanel, registerEditorTool } from '../hooks.js';
import { ED, draftFor, editorCtx, modelOf, paintSide, showText, syncFromFrame } from './editor.js';
import { BLOCKS, blockHtml, convertBlock, imageUrlOk, insertBlock, insertionPoint, moveSection, outline, textBlockOf } from './blocks.js';
import { summary } from './diff.js';

const PATHS = {
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
  grip: '<circle cx="9" cy="6" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="18" r="1"/>',
};

function glyph(name) {
  if (!PATHS[name]) return icon(name);
  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = PATHS[name];
  return svg;
}

const currentDraft = () => (ED.refs ? draftFor(ED.refs.page) : null);

/* ------------------------------------------------------------------ undo and redo */

const historyButtons = new Set();

function refreshHistory() {
  const ctx = editorCtx();
  for (const [undoBtn, redoBtn] of historyButtons) {
    if (!undoBtn.isConnected) {
      historyButtons.delete([undoBtn, redoBtn]);
      continue;
    }
    undoBtn.disabled = !ctx || !ctx.canUndo;
    redoBtn.disabled = !ctx || !ctx.canRedo;
  }
}

registerEditorTool({
  key: 'history',
  order: 20,
  render(ctx) {
    const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
    const undoBtn = h('button', { class: 'icon-btn', type: 'button', title: `Undo (${mac ? 'Cmd' : 'Ctrl'} Z)`, 'aria-label': 'Undo', disabled: !ctx.canUndo, onClick: () => editorCtx().undo() }, glyph('undo'));
    const redoBtn = h('button', { class: 'icon-btn', type: 'button', title: `Redo (${mac ? 'Cmd' : 'Ctrl'} Shift Z)`, 'aria-label': 'Redo', disabled: !ctx.canRedo, onClick: () => editorCtx().redo() }, glyph('redo'));
    historyButtons.clear();
    historyButtons.add([undoBtn, redoBtn]);
    return h('div', { class: 'ed-group', role: 'group', 'aria-label': 'History' }, undoBtn, redoBtn);
  },
});

/* ------------------------------------------------------------------ switch a block: paragraph, H2, H3 */

let blockSeg = null;

function caretBlock(doc) {
  if (!doc || !ED.caret || !doc.contains(ED.caret)) return null;
  return textBlockOf(ED.caret);
}

function refreshBlock() {
  if (!blockSeg || !blockSeg.isConnected) return;
  const ctx = editorCtx();
  const el = caretBlock(ctx && ctx.doc());
  const tag = el ? el.tagName.toLowerCase() : '';
  for (const btn of blockSeg.querySelectorAll('button')) {
    btn.disabled = !el;
    btn.setAttribute('aria-pressed', String(btn.dataset.tag === tag));
  }
}

registerEditorTool({
  key: 'block',
  order: 30,
  render() {
    blockSeg = h('div', { class: 'seg seg-sm', role: 'group', 'aria-label': 'Block type', title: 'Click a line in the page, then pick its type' });
    for (const [tag, label] of [['p', 'Text'], ['h2', 'H2'], ['h3', 'H3']]) {
      blockSeg.append(h('button', {
        type: 'button',
        dataset: { tag },
        disabled: true,
        'aria-pressed': 'false',
        onClick: () => {
          const ctx = editorCtx();
          if (!ctx || !ctx.doc()) return;
          let made = null;
          ctx.edit((doc) => {
            const el = caretBlock(doc);
            if (!el || el.tagName.toLowerCase() === tag) return false;
            made = convertBlock(el, tag);
            return made !== el;
          });
          if (made) placeCaret(made);
          refreshBlock();
        },
      }, label));
    }
    queueMicrotask(refreshBlock);
    return blockSeg;
  },
});

function placeCaret(el) {
  const doc = el.ownerDocument;
  const sel = doc.getSelection();
  if (!sel) return;
  const range = doc.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  sel.removeAllRanges();
  sel.addRange(range);
  ED.caret = el;
}

/* ------------------------------------------------------------------ insert a block */

function insertNow(html, label) {
  const ctx = editorCtx();
  if (!ctx) return;
  let first = null;
  const done = ctx.edit((doc) => {
    const at = ED.caret && doc.contains(ED.caret) ? insertionPoint(ED.caret) : null;
    let ref = at;
    if (!ref) { // no cursor yet: the end of the main content
      const holder = doc.querySelector('main, article, [role="main"]') || doc.body;
      ref = holder.lastElementChild;
      if (!ref) {
        holder.insertAdjacentHTML('beforeend', '<p></p>');
        ref = holder.lastElementChild;
      }
    }
    const nodes = insertBlock(doc, html, ref);
    first = nodes.find((node) => node.nodeType === 1) || null;
    return nodes.length > 0;
  });
  if (!done) return;
  toast(`${label} added. Click it to edit.`);
  const doc = ctx.doc();
  if (doc && first && doc.contains(first)) {
    first.scrollIntoView({ block: 'center', behavior: reduceMotion.matches ? 'auto' : 'smooth' });
    placeCaret(first.querySelector('h2,h3,p,li,td,th,figcaption') || first);
  }
}

function insertSheet(kind) {
  openSheet((sheet, closeBtn) => {
    const isFaq = kind === 'faq';
    sheet.append(h('div', { class: 'sheet-head' }, h('div', null, h('h2', { text: isFaq ? 'Add an FAQ' : 'Add an image' }), h('div', { class: 'sub', text: isFaq ? 'Three questions to start. Edit them on the page.' : 'Use an image address that starts with https.' })), closeBtn));
    const body = h('div', { class: 'sheet-body' });
    const form = h('form', { class: 'form' });
    if (isFaq) {
      const ld = h('input', { type: 'checkbox', checked: true, id: 'faq-ld' });
      form.append(h('label', { class: 'check-row', for: 'faq-ld' }, ld, h('span', null, h('strong', { text: 'Add FAQ structured data' }), h('span', { class: 'muted', text: 'FAQPage JSON-LD that Google reads. It follows your edits to the questions.' }))));
      form.append(h('div', { class: 'form-actions' }, h('button', { class: 'btn btn-primary btn-large', type: 'submit' }, 'Add FAQ')));
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        closeSheet(true);
        insertNow(blockHtml('faq', { jsonld: ld.checked }), 'FAQ');
      });
    } else {
      const src = h('input', { class: 'field', type: 'url', placeholder: 'https://', 'aria-label': 'Image address', required: true });
      const alt = h('input', { class: 'field', type: 'text', placeholder: 'What the image shows', 'aria-label': 'Alt text', required: true, maxlength: '200' });
      const caption = h('input', { class: 'field', type: 'text', placeholder: 'Optional', 'aria-label': 'Caption', maxlength: '200' });
      const note = h('p', { class: 'muted', role: 'status' });
      form.append(
        h('div', { class: 'form-row' }, h('label', { class: 'row-label', text: 'Image address' }), src),
        h('div', { class: 'form-row' }, h('label', { class: 'row-label', text: 'Alt text' }), alt),
        h('div', { class: 'form-row' }, h('label', { class: 'row-label', text: 'Caption' }), caption),
        note,
        h('div', { class: 'form-actions' }, h('button', { class: 'btn btn-primary btn-large', type: 'submit' }, 'Add image')));
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        if (!imageUrlOk(src.value)) {
          note.textContent = 'The address must start with http or https.';
          return;
        }
        if (!alt.value.trim()) {
          note.textContent = 'Alt text helps Google and screen readers. Describe the image.';
          return;
        }
        closeSheet(true);
        insertNow(blockHtml('image', { src: src.value.trim(), alt: alt.value.trim(), caption: caption.value.trim() }), 'Image');
      });
    }
    body.append(form);
    sheet.append(body);
  });
}

function closeMenus() {
  document.querySelectorAll('.ed-menu').forEach((menu) => menu.remove());
}

document.addEventListener('click', (event) => {
  if (!event.target.closest || !event.target.closest('.ed-insert')) closeMenus();
});

registerEditorTool({
  key: 'insert',
  order: 40,
  render(ctx) {
    const wrap = h('div', { class: 'ed-insert' });
    const btn = h('button', { class: 'btn btn-sm', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false' }, icon('plus'), 'Insert');
    void ctx;
    btn.addEventListener('click', () => {
      const now = editorCtx();
      if (!now || !now.draft.text) {
        toast('Wait for the page to open, then insert.');
        return;
      }
      const open = wrap.querySelector('.ed-menu');
      closeMenus();
      if (open) {
        btn.setAttribute('aria-expanded', 'false');
        return;
      }
      btn.setAttribute('aria-expanded', 'true');
      const menu = h('div', { class: 'ed-menu', role: 'menu' });
      for (const block of BLOCKS) {
        menu.append(h('button', {
          type: 'button',
          role: 'menuitem',
          dataset: { block: block.key },
          onClick: () => {
            closeMenus();
            btn.setAttribute('aria-expanded', 'false');
            if (block.key === 'faq' || block.key === 'image') insertSheet(block.key);
            else insertNow(blockHtml(block.key), block.label);
          },
        }, h('strong', { text: block.label }), h('span', { text: block.note })));
      }
      wrap.append(menu);
      const firstItem = menu.querySelector('button');
      if (firstItem) firstItem.focus({ preventScroll: true });
    });
    wrap.append(btn);
    return wrap;
  },
});

/* ------------------------------------------------------------------ outline: click to scroll, drag to move */

function outlineDoc(ctx) {
  const live = ctx.doc();
  if (live) return live;
  if (!ctx.draft.text) return null;
  return new DOMParser().parseFromString(ctx.draft.text, 'text/html');
}

function outlinePanel(ctx) {
  const doc = outlineDoc(ctx);
  const tile = h('section', { class: 'tile ed-outline' }, h('div', { class: 'tile-k', text: 'Outline' }));
  if (!doc) return tile;
  const { flat } = outline(doc);
  if (!flat.length) {
    tile.append(h('p', { class: 'tile-sub', text: 'No headings yet. Insert a section to add one.' }));
    return tile;
  }
  const live = !!ctx.doc();
  const list = h('ol', { class: 'outline-list' });
  let dragging = -1;
  flat.forEach((item, index) => {
    const row = h('li', {
      class: `depth-${Math.min(item.depth, 3)}`,
      draggable: live ? 'true' : null,
      dataset: { index: String(index) },
    },
    live ? h('span', { class: 'grip', 'aria-hidden': 'true' }, glyph('grip')) : null,
    h('button', {
      type: 'button',
      class: 'outline-row',
      title: live ? 'Click to go there. Drag to move the section.' : 'Switch to Visual to go there',
      onClick: () => {
        const el = item.el;
        if (!ctx.doc() || !el.isConnected) return;
        el.scrollIntoView({ block: 'start', behavior: reduceMotion.matches ? 'auto' : 'smooth' });
        placeCaret(el);
        refreshBlock();
      },
    }, h('span', { class: 'lvl', text: `H${item.level}` }), h('span', { class: 'txt', text: item.text })));
    if (live) {
      row.addEventListener('dragstart', (event) => {
        dragging = index;
        row.classList.add('dragging');
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', item.text);
      });
      row.addEventListener('dragend', () => {
        row.classList.remove('dragging');
        list.querySelectorAll('.drop-before,.drop-after').forEach((node) => node.classList.remove('drop-before', 'drop-after'));
      });
      row.addEventListener('dragover', (event) => {
        if (dragging < 0 || dragging === index) return;
        event.preventDefault();
        const box = row.getBoundingClientRect();
        const after = event.clientY > box.top + box.height / 2;
        row.classList.toggle('drop-after', after);
        row.classList.toggle('drop-before', !after);
      });
      row.addEventListener('dragleave', () => row.classList.remove('drop-before', 'drop-after'));
      row.addEventListener('drop', (event) => {
        event.preventDefault();
        const after = row.classList.contains('drop-after');
        row.classList.remove('drop-before', 'drop-after');
        moveByIndex(dragging, index, after ? 'after' : 'before');
        dragging = -1;
      });
    }
    list.append(row);
  });
  tile.append(list);
  if (live) tile.append(h('p', { class: 'tile-sub', text: 'Drag a heading to move its whole section.' }));
  return tile;
}

/* Moves the section under heading `from` before or after the section of heading `to` (outline indexes). */
export function moveByIndex(from, to, place) {
  const ctx = editorCtx();
  if (!ctx || from < 0 || from === to) return false;
  const moved = ctx.edit((doc) => {
    const { flat } = outline(doc);
    if (!flat[from] || !flat[to]) return false;
    return moveSection(flat[from].el, flat[to].el, place);
  });
  if (moved) toast('Section moved. Undo puts it back.');
  return moved;
}

registerEditorPanel({ key: 'outline', order: 25, render: (ctx) => outlinePanel(ctx) });

/* ------------------------------------------------------------------ what changed */

let diffMemo = { text: null, diff: null };

function diffNow(ctx) {
  const rs = modelOf(ctx.draft);
  if (!rs) return null;
  if (diffMemo.text !== rs.text || diffMemo.base !== rs.base) diffMemo = { text: rs.text, base: rs.base, diff: ctx.diff() };
  return diffMemo.diff;
}

function fileName(url) {
  return `${pathOf(url).replace(/^\/|\/$/g, '').replace(/[^a-z0-9._-]+/gi, '-') || 'index'}.html`;
}

/* Downloads the whole page with the edits: the original HTML, scripts and all. */
export function downloadPage(ctx) {
  const html = ctx.exportHtml();
  const blob = new Blob([html], { type: 'text/html' });
  const a = h('a', { href: URL.createObjectURL(blob), download: fileName(ctx.url) });
  document.body.append(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(a.href);
    a.remove();
  }, 500);
}

function words(segments) {
  return h('p', { class: 'words' }, segments.map((seg) => (seg.op === '=' ? seg.text : h(seg.op === '+' ? 'ins' : 'del', { text: seg.text }))));
}

function changesSheet() {
  const ctx = editorCtx();
  if (!ctx) return;
  const diff = ctx.diff();
  openSheet((sheet, closeBtn) => {
    sheet.append(h('div', { class: 'sheet-head' }, h('div', null, h('h2', { text: 'What changed' }), h('div', { class: 'sub', text: summary(diff.counts) })), closeBtn));
    const body = h('div', { class: 'sheet-body' });
    if (!diff.total) body.append(h('p', { class: 'muted', text: 'The page is the same as when it was opened.' }));
    const list = h('ol', { class: 'change-list' });
    for (const change of diff.changes) {
      const item = h('li', { class: `change ${change.type}` }, h('div', { class: 'change-k', text: change.label }));
      if (change.type === 'changed' && change.words) item.append(words(change.words));
      else if (change.type === 'removed') item.append(h('p', { class: 'words' }, h('del', { text: change.before || '(empty)' })));
      else if (change.type === 'added') item.append(h('p', { class: 'words' }, h('ins', { text: change.after || '(empty)' })));
      else item.append(h('p', { class: 'words', text: change.after }));
      list.append(item);
    }
    body.append(list);
    body.append(h('div', { class: 'form-actions' },
      h('button', { class: 'btn btn-dark btn-large', type: 'button', onClick: () => downloadPage(editorCtx() || ctx) }, 'Download page'),
      h('button', {
        class: 'btn btn-large',
        type: 'button',
        onClick: async () => {
          try {
            await navigator.clipboard.writeText((editorCtx() || ctx).exportHtml());
            toast('Copied the full page with your edits.');
          } catch (_err) {
            toast('Copy is blocked here. Use Download instead.');
          }
        },
      }, 'Copy HTML')));
    body.append(h('p', { class: 'muted', text: 'The file is the original page with your edits. Its scripts and styles are kept, so it can replace the page as is.' }));
    sheet.append(body);
  });
}

function changesPanel(ctx) {
  const diff = diffNow(ctx);
  const tile = h('section', { class: 'tile ed-changes' }, h('div', { class: 'tile-k', text: 'Changes' }));
  if (!diff) {
    tile.append(h('p', { class: 'tile-sub', text: 'Your edits show here.' }));
    return tile;
  }
  const lead = diff.total ? summary(diff.counts) : 'No changes yet.';
  tile.append(h('p', { class: 'tile-sub', text: lead }));
  const preview = diff.changes.slice(0, 3).map((change) => h('li', { text: change.label }));
  if (preview.length) tile.append(h('ul', { class: 'change-peek' }, preview, diff.changes.length > 3 ? h('li', { class: 'muted', text: `and ${diff.changes.length - 3} more` }) : null));
  tile.append(h('div', { class: 'result-actions' },
    h('button', { class: 'btn btn-block', type: 'button', disabled: !diff.total, onClick: changesSheet }, 'Review changes'),
    h('button', { class: 'btn btn-block', type: 'button', onClick: () => downloadPage(editorCtx() || ctx) }, 'Download page')));
  return tile;
}

registerEditorPanel({ key: 'changes', order: 15, render: (ctx) => changesPanel(ctx) });

/* ------------------------------------------------------------------ saved versions (plugins/drafts.py) */

const versionLists = new Map(); // url -> Promise of versions

function versionsUrl() {
  return `/api/v1/projects/${enc(S.slug)}/drafts`;
}

function listVersions(url, fresh) {
  if (fresh || !versionLists.has(url)) {
    versionLists.set(url, api.get(`${versionsUrl()}?url=${enc(url)}`).then((data) => data.versions || []).catch((err) => {
      versionLists.delete(url);
      throw err;
    }));
  }
  return versionLists.get(url);
}

async function sha256(text) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(bytes)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function saveVersion(ctx, name) {
  syncFromFrame();
  const rs = modelOf(ctx.draft);
  if (!rs) throw new Error('Open a page first.');
  const body = { url: ctx.url, name, source: await sha256(rs.source), base: await sha256(rs.base), draft: rs.text };
  try {
    return await api.post(versionsUrl(), body);
  } catch (err) {
    if (err.status !== 409) throw err;
  }
  await api.post(`${versionsUrl()}/blobs`, { text: rs.source });
  await api.post(`${versionsUrl()}/blobs`, { text: rs.base });
  return api.post(versionsUrl(), body);
}

async function loadVersion(id) {
  const data = await api.get(`${versionsUrl()}/${enc(id)}`);
  const draft = currentDraft();
  if (!draft) return;
  const rs = modelOf(draft);
  if (!rs) return;
  if (rs.source !== data.source || rs.base !== data.base) {
    rs.source = data.source; // the page has changed since: the version brings its own copy of the page
    rs.base = data.base;
    rs.history.reset(rs.text);
  }
  rs.history.record(data.draft);
  showText(draft, data.draft);
  toast(`Opened ${data.name}. Undo goes back.`);
}

function when(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function versionsPanel(ctx) {
  const tile = h('section', { class: 'tile ed-versions' }, h('div', { class: 'tile-k', text: 'Versions' }));
  if (!S.slug || !S.report || (S.project && S.project.read_only)) {
    tile.append(h('p', { class: 'tile-sub', text: 'Saved versions are kept once the site is set up. Your edits are kept in this browser until then.' }));
    return tile;
  }
  const name = h('input', { class: 'field', type: 'text', placeholder: 'Name this version', 'aria-label': 'Version name', maxlength: '80', dataset: { field: 'version' } });
  const save = h('button', { class: 'btn', type: 'submit', disabled: !ctx.draft.text }, 'Save');
  const form = h('form', { class: 'version-form' }, name, save);
  const list = h('ul', { class: 'version-list' }, h('li', { class: 'muted', text: 'Loading' }));
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    save.disabled = true;
    try {
      const saved = await saveVersion(ctx, name.value.trim());
      name.value = '';
      toast(`Saved ${saved.name}.`);
      await listVersions(ctx.url, true);
    } catch (err) {
      toast(err.message);
    } finally {
      save.disabled = false;
      paintSide();
    }
  });
  listVersions(ctx.url).then((versions) => {
    list.replaceChildren(...(versions.length ? versions.map((v) => h('li', null,
      h('button', { type: 'button', class: 'link-btn', title: 'Open this version (undo goes back)', onClick: () => loadVersion(v.id).catch((err) => toast(err.message)) }, h('span', { class: 'v-name', text: v.name }), h('span', { class: 'muted', text: when(v.created) })),
      h('button', {
        type: 'button',
        class: 'icon-btn',
        'aria-label': `Delete ${v.name}`,
        title: 'Delete',
        onClick: async () => {
          try {
            await api.request('DELETE', `${versionsUrl()}/${enc(v.id)}`);
            await listVersions(ctx.url, true);
          } catch (err) {
            toast(err.message);
          }
          paintSide();
        },
      }, icon('x')))) : [h('li', { class: 'muted', text: 'No saved versions yet.' })]));
  }).catch(() => list.replaceChildren(h('li', { class: 'muted', text: 'Versions could not be loaded.' })));
  tile.append(form, list);
  return tile;
}

registerEditorPanel({ key: 'versions', order: 50, render: (ctx) => versionsPanel(ctx) });

/* ------------------------------------------------------------------ keep the tools in step */

on('draft-changed', () => {
  refreshHistory();
  refreshBlock();
});
on('editor-ready', () => {
  refreshHistory();
  refreshBlock();
});
on('editor-caret', () => refreshBlock());
