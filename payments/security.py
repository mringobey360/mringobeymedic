"""Response hardening for pages that handle payments (V117.11).

* never cached (browser / proxy / back-button cannot replay a payment page)
* strict-origin referrer, no sniffing, powerful browser features switched off
* CSP limited to what a payment page may do: no <object>/<embed>, no <base> hijack, cannot be
  framed by another site, and may only embed the payment gateway's own hosted checkout.
  (script-src is deliberately not restricted here so existing inline scripts keep working.)
"""
from functools import wraps

from django.conf import settings
from django.utils.cache import add_never_cache_headers

DEFAULT_FRAME_SRC = ("'self'", 'https://*.pesapal.com')


def payment_csp():
    frames = ' '.join(getattr(settings, 'PAYMENT_FRAME_SRC', DEFAULT_FRAME_SRC))
    return f"base-uri 'self'; object-src 'none'; frame-ancestors 'none'; frame-src {frames}"


def secure_payment_page(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        add_never_cache_headers(response)
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private, max-age=0'
        response['Pragma'] = 'no-cache'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['Permissions-Policy'] = 'geolocation=(), camera=(), microphone=(), usb=(), bluetooth=()'
        if 'Content-Security-Policy' not in response:
            response['Content-Security-Policy'] = payment_csp()
        return response
    return wrapper
