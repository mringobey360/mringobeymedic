/* Movie trailers: autoplay with sound ON by default (falls back to muted until the first tap if the browser blocks it), polite (one at a time), data-saver aware and Safari/iPhone safe. */
(function () {
  if (window.__mvInit) return;
  window.__mvInit = true;

  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var conn = navigator.connection || {};
  var slow = !!conn.saveData;            // only the browser's explicit Data-saver switch stops autoplay
  var autoplay = !slow;
  var MAX_PLAYING = 1;                   // sound is on by default, so only the most visible trailer plays
  var soundOn = true;                    // global preference: sound ON by default, user can mute
  try { if (localStorage.getItem('mvSound') === '0') soundOn = false; } catch (e) {}
  var gestured = !!(navigator.userActivation && navigator.userActivation.hasBeenActive);
  var ratios = new Map();   // media wrapper -> visible ratio
  
  function videoOf(wrap) { return wrap.querySelector('video.mv-video'); }

  function setPlaying(wrap, on) {
    var host = wrap.closest('.mv-card, .mv-stage') || wrap;
    wrap.classList.toggle('is-playing', on);
    host.classList.toggle('is-playing', on);
  }

  function syncButtons() {
    document.querySelectorAll('.mv-sound').forEach(function (btn) {
      var host = btn.closest('.mv-card, .mv-stage');
      var v = host && host.querySelector('video.mv-video');
      var audible = !!v && !v.muted && soundOn;
      btn.classList.toggle('is-on', audible);
      btn.setAttribute('aria-pressed', audible ? 'true' : 'false');
      btn.setAttribute('aria-label', audible ? 'Mute trailer sound' : 'Turn trailer sound on');
    });
  }

  function start(wrap) {
    var v = videoOf(wrap);
    if (!v) return;
    if (!v.getAttribute('src') && v.dataset.src) { v.preload = 'auto'; v.src = v.dataset.src; }
    v.__ap = true;
    v.muted = !soundOn;
    var p = v.play();
    if (p && p.catch) p.catch(function () {
      // Browser blocks sound before the first tap/click: play muted, unmute on the first gesture.
      if (!v.muted) {
        v.muted = true; v.__ap = true;
        var q = v.play();
        if (q && q.catch) q.catch(function () { setPlaying(wrap, false); });
        syncButtons();
      } else setPlaying(wrap, false);
    });
  }

  function stop(wrap) {
    var v = videoOf(wrap);
    if (!v) return;
    v.__auto = true;
    v.pause();
  }

  var playing = new Set();

  function pick() {
    var cand = [];
    ratios.forEach(function (r, wrap) {
      var v = videoOf(wrap);
      if (!v || v.dataset.userPaused === '1' || v.dataset.failed === '1') return;
      if (r >= 0.4) cand.push([r, wrap]);
    });
    cand.sort(function (a, b) { return b[0] - a[0]; });
    var want = new Set(cand.slice(0, MAX_PLAYING).map(function (c) { return c[1]; }));
    playing.forEach(function (w) { if (!want.has(w)) { stop(w); playing.delete(w); } });
    if (!autoplay || document.hidden) return;
    want.forEach(function (w) { if (!playing.has(w)) { playing.add(w); start(w); } });
  }

  function wire(wrap) {
    var v = videoOf(wrap);
    if (!v || wrap.__mv) return;
    wrap.__mv = true;
    v.addEventListener('playing', function () { setPlaying(wrap, true); });
    v.addEventListener('pause', function () {
      setPlaying(wrap, false);
      if (!v.__auto) v.dataset.userPaused = '1';
      v.__auto = false;
    });
    var bar = (wrap.closest('.mv-card') || wrap).querySelector('.mv-progress');
    if (bar) v.addEventListener('timeupdate', function () {
      if (v.duration) bar.style.setProperty('--p', Math.min(1, v.currentTime / v.duration).toFixed(3));
    });
    v.addEventListener('play', function () {
      if (!v.__ap) delete v.dataset.userPaused;
      v.__ap = false;
    });
    v.addEventListener('error', function () {
      v.dataset.failed = '1';
      setPlaying(wrap, false);
      (wrap.closest('.mv-card, .mv-stage') || wrap).classList.add('mv-nosrc');
      ratios.delete(wrap);
      playing.delete(wrap); pick();
    });
    if (wrap.tagName === 'A') wrap.addEventListener('click', function (e) {
      // Opening the movie page from a card: stop the trailer (and its sound) right away.
      if (e.defaultPrevented || e.target.closest('.mv-sound')) return;
      v.__auto = true; v.pause(); playing.delete(wrap);
    });
    var snd = (wrap.closest('.mv-card, .mv-stage') || wrap).querySelector('.mv-sound');
    v.addEventListener('volumechange', syncButtons);
    if (snd) snd.addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation();
      soundOn = v.muted;                       // muted now -> user wants sound; audible now -> mute
      try { localStorage.setItem('mvSound', soundOn ? '1' : '0'); } catch (err) {}
      document.querySelectorAll('video.mv-video').forEach(function (x) { if (!x.paused) x.muted = !soundOn; });
      v.muted = !soundOn;
      if (v.paused) { delete v.dataset.userPaused; start(wrap); }
      syncButtons();
    });
    if (!autoplay) {
      // Data-saver / reduced motion: show a play badge; tapping the card still opens the movie page.
      var host = wrap.closest('.mv-card, .mv-stage') || wrap;
      host.classList.add('mv-manual');
    }
  }

  var io = 'IntersectionObserver' in window ? new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (en.isIntersecting) ratios.set(en.target, en.intersectionRatio); else ratios.delete(en.target);
    });
    pick();
  }, { threshold: [0, 0.25, 0.5, 0.6, 0.75, 1], rootMargin: '0px 0px 120px 0px' }) : null;

  function scan(root) {
    (root || document).querySelectorAll('[data-mv-media]').forEach(function (wrap) {
      if (wrap.__mvSeen) return;
      wrap.__mvSeen = true;
      wire(wrap);
      if (io) io.observe(wrap);
    });
  }

  function unlock(e) {
    if (gestured) return;
    if (e && e.target && e.target.closest && e.target.closest('.mv-sound')) return;   // the sound button handles itself
    gestured = true;
    ['pointerdown', 'touchstart', 'keydown', 'click'].forEach(function (ev) { document.removeEventListener(ev, unlock); });
    if (soundOn) document.querySelectorAll('video.mv-video').forEach(function (x) { if (!x.paused) x.muted = false; });
    syncButtons();
  }
  if (!gestured) ['pointerdown', 'touchstart', 'keydown', 'click'].forEach(function (ev) {
    document.addEventListener(ev, unlock, { passive: true });
  });

  window.addEventListener('pageshow', function (e) { if (e.persisted) pick(); });

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { playing.forEach(stop); playing.clear(); }
    else pick();
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { scan(); });
  else scan();
  window.MKMovies = { scan: scan };
})();
