/* Rank history (track workstream): Overview tile "Rank changes", a trend column in Searches, a History screen
   (/app/<slug>/track-history) whose rows open a per-search chart, and a Settings section for the re-check schedule and the monthly search meter.

   Data: report.history (compact, from plugins/tracking.py extend_report), GET /api/v1/projects/{slug}/history (full,
   for the sheet), GET /api/v1/tracking/status and GET|POST /api/v1/projects/{slug}/tracking. Every position shown is
   a REAL Google position from a stored pull; an empty point means not in the top 10 we hold. Charts are hand-written
   SVG: lower position numbers are drawn higher. */

import { registerOverviewTile, registerSearchColumn, registerSettingsSection, registerScreen, on } from '../hooks.js';
import { S, SVGNS, h, api, enc, bar, toast, openSheet, plural, page, link, icon, noSearchData } from '../core.js';

const SERIES = ['history-you', 'history-c1', 'history-c2'];
const DEPTH = 10;
const full = new Map(); // slug -> Promise of the full history

on('report-loaded', ({ slug, report }) => {
  full.delete(slug);
  baselineFor(slug); // the check seen on the last visit, read once per page load, before this visit is recorded
  const last = report && report.history && report.history.last_at;
  if (last) writeSeen(slug, last);
});

/* ------------------------------------------------------------------ since the last visit */

/* The newest check the owner has seen, per site, in this browser. Read once per page load so the changes stay visible
   for the whole visit; the visit itself is recorded straight away for next time. Storage can be unavailable (private
   mode, blocked site data): then every visit compares the latest check with the one before it. */
const SEEN_KEY = (slug) => `ranksim.track.seen.${slug}`;
const baseline = new Map(); // slug -> ISO time of the last check seen before this page load, or null
const dismissed = new Set();

function readSeen(slug) {
  try {
    return window.localStorage.getItem(SEEN_KEY(slug));
  } catch (err) {
    return null;
  }
}

function writeSeen(slug, iso) {
  try {
    window.localStorage.setItem(SEEN_KEY(slug), iso);
  } catch (err) {
    /* nothing to remember with; the next visit compares the last two checks */
  }
}

function baselineFor(slug) {
  if (!baseline.has(slug)) baseline.set(slug, readSeen(slug));
  return baseline.get(slug);
}

/* How a search moved since the check the owner last saw: { change, delta, from, to } with the server's words
   (up, down, new, lost, same), or null when there is nothing to compare. */
export function sinceVisit(row, seenAt) {
  const spark = (row && row.spark) || [];
  const at = (row && row.spark_at) || [];
  const n = spark.length;
  if (n < 2 || at.length !== n) return null;
  let base = n - 2; // no remembered visit: the check before the latest one
  if (seenAt) {
    base = -1;
    for (let i = 0; i < n; i += 1) if (at[i] <= seenAt) base = i;
    if (base === n - 1) return null; // the latest check was already seen
    if (base < 0) base = 0; // the visit was before the oldest check kept here
  }
  const from = spark[base];
  const to = spark[n - 1];
  if (from == null && to == null) return null;
  if (from == null) return { change: 'new', delta: null, from, to };
  if (to == null) return { change: 'lost', delta: null, from, to };
  if (to < from) return { change: 'up', delta: from - to, from, to };
  if (to > from) return { change: 'down', delta: from - to, from, to };
  return { change: 'same', delta: 0, from, to };
}

function visitChanges(R, slug) {
  const H = R && R.history;
  if (!H || !H.searches) return null;
  const seenAt = baselineFor(slug);
  const counts = { up: 0, down: 0, new: 0, lost: 0 };
  const moved = [];
  for (const row of H.searches) {
    const move = sinceVisit(row, seenAt);
    if (move && counts[move.change] != null) {
      counts[move.change] += 1;
      moved.push({ search: row.search, ...move });
    }
  }
  return moved.length ? { counts, moved, seenAt } : null;
}

/* "3 searches moved up, 1 dropped out of the top 10". */
export function changeSentence(counts) {
  const parts = [];
  const lead = (n) => `${n} ${n === 1 ? 'search' : 'searches'}`;
  const add = (n, words) => { if (n) parts.push(parts.length ? `${n} ${words}` : `${lead(n)} ${words}`); };
  add(counts.up, 'moved up');
  add(counts.new, 'entered the top 10');
  add(counts.down, 'moved down');
  add(counts.lost, 'dropped out of the top 10');
  if (!parts.length) return '';
  return parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
}

/* ------------------------------------------------------------------ helpers */

function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value != null && value !== false) el.setAttribute(key, String(value));
  }
  for (const kid of kids.flat(Infinity)) if (kid) el.append(kid);
  return el;
}

function day(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/* The SVG coordinate width. A narrower box on phones keeps axis text readable once the chart scales to fit. */
function chartWidth() {
  return window.innerWidth && window.innerWidth < 720 ? 360 : 640;
}

/* A next check date in the past means the scheduled run has not happened (no CRON_SECRET, or it failed). */
function nextText(iso) {
  const when = new Date(iso);
  if (!Number.isNaN(when.getTime()) && when.getTime() < Date.now()) return 'Re-check overdue';
  return `Next check ${day(iso)}`;
}

/* A row's current position in words. A search with no Google check yet is "Not checked yet", never "Not in top 10",
   which would read as a real Google result. */
function rowText(row) {
  if (!row || row.checked === false || (row.checked == null && !(row.spark || row.points || []).length)) return 'Not checked yet';
  return rankText(row.position);
}

function rankText(position) {
  return position ? `#${position}` : 'Not in top 10';
}

function historyFor(slug) {
  if (!full.has(slug)) {
    full.set(slug, api.get(`/api/v1/projects/${enc(slug)}/history`).catch((err) => {
      full.delete(slug);
      throw err;
    }));
  }
  return full.get(slug);
}

function compactRow(R, search) {
  const rows = (R && R.history && R.history.searches) || [];
  return rows.find((row) => row.search === search) || null;
}

/* The change since the last check, as a small chip with words, never color alone. */
function changeChip(row) {
  if (!row) return null;
  const map = {
    up: ['history-up', `Up ${row.delta}`],
    down: ['history-down', `Down ${Math.abs(row.delta || 0)}`],
    new: ['history-new', 'New'],
    lost: ['history-lost', 'Lost'],
    same: ['history-same', 'Same'],
  };
  const hit = map[row.change];
  return hit ? h('span', { class: `history-chip ${hit[0]}`, text: hit[1] }) : null;
}

/* ------------------------------------------------------------------ charts */

/* Puts the tooltip beside the point, inside the chart: right of it when it fits, else left, never past an edge. */
function placeTip(wrap, tip, share) {
  tip.hidden = false; // shown first, so its width can be measured
  const width = wrap.clientWidth || 1;
  const px = share * width;
  const tipWidth = tip.offsetWidth;
  let left = px + 12;
  if (left + tipWidth > width) left = px - 12 - tipWidth;
  left = Math.max(0, Math.min(left, width - tipWidth));
  tip.style.left = `${Math.round(left)}px`;
  tip.style.transform = 'none';
}

/* A tap shows a tooltip (touch has no hover), so it stays until the next tap somewhere else. */
document.addEventListener('pointerdown', (event) => {
  for (const chart of document.querySelectorAll('.history-chart')) {
    if (!chart.contains(event.target) && typeof chart.historyHide === 'function') chart.historyHide();
  }
});

/* A small line with no axes. Lower position numbers sit higher. Gaps are checks where the site was not in the top 10. */
function sparkline(values, label) {
  const W = 84;
  const H = 24;
  const pad = 3;
  const n = values.length;
  const svg = s('svg', { class: 'history-spark', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': label });
  if (!n) return svg;
  const x = (i) => (n === 1 ? W / 2 : pad + (i * (W - pad * 2)) / (n - 1));
  const y = (v) => pad + ((v - 1) * (H - pad * 2)) / (DEPTH - 1);
  svg.append(s('line', { class: 'history-spark-base', x1: 0, x2: W, y1: H - 1, y2: H - 1 }));
  let path = '';
  values.forEach((v, i) => {
    if (v == null) return;
    path += `${path && values[i - 1] != null ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
  });
  if (path) svg.append(s('path', { class: 'history-spark-line', d: path }));
  values.forEach((v, i) => {
    // a check with no ranked neighbour draws no line, so give it a dot; the last check always gets one
    const alone = v != null && values[i - 1] == null && values[i + 1] == null;
    if (v != null && (alone || i === n - 1)) svg.append(s('circle', { class: 'history-spark-dot', cx: x(i), cy: y(v), r: 2.5 }));
  });
  return svg;
}

/* A line chart of positions over time with a crosshair and a tooltip.
   series: [{ name, cls, points: [{ at, position }] }], all on the same dates (the first series sets them). */
function lineChart(series, { yLabel = 'Position', height = 220, values = 'position', yMax = DEPTH, format = rankText } = {}) {
  const dates = [...new Set(series.flatMap((one) => one.points.map((p) => p.at)))].sort();
  const W = chartWidth();
  const H = height;
  const left = 34;
  const right = 14;
  const top = 12;
  const bottom = 26;
  const n = dates.length;
  const x = (i) => (n === 1 ? left + (W - left - right) / 2 : left + (i * (W - left - right)) / (n - 1));
  const y = (v) => top + ((v - 1) * (H - top - bottom)) / Math.max(1, yMax - 1);
  const wrap = h('div', { class: 'history-chart' });
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `${yLabel} over time`, preserveAspectRatio: 'xMidYMid meet' });
  const ticks = yMax <= 10 ? [1, 3, 5, 10].filter((t) => t <= yMax) : [1, Math.round(yMax / 2), yMax];
  for (const t of ticks) {
    svg.append(s('line', { class: 'history-grid', x1: left, x2: W - right, y1: y(t), y2: y(t) }));
    const label = s('text', { class: 'history-axis', x: left - 8, y: y(t) + 4, 'text-anchor': 'end' });
    label.textContent = `#${t}`;
    svg.append(label);
  }
  const every = Math.max(1, Math.ceil(n / 6));
  dates.forEach((at, i) => {
    if (i !== n - 1 && (i % every || (i > 0 && n - 1 - i < every))) return; // keep the last date clear of its neighbour
    const label = s('text', { class: 'history-axis', x: x(i), y: H - 8, 'text-anchor': n === 1 ? 'middle' : i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle' });
    label.textContent = day(at);
    svg.append(label);
  });
  const lookup = series.map((one) => new Map(one.points.map((p) => [p.at, p[values]])));
  series.forEach((one, k) => {
    let path = '';
    let prev = null;
    const dots = [];
    dates.forEach((at, i) => {
      const v = lookup[k].get(at);
      if (v == null) {
        prev = null;
        return;
      }
      path += `${prev == null ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      prev = v;
      dots.push(s('circle', { class: `history-dot ${one.cls}`, cx: x(i), cy: y(v), r: 5 }));
    });
    svg.append(s('g', { class: `history-series ${one.cls}` }, path ? s('path', { class: 'history-line', d: path }) : null, dots));
  });
  const cross = s('line', { class: 'history-cross', x1: 0, x2: 0, y1: top, y2: H - bottom, visibility: 'hidden' });
  svg.append(cross);
  const tip = h('div', { class: 'history-tip', role: 'status', hidden: true });
  const hit = s('rect', { class: 'history-hit', x: left - 10, y: 0, width: W - left - right + 20, height: H });
  const show = (event) => {
    const box = svg.getBoundingClientRect();
    if (!box.width || !n) return;
    const px = ((event.clientX - box.left) / box.width) * W;
    let best = 0;
    for (let i = 1; i < n; i += 1) if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i;
    const at = dates[best];
    cross.setAttribute('x1', x(best));
    cross.setAttribute('x2', x(best));
    cross.setAttribute('visibility', 'visible');
    tip.replaceChildren(
      h('div', { class: 'history-tip-k', text: day(at) }),
      ...series.map((one, k) => h('div', { class: 'history-tip-row' },
        h('i', { class: `history-key ${one.cls}`, 'aria-hidden': 'true' }),
        h('span', { text: one.name }),
        h('b', { text: format(lookup[k].get(at)) }))));
    placeTip(wrap, tip, x(best) / W);
  };
  const hide = () => {
    tip.hidden = true;
    cross.setAttribute('visibility', 'hidden');
  };
  hit.addEventListener('pointermove', show);
  hit.addEventListener('pointerdown', show);
  hit.addEventListener('pointerleave', (event) => { if (event.pointerType === 'mouse') hide(); });
  wrap.historyHide = hide;
  svg.append(hit);
  wrap.append(svg, tip);
  return wrap;
}

/* One bar per check: how many tracked searches had the site in the top 10. Dark is the top 3 part. */
function firstPageBars(trend) {
  const W = chartWidth();
  const H = 120;
  const top = 18;
  const bottom = 22;
  const most = Math.max(1, ...trend.map((row) => row.searches || 0));
  const n = trend.length;
  const slot = W / n;
  const width = Math.min(56, slot * 0.6);
  const yOf = (v) => H - bottom - (v / most) * (H - top - bottom);
  const wrap = h('div', { class: 'history-chart' });
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `Searches on the first page at each check: ${trend.map((row) => `${day(row.at)} ${row.ranked} of ${row.searches}`).join(', ')}` });
  svg.append(s('line', { class: 'history-grid', x1: 0, x2: W, y1: H - bottom, y2: H - bottom }));
  const tip = h('div', { class: 'history-tip', role: 'status', hidden: true });
  trend.forEach((row, i) => {
    const cx = slot * i + slot / 2;
    const ranked = row.ranked || 0;
    const top3 = row.top3 || 0;
    const group = s('g', { class: 'history-bar-g' });
    if (ranked) {
      // Stacked: the top 3 part at the base in dark, the rest of the top 10 above it in blue, 2px apart.
      const y = yOf(ranked);
      const y3 = top3 ? yOf(top3) : H - bottom;
      if (top3) group.append(s('rect', { class: 'history-bar-top', x: cx - width / 2, y: y3, width, height: H - bottom - y3, rx: 4 }));
      if (ranked > top3) group.append(s('rect', { class: 'history-bar', x: cx - width / 2, y, width, height: Math.max(1, y3 - y - (top3 ? 2 : 0)), rx: 4 }));
    }
    const value = s('text', { class: 'history-bar-v', x: cx, y: (ranked ? yOf(ranked) : H - bottom) - 5, 'text-anchor': 'middle' });
    value.textContent = String(ranked);
    const label = s('text', { class: 'history-axis', x: cx, y: H - 6, 'text-anchor': 'middle' });
    label.textContent = n > 8 && i % 2 && i !== n - 1 ? '' : day(row.at);
    const hit = s('rect', { class: 'history-hit', x: slot * i, y: 0, width: slot, height: H });
    const show = () => {
      tip.replaceChildren(h('div', { class: 'history-tip-k', text: day(row.at) }),
        h('div', { text: `${ranked} of ${plural(row.searches || 0, 'search', 'searches')} on the first page` }),
        h('div', { class: 'history-tip-k', text: `${top3} in the top 3` }));
      placeTip(wrap, tip, cx / W);
    };
    hit.addEventListener('pointerenter', show);
    hit.addEventListener('pointerdown', show);
    hit.addEventListener('pointerleave', (event) => { if (event.pointerType === 'mouse') tip.hidden = true; });
    svg.append(group, value, label, hit);
  });
  wrap.historyHide = () => { tip.hidden = true; };
  wrap.append(svg, tip, h('div', { class: 'history-legend' },
    h('span', null, h('i', { class: 'history-key history-key-top', 'aria-hidden': 'true' }), 'Top 3'),
    h('span', null, h('i', { class: 'history-key history-key-on', 'aria-hidden': 'true' }), 'Top 10')));
  return wrap;
}

function legend(series) {
  if (series.length < 2) return null;
  return h('div', { class: 'history-legend' }, series.map((one) => h('span', null, h('i', { class: `history-key ${one.cls}`, 'aria-hidden': 'true' }), one.name)));
}

/* The same numbers as a table, for screen readers and anyone who prefers rows. */
function table(series) {
  const dates = [...new Set(series.flatMap((one) => one.points.map((p) => p.at)))].sort().reverse();
  const lookup = series.map((one) => new Map(one.points.map((p) => [p.at, p.position])));
  return h('details', { class: 'history-table' },
    h('summary', { text: 'Show as a table' }),
    h('div', { class: 'history-table-scroll' },
      h('table', null,
        h('thead', null, h('tr', null, h('th', { text: 'Checked' }), series.map((one) => h('th', { text: one.name })))),
        h('tbody', null, dates.map((at) => h('tr', null, h('td', { text: day(at) }), series.map((one, k) => h('td', { text: rankText(lookup[k].get(at)) }))))))));
}

/* ------------------------------------------------------------------ no search data key */

/* The owner's view when the server has no Google results key: one calm notice per screen with the exact steps,
   instead of empty charts. Never on the public demo; the client report page (static/share) loads no plugin, so it
   never shows there either. Search Console positions, when connected, are named as the fallback. */
export function keyMissing(R) {
  return !!R && R.google_ready === false && !S.publicDemo;
}

function gscFallback(R) {
  const positions = R && R.gsc && R.gsc.positions;
  return positions && Object.keys(positions).length ? Object.keys(positions).length : 0;
}

export function keyNotice(R, { wide = true } = {}) {
  const fallback = gscFallback(R);
  const now = fallback
    ? `Until then, ${fallback === 1 ? '1 search shows its' : `${fallback} searches show their`} Search Console average position for the last 28 days, labeled as such. That is not a Google top 10 position.`
    : R.search_data
      ? 'The positions shown are from the last Google check and will not update until the key is added.'
      : 'Until then, this site shows its own pages and issues only.';
  const steps = h('ol', { class: 'history-steps' },
    h('li', { text: 'Get an API key at serper.dev (the free plan includes 2,500 searches).' }),
    h('li', { text: 'Open vercel.com, then the project ranksim, then Settings, then Environment Variables.' }),
    h('li', null, 'Add ', h('code', { text: 'SERPER_API_KEY' }), ' with the key as its value, for Production.'),
    h('li', null, 'Optional, for weekly re-checks: add ', h('code', { text: 'CRON_SECRET' }), ' with any long random text.'),
    h('li', { text: 'Open Deployments, pick the latest one and choose Redeploy.' }),
    h('li', { text: 'Back here, open Settings and choose Refresh now.' }));
  return h('section', { class: `tile history-notice${wide ? ' span-4' : ''}`, role: 'note' },
    h('div', { class: 'history-notice-head' }, icon('warn'), h('div', null,
      h('div', { class: 'history-notice-title', text: 'Google rankings are off.' }),
      h('div', { class: 'tile-sub', text: 'Add a search data key in Vercel to turn them on.' }))),
    h('p', { class: 'tile-sub', text: now }),
    h('details', { class: 'history-notice-steps' }, h('summary', { text: 'How to turn them on' }), steps));
}

/* One notice on Overview: this replaces the built-in "search data" slot so the two never stack. With a key, the
   built-in callout shows exactly as before. */
registerOverviewTile({
  key: 'search-data',
  replace: true,
  order: 0,
  render(R) {
    return keyMissing(R) ? keyNotice(R) : noSearchData();
  },
});

/* ------------------------------------------------------------------ overview alert */

registerOverviewTile({
  key: 'track-alert',
  order: 1, // right after the search data slot (order 0), so a missing key is the first thing on the page
  render(R, ctx) {
    const slug = ctx.slug || S.slug;
    if (!R.history || R.history.real === false || dismissed.has(slug)) return null;
    const found = visitChanges(R, slug);
    if (!found) return null;
    const when = found.seenAt ? `Since your last visit on ${day(found.seenAt)}` : `Since the check on ${day(previousCheck(R))}`;
    const tile = h('section', { class: 'tile span-4 history-alert', role: 'status' });
    const close = h('button', { class: 'btn btn-plain', type: 'button', text: 'Got it' });
    close.addEventListener('click', () => {
      dismissed.add(slug);
      tile.remove();
    });
    tile.append(
      h('div', { class: 'history-alert-text' },
        h('div', { class: 'tile-k', text: when }),
        h('div', { class: 'history-alert-big', text: `${changeSentence(found.counts)}.` }),
        h('div', { class: 'history-alert-list' }, found.moved.slice(0, 4).map((item) => h('span', null, changeChip({ change: item.change, delta: item.delta }), item.search)))),
      h('div', { class: 'history-alert-actions' }, link('track-history', 'See history', { class: 'btn btn-dark' }), close));
    return tile;
  },
});

function previousCheck(R) {
  const trend = (R.history && R.history.trend) || [];
  return trend.length >= 2 ? trend[trend.length - 2].at : null;
}

/* ------------------------------------------------------------------ overview tile */

registerOverviewTile({
  key: 'track-rank-changes',
  order: 55,
  render(R) {
    const H = R.history;
    if (!H) return null;
    const C = H.changes || {};
    const tile = h('section', { class: 'tile white span-2 history-tile' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Rank changes' }), h('span', { class: 'aside', text: H.real === false ? 'Demo data, not Google results' : H.last_at ? `Last check ${day(H.last_at)}` : 'Not checked yet' })));
    const stat = (cls, label, value) => h('div', { class: `history-stat ${cls}` }, h('b', { text: String(value || 0) }), h('span', { text: label }));
    if ((H.checks || 0) < 2) {
      tile.append(h('p', { class: 'tile-sub', text: H.checks ? 'Changes show after the second check.' : 'Positions show after the first Google check.' }));
    } else {
      tile.append(h('div', { class: 'history-stats' }, stat('history-up', 'Up', C.up), stat('history-down', 'Down', C.down), stat('history-new', 'New', C.new), stat('history-lost', 'Lost', C.lost)));
    }
    const trend = H.trend || [];
    if (trend.length >= 2) tile.append(h('div', { class: 'history-cap', text: 'Searches on the first page at each check' }), firstPageBars(trend.slice(-12)));
    const foot = h('div', { class: 'history-foot' });
    if (H.next_check_at) foot.append(h('span', { class: 'tile-sub', text: nextText(H.next_check_at) }));
    else if (H.schedule === 'off') foot.append(h('span', { class: 'tile-sub', text: 'Re-checks are off. Turn them on in Settings.' }));
    if (H.checks) foot.append(link('track-history', 'Every search', { class: 'btn btn-plain' }));
    tile.append(foot);
    return tile;
  },
});

/* ------------------------------------------------------------------ searches column */

registerSearchColumn({
  key: 'track-history',
  order: 50,
  header: 'Since last visit',
  cell(row) {
    const item = compactRow(S.report, row.q);
    if (!item || !item.spark || !item.spark.length) return h('span', { class: 'history-none', text: 'Not checked yet' });
    // The Searches row is a button, so this cell stays passive. The History screen opens the full chart.
    // The chip shows the move since the owner's last visit, the same numbers as the Overview alert.
    const label = `Positions at each check: ${item.spark.map(rankText).join(', ')}`;
    const move = sinceVisit(item, baselineFor(S.slug));
    const chip = move && move.change !== 'same' ? changeChip(move) : null;
    return h('span', { class: 'history-cell' }, sparkline(item.spark, label), chip);
  },
});

/* ------------------------------------------------------------------ history screen */

registerScreen('track-history', {
  label: 'History',
  nav: false,
  order: 35,
  render(ctx) {
    const R = ctx.report || {};
    const H = R.history || {};
    const rows = (H.searches || []).slice().sort((a, b) => (a.position || 99) - (b.position || 99) || a.search.localeCompare(b.search));
    const list = h('div', { class: 'history-rows' });
    for (const row of rows) {
      list.append(h('button', { class: 'history-row-btn', type: 'button', onClick: () => openHistory(row.search) },
        h('span', { class: 'history-q', text: row.search }),
        h('span', { class: `history-p${row.position ? '' : ' none'}`, text: rowText(row) }),
        h('span', { class: 'history-trend' }, sparkline(row.spark || [], `Positions at each check: ${(row.spark || []).map(rankText).join(', ')}`), changeChip(row))));
    }
    if (!rows.length) list.append(h('div', { class: 'empty-note', text: 'No searches yet.' }));
    const sub = H.real === false ? 'Demo data, not Google results. Pick a search to see it over time.' : H.checks ? `${plural(H.checks, 'Google check')} since ${day(H.first_at)}. Pick a search to see it over time.` : 'Positions show after the first Google check.';
    const notice = keyMissing(R) ? h('div', { class: 'history-notice-wrap' }, keyNotice(R, { wide: false })) : null;
    return page('Rank history', sub, null, notice, weekView(H), h('div', { class: 'tile' }, h('div', { class: 'tile-head' }, h('h2', { text: 'Every search' })), list));
  },
});

/* ------------------------------------------------------------------ since last week */

/* Monday morning view: what changed for this client since last week. Every number is a pair of real dated checks
   (each search's newest check against its check a week earlier), worked out on the server (tracking.weekly_summary). */
function weekView(H) {
  const W = H && H.week;
  if (!W || H.real === false) return null;
  const head = h('div', { class: 'tile-head' }, h('h2', { text: 'Since last week' }), h('span', { class: 'aside', text: W.ready ? `${day(W.from)} to ${day(W.to)}` : '' }));
  if (!W.ready) return h('section', { class: 'tile history-week' }, head, h('p', { class: 'tile-sub', text: W.reason || 'Needs two Google checks at least a week apart.' }));
  const stat = (cls, value, label) => h('div', { class: `history-stat ${cls}` }, h('b', { text: String(value) }), h('span', { text: label }));
  const row = (search, right) => h('button', { class: 'history-week-row', type: 'button', onClick: () => openHistory(search) }, h('span', { class: 'history-q', text: search }), right);
  const pos = (value) => (value ? `#${value}` : 'not in top 10');
  const block = (title, items, empty) => h('div', { class: 'history-week-block' },
    h('div', { class: 'tile-k', text: title }),
    items.length ? h('div', { class: 'history-week-rows' }, items) : h('div', { class: 'history-none', text: empty }));
  const gained = W.gained.map((item) => row(item.search, h('span', { class: 'history-week-val' }, changeChip({ change: 'new' }), `now #${item.to}`)));
  const lost = W.lost.map((item) => row(item.search, h('span', { class: 'history-week-val' }, changeChip({ change: 'lost' }), `was #${item.from}`)));
  const moves = W.moves.map((item) => row(item.search, h('span', { class: 'history-week-val' }, changeChip({ change: item.delta > 0 ? 'up' : 'down', delta: item.delta }), `#${item.from} to #${item.to}`)));
  const passed = W.passed.map((item) => row(item.search, h('span', { class: 'history-week-val history-week-pass' },
    h('b', { text: item.domain }), ` ${pos(item.their_from)} to ${pos(item.their_to)}, you ${pos(item.our_from)} to ${pos(item.our_to)}`)));
  const more = W.moves_total > W.moves.length ? h('div', { class: 'history-none', text: `${W.moves_total - W.moves.length} smaller moves not shown.` }) : null;
  const skipped = W.skipped ? ` ${plural(W.skipped, 'search', 'searches')} had no check a week earlier or none this week, so they are left out.` : '';
  return h('section', { class: 'tile white history-week' },
    head,
    h('div', { class: 'history-stats' },
      stat('history-new', W.gained.length, 'Onto page one'),
      stat('history-lost', W.lost.length, 'Off page one'),
      stat('history-up', W.up, 'Moved up'),
      stat('history-down', W.down, 'Moved down')),
    h('div', { class: 'history-week-grid' },
      block('Onto page one', gained, 'None this week.'),
      block('Off page one', lost, 'None this week.'),
      h('div', { class: 'history-week-block' }, block('Biggest moves', moves, 'No position changes.'), more),
      block('Competitors who moved past you', passed, 'No tracked competitor passed you.')),
    h('p', { class: 'history-note', text: `Each of ${plural(W.compared, 'search', 'searches')} compared with its own Google check a week earlier.${skipped}` }));
}

/* ------------------------------------------------------------------ per-search sheet */

export function openHistory(search) {
  const slug = S.slug;
  openSheet((sheet, closeBtn) => {
    const body = h('div', { class: 'sheet-body history-sheet' }, h('p', { class: 'tile-sub', text: 'Loading history.' }));
    sheet.append(h('div', { class: 'sheet-head' }, h('div', null, h('h2', { text: search }), h('div', { class: 'sub', text: 'Position history' })), closeBtn), body);
    historyFor(slug).then((data) => {
      const row = (data.searches || []).find((item) => item.search === search);
      body.replaceChildren(...sheetBody(data, row).filter(Boolean));
    }).catch(() => body.replaceChildren(h('p', { class: 'tile-sub', text: 'History could not be loaded. Try again.' })));
  });
}

function sheetBody(data, row) {
  if (!row || !row.points.length) return [h('p', { class: 'tile-sub', text: 'No Google checks for this search yet.' })];
  const series = [{ name: 'You', cls: SERIES[0], points: row.points }];
  const rivals = Object.entries(row.competitors || {})
    .filter(([, points]) => points.some((p) => p.position))
    .sort((a, b) => best(a[1]) - best(b[1]))
    .slice(0, 2);
  rivals.forEach(([domain, points], i) => series.push({ name: domain, cls: SERIES[i + 1], points }));
  const out = [
    h('div', { class: 'tile white history-main' },
      h('div', { class: 'history-now' },
        h('div', null, h('div', { class: 'tile-k', text: 'Now' }), h('div', { class: 'tile-big', text: row.position ? `#${row.position}` : 'Not in top 10' })),
        changeChip(row)),
      h('div', { class: 'tile-sub', text: `${plural(row.points.length, 'check')} since ${day(row.points[0].at)}` }),
      legend(series),
      lineChart(series),
      table(series)),
  ];
  const F = row.features || {};
  if (F.people_also_ask && F.people_also_ask.length) {
    out.push(h('div', { class: 'tile' }, h('div', { class: 'tile-k', text: 'People also ask' }), h('ul', { class: 'history-list' }, F.people_also_ask.map((item) => h('li', { text: item.question })))));
  }
  if (F.related_searches && F.related_searches.length) {
    out.push(h('div', { class: 'tile' }, h('div', { class: 'tile-k', text: 'Related searches' }), h('div', { class: 'history-chips' }, F.related_searches.map((q) => h('span', { class: 'chip chip-gray', text: q })))));
  }
  const extras = featureNames(F).filter((name) => !['People also ask', 'Related searches'].includes(name));
  if (extras.length) out.push(h('p', { class: 'tile-sub', text: `Also on this results page: ${extras.join(', ')}.` }));
  out.push(h('p', { class: 'history-note', text: data.real === false ? 'Demo data, invented for this demo. Not Google results.' : 'Real Google positions from each check. Not in top 10 means the site was not in the results we hold.' }));
  return out;
}

function best(points) {
  return Math.min(...points.map((p) => p.position || 99));
}

const FEATURE_NAMES = {
  people_also_ask: 'People also ask',
  related_searches: 'Related searches',
  answer_box: 'answer box',
  knowledge_panel: 'knowledge panel',
  top_stories: 'top stories',
  videos: 'videos',
  images: 'images',
  sitelinks: 'sitelinks',
};

function featureNames(features) {
  return Object.keys(features || {}).filter((key) => FEATURE_NAMES[key]).map((key) => FEATURE_NAMES[key]);
}

/* ------------------------------------------------------------------ settings */

registerSettingsSection({
  key: 'tracking-key',
  order: 5,
  render({ report }) {
    return keyMissing(report) ? keyNotice(report) : null;
  },
});

registerSettingsSection({
  key: 'tracking',
  order: 15,
  render({ slug, reload }) {
    const section = h('section', { class: 'tile span-2 history-settings' },
      h('div', { class: 'tile-head' }, h('h2', { text: 'Re-checks and usage' })),
      h('p', { class: 'tile-sub', text: 'Loading.' }));
    Promise.all([api.get(`/api/v1/projects/${enc(slug)}/tracking`), api.get('/api/v1/tracking/status')])
      .then(([tracking, status]) => section.replaceChildren(...settingsBody(slug, tracking, status, reload).filter(Boolean)))
      .catch(() => section.replaceChildren(h('div', { class: 'tile-head' }, h('h2', { text: 'Re-checks and usage' })), h('p', { class: 'tile-sub', text: 'Could not load. Try again.' })));
    return section;
  },
});

function settingsBody(slug, tracking, status, reload) {
  const labels = { off: 'Off', daily: 'Daily', weekly: 'Weekly' };
  const seg = h('div', { class: 'history-seg', role: 'group', 'aria-label': 'Re-check schedule' });
  const note = h('p', { class: 'tile-sub history-when' });
  const paintNote = (data) => {
    if (data.schedule === 'off') note.textContent = 'This site is only checked when you refresh it.';
    else if (data.scheduled_checks_ready === false) note.textContent = 'Scheduled checks are not set up on this server yet (they need a search data key and CRON_SECRET). Refresh by hand until then.';
    else if (data.next_check_at) note.textContent = `Last check ${day(data.last_check_at)}. ${nextText(data.next_check_at)}.`;
    else note.textContent = 'The first check runs on the next scheduled run.';
    if (data.last_result === 'error' && data.last_error) note.textContent += ' The last re-check failed.';
  };
  const paintSeg = (current) => {
    seg.replaceChildren(...tracking.schedules.map((key) => {
      const button = h('button', { type: 'button', class: `history-seg-btn${key === current ? ' on' : ''}`, 'aria-pressed': key === current ? 'true' : 'false', text: labels[key] || key });
      button.addEventListener('click', async () => {
        if (key === current) return;
        seg.querySelectorAll('button').forEach((b) => { b.disabled = true; });
        try {
          const data = await api.post(`/api/v1/projects/${enc(slug)}/tracking`, { schedule: key });
          tracking = data;
          paintSeg(data.schedule);
          paintNote(data);
          full.delete(slug);
          if (typeof reload === 'function') reload().catch(() => {}); // the Overview tile shows the schedule too
          toast(key === 'off' ? 'Re-checks turned off' : `Checks ${labels[key].toLowerCase()} from now on`);
        } catch (err) {
          toast(err && err.message ? err.message : 'Could not save. Try again.');
          paintSeg(current);
        }
      });
      return button;
    }));
  };
  paintSeg(tracking.schedule);
  paintNote(tracking);

  const used = Number(status.used || 0);
  const cap = Number(status.cap || 0);
  const meter = h('div', { class: 'history-meter' },
    h('div', { class: 'history-meter-top' },
      h('span', { text: `Searches used in ${status.month_label}` }),
      h('b', { text: cap ? `${used} of ${cap}` : `${used}, no searches allowed` })),
    bar(cap ? Math.min(1, used / cap) : 1, used >= cap ? 'dark' : null),
    h('div', { class: 'tile-sub', text: `${plural(Number(status.reused || 0), 'search', 'searches')} reused from the last ${status.fresh_hours} hours this month at no cost. Resets ${day(status.resets)}.` }));
  const provider = status.configured
    ? `Google results from ${status.provider}.${status.healthy === false ? ` The last request failed (${status.last_error}).` : ''}`
    : 'No search data key on this server yet, so no searches are used. The steps are at the top of this page.';
  const cron = status.cron && status.cron.secret_set ? null : h('p', { class: 'tile-sub', text: 'Scheduled checks need CRON_SECRET on the server. Until then, refresh by hand.' });
  return [
    h('div', { class: 'tile-head' }, h('h2', { text: 'Re-checks and usage' })),
    h('div', { class: 'history-row' }, h('span', { class: 'label label-plain', text: 'Check Google' }), seg),
    note,
    meter,
    h('p', { class: 'tile-sub', text: provider }),
    cron,
  ];
}
