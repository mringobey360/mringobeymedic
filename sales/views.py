import io
import json
import re
import csv
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum, F, Count, DecimalField
from django.db.models.functions import Coalesce
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_protect

from accounts.decorators import admin_required, employee_or_admin_required
from accounts.models import User
from accounts.workday_guard import workday_sales_block_message, closed_workday_for, closed_moment
from accounts.phone_utils import normalize_phone_number
from inventory.models import Product, ProductBatch, StockMovement, FEFO_ORDER
from sms_integration.utils import send_debt_reminder_sms, has_active_debt_sms, send_receipt_sms, has_active_receipt_sms
from .models import Sale, SaleItem, Debt, DebtPayment, SaleReturnRequest
from . import late_entry as late_entry_rules
from .debt_payments import DebtPaymentError, normalize_method, record_debt_payment
from customer_registry.models import CustomerContact
from .forms import DebtEditForm
from . import returns
from core.pdf_utils import generate_multi_section_pdf_report
from reportlab.lib.pagesizes import A4, landscape


def _require_pharmacy(request):
    if request.pharmacy is None:
        messages.warning(request, "You don't have an active branch yet. Create one to get started.")
        return False
    return True


def _validate_discount(product, value):
    """
    Validate a POS discount against the manager policy.
    Returns (kind, value): ('PERCENT', pct) or ('AMOUNT', tzs_per_unit).
    For AMOUNT products `value` is a TZS amount taken off each unit.
    """
    is_amount = product.discount_type == Product.DiscountType.AMOUNT
    kind = 'AMOUNT' if is_amount else 'PERCENT'
    try:
        discount = Decimal(str(value if value not in (None, '') else '0')).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Invalid discount for '{product.name}'.")
    if not product.discount_enabled:
        if discount != 0:
            raise ValueError(f"Discounts are not enabled for '{product.name}'.")
        return kind, Decimal('0.00')
    # A configured discount policy defines the range that may be USED, not a
    # mandatory discount. Zero always means "no discount" and must therefore
    # be accepted even when the manager configured a positive minimum.
    # This is important when a customer accepts the normal selling price.
    if discount == 0:
        return kind, Decimal('0.00')

    if is_amount:
        minimum = product.min_discount_amount or Decimal('0')
        maximum = product.max_discount_amount or Decimal('0')
        if discount < 0:
            raise ValueError(f"Discount for '{product.name}' cannot be negative.")
        if maximum <= 0:
            raise ValueError(f"Discounts are not configured for '{product.name}'.")
        if discount < minimum or discount > maximum:
            raise ValueError(
                f"If a discount is applied to '{product.name}', it must be between "
                f"TZS {minimum:,.0f} and TZS {maximum:,.0f} per unit. "
                f"Leave the discount at 0 for the normal price."
            )
        return kind, discount

    if discount < 0 or discount > 100:
        raise ValueError(f"Discount for '{product.name}' must be between 0% and 100%.")
    minimum = product.min_discount_percent or Decimal('0')
    maximum = product.max_discount_percent or Decimal('0')
    if maximum <= 0:
        raise ValueError(f"Discounts are not configured for '{product.name}'.")
    if discount < minimum or discount > maximum:
        raise ValueError(
            f"If a discount is applied to '{product.name}', it must be between "
            f"{minimum}% and {maximum}%. Leave the discount at 0 for the normal price."
        )
    return kind, discount


def _cart_discount_value(product, item):
    """Pick the discount figure from a cart line. New clients send `discount_value`;
    older/offline-queued clients send `discount_percent` (only valid for percentage products)."""
    raw = item.get('discount_value')
    if raw not in (None, ''):
        return raw
    legacy = item.get('discount_percent', '0')
    if product.discount_type == Product.DiscountType.AMOUNT and legacy not in (None, '', 0, '0', '0.0', '0.00'):
        raise ValueError(f"The discount for '{product.name}' is now a fixed TZS amount. Refresh the POS page and enter it again.")
    return legacy


def _batch_price_floor(original_price, unit_cost, max_discount=None):
    """Lowest price one unit of a batch may be sold for.

    1. Cost guard: the batch cost, or the normal price when that price is already below cost (then no discount).
    2. Batch discount limit (optional, set by the manager on the batch): the normal price minus the most that may be
       taken off one unit of THIS batch. A limit of 0 means no discount at all on the batch.
    The higher of the two wins, so a batch limit can only make a sale safer, never allow a loss."""
    original_price = Decimal(str(original_price))
    floor = min(original_price, Decimal(str(unit_cost)))
    if max_discount is not None:
        floor = max(floor, original_price - max(Decimal('0'), Decimal(str(max_discount))))
    return floor.quantize(Decimal('0.01'))


def _cost_floor_price(final_price, original_price, unit_cost, max_discount=None):
    """Never let a discount push a unit below the cost of the batch it is issued from (or below the batch's own
    discount limit, when the manager set one).

    Batches of one product can have different costs (e.g. 11,000 and 12,000), so a discount that is safe for the
    cheaper batch can sell the dearer one at a loss. The POS applies the same rule so totals match."""
    final_price = Decimal(str(final_price)).quantize(Decimal('0.01'))
    return max(final_price, _batch_price_floor(original_price, unit_cost, max_discount))


def _apply_vip_discount(pre_vip_price, unit_cost, vip_discount_value, discount_type='PERCENT'):
    """Apply a VIP percentage or fixed TZS-per-unit discount, never below actual batch cost."""
    pre_vip_price = Decimal(str(pre_vip_price)).quantize(Decimal('0.01'))
    unit_cost = Decimal(str(unit_cost)).quantize(Decimal('0.01'))
    value = max(Decimal('0'), Decimal(str(vip_discount_value or 0)))
    if discount_type == 'AMOUNT':
        requested = value.quantize(Decimal('0.01'))
    else:
        rate = min(Decimal('100'), value)
        requested = (pre_vip_price * rate / Decimal('100')).quantize(Decimal('0.01'))
    applied = min(requested, max(Decimal('0'), pre_vip_price - unit_cost))
    return (pre_vip_price - applied).quantize(Decimal('0.01')), applied.quantize(Decimal('0.01'))


def _fulfil_line_fefo(product, requested_qty, sale, user, discount_percent=Decimal('0.00'), note_prefix="", vip_discount_percent=Decimal('0.00'), vip_discount_type='PERCENT'):  # discount_percent = discount figure (percent or TZS per unit, by product.discount_type)
    """
    Deducts requested_qty from product's batches in FEFO order (earliest
    expiry first), creating one SaleItem per batch actually drawn from so
    profit stays accurate to what was really sold from each batch.

    Never raises if stock is insufficient — returns however much it could
    actually fulfil (possibly less than requested, possibly 0). This lets
    the caller decide whether to flag the sale for manual review instead of
    losing the transaction entirely (critical for offline sync, where the
    stock picture may be stale).
    """
    discount_kind, discount_value = _validate_discount(product, discount_percent)
    fulfilled = 0
    line_total = Decimal('0')
    pre_vip_line_total = Decimal('0')
    # Each SaleItem keeps the actual cost of the physical batch that was
    # issued. WAC may still be useful for product-level reporting, but it must
    # never replace the batch cost on a sale when batches have different costs.
    # This makes profit, returns and audit history lot-accurate.

    batches = ProductBatch.objects.select_for_update().filter(
        product=product, quantity__gt=0
    ).order_by(*FEFO_ORDER)

    for batch in batches:
        if fulfilled >= requested_qty:
            break
        if batch.is_expired:
            continue
        take = min(batch.quantity, requested_qty - fulfilled)
        if take <= 0:
            continue
        original_price = batch.selling_price
        if discount_kind == 'AMOUNT':
            if discount_value > original_price:
                raise ValueError(f"Discount of TZS {discount_value:,.0f} is higher than the price of '{product.name}' (TZS {original_price:,.0f}).")
            final_price = (original_price - discount_value).quantize(Decimal('0.01'))
            discount_percent = (discount_value / original_price * Decimal('100')).quantize(Decimal('0.01')) if original_price > 0 else Decimal('0.00')
        else:
            discount_percent = discount_value
            final_price = (original_price * (Decimal('100') - discount_percent) / Decimal('100')).quantize(Decimal('0.01'))
        # Profit guard: the item's own discount may not take the price below this batch's cost.
        # The batch's own discount limit (if any) is part of the same floor.
        batch_cap = batch.discount_cap_amount
        final_price = _cost_floor_price(final_price, original_price, batch.purchase_price, batch_cap)
        # VIP benefit is applied server-side after the item's configured discount.
        # It cannot make the final unit price lower than the actual batch cost or the batch's discount limit.
        pre_vip_price = final_price
        final_price, vip_discount_per_unit = _apply_vip_discount(pre_vip_price, _batch_price_floor(original_price, batch.purchase_price, batch_cap), vip_discount_percent, vip_discount_type)
        vip_discount_total = (vip_discount_per_unit * take).quantize(Decimal('0.01'))
        discount_amount = ((original_price - final_price) * take).quantize(Decimal('0.01'))
        total_discount_percent = ((original_price-final_price) / original_price * Decimal('100')).quantize(Decimal('0.01')) if original_price > 0 else Decimal('0.00')
        SaleItem.objects.create(
            sale=sale, product=product, batch=batch, quantity=take,
            unit_price=final_price, unit_cost=batch.purchase_price, original_unit_price=original_price,
            discount_percent=total_discount_percent, discount_amount=discount_amount,
            vip_discount_amount=vip_discount_total,
        )
        batch.quantity -= take
        batch.save(update_fields=['quantity'])
        StockMovement.objects.create(
            product=product, batch=batch, movement_type=StockMovement.MovementType.SALE,
            quantity_change=-take, note=f"{note_prefix}Sale #{sale.id}", performed_by=user,
        )
        line_total += final_price * take
        pre_vip_line_total += pre_vip_price * take
        fulfilled += take

    return fulfilled, line_total, pre_vip_line_total


def _vip_discount_for_customer(pharmacy, customer_contact, business_date=None):
    """Return the rate from the branch's manager-configured VIP policy."""
    from rewards.services import customer_vip_metrics
    metrics = customer_vip_metrics(pharmacy, customer_contact, business_date)
    return metrics.get('discount_value', Decimal('0.00')), metrics.get('discount_type', 'PERCENT')


def _customer_loyalty_snapshot(pharmacy, customer_contact, business_date=None):
    """Return branch-scoped loyalty metrics using the same policy as POS discounts."""
    from rewards.services import customer_vip_metrics
    metrics = customer_vip_metrics(pharmacy, customer_contact, business_date)
    return {key: metrics[key] for key in ('visits', 'spend', 'vip', 'discount_percent', 'discount_type', 'discount_amount', 'discount_value', 'discount_label')}


def _notify_customer_became_vip(pharmacy, customer_contact, snapshot):
    """Send one deduplicated in-app alert to branch managers when a customer qualifies."""
    if not customer_contact or not snapshot.get('vip'):
        return
    try:
        from notifications.utils import notify_many, management_recipients
        from notifications.models import Notification
        notify_many(
            management_recipients(pharmacy),
            'New VIP customer identified',
            f'{customer_contact.name} ({customer_contact.phone}) now qualifies for VIP loyalty: '
            f"{snapshot['visits']} completed purchases in the configured loyalty window; "
            f"tracked spend TZS {snapshot['spend']:,.0f}. Review the permitted discount before applying it.",
            category=Notification.Category.SALES,
            level=Notification.Level.SUCCESS,
            link_url='/performance/', pharmacy=pharmacy,
            dedupe_key=f'vip-customer-{pharmacy.pk}-{customer_contact.pk}',
        )
    except Exception:
        # Customer checkout must never fail just because an alert provider is unavailable.
        import logging
        logging.getLogger(__name__).exception('VIP qualification notification failed for customer_id=%s', customer_contact.pk)


@employee_or_admin_required
def pos_view(request):
    if not _require_pharmacy(request):
        return redirect('pharmacies:list' if request.user.is_management_user() else 'dashboard:home')
    # v97.18: a closed workday locks the POS until the Boss approves the reopening request.
    locked = workday_sales_block_message(request.user)
    if locked:
        messages.error(request, locked)
        return redirect('dashboard:home')
    # Managers / active assistants get a Sell | Restock switch. The server still enforces access on every restock call.
    can_restock = bool(request.user.is_superuser or request.user.is_management_user())
    from datetime import timedelta as _td
    from .models import LateEntryReason
    today = timezone.localdate()
    late_days = late_entry_rules.max_days()
    late_dates = [(today - _td(days=n)) for n in range(1, late_days + 1)]
    return render(request, 'sales/pos.html', {
        'can_restock': can_restock, 'can_override_layaway': can_restock,
        'late_entry_dates': late_dates, 'late_entry_reasons': LateEntryReason.choices,
    })


@employee_or_admin_required
def customer_debt_lookup_api(request):
    """Used by the POS to warn staff if a customer already has an outstanding balance."""
    name = request.GET.get('name', '').strip()
    if not name or request.pharmacy is None:
        return JsonResponse({'has_debt': False})

    debts = Debt.objects.filter(pharmacy=request.pharmacy, customer_name__iexact=name, is_settled=False)
    agg = debts.aggregate(
        total_balance=Coalesce(Sum(F('amount_owed') - F('amount_paid')), Decimal('0')),
        debt_count=Count('id'),
    )
    total_balance = agg['total_balance']

    return JsonResponse({
        'has_debt': total_balance > 0,
        'total_balance': str(total_balance),
        'debt_count': agg['debt_count'],
    })


def _batch_price_snapshot(product):
    """Return the exact sellable batch sequence the POS should currently price.

    The server remains authoritative at checkout. This snapshot is only for
    transparent POS pricing, so a cashier can see when one product spans
    multiple batches with different selling prices.
    """
    rows = []
    for batch in product.sellable_batches():
        rows.append({
            'id': batch.id,
            'batch_number': batch.batch_number or '',
            'quantity': batch.quantity,
            'selling_price': str(batch.selling_price),
            'purchase_price': str(batch.purchase_price),
            'max_discount': str(batch.discount_cap_amount) if batch.discount_cap_amount is not None else None,
            'expiry_date': batch.expiry_date.isoformat() if batch.expiry_date else None,
        })
    return rows


from inventory.barcodes import barcode_variants  # noqa: E402  (re-exported for tests)


def _layaway_reservations(pharmacy, product_ids):
    """{product_id: {'qty','plans','ready'}} of units customers are still paying for through Layaway.

    Display only: layaway never locks stock. Any failure here must never break the POS, so it degrades to no label."""
    try:
        from layaway.services import reserved_quantities
        return reserved_quantities(pharmacy, product_ids)
    except Exception:  # noqa: BLE001
        return {}


def _pos_product_payload(p, sellable_qty, reservation=None):
    """The product dict the POS cart needs (shared by text search and barcode scan)."""
    if reservation is None:
        reservation = _layaway_reservations(p.pharmacy_id, [p.pk]).get(p.pk, {})
    return {
        'layaway_reserved': int(reservation.get('qty', 0)),
        'layaway_plans': int(reservation.get('plans', 0)),
        'layaway_ready': int(reservation.get('ready', 0)),
        'id': p.id,
        'name': p.name,
        'barcode': p.effective_barcode,
        'image_url': p.image_url,
        'unit': p.get_unit_display(),
        'selling_price': str(p.current_selling_price),
        'batch_prices': _batch_price_snapshot(p),
        'discount_enabled': p.discount_enabled,
        'discount_type': p.discount_type,
        'min_discount_percent': str(p.min_discount_percent),
        'max_discount_percent': str(p.max_discount_percent),
        'min_discount_amount': str(p.min_discount_amount),
        'max_discount_amount': str(p.max_discount_amount),
        'purchase_price': str(p.current_purchase_price),
        'quantity_available': sellable_qty,
        'is_expiring_soon': p.is_expiring_soon,
        'days_to_expiry': p.days_to_expiry,
    }


def find_product_by_code(pharmacy, code):
    """Exact barcode/SKU lookup inside one branch. Returns an active Product or None."""
    # Camera decoders and USB scanners can include AIM prefixes, line endings,
    # GS1 group separators, or surrounding whitespace. Normalize the transport
    # representation before an exact, branch-scoped lookup; never fuzzy-match.
    code = (code or '').replace('\x1d', '').replace('\x00', '').strip()
    code = re.sub(r'^\][A-Za-z]\d', '', code)
    code = re.sub(r'[\r\n\t]+', '', code).strip()
    if not code or len(code) > 64:
        return None
    qs = Product.objects.filter(pharmacy=pharmacy, is_active=True).prefetch_related('batches')
    variants = barcode_variants(code)
    product = qs.filter(barcode__in=variants).first()
    if product is None:
        # Preserve case-insensitive matching for custom non-numeric labels.
        product = qs.filter(barcode__iexact=code).first()
    if product is None:
        product = qs.filter(sku__iexact=code).first()
    if product is None:
        # Legacy rows without a stored barcode print as PM<pharmacy:04d><pk:08d> (see Product.effective_barcode).
        prefix = f"PM{pharmacy.id:04d}"
        if code.startswith(prefix) and code[len(prefix):].isdigit():
            product = qs.filter(pk=int(code[len(prefix):]), barcode__isnull=True).first() or \
                qs.filter(pk=int(code[len(prefix):]), barcode='').first()
    return product


@employee_or_admin_required
def barcode_lookup_api(request):
    """Scan → product. Exact match only, so one scan can never add the wrong item."""
    if request.pharmacy is None:
        return JsonResponse({'status': 'error', 'message': 'No active branch.'}, status=400)
    code = request.GET.get('code', '').strip()
    if not code:
        return JsonResponse({'status': 'not_found', 'code': code})
    product = find_product_by_code(request.pharmacy, code)
    if product is None:
        return JsonResponse({'status': 'not_found', 'code': code})
    sellable_qty = product.sellable_quantity
    if sellable_qty <= 0:
        return JsonResponse({'status': 'out_of_stock', 'code': code, 'name': product.name})
    return JsonResponse({'status': 'ok', 'code': code, 'product': _pos_product_payload(product, sellable_qty)})


@employee_or_admin_required
def product_search_api(request):
    if request.pharmacy is None:
        return JsonResponse({'results': []})

    query = request.GET.get('q', '').strip()
    products = Product.objects.filter(pharmacy=request.pharmacy, is_active=True).prefetch_related('batches')
    if query:
        products = products.filter(Q(name__icontains=query) | Q(barcode__icontains=query) | Q(batches__batch_number__icontains=query)).distinct()
        if query.startswith('PM') and query[2:].isdigit():
            tail = query[2:]
            try:
                legacy_pk = int(tail[-10:])
                products = (products | Product.objects.filter(pharmacy=request.pharmacy, pk=legacy_pk, is_active=True)).distinct()
            except ValueError:
                pass

    data = []
    candidates = list(products)[:30]
    reservations = _layaway_reservations(request.pharmacy, [p.pk for p in candidates])
    for p in candidates:
        sellable_qty = p.sellable_quantity
        if sellable_qty <= 0:
            continue  # nothing sellable — either no stock or everything left has expired
        data.append(_pos_product_payload(p, sellable_qty, reservations.get(p.pk, {})))
        if len(data) >= 15:
            break

    return JsonResponse({'results': data})


@employee_or_admin_required
def offline_catalog_api(request):
    """
    Full sellable-product snapshot for the POS to cache locally (IndexedDB)
    so search/pricing keeps working with no connection. This is only ever a
    SNAPSHOT — actual stock is re-validated server-side when an offline sale
    is synced, since another device may have sold the same stock meanwhile.
    """
    if request.pharmacy is None:
        return JsonResponse({'products': [], 'generated_at': timezone.now().isoformat()})

    products = Product.objects.filter(pharmacy=request.pharmacy, is_active=True).prefetch_related('batches')
    data = []
    for p in products:
        sellable_qty = p.sellable_quantity
        if sellable_qty <= 0:
            continue
        data.append({
            'id': p.id, 'name': p.name, 'barcode': p.effective_barcode, 'image_url': p.image_url, 'unit': p.get_unit_display(),
            'selling_price': str(p.current_selling_price),
            'batch_prices': _batch_price_snapshot(p),
            'discount_enabled': p.discount_enabled, 'discount_type': p.discount_type,
            'min_discount_percent': str(p.min_discount_percent), 'max_discount_percent': str(p.max_discount_percent),
            'min_discount_amount': str(p.min_discount_amount), 'max_discount_amount': str(p.max_discount_amount),
            'quantity_available': sellable_qty,
        })

    return JsonResponse({'products': data, 'generated_at': timezone.now().isoformat()})


def _get_pharmacy_owner(pharmacy, fallback_user=None):
    if fallback_user is not None and fallback_user.is_superuser:
        return fallback_user
    if pharmacy is None:
        return None
    return pharmacy.managers.filter(role=User.Role.ADMIN, is_active=True).order_by('id').first()

@require_http_methods(["GET"])
def pos_session_status_view(request):
    """Lightweight POS heartbeat. Never redirects an expired AJAX session to HTML."""
    if not request.user.is_authenticated:
        return JsonResponse({"authenticated": False}, status=401)
    return JsonResponse({
        "authenticated": True,
        "user_id": request.user.id,
        "server_time": timezone.now().isoformat(),
    })


def _get_or_create_customer_contact(pharmacy, user, name, phone):
    """Create/reuse the branch customer master record while keeping sale snapshots immutable."""
    name = (name or '').strip()
    phone = (phone or '').strip()
    if not name and not phone:
        return None
    if phone:
        contact = CustomerContact.objects.filter(pharmacy=pharmacy, phone=phone).first()
        if contact:
            changed = []
            if name and contact.name != name:
                contact.name = name; changed.append('name')
            if not contact.is_active:
                contact.is_active = True; changed.append('is_active')
            if changed:
                changed.append('updated_at'); contact.save(update_fields=changed)
            return contact
    if name:
        contact = CustomerContact.objects.filter(pharmacy=pharmacy, name__iexact=name).order_by('id').first()
        if contact:
            if phone and contact.phone != phone:
                # Do not silently collide with another customer. In that case the
                # new sale gets its own contact record and the manager can merge
                # the records intentionally later.
                if not CustomerContact.objects.filter(pharmacy=pharmacy, phone=phone).exists():
                    contact.phone = phone
                    contact.is_active = True
                    contact.save(update_fields=['phone', 'is_active', 'updated_at'])
            return contact
    return CustomerContact.objects.create(pharmacy=pharmacy, name=name or phone, phone=phone, created_by=user)


def _phone_search_digits(q):
    """Return the national significant digits typed so far, or None if ``q`` is not phone-like.

    Understands +255712..., 255712..., 0712..., 712... and partial input such as
    "06", "6", "+25". Spaces, dashes and brackets are ignored.
    """
    v = re.sub(r'[\s\-().]', '', q or '')
    if not v or not re.fullmatch(r'\+?[0-9]+', v):
        return None
    if v.startswith('+'):
        v = v[1:]
        # "+2", "+25" are a partial country code: nothing national typed yet.
        if '255'.startswith(v):
            return ''
        if v.startswith('255'):
            return v[3:]
        return v
    if v.startswith('00255'):
        return v[5:]
    if v.startswith('255'):
        return v[3:]
    if '255'.startswith(v) and len(v) > 1:  # "25" -> still typing country code
        return ''
    if v.startswith('0'):
        return v[1:]
    return v


def _branch_mismatch_response(request, claimed):
    """409 when the page that sent a POS request belongs to a different branch than the session.

    A manager can switch branch in one tab while another tab still shows the old
    branch's products/cart. Without this guard that stale tab would sell old-branch
    items against the newly active branch.
    """
    try:
        claimed = int(claimed) if claimed not in (None, '') else None
    except (TypeError, ValueError):
        claimed = None
    if claimed is None or request.pharmacy is None or claimed == request.pharmacy.id:
        return None
    return JsonResponse({
        'success': False,
        'branch_changed': True,
        'active_branch': request.pharmacy.name,
        'error': (f"The active branch is now '{request.pharmacy.name}', but this page was opened for another branch. "
                  "Nothing was sold. Reload the POS page to continue in the correct branch."),
    }, status=409)


@employee_or_admin_required
@require_http_methods(["GET"])
def pos_context_api(request):
    """Tiny no-store endpoint the POS page polls on focus to detect a branch switch made elsewhere."""
    resp = JsonResponse({
        'branch_id': request.pharmacy.id if request.pharmacy else None,
        'branch_name': request.pharmacy.name if request.pharmacy else '',
    })
    resp['Cache-Control'] = 'no-store'
    return resp


@employee_or_admin_required
@require_http_methods(["GET"])
def customer_search_api(request):
    """Fast, branch-scoped customer lookup used by the POS customer panel.

    Works for names and for phone numbers typed in any local format.
    ``exact`` is true when a complete phone number matches exactly one customer,
    which lets the POS auto-fill without the cashier picking from a list.
    """
    if request.pharmacy is None:
        return JsonResponse({'results': [], 'exact': False})
    q = (request.GET.get('q') or '').strip()
    base = CustomerContact.objects.filter(pharmacy=request.pharmacy, is_active=True)
    national = _phone_search_digits(q)

    if national is not None:
        if national == '':
            return JsonResponse({'results': [], 'exact': False})
        canonical = '+255' + national
        cond = Q(phone__startswith=canonical)
        if len(national) >= 4:
            cond |= Q(phone__contains=national)
        # Non-Tanzanian numbers (e.g. +44...) are matched by their literal prefix.
        raw = re.sub(r'[\s\-().]', '', q)
        if raw.startswith('+') and not raw.startswith('+255'):
            cond |= Q(phone__startswith=raw)
        qs = list(base.filter(cond).order_by('name', 'id')[:30])
        # Prefix matches first, then looser "contains" matches.
        qs.sort(key=lambda c: (not c.phone.startswith(canonical), c.name.lower(), c.id))
        qs = qs[:8]
        exact = len(national) == 9 and any(c.phone == canonical for c in qs)
    else:
        if len(q) < 2:
            return JsonResponse({'results': [], 'exact': False})
        qs = list(base.filter(Q(name__icontains=q) | Q(phone__icontains=q)).order_by('name', 'id')[:8])
        exact = False

    results = []
    for c in qs:
        loyalty = _customer_loyalty_snapshot(request.pharmacy, c)
        results.append({
            'id': c.id, 'name': c.name, 'phone': c.phone,
            'loyalty_label': 'VIP' if loyalty['vip'] else ('LOYAL' if loyalty['visits'] >= 2 else 'REGULAR'),
            'vip': loyalty['vip'], 'visits_90d': loyalty['visits'],
            'spend_90d': str(loyalty['spend']),
            'vip_discount_percent': str(loyalty['discount_percent']),
            'vip_discount_type': loyalty['discount_type'],
            'vip_discount_amount': str(loyalty['discount_amount']),
            'vip_discount_label': loyalty['discount_label'],
        })
    return JsonResponse({'results': results, 'exact': exact})


@employee_or_admin_required
@require_http_methods(["POST"])
@csrf_protect
def checkout_view(request):
    """Online, interactive checkout — stock is live, so this can safely reject upfront if insufficient."""
    if request.pharmacy is None:
        return JsonResponse({'success': False, 'error': 'No active branch is set for your account.'}, status=400)

    # v97.18: server-side lock. 423 (not 403) so the POS does not treat it as a session problem and retry.
    locked = workday_sales_block_message(request.user)
    if locked:
        return JsonResponse({'success': False, 'error': locked, 'workday_closed': True}, status=423)

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid request data.'}, status=400)

    mismatch = _branch_mismatch_response(request, payload.get('branch_id'))
    if mismatch is not None:
        return mismatch

    # Late entry: a sale that really happened on an earlier day (paper notebook) typed in now.
    # Everything (reports, drawer, cash control) uses the sale's own business date.
    try:
        late = late_entry_rules.parse_late_entry(payload)
    except late_entry_rules.LateEntryError as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=exc.status)
    business_date = late.business_date if late else timezone.localdate()

    cart = payload.get('cart', [])
    payment_method = payload.get('payment_method', Sale.PaymentMethod.CASH)
    allow_layaway_reserved_sale = payload.get('allow_layaway_reserved_sale') is True
    layaway_override_reason = str(payload.get('layaway_override_reason') or '').strip()[:500]
    is_layaway_override_manager = bool(request.user.is_superuser or (callable(getattr(request.user, 'is_management_user', None)) and request.user.is_management_user()))
    if allow_layaway_reserved_sale and not is_layaway_override_manager:
        return JsonResponse({'success': False, 'error': 'Only a shop owner/manager or authorised assistant can approve a sale against unpaid Layaway reservations.'}, status=403)
    if allow_layaway_reserved_sale and len(layaway_override_reason) < 8:
        return JsonResponse({'success': False, 'error': 'Enter a clear reason (at least 8 characters) for releasing unpaid Layaway reservations.'}, status=400)
    customer_name = payload.get('customer_name', '').strip()
    customer_phone = payload.get('customer_phone', '').strip()
    if customer_phone:
        try:
            customer_phone = normalize_phone_number(customer_phone)
        except Exception as exc:
            return JsonResponse({'success': False, 'error': str(exc)}, status=400)

    if not cart:
        return JsonResponse({'success': False, 'error': 'Your cart is empty.'}, status=400)

    # Free Plan: once this month's free sales are used, new sales pause until the owner upgrades.
    # Sales that only contain sample products never count, so a new customer can always practise.
    from hq.free_plan import sales_block_message
    only_samples = not Product.objects.filter(
        pharmacy=request.pharmacy, id__in=[i.get('product_id') for i in cart if isinstance(i, dict)], is_demo=False,
    ).exists()
    block = None if only_samples else sales_block_message(request.pharmacy)
    if block:
        return JsonResponse({'success': False, 'error': block, 'upgrade_required': True, 'upgrade_url': reverse('hq:packages_center') + '?renew=1'}, status=402)

    if payment_method == Sale.PaymentMethod.DEBT and not customer_name:
        return JsonResponse({'success': False, 'error': 'Customer name is required for debt sales.'}, status=400)

    if late is None and payment_method in (Sale.PaymentMethod.CASH, Sale.PaymentMethod.SPLIT):
        # Previous days may still be awaiting physical reconciliation. That must
        # never stop today's trading. A physical close is an accounting snapshot,
        # not a POS shutdown; any sale after it is automatically treated as
        # post-close activity by CashDay audit properties.
        from expenses.services import get_current_cash_day_for_sale, CashSaleBlocked, CashSaleDayNotReady
        try:
            get_current_cash_day_for_sale(request.pharmacy, request.user)
        except (CashSaleBlocked, CashSaleDayNotReady) as exc:
            return JsonResponse({'success': False, 'error': str(exc)}, status=409)

    try:
        with transaction.atomic():
            closed_day_correction = False
            late_cash_day = None
            is_owner_side_user = bool(request.user.is_superuser or request.user.is_management_user())
            if late is not None:
                # The money belongs to the sale's own day: lock THAT day's cash control, not today's.
                if late_entry_rules.needs_cash_day(payment_method, payload.get('deposit_amount'), payload.get('deposit_method')):
                    try:
                        late_cash_day, closed_day_correction = late_entry_rules.lock_cash_day_for_late_entry(
                            request.pharmacy, late.business_date,
                            is_owner_side=is_owner_side_user,
                            acknowledged=payload.get('ack_closed_day') is True,
                        )
                    except late_entry_rules.LateEntryError as exc:
                        body = {'success': False, 'error': str(exc)}
                        if exc.code == 'closed_day':
                            body.update({'closed_day_correction': True, 'can_ack': exc.can_ack})
                            if not exc.can_ack:
                                late_entry_rules.notify_owner(
                                    request.pharmacy, 'Late cash sale needs the owner',
                                    f'{request.user.get_full_name() or request.user.get_username()} tried to record a cash sale '
                                    f'for {late.business_date:%d %b %Y}, but that cash day is closed. Please record it from the POS '
                                    f'(earlier-day sale) or ask them for the details.',
                                    link_url=reverse('sales:pos'),
                                    dedupe_key=f'late-closed-{request.pharmacy.pk}-{request.user.pk}-{late.business_date}')
                        return JsonResponse(body, status=exc.status)
            elif payment_method in (Sale.PaymentMethod.CASH, Sale.PaymentMethod.SPLIT):
                from expenses.services import lock_cash_day_for_sale, CashSaleBlocked, CashSaleDayNotReady
                try:
                    lock_cash_day_for_sale(request.pharmacy, request.user)
                except (CashSaleBlocked, CashSaleDayNotReady) as exc:
                    return JsonResponse({'success': False, 'error': str(exc)}, status=409)
            product_ids = [item['product_id'] for item in cart]
            products = {
                p.id: p for p in Product.objects.filter(
                    id__in=product_ids, pharmacy=request.pharmacy
                ).prefetch_related('batches')
            }

            # Pre-flight: protect Layaway reservations by default. Manager override can release ONLY
            # unpaid ACTIVE plans into Needs Resolution; fully-paid plans are never overridden.
            from layaway.models import LayawayPlan, LayawayPayment, LayawayAudit
            from django.db.models import Sum as _Sum
            override_events = []
            pending_release_plans = []
            for item in cart:
                product = products.get(item['product_id'])
                qty = int(item['quantity'])
                if not product:
                    return JsonResponse({'success': False, 'error': 'A product in your cart no longer exists.'}, status=400)
                sellable = product.sellable_quantity
                if qty > sellable:
                    return JsonResponse({
                        'success': False,
                        'error': f"Not enough sellable stock for '{product.name}'. Only {sellable} available (expired stock is excluded)."
                    }, status=400)
                # Lock concrete LayawayPlan rows only. Do not annotate/aggregate
                # on this select_for_update() queryset: PostgreSQL rejects FOR UPDATE
                # when GROUP BY is introduced by the handover SUM, causing checkout
                # to fail even for products with no active reservations.
                reservations = list(LayawayPlan.objects.select_for_update().filter(
                    pharmacy=request.pharmacy, product=product,
                    status__in=(LayawayPlan.Status.ACTIVE, LayawayPlan.Status.PARTIALLY_HANDED_OVER, LayawayPlan.Status.PAID_PENDING_HANDOVER, LayawayPlan.Status.NEEDS_RESOLUTION),
                    reservation_released=False,
                ).order_by('created_at', 'pk'))
                # Calculate handed-over quantities in a separate, read-only aggregate
                # query after locking the plans. This preserves row-level locks on the
                # plan records while avoiding FOR UPDATE + GROUP BY SQL.
                handed_by_plan = {
                    row['pk']: int(row['handed_qty'] or 0)
                    for row in LayawayPlan.objects.filter(pk__in=[p.pk for p in reservations])
                    .values('pk')
                    .annotate(handed_qty=_Sum('handovers__quantity'))
                    .values('pk', 'handed_qty')
                }
                # Use the actual related key consistently (the values() alias above
                # is plan pk; normalize it to an integer lookup key).
                reserved_total = sum(
                    max(0, int(p.quantity) - int(handed_by_plan.get(p.pk, 0)))
                    for p in reservations
                )
                unreserved = max(0, sellable - reserved_total)
                if qty <= unreserved:
                    continue
                needed_from_reserved = qty - unreserved
                if not allow_layaway_reserved_sale:
                    return JsonResponse({
                        'success': False,
                        'error': f"'{product.name}' has {unreserved} unreserved unit(s) available; {reserved_total} unit(s) are reserved for Layaway. A shop manager must approve and give a reason to sell against an unpaid reservation. Fully-paid customer reservations remain protected.",
                        'layaway_reservation_conflict': True,
                    }, status=409)
                eligible = []
                for reserved_plan in reservations:
                    if reserved_plan.status not in (LayawayPlan.Status.ACTIVE, LayawayPlan.Status.PARTIALLY_HANDED_OVER):
                        continue
                    paid_sum = reserved_plan.payments.filter(status=LayawayPayment.Status.RECEIVED).aggregate(v=_Sum('amount'))['v'] or Decimal('0')
                    if paid_sum < reserved_plan.total_amount:
                        eligible.append((reserved_plan, paid_sum))
                releasable = sum(max(0, int(plan.quantity) - int(handed_by_plan.get(plan.pk, 0))) for plan, _paid in eligible)
                if releasable < needed_from_reserved:
                    return JsonResponse({
                        'success': False,
                        'error': f"Cannot override '{product.name}'. The sale would consume stock protected for a fully-paid Layaway customer or exceed unpaid reservations available for release. Restock or resolve the customer plan first.",
                        'layaway_reservation_conflict': True,
                    }, status=409)
                released = 0
                for reserved_plan, paid_sum in eligible:
                    if released >= needed_from_reserved:
                        break
                    if reserved_plan.pk not in {row[0].pk for row in pending_release_plans}:
                        pending_release_plans.append((reserved_plan, product.name, max(0, int(reserved_plan.quantity) - int(handed_by_plan.get(reserved_plan.pk, 0))), paid_sum, layaway_override_reason))
                        released += max(0, int(reserved_plan.quantity) - int(handed_by_plan.get(reserved_plan.pk, 0)))
                if released < needed_from_reserved:
                    return JsonResponse({'success': False, 'error': f"Not enough unpaid Layaway reservation quantity can be released for '{product.name}'.", 'layaway_reservation_conflict': True}, status=409)
            # Apply reservation changes only after every cart line has passed pre-flight.
            # This prevents a later validation failure from leaving a released plan behind.
            for reserved_plan, product_name, released_qty, paid_sum, reason in pending_release_plans:
                resolution = (f"POS stock released by manager-approved sale. Reason: {reason}. "
                              f"Sale operator: {request.user.get_username()}. Customer paid TZS {paid_sum:,.2f} of TZS {reserved_plan.total_amount:,.2f}; manager must contact customer and resolve/refund/adjust the plan.")
                reserved_plan.status = LayawayPlan.Status.NEEDS_RESOLUTION
                reserved_plan.reservation_released = True
                reserved_plan.resolution_note = resolution
                reserved_plan.resolved_at = timezone.now()
                reserved_plan.resolved_by = request.user
                reserved_plan.save(update_fields=['status', 'reservation_released', 'resolution_note', 'resolved_at', 'resolved_by'])
                LayawayAudit.objects.create(plan=reserved_plan, actor=request.user, action='RESERVATION_RELEASED_FOR_POS_SALE', detail=resolution)
                override_events.append((reserved_plan, product_name, released_qty, reason))

            customer_contact = _get_or_create_customer_contact(request.pharmacy, request.user, customer_name, customer_phone)
            prior_loyalty = _customer_loyalty_snapshot(request.pharmacy, customer_contact, business_date)
            late_fields = {}
            if late is not None:
                is_owner_side = is_owner_side_user
                review_text = late.review_note(request.user) if not is_owner_side else ''
                if closed_day_correction:
                    review_text = (review_text + ' ' if review_text else '') + (
                        f'{late_entry_rules.CLOSED_DAY_MARK} ({late.business_date:%d %b %Y}): open Cash Control and use '
                        f'Reopen & Recount for that day to reconcile the drawer. It resolves itself once the day is closed again.')
                late_fields = dict(
                    is_late_entry=True, late_entry_reason=late.reason, late_entry_note=late.note,
                    # Owner/manager entries are trusted; an employee's are queued for the owner to verify.
                    # A sale posted onto a closed cash day stays on the review list until it is reconciled.
                    needs_review=(not is_owner_side) or closed_day_correction,
                    review_note=review_text,
                )
            sale = Sale.objects.create(
                pharmacy=request.pharmacy, served_by=request.user, payment_method=payment_method,
                customer_name=customer_name, customer_phone=customer_phone, customer_contact=customer_contact,
                business_date=business_date, **late_fields,
            )

            vip_discount_value, vip_discount_type = _vip_discount_for_customer(request.pharmacy, customer_contact, business_date)
            total_amount = Decimal('0')
            expected_total_basis = Decimal('0')
            for item in cart:
                product = products[item['product_id']]
                fulfilled, line_total, pre_vip_line_total = _fulfil_line_fefo(product, int(item['quantity']), sale, request.user, discount_percent=_cart_discount_value(product, item), note_prefix=(late.stock_note_prefix() if late else ""), vip_discount_percent=vip_discount_value, vip_discount_type=vip_discount_type)
                total_amount += line_total
                expected_total_basis += pre_vip_line_total
                if fulfilled < int(item['quantity']):
                    # Should not happen given the pre-flight check, but guard anyway
                    raise ValueError(f"Could not fulfil the requested quantity for '{product.name}'.")

            expected_total_raw = payload.get('expected_total')
            if expected_total_raw not in (None, ''):
                try:
                    expected_total = Decimal(str(expected_total_raw)).quantize(Decimal('0.01'))
                except (InvalidOperation, TypeError, ValueError):
                    raise ValueError('Invalid expected sale total. Refresh the POS and try again.')
                actual_total = expected_total_basis.quantize(Decimal('0.01'))
                if actual_total != expected_total:
                    # The transaction is atomic, so the sale and stock deductions
                    # are rolled back. This prevents the cashier from charging a
                    # customer using an old batch price while the server has moved
                    # to a different batch price.
                    raise ValueError(
                        f'Batch pricing changed while this sale was being completed. '
                        f'Expected TZS {expected_total:,.2f}, current total is TZS {actual_total:,.2f}. '
                        'Refresh the cart and confirm the customer price again.'
                    )

            if payment_method == Sale.PaymentMethod.SPLIT:
                _apply_split_payment(sale, payload.get('cash_paid'), total_amount)

            if payment_method == Sale.PaymentMethod.DEBT:
                new_debt = Debt.objects.create(
                    pharmacy=request.pharmacy, sale=sale, customer_name=customer_name,
                    customer_phone=customer_phone, amount_owed=total_amount,
                )
                _apply_pos_deposit(new_debt, payload.get('deposit_amount'), payload.get('deposit_method'), request.user,
                                   paid_at=late_entry_rules.paid_at_for(late) if late else None)

            if late is not None and not is_owner_side_user:
                late_entry_rules.notify_owner(
                    request.pharmacy, 'Late entry to verify',
                    f'{request.user.get_full_name() or request.user.get_username()} recorded sale '
                    f'#{sale.sale_number or sale.pk} (TZS {total_amount:,.0f}) for {late.business_date:%d %b %Y}. '
                    f'Compare it with the paper record.',
                    link_url=reverse('sales:needs_review'), dedupe_key=f'late-entry-{sale.pk}', level='INFO')
            if closed_day_correction and late_cash_day is not None:
                late_entry_rules.notify_owner(
                    request.pharmacy, 'Reconcile a closed cash day',
                    f'A late cash sale was posted to {late.business_date:%d %b %Y}, which was already closed. '
                    f'Use Reopen & Recount to reconcile the drawer.',
                    link_url=reverse('sales:late_entry_reconcile'), dedupe_key=f'late-recon-{request.pharmacy.pk}-{late.business_date}')
                # Immutable audit row: who posted cash onto a verified day, how much, and why.
                from expenses.models import CashAuditEvent
                from expenses.services import record_audit
                record_audit(
                    late_cash_day, CashAuditEvent.Action.POST_CLOSE_DETECTED, request.user,
                    old=late_cash_day.actual_closing_cash, new=f'Late entry: sale #{sale.sale_number or sale.pk}',
                    reason=(f'Sale #{sale.sale_number or sale.pk} (TZS {total_amount:,.0f}, {payment_method}) made on '
                            f'{late.business_date:%d %b %Y} was posted after the day was closed. Reason: '
                            f'{late.reason}. {late.note}').strip(),
                    request=request,
                )

            current_loyalty = _customer_loyalty_snapshot(request.pharmacy, customer_contact, business_date)
            if customer_contact and not prior_loyalty['vip'] and current_loyalty['vip']:
                _notify_customer_became_vip(request.pharmacy, customer_contact, current_loyalty)

            if override_events:
                # All changes are in the same transaction as the sale; if checkout fails, released reservations roll back too.
                from layaway.models import LayawayAudit
                for released_plan, product_name, released_qty, reason in override_events:
                    LayawayAudit.objects.create(
                        plan=released_plan, actor=request.user, action='POS_SALE_AFTER_RESERVATION_RELEASE',
                        detail=(f"Manager-approved POS sale #{sale.sale_number or sale.pk} of {product_name} required releasing this unpaid plan's reservation. "
                                f"Reason: {reason}. Released plan quantity: {released_qty}. Customer follow-up is required."),
                    )
            return JsonResponse({'success': True, 'sale_id': sale.id, 'receipt_no': f'PM-{request.pharmacy.id}-{(sale.sale_number or sale.id):08d}', 'total': str(total_amount), 'layaway_reservations_released': len(override_events),
                                 'late_entry': late is not None, 'business_date': business_date.isoformat(),
                                 'closed_day_correction': closed_day_correction})

    except Exception as exc:  # noqa: BLE001
        return JsonResponse({'success': False, 'error': f'Checkout failed: {exc}'}, status=500)


def _offline_sale_made_after_close(entry, closed_at):
    """True when a queued offline sale was made at/after the employee closed the workday.

    A sale queued BEFORE the close is legitimate even if it syncs later. A sale with a
    missing or unreadable ``queued_at`` is treated as after-close (fail closed).
    """
    from django.utils.dateparse import parse_datetime
    if closed_at is None:
        return True
    try:
        queued = parse_datetime(str(entry.get('queued_at') or '').strip())
    except (ValueError, TypeError):
        queued = None
    if queued is None:
        return True
    if timezone.is_naive(queued):
        queued = timezone.make_aware(queued)
    return queued >= closed_at


@employee_or_admin_required
@require_http_methods(["POST"])
@csrf_protect
def sync_offline_sales_view(request):
    """
    Accepts a batch of sales that were made while the device had no
    connection and queued locally. Each sale is matched to real stock in
    FEFO order — if stock fully covers it, it completes exactly like a
    normal sale. If NOT (another device sold the same stock while this one
    was offline), the sale is still recorded in full — never dropped — but
    flagged needs_review so the owner can resolve it deliberately instead of
    the system silently overselling.

    Idempotent: each sale carries a client-generated UUID, so retrying a
    sync (e.g. connection drops mid-upload) never creates duplicates.
    """
    if request.pharmacy is None:
        return JsonResponse({'results': []})

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'error': 'Invalid request data.'}, status=400)

    mismatch = _branch_mismatch_response(request, payload.get('branch_id'))
    if mismatch is not None:
        return mismatch

    offline_sales = payload.get('sales', [])
    results = []
    closed_today = closed_workday_for(request.user)  # v97.18: employee's closed workday for today, if any
    closed_at_today = closed_moment(closed_today)

    for entry in offline_sales:
        client_uuid = entry.get('client_uuid')
        if not client_uuid:
            results.append({'client_uuid': None, 'status': 'error', 'message': 'Missing client_uuid'})
            continue

        existing = Sale.objects.filter(client_uuid=client_uuid).first()
        if existing:
            results.append({'client_uuid': client_uuid, 'status': 'already_synced', 'sale_id': existing.id})
            continue

        cart = entry.get('cart', [])
        payment_method = entry.get('payment_method', Sale.PaymentMethod.CASH)
        customer_name = (entry.get('customer_name') or '').strip()
        customer_phone = (entry.get('customer_phone') or '').strip()
        raw_business_date = (entry.get('business_date') or '').strip()
        try:
            from datetime import date as _date
            business_date = _date.fromisoformat(raw_business_date) if raw_business_date else timezone.localdate()
        except ValueError:
            results.append({'client_uuid': client_uuid, 'status': 'error', 'message': 'Invalid offline business date.'})
            continue
        current_business_date = timezone.localdate()
        if business_date > current_business_date:
            results.append({'client_uuid': client_uuid, 'status': 'error', 'message': 'Offline sale date cannot be in the future.'})
            continue
        if closed_today is not None and business_date == current_business_date and _offline_sale_made_after_close(entry, closed_at_today):
            results.append({'client_uuid': client_uuid, 'status': 'blocked_workday_closed',
                            'message': workday_sales_block_message(request.user)})
            continue
        if customer_phone:
            try:
                customer_phone = normalize_phone_number(customer_phone)
            except Exception as exc:
                results.append({'client_uuid': client_uuid, 'status': 'error', 'message': str(exc)})
                continue

        if not cart:
            results.append({'client_uuid': client_uuid, 'status': 'error', 'message': 'Empty cart'})
            continue

        try:
            with transaction.atomic():
                delayed_offline = business_date < current_business_date
                if payment_method in (Sale.PaymentMethod.CASH, Sale.PaymentMethod.SPLIT):
                    from expenses.models import CashDay
                    if delayed_offline:
                        historical_day = CashDay.objects.filter(pharmacy=request.pharmacy, date=business_date).first()
                        if historical_day is None:
                            results.append({'client_uuid': client_uuid, 'status': 'blocked_historical_business_date', 'message': f'No cash-control record exists for {business_date}. Manager review is required before this offline cash sale can be posted.'})
                            continue
                        if historical_day.status == CashDay.Status.CLOSED:
                            results.append({'client_uuid': client_uuid, 'status': 'blocked_historical_closed', 'message': f'Cash day {business_date} is already physically closed. Reopen it through Cash Control before posting this offline sale.'})
                            continue
                    else:
                        from expenses.services import lock_cash_day_for_sale, CashSaleBlocked, CashSaleDayNotReady
                        try:
                            lock_cash_day_for_sale(request.pharmacy, request.user)
                        except (CashSaleBlocked, CashSaleDayNotReady) as exc:
                            results.append({'client_uuid': client_uuid, 'status': 'blocked_cash_day', 'message': str(exc)})
                            continue
                customer_contact = _get_or_create_customer_contact(request.pharmacy, request.user, customer_name, customer_phone)
                sale = Sale.objects.create(
                    pharmacy=request.pharmacy, served_by=request.user, payment_method=payment_method,
                    customer_name=customer_name, customer_phone=customer_phone, customer_contact=customer_contact,
                    business_date=business_date,
                    client_uuid=client_uuid, synced_offline=True,
                    needs_review=delayed_offline,
                    review_note=(f'Offline cash sale made on {business_date} and synchronized on {current_business_date}; verify the historical cash-day reconciliation.' if delayed_offline else ''),
                )

                vip_discount_value, vip_discount_type = _vip_discount_for_customer(request.pharmacy, customer_contact, business_date)
                total_amount = Decimal('0')
                expected_total_basis = Decimal('0')
                shortfalls = []
                for item in cart:
                    product = Product.objects.filter(id=item.get('product_id'), pharmacy=request.pharmacy).first()
                    requested_qty = int(item.get('quantity') or 0)
                    if not product:
                        shortfalls.append(f"Product #{item.get('product_id')} no longer exists")
                        continue
                    fulfilled, line_total, pre_vip_line_total = _fulfil_line_fefo(
                        product, requested_qty, sale, request.user,
                        discount_percent=_cart_discount_value(product, item),
                        note_prefix="[Offline sync] ", vip_discount_percent=vip_discount_value, vip_discount_type=vip_discount_type
                    )
                    total_amount += line_total
                    expected_total_basis += pre_vip_line_total
                    if fulfilled < requested_qty:
                        shortfalls.append(f"{product.name}: requested {requested_qty}, only {fulfilled} available")

                expected_total_raw = entry.get('expected_total')
                if expected_total_raw not in (None, ''):
                    try:
                        expected_total = Decimal(str(expected_total_raw)).quantize(Decimal('0.01'))
                        actual_total = expected_total_basis.quantize(Decimal('0.01'))
                        if actual_total != expected_total:
                            shortfalls.append(
                                f'Offline batch pricing changed: customer snapshot TZS {expected_total:,.2f}, '
                                f'server batch total TZS {actual_total:,.2f}'
                            )
                    except (InvalidOperation, TypeError, ValueError):
                        shortfalls.append('Invalid offline expected total; manager review required.')
                if payment_method == Sale.PaymentMethod.SPLIT:
                    try:
                        _apply_split_payment(sale, entry.get('cash_paid'), total_amount)
                    except ValueError as exc:
                        shortfalls.append(f'Split payment needs review: {exc}')
                        sale.payment_method = Sale.PaymentMethod.CASH
                if shortfalls:
                    sale.needs_review = True
                    sale.review_note = "; ".join(shortfalls)
                sale.save()

                if payment_method == Sale.PaymentMethod.DEBT and total_amount > 0:
                    new_debt = Debt.objects.create(
                        pharmacy=request.pharmacy, sale=sale, customer_name=customer_name,
                        customer_phone=customer_phone, amount_owed=total_amount,
                    )
                    _apply_pos_deposit(new_debt, entry.get('deposit_amount'), entry.get('deposit_method'), request.user,
                                       paid_at=sale.created_at)

                results.append({
                    'client_uuid': client_uuid, 'status': 'needs_review' if shortfalls else 'ok',
                    'sale_id': sale.id, 'total': str(total_amount),
                })
        except Exception as exc:  # noqa: BLE001
            results.append({'client_uuid': client_uuid, 'status': 'error', 'message': str(exc)})

    return JsonResponse({'results': results})


@admin_required
def sales_needing_review_view(request):
    if request.pharmacy is None:
        sales = Sale.objects.none()
    else:
        late_entry_rules.auto_resolve_reconciled(request.pharmacy, request.user)
        sales = (
            Sale.objects.filter(pharmacy=request.pharmacy, needs_review=True)
            .select_related('served_by').prefetch_related('items__product', 'debt').order_by('-created_at')
        )
    paginator = Paginator(sales, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'sales/needs_review.html', {'page_obj': page_obj})


@admin_required
def late_entry_reconcile_view(request):
    """Owner's guided flow for late cash sales posted onto an already-closed cash day."""
    if request.pharmacy is None:
        return redirect('pharmacies:list')
    late_entry_rules.auto_resolve_reconciled(request.pharmacy, request.user)
    return render(request, 'sales/late_entry_reconcile.html', {
        'rows': late_entry_rules.reconcile_overview(request.pharmacy),
    })


@admin_required
@require_http_methods(["POST"])
def mark_sale_reviewed_view(request, pk):
    sale = get_object_or_404(Sale, pk=pk, pharmacy=request.pharmacy, needs_review=True)
    sale.needs_review = False
    sale.reviewed_at = timezone.now()
    sale.reviewed_by = request.user
    sale.save(update_fields=['needs_review', 'reviewed_at', 'reviewed_by'])
    messages.success(request, f"Sale #{sale.id} imewekwa kama imeshughulikiwa (marked as reviewed).")
    return redirect('sales:needs_review')


@employee_or_admin_required
def sale_list_view(request):
    search_query = request.GET.get('q', '').strip()[:120]
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    from django.utils.dateparse import parse_date
    date_from_value = parse_date(date_from) if date_from else None
    date_to_value = parse_date(date_to) if date_to else None
    payment_filter = request.GET.get('payment', '').strip().upper()
    status_filter = request.GET.get('status', '').strip().upper()

    seller_rows, employee_id = [], ''
    if request.pharmacy is None:
        sales = Sale.objects.none()
    else:
        sales = Sale.objects.filter(pharmacy=request.pharmacy).select_related('served_by', 'customer_contact').prefetch_related('items__product', 'debt__payments')
        if request.user.is_employee_role():
            # An employee sees their own sales AND every sale made at THEIR branch by a
            # Shop Owner / Manager Assistant who manages it (the boss may switch to this
            # branch and sell from his own account). Read-only: returns/edits stay limited
            # to the employee's own sales (see sales.returns.user_can_request).
            branch_managers = User.objects.filter(
                role=User.Role.ADMIN, managed_pharmacies=request.pharmacy,
            ).values('pk')
            sales = sales.filter(
                Q(served_by=request.user)
                | Q(served_by__in=branch_managers)
                | Q(served_by__assistant_profile__manager__in=branch_managers)
            )
        # "Who sold it?" - everybody can trace sales by seller inside the sales they are already
        # allowed to see (an employee: own + the branch owner/manager's; management: everyone).
        # The list is built BEFORE the seller filter so the dropdown never shrinks to one person.
        seller_ids = list(sales.exclude(served_by__isnull=True).values_list('served_by_id', flat=True).distinct())
        seller_rows = list(User.objects.filter(pk__in=seller_ids).order_by('first_name', 'last_name', 'username'))
        employee_id = (request.GET.get('employee') or '').strip()
        if employee_id == 'me':
            sales = sales.filter(served_by=request.user)
        elif employee_id.isdigit() and int(employee_id) in set(seller_ids):
            sales = sales.filter(served_by_id=int(employee_id))
        else:
            employee_id = ''

        # Search by accounting/business date, sale number, product, customer, or cashier.
        if search_query:
            for term in search_query.split():
                term_q = (Q(items__product__name__icontains=term) |
                          Q(customer_name__icontains=term) |
                          Q(customer_phone__icontains=term) |
                          Q(customer_contact__name__icontains=term) |
                          Q(customer_contact__phone__icontains=term) |
                          Q(served_by__username__icontains=term) |
                          Q(served_by__first_name__icontains=term) |
                          Q(served_by__last_name__icontains=term))
                numeric_term = term.lstrip('#')
                if numeric_term.isdigit():
                    term_q |= Q(sale_number=int(numeric_term)) | Q(pk=int(numeric_term))
                sales = sales.filter(term_q)
            sales = sales.distinct()
        if date_from_value:
            sales = sales.filter(business_date__gte=date_from_value)
        if date_to_value:
            sales = sales.filter(business_date__lte=date_to_value)
        valid_payments = {choice[0] for choice in Sale.PaymentMethod.choices}
        if payment_filter in valid_payments:
            sales = sales.filter(payment_method=payment_filter)
        valid_statuses = {choice[0] for choice in Sale.Status.choices}
        if status_filter in valid_statuses:
            sales = sales.filter(status=status_filter)
        if request.GET.get('entry', '').strip().lower() == 'late':
            sales = sales.filter(is_late_entry=True)
        # Newest business day first; inside a day, newest entry first. A late entry therefore
        # sits under the day it really happened instead of jumping to the top of today.
        sales = sales.order_by('-business_date', '-created_at', '-pk')

    paginator = Paginator(sales, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Flags used by the template: is a return already waiting, and may THIS user request one?
    page_obj.object_list = list(page_obj.object_list)
    pending_sale_ids = set(
        SaleReturnRequest.objects.filter(
            sale__in=page_obj.object_list, status=SaleReturnRequest.Status.PENDING
        ).values_list('sale_id', flat=True)
    ) if page_obj.object_list else set()
    short_roles = {User.Role.ADMIN: ('Owner', 'owner'), User.Role.ASSISTANT: ('Manager', 'manager'),
                   User.Role.EMPLOYEE: ('Employee', 'employee')}
    for sale in page_obj.object_list:
        sale.has_pending_return = sale.pk in pending_sale_ids
        sale.can_request_return = returns.user_can_request(request.user, sale)
        seller = sale.served_by
        sale.seller_name = (seller.get_full_name() or seller.get_username()) if seller else 'Unknown user'
        sale.seller_role_label, sale.seller_role_key = short_roles.get(getattr(seller, 'role', None), ('Staff', 'employee'))
        sale.seller_is_me = bool(seller) and seller.pk == request.user.pk

    sellers = [{'id': u.pk, 'name': u.get_full_name() or u.get_username(), 'role': short_roles.get(u.role, ('Staff', ''))[0],
                'is_me': u.pk == request.user.pk} for u in seller_rows]
    # Totals for exactly what is on screen (voided sales are not revenue), so a filtered view
    # such as "everything Theodora sold on Saturday" can be checked at a glance.
    summary = {'count': 0, 'total': 0}
    if request.pharmacy is not None:
        counted = Sale.objects.filter(pk__in=sales.values('pk')).exclude(status=Sale.Status.VOIDED)
        # Sale.total_amount is a computed property (sum of item subtotals), not a column, so the
        # money total is aggregated from the items: unit_price x net quantity.
        from .models import SaleItem
        total = SaleItem.objects.filter(sale__in=counted.values('pk')).aggregate(
            t=Sum(F('unit_price') * F('quantity'), output_field=DecimalField(max_digits=16, decimal_places=2)))['t']
        summary = {'count': counted.count(), 'total': total or 0}
    selected_seller = next((x for x in sellers if str(x['id']) == employee_id), None)
    if employee_id == 'me':
        selected_seller = {'name': 'you', 'is_me': True}
    has_filters = any([search_query, date_from_value, date_to_value, payment_filter, status_filter,
                       employee_id, request.GET.get('entry', '').strip().lower() == 'late'])
    return render(request, 'sales/sale_list.html', {
        'page_obj': page_obj, 'sellers': sellers, 'employee_id': employee_id,
        'selected_seller': selected_seller, 'history_summary': summary, 'has_filters': has_filters,
    })


def _debt_report_queryset(request):
    """Return branch-scoped debt records with optional report filters."""
    if request.pharmacy is None:
        return Debt.objects.none()
    qs = Debt.objects.filter(pharmacy=request.pharmacy).select_related('sale', 'sale__served_by')
    status = request.GET.get('status', 'open').strip().lower()
    if status == 'settled':
        qs = qs.filter(is_settled=True)
    elif status == 'all':
        pass
    else:
        qs = qs.filter(is_settled=False)

    search = request.GET.get('q', '').strip()
    if search:
        qs = qs.filter(Q(customer_name__icontains=search) | Q(customer_phone__icontains=search))

    return qs.order_by('-created_at')


def _build_debt_analysis(debts):
    """Build management-friendly receivables analysis from current debt balances."""
    today = timezone.localdate()
    rows = []
    customer_map = {}
    total_owed = Decimal('0')
    total_paid = Decimal('0')
    total_balance = Decimal('0')
    open_count = 0
    settled_count = 0
    buckets = {
        'Current (0–30 days)': Decimal('0'),
        '31–60 days': Decimal('0'),
        '61–90 days': Decimal('0'),
        '91+ days': Decimal('0'),
    }
    bucket_counts = {k: 0 for k in buckets}

    for debt in debts:
        balance = max(Decimal('0'), debt.balance)
        owed = Decimal(debt.amount_owed or 0)
        paid = Decimal(debt.amount_paid or 0)
        age_days = max(0, (today - timezone.localtime(debt.created_at).date()).days)
        if age_days <= 30:
            bucket = 'Current (0–30 days)'
        elif age_days <= 60:
            bucket = '31–60 days'
        elif age_days <= 90:
            bucket = '61–90 days'
        else:
            bucket = '91+ days'
        buckets[bucket] += balance
        bucket_counts[bucket] += 1

        total_owed += owed
        total_paid += paid
        total_balance += balance
        if debt.is_settled or balance <= 0:
            settled_count += 1
        else:
            open_count += 1

        key = (debt.customer_phone.strip() if debt.customer_phone else '', debt.customer_name.strip().lower())
        if key not in customer_map:
            customer_map[key] = {
                'name': debt.customer_name,
                'phone': debt.customer_phone,
                'debt_count': 0,
                'owed': Decimal('0'),
                'paid': Decimal('0'),
                'balance': Decimal('0'),
                'oldest_days': 0,
                'open_count': 0,
            }
        c = customer_map[key]
        c['debt_count'] += 1
        c['owed'] += owed
        c['paid'] += paid
        c['balance'] += balance
        c['oldest_days'] = max(c['oldest_days'], age_days)
        if balance > 0:
            c['open_count'] += 1

        rows.append({
            'debt': debt,
            'age_days': age_days,
            'bucket': bucket,
            'balance': balance,
            'payment_ratio': (paid / owed * Decimal('100')) if owed else Decimal('100'),
        })

    customers = sorted(customer_map.values(), key=lambda x: x['balance'], reverse=True)
    open_balances = [r for r in rows if r['balance'] > 0]
    open_ages = [r['age_days'] for r in open_balances]
    avg_age = (Decimal(sum(open_ages)) / Decimal(len(open_ages))) if open_ages else Decimal('0')
    if open_ages:
        ordered_ages = sorted(open_ages)
        mid = len(ordered_ages) // 2
        median_age = Decimal(ordered_ages[mid]) if len(ordered_ages) % 2 else (Decimal(ordered_ages[mid - 1]) + Decimal(ordered_ages[mid])) / Decimal('2')
    else:
        median_age = Decimal('0')

    collection_rate = (total_paid / total_owed * Decimal('100')) if total_owed else Decimal('0')
    overdue_31 = buckets['31–60 days'] + buckets['61–90 days'] + buckets['91+ days']
    overdue_90 = buckets['91+ days']
    overdue_60 = buckets['61–90 days'] + overdue_90
    overdue_31_share = (overdue_31 / total_balance * Decimal('100')) if total_balance else Decimal('0')
    overdue_60_share = (overdue_60 / total_balance * Decimal('100')) if total_balance else Decimal('0')
    overdue_90_share = (overdue_90 / total_balance * Decimal('100')) if total_balance else Decimal('0')
    concentration = (customers[0]['balance'] / total_balance * Decimal('100')) if customers and total_balance else Decimal('0')
    open_customer_count = sum(1 for c in customers if c['balance'] > 0)
    customers_61_plus = sum(1 for c in customers if c['balance'] > 0 and c['oldest_days'] >= 61)
    no_payment_count = sum(1 for r in open_balances if Decimal(r['debt'].amount_paid or 0) <= 0)
    average_open_balance = (total_balance / Decimal(open_count)) if open_count else Decimal('0')

    return {
        'rows': rows,
        'customers': customers,
        'total_owed': total_owed,
        'total_paid': total_paid,
        'total_balance': total_balance,
        'open_count': open_count,
        'settled_count': settled_count,
        'customer_count': len(customers),
        'open_customer_count': open_customer_count,
        'buckets': buckets,
        'bucket_counts': bucket_counts,
        'aging_rows': [{'label': b, 'amount': buckets[b], 'count': bucket_counts[b]} for b in buckets],
        'avg_age': avg_age,
        'median_age': median_age,
        'average_open_balance': average_open_balance,
        'no_payment_count': no_payment_count,
        'customers_61_plus': customers_61_plus,
        'collection_rate': collection_rate,
        'overdue_31': overdue_31,
        'overdue_31_share': overdue_31_share,
        'overdue_60': overdue_60,
        'overdue_60_share': overdue_60_share,
        'overdue_90': overdue_90,
        'overdue_90_share': overdue_90_share,
        'top_customer_concentration': concentration,
    }


@employee_or_admin_required
def debt_reports_view(request):
    debts = list(_debt_report_queryset(request))
    analysis = _build_debt_analysis(debts)
    context = {
        'analysis': analysis,
        'status': request.GET.get('status', 'open').strip().lower() or 'open',
        'search': request.GET.get('q', '').strip(),
    }
    return render(request, 'sales/debt_reports.html', context)


@employee_or_admin_required
def debt_report_csv_view(request):
    debts = list(_debt_report_queryset(request))
    analysis = _build_debt_analysis(debts)
    pharmacy = request.pharmacy
    stamp = timezone.localtime().strftime('%Y%m%d_%H%M')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="debt_report_{pharmacy.id}_{stamp}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow([pharmacy.name, 'Debt & Receivables Analysis'])
    writer.writerow(['Generated', timezone.localtime().strftime('%Y-%m-%d %H:%M')])
    writer.writerow([])
    writer.writerow(['Portfolio Summary'])
    writer.writerow(['Total Debt Value', f'{analysis["total_owed"]:.2f}'])
    writer.writerow(['Total Paid Against Debt', f'{analysis["total_paid"]:.2f}'])
    writer.writerow(['Outstanding Balance', f'{analysis["total_balance"]:.2f}'])
    writer.writerow(['Open Debt Records', analysis['open_count']])
    writer.writerow(['Settled Debt Records', analysis['settled_count']])
    writer.writerow(['Unique Debtors', analysis['customer_count']])
    writer.writerow(['Collection Rate %', f'{analysis["collection_rate"]:.2f}'])
    writer.writerow(['Average Open Debt Age (days)', f'{analysis["avg_age"]:.1f}'])
    writer.writerow(['Median Open Debt Age (days)', f'{analysis["median_age"]:.1f}'])
    writer.writerow(['Average Outstanding per Open Debt', f'{analysis["average_open_balance"]:.2f}'])
    writer.writerow(['Open Debts with No Payment', analysis['no_payment_count']])
    writer.writerow(['Open Debtors', analysis['open_customer_count']])
    writer.writerow(['Debtors with 61+ Day Exposure', analysis['customers_61_plus']])
    writer.writerow(['Balance 31+ Days', f'{analysis["overdue_31"]:.2f}'])
    writer.writerow(['Balance 31+ Days Share %', f'{analysis["overdue_31_share"]:.2f}'])
    writer.writerow(['Balance 61+ Days', f'{analysis["overdue_60"]:.2f}'])
    writer.writerow(['Balance 61+ Days Share %', f'{analysis["overdue_60_share"]:.2f}'])
    writer.writerow(['Balance 91+ Days', f'{analysis["overdue_90"]:.2f}'])
    writer.writerow(['Balance 91+ Days Share %', f'{analysis["overdue_90_share"]:.2f}'])
    writer.writerow(['Largest Debtor Concentration %', f'{analysis["top_customer_concentration"]:.2f}'])
    writer.writerow([])
    writer.writerow(['Aging Analysis'])
    writer.writerow(['Age Bucket', 'Debt Records', 'Outstanding Balance'])
    for bucket, amount in analysis['buckets'].items():
        writer.writerow([bucket, analysis['bucket_counts'][bucket], f'{amount:.2f}'])
    writer.writerow([])
    writer.writerow(['Customer Summary'])
    writer.writerow(['Customer', 'Phone', 'Debt Records', 'Total Owed', 'Total Paid', 'Outstanding', 'Oldest Debt (days)', 'Open Debts'])
    for c in analysis['customers']:
        writer.writerow([c['name'], c['phone'], c['debt_count'], f'{c["owed"]:.2f}', f'{c["paid"]:.2f}', f'{c["balance"]:.2f}', c['oldest_days'], c['open_count']])
    writer.writerow([])
    writer.writerow(['Debt Detail'])
    writer.writerow(['Customer', 'Phone', 'Sale #', 'Debt Date', 'Age (days)', 'Age Bucket', 'Amount Owed', 'Amount Paid', 'Outstanding', 'Payment %', 'Status', 'Served By'])
    for r in analysis['rows']:
        d = r['debt']
        sale_no = d.sale.sale_number or d.sale.id
        writer.writerow([
            d.customer_name, d.customer_phone, sale_no,
            timezone.localtime(d.created_at).strftime('%Y-%m-%d'), r['age_days'], r['bucket'],
            f'{d.amount_owed:.2f}', f'{d.amount_paid:.2f}', f'{r["balance"]:.2f}',
            f'{r["payment_ratio"]:.2f}', 'Settled' if d.is_settled else 'Open',
            getattr(d.sale.served_by, 'get_full_name', lambda: '')() or getattr(d.sale.served_by, 'username', '') if d.sale.served_by else '',
        ])
    return response


@employee_or_admin_required
def debt_report_pdf_view(request):
    """Export the same debt analysis used by the screen/CSV as a branded PDF."""
    debts = list(_debt_report_queryset(request))
    analysis = _build_debt_analysis(debts)
    pharmacy = request.pharmacy
    stamp = timezone.localtime().strftime('%Y%m%d_%H%M')

    summary = [
        ('Branch', pharmacy.name if pharmacy else '-'),
        ('Portfolio', {'open': 'Open debts', 'settled': 'Settled debts', 'all': 'All debts'}.get(
            request.GET.get('status', 'open').strip().lower(), 'Open debts')),
        ('Search filter', request.GET.get('q', '').strip() or 'All customers'),
        ('Outstanding balance', f'TZS {analysis["total_balance"]:,.0f}'),
        ('Recorded debt value', f'TZS {analysis["total_owed"]:,.0f}'),
        ('Paid against debt', f'TZS {analysis["total_paid"]:,.0f}'),
        ('Collection rate', f'{analysis["collection_rate"]:.1f}%'),
        ('Open debt records', analysis['open_count']),
        ('Open debtors', analysis['open_customer_count']),
        ('Average open debt age', f'{analysis["avg_age"]:.1f} days'),
        ('Balance 31+ days', f'TZS {analysis["overdue_31"]:,.0f} ({analysis["overdue_31_share"]:.1f}%)'),
        ('Balance 61+ days', f'TZS {analysis["overdue_60"]:,.0f} ({analysis["overdue_60_share"]:.1f}%)'),
        ('Balance 91+ days', f'TZS {analysis["overdue_90"]:,.0f} ({analysis["overdue_90_share"]:.1f}%)'),
        ('Largest debtor share', f'{analysis["top_customer_concentration"]:.1f}%'),
    ]

    aging_rows = []
    for bucket in analysis['buckets']:
        amount = analysis['buckets'][bucket]
        share = (amount / analysis['total_balance'] * Decimal('100')) if analysis['total_balance'] else Decimal('0')
        aging_rows.append([bucket, analysis['bucket_counts'][bucket], f'TZS {amount:,.0f}', f'{share:.1f}%'])

    customer_rows = []
    for c in analysis['customers'][:20]:
        customer_rows.append([
            c['name'], c['phone'] or '—', c['debt_count'], c['open_count'],
            f'TZS {c["owed"]:,.0f}', f'TZS {c["paid"]:,.0f}',
            f'TZS {c["balance"]:,.0f}', f'{c["oldest_days"]} days',
        ])

    detail_rows = []
    for r in analysis['rows']:
        d = r['debt']
        sale_no = d.sale.sale_number or d.sale.id
        detail_rows.append([
            d.customer_name, str(sale_no), timezone.localtime(d.created_at).strftime('%d %b %Y'),
            r['age_days'], r['bucket'], f'TZS {d.amount_owed:,.0f}',
            f'TZS {d.amount_paid:,.0f}', f'TZS {r["balance"]:,.0f}',
            f'{r["payment_ratio"]:.1f}%', 'Settled' if d.is_settled else 'Open',
        ])

    analysis_rows = [
        ['Metric', 'Measured value'],
        ['Open outstanding balance', f'TZS {analysis["total_balance"]:,.0f}'],
        ['31+ day balance', f'TZS {analysis["overdue_31"]:,.0f} ({analysis["overdue_31_share"]:.1f}% of outstanding)'],
        ['61+ day balance', f'TZS {analysis["overdue_60"]:,.0f} ({analysis["overdue_60_share"]:.1f}% of outstanding)'],
        ['91+ day balance', f'TZS {analysis["overdue_90"]:,.0f} ({analysis["overdue_90_share"]:.1f}% of outstanding)'],
        ['Average outstanding per open debt', f'TZS {analysis["average_open_balance"]:,.0f}'],
        ['Average open debt age', f'{analysis["avg_age"]:.1f} days'],
        ['Median open debt age', f'{analysis["median_age"]:.1f} days'],
        ['Open debts with no payment', str(analysis['no_payment_count'])],
        ['Open debtors aged 61+ days', str(analysis['customers_61_plus'])],
        ['Largest debtor concentration', f'{analysis["top_customer_concentration"]:.1f}%'],
    ]

    sections = [
        {
            'title': 'Portfolio Analysis',
            'note': 'Calculated directly from the filtered debt records. Aging is measured from each debt creation date to the report date; balances are current outstanding amounts.',
            'headers': ['Metric', 'Measured value'],
            'rows': analysis_rows[1:],
            'column_widths': [65, 109],
        },
        {
            'title': 'Debt Aging',
            'note': 'Aging buckets use the outstanding balance, not the original debt value, so the report reflects what is actually still collectible.',
            'headers': ['Age bucket', 'Records', 'Outstanding', 'Share'],
            'rows': aging_rows,
            'column_widths': [52, 28, 55, 35],
        },
        {
            'title': 'Top Debtors',
            'note': 'Customers are ordered by current outstanding balance. This is an analytical ordering, not a risk score.',
            'headers': ['Customer', 'Phone', 'Records', 'Open', 'Owed', 'Paid', 'Outstanding', 'Oldest'],
            'rows': customer_rows,
            'column_widths': [43, 31, 18, 18, 28, 28, 32, 22],
        },
        {
            'title': 'Debt Detail',
            'headers': ['Customer', 'Sale #', 'Date', 'Age', 'Bucket', 'Owed', 'Paid', 'Outstanding', 'Paid %', 'Status'],
            'rows': detail_rows,
            'column_widths': [39, 17, 25, 15, 30, 28, 28, 32, 22, 20],
        },
    ]

    return generate_multi_section_pdf_report(
        title='Debt & Receivables Analysis',
        subtitle=f'{pharmacy.name if pharmacy else ""} — Generated {timezone.localtime():%d %B %Y, %H:%M}',
        sections=sections,
        summary=summary,
        filename=f'debt_report_{pharmacy.id}_{stamp}.pdf',
        pagesize=landscape(A4),
    )


_DEBT_MONTHS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}


def _parse_filter_date(value):
    """Parse YYYY-MM-DD from the date inputs; return None if blank/invalid."""
    from datetime import datetime
    try:
        return datetime.strptime((value or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _date_query_from_text(text):
    """Turn free text typed in the search box into a date Q on Debt.created_at.

    Understands: 2026-10-13, 13/10/2026, 13-10-2026, 13.10.2026, 13 Oct 2026,
    Oct 2026 / October 2026 (whole month), 10/2026 (whole month).
    Returns a Q or None when the text does not look like a date.
    """
    import calendar
    from datetime import date, datetime
    t = text.strip().lower()
    if not t:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%d %b %Y', '%d %B %Y', '%d/%m/%y'):
        try:
            d = datetime.strptime(t, fmt).date()
            return Q(created_at__date=d)
        except ValueError:
            continue
    m = re.fullmatch(r'([a-z]{3,9})\.?\s+(\d{4})', t)
    month = year = None
    if m and m.group(1)[:3] in _DEBT_MONTHS:
        month, year = _DEBT_MONTHS[m.group(1)[:3]], int(m.group(2))
    else:
        m = re.fullmatch(r'(\d{1,2})[/-](\d{4})', t)
        if m and 1 <= int(m.group(1)) <= 12:
            month, year = int(m.group(1)), int(m.group(2))
    if month and 1990 <= year <= 2100:
        last = calendar.monthrange(year, month)[1]
        return Q(created_at__date__gte=date(year, month, 1), created_at__date__lte=date(year, month, last))
    return None


def _filtered_debts(request):
    """Apply the Customer Debts page filters. Returns (queryset, filters dict)."""
    today = timezone.localdate()
    status = request.GET.get('status', 'all').strip().lower()
    if status not in ('all', 'pending', 'settled'):
        status = 'all'
    q = request.GET.get('q', '').strip()
    period = request.GET.get('period', '').strip().lower()
    date_from = _parse_filter_date(request.GET.get('date_from'))
    date_to = _parse_filter_date(request.GET.get('date_to'))
    sort = request.GET.get('sort', 'status').strip().lower()

    # Quick periods override the manual range.
    if period == 'today':
        date_from = date_to = today
    elif period == '7d':
        date_from, date_to = today - timedelta(days=6), today
    elif period == '30d':
        date_from, date_to = today - timedelta(days=29), today
    elif period == 'month':
        date_from, date_to = today.replace(day=1), today
    elif period == 'over30':
        date_from, date_to = None, today - timedelta(days=30)
        status = 'pending' if status == 'all' else status
    else:
        period = ''

    qs = Debt.objects.filter(pharmacy=request.pharmacy).select_related('sale')
    if status == 'pending':
        qs = qs.filter(is_settled=False)
    elif status == 'settled':
        qs = qs.filter(is_settled=True)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    if q:
        cond = Q(customer_name__icontains=q)
        digits = re.sub(r'\D', '', q)
        if len(digits) >= 3:
            # Phones are stored normalised (255…), so also match a typed local 0… number.
            cond |= Q(customer_phone__icontains=digits.lstrip('0') if digits.startswith('0') else digits)
        num = q.lstrip('#').strip()
        if num.isdigit() and len(num) <= 9:
            cond |= Q(sale__sale_number=int(num))
        date_q = _date_query_from_text(q)
        if date_q is not None:
            cond |= date_q
        qs = qs.filter(cond)

    qs = qs.annotate(balance_db=F('amount_owed') - F('amount_paid'))
    order = {
        'newest': ('-created_at',),
        'oldest': ('created_at',),
        'balance': ('-balance_db', '-created_at'),
        'name': ('customer_name', '-created_at'),
    }.get(sort, ('is_settled', '-created_at'))
    if sort not in ('newest', 'oldest', 'balance', 'name'):
        sort = 'status'
    qs = qs.order_by(*order)

    return qs, {
        'q': q, 'status': status, 'period': period, 'sort': sort,
        'date_from': date_from.isoformat() if date_from else '',
        'date_to': date_to.isoformat() if date_to else '',
    }


@employee_or_admin_required
def debt_list_view(request):
    """Customer debts: date lent, live auto-filter and flexible search (name, phone, sale #, date).

    Employees can view outstanding debts too — they're the ones most likely to interact with the customer.
    """
    today = timezone.localdate()
    if request.pharmacy is None:
        debts, filters = Debt.objects.none(), {
            'q': '', 'status': 'all', 'period': '', 'sort': 'status', 'date_from': '', 'date_to': '',
        }
    else:
        debts, filters = _filtered_debts(request)

    totals = debts.aggregate(
        owed=Coalesce(Sum('amount_owed'), Decimal('0')),
        paid=Coalesce(Sum('amount_paid'), Decimal('0')),
        count=Count('id'),
    )
    pending_qs = debts.filter(is_settled=False)
    overdue_cutoff = today - timedelta(days=30)
    deposits_total = (DebtPayment.objects.filter(debt__in=debts.values('pk'), kind=DebtPayment.Kind.DEPOSIT)
                      .aggregate(v=Coalesce(Sum('amount'), Decimal('0')))['v'])
    summary = {
        'count': totals['count'],
        'owed': totals['owed'],
        'paid': totals['paid'],
        'deposits': deposits_total,
        'repaid': max(totals['paid'] - deposits_total, Decimal('0')),
        'balance': totals['owed'] - totals['paid'],
        'pending_count': pending_qs.count(),
        'overdue_count': pending_qs.filter(created_at__date__lte=overdue_cutoff).count(),
        'collection_rate': int(min(100, totals['paid'] / totals['owed'] * 100)) if totals['owed'] else 0,
    }

    paginator = Paginator(debts.prefetch_related('payments'), 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    for debt in page_obj.object_list:
        debt.deposit_amount = debt.deposit_paid      # paid at the till when the credit sale was made
        debt.repaid_amount = debt.repaid             # paid later against the balance
        local_dt = timezone.localtime(debt.created_at)
        debt.age_days = max((today - local_dt.date()).days, 0)
        debt.is_overdue = (not debt.is_settled) and debt.age_days > 30
        debt.is_aging = (not debt.is_settled) and 14 < debt.age_days <= 30
        debt.paid_pct = int(min(100, (debt.amount_paid / debt.amount_owed * 100))) if debt.amount_owed else 0

    has_filters = bool(
        filters['q'] or filters['period'] or filters['date_from'] or filters['date_to'] or filters['status'] != 'all'
    )
    context = {
        'page_obj': page_obj, 'filters': filters, 'summary': summary,
        'has_filters': has_filters, 'today': today.isoformat(),
    }
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render(request, 'sales/_debt_results.html', context)
    return render(request, 'sales/debt_list.html', context)


def _debt_list_redirect(request):
    """Return to the Customer Debts page with the user's filters intact."""
    from django.utils.http import url_has_allowed_host_and_scheme
    nxt = request.POST.get('next', '')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(nxt)
    return redirect('sales:debt_list')


def _apply_split_payment(sale, raw_cash, total_amount):
    """Validate and store the two tenders of a Cash + Mobile Money sale. Raises ValueError (so the
    surrounding transaction rolls the whole sale back) rather than ever saving amounts that do not add up."""
    try:
        cash = Decimal(str(raw_cash or 0)).quantize(Decimal('0.01'))
    except Exception:
        raise ValueError('Invalid cash amount for a Cash + Mobile Money sale.')
    total = Decimal(total_amount).quantize(Decimal('0.01'))
    if cash <= 0 or cash >= total:
        raise ValueError(f'Cash must be more than 0 and less than the sale total (TZS {total:,.0f}); '
                         'the remainder is taken as mobile money.')
    sale.cash_paid = cash
    sale.mobile_paid = total - cash
    sale.save(update_fields=['cash_paid', 'mobile_paid'])


def _apply_pos_deposit(debt, raw_amount, raw_method, user, paid_at=None):
    """Part-payment taken at the till for a credit sale: recorded as a normal DebtPayment (so the drawer,
    reports and debt balance all agree). An invalid or oversize amount is ignored/capped, never an error:
    the sale itself must always go through."""
    try:
        amount = Decimal(str(raw_amount or 0))
    except Exception:
        return
    if amount <= 0:
        return
    try:
        record_debt_payment(debt.pk, min(amount, debt.amount_owed), raw_method, user, paid_at=paid_at,
                            kind=DebtPayment.Kind.DEPOSIT, note='Paid at the till when the credit sale was made')
    except DebtPaymentError:
        pass


@employee_or_admin_required
def debt_detail_view(request, pk):
    """Full trace of ONE credit sale: what was sold, what was paid at the till, every later payment (who,
    when, how) and any return that reduced the debt - with a running balance that must end at the
    balance on the Debts screen."""
    debt = get_object_or_404(
        Debt.objects.select_related('sale', 'sale__served_by', 'pharmacy'), pk=pk, pharmacy=request.pharmacy)
    sale = debt.sale
    items = list(sale.items.select_related('product'))
    payments = list(debt.payments.select_related('recorded_by').order_by('paid_at', 'pk'))
    approved_returns = list(sale.return_requests.filter(status=SaleReturnRequest.Status.APPROVED)
                            .select_related('decided_by', 'requested_by').order_by('decided_at', 'pk'))

    def _who(user):
        return (user.get_full_name() or user.get_username()) if user else '-'

    sale_value = sale.original_total
    events = [{'at': sale.created_at, 'kind': 'SALE', 'label': 'Credit sale made', 'who': _who(sale.served_by),
               'method': '', 'note': f'{len(items)} item line(s)', 'debit': sale_value, 'credit': Decimal('0')}]
    for pay in payments:
        events.append({
            'at': pay.paid_at, 'kind': pay.kind,
            'label': 'Paid at the till (deposit)' if pay.kind == DebtPayment.Kind.DEPOSIT else (
                'Earlier repayment (before payment methods)' if pay.payment_method == DebtPayment.Method.LEGACY
                else 'Repayment received'),
            'who': _who(pay.recorded_by), 'method': pay.get_payment_method_display(), 'note': pay.note,
            'debit': Decimal('0'), 'credit': pay.amount})
    for rr in approved_returns:
        events.append({'at': rr.decided_at or rr.requested_at, 'kind': 'RETURN',
                       'label': 'Return / void approved', 'who': _who(rr.decided_by or rr.requested_by),
                       'method': '', 'note': (rr.reason or '')[:120],
                       'debit': Decimal('0'), 'credit': rr.refund_amount})
    events.sort(key=lambda e: e['at'])
    running = Decimal('0')
    for e in events:
        running += e['debit'] - e['credit']
        e['running'] = max(running, Decimal('0'))

    paid_total = sum((p.amount for p in payments), Decimal('0'))
    refunded_back = sum((rr.refund_due for rr in approved_returns), Decimal('0'))
    # amount_paid should equal the recorded payments. A difference means the figure was changed without a
    # payment record (older versions allowed editing it by hand) - show it instead of hiding it.
    untraced = Decimal('0') if refunded_back > 0 else (debt.amount_paid - paid_total)
    return render(request, 'sales/debt_detail.html', {
        'debt': debt, 'sale': sale, 'items': items, 'events': events,
        'sale_value': sale_value, 'returned_value': sum((rr.refund_amount for rr in approved_returns), Decimal('0')),
        'deposit': debt.deposit_paid, 'repaid': debt.repaid, 'credit_given': debt.credit_given,
        'balance': debt.balance, 'untraced': untraced if abs(untraced) >= Decimal('0.01') else Decimal('0'),
    })


@employee_or_admin_required
@require_http_methods(["POST"])
def debt_record_payment_view(request, pk):
    """Any staff member can record a payment — they're usually the one handling the customer directly."""
    debt = get_object_or_404(Debt, pk=pk, pharmacy=request.pharmacy)
    locked = workday_sales_block_message(request.user)  # v97.18: closed workday also locks cash/payment recording
    if locked:
        messages.error(request, locked)
        return _debt_list_redirect(request)
    try:
        amount = Decimal(request.POST.get('amount', '0'))
    except Exception:
        amount = Decimal('0')
    try:
        debt, applied, capped = record_debt_payment(debt.pk, amount, request.POST.get('payment_method'), request.user)
    except DebtPaymentError as exc:
        messages.error(request, str(exc))
        return _debt_list_redirect(request)

    where = 'cash (added to the drawer)' if normalize_method(request.POST.get('payment_method')) == DebtPayment.Method.CASH else 'mobile money / bank'
    if capped:
        messages.warning(
            request,
            f"The amount entered (TZS {amount:,.0f}) is more than {debt.customer_name} owed. "
            f"Only TZS {applied:,.0f} was recorded to settle the debt in full.")
    if debt.is_settled:
        messages.success(request, f"Payment of TZS {applied:,.0f} received by {where}. {debt.customer_name}'s debt is now fully settled.")
    else:
        messages.success(request, f"Payment of TZS {applied:,.0f} received by {where} for {debt.customer_name}. "
                                  f"Remaining balance: TZS {debt.balance:,.0f}.")

    return _debt_list_redirect(request)


@employee_or_admin_required
@require_http_methods(["POST"])
def debt_send_reminder_view(request, pk):
    """Any staff member (employee or owner) can trigger a reminder SMS — they're often the one dealing with the customer directly."""
    debt = get_object_or_404(Debt, pk=pk, pharmacy=request.pharmacy)
    if not debt.customer_phone:
        messages.error(request, "This customer has no phone number on file.")
    else:
        if request.user.is_superuser:
            owner = request.user
        else:
            owner = request.pharmacy.managers.filter(role=request.user.Role.ADMIN, is_active=True).order_by('id').first() if request.pharmacy else None
        if owner is None or not has_active_debt_sms(owner):
            messages.error(request, 'Debt SMS is a separate Premium feature. Activate a Debt SMS package (500 SMS or 10,000 SMS monthly) to use reminders.')
            return redirect('sms_integration:debt_sms')
        sent = send_debt_reminder_sms(debt.customer_phone, debt.customer_name, debt.balance, owner=owner, pharmacy=request.pharmacy)
        if sent:
            messages.success(request, f"Reminder SMS sent to {debt.customer_name}.")
        else:
            messages.warning(request, "Could not send the reminder SMS (check SMS Settings).")
    return _debt_list_redirect(request)


@employee_or_admin_required
def debt_edit_view(request, pk):
    debt = get_object_or_404(Debt, pk=pk, pharmacy=request.pharmacy)
    form = DebtEditForm(request.POST or None, instance=debt)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f"Debt for {debt.customer_name} updated successfully.")
        return redirect('sales:debt_list')
    return render(request, 'sales/debt_edit.html', {'form': form, 'debt': debt})


def _render_sale_receipt_pdf(request, sale):
    """Branch-branded thermal receipt (80 mm or 58 mm).

    The look - colour, logo, font, layout, visible details and extra texts - comes
    from the branch's saved "Receipt Design" (pharmacies.ReceiptSettings). A branch
    that never customised anything gets the standard SmartPOS design. The same
    renderer serves the in-app receipt, reprints and the public SMS receipt link.
    """
    from core.receipt_pdf import receipt_number_for
    from pharmacies.receipt_utils import render_sale_receipt_bytes

    receipt_no = receipt_number_for(sale)
    response = HttpResponse(render_sale_receipt_bytes(sale), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="receipt-{receipt_no}.pdf"'
    return response


@employee_or_admin_required
@require_http_methods(["POST"])
@csrf_protect
def send_receipt_sms_manual_view(request, pk):
    """Send a receipt link only after explicit cashier confirmation.

    The sale must be paid (cash/mobile money, or a debt that has since been
    fully settled). A sent timestamp prevents accidental duplicate SMS and
    therefore protects the customer's Premium Receipt SMS balance.
    """
    wants_json = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.content_type == 'application/json'
        or 'application/json' in request.headers.get('Accept', '')
    )
    def finish(success, error=None, status=200, **extra):
        if wants_json:
            payload = {'success': success}
            if error:
                payload['error'] = error
            payload.update(extra)
            return JsonResponse(payload, status=status)
        if success:
            messages.success(request, extra.get('message', 'Receipt SMS sent successfully.'))
        else:
            messages.warning(request, error or 'Could not send receipt SMS.')
        return redirect('sales:sale_list')

    sale = get_object_or_404(
        Sale.objects.select_related('pharmacy', 'served_by'),
        pk=pk, pharmacy=request.pharmacy
    )

    if sale.status == Sale.Status.VOIDED:
        return finish(False, 'This sale was voided, so no receipt can be sent.', 400)

    if sale.payment_method == Sale.PaymentMethod.DEBT:
        try:
            if not sale.debt.is_settled:
                return finish(False, 'This receipt can only be sent after the debt has been fully paid.', 400)
        except Debt.DoesNotExist:
            return finish(False, 'This debt sale has no payment record.', 400)

    # Prefer the current master customer contact when one exists. This allows a
    # manager to correct a customer's phone number later and safely resend the
    # same receipt without rewriting the historical sale snapshot.
    current_contact = getattr(sale, 'customer_contact', None)
    customer_name = (current_contact.name if current_contact and current_contact.name else sale.customer_name).strip()
    customer_phone = (current_contact.phone if current_contact and current_contact.phone else sale.customer_phone).strip()

    # A one-off correction can also be submitted from the receipt action itself.
    submitted_name = (request.POST.get('customer_name') or '').strip()
    submitted_phone = (request.POST.get('customer_phone') or '').strip()
    if submitted_name or submitted_phone:
        try:
            if submitted_phone:
                submitted_phone = normalize_phone_number(submitted_phone)
            if not submitted_name:
                submitted_name = customer_name or 'Customer'
            contact = _get_or_create_customer_contact(sale.pharmacy, request.user, submitted_name, submitted_phone)
            if contact:
                sale.customer_contact = contact
                sale.save(update_fields=['customer_contact'])
                current_contact = contact
                customer_name = contact.name
                customer_phone = contact.phone
        except Exception as exc:
            return finish(False, f'Customer details could not be updated: {exc}', 400)

    if not customer_phone:
        return finish(False, 'This customer has no phone number on file. Edit the customer details and add a valid mobile number first.', 400)

    owner = _get_pharmacy_owner(sale.pharmacy, request.user)
    if owner is None or not has_active_receipt_sms(owner):
        return finish(False, 'Premium Receipt SMS is not active or has no remaining credits.', 400)

    # Serialize manual sends so two fast clicks cannot consume two credits.
    with transaction.atomic():
        locked_sale = Sale.objects.select_for_update().get(pk=sale.pk)
        locked_contact = getattr(locked_sale, 'customer_contact', None)
        locked_name = (locked_contact.name if locked_contact and locked_contact.name else customer_name).strip()
        locked_phone = (locked_contact.phone if locked_contact and locked_contact.phone else customer_phone).strip()
        if not locked_phone:
            return finish(False, 'This customer has no valid phone number on file.', 400)

        receipt_url = request.build_absolute_uri(
            reverse('sales:public_receipt_pdf', kwargs={'token': locked_sale.receipt_access_token})
        )
        sent = send_receipt_sms(
            locked_phone, locked_name, receipt_url,
            owner=owner, pharmacy=locked_sale.pharmacy, sale=locked_sale
        )
        if not sent:
            return finish(False, 'SMS could not be sent. Check SMS Settings, credentials, network, and Receipt SMS credits.', 502)

        locked_sale.receipt_sms_sent_at = timezone.now()
        locked_sale.receipt_sms_sent_by = request.user
        locked_sale.save(update_fields=['receipt_sms_sent_at', 'receipt_sms_sent_by'])

    return finish(True, message=f'Receipt SMS sent to {locked_phone}.')


@employee_or_admin_required
@require_http_methods(["POST"])
@csrf_protect
def edit_sale_customer_contact_view(request, pk):
    """Correct the customer contact attached to a historical sale.

    This changes the reusable CustomerContact master record when the sale is
    linked to one. The immutable sale snapshot is deliberately retained for
    historical/audit purposes. The updated master contact is what future
    receipt resends use.
    """
    if request.pharmacy is None:
        return JsonResponse({'success': False, 'error': 'No active branch is set.'}, status=400)

    sale = get_object_or_404(
        Sale.objects.select_related('customer_contact'),
        pk=pk, pharmacy=request.pharmacy
    )

    phone = (request.POST.get('customer_phone') or '').strip()
    name = (request.POST.get('customer_name') or '').strip()

    if not phone:
        return JsonResponse({'success': False, 'error': 'Customer phone number is required.'}, status=400)

    try:
        phone = normalize_phone_number(phone)
    except Exception as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)

    try:
        with transaction.atomic():
            contact = sale.customer_contact

            # If this sale has no master contact yet, create one. If it does,
            # update the existing master record so future receipt resends and
            # customer marketing use the corrected number.
            if contact is not None:
                collision = CustomerContact.objects.filter(
                    pharmacy=request.pharmacy,
                    phone=phone,
                ).exclude(pk=contact.pk).first()
                if collision:
                    # Re-link this sale to the existing master customer instead
                    # of violating the branch-level unique phone constraint.
                    contact = collision
                else:
                    contact.phone = phone
                    if name:
                        contact.name = name
                    contact.is_active = True
                    contact.save(update_fields=['phone', 'name', 'is_active', 'updated_at'])
            else:
                contact = _get_or_create_customer_contact(
                    request.pharmacy,
                    request.user,
                    name or sale.customer_name or phone,
                    phone,
                )

            sale.customer_contact = contact
            sale.save(update_fields=['customer_contact'])

    except Exception as exc:
        return JsonResponse({'success': False, 'error': f'Customer contact could not be updated: {exc}'}, status=400)

    return JsonResponse({
        'success': True,
        'name': contact.name,
        'phone': contact.phone,
        'message': f'Customer phone updated to {contact.phone}. You can now resend the receipt.',
    })


@employee_or_admin_required
def sale_receipt_pdf_view(request, pk):
    sale = get_object_or_404(
        Sale.objects.select_related('pharmacy', 'served_by')
        .prefetch_related('items__product', 'items__batch'),
        pk=pk, pharmacy=request.pharmacy
    )
    return _render_sale_receipt_pdf(request, sale)


def public_sale_receipt_pdf_view(request, token):
    """Public customer receipt link.

    Cash/mobile-money sales are considered paid when checkout completes.
    Debt-sale receipts become publicly downloadable only after the linked debt
    is fully settled, matching the manual SMS rule.
    """
    sale = get_object_or_404(
        Sale.objects.select_related('pharmacy', 'served_by')
        .prefetch_related('items__product', 'items__batch'),
        receipt_access_token=token
    )
    if sale.payment_method == Sale.PaymentMethod.DEBT:
        try:
            if not sale.debt.is_settled:
                return HttpResponse('This receipt is available after the outstanding balance is fully paid.', status=403)
        except Debt.DoesNotExist:
            return HttpResponse('This receipt is not available.', status=404)
    return _render_sale_receipt_pdf(request, sale)



# ---------------------------------------------------------------------------
# Returns & voids
# ---------------------------------------------------------------------------

@employee_or_admin_required
def sale_return_info_api(request, pk):
    """What can still be returned from this sale (used to build the Return / Void dialog)."""
    sale = get_object_or_404(Sale, pk=pk, pharmacy=request.pharmacy)
    if not returns.user_can_request(request.user, sale):
        return JsonResponse({'success': False, 'error': 'You cannot return items from this sale.'}, status=403)
    rows = returns.returnable_items(sale)
    return JsonResponse({
        'success': True,
        'sale_number': sale.sale_number or sale.pk,
        'payment_method': sale.get_payment_method_display(),
        'is_owner': request.user.is_management_user(),
        'items': [{
            'id': r['item'].pk,
            'name': r['item'].product.name,
            'unit_price': str(r['item'].unit_price),
            'sold': r['item'].original_quantity,
            'already_returned': r['item'].returned_quantity,
            'pending': r['pending'],
            'available': r['available'],
        } for r in rows],
    })


def _describe_direct_return(rr):
    number = rr.sale.sale_number or rr.sale_id
    what = f"Sale #{number} was voided" if rr.is_full_void else f"Items from sale #{number} were returned"
    parts = [f"{what} (TZS {rr.refund_amount:,.0f})."]
    if rr.disposition == SaleReturnRequest.Disposition.RESTOCK:
        parts.append("Stock has been restored.")
    else:
        parts.append("Items were written off; stock was not increased.")
    if rr.refund_due > 0:
        parts.append(f"Hand TZS {rr.refund_due:,.0f} back to the customer.")
    return " ".join(parts)


@employee_or_admin_required
@require_http_methods(["POST"])
@csrf_protect
def sale_return_request_view(request, pk):
    """Employees create a PENDING request; owners/supervisors apply the return immediately."""
    if request.pharmacy is None:
        return JsonResponse({'success': False, 'error': 'No active branch is set for your account.'}, status=400)
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid request data.'}, status=400)

    lines = {}
    for row in payload.get('items', []):
        try:
            lines[int(row.get('item_id'))] = int(row.get('quantity') or 0)
        except (TypeError, ValueError, AttributeError):
            return JsonResponse({'success': False, 'error': 'Invalid item data.'}, status=400)

    is_owner = request.user.is_management_user()
    disposition = payload.get('disposition') or SaleReturnRequest.Disposition.RESTOCK
    try:
        with transaction.atomic():
            rr = returns.create_request(
                pk, request.user, request.pharmacy, lines, payload.get('reason'), bool(payload.get('full_void')),
            )
            if is_owner:
                rr = returns.approve_request(rr.pk, request.user, disposition, note='Done directly by the shop owner.')
    except returns.ReturnError as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)

    if is_owner:
        message = _describe_direct_return(rr)
    else:
        returns.notify_managers_of_request(rr, request.user)
        message = "Your request was sent to the shop owner for approval. Stock and totals change only once it is approved."
    messages.success(request, message)
    return JsonResponse({'success': True, 'status': rr.status, 'message': message})


@employee_or_admin_required
def sale_returns_list_view(request):
    """Owners: every request in the branch (pending first). Employees: only their own."""
    if request.pharmacy is None:
        qs = SaleReturnRequest.objects.none()
    else:
        qs = SaleReturnRequest.objects.filter(pharmacy=request.pharmacy)
        if not request.user.is_management_user():
            qs = qs.filter(requested_by=request.user)
    qs = qs.select_related('sale', 'requested_by', 'decided_by').prefetch_related('lines__sale_item__product')

    # One aggregate query feeds the four summary tabs at the top of the page.
    Status = SaleReturnRequest.Status
    stats = qs.order_by().aggregate(
        total=Count('pk'),
        pending=Count('pk', filter=Q(status=Status.PENDING)),
        approved=Count('pk', filter=Q(status=Status.APPROVED)),
        rejected=Count('pk', filter=Q(status=Status.REJECTED)),
        approved_value=Sum('refund_amount', filter=Q(status=Status.APPROVED)),
    )
    pending_count = stats['pending'] or 0
    status_filter = request.GET.get('status') or ('pending' if request.user.is_management_user() else 'all')
    status_map = {
        'pending': SaleReturnRequest.Status.PENDING,
        'approved': SaleReturnRequest.Status.APPROVED,
        'rejected': SaleReturnRequest.Status.REJECTED,
    }
    if status_filter in status_map:
        qs = qs.filter(status=status_map[status_filter])
    else:
        status_filter = 'all'

    # Search by sale number as the user types. Keep the query branch-scoped
    # and apply it before pagination so matching results span every page.
    sale_number_query = (request.GET.get('sale_number') or '').strip()
    if sale_number_query:
        digits = ''.join(ch for ch in sale_number_query if ch.isdigit())
        if digits:
            qs = qs.filter(sale__sale_number=int(digits))
        else:
            qs = qs.none()

    if request.user.is_management_user() and status_filter == 'pending':
        qs = qs.order_by('requested_at')  # oldest waiting first
    else:
        qs = qs.order_by('-requested_at')

    paginator = Paginator(qs, 10)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'sales/returns_list.html', {
        'page_obj': page_obj, 'status_filter': status_filter, 'pending_count': pending_count,
        'stats': stats, 'sale_number_query': sale_number_query,
        'disposition_choices': SaleReturnRequest.Disposition.choices,
    })


@admin_required
def void_review_view(request):
    """Owner-only review of approved returns/voids with risk flags (V37.16). Read-only."""
    from datetime import date as _date
    from . import void_review
    pharmacy = request.pharmacy
    if pharmacy is None:
        return redirect('dashboard:home')
    today = timezone.localdate()

    def _d(value, default):
        try:
            return _date.fromisoformat((value or '').strip())
        except ValueError:
            return default
    start = _d(request.GET.get('start'), today - timedelta(days=30))
    end = _d(request.GET.get('end'), today)
    if start > end:
        start, end = end, start
    only_flagged = request.GET.get('flagged') == '1'
    rows, cfg = void_review.build_review(pharmacy, start, end, only_flagged=only_flagged)

    summary = void_review.summarize(rows)

    # Exports intentionally use the complete filtered result set, not the
    # currently visible pagination page. This prevents a PDF/CSV from silently
    # containing only page 1 of a long review queue.
    if request.GET.get('format') == 'csv':
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="void_review_{start}_{end}.csv"'
        w = csv.writer(resp)
        w.writerow(['Sale #', 'Sale date', 'Type', 'Payment', 'Refund TZS', 'Requested by', 'Approved by',
                    'Decided at', 'Hours after sale', 'Flags', 'Reason'])
        for r in rows:
            rr = r['rr']
            w.writerow([
                r['sale_number'], r['sale'].business_date, 'Full void' if r['is_void'] else 'Partial return',
                r['sale'].get_payment_method_display(), f"{rr.refund_amount:.0f}",
                (r['requested_by'].get_full_name() or r['requested_by'].username) if r['requested_by'] else '',
                (r['decided_by'].get_full_name() or r['decided_by'].username) if r['decided_by'] else '',
                timezone.localtime(rr.decided_at).strftime('%Y-%m-%d %H:%M'),
                r['delay_display'],
                ' '.join(r['flags']), (rr.reason or '').replace('\n', ' '),
            ])
        return resp

    if request.GET.get('format') == 'pdf':
        from xml.sax.saxutils import escape

        def person(user):
            if not user:
                return '—'
            return escape(user.get_full_name() or user.username)

        detail_rows = []
        for r in rows:
            rr = r['rr']
            detail_rows.append([
                f"#{r['sale_number']}",
                timezone.localtime(rr.decided_at).strftime('%d %b %Y'),
                'Full void' if r['is_void'] else 'Partial return',
                r['sale'].get_payment_method_display(),
                f"TZS {rr.refund_amount:,.0f}",
                person(r['requested_by']),
                person(r['decided_by']),
                timezone.localtime(rr.decided_at).strftime('%d %b %Y, %H:%M'),
                escape(r['delay_display']),
                ', '.join(r['flags']) if r['flags'] else '—',
                escape((rr.reason or '—').replace('\n', ' ')),
            ])

        user_rows = [
            [escape(name), str(d['count']), f"TZS {d['value']:,.0f}", str(d['flagged'])]
            for name, d in summary['by_user']
        ]
        sections = [
            {
                'title': 'Requester summary',
                'headers': ['Requested by', 'Corrections', 'Value (TZS)', 'Flagged'],
                'rows': user_rows,
                'column_widths': [78, 28, 48, 28],
            },
            {
                'title': 'Correction details',
                'headers': ['Sale #', 'Sale date', 'Type', 'Payment', 'Refund', 'Requested by',
                            'Approved by', 'Decided', 'Delay', 'Flags', 'Reason'],
                'rows': detail_rows,
                # Landscape A4 keeps the audit table readable while the shared
                # PDF helper scales these relative widths to the page exactly.
                'column_widths': [20, 24, 28, 22, 30, 35, 35, 34, 20, 32, 58],
                'note': 'Flags are review signals, not proof of misconduct. Verify the written reason and the people involved.',
            },
        ]
        report_subtitle = (
            f"Branch: {escape(pharmacy.name)} · Period: {start:%d %b %Y} – {end:%d %b %Y}"
            f" · {'Flagged corrections only' if only_flagged else 'All approved corrections'}"
        )
        pdf_summary = [
            ('Corrections', f"{summary['count']} ({summary['voids']} full void · {summary['partial']} partial)"),
            ('Value returned', f"TZS {summary['value']:,.0f}"),
            ('Flagged for review', str(summary['flagged'])),
            ('Flag rules', f"Late > {cfg['late_hours']}h · Cross-day · High ≥ TZS {cfg['high_value']:,.0f} · Repeat ≥ {cfg['repeat_count']}"),
        ]
        return generate_multi_section_pdf_report(
            title='Void & Return Review',
            subtitle=report_subtitle,
            sections=sections,
            filename=f'void_return_review_{start}_{end}.pdf',
            summary=pdf_summary,
            pagesize=landscape(A4),
        )

    # Pagination is applied after all filters/risk classification so every page
    # represents the same filtered dataset and the shared pagination component
    # can preserve start/end/flagged query parameters consistently.
    paginator = Paginator(rows, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'sales/void_review.html', {
        'page_obj': page_obj, 'summary': summary, 'cfg': cfg,
        'start': start, 'end': end, 'only_flagged': only_flagged,
    })


@admin_required
@require_http_methods(["POST"])
def sale_return_decide_view(request, pk):
    rr = get_object_or_404(SaleReturnRequest, pk=pk, pharmacy=request.pharmacy)
    action = request.POST.get('action')
    try:
        if action == 'approve':
            rr = returns.approve_request(
                rr.pk, request.user,
                disposition=request.POST.get('disposition') or SaleReturnRequest.Disposition.RESTOCK,
                note=request.POST.get('note', ''),
            )
            returns.notify_requester_of_decision(rr)
            messages.success(request, _describe_direct_return(rr))
        elif action == 'reject':
            rr = returns.reject_request(rr.pk, request.user, note=request.POST.get('note', ''))
            returns.notify_requester_of_decision(rr)
            messages.info(request, f"Return request for sale #{rr.sale.sale_number or rr.sale_id} was rejected.")
        else:
            messages.error(request, "Unknown action.")
    except returns.ReturnError as exc:
        messages.error(request, str(exc))
    return redirect('sales:returns')
