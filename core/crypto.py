"""Authenticated field-level encryption for sensitive payment data (V117.11).

Algorithm : AES-256-GCM (authenticated: tampering is detected, not just hidden).
Format    : ``enc1$<key id>$<base64url(nonce || ciphertext || tag)>``
Context   : every value is bound to the model field it belongs to (GCM "associated data"),
            so a ciphertext copied from one column/table into another will NOT decrypt.
Keys      : ``FIELD_ENCRYPTION_KEYS`` (env, comma separated, 32 random bytes each, base64).
            The FIRST key encrypts; ALL keys can decrypt -> zero-downtime key rotation.
            A key derived from SECRET_KEY is always kept as a decrypt-only fallback so rows
            written before a dedicated key was configured can still be read.
Legacy    : a value that does not start with ``enc1$`` is treated as old plaintext and is
            returned unchanged (so the upgrade can never make existing rows unreadable).

Generate a key with:  python manage.py generate_field_key
"""
import base64
import hashlib
import logging
import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

logger = logging.getLogger(__name__)

PREFIX = 'enc1$'
_NONCE_BYTES = 12


class EncryptionConfigError(RuntimeError):
    """The key configuration is invalid (wrong length / not base64)."""


class DecryptionError(Exception):
    """A stored value could not be decrypted (wrong key, wrong field, or tampered data)."""


def generate_key():
    """A fresh random 256-bit key, base64url encoded (put it in FIELD_ENCRYPTION_KEYS)."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode('ascii')


def _decode_key(text):
    raw = (text or '').strip()
    try:
        key = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))
    except Exception as exc:  # noqa: BLE001
        raise EncryptionConfigError('FIELD_ENCRYPTION_KEYS contains a value that is not base64.') from exc
    if len(key) != 32:
        raise EncryptionConfigError('Each FIELD_ENCRYPTION_KEYS entry must be exactly 32 bytes (use generate_field_key).')
    return key


def _key_id(key):
    return hashlib.sha256(b'kid:' + key).hexdigest()[:8]


def _derived_key(secret_key):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b'smartpos-field-encryption-v1').derive(secret_key.encode('utf-8'))


@lru_cache(maxsize=8)
def _keyring(configured, secret_key):
    """Returns (primary_kid, {kid: AESGCM}). Cached per (configured keys, SECRET_KEY)."""
    keys = [_decode_key(k) for k in configured if (k or '').strip()]
    fallback = _derived_key(secret_key)
    ordered = keys + [fallback]
    ring = {}
    for key in ordered:
        ring.setdefault(_key_id(key), AESGCM(key))
    return _key_id(ordered[0]), ring


def _current():
    configured = tuple(getattr(settings, 'FIELD_ENCRYPTION_KEYS', ()) or ())
    return _keyring(configured, settings.SECRET_KEY)


def reset_keyring():
    _keyring.cache_clear()


def uses_dedicated_key():
    return any((k or '').strip() for k in (getattr(settings, 'FIELD_ENCRYPTION_KEYS', ()) or ()))


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(plaintext, context):
    """Encrypt text. ``None`` and ``''`` are stored as-is (so "is empty" checks keep working)."""
    if plaintext is None or plaintext == '':
        return plaintext
    if is_encrypted(plaintext):
        return plaintext                      # already protected: never double-encrypt
    kid, ring = _current()
    nonce = os.urandom(_NONCE_BYTES)
    blob = ring[kid].encrypt(nonce, str(plaintext).encode('utf-8'), context.encode('utf-8'))
    return f'{PREFIX}{kid}${base64.urlsafe_b64encode(nonce + blob).decode("ascii")}'


def decrypt(stored, context):
    """Decrypt a stored value. Legacy plaintext is returned unchanged."""
    if stored is None or not is_encrypted(stored):
        return stored
    try:
        _, kid, payload = stored.split('$', 2)
        _, ring = _current()
        aes = ring.get(kid)
        if aes is None:
            raise DecryptionError('unknown key id')
        raw = base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))
        return aes.decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], context.encode('utf-8')).decode('utf-8')
    except DecryptionError:
        raise
    except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
        raise DecryptionError('wrong key, wrong field or tampered data') from exc


def needs_reencryption(stored):
    """True when a value is plaintext or encrypted with a key that is no longer the primary."""
    if not stored:
        return False
    if not is_encrypted(stored):
        return True
    kid, _ = _current()
    return stored.split('$', 2)[1] != kid
