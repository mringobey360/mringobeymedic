from decimal import Decimal

from django.core.management.base import BaseCommand

from sms_integration.models import SMSMarketingPackage


OFFICIAL_PACKAGES = [
    {
        'code': 'MKT_STARTER',
        'name': 'Starter',
        'price': Decimal('15000'),
        'sms_credits': 500,
        'duration_days': 30,
        'sort_order': 10,
    },
    {
        'code': 'MKT_GROWTH',
        'name': 'Growth',
        'price': Decimal('40000'),
        'sms_credits': 1500,
        'duration_days': 30,
        'sort_order': 20,
    },
    {
        'code': 'MKT_BUSINESS',
        'name': 'Business',
        'price': Decimal('125000'),
        'sms_credits': 5000,
        'duration_days': 30,
        'sort_order': 30,
    },
    {
        'code': 'MKT_PROFESSIONAL',
        'name': 'Professional',
        'price': Decimal('240000'),
        'sms_credits': 10000,
        'duration_days': 30,
        'sort_order': 40,
    },
    {
        'code': 'MKT_ENTERPRISE',
        'name': 'Enterprise',
        'price': Decimal('550000'),
        'sms_credits': 25000,
        'duration_days': 30,
        'sort_order': 50,
    },
]


class Command(BaseCommand):
    help = 'Create/update the official Customer Marketing SMS packages.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--deactivate-other-packages',
            action='store_true',
            help='Deactivate active Marketing SMS packages that are not in the official package list.',
        )

    def handle(self, *args, **options):
        official_codes = {item['code'] for item in OFFICIAL_PACKAGES}

        for item in OFFICIAL_PACKAGES:
            package, created = SMSMarketingPackage.objects.update_or_create(
                code=item['code'],
                defaults={
                    **item,
                    'is_active': True,
                },
            )
            action = 'Created' if created else 'Updated'
            self.stdout.write(
                self.style.SUCCESS(
                    f"{action}: {package.name} — TZS {package.price:,.0f} / {package.sms_credits:,} SMS / {package.duration_days} days"
                )
            )

        if options['deactivate_other_packages']:
            changed = SMSMarketingPackage.objects.exclude(code__in=official_codes).filter(is_active=True).update(is_active=False)
            self.stdout.write(self.style.WARNING(f'Deactivated {changed} non-official Marketing SMS package(s).'))

        self.stdout.write(self.style.SUCCESS('Official Customer Marketing SMS packages are ready.'))
