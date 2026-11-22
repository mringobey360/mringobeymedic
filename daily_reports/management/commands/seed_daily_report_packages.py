from django.core.management.base import BaseCommand
from daily_reports.models import DailyReportPackage

class Command(BaseCommand):
    help = 'Create/update the official Intelligent Report SMS packages.'
    PACKAGES = [
        ('INTEL-30', 'Intelligent Reports — 30 SMS', 5000, 30, 30, 1),
        ('INTEL-100', 'Intelligent Reports — 100 SMS', 12000, 100, 30, 2),
        ('INTEL-300', 'Intelligent Reports — 300 SMS', 30000, 300, 30, 3),
    ]
    def handle(self, *args, **kwargs):
        for code,name,price,credits,days,order in self.PACKAGES:
            obj,_=DailyReportPackage.objects.update_or_create(code=code, defaults={'name':name,'price':price,'report_sms_credits':credits,'duration_days':days,'sort_order':order,'is_active':True})
            self.stdout.write(self.style.SUCCESS(f'{obj.name}: TZS {obj.price:,.0f} / {obj.report_sms_credits} SMS'))
