"""Sales / profit / meals / expenses / expected-cash report for any period (V54.9).

One data builder (`collect_report_data`) feeds BOTH exports (PDF and Excel) and the
on-screen tables, so the three can never disagree. The headline expected cash comes
from `period_cash_control`, the same function the Report Builder screen uses.
"""
from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.db.models import DecimalField, ExpressionWrapper, F, Min, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse

from accounts.models import MealLedger
from core.pdf_utils import generate_multi_section_pdf_report
from expenses.ledger import (
    STAFF_MEALS_CATEGORY, CashLedgerService, cash_debt_payments_qs, cash_expenses_qs, cash_refunds_qs,
    cash_sales_qs,
)
from expenses.models import CashDay, Expense
from sales.models import Sale, SaleItem

from .mobile_money import mobile_period_account, mobile_statement_check
from .reconciliation import refund_rows, sales_reconciliation

ZERO = Decimal('0')
MONEY = DecimalField(max_digits=16, decimal_places=2)


def _n(v):
    return f'{(v or ZERO):,.0f}'


# --------------------------------------------------------------------------
# Cash control for a period (single source of truth for screen + exports)
# --------------------------------------------------------------------------

def period_cash_control(pharmacy, start, end):
    """Opening + cash sales + cash debt repayments - refunds - cash expenses - staff meals = expected cash.

    Opening: the manager-entered CashDay opening on `start`; otherwise the latest
    physical close before the period carried forward through the ledger; otherwise a
    provisional estimate rebuilt from earlier recorded cash movements (flagged)."""
    cash_days = list(CashDay.objects.filter(pharmacy=pharmacy, date__gte=start, date__lte=end).order_by('date'))
    cash_day_by_date = {d.date: d for d in cash_days}
    ending_day = cash_day_by_date.get(end)
    actual_cash = (ending_day.actual_closing_cash
                   if ending_day and ending_day.actual_closing_cash is not None else None)

    earliest = [
        cash_sales_qs(pharmacy.pk).aggregate(d=Min('business_date'))['d'],
        cash_refunds_qs(pharmacy.pk).aggregate(d=Min('decided_at__date'))['d'],
        cash_debt_payments_qs(pharmacy.pk).aggregate(d=Min('paid_at__date'))['d'],
        cash_expenses_qs(pharmacy.pk).aggregate(d=Min('expense_date'))['d'],
    ]
    earliest = [d for d in earliest if d is not None]
    earliest_cash_activity = min(earliest) if earliest else start

    opening_day = cash_day_by_date.get(start)
    baseline_source = 'verified_period_opening' if opening_day else ''
    provisional = False
    if opening_day:
        opening = opening_day.opening_cash
    else:
        prior = (CashDay.objects.filter(pharmacy=pharmacy, date__lt=start, actual_closing_cash__isnull=False)
                 .order_by('-date').first())
        if prior:
            opening = prior.actual_closing_cash
            baseline_source = 'previous_physical_close'
            carry_start = prior.date + timedelta(days=1)
            if carry_start < start:
                for t in CashLedgerService.daily_totals(pharmacy.pk, carry_start, start - timedelta(days=1)).values():
                    opening += t.net
        else:
            provisional = True
            baseline_source = 'historical_activity_estimate'
            opening = ZERO
            history_end = start - timedelta(days=1)
            if earliest_cash_activity <= history_end:
                for t in CashLedgerService.daily_totals(pharmacy.pk, earliest_cash_activity, history_end).values():
                    opening += t.net

    activity = CashLedgerService.daily_totals(pharmacy.pk, start, end)
    cash_sales = sum((t.cash_sales for t in activity.values()), ZERO)
    debt_cash = sum((t.cash_debt_collected for t in activity.values()), ZERO)
    refunds = sum((t.cash_refunds for t in activity.values()), ZERO)
    expenses = sum((t.cash_expenses for t in activity.values()), ZERO)
    meals = sum((t.cash_meals for t in activity.values()), ZERO)
    transfer_in = sum((t.transfer_in for t in activity.values()), ZERO)
    transfer_out = sum((t.transfer_out for t in activity.values()), ZERO)
    expected = opening + cash_sales + debt_cash + transfer_in - refunds - expenses - meals - transfer_out
    return {
        'opening': opening, 'cash_sales': cash_sales, 'debt_cash': debt_cash, 'refunds': refunds,
        'transfer_in': transfer_in, 'transfer_out': transfer_out,
        'expenses': expenses, 'meals': meals, 'expected': expected,
        'actual': actual_cash, 'variance': (actual_cash - expected) if actual_cash is not None else None,
        'has_opening_day': bool(opening_day), 'has_ending_count': actual_cash is not None,
        'days_recorded': len(cash_days), 'days_expected': (end - start).days + 1,
        'baseline_source': baseline_source, 'provisional_opening': provisional,
        'earliest_cash_activity': earliest_cash_activity, 'has_historical_activity': bool(earliest),
    }


def expected_cash_by_day(pharmacy, start, end, opening):
    """Running drawer balance per day from the SAME ledger: the last day's expected
    cash therefore equals the period expected cash on the screen."""
    totals = CashLedgerService.daily_totals(pharmacy.pk, start, end)
    cash_days = {d.date: d for d in CashDay.objects.filter(pharmacy=pharmacy, date__range=(start, end))}
    rows, balance, cursor = [], opening, start
    while cursor <= end:
        t = totals.get(cursor)
        sales = t.cash_sales if t else ZERO
        debt_cash = t.cash_debt_collected if t else ZERO
        refunds = t.cash_refunds if t else ZERO
        expenses = t.cash_expenses if t else ZERO
        meals = t.cash_meals if t else ZERO
        tin = t.transfer_in if t else ZERO
        tout = t.transfer_out if t else ZERO
        day_open = balance
        balance = day_open + sales + debt_cash + tin - refunds - expenses - meals - tout
        cd = cash_days.get(cursor)
        actual = cd.actual_closing_cash if cd and cd.actual_closing_cash is not None else None
        rows.append({'date': cursor, 'opening': day_open, 'sales': sales, 'debt_cash': debt_cash, 'refunds': refunds,
                     'expenses': expenses, 'meals': meals, 'expected': balance, 'actual': actual,
                     # V97.39 itemised extras for the on-screen drawer statement
                     'transfer_in': tin, 'transfer_out': tout,
                     'expense_paid': t.expense_paid if t else ZERO, 'expense_reversed': t.expense_reversed if t else ZERO,
                     'cash_after_meals': sales - meals,
                     'net_move': balance - day_open,
                     'variance': (actual - balance) if actual is not None else None})
        cursor += timedelta(days=1)
    return rows


# --------------------------------------------------------------------------
# Detail queries
# --------------------------------------------------------------------------

def items_sold(pharmacy, start, end):
    """One row per product: net units, sales, cost, profit (voided sales excluded)."""
    qs = (SaleItem.objects.filter(sale__pharmacy=pharmacy, sale__business_date__range=(start, end))
          .exclude(sale__status=Sale.Status.VOIDED))
    sales = ExpressionWrapper(F('unit_price') * F('quantity'), output_field=MONEY)
    cost = ExpressionWrapper(F('unit_cost') * F('quantity'), output_field=MONEY)
    return list(qs.values('product__name').annotate(
        units=Coalesce(Sum('quantity'), 0), returned=Coalesce(Sum('returned_quantity'), 0),
        amount=Coalesce(Sum(sales), ZERO, output_field=MONEY),
        cost=Coalesce(Sum(cost), ZERO, output_field=MONEY),
    ).order_by('-amount', 'product__name'))


def expense_rows(pharmacy, start, end):
    """Every approved expense in the period, with whether it left the cash drawer."""
    qs = (Expense.objects.filter(pharmacy=pharmacy, status=Expense.Status.APPROVED,
                                 expense_date__range=(start, end))
          .select_related('category', 'submitted_by').order_by('expense_date', 'submitted_at'))
    rows = []
    for e in qs:
        rows.append({
            'date': e.expense_date, 'category': e.category.name, 'title': e.title,
            'description': e.description, 'paid_from': e.get_payment_source_display(),
            'amount': e.amount, 'drawer': e.payment_source == Expense.PaymentSource.CASH_SALES,
            'is_staff_meals': e.category.name == STAFF_MEALS_CATEGORY,
            'by': e.submitted_by.get_full_name() or e.submitted_by.get_username(),
        })
    return rows


def meal_rows(pharmacy, start, end):
    qs = (MealLedger.objects.filter(pharmacy=pharmacy, date__range=(start, end),
                                    status__in=[MealLedger.Status.EARNED, MealLedger.Status.DEBT])
          .select_related('employee').order_by('date', 'employee__first_name', 'cutoff_at'))
    rows = []
    for m in qs:
        deducted = m.status == MealLedger.Status.EARNED
        rows.append({
            'date': m.date, 'employee': m.employee.get_full_name() or m.employee.get_username(),
            'meal': m.get_meal_type_display(), 'amount': m.amount, 'deducted': deducted,
            'treatment': 'Deducted (paid from drawer)' if deducted else 'Debt - not deducted yet',
        })
    return rows


def expense_by_category(rows):
    out = OrderedDict()
    for r in rows:
        c = out.setdefault(r['category'], {'category': r['category'], 'count': 0, 'total': ZERO, 'drawer': ZERO})
        c['count'] += 1
        c['total'] += r['amount']
        if r['drawer']:
            c['drawer'] += r['amount']
    return sorted(out.values(), key=lambda c: -c['total'])



def layaway_period_summary(pharmacy, start, end):
    """Receipt-date layaway breakdown; handovers are fulfilment, not new receipts."""
    from layaway.models import LayawayPlan, LayawayPayment, LayawayRefund
    payments = LayawayPayment.objects.filter(plan__pharmacy=pharmacy, status=LayawayPayment.Status.RECEIVED, received_at__date__range=(start, end))
    refunds = LayawayRefund.objects.filter(plan__pharmacy=pharmacy, refunded_at__date__range=(start, end))
    result = {
        'cash_received': sum((x.amount for x in payments.filter(method=LayawayPayment.Method.CASH)), ZERO),
        'mobile_received': sum((x.amount for x in payments.filter(method=LayawayPayment.Method.MOBILE)), ZERO),
        'bank_received': sum((x.amount for x in payments.filter(method=LayawayPayment.Method.BANK)), ZERO),
        'other_received': sum((x.amount for x in payments.filter(method=LayawayPayment.Method.OTHER)), ZERO),
        'cash_refunds': sum((x.amount for x in refunds.filter(method=LayawayRefund.Method.CASH)), ZERO),
        'mobile_refunds': sum((x.amount for x in refunds.filter(method=LayawayRefund.Method.MOBILE)), ZERO),
        'plans_created': LayawayPlan.objects.filter(pharmacy=pharmacy, created_at__date__range=(start, end)).count(),
        'handovers': LayawayPlan.objects.filter(pharmacy=pharmacy, handed_over_at__date__range=(start, end)).count(),
    }
    result['total_received'] = sum((result[k] for k in ('cash_received','mobile_received','bank_received','other_received')), ZERO)
    result['total_refunded'] = result['cash_refunds'] + result['mobile_refunds']
    return result

def collect_report_data(pharmacy, start, end, financial):
    """Everything both exports need, computed once."""
    cash = period_cash_control(pharmacy, start, end)
    exp = expense_rows(pharmacy, start, end)
    meals = meal_rows(pharmacy, start, end)
    exp_total = sum((r['amount'] for r in exp), ZERO)
    exp_drawer = sum((r['amount'] for r in exp if r['drawer']), ZERO)
    notes = []
    if any(r['is_staff_meals'] for r in exp):
        notes.append('This period contains manually recorded "Staff Meals" expenses. Scanned meals are already '
                     'deducted automatically, so these may be deducted twice - review them in Expenses.')
    if cash['provisional_opening']:
        notes.append('Opening cash is an estimate rebuilt from earlier recorded cash movements (no verified '
                     'opening count exists before this period).')
    legacy = financial['meals'] - cash['meals']
    if legacy > 0:
        notes.append(f'Earned meals of TZS {_n(legacy)} in this period are charged to profit on their meal dates. '
                     'Their drawer-cash effect is recorded on the day the meal was paid from the drawer or, for meals '
                     'earned before automatic meal payments began, on the one-time settlement date - so it may fall '
                     'outside this period.')
    recon = sales_reconciliation(pharmacy, start, end)
    mobile = mobile_period_account(pharmacy, start, end)
    mobile['check'] = mobile_statement_check(pharmacy, start, end)
    refunds = refund_rows(pharmacy, start, end)
    if recon['voided'] or recon['partial_returns'] or refunds:
        notes.append(
            f"Cash sales are shown at their ORIGINAL amount; refunds are deducted separately. Total sales are NET "
            f"of voids (TZS {_n(recon['voided'])}) and partial returns (TZS {_n(recon['partial_returns'])}). "
            f"Gross sales TZS {_n(recon['gross'])} - voids - returns = net sales TZS {_n(recon['net'])}.")
    earlier = [r for r in refunds if not r['same_day']]
    if earlier:
        notes.append(f"{len(earlier)} refund(s) paid in this period belong to sales made on an earlier date "
                     f"(TZS {_n(sum(r['refund_due'] for r in earlier))}); they reduce drawer cash today but "
                     f"reduce sales on the original sale date.")
    return {
        'recon': recon, 'mobile': mobile, 'refunds': refunds,
        'layaway': layaway_period_summary(pharmacy, start, end),
        'pharmacy': pharmacy, 'start': start, 'end': end, 'financial': financial, 'cash': cash,
        'cash_rows': expected_cash_by_day(pharmacy, start, end, cash['opening']),
        'items': items_sold(pharmacy, start, end), 'meals': meals,
        'expenses': exp, 'expense_total': exp_total, 'expense_drawer': exp_drawer,
        'expense_other': exp_total - exp_drawer, 'expense_categories': expense_by_category(exp),
        'transactions': financial['transactions'], 'notes': notes,
        'daily': [d for d in financial['daily_rows']
                  if d['sales'] or d['meals'] or d['expenses'] or d['meal_debt']],
    }


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

def build_range_report_pdf(pharmacy, start, end, financial):
    d = collect_report_data(pharmacy, start, end, financial)
    cash, cash_rows = d['cash'], d['cash_rows']
    cash_by_date = {r['date']: r for r in cash_rows}

    daily = []
    for r in d['daily']:
        c = cash_by_date.get(r['date'])
        daily.append([f"{r['date']:%d %b %Y}", _n(r['sales']), _n(r['cost']), _n(r['gross_profit']),
                      _n(r['meals']), _n(r['expenses']), _n(r['net_profit']), _n(c['expected']) if c else '-'])
    daily.append(['<b>TOTAL</b>', f"<b>{_n(financial['sales'])}</b>", f"<b>{_n(financial['cost'])}</b>",
                  f"<b>{_n(financial['gross_profit'])}</b>", f"<b>{_n(financial['meals'])}</b>",
                  f"<b>{_n(financial['expenses'])}</b>", f"<b>{_n(financial['net_profit'])}</b>",
                  f"<b>{_n(cash['expected'])}</b>"])

    item_rows = [[r['product__name'], f"{r['units']:,}", f"{r['returned']:,}", _n(r['amount']), _n(r['cost']),
                  _n(r['amount'] - r['cost'])] for r in d['items']]
    if d['items']:
        item_rows.append(['<b>TOTAL</b>', f"<b>{sum(r['units'] for r in d['items']):,}</b>",
                          f"<b>{sum(r['returned'] for r in d['items']):,}</b>",
                          f"<b>{_n(sum(r['amount'] for r in d['items']))}</b>",
                          f"<b>{_n(sum(r['cost'] for r in d['items']))}</b>",
                          f"<b>{_n(sum(r['amount'] - r['cost'] for r in d['items']))}</b>"])

    meal_table = [[f"{m['date']:%d %b %Y}", m['employee'], m['meal'], _n(m['amount']), m['treatment']]
                  for m in d['meals']]
    if meal_table:
        paid = sum((m['amount'] for m in d['meals'] if m['deducted']), ZERO)
        debt = sum((m['amount'] for m in d['meals'] if not m['deducted']), ZERO)
        meal_table.append(['<b>TOTAL</b>', '', '', f'<b>{_n(paid)}</b>',
                           f'<b>Deducted {_n(paid)} · Debt {_n(debt)}</b>'])

    exp_table = [[f"{e['date']:%d %b %Y}", e['category'],
                  e['title'] + (f" - {e['description']}" if e['description'] else ''),
                  e['paid_from'], _n(e['amount']), 'Yes' if e['drawer'] else 'No'] for e in d['expenses']]
    if exp_table:
        exp_table.append(['<b>TOTAL</b>', '', '', '', f"<b>{_n(d['expense_total'])}</b>",
                          f"<b>{_n(d['expense_drawer'])}</b>"])
    cat_table = [[c['category'], c['count'], _n(c['total']), _n(c['drawer'])] for c in d['expense_categories']]

    cash_table = [[f"{r['date']:%d %b %Y}", _n(r['opening']), _n(r['sales'] + r['debt_cash']), _n(r['refunds']),
                   _n(r['expenses']), _n(r['meals']), f"<b>{_n(r['expected'])}</b>",
                   _n(r['actual']) if r['actual'] is not None else 'Not counted'] for r in cash_rows]

    # Layaway detail comes from the shared report builder used by screen and exports.
    layaway_summary = d['layaway']
    rc = d['recon']
    bridge = [
        ['Gross sales (original amounts)', _n(rc['gross'])],
        ['- Voided sales', _n(rc['voided'])],
        ['- Partial returns', _n(rc['partial_returns'])],
        ['<b>= NET SALES (Total sales above)</b>', f"<b>{_n(rc['net'])}</b>"],
    ]
    refund_table = [[f"{r['decided_at']:%d %b %H:%M}", f"#{r['sale_number']}", f"{r['sale_date']:%d %b %Y}",
                     r['kind'], r['method'], _n(r['refund_due']), r['by'], r['reason'] or '-'] for r in d['refunds']]
    if refund_table:
        refund_table.append(['<b>TOTAL</b>', '', '', '', '', f"<b>{_n(sum(r['refund_due'] for r in d['refunds']))}</b>", '', ''])

    mm = d['mobile']
    collected = [
        ['Layaway mobile-money instalments received (already included below)', _n(mm.get('layaway_received', ZERO))],
        ['- Layaway refunds paid by mobile money (already deducted below)', _n(mm.get('layaway_refunds', ZERO))],
        ['Mobile money from sales (incl. mobile part of Cash + Mobile)', _n(mm['sales'] + mm['split'])],
        ['+ Debt repayments received by mobile money', _n(mm['debt'])],
        ['- Refunds paid back by mobile money', _n(mm['refunds'])],
        ['<b>= MOBILE MONEY RECEIVED</b>', f"<b>{_n(mm['received'])}</b>"],
        ['Cash movement in the drawer (expected cash - opening cash)', _n(cash['expected'] - cash['opening'])],
        ['<b>= TOTAL COLLECTED (cash + mobile money)</b>', f"<b>{_n(cash['expected'] - cash['opening'] + mm['received'])}</b>"],
        ['Mobile money account: opening balance', _n(mm['opening'])],
        ['+ Mobile money received', _n(mm['received'])],
        ['- Expenses paid from mobile money (incl. network charges)', _n(mm['expenses'])],
        ['+ Drawer cash put into mobile money', _n(mm['deposits'])],
        ['- Mobile money cashed out into the drawer', _n(mm['cash_outs'])],
        ['<b>= EXPECTED MOBILE MONEY BALANCE (check against your statement)</b>', f"<b>{_n(mm['expected_closing'])}</b>"],
        ['<b>TOTAL MONEY (expected cash + expected mobile balance)</b>', f"<b>{_n(cash['expected'] + mm['expected_closing'])}</b>"],
    ]
    chk = mm.get('check')
    if chk:
        collected += [
            [f"Mobile money balance per statement on {chk['date']:%d %b %Y}", _n(chk['actual'])],
            ['Expected mobile balance for that day', _n(chk['expected'])],
            ['<b>Difference (statement - expected)</b>', f"<b>{_n(chk['difference'])}</b>"],
        ]

    handover = [
        ['Opening cash', _n(cash['opening'])],
        ['+ Cash sales received (before refunds)', _n(cash['cash_sales'])],
        ['+ Debt repayments received in cash', _n(cash['debt_cash'])],
        ['- Cash refunds paid', _n(cash['refunds'])],
        ['+ Mobile money cashed out into the drawer', _n(cash['transfer_in'])],
        ['- Expenses paid from the drawer', _n(cash['expenses'])],
        ['- Staff meals paid from the drawer', _n(cash['meals'])],
        ['- Drawer cash put into mobile money', _n(cash['transfer_out'])],
        ['<b>= EXPECTED CASH after all deductions</b>', f"<b>{_n(cash['expected'])}</b>"],
    ]
    if cash['actual'] is not None:
        handover += [['Counted cash on the last day', _n(cash['actual'])],
                     ['Difference (counted - expected)', _n(cash['variance'])]]
    handover.append(['Not in the drawer (mobile money, card, credit)',
                     _n(financial['mobile_sales'] + financial['credit_sales'] + financial['credit_deposits'])])

    layaway_rows = [
        ['Cash instalments received (drawer inflow)', _n(layaway_summary['cash_received'])],
        ['Mobile-money instalments received (mobile inflow)', _n(layaway_summary['mobile_received'])],
        ['Bank transfers received', _n(layaway_summary['bank_received'])],
        ['Other-method payments received', _n(layaway_summary['other_received'])],
        ['TOTAL LAYAWAY PAYMENTS RECEIVED', _n(layaway_summary['total_received'])],
        ['Cash refunds paid', _n(layaway_summary['cash_refunds'])],
        ['Mobile-money refunds paid', _n(layaway_summary['mobile_refunds'])],
        ['TOTAL LAYAWAY REFUNDS', _n(layaway_summary['total_refunded'])],
        ['Plans created in period', str(layaway_summary['plans_created'])],
        ['Plans handed over in period (fulfilment only; not a new receipt)', str(layaway_summary['handovers'])],
    ]
    sections = [
        {'title': 'Layaway reconciliation (separate from sales revenue)', 'headers': ['Movement', 'Amount / count'], 'rows': layaway_rows, 'column_widths': [120, 54]},
        {'title': 'Sales bridge: gross to net', 'headers': ['Step', 'Amount (TZS)'],
         'rows': bridge, 'column_widths': [120, 54],
         'note': 'Cash sales in the drawer table are at their original amount. Total sales are net of voids and returns.'},
        {'title': 'Expected cash hand-over', 'headers': ['Cash reconciliation', 'Amount (TZS)'],
         'rows': handover, 'column_widths': [120, 54],
         'note': 'This is the cash that should be in the drawer at the end of the period after every deduction.'},
        {'title': 'Mobile money & total collected', 'headers': ['Collection', 'Amount (TZS)'],
         'rows': collected, 'column_widths': [120, 54],
         'note': 'Mobile money is real money. It has its own account (opening, movements, balance) beside the drawer, and both are added together as total money. Moving money between them is a transfer and never changes total money.'},
        {'title': 'Daily sales, profit and expected cash',
         'headers': ['Date', 'Sales', 'Cost', 'Gross profit', 'Meals', 'Expenses', 'Net profit', 'Expected cash'],
         'rows': daily, 'column_widths': [24, 22, 22, 22, 18, 20, 22, 24],
         'note': 'Net profit = gross profit - approved expenses - earned staff meals. Meal debt is not deducted until sales fund it.'},
        {'title': 'Expenses deducted',
         'headers': ['Date', 'Category', 'Description', 'Paid from', 'Amount (TZS)', 'From drawer'],
         'rows': exp_table, 'column_widths': [22, 30, 52, 26, 26, 18],
         'note': 'Every approved expense reduces profit; only those paid from sales/petty cash also reduce expected cash.'},
        {'title': 'Expenses by category',
         'headers': ['Category', 'Entries', 'Total (TZS)', 'From drawer (TZS)'],
         'rows': cat_table, 'column_widths': [74, 20, 40, 40]},
        {'title': 'Staff meals deducted',
         'headers': ['Date', 'Employee', 'Meal', 'Amount (TZS)', 'Treatment'],
         'rows': meal_table, 'column_widths': [24, 44, 28, 26, 48],
         'note': 'Earned meals are paid from the sales drawer automatically and reduce expected cash.'},
        {'title': 'Refunds & voids paid in this period',
         'headers': ['Time', 'Sale', 'Sale date', 'Type', 'Paid by', 'Refund (TZS)', 'Approved by', 'Reason'],
         'rows': refund_table, 'column_widths': [20, 12, 20, 20, 18, 20, 26, 38],
         'note': 'Cash refunds reduce expected cash on the day they are approved.'},
        {'title': 'Items sold in this period',
         'headers': ['Item', 'Units', 'Returned', 'Sales (TZS)', 'Cost (TZS)', 'Profit (TZS)'],
         'rows': item_rows, 'column_widths': [60, 14, 16, 28, 28, 28],
         'note': 'Net units after partial returns (Returned = units taken back). Fully voided sales are excluded, so they never appear here.'},
        {'title': 'Expected cash by day',
         'headers': ['Date', 'Opening', 'Cash in (sales + debt repaid)', 'Refunds', 'Expenses', 'Meals', 'Expected', 'Counted'],
         'rows': cash_table, 'column_widths': [24, 22, 22, 18, 20, 18, 24, 22],
         'note': 'Expected = opening + cash sales + cash debt repayments - refunds - cash expenses - staff meals. '
                 'Mobile money, card and unpaid credit sales never enter the drawer.'},
    ]
    if d['notes']:
        sections.append({'title': 'Notes', 'headers': ['Please review'], 'rows': [[n] for n in d['notes']],
                         'column_widths': [174]})
    name = pharmacy.name if pharmacy else '-'
    summary = [
        ('Branch', name), ('Report period', f'{start:%d %B %Y} - {end:%d %B %Y}'),
        ('Transactions', d['transactions']),
        ('Gross sales', f"TZS {_n(rc['gross'])}"),
        ('Voids + returns', f"TZS {_n(rc['voided'] + rc['partial_returns'])}"),
        ('Total sales (net)', f"TZS {_n(financial['sales'])}"),
        ('Cost of goods sold', f"TZS {_n(financial['cost'])}"),
        ('Gross profit', f"TZS {_n(financial['gross_profit'])}"),
        ('Staff meals deducted', f"TZS {_n(financial['meals'])}"),
        ('Meal debt (not deducted)', f"TZS {_n(financial['meal_debt'])}"),
        ('Approved expenses', f"TZS {_n(d['expense_total'])}"),
        ('Net profit', f"TZS {_n(financial['net_profit'])}"),
        ('Expected cash (hand-over)', f"TZS {_n(cash['expected'])}"),
    ]
    return generate_multi_section_pdf_report(
        title='Sales, Profit & Cash Report', subtitle=f'{name} - {start:%d %B %Y} to {end:%d %B %Y}',
        sections=sections, summary=summary, filename=f'sales_profit_cash_{start}_{end}.pdf')


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------

def build_range_report_xlsx(pharmacy, start, end, financial):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    d = collect_report_data(pharmacy, start, end, financial)
    cash = d['cash']
    cash_by_date = {r['date']: r for r in d['cash_rows']}
    teal = PatternFill('solid', fgColor='0F766E')
    soft = PatternFill('solid', fgColor='F0FDFA')
    grey = PatternFill('solid', fgColor='F1F5F9')
    thin = Side(style='thin', color='E2E8F0')
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    money = '#,##0;[Red]-#,##0'

    wb = Workbook()

    def sheet(title, headers, rows, widths, money_cols=(), total_row=None, first=False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = teal
            c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            c.border = box
        for r in rows:
            ws.append(r)
        if total_row:
            ws.append(total_row)
            for c in ws[ws.max_row]:
                c.font = Font(bold=True)
                c.fill = grey
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.border = box
                if c.column in money_cols:
                    c.number_format = money
                    c.alignment = Alignment(horizontal='right')
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = 'A2'
        return ws

    # Summary
    ws = wb.active
    ws.title = 'Summary'
    ws['A1'] = 'SmartPOS - Sales, Profit & Cash Report'
    ws['A1'].font = Font(bold=True, size=15, color='0F766E')
    ws['A2'] = f"{pharmacy.name if pharmacy else ''} | {start:%d %B %Y} to {end:%d %B %Y}"
    ws['A2'].font = Font(color='64748B')
    lines = [
        ('Transactions', d['transactions'], False),
        ('Gross sales (original amounts)', d['recon']['gross'], True),
        ('- Voided sales', d['recon']['voided'], True), ('- Partial returns', d['recon']['partial_returns'], True),
        ('Total sales (net)', financial['sales'], True), ('Cost of goods sold', financial['cost'], True),
        ('Gross profit', financial['gross_profit'], True),
        ('Staff meals deducted', financial['meals'], True),
        ('Meal debt (not deducted yet)', financial['meal_debt'], True),
        ('Approved expenses (all)', d['expense_total'], True),
        ('   of which paid from the drawer', d['expense_drawer'], True),
        ('NET PROFIT', financial['net_profit'], True), (None, None, False),
        ('EXPECTED CASH HAND-OVER', None, False),
        ('Opening cash', cash['opening'], True), ('+ Cash sales received (before refunds)', cash['cash_sales'], True),
        ('+ Debt repayments received in cash', cash['debt_cash'], True),
        ('- Cash refunds paid', cash['refunds'], True),
        ('+ Mobile money cashed out into the drawer', cash['transfer_in'], True),
        ('- Expenses paid from the drawer', cash['expenses'], True),
        ('- Staff meals paid from the drawer', cash['meals'], True),
        ('- Drawer cash put into mobile money', cash['transfer_out'], True),
        ('= EXPECTED CASH after all deductions', cash['expected'], True),
    ]
    mm = d['mobile']
    # Add layaway detail to the workbook without counting it again in cash/mobile totals.
    from layaway.models import LayawayPlan, LayawayPayment, LayawayRefund
    lp = LayawayPayment.objects.filter(plan__pharmacy=pharmacy, status=LayawayPayment.Status.RECEIVED, received_at__date__range=(start, end))
    lr = LayawayRefund.objects.filter(plan__pharmacy=pharmacy, refunded_at__date__range=(start, end))
    lines += [(None, None, False), ('LAYAWAY RECONCILIATION (already included in cash/mobile balance movements)', None, False),
              ('Cash instalments received', sum((x.amount for x in lp.filter(method=LayawayPayment.Method.CASH)), ZERO), True),
              ('Mobile-money instalments received', sum((x.amount for x in lp.filter(method=LayawayPayment.Method.MOBILE)), ZERO), True),
              ('Bank transfers received', sum((x.amount for x in lp.filter(method=LayawayPayment.Method.BANK)), ZERO), True),
              ('Other-method payments received', sum((x.amount for x in lp.filter(method=LayawayPayment.Method.OTHER)), ZERO), True),
              ('Cash refunds paid', sum((x.amount for x in lr.filter(method=LayawayRefund.Method.CASH)), ZERO), True),
              ('Mobile-money refunds paid', sum((x.amount for x in lr.filter(method=LayawayRefund.Method.MOBILE)), ZERO), True),
              ('Plans created', LayawayPlan.objects.filter(pharmacy=pharmacy, created_at__date__range=(start, end)).count(), False),
              ('Plans handed over (fulfilment, not new receipt)', LayawayPlan.objects.filter(pharmacy=pharmacy, handed_over_at__date__range=(start, end)).count(), False),
              (None, None, False), ('MOBILE MONEY ACCOUNT & TOTAL MONEY', None, False),
              ('Mobile money from sales', mm['sales'] + mm['split'], True),
              ('+ Debt repayments by mobile money', mm['debt'], True),
              ('- Refunds paid by mobile money', mm['refunds'], True),
              ('= MOBILE MONEY RECEIVED', mm['received'], True),
              ('= TOTAL COLLECTED (cash + mobile)', cash['expected'] - cash['opening'] + mm['received'], True),
              (None, None, False),
              ('Mobile money opening balance', mm['opening'], True),
              ('+ Mobile money received', mm['received'], True),
              ('- Expenses paid from mobile money', mm['expenses'], True),
              ('+ Drawer cash put into mobile money', mm['deposits'], True),
              ('- Mobile money cashed out into the drawer', mm['cash_outs'], True),
              ('= EXPECTED MOBILE MONEY BALANCE', mm['expected_closing'], True),
              ('TOTAL MONEY (expected cash + expected mobile balance)', cash['expected'] + mm['expected_closing'], True),
              (None, None, False)]
    if mm.get('check'):
        c = mm['check']
        lines += [(f"Mobile money balance per statement on {c['date']:%d %b %Y}", c['actual'], True),
                  ('Expected mobile balance for that day', c['expected'], True),
                  ('Difference (statement - expected)', c['difference'], True), (None, None, False)]
    if cash['actual'] is not None:
        lines += [('Counted cash on the last day', cash['actual'], True),
                  ('Difference (counted - expected)', cash['variance'], True)]
    lines.append(('Not in drawer (mobile money, card, credit)', financial['mobile_sales'] + financial['credit_sales'] + financial['credit_deposits'], True))
    row = 4
    for label, value, is_money in lines:
        if label is None:
            row += 1
            continue
        a, b = ws.cell(row=row, column=1, value=label), ws.cell(row=row, column=2, value=value)
        if is_money:
            b.number_format = money
        if label.isupper() or label.startswith('='):
            for c in (a, b):
                c.font = Font(bold=True)
                c.fill = soft
        row += 1
    for note in d['notes']:
        row += 1
        ws.cell(row=row, column=1, value='Note: ' + note).alignment = Alignment(wrap_text=True, vertical='top')
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        ws.row_dimensions[row].height = 48
    ws.column_dimensions['A'].width = 52
    ws.column_dimensions['B'].width = 22

    sheet('Daily', ['Date', 'Transactions', 'Sales', 'Cash sales', 'Mobile', 'Credit lent (unpaid)',
                    'Credit paid at sale', 'Debt repayments', 'Cost',
                    'Gross profit', 'Meals deducted', 'Meal debt', 'Expenses', 'Net profit', 'Expected cash'],
          [[r['date'], r['transactions'], r['sales'], r['cash_sales'], r['mobile_sales'], r['credit_sales'],
            r['credit_deposits'], r['debt_collected'], r['cost'], r['gross_profit'], r['meals'], r['meal_debt'],
            r['expenses'], r['net_profit'], cash_by_date[r['date']]['expected']] for r in d['daily']],
          [14, 13, 14, 14, 14, 16, 16, 15, 14, 14, 15, 12, 14, 14, 15], money_cols=range(3, 16),
          total_row=['TOTAL', d['transactions'], financial['sales'], financial['cash_sales'],
                     financial['mobile_sales'], financial['credit_sales'], financial['credit_deposits'],
                     financial['debt_collected'],
                     financial['cost'], financial['gross_profit'], financial['meals'], financial['meal_debt'],
                     financial['expenses'], financial['net_profit'], cash['expected']])
    for r in wb['Daily'].iter_rows(min_row=2, max_col=1):
        for c in r:
            if hasattr(c.value, 'year'):
                c.number_format = 'dd mmm yyyy'

    ws = sheet('Expenses', ['Date', 'Category', 'Title', 'Description', 'Paid from', 'Amount', 'From drawer',
                            'Recorded by'],
               [[e['date'], e['category'], e['title'], e['description'], e['paid_from'], e['amount'],
                 'Yes' if e['drawer'] else 'No', e['by']] for e in d['expenses']],
               [14, 22, 28, 36, 20, 14, 13, 22], money_cols=(6,),
               total_row=['TOTAL', '', '', '', '', d['expense_total'], '', ''])
    for r in ws.iter_rows(min_row=2, max_col=1):
        for c in r:
            if hasattr(c.value, 'year'):
                c.number_format = 'dd mmm yyyy'
    sheet('Expense categories', ['Category', 'Entries', 'Total', 'From drawer'],
          [[c['category'], c['count'], c['total'], c['drawer']] for c in d['expense_categories']],
          [30, 10, 16, 16], money_cols=(3, 4))

    ws = sheet('Staff meals', ['Date', 'Employee', 'Meal', 'Amount', 'Treatment'],
               [[m['date'], m['employee'], m['meal'], m['amount'], m['treatment']] for m in d['meals']],
               [14, 28, 18, 14, 32], money_cols=(4,))
    for r in ws.iter_rows(min_row=2, max_col=1):
        for c in r:
            if hasattr(c.value, 'year'):
                c.number_format = 'dd mmm yyyy'

    sheet('Refunds & voids', ['Approved at', 'Sale #', 'Sale date', 'Type', 'Paid by', 'Refund', 'Approved by', 'Reason'],
          [[r['decided_at'].replace(tzinfo=None), r['sale_number'], r['sale_date'], r['kind'], r['method'],
            r['refund_due'], r['by'], r['reason']] for r in d['refunds']],
          [18, 9, 14, 16, 14, 14, 20, 40], money_cols=(6,),
          total_row=['TOTAL', '', '', '', '', sum(r['refund_due'] for r in d['refunds']), '', ''] if d['refunds'] else None)

    sheet('Items sold', ['Item', 'Units', 'Returned', 'Sales', 'Cost', 'Profit'],
          [[r['product__name'], r['units'], r['returned'], r['amount'], r['cost'], r['amount'] - r['cost']]
           for r in d['items']], [40, 10, 10, 16, 16, 16], money_cols=(4, 5, 6),
          total_row=['TOTAL', sum(r['units'] for r in d['items']), sum(r['returned'] for r in d['items']),
                     sum(r['amount'] for r in d['items']), sum(r['cost'] for r in d['items']),
                     sum(r['amount'] - r['cost'] for r in d['items'])] if d['items'] else None)

    ws = sheet('Expected cash by day', ['Date', 'Opening', 'Cash in (sales + debt repaid)', 'Refunds', 'Expenses', 'Meals', 'Expected',
                                        'Counted'],
               [[r['date'], r['opening'], r['sales'] + r['debt_cash'], r['refunds'], r['expenses'], r['meals'], r['expected'],
                 r['actual'] if r['actual'] is not None else 'Not counted'] for r in d['cash_rows']],
               [14, 14, 14, 14, 14, 14, 15, 15], money_cols=range(2, 9))
    for r in ws.iter_rows(min_row=2, max_col=1):
        for c in r:
            if hasattr(c.value, 'year'):
                c.number_format = 'dd mmm yyyy'

    buf = BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="sales_profit_cash_{start}_{end}.xlsx"'
    return resp
