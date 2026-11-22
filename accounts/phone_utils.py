"""
Phone number normalization shared by every form that collects a phone number
(employee creation, profile, forgot-password lookup, pharmacy branch info).

Accepts common formats a user might type — 0712345678, 255712345678,
+255712345678, with spaces/dashes — and normalizes them all to a single
canonical "+<countrycode><number>" form so the system never mismatches
the same number typed two different ways (e.g. login/OTP lookups failing
because one save used "0712..." and another used "+255712...").
"""
import re

from django import forms

# Tanzania is the default assumed country when a local "0..." number is entered,
# since this system's primary market is Tanzania. Numbers already given with a
# country code (e.g. +254..., +255...) are respected as-is.
DEFAULT_COUNTRY_CODE = '255'


def _fix_tz_extra_zero(digits: str) -> str:
    """255 0712345678 -> 255712345678 (people type the country code AND the leading 0)."""
    if digits.startswith(DEFAULT_COUNTRY_CODE):
        return DEFAULT_COUNTRY_CODE + digits[len(DEFAULT_COUNTRY_CODE):].lstrip('0')
    return digits


_TZ_MOBILE_RE = re.compile(r'^\+255[67]\d{8}$')


def is_sms_deliverable(normalized: str) -> bool:
    """True when a normalized number can really receive an SMS.

    Tanzanian numbers (+255) must be a mobile: +255 then 6 or 7, then 8 more digits (12 digits total).
    Other countries only need a sane length.
    """
    if not normalized or not normalized.startswith('+') or normalized.startswith('+0'):
        return False   # no country code starts with 0 (e.g. a local landline typed as 0222...)
    if normalized.startswith('+255'):
        return bool(_TZ_MOBILE_RE.match(normalized))
    return 9 <= len(normalized) - 1 <= 14


def normalize_phone_number(raw_value: str) -> str:
    """
    Returns a normalized "+<countrycode><digits>" phone number, or raises
    django.forms.ValidationError with a clear, actionable message.
    """
    if not raw_value or not raw_value.strip():
        raise forms.ValidationError("Phone number is required.")

    value = re.sub(r'[\s\-().]', '', raw_value.strip())
    if value.startswith('00') and value[2:].isdigit():
        value = '+' + value[2:]   # 00255... -> +255...

    if value.startswith('+'):
        digits = _fix_tz_extra_zero(value[1:])
        if not digits.isdigit():
            raise forms.ValidationError("Phone number must contain only digits after the '+'.")
        if not (9 <= len(digits) <= 14):
            raise forms.ValidationError(
                "That phone number looks too short or too long. "
                "Include the country code, e.g. +255712345678."
            )
        return '+' + digits

    if not value.isdigit():
        raise forms.ValidationError(
            "Phone number must contain only digits, optionally starting with '+'. "
            "Example: 0712345678 or +255712345678."
        )

    if value.startswith('0') and len(value) == 10 and value[1] in {'6', '7'}:
        # Tanzanian local mobile format: 0611xxxxxx / 0712xxxxxx
        # -> +255611xxxxxx / +255712xxxxxx
        return '+' + DEFAULT_COUNTRY_CODE + value[1:]

    if len(value) == 9 and value[0] in {'6', '7'}:
        # Tanzanian mobile number entered without the leading zero or country
        # code, e.g. 611123456 -> +255611123456.
        return '+' + DEFAULT_COUNTRY_CODE + value

    if value.startswith(DEFAULT_COUNTRY_CODE) and len(value) in (12, 13):
        # Already has country code but missing '+' e.g. 255712345678 (or 2550712345678 with a stray 0)
        return '+' + _fix_tz_extra_zero(value)

    if 9 <= len(value) <= 14:
        # Some other country code typed without '+' — accept but require a sane length
        return '+' + value

    raise forms.ValidationError(
        "Enter a valid phone number, e.g. 0712345678 (local) or +255712345678 (with country code)."
    )
