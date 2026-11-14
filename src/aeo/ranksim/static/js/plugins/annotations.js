/* Annotations: the audit's comments pinned on the page in the editor.

   - Numbered pins on the elements the issues point at, inside the editor preview.
   - A side panel that lists every comment for the page, with the fix, the suggestion, "Apply suggestion" (changes
     the element in the draft through ctx.edit or ctx.replaceText) and "Show on page".
   - "Fix in editor" on the Issues screen opens /app/<slug>/editor?page=<url>&focus=<selector>; on editor-ready this
     plugin selects that element and opens its comment.

   Everything drawn in the preview carries data-rs (pins, the pin layer, the style) and the only attribute set on the
   page's own elements is data-rs-annotations, so none of it reaches the saved, tested or published HTML. */

import { on, registerEditorPanel } from '../hooks.js';
import { S, h, icon, looseKey, plural, debounce, toast } from '../core.js';

const STATE = { active: null, applied: new Map(), focused: '', wired: new WeakSet(), ctx: null };
const HEAD_ONLY = /^(head\b|title\b|meta\b|link\b)/;
const FOCUS_KEY = 'rs-audit-focus'; // set by the Issues screen's Fix in editor, read once here

/* Applied suggestions are remembered per page draft: a reset or reloaded draft forgets them. */
const appliedKey = (ctx, issue) => `${looseKey(ctx.page)}|${issue.id}`;
function isApplied(ctx, issue) {
  const draft = STATE.applied.get(appliedKey(ctx, issue));
  return !!draft && draft === ctx.draft && ctx.draft.touched;
}

const PIN_CSS = `[data-rs="annotations"]{position:absolute!important;left:0!important;top:0!important;width:0!important;height:0!important;margin:0!important;padding:0!important;border:0!important;z-index:2147483646!important;pointer-events:none!important}
[data-rs="annotations"] [data-rs-pin]{all:initial;position:absolute;box-sizing:border-box;min-width:22px;height:22px;padding:0 6px;border-radius:11px;background:#3358eb;color:#fff;font:600 12px/22px "Geist",ui-sans-serif,-apple-system,"Segoe UI",sans-serif;text-align:center;cursor:pointer;pointer-events:auto;box-shadow:0 0 0 2px #fff,0 2px 8px rgba(0,0,0,.25);user-select:none;-webkit-user-select:none}
[data-rs="annotations"] [data-rs-pin="high"]{background:#e5484d}
[data-rs="annotations"] [data-rs-pin="medium"]{background:#e69a10}
[data-rs="annotations"] [data-rs-pin="low"]{background:#63635d}
[data-rs="annotations"] [data-rs-pin].is-active{box-shadow:0 0 0 2px #fff,0 0 0 5px #3358eb,0 2px 8px rgba(0,0,0,.25)}
[data-rs-annotations]{outline:1.5px dashed rgba(51,88,235,.55)!important;outline-offset:2px}
[data-rs-annotations="active"]{outline:2px solid #3358eb!important}`;

const pagesOf = (issue) => (issue.pages && issue.pages.length ? issue.pages : issue.page ? [issue.page] : []);

/* The report's issues for the page in the editor, in report order (most urgent first). */
export function issuesFor(ctx) {
  const report = (ctx && ctx.report) || S.report;
  if (!report || !report.issues) return [];
  const keys = new Set([ctx.page, ctx.url].filter(Boolean).map(looseKey));
  return report.issues.filter((issue) => pagesOf(issue).some((url) => keys.has(looseKey(url))));
}

function find(doc, selector) {
  if (!doc || !selector) return null;
  try {
    return doc.querySelector(selector);
  } catch (_err) {
    return null;
  }
}

const norm = (text) => String(text || '').replace(/\s+/g, ' ').trim().replace(/\.\.\.$/, '').toLowerCase();

function looksRight(el, element) {
  if (!el || el.tagName.toLowerCase() !== (element.tag || el.tagName.toLowerCase())) return false;
  const want = norm(element.text);
  if (!want) return true;
  const tag = el.tagName.toLowerCase();
  const have = tag === 'meta' ? el.getAttribute('content') : tag === 'img' ? (el.getAttribute('alt') || el.getAttribute('src')) : tag === 'link' ? el.getAttribute('href') : el.textContent;
  return norm(have).startsWith(want);
}

/* The element an issue points at. The selector comes first; when an earlier change moved things (a retagged
   heading shifts the nth-of-type of the ones after it), the element with the same tag and text is used instead,
   and only when exactly one matches. */
function resolve(doc, issue) {
  const element = issue.element;
  if (!doc || !element) return null;
  const first = find(doc, element.selector);
  if (looksRight(first, element)) return first;
  if (!norm(element.text) || !element.tag) return null;
  let matches = [];
  try {
    matches = Array.from(doc.querySelectorAll(element.tag)).filter((el) => !el.closest('[data-rs]') && looksRight(el, element));
  } catch (_err) {
    return null;
  }
  return matches.length === 1 ? matches[0] : null;
}

const pinnable = (issue) => issue.element && issue.element.selector && !HEAD_ONLY.test(issue.element.selector);

/* Whether "Apply suggestion" can change the draft. Scripts (JSON-LD) are left out of the preview, so those are
   copied instead. */
export function canApply(issue) {
  if (!issue.suggestion || !issue.element || !issue.element.selector) return false;
  if (!issue.action || issue.action === 'copy') return false;
  if (/<script/i.test(issue.suggestion) || /^script/.test(issue.element.tag || '')) return false;
  if (issue.action === 'attr' && !issue.element.attr) return false;
  if (issue.action === 'retag' && !/^h[1-6]$/.test(issue.suggestion)) return false;
  return true;
}

function fragment(doc, html) {
  const box = doc.createElement('template');
  box.innerHTML = html;
  for (const el of box.content.querySelectorAll('*')) {
    if (el.tagName === 'SCRIPT') {
      el.remove();
      continue;
    }
    for (const attr of Array.from(el.attributes)) if (/^on/i.test(attr.name)) el.removeAttribute(attr.name);
  }
  return box.content;
}

/* Applies one issue's suggestion to the element in the draft. Returns true when the draft changed. */
export function applySuggestion(ctx, issue) {
  if (!canApply(issue)) return false;
  const { attr } = issue.element;
  const text = issue.suggestion;
  return ctx.edit((doc) => {
    const el = resolve(doc, issue);
    if (!el) return false;
    if (issue.action === 'text') {
      const leaf = el.children.length ? Array.from(el.querySelectorAll('*')).find((node) => !node.children.length && norm(node.textContent) === norm(el.textContent)) : null;
      (leaf || el).textContent = text; // keeps a link or span the text sits in
    } else if (issue.action === 'attr') el.setAttribute(attr, text);
    else if (issue.action === 'retag') {
      const next = doc.createElement(text);
      for (const item of Array.from(el.attributes)) if (!item.name.startsWith('data-rs')) next.setAttribute(item.name, item.value);
      while (el.firstChild) next.append(el.firstChild);
      el.replaceWith(next);
    } else if (issue.action === 'append') el.append(fragment(doc, text));
    else if (issue.action === 'prepend') el.prepend(fragment(doc, text));
    else if (issue.action === 'after') el.after(fragment(doc, text));
    else if (issue.action === 'replace') el.replaceWith(fragment(doc, text));
    else return false;
    return true;
  });
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Copied.');
  } catch (_err) {
    toast('Copy did not work here. Select the text and copy it.');
  }
}

/* ------------------------------------------------------------------ pins in the preview */

function clearPins(doc) {
  doc.querySelectorAll('[data-rs="annotations"],[data-rs="annotations-style"]').forEach((node) => node.remove());
  doc.querySelectorAll('[data-rs-annotations]').forEach((node) => node.removeAttribute('data-rs-annotations'));
}

export function drawPins(ctx) {
  const doc = ctx && ctx.doc();
  if (!doc || !doc.body) return 0;
  clearPins(doc);
  const list = issuesFor(ctx);
  if (!list.length) return 0;
  const style = doc.createElement('style');
  style.setAttribute('data-rs', 'annotations-style');
  style.textContent = PIN_CSS;
  (doc.head || doc.documentElement).append(style);
  const layer = doc.createElement('div');
  layer.setAttribute('data-rs', 'annotations');
  layer.setAttribute('contenteditable', 'false');
  layer.setAttribute('aria-hidden', 'true');
  doc.body.append(layer);
  const base = layer.getBoundingClientRect();
  const taken = new Map();
  let drawn = 0;
  list.forEach((issue, index) => {
    if (!pinnable(issue) || isApplied(ctx, issue)) return;
    const el = resolve(doc, issue);
    if (!el) return;
    const box = el.getBoundingClientRect();
    if (box.width < 1 && box.height < 1) return;
    const spot = `${Math.round(box.top)}:${Math.round(box.left)}`;
    const stack = taken.get(spot) || 0;
    taken.set(spot, stack + 1);
    const pin = doc.createElement('span');
    pin.setAttribute('data-rs', 'annotations-pin');
    pin.setAttribute('data-rs-pin', issue.severity);
    pin.setAttribute('title', issue.title);
    pin.textContent = String(index + 1);
    pin.style.top = `${Math.max(0, box.top - base.top - 11)}px`;
    pin.style.left = `${Math.max(0, box.left - base.left - 11 + stack * 26)}px`;
    if (STATE.active === issue.id) pin.classList.add('is-active');
    pin.addEventListener('mousedown', (event) => {
      event.preventDefault();
      event.stopPropagation();
      activate(ctx, issue, { scroll: false });
    });
    layer.append(pin);
    el.setAttribute('data-rs-annotations', STATE.active === issue.id ? 'active' : String(index + 1));
    drawn += 1;
  });
  return drawn;
}

function activate(ctx, issue, { scroll = true } = {}) {
  STATE.active = issue.id;
  const doc = ctx.doc();
  const el = pinnable(issue) ? resolve(doc, issue) : null;
  drawPins(ctx); // marks the active element data-rs-annotations="active"
  if (el) {
    doc.querySelectorAll('[data-rs-annotations="active"]').forEach((node) => { if (node !== el) node.removeAttribute('data-rs-annotations'); });
    el.setAttribute('data-rs-annotations', 'active');
    ctx.select('[data-rs-annotations="active"]', { scroll });
  } else ctx.select(null);
  ctx.repaint();
  const item = document.querySelector(`.annot-item[data-issue="${CSS.escape(issue.id)}"]`);
  if (item) item.scrollIntoView({ block: 'nearest' });
}

function wire(ctx) {
  const doc = ctx.doc();
  if (!doc || STATE.wired.has(doc)) return;
  STATE.wired.add(doc);
  const redraw = debounce(() => {
    const now = STATE.ctx;
    if (now && now.doc() === doc) drawPins(now);
  }, 150);
  if (doc.defaultView) doc.defaultView.addEventListener('resize', redraw);
  doc.addEventListener('load', redraw, true); // images and fonts that load late move the elements
}

/* ------------------------------------------------------------------ the side panel */

function item(ctx, issue, index) {
  const open = STATE.active === issue.id;
  const applied = isApplied(ctx, issue);
  const head = h('button', { class: 'annot-head', type: 'button', 'aria-expanded': String(open), onClick: () => (open ? (STATE.active = null, ctx.repaint(), drawPins(ctx)) : activate(ctx, issue)) },
    h('span', { class: `annot-num ${issue.severity}`, text: String(index + 1) }),
    h('span', { class: 'annot-title', text: issue.title }),
    applied ? h('span', { class: 'annot-done', text: 'Applied' }) : null);
  const body = open ? h('div', { class: 'annot-body' },
    h('p', { class: 'annot-comment', text: issue.comment }),
    issue.fix ? h('p', { class: 'annot-fix', text: issue.fix }) : null,
    issue.element && issue.element.text ? h('div', { class: 'annot-el' }, h('span', { class: 'annot-tag', text: `<${issue.element.tag || 'element'}>` }), h('span', { text: issue.element.attr ? `${issue.element.attr}="${issue.element.text}"` : issue.element.text })) : null,
    issue.suggestion ? h('pre', { class: 'annot-suggestion', text: issue.action === 'retag' ? `<${issue.suggestion}>` : issue.suggestion }) : null,
    h('div', { class: 'annot-actions' },
      canApply(issue) && !applied ? h('button', {
        class: 'btn btn-primary',
        type: 'button',
        onClick: () => {
          if (applySuggestion(ctx, issue)) {
            STATE.applied.set(appliedKey(ctx, issue), ctx.draft);
            toast('Suggestion applied. Test the change before you publish.');
          } else toast('That element is no longer on the page.');
          ctx.repaint();
          drawPins(ctx);
        },
      }, icon('check'), 'Apply suggestion') : null,
      issue.suggestion && !canApply(issue) ? h('button', { class: 'btn', type: 'button', onClick: () => copy(issue.action === 'retag' ? `<${issue.suggestion}>` : issue.suggestion) }, icon('doc'), 'Copy') : null,
      pinnable(issue) ? h('button', { class: 'btn btn-plain', type: 'button', onClick: () => activate(ctx, issue) }, icon('eye'), 'Show on page') : null),
    issue.suggestion && /<script/i.test(issue.suggestion) ? h('p', { class: 'annot-note', text: 'The editor leaves scripts out of the page, so add this in your site\'s head.' }) : null) : null;
  return h('li', { class: `annot-item${open ? ' open' : ''}`, dataset: { issue: issue.id } }, head, body);
}

registerEditorPanel({
  key: 'audit-annotations',
  order: 15,
  render(ctx) {
    STATE.ctx = ctx;
    if (!ctx.report || !ctx.draft || !ctx.draft.text) return null;
    const list = issuesFor(ctx);
    const high = list.filter((issue) => issue.severity === 'high').length;
    return h('section', { class: 'tile annot-panel' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Comments' }), h('span', { class: 'aside', text: list.length ? (high ? `${plural(list.length, 'issue')}, ${high} high` : plural(list.length, 'issue')) : '' })),
      list.length
        ? h('ol', { class: 'annot-list' }, list.map((issue, index) => item(ctx, issue, index)))
        : h('p', { class: 'tile-sub', text: 'No issues on this page.' }));
  },
});

/* ------------------------------------------------------------------ events */

/* The element to focus: a Fix in editor click (a one-time token the Issues screen leaves), else the focus param of
   an address opened directly (a reload, a pasted link), once per address. */
function pendingFocus(ctx) {
  const focus = S.params.get('focus');
  if (!focus) return null;
  let token = null;
  try {
    token = JSON.parse(sessionStorage.getItem(FOCUS_KEY) || 'null');
  } catch (_err) {
    token = null;
  }
  if (token && token.selector === focus && looseKey(token.page || '') === looseKey(ctx.page)) return { focus, token: true };
  return STATE.focused === location.href ? null : { focus, token: false };
}

on('editor-ready', (ctx) => {
  if (!ctx) return;
  STATE.ctx = ctx;
  wire(ctx);
  const want = ctx.doc() ? pendingFocus(ctx) : null;
  if (want) {
    const match = issuesFor(ctx).find((issue) => issue.element && issue.element.selector === want.focus);
    STATE.focused = location.href;
    if (want.token) {
      try {
        sessionStorage.removeItem(FOCUS_KEY);
      } catch (_err) {
        // storage can be off; the address check above still stops a repeat
      }
    }
    if (match) {
      drawPins(ctx);
      activate(ctx, match);
      return;
    }
    if (ctx.select(want.focus, { scroll: true })) ctx.repaint();
  }
  drawPins(ctx);
});

const later = debounce((ctx) => drawPins(ctx), 250);
on('draft-changed', (ctx) => {
  if (ctx && ctx.doc()) later(ctx);
});

on('report-loaded', () => {
  STATE.active = null; // applied suggestions stay with their drafts (isApplied checks the draft)
});

