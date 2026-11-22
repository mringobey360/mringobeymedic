from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from django.core.cache import cache
from datetime import timedelta
from unittest.mock import patch
from django.urls import reverse

from accounts import device_pin
from accounts.models import PinSecurityAudit, TrustedDevice, User, UserPin

PASSWORD = 'S3cret-pass!'
AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


class PinValidationTests(SimpleTestCase):
    def test_accepts_good_pin(self):
        self.assertIsNone(device_pin.validate_pin('7391'))

    def test_rejects_wrong_length_and_non_digits(self):
        for bad in ('', '123', '12345', 'abcd', '12a4', ' 7391'):
            self.assertIsNotNone(device_pin.validate_pin(bad), bad)

    def test_rejects_weak_pins(self):
        for weak in ('0000', '1111', '1234', '4321'):
            self.assertIsNotNone(device_pin.validate_pin(weak), weak)


class PinTestCase(TestCase):
    def make_user(self, username='cashier', role=User.Role.EMPLOYEE, **extra):
        # is_superuser bypasses the subscription gate; the PIN logic itself is role based.
        return User.objects.create_user(
            username=username, password=PASSWORD, first_name='Amina', role=role,
            is_superuser=True, **extra,
        )

    def setUp(self):
        cache.clear()
        self.user = self.make_user()

    def password_login(self, username='cashier', **extra):
        return self.client.post(reverse('accounts:login'), {'username': username, 'password': PASSWORD, **extra})

    def create_pin_via_page(self, pin='7391'):
        return self.client.post(reverse('accounts:pin_setup'), {'pin': pin, 'pin_confirm': pin, 'trust_device': 'on'}, **AJAX)

    def device_cookie(self):
        morsel = self.client.cookies.get(device_pin.COOKIE_NAME)
        return morsel.value if morsel else ''


class ForcedSetupTests(PinTestCase):
    def test_user_without_pin_is_forced_to_create_one(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard:home'))
        self.assertRedirects(resp, reverse('accounts:pin_setup'), fetch_redirect_response=False)

    def test_ajax_request_gets_403_json_instead_of_redirect(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard:home'), **AJAX)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['error'], 'pin_setup_required')

    def test_setup_page_renders_and_logout_stays_reachable(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('accounts:pin_setup')).status_code, 200)
        resp = self.client.get(reverse('accounts:logout'))
        self.assertRedirects(resp, reverse('accounts:login'), fetch_redirect_response=False)

    def test_creating_pin_without_trust_does_not_remember_device(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('accounts:pin_setup'), {'pin': '7391', 'pin_confirm': '7391'}, **AJAX)
        self.assertTrue(resp.json()['ok'])
        self.assertFalse(TrustedDevice.objects.filter(user=self.user).exists())
        audit = PinSecurityAudit.objects.get(user=self.user, action=PinSecurityAudit.Action.CHANGED)
        self.assertEqual(audit.details.get('event'), 'created')

    def test_creating_pin_stores_hash_and_remembers_device(self):
        self.client.force_login(self.user)
        resp = self.create_pin_via_page()
        self.assertTrue(resp.json()['ok'])
        pin = UserPin.objects.get(user=self.user)
        self.assertNotIn('7391', pin.pin_hash)
        raw = self.device_cookie()
        self.assertTrue(raw)
        device = TrustedDevice.objects.get(user=self.user)
        self.assertEqual(device.token_hash, device_pin.hash_token(raw))
        self.assertNotEqual(device.token_hash, raw)

    def test_pin_is_saved_even_if_trusted_device_registration_fails(self):
        self.client.force_login(self.user)
        with patch('accounts.views.device_pin.trust_device', side_effect=RuntimeError('simulated device failure')):
            resp = self.create_pin_via_page()
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['ok'])
        self.assertIn('could not be trusted', payload['warning'])
        pin = UserPin.objects.get(user=self.user)
        self.assertTrue(pin.pin_hash)
        self.assertFalse(TrustedDevice.objects.filter(user=self.user).exists())

    def test_weak_or_mismatched_pin_is_rejected(self):
        self.client.force_login(self.user)
        self.assertEqual(self.create_pin_via_page('1234').status_code, 400)
        resp = self.client.post(reverse('accounts:pin_setup'), {'pin': '7391', 'pin_confirm': '7392'}, **AJAX)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(UserPin.objects.exists())

    def test_user_with_pin_is_not_forced_again(self):
        device_pin.set_pin(self.user, '7391')
        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard:home'))
        self.assertNotEqual(resp.get('Location'), reverse('accounts:pin_setup'))
        # The setup page sends users who already have a PIN to the change-PIN page.
        resp = self.client.get(reverse('accounts:pin_setup'))
        self.assertRedirects(resp, reverse('accounts:device_pin'), fetch_redirect_response=False)

    def test_must_change_password_comes_first(self):
        self.user.must_change_password = True
        self.user.save()
        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard:home'))
        self.assertRedirects(resp, reverse('accounts:change_password'), fetch_redirect_response=False)

    def test_branch_display_accounts_are_not_forced(self):
        kiosk = self.make_user('kiosk', role=User.Role.BRANCH_DISPLAY)
        self.assertFalse(device_pin.needs_pin_setup(kiosk))

    @override_settings(DEVICE_PIN_REQUIRED=False)
    def test_feature_can_be_switched_off(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard:home'))
        self.assertNotEqual(resp.get('Location'), reverse('accounts:pin_setup'))


class PinSignInTests(PinTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.create_pin_via_page()
        self.client.logout()

    def test_login_page_shows_pin_pad_after_logout(self):
        page = self.client.get(reverse('accounts:login'))
        self.assertContains(page, 'id="pin-root"')
        self.assertContains(page, 'Use another account')
        self.assertContains(page, 'Forgot PIN?')

    def test_logout_remembers_pin_user_on_current_browser_by_default(self):
        # Simulate a browser that has no remembered-device cookie, while the
        # authenticated user already has a valid PIN. Logout should establish
        # the browser's PIN sign-in state so password fields are not the default.
        self.client.force_login(self.user)
        self.client.cookies.pop(device_pin.COOKIE_NAME, None)
        response = self.client.get(reverse('accounts:logout'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(device_pin.COOKIE_NAME, response.cookies)
        page = self.client.get(reverse('accounts:login'))
        self.assertContains(page, 'id="pin-root"')
        self.assertContains(page, 'Use another account')

    def test_use_another_account_shows_password_form(self):
        page = self.client.get(reverse('accounts:login') + '?use=password')
        self.assertNotContains(page, 'id="pin-root"')
        self.assertContains(page, 'Sign in to your account')

    def test_forgot_pin_banner_and_hidden_field(self):
        page = self.client.get(reverse('accounts:login') + '?use=password&reason=forgot')
        self.assertContains(page, 'Forgot your PIN?')
        self.assertContains(page, 'name="reason" value="forgot"')

    def test_correct_pin_signs_in(self):
        resp = self.client.post(reverse('accounts:pin_login'), {'pin': '7391'})
        self.assertTrue(resp.json()['ok'])
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)

    def test_progressive_lockout(self):
        url = reverse('accounts:pin_login')
        for _ in range(2):
            resp = self.client.post(url, {'pin': '0001'})
            self.assertEqual(resp.status_code, 400)
        resp = self.client.post(url, {'pin': '0001'})
        self.assertEqual(resp.status_code, 429)
        pin = UserPin.objects.get(user=self.user)
        self.assertEqual(pin.failed_attempts, 3)
        self.assertTrue(pin.locked_until)
        pin.locked_until = timezone.now() - timedelta(seconds=1)
        pin.save(update_fields=['locked_until'])
        resp = self.client.post(url, {'pin': '0001'})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post(url, {'pin': '0001'})
        self.assertEqual(resp.status_code, 429)
        pin.refresh_from_db()
        self.assertEqual(pin.failed_attempts, 5)
        pin.locked_until = timezone.now() - timedelta(seconds=1)
        pin.save(update_fields=['locked_until'])
        resp = self.client.post(url, {'pin': '0001'})
        self.assertEqual(resp.status_code, 403)
        pin.refresh_from_db()
        self.assertTrue(pin.is_locked)

    def test_locked_pin_is_recreated_after_password_login(self):
        UserPin.objects.filter(user=self.user).update(locked_at=device_pin.timezone.now())
        self.password_login()
        resp = self.client.get(reverse('dashboard:home'))
        self.assertRedirects(resp, reverse('accounts:pin_setup'), fetch_redirect_response=False)
        self.assertTrue(self.create_pin_via_page('8264').json()['ok'])
        pin = UserPin.objects.get(user=self.user)
        self.assertFalse(pin.is_locked)
        self.assertEqual(pin.failed_attempts, 0)

    def test_forgot_pin_flow_forces_new_pin_after_password_login(self):
        self.password_login(reason='forgot')
        self.assertTrue(UserPin.objects.get(user=self.user).is_locked)
        resp = self.client.get(reverse('dashboard:home'))
        self.assertRedirects(resp, reverse('accounts:pin_setup'), fetch_redirect_response=False)

    def test_password_login_remembers_browser_so_logout_shows_pin_pad(self):
        self.client.cookies.pop(device_pin.COOKIE_NAME, None)  # e.g. cookie expired / cleared
        self.assertNotContains(self.client.get(reverse('accounts:login')), 'id="pin-root"')
        self.password_login(trust_device='on')
        self.client.get(reverse('accounts:logout'))
        self.assertContains(self.client.get(reverse('accounts:login')), 'id="pin-root"')

    def test_password_login_without_trust_removes_existing_browser_trust(self):
        self.client.cookies.pop(device_pin.COOKIE_NAME, None)
        self.password_login(trust_device='on')
        self.assertTrue(TrustedDevice.objects.filter(user=self.user).exists())
        self.client.get(reverse('accounts:logout'))
        self.password_login(trust_device='')
        self.assertFalse(TrustedDevice.objects.filter(user=self.user).exists())
        self.client.get(reverse('accounts:logout'))
        self.assertNotContains(self.client.get(reverse('accounts:login')), 'id="pin-root"')

    def test_other_account_signing_in_replaces_remembered_user(self):
        other = self.make_user('other')
        device_pin.set_pin(other, '8264')
        first_cookie = self.device_cookie()
        self.password_login('other', trust_device='on')
        self.client.get(reverse('accounts:logout'))
        self.assertNotEqual(self.device_cookie(), first_cookie)
        page = self.client.get(reverse('accounts:login'))
        self.assertContains(page, '@other')
        self.assertFalse(TrustedDevice.objects.filter(user=self.user).exists())

    def test_pin_without_cookie_is_refused(self):
        self.client.cookies.pop(device_pin.COOKIE_NAME, None)
        resp = self.client.post(reverse('accounts:pin_login'), {'pin': '7391'})
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.json()['fallback'])

    def test_deactivated_user_cannot_use_pin(self):
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.post(reverse('accounts:pin_login'), {'pin': '7391'}).status_code, 403)

    def test_must_change_password_blocks_pin(self):
        self.user.must_change_password = True
        self.user.save()
        self.assertEqual(self.client.post(reverse('accounts:pin_login'), {'pin': '7391'}).status_code, 403)


class IdleLockTests(PinTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.create_pin_via_page()
        self.client.get(reverse('dashboard:home'))

    @override_settings(DEVICE_PIN_IDLE_LOCK_MINUTES=1)
    def test_idle_lock_screen_preserves_session_and_requires_pin(self):
        session = self.client.session
        session['device_pin_idle_enabled'] = True
        session['device_pin_last_activity'] = int((timezone.now() - timedelta(minutes=2)).timestamp())
        session.save()
        resp = self.client.get(reverse('dashboard:home'))
        self.assertRedirects(resp, reverse('accounts:device_pin_lock'), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)
        self.assertEqual(self.client.get(reverse('accounts:device_pin_lock')).status_code, 200)
        resp = self.client.post(reverse('accounts:device_pin_unlock'), {'pin': '7391'}, **AJAX)
        self.assertTrue(resp.json()['ok'])
        self.assertNotIn('device_pin_locked', self.client.session)

    @override_settings(DEVICE_PIN_IDLE_LOCK_MINUTES=1)
    def test_idle_lock_preserves_original_destination_after_pin_unlock(self):
        session = self.client.session
        session['device_pin_idle_enabled'] = True
        session['device_pin_last_activity'] = int((timezone.now() - timedelta(minutes=2)).timestamp())
        session.save()

        target = reverse('inventory:product_list')
        resp = self.client.get(target)
        self.assertRedirects(
            resp,
            reverse('accounts:device_pin_lock') + '?next=' + target,
            fetch_redirect_response=False,
        )

        resp = self.client.post(
            reverse('accounts:device_pin_unlock'),
            {'pin': '7391', 'next': target},
            **AJAX,
        )
        self.assertTrue(resp.json()['ok'])
        self.assertEqual(resp.json()['redirect'], target)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)

    @override_settings(DEVICE_PIN_IDLE_LOCK_MINUTES=1)
    def test_idle_lock_never_logs_out_when_trusted_device_is_missing(self):
        session = self.client.session
        session['device_pin_idle_enabled'] = True
        session['device_pin_last_activity'] = int((timezone.now() - timedelta(minutes=2)).timestamp())
        session.save()

        # Simulate a mobile browser losing its trusted-device cookie/record.
        TrustedDevice.objects.filter(user=self.user).delete()
        resp = self.client.get(reverse('dashboard:home'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)
        self.assertFalse(self.client.session.get('device_pin_idle_enabled', True))


class PinManagementTests(PinTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.create_pin_via_page()

    def test_change_pin_requires_password(self):
        url = reverse('accounts:device_pin')
        self.client.post(url, {'action': 'change', 'current_password': 'wrong', 'pin': '8264', 'pin_confirm': '8264'})
        self.assertTrue(device_pin.check_password('7391', UserPin.objects.get(user=self.user).pin_hash))
        self.client.post(url, {'action': 'change', 'current_password': PASSWORD, 'pin': '8264', 'pin_confirm': '8264'})
        self.assertTrue(device_pin.check_password('8264', UserPin.objects.get(user=self.user).pin_hash))

    def test_reset_pin_removes_pin_and_devices_and_forces_new_one(self):
        device_pin.reset_pin(self.user)
        self.assertFalse(UserPin.objects.filter(user=self.user).exists())
        self.assertFalse(TrustedDevice.objects.filter(user=self.user).exists())
        self.assertTrue(device_pin.needs_pin_setup(self.user))

    def test_password_change_keeps_this_device_but_drops_others(self):
        TrustedDevice.objects.create(user=self.user, token_hash='b' * 64, label='Old phone')
        self.client.post(reverse('accounts:change_password'), {
            'old_password': PASSWORD, 'new_password1': 'Brand-new-pass-91', 'new_password2': 'Brand-new-pass-91',
        })
        remaining = list(TrustedDevice.objects.filter(user=self.user).values_list('token_hash', flat=True))
        self.assertEqual(remaining, [device_pin.hash_token(self.device_cookie())])

    def test_cannot_revoke_another_users_device(self):
        other = self.make_user('other')
        foreign = TrustedDevice.objects.create(user=other, token_hash='x' * 64)
        self.client.post(reverse('accounts:device_pin'), {'action': 'revoke', 'device_id': foreign.pk})
        self.assertTrue(TrustedDevice.objects.filter(pk=foreign.pk).exists())

    def test_revoke_own_device(self):
        device = TrustedDevice.objects.get(user=self.user)
        self.client.post(reverse('accounts:device_pin'), {'action': 'revoke', 'device_id': device.pk})
        self.assertFalse(TrustedDevice.objects.filter(pk=device.pk).exists())
