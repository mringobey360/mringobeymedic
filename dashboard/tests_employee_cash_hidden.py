"""Employees see only their own hand-over statement, never the drawer-level expected cash or the history page."""
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase

from . import views


class EmployeeCannotSeeExpectedCashTests(SimpleTestCase):
    def test_cash_history_page_is_refused_for_employees(self):
        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=True, is_employee_role=lambda: True), GET={}, method='GET')
        with self.assertRaises(PermissionDenied):
            views.my_cash_history_view(request)

    def test_employee_dashboard_shows_only_own_handover_statement_not_drawer_expected_cash(self):
        html = (Path(settings.BASE_DIR) / 'templates' / 'dashboard' / 'employee_dashboard.html').read_text(encoding='utf-8')
        self.assertIn('my_cash_ref', html)
        # blind count: the statement is only rendered after a count exists, and the form comes first
        self.assertIn('{% if not my_count %}', html)
        self.assertIn('shift_count_submit', html)
        self.assertNotIn('my_cash_history', html)
        self.assertNotIn('expected cash', html.lower())
        stmt = (Path(settings.BASE_DIR) / 'templates' / 'dashboard' / '_cash_statement.html').read_text(encoding='utf-8')
        self.assertNotIn('expected cash', stmt.lower())

    def test_expense_form_shown_to_employees_never_mentions_expected_cash(self):
        html = (Path(settings.BASE_DIR) / 'expenses' / 'templates' / 'expenses' / 'form.html').read_text(encoding='utf-8')
        self.assertNotIn('expected cash', html.lower())


class ShiftCountRulesTests(SimpleTestCase):
    def test_amount_parsing(self):
        from decimal import Decimal
        from django.core.exceptions import ValidationError
        from expenses.shift_count import parse_amount
        self.assertEqual(parse_amount('1,250,000'), Decimal('1250000.00'))
        self.assertEqual(parse_amount(' 0 '), Decimal('0.00'))
        for bad in ('', 'abc', '-5', 'NaN', 'Infinity', '1e30'):
            with self.assertRaises(ValidationError):
                parse_amount(bad)

    def test_boss_decision_endpoint_is_management_only(self):
        from expenses import views as ev
        self.assertEqual(getattr(ev.shift_count_decide, 'access_policy', ''), 'management')
