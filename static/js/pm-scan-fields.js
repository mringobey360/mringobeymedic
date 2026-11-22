/*
 * Adds "Scan" buttons to inventory pages (needs pm-scanner.js loaded first):
 *   - Product create/edit  : scan the pack barcode into the Barcode field (with duplicate check).
 *                            A GS1 pharma barcode (Data Matrix / GS1-128) also pre-fills batch number + expiry.
 *   - Product list         : scan a pack to search for it.
 * Configure with: window.PM_BARCODE_CHECK_URL, window.PM_BARCODE_EXCLUDE_ID (product being edited).
 */
(function () {
    'use strict';
    if (window.__pmScanFields) return;
    window.__pmScanFields = true;

    var ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><path d="M7 8v8M10.5 8v8M14 8v8M17 8v8"/></svg>';

    function setVal(el, v) {
        el.value = v;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }

    async function checkBarcode(code) {
        var url = window.PM_BARCODE_CHECK_URL;
        if (!url) return { exists: false };
        try {
            var q = url + '?code=' + encodeURIComponent(code) + (window.PM_BARCODE_EXCLUDE_ID ? '&exclude=' + encodeURIComponent(window.PM_BARCODE_EXCLUDE_ID) : '');
            var r = await fetch(q, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } });
            if (r.ok) return await r.json();
        } catch (e) { /* offline: the server still enforces uniqueness on save */ }
        return { exists: false, unchecked: true };
    }

    function scanButton(label) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn btn-secondary shrink-0 inline-flex items-center gap-2';
        b.innerHTML = ICON + '<span>' + label + '</span>';
        return b;
    }

    // ---- product form: barcode field ----
    function enhanceBarcodeField() {
        var input = document.getElementById('id_barcode');
        if (!input || input.dataset.pmScan) return;
        input.dataset.pmScan = '1';
        var row = document.createElement('div');
        row.className = 'flex gap-2 items-stretch';
        input.parentNode.insertBefore(row, input);
        row.appendChild(input);
        input.style.flex = '1 1 auto'; input.style.minWidth = '0';
        var btn = scanButton('Scan');
        btn.setAttribute('aria-label', 'Scan barcode with camera');
        row.appendChild(btn);
        var note = document.createElement('p');
        note.className = 'text-xs mt-1 text-ink-400';
        note.textContent = 'Scan the barcode on the pack, or leave blank to auto-generate one you can print.';
        row.parentNode.insertBefore(note, row.nextSibling);

        function show(text, kind) {
            note.textContent = text;
            note.className = 'text-xs mt-1 ' + (kind === 'bad' ? 'text-red-600 font-medium' : kind === 'ok' ? 'text-emerald-700' : 'text-ink-400');
        }

        btn.addEventListener('click', function () {
            if (!window.PMScanner) { alert('Scanner failed to load. Reload the page.'); return; }
            PMScanner.open({
                title: 'Scan product barcode',
                hint: 'Scan the barcode printed on the pack',
                continuous: false,
                onResult: async function (parsed) {
                    var chk = await checkBarcode(parsed.code);
                    if (chk.exists) {
                        show('Already registered as "' + chk.name + '". Pick another pack or edit that product.', 'bad');
                        return { ok: false, message: 'Already registered: ' + chk.name };
                    }
                    setVal(input, parsed.code);
                    var extra = [];
                    if (parsed.isGS1) {
                        var lot = document.getElementById('id_batch_number');
                        var exp = document.getElementById('id_expiry_date');
                        if (lot && parsed.lot && !lot.value) { setVal(lot, parsed.lot); extra.push('batch ' + parsed.lot); }
                        if (exp && parsed.expiry && !exp.value) { setVal(exp, parsed.expiry); extra.push('expiry ' + parsed.expiry); }
                    }
                    show('Barcode captured: ' + parsed.code + (extra.length ? ' (also filled ' + extra.join(', ') + ')' : ''), 'ok');
                    return { ok: true, message: '✓ ' + parsed.code + (extra.length ? '  ·  ' + extra.join(', ') : '') };
                }
            });
        });

        input.addEventListener('change', async function () {
            var v = input.value.trim();
            if (!v) return;
            var chk = await checkBarcode(v);
            if (chk.exists) show('Already registered as "' + chk.name + '". Barcodes must be unique per branch.', 'bad');
        });
    }

    // ---- product list: scan to search ----
    function enhanceListSearch() {
        var input = document.getElementById('product-search-input');
        if (!input || input.dataset.pmScan) return;
        input.dataset.pmScan = '1';
        var wrap = input.parentNode;
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.setAttribute('aria-label', 'Scan barcode to search'); btn.title = 'Scan barcode to search';
        btn.className = 'absolute top-1/2 -translate-y-1/2 right-2 w-8 h-8 inline-flex items-center justify-center rounded-lg text-ink-500 hover:text-brand-700 hover:bg-ink-100 transition';
        btn.innerHTML = ICON;
        wrap.appendChild(btn);
        input.style.paddingRight = '2.8rem';
        var spin = document.getElementById('product-search-spinner');
        if (spin) { spin.classList.remove('right-3'); spin.style.right = '2.9rem'; }
        btn.addEventListener('click', function () {
            if (!window.PMScanner) return;
            PMScanner.open({
                title: 'Scan to find product',
                continuous: false,
                onResult: function (parsed) { setVal(input, parsed.code); return { ok: true, message: '✓ ' + parsed.code }; }
            });
        });
    }

    function init() { enhanceBarcodeField(); enhanceListSearch(); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
