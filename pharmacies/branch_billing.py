"""Per-branch subscription state (staged access policy).

Every branch is judged ONLY by its own billing:
  * additional branch  -> its BranchAddOn.next_billing_date
  * primary/included   -> the owner's account Subscription.end_date
so one branch lapsing never locks or alerts another one.

Stages (the due date itself is still fully usable):
  OK        more than RENEWAL_WINDOW days left
  DUE_SOON  within the renewal window (alert only)
  GRACE     up to GRACE days overdue  (works normally, strong warning)
  READ_ONLY next READONLY days        (view data/reports, no new sales/changes)
  LOCKED    afterwards                (only billing / backup-export for the owner)
"""
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

OK, DUE_SOON, GRACE, READ_ONLY, LOCKED = 'OK', 'DUE_SOON', 'GRACE', 'READ_ONLY', 'LOCKED'
STAGE_LABELS = {OK: 'Active', DUE_SOON: 'Due soon', GRACE: 'Grace period', READ_ONLY: 'Read-only', LOCKED: 'Locked'}

# Backwards compatible constant (older code/tests import it).
RENEWAL_WINDOW_DAYS = 10
# Days before a free trial ends when the countdown banner starts to appear.
TRIAL_BANNER_DAYS = 7


def policy():
    return {
        'window': int(getattr(settings, 'BRANCH_BILLING_RENEWAL_WINDOW_DAYS', 10)),
        'grace': int(getattr(settings, 'BRANCH_BILLING_GRACE_DAYS', 5)),
        'readonly': int(getattr(settings, 'BRANCH_BILLING_READONLY_DAYS', 25)),
    }


def classify(days_left, cfg=None):
    """Pure function: stage for a number of days left (negative = overdue)."""
    cfg = cfg or policy()
    if days_left is None:
        return DUE_SOON
    if days_left > cfg['window']:
        return OK
    if days_left >= 0:
        return DUE_SOON
    overdue = -days_left
    if overdue <= cfg['grace']:
        return GRACE
    if overdue <= cfg['grace'] + cfg['readonly']:
        return READ_ONLY
    return LOCKED


def _primary_due(pharmacy):
    """(due_date, force_locked, subscription) for a branch without an add-on."""
    from hq.models import Subscription
    manager_ids = pharmacy.managers.values_list('pk', flat=True)
    sub = Subscription.objects.filter(owner_id__in=manager_ids).order_by('-end_date').first()
    if sub is None:
        return None, False, None
    today = timezone.localdate()
    if sub.is_trial and (sub.end_date < today or sub.status != Subscription.Status.ACTIVE):
        # A finished free trial has NO grace or read-only stage: it locks completely until upgrade.
        return sub.end_date, True, sub
    if sub.status == Subscription.Status.CANCELLED:
        return sub.end_date, True, sub
    if sub.status == Subscription.Status.EXPIRED and sub.end_date >= today:
        return sub.end_date, True, sub  # manually expired by the platform admin
    return sub.end_date, False, sub


def branch_billing_state(pharmacy, today=None):
    """State dict for a branch, or None when no staged billing applies
    (pending/cancelled add-on, or a primary branch with no subscription at all:
    the legacy account-level lock handles those)."""
    if pharmacy is None:
        return None
    today = today or timezone.localdate()
    cfg = policy()
    addon = getattr(pharmacy, 'addon', None)
    force_locked = False
    is_trial = False
    trial_timed = False
    if addon is not None:
        if addon.status != addon.Status.ACTIVE:
            return None
        kind, due = 'ADDON', addon.next_billing_date
        renew_url = reverse('pharmacies:branch_addon_payment', args=[pharmacy.pk])
    else:
        kind = 'PRIMARY'
        due, force_locked, sub = _primary_due(pharmacy)
        if sub is None:
            return None
        is_trial = bool(sub.is_trial)
        trial_timed = bool(sub.is_timed_trial)
        renew_url = reverse('hq:packages_center') + '?renew=1'
    days_left = None if due is None else (due - today).days
    stage = LOCKED if force_locked else classify(days_left, cfg)
    overdue_days = 0 if days_left is None or days_left >= 0 else -days_left
    grace_days_left = max(0, cfg['grace'] - overdue_days) if stage == GRACE else None
    lock_in_days = None
    if stage in (GRACE, READ_ONLY):
        lock_in_days = cfg['grace'] + cfg['readonly'] + 1 - overdue_days
    # Data for the dynamic renewal banner: how much of the current stage is left
    # (progress bar) and the date the NEXT stage begins (live countdown).
    deadline, progress = None, 0
    if due is not None:
        if stage == DUE_SOON and days_left is not None:
            deadline, progress = due, round(max(0, min(100, days_left * 100 / max(cfg['window'], 1))))
        elif stage == GRACE:
            deadline = due + timedelta(days=cfg['grace'])
            progress = round(max(0, min(100, (grace_days_left or 0) * 100 / max(cfg['grace'], 1))))
        elif stage == READ_ONLY:
            deadline = due + timedelta(days=cfg['grace'] + cfg['readonly'])
            progress = round(max(0, min(100, (lock_in_days or 0) * 100 / max(cfg['readonly'], 1))))
    trial_days_total = 0
    free_usage, free_value = None, None
    if is_trial and not trial_timed and not force_locked:
        # Legacy lifetime Free Plan: the banner shows this month's sales allowance instead of a countdown.
        try:
            from hq.free_plan import account_value, cached_usage
            free_usage = cached_usage(sub)
            free_value = account_value(sub)
            progress = free_usage['percent']
        except Exception:   # a banner must never break a page
            free_usage = None
    return {
        'trial': is_trial, 'trial_timed': trial_timed, 'trial_days_total': trial_days_total,
        'free_usage': free_usage, 'free_value': free_value,
        'progress_pct': progress,
        'deadline_iso': deadline.isoformat() if deadline else '',
        'pharmacy_id': pharmacy.pk, 'name': pharmacy.name, 'kind': kind,
        'due_date': due, 'days_left': days_left, 'days_overdue': overdue_days,
        'stage': stage, 'stage_label': STAGE_LABELS[stage],
        'overdue': overdue_days > 0 or force_locked,
        'due_soon': stage == DUE_SOON,
        'grace': stage == GRACE,
        'read_only': stage == READ_ONLY,
        'locked': stage == LOCKED,
        'forced': bool(force_locked),   # deliberate lock (cancelled / expired by admin / finished trial): never view-only
        # The 30-day trial shows its banner only when the end is near (last TRIAL_BANNER_DAYS days); the legacy
        # lifetime Free Plan keeps its always-on usage meter.
        'alert': stage != OK or (is_trial and not force_locked and (
            not trial_timed or (days_left is not None and days_left <= TRIAL_BANNER_DAYS))),
        'grace_days_left': grace_days_left,
        'readonly_in_days': (cfg['grace'] + 1 - overdue_days) if stage == GRACE else None,
        'lock_in_days': lock_in_days,
        'renew_url': renew_url,
    }


def is_branch_locked(pharmacy, today=None):
    state = branch_billing_state(pharmacy, today)
    return bool(state and state['locked'])
