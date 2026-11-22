from django.core.management.base import BaseCommand
from sms_integration.models import SMSDebtPackage
from decimal import Decimal

class Command(BaseCommand):
    help = "Create/update the default Premium Debt SMS packages."
    def handle(self, *args, **kwargs):
        defaults = [
            ('DEBT500', 'Premium Debt SMS — 500', Decimal('5000'), 500, 30, 1),
            ('DEBT10000', 'Premium Debt SMS — 10,000', Decimal('10000'), 10000, 30, 2),
        ]
        for code, name, price, credits, days, order in defaults:
            SMSDebtPackage.objects.update_or_create(code=code, defaults={'name':name,'price':price,'sms_credits':credits,'duration_days':days,'is_active':True,'sort_order':order})
        self.stdout.write(self.style.SUCCESS('Premium Debt SMS packages are ready: TZS 5,000/500 SMS and TZS 10,000/10,000 SMS per 30 days.'))
