"""Edit history (audit trail) for products and batches.

Usage in a view:
    before = snapshot_product(product)          # BEFORE the form touches the instance
    ... form.save() ...
    log_product_edit(request, product, before)

Only fields that really changed are stored, with their old and new values, so the history
page reads like "Selling price: 1,500 -> 1,800". Logging never breaks the real operation:
a failure to write the audit row is logged and swallowed.
"""
import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from .models import InventoryChangeLog, unit_label

logger = logging.getLogger(__name__)

PRODUCT_FIELDS = {
    'name': 'Name', 'sku': 'SKU', 'barcode': 'Barcode', 'category': 'Category', 'unit': 'Base unit',
    'reorder_level': 'Reorder level', 'requires_expiry': 'Has expiry date', 'is_active': 'Active in catalog',
    'discount_enabled': 'Discount allowed', 'discount_type': 'Discount type',
    'min_discount_percent': 'Minimum discount (%)', 'max_discount_percent': 'Maximum discount (%)',
    'min_discount_amount': 'Minimum discount (TZS)', 'max_discount_amount': 'Maximum discount (TZS)',
    'image': 'Product photo',
}
BATCH_FIELDS = {
    'batch_number': 'Batch number', 'quantity': 'Quantity remaining', 'initial_quantity': 'Quantity received (original)', 'purchase_unit': 'Purchase unit',
    'purchase_quantity': 'Purchase quantity', 'units_per_purchase_unit': 'Base units per purchase unit',
    'purchase_unit_price': 'Purchase price per purchase unit', 'purchase_price': 'Cost per base unit',
    'selling_price': 'Selling price per base unit', 'discount_cap_amount': 'Maximum discount for this batch',
    'expiry_date': 'Expiry date', 'note': 'Note',
}


def _fmt(value):
    if value is None or value == '':
        return '—'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, Decimal):
        text = f'{value:,.2f}'
        return text[:-3] if text.endswith('.00') else text
    if isinstance(value, date):
        return value.strftime('%d %b %Y')
    return str(value)


def _snapshot(instance, fields):
    out = {}
    for name in fields:
        if name == 'category':
            value = instance.category.name if getattr(instance, 'category_id', None) else None
        elif name == 'image':
            value = (instance.image.name or '').rsplit('/', 1)[-1] or None
        elif name in ('unit', 'purchase_unit'):
            value = unit_label(getattr(instance, name, '')) or None
        else:
            value = getattr(instance, name, None)
        out[name] = value
    return out


def snapshot_product(product):
    return _snapshot(product, PRODUCT_FIELDS)


def snapshot_batch(batch):
    return _snapshot(batch, BATCH_FIELDS)


def _same(a, b):
    if isinstance(a, (int, Decimal)) and isinstance(b, (int, Decimal)) and not isinstance(a, bool):
        try:
            return Decimal(a) == Decimal(b)
        except InvalidOperation:
            pass
    return a == b


def diff(before, after, labels):
    changes = []
    for field, label in labels.items():
        old, new = before.get(field), after.get(field)
        if not _same(old, new) and not (old in (None, '') and new in (None, '')):
            changes.append({'field': field, 'label': label, 'old': _fmt(old), 'new': _fmt(new)})
    return changes


def _client_ip(request):
    if request is None:
        return None
    fwd = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    return fwd or request.META.get('REMOTE_ADDR') or None


def record(request, *, pharmacy, action, product=None, batch=None, product_name='', batch_number='',
           summary='', changes=None, reason='', user=None):
    """Write one audit row. Never raises."""
    try:
        user = user or getattr(request, 'user', None)
        if user is not None and not getattr(user, 'is_authenticated', False):
            user = None
        name = ''
        if user is not None:
            name = (user.get_full_name() or user.get_username() or '')[:150]
        try:
            ip = _client_ip(request)
        except Exception:
            ip = None
        return InventoryChangeLog.objects.create(
            pharmacy=pharmacy, product=product, batch=batch, action=action,
            product_name=(product_name or (product.name if product else ''))[:200],
            batch_number=(batch_number or (batch.batch_number if batch else ''))[:100],
            summary=summary[:255], changes=changes or [], reason=(reason or '')[:255],
            performed_by=user, performed_by_name=name, ip_address=ip,
        )
    except Exception:
        logger.exception('Could not write inventory audit record (%s)', action)
        return None


def log_product_edit(request, product, before, reason='', user=None):
    changes = diff(before, snapshot_product(product), PRODUCT_FIELDS)
    if not changes:
        return None
    return record(request, pharmacy=product.pharmacy, action=InventoryChangeLog.Action.PRODUCT_EDITED, product=product,
                  summary='Changed ' + ', '.join(c['label'] for c in changes)[:200], changes=changes, reason=reason, user=user)


def log_batch_edit(request, batch, before, reason='', user=None):
    changes = diff(before, snapshot_batch(batch), BATCH_FIELDS)
    if not changes:
        return None
    return record(request, pharmacy=batch.product.pharmacy, action=InventoryChangeLog.Action.BATCH_EDITED,
                  product=batch.product, batch=batch,
                  summary='Changed ' + ', '.join(c['label'] for c in changes)[:200], changes=changes, reason=reason, user=user)


def log_product_created(user, product, batch, request=None):
    return record(request, pharmacy=product.pharmacy, action=InventoryChangeLog.Action.PRODUCT_CREATED, product=product, batch=batch,
                  summary=f'Registered with opening stock of {batch.quantity} {product.get_unit_display()}', user=user,
                  changes=[{'field': 'opening', 'label': 'Opening stock', 'old': '—', 'new': f'{batch.quantity} {product.get_unit_display()}'},
                           {'field': 'selling_price', 'label': 'Selling price per base unit', 'old': '—', 'new': _fmt(batch.selling_price)},
                           {'field': 'purchase_price', 'label': 'Cost per base unit', 'old': '—', 'new': _fmt(batch.purchase_price)}])


def log_batch_added(user, batch, request=None):
    p = batch.product
    return record(request, pharmacy=p.pharmacy, action=InventoryChangeLog.Action.BATCH_ADDED, product=p, batch=batch, user=user,
                  summary=f'Received {batch.quantity} {p.get_unit_display()}',
                  changes=[{'field': 'quantity', 'label': 'Quantity received', 'old': '—', 'new': f'{batch.quantity} {p.get_unit_display()}'},
                           {'field': 'purchase_price', 'label': 'Cost per base unit', 'old': '—', 'new': _fmt(batch.purchase_price)},
                           {'field': 'selling_price', 'label': 'Selling price per base unit', 'old': '—', 'new': _fmt(batch.selling_price)},
                           {'field': 'expiry_date', 'label': 'Expiry date', 'old': '—', 'new': _fmt(batch.expiry_date)}])
