from unittest import mock

from django.core import signing
from django.test import SimpleTestCase

from accounts import attendance_services as svc


class FakePharmacy:
    pk = 7


class BranchQrTokenTests(SimpleTestCase):
    def test_roundtrip(self):
        token = svc.make_branch_qr_token(FakePharmacy())
        self.assertEqual(svc.read_branch_qr_token(token), 7)

    def test_tampered_token_rejected(self):
        token = svc.make_branch_qr_token(FakePharmacy())
        with self.assertRaises(ValueError):
            svc.read_branch_qr_token(token[:-2] + 'xx')

    def test_garbage_rejected(self):
        with self.assertRaises(ValueError):
            svc.read_branch_qr_token('')

    def test_expired_token_rejected(self):
        token = svc.make_branch_qr_token(FakePharmacy())
        with mock.patch('django.core.signing.time.time', return_value=__import__('time').time() + 3600):
            with self.assertRaises(ValueError):
                svc.read_branch_qr_token(token)


class AttendanceMethodsTests(SimpleTestCase):
    def _user(self, value):
        from accounts.models import User
        return User(attendance_methods=value)

    def test_parses_and_ignores_unknown(self):
        self.assertEqual(self._user('GPS,QR,JUNK').allowed_attendance_methods, {'GPS', 'QR'})

    def test_default_matches_original_behaviour(self):
        from accounts.models import User
        self.assertEqual(User().allowed_attendance_methods, {'GPS', 'MANUAL'})

    def test_qr_refused_when_not_enabled(self):
        with self.assertRaises(ValueError):
            svc.start_workday_with_qr(self._user('GPS'), 'x', None)

    def test_manual_refused_when_not_enabled(self):
        with self.assertRaises(ValueError):
            svc.request_manual_exception(self._user('GPS,QR'), 'reason', None)

    def test_gps_refused_when_not_enabled(self):
        with self.assertRaises(ValueError):
            svc.start_verified_workday(self._user('QR'), 1, 1, 10, None)


class ScanLocationGateTests(SimpleTestCase):
    """The branch QR scan must also prove the employee is at the workplace."""

    def _employee(self):
        from types import SimpleNamespace as NS
        return NS(pharmacy=NS(latitude=-6.18, longitude=35.75, geofence_radius_m=150, attendance_qr_requires_location=True))

    def test_missing_location_is_refused(self):
        with self.assertRaises(ValueError):
            svc.check_scan_location(self._employee(), {}, None)

    def test_weak_accuracy_asks_for_retry_without_accusing(self):
        with self.assertRaises(ValueError) as ctx:
            svc.check_scan_location(self._employee(), {'latitude': '-6.18', 'longitude': '35.75', 'accuracy': '900'}, None)
        self.assertIn('accuracy', str(ctx.exception).lower())

    def test_at_workplace_is_accepted(self):
        lat, lon, acc, dist = svc.check_scan_location(
            self._employee(), {'latitude': '-6.18002', 'longitude': '35.75001', 'accuracy': '20'}, None)
        self.assertLess(dist, 150)

    def test_policy_flag_defaults_on_and_can_be_disabled(self):
        from types import SimpleNamespace as NS
        self.assertTrue(svc.qr_requires_location(NS(attendance_qr_requires_location=True)))
        self.assertFalse(svc.qr_requires_location(NS(attendance_qr_requires_location=False)))
        self.assertFalse(svc.qr_requires_location(None))
