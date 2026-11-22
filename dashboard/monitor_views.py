"""Performance Monitor: manager-customisable sales / profit / transaction view."""
import io

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.decorators import admin_required
from core.pdf_utils import generate_multi_section_pdf_report

from . import perf_core as pc
from .forms import PerformancePreferenceForm
from .models import DashboardPreference
from .performance import build_report, get_preference
from .perf_cash import collect_cash_data, pdf_sections

PER_PAGE_CHOICES = (10, 15, 30, 60)


def _money(value):
    return f'TZS {value:,.0f}'


def _pct(value):
    return '—' if value is None else f'{value:+.0f}%'


def _active_pharmacy(request):
    if getattr(request, 'pharmacy', None) is None:
        messages.warning(request, "You don't have an active branch yet. Create one to get started.")
        return None
    return request.pharmacy


@admin_required
def performance_monitor_view(request):
    pharmacy = _active_pharmacy(request)
    if pharmacy is None:
        return redirect('pharmacies:list')

    saved = get_preference(request.user)

    if request.method == 'POST':
        if 'reset' in request.POST:
            DashboardPreference.objects.filter(user=request.user).delete()
            messages.success(request, 'Dashboard restored to the default: Monthly Sales and Monthly Profit.')
            return redirect('dashboard:performance')
        form = PerformancePreferenceForm(request.POST, instance=saved if saved.pk else DashboardPreference(user=request.user))
        if form.is_valid():
            pref = form.save(commit=False)
            pref.user = request.user
            pref.save()
            messages.success(request, 'Saved. Your dashboard cards and this monitor now use this period.')
            return redirect('dashboard:performance')
        # Invalid: keep showing the report for the last saved settings.
    else:
        form = PerformancePreferenceForm(instance=saved)

    report = build_report(pharmacy, saved)

    try:
        per_page = int(request.GET.get('per_page', 15))
    except (TypeError, ValueError):
        per_page = 15
    if per_page not in PER_PAGE_CHOICES:
        per_page = 15
    # Newest first is the natural order for monitoring.
    page_obj = Paginator(list(reversed(report['buckets'])), per_page).get_page(request.GET.get('page'))

    return render(request, 'dashboard/performance_monitor.html', {
        'form': form, 'pref': saved, 'report': report, 'page_obj': page_obj,
        'per_page': per_page, 'per_page_choices': PER_PAGE_CHOICES,
        'pharmacy': pharmacy, 'is_default': saved.mode == DashboardPreference.Mode.MONTH,
        'today': timezone.localdate(),
    })


def _report_for_export(request):
    pharmacy = _active_pharmacy(request)
    if pharmacy is None:
        return None, None
    return pharmacy, build_report(pharmacy, get_preference(request.user))


@admin_required
def performance_export_pdf(request):
    pharmacy, r = _report_for_export(request)
    if r is None:
        return redirect('pharmacies:list')
    t, prev = r['totals'], r['previous']
    interval_word = {'DAY': 'Day', 'WEEK': 'Week', 'MONTH': 'Month'}[r['interval']]

    summary = [
        ('Branch', pharmacy.name),
        ('Period', f"{r['label']} ({r['start']:%d %b %Y} to {r['end']:%d %b %Y}, {t['days']} days)"),
        ('Total sales', _money(t['sales'])),
        ('Total profit', f"{_money(t['profit'])} ({t['margin']:.1f}% margin)"),
        ('Transactions', f"{t['transactions']:,} (average sale {_money(t['avg_ticket'])})"),
        ('Average per day', f"Sales {_money(t['avg_daily_sales'])} | Profit {_money(t['avg_daily_profit'])}"),
    ]

    sections = [{
        'title': 'Comparison with the previous period',
        'note': f"Previous period: {prev['start']:%d %b %Y} to {prev['end']:%d %b %Y}.",
        'headers': ['Measure', 'This period', 'Previous', 'Change'],
        'rows': [
            ['Sales', _money(t['sales']), _money(prev['sales']), _pct(prev['sales_pct'])],
            ['Profit', _money(t['profit']), _money(prev['profit']), _pct(prev['profit_pct'])],
            ['Transactions', f"{t['transactions']:,}", f"{prev['transactions']:,}", _pct(prev['tx_pct'])],
        ],
        'column_widths': [50, 50, 50, 24],
    }]

    highlights = []
    for title, b in (('Best', r['best']), ('Weakest trading', r['worst']), ('Busiest (most transactions)', r['busiest'])):
        if b:
            highlights.append([title, b['label'], _money(b['sales']), f"{b['tx']:,}"])
    if highlights:
        sections.append({
            'title': f"Highlights by {interval_word.lower()}",
            'headers': ['Highlight', interval_word, 'Sales', 'Transactions'],
            'rows': highlights, 'column_widths': [50, 60, 40, 30],
        })

    sections.append({
        'title': f"{r['interval_label']} breakdown",
        'note': r['interval_note'],
        'headers': [interval_word, 'Sales', 'Profit', 'Margin', 'Txns', 'Avg sale'],
        'rows': [[b['label'], _money(b['sales']), _money(b['profit']), f"{b['margin']:.0f}%", f"{b['tx']:,}", _money(b['avg_ticket'])]
                 for b in r['buckets']],
        'column_widths': [34, 32, 32, 18, 16, 32],
    })

    cash_data = collect_cash_data(pharmacy, r['start'], r['end'])
    cash_sections, cash_summary = pdf_sections(cash_data)
    sections.extend(cash_sections)
    summary.extend(cash_summary)

    return generate_multi_section_pdf_report(
        title='Performance Monitor Report',
        subtitle=f"{pharmacy.name} - Generated {timezone.localtime():%d %B %Y, %H:%M}",
        sections=sections, summary=summary,
        filename=f"performance_{r['start']}_{r['end']}.pdf",
    )


@admin_required
def performance_export_excel(request):
    pharmacy, r = _report_for_export(request)
    if r is None:
        return redirect('pharmacies:list')
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    t, prev = r['totals'], r['previous']
    wb = Workbook()
    head_fill = PatternFill('solid', fgColor='0D9488')
    head_font = Font(bold=True, color='FFFFFF')
    bold = Font(bold=True)

    def text(cell, value):
        # User-controlled text is always written as text, never as a formula.
        cell.value = str(value)
        cell.data_type = 's'

    def header(ws, row, titles):
        for col, h in enumerate(titles, start=1):
            c = ws.cell(row=row, column=col, value=h)
            c.fill, c.font = head_fill, head_font
            c.alignment = Alignment(horizontal='center')

    def money(cell, value):
        cell.value = float(value)
        cell.number_format = '#,##0'

    # ── Summary ──
    ws = wb.active
    ws.title = 'Summary'
    text(ws['A1'], 'Performance Monitor Report')
    ws['A1'].font = Font(bold=True, size=14)
    text(ws['A2'], f"{pharmacy.name} — {r['label']} ({r['start']:%d %b %Y} to {r['end']:%d %b %Y})")
    rows = [
        ('Total sales (TZS)', t['sales'], prev['sales'], prev['sales_pct']),
        ('Total profit (TZS)', t['profit'], prev['profit'], prev['profit_pct']),
        ('Transactions', t['transactions'], prev['transactions'], prev['tx_pct']),
    ]
    header(ws, 4, ['Measure', 'This period', 'Previous period', 'Change %'])
    for i, (label, now, before, pct) in enumerate(rows, start=5):
        text(ws.cell(row=i, column=1), label); ws.cell(row=i, column=1).font = bold
        for col, v in ((2, now), (3, before)):
            c = ws.cell(row=i, column=col, value=float(v)); c.number_format = '#,##0'
        c = ws.cell(row=i, column=4, value=None if pct is None else round(pct, 1)); c.number_format = '0.0'
    extra = [('Profit margin %', round(t['margin'], 1)), ('Average sale (TZS)', float(t['avg_ticket'])),
             ('Average sales per day (TZS)', float(t['avg_daily_sales'])), ('Average profit per day (TZS)', float(t['avg_daily_profit'])),
             ('Days with sales', t['active_days']), ('Days in period', t['days'])]
    for i, (label, v) in enumerate(extra, start=9):
        text(ws.cell(row=i, column=1), label); ws.cell(row=i, column=1).font = bold
        c = ws.cell(row=i, column=2, value=v); c.number_format = '#,##0.0' if isinstance(v, float) else '#,##0'
    for idx, w in enumerate([32, 18, 18, 12], start=1):
        ws.column_dimensions[get_column_letter(idx)].width = w

    # ── Breakdown + daily sheets ──
    def sheet(title, items, first_title):
        s = wb.create_sheet(title)
        header(s, 1, [first_title, 'From', 'To', 'Sales (TZS)', 'Cost (TZS)', 'Profit (TZS)', 'Margin %', 'Transactions', 'Units', 'Avg sale (TZS)'])
        for i, b in enumerate(items, start=2):
            text(s.cell(row=i, column=1), b['label'])
            for col, d in ((2, b['start']), (3, b['end'])):
                c = s.cell(row=i, column=col, value=d); c.number_format = 'DD MMM YYYY'
            money(s.cell(row=i, column=4), b['sales']); money(s.cell(row=i, column=5), b['cost']); money(s.cell(row=i, column=6), b['profit'])
            c = s.cell(row=i, column=7, value=round(b['margin'], 1)); c.number_format = '0.0'
            s.cell(row=i, column=8, value=b['tx']); s.cell(row=i, column=9, value=b['units'])
            money(s.cell(row=i, column=10), b['avg_ticket'])
        if items:
            s.freeze_panes = 'A2'
        for idx, w in enumerate([22, 14, 14, 16, 16, 16, 10, 14, 10, 16], start=1):
            s.column_dimensions[get_column_letter(idx)].width = w

    sheet(f"By {r['interval'].lower()}", r['buckets'], r['interval_label'])
    if r['interval'] != pc.INTERVAL_DAY:
        sheet('Daily detail', r['days'], 'Day')

    # ── Cash: meals, expenses, expected cash ──
    cd = collect_cash_data(pharmacy, r['start'], r['end'])
    cash = cd['cash']
    cs = wb.create_sheet('Expected cash')
    header(cs, 1, ['Day', 'Opening', 'Cash sales', 'Meals deducted', 'Expenses deducted', 'Refunds', 'Expected cash', 'Counted'])
    for i, row in enumerate(cd['cash_rows'], start=2):
        c = cs.cell(row=i, column=1, value=row['date']); c.number_format = 'DD MMM YYYY'
        for col, key in ((2, 'opening'), (3, 'sales'), (4, 'meals'), (5, 'expenses'), (6, 'refunds'), (7, 'expected')):
            money(cs.cell(row=i, column=col), row[key])
        if row['actual'] is not None:
            money(cs.cell(row=i, column=8), row['actual'])
        else:
            text(cs.cell(row=i, column=8), 'Not counted')
    cs.freeze_panes = 'A2'
    for idx, w in enumerate([16, 14, 14, 16, 18, 12, 16, 14], start=1):
        cs.column_dimensions[get_column_letter(idx)].width = w

    ms = wb.create_sheet('Meals')
    header(ms, 1, ['Date', 'Employee', 'Meal', 'Amount (TZS)', 'Treatment'])
    for i, m in enumerate(cd['meals'], start=2):
        c = ms.cell(row=i, column=1, value=m['date']); c.number_format = 'DD MMM YYYY'
        text(ms.cell(row=i, column=2), m['employee']); text(ms.cell(row=i, column=3), m['meal'])
        money(ms.cell(row=i, column=4), m['amount']); text(ms.cell(row=i, column=5), m['treatment'])
    for idx, w in enumerate([14, 28, 16, 14, 32], start=1):
        ms.column_dimensions[get_column_letter(idx)].width = w

    es = wb.create_sheet('Expenses')
    header(es, 1, ['Dated', 'Deducted on', 'Category', 'Description', 'Amount (TZS)', 'Paid from', 'Status'])
    rows_e = [(e, 'Approved') for e in cd['expenses']] + [(dict(p, cash_day=None), 'Pending - not deducted') for p in cd['pending']]
    for i, (e, status) in enumerate(rows_e, start=2):
        c = es.cell(row=i, column=1, value=e['date']); c.number_format = 'DD MMM YYYY'
        if e['cash_day']:
            c = es.cell(row=i, column=2, value=e['cash_day']); c.number_format = 'DD MMM YYYY'
        text(es.cell(row=i, column=3), e['category']); text(es.cell(row=i, column=4), e['title'])
        money(es.cell(row=i, column=5), e['amount'])
        text(es.cell(row=i, column=6), 'Drawer' if e['drawer'] else e['paid_from']); text(es.cell(row=i, column=7), status)
    for idx, w in enumerate([14, 14, 22, 36, 14, 20, 24], start=1):
        es.column_dimensions[get_column_letter(idx)].width = w

    base = 16
    for i, (label, v) in enumerate([('Opening cash (TZS)', cash['opening']), ('Cash sales (TZS)', cash['cash_sales']),
                                    ('Meals deducted (TZS)', cash['meals']), ('Expenses deducted from drawer (TZS)', cash['expenses']),
                                    ('Cash refunds (TZS)', cash['refunds']), ('EXPECTED CASH (TZS)', cash['expected'])], start=base):
        text(ws.cell(row=i, column=1), label); ws.cell(row=i, column=1).font = bold
        money(ws.cell(row=i, column=2), v)

    buffer = io.BytesIO()
    wb.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="performance_{r["start"]}_{r["end"]}.xlsx"'
    return response
