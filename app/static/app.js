(function () {
  'use strict';

  var STATUS_EVERY_MS = 5000;
  var HISTORY_EVERY_MS = 60000;
  var DEFAULT_HOURS = 24;
  var $ = function (id) { return document.getElementById(id); };

  // Self-heal for an unattended screen. If the page has not shown live data a
  // minute after loading (its script broke on load, e.g. a new script meeting an
  // old cached page in the middle of a deploy), reload it: nobody is there to
  // press F5. It only reloads when the server answers; if the server is down the
  // page keeps its "Can't reach the web service" banner and retries, which beats
  // swapping it for the browser's own error page. Armed first, before anything
  // below can throw.
  var booted = false;
  var BOOT_CHECK_MS = window.keezerBootMs || 60 * 1000;   // keezerBootMs: test seam
  function bootWatchdog() {
    if (booted) { return; }
    var again = function () { setTimeout(bootWatchdog, BOOT_CHECK_MS); };
    fetch('/api/status', { cache: 'no-store' }).then(function (r) {
      if (booted) { return; }
      if (r.ok) { reloadPage(); } else { again(); }
    }).catch(again);
  }
  setTimeout(bootWatchdog, BOOT_CHECK_MS);

  // Wall display (/?display=1): bigger, read-only, hides the mouse cursor when idle.
  var root = document.documentElement;
  var DISPLAY = /[?&]display(=1|=true|=on)?(&|$)/.test(window.location.search);
  var READONLY = /[?&]readonly(=1|=true|=on)?(&|$)/.test(window.location.search);   // hides the controls
  if (DISPLAY) {
    root.classList.add('display');
    if (READONLY) { root.classList.add('readonly'); }
    hideCursorWhenIdle();
  }

  function hideCursorWhenIdle() {
    var timer = null;
    function wake() {
      root.classList.remove('idle');
      clearTimeout(timer);
      timer = setTimeout(function () { root.classList.add('idle'); }, 5000);
    }
    ['mousemove', 'mousedown', 'keydown'].forEach(function (name) { document.addEventListener(name, wake); });
    wake();
  }

  var state = {
    version: null,
    settings: null,
    status: null,
    history: { points: [], stats: {}, hours: 24 },
    hours: 24,
    dirty: false,
    hover: null,
    hoverEvent: null
  };

  /* ---------- helpers ---------- */

  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : '';
  }

  function api(path, body) {
    var opts = { headers: { 'Accept': 'application/json' }, cache: 'no-store' };
    if (body !== undefined) {
      opts.method = 'POST';
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-CSRFToken'] = csrfToken();
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) { throw new Error(data.error || ('Request failed (' + r.status + ')')); }
        return data;
      });
    });
  }

  function fmt(n, digits) {
    return (n === null || n === undefined || isNaN(n)) ? '--' : Number(n).toFixed(digits === undefined ? 1 : digits);
  }

  // Whole minutes show without a decimal; a value set from the command line
  // such as 18.3 keeps its decimal so that saving other settings can't round it.
  function fmtMinutes(n) {
    n = Number(n);
    if (isNaN(n) || n <= 0) { return '0'; }
    return n % 1 === 0 ? n.toFixed(0) : n.toFixed(1);
  }

  function ago(seconds) {
    if (seconds === null || seconds === undefined) { return 'never'; }
    if (seconds < 5) { return 'just now'; }
    if (seconds < 90) { return Math.round(seconds) + 's ago'; }
    if (seconds < 5400) { return Math.round(seconds / 60) + 'm ago'; }
    return Math.round(seconds / 3600) + 'h ago';
  }

  var toastTimer = null;
  function toast(message, level) {
    var el = $('toast');
    el.textContent = message;
    el.dataset.level = level || 'info';
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, 4000);
  }

  /* ---------- status ---------- */

  function render() {
    var s = state.status;
    if (!s) { return; }
    var cfg = s.settings;
    state.settings = cfg;

    var tempEl = $('temp');
    tempEl.textContent = s.sensor_ok ? fmt(s.temp_f, 1) : '--';
    var live = s.sensor_ok && s.controller_ok;
    tempEl.parentNode.dataset.bad = live ? '0' : '1';

    var low = cfg.target_temp - cfg.hysteresis;
    var high = cfg.target_temp + cfg.hysteresis;
    var delta = $('delta');
    if (!s.sensor_ok) {
      delta.textContent = 'No valid sensor reading';
    } else if (!s.controller_ok) {
      delta.textContent = 'Last known reading, ' + ago(s.age_seconds);
    } else if (s.temp_f > high) {
      delta.textContent = fmt(s.temp_f - cfg.target_temp) + '° above target';
    } else if (s.temp_f < low) {
      delta.textContent = fmt(cfg.target_temp - s.temp_f) + '° below target';
    } else {
      delta.textContent = 'Within target range';
    }

    var st = $('fridge-state');
    var stText = $('fridge-state-text');
    if (!s.controller_ok) { st.dataset.state = 'unknown'; stText.textContent = 'Unknown'; }
    else if (s.fridge_on === true) { st.dataset.state = 'on'; stText.textContent = 'Cooling'; }
    else if (s.fridge_on === false) { st.dataset.state = 'off'; stText.textContent = 'Idle'; }
    else { st.dataset.state = 'unknown'; stText.textContent = 'Unknown'; }

    $('fact-target').textContent = fmt(cfg.target_temp, 1);
    $('fact-range').textContent = fmt(low) + '–' + fmt(high) + '°';
    $('fact-mode').textContent = { auto: 'Auto', on: 'Forced on', off: 'Forced off' }[cfg.mode] || cfg.mode;
    $('fact-updated').textContent = ago(s.age_seconds);

    setPill('pill-controller', s.controller_ok);
    setPill('pill-sensor', s.sensor_ok);
    var alertsPill = $('pill-alerts');
    alertsPill.dataset.state = !s.notifications_enabled ? 'unknown' : (s.alerts && s.alerts.length ? 'bad' : 'ok');
    alertsPill.title = s.notifications_enabled ? 'Push alerts are on' : 'Push alerts are off (set NTFY_TOPIC to enable)';

    renderBanner(s, cfg);
    renderAlerts(s.alerts || []);
    renderMode(cfg.mode);
    updateTitle(s, live);

    if (!state.dirty) {
      $('target').value = fmt(cfg.target_temp, 1);
      $('hysteresis').value = fmt(cfg.hysteresis, 1);
      $('coast').value = fmtMinutes(cfg.coast_minutes);
      updateBandHint();
    }
    draw();
  }

  function setPill(id, ok) {
    $(id).dataset.state = ok ? 'ok' : 'bad';
  }

  // Browser tab title: "38.1°F · Keezer", so a pinned tab doubles as a gauge.
  function updateTitle(s, live) {
    var warn = (s.alerts && s.alerts.length) || !s.controller_ok || !s.sensor_ok;
    var temp = live ? fmt(s.temp_f, 1) + '°F · ' : '';
    document.title = (warn ? '⚠ ' : '') + temp + 'Keezer';
  }

  function renderAlerts(list) {
    var el = $('alerts');
    el.textContent = '';
    list.forEach(function (a) {
      var li = document.createElement('li');
      var msg = document.createElement('span');
      msg.textContent = a.message;
      li.appendChild(msg);
      if (a.since) {
        var since = document.createElement('small');
        since.textContent = 'since ' + new Date(a.since).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
        li.appendChild(since);
      }
      el.appendChild(li);
    });
    el.hidden = list.length === 0;
  }

  function renderBanner(s, cfg) {
    var el = $('banner');
    var msg = null, level = 'warn';
    if (s.reading_at === null) {
      msg = 'No readings yet. Is the controller service running?'; level = 'bad';
    } else if (!s.controller_ok) {
      msg = 'The controller has not reported for ' + ago(s.age_seconds).replace(' ago', '') +
            '. The freezer is not being regulated. Check the keezer service on the Pi.';
      level = 'bad';
    } else if (!s.sensor_ok) {
      msg = 'The temperature sensor is not returning a valid reading. The controller is holding its current state.';
      level = 'bad';
    } else if (cfg.mode !== 'auto') {
      msg = 'Manual override: the fridge is forced ' + cfg.mode + '. The thermostat is ignored until you switch back to Auto.';
    }
    el.hidden = !msg;
    if (msg) { el.textContent = msg; el.dataset.level = level; }
  }

  var MODE_HINTS = {
    auto: 'Auto holds the target temperature.',
    on: 'The fridge runs continuously until you switch back to Auto.',
    off: 'The fridge stays off until you switch back to Auto.'
  };

  function renderMode(mode) {
    var buttons = $('mode').querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) {
      var active = buttons[i].dataset.mode === mode;
      buttons[i].classList.toggle('active', active);
      buttons[i].setAttribute('aria-checked', active ? 'true' : 'false');
    }
    $('mode-hint').textContent = MODE_HINTS[mode] || '';
  }

  // The server reports a version that changes when it restarts or a page file is
  // updated. If it changes under us, reload, so a wall display never goes stale.
  // (Unless you're mid-edit; the next poll tries again.)
  function reloadPage() {
    if (typeof window.keezerReload === 'function') { window.keezerReload(); }   // test seam
    else { window.location.reload(); }
  }

  function checkVersion(version) {
    if (!version) { return; }
    if (state.version === null) { state.version = version; }
    else if (version !== state.version && !state.dirty) { reloadPage(); }
  }

  function refreshStatus() {
    return api('/api/status').then(function (data) {
      state.status = data;
      checkVersion(data.version);
      render();
      booted = true;
    }).catch(function () {
      setPill('pill-controller', false);
      var el = $('banner');
      el.hidden = false;
      el.dataset.level = 'bad';
      el.textContent = 'Can’t reach the web service. Retrying…';
    });
  }

  /* ---------- history + chart ---------- */

  function refreshHistory() {
    return api('/api/history?hours=' + state.hours).then(function (data) {
      state.history = data;
      var st = data.stats || {};
      $('stat-min').textContent = st.min === null || st.min === undefined ? '--' : fmt(st.min) + '°';
      $('stat-avg').textContent = st.avg === null || st.avg === undefined ? '--' : fmt(st.avg) + '°';
      $('stat-max').textContent = st.max === null || st.max === undefined ? '--' : fmt(st.max) + '°';
      $('stat-duty').textContent = st.duty_percent === null || st.duty_percent === undefined ? '--' : fmt(st.duty_percent, 0) + '%';
      draw();
    }).catch(function () { /* the status banner already reports connectivity */ });
  }

  var canvas = $('chart');
  var ctx = canvas.getContext('2d');
  var layout = null;

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function timeLabel(ms, hours) {
    var d = new Date(ms);
    if (hours > 48) {
      return d.toLocaleDateString([], { weekday: 'short', day: 'numeric' });
    }
    return d.toLocaleTimeString([], { hour: 'numeric', minute: hours <= 6 ? '2-digit' : undefined });
  }

  function niceStep(span, ticks) {
    var raw = span / ticks;
    var pow = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var n = raw / pow;
    return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * pow;
  }

  function draw() {
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) { return; }
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    var pts = (state.history.points || []).map(function (p) {
      return { t: Date.parse(p.t), temp: p.temp, target: p.target, on: p.on };
    });
    $('chart-empty').hidden = pts.length > 0;
    layout = null;
    if (!pts.length) { return; }

    var hyst = state.settings ? state.settings.hysteresis : 1;
    var hours = state.history.hours || state.hours;
    var tMax = Date.now();
    var tMin = tMax - hours * 3600 * 1000;
    var evs = (state.history.events || []).map(function (e) {
      return { t: Date.parse(e.t), kind: e.kind, detail: e.detail };
    }).filter(function (e) { return e.t >= tMin && e.t <= tMax; });

    // Scale to temperatures and the *current* band, so an old target
    // setting doesn't stretch the axis. Older bands are clipped.
    var curTarget = state.settings ? state.settings.target_temp : pts[pts.length - 1].target;
    var lo = Infinity, hi = -Infinity;
    pts.forEach(function (p) {
      if (p.temp !== null) { lo = Math.min(lo, p.temp); hi = Math.max(hi, p.temp); }
    });
    if (curTarget !== null && curTarget !== undefined) {
      lo = Math.min(lo, curTarget - hyst); hi = Math.max(hi, curTarget + hyst);
    }
    if (!isFinite(lo)) { lo = 30; hi = 50; }
    var pad = Math.max(1, (hi - lo) * 0.12);
    lo -= pad; hi += pad;
    var step = niceStep(hi - lo, 4);
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;

    // Everything scales with the chart's width: 11px text on a phone, up to 20px on a wall display.
    var fs = Math.max(11, Math.min(20, Math.round(w / 60)));
    var k = fs / 11;
    var m = { l: Math.round(fs * 3.64), r: Math.round(fs * 0.91), t: Math.round(fs * 0.91), b: Math.round(fs * 2.36) };
    var pw = w - m.l - m.r, ph = h - m.t - m.b;
    var X = function (t) { return m.l + (t - tMin) / (tMax - tMin) * pw; };
    var Y = function (v) { return m.t + (1 - (v - lo) / (hi - lo)) * ph; };
    layout = { pts: pts, events: evs, X: X, Y: Y, m: m, pw: pw, ph: ph, w: w, h: h };

    var cText = css('--muted'), cLine = css('--line'), cAccent = css('--accent'), cOn = css('--on'),
        cEvent = css('--event');

    ctx.font = fs + 'px system-ui, sans-serif';
    ctx.textBaseline = 'middle';

    // horizontal grid + y labels
    ctx.lineWidth = 1;
    for (var v = lo; v <= hi + 1e-9; v += step) {
      var y = Math.round(Y(v)) + 0.5;
      ctx.strokeStyle = cLine; ctx.globalAlpha = 0.7;
      ctx.beginPath(); ctx.moveTo(m.l, y); ctx.lineTo(w - m.r, y); ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = cText; ctx.textAlign = 'right';
      ctx.fillText(Math.round(v * 10) / 10 + '°', m.l - 6, y);
    }

    // x labels
    ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.fillStyle = cText;
    var labels = w < 480 ? 4 : 6;
    for (var i = 0; i <= labels; i++) {
      var tt = tMin + (tMax - tMin) * i / labels;
      ctx.textAlign = i === 0 ? 'left' : i === labels ? 'right' : 'center';
      ctx.fillText(timeLabel(tt, hours), X(tt), h - m.b + Math.round(7 * k));
    }

    // typical sample spacing, to detect gaps (controller down)
    var gaps = [];
    for (var g = 1; g < pts.length; g++) { gaps.push(pts[g].t - pts[g - 1].t); }
    gaps.sort(function (a, b) { return a - b; });
    var typical = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 60000;
    var maxGap = Math.max(typical * 4, 150000);

    ctx.save();
    ctx.beginPath(); ctx.rect(m.l, m.t, pw, ph + 1); ctx.clip();

    // "No data": before the first reading, and across controller outages
    var holes = [];
    if (pts[0].t > tMin) { holes.push([tMin, pts[0].t]); }
    for (var q = 1; q < pts.length; q++) {
      if (pts[q].t - pts[q - 1].t > maxGap) { holes.push([pts[q - 1].t, pts[q].t]); }
    }
    var pattern = hatch(cLine);
    holes.forEach(function (hole) {
      var hx0 = Math.max(m.l, X(hole[0])), hx1 = Math.min(m.l + pw, X(hole[1]));
      if (hx1 - hx0 < 2) { return; }
      ctx.fillStyle = pattern; ctx.globalAlpha = 0.6;
      ctx.fillRect(hx0, m.t, hx1 - hx0, ph);
      ctx.globalAlpha = 1;
      if (hx1 - hx0 > 70) {
        ctx.fillStyle = cText; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText('No data', (hx0 + hx1) / 2, m.t + ph / 2);
      }
    });

    // target band
    var segs = splitSegments(pts, maxGap, function (p) { return p.target !== null && p.target !== undefined; });
    ctx.fillStyle = 'rgba(76, 201, 240, 0.14)';
    segs.forEach(function (seg) {
      if (seg.length < 1) { return; }
      ctx.beginPath();
      seg.forEach(function (p, idx) { var px = X(p.t), py = Y(p.target + hyst); idx ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
      for (var k = seg.length - 1; k >= 0; k--) { ctx.lineTo(X(seg[k].t), Y(seg[k].target - hyst)); }
      ctx.closePath(); ctx.fill();
    });

    // dashed target line through the middle of the band
    ctx.save();
    ctx.strokeStyle = cAccent; ctx.globalAlpha = 0.55; ctx.lineWidth = Math.max(1, k); ctx.setLineDash([6, 4]);
    segs.forEach(function (seg) {
      ctx.beginPath();
      seg.forEach(function (p, idx) { idx ? ctx.lineTo(X(p.t), Y(p.target)) : ctx.moveTo(X(p.t), Y(p.target)); });
      ctx.stroke();
    });
    ctx.restore();

    // fridge-running strip along the bottom
    ctx.fillStyle = cOn; ctx.globalAlpha = 0.85;
    for (var n = 0; n < pts.length; n++) {
      if (pts[n].on) {
        var x0 = X(pts[n].t);
        var nxt = pts[n + 1];
        var x1 = nxt && nxt.t - pts[n].t <= maxGap ? X(nxt.t) : x0 + Math.max(2, pw * typical / (tMax - tMin));
        ctx.fillRect(x0, m.t + ph - 5 * k, Math.max(1.5, x1 - x0), 5 * k);
      }
    }
    ctx.globalAlpha = 1;

    // setting changes: dashed vertical line with a diamond at the top
    evs.forEach(function (e, idx) {
      var ex = Math.round(X(e.t)) + 0.5;
      var hot = state.hoverEvent === idx;
      ctx.save();
      ctx.strokeStyle = cEvent; ctx.fillStyle = cEvent;
      ctx.globalAlpha = hot ? 1 : 0.75; ctx.lineWidth = hot ? 2 : 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(ex, m.t); ctx.lineTo(ex, m.t + ph); ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(ex, m.t + k); ctx.lineTo(ex + 5 * k, m.t + 6 * k); ctx.lineTo(ex, m.t + 11 * k); ctx.lineTo(ex - 5 * k, m.t + 6 * k);
      ctx.closePath(); ctx.fill();
      ctx.restore();
    });

    // temperature line
    ctx.strokeStyle = cAccent; ctx.lineWidth = 2 * k; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    splitSegments(pts, maxGap, function (p) { return p.temp !== null; }).forEach(function (seg) {
      ctx.beginPath();
      seg.forEach(function (p, idx) { idx ? ctx.lineTo(X(p.t), Y(p.temp)) : ctx.moveTo(X(p.t), Y(p.temp)); });
      if (seg.length === 1) { ctx.lineTo(X(seg[0].t) + 0.1, Y(seg[0].temp)); }
      ctx.stroke();
    });
    ctx.restore();

    // hover crosshair
    var hp = state.hover !== null ? pts[state.hover] : null;
    if (hp) {
      var hx = X(hp.t);
      ctx.strokeStyle = cText; ctx.globalAlpha = 0.5; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(Math.round(hx) + 0.5, m.t); ctx.lineTo(Math.round(hx) + 0.5, m.t + ph); ctx.stroke();
      ctx.globalAlpha = 1;
      if (hp.temp !== null) {
        ctx.fillStyle = css('--bg'); ctx.strokeStyle = cAccent; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(hx, Y(hp.temp), 4 * k, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      }
    }
    positionTooltip(hp, state.hoverEvent !== null ? evs[state.hoverEvent] : null);
  }

  function hatch(color) {
    var tile = document.createElement('canvas');
    tile.width = tile.height = 8;
    var g = tile.getContext('2d');
    g.strokeStyle = color; g.lineWidth = 1;
    g.beginPath(); g.moveTo(-2, 10); g.lineTo(10, -2); g.stroke();
    return ctx.createPattern(tile, 'repeat');
  }

  function splitSegments(pts, maxGap, valid) {
    var out = [], cur = [];
    pts.forEach(function (p, i) {
      var brk = i > 0 && p.t - pts[i - 1].t > maxGap;
      if (!valid(p) || brk) {
        if (cur.length) { out.push(cur); cur = []; }
      }
      if (valid(p)) { cur.push(p); }
    });
    if (cur.length) { out.push(cur); }
    return out;
  }

  function positionTooltip(p, ev) {
    var tip = $('tooltip');
    var subject = ev || p;
    if (!subject || !layout) { tip.hidden = true; return; }
    var when = new Date(subject.t).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
    tip.innerHTML = '';
    if (ev) {
      var eb = document.createElement('b');
      eb.className = 'event-title';
      eb.textContent = 'Setting changed';
      var e1 = document.createElement('div'); e1.textContent = ev.detail;
      var e2 = document.createElement('div'); e2.textContent = when;
      tip.appendChild(eb); tip.appendChild(e1); tip.appendChild(e2);
    } else {
      var b = document.createElement('b');
      b.textContent = p.temp === null ? 'No reading' : fmt(p.temp, 1) + '°F';
      var l1 = document.createElement('div'); l1.textContent = when;
      var l2 = document.createElement('div');
      l2.textContent = 'Target ' + (p.target === null || p.target === undefined ? '--' : fmt(p.target, 1) + '°') +
                       ' · ' + (p.on === null ? 'unknown' : p.on ? 'cooling' : 'idle');
      tip.appendChild(b); tip.appendChild(l1); tip.appendChild(l2);
    }
    tip.hidden = false;
    var x = layout.X(subject.t);
    var left = x + 12;
    if (left + tip.offsetWidth > layout.w - 4) { left = x - tip.offsetWidth - 12; }
    tip.style.left = Math.max(4, left) + 'px';
    tip.style.top = (layout.m.t + 4) + 'px';
  }

  function onPointer(ev) {
    if (!layout) { return; }
    var rect = canvas.getBoundingClientRect();
    var x = ev.clientX - rect.left;
    var best = null, bestD = Infinity;
    layout.pts.forEach(function (p, i) {
      var d = Math.abs(layout.X(p.t) - x);
      if (d < bestD) { bestD = d; best = i; }
    });
    var bestEvent = null, bestEventD = Infinity;
    (layout.events || []).forEach(function (e, i) {
      var d = Math.abs(layout.X(e.t) - x);
      if (d < bestEventD) { bestEventD = d; bestEvent = i; }
    });
    if (bestEventD <= 12) {            // a setting-change marker wins over the line
      state.hoverEvent = bestEvent;
      state.hover = null;
    } else {
      state.hoverEvent = null;
      state.hover = bestD <= 30 ? best : null;  // ignore the empty "no data" area
    }
    draw();
  }

  canvas.addEventListener('pointermove', onPointer);
  canvas.addEventListener('pointerdown', onPointer);
  canvas.addEventListener('pointerleave', function () { state.hover = null; state.hoverEvent = null; draw(); });
  window.addEventListener('resize', draw);
  if (window.ResizeObserver) { new ResizeObserver(draw).observe(canvas); }

  /* ---------- controls ---------- */

  function readInputs() {
    return {
      target_temp: parseFloat($('target').value),
      hysteresis: parseFloat($('hysteresis').value),
      coast_minutes: parseFloat($('coast').value)
    };
  }

  function validInputs(v) {
    return !isNaN(v.target_temp) && v.target_temp >= 20 && v.target_temp <= 70 &&
           Math.abs(v.target_temp * 2 - Math.round(v.target_temp * 2)) < 1e-9 &&
           !isNaN(v.hysteresis) && v.hysteresis >= 0.5 && v.hysteresis <= 10 &&
           !isNaN(v.coast_minutes) && v.coast_minutes >= 0 && v.coast_minutes <= 60;
  }

  function updateBandHint() {
    var v = readInputs();
    var hint = $('band-hint');
    if (!validInputs(v)) {
      hint.textContent = 'Target must be 20–70°F in half-degree steps, swing 0.5–10°F and coast 0–60 minutes.';
      return;
    }
    var coast = v.coast_minutes;     // what the form says, so the hint previews what Apply will do
    if (coast > 0) {
      // the controller stops early so the temperature coasts down to about the low edge
      hint.textContent = 'Starts cooling above ' + fmt(v.target_temp + v.hysteresis) +
                         '°F and stops early so it coasts down to about ' + fmt(v.target_temp - v.hysteresis) +
                         '°F (coast ' + fmtMinutes(coast) + ' min).';
      return;
    }
    hint.textContent = 'Starts cooling above ' + fmt(v.target_temp + v.hysteresis) +
                       '°F, stops below ' + fmt(v.target_temp - v.hysteresis) + '°F.';
  }

  function markDirty() {
    state.dirty = true;
    updateBandHint();
    $('apply').disabled = !validInputs(readInputs());
  }

  document.querySelectorAll('.stepper button').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var input = $(btn.dataset.for);
      var cur = parseFloat(input.value);
      if (isNaN(cur)) { cur = parseFloat(input.min); }
      var next = cur + parseFloat(btn.dataset.step);
      next = Math.min(parseFloat(input.max), Math.max(parseFloat(input.min), next));
      if (input.dataset.digits === '0') {
        input.value = String(Math.round(next));          // whole minutes
      } else {
        input.value = (Math.round(next * 2) / 2).toFixed(1);   // half degrees
      }
      markDirty();
    });
  });
  $('target').addEventListener('input', markDirty);
  $('hysteresis').addEventListener('input', markDirty);
  $('coast').addEventListener('input', markDirty);

  $('apply').addEventListener('click', function () {
    var v = readInputs();
    if (!validInputs(v)) { return; }
    $('apply').disabled = true;
    api('/api/settings', v).then(function () {
      state.dirty = false;
      toast('Saved. The controller applies it within 30 seconds.');
      return refreshStatus();
    }).catch(function (err) {
      toast(err.message, 'bad');
      $('apply').disabled = false;
    });
  });

  $('mode').addEventListener('click', function (ev) {
    var btn = ev.target.closest('button[data-mode]');
    if (!btn || btn.classList.contains('active')) { return; }
    var mode = btn.dataset.mode;
    api('/api/mode', { mode: mode }).then(function () {
      toast(mode === 'auto' ? 'Auto mode on.' : 'Fridge forced ' + mode + '. Applies within 30 seconds.');
      return refreshStatus();
    }).catch(function (err) { toast(err.message, 'bad'); });
  });

  // After a few idle minutes on another range, go back to the 24h overview, so a
  // wall display (or a forgotten tab) doesn't stay stuck on 1h.
  var rangeTimer = null;

  function selectRange(hours) {
    state.hours = hours;
    state.hover = null;
    state.hoverEvent = null;
    $('range').querySelectorAll('button').forEach(function (b) {
      var on = parseInt(b.dataset.hours, 10) === hours;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    refreshHistory();
    clearTimeout(rangeTimer);
    if (hours !== DEFAULT_HOURS) {
      rangeTimer = setTimeout(function () { selectRange(DEFAULT_HOURS); },
                              window.keezerIdleMs || 5 * 60 * 1000);   // keezerIdleMs: test seam
    }
  }

  $('range').addEventListener('click', function (ev) {
    var btn = ev.target.closest('button[data-hours]');
    if (btn) { selectRange(parseInt(btn.dataset.hours, 10)); }
  });

  /* ---------- polling ---------- */

  setInterval(function () { if (!document.hidden) { refreshStatus(); } }, STATUS_EVERY_MS);
  setInterval(function () { if (!document.hidden) { refreshHistory(); } }, HISTORY_EVERY_MS);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) { refreshStatus(); refreshHistory(); }
  });

  refreshStatus().then(refreshHistory);
})();
