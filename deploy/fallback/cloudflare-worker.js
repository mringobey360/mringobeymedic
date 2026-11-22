/**
 * SmartPOS — Cloudflare Worker: branded recovery page in front of Railway.
 *
 * Every request for mringobey.store passes through here. Responses from Django
 * (including its own 4xx/5xx and maintenance pages) are returned untouched.
 * When Railway itself cannot answer (502/503/504, redeploy in progress, crashed
 * container, unreachable origin) the visitor sees the SmartPOS page instead of
 * Railway's gateway page.
 *
 * Django vs Railway: every Django response carries an X-Request-ID header
 * (core.middleware.ServiceRecoveryMiddleware). A 5xx without it came from
 * Railway or the edge.
 *
 * Optional variables:
 *   RAILWAY_ORIGIN    e.g. your-app.up.railway.app (only if the custom domain is
 *                     not attached to the Railway service). Never the public domain.
 *   ORIGIN_TIMEOUT_MS default 60000
 */
const FALLBACK_HTML = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#0f766e"><meta name="robots" content="noindex,nofollow"><title>Service temporarily unavailable — SmartPOS</title><style>
:root{--ink:#0f172a;--muted:#64748b;--soft:#f8fafc;--line:#e2e8f0;--brand:#0f766e;--brand2:#115e59;--mint:#ecfdf5;--white:#fff;--shadow:0 30px 80px rgba(15,23,42,.12)}
*{box-sizing:border-box}html{min-height:100%;background:#f8fafc}body{margin:0;min-height:100vh;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--ink);background:radial-gradient(circle at 50% -10%,rgba(20,184,166,.13),transparent 34%),linear-gradient(145deg,#f8fafc 0%,#f0fdfa 48%,#f8fafc 100%);display:grid;place-items:center;padding:24px}.shell{width:min(640px,100%)}.topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:0 2px 16px}.brand{display:flex;align-items:center;gap:11px}.logo{width:44px;height:44px;border-radius:13px;object-fit:cover;box-shadow:0 8px 22px rgba(15,118,110,.18);background:#0f766e}.brand-name{font-size:16px;font-weight:850;letter-spacing:-.02em}.brand-sub{font-size:11px;color:#94a3b8;margin-top:2px}.secure{display:inline-flex;align-items:center;gap:7px;color:#0f766e;background:rgba(255,255,255,.78);border:1px solid #d8eeea;border-radius:999px;padding:7px 10px;font-size:11px;font-weight:800}.secure i{width:7px;height:7px;border-radius:50%;background:#14b8a6;box-shadow:0 0 0 4px rgba(20,184,166,.12)}.card{position:relative;overflow:hidden;background:rgba(255,255,255,.97);border:1px solid rgba(215,231,229,.95);border-radius:28px;padding:38px 38px 30px;box-shadow:var(--shadow);text-align:center}.card:before{content:"";position:absolute;inset:0 0 auto;height:4px;background:linear-gradient(90deg,#0f766e,#14b8a6,#0f766e)}.icon{width:74px;height:74px;border-radius:22px;margin:2px auto 18px;display:grid;place-items:center;background:var(--mint);color:var(--brand);border:1px solid #d8f3ec}.icon svg{width:36px;height:36px}.badge{display:inline-flex;align-items:center;gap:8px;border-radius:999px;background:#f0fdfa;color:var(--brand);border:1px solid #d8f3ec;padding:7px 11px;font-size:11px;font-weight:850;letter-spacing:.01em}.dot{width:7px;height:7px;border-radius:50%;background:#14b8a6;box-shadow:0 0 0 4px rgba(20,184,166,.12)}h1{font-size:31px;line-height:1.14;letter-spacing:-.045em;margin:17px auto 11px;max-width:500px}.lead{margin:0 auto;color:var(--muted);font-size:15px;line-height:1.75;max-width:480px}.reassure{margin:22px auto 0;max-width:500px;display:flex;align-items:flex-start;gap:11px;text-align:left;background:#f8fafc;border:1px solid var(--line);border-radius:16px;padding:14px 15px;font-size:13px;color:#334155;line-height:1.6}.reassure .mini{flex:0 0 28px;width:28px;height:28px;border-radius:9px;display:grid;place-items:center;background:#ecfdf5;color:var(--brand);font-weight:900}.actions{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-top:24px}.btn{appearance:none;border:0;border-radius:13px;padding:12px 18px;font-weight:850;font-size:13px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:8px;min-width:145px;transition:transform .15s ease,box-shadow .15s ease,background .15s ease}.btn:hover{transform:translateY(-1px)}.primary{background:var(--brand);color:#fff;box-shadow:0 8px 20px rgba(15,118,110,.18)}.primary:hover{background:var(--brand2)}.secondary{background:#fff;color:#334155;border:1px solid #cbd5e1}.secondary:hover{background:#f8fafc}.statusline{display:flex;align-items:center;justify-content:center;gap:7px;margin-top:18px;color:#94a3b8;font-size:11px;min-height:18px}.spinner{width:12px;height:12px;border:2px solid #99f6e4;border-top-color:var(--brand);border-radius:50%;animation:spin .8s linear infinite}.incident{margin-top:8px;color:#a0aec0;font-size:10px;letter-spacing:.02em}.footer{text-align:center;color:#94a3b8;font-size:11px;margin-top:15px}.footer strong{color:#64748b;font-weight:750}.divider{height:1px;background:#eef2f7;margin:25px 0 16px}.tips{display:flex;justify-content:center;gap:18px;flex-wrap:wrap;color:#94a3b8;font-size:10px}.tips span{display:inline-flex;align-items:center;gap:5px}.tips b{color:#64748b;font-weight:750}.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:560px){body{padding:14px}.topbar{margin-bottom:12px}.secure{display:none}.card{padding:32px 20px 25px;border-radius:23px}h1{font-size:26px}.lead{font-size:14px}.actions{flex-direction:column}.btn{width:100%}.reassure{font-size:12px}.tips{gap:10px 16px}}

.en{display:block;color:#94a3b8;font-size:.86em;font-weight:500;letter-spacing:0;margin-top:5px}
h1 .en{font-size:15px;line-height:1.4;letter-spacing:0;margin-top:8px}
.lead .en{margin-top:8px}
.ref{margin-top:10px;color:#a0aec0;font-size:11px}
</style></head><body><main class="shell"><header class="topbar"><div class="brand"><div class="logo" style="display:grid;place-items:center;color:#fff;font-weight:900;font-size:20px">P</div><div><div class="brand-name">SmartPOS</div><div class="brand-sub">Professional business management</div></div></div><div class="secure"><i></i>Secure service</div></header><section class="card"><div class="icon"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="6" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><rect x="4" y="14" width="16" height="6" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M7 7h.01M7 17h.01M10 7h6M10 17h6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></div><div class="badge"><span class="dot"></span>Service update in progress</div><h1>We&rsquo;ll be right back.</h1><p class="lead">SmartPOS is briefly unavailable, usually while an update is being applied. We are checking automatically, and this page will open the system as soon as it is ready.</p><div class="reassure"><div class="mini">&#10003;</div><div><strong>Your business data is safe.</strong><br>This interruption does not mean your sales, stock, customers or reports have been lost.</div></div><div class="actions"><button id="retry" class="btn primary" onclick="retry()">Try again</button></div><div id="status" class="statusline"><span class="spinner"></span><span>Checking service availability&hellip;</span></div><div class="ref">Reference: __REF__</div></section><div class="footer"><strong>SmartPOS</strong> &middot; Built by Mutech Apex Innovations</div></main>
<script>
var busy=false,delay=5000,timer;
function setStatus(m){document.getElementById('status').textContent=m}
function retry(){
  if(busy)return;busy=true;
  var b=document.getElementById('retry');b.disabled=true;b.textContent='Checking...';
  var c=new AbortController(),t=setTimeout(function(){c.abort()},7000);
  fetch('/health/?recovery='+Date.now(),{cache:'no-store',signal:c.signal}).then(function(r){
    clearTimeout(t);
    if(r.ok){setStatus('Service restored. Reloading...');location.reload();return}
    setStatus('The service is still recovering. We will check again shortly.');
  }).catch(function(){
    setStatus(navigator.onLine?'The service is not reachable yet. We will keep checking.':'Your device appears to be offline. We will retry when it reconnects.');
  }).finally(function(){
    b.disabled=false;b.textContent='Try again';busy=false;
    clearTimeout(timer);timer=setTimeout(retry,delay);delay=Math.min(delay*1.5,30000);
  });
}
setTimeout(retry,4000);
window.addEventListener('online',retry);
</script></body></html>`;

const EDGE_ERRORS = new Set([502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530]);

function newRef() {
  return crypto.randomUUID().replace(/-/g, "").slice(0, 8).toUpperCase();
}

function isOriginFailure(res) {
  if (res.headers.has("x-railway-fallback")) return true;
  if (!EDGE_ERRORS.has(res.status)) return false;
  // Django responses (errors, maintenance, ...) always include X-Request-ID.
  return !res.headers.has("x-request-id");
}

function fallbackResponse(request) {
  const headers = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Retry-After": "30",
    "X-Robots-Tag": "noindex, nofollow",
    "X-SmartPOS-Edge-Fallback": "1",
  };
  const ref = newRef();
  const wantsHtml = (request.headers.get("Accept") || "").includes("text/html");
  const body = request.method === "HEAD" ? null : undefined;
  if (wantsHtml) {
    return new Response(body === null ? null : FALLBACK_HTML.replace("__REF__", ref), {
      status: 503,
      headers: { ...headers, "Content-Type": "text/html; charset=utf-8" },
    });
  }
  return new Response(
    body === null
      ? null
      : JSON.stringify({
          status: "unavailable",
          ref,
          message: "Service temporarily unavailable. Please try again shortly.",
        }),
    { status: 503, headers: { ...headers, "Content-Type": "application/json; charset=utf-8" } }
  );
}

function buildOriginRequest(request, env, pathOverride) {
  const url = new URL(request.url);
  if (pathOverride) {
    url.pathname = pathOverride;
    url.search = "";
  }
  const origin = (env.RAILWAY_ORIGIN || "").replace(/^https?:\/\//, "").replace(/\/.*$/, "").trim();
  if (!origin || origin.toLowerCase() === url.hostname.toLowerCase()) {
    // Default: custom domain is on Railway; the Worker passes the request to its origin.
    return pathOverride ? new Request(url.toString(), request) : request;
  }
  const publicHost = url.hostname;
  url.hostname = origin;
  url.protocol = "https:";
  url.port = "";
  const proxied = new Request(url.toString(), request);
  proxied.headers.set("X-Forwarded-Host", publicHost);
  proxied.headers.set("X-Forwarded-Proto", "https");
  return proxied;
}

async function callOrigin(request, env) {
  const timeout = Number(env.ORIGIN_TIMEOUT_MS) || 60000;
  return fetch(request, { signal: AbortSignal.timeout(timeout) });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Operator probe: returns a single word.
    if (url.pathname === "/__edge_status") {
      try {
        const res = await fetch(buildOriginRequest(request, env, "/health/"), {
          signal: AbortSignal.timeout(8000),
        });
        const ok = res.status === 200 && !isOriginFailure(res);
        return new Response(ok ? "operational" : "unavailable", {
          status: ok ? 200 : 503,
          headers: { "Cache-Control": "no-store", "Content-Type": "text/plain" },
        });
      } catch (_) {
        return new Response("unavailable", { status: 503, headers: { "Cache-Control": "no-store" } });
      }
    }

    // WebSocket / upgrade requests pass straight through.
    if (request.headers.get("Upgrade")) {
      return fetch(buildOriginRequest(request, env));
    }

    const safeToRetry = request.method === "GET" || request.method === "HEAD";
    // A POST body can be read only once, so only GET/HEAD are retried.
    const attempts = safeToRetry ? 2 : 1;

    for (let i = 0; i < attempts; i++) {
      try {
        const res = await callOrigin(buildOriginRequest(request, env), env);
        if (!isOriginFailure(res)) return res;
      } catch (_) {
        // network error or timeout: retry, then show the recovery page
      }
      if (i + 1 < attempts) await new Promise((r) => setTimeout(r, 700));
    }
    return fallbackResponse(request);
  },
};
