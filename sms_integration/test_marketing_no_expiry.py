"""SMS package credits never expire. Run: python manage.py test sms_integration.test_marketing_no_expiry"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .models import (
    NO_EXPIRY_DATE, SMSDebtPackage, SMSDebtSubscription, SMSMarketingPackage, SMSMarketingSubscription as Sub,
    SMSReceiptPackage, SMSReceiptSubscription,
)
from .utils import (
    _reserve_debt_sms_credit, _reserve_marketing_sms_credits, _reserve_receipt_sms_credit,
    has_active_debt_sms, has_active_marketing_sms, has_active_receipt_sms,
)


class SmsNoExpiryTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='own', password='x')
        self.old = timezone.localdate() - timedelta(days=90)
        mk = SMSMarketingPackage.objects.create(code='M1', name='M1', price=1000, sms_credits=100)
        self.sub = Sub.objects.create(owner=self.owner, package=mk, sms_credits=100, sms_used=40,
                                      start_date=self.old - timedelta(days=30), end_date=self.old,
                                      status=Sub.Status.ACTIVE)

    def _make(self, pkg_model, sub_model):
        pkg = pkg_model.objects.create(code='P1', name='P1', price=1000, sms_credits=10)
        return sub_model.objects.create(owner=self.owner, package=pkg, sms_credits=10, sms_used=0,
                                        start_date=self.old - timedelta(days=30), end_date=self.old,
                                        status=sub_model.Status.ACTIVE)

    def test_marketing_old_end_date_does_not_block_sending(self):
        self.assertTrue(self.sub.is_current)
        self.assertTrue(has_active_marketing_sms(self.owner))
        self.assertTrue(_reserve_marketing_sms_credits(self.owner, 10))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.sms_used, 50)

    def test_marketing_cannot_overspend(self):
        self.assertFalse(_reserve_marketing_sms_credits(self.owner, 61))

    def test_debt_and_receipt_old_end_date_still_usable(self):
        debt = self._make(SMSDebtPackage, SMSDebtSubscription)
        receipt = self._make(SMSReceiptPackage, SMSReceiptSubscription)
        self.assertTrue(debt.is_current and receipt.is_current)
        self.assertTrue(has_active_debt_sms(self.owner) and has_active_receipt_sms(self.owner))
        self.assertTrue(_reserve_debt_sms_credit(self.owner))
        self.assertTrue(_reserve_receipt_sms_credit(self.owner))

    def test_exhausted_credits_are_not_current(self):
        debt = self._make(SMSDebtPackage, SMSDebtSubscription)
        SMSDebtSubscription.objects.filter(pk=debt.pk).update(sms_used=10)
        debt.refresh_from_db()
        self.assertFalse(debt.is_current)
        self.assertFalse(_reserve_debt_sms_credit(self.owner))

    def test_command_restores_expired_credits(self):
        Sub.objects.filter(pk=self.sub.pk).update(status=Sub.Status.EXPIRED)
        call_command('restore_sms_credits', '--apply')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Sub.Status.ACTIVE)
        self.assertEqual(self.sub.end_date, NO_EXPIRY_DATE)
        self.assertEqual(self.sub.credits_remaining, 60)
