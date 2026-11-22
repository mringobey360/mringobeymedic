from datetime import timedelta
from unittest import mock
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from pharmacies.branch_billing import branch_billing_state
from sms_integration.models import SMSDebtSubscription, SMSMarketingSubscription, SMSReceiptSubscription

from inventory.models import Product
from sales.models import Sale

from .free_plan import sales_block_message, sales_this_month
from sms_integration.models import NO_EXPIRY_DATE

from .models import FREE_PLAN_MONTHLY_SALES, PaymentSettings, SignupRequest, Subscription, TRIAL_DAYS, TRIAL_SMS_CREDITS
from .services import renew_subscription_from_payment


class FreeTrialTests(TestCase):
    def setUp(self):
        PaymentSettings.load()

    def _payload(self, **extra):
        data = {
            'business_type': 'RETAIL', 'business_name': 'Neema Stores',
            'owner_first_name': 'Neema', 'owner_last_name': 'Juma',
            'phone_number': '0712345678', 'account_type': 'TRIAL',
        }
        data.update(extra)
        return data

    def _start_trial(self, **extra):
        resp = self.client.post(reverse('hq:signup'), self._payload(**extra))
        signup = SignupRequest.objects.get(business_name=extra.get('business_name', 'Neema Stores'))
        return resp, signup

    def test_signup_page_offers_trial(self):
        html = self.client.get(reverse('hq:signup')).content.decode()
        self.assertIn('name="account_type"', html)
        self.assertIn('value="TRIAL"', html)

    def test_trial_needs_no_payment_and_ignores_tier_sent_by_browser(self):
        resp, signup = self._start_trial(tier='ENTERPRISE', plan='YEARLY')
        self.assertRedirects(resp, reverse('hq:signup_status', kwargs={'public_id': signup.public_id}), fetch_redirect_response=False)
        self.assertTrue(signup.is_trial)
        self.assertEqual(signup.status, SignupRequest.Status.ACTIVE)
        sub = Subscription.objects.get(owner=signup.created_owner)
        self.assertTrue(sub.is_trial)
        self.assertEqual(sub.tier, SignupRequest.Tier.STARTER)   # Starter features only
        self.assertEqual(sub.amount_paid, Decimal('0'))
        # One free month, then the account locks until the owner pays.
        self.assertEqual(TRIAL_DAYS, 30)
        self.assertEqual((sub.end_date - timezone.localdate()).days, TRIAL_DAYS)
        self.assertTrue(sub.is_timed_trial)
        self.assertEqual(sub.included_users, 3)       # full Starter limits during the trial
        self.assertEqual(sub.included_branches, 1)

    def test_trial_gets_twenty_sms_in_every_sms_package(self):
        _, signup = self._start_trial()
        owner = signup.created_owner
        for model in (SMSDebtSubscription, SMSReceiptSubscription, SMSMarketingSubscription):
            sub = model.objects.get(owner=owner)
            self.assertEqual(sub.credits_remaining, TRIAL_SMS_CREDITS)
            self.assertTrue(sub.is_current)
            self.assertFalse(sub.package.is_active)   # hidden from the public packages list

    def test_paid_signup_is_unchanged(self):
        resp = self.client.post(reverse('hq:signup'), self._payload(account_type='PAID', tier='STARTER', plan='MONTHLY'))
        signup = SignupRequest.objects.get(business_name='Neema Stores')
        self.assertFalse(signup.is_trial)
        self.assertEqual(signup.status, SignupRequest.Status.PENDING_PAYMENT)
        self.assertRedirects(resp, reverse('hq:payment_instructions', kwargs={'public_id': signup.public_id}), fetch_redirect_response=False)

    def test_same_phone_cannot_start_a_second_trial(self):
        self._start_trial()
        resp = self.client.post(reverse('hq:signup'), self._payload(business_name='Another Shop'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(SignupRequest.objects.filter(business_name='Another Shop').exists())

    def _expire(self, sub, days_ago=1):
        Subscription.objects.filter(pk=sub.pk).update(end_date=timezone.localdate() - timedelta(days=days_ago))
        sub.refresh_from_db()

    def test_running_trial_shows_banner_state_and_is_not_locked(self):
        _, signup = self._start_trial()
        state = branch_billing_state(signup.created_pharmacy)
        self.assertTrue(state['trial'])
        self.assertTrue(state['alert'])
        self.assertFalse(state['locked'])

    def test_ended_trial_locks_at_once_without_grace_or_read_only(self):
        _, signup = self._start_trial()
        sub = Subscription.objects.get(owner=signup.created_owner)
        self._expire(sub, days_ago=1)   # a paid plan would still be in GRACE here
        state = branch_billing_state(signup.created_pharmacy)
        self.assertTrue(state['locked'])
        self.assertFalse(state['grace'])
        self.assertFalse(state['read_only'])

    def test_ended_trial_owner_is_redirected_to_upgrade_page(self):
        _, signup = self._start_trial()
        sub = Subscription.objects.get(owner=signup.created_owner)
        self._expire(sub)
        owner = signup.created_owner
        owner.must_change_password = False
        owner.save()
        self.client.force_login(owner)
        resp = self.client.get(reverse('dashboard:home'))
        self.assertRedirects(resp, reverse('accounts:subscription_expired'), fetch_redirect_response=False)
        page = self.client.get(reverse('accounts:subscription_expired'))
        self.assertContains(page, 'Free trial ended')
        self.assertContains(page, reverse('hq:packages_center'))

    def test_upgrade_converts_trial_to_paid_and_starts_period_today(self):
        _, signup = self._start_trial()
        sub = Subscription.objects.get(owner=signup.created_owner)
        renew_subscription_from_payment(sub, plan='MONTHLY', tier='PROFESSIONAL', reference_note='TEST', amount_paid=Decimal('50000'))
        sub.refresh_from_db()
        self.assertFalse(sub.is_trial)
        self.assertEqual(sub.tier, 'PROFESSIONAL')
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        # Paid period starts today: a full paid month, never stacked on the Free Plan's far-future date.
        self.assertEqual((sub.end_date - timezone.localdate()).days, 30)
        self.assertFalse(branch_billing_state(signup.created_pharmacy)['trial'])

    def test_upgrade_after_expiry_unlocks_access(self):
        _, signup = self._start_trial()
        sub = Subscription.objects.get(owner=signup.created_owner)
        self._expire(sub, days_ago=3)
        self.assertTrue(branch_billing_state(signup.created_pharmacy)['locked'])
        renew_subscription_from_payment(sub, plan='MONTHLY', tier='BUSINESS', amount_paid=Decimal('30000'))
        self.assertFalse(branch_billing_state(signup.created_pharmacy)['locked'])


class FreePlanLimitTests(TestCase):
    def setUp(self):
        PaymentSettings.load()
        self.client.post(reverse('hq:signup'), {
            'business_type': 'PHARMACY', 'business_name': 'Afya Pharmacy', 'pharmacy_council_fin': '0101726',
            'owner_first_name': 'Asha', 'owner_last_name': 'Mwita', 'phone_number': '0755111222', 'account_type': 'TRIAL',
        })
        self.signup = SignupRequest.objects.get(business_name='Afya Pharmacy')
        self.pharmacy = self.signup.created_pharmacy
        self.sub = Subscription.objects.get(owner=self.signup.created_owner)
        # Simulate a legacy lifetime Free Plan account (new sign-ups get the uncapped 30-day trial instead).
        Subscription.objects.filter(pk=self.sub.pk).update(end_date=NO_EXPIRY_DATE)
        self.sub.refresh_from_db()

    def _real_product(self):
        return Product.objects.create(pharmacy=self.pharmacy, name='My Own Item')

    def _make_sales(self, n, product):
        from sales.models import SaleItem
        for _ in range(n):
            sale = Sale.objects.create(pharmacy=self.pharmacy, served_by=self.signup.created_owner,
                                       payment_method=Sale.PaymentMethod.CASH, business_date=timezone.localdate())
            SaleItem.objects.create(sale=sale, product=product, quantity=1, unit_price=1000, unit_cost=500, original_unit_price=1000)

    def test_registration_loads_ten_sample_products_for_the_business_type(self):
        demo = Product.objects.filter(pharmacy=self.pharmacy, is_demo=True)
        self.assertEqual(demo.count(), 10)
        self.assertTrue(all(p.name.endswith('(Sample)') for p in demo))
        self.assertTrue(all(p.sellable_quantity > 0 for p in demo))
        self.assertTrue(demo.filter(name__startswith='Paracetamol').exists())   # pharmacy catalogue

    def test_each_business_type_has_its_own_ten_samples(self):
        from inventory.demo_data import catalog_for
        names = set()
        for code, _label in SignupRequest.BusinessType.choices:
            rows = catalog_for(code)
            self.assertEqual(len(rows), 10, code)
            names.add(rows[0][0])
        self.assertGreater(len(names), 5)

    def test_sample_sales_never_use_the_free_allowance(self):
        demo = Product.objects.filter(pharmacy=self.pharmacy, is_demo=True).first()
        self._make_sales(FREE_PLAN_MONTHLY_SALES + 5, demo)
        self.assertEqual(sales_this_month(self.sub), 0)
        self.assertIsNone(sales_block_message(self.pharmacy))

    def test_new_sales_pause_after_the_monthly_limit_with_upgrade_message(self):
        self._make_sales(FREE_PLAN_MONTHLY_SALES, self._real_product())
        message = sales_block_message(self.pharmacy)
        self.assertIsNotNone(message)
        self.assertIn('Starter', message)
        self.assertIn('15,000', message)

    def test_new_30_day_trial_has_no_sales_cap(self):
        Subscription.objects.filter(pk=self.sub.pk).update(end_date=timezone.localdate() + timedelta(days=TRIAL_DAYS))
        self._make_sales(FREE_PLAN_MONTHLY_SALES + 5, self._real_product())
        self.assertIsNone(sales_block_message(self.pharmacy))

    def test_below_the_limit_sales_are_allowed(self):
        self._make_sales(FREE_PLAN_MONTHLY_SALES - 1, self._real_product())
        self.assertIsNone(sales_block_message(self.pharmacy))

    def test_paid_accounts_have_no_monthly_sales_limit(self):
        self._make_sales(FREE_PLAN_MONTHLY_SALES, self._real_product())
        renew_subscription_from_payment(self.sub, plan='MONTHLY', tier='STARTER', amount_paid=Decimal('15000'))
        self.assertIsNone(sales_block_message(self.pharmacy))

    def test_removing_sample_data_deletes_unused_and_hides_used_samples(self):
        from inventory.demo_data import remove_demo_products
        used = Product.objects.filter(pharmacy=self.pharmacy, is_demo=True).first()
        self._make_sales(1, used)
        deleted, archived = remove_demo_products(self.pharmacy)
        self.assertEqual((deleted, archived), (9, 1))
        used.refresh_from_db()
        self.assertFalse(used.is_active)
        self.assertEqual(Product.objects.filter(pharmacy=self.pharmacy, is_demo=True, is_active=True).count(), 0)
        self.assertTrue(Product.objects.filter(pk=used.pk).exists())   # sales history stays intact

    def test_landing_page_shows_new_headline_free_plan_and_setup_offer(self):
        html = self.client.get('/').content.decode()
        self.assertIn('shops and pharmacies', html)
        self.assertNotIn('One system to run your', html)
        self.assertIn('id="free"', html)
        self.assertIn('id="setup"', html)
        self.assertIn('45,000', html)
        self.assertIn('Up to 300 products', html)      # setup offer has a clear limit
        self.assertIn('Custom quote', html)


class PayDirectlyTests(TestCase):
    """A customer may skip the free month entirely and pay straight away (no trial, no free SMS)."""

    def test_paid_signup_gets_no_trial_and_no_free_sms(self):
        PaymentSettings.load()
        self.client.post(reverse('hq:signup'), {
            'business_type': 'RETAIL', 'business_name': 'Pay Now Duka', 'owner_first_name': 'Juma',
            'owner_last_name': 'Ali', 'phone_number': '0713000111', 'account_type': 'PAID', 'tier': 'STARTER', 'plan': 'MONTHLY',
        })
        signup = SignupRequest.objects.get(business_name='Pay Now Duka')
        self.assertFalse(signup.is_trial)
        self.assertEqual(signup.status, SignupRequest.Status.PENDING_PAYMENT)
        self.assertIsNone(signup.created_owner)
        self.assertEqual(SMSDebtSubscription.objects.count(), 0)


class TrialBannerTimingTests(TestCase):
    def setUp(self):
        PaymentSettings.load()
        self.client.post(reverse('hq:signup'), {
            'business_type': 'RETAIL', 'business_name': 'Banner Shop', 'owner_first_name': 'Neema',
            'owner_last_name': 'Juma', 'phone_number': '0712999888', 'account_type': 'TRIAL',
        })
        self.signup = SignupRequest.objects.get(business_name='Banner Shop')
        self.sub = Subscription.objects.get(owner=self.signup.created_owner)

    def _days_left(self, n):
        Subscription.objects.filter(pk=self.sub.pk).update(end_date=timezone.localdate() + timedelta(days=n))

    def test_banner_hidden_early_in_the_trial(self):
        self._days_left(20)
        self.assertFalse(branch_billing_state(self.signup.created_pharmacy)['alert'])

    def test_banner_appears_in_the_last_seven_days(self):
        for n in (7, 3, 1, 0):
            self._days_left(n)
            self.assertTrue(branch_billing_state(self.signup.created_pharmacy)['alert'], n)


class TrialReminderSmsTests(TestCase):
    """Kiswahili trial reminders: one SMS segment each, sent once per milestone."""

    def setUp(self):
        PaymentSettings.load()
        self.client.post(reverse('hq:signup'), {
            'business_type': 'RETAIL', 'business_name': 'Sms Shop', 'owner_first_name': 'Hamis',
            'owner_last_name': 'Ngollo', 'phone_number': '0713555444', 'account_type': 'TRIAL',
        })
        self.signup = SignupRequest.objects.get(business_name='Sms Shop')
        self.sub = Subscription.objects.get(owner=self.signup.created_owner)

    def _days_left(self, n):
        Subscription.objects.filter(pk=self.sub.pk).update(end_date=timezone.localdate() + timedelta(days=n))

    def test_every_message_is_one_gsm_segment(self):
        from pharmacies.billing_notices import trial_copy
        state = {'due_date': timezone.localdate()}
        for ms in ('trial-7', 'trial-3', 'trial-1', 'trial-0', 'trial-ended'):
            sms = trial_copy(state, ms, 'Abdulrahmanimwenyekiti')[3]
            self.assertTrue(sms.isascii(), ms)
            self.assertLessEqual(len(sms), 160, ms)

    def test_milestones_follow_the_countdown(self):
        from pharmacies.billing_notices import trial_milestone
        expected = {20: '', 8: '', 7: 'trial-7', 5: 'trial-7', 3: 'trial-3', 1: 'trial-1', 0: 'trial-0'}
        for days, ms in expected.items():
            self._days_left(days)
            self.assertEqual(trial_milestone(branch_billing_state(self.signup.created_pharmacy)), ms, days)
        self._days_left(-1)
        self.assertEqual(trial_milestone(branch_billing_state(self.signup.created_pharmacy)), 'trial-ended')

    def test_each_reminder_is_sent_only_once(self):
        from pharmacies.billing_notices import process_branch_billing
        self._days_left(3)
        with mock.patch('pharmacies.billing_notices._sms') as sms:
            process_branch_billing()
            process_branch_billing()
        self.assertEqual(sms.call_count, 1)
        self.assertIn('siku 3', sms.call_args[0][1])
