"""Void & Return review (V37.16).

Read-only analysis of SaleReturnRequest rows so an owner can see, at a glance,
which corrections deserve a second look. Nothing here changes any record.

Flags (all thresholds can be overridden in settings):
  LATE      decided more than VOID_REVIEW_LATE_HOURS after the sale was made
  CROSS_DAY decided on a different business date than the sale
  HIGH      refund value >= VOID_REVIEW_HIGH_VALUE (TZS)
  REPEAT    the same requester has >= VOID_REVIEW_REPEAT_COUNT corrections in range
"""
from collections import Counter
from datetime import timedelta
from decimal import Decimal


def format_delay(delay_hours):
    """Return a human-readable audit delay such as '3h 6m' or '6m'."""
    if delay_hours is None:
        return '—'
    total_minutes = max(0, int(round(float(delay_hours) * 60)))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f'{hours}h {minutes}m'
    if hours:
        return f'{hours}h'
    return f'{minutes}m'

from django.conf import settings
from django.utils import timezone

from .models import SaleReturnRequest


def thresholds():
    return {
        'late_hours': getattr(settings, 'VOID_REVIEW_LATE_HOURS', 2),
        'high_value': Decimal(str(getattr(settings, 'VOID_REVIEW_HIGH_VALUE', 50000))),
        'repeat_count': getattr(settings, 'VOID_REVIEW_REPEAT_COUNT', 3),
    }


def classify(*, sale_created_at, sale_business_date, decided_at, refund_amount, cfg=None):
    """Pure function: returns (delay_hours, [flags]) for one decided request."""
    cfg = cfg or thresholds()
    flags = []
    delay_hours = None
    if decided_at and sale_created_at:
        delay_hours = (decided_at - sale_created_at).total_seconds() / 3600.0
        if delay_hours > cfg['late_hours']:
            flags.append('LATE')
    if decided_at and sale_business_date:
        if timezone.localtime(decided_at).date() != sale_business_date:
            flags.append('CROSS_DAY')
    if refund_amount is not None and Decimal(refund_amount) >= cfg['high_value']:
        flags.append('HIGH')
    return delay_hours, flags


def build_review(pharmacy, start_date, end_date, only_flagged=False):
    """Approved corrections decided between start_date and end_date (local dates)."""
    cfg = thresholds()
    qs = (SaleReturnRequest.objects
          .filter(pharmacy=pharmacy, status=SaleReturnRequest.Status.APPROVED,
                  decided_at__date__gte=start_date, decided_at__date__lte=end_date)
          .select_related('sale', 'requested_by', 'decided_by')
          .order_by('-decided_at'))
    rows = []
    for rr in qs:
        sale = rr.sale
        delay, flags = classify(
            sale_created_at=sale.created_at, sale_business_date=sale.business_date,
            decided_at=rr.decided_at, refund_amount=rr.refund_amount, cfg=cfg)
        rows.append({
            'rr': rr, 'sale': sale,
            'sale_number': sale.sale_number or sale.pk,
            'requested_by': rr.requested_by, 'decided_by': rr.decided_by,
            'direct': bool(rr.requested_by_id and rr.requested_by_id == rr.decided_by_id),
            'delay_hours': delay, 'delay_display': format_delay(delay), 'flags': flags,
            'is_void': rr.is_full_void,
        })
    per_user = Counter((r['requested_by'].pk if r['requested_by'] else None) for r in rows)
    for r in rows:
        uid = r['requested_by'].pk if r['requested_by'] else None
        if uid is not None and per_user[uid] >= cfg['repeat_count']:
            r['flags'].append('REPEAT')
    if only_flagged:
        rows = [r for r in rows if r['flags']]
    return rows, cfg


def summarize(rows):
    total = sum((r['rr'].refund_amount or Decimal('0')) for r in rows)
    by_user = {}
    for r in rows:
        u = r['requested_by']
        key = (u.get_full_name() or u.username) if u else 'Unknown'
        d = by_user.setdefault(key, {'count': 0, 'value': Decimal('0'), 'flagged': 0})
        d['count'] += 1
        d['value'] += r['rr'].refund_amount or Decimal('0')
        d['flagged'] += 1 if r['flags'] else 0
    return {
        'count': len(rows),
        'voids': sum(1 for r in rows if r['is_void']),
        'partial': sum(1 for r in rows if not r['is_void']),
        'value': total,
        'flagged': sum(1 for r in rows if r['flags']),
        'by_user': sorted(by_user.items(), key=lambda kv: -kv[1]['value']),
    }
