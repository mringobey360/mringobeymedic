# SmartPOS — Branded recovery page in front of Railway

Django already serves branded 400/403/404/500 and maintenance pages. But while Railway is
deploying, restarting, or the container has crashed, Django cannot answer, and Railway shows
its own "Application failed to respond" page. `cloudflare-worker.js` sits in front of Railway
and shows the SmartPOS recovery page instead.

```text
Visitor -> Cloudflare (Worker) -> Railway / Django
                |
                +-- Railway 502/503/504 or unreachable -> SmartPOS recovery page (HTTP 503)
```

Any response from Django (even a 500 or the maintenance page) passes through untouched.
The Worker recognises Django by the `X-Request-ID` header set in
`core.middleware.ServiceRecoveryMiddleware`; a 5xx without it came from Railway.
`core/test_edge_fallback_contract.py` protects that contract.

## One-time setup

1. Add `mringobey.store` to Cloudflare (use Cloudflare nameservers).
2. DNS: the record pointing to Railway must be **Proxied** (orange cloud). SSL/TLS mode: **Full**.
3. Workers & Pages -> Create Worker -> paste `cloudflare-worker.js` -> Deploy.
4. Worker -> Settings -> Domains & Routes -> add `mringobey.store/*` (and `www.mringobey.store/*` if used).
5. Variables: none needed when the custom domain is attached to the Railway service.
   Otherwise set `RAILWAY_ORIGIN` to the Railway hostname only (never `mringobey.store`)
   and make sure that host is in `ALLOWED_HOSTS`.

## Behaviour

- Browser navigations get the HTML recovery page (HTTP 503, `Retry-After: 30`, never cached).
  The page polls `/health/` and reloads itself when the service is back.
- API/XHR/asset requests get a small JSON 503 instead of HTML.
- GET/HEAD are retried once (700 ms) before the recovery page is shown; POST is never retried.
- WebSocket upgrades pass straight through.
- A short reference code is shown on the page for support.

## Testing

- `https://mringobey.store/__edge_status` returns `operational` or `unavailable`.
- During a controlled window, redeploy or stop the web service and open the site: you must see
  the SmartPOS page, not Railway's. When Railway recovers the page reopens the app by itself.
- `MAINTENANCE_MODE=True` still shows Django's own maintenance page.

## Editing the page

Edit `index.html`, then paste its HTML into `FALLBACK_HTML` in `cloudflare-worker.js`
(keep the `__REF__` placeholder; avoid backticks, `${` and backslashes inside the HTML).

## Limits

This hides infrastructure failures from visitors; it does not keep the app running while
Railway is down. Keep `DEBUG=False` and never expose Railway hostnames or tracebacks publicly.
