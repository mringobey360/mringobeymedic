"""Run: python manage.py test sales.tests_batch_discount

A manager can limit the discount on ONE batch (ProductBatch.discount_cap_amount, TZS per unit).
The limit works together with the never-below-cost guard: the higher floor always wins, so it can only
make a sale safer. Blank = follow the product's discount policy. 0 = no discount on that batch."""
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from inventory.models import Product, ProductBatch
from pharmacies.models import Pharmacy

from .models import Sale, SaleItem
from .views import _apply_vip_discount, _batch_price_floor, _cost_floor_price, _fulfil_line_fefo


class BatchPriceFloorTests(SimpleTestCase):
    def test_without_a_limit_the_cost_guard_is_unchanged(self):
        self.assertEqual(_batch_price_floor(D('12000'), D('11000')), D('11000.00'))
        self.assertEqual(_cost_floor_price(D('8000'), D('12000'), D('11000')), D('11000.00'))

    def test_limit_raises_the_floor_above_cost(self):
        # price 12,000, cost 9,000, at most 1,000 off -> never below 11,000
        self.assertEqual(_batch_price_floor(D('12000'), D('9000'), D('1000')), D('11000.00'))
        self.assertEqual(_cost_floor_price(D('8000'), D('12000'), D('9000'), D('1000')), D('11000.00'))

    def test_discount_inside_the_limit_is_kept(self):
        self.assertEqual(_cost_floor_price(D('11500'), D('12000'), D('9000'), D('1000')), D('11500.00'))

    def test_limit_zero_means_no_discount(self):
        self.assertEqual(_cost_floor_price(D('8000'), D('12000'), D('9000'), D('0')), D('12000.00'))

    def test_limit_can_never_allow_a_sale_below_cost(self):
        # generous limit (5,000) but cost is 11,000 -> cost guard still wins
        self.assertEqual(_cost_floor_price(D('8000'), D('12000'), D('11000'), D('5000')), D('11000.00'))

    def test_price_already_below_cost_gets_no_discount_even_with_a_limit(self):
        self.assertEqual(_cost_floor_price(D('900'), D('1000'), D('1100'), D('500')), D('1000.00'))

    def test_vip_discount_respects_the_batch_limit(self):
        floor = _batch_price_floor(D('12000'), D('9000'), D('1000'))      # 11,000
        self.assertEqual(_apply_vip_discount(D('11000'), floor, D('10')), (D('11000.00'), D('0.00')))
        floor = _batch_price_floor(D('12000'), D('9000'), D('2000'))      # 10,000
        self.assertEqual(_apply_vip_discount(D('11000'), floor, D('10')), (D('10000.00'), D('1000.00')))


class BatchDiscountCheckoutTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='cashier', password='x')
        self.pharmacy = Pharmacy.objects.create(name='Test Branch')
        self.product = Product.objects.create(
            pharmacy=self.pharmacy, name='Revlon Lotion', discount_enabled=True,
            discount_type=Product.DiscountType.AMOUNT, min_discount_amount=D('0'), max_discount_amount=D('4000'),
        )
        self.sale = Sale.objects.create(pharmacy=self.pharmacy, served_by=self.user,
                                        payment_method=Sale.PaymentMethod.CASH, business_date=timezone.localdate())

    def _batch(self, qty, cap=None, cost='9000', price='12000'):
        return ProductBatch.objects.create(
            product=self.product, batch_number=f'B{ProductBatch.objects.count() + 1}', initial_quantity=qty, quantity=qty,
            purchase_price=D(cost), selling_price=D(price), discount_cap_amount=None if cap is None else D(cap),
            received_by=self.user,
        )

    def test_each_batch_gets_its_own_limit_in_one_sale(self):
        limited = self._batch(1, cap='1000')      # at most 1,000 off
        free = self._batch(1)                      # follows the product policy (cost guard only)
        fulfilled, total, _ = _fulfil_line_fefo(self.product, 2, self.sale, self.user, discount_percent=D('3000'))
        self.assertEqual(fulfilled, 2)
        prices = {i.batch_id: i.unit_price for i in SaleItem.objects.filter(sale=self.sale)}
        self.assertEqual(prices[limited.pk], D('11000.00'))   # 12,000 - 3,000 limited to 12,000 - 1,000
        self.assertEqual(prices[free.pk], D('9000.00'))        # full 3,000 discount, still at cost
        self.assertEqual(total, D('20000.00'))

    def test_limit_zero_blocks_any_discount_on_that_batch(self):
        self._batch(1, cap='0')
        _fulfil_line_fefo(self.product, 1, self.sale, self.user, discount_percent=D('3000'))
        self.assertEqual(SaleItem.objects.get(sale=self.sale).unit_price, D('12000.00'))

    def test_vip_discount_cannot_break_the_batch_limit(self):
        self._batch(1, cap='1000')
        _fulfil_line_fefo(self.product, 1, self.sale, self.user, discount_percent=D('1000'),
                          vip_discount_percent=D('10'), vip_discount_type='PERCENT')
        self.assertEqual(SaleItem.objects.get(sale=self.sale).unit_price, D('11000.00'))

    def test_batches_without_a_limit_behave_exactly_as_before(self):
        self._batch(1)
        _fulfil_line_fefo(self.product, 1, self.sale, self.user, discount_percent=D('3000'))
        self.assertEqual(SaleItem.objects.get(sale=self.sale).unit_price, D('9000.00'))
