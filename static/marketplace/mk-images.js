/*
 * SmartPOS Marketplace - resilient image loading (V72).
 *
 * Design rules (the V71 version broke on slow mobile networks, so these are deliberate):
 *   1. An image is NEVER hidden by this script. The container shows a shimmer background while the
 *      image is on its way and the browser paints the photo over it as soon as bytes arrive.
 *      If this script fails, or an event is missed, the photo is still visible.
 *   2. A slow image is not a failed image. After SLOW_MS we only show a "Slow connection" hint and keep
 *      waiting for the SAME request - we never restart the download or add a cache-buster for slowness.
 *   3. Only a real network error (the "error" event) triggers a retry, with back-off and a cache-buster.
 *      After the retries a calm "Image unavailable" tile with a Retry button is shown. If the original
 *      request still completes later, the tile is removed and the photo appears.
 *   4. A frozen background tab / bfcache restore / coming back online re-checks every image.
 *
 * States on the container ([data-img-state]): loading | slow | loaded | error
 */
(function () {
  'use strict';
  if (window.MKImages) return;

  var SELECTOR = '.sp-card-media, .sp-cart-img, .sp-gallery .frame, .sp-sg-img, .sp-avatar, .sp-shop-logo, .mk-thumb, [data-mk-img]';
  var MAX_RETRY = 3, DELAY = [700, 1800, 3500], SLOW_MS = 7000, GIVEUP_MS = 45000;
  var ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/></svg>';

  function skip(img) {
    var s = img.getAttribute('src') || '';
    return !s || /^(data:|blob:)/i.test(s) || img.hasAttribute('data-no-heal');
  }
  function box(img) { return img.closest ? img.closest(SELECTOR) : null; }
  function ok(img) { return img.complete && img.naturalWidth > 0; }
  function setState(c, s) { if (c && c.getAttribute('data-img-state') !== s) c.setAttribute('data-img-state', s); }
  function clearFallback(c) {
    var f = c && c.querySelector(':scope > .mk-img-fallback');
    if (f) f.remove();
  }
  function clearTimers(img) {
    clearTimeout(img._mkSlow); clearTimeout(img._mkGiveUp); clearTimeout(img._mkRetry);
  }

  function showFallback(img, c) {
    setState(c, 'error');
    if (c.querySelector(':scope > .mk-img-fallback')) return;
    var small = c.clientWidth && c.clientWidth < 96;
    var f = document.createElement('div');
    f.className = 'mk-img-fallback' + (small ? ' mk-img-fallback--sm' : '');
    f.setAttribute('role', 'img');
    f.setAttribute('aria-label', 'Image unavailable');
    f.innerHTML = '<span class="mk-img-ic">' + ICON + '</span>' +
      (small ? '' : '<span class="mk-img-msg">Image unavailable</span><button type="button" class="mk-img-retry">Retry</button>');
    c.appendChild(f);
  }

  /* start (or restart after an error) the timers that watch a pending image */
  function watch(img, c) {
    clearTimers(img);
    img._mkSlow = setTimeout(function () {
      if (!ok(img) && c.getAttribute('data-img-state') === 'loading') setState(c, 'slow');
    }, SLOW_MS);
    img._mkGiveUp = setTimeout(function () {
      /* give up showing the skeleton, but do NOT touch the request: a late load still wins */
      if (!ok(img)) showFallback(img, c);
    }, GIVEUP_MS);
  }

  function reload(img) {
    var c = box(img);
    if (!img.isConnected || skip(img)) return;
    var base = img.dataset.mkSrc || img.getAttribute('src');
    img.dataset.mkSrc = base;
    clearFallback(c);
    setState(c, 'loading');
    try {
      var u = new URL(base, location.href);
      u.searchParams.set('_r', String(Date.now()));
      img.src = u.href;
    } catch (e) { img.src = base; }
  }

  function fail(img) {
    var c = box(img);
    if (!c || skip(img)) return;
    clearTimers(img);
    var n = Number(img.dataset.mkTry || 0);
    if (n >= MAX_RETRY) { showFallback(img, c); return; }
    img.dataset.mkTry = String(n + 1);
    setState(c, 'loading');
    img._mkRetry = setTimeout(function () { reload(img); }, DELAY[n] || 3500);
  }

  function arm(img) {
    var c = box(img);
    if (!c || skip(img)) return;
    var src = img.getAttribute('src');
    if (img.dataset.mkArmed === '1' && img.dataset.mkSeen === src) return;
    img.dataset.mkArmed = '1';
    img.dataset.mkSeen = src;
    if (!/[?&]_r=/.test(src)) { img.dataset.mkSrc = src; img.dataset.mkTry = '0'; }
    if (img.dataset.mkTry === undefined) img.dataset.mkTry = '0';
    clearTimers(img);
    if (ok(img)) { clearFallback(c); setState(c, 'loaded'); return; }
    if (img.complete && img.naturalWidth === 0) { fail(img); return; }
    clearFallback(c);
    setState(c, 'loading');
    watch(img, c);
  }

  /* load / error do not bubble: listen in the capture phase on the document */
  document.addEventListener('load', function (e) {
    var t = e.target;
    if (!t || t.tagName !== 'IMG') return;
    var c = box(t);
    if (!c) return;
    clearTimers(t);
    t.dataset.mkTry = '0';
    var wasPending = c.getAttribute('data-img-state') !== 'loaded';
    clearFallback(c);
    if (wasPending) c.setAttribute('data-img-fresh', '');
    setState(c, 'loaded');
  }, true);

  document.addEventListener('error', function (e) {
    var t = e.target;
    if (t && t.tagName === 'IMG') fail(t);
  }, true);

  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.mk-img-retry');
    if (!b) return;
    e.preventDefault(); e.stopPropagation();
    var c = b.closest(SELECTOR), img = c && c.querySelector(':scope > img, img');
    if (img) { img.dataset.mkTry = '0'; reload(img); watch(img, c); }
  }, true);

  /* a safety net for anything the events missed: fix containers whose image is actually ready */
  function recheck() {
    [].forEach.call(document.querySelectorAll('[data-img-state]:not([data-img-state="loaded"])'), function (c) {
      var img = c.querySelector('img');
      if (img && ok(img)) { clearTimers(img); clearFallback(c); setState(c, 'loaded'); }
    });
  }

  function scan(root) {
    var imgs = (root.querySelectorAll ? root.querySelectorAll('img') : []);
    [].forEach.call(imgs, arm);
    if (root.tagName === 'IMG') arm(root);
  }

  function start() {
    scan(document);
    if ('MutationObserver' in window) {
      new MutationObserver(function (list) {
        list.forEach(function (m) {
          if (m.type === 'attributes') { if (m.target.tagName === 'IMG') arm(m.target); return; }
          [].forEach.call(m.addedNodes, function (n) { if (n.nodeType === 1) scan(n); });
        });
      }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
    }
    window.addEventListener('online', function () {
      [].forEach.call(document.querySelectorAll('[data-img-state="error"] img, [data-img-state="slow"] img'), function (i) {
        if (!ok(i)) { i.dataset.mkTry = '0'; reload(i); var c = box(i); if (c) watch(i, c); }
      });
    });
    window.addEventListener('pageshow', function () { scan(document); recheck(); });
    document.addEventListener('visibilitychange', function () { if (!document.hidden) recheck(); });
    setInterval(recheck, 4000);
  }

  try {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
    window.MKImages = { scan: scan, recheck: recheck };
  } catch (err) {
    /* fail safe: plain images are visible anyway; just drop the skeleton styling */
    document.documentElement.classList.remove('mk-js');
  }
})();
