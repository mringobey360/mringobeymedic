"""
Technical Support contact details used by the assistant (both the offline
FAQ engine and the AI prompt). The numbers live in settings.SUPPORT_PHONES
(env var SUPPORT_PHONES) so they can be changed without touching code.
"""
from django.conf import settings

DEFAULT_SUPPORT_PHONES = ('+255610117824', '+255678828519')


def support_numbers():
    numbers = [str(p).strip() for p in (getattr(settings, 'SUPPORT_PHONES', None) or []) if str(p).strip()]
    return numbers or list(DEFAULT_SUPPORT_PHONES)


def support_numbers_text():
    """'+255610117824 | +255678828519'"""
    return ' | '.join(support_numbers())
