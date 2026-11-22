"""V50.14 - a claimed meal past its cut-off must settle without waiting for Close Workday.

Mock-based on purpose: no database or fixtures needed.
Run: python manage.py test accounts.tests.test_meal_claim_settlement
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from accounts import meal_services as ms
from accounts.models import MealLedger


def _ledger(cutoff_at, claimed_at):
    led = SimpleNamespace(
        status=MealLedger.Status.PENDING, claimed_at=claimed_at, cutoff_at=cutoff_at,
        scan_required=True, meal_type='BREAKFAST', reason='', earned_at=None, debt_at=None,
        missed_at=None, amount=1000,
    )
    led.save = mock.Mock()
    return led


class ClaimedMealSettlementTests(SimpleTestCase):
    def _run(self, ledger, *, has_sales, closed_at, now):
        workday = SimpleNamespace(employee=SimpleNamespace(pharmacy=object()), date=now.date(),
                                  closed_at=closed_at, opened_at=now - timedelta(hours=9))
        empty_qs = mock.MagicMock()
        empty_qs.first.return_value = None
        empty_qs.values_list.return_value = []
        with mock.patch.object(ms, 'ensure_ledgers_for_workday', return_value=[ledger]), \
             mock.patch.object(ms, 'sales_exist', return_value=has_sales), \
             mock.patch.object(ms.AttendanceRecord.objects, 'filter', return_value=empty_qs), \
             mock.patch.object(ms.MealIssueReport.objects, 'filter', return_value=empty_qs), \
             mock.patch.object(ms.MealLedger.objects, 'filter', return_value=[ledger]), \
             mock.patch.object(ms, 'reconcile_meal_funding_for_date'), \
             mock.patch.object(ms, '_notify_debt') as notify:
            ms.finalize_workday_meals(workday, now=now)
        return notify

    def test_claimed_no_sales_open_workday_after_cutoff_becomes_debt(self):
        now = timezone.now()
        led = _ledger(now - timedelta(hours=2), now - timedelta(hours=9))
        notify = self._run(led, has_sales=False, closed_at=None, now=now)
        self.assertEqual(led.status, MealLedger.Status.DEBT)
        notify.assert_called_once()

    def test_claimed_with_sales_becomes_earned(self):
        now = timezone.now()
        led = _ledger(now - timedelta(hours=2), now - timedelta(hours=9))
        self._run(led, has_sales=True, closed_at=None, now=now)
        self.assertEqual(led.status, MealLedger.Status.EARNED)

    def test_before_cutoff_stays_pending(self):
        now = timezone.now()
        led = _ledger(now + timedelta(hours=1), now - timedelta(hours=1))
        self._run(led, has_sales=False, closed_at=None, now=now)
        self.assertEqual(led.status, MealLedger.Status.PENDING)

    def test_unclaimed_past_cutoff_is_missed_not_debt(self):
        now = timezone.now()
        led = _ledger(now - timedelta(hours=2), None)
        self._run(led, has_sales=False, closed_at=None, now=now)
        self.assertEqual(led.status, MealLedger.Status.MISSED)
