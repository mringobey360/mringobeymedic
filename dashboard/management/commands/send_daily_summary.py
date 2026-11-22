from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from inventory.models import Product
from pharmacies.models import Pharmacy
from sales.models import SaleItem
from sms_integration.utils import send_daily_summary_sms


class Command(BaseCommand):
    help = (
        "Sends a single end-of-day SMS summary (sales, profit, low-stock count) for EACH pharmacy "
        "branch to every owner who manages it. Intended to run once daily via cron, "
        "e.g. at 21:00: 0 21 * * * python manage.py send_daily_summary"
    )

    def handle(self, *args, **options):
        today = timezone.localdate()
        total_sent = 0

        for pharmacy in Pharmacy.objects.filter(is_active=True):
            items = SaleItem.objects.filter(sale__pharmacy=pharmacy, sale__business_date=today)
            sales_total = sum(i.subtotal for i in items)
            profit_total = sum(i.profit for i in items)
            low_stock_count = len([p for p in Product.objects.filter(pharmacy=pharmacy, is_active=True) if p.is_low_stock])

            owners = User.objects.filter(role=User.Role.ADMIN, is_active=True, managed_pharmacies=pharmacy)
            for owner in owners:
                if send_daily_summary_sms(owner.phone_number, sales_total, profit_total, low_stock_count, owner=owner):
                    total_sent += 1

            self.stdout.write(
                f"[{pharmacy.name}] Sales: {sales_total}, Profit: {profit_total}, "
                f"Low stock: {low_stock_count}, notified {owners.count()} owner(s)."
            )

        self.stdout.write(self.style.SUCCESS(f"Daily summary complete. {total_sent} SMS sent in total."))
