(function () {
  'use strict';

  var STATUS_EVERY_MS = 5000;
  var HISTORY_EVERY_MS = 60000;
  var $ = function (id) { return document.getElementById(id); };

  var state = {
    settings: null,
    status: null,
    history: { points: [], stats: {}, hours: 24 },
    hours: 24,
    dirty: false,
    hover: null
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

    $('fact-target').textContent = cfg.target_temp;
    $('fact-range').textContent = fmt(low) + '–' + fmt(high) + '°';
    $('fact-mode').textContent = { auto: 'Auto', on: 'Forced on', off: 'Forced off' }[cfg.mode] || cfg.mode;
    $('fact-updated').textContent = ago(s.age_seconds);

    setPill('pill-controller', s.controller_ok);
    setPill('pill-sensor', s.sensor_ok);

    renderBanner(s, cfg);
    renderMode(cfg.mode);

    if (!state.dirty) {
      $('target').value = cfg.target_temp;
      $('hysteresis').value = fmt(cfg.hysteresis, 1);
      updateBandHint();
    }
    draw();
  }

  function setPill(id, ok) {
    $(id).dataset.state = ok ? 'ok' : 'bad';
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

  function refreshStatus() {
    return api('/api/status').then(function (data) {
      state.status = data;
      render();
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

    var lo = Infinity, hi = -Infinity;
    pts.forEach(function (p) {
      if (p.temp !== null) { lo = Math.min(lo, p.temp); hi = Math.max(hi, p.temp); }
      if (p.target !== null && p.target !== undefined) {
        lo = Math.min(lo, p.target - hyst); hi = Math.max(hi, p.target + hyst);
      }
    });
    if (!isFinite(lo)) { lo = 30; hi = 50; }
    var pad = Math.max(1, (hi - lo) * 0.12);
    lo -= pad; hi += pad;
    var step = niceStep(hi - lo, 4);
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;

    var m = { l: 40, r: 10, t: 10, b: 26 };
    var pw = w - m.l - m.r, ph = h - m.t - m.b;
    var X = function (t) { return m.l + (t - tMin) / (tMax - tMin) * pw; };
    var Y = function (v) { return m.t + (1 - (v - lo) / (hi - lo)) * ph; };
    layout = { pts: pts, X: X, Y: Y, m: m, pw: pw, ph: ph, w: w, h: h };

    var cText = css('--muted'), cLine = css('--line'), cAccent = css('--accent'), cOn = css('--on');

    ctx.font = '11px system-ui, sans-serif';
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
      ctx.fillText(timeLabel(tt, hours), X(tt), h - m.b + 7);
    }

    // typical sample spacing, to detect gaps (controller down)
    var gaps = [];
    for (var g = 1; g < pts.length; g++) { gaps.push(pts[g].t - pts[g - 1].t); }
    gaps.sort(function (a, b) { return a - b; });
    var typical = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 60000;
    var maxGap = Math.max(typical * 4, 150000);

    ctx.save();
    ctx.beginPath(); ctx.rect(m.l, m.t, pw, ph + 1); ctx.clip();

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

    // fridge-running strip along the bottom
    ctx.fillStyle = cOn; ctx.globalAlpha = 0.85;
    for (var n = 0; n < pts.length; n++) {
      if (pts[n].on) {
        var x0 = X(pts[n].t);
        var nxt = pts[n + 1];
        var x1 = nxt && nxt.t - pts[n].t <= maxGap ? X(nxt.t) : x0 + Math.max(2, pw * typical / (tMax - tMin));
        ctx.fillRect(x0, m.t + ph - 5, Math.max(1.5, x1 - x0), 5);
      }
    }
    ctx.globalAlpha = 1;

    // temperature line
    ctx.strokeStyle = cAccent; ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
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
        ctx.beginPath(); ctx.arc(hx, Y(hp.temp), 4, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      }
    }
    positionTooltip(hp);
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

  function positionTooltip(p) {
    var tip = $('tooltip');
    if (!p || !layout) { tip.hidden = true; return; }
    var when = new Date(p.t).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
    tip.innerHTML = '';
    var b = document.createElement('b');
    b.textContent = p.temp === null ? 'No reading' : fmt(p.temp, 1) + '°F';
    var l1 = document.createElement('div'); l1.textContent = when;
    var l2 = document.createElement('div');
    l2.textContent = 'Target ' + (p.target === null || p.target === undefined ? '--' : p.target + '°') +
                     ' · ' + (p.on === null ? 'unknown' : p.on ? 'cooling' : 'idle');
    tip.appendChild(b); tip.appendChild(l1); tip.appendChild(l2);
    tip.hidden = false;
    var x = layout.X(p.t);
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
    state.hover = best;
    draw();
  }

  canvas.addEventListener('pointermove', onPointer);
  canvas.addEventListener('pointerdown', onPointer);
  canvas.addEventListener('pointerleave', function () { state.hover = null; draw(); });
  window.addEventListener('resize', draw);
  if (window.ResizeObserver) { new ResizeObserver(draw).observe(canvas); }

  /* ---------- controls ---------- */

  function readInputs() {
    return {
      target_temp: parseInt($('target').value, 10),
      hysteresis: parseFloat($('hysteresis').value)
    };
  }

  function validInputs(v) {
    return !isNaN(v.target_temp) && v.target_temp >= 20 && v.target_temp <= 70 &&
           !isNaN(v.hysteresis) && v.hysteresis >= 0.5 && v.hysteresis <= 10;
  }

  function updateBandHint() {
    var v = readInputs();
    var hint = $('band-hint');
    if (!validInputs(v)) {
      hint.textContent = 'Target must be 20–70°F and swing 0.5–10°F.';
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
      input.value = btn.dataset.for === 'target' ? Math.round(next) : (Math.round(next * 2) / 2).toFixed(1);
      markDirty();
    });
  });
  $('target').addEventListener('input', markDirty);
  $('hysteresis').addEventListener('input', markDirty);

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

  $('range').addEventListener('click', function (ev) {
    var btn = ev.target.closest('button[data-hours]');
    if (!btn) { return; }
    state.hours = parseInt(btn.dataset.hours, 10);
    state.hover = null;
    this.querySelectorAll('button').forEach(function (b) {
      var on = b === btn;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    refreshHistory();
  });

  /* ---------- polling ---------- */

  setInterval(function () { if (!document.hidden) { refreshStatus(); } }, STATUS_EVERY_MS);
  setInterval(function () { if (!document.hidden) { refreshHistory(); } }, HISTORY_EVERY_MS);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) { refreshStatus(); refreshHistory(); }
  });

  refreshStatus().then(refreshHistory);
})();
