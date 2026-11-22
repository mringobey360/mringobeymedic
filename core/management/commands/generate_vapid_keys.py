from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Generate a VAPID P-256 key pair for Web Push notifications.'

    def handle(self, *args, **options):
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.backends import default_backend
            import base64
        except ImportError as exc:
            self.stderr.write(self.style.ERROR(f'Cryptography is required. Install project requirements first: {exc}'))
            return

        private = ec.generate_private_key(ec.SECP256R1(), default_backend())
        private_pem = private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode().strip()
        numbers = private.public_key().public_numbers()
        raw_public = b'\x04' + numbers.x.to_bytes(32, 'big') + numbers.y.to_bytes(32, 'big')
        public_key = base64.urlsafe_b64encode(raw_public).rstrip(b'=').decode()

        self.stdout.write('\nWEBPUSH_VAPID_PUBLIC_KEY=')
        self.stdout.write(public_key)
        self.stdout.write('\nWEBPUSH_VAPID_PRIVATE_KEY=')
        self.stdout.write(private_pem)
        self.stdout.write('\nWEBPUSH_VAPID_CLAIMS_EMAIL=mailto:admin@yourdomain.com')
        self.stdout.write(self.style.WARNING('\nKeep the private key secret. Do not commit it to Git or put it in the ZIP.'))
