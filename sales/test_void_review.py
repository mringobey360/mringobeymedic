"""V37.16 — void review flag logic (pure function, no database needed)."""
from datetime import date, datetime, timedelta, timezone as dt_tz
from decimal import Decimal

from django.test import SimpleTestCase, override_settings

from .void_review import classify, thresholds


class ClassifyTests(SimpleTestCase):
    def setUp(self):
        self.cfg = {'late_hours': 2, 'high_value': Decimal('50000'), 'repeat_count': 3}
        self.sale_at = datetime(2026, 10, 1, 17, 0, tzinfo=dt_tz.utc)  # 20:00 Tanzania

    def test_quick_same_day_small_void_has_no_flags(self):
        delay, flags = classify(sale_created_at=self.sale_at, sale_business_date=date(2026, 10, 1),
                                decided_at=self.sale_at + timedelta(minutes=5),
                                refund_amount=Decimal('3000'), cfg=self.cfg)
        self.assertEqual(flags, [])
        self.assertAlmostEqual(delay, 5 / 60, places=3)

    def test_late_and_cross_day_and_high(self):
        delay, flags = classify(sale_created_at=self.sale_at, sale_business_date=date(2026, 10, 1),
                                decided_at=self.sale_at + timedelta(hours=11),
                                refund_amount=Decimal('60000'), cfg=self.cfg)
        self.assertEqual(set(flags), {'LATE', 'CROSS_DAY', 'HIGH'})

    @override_settings(VOID_REVIEW_LATE_HOURS=6, VOID_REVIEW_HIGH_VALUE=10000, VOID_REVIEW_REPEAT_COUNT=5)
    def test_thresholds_come_from_settings(self):
        cfg = thresholds()
        self.assertEqual(cfg['late_hours'], 6)
        self.assertEqual(cfg['high_value'], Decimal('10000'))
        self.assertEqual(cfg['repeat_count'], 5)
