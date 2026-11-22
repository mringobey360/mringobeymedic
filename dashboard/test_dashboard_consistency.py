"""Run: python manage.py test dashboard.test_dashboard_consistency

V117.1 — one business date must give the SAME sales / profit / transaction count on the Today cards
(`_profit_summary`), the Performance Monitor (`performance.period_totals` / `daily_rows`) and the
Report Builder, whatever the payment method, and voided sales must never be counted."""
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from dashboard import performance
from dashboard.views import _cash_card, _profit_summary
from inventory.models import Product
from pharmacies.models import Pharmacy
from sales.models import Debt, DebtPayment, Sale, SaleItem, SaleReturnRequest


class DashboardConsistencyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='mgr', password='x')
        self.pharmacy = Pharmacy.objects.create(name='Test Branch')
        self.day = timezone.localdate()
        self.product = Product.objects.create(pharmacy=self.pharmacy, name='Lotion')

    def _sale(self, method, price, qty=1, cost='6000', status=Sale.Status.COMPLETED, **extra):
        sale = Sale.objects.create(pharmacy=self.pharmacy, served_by=self.user, payment_method=method,
                                   business_date=self.day, status=status, **extra)
        SaleItem.objects.create(sale=sale, product=self.product, quantity=qty,
                                unit_price=D(price), unit_cost=D(cost))
        return sale

    def test_all_screens_agree_and_voided_is_excluded(self):
        self._sale(Sale.PaymentMethod.CASH, '10000')
        self._sale(Sale.PaymentMethod.MOBILE_MONEY, '8000')
        credit = self._sale(Sale.PaymentMethod.DEBT, '30000', customer_name='Asha')
        Debt.objects.create(pharmacy=self.pharmacy, sale=credit, customer_name='Asha', amount_owed=D('30000'))
        self._sale(Sale.PaymentMethod.CASH, '99999', status=Sale.Status.VOIDED)

        today = _profit_summary(self.pharmacy, self.day, self.day)
        monitor = performance.period_totals(self.pharmacy, self.day, self.day)
        daily = performance.daily_rows(self.pharmacy, self.day, self.day)[self.day]

        self.assertEqual(today['sales'], D('48000'))          # 10,000 + 8,000 + 30,000 (credit counted in full)
        self.assertEqual(today['transactions'], 3)            # voided sale not counted
        self.assertEqual(today['cash_sales'], D('10000'))
        self.assertEqual(today['mobile_sales'], D('8000'))
        self.assertEqual(today['credit_sales'], D('30000'))   # nothing paid yet => all of it is lent
        self.assertEqual(today['cash_received'], D('18000'))  # credit is not money until paid

        self.assertEqual(monitor['sales'], today['sales'])
        self.assertEqual(monitor['profit'], today['gross_profit'])
        self.assertEqual(monitor['transactions'], today['transactions'])
        self.assertEqual(daily['sales'], today['sales'])
        self.assertEqual(daily['tx'], today['transactions'])

    def test_refund_of_voided_sale_is_not_deducted_twice(self):
        """Money in (before refunds) - refunds = net. A voided same-day sale is already out of net sales, so its
        refund must not be taken off a second time (was: net -33,000 instead of +51,000)."""
        self._sale(Sale.PaymentMethod.CASH, '15000')
        self._sale(Sale.PaymentMethod.MOBILE_MONEY, '29000')
        voided = self._sale(Sale.PaymentMethod.CASH, '7000', qty=0, status=Sale.Status.VOIDED)
        SaleItem.objects.filter(sale=voided).update(returned_quantity=1)
        SaleReturnRequest.objects.create(
            pharmacy=self.pharmacy, sale=voided, status=SaleReturnRequest.Status.APPROVED, reason='test',
            decided_at=timezone.now(), refund_amount=D('7000'), refund_due=D('7000'))

        today = _profit_summary(self.pharmacy, self.day, self.day)
        self.assertEqual(today['returned_cash'], D('7000'))
        self.assertEqual(today['cash_received'], D('44000'))   # 15,000 cash + 29,000 mobile

    def test_cash_card_shows_net_sales_and_only_earlier_day_refunds(self):
        b = {'opening': D('0'), 'cash_sales': D('51000'), 'cash_refunds': D('29000'), 'expected': D('22000')}
        card = _cash_card(b, D('29000'))                     # all refunds belong to today's own sales
        self.assertEqual(card['net_sales'], D('22000'))
        self.assertEqual(card['earlier_refunds'], D('0'))
        self.assertEqual(b['opening'] + card['net_sales'] - card['earlier_refunds'], b['expected'])

        b2 = {'opening': D('1607000'), 'cash_sales': D('11000'), 'cash_refunds': D('22000'), 'expected': D('1596000')}
        card2 = _cash_card(b2, D('11000'))                   # one refund is for a sale of an earlier day
        self.assertEqual((card2['net_sales'], card2['earlier_refunds']), (D('0'), D('11000')))
        self.assertEqual(b2['opening'] + card2['net_sales'] - card2['earlier_refunds'], b2['expected'])

