"""'Continue with Google' endpoint used by the login page and the registration page."""
import json
import logging

from django.contrib.auth import login
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST

from core import ratelimit

from . import google_auth, single_device
from .models import User

logger = logging.getLogger(__name__)

GOOGLE_MAX_PER_IP = 30          # attempts per window (each is a signed token, so this is generous)
GOOGLE_WINDOW_SECONDS = 15 * 60
ModelBackend = 'django.contrib.auth.backends.ModelBackend'


def _fail(message, status=400, **extra):
    return JsonResponse({'ok': False, 'error': message, **extra}, status=status)


def find_owner(claims):
    """The Shop Owner account for this verified Google identity, linking it by email on first use."""
    sub, email = claims['sub'], claims['email'].strip().lower()
    user = User.objects.filter(google_sub=sub).first()
    if user is not None:
        return user
    candidates = list(User.objects.filter(email__iexact=email, role=User.Role.ADMIN, is_superuser=False, is_staff=False)[:2])
    if len(candidates) == 1 and not candidates[0].google_sub:
        user = candidates[0]
        user.google_sub = sub
        user.save(update_fields=['google_sub'])
        return user
    # Owners whose registration email was never copied onto the user record: match their signup email.
    from hq.models import SignupRequest
    owners = {s.created_owner for s in SignupRequest.objects.filter(email__iexact=email, created_owner__isnull=False)
              .select_related('created_owner')[:3]}
    owners = [o for o in owners if o.role == User.Role.ADMIN and not o.is_superuser and not o.is_staff and not o.google_sub]
    if len(owners) == 1:
        user = owners[0]
        user.google_sub = sub
        user.save(update_fields=['google_sub'])
        return user
    return None


def start_session(request, user, response_factory):
    """Log the owner in exactly like the password form does (single-device rule, 30-day session)."""
    login(request, user, backend=ModelBackend)
    single_device.claim(request, user)
    request.session.set_expiry(60 * 60 * 24 * 30)
    request.session['device_pin_idle_enabled'] = False
    response = response_factory()
    single_device.attach_cookie(request, response)
    return response


@require_POST
def google_auth_view(request):
    """POST {credential}: sign an existing owner in, or remember the verified Google identity for registration."""
    if not google_auth.enabled():
        return _fail('Google sign-in is not available yet.', 404)
    ip = ratelimit.client_ip(request)
    allowed, wait = ratelimit.hit('google-auth', ip, limit=GOOGLE_MAX_PER_IP, window=GOOGLE_WINDOW_SECONDS)
    if not allowed:
        return _fail(f'Too many attempts. Please wait {ratelimit.human_wait(wait)} and try again.', 429)
    try:
        credential = json.loads(request.body.decode('utf-8') or '{}').get('credential', '')
    except (ValueError, UnicodeDecodeError):
        return _fail('Invalid request.')

    nonce = google_auth.pop_nonce(request)     # single use, whatever the outcome
    try:
        claims = google_auth.verify_id_token(credential, expected_nonce=nonce)
    except google_auth.GoogleAuthError as exc:
        return _fail(str(exc), 401)

    user = find_owner(claims)
    if user is None:
        # New to SmartPOS: keep the verified identity for the registration form (30 minutes, this browser only).
        import time
        request.session[google_auth.SIGNUP_SESSION_KEY] = {
            'sub': claims['sub'], 'email': claims['email'].strip().lower(),
            'first_name': (claims.get('given_name') or '')[:100], 'last_name': (claims.get('family_name') or '')[:100],
            'name': (claims.get('name') or '')[:150], 'picture': (claims.get('picture') or '')[:1000], 'ts': int(time.time()),
        }
        return JsonResponse({'ok': True, 'new': True, 'redirect': reverse('hq:signup') + '?google=1'})

    if not user.is_active:
        return _fail('This account has been deactivated. Contact support.', 403)
    if user.role != User.Role.ADMIN or user.is_superuser or user.is_staff:
        return _fail('Google sign-in is for shop owners. Please use your username and password or PIN.', 403)
    blocking = single_device.check_login_allowed(request, user)
    if blocking is not None:
        return _fail(single_device.blocked_message(blocking), 403)

    google_auth.sync_profile_photo(user, claims)   # fills the profile photo from Google if the owner has none
    from .views import _post_login_url
    target = _post_login_url(user)
    return start_session(request, user, lambda: JsonResponse({'ok': True, 'new': False, 'redirect': target}))


def pending_google_signup(request):
    """The verified Google identity waiting in this session for the registration form, or None."""
    import time
    data = request.session.get(google_auth.SIGNUP_SESSION_KEY)
    if not data or time.time() - data.get('ts', 0) > google_auth.SIGNUP_TTL_SECONDS:
        request.session.pop(google_auth.SIGNUP_SESSION_KEY, None)
        return None
    if User.objects.filter(google_sub=data['sub']).exists():   # registered meanwhile
        request.session.pop(google_auth.SIGNUP_SESSION_KEY, None)
        return None
    return data


def _owner_only(request):
    u = request.user
    return u.is_authenticated and u.role == User.Role.ADMIN and not u.is_superuser and not u.is_staff


@require_POST
def google_link_view(request):
    """Signed-in Shop Owner connects a Google account to THIS account (identity proven by the verified token)."""
    if not google_auth.enabled():
        return _fail('Google sign-in is not available yet.', 404)
    if not _owner_only(request):
        return _fail('Only shop owners can connect a Google account.', 403)
    allowed, wait = ratelimit.hit('google-link', request.user.pk, limit=10, window=GOOGLE_WINDOW_SECONDS)
    if not allowed:
        return _fail(f'Too many attempts. Please wait {ratelimit.human_wait(wait)}.', 429)
    try:
        credential = json.loads(request.body.decode('utf-8') or '{}').get('credential', '')
    except (ValueError, UnicodeDecodeError):
        return _fail('Invalid request.')
    nonce = google_auth.pop_nonce(request)
    try:
        claims = google_auth.verify_id_token(credential, expected_nonce=nonce)
    except google_auth.GoogleAuthError as exc:
        return _fail(str(exc), 401)
    if User.objects.filter(google_sub=claims['sub']).exclude(pk=request.user.pk).exists():
        return _fail('This Google account is already connected to another SmartPOS account.', 409)
    user = request.user
    user.google_sub = claims['sub']
    fields = ['google_sub']
    if not user.email:
        user.email = claims['email'].strip().lower()
        fields.append('email')
    user.save(update_fields=fields)
    google_auth.sync_profile_photo(user, claims)
    return JsonResponse({'ok': True, 'redirect': reverse('accounts:profile') + '?google=linked'})


@require_POST
def google_unlink_view(request):
    """Disconnect Google. Only allowed while a password exists, so the owner can never lock themselves out."""
    if not _owner_only(request):
        return _fail('Only shop owners can do this.', 403)
    user = request.user
    if not user.has_usable_password():
        return _fail('Set a password first, then you can disconnect Google.', 400)
    user.google_sub = None
    user.save(update_fields=['google_sub'])
    return JsonResponse({'ok': True, 'redirect': reverse('accounts:profile') + '?google=unlinked'})
