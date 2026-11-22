/* Direct-to-bucket movie uploader: 8 MB parts, 3 in parallel, resumable, with a live progress card. */
(function () {
  'use strict';
  var root = document.getElementById('du-root');
  if (!root) return;
  var input = document.getElementById(root.dataset.input);
  var tokenEl = document.getElementById(root.dataset.token);
  var form = input && input.closest('form');
  if (!input || !tokenEl || !form || !window.XMLHttpRequest || !window.Blob || !Blob.prototype.slice) return;

  var URLS = { init: root.dataset.init, sign: root.dataset.sign, complete: root.dataset.complete, abort: root.dataset.abort };
  var PARALLEL = 3, MAX_TRIES = 5, VIDEO = /\.(mp4|m4v|mov|webm|mkv|avi|3gp|mpeg|mpg)$/i;
  var s = null; // current upload state

  function csrf() {
    var el = form.querySelector('input[name=csrfmiddlewaretoken]');
    return el ? el.value : '';
  }
  function fmtBytes(n) {
    if (n >= 1073741824) return (n / 1073741824).toFixed(2) + ' GB';
    if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
    return Math.max(1, Math.round(n / 1024)) + ' KB';
  }
  function fmtTime(sec) {
    if (!isFinite(sec) || sec < 0) return '–';
    sec = Math.round(sec);
    if (sec < 60) return sec + 's';
    if (sec < 3600) return Math.floor(sec / 60) + 'm ' + (sec % 60) + 's';
    return Math.floor(sec / 3600) + 'h ' + Math.floor((sec % 3600) / 60) + 'm';
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  /* ---------- UI ---------- */
  var card = el('div', 'du-card'); card.hidden = true; card.setAttribute('aria-live', 'polite');
  var head = el('div', 'du-head');
  var nameEl = el('div', 'du-name'), badge = el('span', 'du-badge');
  head.appendChild(nameEl); head.appendChild(badge);
  var bar = el('div', 'du-bar'); bar.setAttribute('role', 'progressbar');
  bar.setAttribute('aria-valuemin', '0'); bar.setAttribute('aria-valuemax', '100');
  var fill = el('div', 'du-fill'); bar.appendChild(fill);
  var stats = el('div', 'du-stats');
  var pctEl = el('b'), ofEl = el('span'), speedEl = el('span'), etaEl = el('span');
  [pctEl, ofEl, speedEl, etaEl].forEach(function (x) { stats.appendChild(x); });
  var msg = el('div', 'du-msg'); msg.setAttribute('role', 'status');
  var actions = el('div', 'du-actions');
  var btnPause = el('button', 'du-btn', 'Pause'), btnRetry = el('button', 'du-btn du-primary', 'Retry'),
      btnCancel = el('button', 'du-btn du-ghost', 'Cancel');
  [btnPause, btnRetry, btnCancel].forEach(function (b) { b.type = 'button'; actions.appendChild(b); });
  [head, bar, stats, msg, actions].forEach(function (x) { card.appendChild(x); });
  root.appendChild(card);

  var STATES = {
    preparing: ['Preparing', 'is-busy'], uploading: ['Uploading', 'is-busy'], paused: ['Paused', 'is-warn'],
    offline: ['Waiting for network', 'is-warn'], finishing: ['Finishing', 'is-busy'],
    done: ['Uploaded', 'is-ok'], failed: ['Failed', 'is-bad']
  };
  function render(state, text) {
    if (!s) { card.hidden = true; return; }
    s.state = state;
    card.hidden = false;
    card.className = 'du-card ' + STATES[state][1];
    badge.textContent = STATES[state][0];
    nameEl.textContent = s.file.name + ' \u00b7 ' + fmtBytes(s.file.size);
    var sent = Math.min(s.sent(), s.file.size), pct = Math.floor(sent * 100 / s.file.size);
    if (state === 'done') { sent = s.file.size; pct = 100; } else if (pct > 99) { pct = 99; }   // 100% only once the server has verified the file
    fill.style.width = pct + '%';
    bar.setAttribute('aria-valuenow', String(pct));
    pctEl.textContent = pct + '%';
    ofEl.textContent = fmtBytes(sent) + ' of ' + fmtBytes(s.file.size);
    var speed = s.speed();
    speedEl.textContent = state === 'uploading' && speed > 0 ? fmtBytes(speed) + '/s' : '';
    etaEl.textContent = state === 'uploading' && speed > 0 ? fmtTime((s.file.size - sent) / speed) + ' left' : '';
    if (text != null) msg.textContent = text;
    btnPause.hidden = !(state === 'uploading' || state === 'offline');
    btnRetry.hidden = !(state === 'paused' || state === 'failed');
    btnRetry.textContent = state === 'paused' ? 'Resume' : 'Retry';
    btnCancel.hidden = state === 'done' ? false : (state === 'preparing' || state === 'finishing');
    btnCancel.textContent = state === 'done' ? 'Remove' : 'Cancel';
  }

  /* ---------- network helpers ---------- */
  function api(url, body) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, message: 'The server sent an unexpected reply (' + r.status + ').', code: 'http' }; })
        .then(function (j) { if (!j.ok) { var e = new Error(j.message || 'Request failed'); e.code = j.code; e.api = true; throw e; } return j; });
    });
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function putPart(url, blob, onProgress, hold) {
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest();
      hold.xhr = x;
      x.open('PUT', url);
      x.upload.onprogress = function (e) { if (e.lengthComputable) onProgress(e.loaded); };
      x.onload = function () {
        if (x.status >= 200 && x.status < 300) return resolve();
        var err = new Error('Storage answered ' + x.status); err.status = x.status; reject(err);
      };
      x.onerror = function () { var err = new Error('network'); err.network = true; reject(err); };
      x.ontimeout = x.onerror;
      x.onabort = function () { var err = new Error('aborted'); err.aborted = true; reject(err); };
      x.timeout = 10 * 60 * 1000;
      x.send(blob);
    });
  }

  /* ---------- upload engine ---------- */
  function start(file) {
    if (s) abandon(false);
    var st = {
      file: file, id: null, partSize: 0, total: 0, done: {}, inflight: {}, urls: {}, tokenSet: false,
      stop: null, samples: [], runId: 0, signBusy: null, cancelled: false
    };
    st.sent = function () {
      var n = 0, k;
      for (k in st.done) n += st.done[k];
      for (k in st.inflight) n += st.inflight[k];
      return n;
    };
    st.speed = function () {
      var now = Date.now(), sm = st.samples;
      while (sm.length && now - sm[0].t > 8000) sm.shift();
      if (sm.length < 2) return 0;
      var dt = (sm[sm.length - 1].t - sm[0].t) / 1000;
      return dt > 0.5 ? (sm[sm.length - 1].b - sm[0].b) / dt : 0;
    };
    s = st;
    tokenEl.value = '';
    render('preparing', 'Checking the file and preparing a secure upload…');
    api(URLS.init, { filename: file.name, size: file.size })
      .then(function (j) {
        if (s !== st) return;
        st.id = j.id; st.partSize = j.part_size; st.total = j.total_parts;
        (j.done || []).forEach(function (p) { st.done[p.n] = p.size; });
        run(st, j.resumed ? 'Resuming where it stopped (' + (j.done || []).length + ' of ' + j.total_parts + ' parts already uploaded).' : '');
      })
      .catch(function (e) { if (s === st) fail(st, explain(e)); });
  }

  function explain(e) {
    if (e && e.api) return e.message;
    if (e && e.network) return 'Your connection dropped. Check your internet and press Retry; the upload continues from where it stopped.';
    if (e && e.status === 403) return 'The storage service refused the upload (403). The link may have expired or the bucket permissions (CORS) are not set. Press Retry; if it repeats, contact support.';
    if (e && e.status >= 500) return 'The storage service is busy right now. Wait a moment and press Retry.';
    return (e && e.message) || 'Something went wrong. Press Retry; the upload continues from where it stopped.';
  }

  function fail(st, text) {
    st.runId++; abortInflight(st);
    render('failed', text);
  }
  function abortInflight(st) {
    Object.keys(st.holds || {}).forEach(function (k) { try { st.holds[k].xhr && st.holds[k].xhr.abort(); } catch (e) {} });
    st.holds = {}; st.inflight = {};
  }

  function pendingParts(st) {
    var out = [];
    for (var n = 1; n <= st.total; n++) if (!(n in st.done)) out.push(n);
    return out;
  }
  function partBounds(st, n) {
    var a = (n - 1) * st.partSize;
    return [a, Math.min(st.file.size, a + st.partSize)];
  }
  function signBatch(st, nums) {
    var need = nums.filter(function (n) { return !st.urls[n]; }).slice(0, 40);
    if (!need.length) return Promise.resolve();
    return api(URLS.sign, { id: st.id, parts: need }).then(function (j) {
      Object.keys(j.urls).forEach(function (k) { st.urls[k] = j.urls[k]; });
    });
  }

  function run(st, note) {
    var runId = ++st.runId;
    st.holds = {}; st.inflight = {};
    st.samples = [{ t: Date.now(), b: st.sent() }];
    var queue = pendingParts(st), failure = null, active = 0;
    render('uploading', note || '');
    if (!queue.length) return finish(st);
    var tick = setInterval(function () {
      if (st.runId !== runId) return clearInterval(tick);
      st.samples.push({ t: Date.now(), b: st.sent() });
      if (st.state === 'uploading') render('uploading');
    }, 500);

    function alive() { return st.runId === runId && s === st; }
    function next() {
      if (!alive()) return;
      if (failure) { if (!active) { clearInterval(tick); fail(st, explain(failure)); } return; }
      if (!queue.length) { if (!active) { clearInterval(tick); finish(st); } return; }
      while (active < PARALLEL && queue.length) { active++; sendPart(queue.shift()); }
    }
    function sendPart(n) {
      var b = partBounds(st, n), blob = st.file.slice(b[0], b[1]), hold = {};
      st.holds[n] = hold;
      var attempt = 0;
      function once() {
        if (!alive()) return;
        signBatch(st, [n].concat(queue.slice(0, 20))).then(function () {
          if (!alive()) return;
          return putPart(st.urls[n], blob, function (loaded) { st.inflight[n] = loaded; }, hold);
        }).then(function () {
          if (!alive()) return;
          delete st.inflight[n]; st.done[n] = b[1] - b[0]; delete st.holds[n];
          active--; next();
        }).catch(function (e) {
          if (!alive() || (e && e.aborted)) return;
          delete st.inflight[n];
          if (e && e.status === 403) delete st.urls[n];            // expired signature: sign again
          if (e && e.api && e.code !== 'storage') { failure = failure || e; active--; return next(); }
          if (!navigator.onLine) { active--; queue.unshift(n); goOffline(st, runId, tick); return; }
          attempt++;
          if (attempt >= MAX_TRIES) { failure = failure || e; active--; return next(); }
          render('uploading', 'Network is unstable. Retrying part ' + n + ' (attempt ' + (attempt + 1) + ' of ' + MAX_TRIES + ')…');
          setTimeout(once, Math.min(15000, 800 * Math.pow(2, attempt)));
        });
      }
      once();
    }
    next();
  }

  function goOffline(st, runId, tick) {
    if (st.runId !== runId) return;
    clearInterval(tick); st.runId++; abortInflight(st);
    render('offline', 'You are offline. The upload will continue automatically when your connection returns.');
  }
  window.addEventListener('online', function () {
    if (s && s.state === 'offline') run(s, 'Connection is back. Continuing…');
  });
  window.addEventListener('offline', function () {
    if (s && s.state === 'uploading') { s.runId++; abortInflight(s); render('offline', 'You are offline. The upload will continue automatically when your connection returns.'); }
  });

  function finish(st) {
    render('finishing', 'All parts sent. Verifying the movie on the server…');
    api(URLS.complete, { id: st.id }).then(function (j) {
      if (s !== st) return;
      tokenEl.value = j.token; st.tokenSet = true;
      try { input.value = ''; } catch (e) {}          // the bytes are already in the bucket; do not send them again with the form
      render('done', 'Upload complete and verified. Fill in the rest and press Save.');
    }).catch(function (e) {
      if (s !== st) return;
      if (e.api && (e.code === 'invalid_file' || e.code === 'mismatch' || e.code === 'inactive' || e.code === 'expired')) {
        st.id = null; render('failed', e.message + ' Choose the file again.'); st.fatal = true; return;
      }
      fail(st, explain(e));
    });
  }

  function abandon(remote) {
    if (!s) return;
    var st = s; st.runId++; abortInflight(st); st.cancelled = true; s = null;
    tokenEl.value = '';
    if (remote && st.id && !st.tokenSet) api(URLS.abort, { id: st.id }).catch(function () {});
    card.hidden = true; msg.textContent = '';
  }

  /* ---------- events ---------- */
  input.addEventListener('change', function () {
    var f = input.files && input.files[0];
    if (!f) return;
    var mv = form.querySelector('[name="is_movie"]');
    if (mv && !mv.checked) return;                   // only movies use the direct route; other files post with the form
    if (!VIDEO.test(f.name)) {
      s = { file: f, sent: function () { return 0; }, speed: function () { return 0; } };
      render('failed', 'A movie must be a video file (MP4, MOV, MKV, WebM, AVI, 3GP or MPEG).');
      btnRetry.hidden = true; s = null; try { input.value = ''; } catch (e) {} return;
    }
    start(f);
  });
  btnPause.addEventListener('click', function () {
    if (!s) return;
    s.runId++; abortInflight(s); render('paused', 'Paused. Press Resume to continue from where it stopped.');
  });
  btnRetry.addEventListener('click', function () {
    if (!s) return;
    if (s.fatal || !s.id) { var f = s.file; start(f); return; }
    run(s, 'Resuming…');
  });
  btnCancel.addEventListener('click', function () {
    if (!s) return;
    var done = s.state === 'done';
    if (!done && !window.confirm('Cancel this upload? The part already sent will be discarded.')) return;
    if (done) { var id = s.id; tokenEl.value = ''; s.tokenSet = false; }
    abandon(true);
    try { input.value = ''; } catch (e) {}
  });

  form.addEventListener('submit', function (ev) {
    if (!s) return;
    if (s.state !== 'done') {
      ev.preventDefault();
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      msg.textContent = s.state === 'failed' || s.state === 'paused'
        ? 'The movie is not fully uploaded yet. Press ' + btnRetry.textContent + ' first, then save.'
        : 'Please wait: the movie is still uploading. You can save as soon as it shows "Uploaded".';
    }
  });
  window.addEventListener('beforeunload', function (e) {
    if (s && s.state !== 'done' && s.state !== 'failed') { e.preventDefault(); e.returnValue = ''; return ''; }
  });
})();
