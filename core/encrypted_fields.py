"""Django model field that stores its value AES-256-GCM encrypted (see core.crypto)."""
import logging

from django import forms
from django.core.exceptions import FieldError
from django.db import models
from django.db.models.lookups import Exact, IsNull

from . import crypto

logger = logging.getLogger(__name__)


class _Unreadable(str):
    """Returned when a value cannot be decrypted (e.g. key missing).

    It reads as an empty string, but remembers the original ciphertext so that saving the
    object does NOT overwrite good encrypted data with an empty value.
    """
    ciphertext = ''

    def __new__(cls, ciphertext):
        obj = super().__new__(cls, '')
        obj.ciphertext = ciphertext
        return obj


class _EncryptedExact(Exact):
    """Only ``field=''`` / ``field=None`` can be searched: ciphertext is randomised."""
    def get_prep_lookup(self):
        if self.rhs not in ('', None):
            raise FieldError('Encrypted fields cannot be searched by value.')
        return self.rhs


class EncryptedTextField(models.TextField):
    description = 'Text stored encrypted with AES-256-GCM'

    def contribute_to_class(self, cls, name, **kwargs):
        super().contribute_to_class(cls, name, **kwargs)
        self._crypto_context = f'{cls._meta.label_lower}.{name}'

    @property
    def crypto_context(self):
        return getattr(self, '_crypto_context', f'unbound.{self.name}')

    # -- DB <-> Python ------------------------------------------------------------------
    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        try:
            return crypto.decrypt(value, self.crypto_context)
        except crypto.DecryptionError:
            logger.error('Could not decrypt %s (check FIELD_ENCRYPTION_KEYS).', self.crypto_context)
            return _Unreadable(value)

    def get_prep_value(self, value):
        if isinstance(value, _Unreadable):
            return value.ciphertext or ''
        value = super().get_prep_value(value)
        if value is None:
            return None
        return crypto.encrypt(value, self.crypto_context)

    # -- searching is not possible on random ciphertext ---------------------------------
    def get_lookup(self, lookup_name):
        if lookup_name == 'exact':
            return _EncryptedExact
        if lookup_name == 'isnull':
            return IsNull
        raise FieldError(f'Encrypted field "{self.name}" does not support the "{lookup_name}" lookup.')

    def formfield(self, **kwargs):
        kwargs.setdefault('widget', forms.TextInput)
        return super().formfield(**kwargs)
