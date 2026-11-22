"""Late entry (backdated paper-notebook sales) - validation rules, no database needed."""
from datetime import date
from unittest import mock

from django.test import SimpleTestCase, override_settings

from . import late_entry as le

TODAY = date(2026, 11, 21)


def _payload(**kw):
    base = {'business_date': '2026-11-20', 'late_entry_reason': 'PAPER_NOTEBOOK', 'late_entry_note': ''}
    base.update(kw)
    return base


@override_settings(LATE_ENTRY_MAX_DAYS=3)
class ParseLateEntryTests(SimpleTestCase):
    def setUp(self):
        p = mock.patch.object(le.timezone, 'localdate', return_value=TODAY)
        p.start()
        self.addCleanup(p.stop)

    def test_no_date_or_today_is_a_normal_sale(self):
        self.assertIsNone(le.parse_late_entry({}))
        self.assertIsNone(le.parse_late_entry({'business_date': ''}))
        self.assertIsNone(le.parse_late_entry({'business_date': '2026-11-21'}))

    def test_yesterday_is_a_late_entry(self):
        late = le.parse_late_entry(_payload())
        self.assertEqual(late.business_date, date(2026, 11, 20))
        self.assertTrue(late.is_late)
        self.assertIn('Late entry for 20 Nov 2026', late.stock_note_prefix())

    def test_future_date_rejected(self):
        with self.assertRaises(le.LateEntryError):
            le.parse_late_entry(_payload(business_date='2026-11-22'))

    def test_invalid_date_rejected(self):
        with self.assertRaises(le.LateEntryError):
            le.parse_late_entry(_payload(business_date='yesterday'))

    def test_limit_of_three_days(self):
        self.assertEqual(le.parse_late_entry(_payload(business_date='2026-11-18')).business_date, date(2026, 11, 18))
        with self.assertRaises(le.LateEntryError):
            le.parse_late_entry(_payload(business_date='2026-11-17'))

    def test_reason_is_mandatory(self):
        with self.assertRaises(le.LateEntryError):
            le.parse_late_entry(_payload(late_entry_reason=''))
        with self.assertRaises(le.LateEntryError):
            le.parse_late_entry(_payload(late_entry_reason='BECAUSE'))

    def test_other_reason_needs_an_explanation(self):
        with self.assertRaises(le.LateEntryError):
            le.parse_late_entry(_payload(late_entry_reason='OTHER', late_entry_note='short'))
        ok = le.parse_late_entry(_payload(late_entry_reason='OTHER', late_entry_note='Power cut all day'))
        self.assertEqual(ok.reason, 'OTHER')

    def test_note_is_capped(self):
        late = le.parse_late_entry(_payload(late_entry_note='x' * 999))
        self.assertEqual(len(late.note), 300)

    @override_settings(LATE_ENTRY_MAX_DAYS=0)
    def test_feature_can_be_switched_off(self):
        with self.assertRaises(le.LateEntryError) as ctx:
            le.parse_late_entry(_payload())
        self.assertEqual(ctx.exception.status, 403)


class CashDayNeedTests(SimpleTestCase):
    def test_which_payments_touch_the_drawer(self):
        self.assertTrue(le.needs_cash_day('CASH'))
        self.assertTrue(le.needs_cash_day('SPLIT'))
        self.assertFalse(le.needs_cash_day('MOBILE_MONEY'))
        self.assertFalse(le.needs_cash_day('DEBT', 0, 'CASH'))
        self.assertFalse(le.needs_cash_day('DEBT', 5000, 'MOBILE_MONEY'))
        self.assertTrue(le.needs_cash_day('DEBT', 5000, 'CASH'))

    def test_deposit_timestamp_is_inside_the_sale_day(self):
        late = le.LateEntry(business_date=date(2026, 11, 20), reason='FORGOT', note='')
        stamp = le.paid_at_for(late)
        from django.utils import timezone
        self.assertEqual(timezone.localtime(stamp).date(), date(2026, 11, 20))


class ClosedDayRulesTests(SimpleTestCase):
    """The DB-free parts of the closed-cash-day rules (the lock itself is exercised on a real database)."""

    def test_error_carries_code_and_ack_flag(self):
        err = le.LateEntryError('x', 409, code='closed_day', can_ack=True)
        self.assertEqual((err.status, err.code, err.can_ack), (409, 'closed_day', True))

    def test_error_defaults_are_safe(self):
        err = le.LateEntryError('x')
        self.assertEqual((err.status, err.code, err.can_ack), (400, '', False))
