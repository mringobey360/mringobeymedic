from django.test import TestCase
from django.urls import reverse

from .models import PaymentSettings, SignupRequest


class SignupCoreOnlyTests(TestCase):
    """Registration sells only the core subscription; add-ons live inside the system."""

    def setUp(self):
        PaymentSettings.load()

    def _payload(self, **extra):
        data = {
            'business_type': 'RETAIL', 'business_name': 'Neema Stores',
            'owner_first_name': 'Neema', 'owner_last_name': 'Juma',
            'phone_number': '0712345678', 'tier': 'STARTER', 'plan': 'MONTHLY',
        }
        data.update(extra)
        return data

    def test_signup_page_has_no_sms_package_fields(self):
        html = self.client.get(reverse('hq:signup')).content.decode()
        self.assertNotIn('name="sms_package"', html)
        self.assertNotIn('name="receipt_sms_package"', html)

    def test_signup_redirects_to_payment_page_with_core_price_only(self):
        resp = self.client.post(reverse('hq:signup'), self._payload(sms_package='1', receipt_sms_package='1'))
        signup = SignupRequest.objects.get(business_name='Neema Stores')
        self.assertIsNone(signup.sms_package)
        self.assertIsNone(signup.receipt_sms_package)
        self.assertRedirects(
            resp, reverse('hq:payment_instructions', kwargs={'public_id': signup.public_id}),
            fetch_redirect_response=False,
        )
        page = self.client.get(reverse('hq:payment_instructions', kwargs={'public_id': signup.public_id}))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['amount'], PaymentSettings.load().price_for('MONTHLY', 'STARTER'))
