"""Send a published system notice to each customer's notification bell (web push follows
automatically through notifications.tasks.dispatch_pending_web_push_notifications).

Idempotent: ``notified_at`` is claimed with a conditional UPDATE first, so two workers or a
retry can never send the same notice twice.
"""
import logging

from django.db import transaction
from django.utils import timezone

from .models import SystemNotice

logger = logging.getLogger(__name__)

_LEVEL = {'INCIDENT': 'DANGER', 'MAINTENANCE': 'WARNING', 'RESOLVED': 'SUCCESS'}
BATCH = 500


def _recipients(notice):
    from accounts.models import User
    roles = [User.Role.ADMIN, User.Role.ASSISTANT]
    if notice.audience == SystemNotice.Audience.ALL:
        roles.append(User.Role.EMPLOYEE)
    return User.objects.filter(is_active=True, is_superuser=False, role__in=roles)


def send_notice_notifications(notice, now=None):
    """Create the bell notifications. Returns how many were created (0 if not due / already sent)."""
    from notifications.models import Notification
    now = now or timezone.now()
    notice.refresh_from_db()
    if not (notice.notify_in_app and notice.is_published and notice.is_active):
        return 0
    if notice.starts_at and notice.starts_at > now:
        return 0
    if notice.ends_at and notice.ends_at <= now:
        return 0
    # Claim it: only one caller wins.
    with transaction.atomic():
        claimed = SystemNotice.objects.filter(pk=notice.pk, notified_at__isnull=True).update(notified_at=now)
    if not claimed:
        return 0
    level = getattr(Notification.Level, _LEVEL.get(notice.kind, 'INFO'))
    key = f'system-notice:{notice.pk}:{int(now.timestamp())}'
    title = f'{notice.badge}: {notice.title}'[:150]
    total, batch = 0, []
    try:
        for user in _recipients(notice).only('pk').iterator(chunk_size=BATCH):
            batch.append(Notification(
                recipient_id=user.pk, pharmacy=None, category=Notification.Category.SYSTEM,
                level=level, title=title, message=notice.message,
                link_url=notice.link_url or '', dedupe_key=f'{key}:{user.pk}',
            ))
            if len(batch) >= BATCH:
                Notification.objects.bulk_create(batch)
                total += len(batch)
                batch = []
        if batch:
            Notification.objects.bulk_create(batch)
            total += len(batch)
    except Exception:
        logger.exception('System notice %s: bell delivery failed after %s sends', notice.pk, total)
    return total


def send_due_notice_notifications(now=None):
    now = now or timezone.now()
    due = SystemNotice.objects.filter(
        publish_state=SystemNotice.Workflow.PUBLISHED, is_active=True,
        notify_in_app=True, notified_at__isnull=True, starts_at__lte=now,
    )
    return sum(send_notice_notifications(n, now) for n in due)
