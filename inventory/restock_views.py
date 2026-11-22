"""Restock history: one auditable ledger of every stock receipt at a branch.

Source of truth is ``StockMovement`` (NEW_BATCH = manual / scan restock,
BULK_UPLOAD = Excel import, either a new batch or an exact-batch top-up), so
nothing that changes stock through a restock path can be missing from it.
All timestamps are shown in the branch business timezone (Africa/Dar_es_Salaam).
"""
import io
import re
from datetime import datetime, timedelta
from decimal import Decimal

import openpyxl
from django.core.paginator import Paginator
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from accounts.decorators import admin_required
from .models import StockImport, StockMovement
from .views import _require_pharmacy

_IMPORT_REF = re.compile(r'IMP-\d{8}-\d{6}-[A-Z0-9]+')
_RESTOCK_TYPES = (StockMovement.MovementType.NEW_BATCH, StockMovement.MovementType.BULK_UPLOAD)
_XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
EXPORT_MAX_ROWS = 20000
PAGE_SIZE = 40


def quick_ranges(date_from, date_to):
    """Quick date-range chips: Today / Last 7 days / Last 30 days / This month."""
    today = timezone.localdate()
    spec = [('Today', today, today), ('Last 7 days', today - timedelta(days=6), today),
            ('Last 30 days', today - timedelta(days=29), today), ('This month', today.replace(day=1), today)]
    return [{'label': l, 'from': a.isoformat(), 'to': b.isoformat(), 'active': date_from == a and date_to == b} for l, a, b in spec]


def _parse_date(raw):
    try:
        return datetime.strptime((raw or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _filters(request):
    """Read and normalise filters from the query string."""
    source = request.GET.get('source', '')
    if source not in {'import', 'manual'}:
        source = ''
    imp = None
    imp_raw = (request.GET.get('import') or '').strip()
    if imp_raw.isdigit():
        imp = StockImport.objects.filter(pk=int(imp_raw), pharmacy=request.pharmacy).first()
    return {
        'q': (request.GET.get('q') or '').strip()[:100],
        'date_from': _parse_date(request.GET.get('from')),
        'date_to': _parse_date(request.GET.get('to')),
        'source': source,
        'import': imp,
    }


def _ledger_qs(request, f):
    qs = (StockMovement.objects
          .filter(product__pharmacy=request.pharmacy, movement_type__in=_RESTOCK_TYPES, quantity_change__gt=0)
          .select_related('product', 'product__category', 'batch', 'performed_by'))
    if f['import']:
        qs = qs.filter(note__startswith=f['import'].reference)
    if f['source'] == 'import':
        qs = qs.filter(movement_type=StockMovement.MovementType.BULK_UPLOAD)
    elif f['source'] == 'manual':
        qs = qs.filter(movement_type=StockMovement.MovementType.NEW_BATCH)
    if f['date_from']:
        qs = qs.filter(created_at__date__gte=f['date_from'])
    if f['date_to']:
        qs = qs.filter(created_at__date__lte=f['date_to'])
    if f['q']:
        qs = qs.filter(Q(product__name__icontains=f['q']) | Q(product__sku__icontains=f['q'])
                       | Q(batch__batch_number__icontains=f['q']) | Q(note__icontains=f['q']))
    return qs.order_by('-created_at', '-pk')


def _row(m):
    """Flatten one movement into a display / export row."""
    b = m.batch
    p = m.product
    is_import = m.movement_type == StockMovement.MovementType.BULK_UPLOAD
    ref_match = _IMPORT_REF.search(m.note or '') if is_import else None
    topup = 'exact batch restock' in (m.note or '')
    cost = b.purchase_price if b else None
    return {
        'when': m.created_at,
        'source': 'Excel upload' if is_import else 'Manual / scan',
        'reference': ref_match.group(0) if ref_match else '',
        'action': 'Added to existing batch' if topup else 'New batch',
        'product': p.name, 'sku': p.sku or '', 'category': p.category.name if p.category else '',
        'batch_number': b.batch_number if b else '',
        'qty': m.quantity_change, 'unit': p.get_unit_display(),
        'purchase_unit': (b.purchase_unit if b else '') or '',
        'units_per_pu': b.units_per_purchase_unit if b else None,
        'cost': cost, 'price': b.selling_price if b else None,
        'line_cost': (cost * m.quantity_change) if cost is not None else None,
        'expiry': b.expiry_date if b else None,
        'by': (m.performed_by.get_full_name() or m.performed_by.username) if m.performed_by else '',
    }


def _summary(qs):
    line = ExpressionWrapper(F('quantity_change') * F('batch__purchase_price'),
                             output_field=DecimalField(max_digits=18, decimal_places=2))
    agg = qs.aggregate(units=Sum('quantity_change'), cost=Sum(line))
    return {'events': qs.count(), 'units': agg['units'] or 0, 'cost': agg['cost'] or Decimal('0')}


@admin_required
def restock_history_view(request):
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')
    f = _filters(request)
    qs = _ledger_qs(request, f)
    page_obj = Paginator(qs, PAGE_SIZE).get_page(request.GET.get('page'))
    page_obj.object_list = [_row(m) for m in page_obj.object_list]
    imports = (StockImport.objects.filter(pharmacy=request.pharmacy)
               .select_related('created_by').order_by('-created_at')[:12])
    qd = request.GET.copy()
    qd.pop('page', None)
    return render(request, 'inventory/restock_history.html', {
        'f': f, 'page_obj': page_obj, 'summary': _summary(qs), 'imports': imports,
        'querystring': qd.urlencode(), 'quick': quick_ranges(f['date_from'], f['date_to']),
        'filtered': any([f['q'], f['date_from'], f['date_to'], f['source'], f['import']]),
    })


def _auto_width(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def build_restock_workbook(request, f, rows, summary, title='Restock History'):
    """Professional Excel: real date/time cells, totals, a summary sheet."""
    headers = ['Date', 'Time', 'Source', 'Import reference', 'Action', 'Product', 'SKU', 'Category', 'Batch No.',
               'Qty received', 'Base unit', 'Purchase unit', 'Units per purchase unit', 'Cost / unit (TZS)',
               'Selling price / unit (TZS)', 'Line cost (TZS)', 'Expiry date', 'Received by']
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Restock History'
    ws.append(headers)
    head_fill = PatternFill('solid', fgColor='0F172A')
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = head_fill
        c.alignment = Alignment(vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 32
    for r in rows:
        local = timezone.localtime(r['when'])
        ws.append([
            local.date(), local.time().replace(microsecond=0), r['source'], r['reference'], r['action'],
            r['product'], r['sku'], r['category'], r['batch_number'], r['qty'], r['unit'], r['purchase_unit'],
            r['units_per_pu'], r['cost'], r['price'], r['line_cost'], r['expiry'], r['by'],
        ])
    last = ws.max_row
    for row in ws.iter_rows(min_row=2, max_row=last):
        row[0].number_format = 'dd mmm yyyy'
        row[1].number_format = 'hh:mm:ss'
        for idx in (13, 14, 15):
            row[idx].number_format = '#,##0.00'
        row[9].number_format = '#,##0'
        row[16].number_format = 'dd mmm yyyy'
    if rows:
        total_row = last + 2
        ws.cell(total_row, 1, 'TOTAL')
        ws.cell(total_row, 10, f'=SUM(J2:J{last})').number_format = '#,##0'
        ws.cell(total_row, 16, f'=SUM(P2:P{last})').number_format = '#,##0.00'
        for c in ws[total_row]:
            c.font = Font(bold=True)
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f'A1:{get_column_letter(len(headers))}{last}'
    _auto_width(ws, [13, 10, 14, 24, 22, 32, 14, 16, 18, 13, 11, 14, 14, 16, 18, 16, 13, 20])

    info = wb.create_sheet('Summary')
    now_local = timezone.localtime()
    lines = [
        (title, ''),
        ('Branch', request.pharmacy.name),
        ('Generated', now_local.strftime('%d %b %Y, %H:%M:%S') + ' (East Africa Time)'),
        ('Generated by', request.user.get_full_name() or request.user.username),
        ('', ''),
        ('Import', f"{f['import'].reference} - {f['import'].get_mode_display()}" if f['import'] else 'All'),
        ('Source', {'import': 'Excel uploads', 'manual': 'Manual / scan'}.get(f['source'], 'All')),
        ('Date from', f['date_from'].strftime('%d %b %Y') if f['date_from'] else 'Beginning'),
        ('Date to', f['date_to'].strftime('%d %b %Y') if f['date_to'] else 'Today'),
        ('Search', f['q'] or '-'),
        ('', ''),
        ('Restock entries', summary['events']),
        ('Total units received', summary['units']),
        ('Total purchase cost (TZS)', float(summary['cost'])),
    ]
    for a, b in lines:
        info.append([a, b])
    info['A1'].font = Font(bold=True, size=14)
    for r in range(2, len(lines) + 1):
        info.cell(r, 1).font = Font(bold=True)
    info['B14'].number_format = '#,##0.00'
    info['B13'].number_format = '#,##0'
    _auto_width(info, [28, 46])
    for ws_ in (ws, info):
        ws_.sheet_view.showGridLines = False if ws_ is info else True
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@admin_required
@require_http_methods(['GET'])
def restock_history_export_view(request):
    """Excel download of the (filtered) restock history, or of one import's batches (?import=<id>)."""
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')
    f = _filters(request)
    qs = _ledger_qs(request, f)
    summary = _summary(qs)
    rows = [_row(m) for m in qs[:EXPORT_MAX_ROWS]]
    title = f"Batches added by {f['import'].reference}" if f['import'] else 'Restock History'
    data = build_restock_workbook(request, f, rows, summary, title=title)
    stamp = timezone.localtime().strftime('%Y%m%d_%H%M')
    name = f"batches_{f['import'].reference}.xlsx" if f['import'] else f'restock_history_{stamp}.xlsx'
    response = HttpResponse(data, content_type=_XLSX)
    response['Content-Disposition'] = f'attachment; filename="{name}"'
    return response
