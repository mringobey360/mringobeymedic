from decimal import Decimal
from django.utils import timezone
from core.pdf_utils import generate_multi_section_pdf_report


def _m(x):
    return f'TZS {Decimal(x or 0):,.0f}'


def _breakdown_rows(d, include_salary):
    rows = [
        ['Total sold', _m(d['gross_sales'])],
        ['- Returns & voids', _m(d['returns_total'])],
        ['Net sales', _m(d['net_sales'])],
        ['- Cost of goods', _m(d['cogs'])],
        ['Gross profit', _m(d['gross_profit'])],
        ['- Expenses (approved)', _m(d['expenses'])],
        ['- Employee meals', _m(d['meals_cost'])],
    ]
    if include_salary:
        rows.append(['- Salary share (daily)', _m(d['salary_accrual'])])
    rows.append(['NET PROFIT', _m(d['estimated_net_profit'])])
    return rows


def _cash_rows(d):
    rows = [
        ['Cash sales', _m(d['cash_sales'])],
        ['Mobile money sales', _m(d['mobile_sales'])],
        ['+ Paid at the till on credit sales', _m(d['deposits_received'])],
        ['+ Old debts collected today', _m(d['debt_collected'])],
        ['- Expenses paid from sales cash', _m(d['cash_expenses'])],
        ['- Employee meals', _m(d['meals_cost'])],
    ]
    if 'refunds_paid_today' in d:
        if Decimal(d.get('returns_cash_in') or 0):
            rows.append(['+ Taken today on sales later returned or voided', _m(d['returns_cash_in'])])
        rows.append(['- Refunds paid out today', _m(d['refunds_paid_today'])])
    else:   # snapshot saved before V86
        rows.append(['- Refunds for earlier days', _m(d.get('refunds_prior_days'))])
    rows.append(['EXPECTED CASH AFTER COSTS', _m(d['cash_after_costs'])])
    rows.append(['Lent on credit today (not yet cash)', _m(d['credit_sales'])])
    return rows


def _debt_rows(d):
    return [
        ['New debts today (sale value on credit)', _m(d['debt_created'])],
        ['  of which paid at the till', _m(d['credit_deposits'])],
        ['Old debts collected today', _m(d['debt_collected'])],
        ['Still outstanding', _m(d['debt_outstanding'])],
    ]


def generate_daily_sales_pdf(report):
    snap = report.snapshot
    s = snap['summary']
    inc_sal = snap.get('include_salary', False)
    cmp_y = snap.get('vs_yesterday')
    sales_line = _m(s['net_sales'])
    if cmp_y and cmp_y.get('net_sales_pct') is not None:
        sales_line += f" ({cmp_y['net_sales_pct']:+.0f}% vs previous day)"
    summary = [
        ('Report date', report.report_date.strftime('%d %B %Y')),
        ('Branches included', snap['branch_count']),
        ('Net sales', sales_line),
        ('Net profit', _m(s['estimated_net_profit'])),
        ('Expected cash after costs', _m(s['cash_after_costs'])),
        ('Customers owe (outstanding)', _m(s['debt_outstanding'])),
        ('Transactions / units', f"{s['transactions']} / {s['units_sold']}"),
    ]
    sections = []
    insights = snap.get('insights') or []
    if insights:
        label = {'alert': 'ALERT', 'warn': 'Watch', 'good': 'Good', 'info': 'Info'}
        sections.append({'title': 'What this means', 'headers': ['Level', 'Insight'],
                         'rows': [[label.get(i.get('level'), 'Info'), f"{i['title']}: {i['text']}"] for i in insights],
                         'column_widths': [22, 152]})
    if any(Decimal(s.get(k, '0') or 0) for k in ('layaway_cash_received','layaway_mobile_received','layaway_bank_received','layaway_other_received','layaway_cash_refunds','layaway_mobile_refunds')) or s.get('layaway_created_count') or s.get('layaway_handover_count'):
        sections.append({'title': 'Layaway reconciliation (not additional sales revenue)',
                         'headers': ['Movement', 'Amount / count'],
                         'rows': [
                             ['Cash instalments received (drawer inflow)', _m(s.get('layaway_cash_received'))],
                             ['Mobile-money instalments received', _m(s.get('layaway_mobile_received'))],
                             ['Bank transfers received', _m(s.get('layaway_bank_received'))],
                             ['Other-method payments received', _m(s.get('layaway_other_received'))],
                             ['Cash refunds paid', _m(s.get('layaway_cash_refunds'))],
                             ['Mobile-money refunds paid', _m(s.get('layaway_mobile_refunds'))],
                             ['Plans created', s.get('layaway_created_count', 0)],
                             ['Plans handed over (fulfilment; not a new receipt)', s.get('layaway_handover_count', 0)],
                         ], 'column_widths': [120, 54]})
    if s.get('timeline'):
        sections.append({'title': 'Sales through the day', 'note': f"First sale {s['first_sale']}, last sale {s['last_sale']}. Average sale {_m(s['avg_sale'])}, largest {_m(s['largest_sale'])}.",
                         'headers': ['Hour', 'Sales', 'Count', 'Share'],
                         'rows': [[t['label'], _m(t['sales']), t['count'], f"{t['share']:.0f}%"] for t in s['timeline']], 'column_widths': [55, 55, 30, 34]})
    if s.get('categories'):
        sections.append({'title': 'Sales by category', 'headers': ['Category', 'Sales', 'Share'],
                         'rows': [[c['name'], _m(c['sales']), f"{c['share']:.0f}%"] for c in s['categories']], 'column_widths': [80, 60, 34]})
    sections.append({'title': 'How the profit was made', 'headers': ['Item', 'Amount'], 'rows': _breakdown_rows(s, inc_sal), 'column_widths': [100, 74]})
    sections.append({'title': 'Cash position', 'headers': ['Item', 'Amount'], 'rows': _cash_rows(s), 'column_widths': [100, 74]})
    sections.append({'title': 'Debts', 'headers': ['Item', 'Amount'], 'rows': _debt_rows(s), 'column_widths': [100, 74]})
    if s.get('top_debtors'):
        sections.append({'title': 'Largest debtors', 'headers': ['Customer', 'Balance'], 'rows': [[d['name'], _m(d['balance'])] for d in s['top_debtors']], 'column_widths': [100, 74]})
    if s.get('expense_categories'):
        sections.append({'title': 'Where expenses went', 'headers': ['Category', 'Amount'], 'rows': [[c['name'], _m(c['amount'])] for c in s['expense_categories']], 'column_widths': [100, 74]})
    sections.append({'title': 'Consolidated payment methods', 'headers': ['Payment method', 'Amount'],
                     'rows': [[{'DEBT': 'Credit (unpaid part)', 'CREDIT_PAID_AT_SALE': 'Credit sales: paid at the till'}.get(k, k.replace('_', ' ').title()), _m(v)] for k, v in s['payment_methods'].items()], 'column_widths': [100, 74]})
    for b in snap['branches']:
        sections.append({'title': f"Branch: {b['branch_name']}", 'note': b.get('location') or '', 'headers': ['Item', 'Amount'],
                         'rows': _breakdown_rows(b, inc_sal) + [['Expected cash after costs', _m(b['cash_after_costs'])], ['Debts outstanding', _m(b['debt_outstanding'])],
                                                               ['Transactions', b['transactions']], ['Units sold', b['units_sold']]],
                         'column_widths': [100, 74]})
        sections.append({'title': f"{b['branch_name']} - Top products", 'headers': ['Product', 'Qty', 'Sales', 'Profit'],
                         'rows': [[p['name'], p['qty'], _m(p['sales']), _m(p['profit'])] for p in b['top_products']], 'column_widths': [70, 25, 45, 40]})
        sections.append({'title': f"{b['branch_name']} - Staff performance", 'headers': ['Staff', 'Sales'],
                         'rows': [[p['name'], _m(p['sales'])] for p in b['staff_performance']], 'column_widths': [100, 74]})
    return generate_multi_section_pdf_report(
        title='Daily Sales Intelligence Report',
        subtitle=f"{report.owner.get_full_name() or report.owner.username} - Generated {timezone.localtime(report.generated_at):%d %B %Y, %H:%M}",
        sections=sections, summary=summary,
        filename=f'daily_sales_intelligence_{report.report_date}_{report.owner_id}.pdf',
    )
