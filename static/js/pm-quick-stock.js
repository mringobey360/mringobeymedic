/*
 * Scan -> Register / Restock (Products page). Needs pm-scanner.js.
 *
 * Flow: scan a pack
 *   - barcode not in this branch  -> "Register new product" sheet (product + opening stock + prices)
 *   - barcode already registered  -> "Restock" sheet (new batch: qty, cost, price, expiry, batch no.)
 * The sheet is rendered by the server from the SAME forms the normal pages use, so validation,
 * audit trail and notifications are identical to the manual / Excel routes.
 * "Save & scan next" reopens the camera so a whole delivery can be received pack by pack.
 *
 * Trigger: any element with  data-pqs-open  and these data attributes:
 *   data-resolve-url, data-register-url, data-restock-url  (restock url contains /0/ for the product id)
 */
(function () {
    'use strict';
    if (window.PMQuickStock) return;

    var CSS = [
        '.pqs-ov{position:fixed;inset:0;z-index:100001;background:rgba(15,23,42,.55);display:flex;align-items:flex-end;justify-content:center}',
        '.pqs-sheet{width:100%;max-width:640px;max-height:94vh;background:#fff;border-radius:18px 18px 0 0;display:flex;flex-direction:column;box-shadow:0 -10px 40px rgba(0,0,0,.25);animation:pqs-up .22s ease-out}',
        '@keyframes pqs-up{from{transform:translateY(40px);opacity:.4}to{transform:none;opacity:1}}',
        '@media(min-width:700px){.pqs-ov{align-items:center}.pqs-sheet{border-radius:18px;max-height:90vh}}',
        '.pqs-body{overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch}',
        '.pqs-loading{padding:40px 20px;text-align:center;color:#64748b;font-size:14px}',
        '.pqs-form{display:flex;flex-direction:column;min-height:0}',
        '.pqs-head{padding:16px 18px 6px}',
        '.pqs-head h2{margin:6px 0 2px;font-size:19px;font-weight:800;color:#0f172a;line-height:1.2}',
        '.pqs-sub{margin:0;font-size:13px;color:#64748b}',
        '.pqs-stock{margin:10px 0 0;padding:12px 14px;border-radius:12px;background:#f8fafc;border:1px solid #e2e8f0}',
        '.pqs-stock.is-ok{background:#f0fdf4;border-color:#bbf7d0}.pqs-stock.is-low{background:#fffbeb;border-color:#fde68a}.pqs-stock.is-out{background:#fef2f2;border-color:#fecaca}',
        '.pqs-stock-top{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}',
        '.pqs-stock-label{width:100%;font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:#475569}',
        '.pqs-stock-num{font-size:32px;font-weight:800;line-height:1;color:#0f172a}.pqs-stock-unit{font-size:14px;font-weight:600;color:#475569}',
        '.pqs-chip{margin-left:auto;font-size:11px;font-weight:800;padding:3px 10px;border-radius:999px}',
        '.pqs-chip.is-ok{background:#dcfce7;color:#166534}.pqs-chip.is-low{background:#fef3c7;color:#92400e}.pqs-chip.is-out{background:#fee2e2;color:#991b1b}',
        '.pqs-meta{display:grid;grid-template-columns:repeat(3,1fr);gap:8px 12px;margin:10px 0 0}.pqs-meta div{min-width:0}',
        '.pqs-meta dt{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#64748b}.pqs-meta dd{margin:1px 0 0;font-size:13px;font-weight:700;color:#0f172a}',
        '.pqs-note{margin:6px 0 0;padding:6px 10px;border-radius:8px;background:#f8fafc;border:1px solid #e2e8f0;font-size:12px;color:#475569}',
        '.pqs-found{margin:6px 0 0;padding:6px 10px;border-radius:8px;background:#f0fdf4;border:1px solid #bbf7d0;font-size:12px;color:#166534}',
        '.pqs-badge{display:inline-block;font-size:10.5px;font-weight:800;letter-spacing:.08em;padding:3px 9px;border-radius:999px}',
        '.pqs-badge.is-new{background:#dbeafe;color:#1d4ed8}.pqs-badge.is-restock{background:#dcfce7;color:#15803d}',
        '.pqs-sec{padding:12px 18px 4px;font-size:11.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:#475569}',
        '.pqs-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 12px;padding:4px 18px}',
        '.pqs-field{min-width:0}.pqs-field.is-wide{grid-column:1/-1}',
        '.pqs-field label{display:block;font-size:12px;font-weight:600;color:#334155;margin-bottom:3px}',
        '.pqs-field input:not([type=checkbox]),.pqs-field select{width:100%;box-sizing:border-box;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;font-size:15px;line-height:1.35;background-color:#fff;color:#0f172a;min-height:44px;max-width:100%;}',
        '.pqs-field select{appearance:auto;-webkit-appearance:menulist;cursor:pointer;color-scheme:light}',
        '.pqs-field select option{background:#fff;color:#0f172a;padding:8px}',
        '.pqs-field input[readonly]{background:#f1f5f9;color:#475569}',
        '.pqs-field input:focus,.pqs-field select:focus{outline:2px solid rgba(15,118,110,.18);outline-offset:0;border-color:#0f766e;box-shadow:0 0 0 1px #0f766e}',
        '.pqs-field.has-error input,.pqs-field.has-error select{border-color:#dc2626}',
        '.pqs-check{display:flex!important;align-items:center;gap:9px;font-size:13px!important;font-weight:600;margin:2px 0}',
        '.pqs-check input{width:20px;height:20px;flex:none}',
        '.pqs-help{margin:3px 0 0;font-size:11px;color:#94a3b8}.pqs-err{margin:3px 0 0;font-size:12px;font-weight:600;color:#dc2626}',
        '.pqs-form>.pqs-err{padding:6px 18px 0}',
        '.pqs-preview{margin:10px 18px 0;padding:10px 12px;border-radius:10px;background:#f0fdfa;border:1px solid #99f6e4;font-size:13px;color:#134e4a;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}',
        '.pqs-preview.is-bad{background:#fef2f2;border-color:#fecaca;color:#991b1b}',
        '.pqs-foot{position:sticky;bottom:0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:12px 18px max(14px,env(safe-area-inset-bottom));margin-top:12px;background:#fff;border-top:1px solid #e2e8f0}',
        '.pqs-more{margin-right:auto;font-size:12.5px;color:#64748b;text-decoration:underline}',
        '.pqs-btn{border:1px solid #cbd5e1;background:#fff;color:#334155;border-radius:10px;padding:11px 16px;font-size:14px;font-weight:700;cursor:pointer;min-height:44px}',
        '.pqs-btn.is-primary{background:#0f766e;border-color:#0f766e;color:#fff}.pqs-btn.is-strong{background:#115e59}',
        '.pqs-btn[disabled]{opacity:.55;cursor:wait}',
        '@media(max-width:520px){.pqs-meta{grid-template-columns:1fr 1fr}.pqs-grid{grid-template-columns:1fr}.pqs-foot .pqs-btn{flex:1 1 auto}.pqs-more{flex-basis:100%;margin-bottom:2px}}'
    ].join('');

    var cfg = null, pending = null, dirty = false, sheet = null, lastId = null, enabled = true;

    function injectCss() {
        if (document.getElementById('pqs-css')) return;
        var st = document.createElement('style'); st.id = 'pqs-css'; st.textContent = CSS; document.head.appendChild(st);
    }
    function toast(msg, kind) { if (typeof window.showToast === 'function') window.showToast(msg, kind || 'success'); }
    function qs(o) { return Object.keys(o).filter(function (k) { return o[k]; }).map(function (k) { return k + '=' + encodeURIComponent(o[k]); }).join('&'); }

    // Always finish on the Products page (never the dashboard). A saved item is highlighted in the list.
    function goToProducts(id) {
        var base = (cfg && cfg.listUrl) || '/inventory/';
        window.location.assign(id ? base + (base.indexOf('?') === -1 ? '?' : '&') + 'highlight=' + encodeURIComponent(id) : base);
    }

    async function getJson(url) {
        var r = await fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' } });
        if ((r.headers.get('content-type') || '').indexOf('json') === -1) throw new Error('session');
        return r.json();
    }

    // ------------------------------------------------------------ scanning
    function startScan() {
        if (!window.PMScanner) { toast('The scanner could not be loaded. Please reload the page.', 'error'); return; }
        pending = null;
        PMScanner.open({
            title: 'Scan a product barcode',
            hint: 'New products open the registration form. Existing products open the restock form.',
            continuous: false,
            onResult: onScan,
            onClose: function () {
                if (pending) { var p = pending; pending = null; openSheet(p); }
                else if (dirty && !(cfg && cfg.stay)) { dirty = false; goToProducts(lastId); }
            }
        });
    }

    async function onScan(parsed) {
        if (parsed.link) return { ok: false, message: 'This is a brand link or QR code, not a product barcode. Please scan the barcode (the black lines).' };
        var res;
        try { res = await getJson(cfg.resolveUrl + '?' + qs({ code: parsed.code })); }
        catch (e) { return { ok: false, message: e.message === 'session' ? 'Your session has expired or you do not have permission. Please reload the page.' : 'Network problem. Please try again.' }; }
        if (res.status === 'new') {
            pending = { mode: 'register', code: parsed.code, lot: parsed.lot, expiry: parsed.expiry };
            return { ok: true, message: 'New product. Opening the registration form...' };
        }
        if (res.status === 'exists') {
            pending = { mode: 'restock', id: res.id, code: parsed.code, lot: parsed.lot, expiry: parsed.expiry };
            return { ok: true, message: res.name + '. Opening the restock form...' };
        }
        if (res.status === 'inactive') return { ok: false, message: res.name + ' is deactivated. Reactivate it before restocking.' };
        return { ok: false, message: res.message || 'This code could not be read. Please try again.' };
    }

    // ------------------------------------------------------------ sheet
    function closeSheet() { if (sheet) { sheet.remove(); sheet = null; } }

    async function openSheet(p) {
        injectCss(); closeSheet();
        var ov = document.createElement('div'); ov.className = 'pqs-ov'; ov.setAttribute('role', 'dialog'); ov.setAttribute('aria-modal', 'true');
        var box = document.createElement('div'); box.className = 'pqs-sheet';
        var body = document.createElement('div'); body.className = 'pqs-body';
        body.innerHTML = '<div class="pqs-loading">Loading...</div>';
        box.appendChild(body); ov.appendChild(box); document.body.appendChild(ov); sheet = ov;
        // Tapping outside intentionally does nothing: typed data is never discarded by accident (use Cancel).

        var url = p.mode === 'register'
            ? cfg.registerUrl + '?' + qs({ code: p.code, lot: p.lot, expiry: p.expiry, stay: cfg.stay ? '1' : '' })
            : cfg.restockUrl.replace('/0/', '/' + p.id + '/') + '?' + qs({ lot: p.lot, expiry: p.expiry, stay: cfg.stay ? '1' : '' });
        try {
            var r = await fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            if (!r.ok || (r.headers.get('content-type') || '').indexOf('html') === -1) throw new Error('bad');
            body.innerHTML = await r.text();
        } catch (e) {
            body.innerHTML = '<div class="pqs-loading">The form could not be opened (session expired or no permission). <a href="' + window.location.href + '">Reload this page</a></div>';
            return;
        }
        wire(body, p);
    }

    function wire(body, p) {
        var form = body.querySelector('form'); if (!form) return;
        if (window.PMInlineAdd) PMInlineAdd.init(body);     // "+ Add new category / unit" inside the sheet
        var submitter = null;
        form.querySelectorAll('[type=submit]').forEach(function (b) { b.addEventListener('click', function () { submitter = b; }); });
        form.querySelector('[data-pqs-cancel]').addEventListener('click', function () {
            closeSheet();
            if (dirty && !(cfg && cfg.stay)) { dirty = false; goToProducts(lastId); }    // otherwise stay where we are
        });
        ['input', 'change'].forEach(function (ev) { form.addEventListener(ev, function () { preview(form); }); });
        preview(form);
        var first = form.querySelector(p.mode === 'register' ? '[name=name]' : '[name=purchase_quantity]');
        if (first) setTimeout(function () { try { first.focus(); } catch (e) { /* ignore */ } }, 50);

        form.addEventListener('submit', async function (e) {
            e.preventDefault();
            var goNext = submitter && submitter.value === '1';
            var btns = form.querySelectorAll('[type=submit]'); btns.forEach(function (b) { b.disabled = true; });
            var data;
            try {
                var fd = new FormData(form); fd.set('next', goNext ? '1' : '0');
                var r = await fetch(form.action, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' } });
                if ((r.headers.get('content-type') || '').indexOf('json') === -1) throw new Error('bad');
                data = await r.json();
            } catch (err) {
                btns.forEach(function (b) { b.disabled = false; });
                toast('Could not save because of a network or session problem. Nothing was saved. Please try again.', 'error');
                return;
            }
            if (data.ok) {
                dirty = true; lastId = (data.product && data.product.id) || lastId; closeSheet();
                if (window.PMScanner) PMScanner.beep(true);
                toast(data.message, 'success');
                document.dispatchEvent(new CustomEvent('pqs:saved', { detail: { product: data.product, stock: data.stock, sellable: data.sellable, unit: data.unit } }));
                if (goNext) startScan();
                else if (cfg && cfg.stay) { dirty = false; }      // POS restock mode: stay on the POS page
                else { dirty = false; setTimeout(function () { window.location.assign(data.redirect_url || ((cfg && cfg.listUrl) || '/inventory/')); }, 500); }
            } else if (data.html) {
                body.innerHTML = data.html;      // same sheet again, with the errors and the typed values kept
                wire(body, p);
                var bad = body.querySelector('.has-error input, .has-error select'); if (bad) bad.focus();
                if (window.PMScanner) PMScanner.beep(false);
            } else {
                btns.forEach(function (b) { b.disabled = false; });
                toast(data.message || 'Could not save. Please try again.', 'error');
            }
        });
    }

    function num(form, name) { var el = form.querySelector('[name=' + name + ']'); var v = el ? parseFloat(el.value) : NaN; return isNaN(v) ? null : v; }
    function money(n) { return Math.round(n * 100) / 100; }
    function preview(form) {
        var out = form.querySelector('[data-pqs-preview]'), box = form.querySelector('.pqs-preview'); if (!out) return;
        var qty = num(form, 'purchase_quantity'), per = num(form, 'units_per_purchase_unit') || 1;
        var price = num(form, 'purchase_unit_price'), sell = num(form, 'selling_price');
        var parts = [], bad = false;
        var cur = parseFloat(form.dataset.currentStock), lastCost = parseFloat(form.dataset.lastCost);
        if (qty) {
            var add = qty * per;
            if (form.dataset.mode === 'restock' && !isNaN(cur)) parts.push('Stock ' + cur.toLocaleString() + ' \u2192 ' + (cur + add).toLocaleString() + ' (+' + add.toLocaleString() + ')');
            else parts.push('Adds ' + add.toLocaleString() + ' units');
        }
        if (price != null && per) {
            var cost = price / per; parts.push('cost TSh ' + money(cost).toLocaleString() + '/unit');
            if (!isNaN(lastCost) && lastCost > 0) {
                var chg = (cost - lastCost) / lastCost * 100;
                if (Math.abs(chg) >= 1) parts.push('cost ' + (chg > 0 ? 'up ' : 'down ') + Math.abs(Math.round(chg)) + '% vs last purchase');
            }
            if (sell != null) {
                var profit = sell - cost;
                if (profit < 0) { bad = true; parts.push('SELLING BELOW COST'); }
                else parts.push('profit TSh ' + money(profit).toLocaleString() + '/unit (' + (sell ? Math.round(profit / sell * 100) : 0) + '%)');
            }
        }
        out.textContent = parts.length ? parts.join(' · ') : 'Enter quantity and prices.';
        if (box) box.classList.toggle('is-bad', bad);
    }

    // ------------------------------------------------------------ hardware scanner (auto-detect, no button)
    // USB / Bluetooth scanners type the code in a burst and press Enter. A burst of >= 6 characters with
    // < 80 ms between keys can't be a human typing, so it is treated as a scan from anywhere on the page.
    var wBuf = '', wLast = 0;
    function wedgeReset() { wBuf = ''; wLast = 0; }
    async function wedgeScan(code) {
        var parsed = window.PMScanner ? PMScanner.parse(code) : { code: code, raw: code };
        var res = await onScan(parsed);
        if (window.PMScanner) PMScanner.beep(res.ok);
        if (res.ok && pending) { var p = pending; pending = null; openSheet(p); }
        else toast(res.message, 'warning');
    }
    function onWedgeKey(e) {
        if (!cfg || !enabled || sheet || document.querySelector('.pms-ov')) { wedgeReset(); return; }
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        var ae = document.activeElement, tag = ae && ae.tagName;
        var typing = (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (ae && ae.isContentEditable));
        if (typing && ae.id !== 'product-search-input') { wedgeReset(); return; }   // user is filling another field
        var now = Date.now();
        if (e.key === 'Enter') {
            var code = wBuf; wedgeReset();
            if (code.length >= 6 && /^[A-Za-z0-9\-_.\/]+$/.test(code)) {
                e.preventDefault(); e.stopPropagation();
                if (ae && ae.id === 'product-search-input') { ae.value = ''; ae.dispatchEvent(new Event('input', { bubbles: true })); }
                wedgeScan(code);
            }
            return;
        }
        if (e.key.length !== 1) return;
        if (wLast && now - wLast > 80) wBuf = '';
        wBuf += e.key; wLast = now;
    }

    // ------------------------------------------------------------ boot
    function init() {
        document.querySelectorAll('[data-pqs-open]').forEach(function (btn) {
            if (btn.dataset.pqsWired) return; btn.dataset.pqsWired = '1';
            cfg = cfg || { resolveUrl: btn.dataset.resolveUrl, registerUrl: btn.dataset.registerUrl, restockUrl: btn.dataset.restockUrl, listUrl: btn.dataset.listUrl };
            btn.addEventListener('click', function () {
                cfg = { resolveUrl: btn.dataset.resolveUrl, registerUrl: btn.dataset.registerUrl, restockUrl: btn.dataset.restockUrl, listUrl: btn.dataset.listUrl };
                dirty = false; lastId = null; startScan();
            });
        });
        // Manual registration opens the exact same quick-register sheet used after a barcode scan.
        // No scanner is started and no full-page navigation occurs.
        document.querySelectorAll('[data-pqs-register-open]').forEach(function (btn) {
            if (btn.dataset.pqsRegisterWired) return; btn.dataset.pqsRegisterWired = '1';
            cfg = cfg || { resolveUrl: btn.dataset.resolveUrl, registerUrl: btn.dataset.registerUrl, restockUrl: btn.dataset.restockUrl, listUrl: btn.dataset.listUrl };
            btn.addEventListener('click', function () {
                cfg = { resolveUrl: btn.dataset.resolveUrl, registerUrl: btn.dataset.registerUrl, restockUrl: btn.dataset.restockUrl, listUrl: btn.dataset.listUrl };
                dirty = false; lastId = null;
                openSheet({ mode: 'register', code: '', lot: '', expiry: '' });
            });
        });
        if (cfg && !window.__pqsWedge) { window.__pqsWedge = true; document.addEventListener('keydown', onWedgeKey, true); }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
    function configure(c) {
        cfg = c;
        if (!window.__pqsWedge) { window.__pqsWedge = true; document.addEventListener('keydown', onWedgeKey, true); }
    }
    window.PMQuickStock = {
        init: init, start: startScan, configure: configure,
        setEnabled: function (v) { enabled = !!v; wedgeReset(); },
        scanCode: wedgeScan,
        openRestock: function (id) { if (cfg && id) openSheet({ mode: 'restock', id: id }); }
    };
})();
