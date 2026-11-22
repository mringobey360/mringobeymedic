"""Digital downloads, private files, seller sidebar and .jfif photo handling."""
import shutil
import tempfile
import uuid
from decimal import Decimal
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase
from django.urls import resolve, reverse

from pharmacies.models import Pharmacy

from . import orders as order_flow
from . import views
from .context import seller_workspace
from .digital import normalise_image_name, validate_digital_upload
from .listing_views import _validate_images
from .models import Category, DownloadGrant, Order, OrderItem, Product
from .tests import ShopFixtureMixin, User

PDF = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n'


class PrivateStorageMixin:
    """Keep test uploads out of the real private folder."""
    def use_temp_private_storage(self):
        self.tmp = tempfile.mkdtemp()
        field = Product._meta.get_field('digital_file')
        original = field.storage
        field.storage = FileSystemStorage(location=self.tmp)
        self.addCleanup(lambda: setattr(field, 'storage', original))
        self.addCleanup(shutil.rmtree, self.tmp, True)


class DigitalValidationTests(TestCase):
    def test_extension_must_match_content(self):
        fake = SimpleUploadedFile('book.pdf', b'this is not a pdf', content_type='application/pdf')
        self.assertIn('real PDF', validate_digital_upload(fake))
        real = SimpleUploadedFile('book.pdf', PDF, content_type='application/pdf')
        self.assertIsNone(validate_digital_upload(real))

    def test_other_formats_are_checked_by_signature(self):
        self.assertIsNone(validate_digital_upload(SimpleUploadedFile('a.epub', b'PK\x03\x04rest')))
        self.assertIsNotNone(validate_digital_upload(SimpleUploadedFile('a.epub', b'nope')))
        self.assertIsNone(validate_digital_upload(SimpleUploadedFile('m.mp4', b'\x00\x00\x00\x18ftypmp42')))
        self.assertIsNone(validate_digital_upload(SimpleUploadedFile('s.mp3', b'ID3\x03\x00')))
        self.assertIsNone(validate_digital_upload(SimpleUploadedFile('m.mov', b'\x00\x00\x00\x18ftypqt  ')))
        self.assertIsNone(validate_digital_upload(SimpleUploadedFile('m.webm', b'\x1aE\xdf\xa3rest')))
        self.assertIsNone(validate_digital_upload(SimpleUploadedFile('m.avi', b'RIFFxxxxAVI rest')))

    def test_dangerous_or_unknown_types_are_refused(self):
        for name in ('run.exe', 'page.html', 'shell.php', 'x.svg'):
            self.assertIsNotNone(validate_digital_upload(SimpleUploadedFile(name, b'data')), name)

    def test_empty_file_refused(self):
        self.assertIsNotNone(validate_digital_upload(SimpleUploadedFile('a.pdf', b'')))


class JfifPhotoTests(TestCase):
    def _jpeg(self, name):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new('RGB', (20, 20), 'red').save(buf, 'JPEG')
        return SimpleUploadedFile(name, buf.getvalue(), content_type='image/jpeg')

    def test_jfif_photo_is_accepted_and_stored_as_jpg(self):
        ok, errors = _validate_images([self._jpeg('images (4).jfif')])
        self.assertEqual(errors, [])
        self.assertEqual(len(ok), 1)
        self.assertTrue(ok[0].name.endswith('.jpg'))

    def test_non_image_with_image_name_is_refused(self):
        bad = SimpleUploadedFile('virus.jfif', b'MZ not an image')
        ok, errors = _validate_images([bad])
        self.assertEqual(ok, [])
        self.assertEqual(len(errors), 1)

    def test_gif_is_still_refused(self):
        self.assertTrue(_validate_images([SimpleUploadedFile('a.gif', b'GIF89a')])[1])

    def test_normalise_leaves_other_names_alone(self):
        f = SimpleUploadedFile('photo.png', b'x')
        self.assertEqual(normalise_image_name(f).name, 'photo.png')


class DigitalProductFormTests(PrivateStorageMixin, ShopFixtureMixin, TestCase):
    def setUp(self):
        self.use_temp_private_storage()
        self.manager, self.store = self.make_shop('Books')
        self.cat = Category.objects.create(seller_store=self.store, name='E-books', slug='books-ebooks')

    def _data(self, **extra):
        data = {'kind': 'DIGITAL', 'name': 'Form Four Notes', 'description': 'All subjects', 'price': '5000',
                'category': self.cat.pk, 'is_active': 'on'}
        data.update(extra)
        return data

    def test_digital_needs_a_file(self):
        form = views.SellerProductForm(self._data(rights_confirmed='on'), store=self.store)
        self.assertFalse(form.is_valid())
        self.assertIn('digital_file', form.errors)

    def test_digital_needs_rights_confirmation(self):
        files = {'digital_file': SimpleUploadedFile('notes.pdf', PDF, content_type='application/pdf')}
        form = views.SellerProductForm(self._data(), files, store=self.store)
        self.assertFalse(form.is_valid())
        self.assertIn('rights_confirmed', form.errors)

    def test_digital_saves_without_stock_and_records_file_details(self):
        files = {'digital_file': SimpleUploadedFile('notes.pdf', PDF, content_type='application/pdf')}
        form = views.SellerProductForm(self._data(rights_confirmed='on'), files, store=self.store)
        self.assertTrue(form.is_valid(), form.errors)
        product = form.save(commit=False)
        product.seller_store = self.store
        product.save()
        self.assertTrue(product.is_digital)
        self.assertEqual(product.stock, 0)
        self.assertEqual(product.digital_file_name, 'notes.pdf')
        self.assertEqual(product.digital_file_size, len(PDF))
        self.assertTrue(product.in_stock)
        self.assertEqual(product.max_cart_qty, 1)
        self.assertNotIn('notes', product.digital_file.name)   # stored under a random name

    def test_physical_product_still_requires_stock(self):
        form = views.SellerProductForm({'kind': 'PHYSICAL', 'name': 'Pen', 'description': 'blue', 'price': '500',
                                        'category': self.cat.pk, 'is_active': 'on'}, store=self.store)
        self.assertFalse(form.is_valid())
        self.assertIn('stock', form.errors)

    def test_file_cannot_be_attached_to_a_physical_product(self):
        files = {'digital_file': SimpleUploadedFile('notes.pdf', PDF)}
        form = views.SellerProductForm({'kind': 'PHYSICAL', 'name': 'Pen', 'description': 'blue', 'price': '500', 'stock': '4',
                                        'category': self.cat.pk}, files, store=self.store)
        self.assertFalse(form.is_valid())
        self.assertIn('digital_file', form.errors)

    def test_digital_without_file_is_not_available(self):
        product = Product.objects.create(category=self.cat, seller_store=self.store, name='Empty', slug='empty',
                                         description='d', price=1000, kind='DIGITAL')
        self.assertFalse(product.in_stock)
        self.assertEqual(product.max_cart_qty, 0)


class DigitalDeliveryTests(PrivateStorageMixin, ShopFixtureMixin, TestCase):
    def setUp(self):
        self.use_temp_private_storage()
        self.manager, self.store = self.make_shop('Books')
        cat = Category.objects.create(seller_store=self.store, name='E-books', slug='books-ebooks')
        self.ebook = Product.objects.create(category=cat, seller_store=self.store, seller_name='Books', name='Notes', slug='books-notes',
                                            description='d', price=5000, kind='DIGITAL', digital_file_name='notes.pdf',
                                            digital_file_size=len(PDF))
        self.ebook.digital_file.save('x.pdf', ContentFile(PDF), save=True)
        self.pen = self.make_product(self.store, name='Pen', stock=5, price=1000)
        self.client = Client()

    def _order(self, paid=True, items=('ebook',)):
        order = Order.objects.create(seller_store=self.store, customer_name='Asha', phone='255712345678', location='x', total=Decimal('6000'),
                                     payment_status=Order.PaymentStatus.PAID if paid else Order.PaymentStatus.UNPAID)
        for key in items:
            product = getattr(self, key)
            OrderItem.objects.create(order=order, product=product, name=product.name, quantity=1, unit_price=product.price,
                                     is_digital=product.is_digital)
        return order

    def test_digital_only_paid_order_is_issued_links_and_completed(self):
        order = self._order()
        self.assertEqual(order_flow.provision_digital(order), 1)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)
        self.assertEqual(DownloadGrant.objects.filter(order_item__order=order).count(), 1)
        order_flow.provision_digital(order)   # idempotent
        self.assertEqual(DownloadGrant.objects.filter(order_item__order=order).count(), 1)

    def test_unpaid_order_gets_no_links(self):
        order = self._order(paid=False)
        self.assertEqual(order_flow.provision_digital(order), 0)
        self.assertFalse(DownloadGrant.objects.exists())

    def test_mixed_order_keeps_delivery_workflow_for_physical_items(self):
        order = self._order(items=('ebook', 'pen'))
        order_flow.provision_digital(order)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.NEW)
        self.assertEqual(DownloadGrant.objects.filter(order_item__order=order).count(), 1)

    def test_download_page_does_not_use_up_allowance_but_post_does(self):
        order = self._order(); order_flow.provision_digital(order)
        grant = DownloadGrant.objects.get()
        url = reverse('marketplace:download', args=[grant.token])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.head(url).status_code, 200)
        grant.refresh_from_db(); self.assertEqual(grant.download_count, 0)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), PDF)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])
        grant.refresh_from_db(); self.assertEqual(grant.download_count, 1)

    def test_download_post_supports_origin_null_clients(self):
        order = self._order(); order_flow.provision_digital(order)
        grant = DownloadGrant.objects.get()
        url = reverse('marketplace:download', args=[grant.token])
        client = Client(enforce_csrf_checks=True)
        response = client.post(url, HTTP_ORIGIN='null')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), PDF)
        grant.refresh_from_db()
        self.assertEqual(grant.download_count, 1)

    def test_download_endpoint_does_not_depend_on_buyer_login(self):
        order = self._order(); order_flow.provision_digital(order)
        grant = DownloadGrant.objects.get()
        url = reverse('marketplace:download', args=[grant.token])
        client = Client(enforce_csrf_checks=True)
        response = client.post(url, HTTP_ORIGIN='null')
        self.assertEqual(response.status_code, 200)

    def test_limit_is_enforced(self):
        order = self._order(); order_flow.provision_digital(order)
        grant = DownloadGrant.objects.get(); grant.max_downloads = 2; grant.save()
        url = reverse('marketplace:download', args=[grant.token])
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 403)

    def test_no_download_before_payment_is_confirmed(self):
        order = self._order(); order_flow.provision_digital(order)
        grant = DownloadGrant.objects.get()
        Order.objects.filter(pk=order.pk).update(payment_status=Order.PaymentStatus.REFUNDED)
        self.assertEqual(self.client.post(reverse('marketplace:download', args=[grant.token])).status_code, 403)

    def test_revoked_grant_is_refused(self):
        order = self._order(); order_flow.provision_digital(order)
        grant = DownloadGrant.objects.get(); grant.revoked = True; grant.save()
        self.assertEqual(self.client.post(reverse('marketplace:download', args=[grant.token])).status_code, 403)

    def test_unknown_token_is_404(self):
        self.assertEqual(self.client.get(reverse('marketplace:download', args=[uuid.uuid4()])).status_code, 404)

    def test_cancelling_a_mixed_order_revokes_downloads_and_restores_only_physical_stock(self):
        order = self._order(items=('ebook', 'pen'))
        order_flow.provision_digital(order)
        before = Product.objects.get(pk=self.pen.pk).stock
        order_flow.change_status(order.pk, Order.Status.CANCELLED, note='customer asked')
        self.assertTrue(DownloadGrant.objects.get().revoked)
        self.assertEqual(Product.objects.get(pk=self.pen.pk).stock, before + 1)
        self.assertEqual(Product.objects.get(pk=self.ebook.pk).stock, 0)

    def test_file_is_never_reachable_from_a_public_url(self):
        self.assertNotIn('/media/', self.ebook.digital_file.name)
        response = self.client.get(reverse('marketplace:detail', args=[self.ebook.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.ebook.digital_file.name)

    def test_seller_can_check_own_file_but_not_another_shops(self):
        url = reverse('marketplace:seller_product_file', args=[self.store.pk, self.ebook.pk])
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(url).status_code, 200)
        other_manager, _ = self.make_shop('Other')
        self.client.force_login(other_manager)
        self.assertEqual(self.client.get(url).status_code, 404)


class DigitalCheckoutTests(PrivateStorageMixin, ShopFixtureMixin, TestCase):
    def setUp(self):
        self.use_temp_private_storage()
        self.manager, self.store = self.make_shop('Books')
        cat = Category.objects.create(seller_store=self.store, name='E-books', slug='books-ebooks')
        self.ebook = Product.objects.create(category=cat, seller_store=self.store, name='Notes', slug='books-notes',
                                            description='d', price=5000, kind='DIGITAL', digital_file_name='notes.pdf')
        self.ebook.digital_file.save('x.pdf', ContentFile(PDF), save=True)
        self.pen = self.make_product(self.store, name='Pen', stock=5, price=1000)

    def _checkout(self, cart, **post):
        client = Client()
        session = client.session
        session['marketplace_cart'] = {str(k): v for k, v in cart.items()}
        session.save()
        data = {'name': 'Asha', 'phone': '0712345678'}
        data.update(post)
        txn = mock.Mock(merchant_reference=uuid.uuid4())
        with mock.patch('marketplace.views.create_transaction', return_value=txn):
            return client.post(reverse('marketplace:checkout'), data), txn

    def test_digital_only_cart_needs_no_delivery_location(self):
        response, txn = self._checkout({self.ebook.pk: 1})
        self.assertEqual(response.status_code, 302)
        self.assertIn(str(txn.merchant_reference), response['Location'])

    def test_cart_with_physical_item_still_needs_location(self):
        response, _ = self._checkout({self.ebook.pk: 1, self.pen.pk: 1})
        self.assertEqual(response['Location'], reverse('marketplace:cart'))
        response, txn = self._checkout({self.ebook.pk: 1, self.pen.pk: 1}, location='Dodoma')
        self.assertIn(str(txn.merchant_reference), response['Location'])

    def test_digital_item_cannot_be_bought_twice_in_one_order(self):
        client = Client()
        response = client.post(reverse('marketplace:add_cart', args=[self.ebook.pk]), {'operation': 'set', 'quantity': '2'},
                               HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)

    def test_public_store_lists_digital_items_without_stock(self):
        response = Client().get(reverse('marketplace:seller_store_public', args=[self.store.slug]))
        self.assertContains(response, 'Notes')


class SellerWorkspaceSidebarTests(ShopFixtureMixin, TestCase):
    def _ctx(self, path, user, pharmacy=None):
        request = RequestFactory().get(path)
        request.user = user
        request.pharmacy = pharmacy
        request.resolver_match = resolve(path)
        return seller_workspace(request)

    def test_seller_centre_pages_switch_to_the_seller_sidebar(self):
        manager, store = self.make_shop('Alpha')
        ctx = self._ctx(reverse('marketplace:seller_dashboard'), manager, store.pharmacy)
        self.assertTrue(ctx['in_seller_centre'])
        self.assertTrue(ctx['seller_has_pos'])
        self.assertEqual(ctx['seller_nav_store'], store)

    def test_public_marketplace_pages_do_not(self):
        manager, store = self.make_shop('Alpha')
        self.assertFalse(self._ctx(reverse('marketplace:catalog'), manager, store.pharmacy)['in_seller_centre'])

    def test_online_seller_accounts_never_see_pos_sections(self):
        pharmacy = Pharmacy.objects.create(name='Online only', phone_number='0712345678', business_type='ONLINE_SELLER')
        manager = User.objects.create_user(username='online', password='x', role=User.Role.ADMIN)
        manager.managed_pharmacies.add(pharmacy)
        ctx = self._ctx(reverse('dashboard:home'), manager, pharmacy)
        self.assertTrue(ctx['in_seller_centre'])
        self.assertFalse(ctx['seller_has_pos'])

    def test_anonymous_and_owner_are_unaffected(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(self._ctx(reverse('marketplace:seller_dashboard'), AnonymousUser())['in_seller_centre'])
        owner = User.objects.create_superuser(username='root', password='x', email='r@example.com', role=User.Role.ADMIN)
        self.assertFalse(self._ctx(reverse('marketplace:seller_dashboard'), owner)['in_seller_centre'])
