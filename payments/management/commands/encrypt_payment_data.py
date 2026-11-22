"""Encrypt (or re-key) every sensitive payment column.

    python manage.py encrypt_payment_data --dry-run   # report only
    python manage.py encrypt_payment_data             # encrypt plaintext + move old-key rows to the primary key

Run it after changing FIELD_ENCRYPTION_KEYS (put the NEW key first, keep the old one after it
until this command has finished, then remove the old key). Safe to run repeatedly.
"""
from django.core.management.base import BaseCommand

from core import crypto
from payments.data_encryption import count_plaintext, encrypt_model_rows
from payments.models import PaymentGatewaySettings, PesapalSettings, PesapalTransaction

TARGETS = (
    (PaymentGatewaySettings, ['abliner_api_key', 'abliner_webhook_secret']),
    (PesapalSettings, ['consumer_key', 'consumer_secret', 'cached_token']),
    (PesapalTransaction, ['payer_name', 'payer_email', 'payer_phone', 'payment_account', 'raw_response']),
)


class Command(BaseCommand):
    help = 'Encrypt stored payment data with the primary FIELD_ENCRYPTION_KEYS key.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only report; change nothing.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        if not crypto.uses_dedicated_key():
            self.stdout.write(self.style.WARNING(
                'FIELD_ENCRYPTION_KEYS is not set: using a key derived from SECRET_KEY. '
                'Run `generate_field_key`, set the variable, then run this command again.'))
        total = 0
        for model, names in TARGETS:
            changed = encrypt_model_rows(model, names, dry_run=dry)
            for name, n in changed.items():
                total += n
                self.stdout.write(f'{model.__name__}.{name}: {n} row(s) {"to encrypt" if dry else "encrypted"}')
        if not dry:
            left = sum(sum(count_plaintext(m, n).values()) for m, n in TARGETS)
            self.stdout.write(self.style.SUCCESS(f'Done. {total} value(s) protected, {left} plaintext value(s) remaining.'))
