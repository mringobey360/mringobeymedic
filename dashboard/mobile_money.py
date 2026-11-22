"""Mobile money account (V54.94 / V54.96) - the twin of the drawer, for money that is NOT in the drawer.

    Mobile sales (original amounts) + mobile part of Cash+Mobile sales
  + debt repayments received by mobile money
  - refunds paid back by mobile money
  = MOBILE MONEY RECEIVED (net)

Mobile account balance:
    opening (carried forward, or set manually)
  + MOBILE MONEY RECEIVED
  - approved expenses paid from mobile money (incl. network charges)
  + drawer cash put into mobile money (CashTransfer CASH_TO_MOBILE)
  - mobile money cashed out into the drawer (CashTransfer MOBILE_TO_CASH)
  = EXPECTED MOBILE BALANCE            -> compare with the shop's mobile money statement balance

Expected cash (the drawer) is separate; total money = expected cash + expected mobile balance.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce

from expenses.ledger import cash_refund_share
from sales.models import DebtPayment, Sale, SaleItem, SaleReturnRequest

ZERO = Decimal('0')
MONEY = DecimalField(max_digits=16, decimal_places=2)
LIVE = [Sale.Status.COMPLETED, Sale.Status.PARTIALLY_RETURNED, Sale.Status.VOIDED]


def mobile_money_totals(pharmacy, start, end):
    original = ExpressionWrapper(F('unit_price') * (F('quantity') + F('returned_quantity')), output_field=MONEY)
    sales_qs = Sale.objects.filter(pharmacy=pharmacy, business_date__range=(start, end), status__in=LIVE, layaway_plan__isnull=True)
    # A layaway handover Sale records stock/revenue, not a new receipt: instalments were
    # already recorded when received. Never count its tender a second time here.

    mobile_sales = SaleItem.objects.filter(
        sale__in=sales_qs.filter(payment_method=Sale.PaymentMethod.MOBILE_MONEY).values('pk')
    ).aggregate(v=Coalesce(Sum(original), Value(ZERO, output_field=MONEY), output_field=MONEY))['v']
    split_mobile = sales_qs.filter(payment_method=Sale.PaymentMethod.SPLIT).aggregate(
        v=Coalesce(Sum('mobile_paid'), Value(ZERO, output_field=MONEY), output_field=MONEY))['v']

    debt_mobile = DebtPayment.objects.filter(
        pharmacy=pharmacy, payment_method=DebtPayment.Method.MOBILE_MONEY, paid_at__date__range=(start, end),
    ).aggregate(v=Coalesce(Sum('amount'), Value(ZERO, output_field=MONEY), output_field=MONEY))['v']

    refunds = ZERO
    for rr in (SaleReturnRequest.objects.filter(pharmacy=pharmacy, status=SaleReturnRequest.Status.APPROVED,
                                                decided_at__date__range=(start, end), refund_due__gt=0)
               .select_related('sale')):
        method = rr.sale.payment_method
        if method == Sale.PaymentMethod.MOBILE_MONEY:
            refunds += rr.refund_due
        elif method == Sale.PaymentMethod.SPLIT:
            refunds += rr.refund_due - cash_refund_share(rr)
        elif method == Sale.PaymentMethod.DEBT and rr.sale.debt.payments.filter(
                payment_method=DebtPayment.Method.MOBILE_MONEY).exists():
            refunds += rr.refund_due      # same rule as the drawer: mixed/mobile-repaid debts are not charged to cash

    from layaway.models import LayawayPayment, LayawayRefund
    layaway_mobile = LayawayPayment.objects.filter(
        plan__pharmacy=pharmacy, method=LayawayPayment.Method.MOBILE,
        status=LayawayPayment.Status.RECEIVED, received_at__date__range=(start, end),
    ).aggregate(v=Coalesce(Sum('amount'), Value(ZERO, output_field=MONEY), output_field=MONEY))['v']
    layaway_mobile_refunds = LayawayRefund.objects.filter(
        plan__pharmacy=pharmacy, method=LayawayRefund.Method.MOBILE, refunded_at__date__range=(start, end),
    ).aggregate(v=Coalesce(Sum('amount'), Value(ZERO, output_field=MONEY), output_field=MONEY))['v']
    received = mobile_sales + split_mobile + debt_mobile + layaway_mobile - refunds - layaway_mobile_refunds

    from expenses.models import CashTransfer, Expense
    expenses = Expense.objects.filter(
        pharmacy=pharmacy, status=Expense.Status.APPROVED, payment_source=Expense.PaymentSource.MOBILE_MONEY,
    ).annotate(mm_day=Coalesce('cash_date', 'expense_date')).filter(mm_day__range=(start, end)).aggregate(
        v=Coalesce(Sum('amount'), Value(ZERO, output_field=MONEY), output_field=MONEY))['v']
    tr = CashTransfer.objects.filter(pharmacy=pharmacy, transfer_date__range=(start, end))
    deposits = tr.filter(direction=CashTransfer.Direction.CASH_TO_MOBILE).aggregate(
        v=Coalesce(Sum('amount'), Value(ZERO, output_field=MONEY), output_field=MONEY))['v']
    cash_outs = tr.filter(direction=CashTransfer.Direction.MOBILE_TO_CASH).aggregate(
        v=Coalesce(Sum('amount'), Value(ZERO, output_field=MONEY), output_field=MONEY))['v']
    net = received - expenses + deposits - cash_outs
    return {'sales': mobile_sales, 'split': split_mobile, 'debt': debt_mobile,
            'layaway_received': layaway_mobile, 'layaway_refunds': layaway_mobile_refunds, 'refunds': refunds,
            'received': received, 'expenses': expenses, 'deposits': deposits, 'cash_outs': cash_outs, 'net': net}


def mobile_net(pharmacy, start, end):
    """Net movement of the mobile money account over [start, end] (0 for an empty range)."""
    if start > end:
        return ZERO
    return mobile_money_totals(pharmacy, start, end)['net']


def mobile_opening_balance(pharmacy, target_date, manual=None):
    """Mobile account balance at the START of target_date.

    1. A manually entered opening for that day wins.
    2. Otherwise the latest earlier reference point: a day whose statement balance was entered (its closing
       balance, carried from the next day) or a day with a manual opening - then add the movements since.
    3. With no reference point at all the opening is 0 (the shop has not started tracking mobile money yet)."""
    if manual is not None:
        return manual
    from expenses.models import CashDay
    base = None   # (first day whose movements still have to be added, balance at that moment)
    closed = (CashDay.objects.filter(pharmacy=pharmacy, date__lt=target_date, actual_mobile_balance__isnull=False)
              .order_by('-date').first())
    opened = (CashDay.objects.filter(pharmacy=pharmacy, date__lt=target_date, opening_mobile__isnull=False)
              .order_by('-date').first())
    if closed is not None:
        base = (closed.date + timedelta(days=1), closed.actual_mobile_balance)
    if opened is not None and (base is None or opened.date >= base[0]):
        base = (opened.date, opened.opening_mobile)
    if base is None:
        return ZERO
    first_day, balance = base
    return balance + mobile_net(pharmacy, first_day, target_date - timedelta(days=1))


def mobile_statement_check(pharmacy, start, end):
    """The latest day in the period whose statement balance was entered, compared with the expected balance
    for that same day. Returns None when no day in the period was checked."""
    from expenses.models import CashDay
    days = list(CashDay.objects.filter(pharmacy=pharmacy, date__range=(start, end),
                                       actual_mobile_balance__isnull=False).order_by('date'))
    if not days:
        return None
    last = days[-1]
    expected = last.expected_mobile_balance
    return {'days': len(days), 'period_days': (end - start).days + 1, 'date': last.date,
            'actual': last.actual_mobile_balance, 'expected': expected,
            'difference': last.actual_mobile_balance - expected}


def mobile_period_account(pharmacy, start, end):
    """Opening -> movements -> expected closing balance of the mobile account for a report period."""
    t = mobile_money_totals(pharmacy, start, end)
    opening = mobile_opening_balance(pharmacy, start)
    t['opening'] = opening
    t['expected_closing'] = opening + t['net']
    t['tracked'] = bool(opening) or any(t[k] for k in ('received', 'expenses', 'deposits', 'cash_outs'))
    return t
