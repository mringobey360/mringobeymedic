"""Database layer of the Performance Monitor.

Uses exactly the same SaleItem filter and money maths as the dashboard cards
(`dashboard.views._profit_summary`, business_date based since V117.1) so a card and the monitor can
never disagree for the same period. Two aggregate queries per period, however
long the range is.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import DatabaseError, transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from sales.models import Sale, SaleItem

from . import perf_core as pc
from .models import DashboardPreference

logger = logging.getLogger(__name__)
MONEY = DecimalField(max_digits=14, decimal_places=2)


def _bounds(start, end):
    tz = timezone.get_current_timezone()
    mn = timezone.datetime.min.time()
    return (
        timezone.make_aware(timezone.datetime.combine(start, mn), tz),
        timezone.make_aware(timezone.datetime.combine(end + timedelta(days=1), mn), tz),
    )


def get_preference(user):
    """Saved preference, or an unsaved default (never creates a row on read)."""
    try:
        # Savepoint, so a database problem here can never poison the request's transaction.
        with transaction.atomic():
            pref = DashboardPreference.objects.filter(user=user).first()
    except DatabaseError:
        logger.exception('Could not load dashboard preference for user %s; using the default monthly view.', getattr(user, 'pk', None))
        pref = None
    return pref or DashboardPreference(user=user)


def period_for(pref, today=None):
    today = today or timezone.localdate()
    start, end, label = pc.resolve_period(pref.mode, pref.last_n, pref.custom_start, pref.custom_end, today)
    return start, end, label


def daily_rows(pharmacy, start, end):
    """{date: {'sales','cost','units','tx'}} for an inclusive range."""
    # V117.1: business_date + non-voided, identical to Reports / Cash Control / the Today cards.
    items = (
        SaleItem.objects.filter(sale__pharmacy=pharmacy, sale__business_date__gte=start, sale__business_date__lte=end)
        .exclude(sale__status=Sale.Status.VOIDED)
        .annotate(day=F('sale__business_date'))
        .values('day')
        .annotate(
            sales=Coalesce(Sum(ExpressionWrapper(F('unit_price') * F('quantity'), output_field=MONEY)), Decimal('0'), output_field=MONEY),
            cost=Coalesce(Sum(ExpressionWrapper(F('unit_cost') * F('quantity'), output_field=MONEY)), Decimal('0'), output_field=MONEY),
            units=Coalesce(Sum('quantity'), 0),
        )
    )
    daily = {r['day']: {'sales': r['sales'], 'cost': r['cost'], 'units': r['units'], 'tx': 0} for r in items}
    counts = (
        Sale.objects.filter(pharmacy=pharmacy, business_date__gte=start, business_date__lte=end)
        .exclude(status=Sale.Status.VOIDED)
        .annotate(day=F('business_date'))
        .values('day')
        .annotate(n=Count('id'))
    )
    for r in counts:
        row = daily.setdefault(r['day'], {'sales': Decimal('0'), 'cost': Decimal('0'), 'units': 0, 'tx': 0})
        row['tx'] = r['n']
    return daily


def period_totals(pharmacy, start, end):
    """Cheap totals for the dashboard cards (no per-day work)."""
    agg = SaleItem.objects.filter(
        sale__pharmacy=pharmacy, sale__business_date__gte=start, sale__business_date__lte=end
    ).exclude(sale__status=Sale.Status.VOIDED).aggregate(
        sales=Coalesce(Sum(ExpressionWrapper(F('unit_price') * F('quantity'), output_field=MONEY)), Decimal('0'), output_field=MONEY),
        cost=Coalesce(Sum(ExpressionWrapper(F('unit_cost') * F('quantity'), output_field=MONEY)), Decimal('0'), output_field=MONEY),
    )
    tx = Sale.objects.filter(pharmacy=pharmacy, business_date__gte=start, business_date__lte=end).exclude(status=Sale.Status.VOIDED).count()
    return {'sales': agg['sales'], 'cost': agg['cost'], 'profit': agg['sales'] - agg['cost'], 'transactions': tx}


def deductions_by_day(pharmacy, start, end):
    """{date: {'meals','refunds'}}: money that left the drawer for staff meals and cash refunds, from the SAME
    ledger Cash Control uses, so this page can never disagree with it. Never breaks the page."""
    try:
        from expenses.ledger import CashLedgerService
        totals = CashLedgerService.daily_totals(pharmacy.pk, start, end)
        return {d: {'meals': t.cash_meals, 'refunds': t.cash_refunds} for d, t in totals.items()}
    except Exception:  # noqa: BLE001
        logger.exception('Could not load meal / refund deductions for the performance monitor.')
        return {}


def _attach_deductions(rows, ded):
    for b in rows:
        meals = refunds = Decimal('0')
        d = b['start']
        while d <= b['end']:
            r = ded.get(d)
            if r:
                meals += r['meals']
                refunds += r['refunds']
            d += timedelta(days=1)
        b['meals'], b['refunds'], b['deducted'] = meals, refunds, meals + refunds


def drawer_statement(pharmacy, start, end):
    """Opening -> movements -> expected cash for the period, plus the running expected cash at the end of every day.

    Same two functions Cash Control, the Report Builder and the PDF/Excel exports use, so every screen agrees.
    Returns None (and logs) if the ledger cannot be read, so the monitor page itself never breaks."""
    try:
        from .range_report import expected_cash_by_day, period_cash_control
        cash = period_cash_control(pharmacy, start, end)
        rows = expected_cash_by_day(pharmacy, start, end, cash['opening'])
        last = rows[-1]['expected'] if rows else cash['opening']
        cash['consistent'] = abs(last - cash['expected']) < Decimal('1')
        cash['cash_in_other'] = cash['debt_cash'] + cash['transfer_in']
        return cash, {r['date']: r for r in rows}
    except Exception:  # noqa: BLE001
        logger.exception('Could not build the drawer statement for the performance monitor.')
        return None


def _attach_drawer(rows, by_day):
    for b in rows:
        end_row = by_day.get(b['end'])
        b['expected'] = end_row['expected'] if end_row else None
        b['counted'] = end_row['actual'] if end_row else None
        b['variance'] = end_row['variance'] if end_row else None


def card_summary(user, pharmacy, today=None):
    """Data for the dashboard Sales / Profit cards."""
    today = today or timezone.localdate()
    pref = get_preference(user)
    start, end, label = period_for(pref, today)
    totals = period_totals(pharmacy, start, end)
    default = pref.mode == DashboardPreference.Mode.MONTH
    return {
        'start': start, 'end': end, 'label': label, 'is_default': default,
        'sales': totals['sales'], 'profit': totals['profit'], 'transactions': totals['transactions'],
        'sales_title': 'Monthly Sales' if default else f'Sales · {label}',
        'profit_title': 'Monthly Profit' if default else f'Profit · {label}',
    }


def build_report(pharmacy, pref, today=None):
    """Everything the monitor page and exports need, for the saved preference."""
    today = today or timezone.localdate()
    start, end, label = period_for(pref, today)
    interval, note = pc.effective_interval(pref.interval, start, end)
    daily = daily_rows(pharmacy, start, end)
    totals = pc.totals_from_daily(daily, start, end)
    buckets = pc.build_buckets(start, end, interval, daily)
    day_rows = buckets if interval == pc.INTERVAL_DAY else pc.build_buckets(start, end, pc.INTERVAL_DAY, daily)

    ded = deductions_by_day(pharmacy, start, end)
    _attach_deductions(buckets, ded)
    if day_rows is not buckets:
        _attach_deductions(day_rows, ded)
    totals['meals'] = sum((b['meals'] for b in buckets), Decimal('0'))
    totals['refunds'] = sum((b['refunds'] for b in buckets), Decimal('0'))
    totals['deducted'] = totals['meals'] + totals['refunds']

    drawer = drawer_statement(pharmacy, start, end)
    cash = drawer[0] if drawer else None
    if drawer:
        _attach_drawer(buckets, drawer[1])
        if day_rows is not buckets:
            _attach_drawer(day_rows, drawer[1])

    p_start, p_end = pc.previous_window(pref.mode, start, end)
    prev = period_totals(pharmacy, p_start, p_end)
    previous = {
        'start': p_start, 'end': p_end, **prev,
        'sales_pct': pc.pct_change(totals['sales'], prev['sales']),
        'profit_pct': pc.pct_change(totals['profit'], prev['profit']),
        'tx_pct': pc.pct_change(totals['transactions'], prev['transactions']),
    }
    trading = [b for b in buckets if b['tx'] or b['sales']]
    return {
        'start': start, 'end': end, 'label': label, 'mode': pref.mode,
        'interval': interval, 'interval_label': dict(DashboardPreference.Interval.choices)[interval],
        'interval_note': note, 'totals': totals, 'buckets': buckets, 'days': day_rows,
        'best': max(trading, key=lambda b: b['sales'], default=None),
        'worst': min(trading, key=lambda b: b['sales'], default=None),
        'busiest': max(trading, key=lambda b: b['tx'], default=None),
        'previous': previous, 'cash': cash,
    }
