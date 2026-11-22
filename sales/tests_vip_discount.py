from decimal import Decimal
from django.test import SimpleTestCase
from .views import _apply_vip_discount, _cost_floor_price

class VIPDiscountCostFloorTests(SimpleTestCase):
    def test_vip_discount_applies_to_checkout_price(self):
        price, discount = _apply_vip_discount(Decimal('1000'), Decimal('600'), Decimal('5'))
        self.assertEqual(price, Decimal('950.00'))
        self.assertEqual(discount, Decimal('50.00'))

    def test_discount_never_reduces_price_below_actual_batch_cost(self):
        price, discount = _apply_vip_discount(Decimal('1000'), Decimal('980'), Decimal('10'))
        self.assertEqual(price, Decimal('980.00'))
        self.assertEqual(discount, Decimal('20.00'))
        self.assertGreaterEqual(price, Decimal('980.00'))

    def test_fixed_amount_discount_is_applied_per_unit(self):
        price, discount = _apply_vip_discount(Decimal('1000'), Decimal('600'), Decimal('150'), 'AMOUNT')
        self.assertEqual(price, Decimal('850.00'))
        self.assertEqual(discount, Decimal('150.00'))

    def test_fixed_amount_discount_is_capped_at_cost_floor(self):
        price, discount = _apply_vip_discount(Decimal('1000'), Decimal('950'), Decimal('200'), 'AMOUNT')
        self.assertEqual(price, Decimal('950.00'))
        self.assertEqual(discount, Decimal('50.00'))

    def test_zero_rate_and_cost_at_or_above_price_do_not_discount(self):
        self.assertEqual(_apply_vip_discount(Decimal('1000'), Decimal('600'), Decimal('0')), (Decimal('1000.00'), Decimal('0.00')))
        self.assertEqual(_apply_vip_discount(Decimal('1000'), Decimal('1100'), Decimal('5')), (Decimal('1000.00'), Decimal('0.00')))


class ProductDiscountCostFloorTests(SimpleTestCase):
    def test_discount_that_goes_below_batch_cost_is_lifted_to_cost(self):
        # Revlon lotion: price 15,000, discount 4,000 -> 11,000, but this batch cost 12,000.
        self.assertEqual(_cost_floor_price(Decimal('11000'), Decimal('15000'), Decimal('12000')), Decimal('12000.00'))

    def test_same_discount_is_kept_on_the_cheaper_batch(self):
        self.assertEqual(_cost_floor_price(Decimal('11000'), Decimal('15000'), Decimal('11000')), Decimal('11000.00'))
        self.assertEqual(_cost_floor_price(Decimal('11000'), Decimal('15000'), Decimal('9000')), Decimal('11000.00'))

    def test_price_already_below_cost_gets_no_further_discount(self):
        self.assertEqual(_cost_floor_price(Decimal('900'), Decimal('1000'), Decimal('1100')), Decimal('1000.00'))
