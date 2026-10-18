#!/usr/bin/env node
/*
 * Draws an annotated picture of one compressor cycle from the keezer's own history.
 * Used for docs/images/coast-cycle.svg; re-run it to make a fresh one, for example
 * to compare before and after turning coast-aware control on.
 *
 *   node docs/tools/cycle-chart.js http://<pi>:5000 --hours 24 --out docs/images/coast-cycle.svg
 *   node docs/tools/cycle-chart.js history.json --swing 0.5 --out my-chart.svg
 *
 * The source is either the keezer's web address (it fetches /api/history and, to learn the
 * swing, /api/status) or a saved copy of /api/history. Options:
 *
 *   --hours N       how much history to fetch (default 24). The API averages readings into at
 *                   most 300 points, so a shorter window gives sharper numbers
 *   --swing F       the swing in degrees F when reading a file (default 0.5)
 *   --cycle N       which complete cycle to annotate, counting from 1 (default: the middle one)
 *   --title TEXT    heading; --subtitle TEXT  the line under it
 *   --out FILE      where to write the SVG (default: cycle.svg)
 *
 * It needs at least one complete cycle (a compressor run, the coast and rise after it, then the
 * next start) in the window. The numbers in the key are measured from the data, so they are
 * approximate: each point is an average of several readings.
 */
'use strict';
const fs = require('fs');

const W = 1000, H = 560, L = 64, R = 28, T = 78, B = 190;

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
const f1 = (n) => n.toFixed(1);

/** Where the compressor runs: [firstIndex, lastIndex] of each stretch of "on" points. */
function findRuns(pts) {
  const runs = [];
  for (let i = 0; i < pts.length; i++) {
    if (pts[i].on && (i === 0 || !pts[i - 1].on)) {
      let j = i;
      while (j + 1 < pts.length && pts[j + 1].on) { j++; }
      runs.push([i, j]);
    }
  }
  return runs;
}

/** Measure one complete cycle: start, stop, lowest point and the next start. */
function measureCycle(pts, which) {
  const runs = findRuns(pts);
  // complete = not the first point of the data, and followed by another start
  const complete = runs.filter((r, k) => k + 1 < runs.length && r[0] > 0);
  if (!complete.length) { return null; }
  const pick = which ? complete[which - 1] : complete[Math.floor(complete.length / 2)];
  if (!pick) { return null; }
  const next = runs[runs.indexOf(pick) + 1];
  const s = pick[0], e = pick[1];
  // the stop is the first point with the compressor off: that is the reading it decided on
  const stop = Math.min(e + 1, next[0] - 1);
  let b = stop;
  for (let i = stop; i < next[0]; i++) { if (pts[i].v < pts[b].v) { b = i; } }
  let peak = s;
  for (let i = Math.max(0, s - 6); i <= e; i++) { if (pts[i].v > pts[peak].v) { peak = i; } }
  return {
    s, e, stop, b, peak, nextStart: next[0],
    runMinutes: (pts[stop].t - pts[s].t) / 60000,
    stopTemp: pts[stop].v, low: pts[b].v, startTemp: pts[peak].v,
    riseHours: (pts[next[0]].t - pts[b].t) / 3600000
  };
}

/** points: [{t: ms, v: degrees F, on: bool}], oldest first. Returns {svg, cycle}. */
function buildSvg(pts, opts) {
  opts = Object.assign({ target: 39, swing: 0.5, title: 'One compressor cycle', subtitle: '', which: 0 }, opts);
  const cycle = measureCycle(pts, opts.which);
  if (!cycle) { throw new Error('No complete cycle in this data. Try a longer --hours.'); }

  const high = opts.target + opts.swing, lowEdge = opts.target - opts.swing;
  const vMin = Math.floor(Math.min(...pts.map((p) => p.v)) - 0.5);
  const vMax = Math.ceil(Math.max(...pts.map((p) => p.v)) + 0.7);
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const X = (t) => L + (t - t0) / (t1 - t0) * (W - L - R);
  const Y = (v) => T + (vMax - v) / (vMax - vMin) * (H - T - B);
  const f = (n) => n.toFixed(1);

  let svg = '';
  const add = (s) => { svg += s + '\n'; };
  add(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" font-family="-apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif">`);
  add(`<title>${esc(opts.title)}</title>`);
  add(`<desc>Temperature history with one compressor cycle annotated: it starts near ${f1(cycle.startTemp)}F, stops near ${f1(cycle.stopTemp)}F, keeps falling to about ${f1(cycle.low)}F, then warms again.</desc>`);
  add(`<rect width="${W}" height="${H}" rx="14" fill="#0f1722"/>`);
  add(`<text x="${L}" y="34" fill="#e8eef7" font-size="20" font-weight="700">${esc(opts.title)}</text>`);
  if (opts.subtitle) { add(`<text x="${L}" y="56" fill="#93a4bb" font-size="13">${esc(opts.subtitle)}</text>`); }

  for (let v = vMin; v <= vMax; v++) {
    add(`<line x1="${L}" x2="${W - R}" y1="${f(Y(v))}" y2="${f(Y(v))}" stroke="#223044"/>`);
    add(`<text x="${L - 10}" y="${f(Y(v) + 4)}" fill="#7d8ea6" font-size="12" text-anchor="end">${v}°</text>`);
  }
  const hourMs = 3600e3, step = (t1 - t0) > 30 * hourMs ? 12 * hourMs : 4 * hourMs;
  for (let t = Math.ceil(t0 / step) * step; t < t1; t += step) {
    const h = new Date(t).getHours();
    const label = (h % 12 === 0 ? 12 : h % 12) + (h < 12 ? ' AM' : ' PM');
    add(`<line x1="${f(X(t))}" x2="${f(X(t))}" y1="${T}" y2="${H - B}" stroke="#182334"/>`);
    add(`<text x="${f(X(t))}" y="${H - B + 20}" fill="#7d8ea6" font-size="12" text-anchor="middle">${label}</text>`);
  }

  add(`<rect x="${L}" y="${f(Y(high))}" width="${W - L - R}" height="${f(Y(lowEdge) - Y(high))}" fill="#4cc9f0" fill-opacity="0.13"/>`);
  add(`<line x1="${L}" x2="${W - R}" y1="${f(Y(opts.target))}" y2="${f(Y(opts.target))}" stroke="#4cc9f0" stroke-opacity="0.55" stroke-dasharray="6 5"/>`);
  add(`<text x="${W - R - 6}" y="${f(Y(high) - 6)}" fill="#7fd3ee" font-size="12" text-anchor="end">target band ${f1(lowEdge)} – ${f1(high)}°F</text>`);

  const stripY = H - B + 38;
  add(`<text x="${L - 10}" y="${stripY + 9}" fill="#7d8ea6" font-size="11" text-anchor="end">on</text>`);
  pts.forEach((p, i) => {
    if (!p.on) { return; }
    const x0 = X(p.t), x1 = i + 1 < pts.length ? X(pts[i + 1].t) : x0 + 3;
    add(`<rect x="${f(x0)}" y="${stripY}" width="${f(Math.max(3, x1 - x0))}" height="10" rx="2" fill="#f0a04b"/>`);
  });

  add(`<polyline fill="none" stroke="#4cc9f0" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round" points="${pts.map((p) => f(X(p.t)) + ',' + f(Y(p.v))).join(' ')}"/>`);

  const px = X(pts[cycle.peak].t), py = Y(cycle.startTemp);
  const sx = X(pts[cycle.stop].t), sy = Y(cycle.stopTemp);
  const bx = X(pts[cycle.b].t), by = Y(cycle.low);
  const badge = (x, y, n, c) => {
    add(`<circle cx="${f(x)}" cy="${f(y)}" r="12" fill="${c}" stroke="#0f1722" stroke-width="2"/>`);
    add(`<text x="${f(x)}" y="${f(y + 4.5)}" fill="#0f1722" font-size="13" font-weight="800" text-anchor="middle">${n}</text>`);
  };
  [[px, py, '#f0a04b'], [sx, sy, '#f0a04b'], [bx, by, '#ff6b81']].forEach(([x, y, c]) =>
    add(`<circle cx="${f(x)}" cy="${f(y)}" r="4" fill="${c}"/>`));
  if (cycle.stopTemp - cycle.low > 0.15) {          // draw the coast only when there is one
    add(`<line x1="${f(sx + 14)}" y1="${f(sy + 10)}" x2="${f(sx + 14)}" y2="${f(by - 10)}" stroke="#ff6b81" stroke-width="2.2" stroke-dasharray="5 4"/>`);
    add(`<path d="M ${f(sx + 14)} ${f(by - 6)} l -5 -9 l 10 0 z" fill="#ff6b81"/>`);
  }
  badge(px - 20, py - 14, 1, '#f0a04b');
  badge(sx + 20, sy - 10, 2, '#f0a04b');
  badge(sx + 30, (sy + by) / 2, 3, '#ff6b81');
  badge(bx - 6, by + 24, 4, '#ff6b81');
  const rx = X((pts[cycle.b].t + pts[cycle.nextStart].t) / 2), ry = Y((cycle.low + pts[cycle.nextStart].v) / 2);
  badge(rx + 26, ry + 10, 5, '#a9bad1');

  const lowVs = cycle.low < lowEdge ? 'under' : 'above';
  const key = [
    ['#f0a04b', '1', `The compressor starts when the temperature reaches the high edge (${f1(high)}°F).`],
    ['#f0a04b', '2', `It runs about ${Math.round(cycle.runMinutes)} minutes, and the controller stops it at about ${f1(cycle.stopTemp)}°F (the low edge is ${f1(lowEdge)}°F).`],
    ['#ff6b81', '3', `The temperature keeps falling about ${f1(Math.max(0, cycle.stopTemp - cycle.low))}°F more after the stop: the “coast”.`],
    ['#ff6b81', '4', `It bottoms out near ${f1(cycle.low)}°F, ${f1(Math.abs(cycle.low - lowEdge))}°F ${lowVs} the low edge.`],
    ['#a9bad1', '5', `Then it warms for about ${f1(cycle.riseHours)} hours until it reaches the high edge again.`]
  ];
  const keyTop = H - B + 76;
  key.forEach(([c, n, text], i) => {
    const y = keyTop + i * 22;
    add(`<circle cx="${L + 8}" cy="${y - 4}" r="9" fill="${c}"/>`);
    add(`<text x="${L + 8}" y="${y}" fill="#0f1722" font-size="11" font-weight="800" text-anchor="middle">${n}</text>`);
    add(`<text x="${L + 26}" y="${y}" fill="#cfd9e8" font-size="13">${esc(text)}</text>`);
  });
  add(`<text x="${W - R}" y="${H - 12}" fill="#5f7089" font-size="11" text-anchor="end">orange strip = compressor running · times are local to the computer that drew this</text>`);
  add('</svg>');
  return { svg, cycle };
}

/** The /api/history payload to the points buildSvg wants. */
function pointsFromHistory(history) {
  return history.points.filter((p) => p.temp !== null)
    .map((p) => ({ t: Date.parse(p.t), v: p.temp, on: !!p.on }));
}

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith('--')) { args[argv[i].slice(2)] = argv[++i]; } else { args._.push(argv[i]); }
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const source = args._[0];
  if (!source) { console.error('usage: node docs/tools/cycle-chart.js <http://pi:5000 | history.json> [--hours N] [--swing F] [--out FILE]'); process.exit(2); }

  let history, target, swing = args.swing !== undefined ? parseFloat(args.swing) : 0.5;
  if (/^https?:/.test(source)) {
    const base = source.replace(/\/+$/, '');
    history = await (await fetch(`${base}/api/history?hours=${args.hours || 24}`)).json();
    const status = await (await fetch(`${base}/api/status`)).json();
    target = status.settings.target_temp;
    if (args.swing === undefined) { swing = status.settings.hysteresis; }
  } else {
    history = JSON.parse(fs.readFileSync(source, 'utf8').replace(/^﻿/, ''));
  }
  const pts = pointsFromHistory(history);
  if (target === undefined) { const last = history.points.filter((p) => p.target !== null).pop(); target = last ? last.target : 39; }

  const { svg, cycle } = buildSvg(pts, {
    target, swing, which: args.cycle ? parseInt(args.cycle, 10) : 0,
    title: args.title || 'One compressor cycle', subtitle: args.subtitle || ''
  });
  const out = args.out || 'cycle.svg';
  fs.writeFileSync(out, svg);
  console.log(`wrote ${out}: starts ${f1(cycle.startTemp)}F, stops ${f1(cycle.stopTemp)}F after ~${Math.round(cycle.runMinutes)} min, ` +
              `low ${f1(cycle.low)}F, warms for ~${f1(cycle.riseHours)} h`);
}

if (require.main === module) {
  main().catch((err) => { console.error('error: ' + err.message); process.exit(1); });
}

module.exports = { buildSvg, measureCycle, findRuns, pointsFromHistory };
