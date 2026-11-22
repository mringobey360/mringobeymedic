"""Glue between a branch's saved receipt design and the PDF renderer."""
import datetime
import io
import os
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.utils import timezone

from core.receipt_pdf import DEFAULT_STYLE, build_receipt_pdf

STYLE_FIELDS = tuple(DEFAULT_STYLE.keys())


def get_receipt_style(pharmacy):
    """The branch's saved design, or the standard SmartPOS look when none exists."""
    saved = getattr(pharmacy, 'receipt_settings', None)   # missing row -> None
    if saved is None:
        return dict(DEFAULT_STYLE)
    return {name: getattr(saved, name) for name in STYLE_FIELDS}


def get_logo_source(pharmacy, use_branch_logo=True, logo_override=None):
    """Return a receipt logo without forcing unsaved preview uploads into storage.

    ``logo_override`` is used by the Receipt Design PDF preview so a newly
    selected logo can be rendered before the manager saves it.
    """
    if logo_override is not None:
        try:
            if hasattr(logo_override, 'seek'):
                logo_override.seek(0)
            return io.BytesIO(logo_override.read())
        except Exception:
            pass

    if use_branch_logo and pharmacy.logo:
        try:
            if pharmacy.logo.storage.exists(pharmacy.logo.name):
                with pharmacy.logo.open('rb') as handle:
                    return io.BytesIO(handle.read())
        except Exception:
            pass
    candidate = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.jpg')
    return candidate if os.path.exists(candidate) else None


def render_sale_receipt_bytes(sale):
    pharmacy = sale.pharmacy
    return build_receipt_pdf(sale, get_receipt_style(pharmacy), get_logo_source(pharmacy), timezone.localtime)


def sample_sale_for(pharmacy, user):
    """An in-memory demo sale so managers can preview their design (nothing is saved)."""
    class _Items:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    from .receipt_samples import sample_lines
    product = lambda name, unit: SimpleNamespace(name=name, get_unit_display=lambda: unit)
    rows = []
    for line in sample_lines(getattr(pharmacy, 'business_type', 'OTHER')):
        batch = SimpleNamespace(batch_number=line['batch'], expiry_date=line['expiry'])
        rows.append(SimpleNamespace(
            product=product(line['name'], line['unit']), batch=batch, quantity=line['quantity'],
            unit_price=line['unit_price'], subtotal=line['subtotal'], returned_quantity=0,
            original_quantity=line['quantity'],
        ))
    total = sum(r.subtotal for r in rows)
    sale = SimpleNamespace(
        pharmacy=pharmacy, id=0, sale_number=1, status='COMPLETED', served_by=user,
        customer_name='Sample Customer', customer_phone='0712 345 678',
        created_at=timezone.now(), get_payment_method_display=lambda: 'Cash',
        items=_Items(rows), returned_total=Decimal(0), original_total=total, total_amount=total,
    )
    return sale


def render_sample_receipt_bytes(pharmacy, user, style=None, logo_override=None, use_branch_logo=True):
    """Render a demo receipt entirely in memory.

    ``style`` and ``logo_override`` are optional so the manager can preview
    unsaved receipt customisations (including a newly selected logo).
    Nothing is written to the database or media storage by this function.
    """
    sale = sample_sale_for(pharmacy, user)
    receipt_style = dict(style or get_receipt_style(pharmacy))
    logo_source = get_logo_source(
        pharmacy,
        use_branch_logo=use_branch_logo,
        logo_override=logo_override,
    )
    return build_receipt_pdf(sale, receipt_style, logo_source, timezone.localtime)


def copy_design(source_settings, target_pharmacy, user):
    """Copy every design field from one branch's settings onto another branch."""
    from .models import ReceiptSettings
    target, _ = ReceiptSettings.objects.get_or_create(pharmacy=target_pharmacy)
    for name in ReceiptSettings.STYLE_FIELDS:
        setattr(target, name, getattr(source_settings, name))
    target.updated_by = user
    target.save()
    return target
