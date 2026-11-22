"""Make every owner's remaining SMS-package credits permanent (no expiry) - V54.10.

Debt, Receipt, Marketing and Intelligent Report SMS used to expire after the package days, which
lost unused credits. They now last until used. Run this ONCE after deploying so existing owners
get the same rule, including credits that had already expired:

    python manage.py restore_sms_credits                     # preview only, changes nothing
    python manage.py restore_sms_credits --apply             # apply
    python manage.py restore_sms_credits --service marketing # one service only (debt|receipt|marketing|report)

Per owner and service it merges the UNUSED credits of every non-cancelled entitlement into the
newest one, marks it ACTIVE and parks end_date on the no-expiry marker. Older rows keep their history;
their unused credits move to the merged row.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from daily_reports.models import DailyReportSubscription
from sms_integration.models import (
    NO_EXPIRY_DATE, SMSDebtSubscription, SMSMarketingSubscription, SMSReceiptSubscription,
)

SERVICES = {
    'debt': (SMSDebtSubscription, 'sms_credits', 'sms_used'),
    'receipt': (SMSReceiptSubscription, 'sms_credits', 'sms_used'),
    'marketing': (SMSMarketingSubscription, 'sms_credits', 'sms_used'),
    'report': (DailyReportSubscription, 'report_sms_credits', 'report_sms_used'),
}


class Command(BaseCommand):
    help = 'Make unused SMS-package credits permanent (default: dry run).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write the changes (otherwise preview only).')
        parser.add_argument('--service', choices=sorted(SERVICES), help='Only this service.')

    def handle(self, *args, **options):
        apply = options['apply']
        names = [options['service']] if options['service'] else list(SERVICES)
        grand_owners = grand_credits = 0
        for name in names:
            model, credits_f, used_f = SERVICES[name]
            owners = (model.objects.exclude(status=model.Status.CANCELLED)
                      .values_list('owner_id', flat=True).distinct())
            n_owners = n_credits = 0
            for owner_id in owners:
                with transaction.atomic():
                    rows = list(model.objects.select_for_update().filter(owner_id=owner_id)
                                .exclude(status=model.Status.CANCELLED).order_by('id'))
                    total = sum(r.credits_remaining for r in rows)
                    if total <= 0:
                        continue
                    target = rows[-1]
                    self.stdout.write(f'[{name}] owner #{owner_id}: {total:,} SMS kept '
                                      f'({len(rows)} entitlement(s)) -> row #{target.pk}'
                                      f'{" (reactivated)" if target.status != model.Status.ACTIVE else ""}')
                    n_owners += 1
                    n_credits += total
                    if not apply:
                        continue
                    for r in rows[:-1]:
                        if r.credits_remaining:
                            setattr(r, credits_f, getattr(r, used_f))   # unused credits move to the merged row
                            r.status = model.Status.EXPIRED
                            r.save(update_fields=[credits_f, 'status', 'updated_at'])
                    setattr(target, credits_f, getattr(target, used_f) + total)
                    target.status = model.Status.ACTIVE
                    target.end_date = NO_EXPIRY_DATE
                    target.save(update_fields=[credits_f, 'status', 'end_date', 'updated_at'])
            self.stdout.write(f'{name}: {n_owners} owner(s), {n_credits:,} SMS')
            grand_owners += n_owners
            grand_credits += n_credits
        verb = 'Updated' if apply else 'Would update'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {grand_owners} owner entitlement(s); {grand_credits:,} SMS credits now never expire.'
            + ('' if apply else ' (dry run - add --apply to save)')))
