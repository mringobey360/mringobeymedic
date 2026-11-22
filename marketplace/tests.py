from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from pharmacies.models import Pharmacy

from . import orders as order_flow
from . import views
from .models import (Category, Order, OrderItem, OnlineServiceRequest, OnlineServiceSubscription,
                     PaymentAttempt, Product, SellerStore)

User = get_user_model()


def _request(user, method='post', path='/x/', data=None):
    rf = RequestFactory()
    request = getattr(rf, method)(path, data or {})
    request.user = user
    request.session = SessionStore()
    request._messages = FallbackStorage(request)
    return request


class ShopFixtureMixin:
    def make_shop(self, name, manager=None, active=True):
        pharmacy = Pharmacy.objects.create(name=f'{name} branch', phone_number='0712345678')
        manager = manager or User.objects.create_user(username=f'{name}-mgr', password='x', role=User.Role.ADMIN)
        manager.managed_pharmacies.add(pharmacy)
        store = SellerStore.objects.create(pharmacy=pharmacy, slug=name.lower(), public_name=name, is_published=active)
        if active:
            now = timezone.now()
            OnlineServiceSubscription.objects.create(
                store=store, service=OnlineServiceSubscription.Service.ONLINE_SELLING,
                status=OnlineServiceSubscription.Status.ACTIVE, monthly_amount=10000,
                period_start=now, period_end=now + timedelta(days=30))
        return manager, store

    def make_product(self, store, name='Perfume', stock=5, price=20000, category=None):
        category = category or Category.objects.create(seller_store=store, name=f'{name} cat', slug=f'{store.slug}-{name.lower()}-cat')
        return Product.objects.create(category=category, seller_store=store, seller_name=store.public_name,
                                      name=name, slug=f'{store.slug}-{name.lower()}', description='d', price=price, stock=stock)


class CategoryIsolationTests(ShopFixtureMixin, TestCase):
    def test_product_must_use_its_own_shops_category(self):
        _, a = self.make_shop('Alpha')
        _, b = self.make_shop('Beta')
        foreign = Category.objects.create(seller_store=b, name='Shoes', slug='beta-shoes')
        form = views.SellerProductForm({'name': 'Boot', 'description': 'x', 'price': '1000', 'stock': '1', 'category': foreign.pk, 'is_active': 'on'}, store=a)
        self.assertFalse(form.is_valid())
        self.assertIn('category', form.errors)

    def test_seller_form_only_offers_own_categories_and_can_create_one(self):
        _, a = self.make_shop('Alpha')
        _, b = self.make_shop('Beta')
        Category.objects.create(seller_store=b, name='Shoes', slug='beta-shoes')
        mine = Category.objects.create(seller_store=a, name='Hair', slug='alpha-hair')
        form = views.SellerProductForm(store=a)
        self.assertEqual(list(form.fields['category'].queryset), [mine])
        form = views.SellerProductForm({'name': 'Oil', 'description': 'x', 'price': '1000', 'stock': '2', 'new_category': 'Body Oils', 'is_active': 'on'}, store=a)
        self.assertTrue(form.is_valid(), form.errors)
        product = form.save(commit=False)
        product.seller_store = a
        product.save()
        self.assertEqual(product.category.seller_store_id, a.pk)
        self.assertEqual(product.category.name, 'Body Oils')

    def test_same_category_name_allowed_in_two_shops(self):
        _, a = self.make_shop('Alpha')
        _, b = self.make_shop('Beta')
        self.assertNotEqual(views._create_store_category(a, 'Shoes').pk, views._create_store_category(b, 'Shoes').pk)
        # ...but never twice in the same shop (case-insensitive).
        self.assertEqual(views._create_store_category(a, 'shoes').pk, views._create_store_category(a, 'Shoes').pk)


class SingleShopPerManagerTests(ShopFixtureMixin, TestCase):
    def test_manager_cannot_create_a_second_shop(self):
        manager, store = self.make_shop('Alpha')
        second = Pharmacy.objects.create(name='Second branch')
        manager.managed_pharmacies.add(second)
        request = _request(manager, data={'pharmacy': second.pk, 'public_name': 'Another shop'})
        response = views.seller_store_setup(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SellerStore.objects.filter(pharmacy__in=manager.managed_pharmacies.all()).count(), 1)
        store.refresh_from_db()
        self.assertEqual(store.public_name, 'Another shop')  # the existing shop is edited instead

    def test_service_request_only_for_the_managers_own_shop(self):
        manager, store = self.make_shop('Alpha', active=False)
        other = Pharmacy.objects.create(name='Other branch')
        manager.managed_pharmacies.add(other)
        legacy = SellerStore.objects.create(pharmacy=other, slug='legacy', public_name='Legacy')
        request = _request(manager, data={'service': 'ONLINE_SELLING'})
        views.seller_request_service(request, legacy.pk)
        self.assertFalse(OnlineServiceRequest.objects.filter(store=legacy).exists())

    def test_repeated_request_reuses_the_pending_one(self):
        manager, store = self.make_shop('Alpha', active=False)
        for _ in range(2):
            views.seller_request_service(_request(manager, data={'service': 'ONLINE_SELLING'}), store.pk)
        self.assertEqual(OnlineServiceRequest.objects.filter(store=store, service='ONLINE_SELLING').count(), 1)
        self.assertEqual(OnlineServiceSubscription.objects.filter(store=store, status='PENDING').count(), 1)


class AdminReviewTests(ShopFixtureMixin, TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='root', password='x', email='r@example.com', role=User.Role.ADMIN)
        self.manager, self.store = self.make_shop('Alpha', active=False)
        self.sub = OnlineServiceSubscription.objects.create(store=self.store, service='ONLINE_SELLING', monthly_amount=10000)
        self.req = OnlineServiceRequest.objects.create(store=self.store, subscription=self.sub, requested_by=self.manager,
                                                       service='ONLINE_SELLING', status='AWAITING_PAYMENT', amount=10000)

    def test_approve_activates_and_publishes(self):
        views.admin_review_service_request(_request(self.admin, data={'action': 'approve', 'admin_note': 'ok'}), self.req.pk)
        self.req.refresh_from_db(); self.sub.refresh_from_db(); self.store.refresh_from_db()
        self.assertEqual(self.req.status, 'ACTIVE')
        self.assertEqual(self.sub.status, 'ACTIVE')
        self.assertTrue(self.store.is_published)

    def test_reject_cancels_pending_subscription(self):
        views.admin_review_service_request(_request(self.admin, data={'action': 'reject', 'admin_note': 'no'}), self.req.pk)
        self.req.refresh_from_db(); self.sub.refresh_from_db()
        self.assertEqual(self.req.status, 'REJECTED')
        self.assertEqual(self.sub.status, 'CANCELLED')
        self.assertEqual(self.req.reviewed_by_id, self.admin.pk)


class OrderWorkflowTests(ShopFixtureMixin, TestCase):
    def setUp(self):
        self.manager, self.store = self.make_shop('Alpha')
        self.product = self.make_product(self.store, stock=3)
        self.order = Order.objects.create(seller_store=self.store, customer_name='Asha', phone='255712345678', location='Dodoma',
                                          total=Decimal('40000'), payment_status=Order.PaymentStatus.PAID)
        OrderItem.objects.create(order=self.order, product=self.product, name='Perfume', quantity=2, unit_price=Decimal('20000'))

    def test_full_delivery_flow(self):
        for step in ('confirmed', 'out_for_delivery', 'fulfilled'):
            order_flow.change_status(self.order.pk, step, actor=self.manager, store=self.store)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'fulfilled')
        self.assertIsNotNone(self.order.dispatched_at)
        self.assertIsNotNone(self.order.delivered_at)
        self.assertEqual(self.order.events.count(), 3)

    def test_steps_cannot_be_skipped_or_reopened(self):
        with self.assertRaises(order_flow.TransitionError):
            order_flow.change_status(self.order.pk, 'fulfilled', store=self.store)
        order_flow.change_status(self.order.pk, 'cancelled', note='Out of stock', store=self.store)
        with self.assertRaises(order_flow.TransitionError):
            order_flow.change_status(self.order.pk, 'confirmed', store=self.store)

    def test_cancel_needs_reason_and_restocks_once(self):
        with self.assertRaises(order_flow.TransitionError):
            order_flow.change_status(self.order.pk, 'cancelled', store=self.store)
        order_flow.change_status(self.order.pk, 'cancelled', note='Customer changed mind', store=self.store)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertTrue(Order.objects.get(pk=self.order.pk).stock_restored)

    def test_another_shop_cannot_touch_the_order(self):
        _, other = self.make_shop('Beta')
        with self.assertRaises(Order.DoesNotExist):
            order_flow.change_status(self.order.pk, 'confirmed', store=other)

    def test_unpaid_order_cannot_be_processed(self):
        Order.objects.filter(pk=self.order.pk).update(payment_status='unpaid')
        with self.assertRaises(order_flow.TransitionError):
            order_flow.change_status(self.order.pk, 'confirmed', store=self.store)

    def test_order_stats_are_scoped_to_the_shop(self):
        _, other = self.make_shop('Beta')
        Order.objects.create(seller_store=other, customer_name='Zed', phone='255700000000', location='Arusha', total=1)
        stats = views._order_stats(self.store)
        self.assertEqual(stats['order_count'], 1)
        self.assertEqual(stats['new'], 1)
        self.assertEqual(stats['revenue_total'], Decimal('40000'))


class StorefrontAndCartTests(ShopFixtureMixin, TestCase):
    def test_storefront_shows_only_its_own_products_and_categories(self):
        _, a = self.make_shop('Alpha')
        _, b = self.make_shop('Beta')
        self.make_product(a, 'Perfume')
        self.make_product(b, 'Sneakers')
        body = self.client.get(reverse('marketplace:seller_store_public', args=['alpha'])).content.decode()
        self.assertIn('Perfume', body)
        self.assertNotIn('Sneakers', body)
        self.assertNotIn('Sneakers cat', body)

    def test_unavailable_shop_shows_friendly_page(self):
        self.make_shop('Closed', active=False)
        response = self.client.get(reverse('marketplace:seller_store_public', args=['closed']))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'not open right now', status_code=404)

    def test_cart_holds_one_shop_only(self):
        _, a = self.make_shop('Alpha')
        _, b = self.make_shop('Beta')
        pa, pb = self.make_product(a, 'Perfume'), self.make_product(b, 'Sneakers')
        self.client.post(reverse('marketplace:add_cart', args=[pa.pk]), {'quantity': 1})
        response = self.client.post(reverse('marketplace:add_cart', args=[pb.pk]), {'quantity': 1},
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.session['marketplace_cart'], {str(pa.pk): 1})

    def test_unsubscribed_shop_products_cannot_be_added(self):
        _, a = self.make_shop('Lapsed', active=False)
        product = self.make_product(a, 'Soap')
        self.assertEqual(self.client.post(reverse('marketplace:add_cart', args=[product.pk]), {'quantity': 1}).status_code, 404)

    def test_checkout_requires_phone_and_binds_the_shop(self):
        _, a = self.make_shop('Alpha')
        product = self.make_product(a, 'Perfume', price=20000)
        self.client.post(reverse('marketplace:add_cart', args=[product.pk]), {'quantity': 1})
        self.client.post(reverse('marketplace:checkout'), {'name': 'Asha', 'location': 'Dodoma'})
        self.assertFalse(PaymentAttempt.objects.exists())

    def test_get_add_cart_is_not_allowed(self):
        _, a = self.make_shop('Alpha')
        product = self.make_product(a, 'Perfume')
        self.assertEqual(self.client.get(reverse('marketplace:add_cart', args=[product.pk])).status_code, 405)

    def test_track_order_matches_phone_in_any_format(self):
        _, a = self.make_shop('Alpha')
        order = Order.objects.create(seller_store=a, customer_name='Asha', phone='255712345678', location='x', total=1)
        response = self.client.get(reverse('marketplace:track'), {'reference': str(order.reference), 'phone': '0712 345 678'})
        self.assertContains(response, str(order.reference))

    def test_track_order_accepts_public_sp_ticket_id(self):
        _, store = self.make_shop('Ticket recovery')
        order = Order.objects.create(seller_store=store, customer_name='Asha', phone='255610117824',
                                     location='Dodoma', total=25000,
                                     payment_status=Order.PaymentStatus.PAID)
        ticket = 'SP-' + str(order.reference).replace('-', '')[:8].upper()
        response = self.client.get(reverse('marketplace:track'),
                                   {'reference': ticket, 'phone': '0610117824'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ticket)
        self.assertContains(response, 'TSh')

    def test_track_order_accepts_alphanumeric_payment_ticket_and_checks_phone(self):
        _, a = self.make_shop('Alpha')
        order = Order.objects.create(seller_store=a, customer_name='Asha', phone='+255610117824', location='x', total=1,
                                     payment_status=Order.PaymentStatus.PAID, payment_reference='LCPCAJJZTJ8V7B')
        response = self.client.get(reverse('marketplace:track'), {'reference': 'LCPCAJJZTJ8V7B', 'phone': '0610117824'})
        self.assertContains(response, str(order.reference))
        wrong_phone = self.client.get(reverse('marketplace:track'), {'reference': 'LCPCAJJZTJ8V7B', 'phone': '0712345678'})
        self.assertContains(wrong_phone, 'No matching order found')


class LiveSearchTests(ShopFixtureMixin, TestCase):
    def test_suggest_finds_matching_product_automatically(self):
        _, store = self.make_shop('Alpha')
        self.make_product(store, 'Body Perfume', price=25000)
        data = self.client.get(reverse('marketplace:search_suggest'), {'q': 'perfu'}).json()
        self.assertEqual([r['name'] for r in data['results']], ['Body Perfume'])
        self.assertTrue(data['results'][0]['in_stock'])

    def test_suggest_returns_empty_list_for_unknown_product(self):
        _, store = self.make_shop('Alpha')
        self.make_product(store, 'Perfume')
        data = self.client.get(reverse('marketplace:search_suggest'), {'q': 'zzzxqy'}).json()
        self.assertEqual(data['results'], [])
        self.assertIn('categories', data)

    def test_suggest_ignores_very_short_queries(self):
        data = self.client.get(reverse('marketplace:search_suggest'), {'q': 'p'}).json()
        self.assertEqual(data['results'], [])

    def test_suggest_hides_products_of_unpublished_or_unpaid_shops(self):
        _, store = self.make_shop('Closed', active=False)
        self.make_product(store, 'Secret Item')
        data = self.client.get(reverse('marketplace:search_suggest'), {'q': 'secret'}).json()
        self.assertEqual(data['results'], [])

    def test_catalog_shows_friendly_message_when_nothing_matches(self):
        response = self.client.get(reverse('marketplace:catalog'), {'q': 'nonexistentthing'})
        self.assertContains(response, 'No product found for')
        self.assertContains(response, 'nonexistentthing')

    def test_multi_word_search_matches_every_word(self):
        _, store = self.make_shop('Alpha')
        self.make_product(store, 'Red Shoes')
        self.make_product(store, 'Blue Shoes')
        data = self.client.get(reverse('marketplace:search_suggest'), {'q': 'red shoes'}).json()
        self.assertEqual([r['name'] for r in data['results']], ['Red Shoes'])


class SellerProductManagementTests(ShopFixtureMixin, TestCase):
    def setUp(self):
        self.manager, self.store = self.make_shop('Alpha')
        self.product = self.make_product(self.store, 'Perfume', stock=5)
        self.client.force_login(self.manager)

    def act(self, action, **extra):
        url = reverse('marketplace:seller_product_action', args=[self.store.pk, self.product.pk])
        return self.client.post(url, {'action': action, **extra})

    def test_product_list_page_loads_and_searches(self):
        response = self.client.get(reverse('marketplace:seller_store_products', args=[self.store.pk]), {'q': 'perf'})
        self.assertContains(response, 'Perfume')
        response = self.client.get(reverse('marketplace:seller_store_products', args=[self.store.pk]), {'q': 'nothing-here'})
        self.assertContains(response, 'No products match')

    def test_toggle_hide_and_show(self):
        self.act('toggle_active')
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)
        self.act('toggle_active')
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)

    def test_set_stock_validates_input(self):
        self.act('set_stock', stock='12')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 12)
        self.act('set_stock', stock='-3')
        self.act('set_stock', stock='abc')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 12)

    def test_delete_removes_product_without_orders(self):
        self.act('delete')
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())

    def test_delete_archives_product_with_order_history(self):
        order = Order.objects.create(seller_store=self.store, customer_name='Ann', phone='255712345678', location='Dodoma', total=20000)
        OrderItem.objects.create(order=order, product=self.product, name='Perfume', quantity=1, unit_price=20000)
        self.act('delete')
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)
        self.assertEqual(self.product.stock, 0)

    def test_another_shops_manager_cannot_change_the_product(self):
        other_manager, _ = self.make_shop('Beta')
        self.client.force_login(other_manager)
        response = self.act('delete')
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_next_redirect_is_restricted_to_this_site(self):
        response = self.act('toggle_featured', next='https://evil.example/phish')
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('evil.example', response['Location'])


class PaymentCancelFlowTests(ShopFixtureMixin, TestCase):
    def setUp(self):
        _, self.store = self.make_shop('Alpha')
        self.product = self.make_product(self.store, 'Perfume', price=20000)
        self.attempt = PaymentAttempt.objects.create(
            customer_name='Ann', phone='255712345678', location='Dodoma', total=20000, seller_store=self.store,
            items_snapshot=[{'product_id': self.product.pk, 'name': 'Perfume', 'quantity': 1, 'unit_price': '20000'}])

    def test_cancelled_page_explains_nothing_was_charged(self):
        response = self.client.get(reverse('marketplace:payment_cancelled', args=[self.attempt.reference]))
        self.assertContains(response, 'Your payment was cancelled')
        self.assertContains(response, 'no money was taken')
        # The full UUID is the access key to this page: it must never be printed on screen.
        self.assertNotContains(response, str(self.attempt.reference))
        self.assertContains(response, 'SP-' + str(self.attempt.reference).replace('-', '')[:8].upper())
        self.assertIn('no-store', response['Cache-Control'])

    def test_cancelled_page_sends_paid_customers_to_the_confirmation(self):
        Order.objects.create(reference=self.attempt.reference, seller_store=self.store, customer_name='Ann',
                             phone='255712345678', location='Dodoma', total=20000,
                             payment_status=Order.PaymentStatus.PAID)
        response = self.client.get(reverse('marketplace:payment_cancelled', args=[self.attempt.reference]))
        self.assertRedirects(response, reverse('marketplace:payment_result', args=[self.attempt.reference]),
                             fetch_redirect_response=False)

    def test_unknown_reference_is_a_404(self):
        import uuid
        response = self.client.get(reverse('marketplace:payment_cancelled', args=[uuid.uuid4()]))
        self.assertEqual(response.status_code, 404)


class StoreTrendingTests(ShopFixtureMixin, TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def _order(self, store, product, qty, **kw):
        order = Order.objects.create(seller_store=store, customer_name='C', phone='0712000000', location='Dodoma', total=product.price * qty, **kw)
        OrderItem.objects.create(order=order, product=product, name=product.name, quantity=qty, unit_price=product.price)
        return order

    def test_trending_ranks_by_units_and_ignores_cancelled_and_other_shops(self):
        _, a = self.make_shop('Alpha')
        _, b = self.make_shop('Beta')
        p1, p2, p3 = (self.make_product(a, n) for n in ('Perfume', 'Soap', 'Lotion'))
        other = self.make_product(b, 'Sneakers')
        self._order(a, p2, 5)
        self._order(a, p1, 2)
        self._order(a, p3, 50, status=Order.Status.CANCELLED)
        self._order(b, other, 99)
        r = self.client.get(reverse('marketplace:seller_store_public', args=['alpha']))
        trending = r.context['trending']
        self.assertEqual([p.name for p in trending], ['Soap', 'Perfume'])
        self.assertEqual(r.context['trending_mode'], 'sales')
        self.assertContains(r, 'Trending in Alpha')
        self.assertNotContains(r, 'Sneakers')

    def test_no_sales_falls_back_to_honest_top_picks(self):
        _, a = self.make_shop('Alpha')
        self.make_product(a, 'Perfume')
        r = self.client.get(reverse('marketplace:seller_store_public', args=['alpha']))
        self.assertEqual(r.context['trending_mode'], 'picks')
        self.assertContains(r, 'Top picks from Alpha')
        self.assertNotContains(r, 'Trending in Alpha')

    def test_hidden_while_searching_or_filtering(self):
        _, a = self.make_shop('Alpha')
        p = self.make_product(a, 'Perfume')
        self._order(a, p, 3)
        r = self.client.get(reverse('marketplace:seller_store_public', args=['alpha']), {'q': 'Perf'})
        self.assertEqual(r.context['trending'], [])
        self.assertContains(r, 'Perfume')


class PlatformOwnerShopTests(TestCase):
    """The system admin sells as the "SmartPOS" business without an Online Selling subscription."""

    def setUp(self):
        self.admin = User.objects.create_superuser(username='sysroot', password='x', email='s@example.com', role=User.Role.ADMIN)

    def test_setup_creates_platform_business_and_store_is_entitled(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('marketplace:seller_store_setup'))
        self.assertEqual(resp.status_code, 200)
        platform = Pharmacy.objects.get(name__iexact='SmartPOS')
        self.assertEqual(resp.context['pharmacies'][0].pk, platform.pk)
        resp = self.client.post(reverse('marketplace:seller_store_setup'), {
            'pharmacy': platform.pk, 'public_name': 'SmartPOS Official', 'is_published': 'on'})
        self.assertEqual(resp.status_code, 302)
        store = SellerStore.objects.get(pharmacy=platform)
        self.assertTrue(store.is_published)
        self.assertTrue(views._online_selling_active(store))

    def test_platform_product_is_public_without_subscription(self):
        platform = views._platform_pharmacy()
        store = SellerStore.objects.create(pharmacy=platform, slug='smartpos', public_name='SmartPOS', is_published=True)
        cat = Category.objects.create(seller_store=store, name='General')
        product = Product.objects.create(seller_store=store, category=cat, name='Router', slug='router', price=Decimal('1000'), stock=5)
        self.assertIn(product, views._public_marketplace_products())
