"""Run: python manage.py test sales.test_credit_split

A customer buys goods worth 30,000 on credit and pays 10,000 at the till: the system must show
10,000 received + 20,000 lent (not 30,000 on credit), keep every later payment traceable, and keep the
drawer / mobile money totals exactly as before."""
import importlib
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from dashboard import reconciliation
from dashboard.views import _profit_summary
from expenses.ledger import CashLedgerService
from expenses.models import CashDay
from inventory.models import Product
from pharmacies.models import Pharmacy

from .debt_payments import record_debt_payment
from .models import Debt, DebtPayment, Sale, SaleItem


class CreditSplitTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='mgr', password='x')
        self.pharmacy = Pharmacy.objects.create(name='Test Branch')
        self.day = timezone.localdate()
        self.cash_day = CashDay.objects.create(pharmacy=self.pharmacy, date=self.day,
                                               opening_cash=D('1000'), opened_by=self.user)
        self.sale = Sale.objects.create(pharmacy=self.pharmacy, served_by=self.user, customer_name='Asha',
                                        payment_method=Sale.PaymentMethod.DEBT, business_date=self.day)
        product = Product.objects.create(pharmacy=self.pharmacy, name='Amoxicillin')
        SaleItem.objects.create(sale=self.sale, product=product, quantity=3, unit_price=D('10000'),
                                unit_cost=D('6000'))                                   # 30,000
        self.debt = Debt.objects.create(pharmacy=self.pharmacy, sale=self.sale, customer_name='Asha',
                                        amount_owed=D('30000'))

    def _deposit(self, amount='10000', method='CASH'):
        return record_debt_payment(self.debt.pk, D(amount), method, self.user, kind=DebtPayment.Kind.DEPOSIT)

    def test_debt_splits_into_paid_now_and_lent(self):
        self._deposit()
        debt = Debt.objects.get(pk=self.debt.pk)
        self.assertEqual((debt.deposit_paid, debt.credit_given, debt.balance, debt.repaid),
                         (D('10000'), D('20000'), D('20000'), D('0')))
        sale = Sale.objects.get(pk=self.sale.pk)
        self.assertEqual((sale.paid_at_sale, sale.credit_amount), (D('10000'), D('20000')))
        self.assertIn('paid 10,000 now', sale.payment_summary)

    def test_later_repayment_is_not_a_deposit(self):
        self._deposit()
        record_debt_payment(self.debt.pk, D('5000'), 'MOBILE_MONEY', self.user)
        debt = Debt.objects.get(pk=self.debt.pk)
        self.assertEqual((debt.deposit_paid, debt.repaid, debt.balance), (D('10000'), D('5000'), D('15000')))
        self.assertEqual(debt.payments.get(kind=DebtPayment.Kind.REPAYMENT).amount, D('5000'))

    def test_dashboard_shows_30000_sold_10000_received_20000_lent(self):
        self._deposit()
        fin = _profit_summary(self.pharmacy, self.day, self.day)
        self.assertEqual(fin['sales'], D('30000'))                      # revenue is still the full sale
        self.assertEqual(fin['credit_sales'], D('20000'))               # only this part is credit
        self.assertEqual(fin['credit_deposits'], D('10000'))            # this part was paid at the till
        self.assertEqual(fin['debt_collected'], D('0'))                 # a deposit is not a repayment
        self.assertEqual(fin['deposits_received'], D('10000'))
        self.assertEqual(fin['cash_received'], D('10000'))
        self.assertEqual(fin['cash_sales'] + fin['mobile_sales'] + fin['credit_sales'] + fin['credit_deposits'],
                         fin['sales'])

    def test_repayment_adds_to_receipts_without_changing_credit_given(self):
        self._deposit()
        record_debt_payment(self.debt.pk, D('5000'), 'MOBILE_MONEY', self.user)
        fin = _profit_summary(self.pharmacy, self.day, self.day)
        self.assertEqual((fin['credit_sales'], fin['debt_collected'], fin['cash_received']),
                         (D('20000'), D('5000'), D('15000')))

    def test_drawer_still_counts_only_cash_deposit(self):
        self._deposit('10000', 'CASH')
        record_debt_payment(self.debt.pk, D('4000'), 'MOBILE_MONEY', self.user)
        self.assertEqual(CashLedgerService.expected_cash(self.cash_day), D('11000'))   # 1,000 + 10,000 cash only

    def test_deposit_never_exceeds_sale_value(self):
        _, applied, capped = self._deposit('99999')
        self.assertEqual((applied, capped), (D('30000'), True))
        fin = _profit_summary(self.pharmacy, self.day, self.day)
        self.assertEqual((fin['credit_sales'], fin['credit_deposits']), (D('0'), D('30000')))

    def test_daily_report_splits_credit(self):
        from daily_reports.services import _branch_report
        self._deposit()
        r = _branch_report(self.pharmacy, self.day)
        self.assertEqual((D(r['net_sales']), D(r['credit_sales']), D(r['credit_deposits'])),
                         (D('30000'), D('20000'), D('10000')))
        self.assertEqual((D(r['debt_collected']), D(r['deposits_received'])), (D('0'), D('10000')))
        self.assertEqual(D(r['payment_methods']['DEBT']), D('20000'))
        self.assertEqual(D(r['payment_methods']['CREDIT_PAID_AT_SALE']), D('10000'))

    def test_untraced_paid_amount_is_flagged(self):
        Debt.objects.filter(pk=self.debt.pk).update(amount_paid=D('7000'))        # typed in by hand, no payment row
        issues = reconciliation.run_integrity_checks(self.pharmacy, self.day, self.day)
        self.assertTrue(any(i.code == 'DEBT_UNTRACED' for i in issues))
        Debt.objects.filter(pk=self.debt.pk).update(amount_paid=D('0'))
        self._deposit('7000')
        issues = reconciliation.run_integrity_checks(self.pharmacy, self.day, self.day)
        self.assertFalse(any(i.code == 'DEBT_UNTRACED' for i in issues))

    def test_migration_tags_old_pos_deposits_only(self):
        mig = importlib.import_module('sales.migrations.0014_debtpayment_kind_note')
        from django.apps import apps
        at_sale = DebtPayment.objects.create(debt=self.debt, pharmacy=self.pharmacy, amount=D('1000'),
                                             paid_at=self.sale.created_at)
        later = DebtPayment.objects.create(debt=self.debt, pharmacy=self.pharmacy, amount=D('500'),
                                           paid_at=self.sale.created_at + timedelta(days=1))
        legacy = DebtPayment.objects.create(debt=self.debt, pharmacy=self.pharmacy, amount=D('200'),
                                            paid_at=self.sale.created_at, payment_method=DebtPayment.Method.LEGACY)
        mig.mark_existing_deposits(apps, None)
        for row in (at_sale, later, legacy):
            row.refresh_from_db()
        self.assertEqual((at_sale.kind, later.kind, legacy.kind),
                         (DebtPayment.Kind.DEPOSIT, DebtPayment.Kind.REPAYMENT, DebtPayment.Kind.REPAYMENT))
