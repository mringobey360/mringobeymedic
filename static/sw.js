// SmartPOS — Service Worker v10
// Resilient connection handling:
// - Never call a transient navigation failure "No internet connection" immediately.
// - Retry the requested page before showing any recovery UI.
// - Distinguish an unreachable Django/Railway service from a lost client connection.
// - Cache recovery pages one-by-one so one missing asset cannot invalidate the whole shell.
// - POST/PUT/PATCH/DELETE requests are never intercepted here.

const CACHE_NAME = 'smartpos-v119-bottom-nav';
// Uploaded photos live in their own cache so they survive app-shell upgrades (file names are unique per upload).
const MEDIA_CACHE = 'smartpos-media-v1';
const MEDIA_CACHE_MAX = 300;
const APP_SHELL = [
    '/static/img/logo.jpg',
    '/static/img/icons/icon-192.png',
    '/static/img/icons/icon-512.png',
    '/static/js/main.js',
    '/static/js/ui-kit.js',
    '/static/js/offline-pos.js',
    '/static/js/pm-scanner.js',
    '/static/js/pm-scan-fields.js',
    '/static/js/pm-quick-stock.js',
    '/static/js/pm-inline-add.js',
    '/static/audio/notification-chime.wav',
    '/offline/',
    '/server-unavailable/',
    '/maintenance/',
    '/sales/pos/',
];
const CDN_ASSETS = [
    'https://cdn.tailwindcss.com',
    'https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/jsQR/1.4.0/jsQR.min.js',
    'https://cdn.jsdelivr.net/npm/@zxing/library@0.21.3/umd/index.min.js',
];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchWithRetry(request, attempts = 3, delayMs = 500) {
    let lastError;
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
        try {
            return await fetch(request, { cache: 'no-store' });
        } catch (error) {
            lastError = error;
            if (attempt < attempts) await sleep(delayMs * attempt);
        }
    }
    throw lastError || new Error('Network request failed');
}

async function trimMediaCache(cache) {
    try {
        const keys = await cache.keys();
        for (let i = 0; i < keys.length - MEDIA_CACHE_MAX; i += 1) await cache.delete(keys[i]);
    } catch (_) {}
}

self.addEventListener('install', (event) => {
    event.waitUntil((async () => {
        const cache = await caches.open(CACHE_NAME);

        // Cache each item independently. A single unavailable asset must never
        // prevent the recovery pages themselves from being stored.
        await Promise.all(APP_SHELL.map(async (url) => {
            try {
                const response = await fetch(url, { cache: 'no-store' });
                if (response.ok) await cache.put(url, response.clone());
            } catch (_) {}
        }));

        await Promise.all(CDN_ASSETS.map(async (url) => {
            try {
                const response = await fetch(url, { mode: 'no-cors', cache: 'no-store' });
                await cache.put(url, response);
            } catch (_) {}
        }));

        self.skipWaiting();
    })());
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((names) =>
            Promise.all(
                names
                    .filter((name) => name !== CACHE_NAME && name !== MEDIA_CACHE)
                    .map((name) => caches.delete(name))
            )
        ).then(() => self.clients.claim())
    );
});

async function backendIsReachable() {
    try {
        const response = await fetch('/health/?sw_check=1&t=' + Date.now(), {
            method: 'GET',
            cache: 'no-store',
            credentials: 'same-origin',
            headers: { 'X-Pharmacy-Health-Check': '1' },
        });
        return response.ok;
    } catch (_) {
        return false;
    }
}

function connectionRecoveryHTML() {
    return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0f766e">
<title>Reconnecting — SmartPOS</title>
<style>
:root{--ink:#0f172a;--muted:#64748b;--brand:#0f766e;--brand2:#115e59;--line:#e2e8f0;--soft:#f8fafc}
*{box-sizing:border-box}html,body{min-height:100%;margin:0}body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:radial-gradient(circle at 50% -10%,rgba(20,184,166,.13),transparent 34%),linear-gradient(145deg,#f8fafc,#f0fdfa 48%,#f8fafc);display:grid;place-items:center;padding:20px}.shell{width:min(620px,100%)}.top{display:flex;align-items:center;justify-content:space-between;margin:0 2px 14px}.brand{font-weight:850;font-size:16px;letter-spacing:-.02em}.sub{font-size:11px;color:#94a3b8;font-weight:500;margin-top:2px}.pill{font-size:11px;font-weight:800;color:var(--brand);background:#fff;border:1px solid #d8eeea;border-radius:999px;padding:7px 10px}.card{background:rgba(255,255,255,.97);border:1px solid #d7e7e5;border-radius:26px;padding:38px 34px 28px;text-align:center;box-shadow:0 30px 80px rgba(15,23,42,.12);position:relative;overflow:hidden}.card:before{content:"";position:absolute;left:0;right:0;top:0;height:4px;background:linear-gradient(90deg,#0f766e,#14b8a6,#0f766e)}.icon{width:72px;height:72px;border-radius:21px;margin:2px auto 18px;display:grid;place-items:center;background:#ecfdf5;border:1px solid #d8f3ec;color:var(--brand)}.icon svg{width:35px;height:35px}.badge{display:inline-flex;align-items:center;gap:8px;border-radius:999px;background:#f0fdfa;color:var(--brand);border:1px solid #d8f3ec;padding:7px 11px;font-size:11px;font-weight:850}.dot{width:7px;height:7px;border-radius:50%;background:#14b8a6;box-shadow:0 0 0 4px rgba(20,184,166,.12)}h1{font-size:30px;line-height:1.14;letter-spacing:-.045em;margin:17px auto 11px;max-width:500px}.lead{margin:0 auto;color:var(--muted);font-size:15px;line-height:1.7;max-width:480px}.reassure{margin:22px auto 0;max-width:490px;text-align:left;background:var(--soft);border:1px solid var(--line);border-radius:15px;padding:13px 15px;color:#475569;font-size:13px;line-height:1.6}.actions{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin-top:22px}.btn{border:0;border-radius:13px;padding:12px 18px;font-size:13px;font-weight:850;cursor:pointer}.primary{background:var(--brand);color:#fff;box-shadow:0 8px 20px rgba(15,118,110,.18)}.secondary{background:#fff;color:#334155;border:1px solid #cbd5e1}.status{min-height:20px;margin-top:16px;color:#94a3b8;font-size:11px}.spinner{display:inline-block;width:12px;height:12px;border:2px solid #99f6e4;border-top-color:var(--brand);border-radius:50%;vertical-align:-2px;margin-right:7px;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.footer{text-align:center;color:#94a3b8;font-size:11px;margin-top:14px}.footer strong{color:#64748b}@media(max-width:560px){.pill{display:none}.card{padding:32px 20px 25px;border-radius:22px}h1{font-size:26px}.lead{font-size:14px}.actions{flex-direction:column}.btn{width:100%}}
</style>
</head>
<body>
<main class="shell">
<header class="top"><div><div class="brand">SmartPOS</div><div class="sub">Professional business management</div></div><div class="pill">Connection recovery</div></header>
<section class="card">
<div class="icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 8.8a13.5 13.5 0 0 1 17 0M6.8 12.2a8.3 8.3 0 0 1 10.4 0M10 15.5a3.2 3.2 0 0 1 4 0" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="m4 4 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></div>
<div class="badge"><span class="dot"></span>Connection interrupted</div>
<h1>We’re reconnecting you.</h1>
<p class="lead">The connection to SmartPOS was interrupted briefly. Your account and business records are not being changed by this screen.</p>
<div class="reassure">We’ll retry automatically. If the connection returns, this page will close and your requested page will load.</div>
<div class="actions"><button id="retry" class="btn primary">Try again</button><button id="home" class="btn secondary">Open dashboard</button></div>
<div id="status" class="status"><span class="spinner"></span>Checking connection…</div>
</section>
<div class="footer"><strong>SmartPOS</strong> · Built by Mutech Apex Innovations</div>
</main>
<script>
const statusEl=document.getElementById('status');
const retry=document.getElementById('retry');
const home=document.getElementById('home');
let busy=false;
async function check(){if(busy)return;busy=true;retry.disabled=true;statusEl.innerHTML='<span class="spinner"></span>Checking connection…';try{const r=await fetch('/health/?reconnect='+Date.now(),{cache:'no-store'});if(r.ok){statusEl.textContent='Connection restored. Returning…';setTimeout(()=>location.reload(),250);return}statusEl.textContent='The service is responding but is still recovering.'}catch(e){statusEl.textContent='Still reconnecting… we’ll keep trying automatically.'}finally{busy=false;retry.disabled=false}}
retry.addEventListener('click',check);home.addEventListener('click',()=>location.href='/');window.addEventListener('online',check);setInterval(check,7000);check();
</script>
</body></html>`;
}

async function navigationFallback() {
    // Give a flapping connection a little time to recover before rendering any UI.
    try {
        const probe = await fetchWithRetry(new Request('/health/?sw_probe=1&t=' + Date.now(), {
            method: 'GET', cache: 'no-store', credentials: 'same-origin',
            headers: { 'X-Pharmacy-Health-Check': '1' },
        }), 2, 700);
        if (probe.ok) {
            return new Response(connectionRecoveryHTML(), {
                status: 503,
                headers: {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'},
            });
        }
        const serverCached = await caches.match('/server-unavailable/');
        if (serverCached) return serverCached;
    } catch (_) {}

    const offlineCached = await caches.match('/offline/');
    if (offlineCached) return offlineCached;

    // Absolute last resort: never expose a generic browser-looking error page.
    return new Response(connectionRecoveryHTML(), {
        status: 503,
        headers: {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'},
    });
}

self.addEventListener('fetch', (event) => {
    const {request} = event;
    if (request.method !== 'GET') return;

    const url = new URL(request.url);
    if (url.origin === self.location.origin && url.pathname === '/health/') return;

    if (request.mode === 'navigate' && url.pathname === '/sales/pos/') {
        event.respondWith((async () => {
            try {
                const response = await fetchWithRetry(request, 2, 450);
                if (response.ok) {
                    const cache = await caches.open(CACHE_NAME);
                    await cache.put(request, response.clone()).catch(() => {});
                }
                return response;
            } catch (_) {
                const cached = await caches.match(request);
                return cached || navigationFallback();
            }
        })());
        return;
    }

    if (request.mode === 'navigate') {
        event.respondWith((async () => {
            try {
                const response = await fetchWithRetry(request, 3, 500);
                if (response.status >= 500 &&
                    response.headers.get('X-Pharmacy-Maintenance') !== '1' &&
                    response.headers.get('X-Pharmacy-Branded-Error') !== '1') {
                    return (await caches.match('/server-unavailable/')) || response;
                }
                return response;
            } catch (_) {
                return navigationFallback();
            }
        })());
        return;
    }

    if (CDN_ASSETS.includes(request.url)) {
        event.respondWith(
            caches.match(request).then((cached) => cached || fetch(request, {mode:'no-cors'}))
        );
        return;
    }

    // Uploaded images (profile photos, product photos, logos, chat images): cache first.
    // Upload names are unique, so a cached copy never goes stale. On a weak connection the photo
    // appears instantly from the device; a missing one is fetched with retries and then kept.
    if (url.origin === self.location.origin && url.pathname.startsWith('/media/') && request.destination === 'image') {
        event.respondWith((async () => {
            const cache = await caches.open(MEDIA_CACHE);
            const cached = await cache.match(request, {ignoreSearch: true});
            if (cached) return cached;
            try {
                const response = await fetchWithRetry(request, 3, 400);
                if (response.ok && response.status === 200) {
                    cache.put(new Request(url.origin + url.pathname), response.clone()).then(() => trimMediaCache(cache)).catch(() => {});
                }
                return response;
            } catch (_) {
                return Response.error();
            }
        })());
        return;
    }

    if (url.pathname.startsWith('/static/')) {
        event.respondWith((async () => {
            const cached = await caches.match(request);
            if (cached) {
                // Refresh in the background; serve the cached copy instantly.
                fetch(request).then((response) => {
                    if (response && response.ok) {
                        caches.open(CACHE_NAME).then((cache) => cache.put(request, response.clone())).catch(() => {});
                    }
                }).catch(() => {});
                return cached;
            }
            try {
                const response = await fetchWithRetry(request, 3, 400);
                if (response.ok) {
                    caches.open(CACHE_NAME).then((cache) => cache.put(request, response.clone())).catch(() => {});
                }
                return response;
            } catch (_) {
                return Response.error();
            }
        })());
    }
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const targetUrl = event.notification?.data?.url || '/notifications/';
    event.waitUntil(
        clients.matchAll({type:'window', includeUncontrolled:true}).then((clientList) => {
            for (const client of clientList) {
                if ('focus' in client) {
                    if (targetUrl && 'navigate' in client) client.navigate(targetUrl);
                    return client.focus();
                }
            }
            return clients.openWindow(targetUrl);
        })
    );
});


// Background Web Push: works when the app tab is closed, provided the browser
// and OS notification permission allow it.
self.addEventListener('push', (event) => {
    let payload = {};
    try { payload = event.data ? event.data.json() : {}; } catch (_) {}
    const title = payload.title || 'SmartPOS';
    const body = payload.body || 'You have a new reminder.';
    const url = payload.url || '/notifications/';
    event.waitUntil(self.registration.showNotification(title, {
        body,
        icon: '/static/img/icons/icon-192.png',
        badge: '/static/img/icons/icon-192.png',
        tag: payload.tag || 'pharmacy-notification',
        renotify: true,
        requireInteraction: false,
        silent: false,
        // Best-effort vibration on supported mobile browsers/OS notification stacks.
        vibrate: [180, 80, 180, 80, 300],
        data: { url },
    }));
});

