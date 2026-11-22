"""Employees waiting for attendance re-verification must not be locked out of meals.

Mock-based (no database needed).
Run: python manage.py test accounts.tests.test_meal_rescan_review
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from accounts import attendance_services as att
from accounts import meal_services as ms
from accounts.models import AttendanceRecord

S = AttendanceRecord.Status


def _attendance(status, checked_in=True, checked_out=False):
    now = timezone.now()
    return SimpleNamespace(
        status=status, is_verified=status in (S.VERIFIED, S.CLOSED),
        check_in_at=now - timedelta(hours=6) if checked_in else None,
        check_out_at=now if checked_out else None,
        checkpoints=mock.MagicMock(), review_note='',
    )


class AtWorkHelpersTests(SimpleTestCase):
    def test_review_counts_as_at_work_but_needs_reverification(self):
        a = _attendance(S.REVIEW)
        self.assertTrue(ms.attendance_at_work(a))
        self.assertTrue(ms.attendance_needs_reverification(a))

    def test_verified_is_at_work_and_not_in_review(self):
        a = _attendance(S.VERIFIED)
        self.assertTrue(ms.attendance_at_work(a))
        self.assertFalse(ms.attendance_needs_reverification(a))

    def test_not_checked_in_rejected_or_checked_out_are_not_at_work(self):
        self.assertFalse(ms.attendance_at_work(None))
        self.assertFalse(ms.attendance_at_work(_attendance(S.REVIEW, checked_in=False)))
        self.assertFalse(ms.attendance_at_work(_attendance(S.REJECTED)))
        self.assertFalse(ms.attendance_at_work(_attendance(S.VERIFIED, checked_out=True)))


class RestoreAfterScanTests(SimpleTestCase):
    def test_gps_verified_scan_restores_review_to_verified(self):
        a = _attendance(S.REVIEW)
        a.save = mock.Mock()
        att._restore_verified_after_scan(a, timezone.now())
        self.assertEqual(a.status, S.VERIFIED)
        self.assertIsNone(a.outside_since)
        self.assertIn('Re-verified by QR scan', a.review_note)
        a.save.assert_called_once()


class ManagerGrantForReviewAttendanceTests(SimpleTestCase):
    """A manager may verify a meal for a checked-in employee whose attendance awaits re-verification."""

    def _grant(self, att_status, *, checked_in=True):
        from datetime import date
        from accounts.models import MealLedger
        now = timezone.now()
        employee = SimpleNamespace(pk=2, get_full_name=lambda: 'Theodora', username='theo')
        manager = SimpleNamespace(pk=1, get_full_name=lambda: 'Boss', username='boss')
        ledger = SimpleNamespace(
            pk=9, employee=employee, pharmacy=object(), date=timezone.localtime(now).date(),
            meal_type='LUNCH', claimed_at=None, status=MealLedger.Status.PENDING, reason='',
            get_meal_type_display=lambda: 'Lunch', save=mock.Mock(), missed_at=None,
        )
        att = SimpleNamespace(status=att_status, is_verified=att_status in (S.VERIFIED, S.CLOSED),
                              check_in_at=now if checked_in else None)
        qs = mock.MagicMock()
        qs.select_for_update.return_value.select_related.return_value.get.return_value = ledger
        with mock.patch.object(ms.MealLedger.objects, 'select_for_update', return_value=qs.select_for_update.return_value), \
             mock.patch.object(ms.AttendanceRecord.objects, 'filter', return_value=mock.Mock(first=lambda: att)), \
             mock.patch.object(ms.AttendanceCheckpoint.objects, 'get_or_create') as chk, \
             mock.patch.object(ms.MealIssueReport.objects, 'filter', return_value=mock.Mock(update=mock.Mock())), \
             mock.patch.object(ms.MealWorkday.objects, 'filter', return_value=mock.Mock(first=lambda: None)), \
             mock.patch.object(ms, 'notify'):
            result = ms.manager_grant_meal.__wrapped__(ledger, manager, 'OTHER', 'Worked all day', now=now) \
                if hasattr(ms.manager_grant_meal, '__wrapped__') else ms.manager_grant_meal(ledger, manager, 'OTHER', 'Worked all day', now=now)
        return result, chk

    def test_review_attendance_can_be_verified_and_is_tagged(self):
        ledger, chk = self._grant(S.REVIEW)
        self.assertIn('awaiting re-verification', ledger.reason)
        self.assertIn('awaiting re-verification', chk.call_args.kwargs['defaults']['note'])

    def test_rejected_attendance_still_refused(self):
        with self.assertRaises(ValueError):
            self._grant(S.REJECTED)

    def test_not_checked_in_still_refused(self):
        with self.assertRaises(ValueError):
            self._grant(S.REVIEW, checked_in=False)


class CreditDecisionTests(SimpleTestCase):
    """'credit' approves the request: the meal is recorded as if scanned in time."""

    def _decide(self, decision, note=''):
        from accounts.models import MealIssueReport
        manager = SimpleNamespace(pk=1)
        issue = SimpleNamespace(
            pk=1, status=MealIssueReport.Status.PENDING, employee=SimpleNamespace(), pharmacy=object(),
            date=timezone.localdate(), meal_type='LUNCH', reference='MI-0001',
            get_meal_type_display=lambda: 'Lunch', refresh_from_db=mock.Mock(),
        )
        ledger = SimpleNamespace(pk=5)
        decide = getattr(ms.decide_meal_issue, '__wrapped__', ms.decide_meal_issue)
        with mock.patch.object(ms.MealIssueReport.objects, 'select_for_update') as sfu, \
             mock.patch.object(ms.MealLedger.objects, 'filter', return_value=mock.Mock(first=lambda: ledger)), \
             mock.patch.object(ms, 'manager_grant_meal') as grant:
            sfu.return_value.select_related.return_value.get.return_value = issue
            decide(issue, manager, decision, note, '')
        return grant

    def test_credit_grants_with_request_label_and_default_note(self):
        grant = self._decide('credit')
        grant.assert_called_once()
        self.assertEqual(grant.call_args.kwargs['source_label'], 'Approved employee request')
        self.assertIn('MI-0001', grant.call_args.args[3])

    def test_credit_keeps_manager_note(self):
        grant = self._decide('credit', 'Was serving customers')
        self.assertEqual(grant.call_args.args[3], 'Was serving customers')
