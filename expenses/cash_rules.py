"""Shared cash-control rules (V54.69).

Two small, pure helpers used by the views, forms, models and templates so the
whole module agrees on what "matched" means and on how a past cash day can be
verified when it is counted late.
"""
from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal('0')
ONE = Decimal('1')


def clean_variance(value):
    """Round a cash difference to the whole shilling; |diff| < 0.5 becomes exactly 0.

    Sub-shilling residue (e.g. -0.30 from a fractional sale amount) used to be
    displayed as "Shortage -TZS 0" because the template tested ``variance < 0``
    on the raw value but printed it with ``floatformat:0``.
    """
    if value is None:
        return None
    rounded = Decimal(value).quantize(ONE, rounding=ROUND_HALF_UP)
    return ZERO if rounded == 0 else rounded


def reconstruct_closing_cash(day):
    """Suggest the physical closing cash of a past, still-unverified ``day``.

    A drawer cannot be re-counted as it was at midnight. If a *later* physical
    count exists (e.g. today's spot check), the drawer at the end of ``day`` is
    that count minus every cash movement recorded after ``day`` ended, up to the
    moment of that count:

        implied closing = later count - net activity of each later day (up to count time)

    Returns ``None`` when no usable later count exists, otherwise a dict with the
    amount and the facts needed to explain it. It is only a *suggestion*: the
    manager must still confirm it and the reconstruction is written to the note
    and audit trail. Never applied automatically.
    """
    from .ledger import CashLedgerService
    from .models import CashDay, CashCount

    if day.status == CashDay.Status.CLOSED:
        return None
    later_days = list(CashDay.objects
                      .filter(pharmacy=day.pharmacy, date__gt=day.date)
                      .order_by('date'))
    if not later_days:
        return None
    count = (CashCount.objects
             .filter(cash_day__in=later_days, confirmed=True)
             .order_by('-counted_at').first())
    if count is None:
        return None

    movement = ZERO
    steps = []
    for later in later_days:
        if later.date > count.cash_day.date:
            break
        upto = count.counted_at if later.pk == count.cash_day_id else None
        if upto is None and later.status not in (CashDay.Status.DAY_ENDED, CashDay.Status.CLOSED):
            return None  # an intermediate day is still live; cannot be frozen safely
        net = CashLedgerService.totals(later, upto=upto or later._expected_cutoff()).net
        movement += net
        steps.append({'date': later.date, 'net': net})

    return {
        'amount': clean_variance(count.physical_cash - movement),
        'count': count,
        'count_day': count.cash_day,
        'movement': movement,
        'steps': steps,
        'note': (f"Reconstructed from the physical count of TZS {count.physical_cash:,.0f} on "
                 f"{count.counted_at:%d %b %Y %H:%M} less TZS {movement:,.0f} of cash movement recorded "
                 f"after {day.date} ended."),
    }
