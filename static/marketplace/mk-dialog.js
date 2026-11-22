/*
 * SmartPOS Marketplace — professional dialogs.
 * Replaces the browser's native alert()/confirm() with accessible in-page modals.
 *
 * Declarative use (no JavaScript needed in templates):
 *   <form method="post" data-mk-confirm="Delete this product?" data-mk-detail="This cannot be undone."
 *         data-mk-accept="Yes, delete" data-mk-tone="danger"> ... </form>
 *   <button name="action" value="cancelled" data-mk-confirm="Cancel this order?" ...>
 *   <a href="/x/" data-mk-confirm="Leave this page?">
 *
 * Programmatic use:
 *   MKDialog.confirm({title, message, confirmText, cancelText, tone}).then(function (ok) { ... });
 *   MKDialog.alert({title, message, tone});
 */
(function () {
  'use strict';
  if (window.MKDialog) return;

  var css = '' +
    '.mkd-backdrop{position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(15,23,42,.55);-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px);opacity:0;transition:opacity .18s ease}' +
    '.mkd-backdrop.mkd-in{opacity:1}' +
    '.mkd-card{width:100%;max-width:440px;background:#fff;color:#0f172a;border-radius:18px;box-shadow:0 24px 70px rgba(15,23,42,.35);padding:26px 24px 20px;transform:translateY(14px) scale(.98);transition:transform .2s cubic-bezier(.2,.8,.2,1);font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}' +
    '.mkd-in .mkd-card{transform:none}' +
    '.mkd-icon{width:52px;height:52px;border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 14px;background:#ecfdf5;color:#047857}' +
    '.mkd-icon svg{width:26px;height:26px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}' +
    '.mkd-danger .mkd-icon{background:#fef2f2;color:#b91c1c}' +
    '.mkd-warn .mkd-icon{background:#fffbeb;color:#b45309}' +
    '.mkd-info .mkd-icon{background:#eff6ff;color:#1d4ed8}' +
    '.mkd-title{margin:0;text-align:center;font-size:1.1rem;font-weight:800;letter-spacing:-.01em}' +
    '.mkd-msg{margin:8px 0 0;text-align:center;font-size:.9rem;line-height:1.55;color:#475569;white-space:pre-line}' +
    '.mkd-actions{display:flex;gap:10px;margin-top:22px}' +
    '.mkd-actions button{flex:1;min-height:44px;padding:10px 14px;border-radius:12px;font:inherit;font-size:.9rem;font-weight:700;cursor:pointer;border:1px solid transparent;transition:filter .15s,background .15s}' +
    '.mkd-cancel{background:#fff;color:#334155;border-color:#cbd5e1!important}' +
    '.mkd-cancel:hover{background:#f8fafc}' +
    '.mkd-ok{background:#0f766e;color:#fff}' +
    '.mkd-ok:hover{filter:brightness(1.08)}' +
    '.mkd-danger .mkd-ok{background:#b91c1c}' +
    '.mkd-warn .mkd-ok{background:#b45309}' +
    '.mkd-actions button:focus-visible{outline:3px solid rgba(15,118,110,.45);outline-offset:2px}' +
    '.mkd-actions button[disabled]{opacity:.7;cursor:progress}' +
    '@media (max-width:480px){.mkd-backdrop{align-items:flex-end;padding:0}.mkd-card{max-width:none;border-radius:20px 20px 0 0;padding-bottom:max(20px,env(safe-area-inset-bottom))}.mkd-actions{flex-direction:column-reverse}}' +
    '@media (prefers-reduced-motion:reduce){.mkd-backdrop,.mkd-card{transition:none}}';

  var style = document.createElement('style');
  style.setAttribute('data-mk-dialog', '');
  style.textContent = css;
  (document.head || document.documentElement).appendChild(style);

  var ICONS = {
    danger: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/>',
    warn: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    success: '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 3 3 5-6"/>'
  };

  var active = null;

  function build(opts, withCancel) {
    var tone = opts.tone || 'success';
    var backdrop = document.createElement('div');
    backdrop.className = 'mkd-backdrop mkd-' + tone;
    var card = document.createElement('div');
    card.className = 'mkd-card';
    card.setAttribute('role', withCancel ? 'alertdialog' : 'dialog');
    card.setAttribute('aria-modal', 'true');
    var titleId = 'mkd-t-' + Date.now();
    card.setAttribute('aria-labelledby', titleId);

    var icon = document.createElement('div');
    icon.className = 'mkd-icon';
    icon.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[tone] || ICONS.info) + '</svg>';

    var h = document.createElement('h2');
    h.className = 'mkd-title';
    h.id = titleId;
    h.textContent = opts.title || (withCancel ? 'Please confirm' : 'Notice');

    card.appendChild(icon);
    card.appendChild(h);
    if (opts.message) {
      var p = document.createElement('p');
      p.className = 'mkd-msg';
      p.textContent = opts.message;
      card.appendChild(p);
    }
    var actions = document.createElement('div');
    actions.className = 'mkd-actions';
    var cancel = null;
    if (withCancel) {
      cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'mkd-cancel';
      cancel.textContent = opts.cancelText || 'Cancel';
      actions.appendChild(cancel);
    }
    var ok = document.createElement('button');
    ok.type = 'button';
    ok.className = 'mkd-ok';
    ok.textContent = opts.confirmText || (withCancel ? 'Confirm' : 'OK');
    actions.appendChild(ok);
    card.appendChild(actions);
    backdrop.appendChild(card);
    return { backdrop: backdrop, card: card, ok: ok, cancel: cancel };
  }

  function open(opts, withCancel) {
    return new Promise(function (resolve) {
      if (active) active.close(false);
      var parts = build(opts, withCancel);
      var previous = document.activeElement;
      var closed = false;

      function close(result) {
        if (closed) return;
        closed = true;
        document.removeEventListener('keydown', onKey, true);
        parts.backdrop.classList.remove('mkd-in');
        setTimeout(function () {
          if (parts.backdrop.parentNode) parts.backdrop.parentNode.removeChild(parts.backdrop);
          document.documentElement.style.overflow = parts.prevOverflow || '';
          if (previous && previous.focus) { try { previous.focus(); } catch (e) {} }
        }, 180);
        active = null;
        resolve(result);
      }

      function onKey(e) {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(false); return; }
        if (e.key === 'Tab') {
          var f = parts.card.querySelectorAll('button');
          if (!f.length) return;
          var first = f[0], last = f[f.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
      }

      parts.ok.addEventListener('click', function () { close(true); });
      if (parts.cancel) parts.cancel.addEventListener('click', function () { close(false); });
      parts.backdrop.addEventListener('mousedown', function (e) { if (e.target === parts.backdrop) close(false); });
      document.addEventListener('keydown', onKey, true);

      parts.prevOverflow = document.documentElement.style.overflow;
      document.documentElement.style.overflow = 'hidden';
      document.body.appendChild(parts.backdrop);
      requestAnimationFrame(function () {
        parts.backdrop.classList.add('mkd-in');
        // Safer default focus for destructive actions: the Cancel button.
        (opts.tone === 'danger' && parts.cancel ? parts.cancel : parts.ok).focus();
      });
      active = { close: close };
    });
  }

  var API = {
    confirm: function (opts) { return open(typeof opts === 'string' ? { message: opts } : (opts || {}), true); },
    alert: function (opts) { return open(typeof opts === 'string' ? { message: opts, tone: 'info' } : (opts || {}), false); }
  };
  window.MKDialog = API;

  // Never show the browser's native alert box inside the marketplace.
  window.alert = function (msg) { API.alert({ title: 'Notice', message: String(msg == null ? '' : msg), tone: 'info' }); };

  /* ---- declarative bindings ------------------------------------------------ */
  function optsFrom(el) {
    var d = el.dataset;
    return {
      title: d.mkConfirm,
      message: d.mkDetail || '',
      confirmText: d.mkAccept || 'Yes, continue',
      cancelText: d.mkCancel || 'Keep it',
      tone: d.mkTone || 'danger'
    };
  }

  document.addEventListener('click', function (e) {
    var el = e.target.closest && e.target.closest('[data-mk-confirm]');
    if (!el || el.tagName === 'FORM') return;
    if (el.dataset.mkApproved === '1') { delete el.dataset.mkApproved; return; }
    e.preventDefault();
    e.stopPropagation();
    API.confirm(optsFrom(el)).then(function (ok) {
      if (!ok) return;
      var form = el.form || (el.closest && el.closest('form'));
      if (el.tagName === 'A') { window.location.href = el.href; }
      else if (form && form.requestSubmit) { form.requestSubmit(el); }
      else if (form) { form.submit(); }
      else { el.dataset.mkApproved = '1'; el.click(); }
    });
  }, true);

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form || !form.hasAttribute || !form.hasAttribute('data-mk-confirm')) return;
    if (form.dataset.mkApproved === '1') { delete form.dataset.mkApproved; return; }
    e.preventDefault();
    e.stopPropagation();
    // Preserve the original submit button. Django action forms rely on
    // name/value (for example action=delete); requestSubmit() without the
    // submitter silently drops that value and makes the server do nothing.
    var submitter = e.submitter || null;
    API.confirm(optsFrom(form)).then(function (ok) {
      if (!ok) return;
      form.dataset.mkApproved = '1';
      if (form.requestSubmit) {
        try {
          if (submitter && submitter.form === form && !submitter.disabled) form.requestSubmit(submitter);
          else form.requestSubmit();
        } catch (_) { form.submit(); }
      } else { form.submit(); }
    });
  }, true);
})();
