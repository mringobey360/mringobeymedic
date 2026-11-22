"""Run: python manage.py test dashboard.test_reconciliation"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from pharmacies.models import Pharmacy
from sales.models import Sale, SaleItem

from . import reconciliation
from .views import _profit_summary


class ReconciliationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='mgr', password='x')
        self.pharmacy = Pharmacy.objects.create(name='Test Branch')
        self.day = timezone.localdate()

    def _sale(self, status, qty, returned, price='1000', method=Sale.PaymentMethod.CASH):
        sale = Sale.objects.create(pharmacy=self.pharmacy, served_by=self.user, payment_method=method,
                                   status=status, business_date=self.day)
        SaleItem.objects.create(sale=sale, product=self._product(), quantity=qty, returned_quantity=returned,
                                unit_price=Decimal(price), unit_cost=Decimal('600'))
        return sale

    def _product(self):
        from inventory.models import Product
        return Product.objects.create(pharmacy=self.pharmacy, name=f'P{Product.objects.count()}')

    def test_bridge_balances_with_void_and_partial_return(self):
        self._sale(Sale.Status.COMPLETED, 3, 0)            # 3,000 net
        self._sale(Sale.Status.PARTIALLY_RETURNED, 1, 1)   # 1,000 net, 1,000 returned
        self._sale(Sale.Status.VOIDED, 0, 2)               # 2,000 voided
        r = reconciliation.sales_reconciliation(self.pharmacy, self.day, self.day)
        self.assertEqual((r['gross'], r['voided'], r['partial_returns'], r['net']),
                         (Decimal('7000'), Decimal('2000'), Decimal('1000'), Decimal('4000')))
        self.assertTrue(r['balanced'])
        self.assertEqual(r['net'], _profit_summary(self.pharmacy, self.day, self.day)['sales'])

    def test_status_drift_is_repaired(self):
        sale = self._sale(Sale.Status.COMPLETED, 1, 1)     # should be PARTIALLY_RETURNED
        issues = reconciliation.run_integrity_checks(self.pharmacy, self.day, self.day, fix=True)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.PARTIALLY_RETURNED)
        self.assertTrue(any(i.code == 'SALE_STATUS' and i.fixed for i in issues))


class SplitPaymentTests(ReconciliationTests):
    """Cash + Mobile Money sales: only the cash part enters the drawer; refunds go back in proportion."""

    def test_split_sale_only_cash_part_in_drawer_and_in_sales(self):
        from decimal import Decimal as D
        from expenses.models import CashDay
        from expenses.ledger import CashLedgerService
        CashDay.objects.create(pharmacy=self.pharmacy, date=self.day, opening_cash=D('1000'), opened_by=self.user)
        sale = self._sale(Sale.Status.COMPLETED, 10, 0, method=Sale.PaymentMethod.SPLIT)   # total 10,000
        Sale.objects.filter(pk=sale.pk).update(cash_paid=D('6000'), mobile_paid=D('4000'))
        cd = CashDay.objects.get(pharmacy=self.pharmacy, date=self.day)
        self.assertEqual(CashLedgerService.expected_cash(cd), D('7000'))        # 1,000 + 6,000 cash only
        fin = _profit_summary(self.pharmacy, self.day, self.day)
        self.assertEqual((fin['sales'], fin['cash_sales'], fin['mobile_sales']), (D('10000'), D('6000'), D('4000')))


    def test_mobile_money_received_counts_mobile_sales_and_split_mobile_part(self):
        from decimal import Decimal as D
        from .mobile_money import mobile_money_totals
        self._sale(Sale.Status.COMPLETED, 2, 0, method=Sale.PaymentMethod.MOBILE_MONEY)       # 2,000
        split = self._sale(Sale.Status.COMPLETED, 10, 0, method=Sale.PaymentMethod.SPLIT)     # 10,000
        Sale.objects.filter(pk=split.pk).update(cash_paid=D('6000'), mobile_paid=D('4000'))
        self.assertEqual(mobile_money_totals(self.pharmacy, self.day, self.day)['received'], D('6000'))

    # ---- V54.96 mobile money account -------------------------------------------------
    def _day_obj(self, opening='1000'):
        from decimal import Decimal as D
        from expenses.models import CashDay
        return CashDay.objects.create(pharmacy=self.pharmacy, date=self.day, opening_cash=D(opening), opened_by=self.user)

    def test_mobile_balance_never_touches_drawer_cash(self):
        from decimal import Decimal as D
        from expenses.ledger import CashLedgerService
        cd = self._day_obj()
        self._sale(Sale.Status.COMPLETED, 10, 0, method=Sale.PaymentMethod.MOBILE_MONEY)      # 10,000 mobile
        cash_before = CashLedgerService.expected_cash(cd)
        self.assertEqual(cd.mobile_opening_balance, D('0'))                                    # not tracking yet
        self.assertEqual(cd.expected_mobile_balance, D('10000'))
        self.assertIsNone(cd.mobile_money_difference)
        cd.actual_mobile_balance = D('9500')
        cd.save(update_fields=['actual_mobile_balance'])
        self.assertEqual(cd.mobile_money_difference, D('-500'))
        self.assertEqual(CashLedgerService.expected_cash(cd), cash_before)
        self.assertEqual(cd.expected_total_money, cash_before + D('10000'))

    def test_mobile_expense_reduces_mobile_not_drawer(self):
        from decimal import Decimal as D
        from expenses.ledger import CashLedgerService
        from expenses.models import Expense, ExpenseCategory
        cd = self._day_obj()
        self._sale(Sale.Status.COMPLETED, 10, 0, method=Sale.PaymentMethod.MOBILE_MONEY)
        cat = ExpenseCategory.objects.create(pharmacy=self.pharmacy, name='Bank / Mobile Money Charges')
        Expense.objects.create(pharmacy=self.pharmacy, expense_date=self.day, cash_date=self.day, category=cat, title='Network charge',
                               amount=D('300'), payment_source=Expense.PaymentSource.MOBILE_MONEY,
                               submitted_by=self.user, status=Expense.Status.APPROVED, decided_at=timezone.now())
        self.assertEqual(cd.expected_mobile_balance, D('9700'))
        self.assertEqual(CashLedgerService.expected_cash(cd), D('1000'))

    def test_transfer_moves_both_sides_and_total_money_is_unchanged(self):
        from decimal import Decimal as D
        from expenses.ledger import CashLedgerService
        from expenses.models import CashTransfer
        cd = self._day_obj(opening='5000')
        self._sale(Sale.Status.COMPLETED, 10, 0, method=Sale.PaymentMethod.MOBILE_MONEY)      # mobile 10,000
        total_before = cd.expected_total_money
        CashTransfer.objects.create(pharmacy=self.pharmacy, transfer_date=self.day, created_by=self.user,
                                    direction=CashTransfer.Direction.CASH_TO_MOBILE, amount=D('2000'))
        self.assertEqual(CashLedgerService.expected_cash(cd), D('3000'))
        self.assertEqual(cd.expected_mobile_balance, D('12000'))
        CashTransfer.objects.create(pharmacy=self.pharmacy, transfer_date=self.day, created_by=self.user,
                                    direction=CashTransfer.Direction.MOBILE_TO_CASH, amount=D('500'))
        self.assertEqual(CashLedgerService.expected_cash(cd), D('3500'))
        self.assertEqual(cd.expected_mobile_balance, D('11500'))
        self.assertEqual(cd.expected_total_money, total_before)

    def test_mobile_opening_is_carried_from_previous_statement_balance(self):
        from datetime import timedelta
        from decimal import Decimal as D
        from expenses.models import CashDay
        yesterday = self.day - timedelta(days=1)
        CashDay.objects.create(pharmacy=self.pharmacy, date=yesterday, opening_cash=D('0'), opened_by=self.user,
                               actual_mobile_balance=D('80000'))
        today = self._day_obj()
        self.assertEqual(today.mobile_opening_balance, D('80000'))
        self._sale(Sale.Status.COMPLETED, 5, 0, method=Sale.PaymentMethod.MOBILE_MONEY)       # +5,000
        self.assertEqual(today.expected_mobile_balance, D('85000'))
        today.opening_mobile = D('90000')                                                      # manual override wins
        self.assertEqual(today.mobile_opening_balance, D('90000'))

    def test_period_account_bridge(self):
        from datetime import timedelta
        from decimal import Decimal as D
        from expenses.models import CashDay
        from .mobile_money import mobile_period_account, mobile_statement_check
        CashDay.objects.create(pharmacy=self.pharmacy, date=self.day - timedelta(days=3), opening_cash=D('0'),
                               opened_by=self.user, opening_mobile=D('10000'))
        self._sale(Sale.Status.COMPLETED, 4, 0, method=Sale.PaymentMethod.MOBILE_MONEY)       # +4,000
        acct = mobile_period_account(self.pharmacy, self.day, self.day)
        self.assertEqual((acct['opening'], acct['received'], acct['expected_closing']), (D('10000'), D('4000'), D('14000')))
        cd = self._day_obj()
        cd.actual_mobile_balance = D('13500')
        cd.save(update_fields=['actual_mobile_balance'])
        chk = mobile_statement_check(self.pharmacy, self.day, self.day)
        self.assertEqual((chk['actual'], chk['expected'], chk['difference']), (D('13500'), D('14000'), D('-500')))
