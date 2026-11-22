from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from daily_reports.tasks import generate_and_send_daily_report
from accounts.models import User

class Command(BaseCommand):
    help = 'Generate and send Premium Daily Sales Intelligence for all eligible managers for yesterday.'
    def handle(self, *args, **options):
        date = timezone.localdate() - timedelta(days=1)
        count = 0
        for owner in User.objects.filter(role=User.Role.ADMIN, is_active=True):
            if owner.managed_pharmacies.filter(is_active=True).exists():
                result = generate_and_send_daily_report(owner.id, date.isoformat())
                if result.get('status') == 'sent': count += 1
        self.stdout.write(self.style.SUCCESS(f'Daily intelligence delivery complete: {count} sent.'))
