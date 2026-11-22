"""
Tanzanian mobile-money networks used by the online checkout.

Everything the checkout needs to know about a network lives here: its display
name, the PIN wording customers recognise, the number prefixes used to
auto-detect it, and the logo file.

LOGOS
-----
Logo files live in  static/img/payments/  (names below). They are landscape 360x240
PNGs on a white background; the checkout shows them in a wide box, so any logo shape
fits. If a file is missing the checkout shows a clean coloured badge with the
network's initial, so nothing ever looks broken.

The colours below are only used for that fallback badge and the selection highlight.
"""
import re

# key -> display data. Order = order shown on the checkout.
NETWORKS = [
    {
        'key': 'vodacom', 'label': 'M-Pesa', 'operator': 'Vodacom',
        'pin_name': 'M-Pesa PIN', 'prefixes': ('074', '075', '076'),
        'logo': 'img/payments/vodacom-mpesa.png', 'color': '#e60000', 'initial': 'M',
    },
    {
        'key': 'airtel', 'label': 'Airtel Money', 'operator': 'Airtel',
        'pin_name': 'Airtel Money PIN', 'prefixes': ('068', '069', '078'),
        'logo': 'img/payments/airtel-money.png', 'color': '#ed1c24', 'initial': 'A',
    },
    {
        'key': 'yas', 'label': 'Mixx by Yas', 'operator': 'Yas (Tigo)',
        'pin_name': 'Mixx PIN', 'prefixes': ('065', '067', '071', '077'),
        'logo': 'img/payments/mixx-by-yas.png', 'color': '#0b2a8f', 'initial': 'Y',
    },
    {
        'key': 'halotel', 'label': 'HaloPesa', 'operator': 'Halotel',
        'pin_name': 'HaloPesa PIN', 'prefixes': ('061', '062'),
        'logo': 'img/payments/halopesa.png', 'color': '#f15a24', 'initial': 'H',
    },
]

_BY_KEY = {n['key']: n for n in NETWORKS}
_PREFIX_TO_KEY = {p: n['key'] for n in NETWORKS for p in n['prefixes']}

# Valid Tanzanian mobile number in international form: 255 + 6xx/7xx + 7 digits.
_MSISDN_RE = re.compile(r'^255[67]\d{8}$')


def network_by_key(key):
    """Return the network dict for `key`, or None."""
    return _BY_KEY.get((key or '').strip().lower())


def normalize_msisdn(raw):
    """
    '0712 345 678', '712345678', '+255712345678', '255-712-345-678'
    -> '255712345678'. Returns None when it is not a valid Tanzanian mobile number.
    """
    digits = re.sub(r'\D', '', raw or '')
    if digits.startswith('255'):
        pass
    elif digits.startswith('0') and len(digits) == 10:
        digits = '255' + digits[1:]
    elif len(digits) == 9 and digits[0] in '67':
        digits = '255' + digits
    return digits if _MSISDN_RE.match(digits) else None


def local_format(msisdn):
    """'255712345678' -> '0712 345 678' (for display)."""
    if not msisdn or not _MSISDN_RE.match(msisdn):
        return msisdn or ''
    local = '0' + msisdn[3:]
    return f'{local[:4]} {local[4:7]} {local[7:]}'


def mask_msisdn(msisdn):
    """'255712345678' -> '0712 ••• 678' (shown on the waiting screen)."""
    if not msisdn or not _MSISDN_RE.match(msisdn):
        return ''
    local = '0' + msisdn[3:]
    return f'{local[:4]} ••• {local[7:]}'


def detect_network(msisdn):
    """
    Best guess of the network from the number prefix, or None.
    Only a hint: Tanzanians can keep their number when changing network, so the
    customer's own choice always wins and a mismatch is never an error.
    """
    if not msisdn or not _MSISDN_RE.match(msisdn):
        return None
    return network_by_key(_PREFIX_TO_KEY.get('0' + msisdn[3:5]))   # '255744…' -> '074'
