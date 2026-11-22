from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from layaway.models import LayawayPlan, LayawaySMSLog
from layaway.sms_service import build_reminder_sms, send_plan_event


class Command(BaseCommand):
    help = 'Send due-date Layaway SMS reminders once per planned event (schedule daily via cron/Railway job).'

    def handle(self, *args, **options):
        today = timezone.localdate()
        due_3 = today + timedelta(days=3)
        base = LayawayPlan.objects.select_related('pharmacy', 'product', 'created_by').filter(
            status__in=[LayawayPlan.Status.ACTIVE, LayawayPlan.Status.PARTIALLY_HANDED_OVER],
            due_date__isnull=False,
        ).order_by('due_date')
        sent = skipped = failed = 0
        # Retry recent failed/unfinished messages at most three times; never replay old customer events.
        retry_cutoff = timezone.now() - timedelta(hours=24)
        retryable = LayawaySMSLog.objects.filter(
            status__in=[LayawaySMSLog.Status.FAILED, LayawaySMSLog.Status.NO_CREDITS, LayawaySMSLog.Status.PENDING],
            attempts__lt=3, updated_at__gte=retry_cutoff,
        ).select_related('plan__pharmacy', 'plan__product', 'plan__created_by')
        for log in retryable.iterator():
            plan = log.plan
            owner = plan.created_by.business_owner() if plan.created_by and callable(getattr(plan.created_by, 'business_owner', None)) else plan.created_by
            try:
                ok, state = send_plan_event(plan, log.event_type, log.message, owner=owner, event_key=log.event_key, retry_failed=True)
                if ok:
                    sent += 1
                elif state in ('NO_CREDITS', 'SEND_FAILED'):
                    failed += 1
                else:
                    skipped += 1
            except Exception as exc:
                failed += 1
                self.stderr.write(f'{plan.reference} SMS retry: {exc}')
        for plan in base.iterator():
            if plan.balance <= 0:
                continue
            kind = None
            if plan.due_date == due_3:
                kind = LayawaySMSLog.EventType.REMINDER_3_DAYS
            elif plan.due_date == today:
                kind = LayawaySMSLog.EventType.REMINDER_DUE
            elif plan.due_date < today:
                overdue_days = (today - plan.due_date).days
                # First overdue reminder after 3 days, then weekly; avoids daily SMS spam.
                if overdue_days >= 3 and (overdue_days - 3) % 7 == 0:
                    kind = LayawaySMSLog.EventType.REMINDER_OVERDUE
            if not kind:
                continue
            date_key = today.isoformat()
            event_key = f'plan:{plan.pk}:{kind}:{date_key}'
            owner = plan.created_by.business_owner() if plan.created_by and callable(getattr(plan.created_by, 'business_owner', None)) else plan.created_by
            try:
                ok, state = send_plan_event(plan, kind, build_reminder_sms(plan, kind), owner=owner, event_key=event_key)
                if ok:
                    sent += 1
                elif state in ('NO_CREDITS', 'SEND_FAILED'):
                    failed += 1
                else:
                    skipped += 1
            except Exception as exc:  # a bad customer record must not stop the daily batch
                failed += 1
                self.stderr.write(f'{plan.reference}: {exc}')
        self.stdout.write(self.style.SUCCESS(f'Layaway reminders finished: sent={sent}, skipped={skipped}, failed={failed}.'))
