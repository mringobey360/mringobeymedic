/*
 * SmartPOS - system-wide resilient image handling.
 *
 * What it does for EVERY <img> in the system (profile photos, marketplace photos, logos, chat):
 *   1. Shows a soft shimmer on the image while it loads, so layout never looks broken.
 *   2. A slow image is NOT a failed image: we keep waiting for the same request.
 *   3. A real network error is retried with back-off (and a cache-buster). When we run out of
 *      retries a neutral placeholder is shown instead of a broken-image icon.
 *   4. While the device is offline nothing is retried or counted as a failure; the moment the
 *      connection returns (online / tab visible / page restored) every failed image reloads.
 *   5. Failed images are re-tried again later in the background (every 30 s, a few times).
 *   6. Save-Data / 2G connections get the calm "sp-lowdata" mode (no animations).
 *
 * Marketplace cards keep their own richer states from marketplace/mk-images.js; this script
 * leaves those containers alone. Add data-no-heal to any <img> that must be ignored.
 * States on the image: data-sp-img = loading | slow | loaded | error
 */
(function () {
  'use strict';
  if (window.SPImages) return;

  var MK = '.sp-card-media,.sp-cart-img,.sp-gallery .frame,.sp-sg-img,.sp-avatar,.sp-shop-logo,.mk-thumb,[data-mk-img]';
  var MAX_RETRY = 3, DELAY = [700, 1800, 4000], SLOW_MS = 7000, REVIVE_MS = 30000, MAX_REVIVE = 6;

  function svg(inner) {
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><rect width="96" height="96" fill="#f1f5f9"/>' +
      '<g fill="none" stroke="#94a3b8" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">' + inner + '</g></svg>');
  }
  var PH_IMAGE = svg('<rect x="26" y="28" width="44" height="40" rx="6"/><circle cx="40" cy="42" r="4"/><path d="m70 58-12-12L32 68"/>');
  var PH_PERSON = svg('<circle cx="48" cy="38" r="12"/><path d="M26 72c3-12 12-18 22-18s19 6 22 18"/>');

  function srcOf(img) { return img.getAttribute('src') || ''; }
  function isAvatar(img) {
    return /\/profile_photos\//.test(img.dataset.spOrig || srcOf(img)) ||
      !!(img.closest && img.closest('.spx-avatar,.profile-avatar,.swa-avatar,.chat-avatar')) ||
      /\brounded-full\b/.test(img.className || '');
  }
  function skip(img) {
    var s = srcOf(img);
    if (img.dataset.spPh === '1') return true;
    if (!s || /^(data:|blob:)/i.test(s) || img.hasAttribute('data-no-heal')) return true;
    return !!(window.MKImages && img.closest && img.closest(MK));
  }
  function ok(img) { return img.complete && img.naturalWidth > 0; }
  function setState(img, s) { if (img.getAttribute('data-sp-img') !== s) img.setAttribute('data-sp-img', s); }
  function clearTimers(img) { clearTimeout(img._spSlow); clearTimeout(img._spRetry); }

  function reload(img, base) {
    if (!img.isConnected) return;
    base = base || img.dataset.spSrc || srcOf(img);
    img.dataset.spSrc = base;
    img.dataset.spPh = '';
    setState(img, 'loading');
    try {
      var u = new URL(base, location.href);
      u.searchParams.set('_r', String(Date.now()));
      img.src = u.href;
    } catch (e) { img.src = base; }
    watch(img);
  }

  function watch(img) {
    clearTimeout(img._spSlow);
    img._spSlow = setTimeout(function () {
      if (!ok(img) && img.getAttribute('data-sp-img') === 'loading') setState(img, 'slow');
    }, SLOW_MS);
  }

  function placeholder(img) {
    clearTimers(img);
    var base = img.dataset.spSrc || srcOf(img);
    img.dataset.spOrig = base;
    img.dataset.spPh = '1';
    img.removeAttribute('srcset');
    img.src = isAvatar(img) ? PH_PERSON : PH_IMAGE;
    img.title = img.title || 'Image unavailable. It will reload when your connection improves.';
    setState(img, 'error');
    ensureReviveTimer();
  }

  function fail(img) {
    if (skip(img)) return;
    clearTimers(img);
    if (navigator.onLine === false) { setState(img, 'slow'); img.dataset.spWaitOnline = '1'; return; }
    var n = Number(img.dataset.spTry || 0);
    if (n >= MAX_RETRY) { placeholder(img); return; }
    img.dataset.spTry = String(n + 1);
    setState(img, 'loading');
    img._spRetry = setTimeout(function () { reload(img); }, DELAY[n] || 4000);
  }

  function arm(img) {
    if (skip(img)) return;
    var s = srcOf(img);
    if (img.dataset.spSeen === s && img.hasAttribute('data-sp-img')) return;
    img.dataset.spSeen = s;
    if (!/[?&]_r=/.test(s)) { img.dataset.spSrc = s; img.dataset.spTry = '0'; }
    clearTimers(img);
    if (ok(img)) { setState(img, 'loaded'); return; }
    if (img.complete && img.naturalWidth === 0) { fail(img); return; }
    setState(img, 'loading');
    watch(img);
  }

  document.addEventListener('load', function (e) {
    var t = e.target;
    if (!t || t.tagName !== 'IMG' || t.dataset.spPh === '1') return;
    if (skip(t)) return;
    clearTimers(t);
    t.dataset.spTry = '0';
    delete t.dataset.spWaitOnline;
    setState(t, 'loaded');
  }, true);

  document.addEventListener('error', function (e) {
    var t = e.target;
    if (t && t.tagName === 'IMG') fail(t);
  }, true);

  function revive(force) {
    [].forEach.call(document.querySelectorAll('img[data-sp-ph="1"], img[data-sp-wait-online="1"]'), function (img) {
      var n = Number(img.dataset.spRevive || 0);
      if (!force && n >= MAX_REVIVE) return;
      img.dataset.spRevive = String(force ? 0 : n + 1);
      img.dataset.spTry = '0';
      delete img.dataset.spWaitOnline;
      reload(img, img.dataset.spOrig || img.dataset.spSrc);
    });
  }
  var reviveTimer = null;
  function ensureReviveTimer() {
    if (reviveTimer) return;
    reviveTimer = setInterval(function () {
      if (document.hidden || navigator.onLine === false) return;
      if (!document.querySelector('img[data-sp-ph="1"]')) { clearInterval(reviveTimer); reviveTimer = null; return; }
      revive(false);
    }, REVIVE_MS);
  }

  function recheck() {
    [].forEach.call(document.querySelectorAll('img[data-sp-img="loading"],img[data-sp-img="slow"]'), function (img) {
      if (ok(img)) { clearTimers(img); setState(img, 'loaded'); }
    });
  }

  function scan(root) {
    if (!root) return;
    if (root.tagName === 'IMG') arm(root);
    if (root.querySelectorAll) [].forEach.call(root.querySelectorAll('img'), arm);
  }

  function lowData() {
    var c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    var slow = !!(c && (c.saveData || /(^|-)2g$/.test(c.effectiveType || '')));
    document.documentElement.classList.toggle('sp-lowdata', slow);
  }

  function start() {
    lowData();
    var c = navigator.connection;
    if (c && c.addEventListener) c.addEventListener('change', lowData);
    scan(document);
    if ('MutationObserver' in window) {
      new MutationObserver(function (list) {
        list.forEach(function (m) {
          if (m.type === 'attributes') { if (m.target.tagName === 'IMG') arm(m.target); return; }
          [].forEach.call(m.addedNodes, function (n) { if (n.nodeType === 1) scan(n); });
        });
      }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
    }
    window.addEventListener('online', function () { revive(true); });
    window.addEventListener('pageshow', function () { scan(document); recheck(); });
    document.addEventListener('visibilitychange', function () { if (!document.hidden) { recheck(); revive(false); } });
    window.addEventListener('sp:content-updated', function () { setTimeout(function () { scan(document); }, 300); });
    window.addEventListener('pharmacy:content-loaded', function () { setTimeout(function () { scan(document); }, 300); });
    setInterval(recheck, 4000);
  }

  try {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
    window.SPImages = { scan: scan, revive: function () { revive(true); } };
  } catch (err) { /* images stay visible without any of the extras */ }
})();
