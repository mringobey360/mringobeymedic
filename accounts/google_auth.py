"""Google Sign-In (Google Identity Services) for SmartPOS shop owners.

Security model
--------------
* The browser gets a Google **ID token** (a signed JWT) from the Google button and posts it to us.
  We never trust the browser's word for who the user is: the token's RS256 signature is verified
  against Google's published public keys (JWKS), and these claims are checked:
  issuer, audience (our client id), expiry, ``email_verified`` and the one-time **nonce** that
  we put in the user's session when the page was rendered (stops token replay / injection).
* No password is ever sent to or stored for Google. No extra pip package is needed:
  verification uses ``cryptography`` and ``requests``, which are already in requirements.txt.
* Google sign-in is for Shop Owner accounts only. Platform superadmins, staff and employees keep
  their password / PIN rules, so a Google account can never reach the HQ or an employee login.
* Enabled only when ``GOOGLE_CLIENT_ID`` is configured; otherwise the button is simply hidden.
"""
import base64
import json
import logging
import secrets
import time

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

GOOGLE_JWKS_URL = 'https://www.googleapis.com/oauth2/v3/certs'
GOOGLE_ISSUERS = ('accounts.google.com', 'https://accounts.google.com')
NONCE_SESSION_KEY = 'google_nonce'
SIGNUP_SESSION_KEY = 'google_signup'
SIGNUP_TTL_SECONDS = 30 * 60
_JWKS_CACHE_KEY = 'google:jwks:v1'
_CLOCK_SKEW = 60


class GoogleAuthError(Exception):
    """The Google token could not be trusted. ``str(exc)`` is safe to show to the visitor."""


def client_id():
    return (getattr(settings, 'GOOGLE_CLIENT_ID', '') or '').strip()


def enabled():
    return bool(client_id())


def _b64url(data):
    return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4))


def _load_jwks(force=False):
    if not force:
        cached = cache.get(_JWKS_CACHE_KEY)
        if cached:
            return cached
    try:
        resp = requests.get(GOOGLE_JWKS_URL, timeout=6)
        resp.raise_for_status()
        keys = resp.json().get('keys', [])
    except Exception as exc:  # noqa: BLE001
        logger.warning('Could not download Google signing keys: %s', exc)
        raise GoogleAuthError('Google sign-in is temporarily unavailable. Please try again or use your username.') from exc
    if keys:
        cache.set(_JWKS_CACHE_KEY, keys, 3600)
    return keys


def _public_key(kid):
    for force in (False, True):    # a key we do not know yet: refresh once (Google rotates keys)
        for jwk in _load_jwks(force=force):
            if jwk.get('kid') == kid and jwk.get('kty') == 'RSA':
                n = int.from_bytes(_b64url(jwk['n']), 'big')
                e = int.from_bytes(_b64url(jwk['e']), 'big')
                return rsa.RSAPublicNumbers(e, n).public_key()
    raise GoogleAuthError('Google sign-in could not be verified. Please try again.')


def verify_id_token(token, *, expected_nonce):
    """Return the verified claims dict, or raise GoogleAuthError."""
    cid = client_id()
    if not cid:
        raise GoogleAuthError('Google sign-in is not set up yet.')
    if not isinstance(token, str) or token.count('.') != 2 or len(token) > 8192:
        raise GoogleAuthError('Invalid Google response.')
    header_b64, payload_b64, sig_b64 = token.split('.')
    try:
        header = json.loads(_b64url(header_b64))
        claims = json.loads(_b64url(payload_b64))
        signature = _b64url(sig_b64)
    except Exception as exc:  # noqa: BLE001
        raise GoogleAuthError('Invalid Google response.') from exc
    if header.get('alg') != 'RS256' or not header.get('kid'):
        raise GoogleAuthError('Invalid Google response.')

    key = _public_key(header['kid'])
    try:
        key.verify(signature, f'{header_b64}.{payload_b64}'.encode('ascii'), padding.PKCS1v15(), hashes.SHA256())
    except Exception as exc:  # noqa: BLE001 - InvalidSignature and malformed input alike
        raise GoogleAuthError('Google sign-in could not be verified.') from exc

    now = time.time()
    if claims.get('iss') not in GOOGLE_ISSUERS:
        raise GoogleAuthError('Google sign-in could not be verified.')
    aud = claims.get('aud')
    if aud != cid and not (isinstance(aud, list) and cid in aud):
        raise GoogleAuthError('Google sign-in could not be verified.')
    if float(claims.get('exp', 0)) < now - _CLOCK_SKEW:
        raise GoogleAuthError('This Google sign-in expired. Please try again.')
    if float(claims.get('iat', 0)) > now + _CLOCK_SKEW:
        raise GoogleAuthError('Google sign-in could not be verified.')
    if not expected_nonce or not secrets.compare_digest(str(claims.get('nonce', '')), str(expected_nonce)):
        raise GoogleAuthError('This sign-in page expired. Refresh the page and try again.')
    if not claims.get('sub') or not claims.get('email'):
        raise GoogleAuthError('Your Google account did not share an email address.')
    if claims.get('email_verified') not in (True, 'true'):
        raise GoogleAuthError('Your Google email address is not verified.')
    return claims


def new_nonce(request):
    """One nonce per rendered page, kept in the session and checked when the token comes back."""
    nonce = secrets.token_urlsafe(24)
    request.session[NONCE_SESSION_KEY] = nonce
    return nonce


def pop_nonce(request):
    return request.session.pop(NONCE_SESSION_KEY, None)


def template_context(request):
    """Context for partials/google_button.html (empty when Google sign-in is not configured)."""
    if not enabled():
        return {'google_enabled': False}
    return {'google_enabled': True, 'google_client_id': client_id(), 'google_nonce': new_nonce(request)}


# --------------------------------------------------------------------------------------
# Google profile picture -> SmartPOS profile photo
# --------------------------------------------------------------------------------------
PHOTO_MAX_BYTES = 3 * 1024 * 1024
PHOTO_TIMEOUT_SECONDS = 4
_PHOTO_HOST_SUFFIXES = ('.googleusercontent.com', '.ggpht.com')


def _safe_picture_url(url):
    """Only https URLs on Google's own image hosts are fetched (prevents SSRF via a crafted claim)."""
    from urllib.parse import urlparse
    if not isinstance(url, str) or len(url) > 1000:
        return ''
    parts = urlparse(url)
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or not host.endswith(_PHOTO_HOST_SUFFIXES):
        return ''
    # Google serves any size: ask for a sharp 400px square instead of the tiny default (=s96-c).
    import re
    return re.sub(r'=s\d+(-c)?$', '=s400-c', url)


def sync_profile_photo(user, claims):
    """Use the Google account picture as the profile photo, but never replace a photo the user already has.

    Best effort: any failure (offline, timeout, odd file) is logged and ignored, so it can never block sign-in.
    Returns True when a photo was saved.
    """
    try:
        if getattr(user, 'profile_photo', None):
            return False
        url = _safe_picture_url(claims.get('picture'))
        if not url:
            return False
        resp = requests.get(url, timeout=PHOTO_TIMEOUT_SECONDS, stream=True, allow_redirects=False)
        if resp.status_code != 200 or not str(resp.headers.get('Content-Type', '')).startswith('image/'):
            return False
        data = b''
        for chunk in resp.iter_content(64 * 1024):
            data += chunk
            if len(data) > PHOTO_MAX_BYTES:
                return False
        if not data:
            return False
        from django.core.files.base import ContentFile
        user.profile_photo.save(f'google_{user.pk}.jpg', ContentFile(data), save=False)   # compressed by core.image_signals
        user.save(update_fields=['profile_photo'])
        return True
    except Exception:  # noqa: BLE001 - cosmetic feature, never block login
        logger.warning('Google profile photo could not be saved for user %s', getattr(user, 'pk', '?'), exc_info=True)
        return False
