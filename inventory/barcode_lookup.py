"""Name look-up for scanned barcodes using FREE services (no API key, no payment).

Order:  1) our own table (BarcodeCatalog, includes names your team already registered)
        2) Open Food Facts   3) Open Beauty Facts   4) Open Products Facts   5) UPCitemdb (backup).

Rules that keep the system safe and fast:
  * Only identity data is looked up (name, brand, size, picture). Prices and stock are NEVER guessed.
  * A found product is saved in BarcodeCatalog, so the same barcode never calls the internet twice.
  * Every product registered in SmartPOS with a retail barcode is added to BarcodeCatalog (name only),
    so the next scan of that pack - in any branch - is filled in automatically.
  * A barcode that every service answered "unknown" for is remembered for NOT_FOUND_DAYS, so the
    UPCitemdb free limit (about 100 requests / day) is not wasted on the same unknown pack.
  * A network error / timeout is NOT remembered as "unknown" - it is simply tried again next time.
  * One look-up never takes longer than BUDGET seconds in total, even if the internet is down.
  * Any failure returns None: the Register form still opens normally and nothing is blocked.
  * Used only by the Register sheet / Add Product form (never by POS), so selling is never slowed.
"""
import logging
import time
from datetime import timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.utils import timezone

from .barcodes import barcode_variants

logger = logging.getLogger(__name__)

OPEN_FACTS = (   # same public API, three catalogues: food, cosmetics, general products
    ('openfoodfacts', 'world.openfoodfacts.org'),
    ('openbeautyfacts', 'world.openbeautyfacts.org'),
    ('openproductsfacts', 'world.openproductsfacts.org'),
)
OFF_FIELDS = 'product_name,generic_name,brands,quantity,image_front_small_url'
UPC_URL = 'https://api.upcitemdb.com/prod/trial/lookup'

TIMEOUT = float(getattr(settings, 'BARCODE_LOOKUP_TIMEOUT', 3.0))
BUDGET = float(getattr(settings, 'BARCODE_LOOKUP_BUDGET', 7.0))   # max seconds for ALL services together
NOT_FOUND_DAYS = int(getattr(settings, 'BARCODE_LOOKUP_NOT_FOUND_DAYS', 7))
UPC_DAILY_LIMIT = int(getattr(settings, 'BARCODE_LOOKUP_UPC_DAILY_LIMIT', 90))   # free plan = 100 / day
USER_AGENT = getattr(settings, 'BARCODE_LOOKUP_USER_AGENT', 'SmartPOS/54 (barcode name lookup; contact: support@smartpos.local)')

_OK, _UNKNOWN, _ERROR = 'ok', 'unknown', 'error'


def is_lookup_enabled():
    return bool(getattr(settings, 'BARCODE_LOOKUP_ENABLED', True))


def is_lookup_candidate(code):
    """Only real retail numbers (EAN-8 / UPC-A / EAN-13 / GTIN-14). Internal PM... labels are never sent out."""
    code = (code or '').strip()
    return code.isdigit() and 8 <= len(code) <= 14


def _clean(text, limit):
    return ' '.join(str(text or '').split())[:limit]


def build_display_name(name, brand='', quantity=''):
    """'Drinking Water' + 'Azam' + '1.5 L'  ->  'Azam Drinking Water 1.5 L' (no repeated words)."""
    name, brand, quantity = _clean(name, 150), _clean(brand.split(',')[0] if brand else '', 60), _clean(quantity, 40)
    if not name:
        return ''
    parts = [name]
    if brand and brand.lower() not in name.lower():
        parts.insert(0, brand)
    if quantity and quantity.lower().replace(' ', '') not in ''.join(parts).lower().replace(' ', ''):
        parts.append(quantity)
    return _clean(' '.join(parts), 200)


# ----------------------------------------------------------------------------- remote services
def _query_open_facts(code, source, host, timeout):
    try:
        r = requests.get(f'https://{host}/api/v2/product/{code}.json', params={'fields': OFF_FIELDS},
                         headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'}, timeout=timeout)
    except requests.RequestException as exc:
        logger.info('%s unreachable for %s: %s', source, code, exc)
        return _ERROR, None
    if r.status_code == 404:
        return _UNKNOWN, None
    if r.status_code != 200:
        return _ERROR, None
    try:
        data = r.json()
    except ValueError:
        return _ERROR, None
    product = data.get('product') or {}
    if data.get('status') != 1 or not isinstance(product, dict):
        return _UNKNOWN, None
    title = product.get('product_name') or product.get('generic_name') or ''
    if not _clean(title, 5):
        return _UNKNOWN, None
    return _OK, {
        'name': _clean(title, 150), 'brand': _clean((product.get('brands') or '').split(',')[0], 100),
        'quantity': _clean(product.get('quantity'), 60), 'image_url': _clean(product.get('image_front_small_url'), 500),
        'source': source,
    }


def _query_open_food_facts(code, timeout=TIMEOUT):
    return _query_open_facts(code, *OPEN_FACTS[0], timeout)


def _query_open_beauty_facts(code, timeout=TIMEOUT):
    return _query_open_facts(code, *OPEN_FACTS[1], timeout)


def _query_open_products_facts(code, timeout=TIMEOUT):
    return _query_open_facts(code, *OPEN_FACTS[2], timeout)


def _upc_quota_available():
    key = f'barcode-upc-count:{timezone.now():%Y%m%d}'
    count = cache.get(key, 0)
    if count >= UPC_DAILY_LIMIT:
        return False
    cache.set(key, count + 1, 60 * 60 * 26)
    return True


def _query_upcitemdb(code, timeout=TIMEOUT):
    if not _upc_quota_available():
        return _ERROR, None            # not "unknown": quota used up, try again tomorrow
    try:
        r = requests.get(UPC_URL, params={'upc': code}, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'},
                         timeout=timeout)
    except requests.RequestException as exc:
        logger.info('UPCitemdb unreachable for %s: %s', code, exc)
        return _ERROR, None
    if r.status_code == 429:
        cache.set(f'barcode-upc-count:{timezone.now():%Y%m%d}', UPC_DAILY_LIMIT, 60 * 60 * 26)
        return _ERROR, None
    if r.status_code != 200:
        return _ERROR, None
    try:
        data = r.json()
    except ValueError:
        return _ERROR, None
    items = data.get('items') or []
    if data.get('code') != 'OK' or not items:
        return _UNKNOWN, None
    item = items[0] or {}
    title = _clean(item.get('title'), 150)
    if not title:
        return _UNKNOWN, None
    images = item.get('images') or []
    return _OK, {
        'name': title, 'brand': _clean(item.get('brand'), 100), 'quantity': _clean(item.get('size'), 60),
        'image_url': _clean(images[0] if images else '', 500), 'source': 'upcitemdb',
    }


# ----------------------------------------------------------------------------- public API
def _store(code, found, info=None):
    from .models import BarcodeCatalog
    info = info or {}
    values = dict(found=found, name=info.get('name', ''), brand=info.get('brand', ''), quantity=info.get('quantity', ''),
                  image_url=info.get('image_url', ''), source=info.get('source', ''), looked_up_at=timezone.now())
    try:
        BarcodeCatalog.objects.update_or_create(barcode=code, defaults=values)
    except IntegrityError:      # two cashiers scanned the same new pack at the same second
        pass


def _result(row):
    return {
        'barcode': row.barcode, 'name': row.name, 'brand': row.brand, 'quantity': row.quantity,
        'image_url': row.image_url, 'source': row.source,
        'display_name': build_display_name(row.name, row.brand, row.quantity),
        'source_label': row.get_source_display(), 'from_shop': row.source == 'smartpos',
    }


def lookup_barcode(code):
    """Return {'display_name', 'name', 'brand', 'quantity', 'image_url', 'source', 'source_label'} or None."""
    from .models import BarcodeCatalog
    code = (code or '').strip()
    if not is_lookup_enabled() or not is_lookup_candidate(code):
        return None

    variants = barcode_variants(code)
    rows = list(BarcodeCatalog.objects.filter(barcode__in=variants))
    hit = next((r for r in rows if r.found and r.name), None)
    if hit:
        return _result(hit)
    fresh_limit = timezone.now() - timedelta(days=NOT_FOUND_DAYS)
    if any(not r.found and r.looked_up_at >= fresh_limit for r in rows):
        return None                       # both services already said "unknown" recently

    # EAN-13 is what both services index best; a zero-padded GTIN-14 / UPC-A is tried in its EAN-13 form.
    stripped = code.lstrip('0')
    ask = code if len(code) == 8 or len(stripped) > 13 else stripped.zfill(13)
    statuses = []
    deadline = time.monotonic() + BUDGET
    for query in (_query_open_food_facts, _query_open_beauty_facts, _query_open_products_facts, _query_upcitemdb):
        remaining = deadline - time.monotonic()
        if remaining < 0.5:
            statuses.append(_ERROR)       # out of time: never remembered as "unknown"
            break
        try:
            status, info = query(ask, timeout=min(TIMEOUT, remaining))
        except Exception:                 # never let a look-up break the Register form
            logger.exception('barcode look-up failed for %s', ask)
            status, info = _ERROR, None
        statuses.append(status)
        if status == _OK and info:
            _store(code, True, info)
            return _result(BarcodeCatalog.objects.get(barcode=code))

    if statuses and all(s == _UNKNOWN for s in statuses) and len(statuses) == 4:
        _store(code, False)               # remembered as "unknown" for NOT_FOUND_DAYS
    return None


def remember_registered_name(barcode, name):
    """Learn from the shop's own registrations: save barcode -> name (NOTHING else) for the next scan.

    Never overwrites a name that an online catalogue already supplied. Never raises.
    """
    from django.db import transaction
    from .models import BarcodeCatalog
    barcode, name = (barcode or '').strip(), _clean(name, 200)
    if not name or not is_lookup_candidate(barcode):
        return
    try:
        with transaction.atomic():
            row = BarcodeCatalog.objects.filter(barcode=barcode).first()
            if row and row.found and row.name and row.source != 'smartpos':
                return
            BarcodeCatalog.objects.update_or_create(barcode=barcode, defaults=dict(
                found=True, name=name, brand='', quantity='', image_url='', source='smartpos',
                looked_up_at=timezone.now()))
    except Exception:
        logger.exception('could not remember registered name for %s', barcode)
