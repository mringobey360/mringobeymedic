"""Print a new random 256-bit key for FIELD_ENCRYPTION_KEYS."""
from django.core.management.base import BaseCommand

from core import crypto


class Command(BaseCommand):
    help = 'Generate a key for FIELD_ENCRYPTION_KEYS (store it ONLY in your host environment variables).'

    def handle(self, *args, **opts):
        self.stdout.write(crypto.generate_key())
        self.stderr.write('Put this in Railway Variables as FIELD_ENCRYPTION_KEYS. Never commit it to git. '
                          'Back it up in a password manager: without it encrypted data cannot be read.')
