# SmartPOS — Professional Service Recovery

## Included in this release
- Standalone branded 400/403/404/500 pages with no CDN dependency.
- A DB/session-independent maintenance middleware.
- `GET /health/` liveness probe (web process only).
- `GET /ready/` readiness probe (includes PostgreSQL connectivity).
- `GET /service-status/` browser-safe recovery status.
- Branded `/maintenance/`, `/server-unavailable/` and `/offline/` pages.
- Service Worker v13 preserves intentional maintenance/branded error responses.
- Every response receives an `X-Request-ID` for support correlation.
- External Cloudflare Worker fallback under `deploy/fallback/` for outages where Railway cannot serve Django at all.

## Railway variables
Optional planned-maintenance controls:

```
MAINTENANCE_MODE=False
MAINTENANCE_MESSAGE=We are carrying out a brief service improvement. Please try again shortly.
MAINTENANCE_RETRY_SECONDS=60
```

Set `MAINTENANCE_MODE=True` only for planned maintenance, then return it to `False` when finished.

## Railway health check
Use `/health/` as Railway's liveness/deploy health-check path. It intentionally does not query PostgreSQL, so a short database interruption does not cause Railway to restart an otherwise healthy web process.

Use `/ready/` in an external uptime monitor when you want to verify database readiness too.

## Important: when Railway itself is suspended/down
Django cannot render any page if the Railway service is not running. To keep a branded customer page available during billing suspension, platform outage, container boot failure, or Railway gateway failure, put an independent edge layer in front of Railway.

A Cloudflare Worker example is included at:

`deploy/fallback/cloudflare-worker.js`

Configure a Worker secret/variable:

`RAILWAY_ORIGIN=<your-service>.up.railway.app`

Then route the customer-facing domain through that Worker. The Worker forwards normal requests to Railway and replaces infrastructure gateway failures (502/503/504/52x) with the SmartPOS recovery page. The edge layer must remain outside Railway; otherwise it cannot protect against a total Railway outage.

## Security behavior
- No traceback, database exception, infrastructure secret, or Railway internal detail is shown to customers.
- 403 pages do not display raw permission exception strings.
- Error/recovery responses are `no-store`.
- Recovery endpoints disclose only operational state.


## V30.24 connection recovery deployment check

If the browser still shows the old plain text page (`SmartPOS server is temporarily unavailable.`), the request is reaching an older deployment or a platform/edge 503 before the Django application. Deploy this version to Railway and ensure the external edge fallback is configured in front of `mringobey.store`. A Django template cannot replace a 503 generated before Django is reached.

After deployment, verify: `/health/`, `/ready/`, `/service-status/`, `/server-unavailable/`, and an intentionally invalid URL. Hard-refresh the browser and unregister an old service worker only if the browser continues serving stale recovery HTML.
