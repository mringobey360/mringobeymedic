import base64
import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

def _public_key():
    return getattr(settings, "WEBPUSH_VAPID_PUBLIC_KEY", "").strip()

def push_is_configured():
    return bool(_public_key() and getattr(settings, "WEBPUSH_VAPID_PRIVATE_KEY", "").strip() and getattr(settings, "WEBPUSH_VAPID_CLAIMS_EMAIL", "").strip())

def send_web_push(subscription, title, message, url="/notifications/", tag="pharmacy-alert"):
    if not push_is_configured():
        return False, "Web Push is not configured."
    try:
        from pywebpush import webpush, WebPushException
        data = json.dumps({"title": title, "body": message, "url": url, "tag": tag or "pharmacy-alert"})
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=data,
            vapid_private_key=settings.WEBPUSH_VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.WEBPUSH_VAPID_CLAIMS_EMAIL},
            ttl=3600,
        )
        return True, ""
    except Exception as exc:
        return False, str(exc)[:1000]
