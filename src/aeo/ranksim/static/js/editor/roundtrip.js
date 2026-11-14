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
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', copy: '©', reg: '®', trade: '™',
  hellip: '…', mdash: '—', ndash: '–', lsquo: '‘', rsquo: '’', sbquo: '‚', ldquo: '“',
  rdquo: '”', bdquo: '„', bull: '•', middot: '·', laquo: '«', raquo: '»', lsaquo: '‹',
  rsaquo: '›', times: '×', divide: '÷', deg: '°', euro: '€', pound: '£', yen: '¥',
  cent: '¢', sect: '§', para: '¶', plusmn: '±', frac12: '½', frac14: '¼', frac34: '¾',
  sup1: '¹', sup2: '²', sup3: '³', micro: 'µ', iexcl: '¡', iquest: '¿', shy: '­',
  ensp: ' ', emsp: ' ', thinsp: ' ', zwj: '‍', zwnj: '‌', lrm: '‎', rlm: '‏',
  larr: '←', rarr: '→', uarr: '↑', darr: '↓', harr: '↔', rArr: '⇒', lArr: '⇐',
  hearts: '♥', check: '✓', star: '☆', starf: '★', prime: '′', Prime: '″', dagger: '†',
  Dagger: '‡', permil: '‰', minus: '−', le: '≤', ge: '≥', ne: '≠', asymp: '≈',
  infin: '∞', not: '¬', ordf: 'ª', ordm: 'º', acute: '´', cedil: '¸', uml: '¨',
  macr: '¯', brvbar: '¦', curren: '¤', szlig: 'ß', aacute: 'á', Aacute: 'Á',
  agrave: 'à', Agrave: 'À', acirc: 'â', auml: 'ä', Auml: 'Ä', atilde: 'ã', aring: 'å',
  aelig: 'æ', ccedil: 'ç', Ccedil: 'Ç', eacute: 'é', Eacute: 'É', egrave: 'è',
  Egrave: 'È', ecirc: 'ê', euml: 'ë', iacute: 'í', Iacute: 'Í', igrave: 'ì', icirc: 'î',
  iuml: 'ï', ntilde: 'ñ', Ntilde: 'Ñ', oacute: 'ó', Oacute: 'Ó', ograve: 'ò', ocirc: 'ô',
  ouml: 'ö', Ouml: 'Ö', otilde: 'õ', oslash: 'ø', Oslash: 'Ø', uacute: 'ú', Uacute: 'Ú',
  ugrave: 'ù', ucirc: 'û', uuml: 'ü', Uuml: 'Ü', yacute: 'ý', yuml: 'ÿ', oelig: 'œ',
  scaron: 'š', Scaron: 'Š', zcaron: 'ž', fnof: 'ƒ', circ: 'ˆ', tilde: '˜', alpha: 'α',
  beta: 'β', gamma: 'γ', delta: 'δ', pi: 'π', sigma: 'σ', mu: 'μ', Omega: 'Ω',
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
    if (code === 0 || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) return '�';
    return String.fromCodePoint(code);
  });
  return out;
}

export function escapeText(text) {
  return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/ /g, '&nbsp;');
}

export function escapeAttr(text) {
  return String(text).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/ /g, '&nbsp;');
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
    const raw = !foreign && (RAW.has(name) || RCDATA.has(name) || name === 'plaintext');
    if (raw) {
      el.raw = RAW.has(name) || name === 'plaintext';
      let close = -1;
      if (name !== 'plaintext') {
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

/* The source with data-rs-id="N" added to every start tag, in the same order parse() numbers them. */
export function annotate(source) {
  const tree = parse(source);
  const out = [];
  let at = 0;
  for (const el of tree.elements) {
    const cut = el.start + 1 + el.rawName.length;
    out.push(source.slice(at, cut), ` ${ID}="${el.rsid}"`);
    at = cut;
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

const isXid = (id) => typeof id === 'string' && id.charCodeAt(0) === 120; // 'x'

/* Indexes a strict parse of base or draft. Elements are real (map to a source element), see-through (made up by the
   parser, like an implied tbody: their children belong to the parent) or new (no id: typed or inserted by the user). */
function indexTree(tree, srcIds, baseCounts) {
  const real = new Map();
  const seen = new Map();
  const kind = new Map();
  for (const el of tree.elements) {
    const id = el.rsid;
    if (id === null || id === undefined) { kind.set(el, 'new'); continue; }
    if (isXid(id)) { kind.set(el, 'see'); continue; }
    if (!srcIds.has(id)) { kind.set(el, 'new'); continue; }
    const k = (seen.get(id) || 0) + 1;
    seen.set(id, k);
    if (k === 1) {
      kind.set(el, 'real');
      real.set(id, el);
    } else if (baseCounts && k > (baseCounts.get(id) || 1)) {
      kind.set(el, 'new'); // a copy the user pasted
    } else {
      kind.set(el, 'see'); // a copy the parser made (misnested formatting); see-through on both sides
    }
  }
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
  return { tree, real, kind, lists, parentOf, seen };
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
  for (const a of el.attrs || []) if (!a.name.startsWith('data-rs') && !map.has(a.name)) map.set(a.name, a.value);
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

/* The original page with the draft's edits applied. */
export function exportHtml(source, base, draft) {
  const src = parse(source);
  const srcIds = new Map(src.elements.map((el) => [el.rsid, el]));
  const B = indexTree(parse(base, { strict: true }), srcIds, null);
  const E = indexTree(parse(draft, { strict: true }), srcIds, B.seen);
  const changed = new Set();
  for (const [id, list] of E.lists) if (id === '' || B.real.has(id)) if (!sameKeys(B.lists.get(id), list)) changed.add(id);

  // Ids that the draft places somewhere new; the source walk skips them where they used to be.
  const placed = new Set();
  const plans = new Map();
  for (const id of changed) {
    const plan = planMerge(id === '' ? src : srcIds.get(id), id, B, E);
    plans.set(id, plan);
    for (const item of plan.inserts) markPlaced(item.node, E, placed);
  }

  const out = [];
  const ctx = { source, draft, src, srcIds, B, E, changed, plans, placed, out };
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

function planMerge(srcNode, id, B, E) {
  const kids = srcNode.children;
  const vis = [];
  kids.forEach((child, s) => {
    if (child.type === 'text') {
      if (srcNode.raw) vis.push({ s, key: `t${child.text}` });
      else if (!isSpace(child.text)) vis.push({ s, key: `t${decodeText(child)}` });
    } else if (child.type === 'element' && B.real.has(child.rsid) && B.parentOf.get(child.rsid) === id) {
      vis.push({ s, key: `#${child.rsid}` });
    }
  });
  const items = E.lists.get(id) || [];
  const pairs = lcs(vis.map((v) => v.key), items.map((it) => it.key), (x, y) => x !== null && x === y);
  const matchOf = new Map(); // source index -> draft item
  const matchedItems = new Set();
  for (const [v, e] of pairs) {
    matchOf.set(vis[v].s, items[e]);
    matchedItems.add(e);
  }
  const visible = new Set(vis.map((v) => v.s));
  // Each run of unmatched draft items takes the place of the first deleted source item between the same neighbours,
  // or goes right after the previous match, or right before the next one.
  const sOfItem = new Map(pairs.map(([v, e]) => [e, vis[v].s]));
  const beforeS = new Map();
  const afterS = new Map();
  const tail = [];
  const inserts = [];
  const put = (map, s, run) => { if (!map.has(s)) map.set(s, []); map.get(s).push(...run); };
  let e = 0;
  let prevS = -1;
  while (e < items.length) {
    if (matchedItems.has(e)) { prevS = sOfItem.get(e); e += 1; continue; }
    const run = [];
    while (e < items.length && !matchedItems.has(e)) { run.push(items[e]); e += 1; }
    inserts.push(...run);
    const nextS = e < items.length ? sOfItem.get(e) : kids.length;
    const gone = vis.find((v) => v.s > prevS && v.s < nextS && !matchOf.has(v.s));
    if (gone) put(beforeS, gone.s, run);
    else if (prevS >= 0) put(afterS, prevS, run);
    else if (nextS < kids.length) put(beforeS, nextS, run);
    else tail.push(...run);
  }
  return { matchOf, visible, beforeS, afterS, tail, inserts };
}

function emitWalk(srcNode, ctx) {
  for (const child of srcNode.children) emitAnchored(child, ctx);
}

function emitAnchored(node, ctx) {
  if (node.type !== 'element') {
    ctx.out.push(ctx.source.slice(node.start, node.end));
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
        emitRun(plan.afterS.get(s));
      }
      return; // unmatched visible source items were deleted or moved
    }
    emitAnchored(child, ctx);
  });
  emitRun(plan.tail);
}

/* A draft item with no place in the source: a moved element, a new element, or new text. */
function emitDraftItem(item, ctx) {
  const node = item.node;
  if (node.type === 'text') {
    ctx.out.push(ctx.draft.slice(node.start, node.end)); // already escaped by the browser serializer
    return;
  }
  emitDraftNode(node, ctx);
}

function emitDraftNode(node, ctx) {
  if (node.type === 'text') {
    ctx.out.push(ctx.draft.slice(node.start, node.end));
    return;
  }
  if (node.type === 'comment' || node.type === 'other') {
    ctx.out.push(ctx.draft.slice(node.start, node.end));
    return;
  }
  const k = ctx.E.kind.get(node);
  if (k === 'real') {
    emitNode(ctx.srcIds.get(node.rsid), node, ctx);
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
    if (a.name.startsWith('data-rs')) continue;
    out += a.value === '' && a.raw === null ? ` ${a.name}` : ` ${a.name}="${escapeAttr(a.value)}"`;
  }
  return `${out}>`;
}

/* One source element with its draft counterpart (or null when the cleaner hid it). */
function emitNode(srcEl, draftEl, ctx) {
  const { source, out } = ctx;
  if (!draftEl) { // hidden by the cleaner: tags as they were, but edits to anything inside still apply
    if (srcEl.raw || !srcEl.children.some((child) => child.type === 'element')) {
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
  if (srcEl.startEnd === srcEl.end && !srcEl.hasEnd) { // void or self-closed
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
   removed (event handlers) are kept; changed values are rewritten; new ones are appended. */
function patchStart(srcEl, name, bAttrs, dAttrs, source) {
  let out = `<${name}`;
  const done = new Set();
  let last = srcEl.start + 1 + srcEl.rawName.length;
  for (const a of srcEl.attrs) {
    const piece = source.slice(a.start, a.end);
    last = a.end;
    if (a.name.startsWith('data-rs') || done.has(a.name) || !bAttrs.has(a.name)) {
      if (!a.name.startsWith('data-rs')) out += piece; // hidden or duplicate: as it was
      continue;
    }
    done.add(a.name);
    if (!dAttrs.has(a.name)) continue;
    const value = dAttrs.get(a.name);
    if (value === bAttrs.get(a.name)) out += piece;
    else out += ` ${a.name}="${escapeAttr(value)}"`;
  }
  for (const [k, v] of dAttrs) if (!bAttrs.has(k)) out += v === '' ? ` ${k}` : ` ${k}="${escapeAttr(v)}"`;
  return out + source.slice(last, srcEl.startEnd); // whitespace, an optional slash and the closing bracket
}

/* ------------------------------------------------------------------ helpers for the editor */

/* The draft without editor attributes: what HTML mode shows and what the test sends. */
export function stripRs(html) {
  const tree = parse(html, { strict: true });
  const out = [];
  let at = 0;
  for (const el of tree.elements) {
    for (const a of el.attrs) {
      if (!a.name.startsWith('data-rs')) continue;
      out.push(html.slice(at, a.start));
      at = a.end;
    }
  }
  out.push(html.slice(at));
  return out.join('');
}

function signature(el) {
  const attrs = (el.attrs || []).filter((a) => !a.name.startsWith('data-rs')).map((a) => `${a.name}=${a.value}`).sort().join('&');
  return `${el.tag}|${attrs}|${el.textContent.replace(/\s+/g, ' ').trim().slice(0, 400)}`;
}

/* Puts ids back on an edited copy of the draft (HTML mode). `edited` is a browser serialization without ids;
   `previous` is the draft before the edit. Elements are matched in document order, exact signature first, then by tag
   between the anchors. Returns the edited HTML with data-rs-id attributes. */
export function reattachIds(previous, edited) {
  const before = parse(previous, { strict: true }).elements.filter((el) => el.rsid !== null && el.rsid !== undefined);
  const after = parse(edited, { strict: true });
  const now = after.elements;
  const ids = new Array(now.length).fill(null);
  const exact = lcs(before.map(signature), now.map(signature));
  for (const [x, y] of exact) ids[y] = before[x].rsid;
  // between anchors, match by tag
  const anchors = [[-1, -1], ...exact, [before.length, now.length]];
  for (let k = 0; k + 1 < anchors.length; k += 1) {
    const [x0, y0] = anchors[k];
    const [x1, y1] = anchors[k + 1];
    if (x1 - x0 <= 1 || y1 - y0 <= 1) continue;
    const a = before.slice(x0 + 1, x1);
    const b = now.slice(y0 + 1, y1);
    for (const [x, y] of lcs(a.map((el) => el.tag), b.map((el) => el.tag))) ids[y0 + 1 + y] = a[x].rsid;
  }
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
