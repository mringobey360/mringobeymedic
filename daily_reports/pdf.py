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
    profit = Decimal(s['estimated_net_profit'] or 0)
    verdict = ('LOSS of ' + _m(abs(profit))) if profit < 0 else ('PROFIT of ' + _m(profit))
    summary = [
        ('Report date', report.report_date.strftime('%d %B %Y')),
        ('Result of the day', verdict),
        ('Sales', sales_line),
        ('Expected cash after costs', _m(s['cash_after_costs'])),
        ('Customers owe you', _m(s['debt_outstanding'])),
    ]
    sections = []
    # ---- Page 1: only what the boss needs ----
    insights = (snap.get('insights') or [])
    label = {'alert': 'ALERT', 'warn': 'Watch', 'good': 'Good', 'info': 'Info'}
    if insights:
        sections.append({'title': 'What needs your attention', 'headers': ['Level', 'Insight'],
                         'rows': [[label.get(i.get('level'), 'Info'), f"{i['title']}: {i['text']}"] for i in insights[:4]],
                         'column_widths': [22, 152]})
    other_costs = Decimal(s['expenses'] or 0) + Decimal(s['meals_cost'] or 0)
    rows = [['Sales', _m(s['net_sales'])], ['- Cost of goods', _m(s['cogs'])], ['- Expenses & employee meals', _m(other_costs)]]
    if inc_sal:
        rows.append(['- Salary share (daily)', _m(s['salary_accrual'])])
    rows.append(['PROFIT', _m(s['estimated_net_profit'])])
    sections.append({'title': 'How the profit was made', 'headers': ['Item', 'Amount'], 'rows': rows, 'column_widths': [100, 74]})
    sections.append({'title': 'Where the money is', 'headers': ['Item', 'Amount'], 'rows': [
        ['Cash sales', _m(s['cash_sales'])], ['Mobile money sales', _m(s['mobile_sales'])],
        ['+ Debts & deposits collected', _m(Decimal(s['debt_collected'] or 0) + Decimal(s['deposits_received'] or 0))],
        ['EXPECTED CASH AFTER COSTS', _m(s['cash_after_costs'])],
        ['Lent on credit today (not yet cash)', _m(s['credit_sales'])]], 'column_widths': [100, 74]})
    if s.get('top_debtors'):
        sections.append({'title': 'Who owes you most', 'headers': ['Customer', 'Balance'],
                         'rows': [[d['name'], _m(d['balance'])] for d in s['top_debtors'][:3]], 'column_widths': [100, 74]})
    analysis = snap.get('branch_analysis') or {}
    if analysis.get('rows'):
        sections.append({'title': 'Branch analysis', 'note': (analysis.get('headline') or '') + '.',
                         'headers': ['#', 'Branch', 'Sales', 'Share', 'Profit', 'Margin', 'vs prev. day', 'Owed'],
                         'rows': [[r['rank'], r['branch_name'], _m(r['net_sales']), f"{r['sales_share']:.0f}%", _m(r['profit']),
                                   f"{r['margin']:.0f}%" if r['margin'] is not None else '-',
                                   f"{r['vs_yesterday_pct']:+.0f}%" if r['vs_yesterday_pct'] is not None else '-', _m(r['debt_outstanding'])]
                                  for r in analysis['rows']],
                         'column_widths': [8, 30, 28, 14, 28, 16, 20, 28]})
        flagged = [[r['branch_name'], '; '.join(f['text'] for f in r['flags'])] for r in analysis['rows'] if r['flags']]
        if flagged:
            sections.append({'title': 'Branches that need attention', 'headers': ['Branch', 'Why'], 'rows': flagged, 'column_widths': [45, 129]})
    elif len(snap['branches']) > 1:
        sections.append({'title': 'Branches', 'headers': ['Branch', 'Sales', 'Profit', 'Owed'],
                         'rows': [[b['branch_name'], _m(b['net_sales']), _m(b['estimated_net_profit']), _m(b['debt_outstanding'])] for b in snap['branches']],
                         'column_widths': [60, 40, 40, 34]})
    # ---- Details, for checking the numbers ----
    if insights[4:]:
        sections.append({'title': 'More points', 'headers': ['Level', 'Insight'],
                         'rows': [[label.get(i.get('level'), 'Info'), f"{i['title']}: {i['text']}"] for i in insights[4:]], 'column_widths': [22, 152]})
    sections.append({'title': 'DETAILS - full profit breakdown', 'headers': ['Item', 'Amount'], 'rows': _breakdown_rows(s, inc_sal), 'column_widths': [100, 74]})
    sections.append({'title': 'DETAILS - full cash breakdown', 'headers': ['Item', 'Amount'], 'rows': _cash_rows(s), 'column_widths': [100, 74]})
    sections.append({'title': 'DETAILS - debts', 'headers': ['Item', 'Amount'], 'rows': _debt_rows(s), 'column_widths': [100, 74]})
    if any(Decimal(s.get(k, '0') or 0) for k in ('layaway_cash_received','layaway_mobile_received','layaway_bank_received','layaway_other_received','layaway_cash_refunds','layaway_mobile_refunds')) or s.get('layaway_created_count') or s.get('layaway_handover_count'):
        sections.append({'title': 'DETAILS - layaway (not additional sales revenue)', 'headers': ['Movement', 'Amount / count'],
                         'rows': [
                             ['Cash instalments received', _m(s.get('layaway_cash_received'))],
                             ['Mobile-money instalments received', _m(s.get('layaway_mobile_received'))],
                             ['Bank transfers received', _m(s.get('layaway_bank_received'))],
                             ['Other-method payments received', _m(s.get('layaway_other_received'))],
                             ['Cash refunds paid', _m(s.get('layaway_cash_refunds'))],
                             ['Mobile-money refunds paid', _m(s.get('layaway_mobile_refunds'))],
                             ['Plans created', s.get('layaway_created_count', 0)],
                             ['Plans handed over (fulfilment only)', s.get('layaway_handover_count', 0)],
                         ], 'column_widths': [120, 54]})
    if s.get('timeline'):
        sections.append({'title': 'DETAILS - sales through the day', 'note': f"First sale {s['first_sale']}, last sale {s['last_sale']}. Average sale {_m(s['avg_sale'])}, largest {_m(s['largest_sale'])}.",
                         'headers': ['Hour', 'Sales', 'Count', 'Share'],
                         'rows': [[t['label'], _m(t['sales']), t['count'], f"{t['share']:.0f}%"] for t in s['timeline']], 'column_widths': [55, 55, 30, 34]})
    if s.get('categories'):
        sections.append({'title': 'DETAILS - sales by category', 'headers': ['Category', 'Sales', 'Share'],
                         'rows': [[c['name'], _m(c['sales']), f"{c['share']:.0f}%"] for c in s['categories']], 'column_widths': [80, 60, 34]})
    if s.get('top_products'):
        sections.append({'title': 'DETAILS - top products', 'headers': ['Product', 'Qty', 'Sales', 'Profit'],
                         'rows': [[p['name'], p['qty'], _m(p['sales']), _m(p['profit'])] for p in s['top_products'][:5]], 'column_widths': [70, 25, 45, 40]})
    if s.get('top_staff'):
        sections.append({'title': 'DETAILS - top staff', 'headers': ['Staff', 'Branch', 'Sales'],
                         'rows': [[p['name'], p['branch'], _m(p['sales'])] for p in s['top_staff'][:5]], 'column_widths': [70, 54, 50]})
    if s.get('expense_categories'):
        sections.append({'title': 'DETAILS - where expenses went', 'headers': ['Category', 'Amount'], 'rows': [[c['name'], _m(c['amount'])] for c in s['expense_categories']], 'column_widths': [100, 74]})
    sections.append({'title': 'DETAILS - payment methods', 'headers': ['Payment method', 'Amount'],
                     'rows': [[{'DEBT': 'Credit (unpaid part)', 'CREDIT_PAID_AT_SALE': 'Credit sales: paid at the till'}.get(k, k.replace('_', ' ').title()), _m(v)] for k, v in s['payment_methods'].items()], 'column_widths': [100, 74]})
    if len(snap['branches']) > 1:
        for b in snap['branches']:
            sections.append({'title': f"DETAILS - Branch: {b['branch_name']}", 'note': b.get('location') or '', 'headers': ['Item', 'Amount'],
                             'rows': _breakdown_rows(b, inc_sal) + [['Expected cash after costs', _m(b['cash_after_costs'])], ['Debts outstanding', _m(b['debt_outstanding'])],
                                                                   ['Transactions', b['transactions']], ['Units sold', b['units_sold']]],
                             'column_widths': [100, 74]})
    return generate_multi_section_pdf_report(
        title='Daily Sales Intelligence Report',
        subtitle=f"{report.owner.get_full_name() or report.owner.username} - Generated {timezone.localtime(report.generated_at):%d %B %Y, %H:%M}",
        sections=sections, summary=summary,
        filename=f'daily_sales_intelligence_{report.report_date}_{report.owner_id}.pdf',
    )
