"""Professional cross-module automation and alert rules.

The rules here are deliberately idempotent: every recurring alert carries a
stable dedupe key, so a Celery/Beat retry cannot spam users.
"""
import hashlib
from decimal import Decimal

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from expenses.models import CashDay, Expense
from expenses.services import ensure_cash_day_for_date, end_cash_day_automatically, get_cash_activity_history, rollover_business_day
from inventory.models import Product
from .models import Notification
from .utils import notify


def _managers(pharmacy):
    """Return branch owners and their active, authorized manager assistants."""
    owners = User.objects.filter(
        role=User.Role.ADMIN, is_active=True, managed_pharmacies=pharmacy,
    ).distinct()
    owner_ids = owners.values_list('pk', flat=True)
    assistants = User.objects.filter(
        role=User.Role.ASSISTANT, is_active=True,
        assistant_profile__active=True,
        assistant_profile__manager_id__in=owner_ids,
    ).distinct()
    # Keep alerts branch-scoped: an assistant receives a branch notice only if
    # their delegated profile currently grants access to that specific branch.
    from accounts.models import ManagerAssistant
    allowed_ids = []
    for profile in ManagerAssistant.objects.filter(
        assistant__in=assistants, active=True,
    ).prefetch_related('branches'):
        if profile.branch_access_mode == ManagerAssistant.BranchAccessMode.ALL_MANAGER_BRANCHES:
            if profile.manager.managed_pharmacies.filter(pk=pharmacy.pk).exists():
                allowed_ids.append(profile.assistant_id)
        elif profile.branches.filter(pk=pharmacy.pk).exists():
            allowed_ids.append(profile.assistant_id)
    # Combine by primary key: OR-ing a .distinct() queryset with a plain one
    # raises "Cannot combine a unique query with a non-unique query".
    return User.objects.filter(
        Q(pk__in=list(owner_ids)) | Q(pk__in=allowed_ids), is_active=True,
    ).distinct()


def _alert_all(pharmacy, title, message, *, level, category, dedupe_key, link=''):
    recipients = list(_managers(pharmacy))
    created = 0
    for user in recipients:
        before = Notification.objects.filter(recipient=user, dedupe_key=dedupe_key).exists()
        notify(user, title, message, category=category, level=level,
               link_url=link, pharmacy=pharmacy, dedupe_key=dedupe_key)
        if not before:
            created += 1
    return created


def audit_cash_activity_history(pharmacy, today=None):
    """Scan source ledgers so historical cash activity remains visible and traceable."""
    today = today or timezone.localdate()
    rows = get_cash_activity_history(pharmacy, end_date=today, limit_days=90)
    activity_days = sum(1 for row in rows if row['cash_sales'] or row['cash_refunds'] or row['cash_expenses'])
    return {'activity_days': activity_days, 'latest_activity_date': rows[0]['date'] if rows else None}


def audit_cash_day(pharmacy, user, today=None):
    """End completed business dates, then prepare today only after verified carry-forward."""
    today = today or timezone.localdate()
    now = timezone.now()
    day_ended = 0
    try:
        current, ended_days = rollover_business_day(pharmacy, user, now=now)
        day_ended = len(ended_days)
        for ended in ended_days:
            _alert_all(pharmacy, 'Business day ended automatically',
                f'{ended.date:%d %b %Y} reached the midnight business boundary and is now locked for new transactions. This is not a physical cash close; a manager must count and reconcile the drawer.',
                level=Notification.Level.INFO, category=Notification.Category.SALES,
                dedupe_key=f'cash-day-ended:{pharmacy.pk}:{ended.pk}', link=reverse('expenses:cash_close', args=[ended.pk]))
    except ValueError as exc:
        _alert_all(pharmacy, 'Cash control needs attention', str(exc),
            level=Notification.Level.WARNING, category=Notification.Category.SALES,
            dedupe_key=f'cash-blocked:{pharmacy.pk}:{today}', link=reverse('expenses:cash'))
        return 'blocked'

    if current is None:
        try:
            current = ensure_cash_day_for_date(pharmacy, user, today)
        except ValueError as exc:
            _alert_all(pharmacy, 'Cash control needs attention', str(exc),
                level=Notification.Level.WARNING, category=Notification.Category.SALES,
                dedupe_key=f'cash-blocked:{pharmacy.pk}:{today}', link=reverse('expenses:cash'))
            return 'blocked'
    if current is not None and current.opening_is_provisional:
        _alert_all(pharmacy, 'Today is trading with a provisional opening',
            f'{today:%d %b %Y} can continue operating normally. Its opening cash is provisional until the previous cash day is physically verified; the system will reconcile the opening automatically and keep the audit trail.',
            level=Notification.Level.INFO, category=Notification.Category.SALES,
            dedupe_key=f'cash-provisional-opening:{pharmacy.pk}:{current.pk}', link=reverse('expenses:cash'))
    if current is None:
        _alert_all(pharmacy, 'Cash control setup required',
            'No verified cash anchor exists yet. Count the first physical opening cash once; future verified closing balances will carry forward automatically.',
            level=Notification.Level.INFO, category=Notification.Category.SALES,
            dedupe_key=f'cash-first-day:{pharmacy.pk}', link=reverse('expenses:cash_open'))
        return 'first_day'
    current.automation_checked_at = now
    current.save(update_fields=['automation_checked_at'])
    if current.status == CashDay.Status.CLOSED and current.cash_difference not in (None, Decimal('0')):
        diff=current.cash_difference; sign='+' if diff > 0 else ''
        _alert_all(pharmacy, 'Cash variance detected', f'{current.date:%d %b %Y}: closing cash differs from expected by TZS {sign}{diff:,.0f}. Review the reconciliation.', level=Notification.Level.DANGER, category=Notification.Category.SALES, dedupe_key=f'cash-variance:{pharmacy.pk}:{current.pk}:{current.cash_difference}', link=reverse('expenses:cash'))
        return 'variance'
    pending=Expense.objects.filter(pharmacy=pharmacy, expense_date=today, status=Expense.Status.PENDING, payment_source=Expense.PaymentSource.CASH_SALES).count()
    if pending:
        _alert_all(pharmacy, 'Cash expenses awaiting approval', f'{pending} sales-cash expense(s) are waiting for approval. They will block final cash closing until they are resolved.', level=Notification.Level.INFO, category=Notification.Category.SALES, dedupe_key=f'cash-pending-today:{pharmacy.pk}:{today}', link=reverse('expenses:list'))
        return 'pending'
    return 'day_ended' if day_ended else 'ok'

def _digest(pharmacy, kind, title, items, *, level, link, today, noun='product'):
    """One grouped alert per kind (expired / expiring / low stock) instead of one per product.

    The dedupe key contains a fingerprint of the affected products, so the same list never
    repeats during the day, but a genuinely new product re-triggers a fresh, updated summary.
    """
    if not items:
        return 0
    items = sorted(items, key=lambda row: row[1].lower())
    fingerprint = hashlib.md5(','.join(str(pk) for pk, _, _ in items).encode()).hexdigest()[:10]
    count = len(items)
    names = ', '.join(name for _, name, _ in items[:4])
    more = f' and {count - 4} more' if count > 4 else ''
    plural = noun if count == 1 else noun + 's'
    message = f'{count} {plural}: {names}{more}. Open the list to review and reorder.'
    return _alert_all(
        pharmacy, title.format(count=count, plural=plural), message,
        level=level, category=Notification.Category.STOCK_ALERT,
        dedupe_key=f'{kind}-digest:{pharmacy.pk}:{today}:{fingerprint}', link=link,
    )


def audit_inventory(pharmacy, today=None):
    """Create grouped, actionable stock alerts (one summary per kind) without spam."""
    today = today or timezone.localdate()
    created = 0
    expired, expiring, low = [], [], []
    products = Product.objects.filter(pharmacy=pharmacy, is_active=True).prefetch_related('batches')
    for product in products:
        if product.is_expired:
            expired.append((product.pk, product.name, None))
        elif product.is_expiring_soon:
            expiring.append((product.pk, product.name, product.days_to_expiry))
        if product.is_low_stock:
            low.append((product.pk, product.name, product.total_quantity))
    list_url = reverse('inventory:product_list')
    created += _digest(pharmacy, 'expired', 'Expired stock needs action ({count} {plural})', expired,
                       level=Notification.Level.DANGER, link=list_url + '?stock=expiring', today=today)
    created += _digest(pharmacy, 'expiry', 'Expiry approaching ({count} {plural})', expiring,
                       level=Notification.Level.WARNING, link=list_url + '?stock=expiring', today=today)
    created += _digest(pharmacy, 'low-stock', 'Low stock ({count} {plural})', low,
                       level=Notification.Level.WARNING, link=list_url + '?stock=low', today=today)
    return created
