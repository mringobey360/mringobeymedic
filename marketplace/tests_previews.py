"""Free preview of digital products: validation, seller form rules, streaming and the buyer page."""
import shutil
import tempfile
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import views
from .digital import parse_whats_inside, preview_kind, validate_preview_upload
from .models import Category, Product
from .tests import ShopFixtureMixin
from .tests_digital import PDF, PrivateStorageMixin

BIG_PDF = PDF + b'0' * 6000


def real_pdf(pages=10):
    """A genuine multi-page PDF (the minimal PDF constant above is not parseable)."""
    from reportlab.pdfgen import canvas
    buf = BytesIO()
    c = canvas.Canvas(buf)
    for i in range(pages):
        c.drawString(100, 700, 'Chapter page %d' % (i + 1))
        c.showPage()
    c.save()
    return buf.getvalue()
MP3 = b'ID3' + bytes(range(256)) * 8          # 2,051 bytes
MP4 = b'\x00\x00\x00\x18ftypmp42' + bytes(range(256)) * 8


def up(name, data, ctype='application/octet-stream'):
    return SimpleUploadedFile(name, data, content_type=ctype)


class PreviewValidationTests(SimpleTestCase):
    def test_real_pdf_mp3_and_mp4_samples_are_accepted(self):
        self.assertIsNone(validate_preview_upload(up('s.pdf', PDF)))
        self.assertIsNone(validate_preview_upload(up('s.mp3', MP3)))
        self.assertIsNone(validate_preview_upload(up('s.mp4', MP4)))

    def test_extension_must_match_content_and_be_allowed(self):
        self.assertIn('real PDF', validate_preview_upload(up('s.pdf', b'not a pdf')))
        self.assertIn('MP3', validate_preview_upload(up('s.mp3', b'plain text here')))
        self.assertIn('PDF, MP3, MP4 or WebM', validate_preview_upload(up('s.zip', b'PK\x03\x04data')))
        self.assertIn('empty', validate_preview_upload(up('s.pdf', b'')))

    def test_sample_must_be_smaller_than_the_paid_file(self):
        self.assertIn('smaller', validate_preview_upload(up('s.pdf', PDF), full_size=len(PDF)))
        self.assertIsNone(validate_preview_upload(up('s.pdf', PDF), full_size=len(PDF) + 1))

    def test_audio_sample_must_be_a_short_extract_of_the_paid_file(self):
        self.assertIn('short extract', validate_preview_upload(up('s.mp3', MP3), full_size=len(MP3) + 100))
        self.assertIsNone(validate_preview_upload(up('s.mp3', MP3), full_size=len(MP3) * 5))

    def test_oversize_sample_is_rejected(self):
        f = up('s.pdf', PDF)
        f.size = 16 * 1024 * 1024
        self.assertIn('15 MB', validate_preview_upload(f))

    def test_kind_by_extension(self):
        self.assertEqual((preview_kind('a.PDF'), preview_kind('a.mp3'), preview_kind('a.webm'), preview_kind('a.zip')),
                         ('pdf', 'audio', 'video', ''))

    def test_whats_inside_is_cleaned(self):
        items = parse_whats_inside('- Stock sheet\n\u2022 Debt  register\r\n\nstock sheet\n  * Guide ')
        self.assertEqual(items, ['Stock sheet', 'Debt register', 'Guide'])
        self.assertEqual(len(parse_whats_inside('\n'.join('item %d' % i for i in range(40)))), 12)


class PreviewFixture(PrivateStorageMixin, ShopFixtureMixin):
    def setup_previews(self):
        self.use_temp_private_storage()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.manager, self.store = self.make_shop('Biashara')
        self.cat = Category.objects.create(seller_store=self.store, name='Templates', slug='biashara-templates')

    def make_digital(self, slug='biz-kit', preview=('sample.pdf', PDF), **extra):
        defaults = dict(category=self.cat, seller_store=self.store, seller_name='Biashara', name='Biz Kit', slug=slug,
                        description='Excel templates', price=5000, kind='DIGITAL', digital_file_name='kit.pdf',
                        digital_file_size=len(BIG_PDF))
        defaults.update(extra)
        p = Product.objects.create(**defaults)
        p.digital_file.save('kit.pdf', ContentFile(BIG_PDF), save=False)
        if preview:
            p.preview_file.save(preview[0], ContentFile(preview[1]), save=False)
        p.save()
        return p


class PreviewFormTests(PreviewFixture, TestCase):
    def setUp(self):
        self.setup_previews()

    def _data(self, **extra):
        data = {'kind': 'DIGITAL', 'name': 'Biz Kit', 'description': 'Excel templates', 'price': '5000',
                'category': self.cat.pk, 'is_active': 'on', 'rights_confirmed': 'on'}
        data.update(extra)
        return data

    def _files(self, **extra):
        files = {'digital_file': up('kit.pdf', BIG_PDF, 'application/pdf')}
        files.update(extra)
        return files

    def test_valid_preview_and_facts_are_saved(self):
        data = self._data(whats_inside='- Stock sheet\n- Debt register', content_summary='  120   pages ', content_language='Kiswahili')
        form = views.SellerProductForm(data, self._files(preview_file=up('s.pdf', PDF, 'application/pdf')), store=self.store)
        self.assertTrue(form.is_valid(), form.errors)
        p = form.save(commit=False)
        p.seller_store = self.store
        p.save()
        self.assertTrue(p.has_preview)
        self.assertEqual(p.preview_kind, 'pdf')
        self.assertEqual(p.preview_items, ['Stock sheet', 'Debt register'])
        self.assertEqual(p.content_summary, '120 pages')

    def test_preview_must_be_smaller_than_the_paid_file(self):
        form = views.SellerProductForm(self._data(), self._files(preview_file=up('s.pdf', BIG_PDF, 'application/pdf')), store=self.store)
        self.assertFalse(form.is_valid())
        self.assertIn('preview_file', form.errors)

    def test_fake_preview_file_is_rejected(self):
        form = views.SellerProductForm(self._data(), self._files(preview_file=up('s.pdf', b'not a pdf', 'application/pdf')), store=self.store)
        self.assertFalse(form.is_valid())
        self.assertIn('preview_file', form.errors)

    def test_physical_product_cannot_have_a_preview(self):
        data = self._data(kind='PHYSICAL', stock='3')
        form = views.SellerProductForm(data, {'preview_file': up('s.pdf', PDF, 'application/pdf')}, store=self.store)
        self.assertFalse(form.is_valid())
        self.assertIn('preview_file', form.errors)

    def test_too_many_checklist_items_are_rejected(self):
        form = views.SellerProductForm(self._data(whats_inside='\n'.join('item %d' % i for i in range(13))), self._files(), store=self.store)
        self.assertFalse(form.is_valid())
        self.assertIn('whats_inside', form.errors)

    def test_remove_preview_clears_the_sample(self):
        p = self.make_digital()
        self.assertTrue(p.has_preview)
        form = views.SellerProductForm(self._data(remove_preview='on'), {}, instance=p, store=self.store)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertFalse(saved.preview_file)


class PreviewPageTests(PreviewFixture, TestCase):
    def setUp(self):
        self.setup_previews()
        self.client = Client()

    def _file_url(self, p, token=None):
        from .preview_views import make_token
        return reverse('marketplace:preview_file', args=[p.slug, token or make_token(p.slug)])

    def test_preview_url_serves_the_viewer_page_never_a_pdf(self):
        p = self.make_digital(preview=('sample.pdf', real_pdf(3)))
        for headers in ({}, {'HTTP_SEC_FETCH_DEST': 'document'}, {'HTTP_SEC_FETCH_DEST': 'empty'}):   # service workers drop Sec-Fetch-Dest
            r = self.client.get(reverse('marketplace:preview', args=[p.slug]), **headers)
            self.assertEqual(r.status_code, 200)
            self.assertIn('text/html', r['Content-Type'])
            self.assertEqual(r['X-Frame-Options'], 'SAMEORIGIN')
            self.assertIn('no-store', r['Cache-Control'])
            html = r.content.decode()
            self.assertIn('Free sample', html)
            self.assertIn(reverse('marketplace:detail', args=[p.slug]), html)       # Buy button
            self.assertIn('/preview/file/', html)
            self.assertNotIn('<iframe', html)
            self.assertNotIn('<embed', html)
        embedded = self.client.get(reverse('marketplace:preview', args=[p.slug]) + '?embed=1').content.decode()
        self.assertIn('class="embed"', embedded)

    def test_pdf_bytes_need_the_viewer_header_and_a_valid_token(self):
        p = self.make_digital(preview=('sample.pdf', real_pdf(3)))
        url = self._file_url(p)
        self.assertEqual(self.client.get(url).status_code, 403)                                    # plain navigation / new tab
        self.assertEqual(self.client.get(url, HTTP_X_PV_VIEWER='1', HTTP_SEC_FETCH_DEST='document').status_code, 403)
        self.assertEqual(self.client.get(url, HTTP_X_PV_VIEWER='1', HTTP_SEC_FETCH_DEST='iframe').status_code, 403)
        self.assertEqual(self.client.get(self._file_url(p, 'forged:token:value'), HTTP_X_PV_VIEWER='1').status_code, 403)
        other = self.make_digital(slug='other', preview=('o.pdf', real_pdf(3)))
        from .preview_views import make_token
        self.assertEqual(self.client.get(self._file_url(p, make_token(other.slug)), HTTP_X_PV_VIEWER='1').status_code, 403)
        self.assertEqual(self.client.get(url, HTTP_X_PV_VIEWER='1', HTTP_SEC_FETCH_DEST='empty').status_code, 200)

    def test_expired_token_is_refused(self):
        from unittest import mock
        p = self.make_digital(preview=('sample.pdf', real_pdf(3)))
        url = self._file_url(p)
        with mock.patch('marketplace.preview_views.TOKEN_MAX_AGE', -1):
            self.assertEqual(self.client.get(url, HTTP_X_PV_VIEWER='1').status_code, 403)

    def test_file_endpoint_serves_only_the_protected_sample(self):
        from pypdf import PdfReader
        from .preview_protect import max_pages
        raw = real_pdf(12)
        p = self.make_digital(preview=('sample.pdf', raw))
        r = self.client.get(self._file_url(p), HTTP_X_PV_VIEWER='1')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r['Content-Disposition'].startswith('inline'))
        self.assertIn('no-store', r['Cache-Control'])
        body = r.content
        self.assertNotEqual(body, raw)                       # never the uploaded file itself
        reader = PdfReader(BytesIO(body))
        self.assertTrue(reader.is_encrypted)
        self.assertTrue(reader.decrypt(''))
        self.assertEqual(len(reader.pages), max_pages())     # 12 uploaded pages, only the first few are public
        self.assertEqual(reader.user_access_permissions, 0)  # no print / copy / edit

    def test_sample_with_few_pages_is_kept_whole(self):
        from pypdf import PdfReader
        p = self.make_digital(slug='short', preview=('sample.pdf', real_pdf(2)))
        r = self.client.get(self._file_url(p), HTTP_X_PV_VIEWER='1')
        reader = PdfReader(BytesIO(r.content))
        reader.decrypt('')
        self.assertEqual(len(reader.pages), 2)

    def test_broken_pdf_is_never_served_raw(self):
        p = self.make_digital(slug='broken')                  # the tiny fake PDF cannot be parsed
        r = self.client.get(self._file_url(p), HTTP_X_PV_VIEWER='1')
        self.assertEqual(r.status_code, 404)

    def test_stored_sample_name_is_random_not_the_seller_file_name(self):
        p = self.make_digital(preview=('my-secret-book.pdf', real_pdf(3)))
        self.assertNotIn('secret', p.preview_file.name)
        self.assertRegex(p.preview_file.name, r'^marketplace/previews/[0-9a-f]{32}\.pdf$')

    def test_preview_endpoint_is_rate_limited(self):
        from django.core.cache import cache
        from . import preview_views
        cache.clear()
        p = self.make_digital(preview=('sample.pdf', real_pdf(2)))
        url = reverse('marketplace:preview', args=[p.slug])
        old = preview_views.RATE_LIMIT
        preview_views.RATE_LIMIT = 3
        self.addCleanup(setattr, preview_views, 'RATE_LIMIT', old)
        codes = [self.client.get(url).status_code for _ in range(5)]
        self.assertEqual(codes[:3], [200, 200, 200])
        self.assertEqual(codes[-1], 429)
        cache.clear()

    def test_stream_supports_range_for_audio(self):
        p = self.make_digital(slug='talk', preview=('clip.mp3', MP3))
        r = self.client.get(reverse('marketplace:preview', args=[p.slug]), HTTP_RANGE='bytes=0-9')
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r['Content-Range'], 'bytes 0-9/%d' % len(MP3))
        self.assertEqual(b''.join(r.streaming_content), MP3[:10])

    def test_hidden_products_and_movies_have_no_public_sample(self):
        hidden = self.make_digital(slug='hidden', is_active=False)
        movie = self.make_digital(slug='film', is_movie=True)
        for p in (hidden, movie):
            self.assertEqual(self.client.get(reverse('marketplace:preview', args=[p.slug])).status_code, 404)

    def test_missing_sample_is_404(self):
        p = self.make_digital(slug='nosample', preview=None)
        self.assertEqual(self.client.get(reverse('marketplace:preview', args=[p.slug])).status_code, 404)

    def test_detail_page_shows_preview_panel_and_pdf_viewer(self):
        p = self.make_digital(whats_inside='Stock sheet\nDebt register', content_summary='120 pages', content_language='Kiswahili')
        html = self.client.get(reverse('marketplace:detail', args=[p.slug])).content.decode()
        for text in ('Preview before you buy', 'Read the free sample', "What's inside", 'Stock sheet', '120 pages', 'Kiswahili', 'id="pv-modal"'):
            self.assertIn(text, html)

    def test_detail_page_without_preview_info_has_no_panel(self):
        p = self.make_digital(slug='plain', preview=None)
        html = self.client.get(reverse('marketplace:detail', args=[p.slug])).content.decode()
        self.assertNotIn('Preview before you buy', html)

    def test_facts_only_panel_has_no_viewer(self):
        p = self.make_digital(slug='facts', preview=None, content_summary='8 lessons')
        html = self.client.get(reverse('marketplace:detail', args=[p.slug])).content.decode()
        self.assertIn('Preview before you buy', html)
        self.assertNotIn('Read the free sample', html)
        self.assertNotIn('id="pv-modal"', html)

    def test_catalog_card_shows_preview_badge(self):
        self.make_digital()
        html = self.client.get(reverse('marketplace:catalog')).content.decode()
        self.assertIn('pv-tag', html)
