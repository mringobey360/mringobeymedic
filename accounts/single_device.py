"""Single-device sign-in: an account can be active on ONE device at a time.

Rules
-----
* Every browser gets a random, HttpOnly device cookie (`smartpos_dev`); only its
  SHA-256 hash is stored in ``ActiveDeviceSession`` (one row per user).
* While an account is active on device A, a sign-in for that account from device B
  (password OR PIN) is refused - even with the correct password - and the attempt is
  audited.
* The lock is released automatically when: the user logs out, the Django session
  expires, the device has been idle for SINGLE_DEVICE_IDLE_RELEASE_MINUTES, a manager
  releases the device, or the user completes a password recovery (SMS OTP).
* Enforcement is server-side on every request (SingleDeviceSessionMiddleware): if a
  session is no longer the one that owns the lock, it is signed out.
* Exempt: platform superusers, Branch Display kiosks, impersonation sessions and
  accounts flagged ``allow_multiple_devices``.

Settings: SINGLE_DEVICE_LOGIN_ENABLED (True), SINGLE_DEVICE_IDLE_RELEASE_MINUTES (15;
0 = release only on logout / session expiry), SINGLE_DEVICE_TOUCH_SECONDS (60).
"""
import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import device_pin
from .models import ActiveDeviceSession, DeviceSessionAudit, User

logger = logging.getLogger(__name__)

COOKIE_NAME = 'smartpos_dev'
COOKIE_DAYS = 365

# Background polling must not count as "the user is using this device", otherwise a
# tab left open overnight would hold the lock forever.
BACKGROUND_PATH_PARTS = (
    '/notifications/api/feed', '/attendance/presence', '/attendance/qr/token',
    '/api/typing', '/health/', '/ready/', '/service-status/',
)


def enabled():
    return getattr(settings, 'SINGLE_DEVICE_LOGIN_ENABLED', True)


def idle_release_minutes():
    return int(getattr(settings, 'SINGLE_DEVICE_IDLE_RELEASE_MINUTES', 15))


def touch_seconds():
    return int(getattr(settings, 'SINGLE_DEVICE_TOUCH_SECONDS', 60))


def applies_to(user):
    """Whether the one-device rule applies to this account."""
    if not enabled() or user is None or not user.is_authenticated:
        return False
    if user.is_superuser or user.is_branch_display_role:
        return False
    return not getattr(user, 'allow_multiple_devices', False)


# --------------------------------------------------------------------------
# Device identity
# --------------------------------------------------------------------------
def _hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def device_token(request):
    """The browser's device token, minting one (to be set on the response) if missing."""
    token = request.COOKIES.get(COOKIE_NAME) or getattr(request, '_smartpos_new_dev_token', '')
    if not token:
        token = secrets.token_urlsafe(32)
        request._smartpos_new_dev_token = token
    return token


def device_hash(request):
    return _hash(device_token(request))


def attach_cookie(request, response):
    """Set/refresh the device cookie on `response` (call on every login response)."""
    response.set_cookie(
        COOKIE_NAME, device_token(request), max_age=COOKIE_DAYS * 24 * 3600,
        httponly=True, secure=not settings.DEBUG, samesite='Lax', path='/',
    )
    return response


# --------------------------------------------------------------------------
# Lock state
# --------------------------------------------------------------------------
def _is_live(lock):
    """True while the lock's session is still valid and the device not idle-released."""
    if lock.session_key and not Session.objects.filter(
            session_key=lock.session_key, expire_date__gt=timezone.now()).exists():
        return False
    minutes = idle_release_minutes()
    if minutes > 0 and timezone.now() - lock.last_seen > timedelta(minutes=minutes):
        return False
    return True


def _audit(user, action, request=None, actor=None, details=None):
    try:
        DeviceSessionAudit.objects.create(
            user=user, actor=actor, action=action, details=details or {},
            ip_address=(device_pin.client_ip(request) or None) if request else None,
            user_agent=(request.META.get('HTTP_USER_AGENT', '')[:500] if request else ''),
        )
    except Exception:
        logger.exception('Could not write device session audit for user_id=%s', getattr(user, 'pk', None))


def check_login_allowed(request, user):
    """Return None if `user` may sign in from this browser, else the blocking lock."""
    if not applies_to_account(user):
        return None
    lock = ActiveDeviceSession.objects.filter(user=user).first()
    if lock is None or lock.device_hash == device_hash(request):
        return None
    if not _is_live(lock):
        return None  # stale: previous device closed / expired / idle -> may take over
    _audit(user, DeviceSessionAudit.Action.BLOCKED, request=request,
           details={'active_device': lock.label, 'attempt_device': device_pin.describe_device(request)})
    return lock


def applies_to_account(user):
    """Like applies_to() but for a not-yet-authenticated user object (login time)."""
    if not enabled() or user is None:
        return False
    if user.is_superuser or user.is_branch_display_role:
        return False
    return not getattr(user, 'allow_multiple_devices', False)


def claim(request, user):
    """Make this browser/session the account's single active device (after login())."""
    if not applies_to_account(user):
        return None
    session_key = request.session.session_key
    if not session_key:
        request.session.save()
        session_key = request.session.session_key
    now = timezone.now()
    values = {
        'device_hash': device_hash(request), 'session_key': session_key or '',
        'label': device_pin.describe_device(request),
        'ip_address': device_pin.client_ip(request) or None, 'last_seen': now,
    }
    try:
        with transaction.atomic():
            lock, created = ActiveDeviceSession.objects.select_for_update().get_or_create(
                user=user, defaults=values)
            if not created:
                if lock.device_hash != values['device_hash']:
                    _audit(user, DeviceSessionAudit.Action.SUPERSEDED, request=request,
                           details={'previous_device': lock.label, 'new_device': values['label']})
                for k, v in values.items():
                    setattr(lock, k, v)
                lock.save()
            return lock
    except IntegrityError:  # concurrent first sign-in; the other request won
        return ActiveDeviceSession.objects.filter(user=user).first()


def release(user, session_key=None):
    """Release the lock (optionally only if it belongs to `session_key`)."""
    qs = ActiveDeviceSession.objects.filter(user=user)
    if session_key is not None:
        qs = qs.filter(session_key=session_key)
    return qs.delete()[0]


def release_for_request(request, user):
    """Release the lock when the signing-out browser/session is the one that owns it."""
    from django.db.models import Q
    cond = Q(session_key=request.session.session_key or '__none__')
    token = request.COOKIES.get(COOKIE_NAME)
    if token:
        cond |= Q(device_hash=_hash(token))
    return ActiveDeviceSession.objects.filter(user=user).filter(cond).delete()[0]


def release_by_manager(user, actor, request=None):
    lock = ActiveDeviceSession.objects.filter(user=user).first()
    if lock is None:
        return False
    _audit(user, DeviceSessionAudit.Action.RELEASED_BY_MANAGER, request=request, actor=actor,
           details={'released_device': lock.label})
    lock.delete()
    return True


def release_by_recovery(user, request=None):
    lock = ActiveDeviceSession.objects.filter(user=user).first()
    if lock is not None:
        _audit(user, DeviceSessionAudit.Action.RELEASED_BY_RECOVERY, request=request,
               details={'released_device': lock.label})
        lock.delete()


def is_background_request(request):
    if request.method != 'GET':
        return False
    return any(part in request.path for part in BACKGROUND_PATH_PARTS)


def blocked_message(lock):
    minutes = idle_release_minutes()
    when = timezone.localtime(lock.last_seen).strftime('%H:%M')
    text = (f"This account is already signed in on another device ({lock.label}, last active {when}). "
            "Only one device can use an account at a time. Sign out on that device first")
    if minutes > 0:
        text += f", wait about {minutes} minutes of inactivity,"
    text += " or ask your manager to release the device."
    return text
