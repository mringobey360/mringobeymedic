from django.apps import AppConfig


class HqConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'hq'
    verbose_name = 'HQ — Signups & Billing'

    def ready(self):
        from . import impersonation  # noqa: F401  (registers logout signal)
