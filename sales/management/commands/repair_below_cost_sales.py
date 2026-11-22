"""REPORT-ONLY: list sale lines that were sold below the cost of their batch because of a discount.

Before the cost-floor fix (v70) a product discount could push a unit below the cost of the batch it was
issued from (e.g. 15,000 - 4,000 = 11,000 on a batch that cost 12,000) and show a negative profit.
New sales can no longer do this.

This command deliberately does NOT change any record. Rewriting the price of a paid cash sale makes
'Cash sales' and the expected drawer cash differ from the money really taken, so the loss stays on the
books as it really happened. The dashboard shows it as a 'Loss' instead of a negative number.

  python manage.py repair_below_cost_sales                 # all history
  python manage.py repair_below_cost_sales --days 30
  python manage.py repair_below_cost_sales --pharmacy 3
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from sales.models import SaleItem


class Command(BaseCommand):
    help = "Report (never modify) sale lines sold below batch cost."

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=0, help='Only sales from the last N days (0 = all).')
        parser.add_argument('--pharmacy', type=int, default=0, help='Only this pharmacy/branch id.')

    def handle(self, *args, **opts):
        qs = (SaleItem.objects.select_related('sale', 'product')
              .filter(unit_price__lt=F('unit_cost'))
              .exclude(sale__status='VOIDED'))
        if opts['days'] > 0:
            qs = qs.filter(sale__business_date__gte=timezone.localdate() - timedelta(days=opts['days']))
        if opts['pharmacy']:
            qs = qs.filter(sale__pharmacy_id=opts['pharmacy'])
        lines = list(qs.order_by('sale__business_date', 'sale_id'))
        if not lines:
            self.stdout.write(self.style.SUCCESS('No below-cost sale lines found.'))
            return
        total = 0
        for item in lines:
            total += -item.profit
            self.stdout.write(f"Sale #{item.sale.sale_number or item.sale_id} {item.sale.business_date} | {item.product.name} | "
                              f"price {item.unit_price:,.0f} < cost {item.unit_cost:,.0f} | loss {-item.profit:,.0f} | qty {item.quantity}")
        self.stdout.write(f"\n{len(lines)} line(s), total loss {total:,.0f}. No records were changed.")
