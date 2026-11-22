from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from pharmacies.models import Pharmacy

from .models import Inquiry, Listing, ListingImage, OnlineServiceSubscription, SellerStore

User = get_user_model()


def _png(name='a.png'):
    from PIL import Image
    buf = BytesIO()
    Image.new('RGB', (40, 30), (20, 120, 100)).save(buf, 'PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


class ListingBase(TestCase):
    def setUp(self):
        cache.clear()
        self.pharmacy = Pharmacy.objects.create(name='Agent Hub', business_type='REAL_ESTATE', phone_number='0712345678')
        self.user = User.objects.create_user(username='agent', password='x', role=User.Role.ADMIN)
        self.user.managed_pharmacies.add(self.pharmacy)
        self.store = SellerStore.objects.create(pharmacy=self.pharmacy, slug='agent-hub', public_name='Agent Hub')

    def make_listing(self, **kw):
        data = dict(store=self.store, title='3 bedroom house Mtumba', slug='3-bed-mtumba', purpose='RENT',
                    property_type='HOUSE', price=Decimal('450000'), price_period='MONTH', region='Dodoma',
                    district='Dodoma Urban', contact_phone='0712345678', status='PUBLISHED',
                    published_at=timezone.now())
        data.update(kw)
        return Listing.objects.create(**data)

    def form_data(self, **kw):
        data = {'purpose': 'RENT', 'property_type': 'HOUSE', 'title': 'Nice house', 'description': 'Good house.',
                'price': '300000', 'price_period': 'MONTH', 'region': 'Dodoma', 'district': 'Dodoma Urban',
                'contact_phone': '0712 345 678', 'size_unit': 'SQM', 'title_type': ''}
        data.update(kw)
        return data


class PublicListingTests(ListingBase):
    def test_only_public_statuses_are_listed(self):
        self.make_listing()
        self.make_listing(title='Draft one', slug='draft-one', status='DRAFT')
        self.make_listing(title='Pending one', slug='pending-one', status='PENDING')
        r = self.client.get(reverse('marketplace:listings'))
        self.assertContains(r, '3 bedroom house Mtumba')
        self.assertNotContains(r, 'Draft one')
        self.assertNotContains(r, 'Pending one')

    def test_draft_detail_is_404(self):
        self.make_listing(status='DRAFT')
        self.assertEqual(self.client.get(reverse('marketplace:listing_detail', args=['3-bed-mtumba'])).status_code, 404)

    def test_filters_by_purpose_region_and_price(self):
        self.make_listing()
        self.make_listing(title='Plot Kisasa', slug='plot-kisasa', purpose='SALE', property_type='LAND', price=Decimal('15000000'), price_period='ONCE', region='Dodoma')
        self.assertNotContains(self.client.get(reverse('marketplace:listings_rent')), 'Plot Kisasa')
        self.assertContains(self.client.get(reverse('marketplace:listings_sale')), 'Plot Kisasa')
        self.assertNotContains(self.client.get(reverse('marketplace:listings'), {'region': 'Arusha'}), 'Plot Kisasa')
        self.assertNotContains(self.client.get(reverse('marketplace:listings'), {'max': '1000000'}), 'Plot Kisasa')

    def test_detail_shows_contact_and_counts_view_once_per_session(self):
        listing = self.make_listing()
        url = reverse('marketplace:listing_detail', args=[listing.slug])
        r = self.client.get(url)
        self.assertContains(r, 'wa.me/255712345678')
        self.client.get(url)
        listing.refresh_from_db()
        self.assertEqual(listing.views_count, 1)

    def test_inquiry_creates_lead_and_honeypot_is_ignored(self):
        listing = self.make_listing()
        url = reverse('marketplace:listing_inquiry', args=[listing.slug])
        self.client.post(url, {'name': 'Asha', 'phone': '0755111222', 'message': 'Still available?'})
        self.assertEqual(Inquiry.objects.filter(listing=listing, name='Asha').count(), 1)
        self.client.post(url, {'name': 'Bot', 'phone': '0755111222', 'website': 'spam.com'})
        self.assertFalse(Inquiry.objects.filter(name='Bot').exists())

    def test_inquiry_rejects_bad_phone_and_is_rate_limited(self):
        listing = self.make_listing()
        url = reverse('marketplace:listing_inquiry', args=[listing.slug])
        self.client.post(url, {'name': 'Asha', 'phone': '12'})
        self.assertEqual(Inquiry.objects.count(), 0)
        for _ in range(5):
            self.client.post(url, {'name': 'Asha', 'phone': '0755111222'})
        self.assertEqual(Inquiry.objects.count(), 3)

    def test_closed_listing_does_not_take_inquiries(self):
        listing = self.make_listing(status='CLOSED')
        self.client.post(reverse('marketplace:listing_inquiry', args=[listing.slug]), {'name': 'Asha', 'phone': '0755111222'})
        self.assertEqual(Inquiry.objects.count(), 0)

    def test_listing_only_store_page_is_available_without_subscription(self):
        self.make_listing()
        r = self.client.get(reverse('marketplace:seller_store_public', args=[self.store.slug]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '3 bedroom house Mtumba')


class PropertiesIndexV2Tests(ListingBase):
    """Properties page: quick type chips, active-filter chips, empty state, pager."""

    def test_index_shows_type_chips_and_icons_sprite(self):
        self.make_listing()
        r = self.client.get(reverse('marketplace:listings'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'class="ls-types"')
        self.assertContains(r, 'id="ls-i-bed"')          # icon sprite is present for the cards
        self.assertContains(r, '1</b> property found')

    def test_active_filter_chips_and_clear_link(self):
        self.make_listing()
        r = self.client.get(reverse('marketplace:listings'), {'region': 'Dodoma', 'min': '100000', 'beds': '2', 'type': 'HOUSE'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'class="ls-active"')
        self.assertContains(r, 'From TSh 100,000')
        self.assertContains(r, '2+ bedrooms')
        self.assertContains(r, 'Clear all')

    def test_empty_state_offers_clear_filters(self):
        self.make_listing()
        r = self.client.get(reverse('marketplace:listings'), {'region': 'Arusha'})
        self.assertContains(r, 'No properties match your search')
        self.assertContains(r, 'Clear all filters')

    def test_pagination_window_and_params_are_kept(self):
        for i in range(30):
            self.make_listing(title=f'House {i}', slug=f'house-{i}')
        r = self.client.get(reverse('marketplace:listings'), {'region': 'Dodoma', 'page': 2})
        self.assertContains(r, 'class="ls-pager"')
        self.assertContains(r, 'region=Dodoma')
        self.assertEqual(len(r.context['page'].object_list), 12)

    def test_show_more_json_batches(self):
        for i in range(30):
            self.make_listing(title=f'Bulk {i}', slug=f'bulk-{i}')
        url = reverse('marketplace:listings')
        page1 = self.client.get(url)
        self.assertContains(page1, 'id="ls-more"')
        self.assertContains(page1, 'id="ls-grid"')
        r2 = self.client.get(url, {'page': 2, 'partial': 1})
        self.assertEqual(r2.status_code, 200)
        data = r2.json()
        self.assertEqual(data['html'].count('class="ls-card'), 12)
        self.assertIn('page=3', data['next'])
        self.assertNotIn('partial', data['next'])
        self.assertEqual(data['total'], 30)
        r3 = self.client.get(url, {'page': 3, 'partial': 1}).json()
        self.assertEqual(r3['html'].count('class="ls-card'), 6)
        self.assertEqual(r3['next'], '')
        beyond = self.client.get(url, {'page': 9, 'partial': 1}).json()
        self.assertEqual((beyond['html'], beyond['next']), ('', ''))

    def test_show_more_keeps_filters(self):
        for i in range(14):
            self.make_listing(title=f'Dodoma {i}', slug=f'dod-{i}')
        data = self.client.get(reverse('marketplace:listings'), {'region': 'Dodoma', 'partial': 1}).json()
        self.assertIn('region=Dodoma', data['next'])

    def test_new_badge_only_for_recent_published(self):
        recent = self.make_listing(slug='recent-one', title='Recent one')
        old = self.make_listing(slug='old-one', title='Old one', published_at=timezone.now() - timedelta(days=30))
        self.assertTrue(recent.is_new)
        self.assertFalse(old.is_new)


class SellerListingTests(ListingBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_create_listing_with_photo_starts_as_draft(self):
        url = reverse('marketplace:seller_listing_add', args=[self.store.pk])
        r = self.client.post(url, {**self.form_data(), 'images': [_png('1.png'), _png('2.png')]})
        self.assertEqual(r.status_code, 302)
        listing = Listing.objects.get(title='Nice house')
        self.assertEqual(listing.status, 'DRAFT')
        self.assertEqual(listing.images.count(), 2)

    def test_non_image_upload_is_rejected(self):
        url = reverse('marketplace:seller_listing_add', args=[self.store.pk])
        bad = SimpleUploadedFile('x.png', b'not an image', content_type='image/png')
        r = self.client.post(url, {**self.form_data(), 'images': [bad]})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Listing.objects.exists())

    def test_land_ignores_bedrooms(self):
        url = reverse('marketplace:seller_listing_add', args=[self.store.pk])
        self.client.post(url, self.form_data(property_type='LAND', purpose='SALE', price_period='ONCE', bedrooms='3'))
        self.assertIsNone(Listing.objects.get(title='Nice house').bedrooms)

    def test_gps_needs_both_values(self):
        url = reverse('marketplace:seller_listing_add', args=[self.store.pk])
        r = self.client.post(url, self.form_data(latitude='-6.16'))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Listing.objects.exists())

    def test_cannot_submit_without_photo(self):
        listing = self.make_listing(status='DRAFT')
        self.client.post(reverse('marketplace:seller_listing_action', args=[self.store.pk, listing.pk]), {'action': 'submit'})
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'DRAFT')

    def test_submit_goes_to_review_then_free_limit_applies(self):
        for i in range(3):
            self.make_listing(title=f'L{i}', slug=f'l{i}', status='PUBLISHED')
        extra = self.make_listing(title='Extra', slug='extra', status='DRAFT')
        ListingImage.objects.create(listing=extra, image=_png('e.png'))
        url = reverse('marketplace:seller_listing_action', args=[self.store.pk, extra.pk])
        self.client.post(url, {'action': 'submit'})
        extra.refresh_from_db()
        self.assertEqual(extra.status, 'DRAFT')            # free limit of 3 reached
        OnlineServiceSubscription.objects.create(
            store=self.store, service='ONLINE_SELLING', status='ACTIVE', monthly_amount=10000,
            period_start=timezone.now(), period_end=timezone.now() + timedelta(days=30))
        self.client.post(url, {'action': 'submit'})
        extra.refresh_from_db()
        self.assertEqual(extra.status, 'PENDING')

    @override_settings(MARKETPLACE_LISTING_AUTO_APPROVE=True)
    def test_auto_approve_publishes_immediately(self):
        listing = self.make_listing(status='DRAFT')
        ListingImage.objects.create(listing=listing, image=_png('p.png'))
        self.client.post(reverse('marketplace:seller_listing_action', args=[self.store.pk, listing.pk]), {'action': 'submit'})
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'PUBLISHED')
        self.assertIsNotNone(listing.published_at)

    def test_other_seller_cannot_touch_my_listing(self):
        other_ph = Pharmacy.objects.create(name='Other', phone_number='0711111111')
        other = User.objects.create_user(username='other', password='x', role=User.Role.ADMIN)
        other.managed_pharmacies.add(other_ph)
        SellerStore.objects.create(pharmacy=other_ph, slug='other', public_name='Other')
        listing = self.make_listing()
        self.client.force_login(other)
        r = self.client.post(reverse('marketplace:seller_listing_action', args=[self.store.pk, listing.pk]), {'action': 'delete'})
        self.assertEqual(r.status_code, 404)
        self.assertTrue(Listing.objects.filter(pk=listing.pk).exists())

    def test_editing_live_listing_returns_it_to_review(self):
        listing = self.make_listing()
        url = reverse('marketplace:seller_listing_edit', args=[self.store.pk, listing.pk])
        self.client.post(url, self.form_data(title='Updated title'))
        listing.refresh_from_db()
        self.assertEqual((listing.title, listing.status), ('Updated title', 'PENDING'))

    def test_inquiry_status_update(self):
        listing = self.make_listing()
        inquiry = Inquiry.objects.create(listing=listing, name='Asha', phone='0755111222')
        self.client.post(reverse('marketplace:seller_store_inquiries', args=[self.store.pk]), {'inquiry': inquiry.pk, 'status': 'viewing', 'seller_note': 'Sat 10am'})
        inquiry.refresh_from_db()
        self.assertEqual((inquiry.status, inquiry.seller_note), ('viewing', 'Sat 10am'))

    def test_seller_pages_render(self):
        listing = self.make_listing()
        for name, args in (('seller_store_listings', [self.store.pk]), ('seller_listing_add', [self.store.pk]),
                           ('seller_listing_edit', [self.store.pk, listing.pk]), ('seller_store_inquiries', [self.store.pk])):
            self.assertEqual(self.client.get(reverse(f'marketplace:{name}', args=args)).status_code, 200, name)


from unittest import mock

from .models import Category, ListingReport, Product


class ExtrasTests(ListingBase):
    def test_discount_percent(self):
        cat = Category.objects.create(seller_store=self.store, name='Shoes', slug='shoes-x')
        p = Product.objects.create(category=cat, seller_store=self.store, name='Boot', slug='boot', description='d',
                                   price=Decimal('7500'), compare_at_price=Decimal('10000'), stock=3)
        self.assertEqual(p.discount_percent, 25)
        p.compare_at_price = Decimal('5000')
        self.assertEqual(p.discount_percent, 0)

    def test_report_is_saved_and_validated(self):
        listing = self.make_listing()
        url = reverse('marketplace:listing_report', args=[listing.slug])
        self.client.post(url, {'reason': 'bogus'})
        self.assertEqual(ListingReport.objects.count(), 0)
        self.client.post(url, {'reason': 'SCAM', 'details': 'asked deposit first'})
        self.assertEqual(ListingReport.objects.filter(listing=listing, reason='SCAM').count(), 1)

    def test_qr_and_flyer(self):
        listing = self.make_listing()
        qr = self.client.get(reverse('marketplace:listing_qr', args=[listing.slug]))
        self.assertEqual(qr.status_code, 200)
        self.assertTrue(qr['Content-Type'].startswith('image/svg+xml'))
        body = qr.content.decode('utf-8')
        # Regression: a standalone SVG without xmlns renders as a BROKEN <img> in browsers.
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', body)
        self.assertTrue(body.lstrip().startswith('<?xml'))

    def test_qr_png_is_a_real_png(self):
        listing = self.make_listing()
        res = self.client.get(reverse('marketplace:listing_qr_png', args=[listing.slug]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'image/png')
        self.assertTrue(res.content.startswith(b'\x89PNG'))
        dl = self.client.get(reverse('marketplace:listing_qr_png', args=[listing.slug]) + '?download=1')
        self.assertIn('attachment', dl['Content-Disposition'])

    def test_flyer_embeds_qr_inline(self):
        listing = self.make_listing()
        res = self.client.get(reverse('marketplace:listing_flyer', args=[listing.slug]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'class="qr-svg"')
        self.assertContains(res, 'Scan to view listing')

    def test_draft_has_no_qr_or_flyer(self):
        listing = self.make_listing(status='DRAFT', slug='draft-qr')
        self.assertEqual(self.client.get(reverse('marketplace:listing_qr', args=[listing.slug])).status_code, 404)
        self.assertEqual(self.client.get(reverse('marketplace:listing_flyer', args=[listing.slug])).status_code, 404)

    def test_verified_badge_shows(self):
        listing = self.make_listing()
        url = reverse('marketplace:listing_detail', args=[listing.slug])
        self.assertNotContains(self.client.get(url), 'Verified')
        self.store.verified = True
        self.store.save()
        self.assertContains(self.client.get(url), 'Verified')

    def test_lead_sms_uses_order_sms_allowance(self):
        listing = self.make_listing()
        sub = OnlineServiceSubscription.objects.create(
            store=self.store, service='ORDER_SMS', status='ACTIVE', monthly_amount=5000, sms_quota=2,
            period_start=timezone.now(), period_end=timezone.now() + timedelta(days=30))
        url = reverse('marketplace:listing_inquiry', args=[listing.slug])
        with mock.patch('sms_integration.utils.send_sms', return_value=True) as send:
            self.client.post(url, {'name': 'Asha', 'phone': '0755111222'})
        self.assertEqual(send.call_count, 1)
        sub.refresh_from_db()
        self.assertEqual(sub.sms_used, 1)

    def test_no_sms_without_addon(self):
        listing = self.make_listing()
        with mock.patch('sms_integration.utils.send_sms', return_value=True) as send:
            self.client.post(reverse('marketplace:listing_inquiry', args=[listing.slug]), {'name': 'Asha', 'phone': '0755111222'})
        send.assert_not_called()
