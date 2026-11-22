import calendar
import json
from datetime import timedelta, date as date_cls
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum, F, DecimalField, ExpressionWrapper, Q
from django.db.models.functions import Coalesce, TruncDate
from django.shortcuts import render, redirect
from django.utils import timezone

from accounts.decorators import admin_required, employee_or_admin_required
from accounts.models import User, MealLedger, MealWorkday, AttendanceRecord, PayrollRun, PayrollItem, WorkdayReopenRequest
from core.pdf_utils import generate_pdf_report
from inventory.models import Product, ProductBatch
from sales.models import Sale, SaleItem, Debt, DebtPayment, SaleReturnRequest
from expenses.models import Expense, CashDay


MONEY = DecimalField(max_digits=14, decimal_places=2)


def _sales_between(pharmacy, start, end):
    """Return SaleItems in an inclusive local-date range using index-friendly
    datetime bounds instead of applying DATE() to every created_at value.
    """
    # Sale.business_date is the day the sale really happened (equal to the entry date for normal
    # sales, earlier for "late entry" paper-notebook sales), so every figure lands on the right day.
    return SaleItem.objects.filter(
        sale__pharmacy=pharmacy, sale__business_date__gte=start, sale__business_date__lte=end
    )


def _sum_items(items):
    """
    Aggregate revenue/cost/units for a SaleItem queryset ENTIRELY in the
    database (SUM(unit_price*quantity) etc. via Postgres), instead of pulling
    every row into Python. With a handful of sales this made no visible
    difference; with millions of SaleItem rows, looping in Python to build
    the same total is the difference between a sub-second query and a
    multi-minute one (or an out-of-memory worker).
    """
    agg = items.aggregate(
        total_sales=Coalesce(Sum(ExpressionWrapper(F('unit_price') * F('quantity'), output_field=MONEY)), Decimal('0'), output_field=MONEY),
        total_cost=Coalesce(Sum(ExpressionWrapper(F('unit_cost') * F('quantity'), output_field=MONEY)), Decimal('0'), output_field=MONEY),
        total_units=Coalesce(Sum('quantity'), 0),
    )
    return agg['total_sales'], agg['total_cost'], agg['total_units']


def _returned_value_by_day(pharmacy, start, end):
    """{business_date: {'cash': x, 'mobile': y}} - original value of returned / voided cash, mobile and split sales
    on the sale's own business date. The ONLY place this is calculated: the money-flow figures, Reports, the Daily
    Report and the Cash card all read it through _financial_daily_rows, so they can never disagree."""
    zero = Decimal('0')
    out = {}
    rows = (SaleItem.objects.filter(
        sale__pharmacy=pharmacy, sale__business_date__gte=start, sale__business_date__lte=end,
        sale__layaway_plan__isnull=True, returned_quantity__gt=0,
        sale__payment_method__in=[Sale.PaymentMethod.CASH, Sale.PaymentMethod.MOBILE_MONEY, Sale.PaymentMethod.SPLIT],
    ).values('sale__business_date', 'sale__payment_method', 'sale__cash_paid', 'sale__mobile_paid')
        .annotate(value=Coalesce(Sum(ExpressionWrapper(F('unit_price') * F('returned_quantity'), output_field=MONEY)),
                                 Decimal('0'), output_field=MONEY)))
    for row in rows:
        bucket = out.setdefault(row['sale__business_date'], {'cash': zero, 'mobile': zero})
        value = row['value'] or zero
        method = row['sale__payment_method']
        if method == Sale.PaymentMethod.CASH:
            bucket['cash'] += value
        elif method == Sale.PaymentMethod.MOBILE_MONEY:
            bucket['mobile'] += value
        else:
            paid = (row['sale__cash_paid'] or zero) + (row['sale__mobile_paid'] or zero)
            cash_share = (value * (row['sale__cash_paid'] or zero) / paid) if paid > 0 else value
            bucket['cash'] += cash_share
            bucket['mobile'] += value - cash_share
    return out


def _cash_card(breakdown, returned_cash):
    """Figures for the dashboard Cash card from the cash ledger breakdown. Cash sales are shown AFTER today's returns
    and only refunds of earlier days are listed, so a sale returned the same day is not shown as a sale plus a refund.
    opening + net sales - earlier refunds (+ other items) is still exactly the ledger's expected cash."""
    zero = Decimal('0')
    returned = min(returned_cash, breakdown['cash_sales'])
    return {
        'net_sales': breakdown['cash_sales'] - returned,
        'returned': returned,
        'earlier_refunds': max(breakdown['cash_refunds'] - returned, zero),
        'b': breakdown,
    }


def _financial_daily_rows(pharmacy, start, end):
    """Build date-by-date management figures using the branch's business date.

    Revenue/profit use completed, non-voided sale lines. Actual receipts are
    reported separately from credit sales, and approved expenses + earned or debt-status
    meals are shown as operating costs. This prevents sales revenue being mistaken
    for cash collected.
    """
    from django.db.models import Count

    zero = Decimal('0')
    days = {}
    cursor = start
    while cursor <= end:
        days[cursor] = {
            'date': cursor, 'transactions': 0, 'units': 0,
            'sales': zero, 'cost': zero, 'gross_profit': zero,
            'cash_sales': zero, 'mobile_sales': zero,
            # Credit sales are split: `credit_deposits` = paid at the till on the sale (money received),
            # `credit_sales` = the part really lent (still a debt). credit_deposits + credit_sales = credit sale value.
            'credit_sales': zero, 'credit_deposits': zero,
            # Money received on a day: `deposits_received` (paid at the till on credit sales) and
            # `debt_collected` (REPAYMENTS of earlier credit only), so a deposit is never shown as a repayment.
            'debt_collected': zero, 'deposits_received': zero, 'cash_received': zero,
            'expenses': zero, 'meals': zero, 'meal_debt': zero, 'net_profit': zero,
            'layaway_cash_received': zero, 'layaway_mobile_received': zero,
            'layaway_cash_refunds': zero, 'layaway_mobile_refunds': zero,
            # Money that DID come in on the sale's own day but belongs to a sale (or part of one) that was later
            # returned / voided. Sales above are NET of returns, while refunds are deducted in full on the day they are
            # paid, so without this the same money would be taken out twice. cash_received = money in - refunds.
            'returned_cash': zero, 'returned_mobile': zero,
        }
        cursor += timedelta(days=1)

    items = (SaleItem.objects.filter(sale__pharmacy=pharmacy, sale__business_date__gte=start,
                                     sale__business_date__lte=end)
             .exclude(sale__status=Sale.Status.VOIDED))
    amount_expr = ExpressionWrapper(F('unit_price') * F('quantity'), output_field=MONEY)
    cost_expr = ExpressionWrapper(F('unit_cost') * F('quantity'), output_field=MONEY)
    handover_sale_ids = set(Sale.objects.filter(pharmacy=pharmacy, layaway_plan__isnull=False, business_date__gte=start, business_date__lte=end).values_list('pk', flat=True))
    split_rows = list(items.filter(sale__payment_method=Sale.PaymentMethod.SPLIT)
                      .values('sale_id', 'sale__business_date', 'sale__cash_paid', 'sale__mobile_paid')
                      .annotate(amount=Coalesce(Sum(amount_expr), Decimal('0'), output_field=MONEY),
                                cost=Coalesce(Sum(cost_expr), Decimal('0'), output_field=MONEY),
                                units=Coalesce(Sum('quantity'), 0)))
    debt_rows = list(items.filter(sale__payment_method=Sale.PaymentMethod.DEBT)
                     .values('sale_id', 'sale__business_date')
                     .annotate(amount=Coalesce(Sum(amount_expr), Decimal('0'), output_field=MONEY),
                               cost=Coalesce(Sum(cost_expr), Decimal('0'), output_field=MONEY),
                               units=Coalesce(Sum('quantity'), 0)))
    deposits_by_sale = {
        r['debt__sale_id']: r['total'] for r in DebtPayment.objects.filter(
            pharmacy=pharmacy, kind=DebtPayment.Kind.DEPOSIT,
            debt__sale__business_date__gte=start, debt__sale__business_date__lte=end,
        ).values('debt__sale_id').annotate(total=Coalesce(Sum('amount'), Decimal('0'), output_field=MONEY))
    }
    items = items.exclude(sale__payment_method__in=[Sale.PaymentMethod.SPLIT, Sale.PaymentMethod.DEBT])
    for row in items.values('sale__business_date', 'sale__payment_method').annotate(
        amount=Coalesce(Sum(amount_expr), Decimal('0'), output_field=MONEY),
        cost=Coalesce(Sum(cost_expr), Decimal('0'), output_field=MONEY),
        units=Coalesce(Sum('quantity'), 0),
    ):
        day = days.get(row['sale__business_date'])
        if not day:
            continue
        amount = row['amount'] or zero
        day['sales'] += amount
        day['cost'] += row['cost'] or zero
        day['units'] += row['units'] or 0
        method = row['sale__payment_method']
        # Revenue from handover sales remains recognised above, but tender was received
        # as instalments earlier and must not be counted as a new receipt.
        receipt_rows = SaleItem.objects.filter(sale__pharmacy=pharmacy, sale__business_date=row['sale__business_date'], sale__payment_method=method, sale__layaway_plan__isnull=True)
        if method in (Sale.PaymentMethod.CASH, Sale.PaymentMethod.MOBILE_MONEY):
            receipt_amount = receipt_rows.aggregate(v=Coalesce(Sum(amount_expr), Decimal('0'), output_field=MONEY))['v'] or zero
            if method == Sale.PaymentMethod.CASH:
                day['cash_sales'] += receipt_amount
            else:
                day['mobile_sales'] += receipt_amount

    # Credit sales: the sale is revenue in full, but only the unpaid part is "credit". What the customer paid
    # at the till (never more than the sale's current value) is shown as money received, not as credit.
    for row in debt_rows:
        day = days.get(row['sale__business_date'])
        if not day:
            continue
        amount = row['amount'] or zero
        paid_now = min(deposits_by_sale.get(row['sale_id'], zero), amount)
        day['sales'] += amount
        day['cost'] += row['cost'] or zero
        day['units'] += row['units'] or 0
        day['credit_deposits'] += paid_now
        day['credit_sales'] += amount - paid_now

    # Cash + Mobile Money sales: the sale's NET value is shared between cash and mobile in the proportion paid.
    for row in split_rows:
        day = days.get(row['sale__business_date'])
        if not day:
            continue
        day['sales'] += row['amount'] or zero
        day['cost'] += row['cost'] or zero
        day['units'] += row['units'] or 0
        paid = (row['sale__cash_paid'] or zero) + (row['sale__mobile_paid'] or zero)
        if row['sale_id'] not in handover_sale_ids:
            cash_share = (row['amount'] * row['sale__cash_paid'] / paid) if paid > 0 else row['amount']
            day['cash_sales'] += cash_share
            day['mobile_sales'] += row['amount'] - cash_share

    for row in Sale.objects.filter(pharmacy=pharmacy, business_date__gte=start, business_date__lte=end)             .exclude(status=Sale.Status.VOIDED).values('business_date').annotate(total=Count('id')):
        if row['business_date'] in days:
            days[row['business_date']]['transactions'] = row['total']

    for row in Expense.objects.filter(pharmacy=pharmacy, expense_date__gte=start, expense_date__lte=end,
                                      status=Expense.Status.APPROVED).values('expense_date').annotate(
        total=Coalesce(Sum('amount'), Decimal('0'), output_field=MONEY)):
        if row['expense_date'] in days:
            days[row['expense_date']]['expenses'] = row['total'] or zero

    # V50.42: only EARNED (funded) meals are a cost. Meal debt is shown for information
    # but is not deducted from profit until the employee's sales fund it.
    for row in MealLedger.objects.filter(pharmacy=pharmacy, date__gte=start, date__lte=end,
                                         status__in=[MealLedger.Status.EARNED, MealLedger.Status.DEBT]).values('date', 'status').annotate(
        total=Coalesce(Sum('amount'), Decimal('0'), output_field=MONEY)):
        if row['date'] in days:
            if row['status'] == MealLedger.Status.DEBT:
                days[row['date']]['meal_debt'] += row['total'] or zero
            else:
                days[row['date']]['meals'] += row['total'] or zero

    for row in DebtPayment.objects.filter(pharmacy=pharmacy, paid_at__date__gte=start, paid_at__date__lte=end)             .annotate(day=TruncDate('paid_at')).values('day', 'kind').annotate(
        total=Coalesce(Sum('amount'), Decimal('0'), output_field=MONEY)):
        if row['day'] in days:
            key = 'deposits_received' if row['kind'] == DebtPayment.Kind.DEPOSIT else 'debt_collected'
            days[row['day']][key] += row['total'] or zero

    for row in SaleReturnRequest.objects.filter(pharmacy=pharmacy, status=SaleReturnRequest.Status.APPROVED,
                                                decided_at__date__gte=start, decided_at__date__lte=end)             .annotate(day=TruncDate('decided_at')).values('day').annotate(
        total=Coalesce(Sum('refund_due'), Decimal('0'), output_field=MONEY)):
        if row['day'] in days:
            days[row['day']]['refunds'] = row['total'] or zero

    from layaway.models import LayawayPayment, LayawayRefund
    for row in LayawayPayment.objects.filter(plan__pharmacy=pharmacy, status=LayawayPayment.Status.RECEIVED, received_at__date__range=(start, end)).values('received_at__date', 'method').annotate(total=Coalesce(Sum('amount'), Decimal('0'), output_field=MONEY)):
        day = days.get(row['received_at__date'])
        if day and row['method'] == LayawayPayment.Method.CASH:
            day['layaway_cash_received'] += row['total'] or zero
        elif day and row['method'] == LayawayPayment.Method.MOBILE:
            day['layaway_mobile_received'] += row['total'] or zero
    for row in LayawayRefund.objects.filter(plan__pharmacy=pharmacy, refunded_at__date__range=(start, end)).values('refunded_at__date', 'method').annotate(total=Coalesce(Sum('amount'), Decimal('0'), output_field=MONEY)):
        day = days.get(row['refunded_at__date'])
        if day and row['method'] == LayawayRefund.Method.CASH:
            day['layaway_cash_refunds'] += row['total'] or zero
        elif day and row['method'] == LayawayRefund.Method.MOBILE:
            day['layaway_mobile_refunds'] += row['total'] or zero

    # Money taken on a sale's own day that belongs to sales (or parts of sales) later returned / voided, by tender.
    for day_key, tender in _returned_value_by_day(pharmacy, start, end).items():
        day = days.get(day_key)
        if day:
            day['returned_cash'] += tender['cash']
            day['returned_mobile'] += tender['mobile']
    # Credit-sale deposits need no add-back: `deposits_received` is already the GROSS amount paid at the till.

    for day in days.values():
        day['gross_profit'] = day['sales'] - day['cost']
        day['cash_received'] = (day['returned_cash'] + day['returned_mobile'] + (day['cash_sales'] + day['mobile_sales'] + day['deposits_received']
                                + day['debt_collected'] + day['layaway_cash_received'] + day['layaway_mobile_received']
                                - day.get('refunds', zero) - day['layaway_cash_refunds'] - day['layaway_mobile_refunds']))
        day['net_profit'] = day['gross_profit'] - day['expenses'] - day['meals']
        day['margin'] = (day['gross_profit'] / day['sales'] * Decimal('100')) if day['sales'] else zero
    return list(days.values())


def _profit_summary(pharmacy, start, end):
    """Return revenue, actual receipts and estimated net profit for a date range."""
    rows = _financial_daily_rows(pharmacy, start, end)
    keys = ('sales', 'cost', 'gross_profit', 'cash_sales', 'mobile_sales', 'credit_sales', 'credit_deposits',
            'debt_collected', 'deposits_received', 'cash_received', 'expenses', 'meals', 'meal_debt', 'net_profit',
            'layaway_cash_received', 'layaway_mobile_received', 'layaway_cash_refunds', 'layaway_mobile_refunds',
            'returned_cash', 'returned_mobile')
    result = {key: sum((row[key] for row in rows), Decimal('0')) for key in keys}
    result.update({
        'start': start, 'end': end,
        'profit': result['gross_profit'],
        'margin': (result['gross_profit'] / result['sales'] * Decimal('100')) if result['sales'] else Decimal('0'),
        'units': sum(row['units'] for row in rows),
        'transactions': sum(row['transactions'] for row in rows),
        'daily_rows': rows,
    })
    return result


def _parse_report_date(value, fallback):
    try:
        return date_cls.fromisoformat(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _require_pharmacy(request):
    from django.contrib import messages
    if request.pharmacy is None:
        messages.warning(request, "You don't have an active branch yet. Create one to get started.")
        return False
    return True


def _inventory_alerts(pharmacy, today, with_lists=False):
    """Low stock / expiring / expired figures (one annotated query each - no per-product N+1)."""
    base_products = Product.objects.filter(pharmacy=pharmacy, is_active=True)
    with_stock = base_products.annotate(
        total_qty=Coalesce(Sum('batches__quantity', filter=Q(batches__quantity__gt=0)), 0)
    )
    low_stock_qs = with_stock.filter(total_qty__lte=F('reorder_level')).order_by('total_qty')
    expiring_qs = base_products.filter(
        batches__quantity__gt=0, batches__expiry_date__gte=today,
        batches__expiry_date__lte=today + timedelta(days=30),
    ).distinct().order_by('name')
    expired_count = base_products.filter(
        batches__quantity__gt=0, batches__expiry_date__lt=today,
    ).exclude(
        batches__quantity__gt=0, batches__expiry_date__gte=today,
    ).exclude(
        batches__quantity__gt=0, batches__expiry_date__isnull=True,  # stock without an expiry date never expires
    ).distinct().count()
    data = {
        'low_stock_count': low_stock_qs.count(), 'expiring_count': expiring_qs.count(),
        'expired_count': expired_count, 'total_products': base_products.count(),
    }
    if with_lists:
        data['low_stock_products'] = list(low_stock_qs[:10])
        data['expiring_products'] = list(expiring_qs[:10])
    return data


def _workforce_presence(pharmacy, today):
    rows = []
    employees = User.objects.filter(pharmacy=pharmacy, role=User.Role.EMPLOYEE, is_active=True).order_by('first_name', 'last_name', 'username')
    today_attendance = {a.employee_id: a for a in AttendanceRecord.objects.filter(pharmacy=pharmacy, date=today).select_related('employee')}
    for employee in employees:
        attendance_row = today_attendance.get(employee.id)
        rows.append({
            'employee': employee,
            'attendance': attendance_row,
            'status': attendance_row.effective_presence_status if attendance_row else 'NOT_CHECKED_IN',
            'label': attendance_row.effective_presence_label if attendance_row else 'Not checked in',
        })
    outside = sum(1 for r in rows if r['status'] == AttendanceRecord.PresenceStatus.OUTSIDE)
    stale = sum(1 for r in rows if r['status'] in {AttendanceRecord.PresenceStatus.STALE, AttendanceRecord.PresenceStatus.UNAVAILABLE})
    return rows, outside, stale


def _cash_and_debt_context(pharmacy, today):
    pending_debts = Debt.objects.filter(pharmacy=pharmacy, is_settled=False)
    today_cash_day = CashDay.objects.filter(pharmacy=pharmacy, date=today).first()
    previous_cash_day = CashDay.objects.filter(pharmacy=pharmacy, date__lt=today).order_by('-date').first()
    return {
        'pending_debts_count': pending_debts.count(),
        'total_debt_outstanding': pending_debts.aggregate(
            total=Coalesce(Sum(F('amount_owed') - F('amount_paid')), Decimal('0'), output_field=MONEY))['total'],
        'pending_expenses_count': Expense.objects.filter(pharmacy=pharmacy, status=Expense.Status.PENDING).count(),
        'today_expenses': Expense.objects.filter(pharmacy=pharmacy, expense_date=today, status=Expense.Status.APPROVED)
            .aggregate(total=Coalesce(Sum('amount'), Decimal('0')))['total'],
        'today_cash_day': today_cash_day,
        'previous_cash_day': previous_cash_day,
        'cash_automation_state': 'READY' if today_cash_day else (
            'BLOCKED' if previous_cash_day and previous_cash_day.status != CashDay.Status.CLOSED else 'FIRST_SETUP'),
    }


@employee_or_admin_required
def business_overview_view(request):
    """Secondary management detail moved off the dashboard: stock lists and daily cash control."""
    if request.pharmacy is None or not request.user.is_management_user():
        return redirect('dashboard:home')
    today = timezone.localdate()
    pharmacy = request.pharmacy
    context = {
        **_inventory_alerts(pharmacy, today, with_lists=True),
        **_cash_and_debt_context(pharmacy, today),
    }
    return render(request, 'dashboard/business_overview.html', context)


@employee_or_admin_required
def workforce_presence_view(request):
    """Dedicated page: where each employee is right now (checked periodically, not continuous GPS tracking)."""
    if request.pharmacy is None or not request.user.is_management_user():
        return redirect('dashboard:home')
    today = timezone.localdate()
    pharmacy = request.pharmacy
    rows, outside, stale = _workforce_presence(pharmacy, today)
    status_of = lambda r: r['status']
    context = {
        'workforce_presence': rows,
        'total_staff': len(rows),
        'count_verified': sum(1 for r in rows if status_of(r) == AttendanceRecord.PresenceStatus.VERIFIED),
        'count_outside': outside,
        'count_stale': stale,
        'count_not_in': sum(1 for r in rows if status_of(r) == 'NOT_CHECKED_IN'),
        'presence_interval_minutes': max(5, min(int(pharmacy.presence_check_interval_minutes or 15), 60)),
        'today': today,
    }
    return render(request, 'dashboard/workforce_presence.html', context)


@employee_or_admin_required
def dashboard_home_view(request):
    if request.pharmacy is None:
        if request.user.is_management_user():
            messages.warning(request, "You don't have an active branch yet. Create one to get started.")
            return redirect('pharmacies:list')
        # An employee with no branch assigned must NEVER be redirected to login —
        # they are already authenticated, and doing so would bounce them straight
        # back here (login_view sends authenticated users to dashboard:home),
        # creating an infinite redirect loop. Show a clear message instead.
        return render(request, 'dashboard/no_pharmacy_assigned.html')

    # Listing accounts (online seller / property) have no POS: their home is the Seller Centre.
    if request.user.is_management_user() and request.pharmacy.business_type in ('ONLINE_SELLER', 'REAL_ESTATE'):
        return redirect('marketplace:seller_dashboard')

    today = timezone.localdate()
    month_start = today.replace(day=1)
    pharmacy = request.pharmacy

    if request.user.is_management_user():
        # Same source as Reports / Cash Control (business_date, voided sales excluded, payment-method split),
        # so the Today cards can never disagree with the Report Builder for the same day.
        _today = _profit_summary(pharmacy, today, today)
        today_sales_total = _today['sales']
        today_profit_total = _today['gross_profit']
        today_breakdown = {
            'cash': _today['cash_sales'], 'mobile': _today['mobile_sales'],
            'credit': _today['credit_sales'], 'credit_paid_now': _today['credit_deposits'],
            'debt_collected': _today['debt_collected'],
            # Money actually received today: cash + mobile + deposits paid at the till on credit sales
            # + repayments of earlier debts - refunds. Credit sales are NOT money until paid.
            'received': _today['cash_received'],
            'received_sales': _today['cash_sales'] + _today['mobile_sales'] + _today['credit_deposits'],
        }
        # Composition bar for the "Today's money flow" panel (display only; percentages of gross money in).
        _parts = [
            ('cash', 'Cash', _today['cash_sales'] + _today['returned_cash']),
            ('mobile', 'Mobile money', _today['mobile_sales'] + _today['returned_mobile']),
            ('deposit', 'Credit deposits', _today['deposits_received']),
            ('debt', 'Old debts paid', _today['debt_collected']),
            ('layaway_cash', 'Layaway cash instalments', _today['layaway_cash_received']),
            ('layaway_mobile', 'Layaway mobile instalments', _today['layaway_mobile_received']),
        ]
        # _parts are money IN before refunds (returned / voided sales included, as in Cash Control); refunds are shown
        # separately and net = gross - refunds, so the same money is never deducted twice.
        _gross_in = sum((amt for _k, _l, amt in _parts), Decimal('0'))
        _refunds_total = max(_gross_in - _today['cash_received'], Decimal('0'))
        # The legend shows what is LEFT per tender after refunds, so it adds up to the big "Money received" figure:
        # refunds that left the drawer come off Cash, every other refund (mobile money) comes off Mobile money.
        from expenses.ledger import cash_refunds_qs, cash_refund_amount
        _cash_ref = (cash_refunds_qs(pharmacy.id).filter(decided_at__date=today)
                     .aggregate(v=Sum(cash_refund_amount()))['v'] or Decimal('0')) + _today['layaway_cash_refunds']
        _cash_ref = min(_cash_ref, _refunds_total)
        _other_ref = _refunds_total - _cash_ref
        _net_parts = [(k, l, amt - (_cash_ref if k == 'cash' else _other_ref if k == 'mobile' else Decimal('0')))
                      for k, l, amt in _parts]
        _pos_total = sum((max(amt, Decimal('0')) for _k, _l, amt in _net_parts), Decimal('0'))
        today_breakdown['parts'] = [
            {'key': k, 'label': l, 'amount': amt,
             'pct': (float(max(amt, Decimal('0')) / _pos_total * 100) if _pos_total > 0 else 0)}
            for k, l, amt in _net_parts
        ]
        today_breakdown['refunds'] = _refunds_total
        # Only refunds for sales of EARLIER days need explaining on the card: a same-day return has already left today's
        # sales, so it is not what makes received money lower than sales.
        today_breakdown['refunds_earlier'] = (
            (SaleReturnRequest.objects.filter(
                pharmacy=pharmacy, status=SaleReturnRequest.Status.APPROVED, decided_at__date=today,
                sale__business_date__lt=today,
            ).aggregate(v=Coalesce(Sum('refund_due'), Decimal('0'), output_field=MONEY))['v'] or Decimal('0'))
            + _today['layaway_cash_refunds'] + _today['layaway_mobile_refunds'])
        # Display only: money that really came in today (before refunds) and what is left after refunds.
        # 'received' stays NET so it keeps matching Reports / Cash Control; the card shows the two separately.
        today_breakdown['gross_received'] = _gross_in
        today_breakdown['net_received'] = _today['cash_received']
        today_breakdown['net_abs'] = abs(today_breakdown['net_received'])
        # Sales / Profit cards follow the manager's saved period (default: this month).
        from .performance import card_summary
        card = card_summary(request.user, pharmacy, today)
        month_sales_total = card['sales']
        month_profit_total = card['profit']

        today_transaction_count = _today['transactions']

        _, workforce_outside_count, workforce_stale_count = _workforce_presence(pharmacy, today)
        alerts = _inventory_alerts(pharmacy, today)
        cash_ctx = _cash_and_debt_context(pharmacy, today)
        # Cash card: sales AFTER today's returns, and only refunds for sales of earlier days. A sale returned the same
        # day nets to zero, so it is not shown as a sale and a refund (opening + sales - refunds still = drawer).
        _cd = cash_ctx.get('today_cash_day')
        cash_card = _cash_card(_cd.expected_breakdown, _today['returned_cash']) if _cd else None

        try:
            from .onboarding import onboarding_checklist
            _onboarding = onboarding_checklist(request.user, pharmacy)
        except Exception:   # a welcome card must never break the dashboard
            _onboarding = None
        context = {
            'onboarding': _onboarding,
            'cash_card': cash_card,
            'workforce_outside_count': workforce_outside_count,
            'workforce_stale_count': workforce_stale_count,
            'today_sales_total': today_sales_total,
            'today_profit_total': today_profit_total,
            'month_sales_total': month_sales_total,
            'month_profit_total': month_profit_total,
            'card': card,
            'today_transaction_count': today_transaction_count,
            'today_breakdown': today_breakdown,
            **alerts,
            **cash_ctx,
        }
        return render(request, 'dashboard/admin_dashboard.html', context)

    else:
        my_sales_today = Sale.objects.filter(pharmacy=pharmacy, served_by=request.user, business_date=today).exclude(status=Sale.Status.VOIDED)
        from accounts.meal_services import ensure_ledgers_for_workday, finalize_workday_meals, sales_exist
        attendance = AttendanceRecord.objects.filter(employee=request.user, date=today).first()
        workday = MealWorkday.objects.filter(employee=request.user, date=today).first()
        _reopen_qs = WorkdayReopenRequest.objects.filter(employee=request.user, work_date=today)
        _reopen_req = _reopen_qs.first()  # newest first (Meta ordering)
        _reopen_count = _reopen_qs.count()
        if attendance and attendance.is_verified and workday:
            ensure_ledgers_for_workday(workday)
            finalize_workday_meals(workday, now=timezone.now())
        meal_qs = MealLedger.objects.filter(employee=request.user, date=today)
        from accounts.meal_services import meal_board_for
        from accounts.models import MealIssueReport
        meal_board = meal_board_for(request.user, timezone.now(), attendance) if attendance and attendance.is_verified else []
        board_by_type = {m['meal_type']: m for m in meal_board}
        _all_today = list(meal_qs.order_by('cutoff_at'))
        # Settled meals (Earned / Meal Debt / Missed) leave "Today's Meal Status" and live in Meal History.
        today_meals = [x for x in _all_today if x.status == MealLedger.Status.PENDING]
        settled_today_count = len(_all_today) - len(today_meals)
        _now = timezone.now()
        for _ledger in today_meals:
            _item = board_by_type.get(_ledger.meal_type)
            _ledger.board_state = _item['state'] if _item else ''
            _ledger.can_scan_now = bool(_item and _item['can_scan'])
            _cut = timezone.localtime(_ledger.cutoff_at).strftime('%H:%M')
            if _ledger.claimed_at:
                _ledger.status_note = (f'Scan recorded. Settles automatically at {_cut} once branch sales are confirmed.'
                                       if _now < _ledger.cutoff_at else
                                       'Scan recorded. Settles as soon as a completed branch sale or the workday close is recorded.')
            elif _ledger.board_state == 'REVIEW':
                _ledger.status_note = 'Your issue report is awaiting manager review.'
            elif _ledger.board_state == 'OPEN' and _item:
                _ledger.status_note = f"Scan the branch QR before {timezone.localtime(_item['end']).strftime('%H:%M')} to claim."
            elif _ledger.board_state == 'UPCOMING' and _item:
                _ledger.status_note = f"Opens at {timezone.localtime(_item['start']).strftime('%H:%M')}."
            else:
                _ledger.status_note = 'Waiting for the meal window and branch sales.'
        my_items_today = SaleItem.objects.filter(sale__in=my_sales_today)
        my_total_today, _my_cost_today, _my_units_today = _sum_items(my_items_today)
        pending_expenses_count = Expense.objects.filter(pharmacy=pharmacy, submitted_by=request.user, status=Expense.Status.PENDING).count()
        context = {
            'pending_expenses_count': pending_expenses_count,
            'my_sales_count_today': my_sales_today.count(),
            'my_total_today': my_total_today,
            'meal_workday': workday,
            'reopen_request': _reopen_req,
            'reopen_attempts_left': max(0, 2 - _reopen_count),
            'attendance': attendance,
            'attendance_methods': request.user.allowed_attendance_methods,
            'today_meals': today_meals,
            'settled_today_count': settled_today_count,
            'meal_board': meal_board,
            'meal_issue_reasons': [c for c in MealIssueReport.Reason.choices if c[0] != 'BUSY_AT_WORK'],
            'meal_late_reasons': [c for c in MealIssueReport.Reason.choices if c[0] in ('BUSY_AT_WORK', 'NETWORK_PROBLEM', 'DEVICE_PROBLEM', 'OTHER')],
            'today_meal_earned': sum((x.amount for x in meal_qs.filter(status=MealLedger.Status.EARNED)), Decimal('0')),
            'today_meal_debt': sum((x.amount for x in meal_qs.filter(status=MealLedger.Status.DEBT)), Decimal('0')),
            'today_meal_awaiting': sum((x.amount for x in meal_qs.filter(status=MealLedger.Status.PENDING, claimed_at__isnull=False)), Decimal('0')),
            'today_branch_has_sales': sales_exist(request.user, today),
        }
        from expenses.ledger import employee_cash_reference
        _ref = employee_cash_reference(pharmacy.pk, today, request.user)
        context['my_cash_ref'] = _ref[0] if _ref else None
        return render(request, 'dashboard/employee_dashboard.html', context)


def _compute_payroll_breakdown(pharmacy, start, end):
    """
    Day-by-day: sales, gross profit, staff meal cost (deducted from that day's
    sales), and net profit after meals. Salary is NOT included in this daily
    calculation — it's a fixed monthly figure, shown separately as reference.
    """
    active_employees = list(User.objects.filter(pharmacy=pharmacy, role=User.Role.EMPLOYEE, is_active=True))

    # One grouped query for the whole range instead of one query PER DAY.
    # The old version ran _sales_between(pharmacy, cursor, cursor) inside the
    # while-loop below — for a 30-day month that's 30 separate queries, each
    # pulling every SaleItem for that day into Python to sum. With millions
    # of sale rows across a busy pharmacy, that's the difference between one
    # fast aggregate query and dozens of slow full scans.
    daily_totals = {
        row['day']: row
        for row in _sales_between(pharmacy, start, end)
        .annotate(day=TruncDate('sale__created_at'))
        .values('day')
        .annotate(
            day_sales=Coalesce(Sum(ExpressionWrapper(F('unit_price') * F('quantity'), output_field=MONEY)), Decimal('0'), output_field=MONEY),
            day_cost=Coalesce(Sum(ExpressionWrapper(F('unit_cost') * F('quantity'), output_field=MONEY)), Decimal('0'), output_field=MONEY),
        )
    }

    daily_rows = []
    total_sales = Decimal('0')
    total_gross_profit = Decimal('0')
    total_meal_cost = Decimal('0')
    total_meal_debt = Decimal('0')
    total_morning_cost = Decimal('0')
    total_afternoon_cost = Decimal('0')
    total_evening_cost = Decimal('0')

    cursor = start
    while cursor <= end:
        row = daily_totals.get(cursor)
        day_sales = row['day_sales'] if row else Decimal('0')
        day_profit = (row['day_sales'] - row['day_cost']) if row else Decimal('0')

        # Only EARNED meals are deducted from sales. Fixed salary is never
        # touched. Meal debts are tracked separately and do not reduce today's
        # net profit because no meal was actually funded/paid from sales.
        ledgers = MealLedger.objects.filter(employee__in=active_employees, date=cursor)
        day_morning_cost = sum((x.amount for x in ledgers.filter(meal_type=MealLedger.MealType.BREAKFAST, status=MealLedger.Status.EARNED)), Decimal('0'))
        day_afternoon_cost = sum((x.amount for x in ledgers.filter(meal_type=MealLedger.MealType.LUNCH, status=MealLedger.Status.EARNED)), Decimal('0'))
        day_evening_cost = sum((x.amount for x in ledgers.filter(meal_type=MealLedger.MealType.DINNER, status=MealLedger.Status.EARNED)), Decimal('0'))
        day_debt = sum((x.amount for x in ledgers.filter(status=MealLedger.Status.DEBT)), Decimal('0'))
        day_meal_cost = day_morning_cost + day_afternoon_cost + day_evening_cost
        day_net_profit = day_profit - day_meal_cost

        daily_rows.append({
            'date': cursor, 'sales': day_sales, 'gross_profit': day_profit,
            'morning_cost': day_morning_cost, 'afternoon_cost': day_afternoon_cost,
            'evening_cost': day_evening_cost, 'meal_cost': day_meal_cost,
            'meal_debt': day_debt, 'net_profit': day_net_profit,
        })
        total_sales += day_sales
        total_gross_profit += day_profit
        total_meal_cost += day_meal_cost
        total_meal_debt += day_debt
        total_morning_cost += day_morning_cost
        total_afternoon_cost += day_afternoon_cost
        total_evening_cost += day_evening_cost
        cursor += timedelta(days=1)

    total_monthly_salary = sum((emp.monthly_salary or Decimal('0')) for emp in active_employees)

    return {
        'daily_rows': daily_rows,
        'total_sales': total_sales,
        'total_gross_profit': total_gross_profit,
        'total_meal_cost': total_meal_cost,
        'total_meal_debt': total_meal_debt,
        'total_morning_cost': total_morning_cost,
        'total_afternoon_cost': total_afternoon_cost,
        'total_evening_cost': total_evening_cost,
        'total_net_profit': total_gross_profit - total_meal_cost,
        'total_monthly_salary': total_monthly_salary,
        'employee_count': len(active_employees),
    }


@admin_required
@admin_required
def payroll_report_view(request):
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')

    pharmacy = request.pharmacy
    today = timezone.localdate()
    try:
        year = int(request.GET.get('year', today.year))
        month = int(request.GET.get('month', today.month))
        if month < 1 or month > 12:
            raise ValueError
    except (TypeError, ValueError):
        year, month = today.year, today.month

    start, end, is_current_month = _resolve_calendar_month(year, month, today)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_run':
            try:
                run, created = PayrollRun.objects.get_or_create(
                    pharmacy=pharmacy, period_start=start, period_end=end,
                    defaults={'created_by': request.user},
                )
                if created or not run.items.exists():
                    employees = User.objects.filter(pharmacy=pharmacy, role=User.Role.EMPLOYEE, is_active=True).order_by('first_name', 'last_name', 'username')
                    for employee in employees:
                        meal_total = MealLedger.objects.filter(
                            pharmacy=pharmacy, employee=employee, date__gte=start, date__lte=end,
                            status=MealLedger.Status.EARNED,
                        ).aggregate(total=Coalesce(Sum('amount'), Decimal('0')))['total'] or Decimal('0')
                        attendance_days = AttendanceRecord.objects.filter(
                            pharmacy=pharmacy, employee=employee, date__gte=start, date__lte=end,
                            status__in=[AttendanceRecord.Status.VERIFIED, AttendanceRecord.Status.CLOSED],
                        ).values('date').distinct().count()
                        PayrollItem.objects.get_or_create(
                            payroll=run, employee=employee,
                            defaults={
                                'basic_salary': employee.monthly_salary or Decimal('0'),
                                'earned_meals': meal_total,
                                'attendance_days': attendance_days,
                                'working_days': (end - start).days + 1,
                            },
                        )
                messages.success(request, f'Payroll run ready for {start:%B %Y}.')
            except Exception as exc:
                messages.error(request, f'Payroll could not be created: {exc}')
            return redirect(f'{request.path}?year={year}&month={month}')

        run_id = request.POST.get('run_id')
        run = PayrollRun.objects.filter(pk=run_id, pharmacy=pharmacy).first() if run_id else None
        if not run:
            messages.error(request, 'Payroll run was not found.')
            return redirect(f'{request.path}?year={year}&month={month}')

        if action == 'save_items' and run.status == PayrollRun.Status.DRAFT:
            for item in run.items.select_related('employee'):
                def money(name):
                    try:
                        return max(Decimal('0'), Decimal(request.POST.get(f'{name}_{item.pk}', '0') or '0'))
                    except Exception:
                        return Decimal('0')
                item.other_allowances = money('allowance')
                item.tax_deduction = money('tax')
                item.other_deductions = money('deduction')
                item.note = request.POST.get(f'note_{item.pk}', '')[:2000]
                item.save(update_fields=['other_allowances', 'tax_deduction', 'other_deductions', 'note'])
            messages.success(request, 'Payroll adjustments saved.')

        elif action == 'approve' and run.status == PayrollRun.Status.DRAFT:
            run.status = PayrollRun.Status.APPROVED
            run.approved_by = request.user
            run.approved_at = timezone.now()
            run.save(update_fields=['status', 'approved_by', 'approved_at'])
            messages.success(request, 'Payroll approved. It is now locked for payment.')

        elif action == 'mark_paid' and run.status == PayrollRun.Status.APPROVED:
            if run.period_end > today:
                messages.error(request, 'This payroll period is still in progress. It cannot be marked paid before the period ends.')
                return redirect(f'{request.path}?year={year}&month={month}')
            run.status = PayrollRun.Status.PAID
            run.paid_by = request.user
            run.paid_at = timezone.now()
            run.save(update_fields=['status', 'paid_by', 'paid_at'])
            messages.success(request, 'Payroll marked as paid successfully.')

        return redirect(f'{request.path}?year={year}&month={month}')

    data = _compute_payroll_breakdown(pharmacy, start, end)
    run = PayrollRun.objects.filter(pharmacy=pharmacy, period_start=start, period_end=end).prefetch_related('items__employee').first()
    payroll_items = list(run.items.all()) if run else []
    payroll_items_page = Paginator(payroll_items, 25).get_page(request.GET.get('payroll_page'))
    payroll_gross = sum((x.gross_pay for x in payroll_items), Decimal('0'))
    payroll_deductions = sum((x.total_deductions for x in payroll_items), Decimal('0'))
    payroll_net = sum((x.net_pay for x in payroll_items), Decimal('0'))

    prev_month = month - 1 or 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    has_next = (next_year, next_month) <= (today.year, today.month)

    return render(request, 'dashboard/payroll_report.html', {
        **data, 'payroll_run': run, 'payroll_items': payroll_items_page, 'payroll_items_page': payroll_items_page,
        'payroll_gross': payroll_gross, 'payroll_deductions': payroll_deductions, 'payroll_net': payroll_net,
        'period_start': start, 'period_end': end, 'selected_year': year, 'selected_month': month,
        'month_label': start.strftime('%B %Y'), 'is_current_month': is_current_month,
        'prev_year': prev_year, 'prev_month': prev_month, 'next_year': next_year, 'next_month': next_month, 'has_next': has_next,
    })


def _resolve_calendar_month(year, month, today):
    """
    Returns (start, end, is_current_month) for a real calendar month —
    never fabricating figures for days that haven't happened yet. If the
    requested month is the current one, end is capped at today (not the
    last day of the month, since those days have no sales yet). Any other
    month uses its full, real start-to-end range.
    """
    start = date_cls(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    natural_end = date_cls(year, month, last_day)

    is_current_month = (year, month) == (today.year, today.month)
    end = today if is_current_month else natural_end
    if end > today:
        end = today  # never show days that haven't happened yet, even for a "future" selection
    return start, end, is_current_month


@admin_required
def export_payroll_report_pdf(request):
    pharmacy = request.pharmacy
    today = timezone.localdate()

    try:
        year = int(request.GET.get('year', today.year))
        month = int(request.GET.get('month', today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month

    start, end, _ = _resolve_calendar_month(year, month, today)
    period_title = f"Monthly Payroll & Meal Cost Report — {start:%B %Y}"

    data = _compute_payroll_breakdown(pharmacy, start, end)

    rows = [[
        r['date'].strftime('%d %b %Y (%a)'),
        f"{r['sales']:,.0f}", f"{r['gross_profit']:,.0f}",
        f"{r['morning_cost']:,.0f}", f"{r['afternoon_cost']:,.0f}", f"{r['evening_cost']:,.0f}",
        f"{r['meal_cost']:,.0f}", f"{r['meal_debt']:,.0f}", f"{r['net_profit']:,.0f}",
    ] for r in data['daily_rows']]

    payroll_run = PayrollRun.objects.filter(pharmacy=pharmacy, period_start=start, period_end=end).prefetch_related('items').first()
    payroll_net = sum((x.net_pay for x in payroll_run.items.all()), Decimal('0')) if payroll_run else Decimal('0')
    payroll_status = payroll_run.get_status_display() if payroll_run else 'Not created'

    summary = [
        ("Branch", pharmacy.name if pharmacy else '-'),
        ("Payroll Run Status", payroll_status),
        ("Payroll Net Pay", f"TZS {payroll_net:,.0f}"),
        ("Report Month", f"{start:%B %Y}"),
        ("Days Covered", f"{start:%d %b %Y} — {end:%d %b %Y}"),
        ("Active Employees", data['employee_count']),
        ("Total Sales", f"TZS {data['total_sales']:,.0f}"),
        ("Total Gross Profit", f"TZS {data['total_gross_profit']:,.0f}"),
        ("Total Morning Tea Cost", f"TZS {data['total_morning_cost']:,.0f}"),
        ("Total Lunch Cost", f"TZS {data['total_afternoon_cost']:,.0f}"),
        ("Total Dinner Cost", f"TZS {data['total_evening_cost']:,.0f}"),
        ("Total Earned Staff Meal Cost", f"TZS {data['total_meal_cost']:,.0f}"),
        ("Total Meal Debt", f"TZS {data['total_meal_debt']:,.0f}"),
        ("Net Profit (after meals)", f"TZS {data['total_net_profit']:,.0f}"),
        ("Total Monthly Salary Obligation (reference only, not deducted above)",
         f"TZS {data['total_monthly_salary']:,.0f}"),
    ]

    return generate_pdf_report(
        title=period_title,
        subtitle=f"{pharmacy.name if pharmacy else ''} — {start:%d %B %Y} to {end:%d %B %Y}",
        headers=["Date", "Sales (TZS)", "Gross Profit (TZS)", "Morning (TZS)", "Lunch (TZS)",
                 "Dinner (TZS)", "Earned Meals (TZS)", "Meal Debt (TZS)", "Net Profit (TZS)"],
        rows=rows, summary=summary, filename=f"payroll_meals_report_{year}_{month:02d}.pdf",
        column_widths=[24, 22, 24, 18, 18, 18, 22, 22, 24],
    )


def _best_sellers(pharmacy, start, end, limit=5):
    """Top-selling products by quantity within [start, end], with revenue for context."""
    qs = (
        SaleItem.objects.filter(sale__pharmacy=pharmacy, sale__business_date__gte=start, sale__business_date__lte=end)
        .values('product__name')
        .annotate(total_qty=Sum('quantity'), total_revenue=Sum(F('quantity') * F('unit_price')))
        .order_by('-total_qty')[:limit]
    )
    return list(qs)


DEMAND_LOOKBACK_DAYS = 14


def _demand_estimation(pharmacy, limit=15):
    """
    For each product with recent sales, estimates how many days of stock are
    left at its current average daily sales pace — 'demand estimation' the
    owner can act on: what's about to run out fastest, not just what's
    already below its manually-set reorder level. Sorted most-urgent first;
    products with no sales in the lookback window are skipped since there's
    no sales pace to estimate from.
    """
    today = timezone.localdate()
    lookback_start = today - timedelta(days=DEMAND_LOOKBACK_DAYS - 1)

    sold_rows = (
        SaleItem.objects.filter(
            sale__pharmacy=pharmacy, sale__business_date__gte=lookback_start, sale__business_date__lte=today,
        )
        .values('product_id')
        .annotate(total_qty=Sum('quantity'))
    )
    sold_map = {row['product_id']: row['total_qty'] for row in sold_rows}

    estimation = []
    for product in Product.objects.filter(pharmacy=pharmacy, is_active=True, pk__in=sold_map.keys()):
        total_sold = sold_map[product.id]
        avg_daily = total_sold / DEMAND_LOOKBACK_DAYS
        current_stock = product.total_quantity
        days_remaining = (current_stock / avg_daily) if avg_daily > 0 else None
        estimation.append({
            'product': product,
            'avg_daily_sales': round(avg_daily, 1),
            'current_stock': current_stock,
            'days_remaining': round(days_remaining, 1) if days_remaining is not None else None,
        })

    estimation.sort(key=lambda e: (e['days_remaining'] is None, e['days_remaining']))
    return estimation[:limit]


@admin_required
def reports_view(request):
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')

    pharmacy = request.pharmacy
    today = timezone.localdate()
    start = today - timedelta(days=29)  # last 30 days

    # Single grouped query for the whole 30-day chart instead of 30 separate
    # per-day queries (same fix as _compute_payroll_breakdown above).
    chart_daily = {
        row['day']: row['day_sales']
        for row in _sales_between(pharmacy, start, today)
        .annotate(day=TruncDate('sale__created_at'))
        .values('day')
        .annotate(day_sales=Coalesce(Sum(ExpressionWrapper(F('unit_price') * F('quantity'), output_field=MONEY)), Decimal('0'), output_field=MONEY))
    }
    daily_totals = []
    labels = []
    cursor = start
    while cursor <= today:
        daily_totals.append(float(chart_daily.get(cursor, Decimal('0'))))
        labels.append(cursor.strftime('%d %b'))
        cursor += timedelta(days=1)

    best_sellers_qs = (
        SaleItem.objects.filter(sale__pharmacy=pharmacy, sale__business_date__gte=start)
        .values('product__name')
        .annotate(total_qty=Sum('quantity'))
        .order_by('-total_qty')[:8]
    )
    best_seller_labels = [b['product__name'] for b in best_sellers_qs]
    best_seller_values = [b['total_qty'] for b in best_sellers_qs]

    reorder_list = [p for p in Product.objects.filter(pharmacy=pharmacy, is_active=True) if p.is_low_stock]
    reorder_list.sort(key=lambda p: p.total_quantity)

    week_start = today - timedelta(days=6)
    best_sellers_today = _best_sellers(pharmacy, today, today)
    best_sellers_week = _best_sellers(pharmacy, week_start, today)
    demand_estimation = _demand_estimation(pharmacy)

    context = {
        'daily_labels': json.dumps(labels),
        'daily_values': json.dumps(daily_totals),
        'best_seller_labels': json.dumps(best_seller_labels),
        'best_seller_values': json.dumps(best_seller_values),
        'best_sellers_today': best_sellers_today,
        'best_sellers_week': best_sellers_week,
        'demand_estimation': demand_estimation,
        'demand_lookback_days': DEMAND_LOOKBACK_DAYS,
        'reorder_page_obj': Paginator(reorder_list, 15).get_page(request.GET.get('reorder_page')),
        'period_start': start,
        'period_end': today,
    }
    return render(request, 'dashboard/reports.html', context)


def _report_range(request, default_start=None):
    """Inclusive (start, end) from ?month=YYYY-MM, else ?start=&end= (any custom range)."""
    today = timezone.localdate()
    month = (request.GET.get('month') or '').strip()
    if month:
        try:
            year, mon = (int(x) for x in month.split('-')[:2])
            first = date_cls(year, mon, 1)
            return first, date_cls(year, mon, calendar.monthrange(year, mon)[1])
        except (ValueError, TypeError):
            pass
    start = _parse_report_date(request.GET.get('start'), default_start or today)
    end = _parse_report_date(request.GET.get('end'), today)
    if start > end:
        start, end = end, start
    return start, end


@admin_required
def custom_reports_view(request):
    """Professional report builder for daily, weekly, monthly and custom ranges."""
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')
    pharmacy = request.pharmacy
    today = timezone.localdate()
    start, end = _report_range(request)
    summary = _profit_summary(pharmacy, start, end)

    # Same function the PDF/Excel exports use, so screen and exports always agree.
    from .range_report import period_cash_control, expense_rows, meal_rows, expense_by_category, layaway_period_summary
    cash_control = period_cash_control(pharmacy, start, end)
    expense_list = expense_rows(pharmacy, start, end)
    meal_list = meal_rows(pharmacy, start, end)

    # Professional cash view: meals paid from the drawer are taken off cash sales, per day and in total.
    from expenses.ledger import CashLedgerService
    drawer_days = CashLedgerService.daily_totals(pharmacy.pk, start, end)
    daily_rows = summary['daily_rows']
    for row in daily_rows:
        t = drawer_days.get(row['date'])
        meals_out = t.cash_meals if t else Decimal('0')
        row['cash_meals'] = meals_out
        row['cash_after_meals'] = row['cash_sales'] - meals_out
    cash_control['cash_after_meals'] = cash_control['cash_sales'] - cash_control['meals']

    compare_start = _parse_report_date(request.GET.get('compare_start'), None)
    compare_end = _parse_report_date(request.GET.get('compare_end'), None)
    comparison = None
    if compare_start and compare_end:
        if compare_start > compare_end:
            compare_start, compare_end = compare_end, compare_start
        comparison = _profit_summary(pharmacy, compare_start, compare_end)
        comparison['sales_change'] = comparison['sales'] - summary['sales']
        comparison['cost_change'] = comparison['cost'] - summary['cost']
        comparison['margin_change'] = comparison['margin'] - summary['margin']
        comparison['profit_change'] = comparison['profit'] - summary['profit']
        comparison['profit_change_pct'] = ((comparison['profit'] - summary['profit']) / summary['profit'] * Decimal('100')) if summary['profit'] else None
        comparison['net_profit_change'] = comparison['net_profit'] - summary['net_profit']
        comparison['cash_received_change'] = comparison['cash_received'] - summary['cash_received']
        comparison['meals_change'] = comparison['meals'] - summary['meals']
        comparison['expenses_change'] = comparison['expenses'] - summary['expenses']

    return render(request, 'dashboard/custom_reports.html', {
        'summary': summary, 'comparison': comparison, 'today': today,
        'form_start': start, 'form_end': end,
        'daily_rows': daily_rows,
        'compare_start': compare_start, 'compare_end': compare_end,
        'cash_control': cash_control,
        'expense_list': expense_list, 'expense_categories': expense_by_category(expense_list),
        'layaway': layaway_period_summary(pharmacy, start, end),
        'meal_list': meal_list, 'selected_month': request.GET.get('month', ''),
    })


@admin_required
def export_custom_sales_report_pdf(request):
    """Sales + profit for a month (?month=YYYY-MM) or any inclusive range (?start=&end=):
    daily summary, expenses, staff meals, items sold and expected cash after deductions."""
    from .range_report import build_range_report_pdf
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')
    pharmacy = request.pharmacy
    start, end = _report_range(request)
    return build_range_report_pdf(pharmacy, start, end, _profit_summary(pharmacy, start, end))


@admin_required
def export_custom_sales_report_excel(request):
    """Same analysis as the PDF as an Excel workbook (one sheet per section)."""
    from .range_report import build_range_report_xlsx
    if not _require_pharmacy(request):
        return redirect('pharmacies:list')
    pharmacy = request.pharmacy
    start, end = _report_range(request)
    return build_range_report_xlsx(pharmacy, start, end, _profit_summary(pharmacy, start, end))


@admin_required
def export_profit_comparison_pdf(request):
    """Export a side-by-side comparison of two manager-selected periods."""
    pharmacy = request.pharmacy
    today = timezone.localdate()
    a_start = _parse_report_date(request.GET.get('a_start'), today)
    a_end = _parse_report_date(request.GET.get('a_end'), today)
    b_start = _parse_report_date(request.GET.get('b_start'), today)
    b_end = _parse_report_date(request.GET.get('b_end'), today)
    if a_start > a_end: a_start, a_end = a_end, a_start
    if b_start > b_end: b_start, b_end = b_end, b_start
    a = _profit_summary(pharmacy, a_start, a_end)
    b = _profit_summary(pharmacy, b_start, b_end)

    def pct_change(old, new):
        return ((new - old) / abs(old) * Decimal('100')) if old else None

    rows = [
        ['Revenue / Sales (TZS)', f'{a["sales"]:,.0f}', f'{b["sales"]:,.0f}', f'{b["sales"]-a["sales"]:,.0f}', f'{pct_change(a["sales"], b["sales"]):.1f}%' if a['sales'] else '—'],
        ['Cost of Goods Sold (TZS)', f'{a["cost"]:,.0f}', f'{b["cost"]:,.0f}', f'{b["cost"]-a["cost"]:,.0f}', f'{pct_change(a["cost"], b["cost"]):.1f}%' if a['cost'] else '—'],
        ['Gross Profit (TZS)', f'{a["profit"]:,.0f}', f'{b["profit"]:,.0f}', f'{b["profit"]-a["profit"]:,.0f}', f'{pct_change(a["profit"], b["profit"]):.1f}%' if a['profit'] else '—'],
        ['Gross Margin', f'{a["margin"]:.1f}%', f'{b["margin"]:.1f}%', f'{b["margin"]-a["margin"]:.1f} pp', '—'],
        ['Transactions', f'{a["transactions"]:,}', f'{b["transactions"]:,}', f'{b["transactions"]-a["transactions"]:,}', f'{pct_change(Decimal(a["transactions"]), Decimal(b["transactions"])):.1f}%' if a['transactions'] else '—'],
        ['Units Sold', f'{a["units"]:,}', f'{b["units"]:,}', f'{b["units"]-a["units"]:,}', f'{pct_change(Decimal(a["units"]), Decimal(b["units"] )):.1f}%' if a['units'] else '—'],
    ]
    summary = [('Branch', pharmacy.name if pharmacy else '-'),
               ('Period A', f'{a_start:%d %B %Y} — {a_end:%d %B %Y}'),
               ('Period B', f'{b_start:%d %B %Y} — {b_end:%d %B %Y}')]
    return generate_pdf_report(
        title='Profit Comparison Report',
        subtitle=f'{pharmacy.name if pharmacy else ""} — Period A vs Period B',
        headers=['Metric', 'Period A', 'Period B', 'Difference', 'Change'],
        rows=rows, summary=summary, filename=f'profit_comparison_{a_start}_{a_end}_vs_{b_start}_{b_end}.pdf',
        column_widths=[55, 30, 30, 30, 25],
    )


@admin_required
def export_sales_report_pdf(request):
    pharmacy = request.pharmacy
    period = request.GET.get('period', 'week')
    today = timezone.localdate()

    if period == 'month':
        start = today.replace(day=1)
        period_title = f"Monthly Sales Report — {start:%B %Y}"
    else:
        period = 'week'
        start = today - timedelta(days=6)
        period_title = "Weekly Sales Report"

    # Keep one canonical inclusive date range for every section of the PDF.
    # In particular, payroll lookup must use the same `end` used by the sales
    # query; otherwise the export can crash with NameError or mismatch periods.
    end = today

    sales = (
        Sale.objects.filter(pharmacy=pharmacy, business_date__gte=start, business_date__lte=today)
        .exclude(status=Sale.Status.VOIDED)
        .select_related('served_by').prefetch_related('items')
        .order_by('business_date', 'created_at')
    )

    rows = []
    total_sales = Decimal('0')
    total_profit = Decimal('0')
    for sale in sales:
        amount = sale.total_amount
        profit = sale.total_profit
        total_sales += amount
        total_profit += profit
        served_by = sale.served_by.get_full_name() or sale.served_by.username if sale.served_by else '-'
        rows.append([
            f"#{sale.sale_number or sale.id}",
            (f"{sale.business_date:%d %b %Y} (late entry)" if sale.is_late_entry
             else timezone.localtime(sale.created_at).strftime('%d %b %Y, %H:%M')), served_by,
            sale.payment_summary, f"{amount:,.0f}",
        ])

    payroll_run = PayrollRun.objects.filter(pharmacy=pharmacy, period_start=start, period_end=end).prefetch_related('items').first()
    payroll_net = sum((x.net_pay for x in payroll_run.items.all()), Decimal('0')) if payroll_run else Decimal('0')
    payroll_status = payroll_run.get_status_display() if payroll_run else 'Not created'

    summary = [
        ("Branch", pharmacy.name if pharmacy else '-'),
        ("Payroll Run Status", payroll_status),
        ("Payroll Net Pay", f"TZS {payroll_net:,.0f}"),
        ("Report Period", f"{start:%d %B %Y} — {today:%d %B %Y}"),
        ("Total Transactions", sales.count()),
        ("Total Sales", f"TZS {total_sales:,.0f}"),
        ("Total Profit", f"TZS {total_profit:,.0f}"),
    ]

    return generate_pdf_report(
        title=period_title,
        subtitle=f"{pharmacy.name if pharmacy else ''} — {start:%d %B %Y} to {today:%d %B %Y}",
        headers=["Sale #", "Date & Time", "Served By", "Payment", "Amount (TZS)"],
        rows=rows, summary=summary, filename=f"sales_report_{period}_{today}.pdf",
        column_widths=[16, 38, 42, 32, 34],
    )


@admin_required
def export_products_report_pdf(request):
    """Lists every individual batch (not just an aggregate) so the price/stock picture stays batch-accurate."""
    pharmacy = request.pharmacy
    batches = (
        ProductBatch.objects.filter(product__pharmacy=pharmacy, product__is_active=True, quantity__gt=0)
        .select_related('product', 'product__category').order_by('product__name', F('expiry_date').asc(nulls_last=True))
    )

    rows = []
    total_qty = 0
    weighted_buy_sum = Decimal('0')
    weighted_sell_sum = Decimal('0')
    for b in batches:
        rows.append([
            b.product.name,
            b.batch_number or '-',
            b.product.category.name if b.product.category else '-',
            f"{b.purchase_price:,.0f}",
            f"{b.selling_price:,.0f}",
            str(b.quantity),
            b.expiry_date.strftime('%d %b %Y') if b.expiry_date else '—',
        ])
        total_qty += b.quantity
        weighted_buy_sum += b.purchase_price * b.quantity
        weighted_sell_sum += b.selling_price * b.quantity

    # PWA — Price Weighted Average: the average buy/sell price across all batches,
    # weighted by how many units are in each batch (not a simple average of prices,
    # which would misrepresent a large batch the same as a tiny leftover one).
    pwa_buy = (weighted_buy_sum / total_qty) if total_qty else Decimal('0')
    pwa_sell = (weighted_sell_sum / total_qty) if total_qty else Decimal('0')

    today = timezone.localdate()

    summary = [
        ("Branch", pharmacy.name if pharmacy else '-'),
        ("Report Date", today.strftime('%d %B %Y')),
        ("Total Batches Listed", batches.count()),
        ("Price Weighted Average (PWA) — Buy", f"TZS {pwa_buy:,.0f} per unit"),
        ("Price Weighted Average (PWA) — Sell", f"TZS {pwa_sell:,.0f} per unit"),
    ]

    return generate_pdf_report(
        title="Products & Price List (by Batch)",
        subtitle=f"{pharmacy.name if pharmacy else ''} — complete stock as of {today:%d %B %Y}",
        headers=["Product", "Batch No.", "Category", "Buy Price", "Sell Price", "Qty", "Expiry Date"],
        rows=rows, summary=summary, filename=f"products_price_list_{today}.pdf",
        column_widths=[38, 24, 24, 22, 22, 14, 26],
    )


@admin_required
def export_profit_report_pdf(request):
    """Stock valuation and potential profit, broken down per batch for full traceability."""
    pharmacy = request.pharmacy
    batches = (
        ProductBatch.objects.filter(product__pharmacy=pharmacy, product__is_active=True, quantity__gt=0)
        .select_related('product').order_by('product__name', F('expiry_date').asc(nulls_last=True))
    )

    rows = []
    total_cost_value = Decimal('0')
    total_potential_profit = Decimal('0')
    for b in batches:
        cost_value = b.purchase_price * b.quantity
        potential_profit = (b.selling_price - b.purchase_price) * b.quantity
        total_cost_value += cost_value
        total_potential_profit += potential_profit
        rows.append([
            b.product.name, b.batch_number or '-', str(b.quantity),
            f"{b.purchase_price:,.0f}", f"{b.selling_price:,.0f}",
            f"{cost_value:,.0f}", f"{potential_profit:,.0f}",
        ])

    today = timezone.localdate()

    summary = [
        ("Branch", pharmacy.name if pharmacy else '-'),
        ("Report Date", today.strftime('%d %B %Y')),
        ("Total Stock Cost Value", f"TZS {total_cost_value:,.0f}"),
        ("Total Potential Profit (if all stock sold)", f"TZS {total_potential_profit:,.0f}"),
    ]

    return generate_pdf_report(
        title="Stock & Profit Report (by Batch)",
        subtitle=f"{pharmacy.name if pharmacy else ''} — current stock valuation as of {today:%d %B %Y}",
        headers=["Product", "Batch No.", "Qty", "Buy Price", "Sell Price", "Stock Value", "Potential Profit"],
        rows=rows, summary=summary, filename=f"stock_profit_report_{today}.pdf",
        column_widths=[36, 22, 16, 22, 22, 26, 30],
    )



@login_required
def my_cash_history_view(request):
    """V54.44: day-by-day expected cash (cash sales - meal money) for one employee, with CSV export.
    Employees see only themselves; management may open any employee of the branch with ?employee=<id>."""
    from django.http import HttpResponse
    from django.shortcuts import get_object_or_404
    from expenses.ledger import employee_cash_history
    import csv
    if request.user.is_employee_role():
        employee = request.user
    else:
        employee = get_object_or_404(User, pk=request.GET.get('employee'), role=User.Role.EMPLOYEE,
                                     pharmacy=request.pharmacy)
    pharmacy = employee.pharmacy or request.pharmacy
    today = timezone.localdate()

    def _d(value, default):
        try:
            return date_cls.fromisoformat((value or '').strip())
        except ValueError:
            return default

    end = min(_d(request.GET.get('to'), today), today)
    start = _d(request.GET.get('from'), end - timedelta(days=29))
    if start > end:
        start, end = end, start
    if (end - start).days > 92:
        start = end - timedelta(days=92)
    rows = employee_cash_history(pharmacy.pk, employee, start, end)
    totals = {
        'cash_sales': sum((r['cash_sales'] for r in rows), Decimal('0')),
        'cash_refunds': sum((r['cash_refunds'] for r in rows), Decimal('0')),
        'meal_earned': sum((r['meal_earned'] for r in rows), Decimal('0')),
        'meal_not_deducted': sum((r['meal_not_deducted'] for r in rows), Decimal('0')),
    }
    totals['expected'] = totals['cash_sales'] - totals['cash_refunds'] - totals['meal_earned']
    name = employee.get_full_name() or employee.get_username()

    export = (request.GET.get('export') or '').strip().lower()
    # Employees must receive PDF only, including legacy CSV URLs/bookmarks.
    if export == 'csv' and request.user.is_employee_role():
        params = request.GET.copy()
        params['export'] = 'pdf'
        return redirect(f'{request.path}?{params.urlencode()}')

    if export == 'pdf':
        period = f'{start:%d %b %Y} — {end:%d %b %Y}'
        pdf_rows = [[
            f"{r['date']:%d %b %Y}", str(r['sales_count']),
            f"{r['cash_sales']:,.0f}", f"{r['cash_refunds']:,.0f}", f"{r['meal_earned']:,.0f}",
            f"{r['expected']:,.0f}", f"{r['meal_not_deducted']:,.0f}",
        ] for r in rows]
        pdf_rows.append(['TOTAL', '', f"{totals['cash_sales']:,.0f}", f"{totals['cash_refunds']:,.0f}", f"{totals['meal_earned']:,.0f}",
                         f"{totals['expected']:,.0f}", f"{totals['meal_not_deducted']:,.0f}"])
        summary = [
            ('Employee', name), ('Branch', pharmacy.name if pharmacy else '-'), ('Period', period),
            ('Cash sales', f"TZS {totals['cash_sales']:,.0f}"),
            ('Refunds handed back', f"TZS {totals['cash_refunds']:,.0f}"),
            ('Meal money deducted', f"TZS {totals['meal_earned']:,.0f}"),
            ('Expected cash', f"TZS {totals['expected']:,.0f}"),
            ('Meals not yet deducted', f"TZS {totals['meal_not_deducted']:,.0f}"),
        ]
        return generate_pdf_report(
            title='Expected Cash History',
            subtitle=f'{name} — {period}. Reference report; branch drawer is verified by management.',
            headers=['Business date', 'Sales count', 'Cash sales (TZS)', 'Refunds (TZS)', 'Meal deducted (TZS)',
                     'Expected cash (TZS)', 'Meal not deducted (TZS)'],
            rows=pdf_rows, summary=summary,
            filename=f'expected-cash-{employee.pk}-{start}-{end}.pdf',
            column_widths=[25, 14, 26, 24, 26, 28, 28],
        )

    if export == 'csv':
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="expected-cash-{employee.pk}-{start}-{end}.csv"'
        w = csv.writer(resp)
        w.writerow(['Employee', name, 'From', start, 'To', end])
        w.writerow(['Date', 'Cash sales (count)', 'Cash sales (TZS)', 'Refunds (TZS)', 'Meal deducted (TZS)',
                    'Expected cash (TZS)', 'Meal not yet deducted (TZS)'])
        for r in rows:
            w.writerow([r['date'], r['sales_count'], f"{r['cash_sales']:.0f}", f"{r['cash_refunds']:.0f}",
                        f"{r['meal_earned']:.0f}", f"{r['expected']:.0f}", f"{r['meal_not_deducted']:.0f}"])
        w.writerow(['TOTAL', '', f"{totals['cash_sales']:.0f}", f"{totals['cash_refunds']:.0f}", f"{totals['meal_earned']:.0f}",
                    f"{totals['expected']:.0f}", f"{totals['meal_not_deducted']:.0f}"])
        return resp

    page = Paginator(rows, 15).get_page(request.GET.get('page'))
    return render(request, 'dashboard/my_cash_history.html', {
        'employee': employee, 'employee_name': name, 'page_obj': page, 'totals': totals,
        'date_from': start.isoformat(), 'date_to': end.isoformat(), 'today': today,
        'is_self': employee == request.user,
    })
