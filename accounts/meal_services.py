from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from notifications.models import Notification
from notifications.utils import notify
from sales.models import Sale

from .models import (
    AttendanceCheckpoint, AttendanceRecord, MealAdjustment, MealIssueReport, MealLedger,
    MealTimingSettings, MealWorkday, User, WeeklyMealSchedule,
)

MEAL_TYPES = ('BREAKFAST', 'LUNCH', 'DINNER')


def _as_time(value):
    """Return a ``datetime.time`` even when handed an 'HH:MM[:SS]' string.

    A freshly created model instance keeps its field defaults exactly as declared; the
    old ``TimeField(default='10:00')`` therefore produced a *string* on the request that
    created the row, and ``datetime.combine`` crashed the dashboard with a 500.
    """
    if isinstance(value, str):
        text = value.strip()
        for fmt in ('%H:%M:%S', '%H:%M'):
            try:
                return datetime.strptime(text, fmt).time()
            except ValueError:
                continue
        raise ValueError(f'Invalid time value: {value!r}')
    return value


def _aware_on(date_value, time_value):
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(date_value, _as_time(time_value)), tz)


_TIMING_FIELDS = ('breakfast_start', 'breakfast_cutoff', 'lunch_start', 'lunch_cutoff', 'dinner_start', 'dinner_cutoff')


def meal_timing_for(employee):
    settings, created = MealTimingSettings.objects.get_or_create(pharmacy=employee.pharmacy)
    if created:
        # Reload so every TimeField is a real datetime.time (see _as_time).
        settings.refresh_from_db()
    for name in _TIMING_FIELDS:  # belt and braces for any instance still holding a string
        value = getattr(settings, name)
        if isinstance(value, str):
            setattr(settings, name, _as_time(value))
    return settings


def _entitlements(employee, date_value):
    adjustment = employee.meal_adjustments.filter(date=date_value).first()
    if adjustment:
        return {
            'BREAKFAST': adjustment.breakfast,
            'LUNCH': adjustment.lunch,
            'DINNER': adjustment.dinner,
        }, MealLedger.Source.OVERRIDE, adjustment.reason
    schedule = employee.meal_schedules.filter(weekday=date_value.weekday()).first()
    if schedule:
        return {
            'BREAKFAST': schedule.breakfast,
            'LUNCH': schedule.lunch,
            'DINNER': schedule.dinner,
        }, MealLedger.Source.WEEKLY, ''
    # WeeklyMealSchedule is the single source of truth. Do not silently fall
    # back to the deprecated weekend field: missing policy means no entitlement.
    return {'BREAKFAST': False, 'LUNCH': False, 'DINNER': False}, MealLedger.Source.WEEKLY, 'No weekly meal policy configured.'


def _cutoffs(employee, date_value):
    timing = meal_timing_for(employee)
    return {
        'BREAKFAST': _aware_on(date_value, timing.breakfast_cutoff),
        'LUNCH': _aware_on(date_value, timing.lunch_cutoff),
        'DINNER': _aware_on(date_value, timing.dinner_cutoff),
    }


def _amounts(employee):
    return {
        'BREAKFAST': employee.morning_meal_allowance or Decimal('0'),
        'LUNCH': employee.afternoon_meal_allowance or Decimal('0'),
        'DINNER': employee.evening_meal_allowance or Decimal('0'),
    }


def sales_exist(employee, date_value):
    """V50.42: a meal is funded by the sales the EMPLOYEE made that business day.

    Until the employee has a completed sale the meal stays DEBT; once they sell,
    reconcile_meal_funding_for_date() funds it automatically. Returned/voided
    sales are excluded (a fully voided sale no longer funds the meal)."""
    return Sale.objects.filter(
        pharmacy=employee.pharmacy,
        served_by=employee,
        business_date=date_value,
        status__in=[Sale.Status.COMPLETED, Sale.Status.PARTIALLY_RETURNED],
    ).exists()


def open_workday(employee, when=None, note=''):
    when = when or timezone.now()
    local_date = timezone.localtime(when).date()
    if local_date < employee.created_at.date():
        return None
    obj, created = MealWorkday.objects.get_or_create(
        employee=employee, date=local_date,
        defaults={'opened_at': when, 'opened_manually': True, 'note': note},
    )
    if not created and obj.closed_at is not None:
        raise ValueError('This workday is already closed.')
    return obj


def close_workday(employee, when=None):
    when = when or timezone.now()
    local_date = timezone.localtime(when).date()
    obj = MealWorkday.objects.get(employee=employee, date=local_date)
    if obj.closed_at is None:
        obj.closed_at = when
        obj.save(update_fields=['closed_at', 'updated_at'])
    return finalize_workday_meals(obj, now=when)


@transaction.atomic
def reopen_workday(employee, manager, reason, when=None):
    """Reopen today's accidentally closed workday, with a manager audit trail.

    Reopening is intentionally limited to the current local business day. It also
    reopens today's attendance record so an employee can continue working and
    submit a legitimate late-meal request if a meal window has already passed.
    """
    when = when or timezone.now()
    local_date = timezone.localtime(when).date()
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('A reason is required when reopening a workday.')
    if not manager.is_manager_role() and not manager.is_superuser:
        raise ValueError('Only a manager can reopen a workday.')
    if not manager.is_superuser:
        if not manager.managed_pharmacies.filter(pk=employee.pharmacy_id).exists() and not employee.pharmacy.managers.filter(pk=manager.pk).exists():
            raise ValueError('You do not manage this employee’s branch.')
    if local_date != timezone.localdate():
        raise ValueError('Only today’s workday can be reopened.')

    workday = MealWorkday.objects.select_for_update().filter(employee=employee, date=local_date).first()
    if not workday:
        raise ValueError('No workday was opened for this employee today.')
    if workday.closed_at is None:
        raise ValueError('This workday is already open.')

    workday.closed_at = None
    workday.reopened_at = when
    workday.reopened_by = manager
    workday.reopen_note = reason[:500]
    workday.save(update_fields=['closed_at', 'reopened_at', 'reopened_by', 'reopen_note', 'updated_at'])

    attendance = AttendanceRecord.objects.filter(employee=employee, date=local_date).first()
    if attendance and attendance.check_out_at:
        attendance.check_out_at = None
        attendance.status = AttendanceRecord.Status.VERIFIED
        attendance.review_note = f'Workday reopened by manager: {reason[:220]}'
        # The accidental check-out was already reported; clear the stamp so the employee's
        # real, final check-out is reported to the Boss as well (v97.18).
        attendance.check_out_sms_sent_at = None
        attendance.save(update_fields=['check_out_at', 'status', 'review_note', 'check_out_sms_sent_at', 'updated_at'])

    ensure_ledgers_for_workday(workday)
    return workday


def ensure_ledgers_for_workday(workday):
    employee = workday.employee
    date_value = workday.date
    entitlements, source, reason = _entitlements(employee, date_value)
    cutoffs = _cutoffs(employee, date_value)
    amounts = _amounts(employee)
    created = []
    for meal_type, entitled in entitlements.items():
        if not entitled or amounts[meal_type] <= 0:
            continue
        cutoff = cutoffs[meal_type]
        # A meal that already has a ledger row or a late/issue request is a manager-decided
        # entitlement (e.g. an employee who checked in after the window and asked for review).
        # It must stay in the settlement loop, otherwise it would never become EARNED.
        keep = (
            MealLedger.objects.filter(employee=employee, date=date_value, meal_type=meal_type).exists()
            or MealIssueReport.objects.filter(employee=employee, date=date_value, meal_type=meal_type).exists()
        )
        # If the employee starts after the meal's cutoff, that meal is not an entitlement.
        if workday.opened_at > cutoff and not keep:
            continue
        # If the employee closed before the meal cutoff, they did not reach that meal.
        if workday.closed_at and workday.closed_at < cutoff and not keep:
            continue
        obj, _ = MealLedger.objects.get_or_create(
            employee=employee, pharmacy=employee.pharmacy, date=date_value, meal_type=meal_type,
            defaults={
                'amount': amounts[meal_type], 'cutoff_at': cutoff,
                'source': source, 'reason': reason,
            },
        )
        # Keep amount/policy current while the ledger is still pending.
        if obj.status == MealLedger.Status.PENDING:
            changed=[]
            if obj.amount != amounts[meal_type]: obj.amount = amounts[meal_type]; changed.append('amount')
            if obj.cutoff_at != cutoff: obj.cutoff_at = cutoff; changed.append('cutoff_at')
            if changed: obj.save(update_fields=changed + ['updated_at'])
        created.append(obj)
    return created


def ensure_ledger_for_meal(employee, date_value, meal_type):
    """Return the ledger row for one meal, creating it when a manager decides on it.

    Used when an employee checked in after a meal window (so no ledger was generated
    automatically) but a manager approves, reopens or verifies that meal. The meal must
    still be an entitlement for that date (weekly policy or date override) and have an
    allowance greater than zero.
    """
    if meal_type not in MEAL_TYPES:
        raise ValueError('Choose a valid meal.')
    entitlements, source, reason = _entitlements(employee, date_value)
    if not entitlements.get(meal_type):
        raise ValueError(
            f'{employee.get_full_name() or employee.username} is not entitled to this meal on '
            f'{date_value:%d %b}. Add a Meal Override for that date first.'
        )
    amount = _amounts(employee)[meal_type]
    if amount <= 0:
        raise ValueError('No meal allowance is configured for this employee and meal.')
    ledger, _ = MealLedger.objects.get_or_create(
        employee=employee, pharmacy=employee.pharmacy, date=date_value, meal_type=meal_type,
        defaults={'amount': amount, 'cutoff_at': _cutoffs(employee, date_value)[meal_type],
                  'source': source, 'reason': reason or 'Created for manager decision.'},
    )
    return ledger


def _notify_debt(ledger):
    employee = ledger.employee
    from notifications.utils import management_recipients
    owner_qs = management_recipients(employee.pharmacy)
    title = f'Meal debt — {employee.get_full_name() or employee.username}'
    message = (
        f'{ledger.get_meal_type_display()} for {ledger.date:%d %b %Y} is now a meal debt '
        f'of TZS {ledger.amount:,.0f} because no completed sale was recorded by the branch that day.'
    )
    # Open the exact employee's meal-debt ledger, not the unrelated Customer Debts page.
    # The view scopes managers to the active branch and validates employee ownership.
    from django.urls import reverse
    from urllib.parse import urlencode
    link = reverse('accounts:meal_history') + '?' + urlencode({
        'employee': employee.pk, 'status': MealLedger.Status.DEBT,
    })
    for owner in owner_qs:
        notify(owner, title, message, category=Notification.Category.DEBT,
               level=Notification.Level.WARNING, link_url=link, pharmacy=employee.pharmacy)


@transaction.atomic
def reconcile_meal_funding_for_date(pharmacy, date_value, now=None):
    """Reconcile claimed meals against the branch business date.

    Sales can be entered late or synchronized offline. The accounting decision is
    therefore based on Sale.business_date, not Sale.created_at. If a previously
    funded day is later fully voided and no qualifying sale remains, a closed
    workday meal returns to debt so the ledger stays consistent with the sales
    record.
    """
    now = now or timezone.now()
    sellers = set(Sale.objects.filter(
        pharmacy=pharmacy, business_date=date_value,
        status__in=[Sale.Status.COMPLETED, Sale.Status.PARTIALLY_RETURNED],
    ).values_list('served_by_id', flat=True))
    qs = MealLedger.objects.select_for_update().filter(
        pharmacy=pharmacy, date=date_value, claimed_at__isnull=False,
    )
    changed = 0
    for ledger in qs:
        has_sales = ledger.employee_id in sellers
        if has_sales and ledger.status in (MealLedger.Status.PENDING, MealLedger.Status.DEBT):
            ledger.status = MealLedger.Status.EARNED
            ledger.earned_at = ledger.earned_at or now
            ledger.debt_at = None
            ledger.reason = 'Funded by the employee\'s own completed sales for the business date.'
            ledger.save(update_fields=['status', 'earned_at', 'debt_at', 'reason', 'updated_at'])
            changed += 1
        elif (not has_sales and ledger.status == MealLedger.Status.EARNED):
            workday = MealWorkday.objects.filter(employee_id=ledger.employee_id, date=date_value).first()
            if workday and workday.closed_at is not None:
                ledger.status = MealLedger.Status.DEBT
                ledger.debt_at = now
                ledger.reason = 'No completed sale by this employee remains for the business date.'
                ledger.save(update_fields=['status', 'debt_at', 'reason', 'updated_at'])
                changed += 1
    return changed


def finalize_workday_meals(workday, now=None):
    """Settle the workday's meal ledgers.

    Scan-to-claim rule: a meal is only issued (EARNED/DEBT accounting) when it was
    claimed inside its window, by QR scan or by a manager-verified exception.
    After the cut-off an unclaimed meal becomes MISSED: no meal, no cost, no debt.
    A meal with an open issue report stays PENDING until the manager decides.
    """
    now = now or timezone.now()
    employee = workday.employee
    ledgers = ensure_ledgers_for_workday(workday)
    has_sales = sales_exist(employee, workday.date)
    attendance = AttendanceRecord.objects.filter(employee=employee, date=workday.date).first()
    checkpoints = {}
    if attendance:
        for cp in attendance.checkpoints.filter(checkpoint_type__in=MEAL_TYPES):
            checkpoints[cp.checkpoint_type] = cp
    pending_issues = set(
        MealIssueReport.objects.filter(
            employee=employee, date=workday.date, status=MealIssueReport.Status.PENDING,
        ).values_list('meal_type', flat=True)
    )
    for ledger in ledgers:
        if ledger.status != MealLedger.Status.PENDING:
            continue
        claimed = ledger.claimed_at is not None
        cp = checkpoints.get(ledger.meal_type)
        if ledger.scan_required and not claimed and cp is not None:
            # Backfill the claim from the checkpoint (e.g. a scan recorded before ledger creation).
            ledger.claimed_at = cp.scanned_at
            ledger.claim_method = (MealLedger.ClaimMethod.MANUAL if cp.method == AttendanceCheckpoint.Method.MANUAL
                                   else MealLedger.ClaimMethod.QR)
            ledger.save(update_fields=['claimed_at', 'claim_method', 'updated_at'])
            claimed = True
        # A meal is settled only after its cut-off. Future meals are left pending.
        if now < ledger.cutoff_at:
            continue
        if ledger.scan_required and not claimed:
            if ledger.meal_type in pending_issues:
                continue  # waiting for the manager to verify the reported issue
            ledger.status = MealLedger.Status.MISSED
            ledger.missed_at = now
            if attendance is None:
                ledger.reason = 'Absent: no check-in on this day. Meal not paid.'
            else:
                ledger.reason = 'Not scanned within the meal window. Meal not paid.'
            ledger.save(update_fields=['status', 'missed_at', 'reason', 'updated_at'])
            continue
        if has_sales:
            ledger.status = MealLedger.Status.EARNED
            ledger.earned_at = ledger.cutoff_at
            ledger.reason = ledger.reason or 'Funded by employee sales.'
            ledger.save(update_fields=['status', 'earned_at', 'reason', 'updated_at'])
        elif claimed or (workday.closed_at is not None and workday.closed_at >= ledger.cutoff_at):
            # A claimed meal past its cut-off with no branch sale is debt now; it must not
            # wait for Close Workday (otherwise the dashboard shows TZS 0 / TZS 0 all day).
            ledger.status = MealLedger.Status.DEBT
            ledger.debt_at = now
            ledger.reason = ledger.reason or 'No completed branch sale recorded for the workday.'
            ledger.save(update_fields=['status', 'debt_at', 'reason', 'updated_at'])
            _notify_debt(ledger)
    reconcile_meal_funding_for_date(employee.pharmacy, workday.date, now=now)
    return list(MealLedger.objects.filter(employee=employee, date=workday.date))


def accrue_due_meals(now=None):
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    # Active/open workdays are evaluated dynamically; the command can safely run repeatedly.
    workdays = MealWorkday.objects.filter(date__lte=today).select_related('employee', 'employee__pharmacy')
    count = 0
    for workday in workdays.iterator():
        # Any previous calendar day left open is considered closed at the end
        # of that local day. This prevents an employee from keeping a day in
        # PENDING forever if they forgot to press Close Workday.
        if workday.date < today and workday.closed_at is None:
            tz = timezone.get_current_timezone()
            workday.closed_at = timezone.make_aware(datetime.combine(workday.date, time(23, 59, 59)), tz)
            workday.save(update_fields=['closed_at', 'updated_at'])
        before = MealLedger.objects.filter(employee=workday.employee, date=workday.date).count()
        finalize_workday_meals(workday, now=now)
        after = MealLedger.objects.filter(employee=workday.employee, date=workday.date).count()
        count += max(after - before, 0)
    return count


# ---------------------------------------------------------------------------
# Scan-to-claim: windows, claims, issue reports, manager verification
# ---------------------------------------------------------------------------
def meal_windows(employee, date_value):
    """Return {meal_type: (start_dt, end_dt, label)} using the branch meal timing."""
    timing = meal_timing_for(employee)
    return {
        'BREAKFAST': (_aware_on(date_value, timing.breakfast_start), _aware_on(date_value, timing.breakfast_cutoff), 'Breakfast'),
        'LUNCH': (_aware_on(date_value, timing.lunch_start), _aware_on(date_value, timing.lunch_cutoff), 'Lunch'),
        'DINNER': (_aware_on(date_value, timing.dinner_start), _aware_on(date_value, timing.dinner_cutoff), 'Dinner'),
    }


def claim_meal(employee, meal_type, when=None, method=MealLedger.ClaimMethod.QR, verified_by=None,
               date_value=None, reopen_missed=False):
    """Record a meal claim. QR is the normal proof; manager approval only opens a late scan.

    A manager-reopened meal may be scanned outside the original window, but the
    scan is still performed by the employee and remains auditable as QR.
    """
    when = when or timezone.now()
    local_date = date_value or timezone.localtime(when).date()
    workday = MealWorkday.objects.filter(employee=employee, date=local_date).first()
    if workday:
        ensure_ledgers_for_workday(workday)
    ledger = MealLedger.objects.filter(employee=employee, date=local_date, meal_type=meal_type).first()
    if not ledger:
        raise ValueError('This meal is not scheduled for the selected workday.')
    if ledger.status == MealLedger.Status.MISSED and reopen_missed and ledger.claimed_at is None:
        ledger.status = MealLedger.Status.PENDING
        ledger.missed_at = None
        ledger.save(update_fields=['status', 'missed_at', 'updated_at'])
    if ledger.claimed_at is not None:
        return ledger
    if ledger.status != MealLedger.Status.PENDING:
        raise ValueError('This meal is no longer available for scanning.')
    # QR may be used outside the normal window only when a manager explicitly reopened it.
    if method == MealLedger.ClaimMethod.QR:
        start, end, label = meal_windows(employee, local_date)[meal_type]
        local_when = timezone.localtime(when)
        if not (start <= local_when <= end) and not ledger.scan_reopened_at:
            raise ValueError(f'{label} is outside its claim window. Ask your manager to reopen it for a scan.')
    ledger.claimed_at = when
    ledger.claim_method = method
    ledger.verified_by = verified_by
    if method == MealLedger.ClaimMethod.QR and ledger.scan_reopened_at:
        ledger.reason = 'Employee scanned after manager-authorized late reopening.'
    ledger.save(update_fields=['claimed_at', 'claim_method', 'verified_by', 'reason', 'updated_at'])

    # Immediately account for every valid meal claim. If there is no qualifying
    # completed sale yet, mark it as debt now; a later same-branch, same-date sale
    # is reconciled to EARNED by the existing sales signal.
    if sales_exist(employee, local_date):
        reconcile_meal_funding_for_date(employee.pharmacy, local_date, now=when)
    else:
        ledger.status = MealLedger.Status.DEBT
        ledger.debt_at = when
        ledger.earned_at = None
        ledger.reason = (
            'QR meal claimed; awaiting a completed sale at this branch for the business date. '
            'Automatically reconciles to earned when qualifying sales are recorded.'
        )
        ledger.save(update_fields=['status', 'debt_at', 'earned_at', 'reason', 'updated_at'])
        _notify_debt(ledger)

    if method == MealLedger.ClaimMethod.QR:
        MealIssueReport.objects.filter(
            employee=employee, date=local_date, meal_type=meal_type,
            status=MealIssueReport.Status.SCAN_REOPENED,
        ).update(status=MealIssueReport.Status.RESOLVED, decided_at=when, decision_note='Employee scanned successfully after manager reopened the meal.')
    return ledger


@transaction.atomic
def reopen_meal_for_scan(ledger, manager, reason, note='', now=None):
    """Authorize the employee to scan a missed/late meal now. Never claims it for them."""
    now = now or timezone.now()
    ledger = MealLedger.objects.select_for_update().select_related('employee', 'pharmacy').get(pk=ledger.pk)
    employee, label = ledger.employee, ledger.get_meal_type_display()
    if employee.pk == manager.pk:
        raise ValueError('You cannot reopen your own meal. Ask another manager.')
    reason = (reason or '').strip()
    note = (note or '').strip()
    if len(reason) < 3:
        raise ValueError('A reason is required when reopening a meal for scanning.')
    today = timezone.localtime(now).date()
    if ledger.date != today:
        raise ValueError("A meal can only be reopened for an employee scan on today's workday.")
    attendance = AttendanceRecord.objects.filter(employee=employee, date=today).first()
    if not attendance or not attendance.check_in_at or not attendance.is_verified or attendance.check_out_at:
        raise ValueError(f'{employee.get_full_name() or employee.username} must have an open, verified workday.')
    if ledger.claimed_at is not None:
        raise ValueError(f'{label} is already recorded for this employee.')
    if ledger.status not in (MealLedger.Status.PENDING, MealLedger.Status.MISSED):
        raise ValueError('This meal is no longer available for reopening.')
    ledger.status = MealLedger.Status.PENDING
    ledger.missed_at = None
    ledger.scan_reopened_at = now
    ledger.scan_reopened_by = manager
    ledger.scan_reopen_note = f'{reason}. {note}'.strip()[:500]
    ledger.reason = 'Manager authorized a late employee scan.'
    ledger.save(update_fields=['status', 'missed_at', 'scan_reopened_at', 'scan_reopened_by', 'scan_reopen_note', 'reason', 'updated_at'])
    issue = MealIssueReport.objects.filter(employee=employee, date=today, meal_type=ledger.meal_type).order_by('-reported_at').first()
    if issue and issue.status in (MealIssueReport.Status.PENDING, MealIssueReport.Status.RETURNED):
        issue.status = MealIssueReport.Status.SCAN_REOPENED
        issue.decided_by = manager
        issue.decided_at = now
        issue.decision_note = f'Employee scan reopened. {reason}. {note}'.strip()[:500]
        issue.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
    notify(employee, f'{label} reopened for scan',
           f'Your {label.lower()} was reopened by your manager. Please scan the branch QR now to record it. Reason: {reason}.',
           category='SYSTEM', level='INFO', link_url='/dashboard/', pharmacy=ledger.pharmacy)
    return ledger


LATE_REASONS = ('BUSY_AT_WORK', 'NETWORK_PROBLEM', 'DEVICE_PROBLEM', 'OTHER')


def late_quota(employee, now=None):
    """(used, limit) of late meal requests for the employee in the current Monday-Sunday week."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    week_start = today - timedelta(days=today.weekday())
    used = MealIssueReport.objects.filter(
        employee=employee, is_late=True, date__gte=week_start, date__lte=today,
    ).exclude(status=MealIssueReport.Status.RETURNED).count()
    return used, meal_timing_for(employee).late_requests_per_week


def meal_board_for(employee, now, attendance):
    """Per-meal state for today, used by the employee dashboard, scan messages and reports."""
    local_now = timezone.localtime(now)
    date_value = local_now.date()
    entitlements, _, _ = _entitlements(employee, date_value)
    windows = meal_windows(employee, date_value)
    ledgers = {x.meal_type: x for x in MealLedger.objects.filter(employee=employee, date=date_value)}
    issues = {x.meal_type: x for x in MealIssueReport.objects.filter(employee=employee, date=date_value)}
    checkpoints = {}
    if attendance:
        checkpoints = {cp.checkpoint_type: cp for cp in attendance.checkpoints.filter(checkpoint_type__in=MEAL_TYPES)}
    verified = bool(attendance and attendance.is_verified and attendance.check_in_at and not attendance.check_out_at)
    late_used, late_limit = late_quota(employee, now)
    board = []
    for meal_type in MEAL_TYPES:
        if not entitlements.get(meal_type):
            continue
        start, end, label = windows[meal_type]
        ledger, issue, cp = ledgers.get(meal_type), issues.get(meal_type), checkpoints.get(meal_type)
        claimed_at = (ledger.claimed_at if ledger and ledger.claimed_at else (cp.scanned_at if cp else None))
        manual = bool((ledger and ledger.claim_method == MealLedger.ClaimMethod.MANUAL) or (cp and cp.method == AttendanceCheckpoint.Method.MANUAL))
        in_window = start <= now <= end
        if claimed_at:
            state = 'VERIFIED' if manual else 'CLAIMED'
        elif issue and issue.status == MealIssueReport.Status.SCAN_REOPENED and ledger and ledger.scan_reopened_at and not claimed_at:
            state = 'SCAN_REOPENED'
        elif issue and issue.status == MealIssueReport.Status.RETURNED:
            state = 'MISSED'
        elif issue and issue.status == MealIssueReport.Status.PENDING:
            state = 'REVIEW'
        elif ledger and ledger.status == MealLedger.Status.MISSED or now > end:
            state = 'MISSED'
        elif in_window:
            state = 'OPEN'
        else:
            state = 'UPCOMING'
        board.append({
            'meal_type': meal_type, 'label': label, 'start': start, 'end': end, 'state': state,
            'claimed_at': claimed_at, 'issue': issue,
            'can_scan': state in ('OPEN', 'SCAN_REOPENED') and verified,
            'can_report': state == 'OPEN' and verified and issue is None,
            'can_late': state == 'MISSED' and verified and (issue is None or issue.status == MealIssueReport.Status.RETURNED) and late_limit > 0,
            'late_left': max(late_limit - late_used, 0), 'late_limit': late_limit,
        })
    return board


def meal_wait_message(employee, now, attendance):
    """Explain why a scan outside a claimable window cannot issue a meal."""
    board = meal_board_for(employee, now, attendance)
    for item in board:
        if item['state'] == 'MISSED' and item['start'] <= now:
            return (f"{item['label']} window ended at {timezone.localtime(item['end']):%H:%M}. "
                    f"This meal can no longer be claimed.")
    for item in board:
        if item['state'] in ('CLAIMED', 'VERIFIED') and item['start'] <= now <= item['end']:
            return f"{item['label']} is already recorded for today."
    for item in board:
        if item['state'] == 'UPCOMING':
            return f"No meal is claimable yet. {item['label']} opens at {timezone.localtime(item['start']):%H:%M}."
    return 'No QR checkpoint is due right now.'


@transaction.atomic
def submit_meal_issue(employee, meal_type, reason, details, ip_address=None, late=False):
    """Employee reports a scanning problem (inside the window) or sends a late request (after it).

    Neither ever issues the meal: a manager must verify. Late requests are same-day only, need the
    employee to still be at work, require a written explanation and count against a weekly limit.
    """
    if meal_type not in MEAL_TYPES:
        raise ValueError('Choose a valid meal.')
    if reason not in dict(MealIssueReport.Reason.choices):
        raise ValueError('Choose the reason for your request.')
    now = timezone.now()
    local_date = timezone.localtime(now).date()
    attendance = AttendanceRecord.objects.filter(employee=employee, date=local_date).first()
    if not attendance or not attendance.is_verified or not attendance.check_in_at or attendance.check_out_at:
        raise ValueError('You must be at work with verified attendance before you can claim or request a meal.')
    entitlements, _, _ = _entitlements(employee, local_date)
    if not entitlements.get(meal_type):
        raise ValueError('You are not entitled to this meal today.')
    start, end, label = meal_windows(employee, local_date)[meal_type]
    details = (details or '').strip()
    if now < start:
        raise ValueError(f'{label} opens at {timezone.localtime(start):%H:%M}.')
    if attendance.checkpoints.filter(checkpoint_type=meal_type).exists():
        raise ValueError(f'{label} is already recorded for today.')
    existing_issue = MealIssueReport.objects.filter(employee=employee, date=local_date, meal_type=meal_type).first()
    if existing_issue and existing_issue.status not in (MealIssueReport.Status.RETURNED,):
        raise ValueError(f'You already sent a request for {label.lower()} today.')
    is_late = now > end
    if is_late:
        if not late:
            raise ValueError(f'{label} window ended at {timezone.localtime(end):%H:%M}. Use "Request late meal" if you were busy at work.')
        if reason not in LATE_REASONS:
            raise ValueError('Choose a valid reason for the late request.')
        if len(details) < 3:
            raise ValueError('Please explain why you could not claim this meal on time.')
        used, limit = late_quota(employee, now)
        if limit <= 0:
            raise ValueError('Late meal requests are turned off for your branch.')
        if used >= limit:
            raise ValueError(f'You have used all {limit} late meal requests for this week.')
    else:
        if late:
            raise ValueError(f'The {label.lower()} window is still open. Scan the QR, or report the problem.')
        if reason == MealIssueReport.Reason.BUSY_AT_WORK:
            raise ValueError('Choose the reason you could not scan.')
        if reason == MealIssueReport.Reason.OTHER and not details:
            raise ValueError('Please describe the problem.')
    if existing_issue and existing_issue.status == MealIssueReport.Status.RETURNED:
        issue = existing_issue
        issue.window_start_at, issue.window_end_at = start, end
        issue.reason, issue.details, issue.is_late = reason, details[:1000], is_late
        issue.status = MealIssueReport.Status.PENDING
        issue.decided_by = issue.decided_at = issue.returned_at = None
        issue.decision_note = ''
        issue.save(update_fields=['window_start_at', 'window_end_at', 'reason', 'details', 'is_late', 'status', 'decided_by', 'decided_at', 'returned_at', 'decision_note', 'updated_at'])
    else:
        issue = MealIssueReport.objects.create(
            employee=employee, pharmacy=employee.pharmacy, date=local_date, meal_type=meal_type,
            window_start_at=start, window_end_at=end, reason=reason, details=details[:1000],
            is_late=is_late, ip_address=ip_address,
        )
    workday = MealWorkday.objects.filter(employee=employee, date=local_date).first()
    if workday:
        ensure_ledgers_for_workday(workday)  # keeps the meal pending while the request is reviewed
    from notifications.utils import notify_many, management_recipients
    who = employee.get_full_name() or employee.username
    notify_many(
        management_recipients(employee.pharmacy),
        'Late meal request' if is_late else 'Meal verification issue reported',
        (f'{who} requested a late {label.lower()} at {employee.pharmacy.name} ({issue.reference}). Checked in at {timezone.localtime(attendance.check_in_at):%H:%M}.' if is_late
         else f'{who} reported a problem claiming {label} at {employee.pharmacy.name} ({issue.reference}).'),
        category='SYSTEM', level='WARNING', link_url='/accounts/meal-issues/', pharmacy=employee.pharmacy,
    )
    return issue


@transaction.atomic
def decide_meal_issue(issue, manager, decision, note='', reason=''):
    """Return, reopen for employee scan, or reject a meal request."""
    issue = MealIssueReport.objects.select_for_update().select_related('employee', 'pharmacy').get(pk=issue.pk)
    if issue.status not in (MealIssueReport.Status.PENDING, MealIssueReport.Status.RETURNED):
        raise ValueError('This request has already been decided.')
    note = (note or '').strip()
    if decision not in {'reopen', 'verify', 'return', 'reject'}:
        raise ValueError('Invalid meal request decision.')
    if decision in {'return', 'reject', 'verify'} and len(note) < 3:
        raise ValueError('Please give a clear note for this decision.')
    now = timezone.now()
    label = issue.get_meal_type_display()
    ledger = MealLedger.objects.filter(employee=issue.employee, date=issue.date, meal_type=issue.meal_type).first()
    if not ledger:
        # Employee checked in after the window, so no ledger was generated automatically.
        ledger = ensure_ledger_for_meal(issue.employee, issue.date, issue.meal_type)
    if decision == 'verify':
        if issue.date != timezone.localdate() and (timezone.localdate() - issue.date).days > MANUAL_GRANT_MAX_AGE_DAYS:
            raise ValueError('Meals can only be verified for today or yesterday.')
        manager_grant_meal(ledger, manager, reason or MealLedger.ManualReason.OTHER, note, now=now)
        issue.refresh_from_db()
        return issue
    if decision == 'reopen':
        if issue.date != timezone.localdate():
            raise ValueError("Only today's meal can be reopened for an employee scan.")
        reopen_meal_for_scan(ledger, manager, note or 'Manager approved the late meal request.', '', now=now)
        issue.refresh_from_db()
        if issue.status != MealIssueReport.Status.SCAN_REOPENED:
            issue.status = MealIssueReport.Status.SCAN_REOPENED
            issue.decided_by, issue.decided_at, issue.decision_note = manager, now, note[:500]
            issue.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
        return issue
    if decision == 'return':
        issue.status = MealIssueReport.Status.RETURNED
        issue.returned_at = now
        issue.return_count += 1
        issue.decided_by, issue.decided_at, issue.decision_note = manager, now, note[:500]
        issue.save(update_fields=['status', 'returned_at', 'return_count', 'decided_by', 'decided_at', 'decision_note'])
        notify(issue.employee, f'{label} request needs more information',
               f'Your {label.lower()} request was returned by your manager. Please update it and submit again. Manager note: {note}',
               category='SYSTEM', level='WARNING', link_url='/dashboard/', pharmacy=issue.pharmacy)
        return issue
    issue.status = MealIssueReport.Status.REJECTED
    issue.decided_by, issue.decided_at, issue.decision_note = manager, now, note[:500]
    issue.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
    notify(issue.employee, f'{label} request not approved',
           f'Your {label.lower()} request was not approved: {note}',
           category='SYSTEM', level='WARNING', link_url='/accounts/meal-history/', pharmacy=issue.pharmacy)
    return issue


MANUAL_GRANT_MAX_AGE_DAYS = 1  # a manager may verify today's and yesterday's meals only


def manual_grant_dates(now=None):
    today = timezone.localtime(now or timezone.now()).date()
    return [today - timedelta(days=n) for n in range(MANUAL_GRANT_MAX_AGE_DAYS + 1)]


@transaction.atomic
def manager_grant_meal(ledger, manager, reason, note='', now=None):
    """Manager verifies a meal directly (outage recovery). Reason is mandatory and fully audited.

    Allowed only for a meal that is still PENDING or already MISSED, for an employee with verified
    attendance that day, within the grace window. Never for the manager's own meal.
    """
    now = now or timezone.now()
    ledger = MealLedger.objects.select_for_update().select_related('employee', 'pharmacy').get(pk=ledger.pk)
    employee, label = ledger.employee, ledger.get_meal_type_display()
    if employee.pk == manager.pk:
        raise ValueError('You cannot verify your own meal. Ask another manager.')
    if reason not in dict(MealLedger.ManualReason.choices):
        raise ValueError('Choose the reason for verifying this meal.')
    note = (note or '').strip()
    if reason == MealLedger.ManualReason.OTHER and len(note) < 3:
        raise ValueError('Please describe the reason.')
    today = timezone.localtime(now).date()
    if ledger.date > today or (today - ledger.date).days > MANUAL_GRANT_MAX_AGE_DAYS:
        raise ValueError('Meals can only be verified for today or yesterday.')
    if ledger.claimed_at is not None:
        raise ValueError(f'{label} is already recorded for {employee.get_full_name() or employee.username}.')
    if ledger.status not in (MealLedger.Status.PENDING, MealLedger.Status.MISSED):
        raise ValueError('This meal has already been settled.')
    attendance = AttendanceRecord.objects.filter(employee=employee, date=ledger.date).first()
    if not attendance or not attendance.check_in_at or not attendance.is_verified:
        raise ValueError(f'{employee.get_full_name() or employee.username} has no verified attendance on that day.')
    reason_label = dict(MealLedger.ManualReason.choices)[reason]
    mgr_name = manager.get_full_name() or manager.username
    AttendanceCheckpoint.objects.get_or_create(
        attendance=attendance, checkpoint_type=ledger.meal_type,
        defaults={'method': AttendanceCheckpoint.Method.MANUAL,
                  'note': f'Manager verified by {mgr_name}: {reason_label}. {note}'.strip()[:255]},
    )
    if ledger.status == MealLedger.Status.MISSED:
        ledger.status = MealLedger.Status.PENDING
        ledger.missed_at = None
    ledger.claimed_at = now
    ledger.claim_method = MealLedger.ClaimMethod.MANUAL
    ledger.verified_by = manager
    ledger.manual_reason = reason
    ledger.manual_note = note[:500]
    ledger.scan_reopened_at = None
    ledger.scan_reopened_by = None
    ledger.scan_reopen_note = ''
    ledger.reason = f'Verified by manager ({reason_label}).'
    ledger.save(update_fields=['status', 'missed_at', 'claimed_at', 'claim_method', 'verified_by',
                               'manual_reason', 'manual_note', 'scan_reopened_at', 'scan_reopened_by', 'scan_reopen_note', 'reason', 'updated_at'])
    MealIssueReport.objects.filter(
        employee=employee, date=ledger.date, meal_type=ledger.meal_type, status__in=[MealIssueReport.Status.PENDING, MealIssueReport.Status.SCAN_REOPENED, MealIssueReport.Status.RETURNED],
    ).update(status=MealIssueReport.Status.APPROVED, decided_by=manager, decided_at=now,
             decision_note=f'Verified directly by manager: {reason_label}. {note}'.strip()[:500])
    workday = MealWorkday.objects.filter(employee=employee, date=ledger.date).first()
    if workday:
        finalize_workday_meals(workday, now=now)
    notify(
        employee, f'{label} verified',
        f'Your {label.lower()} on {ledger.date:%d %b} was verified by your manager ({reason_label}).',
        category='SYSTEM', level='INFO', link_url='/accounts/meal-history/', pharmacy=ledger.pharmacy,
    )
    return ledger


@transaction.atomic
def manager_grant_missing_meal(employee, meal_type, date_value, manager, reason, note='', now=None):
    """Verify a meal that has no ledger row yet (employee checked in after the meal window).

    Creates the ledger from the employee's policy, then applies the normal audited manual
    verification (same day/yesterday limit, verified attendance, reason + note, never own meal).
    """
    ledger = ensure_ledger_for_meal(employee, date_value, meal_type)
    return manager_grant_meal(ledger, manager, reason, note, now=now)


def bulk_grant_meals(pharmacy, manager, meal_type, date_value, reason, note, now=None):
    """Outage recovery: verify one meal for every unclaimed employee of the branch on that date."""
    note = (note or '').strip()
    if len(note) < 3:
        raise ValueError('A note describing the outage is required for bulk verification.')
    if meal_type not in MEAL_TYPES:
        raise ValueError('Choose a valid meal.')
    granted = skipped = 0
    qs = MealLedger.objects.filter(
        pharmacy=pharmacy, date=date_value, meal_type=meal_type, claimed_at__isnull=True,
        status__in=[MealLedger.Status.PENDING, MealLedger.Status.MISSED],
    ).select_related('employee')
    for ledger in qs:
        try:
            manager_grant_meal(ledger, manager, reason, note, now=now)
            granted += 1
        except ValueError:
            skipped += 1  # no verified attendance, own meal, or outside the grace window
    return granted, skipped


def branch_meals_today(pharmacy, now=None, date_value=None, manager_user=None):
    """Manager board: counts and per-employee rows for a day (today by default)."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    day = date_value or today
    if day == today:
        for wd in MealWorkday.objects.filter(employee__pharmacy=pharmacy, date=today).select_related('employee', 'employee__pharmacy'):
            finalize_workday_meals(wd, now=now)
    pending_issues = {
        (r.employee_id, r.meal_type): r
        for r in MealIssueReport.objects.filter(pharmacy=pharmacy, date=day, status=MealIssueReport.Status.PENDING)
    }
    rows, counts = [], {'scheduled': 0, 'claimed': 0, 'review': 0, 'missed': 0, 'not_issued': 0}
    grantable = {t: 0 for t in MEAL_TYPES}
    can_grant_day = day in manual_grant_dates(now)
    for x in MealLedger.objects.filter(pharmacy=pharmacy, date=day).select_related('employee', 'verified_by').order_by('cutoff_at', 'employee__first_name'):
        counts['scheduled'] += 1
        pending = pending_issues.get((x.employee_id, x.meal_type))
        if x.claimed_at:
            state = 'VERIFIED' if x.claim_method == MealLedger.ClaimMethod.MANUAL else 'CLAIMED'; counts['claimed'] += 1
        elif pending is not None:
            state = 'REVIEW'; counts['review'] += 1
        elif x.scan_reopened_at and x.status == MealLedger.Status.PENDING:
            state = 'SCAN_REOPENED'; counts['review'] += 1
        elif x.status == MealLedger.Status.MISSED:
            state = 'MISSED'; counts['missed'] += 1
        else:
            state = 'AWAITING'
        can_grant = (can_grant_day and x.claimed_at is None
                     and x.status in (MealLedger.Status.PENDING, MealLedger.Status.MISSED))
        if can_grant:
            grantable[x.meal_type] += 1
        rows.append({'ledger': x, 'employee': x.employee, 'meal_type': x.meal_type, 'meal_label': x.get_meal_type_display(),
                     'state': state, 'can_grant': can_grant, 'late': bool(pending is not None and pending.is_late)})
    # Meals the employee is entitled to but that never got a ledger row (checked in after the window).
    # They are not scheduled, cost nothing and create no debt until a manager verifies them.
    present = AttendanceRecord.objects.filter(
        pharmacy=pharmacy, date=day, check_in_at__isnull=False,
        status__in=[AttendanceRecord.Status.VERIFIED, AttendanceRecord.Status.CLOSED],
        employee__role=User.Role.EMPLOYEE, employee__is_active=True,
    ).select_related('employee')
    existing = set(MealLedger.objects.filter(pharmacy=pharmacy, date=day).values_list('employee_id', 'meal_type'))
    labels = dict(MealLedger.MealType.choices)
    for att in present:
        emp = att.employee
        entitled, _, _ = _entitlements(emp, day)
        amounts = _amounts(emp)
        for meal_type in MEAL_TYPES:
            if not entitled.get(meal_type) or amounts[meal_type] <= 0 or (emp.pk, meal_type) in existing:
                continue
            counts['not_issued'] += 1
            rows.append({'ledger': None, 'employee': emp, 'meal_type': meal_type, 'meal_label': labels.get(meal_type, meal_type.title()),
                         'state': 'NOT_ISSUED', 'can_grant': can_grant_day and emp.pk != getattr(manager_user, 'pk', None), 'late': False})
    return counts, rows, grantable


# ---------------------------------------------------------------------------
# Meal History helpers (V50.16): settle stale rows + show absent days as "not paid"
# ---------------------------------------------------------------------------
ABSENT_REASON = 'Absent: no check-in on this day. Meal not paid.'


def settle_overdue_meals_for(employee, now=None):
    """Settle this employee's own past-cutoff PENDING meals right now.

    Meal History must never show a past meal as an unexplained "Pending" just because
    the periodic accrue_meals job has not run yet. Safe to call repeatedly.
    """
    now = now or timezone.now()
    overdue_dates = set(
        MealLedger.objects.filter(
            employee=employee, status=MealLedger.Status.PENDING, cutoff_at__lte=now,
        ).values_list('date', flat=True)
    )
    if not overdue_dates:
        return 0
    today = timezone.localtime(now).date()
    tz = timezone.get_current_timezone()
    settled = 0
    for workday in MealWorkday.objects.filter(employee=employee, date__in=overdue_dates).select_related('employee', 'employee__pharmacy'):
        if workday.date < today and workday.closed_at is None:
            workday.closed_at = timezone.make_aware(datetime.combine(workday.date, time(23, 59, 59)), tz)
            workday.save(update_fields=['closed_at', 'updated_at'])
        finalize_workday_meals(workday, now=now)
        settled += 1
    # Pending rows that have no workday at all can never be earned: the employee did not work.
    orphan_dates = overdue_dates - set(
        MealWorkday.objects.filter(employee=employee, date__in=overdue_dates).values_list('date', flat=True)
    )
    if orphan_dates:
        attended = set(AttendanceRecord.objects.filter(employee=employee, date__in=orphan_dates).values_list('date', flat=True))
        for ledger in MealLedger.objects.filter(
            employee=employee, status=MealLedger.Status.PENDING, cutoff_at__lte=now,
            date__in=orphan_dates, claimed_at__isnull=True,
        ):
            ledger.status = MealLedger.Status.MISSED
            ledger.missed_at = now
            ledger.reason = ('Not scanned within the meal window. Meal not paid.'
                             if ledger.date in attended else ABSENT_REASON)
            ledger.save(update_fields=['status', 'missed_at', 'reason', 'updated_at'])
            settled += 1
    return settled


def absent_meal_rows(employee, date_from=None, date_to=None, now=None, max_days=92):
    """Entitled meals on days the employee did not work: (date, meal_type, amount, cutoff).

    No ledger row exists for such days, so they used to be invisible. They are reported as
    "Not paid" and never create cost, debt or salary changes.
    """
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    last = min(date_to or (today - timedelta(days=1)), today - timedelta(days=1))
    first = max(date_from or (last - timedelta(days=max_days - 1)), employee.created_at.date(), last - timedelta(days=max_days - 1))
    if first > last:
        return []
    worked = set(MealWorkday.objects.filter(employee=employee, date__range=(first, last)).values_list('date', flat=True))
    worked |= set(AttendanceRecord.objects.filter(employee=employee, date__range=(first, last)).values_list('date', flat=True))
    worked |= set(MealLedger.objects.filter(employee=employee, date__range=(first, last)).values_list('date', flat=True))
    amounts = _amounts(employee)
    rows = []
    day = first
    while day <= last:
        if day not in worked:
            entitlements, _source, _reason = _entitlements(employee, day)
            cutoffs = _cutoffs(employee, day)
            for meal_type in MEAL_TYPES:
                if entitlements.get(meal_type) and amounts[meal_type] > 0:
                    rows.append((day, meal_type, amounts[meal_type], cutoffs[meal_type]))
        day += timedelta(days=1)
    return rows
