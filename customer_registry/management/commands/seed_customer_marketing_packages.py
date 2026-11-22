from django.core.management.base import BaseCommand
from sms_integration.models import SMSMarketingPackage


class Command(BaseCommand):
    help = 'Create/update the default Customer Marketing SMS packages.'

    packages = [
        ('MARKETING_STARTER', 'Starter Marketing', 15000, 500, 30, 1),
        ('MARKETING_GROWTH', 'Growth Marketing', 40000, 1500, 30, 2),
        ('MARKETING_BUSINESS', 'Business Marketing', 100000, 5000, 60, 3),
    ]

    def handle(self, *args, **options):
        for code, name, price, credits, days, order in self.packages:
            obj, created = SMSMarketingPackage.objects.update_or_create(
                code=code,
                defaults={
                    'name': name,
                    'price': price,
                    'sms_credits': credits,
                    'duration_days': days,
                    'is_active': True,
                    'sort_order': order,
                },
            )
            self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} {obj.name}: TZS {obj.price:,.0f} / {obj.sms_credits:,} SMS"))
