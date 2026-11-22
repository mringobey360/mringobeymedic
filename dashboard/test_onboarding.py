from django.test import TestCase
from django.urls import reverse

from hq.models import PaymentSettings, SignupRequest, Subscription
from inventory.models import Product

from .onboarding import onboarding_checklist


class OnboardingChecklistTests(TestCase):
    def setUp(self):
        PaymentSettings.load()
        self.client.post(reverse('hq:signup'), {
            'business_type': 'RETAIL', 'business_name': 'Neema Stores', 'owner_first_name': 'Neema',
            'owner_last_name': 'Juma', 'phone_number': '0712345678', 'account_type': 'TRIAL',
        })
        signup = SignupRequest.objects.get(business_name='Neema Stores')
        self.owner, self.pharmacy = signup.created_owner, signup.created_pharmacy

    def test_new_trial_owner_sees_checklist_with_days_left(self):
        data = onboarding_checklist(self.owner, self.pharmacy)
        self.assertEqual(data['done'], 1)
        self.assertEqual(data['next']['key'], 'product')
        self.assertEqual(data['trial_days'], 30)

    def test_adding_a_real_product_ticks_the_step(self):
        Product.objects.create(pharmacy=self.pharmacy, name='My Own Item')
        data = onboarding_checklist(self.owner, self.pharmacy)
        self.assertEqual(data['done'], 2)
        self.assertEqual(data['next']['key'], 'sale')

    def test_employees_never_see_it(self):
        self.assertIsNone(onboarding_checklist(self.owner.__class__(role='EMPLOYEE'), self.pharmacy))
