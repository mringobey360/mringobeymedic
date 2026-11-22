"""v97.18 - a closed workday locks selling until the Boss approves the reopening."""
import datetime as dt
import inspect
import json
from types import SimpleNamespace as NS
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from django.utils import timezone

from accounts import meal_services, workday_guard


def _employee(role='EMPLOYEE'):
    return NS(is_authenticated=True, pharmacy_id=5, is_employee_role=lambda: role == 'EMPLOYEE')


class GuardTests(SimpleTestCase):
    def _patch(self, workday=None, attendance=None):
        wd_qs = mock.Mock(); wd_qs.filter.return_value.first.return_value = workday
        at_qs = mock.Mock(); at_qs.filter.return_value.first.return_value = attendance
        return (mock.patch('accounts.models.MealWorkday.objects', wd_qs),
                mock.patch('accounts.models.AttendanceRecord.objects', at_qs))

    def test_closed_workday_blocks_employee(self):
        wd, at = self._patch(workday=NS(closed_at=timezone.now()))
        with wd, at:
            self.assertEqual(workday_guard.workday_sales_block_message(_employee()), workday_guard.CLOSED_MESSAGE)

    def test_checked_out_attendance_blocks_even_without_workday_row(self):
        wd, at = self._patch(workday=None, attendance=NS(check_out_at=timezone.now()))
        with wd, at:
            self.assertTrue(workday_guard.workday_sales_block_message(_employee()))

    def test_open_workday_is_not_blocked(self):
        wd, at = self._patch(workday=NS(closed_at=None), attendance=None)
        with wd, at:
            self.assertEqual(workday_guard.workday_sales_block_message(_employee()), '')

    def test_managers_and_anonymous_are_never_blocked(self):
        self.assertEqual(workday_guard.workday_sales_block_message(_employee('ADMIN')), '')
        self.assertEqual(workday_guard.workday_sales_block_message(NS(is_authenticated=False)), '')

    def test_employee_without_branch_is_not_blocked(self):
        user = NS(is_authenticated=True, pharmacy_id=None, is_employee_role=lambda: True)
        self.assertEqual(workday_guard.workday_sales_block_message(user), '')

    def test_closed_moment_prefers_closed_at(self):
        moment = timezone.now()
        self.assertEqual(workday_guard.closed_moment(NS(closed_at=moment)), moment)
        self.assertEqual(workday_guard.closed_moment(NS(check_out_at=moment)), moment)
        self.assertIsNone(workday_guard.closed_moment(None))


class CheckoutLockTests(SimpleTestCase):
    def test_checkout_returns_423_when_workday_closed(self):
        from sales import views
        raw = inspect.unwrap(views.checkout_view)
        request = NS(pharmacy=object(), user=_employee(), body=b'{"cart": []}')
        with mock.patch('sales.views.workday_sales_block_message', return_value='locked'):
            response = raw(request)
        self.assertEqual(response.status_code, 423)
        payload = json.loads(response.content)
        self.assertFalse(payload['success'])
        self.assertTrue(payload['workday_closed'])


class OfflineSyncLockTests(SimpleTestCase):
    def test_sale_queued_before_close_is_allowed(self):
        from sales.views import _offline_sale_made_after_close
        closed = dt.datetime(2026, 11, 15, 18, 0, tzinfo=dt.timezone.utc)
        self.assertFalse(_offline_sale_made_after_close({'queued_at': '2026-11-15T14:00:00.000Z'}, closed))

    def test_sale_queued_after_close_is_blocked(self):
        from sales.views import _offline_sale_made_after_close
        closed = dt.datetime(2026, 11, 15, 18, 0, tzinfo=dt.timezone.utc)
        self.assertTrue(_offline_sale_made_after_close({'queued_at': '2026-11-15T18:30:00.000Z'}, closed))

    def test_missing_or_garbage_timestamp_fails_closed(self):
        from sales.views import _offline_sale_made_after_close
        closed = dt.datetime(2026, 11, 15, 18, 0, tzinfo=dt.timezone.utc)
        self.assertTrue(_offline_sale_made_after_close({}, closed))
        self.assertTrue(_offline_sale_made_after_close({'queued_at': 'not-a-date'}, closed))


class MealTimingStringDefaultTests(SimpleTestCase):
    """Regression: TimeField(default='10:00') produced a str on the request that created the row."""

    def test_as_time_accepts_strings_and_times(self):
        self.assertEqual(meal_services._as_time('10:00'), dt.time(10, 0))
        self.assertEqual(meal_services._as_time('07:30:15'), dt.time(7, 30, 15))
        self.assertEqual(meal_services._as_time(dt.time(21, 0)), dt.time(21, 0))
        with self.assertRaises(ValueError):
            meal_services._as_time('late')

    def test_aware_on_no_longer_crashes_on_string(self):
        with timezone.override(ZoneInfo('Africa/Dar_es_Salaam')):
            value = meal_services._aware_on(dt.date(2026, 11, 15), '10:00')
        self.assertEqual((value.hour, value.minute), (10, 0))

    def test_model_defaults_are_real_times(self):
        from accounts.models import MealTimingSettings
        for name in meal_services._TIMING_FIELDS:
            self.assertIsInstance(MealTimingSettings._meta.get_field(name).get_default(), dt.time)
