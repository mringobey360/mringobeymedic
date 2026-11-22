/*
 * SmartPOS / Pharmacy Manager — camera barcode & QR scanner (shared).
 *
 * Works on phones (Android Chrome, iPhone Safari), tablets and laptops with a webcam.
 * Reads: EAN-13, EAN-8, UPC-A/E, Code-128, Code-39, Code-93, ITF, Codabar, QR, Data Matrix.
 *
 * Engines (first one that works wins):
 *   1. Native BarcodeDetector   (Chrome / Android — fastest, no download)
 *   2. ZXing-js                 (iPhone Safari, Firefox, desktop). Loaded from
 *                               /static/vendor/zxing/zxing.min.js if present, otherwise a CDN.
 *   3. jsQR                     (QR codes only — last resort)
 *
 * USB / Bluetooth hardware scanners need no camera: they type the code and press Enter,
 * which the POS and the product search boxes handle separately.
 *
 * Public API
 *   PMScanner.open({
 *       title:      'Scan product',
 *       hint:       'Point the camera at the barcode',
 *       continuous: true|false,          // keep scanning after each hit (POS) or close (forms)
 *       onResult:   async (parsed) => ({ ok: true|false, message: '...' }) | undefined
 *   })
 *   PMScanner.parse(text)   -> { raw, code, gtin, expiry, lot, serial, isGS1 }
 *   PMScanner.variants(code)-> list of equivalent barcodes (EAN-13 / UPC-A / GTIN-14 forms)
 *   PMScanner.close()
 */
(function () {
    'use strict';
    if (window.PMScanner) return;

    var ZXING_SOURCES = [
        '/static/vendor/zxing/zxing.min.js',
        'https://cdn.jsdelivr.net/npm/@zxing/library@0.21.3/umd/index.min.js',
        'https://unpkg.com/@zxing/library@0.21.3/umd/index.min.js',
        'https://cdnjs.cloudflare.com/ajax/libs/zxing-library/0.21.3/index.min.js'
    ];
    var JSQR_SOURCES = [
        'https://cdnjs.cloudflare.com/ajax/libs/jsQR/1.4.0/jsQR.min.js',
        'https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js'
    ];
    var NATIVE_FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'code_93', 'itf', 'codabar', 'qr_code', 'data_matrix'];

    // ------------------------------------------------------------------ helpers
    function loadScript(src) {
        return new Promise(function (resolve, reject) {
            var s = document.createElement('script');
            s.src = src; s.async = true;
            s.onload = function () { resolve(); };
            s.onerror = function () { s.remove(); reject(new Error('load failed: ' + src)); };
            document.head.appendChild(s);
        });
    }
    async function loadFirst(sources, test) {
        if (test()) return true;
        for (var i = 0; i < sources.length; i++) {
            try { await loadScript(sources[i]); if (test()) return true; } catch (e) { /* try next */ }
        }
        return false;
    }

    var audioCtx = null;
    function beep(ok) {
        try {
            var AC = window.AudioContext || window.webkitAudioContext;
            if (!AC) return;
            audioCtx = audioCtx || new AC();
            var o = audioCtx.createOscillator(), g = audioCtx.createGain();
            o.type = 'sine'; o.frequency.value = ok ? 1100 : 280;
            g.gain.value = 0.08;
            o.connect(g); g.connect(audioCtx.destination);
            o.start(); o.stop(audioCtx.currentTime + (ok ? 0.09 : 0.22));
        } catch (e) { /* audio is optional */ }
        try { if (navigator.vibrate) navigator.vibrate(ok ? 60 : [80, 60, 80]); } catch (e) { /* optional */ }
    }

    // ------------------------------------------------------------------ GS1 / code normalisation
    var FIXED_AI = { '00': 18, '01': 14, '02': 14, '11': 6, '12': 6, '13': 6, '15': 6, '16': 6, '17': 6, '20': 2 };
    var VAR_AI = { '10': 20, '21': 20, '22': 20, '30': 8, '37': 8, '240': 30, '241': 30, '250': 30 };

    function gs1Date(yymmdd) {
        if (!/^\d{6}$/.test(yymmdd)) return '';
        var yy = parseInt(yymmdd.slice(0, 2), 10), mm = parseInt(yymmdd.slice(2, 4), 10), dd = parseInt(yymmdd.slice(4, 6), 10);
        if (mm < 1 || mm > 12) return '';
        var year = 2000 + yy;
        var last = new Date(year, mm, 0).getDate();
        if (dd === 0 || dd > last) dd = last;                 // "00" = last day of month; clamp impossible days
        return year + '-' + (mm < 10 ? '0' : '') + mm + '-' + (dd < 10 ? '0' : '') + dd;
    }

    function parseGS1(text) {
        var out = { gtin: '', expiry: '', lot: '', serial: '' };
        var s = text;
        // Human-readable form: (01)06291234567890(17)261231(10)AB123
        if (/^\(\d{2,3}\)/.test(s)) {
            var re = /\((\d{2,3})\)([^(]+)/g, m, any = false;
            while ((m = re.exec(s))) {
                any = true;
                if (m[1] === '01') out.gtin = m[2];
                else if (m[1] === '17') out.expiry = gs1Date(m[2]);
                else if (m[1] === '10') out.lot = m[2];
                else if (m[1] === '21') out.serial = m[2];
            }
            return any && out.gtin ? out : null;
        }
        // Raw element string (FNC1 shown as GS, \x1d). Must start with AI 01 + 14 digits.
        if (!/^01\d{14}/.test(s)) return null;
        var pos = 0;
        while (pos < s.length) {
            if (s.charAt(pos) === '\x1d') { pos++; continue; }
            var ai = null, len;
            if (FIXED_AI[s.substr(pos, 2)] != null) { ai = s.substr(pos, 2); len = FIXED_AI[ai]; }
            else if (VAR_AI[s.substr(pos, 2)] != null) { ai = s.substr(pos, 2); len = VAR_AI[ai]; }
            else if (VAR_AI[s.substr(pos, 3)] != null) { ai = s.substr(pos, 3); len = VAR_AI[ai]; }
            else break;                                       // unknown AI — stop, keep what we have
            pos += ai.length;
            var val;
            if (FIXED_AI[ai] != null) { val = s.substr(pos, len); pos += len; }
            else {
                var end = s.indexOf('\x1d', pos);
                if (end === -1 || end - pos > len) end = Math.min(s.length, pos + len);
                val = s.slice(pos, end); pos = end;
            }
            if (ai === '01') out.gtin = val;
            else if (ai === '17') out.expiry = gs1Date(val);
            else if (ai === '10') out.lot = val;
            else if (ai === '21') out.serial = val;
        }
        return out.gtin ? out : null;
    }

    function parse(text) {
        var raw = String(text == null ? '' : text);
        // Strip AIM symbology identifiers some decoders prepend (]d2, ]C1, ]E0, ]Q1 ...)
        var cleaned = raw.replace(/^\][A-Za-z]\d/, '').replace(/[\r\n\t]+/g, '').trim();
        var g = parseGS1(cleaned);
        if (g) return { raw: raw, code: g.gtin, gtin: g.gtin, expiry: g.expiry, lot: g.lot, serial: g.serial, isGS1: true, link: false };
        // Web link inside a QR (promo / brand site). A GS1 Digital Link (.../01/<GTIN>/10/<lot>) IS a product code.
        if (/^(https?:\/\/|www\.)/i.test(cleaned)) {
            var m = cleaned.match(/\/01\/(\d{14})/);
            if (m) {
                var lot = (cleaned.match(/\/10\/([^\/?#]+)/) || [])[1] || '';
                var ex = (cleaned.match(/\/17\/(\d{6})/) || [])[1] || '';
                return { raw: raw, code: m[1], gtin: m[1], expiry: gs1Date(ex), lot: decodeURIComponent(lot), serial: '', isGS1: true, link: false };
            }
            return { raw: raw, code: cleaned, gtin: '', expiry: '', lot: '', serial: '', isGS1: false, link: true };
        }
        // Anything with spaces or very long is not a retail/product code (barcodes here are max 32 chars).
        var junk = /\s/.test(cleaned) || cleaned.length > 48;
        return { raw: raw, code: cleaned, gtin: '', expiry: '', lot: '', serial: '', isGS1: false, link: junk };
    }

    // Equivalent forms of one retail number: EAN-13 <-> UPC-A (12) <-> GTIN-14 <-> EAN-8 padded.
    function variants(code) {
        var c = String(code || '').trim();
        var list = [c];
        if (/^\d{8,14}$/.test(c)) {
            var stripped = c.replace(/^0+/, '');
            [8, 12, 13, 14].forEach(function (n) {
                if (stripped.length <= n) {
                    var v = stripped;
                    while (v.length < n) v = '0' + v;
                    if (list.indexOf(v) === -1) list.push(v);
                }
            });
        }
        return list;
    }

    // ------------------------------------------------------------------ UI
    var CSS = [
        '.pms-ov{position:fixed;inset:0;z-index:100000;background:#000;display:flex;flex-direction:column;color:#fff;font-family:inherit}',
        '.pms-top{display:flex;align-items:center;gap:10px;padding:max(10px,env(safe-area-inset-top)) 14px 10px;background:rgba(0,0,0,.65)}',
        '.pms-title{flex:1;font-size:15px;font-weight:700;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
        '.pms-btn{border:0;border-radius:10px;background:rgba(255,255,255,.16);color:#fff;font-size:13px;font-weight:600;padding:9px 12px;cursor:pointer;min-height:40px}',
        '.pms-btn[hidden]{display:none}.pms-btn.is-on{background:#facc15;color:#111}',
        '.pms-stage{position:relative;flex:1;min-height:0;overflow:hidden;background:#000}',
        '.pms-video{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}',
        '.pms-frame{position:absolute;left:50%;top:46%;width:min(78vw,420px);height:min(46vw,250px);transform:translate(-50%,-50%);border-radius:16px;box-shadow:0 0 0 100vmax rgba(0,0,0,.5);border:2px solid rgba(255,255,255,.85)}',
        '.pms-frame::after{content:"";position:absolute;left:8%;right:8%;top:50%;height:2px;background:#ef4444;box-shadow:0 0 8px #ef4444;animation:pms-sweep 1.8s ease-in-out infinite alternate}',
        '@keyframes pms-sweep{from{transform:translateY(-60px)}to{transform:translateY(60px)}}',
        '.pms-frame.is-hit{border-color:#22c55e;box-shadow:0 0 0 100vmax rgba(0,40,0,.55)}.pms-frame.is-hit::after{display:none}',
        '.pms-frame.is-bad{border-color:#ef4444}',
        '.pms-msg{position:absolute;left:12px;right:12px;bottom:12px;padding:11px 14px;border-radius:12px;background:rgba(0,0,0,.72);font-size:14px;line-height:1.35;text-align:center}',
        '.pms-msg.ok{background:rgba(21,128,61,.92)}.pms-msg.bad{background:rgba(185,28,28,.94)}',
        '.pms-bottom{padding:10px 14px max(12px,env(safe-area-inset-bottom));background:rgba(0,0,0,.65);display:flex;gap:8px}',
        '.pms-input{flex:1;min-width:0;border:0;border-radius:10px;padding:11px 12px;font-size:16px;background:#fff;color:#111}',
        '.pms-go{background:#0f766e}',
        '@media (prefers-reduced-motion:reduce){.pms-frame::after{animation:none}}'
    ].join('');

    function injectCss() {
        if (document.getElementById('pms-css')) return;
        var st = document.createElement('style');
        st.id = 'pms-css'; st.textContent = CSS;
        document.head.appendChild(st);
    }

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = text;
        return n;
    }

    // ------------------------------------------------------------------ scanner session
    var active = null;

    function open(opts) {
        opts = opts || {};
        if (active) close();
        injectCss();

        var S = {
            opts: opts, stream: null, video: null, raf: 0, stopped: false, busy: false,
            engine: '', native: null, zxReader: null, canvas: document.createElement('canvas'),
            lastCode: '', lastAt: 0, devices: [], deviceIdx: -1, torchOn: false, nativeErrors: 0, ui: {}
        };
        active = S;

        var ov = el('div', 'pms-ov'); ov.setAttribute('role', 'dialog'); ov.setAttribute('aria-modal', 'true');
        var top = el('div', 'pms-top');
        var title = el('div', 'pms-title', opts.title || 'Scan barcode');
        var torchBtn = el('button', 'pms-btn', 'Torch'); torchBtn.type = 'button'; torchBtn.hidden = true;
        var flipBtn = el('button', 'pms-btn', 'Flip'); flipBtn.type = 'button'; flipBtn.hidden = true;
        var closeBtn = el('button', 'pms-btn', opts.continuous ? 'Done' : 'Close'); closeBtn.type = 'button';
        top.appendChild(title); top.appendChild(torchBtn); top.appendChild(flipBtn); top.appendChild(closeBtn);

        var stage = el('div', 'pms-stage');
        var video = el('video', 'pms-video'); video.setAttribute('playsinline', ''); video.setAttribute('muted', ''); video.muted = true; video.autoplay = true;
        var frame = el('div', 'pms-frame');
        var msg = el('div', 'pms-msg', opts.hint || 'Point the camera at the barcode or QR code');
        stage.appendChild(video); stage.appendChild(frame); stage.appendChild(msg);

        var bottom = el('form', 'pms-bottom');
        var input = el('input', 'pms-input'); input.type = 'text'; input.placeholder = 'Or type the code here';
        input.setAttribute('inputmode', 'text'); input.setAttribute('autocomplete', 'off'); input.setAttribute('autocapitalize', 'off'); input.setAttribute('aria-label', 'Type barcode manually');
        var go = el('button', 'pms-btn pms-go', 'OK'); go.type = 'submit';
        bottom.appendChild(input); bottom.appendChild(go);

        ov.appendChild(top); ov.appendChild(stage); ov.appendChild(bottom);
        document.body.appendChild(ov);
        S.ui = { ov: ov, video: video, frame: frame, msg: msg, torchBtn: torchBtn, flipBtn: flipBtn };
        S.video = video;

        closeBtn.addEventListener('click', close);
        bottom.addEventListener('submit', function (e) {
            e.preventDefault();
            var v = input.value.trim();
            if (v) { input.value = ''; handle(S, v, true); }
        });
        torchBtn.addEventListener('click', function () { toggleTorch(S); });
        flipBtn.addEventListener('click', function () { flipCamera(S); });
        document.addEventListener('keydown', escHandler);
        document.addEventListener('visibilitychange', visHandler);

        startCamera(S);
        return S;
    }

    function escHandler(e) { if (e.key === 'Escape') close(); }
    function visHandler() {
        var S = active; if (!S) return;
        if (document.hidden) stopCamera(S); else if (!S.stream) startCamera(S);
    }

    function setMsg(S, text, kind) {
        if (!S.ui.msg) return;
        S.ui.msg.textContent = text;
        S.ui.msg.className = 'pms-msg' + (kind ? ' ' + kind : '');
    }

    async function startCamera(S) {
        if (S.stopped) return;
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setMsg(S, window.isSecureContext === false
                ? 'Camera needs a secure (https) connection. Type the code below instead.'
                : 'This browser cannot open the camera. Type the code below instead.', 'bad');
            return;
        }
        setMsg(S, 'Starting camera…');
        var base = { width: { ideal: 1280 }, height: { ideal: 720 } };
        var attempts = [];
        if (S.deviceIdx >= 0 && S.devices[S.deviceIdx]) attempts.push({ video: Object.assign({ deviceId: { exact: S.devices[S.deviceIdx].deviceId } }, base), audio: false });
        attempts.push({ video: Object.assign({ facingMode: { ideal: 'environment' } }, base), audio: false });
        attempts.push({ video: true, audio: false });

        var stream = null, lastErr = null;
        for (var i = 0; i < attempts.length && !stream; i++) {
            try { stream = await navigator.mediaDevices.getUserMedia(attempts[i]); } catch (e) { lastErr = e; if (e && e.name === 'NotAllowedError') break; }
        }
        if (S.stopped) { if (stream) stream.getTracks().forEach(function (t) { t.stop(); }); return; }
        if (!stream) {
            var n = lastErr && lastErr.name;
            setMsg(S, n === 'NotAllowedError' || n === 'SecurityError'
                ? 'Camera permission denied. Allow camera for this site in the browser settings, then tap Flip or reopen. You can type the code below.'
                : n === 'NotFoundError' ? 'No camera found on this device. Type the code below.' : 'Could not start the camera. Type the code below.', 'bad');
            return;
        }
        S.stream = stream;
        S.video.srcObject = stream;
        try { await S.video.play(); } catch (e) { /* autoplay will start it */ }

        var track = stream.getVideoTracks()[0];
        try {
            var caps = track.getCapabilities ? track.getCapabilities() : {};
            if (caps.focusMode && caps.focusMode.indexOf('continuous') !== -1) await track.applyConstraints({ advanced: [{ focusMode: 'continuous' }] });
            S.ui.torchBtn.hidden = !caps.torch;
        } catch (e) { S.ui.torchBtn.hidden = true; }

        try {
            var devs = await navigator.mediaDevices.enumerateDevices();
            S.devices = devs.filter(function (d) { return d.kind === 'videoinput'; });
            S.ui.flipBtn.hidden = S.devices.length < 2;
        } catch (e) { /* optional */ }

        await pickEngine(S);
        if (S.stopped) return;
        setMsg(S, S.opts.hint || 'Point the camera at the barcode or QR code');
        loop(S);
    }

    function stopCamera(S) {
        cancelAnimationFrame(S.raf); S.raf = 0;
        if (S.stream) { S.stream.getTracks().forEach(function (t) { t.stop(); }); S.stream = null; }
        if (S.video) S.video.srcObject = null;
        S.torchOn = false;
    }

    async function toggleTorch(S) {
        if (!S.stream) return;
        var track = S.stream.getVideoTracks()[0];
        try {
            S.torchOn = !S.torchOn;
            await track.applyConstraints({ advanced: [{ torch: S.torchOn }] });
            S.ui.torchBtn.classList.toggle('is-on', S.torchOn);
        } catch (e) { S.torchOn = false; S.ui.torchBtn.hidden = true; }
    }

    function flipCamera(S) {
        if (S.devices.length < 2) return;
        var current = -1;
        if (S.stream) {
            var id = (S.stream.getVideoTracks()[0].getSettings() || {}).deviceId;
            current = S.devices.findIndex(function (d) { return d.deviceId === id; });
        }
        S.deviceIdx = (current + 1) % S.devices.length;
        stopCamera(S);
        startCamera(S);
    }

    async function pickEngine(S) {
        if (S.engine) return;
        // 1) native detector
        if ('BarcodeDetector' in window) {
            try {
                var supported = BarcodeDetector.getSupportedFormats ? await BarcodeDetector.getSupportedFormats() : NATIVE_FORMATS;
                var formats = NATIVE_FORMATS.filter(function (f) { return supported.indexOf(f) !== -1; });
                if (formats.length >= 3) { S.native = new BarcodeDetector({ formats: formats }); S.engine = 'native'; return; }
            } catch (e) { /* fall through */ }
        }
        // 2) ZXing
        setMsg(S, 'Loading scanner…');
        if (await loadFirst(ZXING_SOURCES, function () { return !!window.ZXing; })) {
            try { S.zxReader = buildZxing(); S.engine = 'zxing'; return; } catch (e) { /* fall through */ }
        }
        // 3) jsQR (QR only)
        if (await loadFirst(JSQR_SOURCES, function () { return !!window.jsQR; })) { S.engine = 'jsqr'; setMsg(S, 'QR-only mode (barcode engine unavailable). Type the code below for barcodes.'); return; }
        S.engine = 'none';
        setMsg(S, 'Scanner engine could not load (check internet). Type the code below.', 'bad');
    }

    function buildZxing() {
        var Z = window.ZXing, F = Z.BarcodeFormat, hints = new Map();
        var formats = [F.EAN_13, F.EAN_8, F.UPC_A, F.UPC_E, F.CODE_128, F.CODE_39, F.CODE_93, F.ITF, F.CODABAR, F.QR_CODE, F.DATA_MATRIX].filter(function (x) { return x != null; });
        hints.set(Z.DecodeHintType.POSSIBLE_FORMATS, formats);
        hints.set(Z.DecodeHintType.TRY_HARDER, true);
        var reader = new Z.MultiFormatReader();
        reader.setHints(hints);
        return reader;
    }

    // ------------------------------------------------------------------ decode loop
    function loop(S) {
        if (S.stopped || !S.stream) return;
        var last = 0;
        function tick(ts) {
            if (S.stopped || !S.stream) return;
            S.raf = requestAnimationFrame(tick);
            if (S.busy || ts - last < 110 || S.video.readyState < 2 || !S.video.videoWidth) return;
            last = ts; S.busy = true;
            decodeFrame(S).then(function (text) {
                S.busy = false;
                if (text) handle(S, text, false);
            }, function () { S.busy = false; });
        }
        S.raf = requestAnimationFrame(tick);
    }

    // Crop to the centre (where the frame is) and cap the size so decoding stays fast on cheap phones.
    function frameToCanvas(S, maxW) {
        var v = S.video, vw = v.videoWidth, vh = v.videoHeight;
        var cw = Math.round(vw * 0.9), ch = Math.round(vh * 0.6);
        var sx = Math.round((vw - cw) / 2), sy = Math.round((vh - ch) / 2);
        var scale = Math.min(1, maxW / cw);
        S.canvas.width = Math.round(cw * scale); S.canvas.height = Math.round(ch * scale);
        var ctx = S.canvas.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(v, sx, sy, cw, ch, 0, 0, S.canvas.width, S.canvas.height);
        return ctx;
    }

    async function decodeFrame(S) {
        if (S.engine === 'native') {
            try {
                var found = await S.native.detect(S.video);
                S.nativeErrors = 0;
                if (found && found.length) {
                    var rank = { ean_13: 0, ean_8: 0, upc_a: 0, upc_e: 0, code_128: 1, code_39: 1, code_93: 1, itf: 1, codabar: 1, data_matrix: 2, qr_code: 3 };
                    found = found.filter(function (f) { return f.rawValue; });
                    found.sort(function (a, b) { return (rank[a.format] == null ? 9 : rank[a.format]) - (rank[b.format] == null ? 9 : rank[b.format]); });
                    // A brand QR (link) next to a real barcode: skip the link and wait for the barcode to be read.
                    var best = found.find(function (f) { return !parse(f.rawValue).link; }) || found[0];
                    if (best) return best.rawValue;
                }
                return null;
            } catch (e) {
                // Some Android devices expose BarcodeDetector but cannot run it — switch to ZXing.
                if (++S.nativeErrors >= 5) {
                    S.engine = ''; S.native = null;
                    if (await loadFirst(ZXING_SOURCES, function () { return !!window.ZXing; })) { try { S.zxReader = buildZxing(); S.engine = 'zxing'; } catch (x) { /* ignore */ } }
                    if (!S.engine) S.engine = 'none';
                }
                return null;
            }
        }
        if (S.engine === 'zxing') {
            var Z = window.ZXing;
            frameToCanvas(S, 900);
            try {
                var lum = new Z.HTMLCanvasElementLuminanceSource(S.canvas);
                var bmp = new Z.BinaryBitmap(new Z.HybridBinarizer(lum));
                return S.zxReader.decode(bmp).getText();
            } catch (e) {
                // NotFoundException on most frames is normal. Try the inverted image occasionally.
                return null;
            }
        }
        if (S.engine === 'jsqr') {
            var ctx = frameToCanvas(S, 800);
            var img = ctx.getImageData(0, 0, S.canvas.width, S.canvas.height);
            var r = window.jsQR(img.data, img.width, img.height, { inversionAttempts: 'attemptBoth' });
            return r && r.data ? r.data : null;
        }
        return null;
    }

    // ------------------------------------------------------------------ result handling
    async function handle(S, text, manual) {
        if (S.stopped) return;
        var parsed = parse(text);
        if (!parsed.code) return;
        if (parsed.link) {
            var t0 = Date.now();
            if (!manual && parsed.code === S.lastCode && t0 - S.lastAt < 2200) { S.lastAt = t0; return; }
            S.lastCode = parsed.code; S.lastAt = t0;
            beep(false);
            S.ui.frame.classList.add('is-bad');
            setMsg(S, 'That is a link/QR from the brand, not a product barcode. Scan the barcode (black lines) on the label.', 'bad');
            setTimeout(function () { if (!S.stopped) S.ui.frame.classList.remove('is-bad'); }, 1200);
            return;
        }
        var now = Date.now();
        // Same label still in front of the camera? Every sighting refreshes the timer, so a label held
        // steady is counted once; it counts again only after it left the view for ~2 s (or via manual entry).
        var same = parsed.code === S.lastCode && now - S.lastAt < 2200;
        S.lastCode = parsed.code; S.lastAt = now;
        if (!manual && same) return;
        if (S.processing) return;
        S.processing = true;
        S.busy = true;

        var res = null;
        try { res = S.opts.onResult ? await S.opts.onResult(parsed) : { ok: true }; }
        catch (e) { res = { ok: false, message: 'Something went wrong. Try again.' }; }
        res = res || { ok: true };
        var ok = res.ok !== false;

        beep(ok);
        S.ui.frame.classList.toggle('is-hit', ok);
        S.ui.frame.classList.toggle('is-bad', !ok);
        setMsg(S, res.message || (ok ? parsed.code : 'Not recognised: ' + parsed.code), ok ? 'ok' : 'bad');

        if (ok && !S.opts.continuous) { setTimeout(close, 250); S.processing = false; return; }
        setTimeout(function () {
            if (S.stopped) return;
            S.ui.frame.classList.remove('is-hit', 'is-bad');
            S.processing = false; S.busy = false;
            setMsg(S, S.opts.hint || 'Point the camera at the next item');
        }, ok ? 900 : 1500);
    }

    function close() {
        var S = active; if (!S) return;
        active = null; S.stopped = true;
        stopCamera(S);
        document.removeEventListener('keydown', escHandler);
        document.removeEventListener('visibilitychange', visHandler);
        if (S.ui.ov && S.ui.ov.parentNode) S.ui.ov.parentNode.removeChild(S.ui.ov);
        if (typeof S.opts.onClose === 'function') { try { S.opts.onClose(); } catch (e) { /* ignore */ } }
    }

    window.PMScanner = { open: open, close: close, parse: parse, variants: variants, beep: beep };
})();
