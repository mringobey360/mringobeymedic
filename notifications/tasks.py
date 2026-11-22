import logging

from celery import shared_task
from django.urls import reverse
from django.utils import timezone
from datetime import datetime, timedelta

from accounts.models import MealReminderDelivery, MealTimingSettings, PushSubscription, User
from accounts.meal_services import _entitlements
from .models import Notification
from .push import send_web_push
from .utils import notify

logger = logging.getLogger(__name__)

MEALS = {
    "BREAKFAST": ("breakfast_start", "Breakfast"),
    "LUNCH": ("lunch_start", "Lunch"),
    "DINNER": ("dinner_start", "Dinner"),
}

@shared_task
def dispatch_due_meal_reminders():
    now = timezone.localtime()
    today = now.date()
    sent_total = 0
    for timing in MealTimingSettings.objects.select_related("pharmacy").iterator():
        pharmacy = timing.pharmacy
        if not pharmacy.is_active:
            continue
        for meal_type, (field, label) in MEALS.items():
            start = getattr(timing, field)
            scheduled = timezone.make_aware(datetime.combine(today, start), timezone.get_current_timezone())
            scheduled -= timedelta(minutes=int(timing.reminder_minutes_before or 0))
            if abs((now - scheduled).total_seconds()) > 59:
                continue
            delivery, created = MealReminderDelivery.objects.get_or_create(
                pharmacy=pharmacy, date=today, meal_type=meal_type, defaults={"scheduled_for": scheduled}
            )
            if not created:
                continue
            delivery_count = 0
            employees = User.objects.filter(pharmacy=pharmacy, role=User.Role.EMPLOYEE, is_active=True).prefetch_related("meal_schedules", "meal_adjustments")
            for employee in employees.iterator(chunk_size=500):
                entitlements, _, _ = _entitlements(employee, today)
                if not entitlements.get(meal_type):
                    continue
                title = f"{label} time — {pharmacy.name}"
                message = f"Your {label.lower()} window is now open. Please scan the branch QR to record your meal."
                notify(
                    employee, title, message, category=Notification.Category.SYSTEM,
                    level=Notification.Level.INFO, link_url="/accounts/attendance/qr/scan/",
                    pharmacy=pharmacy,
                    dedupe_key=f"meal-reminder:{pharmacy.pk}:{today}:{meal_type}:{employee.pk}",
                )
                sent_total += 1
                delivery_count += 1
            delivery.delivered_count = delivery_count
            delivery.save(update_fields=["delivered_count"])
    return {"notifications_created": sent_total}

@shared_task
def dispatch_pending_web_push_notifications(batch_size=100):
    """Deliver all new in-app notifications to subscribed devices.

    Push is intentionally asynchronous: a slow browser push provider must never
    slow down a POS, sale, expense approval, or attendance request.
    """
    qs = Notification.objects.filter(
        push_sent_at__isnull=True,
        push_attempts__lt=5,
        recipient__is_active=True,
    ).select_related('recipient')[:batch_size]
    sent = failed = 0
    for notification in qs:
        subscriptions = PushSubscription.objects.filter(user=notification.recipient, active=True)
        if not subscriptions.exists():
            notification.push_sent_at = timezone.now()
            notification.push_last_error = 'No active push subscription.'
            notification.save(update_fields=['push_sent_at', 'push_last_error'])
            continue
        notification.push_attempts += 1
        notification.push_last_error = ''
        notification.save(update_fields=['push_attempts', 'push_last_error'])
        any_success = False
        last_error = ''
        for sub in subscriptions:
            ok, error = send_web_push(
                sub,
                notification.title,
                notification.message,
                reverse('notifications:open', args=[notification.pk]),
                tag=f'pharmacy-{notification.category.lower()}',
            )
            if ok:
                any_success = True
                sent += 1
            else:
                failed += 1
                last_error = error or 'Push provider rejected delivery.'
                sub.last_error = last_error
                if '410' in last_error or '404' in last_error:
                    sub.active = False
                sub.save(update_fields=['last_error', 'active', 'updated_at'])
        if any_success or notification.push_attempts >= 5:
            notification.push_sent_at = timezone.now()
        notification.push_last_error = '' if any_success else last_error
        notification.save(update_fields=['push_sent_at', 'push_last_error'])
    return {'sent': sent, 'failed': failed}


@shared_task
def auto_close_completed_cash_days():
    """Reconcile completed business days and prepare the next opening balance."""
    from pharmacies.models import Pharmacy
    from .automation import audit_cash_day, audit_cash_activity_history, _managers
    from django.conf import settings
    if not getattr(settings, 'CASH_AUTO_CLOSE_ENABLED', True):
        return {'enabled': False}
    today = timezone.localdate()
    system_user = User.objects.filter(is_superuser=True, is_active=True).first()
    results = {'branches': 0, 'day_ended': 0, 'blocked': 0, 'prepared_today': 0, 'historical_activity_days': 0}
    for pharmacy in Pharmacy.objects.filter(is_active=True):
        results['branches'] += 1
        actor = system_user or _managers(pharmacy).first()
        if not actor:
            results['blocked'] += 1
            continue
        try:
            history = audit_cash_activity_history(pharmacy, today)
            results['historical_activity_days'] += history['activity_days']
            result = audit_cash_day(pharmacy, actor, today)
            if result == 'day_ended':
                results['day_ended'] += 1
            elif result in {'blocked', 'pending', 'variance', 'open_previous'}:
                results['blocked'] += 1
            from expenses.models import CashDay
            if CashDay.objects.filter(pharmacy=pharmacy, date=today).exists():
                results['prepared_today'] += 1
        except Exception as exc:
            results['blocked'] += 1
            for manager in _managers(pharmacy):
                notify(
                    manager, 'Automatic cash control failed',
                    f'Automatic cash reconciliation could not complete for {pharmacy.name}: {str(exc)[:500]}',
                    category=Notification.Category.SYSTEM, level=Notification.Level.DANGER,
                    link_url='/expenses/cash/', pharmacy=pharmacy,
                    dedupe_key=f'cash-auto-error:{pharmacy.pk}:{today}',
                )
    return results


@shared_task
def run_professional_automations():
    """Run branch-wide proactive controls every few minutes."""
    from pharmacies.models import Pharmacy
    today = timezone.localdate()
    results = {'branches': 0, 'cash_alerts': 0, 'stock_alerts': 0}
    system_user = User.objects.filter(is_superuser=True, is_active=True).first()
    from .automation import audit_inventory, _managers
    for pharmacy in Pharmacy.objects.filter(is_active=True):
        results['branches'] += 1
        try:
            results['stock_alerts'] += audit_inventory(pharmacy, today)
        except Exception as exc:
            # A single malformed branch must never stop automation for every other branch.
            for manager in _managers(pharmacy):
                notify(
                    manager,
                    'Automation needs attention',
                    f'Automated branch checks could not complete for {pharmacy.name}: {str(exc)[:500]}',
                    category=Notification.Category.SYSTEM,
                    level=Notification.Level.DANGER,
                    link_url='/notifications/',
                    pharmacy=pharmacy,
                    dedupe_key=f'automation-error:{pharmacy.pk}:{today}',
                )
    return results


@shared_task
def process_branch_billing():
    """Hourly: per-branch subscription reminders (idempotent) + billing audit trail."""
    from pharmacies.billing_notices import process_branch_billing as run
    return run()


@shared_task
def run_sales_integrity_checks():
    """Every 30 min: verify yesterday+today reconcile, auto-repair safe status drift,
    and alert managers (once per day per problem) about anything that needs a human."""
    from datetime import timedelta
    from pharmacies.models import Pharmacy
    from dashboard.reconciliation import run_integrity_checks
    from .automation import _managers
    today = timezone.localdate()
    results = {'branches': 0, 'fixed': 0, 'alerts': 0}
    for pharmacy in Pharmacy.objects.filter(is_active=True):
        results['branches'] += 1
        try:
            issues = run_integrity_checks(pharmacy, today - timedelta(days=1), today, fix=True)
        except Exception as exc:
            issues = []
            logger.exception('Integrity check failed for %s: %s', pharmacy, exc)
        results['fixed'] += sum(1 for i in issues if i.fixed)
        for issue in issues:
            if issue.fixed or issue.severity == 'info':
                continue
            for manager in _managers(pharmacy):
                notify(manager, 'Report check needs attention', issue.message,
                       category=Notification.Category.SYSTEM,
                       level=Notification.Level.DANGER if issue.severity == 'danger' else Notification.Level.WARNING,
                       link_url='/dashboard/', pharmacy=pharmacy,
                       dedupe_key=f'integrity:{pharmacy.pk}:{issue.code}:{today}')
                results['alerts'] += 1
    return results


@shared_task
def dispatch_due_system_notices():
    """Every minute: send bell notifications for published notices whose start time has arrived."""
    from hq.notice_delivery import send_due_notice_notifications
    return send_due_notice_notifications()
