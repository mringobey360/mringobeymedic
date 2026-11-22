from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from sms_integration.models import SMSDebtPackage

from .models import PaymentSettings, SignupRequest
from .packages_views import SERVICES, custom_price


class CustomSmsQuantityTests(TestCase):
    def setUp(self):
        PaymentSettings.load()
        # Published steps: 100 SMS = 10,000 (TZS 100 each), 1,000 SMS = 70,000 (TZS 70 each)
        SMSDebtPackage.objects.create(code='S100', name='Small', price=Decimal('10000'), sms_credits=100, duration_days=30, is_active=True)
        SMSDebtPackage.objects.create(code='S1000', name='Large', price=Decimal('70000'), sms_credits=1000, duration_days=30, is_active=True)
        self.client.post(reverse('hq:signup'), {
            'business_type': 'RETAIL', 'business_name': 'Neema Stores', 'owner_first_name': 'Neema',
            'owner_last_name': 'Juma', 'phone_number': '0712345678', 'account_type': 'TRIAL',
        })
        owner = SignupRequest.objects.get(business_name='Neema Stores').created_owner
        owner.must_change_password = False
        owner.save()
        self.owner = owner
        self.client.force_login(owner)

    def test_small_quantity_never_gets_the_bulk_rate(self):
        # 50 SMS is below the smallest package, so it pays the smallest package's rate (TZS 100 each).
        self.assertEqual(custom_price(SERVICES['debt'], 50), Decimal('5000'))

    def test_price_falls_as_quantity_grows(self):
        self.assertEqual(custom_price(SERVICES['debt'], 250), Decimal('25000'))     # reaches the 100 step
        self.assertEqual(custom_price(SERVICES['debt'], 2000), Decimal('140000'))   # reaches the 1,000 step (TZS 70)

    def test_quote_endpoint_returns_a_private_payable_package(self):
        resp = self.client.post(reverse('hq:custom_sms_quote'), {'service': 'debt', 'quantity': '750'})
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['price'], 75000)
        package = SMSDebtPackage.objects.get(pk=data['package_id'])
        self.assertEqual(package.custom_owner, self.owner)
        self.assertEqual(package.sms_credits, 750)

    def test_same_quantity_reuses_the_package_and_old_unpaid_quotes_are_retired(self):
        first = self.client.post(reverse('hq:custom_sms_quote'), {'service': 'debt', 'quantity': '300'}).json()
        again = self.client.post(reverse('hq:custom_sms_quote'), {'service': 'debt', 'quantity': '300'}).json()
        self.assertEqual(first['package_id'], again['package_id'])
        second = self.client.post(reverse('hq:custom_sms_quote'), {'service': 'debt', 'quantity': '400'}).json()
        self.assertFalse(SMSDebtPackage.objects.get(pk=first['package_id']).is_active)
        self.assertTrue(SMSDebtPackage.objects.get(pk=second['package_id']).is_active)

    def test_quantity_limits_are_enforced_by_the_server(self):
        for bad in ('5', '0', '-3', 'abc', '2000000'):
            resp = self.client.post(reverse('hq:custom_sms_quote'), {'service': 'debt', 'quantity': bad})
            self.assertEqual(resp.status_code, 400, bad)

    def test_calculator_is_on_the_packages_page(self):
        html = self.client.get(reverse('hq:packages_center')).content.decode()
        self.assertIn('data-csq-input', html)
        self.assertIn('id="tiers-debt"', html)
