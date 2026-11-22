/* ui-kit.js (V37.2) - professional toast manager + consistent button icons.
 *
 * Toasts: max 3 visible, the rest queue; identical messages collapse into one with a x2 counter;
 * pause on hover/focus/touch; severity-based duration (errors stay until dismissed); a grouped
 * "N new notifications" toast replaces floods (e.g. many low-stock alerts); Dismiss all.
 */
(function () {
    'use strict';
    var MAX_VISIBLE = 3;
    var DURATION = { success: 4500, info: 5500, warning: 9000, error: 0 };   // 0 = stays until dismissed
    var TONES = {
        success: { icon: 'check-circle', label: 'Success', cls: 'toast-success' },
        error:   { icon: 'alert',        label: 'Action required', cls: 'toast-error' },
        warning: { icon: 'alert',        label: 'Attention', cls: 'toast-warning' },
        info:    { icon: 'info',         label: 'Information', cls: 'toast-info' },
        debug:   { icon: 'info',         label: 'Information', cls: 'toast-info' }
    };
    var queue = [], active = [];

    function svg(name) {
        return '<svg class="icon" aria-hidden="true"><use href="#i-' + name + '"/></svg>';
    }
    function host() {
        var el = document.getElementById('toast-container');
        if (!el) return null;
        if (!el.dataset.ready) {
            el.dataset.ready = '1';
            el.className = 'toast-host';
            el.setAttribute('aria-live', 'polite');
            el.setAttribute('aria-atomic', 'false');
        }
        return el;
    }
    function ensureClear() {
        var el = host(); if (!el) return;
        var btn = el.querySelector('.toast-clear');
        var show = active.length + queue.length > 1;
        if (show && !btn) {
            btn = document.createElement('button');
            btn.type = 'button'; btn.className = 'toast-clear';
            btn.innerHTML = svg('x') + '<span>Dismiss all</span>';
            btn.addEventListener('click', function () { window.toastDismissAll(); });
            el.appendChild(btn);
        } else if (!show && btn) {
            btn.remove(); btn = null;               // V50.7: it used to be re-appended right after remove(), so it stayed on screen forever
        }
        // Keep the button below the stack, but only move it when it is not already last:
        // re-inserting a node mid-click makes the browser drop the click.
        if (btn && el.lastElementChild !== btn) el.appendChild(btn);
    }
    function pump() {
        var el = host(); if (!el) return;
        while (active.length < MAX_VISIBLE && queue.length) { mount(queue.shift()); }
        ensureClear();
    }
    function mount(opts) {
        var el = host(); if (!el) return;
        var tone = TONES[opts.tag] || TONES.info;
        var node = document.createElement('div');
        node.className = 'toast ' + tone.cls;
        node.setAttribute('role', opts.tag === 'error' ? 'alert' : 'status');
        node.innerHTML =
            '<span class="toast-icon">' + svg(opts.icon || tone.icon) + '</span>' +
            '<div class="toast-body"><p class="toast-title"></p><p class="toast-text"></p>' +
            '<div class="toast-actions"></div></div>' +
            '<button type="button" class="toast-close" aria-label="Dismiss notification">' + svg('x') + '</button>' +
            '<span class="toast-progress"></span>';
        node.querySelector('.toast-title').textContent = opts.title || tone.label;
        var textEl = node.querySelector('.toast-text');
        textEl.textContent = opts.text || '';
        if (!opts.text) textEl.remove();
        if (opts.actionLabel && opts.onAction) {
            var a = document.createElement('button');
            a.type = 'button'; a.className = 'toast-action'; a.textContent = opts.actionLabel;
            a.addEventListener('click', function () { try { opts.onAction(); } finally { t.close(); } });
            node.querySelector('.toast-actions').appendChild(a);
        } else { node.querySelector('.toast-actions').remove(); }

        var duration = opts.duration != null ? opts.duration : DURATION[opts.tag === undefined ? 'info' : opts.tag];
        if (duration == null) duration = DURATION.info;
        var remaining = duration, started = 0, timer = null, closed = false;
        var bar = node.querySelector('.toast-progress');
        if (!duration) bar.remove();

        var t = {
            key: opts.key, count: 1, node: node, textEl: textEl,
            close: function (immediate) {
                if (closed) return; closed = true; clearTimeout(timer);
                active = active.filter(function (x) { return x !== t; });
                node.classList.remove('is-in'); node.classList.add('is-out');
                setTimeout(function () { node.remove(); pump(); }, immediate ? 0 : 220);
            },
            bump: function () {
                t.count++;
                var badge = node.querySelector('.toast-count');
                if (!badge) {
                    badge = document.createElement('span'); badge.className = 'toast-count';
                    node.querySelector('.toast-title').appendChild(badge);
                }
                badge.textContent = '\u00d7' + t.count;
                remaining = duration; if (!paused) arm();      // a repeat gives the user more time to read
            }
        };
        var paused = false;
        function arm() {
            if (!duration) return;
            clearTimeout(timer); started = Date.now();
            if (bar) { bar.style.transition = 'none'; bar.style.transform = 'scaleX(' + (remaining / duration) + ')';
                       void bar.offsetWidth; bar.style.transition = 'transform ' + remaining + 'ms linear'; bar.style.transform = 'scaleX(0)'; }
            timer = setTimeout(function () { t.close(); }, remaining);
        }
        function pause() { if (!duration || paused || closed) return; paused = true; clearTimeout(timer);
            remaining -= Date.now() - started; if (bar) { var w = bar.getBoundingClientRect().width / (bar.parentNode.getBoundingClientRect().width || 1);
            bar.style.transition = 'none'; bar.style.transform = 'scaleX(' + w + ')'; } }
        function resume() { if (!duration || !paused || closed) return; paused = false; arm(); }
        node.addEventListener('mouseenter', pause); node.addEventListener('mouseleave', resume);
        node.addEventListener('focusin', pause);    node.addEventListener('focusout', resume);
        node.addEventListener('touchstart', pause, { passive: true }); node.addEventListener('touchend', function () { setTimeout(resume, 1500); });
        node.querySelector('.toast-close').addEventListener('click', function () { t.close(); });

        el.insertBefore(node, el.querySelector('.toast-clear'));
        active.push(t);
        requestAnimationFrame(function () { node.classList.add('is-in'); arm(); });
    }

    /** showToast(text, tag) keeps the old signature; pass an options object for titles/actions. */
    function showToast(text, tag, extra) {
        if (!host()) return;
        var opts = Object.assign({ text: String(text == null ? '' : text), tag: tag || 'info' }, extra || {});
        opts.key = opts.key || (opts.tag + '|' + opts.title + '|' + opts.text);
        var same = active.filter(function (x) { return x.key === opts.key; })[0];
        if (same) { same.bump(); return; }
        var waiting = queue.filter(function (x) { return x.key === opts.key; })[0];
        if (waiting) { waiting.repeat = (waiting.repeat || 0) + 1; return; }
        queue.push(opts); pump();
    }
    window.showToast = showToast;
    window.toastDismissAll = function () { queue = []; active.slice().forEach(function (t) { t.close(true); }); ensureClear(); };

    /** Several server messages at once: show the first two, fold the rest into one summary. */
    window.showDjangoMessagesAsToasts = function () {
        var list = window.DJANGO_MESSAGES || [];
        if (!list.length) return;
        var severity = { error: 3, warning: 2, success: 1, info: 0, debug: 0 };
        var lvl = function (m) { return String(m.tags || '').split(' ')[0]; };
        var ordered = list.slice().sort(function (a, b) { return (severity[lvl(b)] || 0) - (severity[lvl(a)] || 0); });
        ordered.slice(0, 2).forEach(function (m) { showToast(m.text, lvl(m)); });
        if (ordered.length > 2) {
            showToast((ordered.length - 2) + ' more message(s) on this page.', 'info',
                      { title: 'More messages', key: 'django-more-' + ordered.length });
        }
    };
})();

/* ---------------- Consistent button icons ---------------- */
(function () {
    'use strict';
    var RULES = [
        [/^(sign ?up|create (my )?(business )?account|start (\d+-day )?free trial|get started|free trial)/i, 'user-plus'],
        [/^(home|back to home|landing)\b/i, 'home'],
        [/^(pricing|plans?|view subscription|subscription|renew|request renewal|upgrade)/i, 'tag'],
        [/^(reports?|analytics|statistics|summary|performance)\b/i, 'bar-chart'],
        [/^(settings|configure|preferences)\b/i, 'settings'],
        [/^(customers?|staff|employees?|users?|team|assistants?)\b/i, 'users'],
        [/^(stock|inventory|products?|items?|packages?)\b/i, 'package'],
        [/^(sales?|orders?)\b/i, 'receipt'],
        [/^(payments?|pay\b|expenses?|cash|wallet|debts?)/i, 'wallet'],
        [/^(start selling|point of sale|new sale|sell)\b/i, 'cart'],
        [/^(verify (&|and) start|start workday|check.?in)/i, 'play'],
        [/^(verify presence)/i, 'map-pin'],
        [/^(close workday|end workday|check.?out)/i, 'stop'],
        [/^(scan)/i, 'qr'],
        [/location unavailable|request verification|need help|help\b/i, 'help'],
        [/history|log\b|activity/i, 'history'],
        [/^(meal|meals)\b/i, 'utensils'],
        [/^(emergency verify|verify|approve|confirm|accept|reopen)/i, 'check-circle'],
        [/^(reject|decline|void|deny)/i, 'x-circle'],
        [/^(return|refund|undo|revert)/i, 'undo'],
        [/^(add|new|create|register|record)\b/i, 'plus'],
        [/^(save|update|apply|done)\b/i, 'save'],
        [/^(delete|remove|archive)\b/i, 'trash'],
        [/^(edit|modify|change|rename)\b/i, 'edit'],
        [/^(print|receipt)/i, 'printer'],
        [/^(search|find|lookup)\b/i, 'search'],
        [/^(export|download)\b/i, 'download'],
        [/^(import|upload|attach)\b/i, 'upload'],
        [/^(send|submit|report|request)\b/i, 'send'],
        [/^(log ?in|sign ?in)\b/i, 'log-in'],
        [/^(log ?out|sign ?out)\b/i, 'log-out'],
        [/^(refresh|reload|retry|try again|reset|clear)\b/i, 'refresh'],
        [/^(view|details|open|show)\b/i, 'eye'],
        [/^(back|previous)\b/i, 'arrow-left'],
        [/^(next|continue|proceed)\b/i, 'arrow-right'],
        [/^(cancel|dismiss|close)\b/i, 'x'],
        [/^(filter)\b/i, 'filter'],
        [/^(copy)\b/i, 'copy']
    ];
    function iconFor(el) {
        if (el.dataset.icon) return el.dataset.icon === 'none' ? null : el.dataset.icon;
        var text = (el.textContent || '').replace(/\s+/g, ' ').trim();
        if (!text || text.length > 42) return null;
        for (var i = 0; i < RULES.length; i++) if (RULES[i][0].test(text)) return RULES[i][1];
        return null;
    }
    function enhance(root) {
        (root || document).querySelectorAll('.btn, button.row-action, a.row-action').forEach(function (el) {
            if (el.dataset.iconDone) return;
            el.dataset.iconDone = '1';
            if (el.querySelector('svg, img, i, .spinner') || el.classList.contains('no-auto-icon')) return;
            var name = iconFor(el); if (!name) return;
            var span = document.createElement('span');
            span.className = 'btn-label'; span.innerHTML = el.innerHTML;
            el.innerHTML = '<svg class="icon btn-icon" aria-hidden="true"><use href="#i-' + name + '"/></svg>';
            el.appendChild(span);
        });
    }
    /** Safe label update that keeps the icon (used by buttons whose text changes while working). */
    window.setBtnLabel = function (btn, text) {
        var label = btn.querySelector('.btn-label');
        if (label) label.textContent = text; else btn.textContent = text;
    };
    window.enhanceButtons = enhance;
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { enhance(document); });
    else enhance(document);
    if ('MutationObserver' in window) {
        var pending = false;
        new MutationObserver(function () {
            if (pending) return; pending = true;
            requestAnimationFrame(function () { pending = false; enhance(document); });
        }).observe(document.body || document.documentElement, { childList: true, subtree: true });
    }
})();

/* ---------------- Floating panel: a non-blocking, toast-style form ---------------- */
(function () {
    'use strict';
    var opener = null;
    function panels() { return document.querySelectorAll('.float-panel.is-open'); }
    function open(panel, from) {
        if (!panel) return;
        panels().forEach(function (p) { if (p !== panel) close(p, true); });
        opener = from || null;
        panel.hidden = false;
        requestAnimationFrame(function () {
            panel.classList.add('is-open');
            var first = panel.querySelector('input:not([type=hidden]), textarea, select');
            if (first && window.matchMedia('(min-width: 640px)').matches) { try { first.focus({ preventScroll: true }); } catch (e) {} }
        });
        if (from) from.setAttribute('aria-expanded', 'true');
    }
    function close(panel, silent) {
        if (!panel) return;
        panel.classList.remove('is-open');
        setTimeout(function () { if (!panel.classList.contains('is-open')) panel.hidden = true; }, 200);
        document.querySelectorAll('[data-float-open="' + panel.id + '"]').forEach(function (b) { b.setAttribute('aria-expanded', 'false'); });
        if (!silent && opener) { try { opener.focus({ preventScroll: true }); } catch (e) {} }
    }
    document.addEventListener('click', function (e) {
        var o = e.target.closest('[data-float-open]');
        if (o) {
            var panel = document.getElementById(o.getAttribute('data-float-open'));
            if (panel) { panel.classList.contains('is-open') ? close(panel) : open(panel, o); }
            return;
        }
        var c = e.target.closest('[data-float-close]');
        if (c) close(c.closest('.float-panel'));
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') panels().forEach(function (p) { close(p); });
    });
    window.floatPanel = { open: function (id) { open(document.getElementById(id)); }, close: function (id) { close(document.getElementById(id)); } };
})();
