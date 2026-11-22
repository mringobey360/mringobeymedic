from django.apps import AppConfig
from django.core.checks import Warning as CheckWarning, register


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'System Core'

    def ready(self):
        from . import image_signals
        image_signals.connect()

        @register(deploy=True)
        def payment_encryption_key_check(app_configs, **kwargs):
            from django.conf import settings
            from core import crypto
            if settings.DEBUG or crypto.uses_dedicated_key():
                return []
            return [CheckWarning(
                'FIELD_ENCRYPTION_KEYS is not set: payment data is encrypted with a key derived from SECRET_KEY.',
                hint='Run `python manage.py generate_field_key`, add the result to Railway Variables as '
                     'FIELD_ENCRYPTION_KEYS, then run `python manage.py encrypt_payment_data`.',
                id='core.W117',
            )]
