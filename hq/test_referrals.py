from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from hq import referrals
from hq.forms import SignupForm
from hq.models import PaymentSettings, Referral, SignupRequest, Subscription
from hq.services import renew_subscription_from_payment


class ReferralTests(TestCase):
    def setUp(self):
        self.ps = PaymentSettings.load()
        self.referrer = User.objects.create_user(
            username='mama', password='x', role=User.Role.ADMIN, phone_number='+255712000001')
        self.sub = Subscription.objects.create(
            owner=self.referrer, plan=Subscription.Plan.MONTHLY, tier=SignupRequest.Tier.STARTER,
            end_date=timezone.localdate() + timedelta(days=3), amount_paid=Decimal('15000'))
        self.code = referrals.get_or_create_code(self.referrer)
        self._n = 0

    def invite(self, phone=None):
        self._n += 1
        signup = SignupRequest.objects.create(
            business_name=f'Shop {self._n}', owner_first_name='A', owner_last_name='B',
            phone_number=phone or f'+25571300000{self._n}', plan='MONTHLY', tier='STARTER')
        return signup, referrals.register_referral(signup, self.code)

    def test_code_is_stable(self):
        self.assertEqual(referrals.get_or_create_code(self.referrer).pk, self.code.pk)

    def test_cannot_invite_yourself(self):
        _, ref = self.invite(phone='+255712000001')
        self.assertIsNone(ref)

    def test_reward_only_after_payment_and_only_once(self):
        signup, ref = self.invite()
        self.assertEqual(referrals.available_percent(self.referrer), 0)  # registered, not paid
        self.assertIsNone(referrals.qualify(signup, amount_paid=0))      # free trial: nothing
        self.assertIsNotNone(referrals.qualify(signup, amount_paid=15000))
        self.assertIsNone(referrals.qualify(signup, amount_paid=15000))  # idempotent
        self.assertEqual(referrals.available_percent(self.referrer), self.ps.referral_percent_per_invite)

    def test_discount_is_capped(self):
        for _ in range(8):
            signup, _ref = self.invite()
            referrals.qualify(signup, amount_paid=15000)
        self.assertEqual(referrals.available_percent(self.referrer), self.ps.referral_max_percent)
        net, pct = referrals.discounted_price(self.referrer, Decimal('15000'))
        self.assertEqual((net, pct), (Decimal('12750'), 15))

    def test_manual_renewal_applies_and_consumes_reward(self):
        signup, _ = self.invite()
        referrals.qualify(signup, amount_paid=15000)
        renew_subscription_from_payment(self.sub)  # manual confirmation, price list + discount
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.amount_paid, Decimal('15000') + Decimal('14550'))
        self.assertEqual(Referral.objects.get(signup=signup).status, Referral.Status.USED)
        self.assertEqual(referrals.available_percent(self.referrer), 0)

    def test_online_renewal_consumes_only_the_discount_given(self):
        for _ in range(2):
            s, _r = self.invite()
            referrals.qualify(s, amount_paid=15000)
        renew_subscription_from_payment(self.sub, amount_paid=Decimal('14550'))  # charged with 3% only
        self.assertEqual(Referral.objects.filter(status=Referral.Status.USED).count(), 1)
        self.assertEqual(referrals.available_percent(self.referrer), 3)

    def test_full_price_payment_consumes_nothing(self):
        s, _r = self.invite()
        referrals.qualify(s, amount_paid=15000)
        renew_subscription_from_payment(self.sub, amount_paid=Decimal('15000'))
        self.assertEqual(referrals.available_percent(self.referrer), 3)

    def test_signup_form_rejects_unknown_code(self):
        form = SignupForm(data={'referral_code': 'NOPE1234'})
        form.is_valid()
        self.assertIn('referral_code', form.errors)
