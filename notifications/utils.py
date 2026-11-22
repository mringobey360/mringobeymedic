import logging

from django.core.cache import cache
from django.utils import timezone
from .models import Notification

logger = logging.getLogger(__name__)

# These events always belong to a single shop branch and must never silently
# become global if a notification producer forgets to supply ``pharmacy``.
BRANCH_BOUND_CATEGORIES = {
    Notification.Category.STOCK_ALERT,
    Notification.Category.SALES,
    Notification.Category.DEBT,
    Notification.Category.CHAT,
}
_UNSET = object()


def _infer_branch_for_notification(recipient, category):
    """Infer branch ownership only when the recipient's branch is unambiguous.

    Employees are tied to one branch. A manager with exactly one managed branch
    is also unambiguous. For multi-branch managers, callers must pass the branch
    that generated the event; guessing could leak alerts across branches.
    """
    user_branch = getattr(recipient, 'pharmacy', None)
    if user_branch is not None:
        return user_branch
    try:
        if hasattr(recipient, 'accessible_management_pharmacies'):
            branches = recipient.accessible_management_pharmacies().filter(is_active=True)
            first = branches.first()
            if first is not None and not branches.exclude(pk=first.pk).exists():
                return first
    except Exception:
        logger.exception('Unable to infer notification branch for user %s', getattr(recipient, 'pk', '?'))
    logger.warning(
        'Skipped branch-bound notification for recipient=%s category=%s because branch was not supplied and could not be inferred safely.',
        getattr(recipient, 'pk', '?'), category,
    )
    return None

# The clean-up used to run a DELETE on EVERY request (including the bell poll).
# It only needs to happen now and then, so it is throttled to once per interval
# (cluster-wide when REDIS_URL is set, per worker otherwise).
CLEANUP_INTERVAL_SECONDS = 600


def notify(recipient, title, message='', category=Notification.Category.OTHER,
           level=Notification.Level.INFO, link_url='', pharmacy=_UNSET, dedupe_key='',
           action_url='', action_label=''):
    """
    The one function every other app should call to put something on
    someone's notification bell. Usage:

        from notifications.utils import notify
        notify(employee, "New Announcement", "Please open the shop at 8:00 AM tomorrow.",
               category=Notification.Category.ANNOUNCEMENT, pharmacy=branch)
    """
    if dedupe_key:
        existing = Notification.objects.filter(recipient=recipient, dedupe_key=dedupe_key).first()
        if existing:
            return existing
    if pharmacy is _UNSET:
        pharmacy = None
    if category in BRANCH_BOUND_CATEGORIES and pharmacy is None:
        pharmacy = _infer_branch_for_notification(recipient, category)
        if pharmacy is None:
            # Fail closed: a branch-specific operational alert without known
            # ownership is less harmful to omit than to expose across branches.
            return None
    return Notification.objects.create(
        recipient=recipient, pharmacy=pharmacy, category=category,
        level=level, title=title, message=message, link_url=link_url,
        dedupe_key=dedupe_key, action_url=action_url, action_label=action_label,
    )


def notify_many(recipients, title, message='', category=Notification.Category.OTHER,
                 level=Notification.Level.INFO, link_url='', pharmacy=_UNSET, dedupe_key='',
                 action_url='', action_label=''):
    """Same as notify(), but for a whole list/queryset of recipients at once (e.g. an announcement)."""
    created = []
    for recipient in recipients:
        item = notify(
            recipient, title, message=message, category=category, level=level,
            link_url=link_url, pharmacy=pharmacy,
            dedupe_key=(f'{dedupe_key}:{recipient.pk}' if dedupe_key else ''),
            action_url=action_url, action_label=action_label,
        )
        if item is not None:
            created.append(item)
    return created


def cleanup_expired_notifications(force=False):
    """
    Notifications are durable history.  They must not disappear merely
    because the user has not opened the app for 48 hours.

    The old implementation deleted notifications older than 48 hours, which
    made branch switching look as if notification history had been lost.
    Keep this function as a compatibility no-op because older middleware and
    management commands still call it.  A future explicit data-retention
    command can delete records only when an administrator intentionally asks
    for that cleanup.
    """
    return 0


def management_recipients(pharmacy):
    """Everyone who can act on a branch's attendance/meal/cash requests.

    Branch owners plus active Manager Assistants whose delegated access covers
    this branch. Returns a queryset (so ``.filter``/``.first`` keep working).
    """
    from .automation import _managers  # lazy: automation imports heavy modules
    return _managers(pharmacy).filter(is_active=True)
