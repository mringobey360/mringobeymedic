from django.core.management.base import BaseCommand
from layaway.models import LayawaySMSPackage

class Command(BaseCommand):
    help = 'Create or update the default SmartPOS Premium Layaway SMS packages.'

    def handle(self, *args, **options):
        packages = [
            {'code': 'ESSENTIAL-100', 'name': 'Essential', 'tagline': 'Reliable updates for growing shops', 'price': '2500.00', 'sms_credits': 100, 'is_featured': False, 'sort_order': 1},
            {'code': 'BUSINESS-PLUS-250', 'name': 'Business Plus', 'tagline': 'More customer updates, better value', 'price': '5000.00', 'sms_credits': 250, 'is_featured': True, 'sort_order': 2},
        ]
        for data in packages:
            code = data.pop('code')
            LayawaySMSPackage.objects.update_or_create(code=code, defaults=data)
            self.stdout.write(self.style.SUCCESS(f'Configured {code}'))
