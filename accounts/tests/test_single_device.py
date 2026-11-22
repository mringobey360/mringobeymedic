"""Single-device sign-in (accounts/single_device.py)."""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import single_device
from accounts.models import ActiveDeviceSession, DeviceSessionAudit, User

PASSWORD = 'S3cret-pass!'


class SingleDeviceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='cashier', password=PASSWORD, role=User.Role.EMPLOYEE, phone_number='+255712345001')

    def login(self, client, username='cashier'):
        return client.post(reverse('accounts:login'), {'username': username, 'password': PASSWORD})

    def is_logged_in(self, client):
        return '_auth_user_id' in client.session

    def other_device(self):
        return Client()  # separate cookie jar == separate device

    # -- blocking ---------------------------------------------------------
    def test_second_device_is_blocked_even_with_correct_password(self):
        a, b = Client(), self.other_device()
        self.login(a)
        self.assertTrue(self.is_logged_in(a))
        self.login(b)
        self.assertFalse(self.is_logged_in(b))
        self.assertTrue(DeviceSessionAudit.objects.filter(
            user=self.user, action=DeviceSessionAudit.Action.BLOCKED).exists())

    def test_same_device_can_sign_in_again(self):
        a = Client()
        self.login(a)
        again = Client()
        again.cookies[single_device.COOKIE_NAME] = a.cookies[single_device.COOKIE_NAME].value
        self.login(again)
        self.assertTrue(self.is_logged_in(again))

    # -- release paths ----------------------------------------------------
    def test_logout_releases_the_account(self):
        a, b = Client(), self.other_device()
        self.login(a)
        a.get(reverse('accounts:logout'))
        self.assertFalse(ActiveDeviceSession.objects.filter(user=self.user).exists())
        self.login(b)
        self.assertTrue(self.is_logged_in(b))

    def test_idle_device_is_released_and_old_device_is_signed_out(self):
        a, b = Client(), self.other_device()
        self.login(a)
        ActiveDeviceSession.objects.filter(user=self.user).update(
            last_seen=timezone.now() - timedelta(minutes=single_device.idle_release_minutes() + 1))
        self.login(b)
        self.assertTrue(self.is_logged_in(b))
        resp = a.get(reverse('accounts:profile'))
        self.assertRedirects(resp, reverse('accounts:login'), fetch_redirect_response=False)
        self.assertFalse(self.is_logged_in(a))

    def test_manager_release_frees_account_and_signs_old_device_out(self):
        a, b = Client(), self.other_device()
        manager = User.objects.create_user(username='boss', password=PASSWORD, role=User.Role.ADMIN)
        self.login(a)
        self.assertTrue(single_device.release_by_manager(self.user, actor=manager))
        self.login(b)
        self.assertTrue(self.is_logged_in(b))
        resp = a.get(reverse('accounts:profile'))
        self.assertRedirects(resp, reverse('accounts:login'), fetch_redirect_response=False)
        self.assertTrue(DeviceSessionAudit.objects.filter(
            user=self.user, action=DeviceSessionAudit.Action.RELEASED_BY_MANAGER, actor=manager).exists())

    def test_expired_session_does_not_hold_the_lock(self):
        from django.contrib.sessions.models import Session
        a, b = Client(), self.other_device()
        self.login(a)
        Session.objects.all().update(expire_date=timezone.now() - timedelta(minutes=1))
        self.login(b)
        self.assertTrue(self.is_logged_in(b))

    # -- exemptions / switches -------------------------------------------
    def test_exempt_account_can_use_two_devices(self):
        self.user.allow_multiple_devices = True
        self.user.save(update_fields=['allow_multiple_devices'])
        a, b = Client(), self.other_device()
        self.login(a)
        self.login(b)
        self.assertTrue(self.is_logged_in(a) and self.is_logged_in(b))

    def test_platform_superuser_is_exempt(self):
        User.objects.create_user(username='root', password=PASSWORD, is_superuser=True)
        a, b = Client(), self.other_device()
        self.login(a, 'root')
        self.login(b, 'root')
        self.assertTrue(self.is_logged_in(a) and self.is_logged_in(b))

    @override_settings(SINGLE_DEVICE_LOGIN_ENABLED=False)
    def test_feature_can_be_switched_off(self):
        a, b = Client(), self.other_device()
        self.login(a)
        self.login(b)
        self.assertTrue(self.is_logged_in(a) and self.is_logged_in(b))
        self.assertFalse(ActiveDeviceSession.objects.exists())

    def test_session_from_before_the_feature_is_adopted(self):
        a = Client()
        a.force_login(self.user)  # no lock row, no device cookie
        a.get(reverse('accounts:profile'))
        lock = ActiveDeviceSession.objects.get(user=self.user)
        self.assertEqual(lock.session_key, a.session.session_key)
