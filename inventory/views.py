from django.views.decorators.clickjacking import xframe_options_sameorigin
import io
import time
import hashlib
import logging
from collections import defaultdict
from datetime import datetime, date, timedelta
from decimal import Decimal

import openpyxl
import secrets
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.db.models.deletion import ProtectedError
from django.db import transaction, IntegrityError
from django.db.models.functions import Lower
from django.template.loader import render_to_string
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import FileResponse, HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.html import escape
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_http_methods

from accounts.decorators import admin_required, employee_or_admin_required
from . import bulk_import as bi
from .barcodes import barcode_variants
from .barcode_lookup import lookup_barcode, is_lookup_candidate, is_lookup_enabled
from .forms import ProductForm, ProductQuickForm, ProductBatchForm, ProductBatchEditForm, BulkUploadForm
from .services import register_product, receive_batch
from .mistake_detection import flag_batch_if_suspicious, check_batch_for_issues, flag_if_similar_product_exists
from .models import Product, Category, UnitCatalog, ProductBatch, StockMovement, StockImport, BranchStockTransfer, BranchStockTransferItem, FEFO_ORDER
from .transfer_service import TransferError, execute_bulk_transfer, parse_lines
from notifications.models import Notification
from notifications.utils import notify_many, notify

logger = logging.getLogger(__name__)


def _require_pharmacy(request):
    """Every inventory view needs an active pharmacy branch — bounce back with a clear message if missing."""
    if request.pharmacy is None:
        messages.warning(request, "You don't have an active branch yet. Create one to get started.")
        return False
    return True


@employee_or_admin_required
def product_list_view(request):
    if not _require_pharmacy(request):
        return redirect('pharmacies:list' if request.user.is_management_user() else 'dashboard:home')

    query = request.GET.get('q', '').strip()
    stock_filter = request.GET.get('stock', '')
    category_id = request.GET.get('category', '')

    products = Product.objects.filter(pharmacy=request.pharmacy, is_active=True).select_related('category').prefetch_related('batches')

    if query:
        # Every word typed must match somewhere (name, SKU, barcode or batch number),
        # so "para 500" finds "Paracetamol 500mg" regardless of word order.
        for token in query.split():
            products = products.filter(
                Q(name__icontains=token) | Q(sku__icontains=token) |
                Q(barcode__icontains=token) | Q(batches__batch_number__icontains=token)
            )
        products = products.distinct()
    if category_id:
        products = products.filter(category_id=category_id)

    products = list(products)
    if stock_filter == 'low':
        products = [p for p in products if p.is_low_stock]
    elif stock_filter == 'expiring':
        products = [p for p in products if p.is_expiring_soon or p.is_expired]

    # After a scan-save the page opens on the page that holds the product, with its row highlighted.
    highlight = request.GET.get('highlight', '')
    highlight_id = int(highlight) if highlight.isdigit() else None
    page_number = request.GET.get('page')
    if highlight_id and not page_number:
        for index, item in enumerate(products):
            if item.pk == highlight_id:
                page_number = index // 20 + 1
                break
    paginator = Paginator(products, 20)
    page_obj = paginator.get_page(page_number)

    context = {
        'highlight_id': highlight_id,
        'page_obj': page_obj,
        'categories': Category.objects.filter(pharmacy=request.pharmacy).order_by(Lower('name'), 'name'),
        'query': query,
        'stock_filter': stock_filter,
        'category_id': category_id,
        'has_demo_products': Product.objects.filter(pharmacy=request.pharmacy, is_demo=True, is_active=True).exists(),
    }

    # Live search: the page asks for ?partial=1 while the user types and we send
    # back only the results table + pagination (no navbar/sidebar/footer).
    if request.GET.get('partial') == '1':
        response = render(request, 'inventory/_product_results.html', context)
        response['Cache-Control'] = 'no-store'
        return response

    return render(request, 'inventory/product_list.html', context)


@admin_required
@require_http_methods(["POST"])
def demo_products_remove_view(request):
    """Remove all sample products of the active branch in one click (owner/manager only)."""
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')
    from .demo_data import remove_demo_products
    deleted, archived = remove_demo_products(request.pharmacy)
    if deleted or archived:
        note = f" {archived} used in a test sale {'was' if archived == 1 else 'were'} hidden to keep your sales history intact." if archived else ''
        messages.success(request, f"Sample data removed ({deleted} deleted).{note} Your own products are untouched.")
    else:
        messages.info(request, 'There is no sample data left to remove.')
    return redirect('inventory:product_list')




@admin_required
@require_http_methods(["POST"])
def product_barcode_auto_update_api(request, pk):
    """Safely save a scanned real barcode to an existing branch product.

    The product row is locked only inside an atomic transaction (required by
    PostgreSQL). Product identity, batches, stock and transaction history remain
    untouched. Barcode text is preserved exactly apart from whitespace.
    """
    if request.pharmacy is None:
        return JsonResponse({'ok': False, 'message': 'Select an active branch first.'}, status=400)

    code = ''.join((request.POST.get('barcode') or '').split())
    if not code or len(code) > 32 or any(ord(ch) < 32 for ch in code):
        return JsonResponse({'ok': False, 'message': 'The scanned barcode is empty or invalid. Please scan again.'}, status=400)

    try:
        with transaction.atomic():
            product = get_object_or_404(
                Product.objects.select_for_update(), pk=pk, pharmacy=request.pharmacy
            )
            duplicate = Product.objects.filter(
                pharmacy=request.pharmacy, barcode=code
            ).exclude(pk=product.pk).first()
            if duplicate:
                return JsonResponse({
                    'ok': False,
                    'message': f'This barcode already belongs to “{duplicate.name}”. No changes were made.'
                }, status=409)

            previous = product.barcode or ''
            if previous == code:
                return JsonResponse({
                    'ok': True, 'unchanged': True, 'barcode': code,
                    'message': 'This barcode is already saved for this product.'
                })

            product.barcode = code
            product.save(update_fields=['barcode', 'updated_at'])
    except IntegrityError:
        return JsonResponse({
            'ok': False,
            'message': 'This barcode is already registered to another product. No changes were made.'
        }, status=409)

    logger.info(
        'Product barcode updated by user_id=%s pharmacy_id=%s product_id=%s old=%s new=%s',
        request.user.pk, request.pharmacy.pk, product.pk, previous, code
    )
    return JsonResponse({
        'ok': True, 'barcode': code, 'previous_barcode': previous,
        'message': 'Barcode updated successfully. Stock and sales history were preserved.'
    })


@admin_required
def barcode_check_api(request):
    """Is this barcode already registered at the active branch? Used by the scan button on the product form."""
    if request.pharmacy is None:
        return JsonResponse({'exists': False})
    code = request.GET.get('code', '').strip()
    if not code or len(code) > 64:
        return JsonResponse({'exists': False})
    qs = Product.objects.filter(pharmacy=request.pharmacy, barcode__in=barcode_variants(code))
    exclude = request.GET.get('exclude', '')
    if exclude.isdigit():
        qs = qs.exclude(pk=int(exclude))
    p = qs.first()
    if p is None:
        return JsonResponse({'exists': False})
    return JsonResponse({'exists': True, 'id': p.pk, 'name': p.name, 'active': p.is_active})


@admin_required
@require_http_methods(['POST'])
def category_quick_add_api(request):
    """Add a category from inside a product / scan form. An existing name (any capitalisation) is reused."""
    if request.pharmacy is None:
        return JsonResponse({'ok': False, 'message': 'No active branch is selected.'}, status=400)
    name = ' '.join(request.POST.get('name', '').split())
    if len(name) < 2:
        return JsonResponse({'ok': False, 'message': 'Please enter a category name with at least 2 characters.'})
    if len(name) > 100:
        return JsonResponse({'ok': False, 'message': 'The category name is too long (maximum 100 characters).'})
    if name.islower():
        name = name[:1].upper() + name[1:]
    existing = Category.objects.filter(pharmacy=request.pharmacy, name__iexact=name).first()
    if existing:
        return JsonResponse({'ok': True, 'id': existing.pk, 'name': existing.name, 'created': False})
    try:
        category = Category.objects.create(pharmacy=request.pharmacy, name=name)
    except IntegrityError:      # two people added the same name at the same moment
        category = Category.objects.get(pharmacy=request.pharmacy, name__iexact=name)
        return JsonResponse({'ok': True, 'id': category.pk, 'name': category.name, 'created': False})
    return JsonResponse({'ok': True, 'id': category.pk, 'name': category.name, 'created': True})


@admin_required
@require_http_methods(['POST'])
def unit_quick_add_api(request):
    """Persist a reusable custom unit for the active branch from product/batch forms."""
    if request.pharmacy is None:
        return JsonResponse({'ok': False, 'message': 'No active branch is selected.'}, status=400)
    from .forms import UNIT_NAME_RE, normalize_unit_name
    raw_name = ' '.join(request.POST.get('name', '').split())
    name = normalize_unit_name(raw_name)
    if len(name) < 2:
        return JsonResponse({'ok': False, 'message': 'Please enter a unit name with at least 2 characters.'})
    if len(name) > 30 or not UNIT_NAME_RE.match(name):
        return JsonResponse({'ok': False, 'message': 'Use letters, numbers and common unit symbols only, up to 30 characters.'})
    # Standard units already exist globally; return their canonical code without
    # creating duplicate custom catalog entries.
    standard = {code.casefold(): (code, label) for code, label in Product.Unit.choices}
    for code, label in Product.Unit.choices:
        if name.casefold() in (code.casefold(), label.casefold()):
            return JsonResponse({'ok': True, 'id': code, 'name': label, 'value': code, 'created': False, 'standard': True})
    existing = UnitCatalog.objects.filter(pharmacy=request.pharmacy, normalized_name=name.casefold()).first()
    if existing:
        return JsonResponse({'ok': True, 'id': existing.pk, 'name': existing.name, 'value': existing.name, 'created': False})
    try:
        with transaction.atomic():
            unit = UnitCatalog.objects.create(pharmacy=request.pharmacy, name=name)
    except IntegrityError:
        unit = UnitCatalog.objects.get(pharmacy=request.pharmacy, normalized_name=name.casefold())
        return JsonResponse({'ok': True, 'id': unit.pk, 'name': unit.name, 'value': unit.name, 'created': False})
    return JsonResponse({'ok': True, 'id': unit.pk, 'name': unit.name, 'value': unit.name, 'created': True})


def _valid_units(pharmacy):
    """Standard unit codes plus the custom units this branch already uses (for Excel validation / dropdowns)."""
    from .forms import unit_choices
    return [value for value, _ in unit_choices(pharmacy)]


def _find_product_by_scan(pharmacy, code):
    """Branch product (active or not) matching a scanned barcode / SKU / legacy PM label."""
    qs = Product.objects.filter(pharmacy=pharmacy)
    product = qs.filter(barcode__in=barcode_variants(code)).first() or qs.filter(sku__iexact=code).first()
    if product is None:
        prefix = f"PM{pharmacy.id:04d}"
        if code.startswith(prefix) and code[len(prefix):].isdigit():
            product = qs.filter(pk=int(code[len(prefix):])).filter(Q(barcode__isnull=True) | Q(barcode='')).first()
    return product


def _last_batch(product):
    return product.batches.order_by('-received_at', '-pk').first()


@admin_required
def scan_resolve_api(request):
    """What should happen after this scan? new -> register sheet, exists -> restock sheet."""
    if request.pharmacy is None:
        return JsonResponse({'status': 'error', 'message': 'No active branch.'}, status=400)
    code = request.GET.get('code', '').strip()
    if not code or len(code) > 32 or any(ch.isspace() for ch in code) or code.lower().startswith(('http:', 'https:', 'www.')):
        return JsonResponse({'status': 'invalid', 'message': 'This is not a product barcode (link or too long). Scan the barcode on the label.'})
    product = _find_product_by_scan(request.pharmacy, code)
    if product is None:
        return JsonResponse({'status': 'new', 'code': code})
    if not product.is_active:
        return JsonResponse({'status': 'inactive', 'code': code, 'name': product.name, 'id': product.pk})
    last = _last_batch(product)
    return JsonResponse({
        'status': 'exists', 'code': code, 'id': product.pk, 'name': product.name,
        'unit': product.get_unit_display(), 'stock': product.total_quantity,
        'last_cost': str(last.purchase_price) if last else None,
        'last_selling': str(last.selling_price) if last else None,
    })


def _render_sheet(request, **ctx):
    return render_to_string('inventory/_scan_sheet.html', ctx, request=request)


def _sheet_json_error(request, status=200, **ctx):
    return JsonResponse({'ok': False, 'html': _render_sheet(request, **ctx)}, status=status)


def _scan_stay(request):
    """POS restock mode keeps the cashier on the POS page instead of jumping to the Products pages."""
    return (request.POST.get('stay') or request.GET.get('stay')) == '1'


def _scan_saved_payload(request, product, message):
    """JSON after a scan save. 'Save' goes to the product's own page (with a flash message);
    'Save & scan next' keeps scanning and finishes on the Products list with this item highlighted.
    In POS restock mode (stay=1) nothing navigates: the page stays where it is."""
    keep_scanning = request.POST.get('next') == '1'
    stay = _scan_stay(request)
    if stay:
        redirect_url = None
    elif keep_scanning:
        redirect_url = f"{reverse('inventory:product_list')}?highlight={product.pk}"
    else:
        messages.success(request, message)
        redirect_url = reverse('inventory:product_batches', args=[product.pk])
    return {'ok': True, 'product': {'id': product.pk, 'name': product.name}, 'message': message,
            'stock': product.total_quantity, 'sellable': product.sellable_quantity,
            'unit': product.get_unit_display(), 'stay': stay, 'redirect_url': redirect_url}


@admin_required
@require_http_methods(['GET', 'POST'])
def scan_register_view(request):
    """Register a NEW product from a scan. Same forms, rules and service as the full page."""
    if request.pharmacy is None:
        return JsonResponse({'ok': False, 'message': 'No active branch.'}, status=400)
    # The barcode read by the scanner is authoritative for this quick-register flow.
    # Do not allow an empty/mutated readonly input to trigger Product.save()'s generated
    # PM... fallback barcode. The separate hidden value survives custom UI transformations.
    code = (request.POST.get('scanned_barcode') or request.POST.get('barcode', '')
            if request.method == 'POST' else request.GET.get('code', '')).strip()[:32]
    lot = (request.POST.get('batch_number') if request.method == 'POST' else request.GET.get('lot', '')).strip()[:100]
    expiry = request.GET.get('expiry', '').strip()[:10]
    action_url = reverse('inventory:scan_register')
    from urllib.parse import urlencode
    full_url = reverse('inventory:product_create') + '?' + urlencode(
        [(k, v) for k, v in (('barcode', code), ('lot', lot), ('expiry', expiry)) if v])

    if request.method == 'POST':
        # Force the model form to validate/save the exact barcode captured at scan time.
        # The visible barcode input is readonly; this also protects against browser or
        # custom-widget code accidentally submitting it blank and generating a new code.
        product_data = request.POST.copy()
        if code:
            product_data['barcode'] = code
        pform = ProductQuickForm(product_data, pharmacy=request.pharmacy)
        bform = ProductBatchForm(request.POST, require_expiry=bool(request.POST.get('requires_expiry')), pharmacy=request.pharmacy)
        ctx = dict(mode='register', pform=pform, bform=bform, action_url=action_url, code=code, full_url=full_url, stay=_scan_stay(request))
        if pform.is_valid() and bform.is_valid():
            try:
                product, batch = register_product(request.user, request.pharmacy, pform, bform)
            except IntegrityError:
                pform.add_error('name', 'This product (or its barcode) was just registered by someone else. Scan again.')
                return _sheet_json_error(request, **ctx)
            message = f"Product registered: {product.name}. Opening stock: {batch.quantity} {product.get_unit_display()}."
            return JsonResponse(_scan_saved_payload(request, product, message))
        return _sheet_json_error(request, **ctx)

    # Free name look-up (own table -> Open Food Facts -> UPCitemdb). Fills ONLY the name; the person still
    # types prices and stock, and can edit the name. If nothing is found the form is the same as before.
    found = lookup_barcode(code)
    p_initial = {'barcode': code}
    if found and found['display_name']:
        p_initial['name'] = found['display_name']
    pform = ProductQuickForm(pharmacy=request.pharmacy, initial=p_initial)
    initial = {'batch_number': lot, 'purchase_quantity': None, 'units_per_purchase_unit': 1}
    if expiry:
        initial['expiry_date'] = expiry
    bform = ProductBatchForm(initial=initial, require_expiry=True, pharmacy=request.pharmacy)
    return HttpResponse(_render_sheet(request, mode='register', pform=pform, bform=bform, action_url=action_url,
                                      code=code, full_url=full_url, lookup=found, stay=_scan_stay(request),
                                      no_match=bool(is_lookup_enabled() and is_lookup_candidate(code) and not found)))


@admin_required
@require_http_methods(['GET', 'POST'])
def scan_restock_view(request, pk):
    """Receive a NEW batch for an existing product from a scan (restock)."""
    if request.pharmacy is None:
        return JsonResponse({'ok': False, 'message': 'No active branch.'}, status=400)
    product = get_object_or_404(Product, pk=pk, pharmacy=request.pharmacy, is_active=True)
    last = _last_batch(product)
    action_url = reverse('inventory:scan_restock', args=[product.pk])
    full_url = reverse('inventory:batch_add', args=[product.pk])
    active = list(product.active_batches())
    stock = sum(b.quantity for b in active)
    nearest_expiry = next((b.expiry_date for b in active if b.expiry_date), None)
    stock_state = 'out' if stock <= 0 else ('low' if stock <= product.reorder_level else 'ok')
    base = dict(mode='restock', product=product, last=last, stock=stock, stock_state=stock_state,
                reorder_level=product.reorder_level, batch_count=len(active), nearest_expiry=nearest_expiry,
                avg_cost=product.weighted_average_cost, action_url=action_url, full_url=full_url,
                stay=_scan_stay(request))

    if request.method == 'POST':
        bform = ProductBatchForm(request.POST, require_expiry=product.requires_expiry, pharmacy=request.pharmacy)
        if bform.is_valid():
            batch = receive_batch(request.user, product, bform)
            message = (f"Stock updated: {product.name}. Added {batch.quantity} {product.get_unit_display()}; "
                       f"stock is now {product.total_quantity}.")
            return JsonResponse(_scan_saved_payload(request, product, message))
        return _sheet_json_error(request, bform=bform, **base)

    initial = {'batch_number': request.GET.get('lot', '').strip()[:100], 'purchase_quantity': None,
               'units_per_purchase_unit': 1}
    if request.GET.get('expiry'):
        initial['expiry_date'] = request.GET['expiry'].strip()[:10]
    if last:   # same packaging and selling price as last time; cost and quantity are always typed fresh
        initial.update(purchase_unit=last.purchase_unit, units_per_purchase_unit=last.units_per_purchase_unit or 1,
                       selling_price=last.selling_price)
    bform = ProductBatchForm(initial=initial, require_expiry=product.requires_expiry, pharmacy=request.pharmacy)
    return HttpResponse(_render_sheet(request, bform=bform, **base))


@admin_required
def product_create_view(request):
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')

    # Optional prefill from the scan sheet's "More options" link: ?barcode=…&lot=…&expiry=YYYY-MM-DD
    initial = {k: v for k, v in (('barcode', request.GET.get('barcode', '').strip()[:32]),) if v}
    if request.method == 'GET' and initial.get('barcode'):
        found = lookup_barcode(initial['barcode'])     # free name look-up; name only, never prices
        if found and found['display_name']:
            initial['name'] = found['display_name']
    batch_initial = {k: v for k, v in (('batch_number', request.GET.get('lot', '').strip()[:100]),
                                       ('expiry_date', request.GET.get('expiry', '').strip()[:10])) if v}
    product_form = ProductForm(request.POST or None, request.FILES or None, pharmacy=request.pharmacy, initial=initial)
    # The opening batch's expiry requirement follows the "Has expiry date" checkbox
    # on the product form itself. Read it straight from POST because the
    # Product row doesn't exist yet — nothing to check require_expiry against.
    requires_expiry = (request.method != 'POST') or bool(request.POST.get('requires_expiry'))
    batch_form = ProductBatchForm(request.POST or None, require_expiry=requires_expiry, initial=batch_initial, pharmacy=request.pharmacy)

    if request.method == 'POST' and product_form.is_valid() and batch_form.is_valid():
        product, batch = register_product(request.user, request.pharmacy, product_form, batch_form)
        messages.success(request, f"Product '{product.name}' registered with {batch.quantity} {product.get_unit_display()} as opening stock.")
        return redirect('inventory:product_list')

    return render(request, 'inventory/product_form.html', {
        'product_form': product_form, 'batch_form': batch_form, 'is_edit': False,
    })


@admin_required
def product_edit_view(request, pk):
    product = get_object_or_404(Product, pk=pk, pharmacy=request.pharmacy)
    form = ProductForm(request.POST or None, request.FILES or None, instance=product, pharmacy=request.pharmacy)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f"Product '{product.name}' updated successfully.")
        return redirect('inventory:product_list')

    return render(request, 'inventory/product_edit_form.html', {'form': form, 'product': product})


# ---------------------------------------------------------------------------
# Product deletion: the owner chooses between two very different actions.
#   soft       -> product is hidden from the catalog (is_active=False). Stock,
#                 sales history, receipts and reports are all kept.
#   permanent  -> product and EVERYTHING attached to it is erased from the
#                 database (batches, stock movements, sale lines, debts of sales
#                 that only contained this product, related notifications).
#                 Nothing is left behind and it cannot be undone.
# ---------------------------------------------------------------------------
DELETE_CONFIRM_WORD = 'DELETE'

# Extra safeguards that apply ONLY to permanent deletion:
#   1. a fresh data backup must have been created/downloaded in the last hour,
#   2. the owner must re-enter their account password,
#   3. wrong passwords are limited (lock + alert) so a stolen/unattended session
#      cannot be used to guess the password.
PURGE_BACKUP_MAX_AGE = timedelta(minutes=60)
PURGE_MAX_PASSWORD_FAILS = 5
PURGE_LOCK_SECONDS = 15 * 60


def _delete_targets(request, ids):
    """Products of the CURRENT branch matching the posted ids (forged ids are ignored)."""
    clean = []
    for raw in ids:
        try:
            clean.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not clean:
        return Product.objects.none()
    return Product.objects.filter(pk__in=clean, pharmacy=request.pharmacy)


def _purge_impact(products):
    """Describe what a permanent delete would erase, so the owner sees it BEFORE confirming."""
    from sales.models import SaleItem, Debt

    ids = list(products.values_list('pk', flat=True))
    items = SaleItem.objects.filter(product_id__in=ids)
    touched = set(items.values_list('sale_id', flat=True))
    kept = set(
        SaleItem.objects.filter(sale_id__in=touched)
        .exclude(pk__in=items.values('pk'))
        .values_list('sale_id', flat=True)
    )
    removed_sale_ids = touched - kept
    debts = list(Debt.objects.filter(sale_id__in=removed_sale_ids))
    batches = ProductBatch.objects.filter(product_id__in=ids)
    return {
        'products': len(ids),
        'batches': batches.count(),
        'units': batches.aggregate(total=Sum('quantity'))['total'] or 0,
        'movements': StockMovement.objects.filter(product_id__in=ids).count(),
        'sale_lines': items.count(),
        'sales_removed': len(removed_sale_ids),
        'sales_trimmed': len(kept),
        'debts_removed': len(debts),
        'debts_unpaid': sum((d.balance for d in debts if not d.is_settled), Decimal('0')),
    }


def _purge_products(products):
    """Irreversibly delete products and every record that hangs off them.

    Runs in ONE transaction: either everything is erased or nothing is.
    Returns the number of products deleted.
    """
    from sales.models import Sale, SaleItem, Debt
    from notifications.models import Notification

    product_list = list(products)
    ids = [p.pk for p in product_list]
    if not ids:
        return 0
    pharmacy_ids = {p.pharmacy_id for p in product_list}

    with transaction.atomic():
        list(Product.objects.select_for_update().filter(pk__in=ids).order_by('pk'))

        # 1) Sale lines. SaleItem -> Product/Batch is PROTECT, so lines must go first.
        items = SaleItem.objects.filter(product_id__in=ids)
        touched = set()
        removed_value = defaultdict(Decimal)
        for sale_id, price, qty in items.values_list('sale_id', 'unit_price', 'quantity'):
            touched.add(sale_id)
            removed_value[sale_id] += price * qty
        items.delete()

        # 2) Sales left with no lines disappear completely (their Debt row goes with
        #    them via CASCADE). Sales that also contained other products stay, and a
        #    debt attached to them is reduced by the value of the removed lines.
        remaining = set(
            SaleItem.objects.filter(sale_id__in=touched).values_list('sale_id', flat=True)
        )
        Sale.objects.filter(pk__in=touched - remaining).delete()
        for debt in Debt.objects.filter(sale_id__in=remaining):
            new_owed = max(debt.amount_owed - removed_value[debt.sale_id], debt.amount_paid)
            if new_owed != debt.amount_owed:
                debt.amount_owed = new_owed
                debt.is_settled = debt.amount_paid >= new_owed
                debt.save(update_fields=['amount_owed', 'is_settled', 'updated_at'])

        # 3) Notifications that link to these products (merge / duplicate-product alerts).
        link_q = Q()
        for pk in ids:
            prefix = reverse('inventory:product_edit', args=[pk]).rsplit('edit/', 1)[0]
            link_q |= Q(link_url__startswith=prefix)
        Notification.objects.filter(link_q, pharmacy_id__in=pharmacy_ids).delete()

        # 4) The products themselves. CASCADE removes their batches and stock movements.
        import_ids = set(
            ProductBatch.objects.filter(product_id__in=ids, stock_import__isnull=False)
            .values_list('stock_import_id', flat=True)
        )
        Product.objects.filter(pk__in=ids).delete()

        # 5) Import records that no longer have any stock attached. This also lets the
        #    same Excel file be uploaded again (its fingerprint is otherwise remembered).
        StockImport.objects.filter(pk__in=import_ids, batches__isnull=True).delete()

    return len(ids)


def _purge_backup_ready(request):
    """True when this session created a backup for the current user within the last hour."""
    from core.models import UserDataBackup

    info = request.session.get('purge_backup') or {}
    if not info.get('pk'):
        return False
    backup = UserDataBackup.objects.filter(pk=info['pk'], owner=request.user.business_owner()).first()
    return bool(backup and timezone.now() - backup.created_at <= PURGE_BACKUP_MAX_AGE)


def _purge_lock_minutes_left(request):
    """Minutes left on the wrong-password lock (0 when not locked)."""
    until = request.session.get('purge_locked_until') or 0
    remaining = until - time.time()
    if remaining > 0:
        return int(remaining // 60) + 1
    if until:
        request.session.pop('purge_locked_until', None)
        request.session['purge_pw_fails'] = 0
    return 0


def _check_purge_password(request, password):
    """Re-authenticate the owner. Returns (ok, error_message)."""
    left = _purge_lock_minutes_left(request)
    if left:
        return False, f"Too many wrong password attempts. Permanent delete is locked for about {left} more minute{'s' if left != 1 else ''}."

    if password and request.user.check_password(password):
        request.session['purge_pw_fails'] = 0
        return True, None

    fails = request.session.get('purge_pw_fails', 0) + 1
    request.session['purge_pw_fails'] = fails
    if fails >= PURGE_MAX_PASSWORD_FAILS:
        request.session['purge_locked_until'] = time.time() + PURGE_LOCK_SECONDS
        request.session['purge_pw_fails'] = 0
        try:
            notify_many(
                request.pharmacy.managers.all(),
                title='Permanent delete locked',
                message=(
                    f"There were {PURGE_MAX_PASSWORD_FAILS} wrong password attempts on the permanent-delete "
                    f"screen for {request.user.get_full_name() or request.user.username}'s account. "
                    f"It is locked for {PURGE_LOCK_SECONDS // 60} minutes. If this was not you, change your password."
                ),
                category=Notification.Category.OTHER,
                level=Notification.Level.DANGER,
                pharmacy=request.pharmacy,
            )
        except Exception:
            logger.exception('Could not send permanent-delete lock notification')
        return False, f"Too many wrong password attempts. Permanent delete has been locked for {PURGE_LOCK_SECONDS // 60} minutes."
    left_tries = PURGE_MAX_PASSWORD_FAILS - fails
    return False, f"Incorrect password. {left_tries} attempt{'s' if left_tries != 1 else ''} left before a {PURGE_LOCK_SECONDS // 60}-minute lock."


def _render_delete_review(request, products, error=None):
    product_list = list(products.order_by('name'))
    return render(request, 'inventory/product_delete_confirm.html', {
        'products': product_list[:15],
        'more_count': max(len(product_list) - 15, 0),
        'product_ids': [p.pk for p in product_list],
        'impact': _purge_impact(products),
        'confirm_word': DELETE_CONFIRM_WORD,
        'error': error,
        'backup_ready': _purge_backup_ready(request),
        'backup_max_minutes': int(PURGE_BACKUP_MAX_AGE.total_seconds() // 60),
        'purge_lock_minutes': _purge_lock_minutes_left(request),
    })


@admin_required
@require_http_methods(["POST"])
def product_delete_review_view(request):
    """Step 1: show what would happen and let the owner choose soft vs permanent delete."""
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')
    products = _delete_targets(request, request.POST.getlist('product_ids'))
    if not products.exists():
        messages.warning(request, "No matching products were found to delete.")
        return redirect('inventory:product_list')
    return _render_delete_review(request, products)


def _handle_delete(request, ids):
    """Step 2: perform the delete the owner confirmed (mode = soft | permanent)."""
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')
    products = _delete_targets(request, ids)
    if not products.exists():
        messages.warning(request, "No matching products were found to delete.")
        return redirect('inventory:product_list')

    mode = request.POST.get('mode', 'soft')

    if mode == 'permanent':
        if request.POST.get('confirm_text', '').strip() != DELETE_CONFIRM_WORD:
            return _render_delete_review(
                request, products,
                error=f"Type {DELETE_CONFIRM_WORD} exactly to confirm permanent deletion. Nothing was deleted.",
            )
        if _purge_lock_minutes_left(request):
            ok, message = _check_purge_password(request, '')  # returns the lock message without counting
            return _render_delete_review(request, products, error=f"{message} Nothing was deleted.")
        if not _purge_backup_ready(request):
            return _render_delete_review(
                request, products,
                error="Create and download your data backup first (it must be less than an hour old). Nothing was deleted.",
            )
        ok, message = _check_purge_password(request, request.POST.get('current_password', ''))
        if not ok:
            return _render_delete_review(request, products, error=f"{message} Nothing was deleted.")
        try:
            count = _purge_products(products)
        except ProtectedError:
            messages.error(request, "Some records are still linked to these products, so nothing was deleted. Please contact support.")
            return redirect('inventory:product_list')
        request.session.pop('purge_backup', None)  # every permanent delete needs its own fresh backup
        messages.success(request, f"{count} product{'s' if count != 1 else ''} and all related records were permanently deleted.")
        return redirect('inventory:product_list')

    # Default: soft delete (hide only, everything else is preserved).
    soft = products.filter(is_active=True)
    count = soft.count()
    if count == 0:
        messages.warning(request, "These products are already removed from the catalog.")
        return redirect('inventory:product_list')
    soft.update(is_active=False)
    messages.success(request, f"{count} product{'s' if count != 1 else ''} removed from the catalog. Stock and sales history were kept.")
    return redirect('inventory:product_list')


@admin_required
@require_http_methods(["POST"])
def product_delete_backup_view(request):
    """Create the owner's data backup and stream it as a download.

    Called from the permanent-delete screen. A successful response also records
    (server-side, in the session) that a fresh backup exists, which is what
    unlocks permanent deletion.
    """
    from core.backup_utils import create_user_backup

    if request.user.is_superuser or not request.user.accessible_management_pharmacies().exists():
        return JsonResponse({'error': 'A data backup is not available for this account, so permanent delete cannot be used.'}, status=400)
    try:
        backup = create_user_backup(request.user)
        file_obj = default_storage.open(backup.storage_path, 'rb')
    except Exception:
        logger.exception('Backup before permanent delete failed')
        return JsonResponse({'error': 'The backup could not be created, so permanent delete is not available. Please try again.'}, status=500)

    request.session['purge_backup'] = {'pk': backup.pk}
    response = FileResponse(file_obj, as_attachment=True, filename=backup.filename, content_type='application/zip')
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'no-store'
    response['X-Backup-Filename'] = backup.filename
    return response


@admin_required
@require_http_methods(["POST"])
def product_delete_view(request, pk):
    return _handle_delete(request, [pk])


@admin_required
@require_http_methods(["POST"])
def product_bulk_delete_view(request):
    return _handle_delete(request, request.POST.getlist('product_ids'))


@admin_required
def product_merge_view(request, pk):
    """Merge one catalog product into another product in the same branch.

    The destination product keeps its SKU/barcode/catalog identity. Batches,
    stock movements, and historical sale lines belonging to the source are
    reassigned to the destination, then the source is archived (not deleted)
    so the operation remains traceable and old identifiers are not silently
    reused. This is intentionally branch-scoped and atomic.
    """
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')

    source = get_object_or_404(
        Product, pk=pk, pharmacy=request.pharmacy, is_active=True
    )
    targets = Product.objects.filter(
        pharmacy=request.pharmacy, is_active=True
    ).exclude(pk=source.pk).order_by('name')

    requested_target = request.GET.get('target') or request.POST.get('target_product')
    selected_target = None
    if requested_target:
        try:
            selected_target = targets.filter(pk=int(requested_target)).first()
        except (TypeError, ValueError):
            selected_target = None

    if request.method == 'POST':
        if not selected_target:
            messages.error(request, 'Please select the correct destination product before merging.')
        else:
            target = selected_target
            source_name = source.name
            target_name = target.name
            with transaction.atomic():
                # Lock both rows to avoid concurrent merges touching the same
                # catalog entries at the same time.
                locked_ids = sorted([source.pk, target.pk])
                list(Product.objects.select_for_update().filter(pk__in=locked_ids).order_by('pk'))

                # Re-point every historical/current record that identifies the
                # source product. ProductBatch is the important stock move;
                # SaleItem is protected against deletion, so it must be moved
                # explicitly to preserve sales history.
                batch_count = ProductBatch.objects.filter(product=source).update(product=target)
                movement_count = StockMovement.objects.filter(product=source).update(product=target)

                from sales.models import SaleItem
                sale_item_count = SaleItem.objects.filter(product=source).update(product=target)

                source.is_active = False
                source.save(update_fields=['is_active', 'updated_at'])

                notify_many(
                    target.pharmacy.managers.all(),
                    title='Products Merged',
                    message=(
                        f"'{source_name}' was merged into '{target_name}' by "
                        f"{request.user.get_full_name() or request.user.username}. "
                        f"{batch_count} batch(es), {sale_item_count} sale line(s), and "
                        f"{movement_count} stock movement(s) were moved to the destination product. "
                        f"The source product was archived."
                    ),
                    category=Notification.Category.STOCK_ALERT,
                    level=Notification.Level.WARNING,
                    pharmacy=target.pharmacy,
                    link_url=reverse('inventory:product_batches', args=[target.pk]),
                )

            messages.success(
                request,
                f"'{source_name}' has been merged into '{target_name}'. Stock and sales history were preserved."
            )
            return redirect('inventory:product_list')

    return render(request, 'inventory/product_merge.html', {
        'source': source,
        'targets': targets,
        'selected_target': selected_target,
    })


@employee_or_admin_required
@xframe_options_sameorigin   # batch add/edit modal is an iframe; after saving it is redirected here and must not be blocked by X-Frame-Options: DENY
def product_batches_view(request, pk):
    """Shows every batch ever received for a product — the core of batch-level analysis."""
    product = get_object_or_404(Product, pk=pk, pharmacy=request.pharmacy)
    batches = product.batches.all().order_by('-received_at', '-pk')
    page_obj = Paginator(batches, 20).get_page(request.GET.get('page'))
    return render(request, 'inventory/product_batches.html', {'product': product, 'page_obj': page_obj})


@admin_required
@xframe_options_sameorigin
def batch_add_view(request, pk):
    """
    Adds a NEW, distinct batch to an existing product — this is the restock
    action. It never merges into an existing batch's quantity; a fresh batch
    row is always created, so the addition is clearly traceable as new stock.
    """
    product = get_object_or_404(Product, pk=pk, pharmacy=request.pharmacy)
    form = ProductBatchForm(request.POST or None, require_expiry=product.requires_expiry, pharmacy=request.pharmacy)
    if request.method == 'POST' and form.is_valid():
        batch = receive_batch(request.user, product, form)
        messages.success(request, f"New batch of {batch.quantity} {product.get_unit_display()} added to '{product.name}'.")
        return redirect('inventory:product_batches', pk=product.pk)

    return render(request, 'inventory/batch_form.html', {'form': form, 'product': product})


@admin_required
@xframe_options_sameorigin
def batch_edit_view(request, pk, batch_pk):
    """
    Edits an EXISTING batch's buying price, selling price, and remaining
    quantity — for the shop owner/supervisor to correct a mistaken entry or
    adjust stock (e.g. a physical stock count found a different quantity).
    Any quantity change is logged as a StockMovement ADJUSTMENT so the audit
    trail always explains where the numbers came from.
    """
    product = get_object_or_404(Product, pk=pk, pharmacy=request.pharmacy)
    batch = get_object_or_404(ProductBatch, pk=batch_pk, product=product)
    old_quantity = batch.quantity

    form = ProductBatchEditForm(request.POST or None, instance=batch, require_expiry=product.requires_expiry)
    if request.method == 'POST' and form.is_valid():
        updated_batch = form.save()

        quantity_change = updated_batch.quantity - old_quantity
        if quantity_change != 0:
            StockMovement.objects.create(
                product=product, batch=updated_batch, movement_type=StockMovement.MovementType.ADJUSTMENT,
                quantity_change=quantity_change,
                note=f"Manual edit by {request.user.get_full_name() or request.user.username}: quantity/price updated",
                performed_by=request.user,
            )

        messages.success(request, f"Batch for '{product.name}' updated successfully.")
        flag_batch_if_suspicious(updated_batch)
        return redirect('inventory:product_batches', pk=product.pk)

    return render(request, 'inventory/batch_edit_form.html', {'form': form, 'product': product, 'batch': batch})


BULK_UPLOAD_HEADERS = bi.BULK_UPLOAD_HEADERS
RESTOCK_TEMPLATE_HEADERS = bi.RESTOCK_TEMPLATE_HEADERS

# Accepted spellings for the optional 'requires_expiry' bulk-upload column.
# Leave the cell blank to keep the product's current setting (or default to
# YES — enforced — for a brand-new product).
_TRUTHY_REQUIRES_EXPIRY = {'1', 'TRUE', 'YES', 'Y', 'NDIYO', 'NDIO'}
_FALSY_REQUIRES_EXPIRY = {'0', 'FALSE', 'NO', 'N', 'HAPANA'}


def _parse_requires_expiry(raw):
    """Returns True/False if the cell explicitly says so, or None if left blank."""
    value = str(raw).strip().upper() if raw not in (None, '') else ''
    if not value:
        return None
    if value in _TRUTHY_REQUIRES_EXPIRY:
        return True
    if value in _FALSY_REQUIRES_EXPIRY:
        return False
    raise ValueError(
        f"Invalid value '{raw}' for requires_expiry. Use YES/NO (or NDIYO/HAPANA), or leave blank."
    )


@admin_required
def bulk_upload_template_view(request):
    valid_units = _valid_units(request.pharmacy)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Products"
    ws.append(BULK_UPLOAD_HEADERS)
    ws.append([
        'Panadol 500mg', '', 'Painkiller', 'BATCH-001', 'TABLET', '',
        'BOX', 100, 5, 4200, 100, 50, '2027-01-31', 'YES',
    ])
    header_font = openpyxl.styles.Font(bold=True, color='FFFFFF')
    header_fill = openpyxl.styles.PatternFill('solid', fgColor='0F766E')
    for cell in ws[1]:
        cell.font, cell.fill = header_font, header_fill
    for i, header in enumerate(BULK_UPLOAD_HEADERS, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(16, min(28, len(header) + 4))
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions
    bi.text_columns(ws, BULK_UPLOAD_HEADERS)
    bi.add_dropdowns(ws, BULK_UPLOAD_HEADERS, valid_units)

    guide = wb.create_sheet('Guide')
    for line in (
        'SmartPOS bulk product upload - quick guide',
        '',
        'Required on every row: name, purchase_quantity, purchase_price, selling_price.',
        'unit = smallest selling unit (TABLET, BOTTLE, PIECE...). Pick from the dropdown.',
        'purchase_unit / units_per_purchase_unit = how you bought it, e.g. BOX of 100 TABLET.',
        'purchase_price = price of ONE purchase unit. The system works out the cost per base unit.',
        'expiry_date = YYYY-MM-DD. Leave blank only when requires_expiry is NO.',
        'barcode, sku and batch_number are Text. Leave barcode blank to auto-generate one.',
        'Before anything is saved you will see a preview: you can deselect rows, and download the rows with problems to fix them.',
        '',
        'Allowed units: ' + ', '.join(valid_units),
    ):
        guide.append([line])
    guide['A1'].font = openpyxl.styles.Font(bold=True, size=14)
    guide.column_dimensions['A'].width = 110

    buffer = io.BytesIO(); wb.save(buffer); buffer.seek(0)
    response = HttpResponse(buffer.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="product_bulk_upload_template.xlsx"'
    return response


@admin_required
def restock_template_view(request):
    """Download a purchase-ready snapshot of this branch's current catalog.

    Filters: category (repeatable), low_stock=1, product_ids=comma-separated.
    Purchase quantity remains an intentional input column; suggested quantity is
    calculated from reorder level minus current stock and is never auto-applied.
    """
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')
    products = Product.objects.filter(pharmacy=request.pharmacy, is_active=True).select_related('category').prefetch_related('batches')
    categories = [x.strip() for x in request.GET.getlist('category') if x.strip()]
    if categories:
        products = products.filter(category_id__in=categories)
    ids = [x.strip() for x in request.GET.get('product_ids', '').split(',') if x.strip().isdigit()]
    if ids:
        products = products.filter(pk__in=ids)
    products = list(products.order_by('name'))
    if request.GET.get('low_stock') in {'1', 'true', 'yes'}:
        products = [p for p in products if p.is_low_stock]

    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Restock'
    ws.append(RESTOCK_TEMPLATE_HEADERS)
    for p in products:
        base_unit = p.unit
        batch = p.next_batch or p.batches.filter(quantity__gt=0).order_by(*FEFO_ORDER).first()
        purchase_unit = (batch.purchase_unit if batch and batch.purchase_unit else base_unit)
        conversion = (batch.units_per_purchase_unit if batch else 1) or 1
        purchase_price = (batch.purchase_unit_price if batch and batch.purchase_unit_price else p.current_purchase_price)
        selling_price = (batch.selling_price if batch else p.current_selling_price)
        expiry = (batch.expiry_date if batch else p.nearest_expiry_date)
        ws.append([
            p.name, p.sku or '', p.effective_barcode, p.category.name if p.category else '',
            base_unit, p.total_quantity, p.reorder_level, max(p.reorder_level - p.total_quantity, 0),
            purchase_unit, conversion, '', purchase_price or '', selling_price or '',
            '', expiry or '',
            'YES' if p.requires_expiry else 'NO',
        ])
    for i, header in enumerate(RESTOCK_TEMPLATE_HEADERS, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(16, min(30, len(header) + 5))
    ws.freeze_panes = 'A2'; ws.auto_filter.ref = ws.dimensions
    ws['A1'].comment = openpyxl.comments.Comment('Do not edit name/SKU/barcode/current stock. Fill ONLY the rows you received stock for: enter Purchase Quantity, purchase price, selling price and expiry. Rows with an empty Purchase Quantity are ignored on upload, so you do not need to delete them.', 'SmartPOS')
    ws['K1'].comment = openpyxl.comments.Comment('INPUT: number of purchase units received. Example: 5 cartons. Leave EMPTY for products you did not receive - they stay unchanged.', 'SmartPOS')
    ws['N1'].comment = openpyxl.comments.Comment('Optional. Leave empty and the system creates a new batch number automatically.', 'SmartPOS')
    buffer = io.BytesIO(); wb.save(buffer); buffer.seek(0)
    suffix = 'low-stock' if request.GET.get('low_stock') in {'1','true','yes'} else 'all-products'
    response = HttpResponse(buffer.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="restock_template_{suffix}.xlsx"'
    return response



# ---------------------------------------------------------------------------
# Bulk upload: preview -> select -> confirm  (V54.11)
# ---------------------------------------------------------------------------
_PREVIEW_DIR = 'bulk_imports'
_PREVIEW_MAX_AGE_SECONDS = 24 * 3600
_XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _preview_path(pharmacy, token):
    return f'{_PREVIEW_DIR}/{pharmacy.pk}/{token}.xlsx'


def _build_catalog(pharmacy):
    products = Product.objects.filter(pharmacy=pharmacy).annotate(
        stock_total=Sum('batches__quantity', filter=Q(batches__quantity__gt=0))
    ).values('pk', 'name', 'sku', 'barcode', 'unit', 'requires_expiry', 'is_active', 'stock_total')
    items = [{
        'pk': p['pk'], 'name': p['name'], 'sku': p['sku'] or '', 'barcode': p['barcode'] or '',
        'unit': p['unit'], 'requires_expiry': p['requires_expiry'], 'is_active': p['is_active'],
        'stock': int(p['stock_total'] or 0),
    } for p in products]
    categories = list(Category.objects.filter(pharmacy=pharmacy).values_list('name', flat=True))
    return bi.Catalog(items, categories)


def _validate_file(raw, mode, pharmacy):
    headers, rows = bi.read_sheet(raw)
    valid_units = _valid_units(pharmacy)
    results = bi.validate_rows(rows, mode, _build_catalog(pharmacy), valid_units, timezone.localdate())
    return headers, results


def _purge_old_previews(pharmacy):
    """Best-effort cleanup of abandoned preview files older than a day."""
    try:
        _dirs, files = default_storage.listdir(f'{_PREVIEW_DIR}/{pharmacy.pk}')
        now = timezone.now()
        for name in files:
            path = f'{_PREVIEW_DIR}/{pharmacy.pk}/{name}'
            if (now - default_storage.get_modified_time(path)).total_seconds() > _PREVIEW_MAX_AGE_SECONDS:
                default_storage.delete(path)
    except Exception:
        pass


def _forget_preview(request):
    state = request.session.pop('bulk_preview', None)
    if state and getattr(request, 'pharmacy', None):
        try:
            default_storage.delete(_preview_path(request.pharmacy, state['token']))
        except Exception:
            pass


def _preview_state(request, token):
    state = request.session.get('bulk_preview')
    if not state or not token or state.get('token') != token:
        return None, None
    try:
        with default_storage.open(_preview_path(request.pharmacy, token), 'rb') as fh:
            return state, fh.read()
    except Exception:
        return None, None


def _mode_label(mode):
    return dict(StockImport.ImportMode.choices).get(mode, mode)


def _preview_context(request, state, headers, results, extra=None):
    ctx = {
        'form': BulkUploadForm(initial={'import_mode': state['mode']}),
        'categories': Category.objects.filter(pharmacy=request.pharmacy).order_by(Lower('name'), 'name'),
        'preview': {
            'token': state['token'], 'mode': state['mode'], 'mode_label': _mode_label(state['mode']),
            'file_name': state['file_name'], 'rows': results, 'summary': bi.summarise(results),
        },
    }
    if extra:
        ctx.update(extra)
    return ctx


@admin_required
def bulk_upload_view(request):
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')

    form = BulkUploadForm(request.POST or None, request.FILES or None)
    ctx = {'form': form, 'categories': Category.objects.filter(pharmacy=request.pharmacy).order_by('name')}

    if request.method == 'POST' and form.is_valid():
        excel_file = form.cleaned_data['excel_file']
        mode = form.cleaned_data['import_mode']
        raw = excel_file.read()
        sha = hashlib.sha256(raw).hexdigest()
        previous = StockImport.objects.filter(pharmacy=request.pharmacy, mode=mode, file_sha256=sha).first()
        if previous:
            ctx['file_errors'] = [
                f'This exact Excel file was already imported as {previous.reference}. The duplicate upload was blocked to protect stock accuracy. '
                'If some rows were left out, download the "rows not imported" template from that import, fix it and upload the new file.'
            ]
        else:
            try:
                headers, results = _validate_file(raw, mode, request.pharmacy)
            except bi.BulkFileError as exc:
                ctx['file_errors'] = [str(exc)]
            else:
                _forget_preview(request)
                _purge_old_previews(request.pharmacy)
                token = secrets.token_urlsafe(16)
                default_storage.save(_preview_path(request.pharmacy, token), ContentFile(raw))
                state = {'token': token, 'mode': mode, 'file_name': (excel_file.name or '')[:255], 'sha': sha}
                request.session['bulk_preview'] = state
                ctx.update(_preview_context(request, state, headers, results))
    return render(request, 'inventory/bulk_upload.html', ctx)


@admin_required
@require_http_methods(['POST'])
def bulk_upload_confirm_view(request):
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')
    token = request.POST.get('token', '')
    state, raw = _preview_state(request, token)
    if not state:
        messages.error(request, 'This preview has expired. Please upload the Excel file again.')
        return redirect('inventory:bulk_upload')

    mode = state['mode']
    previous = StockImport.objects.filter(pharmacy=request.pharmacy, mode=mode, file_sha256=state['sha']).first()
    if previous:
        _forget_preview(request)
        messages.warning(request, f'This file was already imported as {previous.reference}. Nothing was changed.')
        return redirect('inventory:bulk_upload')

    try:
        headers, results = _validate_file(raw, mode, request.pharmacy)
    except bi.BulkFileError as exc:
        messages.error(request, str(exc))
        return redirect('inventory:bulk_upload')

    selected = {int(x) for x in request.POST.getlist('rows') if x.isdigit()}
    chosen = [r for r in results if r['row'] in selected and r['status'] not in ('error', 'skipped')]
    if not chosen:
        messages.error(request, 'Select at least one valid row to import.')
        return render(request, 'inventory/bulk_upload.html', _preview_context(request, state, headers, results))

    outcome = _commit_import(
        [r['rec'] for r in chosen], mode, request.user, request.pharmacy,
        state['file_name'], state['sha'],
    )
    imported_rows = {r['row'] for r in chosen} if outcome['committed'] else set()
    imported_rows -= set(outcome.get('failed_rows', {}))
    left_out = []
    failed_rows = outcome.get('failed_rows', {})
    skipped_count = 0
    for r in results:
        if r['status'] == 'skipped':
            skipped_count += 1          # untouched restock rows: not a problem, nothing to fix
            continue
        if r['row'] in imported_rows and r['row'] not in failed_rows:
            continue
        reason = 'Has errors - fix it in the template' if r['status'] == 'error' else 'Deselected by you'
        if r['row'] in failed_rows:
            reason = f"Could not be saved: {failed_rows[r['row']]}"
        elif not outcome['committed'] and r['row'] in {c['row'] for c in chosen}:
            reason = 'Not imported (import was rolled back)'
        left_out.append({'row': r['row'], 'name': r['display']['name'], 'status': r['status'],
                         'reason': reason, 'messages': [e['message'] for e in r['errors']] or [w['message'] for w in r['warnings']]})

    if outcome['committed']:
        messages.success(
            request,
            f"Bulk upload complete: {len(imported_rows)} row(s) imported - {outcome['new_products']} new product(s), "
            f"{outcome['new_batches']} new batch(es), {outcome['topped_up']} exact batch restock(s), {outcome['reused_batches']} reused opening batch(es). "
            f"Reference: {outcome['reference']}."
        )
        if outcome.get('no_expiry'):
            messages.info(
                request,
                f"{outcome['no_expiry']} row(s) were saved without an expiry date, so they will never appear in expiry alerts."
            )
        notify_many(
            request.pharmacy.managers.all(),
            title="Bulk Stock Upload Completed",
            message=(
                f"{outcome['new_products']} new product(s), {outcome['new_batches']} new batch(es), "
                f"{outcome['topped_up']} exact batch restock(s) by "
                f"{request.user.get_full_name() or request.user.username}. "
                f"Reference: {outcome['reference']}."
            ),
            category=Notification.Category.STOCK_ALERT, pharmacy=request.pharmacy,
        )
    else:
        messages.error(request, 'Import was rolled back - no stock was changed. See the details below.')

    return render(request, 'inventory/bulk_upload.html', {
        'form': BulkUploadForm(initial={'import_mode': mode}),
        'categories': Category.objects.filter(pharmacy=request.pharmacy).order_by(Lower('name'), 'name'),
        'results': outcome, 'imported_count': len(imported_rows), 'left_out': left_out, 'skipped_count': skipped_count,
        'left_out_rows': [x['row'] for x in left_out], 'token': token,
    })


@admin_required
@require_http_methods(['POST'])
def bulk_upload_fix_template_view(request):
    """Excel of the rows that were not imported (errors, or deselected), with the problems spelled out."""
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')
    token = request.POST.get('token', '')
    state, raw = _preview_state(request, token)
    if not state:
        messages.error(request, 'This preview has expired. Please upload the Excel file again.')
        return redirect('inventory:bulk_upload')
    try:
        headers, results = _validate_file(raw, state['mode'], request.pharmacy)
    except bi.BulkFileError as exc:
        messages.error(request, str(exc))
        return redirect('inventory:bulk_upload')

    asked = {int(x) for x in request.POST.getlist('rows') if x.isdigit()}
    picked = [r for r in results if r['row'] in asked and r['status'] != 'skipped'] if asked else [r for r in results if r['status'] == 'error']
    if not picked:
        messages.info(request, 'There are no problem rows to download.')
        return redirect('inventory:bulk_upload')
    reasons = {r['row']: 'Deselected by you - no errors, ready to upload.' for r in picked if r['status'] != 'error'}
    valid_units = _valid_units(request.pharmacy)
    data = bi.build_fix_workbook(headers, picked, valid_units, reasons)
    response = HttpResponse(data, content_type=_XLSX_MIME)
    response['Content-Disposition'] = f'attachment; filename="products_to_fix_{timezone.localdate():%Y%m%d}.xlsx"'
    return response


def _commit_import(records, mode, user, pharmacy, file_name, file_sha256):
    """Atomically write the already-validated records.

    OPENING never tops up an existing batch; RESTOCK tops up only an exact
    matching live batch (product + batch number + expiry + cost + price +
    packaging). Any unexpected database error rolls the whole import back.
    """
    result = {
        'new_products': 0, 'new_batches': 0, 'topped_up': 0, 'reused_batches': 0,
        'errors': [], 'committed': False, 'reference': '', 'mode': mode, 'failed_rows': {},
    }
    if mode not in {'OPENING', 'RESTOCK'}:
        result['errors'].append({'row': '-', 'message': 'Invalid import mode.'})
        return result
    if not records:
        result['errors'].append({'row': '-', 'message': 'No rows were selected for import.'})
        return result

    reference = f"IMP-{timezone.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3).upper()}"
    result['reference'] = reference
    suspicious_messages = []

    try:
        with transaction.atomic():
            stock_import = StockImport.objects.create(
                pharmacy=pharmacy, mode=mode, reference=reference,
                file_name=(file_name or '')[:255],
                file_sha256=file_sha256,
                row_count=len(records), created_by=user,
            )

            for rec in records:
                snapshot = {k: result[k] for k in ('new_products', 'new_batches', 'topped_up', 'reused_batches')}
                suspicious_len = len(suspicious_messages)
                try:
                    # One savepoint per row: a single bad row must never discard the good ones.
                    with transaction.atomic():
                        category = None
                        if rec['category_name']:
                            category, _ = Category.objects.get_or_create(pharmacy=pharmacy, name=rec['category_name'])

                        # IMPORTANT: include inactive products. Re-uploading a previously
                        # soft-deleted product reactivates its original row instead of
                        # attempting a duplicate Product.objects.create().
                        product = None
                        if rec['sku']:
                            product = Product.objects.select_for_update().filter(
                                pharmacy=pharmacy, sku__iexact=rec['sku']
                            ).first()
                            if product and product.name.casefold() != rec['name'].casefold():
                                raise ValueError(f"SKU '{rec['sku']}' belongs to '{product.name}', not '{rec['name']}'.")
                        if product is None:
                            product = Product.objects.select_for_update().filter(
                                pharmacy=pharmacy, name__iexact=rec['name']
                            ).first()
                        is_new_product = product is None
                        if mode == 'RESTOCK' and is_new_product:
                            raise ValueError(f"Restock row {rec['row']} does not match an existing product. Use a Restock Template generated by this branch.")

                        if is_new_product:
                            product = Product.objects.create(
                                pharmacy=pharmacy, name=rec['name'], sku=rec['sku'] or None, category=category,
                                unit=rec['unit'], barcode=rec['barcode'],
                                reorder_level=rec['reorder_level'], created_by=user,
                                requires_expiry=(
                                    rec['requires_expiry_override']
                                    if rec['requires_expiry_override'] is not None else True
                                ),
                            )
                            flag_if_similar_product_exists(product, user)
                            result['new_products'] += 1
                        else:
                            changed = False
                            if not product.is_active:
                                product.is_active = True
                                changed = True
                            if rec['requires_expiry_override'] is not None and product.requires_expiry != rec['requires_expiry_override']:
                                product.requires_expiry = rec['requires_expiry_override']
                                changed = True
                            if category and product.category_id != category.id:
                                product.category = category
                                changed = True
                            if rec['sku'] and product.sku != rec['sku']:
                                other = Product.objects.filter(pharmacy=pharmacy, sku=rec['sku']).exclude(pk=product.pk).exists()
                                if other:
                                    raise ValueError(f"SKU '{rec['sku']}' is already assigned to another product at this branch.")
                                product.sku = rec['sku']
                                changed = True
                            if rec['barcode'] and product.barcode != rec['barcode']:
                                product.barcode = rec['barcode']
                                changed = True
                            if product.unit != rec['unit']:
                                # Do not silently change a live catalog unit if stock already exists.
                                if product.total_quantity > 0 and product.unit != rec['unit']:
                                    raise ValueError(
                                        f"Product '{product.name}' already uses base unit {product.unit}; "
                                        f"Excel says {rec['unit']}. Edit the existing product instead."
                                    )
                                product.unit = rec['unit']
                                changed = True
                            if product.reorder_level != rec['reorder_level']:
                                product.reorder_level = rec['reorder_level']
                                changed = True
                            if changed:
                                product.save()

                        existing_batch = None
                        reused_opening = False
                        if mode == 'OPENING' and rec['batch_number']:
                            candidate = ProductBatch.objects.select_for_update().filter(
                                product=product, batch_number__iexact=rec['batch_number'],
                                expiry_date=rec['expiry_date'], purchase_price=rec['base_cost'],
                                selling_price=rec['selling_price'], purchase_unit=rec['purchase_unit'],
                                units_per_purchase_unit=rec['conversion'],
                            ).first()
                            if candidate:
                                historical_changes = candidate.movements.exclude(
                                    movement_type=StockMovement.MovementType.BULK_UPLOAD
                                ).exclude(movement_type=StockMovement.MovementType.NEW_BATCH).exists()
                                if not historical_changes:
                                    reused_opening = True
                                    result['reused_batches'] += 1
                        if mode == 'RESTOCK' and rec['batch_number']:
                            # Batch number alone is NOT an identity. Match the complete
                            # lot attributes so unrelated uploads cannot change another lot.
                            existing_batch = ProductBatch.objects.select_for_update().filter(
                                product=product,
                                batch_number=rec['batch_number'],
                                quantity__gt=0,
                                expiry_date=rec['expiry_date'],
                                purchase_price=rec['base_cost'],
                                selling_price=rec['selling_price'],
                                purchase_unit=rec['purchase_unit'],
                                units_per_purchase_unit=rec['conversion'],
                            ).first()

                        if existing_batch:
                            existing_batch.quantity += rec['base_quantity']
                            existing_batch.initial_quantity += rec['base_quantity']
                            existing_batch.purchase_quantity += rec['purchase_qty']
                            existing_batch.purchase_unit_price = rec['purchase_unit_price']
                            existing_batch.save(update_fields=[
                                'quantity', 'initial_quantity', 'purchase_quantity',
                                'purchase_unit_price'
                            ])
                            StockMovement.objects.create(
                                product=product, batch=existing_batch,
                                movement_type=StockMovement.MovementType.BULK_UPLOAD,
                                quantity_change=rec['base_quantity'],
                                note=f"{reference} row {rec['row']} — exact batch restock",
                                performed_by=user,
                            )
                            result['topped_up'] += 1
                            suspicious_messages.extend(msg for _, msg in check_batch_for_issues(existing_batch))
                        elif reused_opening:
                            # Idempotent opening import: an unchanged historical opening batch
                            # is reused rather than duplicating stock or quantity.
                            product.is_active = True
                            product.save(update_fields=['is_active'])
                        else:
                            batch = ProductBatch.objects.create(
                                product=product, stock_import=stock_import,
                                batch_number=rec['batch_number'],
                                purchase_unit=rec['purchase_unit'],
                                purchase_quantity=rec['purchase_qty'],
                                units_per_purchase_unit=rec['conversion'],
                                purchase_unit_price=rec['purchase_unit_price'],
                                initial_quantity=rec['base_quantity'],
                                quantity=rec['base_quantity'],
                                purchase_price=rec['base_cost'],
                                selling_price=rec['selling_price'],
                                expiry_date=rec['expiry_date'],
                                received_by=user,
                                note=f"{reference} row {rec['row']} ({mode.lower()})",
                            )
                            StockMovement.objects.create(
                                product=product, batch=batch,
                                movement_type=StockMovement.MovementType.BULK_UPLOAD,
                                quantity_change=rec['base_quantity'],
                                note=f"{reference} row {rec['row']} — new batch",
                                performed_by=user,
                            )
                            result['new_batches'] += 1
                            suspicious_messages.extend(msg for _, msg in check_batch_for_issues(batch))
                except Exception as row_exc:
                    result.update(snapshot)
                    del suspicious_messages[suspicious_len:]
                    result['failed_rows'][rec['row']] = str(row_exc)

            if len(result['failed_rows']) >= len(records):
                first = next(iter(result['failed_rows'].items()))
                raise ValueError(f'no row could be saved (row {first[0]}: {first[1]})')
            result['import_pk'] = stock_import.pk
            stock_import.row_count = len(records) - len(result['failed_rows'])
            stock_import.save(update_fields=['row_count'])

            stock_import.new_products = result['new_products']
            stock_import.new_batches = result['new_batches']
            stock_import.topped_up = result['topped_up']
            stock_import.reused_batches = result['reused_batches']
            stock_import.save(update_fields=['new_products', 'new_batches', 'topped_up', 'reused_batches'])

        result['committed'] = True
        result['no_expiry'] = sum(1 for rec in records if rec['expiry_date'] is None)
    except Exception as exc:
        result['errors'].append({'row': '-', 'message': f'Import rolled back: {exc}'})
        result['new_products'] = 0
        result['new_batches'] = 0
        result['topped_up'] = 0
        result['reused_batches'] = 0
        return result

    if suspicious_messages:
        summary = (
            f"Import {reference} imekamilika, lakini vipengele {len(suspicious_messages)} "
            "vinahitaji ukaguzi wako:\n\n" +
            '\n'.join(f'- {m}' for m in suspicious_messages[:10])
        )
        if len(suspicious_messages) > 10:
            summary += f"\n... na vingine {len(suspicious_messages) - 10}."
        notify_many(
            pharmacy.managers.all(),
            title=f'Bulk Upload: {len(suspicious_messages)} kipengele vinahitaji ukaguzi',
            message=summary, category=Notification.Category.STOCK_ALERT,
            level=Notification.Level.WARNING, pharmacy=pharmacy,
        )

    return result


@employee_or_admin_required
def product_barcode_view(request, pk):
    """Professional printable barcode label for one product."""
    from reportlab.graphics.barcode import code128
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    product = get_object_or_404(Product, pk=pk, pharmacy=request.pharmacy, is_active=True)
    value = product.effective_barcode
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="barcode-{product.pk}.pdf"'
    c = canvas.Canvas(response, pagesize=A4)
    width, height = A4
    margin = 16 * mm
    c.setTitle(f'Barcode - {product.name}')
    c.setFillColor(colors.HexColor('#0f766e'))
    c.setFont('Helvetica-Bold', 13)
    c.drawString(margin, height-margin, product.pharmacy.name[:55])
    c.setFillColor(colors.HexColor('#0f172a'))
    c.setFont('Helvetica-Bold', 18)
    c.drawString(margin, height-margin-12*mm, product.name[:60])
    c.setFont('Helvetica', 9)
    c.setFillColor(colors.HexColor('#64748b'))
    c.drawString(margin, height-margin-18*mm, f'{product.get_unit_display()} · SmartPOS')
    bc = code128.Code128(value, barHeight=24*mm, barWidth=0.42*mm)
    bc.drawOn(c, margin, height-75*mm)
    c.setFillColor(colors.HexColor('#0f172a'))
    c.setFont('Helvetica-Bold', 11)
    c.drawCentredString(width/2, height-82*mm, value)
    c.setFont('Helvetica-Oblique', 7.5)
    c.setFillColor(colors.HexColor('#94a3b8'))
    c.drawCentredString(width/2, 12*mm, '© 2026 Mutech Apex Innovations · SmartPOS · All rights reserved.')
    c.showPage(); c.save()
    return response


# ---------------------------------------------------------------------------
# Branch-to-branch stock transfers (V54.25: bulk "transfer cart")
# ---------------------------------------------------------------------------

def _managed_transfer_branches(request):
    """Only branches the signed-in manager/authorised assistant may operate."""
    return request.user.accessible_management_pharmacies().filter(is_active=True).order_by('name')


def _batch_payload(batch, qty=None, error=''):
    """JSON-safe description of one batch for the transfer cart."""
    product = batch.product
    return {
        'id': batch.pk, 'product': product.name, 'batch': batch.batch_number or f'#{batch.pk}',
        'available': batch.quantity, 'unit': product.get_unit_display(),
        'expiry': batch.expiry_date.strftime('%d %b %Y') if batch.expiry_date else '',
        'expired': bool(batch.is_expired), 'qty': qty, 'error': error,
    }


@admin_required
def stock_transfer_list_view(request):
    branches = _managed_transfer_branches(request)
    branch_ids = list(branches.values_list('pk', flat=True))
    transfers = BranchStockTransfer.objects.filter(
        Q(source_pharmacy_id__in=branch_ids) | Q(destination_pharmacy_id__in=branch_ids)
    ).select_related('source_pharmacy', 'destination_pharmacy', 'created_by').prefetch_related('items')
    status = request.GET.get('status', '').strip()
    if status in dict(BranchStockTransfer.Status.choices):
        transfers = transfers.filter(status=status)
    query = request.GET.get('q', '').strip()
    if query:
        transfers = transfers.filter(
            Q(reference__icontains=query) | Q(items__product_name__icontains=query)
            | Q(items__source_batch_number__icontains=query) | Q(source_pharmacy__name__icontains=query)
            | Q(destination_pharmacy__name__icontains=query)
        ).distinct()
    page_obj = Paginator(transfers, 25).get_page(request.GET.get('page'))
    return render(request, 'inventory/stock_transfers.html', {'page_obj': page_obj, 'branches': branches, 'status_filter': status, 'query': query})


@admin_required
def stock_transfer_search_view(request):
    """Live batch search for the transfer cart (JSON). Scoped to a branch the user manages."""
    branches = _managed_transfer_branches(request)
    try:
        source = branches.get(pk=int(request.GET.get('source', '')))
    except (TypeError, ValueError, ObjectDoesNotExist):
        return JsonResponse({'results': [], 'error': 'Invalid source branch.'}, status=400)
    term = request.GET.get('q', '').strip()[:100]
    if len(term) < 1:
        return JsonResponse({'results': [], 'total': 0})
    qs = ProductBatch.objects.filter(product__pharmacy=source, quantity__gt=0).filter(
        Q(product__name__icontains=term) | Q(batch_number__icontains=term)
        | Q(product__barcode__iexact=term) | Q(product__sku__iexact=term)
    ).select_related('product').order_by('product__name', *FEFO_ORDER)
    total = qs.count()
    return JsonResponse({'results': [_batch_payload(b) for b in qs[:25]], 'total': total})


@admin_required
def stock_transfer_scan_view(request):
    """Scan a product barcode/QR in the transfer cart (JSON).

    Returns the product with its sellable batches (FEFO order) in the SOURCE branch and, when a destination is
    given, what the destination currently holds of the same product (matched exactly like the transfer service:
    name + unit). Nothing is changed here; the cart is still validated again on the server at confirm time.
    """
    branches = _managed_transfer_branches(request)
    try:
        source = branches.get(pk=int(request.GET.get('source', '')))
    except (TypeError, ValueError, ObjectDoesNotExist):
        return JsonResponse({'status': 'error', 'message': 'Invalid source branch.'}, status=400)
    code = request.GET.get('code', '').strip()
    if not code or len(code) > 32 or any(ch.isspace() for ch in code) or code.lower().startswith(('http:', 'https:', 'www.')):
        return JsonResponse({'status': 'invalid', 'message': 'This is not a product barcode (link or too long). Scan the barcode on the label.'})
    destination = None
    raw_dest = request.GET.get('destination', '')
    if raw_dest.isdigit():
        destination = branches.exclude(pk=source.pk).filter(pk=int(raw_dest)).first()

    product = _find_product_by_scan(source, code)
    if product is None:
        return JsonResponse({'status': 'not_found', 'message': f'Not found in {source.name}: {code}. Check the source branch or scan again.'})
    if not product.is_active:
        return JsonResponse({'status': 'inactive', 'message': f'{product.name} is deactivated in {source.name}.'})
    batches = list(product.batches.filter(quantity__gt=0).order_by(*FEFO_ORDER))
    for b in batches:
        b.product = product            # avoid one query per batch in _batch_payload
    transferable = sum(b.quantity for b in batches if not b.is_expired)
    if transferable < 1:
        msg = (f'{product.name} has only expired stock in {source.name}. Expired batches cannot be transferred.'
               if batches else f'{product.name} is out of stock in {source.name}.')
        return JsonResponse({'status': 'no_stock', 'message': msg})

    dest = None
    if destination:
        dst = Product.objects.filter(pharmacy=destination, name__iexact=product.name).order_by('pk').first()
        dest = {'id': destination.pk, 'name': destination.name, 'exists': bool(dst),
                'stock': dst.total_quantity if dst else 0,
                'reorder_level': dst.reorder_level if dst else product.reorder_level}
    return JsonResponse({
        'status': 'ok', 'code': code,
        'product': {'id': product.pk, 'name': product.name, 'unit': product.get_unit_display(),
                    'barcode': product.barcode or '', 'reorder_level': product.reorder_level},
        'source': {'id': source.pk, 'name': source.name, 'stock': sum(b.quantity for b in batches), 'transferable': transferable},
        'dest': dest,
        'batches': [_batch_payload(b) for b in batches],
    })


@admin_required
@require_http_methods(['GET', 'POST'])
def stock_transfer_create_view(request):
    branches = _managed_transfer_branches(request)
    branch_ids = list(branches.values_list('pk', flat=True))
    if len(branch_ids) < 2:
        messages.info(request, 'Stock transfer requires at least two active branches under your management.')
        return redirect('inventory:stock_transfer_list')

    selected_source = request.POST.get('source_branch') or request.GET.get('source') or str(getattr(request, 'pharmacy', None).pk if getattr(request, 'pharmacy', None) and request.pharmacy.pk in branch_ids else branch_ids[0])
    try:
        selected_source_id = int(selected_source)
    except (TypeError, ValueError):
        selected_source_id = branch_ids[0]
    if selected_source_id not in branch_ids:
        selected_source_id = branch_ids[0]
    source = branches.get(pk=selected_source_id)
    destinations = branches.exclude(pk=source.pk)

    cart, form_error, selected_destination, note = [], '', '', ''
    idempotency_key = secrets.token_urlsafe(24)

    if request.method == 'POST':
        idempotency_key = request.POST.get('idempotency_key', '')
        selected_destination = request.POST.get('destination_branch', '')
        note = request.POST.get('note', '').strip()[:255]
        line_errors = {}
        try:
            lines = parse_lines(request.POST.getlist('item_batch'), request.POST.getlist('item_qty'))
            destination = destinations.filter(pk=int(selected_destination)).first() if selected_destination.isdigit() else None
            if not destination:
                raise TransferError('Choose a valid destination branch (different from the source) among the branches you manage.')
            result = execute_bulk_transfer(
                user=request.user, source=source, destination=destination, lines=lines, note=note,
                idempotency_key=idempotency_key, allowed_branch_ids=set(branch_ids),
            )
            transfer = result.transfer
            if result.replayed:
                messages.info(request, f'Transfer {transfer.reference} was already completed. It was not repeated.')
            else:
                messages.success(request, f'Transfer {transfer.reference} completed: {transfer.item_count} item(s), {transfer.quantity} unit(s) moved to {destination.name}.')
            return redirect('inventory:stock_transfer_detail', pk=transfer.pk)
        except TransferError as exc:
            form_error, line_errors = exc.message, exc.line_errors
        except Exception:
            error_ref = secrets.token_hex(3).upper()
            logger.exception('Bulk stock transfer failed [ref=%s] user=%s source=%s destination=%s',
                             error_ref, request.user.pk, source.pk, selected_destination)
            form_error = (f'The transfer could not be completed because of an unexpected system error (ref {error_ref}). '
                          'No stock was changed and your cart was kept. Please try again, or contact support and quote this reference.')
        # Rebuild the cart from fresh database values so the manager keeps their work and sees current stock.
        posted = []
        for raw_id, raw_qty in zip(request.POST.getlist('item_batch'), request.POST.getlist('item_qty')):
            if str(raw_id).isdigit():
                posted.append((int(raw_id), raw_qty))
        fresh = {b.pk: b for b in ProductBatch.objects.filter(pk__in=[b for b, _ in posted], product__pharmacy=source).select_related('product')}
        for batch_id, raw_qty in posted:
            batch = fresh.get(batch_id)
            if batch:
                qty = int(raw_qty) if str(raw_qty).lstrip('-').isdigit() else None
                cart.append(_batch_payload(batch, qty, line_errors.get(batch_id, '')))

    return render(request, 'inventory/stock_transfer_form.html', {
        'branches': branches, 'source': source, 'destinations': destinations,
        'cart': cart, 'form_error': form_error, 'selected_destination': selected_destination,
        'note': note, 'idempotency_key': idempotency_key,
    })


def _scoped_transfer(request, pk):
    """A transfer the signed-in user may see: source or destination must be a branch they manage."""
    branch_ids = list(_managed_transfer_branches(request).values_list('pk', flat=True))
    transfer = get_object_or_404(BranchStockTransfer.objects.select_related(
        'source_pharmacy', 'destination_pharmacy', 'created_by'
    ).prefetch_related('items').filter(Q(source_pharmacy_id__in=branch_ids) | Q(destination_pharmacy_id__in=branch_ids)), pk=pk)
    items = list(transfer.items.select_related('destination_batch'))
    return transfer, items, sum((i.line_cost for i in items), 0)


@admin_required
def stock_transfer_detail_view(request, pk):
    transfer, items, total_cost = _scoped_transfer(request, pk)
    return render(request, 'inventory/stock_transfer_detail.html', {
        'transfer': transfer, 'items': items, 'total_cost': total_cost,
    })


@admin_required
def stock_transfer_pdf_view(request, pk):
    """One-click PDF download of the transfer receipt (no browser print dialog / print settings)."""
    from .transfer_pdf import build_transfer_pdf
    transfer, items, total_cost = _scoped_transfer(request, pk)
    try:
        pdf = build_transfer_pdf(transfer, items, total_cost)
    except Exception:
        logger.exception('Transfer PDF failed for transfer=%s user=%s', transfer.pk, request.user.pk)
        messages.error(request, 'The PDF could not be generated. Please try again or contact support.')
        return redirect('inventory:stock_transfer_detail', pk=transfer.pk)
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{transfer.reference}.pdf"'
    response['Cache-Control'] = 'private, no-store'
    return response
