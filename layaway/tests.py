"""Run: python manage.py test layaway

Covers the handover flow (Sale, stock, payment split, refusals), the stock-reservation counts used by POS,
and the cash-drawer rules (layaway cash counts the day it is received, never twice, refunds reduce it).
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.db.models import Sum

from expenses.ledger import CashLedgerService
from expenses.models import CashDay
from django.utils import timezone
from inventory.models import Product, ProductBatch, StockMovement
from pharmacies.models import Pharmacy
from sales.models import Sale

from . import services
from .models import LayawayAudit, LayawayPayment, LayawayPlan, LayawayRefund

D = Decimal


class LayawayHandoverTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='cashier', password='x')
        self.pharmacy = Pharmacy.objects.create(name='Manal Test')
        self.product = Product.objects.create(pharmacy=self.pharmacy, name='After Shave')
        self.batch = ProductBatch.objects.create(
            product=self.product, initial_quantity=10, quantity=10,
            purchase_price=D('3000'), selling_price=D('5000'))

    def _plan(self, qty=6, price=D('5000'), payments=((D('30000'), 'MOBILE'),), status=None):
        plan = LayawayPlan.objects.create(
            pharmacy=self.pharmacy, product=self.product, quantity=qty, customer_name='Jummo',
            customer_phone='+255610117824', unit_price=price, created_by=self.user)
        for amount, method in payments:
            LayawayPayment.objects.create(plan=plan, amount=amount, method=method, received_by=self.user)
        paid = sum((a for a, _ in payments), D('0'))
        plan.status = status or (LayawayPlan.Status.PAID_PENDING_HANDOVER if paid >= plan.total_amount
                                 else LayawayPlan.Status.ACTIVE)
        plan.save()
        return plan

    def test_handover_creates_sale_reduces_stock_and_completes(self):
        plan = self._plan()
        sale = services.hand_over(plan.pk, self.user, self.pharmacy)
        plan.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(plan.status, LayawayPlan.Status.COMPLETED)
        self.assertEqual(plan.sale_id, sale.pk)
        self.assertEqual(self.batch.quantity, 4)
        self.assertEqual(sale.total_amount, D('30000'))
        self.assertEqual(sale.payment_method, Sale.PaymentMethod.MOBILE_MONEY)
        self.assertTrue(StockMovement.objects.filter(product=self.product, quantity_change=-6).exists())
        self.assertTrue(LayawayAudit.objects.filter(plan=plan, action='HANDED_OVER').exists())

    def test_cannot_hand_over_twice(self):
        plan = self._plan()
        services.hand_over(plan.pk, self.user, self.pharmacy)
        with self.assertRaises(services.HandoverError):
            services.hand_over(plan.pk, self.user, self.pharmacy)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity, 4)

    def test_unpaid_plan_is_refused(self):
        plan = self._plan(payments=((D('10000'), 'MOBILE'),))
        with self.assertRaises(services.HandoverError):
            services.hand_over(plan.pk, self.user, self.pharmacy)
        self.assertEqual(Sale.objects.count(), 0)

    def test_not_enough_stock_changes_nothing(self):
        self.batch.quantity = 2
        self.batch.save()
        plan = self._plan()
        with self.assertRaises(services.HandoverError) as ctx:
            services.hand_over(plan.pk, self.user, self.pharmacy)
        self.assertIn('2 available', str(ctx.exception))
        plan.refresh_from_db()
        self.assertEqual(plan.status, LayawayPlan.Status.PAID_PENDING_HANDOVER)
        self.assertEqual(Sale.objects.count(), 0)

    def test_handover_does_not_consume_stock_reserved_for_another_customer(self):
        self.batch.quantity = 2
        self.batch.save()
        plan = self._plan(qty=1, payments=((D('5000'), 'CASH'),))
        other = self._plan(qty=2, payments=((D('10000'), 'CASH'),))
        with self.assertRaises(services.HandoverError) as ctx:
            services.hand_over(plan.pk, self.user, self.pharmacy, quantity=1)
        self.assertIn('reserved for other active customer plan', str(ctx.exception))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity, 2)
        self.assertEqual(Sale.objects.count(), 0)
        plan.refresh_from_db()
        self.assertNotEqual(plan.status, LayawayPlan.Status.COMPLETED)

    def test_agreed_price_below_list_price_is_recorded_as_discount(self):
        plan = self._plan(price=D('4500'), payments=((D('27000'), 'MOBILE'),))
        sale = services.hand_over(plan.pk, self.user, self.pharmacy)
        item = sale.items.get()
        self.assertEqual(item.unit_price, D('4500'))
        self.assertEqual(item.original_unit_price, D('5000'))
        self.assertEqual(item.discount_amount, D('3000'))

    def test_cash_and_mobile_become_a_split_sale(self):
        plan = self._plan(payments=((D('10000'), 'CASH'), (D('20000'), 'MOBILE')))
        sale = services.hand_over(plan.pk, self.user, self.pharmacy)
        self.assertEqual(sale.payment_method, Sale.PaymentMethod.SPLIT)
        self.assertEqual((sale.cash_paid, sale.mobile_paid), (D('10000'), D('20000')))

    def test_handover_works_from_needs_resolution_once_paid(self):
        plan = self._plan(status=LayawayPlan.Status.NEEDS_RESOLUTION)
        services.hand_over(plan.pk, self.user, self.pharmacy)
        plan.refresh_from_db()
        self.assertEqual(plan.status, LayawayPlan.Status.COMPLETED)

    def test_other_branch_cannot_hand_over(self):
        plan = self._plan()
        other = Pharmacy.objects.create(name='Other Branch')
        with self.assertRaises(services.HandoverError):
            services.hand_over(plan.pk, self.user, other)


class ReservationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='u', password='x')
        self.pharmacy = Pharmacy.objects.create(name='Branch')
        self.product = Product.objects.create(pharmacy=self.pharmacy, name='Lotion')

    def _plan(self, qty, status):
        return LayawayPlan.objects.create(
            pharmacy=self.pharmacy, product=self.product, quantity=qty, customer_name='A B',
            customer_phone='+255700000000', unit_price=D('1000'), status=status, created_by=self.user)

    def test_open_plans_are_counted_but_closed_ones_are_not(self):
        S = LayawayPlan.Status
        self._plan(2, S.ACTIVE)
        self._plan(3, S.PAID_PENDING_HANDOVER)
        self._plan(4, S.COMPLETED)
        self._plan(5, S.CANCELLED)
        got = services.reserved_quantities(self.pharmacy, [self.product.pk])[self.product.pk]
        self.assertEqual(got, {'qty': 5, 'plans': 2, 'ready': 3})

    def test_other_branch_reservations_are_not_visible(self):
        self._plan(2, LayawayPlan.Status.ACTIVE)
        other = Pharmacy.objects.create(name='Other')
        self.assertEqual(services.reserved_quantities(other), {})

    def test_manager_released_reservation_is_not_counted_as_reserved_stock(self):
        plan = self._plan(2, LayawayPlan.Status.NEEDS_RESOLUTION)
        plan.reservation_released = True
        plan.save(update_fields=['reservation_released'])
        self.assertNotIn(self.product.pk, services.reserved_quantities(self.pharmacy, [self.product.pk]))


class LayawayCashDrawerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='mgr', password='x')
        self.pharmacy = Pharmacy.objects.create(name='Drawer Branch')
        self.day = CashDay.objects.create(pharmacy=self.pharmacy, date=timezone.localdate(),
                                          opening_cash=D('10000'), opened_by=self.user)
        self.product = Product.objects.create(pharmacy=self.pharmacy, name='Perfume')
        ProductBatch.objects.create(product=self.product, initial_quantity=10, quantity=10,
                                    purchase_price=D('3000'), selling_price=D('5000'))
        self.plan = LayawayPlan.objects.create(
            pharmacy=self.pharmacy, product=self.product, quantity=6, customer_name='Jummo',
            customer_phone='+255610117824', unit_price=D('5000'), created_by=self.user)

    def _pay(self, amount, method):
        LayawayPayment.objects.create(plan=self.plan, amount=D(amount), method=method, received_by=self.user)

    def test_cash_payment_is_in_the_drawer_the_day_it_is_received_mobile_is_not(self):
        self._pay('10000', 'CASH')
        self._pay('5000', 'MOBILE')
        self.assertEqual(CashLedgerService.expected_cash(self.day), D('20000'))
        t = CashLedgerService.totals(self.day)
        self.assertEqual(t.cash_layaway_collected, D('10000'))
        kinds = [e.kind for e in CashLedgerService.entries(self.day)]
        self.assertEqual(kinds, ['LAYAWAY_PAYMENT'])

    def test_handover_does_not_count_the_cash_again(self):
        self._pay('10000', 'CASH')
        self._pay('20000', 'MOBILE')
        self.plan.status = LayawayPlan.Status.PAID_PENDING_HANDOVER
        self.plan.save()
        services.hand_over(self.plan.pk, self.user, self.pharmacy)
        self.assertEqual(CashLedgerService.expected_cash(self.day), D('20000'))   # opening 10,000 + 10,000 once

    def test_daily_totals_agree_with_the_per_day_ledger(self):
        self._pay('10000', 'CASH')
        daily = CashLedgerService.daily_totals(self.pharmacy.pk, self.day.date, self.day.date)[self.day.date]
        self.assertEqual(daily.net, CashLedgerService.totals(self.day).net)

    def test_cash_refund_on_cancel_reduces_the_drawer_mobile_refund_does_not(self):
        self._pay('10000', 'CASH')
        LayawayRefund.objects.create(plan=self.plan, amount=D('4000'), method='CASH', refunded_by=self.user)
        LayawayRefund.objects.create(plan=self.plan, amount=D('1000'), method='MOBILE', refunded_by=self.user)
        self.assertEqual(CashLedgerService.expected_cash(self.day), D('16000'))


class LayawayPartialHandoverTests(LayawayHandoverTests):
    """Partial collections are sales per quantity, without charging payments twice."""
    def test_partial_handover_leaves_remaining_quantity_reserved(self):
        plan = self._plan(status=LayawayPlan.Status.PAID_PENDING_HANDOVER)
        sale = services.hand_over(plan.pk, self.user, self.pharmacy, quantity=5, note='Customer collected five')
        plan.refresh_from_db()
        self.assertEqual(plan.quantity_handed_over, 5)
        self.assertEqual(plan.quantity_remaining, 1)
        self.assertEqual(plan.status, LayawayPlan.Status.PAID_PENDING_HANDOVER)
        self.assertEqual(services.reservation_for_product(self.product)['qty'], 1)
        self.assertEqual(sale.items.aggregate(v=Sum('quantity'))['v'], 5)

    def test_partial_handover_cannot_exceed_units_covered_by_payments(self):
        plan = self._plan(status=LayawayPlan.Status.ACTIVE)
        LayawayPayment.objects.create(plan=plan, amount=D('5000'), method='CASH', received_by=self.user)
        with self.assertRaises(services.HandoverError):
            services.hand_over(plan.pk, self.user, self.pharmacy, quantity=2)

    def test_second_handover_completes_plan_without_duplicate_payment(self):
        plan = self._plan(status=LayawayPlan.Status.PAID_PENDING_HANDOVER)
        services.hand_over(plan.pk, self.user, self.pharmacy, quantity=5)
        sale = services.hand_over(plan.pk, self.user, self.pharmacy, quantity=1)
        plan.refresh_from_db()
        self.assertEqual(plan.status, LayawayPlan.Status.COMPLETED)
        self.assertEqual(plan.quantity_handed_over, 6)
        self.assertEqual(plan.quantity_remaining, 0)
        self.assertEqual(plan.handovers.count(), 2)
