"""Month / custom-range report tests. Run: python manage.py test dashboard.test_range_report"""
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from expenses.models import CashDay, Expense, ExpenseCategory
from pharmacies.models import Pharmacy

from . import range_report
from .views import _profit_summary


class RangeReportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='mgr', password='x')
        self.pharmacy = Pharmacy.objects.create(name='Test Branch')
        self.day = timezone.localdate()
        CashDay.objects.create(pharmacy=self.pharmacy, date=self.day, opening_cash=Decimal('10000'),
                               opened_by=self.user)
        cat = ExpenseCategory.objects.create(pharmacy=self.pharmacy, name='Transport')
        for amount, source in ((Decimal('2000'), Expense.PaymentSource.CASH_SALES),
                               (Decimal('3000'), Expense.PaymentSource.BANK)):
            Expense.objects.create(
                pharmacy=self.pharmacy, expense_date=self.day, category=cat, title='Test', amount=amount,
                payment_source=source, submitted_by=self.user, status=Expense.Status.APPROVED,
                decided_by=self.user, decided_at=timezone.now())

    def test_only_drawer_expenses_reduce_expected_cash(self):
        cash = range_report.period_cash_control(self.pharmacy, self.day, self.day)
        self.assertEqual(cash['opening'], Decimal('10000'))
        self.assertEqual(cash['expenses'], Decimal('2000'))
        self.assertEqual(cash['expected'], Decimal('8000'))

    def test_report_data_lists_every_expense_and_matches_screen_cash(self):
        fin = _profit_summary(self.pharmacy, self.day, self.day)
        data = range_report.collect_report_data(self.pharmacy, self.day, self.day, fin)
        self.assertEqual(data['expense_total'], Decimal('5000'))
        self.assertEqual(data['expense_drawer'], Decimal('2000'))
        self.assertEqual(len(data['expenses']), 2)
        self.assertEqual(data['cash_rows'][-1]['expected'], data['cash']['expected'])

    def test_pdf_and_excel_exports_build(self):
        fin = _profit_summary(self.pharmacy, self.day, self.day)
        pdf = range_report.build_range_report_pdf(self.pharmacy, self.day, self.day, fin)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        xlsx = range_report.build_range_report_xlsx(self.pharmacy, self.day, self.day, fin)
        from openpyxl import load_workbook
        wb = load_workbook(BytesIO(xlsx.content))
        self.assertEqual(wb.sheetnames[:3], ['Summary', 'Daily', 'Expenses'])
