from collections import defaultdict
from decimal import Decimal
from urllib.parse import quote

from django.db.models import Q, F
from django.urls import reverse
from django.utils import timezone

from expenses.models import Expense
from pharmacies.models import Pharmacy
from .models import DailyReportSubscription
from sales.models import Sale, SaleItem, Debt, DebtPayment, SaleReturnRequest


def premium_daily_reports_enabled(owner):
    """Return True when Intelligent Reports are included or separately subscribed."""
    sub = getattr(owner, 'subscription', None)
    if sub and sub.status == 'ACTIVE' and not sub.is_expired:
        if getattr(sub, 'premium_daily_reports_enabled', False) or sub.tier in {'PROFESSIONAL', 'ENTERPRISE'}:
            return True
    return DailyReportSubscription.objects.filter(
        owner=owner, status=DailyReportSubscription.Status.ACTIVE, report_sms_credits__gt=F('report_sms_used')
    ).exists()


def owner_branches(owner):
    return list(owner.managed_pharmacies.filter(is_active=True).order_by('name'))


def _money(value):
    return Decimal(value or 0)


def _sum(qs, field):
    from django.db.models import Sum
    return qs.aggregate(total=Sum(field))['total'] or Decimal('0')


def _short_money(value):
    """Compact TZS amount for SMS: 1.25M, 286K, 950."""
    value = Decimal(value or 0)
    sign = '-' if value < 0 else ''
    v = abs(value)
    if v >= 1_000_000:
        out = f'{v / 1_000_000:.2f}'.rstrip('0').rstrip('.') + 'M'
    elif v >= 1_000:
        out = f'{v / 1_000:.0f}K'
    else:
        out = f'{v:.0f}'
    return sign + out


MONEY_KEYS = (
    'gross_sales', 'returns_total', 'net_sales', 'cogs', 'gross_profit', 'expenses', 'cash_expenses',
    'meals_cost', 'meals_unfunded', 'salary_accrual', 'estimated_net_profit', 'discount_total',
    'cash_sales', 'mobile_sales', 'credit_sales', 'credit_deposits', 'debt_created', 'debt_collected', 'deposits_received',
    'refunds_prior_days', 'returns_cash_in', 'refunds_paid_today', 'cash_after_costs', 'debt_outstanding',
    'layaway_cash_received', 'layaway_mobile_received', 'layaway_bank_received', 'layaway_other_received',
    'layaway_cash_refunds', 'layaway_mobile_refunds',
)


def _branch_report(pharmacy, report_date, include_salary=False):
    from accounts.models import MealLedger, User

    sales = list(
        Sale.objects.filter(pharmacy=pharmacy, business_date=report_date)
        .exclude(status=Sale.Status.VOIDED)
        .prefetch_related('items__product__category', 'debt__payments')
        .select_related('served_by')
    )
    credit_deposits = Decimal('0')
    gross_sales = net_sales = cogs = discount_total = Decimal('0')
    quantities = 0
    payment = defaultdict(lambda: Decimal('0'))
    cashier_sales = defaultdict(lambda: {'sales': Decimal('0'), 'count': 0})
    product_sales = defaultdict(lambda: {'qty': 0, 'sales': Decimal('0'), 'profit': Decimal('0')})
    category_sales = defaultdict(lambda: Decimal('0'))
    hourly = [{'hour': h, 'sales': Decimal('0'), 'count': 0} for h in range(24)]
    sale_times = []
    largest_sale = Decimal('0')
    from layaway.models import LayawayPlan
    handover_sale_ids = set(LayawayPlan.objects.filter(pharmacy=pharmacy, sale_id__isnull=False).values_list('sale_id', flat=True))

    for sale in sales:
        sale_net = Decimal('0')
        for item in sale.items.all():
            sale_net += item.subtotal
            gross_sales += item.original_subtotal
            cogs += item.unit_cost * item.quantity
            quantities += item.quantity
            discount_total += item.discount_amount
            row = product_sales[item.product.name]
            row['qty'] += item.quantity
            row['sales'] += item.subtotal
            row['profit'] += item.profit
            category_sales[item.product.category.name if item.product.category_id else 'Uncategorised'] += item.subtotal
        net_sales += sale_net
        # Late entries (paper-notebook sales typed in later) keep their day's totals but must not
        # distort the busiest-hour statistics, which describe when customers really bought.
        if not getattr(sale, 'is_late_entry', False):
            local = timezone.localtime(sale.created_at)
            hourly[local.hour]['sales'] += sale_net
            hourly[local.hour]['count'] += 1
            sale_times.append(local)
        largest_sale = max(largest_sale, sale_net)
        # The linked handover sale records product fulfilment/revenue, not a new payment.
        # Its instalments were counted on the dates they were actually received.
        if sale.pk in handover_sale_ids:
            pass
        elif sale.payment_method == Sale.PaymentMethod.SPLIT and (sale.cash_paid + sale.mobile_paid) > 0:
            cash_share = (sale_net * sale.cash_paid / (sale.cash_paid + sale.mobile_paid))
            payment[Sale.PaymentMethod.CASH] += cash_share
            payment[Sale.PaymentMethod.MOBILE_MONEY] += sale_net - cash_share
        elif sale.payment_method == Sale.PaymentMethod.DEBT:
            # Credit sale: what the customer paid at the till is money received; only the rest is credit.
            paid_now = min(sale.paid_at_sale, sale_net)
            payment[Sale.PaymentMethod.DEBT] += sale_net - paid_now
            if paid_now > 0:
                payment['CREDIT_PAID_AT_SALE'] += paid_now
                credit_deposits += paid_now
        else:
            payment[sale.payment_method] += sale_net
        staff = (sale.served_by.get_full_name() or sale.served_by.username) if sale.served_by else 'Unknown'
        cashier_sales[staff]['sales'] += sale_net
        cashier_sales[staff]['count'] += 1

    returns_total = gross_sales - net_sales          # value returned/voided on this day's sales
    gross_profit = net_sales - cogs
    cash_sales = payment.get(Sale.PaymentMethod.CASH, Decimal('0'))
    mobile_sales = payment.get(Sale.PaymentMethod.MOBILE_MONEY, Decimal('0'))
    credit_sales = payment.get(Sale.PaymentMethod.DEBT, Decimal('0'))

    # Refunds paid today for sales made on EARLIER days (cash really leaves today).
    refunds_prior = _sum(SaleReturnRequest.objects.filter(
        pharmacy=pharmacy, status=SaleReturnRequest.Status.APPROVED,
        decided_at__date=report_date, sale__business_date__lt=report_date,
    ), 'refund_due')

    # Same money rules as the Dashboard / Reports / Cash Control (one source: dashboard.views._financial_daily_rows).
    # Net sales above already exclude anything returned or voided, but every refund paid today is deducted in full,
    # so the cash/mobile value of today's returned sales is shown as money in first - otherwise it is deducted twice.
    from dashboard.views import _financial_daily_rows
    _fin = _financial_daily_rows(pharmacy, report_date, report_date)[0]
    returns_cash_in = _fin['returned_cash'] + _fin['returned_mobile']
    refunds_paid_today = _fin.get('refunds', Decimal('0'))

    # Expenses (approved only)
    expenses_qs = Expense.objects.filter(pharmacy=pharmacy, expense_date=report_date, status=Expense.Status.APPROVED)
    expense_total = _sum(expenses_qs, 'amount')
    cash_expenses = _sum(expenses_qs.filter(payment_source=Expense.PaymentSource.CASH_SALES), 'amount')
    by_cat = defaultdict(lambda: Decimal('0'))
    for e in expenses_qs.select_related('category'):
        by_cat[e.category.name] += e.amount
    expense_categories = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:5]

    # Employee meals: EARNED = funded by the day's sales = a real operating cost.
    meals = MealLedger.objects.filter(pharmacy=pharmacy, date=report_date)
    meals_cost = _sum(meals.filter(status=MealLedger.Status.EARNED), 'amount')
    meals_count = meals.filter(status=MealLedger.Status.EARNED).count()
    meals_unfunded = _sum(meals.filter(status=MealLedger.Status.DEBT), 'amount')
    meals_unfunded_count = meals.filter(status=MealLedger.Status.DEBT).count()
    meals_missed_count = meals.filter(status=MealLedger.Status.MISSED).count()

    # Optional daily salary accrual (monthly salary / days in month). Off by default.
    salary_accrual = Decimal('0')
    if include_salary:
        import calendar
        days = calendar.monthrange(report_date.year, report_date.month)[1]
        monthly = _sum(User.objects.filter(pharmacy=pharmacy, is_active=True, monthly_salary__gt=0), 'monthly_salary')
        salary_accrual = (monthly / days).quantize(Decimal('1'))

    # Debts
    debts_created = _sum(Debt.objects.filter(pharmacy=pharmacy, sale__business_date=report_date).exclude(sale__status=Sale.Status.VOIDED), 'amount_owed')
    day_payments = DebtPayment.objects.filter(pharmacy=pharmacy, paid_at__date=report_date)
    # Repayments of earlier credit vs. deposits paid at the till on a credit sale: never mixed together.
    debt_collected = _sum(day_payments.exclude(kind=DebtPayment.Kind.DEPOSIT), 'amount')
    deposits_received = _sum(day_payments.filter(kind=DebtPayment.Kind.DEPOSIT), 'amount')
    open_debts = list(Debt.objects.filter(pharmacy=pharmacy, is_settled=False))
    debt_outstanding = sum((d.balance for d in open_debts), Decimal('0'))
    by_customer = defaultdict(lambda: Decimal('0'))
    for d in open_debts:
        by_customer[d.customer_name.strip().title() or 'Unknown'] += d.balance
    top_debtors = sorted(by_customer.items(), key=lambda x: x[1], reverse=True)[:5]

    # Layaway receipts are tracked separately from sale revenue. The handover sale is
    # fulfilment of an already-funded plan and must not be presented as a second receipt.
    from layaway.models import LayawayPlan, LayawayPayment, LayawayRefund
    lp = LayawayPayment.objects.filter(plan__pharmacy=pharmacy, status=LayawayPayment.Status.RECEIVED, received_at__date=report_date)
    lr = LayawayRefund.objects.filter(plan__pharmacy=pharmacy, refunded_at__date=report_date)
    layaway_cash_received = _sum(lp.filter(method=LayawayPayment.Method.CASH), 'amount')
    layaway_mobile_received = _sum(lp.filter(method=LayawayPayment.Method.MOBILE), 'amount')
    layaway_bank_received = _sum(lp.filter(method=LayawayPayment.Method.BANK), 'amount')
    layaway_other_received = _sum(lp.filter(method=LayawayPayment.Method.OTHER), 'amount')
    layaway_cash_refunds = _sum(lr.filter(method=LayawayRefund.Method.CASH), 'amount')
    layaway_mobile_refunds = _sum(lr.filter(method=LayawayRefund.Method.MOBILE), 'amount')
    layaway_created_count = LayawayPlan.objects.filter(pharmacy=pharmacy, created_at__date=report_date).count()
    layaway_handover_count = LayawayPlan.objects.filter(pharmacy=pharmacy, handed_over_at__date=report_date).count()
    cash_after_costs = (cash_sales + mobile_sales + deposits_received + debt_collected
                        + layaway_cash_received + layaway_mobile_received
                        - layaway_cash_refunds - layaway_mobile_refunds
                        + returns_cash_in - refunds_paid_today
                        - cash_expenses - meals_cost)
    net_profit = gross_profit - expense_total - meals_cost - salary_accrual

    top_products = sorted(product_sales.items(), key=lambda x: (x[1]['sales'], x[1]['qty']), reverse=True)[:10]
    top_staff = sorted(cashier_sales.items(), key=lambda x: x[1]['sales'], reverse=True)

    return {
        'branch_id': pharmacy.id, 'branch_name': pharmacy.name, 'location': pharmacy.location,
        'transactions': len(sales), 'units_sold': quantities,
        'gross_sales': str(gross_sales), 'returns_total': str(returns_total), 'net_sales': str(net_sales),
        'cogs': str(cogs), 'gross_profit': str(gross_profit),
        'expenses': str(expense_total), 'cash_expenses': str(cash_expenses),
        'meals_cost': str(meals_cost), 'meals_count': meals_count,
        'meals_unfunded': str(meals_unfunded), 'meals_unfunded_count': meals_unfunded_count, 'meals_missed_count': meals_missed_count,
        'salary_accrual': str(salary_accrual),
        'estimated_net_profit': str(net_profit),
        'discount_total': str(discount_total),
        'cash_sales': str(cash_sales), 'mobile_sales': str(mobile_sales), 'credit_sales': str(credit_sales),
        'credit_deposits': str(credit_deposits),
        'debt_created': str(debts_created), 'debt_collected': str(debt_collected),
        'deposits_received': str(deposits_received),
        'debt_outstanding': str(debt_outstanding), 'debt_open_count': len(open_debts),
        'refunds_prior_days': str(refunds_prior), 'returns_cash_in': str(returns_cash_in),
        'refunds_paid_today': str(refunds_paid_today), 'cash_after_costs': str(cash_after_costs),
        'layaway_cash_received': str(layaway_cash_received), 'layaway_mobile_received': str(layaway_mobile_received),
        'layaway_bank_received': str(layaway_bank_received), 'layaway_other_received': str(layaway_other_received),
        'layaway_cash_refunds': str(layaway_cash_refunds), 'layaway_mobile_refunds': str(layaway_mobile_refunds),
        'layaway_created_count': layaway_created_count, 'layaway_handover_count': layaway_handover_count,
        'payment_methods': {k: str(v) for k, v in payment.items()},
        'first_sale': min(sale_times).strftime('%H:%M') if sale_times else '',
        'last_sale': max(sale_times).strftime('%H:%M') if sale_times else '',
        'largest_sale': str(largest_sale),
        'hourly': [dict(hour=h['hour'], sales=str(h['sales']), count=h['count']) for h in hourly],
        'categories': [dict(name=n, sales=str(v)) for n, v in sorted(category_sales.items(), key=lambda x: x[1], reverse=True)[:6]],
        'expense_categories': [dict(name=n, amount=str(v)) for n, v in expense_categories],
        'top_debtors': [dict(name=n, balance=str(v)) for n, v in top_debtors],
        'top_products': [dict(name=n, qty=v['qty'], sales=str(v['sales']), profit=str(v['profit'])) for n, v in top_products],
        'staff_performance': [dict(name=n, sales=str(v['sales']), transactions=v['count']) for n, v in top_staff[:10]],
    }


def _rollup(branch_data):
    totals = {k: Decimal('0') for k in MONEY_KEYS}
    ints = {k: 0 for k in ('transactions', 'units_sold', 'meals_count', 'meals_unfunded_count', 'meals_missed_count', 'debt_open_count')}
    payment = defaultdict(lambda: Decimal('0'))
    products = defaultdict(lambda: {'qty': 0, 'sales': Decimal('0'), 'profit': Decimal('0')})
    categories = defaultdict(lambda: Decimal('0'))
    debtors = defaultdict(lambda: Decimal('0'))
    staff = defaultdict(lambda: {'sales': Decimal('0'), 'transactions': 0})
    cats = defaultdict(lambda: Decimal('0'))
    hours = [{'hour': h, 'sales': Decimal('0'), 'count': 0} for h in range(24)]
    firsts = [b['first_sale'] for b in branch_data if b['first_sale']]
    lasts = [b['last_sale'] for b in branch_data if b['last_sale']]
    for b in branch_data:
        for h in b['hourly']:
            hours[h['hour']]['sales'] += _money(h['sales']); hours[h['hour']]['count'] += h['count']
        for c in b['categories']: cats[c['name']] += _money(c['sales'])
        for k in totals: totals[k] += _money(b.get(k, '0'))
        for k in ints: ints[k] += b[k]
        for k, v in b['payment_methods'].items(): payment[k] += _money(v)
        for p in b['top_products']:
            r = products[p['name']]; r['qty'] += p['qty']; r['sales'] += _money(p['sales']); r['profit'] += _money(p['profit'])
        for c in b['expense_categories']: categories[c['name']] += _money(c['amount'])
        for d in b['top_debtors']: debtors[d['name']] += _money(d['balance'])
        for s in b['staff_performance']:
            r = staff[(b['branch_name'], s['name'])]; r['sales'] += _money(s['sales']); r['transactions'] += s['transactions']
    top_products = sorted(products.items(), key=lambda x: (x[1]['sales'], x[1]['qty']), reverse=True)[:10]
    day_total = sum((h['sales'] for h in hours), Decimal('0'))
    active = [h for h in hours if h['count']]
    timeline, peak = [], None
    if active:
        lo, hi = active[0]['hour'], active[-1]['hour']
        biggest = max(h['sales'] for h in hours) or Decimal('1')
        for h in hours[lo:hi + 1]:
            timeline.append({'label': f"{h['hour']:02d}:00-{(h['hour'] + 1) % 24:02d}:00", 'hour': h['hour'], 'sales': str(h['sales']), 'count': h['count'],
                             'bar': int(h['sales'] / biggest * 100), 'share': float(h['sales'] / day_total * 100) if day_total else 0.0})
        peak = max(timeline, key=lambda x: Decimal(x['sales']))
    cat_total = sum(cats.values(), Decimal('0'))
    avg_sale = (day_total / ints['transactions']) if ints['transactions'] else Decimal('0')
    return {
        **{k: str(v) for k, v in totals.items()}, **ints,
        'first_sale': min(firsts) if firsts else '', 'last_sale': max(lasts) if lasts else '',
        'avg_sale': str(avg_sale.quantize(Decimal('1'))),
        'largest_sale': str(max((_money(b['largest_sale']) for b in branch_data), default=Decimal('0'))),
        'timeline': timeline, 'peak_hour': peak,
        'categories': [dict(name=n, sales=str(v), share=float(v / cat_total * 100) if cat_total else 0.0) for n, v in sorted(cats.items(), key=lambda x: x[1], reverse=True)[:6]],
        'payment_methods': {k: str(v) for k, v in payment.items()},
        'top_products': [dict(name=n, qty=v['qty'], sales=str(v['sales']), profit=str(v['profit'])) for n, v in top_products],
        'expense_categories': [dict(name=n, amount=str(v)) for n, v in sorted(categories.items(), key=lambda x: x[1], reverse=True)[:5]],
        'top_debtors': [dict(name=n, balance=str(v)) for n, v in sorted(debtors.items(), key=lambda x: x[1], reverse=True)[:5]],
        'top_staff': [dict(branch=k[0], name=k[1], sales=str(v['sales']), transactions=v['transactions'])
                      for k, v in sorted(staff.items(), key=lambda x: x[1]['sales'], reverse=True)[:5]],
    }


def _pct_change(now, before):
    before = _money(before)
    if before == 0:
        return None
    return float(((_money(now) - before) / before) * 100)


def _comparison(summary, other):
    """Compare today's summary with another day's summary (None when that day had no sales)."""
    if not other or not other.get('transactions'):
        return None
    return {
        'net_sales': other['net_sales'], 'net_sales_pct': _pct_change(summary['net_sales'], other['net_sales']),
        'profit': other['estimated_net_profit'], 'profit_pct': _pct_change(summary['estimated_net_profit'], other['estimated_net_profit']),
        'transactions': other['transactions'], 'transactions_pct': _pct_change(summary['transactions'], other['transactions']),
    }


def _fmt_pct(p):
    return 'n/a' if p is None else f'{p:+.0f}%'


def build_insights(snapshot):
    """Rule-based analysis written for the business owner. Most important first."""
    s = snapshot['summary']
    branches = snapshot['branches']
    out = []

    def add(level, title, text):
        out.append({'level': level, 'title': title, 'text': text})

    net_sales, profit = _money(s['net_sales']), _money(s['estimated_net_profit'])
    gross_sales, credit = _money(s['gross_sales']), _money(s['credit_sales'])
    money = lambda v: f'TZS {_money(v):,.0f}'

    if not s['transactions']:
        add('warn', 'No sales recorded', 'No completed sales were recorded for this day across your branches. If the shop was open, check that sales were entered.')
    else:
        if profit < 0:
            add('alert', 'Operating loss', f'After goods cost, expenses and meals the day closed at {money(profit)}. Costs were higher than the gross profit of {money(s["gross_profit"])}.')
        elif net_sales > 0:
            margin = float(profit / net_sales * 100)
            add('good' if margin >= 10 else 'info', f'Net margin {margin:.0f}%',
                f'Of every TZS 100 sold, about TZS {margin:.0f} stayed as profit after goods, expenses and meals.')

        peak = s.get('peak_hour')
        if peak and peak['share'] >= 1:
            add('info', f'Busiest hour: {peak["label"]}', f'{money(peak["sales"])} ({peak["share"]:.0f}% of the day\'s sales) from {peak["count"]} sales. Trading ran from {s["first_sale"]} to {s["last_sale"]}.')
            hrs = [t for t in s['timeline'] if t['count'] == 0 or float(_money(t['sales'])) < float(net_sales) / max(len(s['timeline']), 1) * 0.25]
            if len(s['timeline']) >= 5 and hrs:
                add('info', 'Slow hours', 'Little or no selling at ' + ', '.join(t['label'] for t in hrs[:4]) + '. Consider staffing or promotions around these times.')
        if _money(s['avg_sale']) > 0:
            add('info', f'Average sale TZS {_money(s["avg_sale"]):,.0f}', f'Largest single sale was {money(s["largest_sale"])}.')
        cmp_y, cmp_w = snapshot.get('vs_yesterday'), snapshot.get('vs_last_week')
        if cmp_y and cmp_y['net_sales_pct'] is not None:
            p = cmp_y['net_sales_pct']
            add('good' if p >= 5 else 'warn' if p <= -15 else 'info', f'Sales {_fmt_pct(p)} vs previous day',
                f'{money(net_sales)} today against {money(cmp_y["net_sales"])} the day before; profit {_fmt_pct(cmp_y["profit_pct"])}.')
        if cmp_w and cmp_w['net_sales_pct'] is not None:
            p = cmp_w['net_sales_pct']
            add('good' if p >= 5 else 'warn' if p <= -15 else 'info', f'Sales {_fmt_pct(p)} vs same weekday last week',
                f'Last week the same weekday made {money(cmp_w["net_sales"])}.')

        if net_sales > 0 and credit > 0:
            share = float(credit / net_sales * 100)
            paid_now = net_sales - credit
            add('warn' if share >= 30 else 'info', f'{share:.0f}% of sales were on credit',
                f'Only {money(paid_now)} of the {money(net_sales)} sales was paid at the counter; {money(credit)} is still owed by customers.')

        if gross_sales > 0:
            rp = float(_money(s['returns_total']) / gross_sales * 100)
            if rp >= 5:
                add('warn', f'Returns are {rp:.1f}% of sales', f'{money(s["returns_total"])} of sold items were returned or voided. Review the reasons in Returns & Voids.')

        exp, gp = _money(s['expenses']), _money(s['gross_profit'])
        if exp > 0 and gp > 0 and float(exp / gp) >= 0.3:
            top = s['expense_categories'][0] if s['expense_categories'] else None
            extra = f' Largest: {top["name"]} ({money(top["amount"])}).' if top else ''
            add('warn', 'Expenses are heavy', f'Expenses took {float(exp / gp * 100):.0f}% of the gross profit.{extra}')

        if s['products_top_share'] is not None and s['products_top_share'] >= 40 and s['top_products']:
            add('info', 'Sales depend on one product', f'{s["top_products"][0]["name"]} made {s["products_top_share"]:.0f}% of the day\'s sales.')

        if len(branches) > 1:
            ranked = sorted(branches, key=lambda b: _money(b['net_sales']), reverse=True)
            best, worst = ranked[0], ranked[-1]
            add('info', f'Best branch: {best["branch_name"]}', f'{money(best["net_sales"])} sales, profit {money(best["estimated_net_profit"])}. Lowest: {worst["branch_name"]} with {money(worst["net_sales"])}.')
            for b in branches:
                if _money(b['estimated_net_profit']) < 0 and b['transactions']:
                    add('alert', f'{b["branch_name"]} made a loss', f'Net result {money(b["estimated_net_profit"])} after expenses and meals.')

    debt_out = _money(s['debt_outstanding'])
    if debt_out > 0:
        top = s['top_debtors'][0] if s['top_debtors'] else None
        who = f' Largest: {top["name"]} ({money(top["balance"])}).' if top else ''
        created, collected = _money(s['debt_created']) - _money(s['credit_deposits']), _money(s['debt_collected'])
        trend = 'Debts grew today.' if created > collected else 'More was collected than lent today.' if collected > created else ''
        add('warn' if debt_out > net_sales * 3 and net_sales > 0 else 'info', f'Customers owe you {money(debt_out)}',
            f'{s["debt_open_count"]} open debts. New today {money(created)}, collected {money(collected)}. {trend}{who}'.strip())

    if _money(s['meals_unfunded']) > 0:
        add('warn', 'Meals not funded', f'{s["meals_unfunded_count"]} meals ({money(s["meals_unfunded"])}) stayed unfunded because the branch had no completed sale.')
    if s['meals_missed_count']:
        add('info', f'{s["meals_missed_count"]} meals not claimed', 'Employees did not scan these meals in time, so they were not charged.')

    order = {'alert': 0, 'warn': 1, 'good': 2, 'info': 3}
    out.sort(key=lambda x: order[x['level']])
    return out[:11]


def build_daily_snapshot(owner, report_date, with_comparison=True):
    from datetime import timedelta
    settings_obj = getattr(owner, 'daily_report_settings', None)
    include_salary = bool(getattr(settings_obj, 'include_salary_accrual', False))
    branches = owner_branches(owner)
    branch_data = [_branch_report(b, report_date, include_salary) for b in branches]
    summary = _rollup(branch_data)
    net = _money(summary['net_sales'])
    top_share = None
    if net > 0 and summary['top_products']:
        top_share = float(_money(summary['top_products'][0]['sales']) / net * 100)
    summary['products_top_share'] = top_share

    snapshot = {
        'report_date': report_date.isoformat(),
        'generated_timezone': str(timezone.get_current_timezone()),
        'owner_id': owner.id,
        'branch_count': len(branch_data),
        'include_salary': include_salary,
        'branches': branch_data,
        'summary': summary,
    }
    if with_comparison:
        prev = build_daily_snapshot(owner, report_date - timedelta(days=1), with_comparison=False)['summary']
        week = build_daily_snapshot(owner, report_date - timedelta(days=7), with_comparison=False)['summary']
        snapshot['vs_yesterday'] = _comparison(summary, prev)
        snapshot['vs_last_week'] = _comparison(summary, week)
        snapshot['insights'] = build_insights(snapshot)
    return snapshot


def build_sms_message(snapshot, report_url):
    s = snapshot['summary']
    from datetime import date as _date
    d = _date.fromisoformat(snapshot['report_date'])
    parts = [f"SmartPOS {d:%d %b}"]
    sales = f"Sales {_short_money(s['net_sales'])}"
    cmp_y = snapshot.get('vs_yesterday')
    if cmp_y and cmp_y.get('net_sales_pct') is not None:
        sales += f" ({_fmt_pct(cmp_y['net_sales_pct'])})"
    parts += [sales, f"Profit {_short_money(s['estimated_net_profit'])}", f"Cash {_short_money(s['cash_after_costs'])}"]
    if _money(s['debt_outstanding']) > 0:
        parts.append(f"Debts owed {_short_money(s['debt_outstanding'])}")
    msg = '\n'.join(parts)
    alerts = [i for i in snapshot.get('insights', []) if i['level'] in ('alert', 'warn')]
    if alerts:
        msg += f"\nNote: {alerts[0]['title']}"
    return f"{msg}\nFull report: {report_url}"


def approve_daily_report_purchase(purchase, *, reviewed_by=None, reference='', admin_note=''):
    """Activate or top-up an Intelligent Report SMS entitlement.

    Shared by (1) the superadmin approving a manual mobile-money payment and
    (2) a Pesapal online payment completing automatically, so both paths
    behave identically (top-ups add credits and extend validity).

    Returns the entitlement, or None when the purchase had already been handled.
    The purchase is row-locked and re-checked, so it can never be credited twice.
    """
    from django.db import transaction
    from sms_integration.services import (
        apply_package_entitlement, lock_pending_purchase, mark_purchase_approved, notify_package_activated,
    )
    from .models import DailyReportPurchase

    with transaction.atomic():
        locked = lock_pending_purchase(DailyReportPurchase, purchase)
        if locked is None:
            purchase.refresh_from_db()
            return None
        package = locked.package
        payment_reference = reference or locked.payment_reference
        # Same ordering the report sender uses to pick the entitlement it spends from.
        active, _topped_up = apply_package_entitlement(
            DailyReportSubscription, locked.owner, package,
            credits_field='report_sms_credits', credits=package.report_sms_credits,
            amount=locked.amount or package.price, reference=payment_reference,
            order_by=('-created_at', '-id'),
        )
        mark_purchase_approved(locked, reviewed_by, payment_reference, admin_note=admin_note)
    purchase.refresh_from_db()

    notify_package_activated(
        purchase.owner, title='Intelligent Reports activated',
        message=(f'{package.name} is now active. Remaining report SMS: {active.credits_remaining:,}. '
                 'These SMS do not expire - use them until they finish.'),
        link_name='hq:packages_center',
    )
    return active
