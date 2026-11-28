"""Meals, expenses and expected cash for the Performance Monitor exports (V54.68).

Reuses the Report Builder's cash engine (`period_cash_control`, `expected_cash_by_day`) and the shared
`CashLedgerService`, so the Performance Monitor PDF/Excel can never disagree with the cash screens.
An expense is deducted from the drawer on the day it was APPROVED, even if it was submitted earlier.
"""
from decimal import Decimal

from django.db.models import Q

from django.utils import timezone

from expenses.ledger import CashLedgerService
from expenses.models import Expense

from .range_report import ZERO, expected_cash_by_day, meal_rows, period_cash_control


def _expense_cash_rows(pharmacy, start, end):
    """Expenses that belong to the period by date or moved drawer cash inside it.

    A rejected-after-paid expense appears as TWO drawer lines, exactly like the ledger: the original cash-out on the
    day it left, and a negative 'Reversal' on the day of the rejection. So this list always adds up to the Expenses
    column of the expected-cash table (CashLedgerService)."""
    from django.db.models import Q
    qs = (Expense.objects.filter(pharmacy=pharmacy)
          .filter(Q(status=Expense.Status.APPROVED) | Q(reversed_at__isnull=False))
          .select_related('category', 'submitted_by').order_by('expense_date', 'submitted_at'))
    rows = []
    for e in qs:
        drawer = e.payment_source == Expense.PaymentSource.CASH_SALES
        cash_day = (e.cash_date or e.expense_date) if drawer else None
        by = e.submitted_by.get_full_name() or e.submitted_by.get_username()
        reversed_ = e.reversed_at is not None
        reversed_day = timezone.localtime(e.reversed_at).date() if reversed_ else None
        in_period = start <= e.expense_date <= end or (cash_day is not None and start <= cash_day <= end)
        # A rejected expense is not a business cost: it only shows because its cash moved inside the period.
        if reversed_:
            in_period = cash_day is not None and start <= cash_day <= end
        if in_period:
            rows.append({
                'date': e.expense_date, 'cash_day': cash_day, 'category': e.category.name,
                'title': e.title + (' (rejected later)' if reversed_ else ''),
                'paid_from': e.get_payment_source_display(), 'amount': e.amount, 'drawer': drawer,
                'late': bool(cash_day and cash_day != e.expense_date and not e.auto_recorded), 'by': by,
            })
        if reversed_ and drawer and start <= reversed_day <= end:
            rows.append({
                'date': reversed_day, 'cash_day': reversed_day, 'category': e.category.name,
                'title': f'Reversal - {e.title}', 'paid_from': e.get_payment_source_display(),
                'amount': -e.amount, 'drawer': True, 'late': False, 'by': by,
            })
    return rows


def _pending_rows(pharmacy):
    qs = (Expense.objects.filter(pharmacy=pharmacy, status=Expense.Status.PENDING)
          .select_related('category').order_by('expense_date', 'submitted_at'))
    return [{'date': e.expense_date, 'category': e.category.name, 'title': e.title,
             'paid_from': e.get_payment_source_display(), 'amount': e.amount,
             'drawer': e.payment_source == Expense.PaymentSource.CASH_SALES} for e in qs]


def collect_cash_data(pharmacy, start, end):
    cash = period_cash_control(pharmacy, start, end)
    cash_rows = expected_cash_by_day(pharmacy, start, end, cash['opening'])
    meals = meal_rows(pharmacy, start, end)
    expenses = _expense_cash_rows(pharmacy, start, end)
    pending = _pending_rows(pharmacy)
    meals_deducted = sum((m['amount'] for m in meals if m['deducted']), ZERO)
    meals_debt = sum((m['amount'] for m in meals if not m['deducted']), ZERO)
    pending_drawer = sum((p['amount'] for p in pending if p['drawer']), ZERO)
    notes = []
    if cash['provisional_opening']:
        notes.append('Opening cash is an estimate rebuilt from earlier recorded cash movements (no verified '
                     'opening count exists before this period).')
    late = [e for e in expenses if e['late'] and e['drawer']]
    if late:
        notes.append(f"{len(late)} expense(s) were submitted earlier but approved later. They are deducted from the "
                     "drawer on the day they were approved, not on the day they were submitted.")
    if pending_drawer:
        notes.append(f"Pending expenses paid from the drawer (TZS {pending_drawer:,.0f}) are NOT deducted yet. "
                     "They reduce expected cash automatically on the day they are approved.")
    if meals_debt:
        notes.append(f"Meal debt of TZS {meals_debt:,.0f} is not deducted until the employee's sales fund it.")
    return {
        'cash': cash, 'cash_rows': cash_rows, 'meals': meals, 'meals_deducted': meals_deducted,
        'meals_debt': meals_debt, 'expenses': expenses, 'pending': pending, 'pending_drawer': pending_drawer,
        'expense_drawer': sum((e['amount'] for e in expenses if e['drawer'] and
                               start <= e['cash_day'] <= end), ZERO),
        'notes': notes,
    }


def _n(v):
    return f'{(v or ZERO):,.0f}'


def pdf_sections(d):
    """Sections + summary rows for generate_multi_section_pdf_report."""
    cash = d['cash']
    active = [r for r in d['cash_rows'] if r['sales'] or r['refunds'] or r['expenses'] or r['meals']]
    daily = [[f"{r['date']:%a %d %b}", _n(r['opening']), _n(r['sales']), f"-{_n(r['meals'])}" if r['meals'] else '0',
              f"-{_n(r['expenses'])}" if r['expenses'] else '0', f"-{_n(r['refunds'])}" if r['refunds'] else '0',
              f"<b>{_n(r['expected'])}</b>"] for r in active]
    daily.append(['<b>TOTAL</b>', '', f"<b>{_n(cash['cash_sales'])}</b>", f"<b>-{_n(cash['meals'])}</b>",
                  f"<b>-{_n(cash['expenses'])}</b>", f"<b>-{_n(cash['refunds'])}</b>", f"<b>{_n(cash['expected'])}</b>"])

    handover = [
        ['Opening cash', _n(cash['opening'])],
        ['+ Cash sales received', _n(cash['cash_sales'])],
        ['- Staff meals deducted from the drawer', _n(cash['meals'])],
        ['- Expenses deducted from the drawer', _n(cash['expenses'])],
        ['- Cash refunds paid', _n(cash['refunds'])],
        ['<b>= EXPECTED CASH at end of period</b>', f"<b>{_n(cash['expected'])}</b>"],
    ]
    if cash['actual'] is not None:
        handover += [['Counted cash on the last day', _n(cash['actual'])],
                     ['Difference (counted - expected)', _n(cash['variance'])]]
    if d['pending_drawer']:
        handover.append(['Pending expenses (not yet deducted)', _n(d['pending_drawer'])])

    meal_rows_ = [[f"{m['date']:%d %b}", m['employee'], m['meal'], _n(m['amount']), m['treatment']] for m in d['meals']]
    if meal_rows_:
        meal_rows_.append(['<b>TOTAL</b>', '', '', f"<b>{_n(d['meals_deducted'])}</b>",
                           f"<b>Deducted {_n(d['meals_deducted'])} | Debt {_n(d['meals_debt'])}</b>"])
    exp_rows = [[f"{e['date']:%d %b}", f"{e['cash_day']:%d %b}" if e['cash_day'] else '-', e['category'], e['title'],
                 _n(e['amount']), 'Drawer' if e['drawer'] else e['paid_from']] for e in d['expenses']]
    if exp_rows:
        exp_rows.append(['<b>TOTAL</b>', '', '', '', f"<b>{_n(sum((e['amount'] for e in d['expenses']), ZERO))}</b>",
                         f"<b>Drawer {_n(d['expense_drawer'])}</b>"])
    pend_rows = [[f"{p['date']:%d %b}", p['category'], p['title'], _n(p['amount']),
                  'Drawer' if p['drawer'] else p['paid_from']] for p in d['pending']]

    sections = [
        {'title': 'Expected cash up to the end of the period', 'headers': ['Cash reconciliation', 'Amount (TZS)'],
         'rows': handover, 'column_widths': [120, 54],
         'note': 'Opening + cash sales - meals - expenses - refunds. Mobile money, card and credit sales never enter the drawer.'},
        {'title': 'Daily expected cash (meals and expenses deducted)',
         'headers': ['Day', 'Opening', 'Cash sales', 'Meals', 'Expenses', 'Refunds', 'Expected cash'],
         'rows': daily, 'column_widths': [26, 24, 24, 22, 24, 22, 32],
         'note': 'Days with no cash movement are omitted; expected cash carries forward through them.'},
    ]
    if meal_rows_:
        sections.append({'title': 'Staff meals', 'headers': ['Date', 'Employee', 'Meal', 'Amount', 'Treatment'],
                         'rows': meal_rows_, 'column_widths': [18, 48, 26, 24, 58]})
    if exp_rows:
        sections.append({'title': 'Expenses', 'headers': ['Dated', 'Deducted on', 'Category', 'Description', 'Amount', 'Paid from'],
                         'rows': exp_rows, 'column_widths': [18, 22, 32, 56, 24, 22],
                         'note': '"Deducted on" is the day the expense was approved and left the drawer.'})
    if pend_rows:
        sections.append({'title': 'Pending expenses (not yet deducted)',
                         'headers': ['Dated', 'Category', 'Description', 'Amount', 'Paid from'],
                         'rows': pend_rows, 'column_widths': [20, 36, 70, 26, 22],
                         'note': 'Once approved, drawer expenses are deducted automatically on the approval day.'})
    if d['notes']:
        sections.append({'title': 'Notes', 'headers': ['Please review'], 'rows': [[n] for n in d['notes']],
                         'column_widths': [174]})
    summary = [
        ('Meals deducted', f"TZS {_n(d['meals_deducted'])}"),
        ('Expenses deducted from drawer', f"TZS {_n(cash['expenses'])}"),
        ('Expected cash', f"TZS {_n(cash['expected'])}"),
    ]
    return sections, summary
