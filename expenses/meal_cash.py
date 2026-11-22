"""Automatic drawer-cash payment for EARNED staff meals (V50.42).

One rule for every path that changes a MealLedger (scan, network-outage approval,
manager grant, late sales funding, void reversal): after each save, the meal has an
active MealCashPayment if and only if its status is EARNED.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


def sync_meal_cash_payment(meal, now=None):
    from .models import MealCashPayment
    now = now or timezone.now()
    active = MealCashPayment.objects.filter(meal_id=meal.pk, reversed_at__isnull=True).first()
    if meal.status == 'EARNED' and meal.amount and meal.amount > 0:
        if active is None:
            MealCashPayment.objects.create(
                meal=meal, pharmacy_id=meal.pharmacy_id, amount=meal.amount,
                meal_date=meal.date, paid_at=now)
    elif active is not None:
        active.reversed_at = now
        active.save(update_fields=['reversed_at'])


def legacy_settlement_boundary(pharmacy_id, today=None):
    """First meal date whose cash is NOT already inside a verified drawer figure.

    A physical count on day D already contains every meal paid up to D, and a manager-typed
    opening on day X already contains meals before X. Charging those meals again would deduct
    them twice, so only meals dated on/after this boundary are settled. None = no verified
    figure exists (everything is still unsettled).
    """
    from datetime import timedelta
    from .models import CashDay
    today = today or timezone.localdate()
    boundaries = []
    counted = (CashDay.objects.filter(pharmacy_id=pharmacy_id, actual_closing_cash__isnull=False, date__lt=today)
               .order_by('-date').values_list('date', flat=True).first())
    if counted:
        boundaries.append(counted + timedelta(days=1))
    typed = (CashDay.objects.filter(pharmacy_id=pharmacy_id, opening_source__in=['MANUAL', 'OVERRIDE'], date__lte=today)
             .order_by('-date').values_list('date', flat=True).first())
    if typed:
        boundaries.append(typed)
    return max(boundaries) if boundaries else None


def _local_date(dt):
    return dt.astimezone(timezone.get_current_timezone()).date()


def legacy_meal_plan(pharmacy_id, today=None):
    """Decide, meal by meal, whether a pre-V50.42 EARNED meal still needs charging to the drawer.

    A legacy meal is NOT charged when its cash is already accounted for:
      * 'verified'  - dated before the latest verified drawer figure (physical count / typed opening);
                      settled rows are judged as of THEIR settlement day, so a later count never
                      un-does a deduction that is already inside a counted figure;
      * 'expense'   - the manager already recorded it as an approved cash 'Staff Meals' expense on
                      that date (coverage is allocated meal by meal, oldest first, never beyond
                      the expense total, so it can never be deducted twice).
    Everything else is 'charge'. Returns a list of dicts: payment, reason, charge.
    """
    from django.db.models import Q, Sum
    from .ledger import STAFF_MEALS_CATEGORY, cash_expenses_qs
    from .models import MealCashPayment
    today = today or timezone.localdate()
    rows = list(MealCashPayment.objects.filter(
        pharmacy_id=pharmacy_id, reversed_at__isnull=True, meal__status='EARNED',
    ).filter(Q(is_legacy=True) | Q(legacy_settled_at__isnull=False)).select_related('meal').order_by('meal_date', 'pk'))
    cover = {r['expense_date']: r['t'] or 0 for r in
             cash_expenses_qs(pharmacy_id).filter(category__name__iexact=STAFF_MEALS_CATEGORY)
             .values('expense_date').annotate(t=Sum('amount'))}
    boundaries, plan = {}, []
    for pay in rows:
        as_of = _local_date(pay.legacy_settled_at) if pay.legacy_settled_at else today
        if as_of not in boundaries:
            boundaries[as_of] = legacy_settlement_boundary(pharmacy_id, as_of)
        boundary = boundaries[as_of]
        if boundary is not None and pay.meal_date < boundary:
            plan.append({'payment': pay, 'reason': 'verified', 'charge': False})
        elif cover.get(pay.meal_date, 0) >= pay.amount:
            cover[pay.meal_date] -= pay.amount
            plan.append({'payment': pay, 'reason': 'expense', 'charge': False})
        else:
            plan.append({'payment': pay, 'reason': 'charge', 'charge': True})
    return plan


def repair_over_settled_meals(pharmacy_id=None, dry_run=False):
    """Undo settlements that charged the drawer for cash that was already accounted for.

    Rows go back to legacy (never charged). Profit is untouched. Idempotent.
    Returns (number_of_meals, total_amount) given back to the drawer.
    """
    from django.db.models import Sum
    from .models import MealCashPayment
    base = MealCashPayment.objects.filter(is_legacy=False, legacy_settled_at__isnull=False, reversed_at__isnull=True)
    if pharmacy_id is not None:
        base = base.filter(pharmacy_id=pharmacy_id)
    count, total = 0, 0
    for pid in list(base.values_list('pharmacy_id', flat=True).distinct()):
        ids = [x['payment'].pk for x in legacy_meal_plan(pid) if not x['charge'] and x['payment'].legacy_settled_at]
        if not ids:
            continue
        qs = MealCashPayment.objects.filter(pk__in=ids)
        count += len(ids)
        total += qs.aggregate(t=Sum('amount'))['t'] or 0
        if not dry_run:
            qs.update(is_legacy=True, legacy_settled_at=None)
    return count, total


def _settlement_paid_at(pharmacy_id, meal_date, now):
    """When the drawer deduction is recorded.

    On the meal's OWN day (end of day) while that day is still open / awaiting its physical count, so the
    day's expected cash and the next day's carried-forward opening are right automatically. A day that is
    already CLOSED is never rewritten: the deduction then lands now. Today's meals land now.
    """
    import datetime as _dt
    from .models import CashDay
    today = _local_date(now)
    if meal_date >= today:
        return now
    day = CashDay.objects.filter(pharmacy_id=pharmacy_id, date=meal_date).first()
    if day is not None and day.status == CashDay.Status.CLOSED:
        return now
    tz = timezone.get_current_timezone()
    return timezone.make_aware(_dt.datetime.combine(meal_date, _dt.time(23, 59, 59)), tz)


def _refresh_today_openings(pharmacy_ids, now):
    """Let today's provisional opening follow the corrected previous-day expected cash."""
    from .models import CashDay
    from .services import refresh_provisional_opening
    for pid in pharmacy_ids:
        cur = CashDay.objects.filter(pharmacy_id=pid, date=_local_date(now)).select_related('pharmacy').first()
        if cur is not None:
            refresh_provisional_opening(cur.pharmacy, cur, cur.opened_by)


def settle_legacy_meal_payments(pharmacy_id=None, now=None, dry_run=False):
    """Idempotent settlement of meals EARNED before automatic meal payments.

    Profit already carries these meals on their own dates, but the drawer never did. The drawer is charged
    ONCE, on the meal's own day when that day is not closed (see _settlement_paid_at), and ONLY for meals whose
    cash is not already accounted for (see legacy_meal_plan), so nothing is deducted twice. Meals settled
    earlier on a later day are moved onto their own day. Today's provisional opening is refreshed.
    Returns (number_of_meals_newly_settled, total_amount). Re-running changes nothing.
    """
    from django.db.models import Sum
    from .models import MealCashPayment
    now = now or timezone.now()
    base = MealCashPayment.objects.filter(reversed_at__isnull=True, meal__status='EARNED').filter(
        models_Q_legacy_or_settled())
    if pharmacy_id is not None:
        base = base.filter(pharmacy_id=pharmacy_id)
    count, total, touched = 0, 0, set()
    for pid in list(base.values_list('pharmacy_id', flat=True).distinct()):
        for x in legacy_meal_plan(pid, _local_date(now)):
            pay = x['payment']
            if not x['charge']:
                continue
            target = _settlement_paid_at(pid, pay.meal_date, now)
            if pay.is_legacy:
                count += 1
                total += pay.amount
                touched.add(pid)
                if not dry_run:
                    MealCashPayment.objects.filter(pk=pay.pk).update(
                        is_legacy=False, paid_at=target, legacy_settled_at=now)
            elif pay.legacy_settled_at and _local_date(pay.paid_at) != _local_date(target):
                touched.add(pid)
                if not dry_run:
                    MealCashPayment.objects.filter(pk=pay.pk).update(paid_at=target)
    if touched and not dry_run:
        _refresh_today_openings(touched, now)
    return count, total


def relocate_late_meal_payments(pharmacy_id=None, now=None, dry_run=False):
    """Move a meal's drawer payment onto the meal's own day when it was recorded on a LATER day.

    A meal that became EARNED after midnight (late funding, manager approval, workday close) was deducted on
    the day that happened, so the meal's own day showed no meal cash and the next opening was too high.
    Only yesterday's and today's meals, only when the meal's day is not CLOSED (closed days are never rewritten).
    Today's provisional opening is refreshed. Idempotent. Returns a list of (payment, new_paid_at).
    """
    from datetime import timedelta
    from .models import MealCashPayment
    now = now or timezone.now()
    today = _local_date(now)
    qs = MealCashPayment.objects.filter(
        is_legacy=False, legacy_settled_at__isnull=True, reversed_at__isnull=True,
        meal_date__gte=today - timedelta(days=1), meal_date__lt=today,
    ).select_related('meal')
    if pharmacy_id is not None:
        qs = qs.filter(pharmacy_id=pharmacy_id)
    moved, touched = [], set()
    for pay in qs:
        target = _settlement_paid_at(pay.pharmacy_id, pay.meal_date, now)
        if _local_date(target) != pay.meal_date or _local_date(pay.paid_at) <= pay.meal_date:
            continue
        moved.append((pay, target))
        touched.add(pay.pharmacy_id)
        if not dry_run:
            MealCashPayment.objects.filter(pk=pay.pk).update(paid_at=target)
    if touched and not dry_run:
        _refresh_today_openings(touched, now)
    return moved


def ensure_meal_day_payments(pharmacy_id, meal_date, now=None, dry_run=False):
    """Make the drawer of ONE day exact: every EARNED meal dated `meal_date` has exactly one active, non-legacy
    drawer payment recorded on that day. Fixes meals with no payment row, legacy rows, and payments recorded on a
    later day. Refuses a CLOSED day (never rewritten). Profit is untouched.
    Returns (changes, total_amount, manual_staff_meal_expenses_on_that_day).
    """
    import datetime as _dt
    from django.db.models import Sum
    from accounts.models import MealLedger
    from .ledger import STAFF_MEALS_CATEGORY, cash_expenses_qs
    from .models import CashDay, MealCashPayment
    now = now or timezone.now()
    day = CashDay.objects.filter(pharmacy_id=pharmacy_id, date=meal_date).first()
    if day is not None and day.status == CashDay.Status.CLOSED:
        raise ValueError(f'{meal_date} is CLOSED; closed days are never rewritten.')
    tz = timezone.get_current_timezone()
    target = (now if meal_date >= _local_date(now)
              else timezone.make_aware(_dt.datetime.combine(meal_date, _dt.time(23, 59, 59)), tz))
    changes, total = [], 0
    for meal in MealLedger.objects.filter(pharmacy_id=pharmacy_id, date=meal_date, status='EARNED').order_by('pk'):
        if not meal.amount or meal.amount <= 0:
            continue
        active = list(MealCashPayment.objects.filter(meal_id=meal.pk, reversed_at__isnull=True).order_by('pk'))
        keep = active[0] if active else None
        if keep is None:
            changes.append((meal, 'created'))
            if not dry_run:
                MealCashPayment.objects.create(meal=meal, pharmacy_id=pharmacy_id, amount=meal.amount,
                                               meal_date=meal_date, paid_at=target, legacy_settled_at=now)
        elif keep.is_legacy or _local_date(keep.paid_at) != meal_date:
            changes.append((meal, 'legacy' if keep.is_legacy else 'moved'))
            if not dry_run:
                MealCashPayment.objects.filter(pk=keep.pk).update(
                    is_legacy=False, paid_at=target, amount=meal.amount,
                    legacy_settled_at=keep.legacy_settled_at or now)
        for extra in active[1:]:                      # never more than one active payment per meal
            changes.append((meal, 'duplicate removed'))
            if not dry_run:
                MealCashPayment.objects.filter(pk=extra.pk).update(reversed_at=target)
        total += meal.amount
    manual = (cash_expenses_qs(pharmacy_id).filter(expense_date=meal_date, category__name__iexact=STAFF_MEALS_CATEGORY)
              .aggregate(t=Sum('amount'))['t'] or 0)
    if changes and not dry_run:
        _refresh_today_openings({pharmacy_id}, now)
    return changes, total, manual


def charge_closed_day_meals_on(pharmacy_id, meal_date, charge_date, now=None, dry_run=False):
    """Deduct, on `charge_date`, the EARNED meals of a CLOSED day that never reached any drawer figure.

    The closed day itself is never rewritten; `charge_date` must be a later day that is not CLOSED
    (normally the next day, or today). Only meals with no active, non-legacy payment are touched; meals that already
    have a real payment are skipped, so running it twice never deducts twice. Profit is untouched.
    Returns (changes, total_charged, manual_staff_meal_expenses_on_that_day).
    """
    from django.db.models import Sum
    from accounts.models import MealLedger
    from .ledger import STAFF_MEALS_CATEGORY, cash_expenses_qs
    from .models import MealCashPayment
    import datetime as _dt
    from .models import CashDay
    now = now or timezone.now()
    if charge_date <= meal_date:
        raise ValueError('charge date must be after the meal date.')
    if charge_date > _local_date(now):
        raise ValueError('charge date cannot be in the future.')
    target_day = CashDay.objects.filter(pharmacy_id=pharmacy_id, date=charge_date).first()
    if target_day is not None and target_day.status == CashDay.Status.CLOSED:
        raise ValueError(f'{charge_date} is CLOSED; closed days are never rewritten.')
    target = (now if charge_date == _local_date(now)
              else timezone.make_aware(_dt.datetime.combine(charge_date, _dt.time(23, 59, 59)),
                                       timezone.get_current_timezone()))
    changes, total = [], 0
    for meal in MealLedger.objects.filter(pharmacy_id=pharmacy_id, date=meal_date, status='EARNED').order_by('pk'):
        if not meal.amount or meal.amount <= 0:
            continue
        active = list(MealCashPayment.objects.filter(meal_id=meal.pk, reversed_at__isnull=True).order_by('pk'))
        real = [p for p in active if not p.is_legacy]
        if real:
            continue                                   # already deducted somewhere
        if active:                                     # legacy row: make it a real payment dated now
            changes.append((meal, f'legacy -> charged on {charge_date}'))
            if not dry_run:
                MealCashPayment.objects.filter(pk=active[0].pk).update(
                    is_legacy=False, paid_at=target, amount=meal.amount, legacy_settled_at=now)
        else:
            changes.append((meal, f'created (charged on {charge_date})'))
            if not dry_run:
                MealCashPayment.objects.create(meal=meal, pharmacy_id=pharmacy_id, amount=meal.amount,
                                               meal_date=meal_date, paid_at=target, legacy_settled_at=now)
        total += meal.amount
    manual = (cash_expenses_qs(pharmacy_id).filter(expense_date=meal_date, category__name__iexact=STAFF_MEALS_CATEGORY)
              .aggregate(t=Sum('amount'))['t'] or 0)
    if changes and not dry_run:
        _refresh_today_openings({pharmacy_id}, now)
    return changes, total, manual


def heal_open_day_meal_payments(pharmacy_id, now=None):
    """V54.43: self-healing. Every EARNED meal of yesterday/today (only while that cash day is not CLOSED)
    must have exactly one active drawer payment on its own day. Runs whenever the manager opens Cash Control,
    Spot Check or Count & Close, so expected cash always deducts the meals that are visible in Meals Today.
    Idempotent; CLOSED days are never rewritten. Returns the number of corrections made."""
    from datetime import timedelta
    from .models import CashDay
    now = now or timezone.now()
    today = _local_date(now)
    fixed = 0
    for d in (today - timedelta(days=1), today):
        day = CashDay.objects.filter(pharmacy_id=pharmacy_id, date=d).first()
        if day is None or day.status == CashDay.Status.CLOSED:
            continue
        try:
            changes, _total, _manual = ensure_meal_day_payments(pharmacy_id, d, now=now)
        except ValueError:
            continue
        fixed += len(changes)
    return fixed


def models_Q_legacy_or_settled():
    from django.db.models import Q
    return Q(is_legacy=True) | Q(legacy_settled_at__isnull=False)


def _register():
    from accounts.models import MealLedger

    @receiver(post_save, sender=MealLedger, dispatch_uid='expenses_sync_meal_cash_payment')
    def _on_meal_saved(sender, instance, **kwargs):
        sync_meal_cash_payment(instance)


_register()
