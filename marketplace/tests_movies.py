"""Movies: seller form rules, trailer streaming (Range), public pages and pay-before-download."""
import shutil
import tempfile
from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from . import orders as order_flow
from . import views
from .models import Category, DownloadGrant, Order, OrderItem, Product
from .tests import ShopFixtureMixin
from .tests_digital import PrivateStorageMixin

MP4 = b'\x00\x00\x00\x18ftypmp42' + bytes(range(256)) * 8      # 2,072 bytes
WEBM = b'\x1aE\xdf\xa3' + b'\x00' * 64


def poster_bytes():
    from PIL import Image
    buf = BytesIO()
    Image.new('RGB', (40, 60), 'navy').save(buf, 'JPEG')
    return buf.getvalue()


def poster(name='poster.jpg'):
    return SimpleUploadedFile(name, poster_bytes(), content_type='image/jpeg')


class MovieFixture(PrivateStorageMixin, ShopFixtureMixin):
    def setup_movies(self):
        self.use_temp_private_storage()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.manager, self.store = self.make_shop('Cinema')
        self.cat = Category.objects.create(seller_store=self.store, name='Movies', slug='cinema-movies')

    def make_movie(self, name='Safari Kali', slug='safari-kali', trailer=True, **extra):
        defaults = dict(category=self.cat, seller_store=self.store, seller_name='Cinema', name=name, slug=slug,
                        description='Action film', price=3000, kind='DIGITAL', is_movie=True, digital_file_name='movie.mp4',
                        digital_file_size=len(MP4), movie_year=2024, movie_minutes=105, movie_genres='Action, Drama',
                        movie_quality='HD')
        defaults.update(extra)
        movie = Product.objects.create(**defaults)
        movie.digital_file.save('movie.mp4', ContentFile(MP4), save=False)
        movie.image.save('poster.jpg', ContentFile(poster_bytes()), save=False)
        if trailer:
            movie.trailer_file.save('trailer.mp4', ContentFile(MP4), save=False)
        movie.save()
        return movie


class MovieFormTests(MovieFixture, TestCase):
    def setUp(self):
        self.setup_movies()

    def _data(self, **extra):
        data = {'kind': 'DIGITAL', 'is_movie': 'on', 'name': 'Safari Kali', 'description': 'Action film', 'price': '3000',
                'category': self.cat.pk, 'is_active': 'on', 'rights_confirmed': 'on', 'movie_year': '2024',
                'movie_minutes': '105', 'movie_genres': 'Action, Drama, action', 'movie_quality': 'HD'}
        data.update(extra)
        return data

    def _files(self, **extra):
        files = {'digital_file': SimpleUploadedFile('movie.mp4', MP4, content_type='video/mp4'), 'image': poster()}
        files.update(extra)
        return files

    def test_valid_movie_with_trailer_saves_all_details(self):
        files = self._files(trailer_file=SimpleUploadedFile('t.mp4', MP4, content_type='video/mp4'))
        form = views.SellerProductForm(self._data(), files, store=self.store)
        self.assertTrue(form.is_valid(), form.errors)
        movie = form.save(commit=False)
        movie.seller_store = self.store
        movie.save()
        self.assertTrue(movie.is_movie)
        self.assertTrue(movie.has_trailer)
        self.assertEqual(movie.movie_genres, 'Action, Drama')        # de-duplicated
        self.assertEqual(movie.movie_duration, '1h 45m')
        self.assertEqual(movie.stock, 0)

    def test_movie_without_trailer_is_allowed(self):
        form = views.SellerProductForm(self._data(), self._files(), store=self.store)
        self.assertTrue(form.is_valid(), form.errors)

    def test_movie_file_must_be_a_video(self):
        pdf = SimpleUploadedFile('film.pdf', b'%PDF-1.4 x', content_type='application/pdf')
        form = views.SellerProductForm(self._data(), self._files(digital_file=pdf), store=self.store)
        self.assertFalse(form.is_valid())
        self.assertIn('digital_file', form.errors)

    def test_movie_needs_a_poster(self):
        files = self._files()
        del files['image']
        form = views.SellerProductForm(self._data(), files, store=self.store)
        self.assertFalse(form.is_valid())
        self.assertIn('image', form.errors)

    def test_distribution_rights_must_be_confirmed(self):
        data = self._data()
        del data['rights_confirmed']
        form = views.SellerProductForm(data, self._files(), store=self.store)
        self.assertFalse(form.is_valid())
        self.assertIn('rights_confirmed', form.errors)

    def test_fake_or_wrong_trailer_is_refused(self):
        for bad in (SimpleUploadedFile('t.mp4', b'not a video at all'), SimpleUploadedFile('t.avi', MP4),
                    SimpleUploadedFile('t.html', b'<script>')):
            form = views.SellerProductForm(self._data(), self._files(trailer_file=bad), store=self.store)
            self.assertFalse(form.is_valid(), bad.name)
            self.assertIn('trailer_file', form.errors)

    def test_trailer_rights_are_also_required(self):
        data = self._data()
        del data['rights_confirmed']
        files = self._files(trailer_file=SimpleUploadedFile('t.webm', WEBM))
        files['digital_file'] = SimpleUploadedFile('m.mp4', MP4)
        form = views.SellerProductForm(data, files, store=self.store)
        self.assertFalse(form.is_valid())

    def test_movie_must_be_digital(self):
        data = self._data(kind='PHYSICAL', stock='3')
        form = views.SellerProductForm(data, {'image': poster()}, store=self.store)
        self.assertFalse(form.is_valid())
        self.assertIn('is_movie', form.errors)

    def test_trailer_needs_the_movie_switch(self):
        data = self._data()
        del data['is_movie']
        form = views.SellerProductForm(data, self._files(trailer_file=SimpleUploadedFile('t.mp4', MP4)), store=self.store)
        self.assertFalse(form.is_valid())
        self.assertIn('trailer_file', form.errors)

    def test_bad_year_and_minutes_are_refused(self):
        form = views.SellerProductForm(self._data(movie_year='1700', movie_minutes='5000'), self._files(), store=self.store)
        self.assertFalse(form.is_valid())
        self.assertTrue({'movie_year', 'movie_minutes'} & set(form.errors))

    def test_remove_trailer_clears_it(self):
        movie = self.make_movie()
        self.assertTrue(movie.has_trailer)
        data = self._data(remove_trailer='on', name=movie.name)
        form = views.SellerProductForm(data, {}, instance=movie, store=self.store)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertFalse(saved.trailer_file)


class TrailerStreamTests(MovieFixture, TestCase):
    def setUp(self):
        self.setup_movies()
        self.movie = self.make_movie()
        self.url = reverse('marketplace:trailer', args=[self.movie.slug])
        self.client = Client()

    def _body(self, response):
        return b''.join(response.streaming_content)

    def test_full_file_advertises_range_support(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Accept-Ranges'], 'bytes')
        self.assertEqual(r['Content-Type'], 'video/mp4')
        self.assertEqual(int(r['Content-Length']), len(MP4))
        self.assertEqual(self._body(r), MP4)

    def test_range_request_gets_206_with_exact_bytes(self):
        r = self.client.get(self.url, HTTP_RANGE='bytes=10-29')
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r['Content-Range'], 'bytes 10-29/%d' % len(MP4))
        self.assertEqual(self._body(r), MP4[10:30])

    def test_safari_probe_and_open_ended_and_suffix(self):
        r = self.client.get(self.url, HTTP_RANGE='bytes=0-1')
        self.assertEqual((r.status_code, self._body(r)), (206, MP4[:2]))
        r = self.client.get(self.url, HTTP_RANGE='bytes=2000-')
        self.assertEqual(self._body(r), MP4[2000:])
        r = self.client.get(self.url, HTTP_RANGE='bytes=-16')
        self.assertEqual(self._body(r), MP4[-16:])

    def test_out_of_range_is_416(self):
        r = self.client.get(self.url, HTTP_RANGE='bytes=999999-')
        self.assertEqual(r.status_code, 416)
        self.assertEqual(r['Content-Range'], 'bytes */%d' % len(MP4))

    def test_head_has_headers_and_no_body(self):
        r = self.client.head(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(int(r['Content-Length']), len(MP4))

    def test_hidden_or_trailerless_or_non_movie_is_404(self):
        self.movie.is_active = False
        self.movie.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        plain = self.make_movie(name='No trailer', slug='no-trailer', trailer=False)
        self.assertEqual(self.client.get(reverse('marketplace:trailer', args=[plain.slug])).status_code, 404)
        not_movie = self.make_movie(name='Plain', slug='plain-file', is_movie=False)
        self.assertEqual(self.client.get(reverse('marketplace:trailer', args=[not_movie.slug])).status_code, 404)


class MoviePagesTests(MovieFixture, TestCase):
    def setUp(self):
        self.setup_movies()
        self.movie = self.make_movie()
        self.other = self.make_movie(name='Mapenzi Matamu', slug='mapenzi-matamu', movie_genres='Romance', trailer=False)
        self.pen = self.make_product(self.store, name='Pen', stock=5, price=1000)
        self.client = Client()

    def test_movies_page_lists_only_movies(self):
        r = self.client.get(reverse('marketplace:movies'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Safari Kali')
        self.assertContains(r, 'Mapenzi Matamu')
        self.assertNotContains(r, '>Pen<')
        self.assertContains(r, 'mv-card')

    def test_movies_search_and_genre_filter(self):
        r = self.client.get(reverse('marketplace:movies'), {'genre': 'Romance'})
        self.assertContains(r, 'Mapenzi Matamu')
        self.assertNotContains(r, 'Safari Kali</span>')
        r = self.client.get(reverse('marketplace:movies'), {'q': 'safari'})
        self.assertContains(r, 'Safari Kali')

    def test_movie_card_autoplays_trailer_and_hides_private_file(self):
        r = self.client.get(reverse('marketplace:catalog'))
        self.assertContains(r, reverse('marketplace:trailer', args=[self.movie.slug]))
        self.assertContains(r, 'data-mv-media')
        self.assertNotContains(r, 'private_media')
        self.assertNotContains(r, 'digital/store-')

    def test_movie_without_trailer_still_gets_a_movie_card(self):
        r = self.client.get(reverse('marketplace:movies'))
        self.assertNotContains(r, reverse('marketplace:trailer', args=[self.other.slug]))

    def test_detail_shows_trailer_player_and_locked_notice(self):
        r = self.client.get(reverse('marketplace:detail', args=[self.movie.slug]))
        self.assertContains(r, 'mv-stage')
        self.assertContains(r, 'playsinline')
        self.assertContains(r, 'unlocked automatically after your payment')
        self.assertContains(r, '1h 45m')
        self.assertNotContains(r, 'digital/store-')

    def test_plain_products_keep_the_normal_card(self):
        r = self.client.get(reverse('marketplace:detail', args=[self.pen.slug]))
        self.assertNotContains(r, 'mv-stage')

    def test_seller_chooser_offers_movies(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('marketplace:seller_new_listing', args=[self.store.pk]))
        self.assertContains(r, '?kind=movie')

    def test_seller_form_opens_in_movie_mode(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('marketplace:seller_product_add', args=[self.store.pk]), {'kind': 'movie'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Add movie')
        self.assertContains(r, 'name="trailer_file"')


class MoviePayBeforeDownloadTests(MovieFixture, TestCase):
    def setUp(self):
        self.setup_movies()
        self.movie = self.make_movie()
        self.client = Client()

    def _order(self, paid):
        order = Order.objects.create(seller_store=self.store, customer_name='Asha', phone='255712345678', location='x',
                                     total=Decimal('3000'),
                                     payment_status=Order.PaymentStatus.PAID if paid else Order.PaymentStatus.UNPAID)
        OrderItem.objects.create(order=order, product=self.movie, name=self.movie.name, quantity=1,
                                 unit_price=self.movie.price, is_digital=True)
        return order

    def test_unpaid_order_gets_no_download_link(self):
        order = self._order(paid=False)
        order_flow.provision_digital(order)
        self.assertEqual(DownloadGrant.objects.filter(order_item__order=order).count(), 0)

    def test_paid_order_can_download_but_payment_reversal_blocks_it(self):
        order = self._order(paid=True)
        order_flow.provision_digital(order)
        grant = DownloadGrant.objects.get(order_item__order=order)
        url = reverse('marketplace:download', args=[grant.token])
        self.assertEqual(self.client.get(url).status_code, 200)
        order.payment_status = Order.PaymentStatus.UNPAID
        order.save()
        self.assertEqual(self.client.post(url).status_code, 403)
