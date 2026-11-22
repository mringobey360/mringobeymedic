"""Pure (database-free) logic for the Performance Monitor.

Everything here works on plain dates/numbers so it can be unit-tested without
Django and reused by the dashboard cards, the monitor page and the exports.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

MODE_MONTH = 'MONTH'
MODE_LAST_DAYS = 'LAST_DAYS'
MODE_LAST_WEEKS = 'LAST_WEEKS'
MODE_LAST_MONTHS = 'LAST_MONTHS'
MODE_CUSTOM = 'CUSTOM'

INTERVAL_DAY = 'DAY'
INTERVAL_WEEK = 'WEEK'
INTERVAL_MONTH = 'MONTH'

MAX_RANGE_DAYS = 731          # hard cap (2 years) so one page can never become a heavy query
MAX_DAILY_BUCKETS = 120       # beyond this, a daily breakdown is unreadable -> weekly
MAX_WEEKLY_BUCKETS = 160      # beyond this -> monthly

ZERO = Decimal('0')


def months_back(d, n):
    """Same calendar day `n` months earlier (clamped to the month length)."""
    total = d.year * 12 + (d.month - 1) - n
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def resolve_period(mode, last_n, custom_start, custom_end, today):
    """Return (start, end, label). `end` is never after today."""
    last_n = max(int(last_n or 1), 1)
    if mode == MODE_LAST_DAYS:
        n = min(last_n, MAX_RANGE_DAYS)
        start, end = today - timedelta(days=n - 1), today
        label = f'Last {n} day{"s" if n != 1 else ""}'
    elif mode == MODE_LAST_WEEKS:
        n = min(last_n, MAX_RANGE_DAYS // 7)
        start, end = today - timedelta(days=n * 7 - 1), today
        label = f'Last {n} week{"s" if n != 1 else ""}'
    elif mode == MODE_LAST_MONTHS:
        n = min(last_n, 24)
        start, end = months_back(today, n) + timedelta(days=1), today
        label = f'Last {n} month{"s" if n != 1 else ""}'
    elif mode == MODE_CUSTOM and custom_start and custom_end:
        start, end = custom_start, min(custom_end, today)
        if start > end:
            start, end = end, start
        if (end - start).days + 1 > MAX_RANGE_DAYS:
            start = end - timedelta(days=MAX_RANGE_DAYS - 1)
        label = f'{start:%d %b %Y} – {end:%d %b %Y}'
    else:  # default: current month so far
        start, end = today.replace(day=1), today
        label = f'{today:%B %Y}'
    return start, end, label


def previous_window(mode, start, end):
    """The comparison window: same length right before, or - for the default
    monthly view - the same elapsed days of the previous month."""
    length = (end - start).days + 1
    if mode == MODE_MONTH:
        prev_last = start - timedelta(days=1)
        prev_start = prev_last.replace(day=1)
        prev_end = min(prev_start + timedelta(days=length - 1), prev_last)
        return prev_start, prev_end
    prev_end = start - timedelta(days=1)
    return prev_end - timedelta(days=length - 1), prev_end


def effective_interval(interval, start, end):
    """Keep the breakdown readable. Returns (interval, note or '')."""
    days = (end - start).days + 1
    if interval == INTERVAL_DAY and days > MAX_DAILY_BUCKETS:
        return INTERVAL_WEEK, f'Daily view is limited to {MAX_DAILY_BUCKETS} days, so weekly intervals are shown.'
    if interval == INTERVAL_WEEK and days > MAX_WEEKLY_BUCKETS * 7:
        return INTERVAL_MONTH, 'Range is too long for weekly intervals, so monthly intervals are shown.'
    return interval, ''


def pct_change(now, before):
    now, before = Decimal(now or 0), Decimal(before or 0)
    if before == 0:
        return None
    return float((now - before) / before * 100)


def _blank():
    return {'sales': ZERO, 'cost': ZERO, 'units': 0, 'tx': 0}


def _bucket_bounds(d, interval, start, end):
    if interval == INTERVAL_WEEK:
        b_start = d - timedelta(days=d.weekday())            # Monday
        b_end = b_start + timedelta(days=6)
    elif interval == INTERVAL_MONTH:
        b_start = d.replace(day=1)
        b_end = d.replace(day=calendar.monthrange(d.year, d.month)[1])
    else:
        b_start = b_end = d
    return max(b_start, start), min(b_end, end)


def _bucket_label(b_start, b_end, interval):
    if interval == INTERVAL_DAY:
        return f'{b_start:%a %d %b}'
    if interval == INTERVAL_MONTH:
        return f'{b_start:%b %Y}'
    return f'{b_start:%d %b} – {b_end:%d %b}'


def build_buckets(start, end, interval, daily):
    """`daily` maps date -> {'sales','cost','units','tx'}. Every day in range
    is represented (zeros included) so the series is continuous."""
    buckets, order = {}, []
    d = start
    while d <= end:
        b_start, b_end = _bucket_bounds(d, interval, start, end)
        key = b_start
        if key not in buckets:
            buckets[key] = {'start': b_start, 'end': b_end, 'label': _bucket_label(b_start, b_end, interval),
                            'days': 0, **_blank()}
            order.append(key)
        row = daily.get(d)
        b = buckets[key]
        b['days'] += 1
        if row:
            b['sales'] += Decimal(row['sales']); b['cost'] += Decimal(row['cost'])
            b['units'] += int(row['units']); b['tx'] += int(row['tx'])
        d += timedelta(days=1)
    out = [buckets[k] for k in order]
    top_sales = max((b['sales'] for b in out), default=ZERO)
    for b in out:
        b['profit'] = b['sales'] - b['cost']
        b['margin'] = float(b['profit'] / b['sales'] * 100) if b['sales'] else 0.0
        b['avg_ticket'] = (b['sales'] / b['tx']) if b['tx'] else ZERO
        b['bar_sales'] = int(b['sales'] / top_sales * 100) if top_sales > 0 else 0
        # Profit is drawn against the SAME scale as sales so the two bars are honest side by side.
        b['bar_profit'] = int(max(b['profit'], ZERO) / top_sales * 100) if top_sales > 0 else 0
    return out


def totals_from_daily(daily, start, end):
    sales = sum((Decimal(r['sales']) for r in daily.values()), ZERO)
    cost = sum((Decimal(r['cost']) for r in daily.values()), ZERO)
    units = sum(int(r['units']) for r in daily.values())
    tx = sum(int(r['tx']) for r in daily.values())
    days = (end - start).days + 1
    active = sum(1 for r in daily.values() if r['tx'] or r['sales'])
    profit = sales - cost
    return {
        'sales': sales, 'cost': cost, 'profit': profit, 'units': units, 'transactions': tx,
        'margin': float(profit / sales * 100) if sales else 0.0,
        'avg_ticket': (sales / tx) if tx else ZERO,
        'days': days, 'active_days': active,
        'avg_daily_sales': sales / days if days else ZERO,
        'avg_daily_profit': profit / days if days else ZERO,
    }
