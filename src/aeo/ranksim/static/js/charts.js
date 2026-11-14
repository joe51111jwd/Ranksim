/* Ranksim charts. Hand-written inline SVG, no libraries (the CSP blocks CDNs).

   Every chart function is pure: it takes plain data and returns a small node tree
   ({tag, attrs, kids}) so it can be tested in node without a DOM. `toSvg(tree)` makes a string,
   `mount(tree, doc)` makes real SVG elements (createElementNS, no innerHTML), and `fit(host, draw)`
   redraws at the host's real pixel width so text stays 1:1 and lines stay crisp at 2x.

   Colors come from CSS classes (rc-you, rc-rival, rc-other, rc-grid, rc-text, rc-muted, rc-pred), never
   inline styles, because the CSP forbids style attributes. css/base.css maps them to the theme tokens.

   Accessibility: each chart is role="img" with a <title> and a <desc> that states the data in words,
   and every mark has its own <title> for hover. Copy is plain and never uses em or en dashes. */

const NS = 'http://www.w3.org/2000/svg';
const FONT = 12;
const CHAR = 6.6; /* average Geist width at 12px, used to truncate labels without measuring */

let uid = 0;
const nextId = (prefix) => `${prefix}-${(uid += 1)}`;

/* ------------------------------------------------------------------ tree helpers */

export function el(tag, attrs, ...kids) {
  return { tag, attrs: attrs || {}, kids: kids.flat(Infinity).filter((kid) => kid != null && kid !== false) };
}

const text = (value) => ({ text: String(value) });

export function esc(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
}

export function toSvg(node) {
  if (node.text != null) return esc(node.text);
  const attrs = Object.entries(node.attrs)
    .filter(([, value]) => value != null && value !== false)
    .map(([key, value]) => ` ${key}="${esc(value)}"`)
    .join('');
  const inner = node.kids.map(toSvg).join('');
  return `<${node.tag}${node.tag === 'svg' ? ` xmlns="${NS}"` : ''}${attrs}>${inner}</${node.tag}>`;
}

export function mount(node, doc = globalThis.document) {
  if (node.text != null) return doc.createTextNode(node.text);
  const made = doc.createElementNS(NS, node.tag);
  for (const [key, value] of Object.entries(node.attrs)) {
    if (value != null && value !== false) made.setAttribute(key, String(value));
  }
  for (const kid of node.kids) made.append(mount(kid, doc));
  return made;
}

/* Redraw a chart at the host's width. draw(width) returns a tree. Returns a function that stops watching. */
export function fit(host, draw, { min = 240 } = {}) {
  let last = 0;
  const paint = () => {
    const width = Math.max(min, Math.floor(host.clientWidth || min));
    if (width === last) return;
    last = width;
    host.replaceChildren(mount(draw(width), host.ownerDocument));
  };
  paint();
  if (typeof ResizeObserver === 'undefined') return () => {};
  const watcher = new ResizeObserver(() => paint());
  watcher.observe(host);
  return () => watcher.disconnect();
}

/* ------------------------------------------------------------------ small utilities */

export function clip(label, maxWidth, size = FONT) {
  const value = String(label == null ? '' : label);
  const fits = Math.max(1, Math.floor(maxWidth / (CHAR * (size / FONT))));
  return value.length <= fits ? value : `${value.slice(0, Math.max(1, fits - 1)).trimEnd()}…`;
}

/* Half-pixel snap so 1px strokes land on device pixels. */
const crisp = (value) => Math.round(value) + 0.5;
const r1 = (value) => Math.round(value * 10) / 10;

function frame(width, height, title, desc, kids, cls) {
  const titleId = nextId('rc-t');
  const descId = nextId('rc-d');
  return el('svg', {
    class: `rc${cls ? ` ${cls}` : ''}`,
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-labelledby': `${titleId} ${descId}`,
    focusable: 'false',
  }, el('title', { id: titleId }, text(title)), el('desc', { id: descId }, text(desc)), kids);
}

function empty(width, title, message) {
  const height = 64;
  return frame(width, height, title, message, [
    el('text', { class: 'rc-muted', x: width / 2, y: height / 2 + 4, 'text-anchor': 'middle' }, text(message)),
  ], 'rc-empty');
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many || `${one}s`}`;

/* ------------------------------------------------------------------ 1. where you rank, per search */

/* rows: [{q, position (1..10 or null), checked, predicted: {position, of} | null}]
   One line per search. x is the Google position 1 to 10, with a column at the right for "not in top 10".
   Real positions are solid dots; PREDICTED positions, where the model scored the page, are open rings. */
export function rankDots(rows, { width = 560, rowHeight = 30, labelWidth } = {}) {
  const title = 'Where you rank per search';
  const list = (rows || []).filter((row) => row && row.q);
  if (!list.length) return empty(width, title, 'No searches yet');
  const sorted = list.slice().sort((a, b) => (a.position || 99) - (b.position || 99) || String(a.q).localeCompare(String(b.q)));
  const label = labelWidth || Math.min(220, Math.max(96, Math.round(width * 0.36)));
  const top = 24;
  const right = 12;
  const outW = 44;
  const plotX = label + 12;
  const plotW = Math.max(80, width - plotX - right - outW);
  const x = (pos) => plotX + ((pos - 1) / 9) * plotW;
  const outX = plotX + plotW + outW / 2 + 6;
  const height = top + sorted.length * rowHeight + 8;
  const kids = [];

  for (const pos of [1, 3, 10]) {
    kids.push(el('line', { class: 'rc-grid', x1: crisp(x(pos)), x2: crisp(x(pos)), y1: top - 6, y2: height - 6 }));
    kids.push(el('text', { class: 'rc-muted', x: r1(x(pos)), y: 12, 'text-anchor': 'middle' }, text(`#${pos}`)));
  }
  kids.push(el('text', { class: 'rc-muted', x: r1(outX), y: 12, 'text-anchor': 'middle' }, text('Out')));

  let ranked = 0;
  let withPrediction = 0;
  sorted.forEach((row, index) => {
    const cy = top + index * rowHeight + rowHeight / 2;
    const real = row.position && row.position >= 1 && row.position <= 10 ? row.position : null;
    const pred = row.predicted && row.predicted.position != null ? Number(row.predicted.position) : null;
    if (real) ranked += 1;
    const g = [];
    g.push(el('title', null, text(`${row.q}: ${real ? `#${real} on Google` : row.checked === false ? 'not checked yet' : 'not in the top 10'}${pred != null ? `, PREDICTED #${pred}${row.predicted.of ? ` of ${row.predicted.of}` : ''}` : ''}`)));
    g.push(el('text', { class: 'rc-text', x: label, y: r1(cy + 4), 'text-anchor': 'end' }, text(clip(row.q, label))));
    g.push(el('line', { class: 'rc-track', x1: r1(x(1)), x2: r1(x(10)), y1: crisp(cy), y2: crisp(cy) }));
    if (pred != null && pred >= 1) {
      withPrediction += 1;
      const px = pred <= 10 ? x(pred) : outX;
      g.push(el('circle', { class: 'rc-pred', cx: r1(px), cy: r1(cy), r: 6.5 }));
    }
    if (real) {
      g.push(el('circle', { class: real <= 3 ? 'rc-you rc-strong' : 'rc-you', cx: r1(x(real)), cy: r1(cy), r: 5 }));
    } else {
      g.push(el('circle', { class: row.checked === false ? 'rc-none' : 'rc-other', cx: r1(outX), cy: r1(cy), r: 4 }));
    }
    kids.push(el('g', { class: 'rc-row' }, g));
  });

  const desc = `${plural(sorted.length, 'search', 'searches')}. You rank in the top 10 for ${ranked}.`
    + (withPrediction ? ` Open rings are PREDICTED positions for ${plural(withPrediction, 'search', 'searches')}.` : '');
  return frame(width, height, title, desc, kids, 'rc-dots');
}

/* ------------------------------------------------------------------ 2. position distribution */

export const BUCKETS = [
  { key: 'p1', label: '#1', test: (p) => p === 1 },
  { key: 'p2', label: '#2 to 3', test: (p) => p >= 2 && p <= 3 },
  { key: 'p4', label: '#4 to 10', test: (p) => p >= 4 && p <= 10 },
  { key: 'out', label: 'Not in top 10', test: (p, row) => !p && row.checked !== false },
  { key: 'unk', label: 'Not checked', test: (p, row) => !p && row.checked === false },
];

export function distribution(rows) {
  const counts = BUCKETS.map((bucket) => ({ key: bucket.key, label: bucket.label, count: 0 }));
  for (const row of rows || []) {
    const pos = row && row.position >= 1 && row.position <= 10 ? row.position : null;
    const at = BUCKETS.findIndex((bucket) => bucket.test(pos, row || {}));
    if (at >= 0) counts[at].count += 1;
  }
  return counts;
}

/* Columns, one per bucket. Hides "Not checked" when it is zero. */
export function positionColumns(rows, { width = 360, height = 180 } = {}) {
  const title = 'Position distribution';
  const counts = distribution(rows).filter((bucket) => bucket.key !== 'unk' || bucket.count);
  const total = counts.reduce((sum, bucket) => sum + bucket.count, 0);
  if (!total) return empty(width, title, 'No searches yet');
  const most = Math.max(1, ...counts.map((bucket) => bucket.count));
  const top = 22;
  const bottom = 26;
  const plotH = height - top - bottom;
  const gap = Math.max(8, Math.round(width * 0.04));
  const colW = (width - gap * (counts.length - 1)) / counts.length;
  const base = top + plotH;
  const kids = [el('line', { class: 'rc-axis', x1: 0, x2: width, y1: crisp(base), y2: crisp(base) })];
  counts.forEach((bucket, index) => {
    const x0 = index * (colW + gap);
    const barH = bucket.count ? Math.max(3, (bucket.count / most) * plotH) : 0;
    const cls = bucket.key === 'out' || bucket.key === 'unk' ? 'rc-other' : bucket.key === 'p4' ? 'rc-you' : 'rc-you rc-strong';
    kids.push(el('g', { class: 'rc-col' },
      el('title', null, text(`${bucket.label}: ${plural(bucket.count, 'search', 'searches')}`)),
      barH ? el('rect', { class: cls, x: r1(x0), y: r1(base - barH), width: r1(colW), height: r1(barH), rx: Math.min(6, colW / 4) }) : null,
      el('text', { class: 'rc-value', x: r1(x0 + colW / 2), y: r1(base - barH - 6), 'text-anchor': 'middle' }, text(bucket.count)),
      el('text', { class: 'rc-muted', x: r1(x0 + colW / 2), y: height - 8, 'text-anchor': 'middle' }, text(clip(bucket.label, colW + gap - 2)))));
  });
  const desc = counts.map((bucket) => `${bucket.label}: ${bucket.count}`).join('. ') + '.';
  return frame(width, height, title, desc, kids, 'rc-cols');
}

/* ------------------------------------------------------------------ 3. who takes the top 10 */

/* share: [{domain, slots, searches, average, you, competitor, platform}] */
export function shareBars(share, { width = 560, limit = 8, rowHeight = 28, labelWidth } = {}) {
  const title = 'Who takes the top 10';
  const list = (share || []).filter((row) => row && row.domain && row.slots > 0);
  if (!list.length) return empty(width, title, 'No rankings pulled yet');
  const mine = list.find((row) => row.you);
  let shown = list.slice(0, limit);
  if (mine && !shown.includes(mine)) shown = shown.slice(0, limit - 1).concat([mine]);
  const most = Math.max(1, ...shown.map((row) => row.slots));
  const total = list.reduce((sum, row) => sum + row.slots, 0);
  const label = labelWidth || Math.min(200, Math.max(96, Math.round(width * 0.32)));
  const valueW = 64;
  const plotX = label + 12;
  const plotW = Math.max(60, width - plotX - valueW);
  const height = shown.length * rowHeight + 4;
  const kids = [];
  shown.forEach((row, index) => {
    const y = index * rowHeight;
    const barW = Math.max(3, (row.slots / most) * plotW);
    const cls = row.you ? 'rc-you' : row.competitor ? 'rc-rival' : 'rc-other';
    const name = row.you ? `${row.domain} (you)` : row.domain;
    const part = total ? Math.round((row.slots / total) * 100) : 0;
    kids.push(el('g', { class: `rc-row${row.you ? ' rc-is-you' : ''}` },
      el('title', null, text(`${name}: ${plural(row.slots, 'top 10 spot')}, ${part}% of all spots${row.average != null ? `, average #${row.average}` : ''}`)),
      el('text', { class: row.you ? 'rc-text rc-bold' : 'rc-text', x: label, y: r1(y + rowHeight / 2 + 4), 'text-anchor': 'end' }, text(clip(row.domain, label))),
      el('rect', { class: cls, x: plotX, y: r1(y + rowHeight / 2 - 6), width: r1(barW), height: 12, rx: 6 }),
      el('text', { class: 'rc-value', x: r1(plotX + barW + 8), y: r1(y + rowHeight / 2 + 4) }, text(`${row.slots} · ${part}%`))));
  });
  const desc = shown.map((row) => `${row.domain}${row.you ? ' (you)' : ''} ${row.slots}`).join(', ') + `. ${plural(total, 'spot')} in all.`;
  return frame(width, height, title, desc, kids, 'rc-share');
}

/* ------------------------------------------------------------------ 4. PREDICTED versus real */

/* Only searches where both a real Google position and a PREDICTED position exist.
   x = real position, y = PREDICTED position; dots on the diagonal mean the model agreed. */
export function predictedVsReal(rows, { width = 300, height = 260 } = {}) {
  const title = 'PREDICTED versus real position';
  const pairs = (rows || []).filter((row) => row && row.position >= 1 && row.predicted && row.predicted.position >= 1)
    .map((row) => ({ q: row.q, real: Number(row.position), pred: Number(row.predicted.position), of: row.predicted.of }));
  if (!pairs.length) return empty(width, title, 'No PREDICTED positions yet');
  const maxPos = Math.max(10, ...pairs.map((pair) => Math.max(pair.real, pair.pred)));
  const pad = { left: 30, right: 10, top: 10, bottom: 30 };
  const side = Math.max(80, Math.min(width - pad.left - pad.right, height - pad.top - pad.bottom));
  const x = (pos) => pad.left + ((pos - 1) / (maxPos - 1)) * side;
  const y = (pos) => pad.top + ((pos - 1) / (maxPos - 1)) * side;
  const kids = [
    el('rect', { class: 'rc-plot', x: pad.left, y: pad.top, width: side, height: side }),
    el('line', { class: 'rc-diag', x1: x(1), y1: y(1), x2: x(maxPos), y2: y(maxPos) }),
  ];
  for (const pos of [1, maxPos]) {
    kids.push(el('text', { class: 'rc-muted', x: r1(x(pos)), y: pad.top + side + 16, 'text-anchor': 'middle' }, text(`#${pos}`)));
    kids.push(el('text', { class: 'rc-muted', x: pad.left - 6, y: r1(y(pos) + 4), 'text-anchor': 'end' }, text(`#${pos}`)));
  }
  kids.push(el('text', { class: 'rc-muted', x: r1(pad.left + side / 2), y: pad.top + side + 26, 'text-anchor': 'middle' }, text('Real')));
  let agree = 0;
  for (const pair of pairs) {
    const close = Math.abs(pair.real - pair.pred) <= 1;
    if (close) agree += 1;
    kids.push(el('g', { class: 'rc-pt' },
      el('title', null, text(`${pair.q}: real #${pair.real}, PREDICTED #${pair.pred}${pair.of ? ` of ${pair.of}` : ''}`)),
      el('circle', { class: close ? 'rc-you' : 'rc-rival', cx: r1(x(pair.real)), cy: r1(y(pair.pred)), r: 5 })));
  }
  const total = pad.top + side + pad.bottom;
  const desc = `${plural(pairs.length, 'search', 'searches')} with both. PREDICTED within one place of real for ${agree}.`;
  return frame(width, total, title, desc, kids, 'rc-scatter');
}

/* ------------------------------------------------------------------ 5. opportunities */

/* Searches that could move: ranking 4 to 20 with a page (closest to the top 3 first), then searches where
   the top 10 was checked and you have no page. Pure data; the Overview renders it as a list. */
export function opportunities(rows, { limit = 6 } = {}) {
  const list = rows || [];
  const near = list.filter((row) => row && row.position >= 4 && row.position <= 20)
    .sort((a, b) => a.position - b.position)
    .map((row) => ({ kind: 'move', q: row.q, position: row.position, url: row.url || null, gap: row.position - 3 }));
  const missing = list.filter((row) => row && !row.position && row.checked !== false && !row.url)
    .map((row) => ({ kind: 'page', q: row.q, position: null, url: null, gap: null }));
  return near.concat(missing).slice(0, limit);
}

/* ------------------------------------------------------------------ 6. sparkline, for tiles such as rank history */

/* values: numbers (positions, lower is better when invert is true). Nulls break the line. */
export function sparkline(values, { width = 160, height = 40, invert = true, title = 'Trend', label } = {}) {
  const points = (values || []).map((value, index) => ({ index, value: value == null ? null : Number(value) }));
  const real = points.filter((point) => point.value != null && Number.isFinite(point.value));
  if (real.length < 2) return empty(width, title, 'Not enough history yet');
  const lo = Math.min(...real.map((point) => point.value));
  const hi = Math.max(...real.map((point) => point.value));
  const span = hi - lo || 1;
  const pad = 4;
  const x = (index) => pad + (index / Math.max(1, points.length - 1)) * (width - pad * 2);
  const y = (value) => {
    const t = (value - lo) / span;
    return pad + (invert ? t : 1 - t) * (height - pad * 2);
  };
  let d = '';
  let pen = false;
  for (const point of points) {
    if (point.value == null || !Number.isFinite(point.value)) {
      pen = false;
      continue;
    }
    d += `${pen ? 'L' : 'M'}${r1(x(point.index))} ${r1(y(point.value))}`;
    pen = true;
  }
  const last = real[real.length - 1];
  const desc = label || `${real.length} points from ${real[0].value} to ${last.value}.`;
  return frame(width, height, title, desc, [
    el('path', { class: 'rc-line', d, fill: 'none' }),
    el('circle', { class: 'rc-you', cx: r1(x(last.index)), cy: r1(y(last.value)), r: 3 }),
  ], 'rc-spark');
}
