/* Round trip editing: the user edits a cleaned copy of the page, and the export is the original page with only those
   edits applied. Scripts, styles, comments and odd markup in the original survive byte for byte.

   source  the page HTML exactly as fetched (never changes)
   base    source with data-rs-id="N" on every start tag, parsed by the browser, cleaned, serialized (never changes)
   draft   base after edits, same ids

   exportHtml(source, base, draft) copies the source and rewrites only what differs between base and draft.
   Pure: no DOM needed, so it runs under node --test. */

export const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr', 'keygen', 'basefont', 'bgsound', 'frame', 'image']);
const RAW = new Set(['script', 'style', 'xmp', 'iframe', 'noembed', 'noframes']);
const RCDATA = new Set(['textarea', 'title']);
const CLOSES_P = new Set(['address', 'article', 'aside', 'blockquote', 'center', 'details', 'dialog', 'dir', 'div', 'dl', 'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hgroup', 'hr', 'main', 'menu', 'nav', 'ol', 'p', 'pre', 'listing', 'section', 'summary', 'table', 'ul', 'search', 'plaintext', 'xmp']);
const SCOPE = new Set(['html', 'table', 'td', 'th', 'caption', 'marquee', 'object', 'applet', 'template', 'button', 'svg', 'math', 'foreignobject']);
const HEADINGS = new Set(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']);
const LEADING_NEWLINE = new Set(['pre', 'textarea', 'listing']);
const SVG_HTML = new Set(['foreignobject', 'desc', 'title']);
export const ID = 'data-rs-id';
const WS = /^[ \t\n\f\r]*$/;
export const isSpace = (text) => WS.test(text);

/* ------------------------------------------------------------------ entities */

const NAMED = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00a0', copy: '\u00a9', reg: '\u00ae', trade: '\u2122',
  hellip: '\u2026', mdash: '\u2014', ndash: '\u2013', lsquo: '\u2018', rsquo: '\u2019', sbquo: '\u201a', ldquo: '\u201c',
  rdquo: '\u201d', bdquo: '\u201e', bull: '\u2022', middot: '\u00b7', laquo: '\u00ab', raquo: '\u00bb', lsaquo: '\u2039',
  rsaquo: '\u203a', times: '\u00d7', divide: '\u00f7', deg: '\u00b0', euro: '\u20ac', pound: '\u00a3', yen: '\u00a5',
  cent: '\u00a2', sect: '\u00a7', para: '\u00b6', plusmn: '\u00b1', frac12: '\u00bd', frac14: '\u00bc', frac34: '\u00be',
  sup1: '\u00b9', sup2: '\u00b2', sup3: '\u00b3', micro: '\u00b5', iexcl: '\u00a1', iquest: '\u00bf', shy: '\u00ad',
  ensp: '\u2002', emsp: '\u2003', thinsp: '\u2009', zwj: '\u200d', zwnj: '\u200c', lrm: '\u200e', rlm: '\u200f',
  larr: '\u2190', rarr: '\u2192', uarr: '\u2191', darr: '\u2193', harr: '\u2194', rArr: '\u21d2', lArr: '\u21d0',
  hearts: '\u2665', check: '\u2713', star: '\u2606', starf: '\u2605', prime: '\u2032', Prime: '\u2033', dagger: '\u2020',
  Dagger: '\u2021', permil: '\u2030', minus: '\u2212', le: '\u2264', ge: '\u2265', ne: '\u2260', asymp: '\u2248',
  infin: '\u221e', not: '\u00ac', ordf: '\u00aa', ordm: '\u00ba', acute: '\u00b4', cedil: '\u00b8', uml: '\u00a8',
  macr: '\u00af', brvbar: '\u00a6', curren: '\u00a4', szlig: '\u00df', aacute: '\u00e1', Aacute: '\u00c1',
  agrave: '\u00e0', Agrave: '\u00c0', acirc: '\u00e2', auml: '\u00e4', Auml: '\u00c4', atilde: '\u00e3', aring: '\u00e5',
  aelig: '\u00e6', ccedil: '\u00e7', Ccedil: '\u00c7', eacute: '\u00e9', Eacute: '\u00c9', egrave: '\u00e8',
  Egrave: '\u00c8', ecirc: '\u00ea', euml: '\u00eb', iacute: '\u00ed', Iacute: '\u00cd', igrave: '\u00ec', icirc: '\u00ee',
  iuml: '\u00ef', ntilde: '\u00f1', Ntilde: '\u00d1', oacute: '\u00f3', Oacute: '\u00d3', ograve: '\u00f2', ocirc: '\u00f4',
  ouml: '\u00f6', Ouml: '\u00d6', otilde: '\u00f5', oslash: '\u00f8', Oslash: '\u00d8', uacute: '\u00fa', Uacute: '\u00da',
  ugrave: '\u00f9', ucirc: '\u00fb', uuml: '\u00fc', Uuml: '\u00dc', yacute: '\u00fd', yuml: '\u00ff', oelig: '\u0153',
  scaron: '\u0161', Scaron: '\u0160', zcaron: '\u017e', fnof: '\u0192', circ: '\u02c6', tilde: '\u02dc', alpha: '\u03b1',
  beta: '\u03b2', gamma: '\u03b3', delta: '\u03b4', pi: '\u03c0', sigma: '\u03c3', mu: '\u03bc', Omega: '\u03a9',
  colon: ':', comma: ',', period: '.', excl: '!', quest: '?', num: '#', dollar: '$', percnt: '%', lpar: '(', rpar: ')',
  ast: '*', plus: '+', sol: '/', bsol: '\\', semi: ';', equals: '=', lowbar: '_', verbar: '|', vert: '|', lbrace: '{',
  rbrace: '}', lsqb: '[', rsqb: ']', lbrack: '[', rbrack: ']', grave: '`', Hat: '^', commat: '@', NewLine: '\n', Tab: '\t',
};
const LEGACY = new Set(['amp', 'lt', 'gt', 'quot', 'nbsp', 'copy', 'reg', 'AMP', 'LT', 'GT', 'QUOT', 'COPY', 'REG']);
const C1 = { 128: 8364, 130: 8218, 131: 402, 132: 8222, 133: 8230, 134: 8224, 135: 8225, 136: 710, 137: 8240, 138: 352, 139: 8249, 140: 338, 142: 381, 145: 8216, 146: 8217, 147: 8220, 148: 8221, 149: 8226, 150: 8211, 151: 8212, 152: 732, 153: 8482, 154: 353, 155: 8250, 156: 339, 158: 382, 159: 376 };

/* Decode character references the way a browser would for the common cases. An unknown name stays as written; the
   only cost of a miss is that an unchanged text next to an edit gets re-escaped. */
export function decode(text) {
  let out = text.indexOf('\r') >= 0 ? text.replace(/\r\n?/g, '\n') : text;
  if (out.indexOf('&') < 0) return out;
  out = out.replace(/&(?:#([0-9]{1,8});?|#[xX]([0-9a-fA-F]{1,7});?|([A-Za-z][A-Za-z0-9]{1,31})(;?))/g, (whole, dec, hex, name, semi) => {
    if (name) {
      const upper = name.toUpperCase();
      const found = NAMED[name] !== undefined ? NAMED[name] : (LEGACY.has(name) ? NAMED[name.toLowerCase()] : undefined);
      if (found === undefined) {
        if (!semi) { // a legacy name can be a prefix: &ampx reads as &x
          for (const legacy of ['amp', 'lt', 'gt', 'quot', 'nbsp', 'copy', 'reg']) {
            if (name.startsWith(legacy)) return NAMED[legacy] + name.slice(legacy.length);
          }
        }
        return upper === 'AMP' ? '&' : whole;
      }
      if (!semi && !LEGACY.has(name)) return whole;
      return found;
    }
    let code = dec ? parseInt(dec, 10) : parseInt(hex, 16);
    if (C1[code]) code = C1[code];
    if (code === 0 || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) return '\ufffd';
    return String.fromCodePoint(code);
  });
  return out;
}

export function escapeText(text) {
  return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\u00a0/g, '&nbsp;');
}

export function escapeAttr(text) {
  return String(text).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/\u00a0/g, '&nbsp;');
}

/* ------------------------------------------------------------------ lite nodes */

/* A node of the lite tree. It records byte offsets in the text it came from, and implements the small part of the DOM
   API that blocks.js and diff.js use, so the same code runs on real DOM nodes in the browser and on these in node. */
export class LNode {
  constructor(type, props) {
    this.type = type; // 'root' | 'element' | 'text' | 'comment' | 'other'
    this.children = [];
    this.parent = null;
    Object.assign(this, props);
  }

  get nodeType() { return this.type === 'element' ? 1 : this.type === 'text' ? 3 : this.type === 'comment' ? 8 : this.type === 'root' ? 9 : 10; }
  get tagName() { return this.type === 'element' ? this.tag.toUpperCase() : undefined; }
  get nodeName() { return this.type === 'element' ? this.tag.toUpperCase() : this.type === 'text' ? '#text' : '#' + this.type; }
  get localName() { return this.tag; }
  get parentNode() { return this.parent; }
  get parentElement() { return this.parent && this.parent.type === 'element' ? this.parent : null; }
  get childNodes() { return this.children; }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
  get nextSibling() { if (!this.parent) return null; const list = this.parent.children; return list[list.indexOf(this) + 1] || null; }
  get previousSibling() { if (!this.parent) return null; const list = this.parent.children; const at = list.indexOf(this); return at > 0 ? list[at - 1] : null; }
  get nextElementSibling() { let node = this.nextSibling; while (node && node.type !== 'element') node = node.nextSibling; return node; }
  get previousElementSibling() { let node = this.previousSibling; while (node && node.type !== 'element') node = node.previousSibling; return node; }
  get firstElementChild() { return this.children.find((node) => node.type === 'element') || null; }
  get childElements() { return this.children.filter((node) => node.type === 'element'); }
  get data() { return this.type === 'text' ? this.textContent : (this.text !== undefined ? this.text : ''); }
  set data(value) { this.text = String(value); delete this.start; }
  get textContent() {
    if (this.type === 'text') return decodeRawText(this);
    if (this.type === 'comment' || this.type === 'other') return '';
    let out = '';
    for (const child of this.children) if (child.type === 'text' || child.type === 'element') out += child.textContent;
    return out;
  }

  set textContent(value) {
    for (const child of this.children) child.parent = null;
    this.children = [];
    if (value) this.appendChild(new LNode('text', { text: String(value) }));
  }

  getAttribute(name) { const found = (this.attrs || []).find((a) => a.name === name.toLowerCase()); return found ? found.value : null; }
  hasAttribute(name) { return this.getAttribute(name) !== null; }
  setAttribute(name, value) {
    const key = name.toLowerCase();
    const found = (this.attrs || []).find((a) => a.name === key);
    if (found) found.value = String(value);
    else (this.attrs = this.attrs || []).push({ name: key, value: String(value) });
  }

  removeAttribute(name) { this.attrs = (this.attrs || []).filter((a) => a.name !== name.toLowerCase()); }
  get attributes() { return (this.attrs || []).map((a) => ({ name: a.name, value: a.value })); }
  remove() { if (this.parent) { this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; } }
  appendChild(node) { node.remove(); node.parent = this; this.children.push(node); return node; }
  append(...nodes) { for (const node of nodes) this.appendChild(typeof node === 'string' ? new LNode('text', { text: node }) : node); }
  insertBefore(node, ref) {
    if (!ref) return this.appendChild(node);
    node.remove();
    node.parent = this;
    this.children.splice(this.children.indexOf(ref), 0, node);
    return node;
  }

  contains(node) { for (let up = node; up; up = up.parent) if (up === this) return true; return false; }
  * walk() { for (const child of this.children) { yield child; if (child.type === 'element') yield* child.walk(); } }
  get ownerDocument() { let up = this; while (up.parent) up = up.parent; return up; }
  createElement(tag) { return new LNode('element', { tag: tag.toLowerCase(), rawName: tag.toLowerCase(), attrs: [], rsid: null }); }
  createTextNode(text) { return new LNode('text', { text: String(text) }); }
  replaceWith(node) { const parent = this.parent; if (parent) { parent.insertBefore(node, this); this.remove(); } }
  get outerHTML() { return serializeLite(this); }
  get innerHTML() { return this.children.map(serializeLite).join(''); }
}

const RAW_OUT = new Set(['script', 'style', 'xmp', 'iframe', 'noembed', 'noframes', 'plaintext']);

/* Serializes a lite tree the way a browser serializes a DOM. */
export function serializeLite(node) {
  if (node.type === 'root') return node.children.map(serializeLite).join('');
  if (node.type === 'text') {
    const parent = node.parent;
    return parent && parent.type === 'element' && RAW_OUT.has(parent.tag) ? node.text : escapeText(decodeRawText(node));
  }
  if (node.type === 'comment') return `<!--${node.text}-->`;
  if (node.type === 'other') return node.text !== undefined ? node.text : '';
  let out = `<${node.rawName || node.tag}`;
  for (const a of node.attrs || []) out += ` ${a.name}="${escapeAttr(a.value)}"`;
  out += '>';
  if (VOID.has(node.tag) && !node.foreign) return out;
  return `${out}${node.children.map(serializeLite).join('')}</${node.rawName || node.tag}>`;
}

/* Text nodes read from markup hold raw text with references; nodes made in code hold plain text. */
function decodeRawText(node) {
  if (node.start === undefined) return node.text;
  const parent = node.parent;
  return parent && parent.type === 'element' && parent.raw ? node.text : decode(node.text);
}

/* ------------------------------------------------------------------ parser */

const NAME_END = /[\t\n\f\r />]/;
const isAlpha = (code) => (code >= 65 && code <= 90) || (code >= 97 && code <= 122);

/* Reads a start or end tag beginning at `at` (the '<'). Returns null when the tag never closes (the rest is text). */
function readTag(src, at, end) {
  let j = at + (end ? 2 : 1);
  const nameStart = j;
  while (j < src.length && !NAME_END.test(src[j])) j += 1;
  const rawName = src.slice(nameStart, j);
  const attrs = [];
  let selfClosing = false;
  for (;;) {
    const before = j;
    while (j < src.length && /[\t\n\f\r ]/.test(src[j])) j += 1;
    if (j >= src.length) return null;
    const ch = src[j];
    if (ch === '>') { j += 1; break; }
    if (ch === '/') {
      if (src[j + 1] === '>') { selfClosing = true; j += 2; break; }
      j += 1;
      continue;
    }
    const nameAt = j;
    j += 1; // the first character may be '='
    while (j < src.length && !/[\t\n\f\r />=]/.test(src[j])) j += 1;
    const name = src.slice(nameAt, j).toLowerCase();
    let k = j;
    while (k < src.length && /[\t\n\f\r ]/.test(src[k])) k += 1;
    let raw = null;
    if (src[k] === '=') {
      k += 1;
      while (k < src.length && /[\t\n\f\r ]/.test(src[k])) k += 1;
      const q = src[k];
      if (q === '"' || q === "'") {
        const close = src.indexOf(q, k + 1);
        if (close < 0) return null;
        raw = src.slice(k + 1, close);
        j = close + 1;
      } else {
        const valueAt = k;
        while (k < src.length && !/[\t\n\f\r >]/.test(src[k])) k += 1;
        raw = src.slice(valueAt, k);
        j = k;
      }
    }
    attrs.push({ name, raw, value: raw === null ? '' : decode(raw), start: before, end: j });
  }
  return { rawName, attrs, selfClosing, end: j };
}

/* Parses HTML into a lite tree with byte offsets. Every byte of the input belongs to exactly one node piece, so emitting
   the pieces in order gives the input back.

   Source mode (default) gives every start tag an id in document order (the same order annotate() uses) and closes the
   common implied tags. Strict mode reads a browser serialization, where every element has an end tag, and takes ids
   from data-rs-id. */
export function parse(src, opts = {}) {
  const strict = !!opts.strict;
  const root = new LNode('root', { tag: '#root', start: 0, startEnd: 0, end: src.length, endStart: src.length, hasEnd: false });
  const stack = [root];
  const elements = [];
  let textStart = -1;
  let i = 0;
  const n = src.length;
  const top = () => stack[stack.length - 1];
  const add = (node) => { node.parent = top(); top().children.push(node); };
  const flush = (to) => {
    if (textStart >= 0 && to > textStart) add(new LNode('text', { start: textStart, end: to, text: src.slice(textStart, to) }));
    textStart = -1;
  };
  const popTo = (index, at) => { // closes stack[index..] implicitly at `at`
    while (stack.length > index) {
      const el = stack.pop();
      el.end = at;
      el.endStart = at;
      el.hasEnd = false;
    }
  };
  const findOpen = (tags, stops) => {
    for (let k = stack.length - 1; k > 0; k -= 1) {
      const tag = stack[k].tag;
      if (tags.has(tag)) return k;
      if (stops.has(tag) || stack[k].foreign) return -1;
    }
    return -1;
  };
  const implied = (tag, at) => {
    if (CLOSES_P.has(tag) || tag === 'li' || tag === 'dt' || tag === 'dd') {
      const k = findOpen(new Set(['p']), SCOPE);
      if (k > 0) popTo(k, at);
    }
    if (HEADINGS.has(tag) && HEADINGS.has(top().tag)) popTo(stack.length - 1, at);
    if (tag === 'li') {
      const k = findOpen(new Set(['li']), new Set([...SCOPE, 'ul', 'ol', 'menu']));
      if (k > 0) popTo(k, at);
    }
    if (tag === 'dt' || tag === 'dd') {
      const k = findOpen(new Set(['dt', 'dd']), new Set([...SCOPE, 'dl']));
      if (k > 0) popTo(k, at);
    }
    if (tag === 'option' || tag === 'optgroup') {
      if (top().tag === 'option') popTo(stack.length - 1, at);
      if (tag === 'optgroup' && top().tag === 'optgroup') popTo(stack.length - 1, at);
    }
    if (tag === 'tr' || tag === 'td' || tag === 'th' || tag === 'thead' || tag === 'tbody' || tag === 'tfoot' || tag === 'caption' || tag === 'colgroup') {
      const closers = tag === 'td' || tag === 'th' ? new Set(['td', 'th']) : tag === 'tr' ? new Set(['tr', 'td', 'th']) : new Set(['tr', 'td', 'th', 'thead', 'tbody', 'tfoot', 'caption', 'colgroup']);
      const k = findOpen(closers, new Set(['table', 'html', 'template']));
      if (k > 0) popTo(k, at);
    }
    if (tag === 'button') {
      const k = findOpen(new Set(['button']), new Set(['html', 'table', 'td', 'th', 'template']));
      if (k > 0) popTo(k, at);
    }
    if (tag === 'a') {
      const k = findOpen(new Set(['a']), new Set(['html', 'table', 'td', 'th', 'template']));
      if (k > 0) popTo(k, at);
    }
    if (tag === 'body' || tag === 'frameset') {
      const k = findOpen(new Set(['head']), new Set());
      if (k > 0) popTo(k, at);
    }
  };

  while (i < n) {
    const lt = src.indexOf('<', i);
    if (lt < 0) {
      if (textStart < 0) textStart = i;
      i = n;
      break;
    }
    if (lt > i && textStart < 0) textStart = i;
    i = lt;
    const next = src.charCodeAt(i + 1);
    if (src.startsWith('<!--', i)) {
      flush(i);
      let end;
      if (src.startsWith('<!-->', i)) end = i + 5;
      else if (src.startsWith('<!--->', i)) end = i + 6;
      else {
        const a = src.indexOf('-->', i + 4);
        const b = src.indexOf('--!>', i + 4);
        if (a < 0 && b < 0) end = n;
        else if (b >= 0 && (a < 0 || b < a)) end = b + 4;
        else end = a + 3;
      }
      add(new LNode('comment', { start: i, end, text: src.slice(i + 4, Math.max(i + 4, end - 3)) }));
      i = end;
      continue;
    }
    if (next === 33 || next === 63) { // <! or <?
      flush(i);
      let end;
      if (src.startsWith('<![CDATA[', i) && top().foreign) {
        const close = src.indexOf(']]>', i);
        end = close < 0 ? n : close + 3;
      } else {
        const close = src.indexOf('>', i);
        end = close < 0 ? n : close + 1;
      }
      add(new LNode('other', { start: i, end, text: src.slice(i, end) }));
      i = end;
      continue;
    }
    if (next === 47) { // </
      const after = src.charCodeAt(i + 2);
      if (src[i + 2] === '>') {
        flush(i);
        add(new LNode('other', { start: i, end: i + 3, text: '</>' }));
        i += 3;
        continue;
      }
      if (!isAlpha(after)) {
        if (i + 2 >= n) { // a lone '</' at the end is text
          if (textStart < 0) textStart = i;
          i = n;
          continue;
        }
        flush(i);
        const close = src.indexOf('>', i);
        const end = close < 0 ? n : close + 1;
        add(new LNode('other', { start: i, end, text: src.slice(i, end) }));
        i = end;
        continue;
      }
      const tag = readTag(src, i, true);
      if (!tag) {
        if (textStart < 0) textStart = i;
        i = n;
        continue;
      }
      flush(i);
      const name = tag.rawName.toLowerCase();
      let k = -1;
      for (let s = stack.length - 1; s > 0; s -= 1) {
        if (stack[s].tag === name) { k = s; break; }
        if (!strict && (stack[s].tag === 'html' || ((name === 'li' || name === 'dd' || name === 'dt' || name === 'p') && SCOPE.has(stack[s].tag)))) break;
      }
      if (k > 0) {
        const el = stack[k];
        popTo(k + 1, i);
        stack.pop();
        el.endStart = i;
        el.end = tag.end;
        el.hasEnd = true;
      } else {
        add(new LNode('other', { start: i, end: tag.end, text: src.slice(i, tag.end) }));
      }
      i = tag.end;
      continue;
    }
    if (!isAlpha(next)) {
      if (textStart < 0) textStart = i;
      i += 1;
      continue;
    }
    const tag = readTag(src, i, false);
    if (!tag) {
      if (textStart < 0) textStart = i;
      i = n;
      continue;
    }
    flush(i);
    const name = tag.rawName.toLowerCase();
    const parent = top();
    const foreign = name === 'svg' || name === 'math' || (parent.foreign && !(parent.tag === 'foreignobject' || parent.tag === 'desc' || (parent.tag === 'title' && parent.foreign) || parent.tag === 'annotation-xml'));
    if (!strict && !foreign) implied(name, i);
    const el = new LNode('element', { tag: name, rawName: tag.rawName, attrs: tag.attrs, start: i, startEnd: tag.end, selfClosing: tag.selfClosing, foreign, hasEnd: false });
    if (strict) {
      const id = el.getAttribute(ID);
      el.rsid = id === null ? null : id;
    } else {
      el.rsid = String(elements.length + 1);
    }
    elements.push(el);
    add(el);
    i = tag.end;
    if ((VOID.has(name) && !foreign) || (foreign && tag.selfClosing)) {
      el.end = el.endStart = tag.end;
      continue;
    }
    stack.push(el);
    // A browser runs scripts, so <noscript> is raw text to it; the source is read the way a visitor's browser reads it.
    const raw = !foreign && (RAW.has(name) || RCDATA.has(name) || name === 'plaintext' || (name === 'noscript' && !strict));
    if (raw) {
      el.raw = !RCDATA.has(name);
      let close = -1;
      if (name === 'script') close = scriptEnd(src, i);
      else if (name !== 'plaintext') {
        const re = new RegExp(`</${name}[\\t\\n\\f\\r />]`, 'ig');
        re.lastIndex = i;
        const found = re.exec(src);
        close = found ? found.index : -1;
      }
      const stop = close < 0 ? n : close;
      if (stop > i) add(new LNode('text', { start: i, end: stop, text: src.slice(i, stop) }));
      i = stop;
      if (close >= 0) {
        const endTag = readTag(src, close, true);
        stack.pop();
        el.endStart = close;
        el.end = endTag ? endTag.end : n;
        el.hasEnd = true;
        i = el.end;
      }
    }
  }
  flush(n);
  popTo(1, n);
  root.elements = elements;
  root.src = src;
  return root;
}

/* Where a script element ends. Inside a script, '<!--' starts an escaped run in which '<script' nests and its
   '</script>' does not end the outer one (old document.write snippets rely on this). */
function scriptEnd(src, from) {
  const re = /<!--|-->|<\/?script(?=[\t\n\f\r />])/gi;
  re.lastIndex = from;
  let state = 0; // 0 data, 1 escaped, 2 double escaped
  for (let m = re.exec(src); m; m = re.exec(src)) {
    const t = m[0].toLowerCase();
    if (t === '<!--') {
      if (state === 0) {
        state = 1;
        if (src[re.lastIndex] === '>') state = 0; // <!-->
        else if (src.startsWith('->', re.lastIndex)) { state = 0; re.lastIndex += 2; } // <!--->
      }
    } else if (t === '-->') {
      state = 0;
    } else if (t === '</script') {
      if (state === 2) state = 1;
      else return m.index;
    } else if (state === 1) {
      state = 2;
    }
  }
  return -1;
}

/* The source with data-rs-id="N" added to every start tag, in the same order parse() numbers them, for the browser
   to parse. The browser half parses without scripting, so <noscript> contents are left out: a visitor's browser
   never builds them, and a tracking iframe in a head <noscript> would otherwise push the rest of the head into the
   body. */
export function annotate(source) {
  const tree = parse(source);
  const out = [];
  let at = 0;
  for (const el of tree.elements) {
    const cut = el.start + 1 + el.rawName.length;
    if (cut < at) continue;
    out.push(source.slice(at, cut), ` ${ID}="${el.rsid}"`);
    at = cut;
    if (el.tag === 'noscript' && el.raw && el.children.length) {
      out.push(source.slice(at, el.children[0].start));
      at = el.children[el.children.length - 1].end;
    }
  }
  out.push(source.slice(at));
  return out.join('');
}

/* ------------------------------------------------------------------ alignment */

/* Longest common subsequence of two key arrays. Returns matched index pairs in order. Common ends are trimmed first;
   a middle too big for the table falls back to matching unique keys in order. */
export function lcs(a, b, eq = (x, y) => x === y) {
  const pairs = [];
  let s = 0;
  while (s < a.length && s < b.length && eq(a[s], b[s])) { pairs.push([s, s]); s += 1; }
  let ea = a.length;
  let eb = b.length;
  const tail = [];
  while (ea > s && eb > s && eq(a[ea - 1], b[eb - 1])) { ea -= 1; eb -= 1; tail.push([ea, eb]); }
  const n = ea - s;
  const m = eb - s;
  if (n > 0 && m > 0) {
    if (n * m <= 4_000_000) {
      const w = m + 1;
      const table = new Int32Array((n + 1) * w);
      for (let x = n - 1; x >= 0; x -= 1) {
        for (let y = m - 1; y >= 0; y -= 1) {
          table[x * w + y] = eq(a[s + x], b[s + y]) ? table[(x + 1) * w + y + 1] + 1 : Math.max(table[(x + 1) * w + y], table[x * w + y + 1]);
        }
      }
      let x = 0;
      let y = 0;
      while (x < n && y < m) {
        if (eq(a[s + x], b[s + y])) { pairs.push([s + x, s + y]); x += 1; y += 1; }
        else if (table[(x + 1) * w + y] >= table[x * w + y + 1]) x += 1;
        else y += 1;
      }
    } else {
      pairs.push(...uniqueOrder(a.slice(s, ea), b.slice(s, eb)).map(([x, y]) => [x + s, y + s]));
    }
  }
  for (let k = tail.length - 1; k >= 0; k -= 1) pairs.push(tail[k]);
  return pairs;
}

/* Matches keys that occur once on each side, keeping the longest increasing run (patience style). */
function uniqueOrder(a, b) {
  const count = new Map();
  a.forEach((key) => count.set(key, (count.get(key) || 0) + 1));
  const inB = new Map();
  b.forEach((key, y) => inB.set(key, inB.has(key) ? -1 : y));
  const cand = [];
  a.forEach((key, x) => { const y = inB.get(key); if (count.get(key) === 1 && y !== undefined && y >= 0) cand.push([x, y]); });
  return longestIncreasing(cand);
}

/* Longest run of pairs whose second index increases. Pairs come sorted by the first index. */
export function longestIncreasing(pairs) {
  const tails = [];
  const prev = new Array(pairs.length).fill(-1);
  for (let k = 0; k < pairs.length; k += 1) {
    let lo = 0;
    let hi = tails.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (pairs[tails[mid]][1] < pairs[k][1]) lo = mid + 1;
      else hi = mid;
    }
    if (lo > 0) prev[k] = tails[lo - 1];
    tails[lo] = k;
  }
  const out = [];
  for (let k = tails.length ? tails[tails.length - 1] : -1; k >= 0; k = prev[k]) out.push(pairs[k]);
  return out.reverse();
}

/* ------------------------------------------------------------------ the model: base vs draft */

/* The editor's own attributes: data-rs and data-rs-*. A page's data-rsvp or data-rss-feed is the page's. */
export const isRsAttr = (name) => name === 'data-rs' || name.startsWith('data-rs-');
const isXid = (id) => typeof id === 'string' && id.charCodeAt(0) === 120; // 'x'
const NUMERIC = /^[0-9]+$/;

/* The nearest ancestor with a source id, or '' for the top. */
function ownerOf(el) {
  for (let up = el.parent; up; up = up.parent) if (up.type === 'element' && up.rsid && NUMERIC.test(up.rsid)) return up.rsid;
  return '';
}

function prevRealSibling(el) {
  for (let node = el.previousSibling; node; node = node.previousSibling) if (node.type === 'element' && node.rsid && NUMERIC.test(node.rsid) && node.rsid !== el.rsid) return node.rsid;
  return '';
}

/* Indexes a strict parse of base or draft. Elements are real (map to a source element), see-through (made up by the
   parser, like an implied tbody: their children belong to the parent) or new (no id: typed, inserted or pasted).
   `B` is the base index when indexing a draft. */
function indexTree(tree, srcIds, B) {
  const real = new Map();
  const seen = new Map();
  const kind = new Map();
  const groups = new Map();
  for (const el of tree.elements) {
    const id = el.rsid;
    if (id === null || id === undefined || (!isXid(id) && !srcIds.has(id))) { kind.set(el, 'new'); continue; }
    if (isXid(id)) {
      // an empty element the parser made (a stray </p>) that the user filled is the user's own
      const filled = B && B.xEmpty.has(id) && el.children.length > 0;
      kind.set(el, filled ? 'new' : 'see');
      continue;
    }
    if (!groups.has(id)) groups.set(id, []);
    groups.get(id).push(el);
  }
  for (const [id, list] of groups) {
    seen.set(id, list.length);
    let pick = list[0];
    if (B && list.length > (B.seen.get(id) || 1)) {
      // the user pasted a copy: the original is the one that sits where the base had it
      const bEl = B.real.get(id);
      const want = bEl ? [ownerOf(bEl), prevRealSibling(bEl), bEl.textContent] : ['', '', ''];
      let best = -1;
      for (const el of list) {
        const score = (el.textContent === want[2] ? 4 : 0) + (ownerOf(el) === want[0] ? 2 : 0) + (prevRealSibling(el) === want[1] ? 1 : 0);
        if (score > best) { best = score; pick = el; }
      }
    }
    real.set(id, pick);
    const copies = B ? Math.max(0, (B.seen.get(id) || 1) - 1) : Infinity;
    let made = 0;
    for (const el of list) {
      if (el === pick) kind.set(el, 'real');
      else if (made < copies) { kind.set(el, 'see'); made += 1; } // a copy the parser made (misnested formatting)
      else kind.set(el, 'new');
    }
  }
  const xEmpty = new Set();
  if (!B) for (const el of tree.elements) if (isXid(el.rsid) && !el.children.length) xEmpty.add(el.rsid);
  const lists = new Map();
  const parentOf = new Map();
  const visit = (node, owner, list, rawParent) => {
    for (const child of node.children) {
      if (child.type === 'text') {
        if (!rawParent && isSpace(child.text)) continue;
        list.push({ node: child, key: `t${rawParent ? child.text : decodeText(child)}`, raw: rawParent });
      } else if (child.type === 'element') {
        const k = kind.get(child);
        if (k === 'see') {
          visit(child, owner, list, child.raw);
        } else {
          list.push({ node: child, key: k === 'real' ? `#${child.rsid}` : null });
          if (k === 'real') {
            parentOf.set(child.rsid, owner);
            const own = [];
            lists.set(child.rsid, own);
            visit(child, child.rsid, own, child.raw);
          } else {
            visit(child, owner, [], child.raw); // new elements: index real ids inside, they are emitted from there
          }
        }
      }
    }
  };
  const top = [];
  lists.set('', top);
  visit(tree, '', top, false);
  return { tree, real, kind, lists, parentOf, seen, xEmpty };
}

/* Text of a node as the DOM holds it. A browser drops one newline right after <pre>, <textarea> and <listing>. */
function decodeText(node) {
  let text = decode(node.text);
  const parent = node.parent;
  if (parent && parent.type === 'element' && LEADING_NEWLINE.has(parent.tag) && parent.children[0] === node && text[0] === '\n') text = text.slice(1);
  return text;
}

function attrMap(el) {
  const map = new Map();
  for (const a of el.attrs || []) if (!isRsAttr(a.name) && !map.has(a.name)) map.set(a.name, a.value);
  return map;
}

function sameAttrs(a, b) {
  if (a.size !== b.size) return false;
  for (const [k, v] of a) if (b.get(k) !== v) return false;
  return true;
}

function sameKeys(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  for (let k = 0; k < a.length; k += 1) if (a[k].key === null || a[k].key !== b[k].key) return false;
  return true;
}

const eqKey = (x, y) => x !== null && x === y;

/* The visible children of a source node, keyed like the base lists: text (decoded) and elements the base has. */
function sourceItems(srcNode, B) {
  const items = [];
  srcNode.children.forEach((child, s) => {
    if (child.type === 'text') {
      if (srcNode.raw) items.push({ s, node: child, key: `t${child.text}` });
      else if (!isSpace(child.text)) items.push({ s, node: child, key: `t${decodeText(child)}` });
    } else if (child.type === 'element' && B.real.has(child.rsid)) {
      items.push({ s, node: child, key: `#${child.rsid}` });
    }
  });
  return items;
}

/* Where the browser and our parser disagree (foster parented table text, text after </body>, streamed content,
   misnested formatting), a text sits under one parent in the source and another in the base. Pair those up, so an
   edit on either side changes the one copy that the export holds. */
function accountTexts(src, B) {
  const exported = []; // source text nodes the base holds under a different parent, in source order
  const imported = []; // base text items with no source text under the same parent
  const align = new Map(); // id -> {pairs, sItems}
  const visit = (node, id) => {
    const inBase = id === '' || B.real.has(id);
    const sItems = sourceItems(node, B);
    if (inBase) {
      const bList = B.lists.get(id) || [];
      const pairs = lcs(sItems.map((it) => it.key), bList.map((it) => it.key), eqKey);
      align.set(id, { pairs, sItems });
      const sMatched = new Set(pairs.map(([x]) => x));
      const bMatched = new Set(pairs.map(([, y]) => y));
      sItems.forEach((it, x) => { if (!sMatched.has(x) && it.node.type === 'text' && !node.raw) exported.push(it); });
      bList.forEach((it, y) => { if (!bMatched.has(y) && it.node.type === 'text' && !it.raw) imported.push(it); });
    } else if (!node.raw) {
      for (const it of sItems) if (it.node.type === 'text') exported.push(it);
    }
    for (const child of node.children) if (child.type === 'element') visit(child, child.rsid);
  };
  visit(src, '');
  const pool = new Map();
  for (const it of exported) {
    if (!pool.has(it.key)) pool.set(it.key, []);
    pool.get(it.key).push(it.node);
  }
  const home = new Map(); // base text node -> source text node
  for (const it of imported) {
    const list = pool.get(it.key);
    if (list && list.length) home.set(it.node, list.shift());
  }
  return { align, home };
}

/* The original page with the draft's edits applied. */
export function exportHtml(source, base, draft) {
  const src = parse(source);
  const srcIds = new Map(src.elements.map((el) => [el.rsid, el]));
  const B = indexTree(parse(base, { strict: true }), srcIds, null);
  const E = indexTree(parse(draft, { strict: true }), srcIds, B);
  const changed = new Set();
  for (const [id, list] of E.lists) if (id === '' || B.real.has(id)) if (!sameKeys(B.lists.get(id), list)) changed.add(id);

  const acct = changed.size ? accountTexts(src, B) : { align: new Map(), home: new Map() };
  acct.after = new Map(); // source node -> draft items to emit right after it, wherever it is emitted
  acct.before = new Map();
  B.srcIds = srcIds;
  const placed = new Set(); // ids the draft puts somewhere new; the source walk skips them where they were
  const suppressed = new Set(); // source text nodes the user deleted or rewrote where the base shows them
  const plans = new Map();
  for (const id of changed) {
    const plan = planMerge(id === '' ? src : srcIds.get(id), id, B, E, acct, suppressed);
    plans.set(id, plan);
    for (const item of plan.inserts) markPlaced(item.node, E, placed);
  }

  // Scripts and other hidden parts right after a moved element go with it: a section's widget script needs its markup.
  const carried = new Map();
  const carriedSet = new Set();
  for (const id of placed) {
    const el = srcIds.get(id);
    if (!el || !el.parent) continue;
    const kids = el.parent.children;
    const run = [];
    for (let k = kids.indexOf(el) + 1; k < kids.length; k += 1) {
      const node = kids[k];
      if (node.type === 'text' && isSpace(node.text)) run.push(node);
      else if (node.type === 'element' && !B.real.has(node.rsid) && !placed.has(node.rsid)) run.push(node);
      else break;
    }
    while (run.length && run[run.length - 1].type === 'text') run.pop();
    if (!run.some((node) => node.type === 'element')) continue;
    carried.set(id, run);
    for (const node of run) carriedSet.add(node);
  }

  const out = [];
  const ctx = { source, draft, src, srcIds, B, E, changed, plans, placed, suppressed, out, acct, carried, carriedSet };
  if (changed.has('')) emitMerge(src, '', ctx);
  else emitWalk(src, ctx);
  return out.join('');
}

function markPlaced(node, E, placed) {
  if (node.type !== 'element') return;
  const k = E.kind.get(node);
  if (k === 'real') { placed.add(node.rsid); return; }
  for (const child of node.children) markPlaced(child, E, placed);
}

/* How to rebuild one changed parent: source children are matched to base items, base items to draft items. */
function planMerge(srcNode, id, B, E, acct, suppressed) {
  const kids = srcNode.children;
  const { pairs: sbPairs, sItems } = acct.align.get(id) || { pairs: [], sItems: sourceItems(srcNode, B) };
  const bList = B.lists.get(id) || [];
  const items = E.lists.get(id) || [];
  const bOfS = new Map(sbPairs.map(([x, y]) => [x, y]));
  const sOfB = new Map(sbPairs.map(([x, y]) => [y, x]));
  const bePairs = lcs(bList.map((it) => it.key), items.map((it) => it.key), eqKey);
  const eOfB = new Map(bePairs.map(([y, e]) => [y, e]));
  const bOfE = new Map(bePairs.map(([y, e]) => [e, y]));
  // base texts that live elsewhere in the source and are gone from the draft: drop them at home
  bList.forEach((it, y) => {
    if (sOfB.has(y) || eOfB.has(y) || it.node.type !== 'text') return;
    const home = acct.home.get(it.node);
    if (home) suppressed.add(home);
  });
  const matchOf = new Map(); // source child index -> draft item
  const sOfE = new Map();
  const visible = new Set(); // source child indexes the base shows under this parent
  sItems.forEach((it, x) => {
    if (!bOfS.has(x)) return; // shown elsewhere: emitted as it was (or dropped there)
    visible.add(it.s);
    const e = eOfB.get(bOfS.get(x));
    if (e !== undefined) {
      matchOf.set(it.s, items[e]);
      sOfE.set(e, it.s);
    }
  });
  // Draft items with no base item are new here. Each run takes the place of the first deleted source item between
  // the same neighbours, or goes right after the previous match, or right before the next one. Draft items that the
  // base shows here but the source holds elsewhere stay where the source has them.
  const beforeS = new Map();
  const afterS = new Map();
  const tail = [];
  const inserts = [];
  const put = (map, s, run) => { if (!map.has(s)) map.set(s, []); map.get(s).push(...run); };
  // A neighbour the base shows here but the source holds elsewhere is an anchor too: the run then follows it home.
  const homeOf = (e) => {
    const b = bOfE.get(e);
    if (b === undefined || sOfE.has(e)) return null;
    const it = bList[b];
    return it.node.type === 'text' ? acct.home.get(it.node) || null : B.srcIds.get(it.node.rsid) || null;
  };
  let prevS = -1;
  let prevHome = null;
  let e = 0;
  while (e < items.length) {
    if (bOfE.has(e)) {
      if (sOfE.has(e)) { prevS = sOfE.get(e); prevHome = null; } else prevHome = homeOf(e) || prevHome;
      e += 1;
      continue;
    }
    const run = [];
    while (e < items.length && !bOfE.has(e)) { run.push(items[e]); e += 1; }
    inserts.push(...run);
    if (prevHome) {
      put(acct.after, prevHome, run);
      continue;
    }
    let nextS = kids.length;
    let nextHome = null;
    for (let k = e; k < items.length; k += 1) {
      if (sOfE.has(k)) { nextS = sOfE.get(k); break; }
      if (!nextHome && bOfE.has(k)) nextHome = homeOf(k);
    }
    let gone = null;
    for (const s of visible) if (s > prevS && s < nextS && !matchOf.has(s) && (gone === null || s < gone)) gone = s;
    if (gone !== null) put(beforeS, gone, run);
    else if (prevS >= 0) put(afterS, prevS, run);
    else if (nextHome) put(acct.before, nextHome, run);
    else if (nextS < kids.length) put(beforeS, nextS, run);
    else tail.push(...run);
  }
  return { matchOf, visible, beforeS, afterS, tail, inserts };
}

function emitWalk(srcNode, ctx) {
  for (const child of srcNode.children) emitAnchored(child, ctx);
}

function emitAnchored(node, ctx) {
  const before = ctx.acct.before.get(node);
  if (before) for (const item of before) emitDraftNode(item.node, ctx);
  emitAnchoredNode(node, ctx);
  const after = ctx.acct.after.get(node);
  if (after) for (const item of after) emitDraftNode(item.node, ctx);
}

function emitAnchoredNode(node, ctx) {
  if (ctx.carriedSet.has(node)) return; // emitted after the moved element it follows
  if (node.type !== 'element') {
    if (!ctx.suppressed.has(node)) ctx.out.push(ctx.source.slice(node.start, node.end));
    return;
  }
  const id = node.rsid;
  if (ctx.B.real.has(id)) {
    if (!ctx.E.real.has(id) || ctx.placed.has(id)) return; // deleted, or emitted where the draft put it
    emitNode(node, ctx.E.real.get(id), ctx);
    return;
  }
  emitNode(node, null, ctx); // hidden by the cleaner (a script, an iframe): kept as it was
}

function emitMerge(srcNode, id, ctx) {
  const plan = ctx.plans.get(id);
  const emitRun = (run) => { for (const item of run || []) emitDraftItem(item, ctx); };
  srcNode.children.forEach((child, s) => {
    emitRun(plan.beforeS.get(s));
    if (plan.visible.has(s)) {
      const item = plan.matchOf.get(s);
      if (item) {
        if (child.type === 'element') emitNode(child, item.node, ctx);
        else ctx.out.push(ctx.source.slice(child.start, child.end));
      }
      emitRun(plan.afterS.get(s));
      return; // unmatched visible source items were deleted or moved
    }
    emitAnchored(child, ctx);
  });
  emitRun(plan.tail);
}

/* A draft item with no place in the source: a moved element, a new element, or new text. */
function emitDraftItem(item, ctx) {
  emitDraftNode(item.node, ctx);
}

function emitDraftNode(node, ctx) {
  if (node.type !== 'element') {
    ctx.out.push(ctx.draft.slice(node.start, node.end)); // already escaped by the browser serializer
    return;
  }
  if (ctx.E.kind.get(node) === 'real') {
    emitNode(ctx.srcIds.get(node.rsid), node, ctx);
    for (const follower of ctx.carried.get(node.rsid) || []) ctx.out.push(ctx.source.slice(follower.start, follower.end));
    return;
  }
  ctx.out.push(startTag(node.rawName, node.attrs));
  if (VOID.has(node.tag) && !node.foreign) return;
  for (const child of node.children) emitDraftNode(child, ctx);
  ctx.out.push(`</${node.rawName}>`);
}

function startTag(rawName, attrs) {
  let out = `<${rawName}`;
  for (const a of attrs || []) {
    if (isRsAttr(a.name)) continue;
    out += a.value === '' && a.raw === null ? ` ${a.name}` : ` ${a.name}="${escapeAttr(a.value)}"`;
  }
  return `${out}>`;
}

const isLeaf = (el) => (VOID.has(el.tag) && !el.foreign) || (el.foreign && el.selfClosing);

/* One source element with its draft counterpart (or null when the cleaner hid it). */
function emitNode(srcEl, draftEl, ctx) {
  const { source, out } = ctx;
  if (!draftEl) { // hidden by the cleaner: tags as they were, but edits to anything inside still apply
    if (srcEl.raw || !srcEl.children.some((child) => child.type === 'element' || (child.type === 'text' && ctx.suppressed.has(child)))) {
      out.push(source.slice(srcEl.start, srcEl.end));
      return;
    }
    out.push(source.slice(srcEl.start, srcEl.startEnd));
    emitWalk(srcEl, ctx);
    if (srcEl.hasEnd) out.push(source.slice(srcEl.endStart, srcEl.end));
    return;
  }
  const id = srcEl.rsid;
  const baseEl = ctx.B.real.get(id);
  const renamed = baseEl.tag !== draftEl.tag;
  const newName = renamed ? draftEl.rawName : srcEl.rawName;
  const bAttrs = attrMap(baseEl);
  const dAttrs = attrMap(draftEl);
  if (!renamed && sameAttrs(bAttrs, dAttrs)) out.push(source.slice(srcEl.start, srcEl.startEnd));
  else out.push(patchStart(srcEl, newName, bAttrs, dAttrs, source));
  if (isLeaf(srcEl)) {
    if (renamed && !VOID.has(draftEl.tag)) {
      if (ctx.changed.has(id)) emitMerge(srcEl, id, ctx);
      out.push(`</${newName}>`);
    }
    return;
  }
  if (ctx.changed.has(id)) emitMerge(srcEl, id, ctx);
  else emitWalk(srcEl, ctx);
  if (renamed) {
    if (!VOID.has(draftEl.tag)) out.push(`</${newName}>`);
  } else if (srcEl.hasEnd) {
    out.push(source.slice(srcEl.endStart, srcEl.end));
  }
}

/* A start tag with changed attributes. Untouched attributes keep their exact source text; attributes the cleaner
   removed (event handlers) are kept unless the user set them; changed values are rewritten; new ones are appended. */
function patchStart(srcEl, name, bAttrs, dAttrs, source) {
  let out = `<${name}`;
  const done = new Set();
  const spaced = (piece) => (/^[\t\n\f\r ]/.test(piece) ? piece : ` ${piece}`);
  for (const a of srcEl.attrs) { // every source attribute is the page's own, data-rs* named ones included
    const piece = spaced(source.slice(a.start, a.end));
    if (done.has(a.name)) { // a duplicate the browser ignores: as it was
      out += piece;
      continue;
    }
    done.add(a.name);
    if (!bAttrs.has(a.name)) { // hidden by the cleaner: kept, unless the user set it
      out += dAttrs.has(a.name) ? ` ${a.name}="${escapeAttr(dAttrs.get(a.name))}"` : piece;
      continue;
    }
    if (!dAttrs.has(a.name)) continue;
    const value = dAttrs.get(a.name);
    out += value === bAttrs.get(a.name) ? piece : ` ${a.name}="${escapeAttr(value)}"`;
  }
  for (const [k, v] of dAttrs) if (!done.has(k)) out += v === '' ? ` ${k}` : ` ${k}="${escapeAttr(v)}"`;
  const last = srcEl.attrs.length ? srcEl.attrs[srcEl.attrs.length - 1].end : srcEl.start + 1 + srcEl.rawName.length;
  return out + source.slice(last, srcEl.startEnd).replace(/[^\t\n\f\r />]/g, ''); // whitespace, a slash, the bracket
}

/* ------------------------------------------------------------------ helpers for the editor */

/* The attributes a draft carries for the editor (model.js serializeDraft keeps these and drops every other data-rs-*). */
const DRAFT_ATTRS = new Set([ID, 'data-rs-faq', 'data-rs-faq-ld']);

/* The draft without editor attributes: what HTML mode shows and what the test sends. */
export function stripRs(html) {
  const tree = parse(html, { strict: true });
  const out = [];
  let at = 0;
  for (const el of tree.elements) {
    for (const a of el.attrs) {
      if (!DRAFT_ATTRS.has(a.name)) continue;
      out.push(html.slice(at, a.start));
      at = a.end;
    }
  }
  out.push(html.slice(at));
  return out.join('');
}

function signature(el) {
  return `${shape(el)}|${el.textContent.replace(/\s+/g, ' ').trim().slice(0, 400)}`;
}

function shape(el) {
  const attrs = (el.attrs || []).filter((a) => !isRsAttr(a.name)).map((a) => `${a.name}=${a.value}`).sort().join('&');
  return `${el.tag}|${attrs}`;
}

/* Puts ids back on an edited copy of the draft (HTML mode). `edited` is a browser serialization without ids;
   `previous` is the draft before the edit. Elements are matched in document order: exact (tag, attributes, text)
   first; then, between those anchors, same tag and attributes; then same tag where a stretch kept its shape; and
   last, an unmatched element identical to an unmatched old one elsewhere (a move). Anything left is new, which is
   always safe: the export then writes it from the draft. Returns the edited HTML with data-rs-id attributes. */
export function reattachIds(previous, edited) {
  const before = parse(previous, { strict: true }).elements.filter((el) => el.rsid !== null && el.rsid !== undefined);
  const after = parse(edited, { strict: true });
  const now = after.elements;
  const ids = new Array(now.length).fill(null);
  const usedBefore = new Set();
  const take = (x, y) => { ids[y] = before[x].rsid; usedBefore.add(x); };
  const exact = lcs(before.map(signature), now.map(signature));
  for (const [x, y] of exact) take(x, y);
  const gaps = (pairs) => {
    const anchors = [[-1, -1], ...pairs.sort((p, q) => p[0] - q[0]), [before.length, now.length]];
    const out = [];
    for (let k = 0; k + 1 < anchors.length; k += 1) {
      const [x0, y0] = anchors[k];
      const [x1, y1] = anchors[k + 1];
      if (x1 - x0 > 1 && y1 - y0 > 1) out.push([x0 + 1, x1, y0 + 1, y1]);
    }
    return out;
  };
  let matched = [...exact];
  for (const [xa, xb, ya, yb] of gaps(matched)) {
    for (const [x, y] of lcs(before.slice(xa, xb).map(shape), now.slice(ya, yb).map(shape))) {
      take(xa + x, ya + y);
      matched.push([xa + x, ya + y]);
    }
  }
  for (const [xa, xb, ya, yb] of gaps(matched)) {
    const a = before.slice(xa, xb);
    const b = now.slice(ya, yb);
    if (a.length === b.length && a.every((el, k) => el.tag === b[k].tag)) a.forEach((el, k) => take(xa + k, ya + k));
  }
  const left = new Map();
  before.forEach((el, x) => {
    if (usedBefore.has(x)) return;
    const sig = signature(el);
    left.set(sig, left.has(sig) ? -1 : x);
  });
  now.forEach((el, y) => {
    if (ids[y] !== null) return;
    const x = left.get(signature(el));
    if (x !== undefined && x >= 0) {
      take(x, y);
      left.set(signature(el), -1);
    }
  });
  const edits = []; // [at, skipTo, insert]
  now.forEach((el, k) => {
    for (const a of el.attrs) if (a.name === ID) edits.push([a.start, a.end, '']); // pasted ids: the alignment decides
    if (ids[k] !== null) edits.push([el.start + 1 + el.rawName.length, el.start + 1 + el.rawName.length, ` ${ID}="${ids[k]}"`]);
  });
  edits.sort((p, q) => p[0] - q[0] || p[1] - q[1]);
  const out = [];
  let at = 0;
  for (const [cut, skip, insert] of edits) {
    out.push(edited.slice(at, cut), insert);
    at = Math.max(at, skip, cut);
  }
  out.push(edited.slice(at));
  return out.join('');
}
