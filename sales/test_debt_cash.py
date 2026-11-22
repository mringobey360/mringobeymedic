"""Run: python manage.py test sales.test_debt_cash"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from expenses.ledger import CashLedgerService
from expenses.models import CashDay
from pharmacies.models import Pharmacy

from .debt_payments import DebtPaymentError, record_debt_payment
from .models import Debt, DebtPayment, Sale


class DebtCashTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='mgr', password='x')
        self.pharmacy = Pharmacy.objects.create(name='Test Branch')
        self.day = timezone.localdate()
        self.cash_day = CashDay.objects.create(pharmacy=self.pharmacy, date=self.day,
                                               opening_cash=Decimal('10000'), opened_by=self.user)
        sale = Sale.objects.create(pharmacy=self.pharmacy, served_by=self.user,
                                   payment_method=Sale.PaymentMethod.DEBT, business_date=self.day)
        self.debt = Debt.objects.create(pharmacy=self.pharmacy, sale=sale, customer_name='Asha',
                                        amount_owed=Decimal('5000'))

    def test_cash_repayment_enters_drawer_mobile_does_not(self):
        record_debt_payment(self.debt.pk, 1000, 'CASH', self.user)
        record_debt_payment(self.debt.pk, 500, 'MOBILE_MONEY', self.user)
        self.assertEqual(CashLedgerService.expected_cash(self.cash_day), Decimal('11000'))

    def test_legacy_repayments_are_not_added_to_history(self):
        DebtPayment.objects.create(debt=self.debt, pharmacy=self.pharmacy, amount=Decimal('700'),
                                   payment_method=DebtPayment.Method.LEGACY)
        self.assertEqual(CashLedgerService.expected_cash(self.cash_day), Decimal('10000'))

    def test_payment_is_capped_and_settles(self):
        debt, applied, capped = record_debt_payment(self.debt.pk, 9000, 'CASH', self.user)
        self.assertEqual((applied, capped, debt.is_settled), (Decimal('5000'), True, True))
        with self.assertRaises(DebtPaymentError):
            record_debt_payment(self.debt.pk, 100, 'CASH', self.user)

    def test_daily_totals_match_per_day_totals(self):
        record_debt_payment(self.debt.pk, 1200, 'CASH', self.user)
        daily = CashLedgerService.daily_totals(self.pharmacy.pk, self.day, self.day)[self.day]
        self.assertEqual(daily.cash_debt_collected, CashLedgerService.totals(self.cash_day).cash_debt_collected)

    def test_payment_kind_defaults_to_repayment_and_deposit_is_kept(self):
        record_debt_payment(self.debt.pk, 500, 'CASH', self.user)
        record_debt_payment(self.debt.pk, 700, 'CASH', self.user, kind=DebtPayment.Kind.DEPOSIT)
        kinds = sorted(self.debt.payments.values_list('kind', flat=True))
        self.assertEqual(kinds, [DebtPayment.Kind.DEPOSIT, DebtPayment.Kind.REPAYMENT])
