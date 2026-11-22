"""Small cache-backed rate limiter shared by login, payments and customer-data views.

Design notes
------------
* Uses Django's cache. With REDIS_URL set the counters are shared by every gunicorn
  worker; without it each worker counts on its own (still useful, just weaker).
* Fails OPEN on cache errors: a cache outage must never lock the business out of its
  own POS. The other layers (PIN lockout, signed webhooks) keep working.
* Keys are hashed so usernames / phone numbers never appear in cache keys or logs.
"""
import hashlib
import re
import time

from django.core.cache import cache

_IP_RE = re.compile(r'^[0-9a-fA-F:.]{3,45}$')


def client_ip(request):
    """Best-effort client IP behind Railway's proxy (X-Real-IP, then last X-Forwarded-For hop)."""
    real = (request.META.get('HTTP_X_REAL_IP') or '').strip()
    if real and _IP_RE.match(real):
        return real
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')
    last = forwarded[-1].strip() if forwarded else ''
    if last and _IP_RE.match(last):
        return last
    return (request.META.get('REMOTE_ADDR') or 'unknown')[:45]


def _key(scope, ident):
    digest = hashlib.sha256(str(ident).lower().encode('utf-8')).hexdigest()[:32]
    return f'rl:{scope}:{digest}'


def hit(scope, ident, *, limit, window):
    """Count one event. Returns (allowed, retry_after_seconds)."""
    key = _key(scope, ident)
    now = time.time()
    try:
        data = cache.get(key)
        if not data or data[1] <= now:
            data = [0, now + window]
        data[0] += 1
        cache.set(key, data, timeout=max(1, int(data[1] - now)))
    except Exception:  # noqa: BLE001 - fail open
        return True, 0
    if data[0] > limit:
        return False, max(1, int(data[1] - now))
    return True, 0


def peek(scope, ident, *, limit):
    """Is this identity already over the limit? (does not count). Returns (blocked, retry_after)."""
    try:
        data = cache.get(_key(scope, ident))
    except Exception:  # noqa: BLE001
        return False, 0
    now = time.time()
    if data and data[1] > now and data[0] >= limit:
        return True, max(1, int(data[1] - now))
    return False, 0


def reset(scope, ident):
    try:
        cache.delete(_key(scope, ident))
    except Exception:  # noqa: BLE001
        pass


def human_wait(seconds):
    minutes = max(1, (int(seconds) + 59) // 60)
    return f'{minutes} minute' + ('' if minutes == 1 else 's')
