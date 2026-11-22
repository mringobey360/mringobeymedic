/*
 * Inline "Add new ..." for dropdowns (category and unit) - used by the scan sheet and the product / batch forms.
 *
 * Mark a <select> with  data-inline-add="category"  (also needs data-inline-url) or  data-inline-add="unit".
 *  - category: saved on the server straight away (so it exists for the whole branch), then selected.
 *  - unit:     added to the list for this form; it is stored with the product / batch when the form is saved.
 * Dropdown choices are supplied by Django from the active branch: branch categories and standard + branch-used units.
 * Do not hard-code category/unit catalog values in this script.
 * Choosing "+ Add new ..." opens a small panel under the field. Enter = Add, Esc = Cancel.
 */
(function () {
    'use strict';
    if (window.PMInlineAdd) return;

    /* Match SmartPOS .form-input / .btn sizing and the system's teal brand tokens. */
    var CSS = [
        '.pm-ia{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin-top:.5rem;padding:.75rem;border:1px solid rgba(20,184,166,.28);background:#f8fffe;border-radius:.75rem;box-shadow:0 1px 2px rgba(15,23,42,.04)}',
        '.pm-ia .pm-ia-input{flex:1 1 150px;width:auto!important;min-width:0;min-height:40px;box-sizing:border-box;border:1px solid #e2e8f0;border-radius:.5rem;padding:.625rem .75rem;font-size:.875rem;line-height:1.25rem;background:#fff;color:#0f172a;transition:border-color .15s,box-shadow .15s}',
        '.pm-ia .pm-ia-input:focus{outline:2px solid transparent;outline-offset:2px;border-color:#14b8a6;box-shadow:0 0 0 3px rgba(20,184,166,.18)}',
        '.pm-ia-btn{display:inline-flex;align-items:center;justify-content:center;min-height:38px;padding:.55rem .85rem;border-radius:.5rem;border:1px solid #e2e8f0;background:#fff;color:#334155;font-size:.875rem;line-height:1.25rem;font-weight:600;cursor:pointer;transition:background .15s,border-color .15s,box-shadow .15s}',
        '.pm-ia-btn:hover{background:#f8fafc;border-color:#cbd5e1}',
        '.pm-ia-btn:focus-visible{outline:2px solid #14b8a6;outline-offset:2px}',
        '.pm-ia-btn.is-primary{background:#0d9488;border-color:#0d9488;color:#fff}',
        '.pm-ia-btn.is-primary:hover{background:#0f766e;border-color:#0f766e}',
        '.pm-ia-btn[disabled]{opacity:.6;cursor:wait}',
        '.pm-ia-msg{flex-basis:100%;margin:0;font-size:.75rem;line-height:1rem;font-weight:600;color:#dc2626}',
        '.pm-ia-hint{flex-basis:100%;margin:0;font-size:.75rem;line-height:1rem;color:#64748b}',
        'select.form-input[data-inline-add]{width:100%;min-width:0}',
        'select.form-input[data-inline-add] option[value="__new__"]{font-weight:600;color:#0f766e}'
    ].join('');

    var UNIT_RE = /^[A-Za-z0-9][A-Za-z0-9 .'\/&()+-]*$/;

    function injectCss() {
        if (document.getElementById('pm-ia-css')) return;
        var st = document.createElement('style'); st.id = 'pm-ia-css'; st.textContent = CSS; document.head.appendChild(st);
    }
    function toast(msg, kind) { if (typeof window.showToast === 'function') window.showToast(msg, kind || 'success'); }
    function tidy(v) {
        v = String(v || '').replace(/\s+/g, ' ').trim();
        return v && v === v.toLowerCase() ? v.charAt(0).toUpperCase() + v.slice(1) : v;
    }
    function csrf(select) {
        var form = select.closest ? select.closest('form') : null;
        var el = (form && form.querySelector('[name=csrfmiddlewaretoken]')) || document.querySelector('[name=csrfmiddlewaretoken]');
        if (el) return el.value;
        var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }
    function findOption(select, text) {
        var t = text.toLowerCase();
        for (var i = 0; i < select.options.length; i++) {
            var o = select.options[i];
            if (o.value === '__new__') continue;
            if (o.value.toLowerCase() === t || o.text.trim().toLowerCase() === t) return o;
        }
        return null;
    }

    function setup(select) {
        if (select.dataset.pmIa) return;
        select.dataset.pmIa = '1';
        var kind = select.dataset.inlineAdd === 'category' ? 'category' : 'unit';
        var newOpt = document.createElement('option');
        newOpt.value = '__new__'; newOpt.textContent = '＋ Add new ' + kind + '…';
        select.appendChild(newOpt);

        var previous = select.value === '__new__' ? '' : select.value, panel = null;

        function close(restore) {
            if (panel) { panel.remove(); panel = null; }
            if (restore) select.value = previous;
        }
        function choose(option) {
            select.value = option.value; previous = option.value; close(false);
            select.dispatchEvent(new Event('change', { bubbles: true }));
        }
        function addOption(value, text) {
            var o = new Option(text, value);
            select.insertBefore(o, newOpt);
            return o;
        }

        function open() {
            if (panel) { panel.querySelector('input').focus(); return; }
            injectCss();
            panel = document.createElement('div'); panel.className = 'pm-ia';
            panel.innerHTML =
                '<input type="text" class="pm-ia-input" maxlength="' + (kind === 'category' ? 100 : 30) + '" autocomplete="off" ' +
                'placeholder="' + (kind === 'category' ? 'New category name' : 'New unit name, e.g. Crate') + '" aria-label="New ' + kind + ' name">' +
                '<button type="button" class="pm-ia-btn is-primary" data-ia-add>Add</button>' +
                '<button type="button" class="pm-ia-btn" data-ia-cancel>Cancel</button>' +
                '<p class="pm-ia-hint">' + (kind === 'category' ? 'Saved to this branch and selected for the product.' : 'Saved to this branch for reuse in future products and stock batches.') + '</p>' +
                '<p class="pm-ia-msg" hidden></p>';
            select.insertAdjacentElement('afterend', panel);
            var input = panel.querySelector('input'), addBtn = panel.querySelector('[data-ia-add]'), msg = panel.querySelector('.pm-ia-msg');
            function say(text) { msg.textContent = text; msg.hidden = !text; }

            async function submit() {
                var name = tidy(input.value);
                if (name.length < 2) { say('Please enter a name with at least 2 characters.'); return; }
                var existing = findOption(select, name);
                if (existing) { choose(existing); toast('"' + existing.text.trim() + '" already exists and has been selected.', 'info'); return; }
                if (kind === 'unit' && (name.length > 30 || !UNIT_RE.test(name))) { say('Use letters, numbers and common unit symbols only, up to 30 characters.'); return; }
                if (!select.dataset.inlineUrl) {
                    if (kind === 'unit') { choose(addOption(name, name)); return; }
                    say('This field is missing its quick-add endpoint.'); return;
                }
                say(''); addBtn.disabled = true;
                try {
                    var fd = new FormData(); fd.append('name', name);
                    var r = await fetch(select.dataset.inlineUrl, {
                        method: 'POST', body: fd, credentials: 'same-origin',
                        headers: { 'X-CSRFToken': csrf(select), 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' }
                    });
                    if ((r.headers.get('content-type') || '').indexOf('json') === -1) throw new Error('bad');
                    var d = await r.json();
                    if (!d.ok) { say(d.message || ('The ' + kind + ' could not be added.')); addBtn.disabled = false; return; }
                    var value = String(d.value !== undefined ? d.value : d.id);
                    var o = findOption(select, d.name) || addOption(value, d.name);
                    o.value = value;
                    o.textContent = d.name;
                    choose(o);
                    toast(d.created ? (kind === 'category' ? 'Category' : 'Unit') + ' added: ' + d.name + '.' : (kind === 'category' ? 'Category' : 'Unit') + ' selected: ' + d.name + '.', 'success');
                } catch (e) {
                    say('The ' + kind + ' could not be added. Please check your connection and try again.');
                    addBtn.disabled = false;
                }
            }

            addBtn.addEventListener('click', submit);
            panel.querySelector('[data-ia-cancel]').addEventListener('click', function () { close(true); });
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); submit(); }
                else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); select.focus(); }
            });
            setTimeout(function () { try { input.focus(); } catch (e) { /* ignore */ } }, 30);
        }

        select.addEventListener('change', function () {
            if (select.value === '__new__') open();
            else { previous = select.value; close(false); }
        });
    }

    function init(root) {
        (root || document).querySelectorAll('select[data-inline-add]').forEach(setup);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { init(); });
    else init();
    window.addEventListener('sp:content-updated', function () { init(); });
    window.PMInlineAdd = { init: init };
})();
