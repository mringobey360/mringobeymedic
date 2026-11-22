from django.core.management.base import BaseCommand

from pharmacies.billing_notices import process_branch_billing


class Command(BaseCommand):
    help = "Send per-branch subscription reminders (in-app + SMS) and record billing milestones. Safe to run repeatedly."

    def add_arguments(self, parser):
        parser.add_argument('--no-sms', action='store_true', help='Skip SMS (in-app notifications only).')

    def handle(self, *args, **opts):
        r = process_branch_billing(send_sms=not opts['no_sms'])
        self.stdout.write(self.style.SUCCESS(f"Branches checked: {r['branches']}, notices: {r['notices']}, SMS: {r['sms']}"))
