"""Check (and optionally repair) sales/cash integrity.

    python manage.py reconcile_sales                    # read-only, today
    python manage.py reconcile_sales --days 7           # last 7 days
    python manage.py reconcile_sales --fix              # apply the safe automatic repairs
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from dashboard.reconciliation import run_integrity_checks
from pharmacies.models import Pharmacy


class Command(BaseCommand):
    help = 'Verify gross->net sales bridge, cash totals and sale statuses for every branch.'

    def add_arguments(self, parser):
        parser.add_argument('--pharmacy', type=int)
        parser.add_argument('--days', type=int, default=1)
        parser.add_argument('--fix', action='store_true', help='Repair sale statuses derived from their own items')

    def handle(self, *args, **o):
        end = timezone.localdate()
        start = end - timedelta(days=max(o['days'], 1) - 1)
        qs = Pharmacy.objects.filter(is_active=True)
        if o['pharmacy']:
            qs = qs.filter(pk=o['pharmacy'])
        for ph in qs:
            issues = run_integrity_checks(ph, start, end, fix=o['fix'])
            self.stdout.write(self.style.MIGRATE_HEADING(f'{ph} {start}..{end}: {len(issues)} finding(s)'))
            for i in issues:
                line = f"  [{i.severity.upper()}] {i.code}: {i.message}" + ('  -> FIXED' if i.fixed else '')
                self.stdout.write(self.style.ERROR(line) if i.severity == 'danger' else line)
