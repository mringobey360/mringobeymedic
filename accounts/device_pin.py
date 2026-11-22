"""PIN sign-in for trusted devices.

Rules
-----
* Every user (except Branch Display kiosks) has ONE PIN. It is mandatory: after
  a password sign-in a user without a PIN is sent to the "Create your PIN"
  page (see ForcePinSetupMiddleware) and cannot use the app until it is set.
* A successful password sign-in remembers the browser (HttpOnly cookie holding a
  random token; only its hash is stored). After logout the login page shows the
  PIN pad for that user. "Use another account" opens the password form.
* PIN is only accepted together with the cookie. 5 wrong PINs lock it; the user
  then signs in with the password and creates a new PIN.
* Reset paths: "Forgot PIN?" on the pad (password sign-in, then new PIN),
  forgot-password OTP flow (clears PIN), manager "Reset PIN" for staff.
"""
import hashlib
import logging
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from .models import TrustedDevice, User, UserPin, PinSecurityAudit

logger = logging.getLogger(__name__)

COOKIE_NAME = 'smartpos_device'
PIN_LENGTH = getattr(settings, 'DEVICE_PIN_LENGTH', 4)
COOKIE_DAYS = getattr(settings, 'DEVICE_PIN_COOKIE_DAYS', 90)
IDLE_DAYS = getattr(settings, 'DEVICE_PIN_IDLE_DAYS', 30)
IDLE_LOCK_MINUTES = getattr(settings, 'DEVICE_PIN_IDLE_LOCK_MINUTES', 5)
IP_RATE_LIMIT_ATTEMPTS = getattr(settings, 'DEVICE_PIN_IP_RATE_LIMIT_ATTEMPTS', 12)
IP_RATE_LIMIT_WINDOW_SECONDS = getattr(settings, 'DEVICE_PIN_IP_RATE_LIMIT_WINDOW_SECONDS', 60)
MAX_DEVICES_PER_USER = getattr(settings, 'DEVICE_PIN_MAX_DEVICES', 5)
ALLOWED_ROLES = {User.Role.ADMIN, User.Role.ASSISTANT, User.Role.EMPLOYEE}

_WEAK_PINS = {
    '0000', '1111', '2222', '3333', '4444', '5555', '6666', '7777', '8888', '9999',
    '1234', '4321', '0123', '3210', '2345', '5432', '3456', '6543', '4567', '7654',
    '5678', '8765', '6789', '9876', '1357', '7531', '2468', '8642', '2580', '0852',
    '1212', '2121', '1122', '2211', '1221', '2112', '1000', '2000', '3000', '4000',
    '5000', '6000', '7000', '8000', '9000',
}


def pin_required():
    """Global switch (settings.DEVICE_PIN_REQUIRED, default True)."""
    return getattr(settings, 'DEVICE_PIN_REQUIRED', True)


def hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def role_allowed(user):
    return user.role in ALLOWED_ROLES


def validate_pin(pin):
    """Return an error message for an unacceptable PIN, or None if it is fine."""
    if not re.fullmatch(r'\d{%d}' % PIN_LENGTH, pin or ''):
        return f'PIN must be exactly {PIN_LENGTH} digits.'
    if pin in _WEAK_PINS:
        return 'That PIN is too easy to guess. Choose a less obvious one.'
    return None


def describe_device(request):
    """Short human label such as 'Chrome on Windows' from the User-Agent."""
    ua = request.META.get('HTTP_USER_AGENT', '')
    browser = 'Browser'
    for name, pattern in (('Edge', r'Edg/'), ('Opera', r'OPR/'), ('Chrome', r'Chrome/'),
                          ('Firefox', r'Firefox/'), ('Safari', r'Safari/')):
        if re.search(pattern, ua):
            browser = name
            break
    system = 'unknown device'
    for name, pattern in (('Android', r'Android'), ('iPhone', r'iPhone'), ('iPad', r'iPad'),
                          ('Windows', r'Windows'), ('Mac', r'Macintosh|Mac OS'), ('Linux', r'Linux')):
        if re.search(pattern, ua):
            system = name
            break
    return f'{browser} on {system}'[:120]


# --------------------------------------------------------------------------
# PIN state
# --------------------------------------------------------------------------
def get_pin(user):
    return UserPin.objects.filter(user=user).first()


def needs_pin_setup(user):
    """True when the user must create (or re-create) a PIN before using the app."""
    if not pin_required() or not user.is_authenticated:
        return False
    if not role_allowed(user) or user.must_change_password:
        return False
    pin = get_pin(user)
    return pin is None or pin.is_locked


def set_pin(user, pin):
    """Create or replace the user's PIN (clears any lock)."""
    UserPin.objects.update_or_create(
        user=user,
        defaults={'pin_hash': make_password(pin), 'failed_attempts': 0, 'locked_at': None, 'locked_until': None},
    )


def require_reset(user):
    """Lock the PIN so the user must create a new one right after signing in."""
    UserPin.objects.filter(user=user).update(locked_at=timezone.now(), locked_until=None)


def reset_pin(user):
    """Remove the PIN and every trusted device. The user must create a new PIN at next sign-in."""
    UserPin.objects.filter(user=user).delete()
    user.trusted_devices.all().delete()


def verify_pin(pin_obj, pin):
    """Check a PIN with progressive lockout.

    Wrong attempt 3 => 30-second cooldown. Wrong attempt 5 => 5-minute
    cooldown. A further wrong attempt after the 5-minute cooldown permanently
    locks the PIN and forces password + PIN setup.
    Returns a small state dictionary so the UI can explain the cooldown.
    """
    now = timezone.now()
    if pin_obj.is_locked:
        return {'ok': False, 'locked': True, 'cooldown': False, 'attempts_left': 0, 'retry_after': 0}

    if pin_obj.locked_until and pin_obj.locked_until > now:
        retry_after = max(1, int((pin_obj.locked_until - now).total_seconds()))
        return {'ok': False, 'locked': False, 'cooldown': True, 'attempts_left': max(0, UserPin.MAX_FAILED_ATTEMPTS - pin_obj.failed_attempts), 'retry_after': retry_after}

    if check_password(pin or '', pin_obj.pin_hash):
        if pin_obj.failed_attempts or pin_obj.locked_until:
            pin_obj.failed_attempts = 0
            pin_obj.locked_until = None
            pin_obj.save(update_fields=['failed_attempts', 'locked_until', 'updated_at'])
        return {'ok': True, 'locked': False, 'cooldown': False, 'attempts_left': UserPin.MAX_FAILED_ATTEMPTS, 'retry_after': 0}

    pin_obj.failed_attempts += 1
    fields = ['failed_attempts', 'updated_at']
    retry_after = 0
    if pin_obj.failed_attempts == 3:
        pin_obj.locked_until = now + timedelta(seconds=30)
        retry_after = 30
        fields.append('locked_until')
    elif pin_obj.failed_attempts == UserPin.MAX_FAILED_ATTEMPTS:
        pin_obj.locked_until = now + timedelta(minutes=5)
        retry_after = 5 * 60
        fields.append('locked_until')
    elif pin_obj.failed_attempts > UserPin.MAX_FAILED_ATTEMPTS:
        pin_obj.locked_at = now
        pin_obj.locked_until = None
        fields.extend(['locked_at', 'locked_until'])
        try:
            PinSecurityAudit.objects.create(
                user_id=pin_obj.user_id,
                action=PinSecurityAudit.Action.LOCKED,
                details={'failed_attempts': pin_obj.failed_attempts, 'reason': 'progressive_lockout'},
            )
        except Exception:
            logger.exception('Could not write PIN lock audit for user_id=%s', pin_obj.user_id)
        logger.warning('PIN permanently locked after progressive lockout for user_id=%s', pin_obj.user_id)

    pin_obj.save(update_fields=fields)
    return {
        'ok': False, 'locked': pin_obj.is_locked, 'cooldown': retry_after > 0,
        'attempts_left': max(0, UserPin.MAX_FAILED_ATTEMPTS - pin_obj.failed_attempts),
        'retry_after': retry_after,
    }

def client_ip(request):
    """Return the peer IP. Do not trust X-Forwarded-For unless explicitly configured."""
    return (request.META.get('REMOTE_ADDR') or '').strip()[:64]

def check_ip_rate_limit(request):
    """Return seconds to wait when this IP is sending too many PIN requests."""
    ip = client_ip(request) or 'unknown'
    key = 'smartpos:pin-rate:%s' % hashlib.sha256(ip.encode('utf-8')).hexdigest()
    count = cache.get(key, 0)
    if count >= IP_RATE_LIMIT_ATTEMPTS:
        ttl = cache.ttl(key) if hasattr(cache, 'ttl') else None
        return max(1, int(ttl or IP_RATE_LIMIT_WINDOW_SECONDS))
    try:
        cache.add(key, 0, IP_RATE_LIMIT_WINDOW_SECONDS)
        count = cache.incr(key)
    except Exception:
        count = int(cache.get(key, 0) or 0) + 1
        cache.set(key, count, IP_RATE_LIMIT_WINDOW_SECONDS)
    if count > IP_RATE_LIMIT_ATTEMPTS:
        ttl = cache.ttl(key) if hasattr(cache, 'ttl') else None
        return max(1, int(ttl or IP_RATE_LIMIT_WINDOW_SECONDS))
    return 0

def lock_session(request):
    request.session['device_pin_locked'] = True
    request.session['device_pin_last_activity'] = int(timezone.now().timestamp())

def unlock_session(request):
    request.session.pop('device_pin_locked', None)
    request.session['device_pin_last_activity'] = int(timezone.now().timestamp())
    request.session.modified = True

def mark_activity(request):
    request.session['device_pin_last_activity'] = int(timezone.now().timestamp())

def session_idle_seconds(request):
    stamp = request.session.get('device_pin_last_activity')
    if not stamp:
        return 0
    return max(0, int(timezone.now().timestamp()) - int(stamp))


# --------------------------------------------------------------------------
# Trusted device (cookie)
# --------------------------------------------------------------------------
def _cookie_token(request):
    return request.COOKIES.get(COOKIE_NAME) or ''


def current_device(request):
    token = _cookie_token(request)
    if not token:
        return None
    return TrustedDevice.objects.filter(token_hash=hash_token(token)).select_related('user').first()


def get_device(request):
    """Return the TrustedDevice that may show the PIN pad for this browser, else None."""
    device = current_device(request)
    if device is None:
        return None
    user = device.user
    if not user.is_active or not role_allowed(user) or user.must_change_password:
        return None
    pin = get_pin(user)
    if pin is None or pin.is_locked:
        return None
    last_seen = device.last_used_at or device.created_at
    if timezone.now() - last_seen > timedelta(days=IDLE_DAYS):
        return None
    return device


def set_device_cookie(response, raw_token):
    response.set_cookie(
        COOKIE_NAME, raw_token, max_age=COOKIE_DAYS * 24 * 3600,
        httponly=True, secure=not settings.DEBUG, samesite='Lax', path='/',
    )


def clear_device_cookie(response):
    response.delete_cookie(COOKIE_NAME, path='/', samesite='Lax')


def trust_device(request, user, response):
    """Remember this browser for `user` so the login page shows the PIN pad next time."""
    if not pin_required() or not role_allowed(user):
        return None
    now = timezone.now()
    existing = current_device(request)
    if existing is not None and existing.user_id == user.pk:
        existing.last_used_at = now
        existing.label = describe_device(request)
        existing.save(update_fields=['last_used_at', 'label'])
        set_device_cookie(response, _cookie_token(request))  # refresh cookie lifetime
        return existing
    if existing is not None:
        existing.delete()  # the cookie is being replaced; this record would be unreachable

    # Keep the number of remembered devices bounded: drop the oldest ones.
    for stale in list(user.trusted_devices.order_by('-last_used_at', '-created_at'))[MAX_DEVICES_PER_USER - 1:]:
        stale.delete()
    raw_token = secrets.token_urlsafe(32)
    device = TrustedDevice.objects.create(
        user=user, token_hash=hash_token(raw_token), label=describe_device(request), last_used_at=now,
    )
    set_device_cookie(response, raw_token)
    return device


def trust_device_if_pin_ready(request, user, response):
    """After a password sign-in: remember the browser if the user already has a usable PIN.

    Users who still have to create a PIN are remembered right after they create it.
    """
    pin = get_pin(user)
    if pin is not None and not pin.is_locked:
        return trust_device(request, user, response)
    return None


def revoke_devices(user, keep=None):
    """Forget the user's remembered browsers (optionally keeping one)."""
    qs = user.trusted_devices.all()
    if keep is not None:
        qs = qs.exclude(pk=keep.pk)
    qs.delete()

def forget_current_device(request):
    """Delete whichever remembered browser is represented by this request cookie."""
    device = current_device(request)
    if device is not None:
        device.delete()
    return device
