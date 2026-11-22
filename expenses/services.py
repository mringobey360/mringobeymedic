from decimal import Decimal
from django.db import transaction, models
from django.urls import reverse
from django.utils import timezone
from .models import CashDay, CashCount, Expense, ExpenseCategory, CashAuditEvent, CashClosingSnapshot
from notifications.models import Notification
from notifications.utils import notify

def seed_categories(pharmacy=None):
    names = ['LUKU / Electricity','Water','Internet','Airtime','Transport','Cleaning','Stationery','Packaging','Repairs & Maintenance','Business / Government Fees','Bank / Mobile Money Charges','Staff Expense','Staff Meals','Emergency','Other']
    for name in names:
        ExpenseCategory.objects.get_or_create(pharmacy=pharmacy, name=name, defaults={'is_system': pharmacy is None})

def get_or_create_cash_day(pharmacy, date, user, opening_cash=None):
    day = CashDay.objects.filter(pharmacy=pharmacy, date=date).first()
    if day:
        return day
    previous = CashDay.objects.filter(
        pharmacy=pharmacy, date__lt=date
    ).order_by('-date').first()
    if previous and previous.status == CashDay.Status.CLOSED and previous.actual_closing_cash is not None:
        opening = previous.actual_closing_cash
        source = CashDay.OpeningSource.CARRY_FORWARD
        provisional = False
        note = f'Automatically carried forward from {previous.date} verified closing cash.'
    elif previous:
        # Keep the business operational even when yesterday was not physically
        # reconciled. The opening is explicitly provisional and will be rebased
        # automatically once the previous day receives a verified physical close.
        opening = previous.expected_closing_cash
        source = CashDay.OpeningSource.CARRY_FORWARD
        provisional = True
        note = f'Provisional opening based on {previous.date} system-expected closing; pending physical verification.'
    else:
        opening = opening_cash if opening_cash is not None else Decimal('0')
        source = CashDay.OpeningSource.MANUAL
        provisional = False
        note = 'Opening balance entered at first cash-day setup.'
    created = CashDay.objects.create(
        pharmacy=pharmacy, date=date, opening_cash=opening,
        opening_source=source, carried_from=previous,
        opening_note=note, opening_is_provisional=provisional,
        opening_provisional_note=(note if provisional else ''), opened_by=user,
    )
    record_audit(created, CashAuditEvent.Action.DAY_CREATED, user, new=opening, reason=note)
    return created

# ---------------------------------------------------------------------------
# Audit trail + immutable closing snapshots
# ---------------------------------------------------------------------------

def client_ip(request):
    if request is None:
        return None
    fwd = request.META.get('HTTP_X_FORWARDED_FOR')
    ip = (fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR')) or None
    return ip or None


def _money(value):
    return f'TZS {Decimal(value):,.0f}' if value is not None else ''


def record_audit(cash_day, action, actor=None, *, old=None, new=None, reason='', request=None, at=None):
    """Append one immutable audit row. Never raises into the caller's workflow."""
    try:
        with transaction.atomic():  # savepoint: a failed audit write cannot poison the caller's transaction
            return CashAuditEvent.objects.create(
                pharmacy_id=cash_day.pharmacy_id, cash_day=cash_day, business_date=cash_day.date,
                action=action, actor=actor if getattr(actor, 'pk', None) else None,
                old_value='' if old is None else (old if isinstance(old, str) else _money(old)),
                new_value='' if new is None else (new if isinstance(new, str) else _money(new)),
                reason=(reason or '').strip(), ip_address=client_ip(request),
                created_at=at or timezone.now(),
            )
    except Exception:  # audit must never block cash operations
        import logging
        logging.getLogger(__name__).exception('cash audit write failed')
        return None


def create_closing_snapshot(cash_day, closed_by, *, expected, physical, variance, note='', closed_at=None):
    """Freeze this close. A later reopen+close adds revision N+1; old rows stay."""
    from .ledger import CashLedgerService
    closed_at = closed_at or cash_day.closed_at or timezone.now()
    totals = CashLedgerService.totals(cash_day, upto=closed_at)
    revision = cash_day.closing_snapshots.count() + 1
    return CashClosingSnapshot.objects.create(
        cash_day=cash_day, revision=revision, opening_cash=cash_day.opening_cash,
        expected_cash=expected, physical_cash=physical, variance=variance,
        cash_sales=totals.cash_sales, cash_debt_collected=totals.cash_debt_collected, cash_refunds=totals.cash_refunds, cash_expenses=totals.cash_expenses + totals.cash_meals,
        note=note or '', closed_by=closed_by, closed_at=closed_at,
    )


def record_cash_count(cash_day, user, physical_cash, note='', *, supersedes=None, correction_reason='', request=None):
    """Record an immutable intraday physical count without closing the day."""
    if cash_day.status == CashDay.Status.CLOSED:
        raise ValueError('This cash day is already closed. Use the final closing record for the closed day.')
    counted_at = timezone.now()
    with transaction.atomic():
        locked = CashDay.objects.select_for_update().get(pk=cash_day.pk)
        if locked.status == CashDay.Status.CLOSED:
            raise ValueError('This cash day was closed before the count could be recorded.')
        expected = locked.expected_cash_as_of(counted_at)
        physical = Decimal(str(physical_cash))
        from .cash_rules import clean_variance
        variance = clean_variance(physical - expected)
        count = CashCount.objects.create(
            cash_day=locked, counted_by=user, counted_at=counted_at,
            physical_cash=physical, expected_cash=expected, variance=variance,
            note=(note or '').strip(), confirmed=True,
            supersedes=supersedes, correction_reason=(correction_reason or '').strip(),
        )
        if supersedes is not None:
            record_audit(locked, CashAuditEvent.Action.COUNT_CORRECTED, user, old=supersedes.physical_cash,
                         new=physical, reason=correction_reason, at=counted_at, request=request)
        else:
            record_audit(locked, CashAuditEvent.Action.SPOT_CHECK, user, old=expected, new=physical,
                         reason=(note or '') or f'Variance {_money(variance)}', at=counted_at, request=request)
        return count


def notify_managers(pharmacy, title, message, level=Notification.Level.INFO, link=''):
    managers = pharmacy.managers.filter(is_active=True)
    for manager in managers:
        notify(manager, title, message, category=Notification.Category.SALES, level=level, link_url=link, pharmacy=pharmacy)

def approve_expense(expense, user, note=''):
    """Approve a pending expense.

    A sales-cash expense leaves the drawer on the day it is APPROVED (V54.68), not on the day it
    was submitted: if it waited as PENDING from an earlier date and is approved today, today's
    drawer is reduced automatically. Profit still follows expense_date."""
    with transaction.atomic():
        locked = Expense.objects.select_for_update().select_related('pharmacy','submitted_by').get(pk=expense.pk)
        if locked.status != Expense.Status.PENDING:
            raise ValueError('This expense has already been decided.')
        now = timezone.now()
        cash_date = timezone.localtime(now).date()
        if locked.payment_source == Expense.PaymentSource.CASH_SALES:
            day = CashDay.objects.filter(pharmacy=locked.pharmacy, date=cash_date).first()
            if day is None:
                day = ensure_cash_day_for_date(locked.pharmacy, user, cash_date)
            if day is None:
                raise ValueError('Open the cash day before approving a sales-cash expense.')
            if day.status != CashDay.Status.OPEN:
                raise ValueError("Today's cash day has already ended or closed. Reopen it through an authorized correction workflow before approving this expense.")
            locked.cash_date = cash_date
        locked.status = Expense.Status.APPROVED
        locked.decided_by = user
        locked.decided_at = now
        locked.decision_note = note.strip()
        locked.save(update_fields=['status','decided_by','decided_at','decision_note','cash_date','updated_at'])
        notify(locked.submitted_by, 'Expense approved', f'{locked.title} — TZS {locked.amount:,.0f} was approved.', category=Notification.Category.SALES, level=Notification.Level.SUCCESS, link_url=reverse('expenses:detail', args=[locked.pk]), pharmacy=locked.pharmacy)
        return locked

def reject_expense(expense, user, note=''):
    with transaction.atomic():
        locked = Expense.objects.select_for_update().get(pk=expense.pk)
        if locked.status != Expense.Status.PENDING:
            raise ValueError('This expense has already been decided.')
        locked.status = Expense.Status.REJECTED
        locked.decided_by = user
        locked.decided_at = timezone.now()
        locked.decision_note = note.strip()
        locked.save(update_fields=['status','decided_by','decided_at','decision_note','updated_at'])
        notify(locked.submitted_by, 'Expense rejected', f'{locked.title} — TZS {locked.amount:,.0f} was rejected.', category=Notification.Category.SALES, level=Notification.Level.WARNING, link_url=reverse('expenses:detail', args=[locked.pk]), pharmacy=locked.pharmacy)
        return locked

# ---------------------------------------------------------------------------
# Professional cash-day automation
# ---------------------------------------------------------------------------

def end_cash_day_automatically(pharmacy, day, *, now=None):
    """Lock a completed business date at the Tanzania calendar boundary.

    This is a transaction boundary, NOT a physical cash close. A manager must
    still count the drawer later. A day that was explicitly reopened for an
    audited correction is never re-ended automatically while that correction
    is in progress.
    """
    now = now or timezone.now()
    business_date = timezone.localdate(now)
    if day.date >= business_date:
        return day, 'current_or_future'
    with transaction.atomic():
        locked = CashDay.objects.select_for_update().get(pk=day.pk)
        if locked.date >= business_date:
            return locked, 'current_or_future'
        if locked.status == CashDay.Status.CLOSED:
            return locked, 'already_closed'
        if locked.status == CashDay.Status.DAY_ENDED:
            return locked, 'already_day_ended'
        # An authorized reopen creates an audit row and deliberately returns the
        # day to OPEN so the manager can correct/recount it. Never let midnight
        # automation immediately undo that correction workflow.
        if locked.reopen_audits.exists():
            locked.automation_checked_at = now
            locked.save(update_fields=['automation_checked_at'])
            return locked, 'reopened_requires_correction'
        locked.status = CashDay.Status.DAY_ENDED
        locked.day_ended_at = locked.day_ended_at or now
        locked.day_ended_automatically = True
        locked.automation_checked_at = now
        locked.save(update_fields=['status', 'day_ended_at', 'day_ended_automatically', 'automation_checked_at'])
        record_audit(locked, CashAuditEvent.Action.DAY_ENDED, None, new='Automatic day-end', at=now,
                     reason='Business date boundary reached; physical count still required.')
        return locked, 'day_ended'


def rollover_business_day(pharmacy, user, *, now=None):
    """Perform an idempotent calendar rollover for one branch.

    At/after 00:00 Africa/Dar_es_Salaam the previous business date becomes
    transaction-locked and the current date is created/opened. Physical cash is
    never fabricated: if yesterday has not been counted, today's opening is
    explicitly provisional and will be reconciled when yesterday is verified.
    """
    now = now or timezone.now()
    business_date = timezone.localdate(now)
    ended = []
    previous_days = CashDay.objects.filter(
        pharmacy=pharmacy, date__lt=business_date, status=CashDay.Status.OPEN
    ).order_by('date')
    for day in previous_days:
        changed, result = end_cash_day_automatically(pharmacy, day, now=now)
        if result == 'day_ended':
            ended.append(changed)
    current = ensure_cash_day_for_date(pharmacy, user, business_date)
    return current, ended


def auto_close_reconciled_day(pharmacy, day, actor, *, now=None):
    """Backward-compatible wrapper: automation ends the day, never fabricates a close."""
    return end_cash_day_automatically(pharmacy, day, now=now)

class CashSaleBlocked(Exception):
    """Reserved for genuine cash-sale blocking conditions."""


class CashSaleDayNotReady(Exception):
    """Raised when no valid cash-control day exists for the current business date."""


def get_current_cash_day_for_sale(pharmacy, user, *, business_date=None):
    """Return the branch cash day that may receive a cash sale today.

    Business rules:
    - The branch's business date is the only date eligible for new POS cash sales.
    - A previous day being DAY_ENDED or CLOSED never blocks today's trading.
    - Today's OPEN/provisional day is tradeable.
    - DAY_ENDED and CLOSED are hard transaction boundaries for cash operations.
      They cannot receive new cash sales. This prevents a manager from closing a
      business date and then silently creating post-close cash activity on that date.
    - A closed day can only receive corrective cash activity after an authorized
      reopen workflow, which creates an audit record.
    - Cash-day creation is allowed only for today (never a future date).
    - A branch with no initialized first opening cash remains blocked until a manager
      establishes the first physical opening balance.
    """
    business_date = business_date or timezone.localdate()
    # Resilient midnight boundary: the first POS cash request after 00:00 also
    # performs the rollover if the background scheduler was temporarily down.
    if business_date == timezone.localdate():
        day, _ended = rollover_business_day(pharmacy, user, now=timezone.now())
    else:
        day = CashDay.objects.filter(pharmacy=pharmacy, date=business_date).first()
        if day is None:
            day = ensure_cash_day_for_date(pharmacy, user, business_date)
    if day is None:
        raise CashSaleDayNotReady(
            'Cash control has not been initialized for this branch. A manager must set the first physical opening cash before cash sales can be accepted.'
        )
    if day.date != business_date:
        raise CashSaleDayNotReady(
            "Cash control is not available for the current business date. Open today\'s cash control before accepting cash sales."
        )
    # CLOSED and DAY_ENDED are hard cash transaction boundaries. The POS must
    # never create a cash sale for a business date after that date has been ended
    # or finally closed. The manager must reopen the day through the audited
    # correction workflow before cash activity can resume.
    if day.status == CashDay.Status.CLOSED:
        raise CashSaleBlocked(
            f'Cash control for {day.date} was already closed. Reopen that cash day before recording another cash sale.'
        )
    if day.status == CashDay.Status.DAY_ENDED:
        raise CashSaleBlocked(
            f'Cash control for {day.date} has ended. Reopen or complete the cash-day correction workflow before recording another cash sale.'
        )
    return day


def lock_cash_day_for_sale(pharmacy, user, *, business_date=None):
    """Atomically re-check the cash boundary immediately before creating a sale."""
    business_date = business_date or timezone.localdate()
    # Creation is intentionally outside this lock helper; locking an existing row
    # is what prevents two concurrent POS requests from crossing a close boundary.
    day = get_current_cash_day_for_sale(pharmacy, user, business_date=business_date)
    locked = CashDay.objects.select_for_update().get(pk=day.pk)
    if locked.date != business_date:
        raise CashSaleDayNotReady('Cash control is not aligned with the current business date.')
    if locked.status == CashDay.Status.CLOSED:
        raise CashSaleBlocked(
            f'Cash control for {locked.date} was already closed. Reopen that cash day before recording another cash sale.'
        )
    if locked.status == CashDay.Status.DAY_ENDED:
        raise CashSaleBlocked(
            f'Cash control for {locked.date} has ended. Reopen or complete the cash-day correction workflow before recording another cash sale.'
        )
    return locked


def provisional_opening_target(previous):
    """Best estimate of drawer cash at the start of the next day while `previous`
    is not physically verified. Includes cash that moved after its close."""
    from .ledger import CashLedgerService
    if previous.status == CashDay.Status.CLOSED and previous.actual_closing_cash is not None:
        return previous.actual_closing_cash + CashLedgerService.post_close_totals(previous).net
    return CashLedgerService.expected_cash(previous, timezone.now())


def refresh_provisional_opening(pharmacy, current, actor, *, request=None):
    """Keep a PROVISIONAL opening in step with late cash on the previous day.

    Never touches a verified opening. Every change is an immutable
    CashOpeningAdjustment plus an audit event. Returns True if changed."""
    if current is None or current.status != CashDay.Status.OPEN or not current.opening_is_provisional:
        return False
    previous = CashDay.objects.filter(pharmacy=pharmacy, date__lt=current.date).order_by('-date').first()
    if previous is None:
        return False
    target = provisional_opening_target(previous)
    if target == current.opening_cash:
        return False
    from .models import CashOpeningAdjustment
    with transaction.atomic():
        locked = CashDay.objects.select_for_update().get(pk=current.pk)
        if not locked.opening_is_provisional or locked.status != CashDay.Status.OPEN or locked.opening_cash == target:
            return False
        old = locked.opening_cash
        reason = (f'Automatic provisional refresh: {previous.date} closing count plus net cash recorded after it '
                  f'(TZS {old:,.0f} -> TZS {target:,.0f}). Pending physical recount of {previous.date}.')
        CashOpeningAdjustment.objects.create(cash_day=locked, previous_amount=old, new_amount=target,
                                             reason=reason, adjusted_by=actor)
        locked.opening_cash = target
        locked.save(update_fields=['opening_cash'])
        record_audit(locked, CashAuditEvent.Action.OPENING_ADJUSTED, actor, old=old, new=target, reason=reason, request=request)
    current.opening_cash = target
    return True


def ensure_cash_day_for_date(pharmacy, user, day=None):
    """Create the requested business day without blocking POS on old reconciliation.

    A verified prior close is preferred. If the previous day is still DAY_ENDED,
    today's opening is created from the previous day's system-expected closing and
    marked provisional. Once the previous day is physically verified, the current
    opening is automatically rebased with an immutable adjustment record.
    """
    day = day or timezone.localdate()
    with transaction.atomic():
        existing = CashDay.objects.select_for_update().filter(pharmacy=pharmacy, date=day).first()
        if existing:
            return existing
        previous = CashDay.objects.select_for_update().filter(
            pharmacy=pharmacy, date__lt=day
        ).order_by('-date').first()
        if previous is None:
            return None
        if previous.status == CashDay.Status.CLOSED and previous.post_close_cash_activity:
            # A post-close exception is a historical correction, but it must not
            # prevent the branch from trading on a new calendar day.
            opening = provisional_opening_target(previous)
            provisional = True
            note = f'Provisional opening from {previous.date}: closing count plus net post-close cash; previous day requires recount.'
        elif previous.status == CashDay.Status.CLOSED and previous.actual_closing_cash is not None:
            opening = previous.actual_closing_cash
            provisional = False
            note = f'Automatically carried forward from {previous.date} verified closing cash.'
        else:
            opening = previous.expected_closing_cash
            provisional = True
            note = f'Provisional opening based on {previous.date} system-expected closing; physical verification is pending.'
        created = CashDay.objects.create(
            pharmacy=pharmacy, date=day, opening_cash=opening,
            opening_source=CashDay.OpeningSource.CARRY_FORWARD,
            carried_from=previous, opening_note=note,
            opening_is_provisional=provisional,
            opening_provisional_note=note if provisional else '', opened_by=user,
        )
        record_audit(created, CashAuditEvent.Action.DAY_CREATED, user,
                     new=f'{_money(opening)}{" (provisional)" if provisional else " (verified)"}', reason=note)
        return created


def repair_provisional_opening_chain(pharmacy, actor, *, now=None):
    """Repair stale OPEN provisional handovers after an earlier day is verified.

    This is intentionally conservative: only downstream days that are still OPEN
    and explicitly provisional are rebased. CLOSED history is never silently
    rewritten; the existing audited reopen workflow handles that case. Every
    rebasing is recorded by ``reconcile_provisional_openings``.
    """
    now = now or timezone.now()
    changed = 0
    verified_days = (CashDay.objects
                     .filter(pharmacy=pharmacy, status=CashDay.Status.CLOSED,
                             actual_closing_cash__isnull=False)
                     .order_by('date'))
    for verified_day in verified_days:
        changed += reconcile_provisional_openings(pharmacy, verified_day, actor, now=now)
    changed += reflag_unverified_carried_openings(pharmacy, actor, now=now)
    return changed


def reflag_unverified_carried_openings(pharmacy, actor=None, *, now=None):
    """V54.44: an OPEN day whose opening was carried forward from a day that is NOT physically verified must be
    provisional, so refresh_provisional_opening keeps it in step with that day's expected cash. Repairs days
    wrongly un-flagged by the old rebase. Amount is not changed here (the refresh does that, with audit)."""
    now = now or timezone.now()
    fixed = 0
    for day in CashDay.objects.filter(pharmacy=pharmacy, status=CashDay.Status.OPEN, opening_is_provisional=False,
                                      opening_source=CashDay.OpeningSource.CARRY_FORWARD,
                                      carried_from__isnull=False).select_related('carried_from'):
        prev = day.carried_from
        if prev.status == CashDay.Status.CLOSED and prev.actual_closing_cash is not None:
            continue
        note = f'Provisional: {prev.date} is not physically verified yet.'
        CashDay.objects.filter(pk=day.pk).update(opening_is_provisional=True, opening_provisional_note=note,
                                                 opening_reconciled_at=None)
        record_audit(day, CashAuditEvent.Action.OPENING_ADJUSTED, actor, old=day.opening_cash, new=day.opening_cash,
                     reason=f'Opening marked provisional again: {prev.date} has no physical count, so this opening '
                            f'follows its expected cash until it is counted.', at=now)
        fixed += 1
    return fixed


def heal_stale_provisional_closed_days(pharmacy, actor=None, *, now=None):
    """Clear legacy "provisional opening" flags that can never be settled by the UI.

    A CLOSED day whose opening is still flagged provisional blocks every later
    day ("waiting for an earlier day"). That is correct only while the earlier
    day is genuinely unresolved. It becomes a dead end when:

    * the day is the first recorded cash day (nothing earlier can verify it);
    * its opening already equals the verified physical closing of the day before;
    * a LATER day was physically verified and fully reconciled, so the cash chain
      is anchored after it and this old flag no longer protects anything.

    Amounts are never changed. Each healed day gets an append-only audit event
    that states exactly why the flag was cleared. Returns the number healed.
    """
    now = now or timezone.now()
    days = list(CashDay.objects.filter(pharmacy=pharmacy).order_by('date'))
    healed = 0
    for idx, day in enumerate(days):
        if day.status != CashDay.Status.CLOSED or not day.opening_is_provisional:
            continue
        previous = days[idx - 1] if idx else None
        later_anchor = next(
            (d for d in days[idx + 1:]
             if d.status == CashDay.Status.CLOSED and d.actual_closing_cash is not None
             and not d.opening_is_provisional and not d.post_close_cash_activity),
            None,
        )
        if previous is None:
            why = 'first recorded cash day; no earlier day exists to verify its opening'
        elif (previous.status == CashDay.Status.CLOSED and previous.actual_closing_cash is not None
              and previous.actual_closing_cash == day.opening_cash):
            why = f'opening equals the verified physical closing of {previous.date}'
        elif later_anchor is not None:
            why = f'later day {later_anchor.date} was physically verified and reconciled, so the cash chain is anchored there'
        else:
            continue
        CashDay.objects.filter(pk=day.pk).update(
            opening_is_provisional=False, opening_provisional_note='', opening_reconciled_at=now)
        day.opening_is_provisional = False
        record_audit(day, CashAuditEvent.Action.OPENING_RECONCILED, actor,
                     old=day.opening_cash, new=day.opening_cash,
                     reason=f'Stale provisional-opening flag cleared automatically: {why}. Amount unchanged.', at=now)
        healed += 1
    return healed


def reconcile_provisional_openings(pharmacy, verified_day, actor, *, now=None):
    """Rebase immediate downstream provisional days after a verified close.

    Every change is recorded in CashOpeningAdjustment. Closed downstream days are
    never silently rewritten; the normal chronological close rule prevents them
    from being created in an inconsistent state through the UI.
    """
    now = now or timezone.now()
    if verified_day.status != CashDay.Status.CLOSED or verified_day.actual_closing_cash is None:
        return 0
    changed = 0
    previous = verified_day
    downstream = CashDay.objects.filter(
        pharmacy=pharmacy, date__gt=verified_day.date
    ).order_by('date')
    for day in downstream:
        if not day.opening_is_provisional:
            if day.status == CashDay.Status.CLOSED:
                break
            previous = day
            continue

        # V54.44: only a PHYSICALLY VERIFIED predecessor may rebase (and un-flag) a provisional opening.
        # Previously an unverified day (DAY_ENDED, no count) fed its *expected* cash into the next day and
        # cleared the provisional flag, freezing the opening so later corrections (e.g. meals) never reached it.
        if previous.status != CashDay.Status.CLOSED or previous.actual_closing_cash is None:
            break
        new_amount = previous.actual_closing_cash

        # A CLOSED provisional day is safe to auto-clear only when the verified
        # predecessor produces exactly the same opening amount. If the amount
        # differs, do not rewrite a historical close silently; the manager must
        # use the audited reopen workflow and recount that day.
        if day.status == CashDay.Status.CLOSED and day.opening_cash != new_amount:
            break

        old_amount = day.opening_cash
        if old_amount != new_amount:
            from .models import CashOpeningAdjustment
            CashOpeningAdjustment.objects.create(
                cash_day=day,
                previous_amount=old_amount,
                new_amount=new_amount,
                reason=(
                    f'Automatic carry-forward reconciliation: {previous.date} was physically verified. '
                    f'Previous provisional opening TZS {old_amount:,.2f} replaced with verified closing TZS {new_amount:,.2f}.'
                ),
                adjusted_by=actor,
            )
            day.opening_cash = new_amount
            changed += 1
            record_audit(day, CashAuditEvent.Action.OPENING_RECONCILED, actor, old=old_amount, new=new_amount,
                         reason=f'Rebased after {previous.date} was physically verified.', at=now)
        day.opening_source = CashDay.OpeningSource.CARRY_FORWARD
        day.carried_from = previous
        day.opening_is_provisional = False
        day.opening_provisional_note = ''
        day.opening_reconciled_at = now
        day.opening_note = f'Automatically reconciled from {previous.date} verified closing cash.'
        day.save(update_fields=['opening_cash','opening_source','carried_from','opening_is_provisional','opening_provisional_note','opening_reconciled_at','opening_note'])
        previous = day
    return changed

def get_cash_activity_history(pharmacy, start_date=None, end_date=None, limit_days=90):
    """Read-only cash activity timeline.

    Built on CashLedgerService.daily_totals so the timeline uses exactly the same
    filters as expected cash, the post-close banner and the reopen page.
    """
    from datetime import timedelta
    from .ledger import CashLedgerService, LedgerTotals

    today = timezone.localdate()
    end_date = end_date or today
    start_date = start_date or (end_date - timedelta(days=max(limit_days - 1, 0)))
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    activity = CashLedgerService.daily_totals(pharmacy.pk, start_date, end_date)
    cash_days = {
        d.date: d for d in CashDay.objects.filter(pharmacy=pharmacy, date__range=(start_date, end_date)).order_by('-date')
    }
    rows = []
    for d in sorted(set(activity) | set(cash_days), reverse=True):
        t = activity.get(d) or LedgerTotals()
        day = cash_days.get(d)
        post_close = None
        if day is not None and day.status == CashDay.Status.CLOSED and day.closed_at:
            pc = CashLedgerService.post_close_totals(day)
            post_close = pc.net if pc.count else None
        rows.append({
            'date': d,
            'cash_sales': t.cash_sales, 'cash_debt_collected': t.cash_debt_collected,
            'cash_refunds': t.cash_refunds, 'cash_expenses': t.cash_expenses + t.cash_meals,
            'sales_count': t.sales_count, 'refund_count': t.refund_count, 'expense_count': t.expense_count,
            'net_cash_activity': t.net,
            'post_close_net': post_close,
            'cash_day': day,
            'state': day.reconciliation_state if day is not None else 'HISTORICAL_ACTIVITY',
            'is_historical_activity': day is None,
        })
    return rows



# --------------------------------------------------------------------------
# Professional cash-control cycle reset (V50.24)
# --------------------------------------------------------------------------
class CashResetStale(ValueError):
    """Cash movements changed between review and confirmation."""


def cash_reset_snapshot(pharmacy, start_date, *, now=None):
    """Live ledger position for the chosen date (nothing is written)."""
    from .ledger import CashLedgerService
    now = now or timezone.now()
    day = CashDay.objects.filter(pharmacy=pharmacy, date=start_date).first()
    probe = day or CashDay(pharmacy=pharmacy, date=start_date)
    totals = CashLedgerService.totals(probe, upto=now)
    return {'day': day, 'totals': totals, 'net': totals.net, 'now': now}


def build_cash_reset_plan(pharmacy, start_date, method, amount, *, now=None):
    """Validate and compute the exact effect of a reset. Raises ValueError."""
    today = timezone.localdate()
    if start_date < today:
        raise ValueError('Choose today or a future business date. Historical dates cannot be reset from this screen.')
    if method not in ('START_OF_DAY', 'COUNT_NOW'):
        raise ValueError('Choose how the opening cash is being provided.')
    if amount < 0:
        raise ValueError('The amount must be zero or a positive number.')
    snap = cash_reset_snapshot(pharmacy, start_date, now=now)
    day = snap['day']
    if day and day.status != CashDay.Status.OPEN:
        raise ValueError(f'The cash record for {start_date} is already {day.get_status_display().lower()}. '
                         'Use the audited Reopen & Recount workflow to correct it.')
    net = snap['net']
    if method == 'COUNT_NOW':
        if start_date != today:
            raise ValueError('A physical count can only be used when starting the cycle today.')
        opening = amount - net
        if opening < 0:
            raise ValueError(
                f'The drawer count (TZS {amount:,.0f}) is lower than the net cash movement already recorded today '
                f'(TZS {net:,.0f}), which would require a negative opening balance. Recount the drawer or check today\'s sales, refunds and expenses.')
        counted = amount
    else:
        opening = amount
        counted = None
    previous_opening = day.opening_cash if day else None
    return {
        'start_date': start_date, 'method': method, 'entered': amount, 'opening': opening, 'counted': counted,
        'day': day, 'totals': snap['totals'], 'net': net, 'now': snap['now'],
        'previous_opening': previous_opening,
        'expected_before': (previous_opening + net) if previous_opening is not None else None,
        'expected_after': opening + net,
    }


def make_cash_reset_token(pharmacy, user, plan, reason):
    from django.core import signing
    return signing.dumps({
        'p': pharmacy.pk, 'u': user.pk, 'd': plan['start_date'].isoformat(), 'm': plan['method'],
        'a': str(plan['entered']), 'n': str(plan['net']), 'r': reason,
    }, salt='cash-reset-cycle')


def read_cash_reset_token(token, pharmacy, user, *, max_age=900):
    from django.core import signing
    try:
        data = signing.loads(token, salt='cash-reset-cycle', max_age=max_age)
    except signing.BadSignature:
        raise ValueError('The review expired. Please review the reset again.')
    if data.get('p') != pharmacy.pk or data.get('u') != user.pk:
        raise ValueError('This review belongs to a different branch or user. Please start again.')
    return data


def apply_cash_cycle_reset(pharmacy, user, token, *, request=None):
    """Re-validate against live data and apply the reset atomically with audit."""
    from datetime import date as _date
    from decimal import Decimal
    from .models import CashControlReset
    data = read_cash_reset_token(token, pharmacy, user)
    start_date = _date.fromisoformat(data['d'])
    with transaction.atomic():
        CashDay.objects.select_for_update().filter(pharmacy=pharmacy, date=start_date).first()
        plan = build_cash_reset_plan(pharmacy, start_date, data['m'], Decimal(data['a']))
        if plan['net'] != Decimal(data['n']):
            raise CashResetStale('New cash transactions were recorded while you were reviewing. Please review the updated figures and confirm again.')
        reason = data['r']
        reset = CashControlReset.objects.create(
            pharmacy=pharmacy, start_date=start_date, opening_cash=plan['opening'], reason=reason, reset_by=user,
            archived_cash_days=CashDay.objects.filter(pharmacy=pharmacy, date__lt=start_date).count(),
            previous_opening=plan['previous_opening'], method=plan['method'], counted_cash=plan['counted'],
            movement_at_reset=plan['net'], expected_after=plan['expected_after'], expected_before=plan['expected_before'],
        )
        note = f'Fresh cash-control cycle #{reset.pk}. Previous records retained. Reason: {reason[:180]}'
        day = plan['day']
        if day:
            old = day.opening_cash
            day.opening_cash = plan['opening']
            day.opening_source = CashDay.OpeningSource.OVERRIDE
            day.carried_from = None
            day.opening_is_provisional = False
            day.opening_provisional_note = ''
            day.opening_note = note[:255]
            day.save()
            record_audit(day, CashAuditEvent.Action.OPENING_ADJUSTED, user, old=old, new=plan['opening'],
                         reason=f'New cash-control cycle #{reset.pk}: {reason}', request=request)
        else:
            day = CashDay.objects.create(
                pharmacy=pharmacy, date=start_date, opening_cash=plan['opening'], opening_note=note[:255],
                opening_source=CashDay.OpeningSource.OVERRIDE, opened_by=user)
            record_audit(day, CashAuditEvent.Action.DAY_CREATED, user, new=plan['opening'],
                         reason=f'New cash-control cycle #{reset.pk}: {reason}', request=request)
    try:
        notify_managers(pharmacy, 'Cash-control cycle reset',
                        f'{user} started a new cash-control cycle for {start_date}. Opening TZS {plan["opening"]:,.0f}.',
                        level=Notification.Level.WARNING, link=reverse('expenses:cash'))
    except Exception:
        pass
    return reset, plan
