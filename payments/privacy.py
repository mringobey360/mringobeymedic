"""Data-minimisation helpers for the payment flow.

Customer mobile numbers are the raw material for "I sent you money by mistake, please
reverse it" scams. We therefore (1) never store a customer's full number inside gateway
payloads / raw responses / logs, and (2) show only a masked number in admin screens.
The one full copy that must exist (PesapalTransaction.payer_phone, needed to resend a
payment prompt and the receipt SMS) is stored AES-256-GCM encrypted (core.crypto) and is
minimised after the payment is final by the `scrub_payment_pii` management command.
Card numbers / CVV / PINs are never accepted by SmartPOS (card entry happens only inside the
gateway's hosted page); anything that looks like one is stripped from stored text and logs.
"""
import re

# Tanzanian mobile numbers: 255 6/7 xxxxxxxx, +255..., or local 0 6/7 xxxxxxxx.
_TZ_PHONE_RE = re.compile(r'(?<![\d])(?:\+?255|0)[67]\d{8}(?![\d])')
# Explicit international numbers with a leading '+' (plain long digit runs are NOT touched:
# they would also hit timestamps and provider ids).
_LONG_DIGITS_RE = re.compile(r'(?<![\d])\+\d{10,15}(?![\d])')

# 15-19 digit runs (optionally space/dash separated) that pass the Luhn check are card numbers.
_CARD_RE = re.compile(r'(?<![\d])(?:\d[ -]?){14,18}\d(?![\d])')

# Values under these keys are NEVER kept, not even masked (cards, CVV, PINs, passwords, tokens).
SECRET_KEYS = {
    'card', 'card_number', 'cardnumber', 'pan', 'cvv', 'cvc', 'cvv2', 'security_code', 'expiry',
    'expiry_date', 'exp_month', 'exp_year', 'pin', 'password', 'otp', 'secret', 'consumer_secret',
    'api_key', 'token', 'access_token', 'authorization',
}

SENSITIVE_KEYS = {
    'phone', 'phone_number', 'phonenumber', 'msisdn', 'mobile', 'mobile_number', 'payer_phone',
    'customer_phone', 'wallet', 'account', 'account_number', 'payment_account', 'sender', 'receiver',
}


def mask_phone(value):
    """'255712345678' / '+255712345678' / '0712345678' -> '0712 *** 678'. Unknown shapes -> '***'."""
    text = str(value or '').strip()
    if not text:
        return ''
    if '*' in text or '•' in text:      # already masked: keep (makes masking idempotent)
        return text
    digits = re.sub(r'\D', '', text)
    if digits.startswith('255') and len(digits) == 12:
        digits = '0' + digits[3:]
    if len(digits) == 10 and digits.startswith('0'):
        return f'{digits[:4]} *** {digits[7:]}'
    if len(digits) >= 7:
        return f'{digits[:3]}*** {digits[-3:]}'
    return '***'


def luhn_valid(digits):
    total, alt = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if alt:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
        alt = not alt
    return total % 10 == 0


def _redact_cards(text):
    def repl(m):
        digits = re.sub(r'\D', '', m.group(0))
        return '[CARD REDACTED]' if 15 <= len(digits) <= 19 and luhn_valid(digits) else m.group(0)
    return _CARD_RE.sub(repl, text)


def redact_text(text):
    """Mask phone numbers and remove card numbers inside any text blob (JSON, str(dict), log line)."""
    if not text:
        return text
    text = _redact_cards(str(text))
    text = _TZ_PHONE_RE.sub(lambda m: mask_phone(m.group(0)), text)
    return _LONG_DIGITS_RE.sub(lambda m: mask_phone(m.group(0)), text)


def redact_payload(obj):
    """Recursively mask phone/account values in a JSON-like structure (returns a new object)."""
    if isinstance(obj, dict):
        clean = {}
        for key, value in obj.items():
            if str(key).lower() in SECRET_KEYS and not isinstance(value, (dict, list)):
                clean[key] = '[REDACTED]' if value not in (None, '') else value
            elif str(key).lower() in SENSITIVE_KEYS and not isinstance(value, (dict, list)):
                clean[key] = mask_phone(value) if value not in (None, '') else value
            else:
                clean[key] = redact_payload(value)
        return clean
    if isinstance(obj, list):
        return [redact_payload(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj
