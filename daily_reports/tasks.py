from datetime import timedelta
import logging

from celery import shared_task
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from sms_integration.models import SMSLog
from sms_integration.utils import send_sms
from .models import DailyReportSettings, DailySalesReport, DailyReportDeliveryLog, DailyReportSubscription
from .services import premium_daily_reports_enabled, build_daily_snapshot, build_sms_message

logger = logging.getLogger(__name__)


def _absolute_report_url(token):
    from django.conf import settings
    path = reverse('daily_reports:public_report', kwargs={'token': token})
    domain = getattr(settings, 'RAILWAY_PUBLIC_DOMAIN', '').strip()
    if domain:
        return f'https://{domain}{path}'
    # Works locally and behind the request-less worker only when PUBLIC_BASE_URL is configured.
    base = getattr(settings, 'DAILY_REPORT_PUBLIC_BASE_URL', '').strip().rstrip('/')
    return f'{base}{path}' if base else path


@shared_task(bind=True, max_retries=2, default_retry_delay=60, acks_late=True)
def generate_and_send_daily_report(self, owner_id, report_date_iso=None):
    from datetime import date
    try:
        owner = User.objects.get(pk=owner_id, role=User.Role.ADMIN, is_active=True)
    except User.DoesNotExist:
        return {'status': 'missing_owner'}
    if not premium_daily_reports_enabled(owner):
        return {'status': 'not_premium'}
    entitlement = DailyReportSubscription.objects.filter(
        owner=owner, status=DailyReportSubscription.Status.ACTIVE,
        report_sms_credits__gt=F('report_sms_used'),
    ).order_by('-created_at', '-id').first()
    if entitlement is None:
        return {'status': 'no_report_sms_package'}
    report_date = date.fromisoformat(report_date_iso) if report_date_iso else timezone.localdate() - timedelta(days=1)
    settings_obj, _ = DailyReportSettings.objects.get_or_create(owner=owner)
    recipient = settings_obj.effective_phone
    snapshot = build_daily_snapshot(owner, report_date)
    with transaction.atomic():
        report, created = DailySalesReport.objects.select_for_update().get_or_create(
            owner=owner, report_date=report_date,
            defaults={'expires_at': timezone.now() + timedelta(days=settings_obj.report_retention_days), 'snapshot': snapshot},
        )
        if not created:
            report.snapshot = snapshot
            report.expires_at = timezone.now() + timedelta(days=settings_obj.report_retention_days)
            report.save(update_fields=['snapshot', 'expires_at'])
        delivery, created_delivery = DailyReportDeliveryLog.objects.get_or_create(
            owner=owner, report_date=report_date,
            defaults={'report': report, 'scheduled_time': settings_obj.delivery_time, 'recipient': recipient, 'status': DailyReportDeliveryLog.Status.SKIPPED},
        )
        if not created_delivery:
            if delivery.status in {DailyReportDeliveryLog.Status.SENT, DailyReportDeliveryLog.Status.PROCESSING}:
                return {'status': 'already_processing_or_sent', 'report_id': report.id}
            if delivery.attempted_at and delivery.attempted_at > timezone.now() - timedelta(minutes=15):
                return {'status': 'recent_attempt', 'report_id': report.id}
        delivery.status = DailyReportDeliveryLog.Status.PROCESSING
        delivery.report = report
        delivery.scheduled_time = settings_obj.delivery_time
        delivery.recipient = recipient
        delivery.save(update_fields=['status', 'report', 'scheduled_time', 'recipient'])
    report_url = _absolute_report_url(report.access_token)
    message = build_sms_message(snapshot, report_url)
    sent = send_sms(recipient, message, SMSLog.Category.DAILY_SUMMARY, owner=owner, consume_wallet=False)
    if sent:
        entitlement.report_sms_used += 1
        entitlement.save(update_fields=['report_sms_used', 'updated_at'])
    report.sms_message = message
    report.sms_recipient = recipient
    report.sms_sent_at = timezone.now() if sent else None
    report.sms_error = '' if sent else 'SMS provider rejected or SMS settings/wallet did not allow delivery.'
    report.save(update_fields=['sms_message', 'sms_recipient', 'sms_sent_at', 'sms_error'])
    delivery.report = report
    delivery.recipient = recipient
    delivery.message = message
    delivery.status = DailyReportDeliveryLog.Status.SENT if sent else DailyReportDeliveryLog.Status.FAILED
    delivery.error_message = '' if sent else report.sms_error
    delivery.save(update_fields=['report', 'recipient', 'message', 'status', 'error_message'])
    if sent:
        settings_obj.last_sent_for_date = report_date
        settings_obj.save(update_fields=['last_sent_for_date', 'updated_at'])
    return {'status': 'sent' if sent else 'failed', 'report_id': report.id}


@shared_task
def dispatch_due_daily_reports():
    now = timezone.localtime()
    today = now.date()
    # At 04:00, the report covers the previous calendar day. For custom times,
    # the same convention applies: report date = previous Tanzania business day.
    report_date = today - timedelta(days=1)
    owners = User.objects.filter(role=User.Role.ADMIN, is_active=True, managed_pharmacies__is_active=True).distinct()
    queued = 0
    for owner in owners:
        if not premium_daily_reports_enabled(owner):
            continue
        pref, _ = DailyReportSettings.objects.get_or_create(owner=owner)
        if not pref.enabled or pref.delivery_time > now.time().replace(second=0, microsecond=0):
            continue
        delivery = DailyReportDeliveryLog.objects.filter(owner=owner, report_date=report_date).first()
        if delivery and delivery.status == DailyReportDeliveryLog.Status.SENT:
            continue
        if delivery and delivery.attempted_at and delivery.attempted_at > timezone.now() - timedelta(minutes=15):
            continue
        generate_and_send_daily_report.delay(owner.id, report_date.isoformat())
        queued += 1
    return {'queued': queued, 'local_time': now.isoformat()}


@shared_task
def cleanup_expired_reports():
    now = timezone.now()
    deleted, _ = DailySalesReport.objects.filter(expires_at__lt=now).delete()
    return {'deleted': deleted}
