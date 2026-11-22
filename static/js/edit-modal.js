/*
 * Edit Modal — one consistent way to edit anything.
 *
 * Any link that points to an "edit" screen (…/edit/, …/compensation/, …/permissions/)
 * or that carries data-edit-modal opens in a responsive modal instead of a new page:
 *   - bottom sheet on phones, centred dialog on tablets/desktops
 *   - the real server-rendered form is used, so validation, permissions and
 *     business rules stay exactly the same
 *   - saves through fetch: validation errors re-render inside the modal,
 *     success closes it and refreshes the page (Django messages appear as toasts)
 *   - opt-out with data-no-modal; ctrl/cmd-click and target=_blank keep native behaviour
 * If anything unexpected happens the link simply navigates normally.
 */
(function () {
    'use strict';

    var EDIT_PATH = /\/(edit|compensation|permissions)\/?(\?.*)?$/;
    var root = null, lastFocus = null, currentUrl = '', busy = false;

    function ensureRoot() {
        if (root) return root;
        var style = document.createElement('style');
        style.textContent =
            '#edit-modal{position:fixed;inset:0;z-index:70;display:none}' +
            '#edit-modal.is-open{display:block}' +
            '#edit-modal .em-backdrop{position:absolute;inset:0;background:rgba(15,23,42,.55);backdrop-filter:blur(3px);opacity:0;transition:opacity .18s}' +
            '#edit-modal.is-open .em-backdrop{opacity:1}' +
            '#edit-modal .em-wrap{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:16px;pointer-events:none}' +
            /* The panel never grows past the screen: header + footer stay put, only the body scrolls. */
            '#edit-modal .em-panel{pointer-events:auto;width:min(100%,46rem);max-height:calc(100vh - 32px);max-height:calc(100dvh - 32px);display:flex;flex-direction:column;background:#fff;border-radius:18px;box-shadow:0 30px 80px -20px rgba(0,0,0,.45),0 0 0 1px rgba(15,23,42,.06);transform:translateY(12px) scale(.985);opacity:0;transition:transform .2s ease,opacity .2s ease;overflow:hidden}' +
            '#edit-modal.is-open .em-panel{transform:none;opacity:1}' +
            '@media(max-width:639px){#edit-modal .em-wrap{align-items:flex-end;padding:0}#edit-modal .em-panel{width:100%;max-height:92vh;max-height:92dvh;border-radius:20px 20px 0 0;transform:translateY(28px)}}' +
            '#edit-modal .em-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:16px 24px;border-bottom:1px solid #e2e8f0;background:linear-gradient(#fff,#f8fafc);flex:none}' +
            '#edit-modal .em-kicker{font-size:11px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:#0f766e;margin:0}' +
            '#edit-modal .em-title{margin:2px 0 0;font-size:18px;font-weight:800;color:#0f172a;line-height:1.25;word-break:break-word}' +
            '#edit-modal .em-close{flex:none;border:0;background:transparent;border-radius:999px;padding:6px;margin:-4px -8px 0 0;color:#94a3b8;cursor:pointer}' +
            '#edit-modal .em-close:hover{background:#f1f5f9;color:#475569}' +
            '#edit-modal .em-body{flex:1 1 auto;min-height:0;overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch;padding:20px 24px 0;font-size:14px;line-height:1.45;scrollbar-width:thin}' +
            /* Page templates wrap forms in max-w-lg etc. — that is what squeezed the form into half the dialog. */
            '#edit-modal .em-holder>*{max-width:none!important;width:100%}' +
            '#edit-modal .em-body .card{box-shadow:none;border:0;padding:0;background:transparent}' +
            '#edit-modal .em-body label{font-size:13px;font-weight:600;color:#334155}' +
            '#edit-modal .em-body input:not([type=checkbox]):not([type=radio]):not([type=file]):not([type=hidden]),#edit-modal .em-body select{min-height:40px;height:40px;padding:.5rem .75rem;font-size:14px;width:100%}' +
            '#edit-modal .em-body textarea{min-height:88px;padding:.55rem .75rem;font-size:14px;width:100%}' +
            '#edit-modal .em-body form{min-width:0}' +
            '#edit-modal .em-body form.em-auto-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem 1.25rem;align-items:start}' +
            '#edit-modal .em-body form > [data-field]{min-width:0}' +
            '#edit-modal .em-body form > .rounded-xl,#edit-modal .em-body form > p,#edit-modal .em-body form > .flex{grid-column:1 / -1}' +
            '@media(max-width:639px){#edit-modal .em-body form.em-auto-grid{grid-template-columns:1fr}#edit-modal .em-body{padding:16px 16px 0}#edit-modal .em-head{padding:14px 16px}}' +
            '#edit-modal .em-body form .helptext,#edit-modal .em-body form .text-ink-400{line-height:1.35}' +
            '#edit-modal .em-body .space-y-6>*+*{margin-top:1rem!important}' +
            '#edit-modal .em-body .space-y-5>*+*{margin-top:.9rem!important}' +
            '#edit-modal .em-body .space-y-4>*+*{margin-top:.9rem!important}' +
            '#edit-modal .em-body h1,#edit-modal .em-body .page-header{display:none}' +
            /* Action bar: always visible at the bottom of the dialog, buttons on the right. */
            '#edit-modal .em-body form > .flex:last-child,#edit-modal .em-body form .em-actions{position:sticky;bottom:0;z-index:2;display:flex;justify-content:flex-end;gap:.75rem;background:#fff;margin:20px -24px 0;padding:14px 24px;border-top:1px solid #e2e8f0}' +
            '#edit-modal .em-body form > .flex:last-child .btn,#edit-modal .em-body form .em-actions .btn{flex:0 0 auto;min-width:120px;justify-content:center}' +
            '@media(max-width:639px){#edit-modal .em-body form > .flex:last-child,#edit-modal .em-body form .em-actions{margin:16px -16px 0;padding:12px 16px calc(12px + env(safe-area-inset-bottom,0px))}#edit-modal .em-body form > .flex:last-child .btn,#edit-modal .em-body form .em-actions .btn{flex:1 1 0}}' +
            '#edit-modal .em-skel{display:grid;gap:12px;padding-bottom:20px}' +
            '#edit-modal .em-skel i{display:block;height:42px;border-radius:10px;background:linear-gradient(90deg,#f1f5f9,#e2e8f0,#f1f5f9);background-size:200% 100%;animation:emsh 1.1s linear infinite}' +
            '#edit-modal .em-skel i:first-child{height:14px;width:35%}' +
            '@keyframes emsh{to{background-position:-200% 0}}' +
            '@media(prefers-reduced-motion:reduce){#edit-modal *{transition:none!important;animation:none!important}}';
        document.head.appendChild(style);

        root = document.createElement('div');
        root.id = 'edit-modal';
        root.setAttribute('role', 'dialog');
        root.setAttribute('aria-modal', 'true');
        root.setAttribute('aria-labelledby', 'em-title');
        root.innerHTML =
            '<div class="em-backdrop" data-em-close></div>' +
            '<div class="em-wrap"><div class="em-panel">' +
            '<div class="em-head"><div><p class="em-kicker">Edit</p><h2 class="em-title" id="em-title">Loading…</h2></div>' +
            '<button type="button" class="em-close" data-em-close aria-label="Close"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>' +
            '<div class="em-body" id="em-body"></div>' +
            '</div></div>';
        document.body.appendChild(root);

        root.addEventListener('click', function (e) {
            if (e.target.closest('[data-em-close]')) { e.preventDefault(); close(); }
        });
        root.addEventListener('submit', onSubmit, true);
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && root.classList.contains('is-open')) close();
        });
        return root;
    }

    function body() { return document.getElementById('em-body'); }
    function open() {
        ensureRoot();
        lastFocus = document.activeElement;
        root.classList.add('is-open');
        document.body.style.overflow = 'hidden';
    }

    function close() {
        if (!root || busy) return;
        root.classList.remove('is-open');
        document.body.style.overflow = '';
        setTimeout(function () { if (!root.classList.contains('is-open')) body().innerHTML = ''; }, 220);
        if (lastFocus && lastFocus.focus) lastFocus.focus();
    }

    function skeleton() {
        body().innerHTML = '<div class="em-skel"><i></i><i></i><i></i><i></i><i></i></div>';
    }

    function toast(msg, tag) {
        if (typeof window.showToast === 'function') window.showToast(msg, tag || 'error');
    }

    // Pull the editable content out of a full server-rendered page.
    function render(html, url) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var main = doc.querySelector('#app-content') || doc.querySelector('main');
        var form = main && main.querySelector('form[method="post" i], form[method="POST"]');
        if (!main || !form) return false;

        var h = main.querySelector('h1') || main.querySelector('h2');
        document.getElementById('em-title').textContent = (h && h.textContent.trim()) || (doc.title || 'Edit').split(' - ')[0];

        // Keep the page's own helper styles (page-scoped) working inside the modal.
        doc.querySelectorAll('style[data-spa-page-style]').forEach(function (st) {
            var id = 'em-style-' + (st.getAttribute('data-spa-page-style') || '');
            if (!document.getElementById(id)) { var c = document.createElement('style'); c.id = id; c.textContent = st.textContent; document.head.appendChild(c); }
        });

        var holder = document.createElement('div');
        holder.className = 'em-holder';
        Array.prototype.slice.call(main.children).forEach(function (n) {
            if (n.tagName === 'SCRIPT') return;
            holder.appendChild(document.importNode(n, true));
        });
        // Messages blocks inside the page are shown as toasts elsewhere; remove duplicates.
        holder.querySelectorAll('[data-django-messages], .messages').forEach(function (n) { n.remove(); });

        // Cancel / Back links become "close" buttons.
        holder.querySelectorAll('form a').forEach(function (a) {
            if (/cancel|back|rudi|funga/i.test(a.textContent.trim()) || a.classList.contains('btn-secondary') && !a.hasAttribute('data-keep')) {
                var b = document.createElement('button');
                b.type = 'button'; b.className = a.className; b.textContent = a.textContent.trim() || 'Cancel';
                b.setAttribute('data-em-close', '');
                a.replaceWith(b);
            }
        });
        var f = holder.querySelector('form');
        if (f) {
            f.setAttribute('data-em-form', '1');
            f.setAttribute('data-em-action', f.getAttribute('action') || url);
            f.removeAttribute('action');
            // Dense edit forms are laid out in two compact columns on desktop.
            // Long forms scroll inside the dialog body; header and action bar stay visible.
            var directFields = f.querySelectorAll(':scope > [data-field]');
            if (directFields.length >= 3) f.classList.add('em-auto-grid');
        }
        body().innerHTML = '';
        body().appendChild(holder);

        // Execute the page's inline scripts (they enhance the form: dependent fields, etc.).
        main.querySelectorAll('script:not([src])').forEach(function (old) {
            try { (new Function(old.textContent))(); } catch (err) { /* page script not needed in modal */ }
        });
        if (typeof window.enhanceButtons === 'function') window.enhanceButtons(body());
        var first = body().querySelector('input:not([type=hidden]):not([disabled]), select, textarea');
        if (first) setTimeout(function () { try { first.focus({ preventScroll: true }); } catch (e) {} }, 60);
        return true;
    }

    function openEdit(url, kicker) {
        currentUrl = url;
        open();
        var k = root.querySelector('.em-kicker');
        if (k) k.textContent = kicker || 'Edit';
        document.getElementById('em-title').textContent = 'Loading…';
        skeleton();
        fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'text/html' } })
            .then(function (res) {
                if (res.redirected && !EDIT_PATH.test(new URL(res.url).pathname)) { throw { nav: res.url }; }
                if (!res.ok) throw { nav: url };
                return res.text();
            })
            .then(function (html) { if (!render(html, url)) throw { nav: url }; })
            .catch(function (err) {
                busy = false;
                root.classList.remove('is-open'); document.body.style.overflow = '';
                window.location.href = (err && err.nav) || url;
            });
    }

    function onSubmit(e) {
        var form = e.target.closest('form[data-em-form]');
        if (!form) return;
        e.preventDefault(); e.stopPropagation();
        if (busy) return;
        if (form.checkValidity && !form.checkValidity()) { form.reportValidity(); return; }
        busy = true;
        var submitter = e.submitter || form.querySelector('button[type=submit]');
        var btns = form.querySelectorAll('button[type=submit]');
        var label = submitter ? submitter.innerHTML : '';
        btns.forEach(function (b) { b.disabled = true; });
        if (submitter) submitter.innerHTML = '<span class="spinner"></span> <span>Saving…</span>';

        var data = new FormData(form);
        if (submitter && submitter.name) data.append(submitter.name, submitter.value);
        var action = form.getAttribute('data-em-action') || currentUrl;

        fetch(action, { method: 'POST', body: data, credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (res) {
                if (res.redirected) {
                    busy = false;
                    // V54.30 fail-safe: if the navigation below never replaces the page, free the buttons.
                    setTimeout(function () {
                        btns.forEach(function (b) { b.disabled = false; });
                        if (submitter) submitter.innerHTML = label;
                    }, 12000);
                    var target = new URL(res.url);
                    root.classList.remove('is-open'); document.body.style.overflow = '';
                    if (target.pathname === window.location.pathname && !target.search) window.location.reload();
                    else window.location.href = res.url;
                    return null;
                }
                return res.text().then(function (html) { return { ok: res.ok, html: html }; });
            })
            .then(function (out) {
                if (!out) return;
                busy = false;
                if (!render(out.html, action)) {
                    toast(out.ok ? 'Saved.' : 'Could not save. Please try again.', out.ok ? 'success' : 'error');
                    close();
                    if (out.ok) window.location.reload();
                } else {
                    var err = body().querySelector('.text-red-600, .errorlist, [class*="error"]');
                    toast('Please correct the highlighted fields.', 'error');
                    if (err && err.scrollIntoView) err.scrollIntoView({ block: 'center', behavior: 'smooth' });
                }
            })
            .catch(function () {
                busy = false;
                btns.forEach(function (b) { b.disabled = false; });
                if (submitter) submitter.innerHTML = label;
                toast('Network problem — your changes were not saved. Check your connection and try again.', 'error');
            });
    }

    // Capture phase so it wins over the SPA navigation handler.
    document.addEventListener('click', function (e) {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var a = e.target.closest && e.target.closest('a[href]');
        if (!a || a.hasAttribute('data-no-modal') || a.target === '_blank' || a.hasAttribute('download')) return;
        var url;
        try { url = new URL(a.href, window.location.href); } catch (x) { return; }
        if (url.origin !== window.location.origin) return;
        if (!a.hasAttribute('data-edit-modal') && !EDIT_PATH.test(url.pathname + url.search)) return;
        if (url.pathname.indexOf('/admin/') === 0) return;
        e.preventDefault(); e.stopPropagation();
        openEdit(url.pathname + url.search, a.getAttribute('data-em-kicker') || '');
    }, true);

    window.openEditModal = openEdit;
})();
