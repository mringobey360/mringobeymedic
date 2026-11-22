from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import Order, OrderItem, ProductReview
from .tests import ShopFixtureMixin


class ProductReviewTests(ShopFixtureMixin, TestCase):
    def setUp(self):
        cache.clear()
        _, self.store = self.make_shop('Alpha')
        self.product = self.make_product(self.store, name='Perfume')
        self.order = self._order(phone='255712345678', paid=True)
        self.url = reverse('marketplace:submit_review', args=[self.product.slug])

    def _order(self, phone, paid, status='fulfilled'):
        order = Order.objects.create(
            seller_store=self.store, customer_name='Asha Mushi', phone=phone, location='Dodoma', total=20000, status=status,
            payment_status=Order.PaymentStatus.PAID if paid else Order.PaymentStatus.UNPAID)
        OrderItem.objects.create(order=order, product=self.product, name='Perfume', quantity=1, unit_price=Decimal('20000'))
        return order

    def _post(self, **kw):
        data = {'reference': str(self.order.reference), 'phone': '0712345678', 'rating': '5', 'comment': 'Great'}
        data.update(kw)
        return self.client.post(self.url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_verified_buyer_can_rate_and_average_updates(self):
        r = self._post()
        self.assertEqual(r.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual((self.product.rating_count, self.product.rating_avg), (1, Decimal('5.00')))

    def test_ticket_id_format_works(self):
        ticket = 'SP-' + str(self.order.reference).replace('-', '')[:8].upper()
        self.assertEqual(self._post(reference=ticket).status_code, 200)

    def test_wrong_phone_is_rejected(self):
        r = self._post(phone='0799999999')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ProductReview.objects.count(), 0)

    def test_unpaid_order_cannot_rate(self):
        unpaid = self._order('255700000001', paid=False)
        self.assertEqual(self._post(reference=str(unpaid.reference), phone='0700000001').status_code, 400)

    def test_rating_must_be_1_to_5(self):
        for bad in ('0', '6', 'x', ''):
            self.assertEqual(self._post(rating=bad).status_code, 400)

    def test_same_order_updates_existing_review(self):
        self._post(rating='5')
        self._post(rating='2', comment='Changed my mind')
        self.assertEqual(ProductReview.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual((self.product.rating_count, self.product.rating_avg), (1, Decimal('2.00')))

    def test_hiding_a_review_recalculates_rating(self):
        self._post(rating='4')
        ProductReview.objects.update(is_visible=False)
        review = ProductReview.objects.get()
        review.save()  # signal path used by the admin action
        self.product.refresh_from_db()
        self.assertEqual((self.product.rating_count, self.product.rating_avg), (0, Decimal('0.00')))

    def test_detail_page_shows_reviews(self):
        self._post(rating='4', comment='Lovely scent')
        page = self.client.get(reverse('marketplace:detail', args=[self.product.slug]))
        self.assertContains(page, 'Lovely scent')
        self.assertContains(page, 'Verified purchase')
        self.assertContains(page, 'Asha M.')
