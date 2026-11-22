# Scaling & production hardening (v20.22)

Goal: run comfortably for **~400 pharmacy customers with their branches** on Railway (PostgreSQL + bucket + web service),
and make problems visible before customers notice them.

> The capacity numbers below are **estimates** from the request patterns in the code, not measurements.
> Run the load test (section 6) on a staging copy to get your real number before onboarding everyone.

## 1. What was slowing the system down (and what changed)

| Problem found in the code | Fix in v20.22 |
|---|---|
| Bell polled `/notifications/api/feed/` every **3 s** for every logged-in user | Every **15 s** (30 s on chat pages), **45 s / paused** when the tab is hidden, instant refresh when the tab is visible again |
| Chat polled messages every **1 s** *and* typing every **1.5 s**, and posted "typing" on **every keystroke** | **One** poll every 2.5 s (typing is returned with the message feed), 15 s when hidden, typing ping at most once per 2.5 s |
| A `DELETE` of old notifications ran on **every single request** (twice on bell polls) | Runs at most once per 10 minutes (cluster-wide with Redis) |
| Typing indicator loaded **every participant** of the thread (the customers group = every shop owner) and did one cache read each, per poll | One small cache entry per thread, one read, no database query |
| Cache was per-worker memory: with 2+ workers typing indicators and chatbot rate limits were unreliable | Optional shared Redis cache (`REDIS_URL`), cache outages are ignored instead of crashing pages |
| Only 2 x 2 = 4 simultaneous requests | Default 3 x 4 = 12 (tunable by env variables), workers recycled every ~1,500 requests to stop slow memory growth |
| Every request logged by gunicorn (floods Railway logs when polling) | Access log off by default (`GUNICORN_ACCESS_LOG=1` to enable), structured app logging to stdout |
| No platform health check | Railway health check on `/health/`; `/health/?deep=1` also checks the database (for uptime monitors) |
| Stale connections after a database restart | `CONN_HEALTH_CHECKS` enabled (dead connections are replaced automatically) |
| Uncompressed HTML/JSON | GZip middleware (static files were already pre-compressed by WhiteNoise) |

Requests per user (approx.): bell 0.33 -> 0.07 per second; open chat 1.67 -> 0.4 per second (**~4-5x less load**).

## 2. Railway setup (once)

1. **Add Redis:** in the Railway project, *New -> Database -> Redis*.
2. In the **web service -> Variables** add:
   - `REDIS_URL` = `${{Redis.REDIS_URL}}` (reference variable; adjust `Redis` to your service name)
   - `WEB_CONCURRENCY` = `3`, `GUNICORN_THREADS` = `4` (defaults, listed for clarity)
3. Give the web service enough resources: **at least 1 vCPU and 1 GB RAM** (3 workers need ~450-750 MB).
4. Redeploy. The deploy is marked healthy only when `/health/` answers.
5. Hard-refresh browsers once (Ctrl+F5). The service worker cache was bumped (v11) so old polling JavaScript is dropped automatically.

Redis is optional: without `REDIS_URL` everything still works, but use a single worker (`WEB_CONCURRENCY=1`, more threads)
if typing indicators must be exact.

## 3. Sizing guide (estimates - verify with the load test)

| Setup | Comfortable concurrent users (online at the same moment) |
|---|---|
| Old defaults (2 workers x 2 threads), old polling | ~30-60 |
| New defaults (3 x 4), new polling, 1-2 vCPU | ~150-300 |
| 4-6 workers x 4 threads, 2 vCPU, 2 GB RAM | ~300-600 |

400 customers usually means roughly 100-250 people online together (shops are not all open at once), so the new defaults fit.
When CPU stays above ~70 % at peak, raise `WEB_CONCURRENCY` (and RAM) or split the load; Postgres and the bucket are not the limit at this size.

## 4. Monitoring

- **Uptime monitor** (UptimeRobot, Better Stack, ...): `https://<domain>/health/?deep=1` every 1-5 min. `200` = web + database OK, `503` = database problem.
- **Railway dashboard:** watch CPU, RAM and restarts of the web service; set alerts for high CPU/RAM.
- **Logs:** errors appear in the deploy logs (`LOG_LEVEL=INFO`, raise to `WARNING` to reduce noise). Enable `GUNICORN_ACCESS_LOG=1` only while debugging.

## 5. Operations checklist

- [ ] Redis attached and `REDIS_URL` set; deploy log shows no cache errors.
- [ ] `/health/` and `/health/?deep=1` return 200.
- [ ] Postgres backups enabled/verified (see `docs/BACKUPS.md`); test a restore at least once.
- [ ] Monthly spend limit / usage alert set in Railway (usage-based billing has no hard cap by default).
- [ ] Alerts on CPU/RAM and an uptime monitor with SMS/e-mail to you.
- [ ] Load test run on staging and the result written down (users, p95, CPU).
- [ ] Single web replica note: `start.sh` runs `migrate` at boot. If you ever run 2+ replicas, move `migrate` into a one-off release step.

## 6. Load test

`loadtest/locustfile.py` simulates logged-in pharmacy users (bell polling, dashboard, POS, products, optional chat polling).
Follow the instructions at the top of the file. Use a **staging** copy and test accounts, never production data.
Increase users in steps (50 -> 100 -> 200 -> 400); capacity is the level just before p95 response time passes ~1 s or errors appear.

## 7. Known limits (be honest with yourself)

- Chat and notifications are **polling based** (efficient now, but not instant). If you later need instant delivery for thousands of users, move to WebSockets/Server-Sent Events with Django Channels.
- SMS sending and PDF/report generation still run inside web requests. If they ever feel slow at scale, move them to a background worker (Celery/RQ + Redis).
- Railway is a shared platform and has had incidents; keep backups off-platform (download owner backups / scheduled DB dumps) for a business-critical system.
