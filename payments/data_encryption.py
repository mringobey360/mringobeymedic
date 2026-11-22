"""Helpers that (re-)encrypt stored payment data. Used by migration 0007 and the
`encrypt_payment_data` management command (initial run, and key rotation)."""
from django.db import connection

from core import crypto
from core.encrypted_fields import _Unreadable


def _raw_values(model, name):
    """pk -> value exactly as stored in the database (ciphertext or legacy plaintext)."""
    table = connection.ops.quote_name(model._meta.db_table)
    col = connection.ops.quote_name(model._meta.get_field(name).column)
    pk = connection.ops.quote_name(model._meta.pk.column)
    with connection.cursor() as cur:
        cur.execute(f"SELECT {pk}, {col} FROM {table} WHERE {col} IS NOT NULL AND {col} <> ''")
        return cur.fetchall()


def encrypt_model_rows(model, names, dry_run=False):
    """Encrypt plaintext / re-key stale values. Returns {field: rows_changed}."""
    changed = {}
    for name in names:
        count = 0
        for pk, raw in _raw_values(model, name):
            if not crypto.needs_reencryption(raw):
                continue
            count += 1
            if dry_run:
                continue
            value = model.objects.filter(pk=pk).values_list(name, flat=True).first()
            if isinstance(value, _Unreadable) or value in (None, ''):
                continue                      # cannot decrypt with current keys: leave untouched
            model.objects.filter(pk=pk).update(**{name: value})   # field encrypts with primary key
        changed[name] = count
    return changed


def count_plaintext(model, names):
    return {name: sum(1 for _, raw in _raw_values(model, name) if not crypto.is_encrypted(raw)) for name in names}
