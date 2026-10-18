'use strict';
// Tests for docs/tools/cycle-chart.js (the generator behind docs/images/coast-cycle.svg).
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { buildSvg, measureCycle, findRuns, pointsFromHistory } = require(path.join(__dirname, '..', 'docs', 'tools', 'cycle-chart.js'));

const MIN = 60000;

/**
 * Synthetic readings every 5 minutes. Each cycle: the compressor runs `runMin` minutes falling
 * from `top`, the temperature keeps falling `coast` more over an hour, then it rises back to
 * `top` over `riseMin` minutes, where the next run starts.
 */
function cycles(count, o) {
  o = Object.assign({ top: 39.5, runMin: 25, drop: 1.1, coast: 1.7, riseMin: 180 }, o);
  const pts = [];
  let t = Date.UTC(2026, 9, 18, 0, 0, 0);
  const push = (v, on) => { pts.push({ t, v, on }); t += 5 * MIN; };
  push(o.top - 0.6, false);                                   // a little lead-in before the first start
  for (let c = 0; c < count; c++) {
    for (let m = 0; m < o.runMin; m += 5) { push(o.top - o.drop * m / o.runMin, true); }
    const stop = o.top - o.drop;
    for (let m = 0; m < 60; m += 5) { push(stop - o.coast * m / 60, false); }
    const bottom = stop - o.coast;
    for (let m = 0; m < o.riseMin; m += 5) { push(bottom + (o.top - bottom) * m / o.riseMin, false); }
  }
  push(o.top, true);                                          // the start that ends the last cycle
  return pts;
}

test('finds the compressor runs', () => {
  const runs = findRuns(cycles(3));
  assert.equal(runs.length, 4);                                // three cycles plus the final start
});

test('measures a cycle: run time, stop, lowest point and how long it warms', () => {
  const m = measureCycle(cycles(3), 0);
  assert.ok(Math.abs(m.runMinutes - 25) <= 5, 'run minutes ' + m.runMinutes);
  assert.ok(Math.abs(m.startTemp - 39.5) < 0.2);
  assert.ok(Math.abs(m.stopTemp - 38.5) < 0.3, 'stop ' + m.stopTemp);
  assert.ok(Math.abs(m.low - 36.7) < 0.3, 'low ' + m.low);
  assert.ok(m.riseHours > 2.5 && m.riseHours < 3.5, 'rise hours ' + m.riseHours);
});

test('annotates the requested cycle', () => {
  const pts = cycles(3);
  const first = measureCycle(pts, 1), second = measureCycle(pts, 2);
  assert.ok(pts[second.s].t > pts[first.s].t);
  assert.equal(measureCycle(pts, 9), null);                    // there is no ninth cycle
});

test('the picture states the measured numbers in its key', () => {
  const { svg } = buildSvg(cycles(3), { target: 39, swing: 0.5, title: 'My title', subtitle: 'My subtitle' });
  assert.ok(svg.startsWith('<svg '));
  assert.ok(svg.trimEnd().endsWith('</svg>'));
  assert.match(svg, />My title</);
  assert.match(svg, />My subtitle</);
  assert.match(svg, /target band 38\.5 – 39\.5°F/);
  assert.match(svg, /high edge \(39\.5°F\)/);
  assert.match(svg, /It runs about \d+ minutes/);
  assert.match(svg, /keeps falling about 1\.\d°F more/);
  assert.match(svg, /under the low edge/);
  assert.match(svg, /warms for about 3\.\d hours/);
  assert.equal((svg.match(/<polyline/g) || []).length, 1);
});

test('with no coast it says so instead of drawing an arrow', () => {
  const { svg } = buildSvg(cycles(3, { coast: 0.05, drop: 1.0 }), { target: 39, swing: 0.5 });
  assert.match(svg, /keeps falling about 0\.\dF? ?more|keeps falling about 0\.\d°F more/);
  assert.ok(!svg.includes('stroke-dasharray="5 4"'), 'no coast arrow when there is no coast');
});

test('says "above" when it stops short of the low edge', () => {
  // it stops at 38.9 and coasts only to 38.6: above the 38.5 low edge
  const { svg } = buildSvg(cycles(3, { drop: 0.6, coast: 0.3, riseMin: 120 }), { target: 39, swing: 0.5 });
  assert.match(svg, /above the low edge/);
});

test('data without a complete cycle is a clear error', () => {
  assert.throws(() => buildSvg(cycles(1).slice(0, 8), { target: 39, swing: 0.5 }), /No complete cycle/);
  assert.throws(() => buildSvg([], { target: 39, swing: 0.5 }));
});

test('text from the options cannot break the markup', () => {
  const { svg } = buildSvg(cycles(3), { target: 39, swing: 0.5, title: 'a < b & c', subtitle: '<script>' });
  assert.ok(!svg.includes('<script>'));
  assert.match(svg, /a &lt; b &amp; c/);
});

test('reads the keezer history format', () => {
  const pts = pointsFromHistory({ points: [
    { t: '2026-10-18T10:00:00.000000Z', temp: 38.1, target: 39, on: false },
    { t: '2026-10-18T10:05:00.000000Z', temp: null, target: 39, on: true },      // a failed read is skipped
    { t: '2026-10-18T10:10:00.000000Z', temp: 38.4, target: 39, on: true }
  ] });
  assert.equal(pts.length, 2);
  assert.deepEqual(pts.map((p) => [p.v, p.on]), [[38.1, false], [38.4, true]]);
  assert.equal(pts[0].t, Date.UTC(2026, 9, 18, 10, 0, 0));
});
