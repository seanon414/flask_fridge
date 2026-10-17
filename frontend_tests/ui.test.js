'use strict';
/*
 * Browser-side tests for app/static/app.js, run under Node with jsdom.
 *
 *   npm install && npm test
 *
 * The real index.html and app.js are loaded into jsdom. fetch is replaced
 * with a scriptable fake API, and the canvas with a recorder, so we can
 * assert on what the page renders and on what it sends to the server.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const APP = path.join(__dirname, '..', 'app');
const HTML = fs.readFileSync(path.join(APP, 'templates', 'index.html'), 'utf8')
  .replace(/\{\{ url_for\('static', filename='[^']+'\) \}\}/g, '/static/x')
  .replace('{{ csrf_token() }}', 'test-token')
  .replace(/<script[^>]*><\/script>/g, '');   // app.js is evaluated by the harness
const APP_JS = fs.readFileSync(path.join(APP, 'static', 'app.js'), 'utf8');

const DEG = '°';
const HOUR = 3600 * 1000;

/* ---------- data builders ---------- */

const iso = (ms) => new Date(ms).toISOString();

function settings(o) {
  return Object.assign({ target_temp: 38, hysteresis: 1, min_cycle_seconds: 300, mode: 'auto' }, o);
}

function statusBody(o) {
  return Object.assign({
    settings: settings(),
    temp_f: 37.5,
    fridge_on: true,
    reading_at: iso(Date.now() - 10000),
    age_seconds: 10,
    controller_ok: true,
    sensor_ok: true
  }, o);
}

/** Readings every `stepMin` minutes from `fromAgoH` hours ago to `toAgoH` hours ago. */
function points(fromAgoH, toAgoH, stepMin) {
  const out = [];
  for (let t = Date.now() - fromAgoH * HOUR; t <= Date.now() - toAgoH * HOUR; t += (stepMin || 5) * 60000) {
    out.push({ t: iso(t), temp: 38, target: 38, on: false });
  }
  return out;
}

function historyBody(pts, stats) {
  return {
    points: pts,
    stats: stats || { min: 36, max: 39, avg: 37.5, duty_percent: 15, samples: pts.length }
  };
}

/* ---------- harness ---------- */

const settle = async () => { for (let i = 0; i < 8; i++) { await new Promise((r) => setImmediate(r)); } };

async function boot(t, opts) {
  opts = opts || {};
  const state = {
    status: opts.status || statusBody(),
    history: opts.history || historyBody(points(24.1, 0, 5)),
    networkDown: !!opts.networkDown,
    responses: { settings: { status: 200, body: {} }, mode: { status: 200, body: {} } }
  };
  const calls = [];
  const draws = [];

  const dom = new JSDOM(HTML, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  t.after(() => win.close());

  // Canvas recorder: every method call is logged, nothing is drawn.
  const ctx = new Proxy({}, {
    get(target, prop) {
      if (prop in target) { return target[prop]; }
      return (...args) => { draws.push([prop].concat(args)); return prop === 'createPattern' ? {} : undefined; };
    },
    set(target, prop, value) { target[prop] = value; return true; }
  });
  win.HTMLCanvasElement.prototype.getContext = () => ctx;
  Object.defineProperty(win.HTMLCanvasElement.prototype, 'clientWidth', { get: () => 600 });
  Object.defineProperty(win.HTMLCanvasElement.prototype, 'clientHeight', { get: () => 300 });

  win.fetch = (url, init) => {
    init = init || {};
    const method = init.method || 'GET';
    const body = init.body ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, headers: init.headers || {}, body });
    if (state.networkDown) { return Promise.reject(new Error('network down')); }

    let res = { status: 404, body: { error: 'not found' } };
    if (method === 'GET' && url.startsWith('/api/status')) {
      res = { status: 200, body: state.status };
    } else if (method === 'GET' && url.startsWith('/api/history')) {
      const hours = parseFloat(new URL(url, 'http://x').searchParams.get('hours'));
      res = { status: 200, body: Object.assign({ hours }, state.history) };
    } else if (method === 'POST' && url === '/api/settings') {
      res = state.responses.settings;
      if (res.status < 400) { Object.assign(state.status.settings, body); }
    } else if (method === 'POST' && url === '/api/mode') {
      res = state.responses.mode;
      if (res.status < 400) { state.status.settings.mode = body.mode; }
    }
    return Promise.resolve({ ok: res.status < 400, status: res.status, json: () => Promise.resolve(res.body) });
  };

  win.eval(APP_JS);
  await settle();

  const $ = (id) => win.document.getElementById(id);
  const click = (selector) => { win.document.querySelector(selector).click(); };
  const posts = () => calls.filter((c) => c.method === 'POST');
  return { win, $, click, calls, posts, draws, state, settle };
}

/* ---------- status rendering ---------- */

test('renders live status', async (t) => {
  const ui = await boot(t);
  assert.equal(ui.$('temp').textContent, '37.5');
  assert.equal(ui.$('delta').textContent, 'Within target range');
  assert.equal(ui.$('fridge-state').dataset.state, 'on');
  assert.equal(ui.$('fridge-state-text').textContent, 'Cooling');
  assert.equal(ui.$('fact-target').textContent, '38');
  assert.equal(ui.$('fact-range').textContent, '37.0–39.0' + DEG);
  assert.equal(ui.$('fact-mode').textContent, 'Auto');
  assert.equal(ui.$('pill-controller').dataset.state, 'ok');
  assert.equal(ui.$('pill-sensor').dataset.state, 'ok');
  assert.equal(ui.$('banner').hidden, true);
});

test('says how far above or below target', async (t) => {
  let ui = await boot(t, { status: statusBody({ temp_f: 41 }) });
  assert.equal(ui.$('delta').textContent, '3.0' + DEG + ' above target');
  ui = await boot(t, { status: statusBody({ temp_f: 35.8, fridge_on: false }) });
  assert.equal(ui.$('delta').textContent, '2.2' + DEG + ' below target');
  assert.equal(ui.$('fridge-state-text').textContent, 'Idle');
});

test('stale controller shows a red banner and dims the reading', async (t) => {
  const ui = await boot(t, { status: statusBody({ controller_ok: false, age_seconds: 900 }) });
  assert.equal(ui.$('banner').hidden, false);
  assert.equal(ui.$('banner').dataset.level, 'bad');
  assert.match(ui.$('banner').textContent, /controller has not reported/i);
  assert.equal(ui.$('pill-controller').dataset.state, 'bad');
  assert.equal(ui.$('temp').parentNode.dataset.bad, '1');
  assert.equal(ui.$('fridge-state-text').textContent, 'Unknown');
  assert.match(ui.$('delta').textContent, /Last known reading/);
});

test('sensor failure shows dashes and a banner', async (t) => {
  const ui = await boot(t, { status: statusBody({ sensor_ok: false, temp_f: null }) });
  assert.equal(ui.$('temp').textContent, '--');
  assert.equal(ui.$('banner').dataset.level, 'bad');
  assert.match(ui.$('banner').textContent, /sensor/i);
  assert.equal(ui.$('pill-sensor').dataset.state, 'bad');
});

test('no readings at all explains what to check', async (t) => {
  const ui = await boot(t, {
    status: statusBody({ reading_at: null, age_seconds: null, controller_ok: false, sensor_ok: false, temp_f: null, fridge_on: null })
  });
  assert.match(ui.$('banner').textContent, /No readings yet/);
});

test('manual override is called out', async (t) => {
  const ui = await boot(t, { status: statusBody({ settings: settings({ mode: 'off' }) }) });
  assert.equal(ui.$('banner').hidden, false);
  assert.equal(ui.$('banner').dataset.level, 'warn');
  assert.match(ui.$('banner').textContent, /forced off/);
  assert.equal(ui.$('fact-mode').textContent, 'Forced off');
});

test('unreachable server shows a retry banner', async (t) => {
  const ui = await boot(t, { networkDown: true });
  assert.equal(ui.$('banner').hidden, false);
  assert.match(ui.$('banner').textContent, /reach the web service/);
});

/* ---------- controls ---------- */

test('steppers edit the form, update the hint and enable Apply', async (t) => {
  const ui = await boot(t);
  assert.equal(ui.$('target').value, '38');
  assert.equal(ui.$('hysteresis').value, '1.0');
  assert.equal(ui.$('apply').disabled, true);

  ui.click('[data-for="target"][data-step="1"]');
  ui.click('[data-for="hysteresis"][data-step="0.5"]');

  assert.equal(ui.$('target').value, '39');
  assert.equal(ui.$('hysteresis').value, '1.5');
  assert.equal(ui.$('apply').disabled, false);
  assert.equal(ui.$('band-hint').textContent,
    'Starts cooling above 40.5' + DEG + 'F, stops below 37.5' + DEG + 'F.');
});

test('steppers stop at the allowed limits', async (t) => {
  const ui = await boot(t);
  ui.$('target').value = '70';
  for (let i = 0; i < 3; i++) { ui.click('[data-for="target"][data-step="1"]'); }
  assert.equal(ui.$('target').value, '70');
  ui.$('hysteresis').value = '0.5';
  ui.click('[data-for="hysteresis"][data-step="-0.5"]');
  assert.equal(ui.$('hysteresis').value, '0.5');
});

test('invalid input disables Apply and explains the limits', async (t) => {
  const ui = await boot(t);
  ui.$('target').value = '5';
  ui.$('target').dispatchEvent(new ui.win.Event('input'));
  assert.equal(ui.$('apply').disabled, true);
  assert.match(ui.$('band-hint').textContent, /20.70/);
});

test('Apply posts the settings with the CSRF token', async (t) => {
  const ui = await boot(t);
  ui.click('[data-for="target"][data-step="1"]');
  ui.click('#apply');
  await ui.settle();

  const [post] = ui.posts();
  assert.equal(post.url, '/api/settings');
  assert.deepEqual(post.body, { target_temp: 39, hysteresis: 1 });
  assert.equal(post.headers['X-CSRFToken'], 'test-token');
  assert.equal(post.headers['Content-Type'], 'application/json');
  assert.equal(ui.$('toast').hidden, false);
  assert.match(ui.$('toast').textContent, /Saved/);
  assert.equal(ui.$('fact-target').textContent, '39');   // refreshed from the server
});

test('a rejected save shows the server message and re-enables Apply', async (t) => {
  const ui = await boot(t);
  ui.state.responses.settings = { status: 400, body: { error: 'target_temp must be between 20 and 70' } };
  ui.click('[data-for="target"][data-step="1"]');
  ui.click('#apply');
  await ui.settle();
  assert.equal(ui.$('toast').textContent, 'target_temp must be between 20 and 70');
  assert.equal(ui.$('toast').dataset.level, 'bad');
  assert.equal(ui.$('apply').disabled, false);
});

test('polling does not overwrite values being edited', async (t) => {
  const ui = await boot(t);
  ui.$('target').value = '45';
  ui.$('target').dispatchEvent(new ui.win.Event('input'));
  ui.win.document.dispatchEvent(new ui.win.Event('visibilitychange'));   // triggers a refresh
  await ui.settle();
  assert.equal(ui.$('target').value, '45');
});

test('mode buttons post the new mode and the page reflects it', async (t) => {
  const ui = await boot(t);
  ui.click('[data-mode="on"]');
  await ui.settle();

  const [post] = ui.posts();
  assert.equal(post.url, '/api/mode');
  assert.deepEqual(post.body, { mode: 'on' });
  assert.equal(post.headers['X-CSRFToken'], 'test-token');
  assert.ok(ui.$('mode').querySelector('[data-mode="on"]').classList.contains('active'));
  assert.equal(ui.$('mode').querySelector('[data-mode="on"]').getAttribute('aria-checked'), 'true');
  assert.equal(ui.$('mode').querySelector('[data-mode="auto"]').getAttribute('aria-checked'), 'false');
  assert.match(ui.$('banner').textContent, /forced on/);
});

test('clicking the active mode does nothing', async (t) => {
  const ui = await boot(t);
  ui.click('[data-mode="auto"]');
  await ui.settle();
  assert.equal(ui.posts().length, 0);
});

/* ---------- history and chart ---------- */

test('shows history stats', async (t) => {
  const ui = await boot(t);
  assert.equal(ui.$('stat-min').textContent, '36.0' + DEG);
  assert.equal(ui.$('stat-avg').textContent, '37.5' + DEG);
  assert.equal(ui.$('stat-max').textContent, '39.0' + DEG);
  assert.equal(ui.$('stat-duty').textContent, '15%');
});

test('empty history shows the empty message and dashes', async (t) => {
  const ui = await boot(t, {
    history: historyBody([], { min: null, max: null, avg: null, duty_percent: null, samples: 0 })
  });
  assert.equal(ui.$('chart-empty').hidden, false);
  assert.equal(ui.$('stat-min').textContent, '--');
  assert.equal(ui.$('stat-duty').textContent, '--');
});

test('range buttons fetch that window and move the selection', async (t) => {
  const ui = await boot(t);
  ui.click('[data-hours="6"]');
  await ui.settle();
  assert.ok(ui.calls.some((c) => c.url === '/api/history?hours=6'));
  assert.ok(ui.$('range').querySelector('[data-hours="6"]').classList.contains('active'));
  assert.ok(!ui.$('range').querySelector('[data-hours="24"]').classList.contains('active'));
  assert.equal(ui.$('range').querySelector('[data-hours="6"]').getAttribute('aria-selected'), 'true');
});

const noDataLabels = (ui) => {
  ui.draws.length = 0;
  ui.win.dispatchEvent(new ui.win.Event('resize'));   // forces a redraw
  return ui.draws.filter((d) => d[0] === 'fillText' && d[1] === 'No data').length;
};

test('chart: no "No data" label when the window is fully covered', async (t) => {
  const ui = await boot(t);   // 24.1h of readings in a 24h window
  assert.equal(noDataLabels(ui), 0);
});

test('chart: labels the empty stretch before the first reading', async (t) => {
  const ui = await boot(t, { history: historyBody(points(2, 0, 1)) });   // only 2h recorded
  assert.equal(noDataLabels(ui), 1);
});

test('chart: labels a gap in the middle (controller outage)', async (t) => {
  const pts = points(24.1, 12, 5).concat(points(6, 0, 5));   // 6h hole
  const ui = await boot(t, { history: historyBody(pts) });
  assert.equal(noDataLabels(ui), 1);
});

test('chart draws with missing temperatures and a lone point without throwing', async (t) => {
  const pts = points(3, 0, 10).map((p, i) => (i % 4 === 0 ? Object.assign(p, { temp: null }) : p));
  const ui = await boot(t, { history: historyBody(pts) });
  assert.doesNotThrow(() => ui.win.dispatchEvent(new ui.win.Event('resize')));

  const lone = await boot(t, { history: historyBody([points(0.1, 0, 5)[0]]) });
  assert.doesNotThrow(() => lone.win.dispatchEvent(new lone.win.Event('resize')));
});

const hover = (ui, x) => {
  ui.$('chart').dispatchEvent(new ui.win.MouseEvent('pointermove', { clientX: x, clientY: 100, bubbles: true }));
};

test('tooltip appears near a reading and shows its values', async (t) => {
  const ui = await boot(t);
  hover(ui, 589);   // right edge of the plot, where the newest reading is
  assert.equal(ui.$('tooltip').hidden, false);
  assert.match(ui.$('tooltip').textContent, new RegExp('38\\.0' + DEG + 'F'));
  assert.match(ui.$('tooltip').textContent, /Target 38/);
});

test('tooltip stays hidden over the empty "No data" area', async (t) => {
  const ui = await boot(t, { history: historyBody(points(2, 0, 1)) });
  hover(ui, 100);   // far from the readings bunched at the right edge
  assert.equal(ui.$('tooltip').hidden, true);
});
