from django.core.management.base import BaseCommand

from accounts.models import User
from inventory.models import Product
from sms_integration.utils import send_low_stock_sms, send_expiry_sms


class Command(BaseCommand):
    help = (
        "Checks all products (in every branch) for low stock or upcoming expiry and SMS's "
        "only the owner(s) who manage that specific branch. "
        "Intended to run once daily via cron, e.g.: 0 8 * * * python manage.py check_alerts"
    )

    def handle(self, *args, **options):
        low_stock_sent, expiry_sent = 0, 0

        for product in Product.objects.filter(is_active=True).select_related('pharmacy'):
            owners = User.objects.filter(role=User.Role.ADMIN, is_active=True, managed_pharmacies=product.pharmacy)
            if not owners.exists():
                continue

            if product.is_low_stock:
                for owner in owners:
                    if send_low_stock_sms(owner.phone_number, product.name, product.total_quantity, owner=owner):
                        low_stock_sent += 1

            if product.is_expiring_soon and not product.is_expired:
                for owner in owners:
                    if send_expiry_sms(owner.phone_number, product.name, product.days_to_expiry, owner=owner):
                        expiry_sent += 1

        self.stdout.write(self.style.SUCCESS(
            f"Alert check complete. Low-stock SMS sent: {low_stock_sent}, Expiry SMS sent: {expiry_sent}."
        ))
