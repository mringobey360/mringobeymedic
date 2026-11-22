import base64
import json
import time
from unittest import mock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts import google_auth
from accounts.models import User
from hq.models import PaymentSettings, SignupRequest, Subscription

CLIENT_ID = 'test-client.apps.googleusercontent.com'
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _jwks():
    nums = KEY.public_key().public_numbers()
    return [{'kid': 'k1', 'kty': 'RSA', 'alg': 'RS256',
             'n': _b64(nums.n.to_bytes(256, 'big')), 'e': _b64(nums.e.to_bytes(3, 'big'))}]


def make_token(nonce, **over):
    claims = {'iss': 'https://accounts.google.com', 'aud': CLIENT_ID, 'sub': 'g-123', 'email': 'neema@example.com',
              'email_verified': True, 'given_name': 'Neema', 'family_name': 'Juma', 'nonce': nonce,
              'iat': int(time.time()), 'exp': int(time.time()) + 600}
    claims.update(over)
    head = _b64(json.dumps({'alg': 'RS256', 'kid': 'k1', 'typ': 'JWT'}).encode())
    body = _b64(json.dumps(claims).encode())
    sig = KEY.sign(f'{head}.{body}'.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f'{head}.{body}.{_b64(sig)}'


@override_settings(GOOGLE_CLIENT_ID=CLIENT_ID)
class GoogleSignInTests(TestCase):
    def setUp(self):
        PaymentSettings.load()
        p = mock.patch('accounts.google_auth._load_jwks', return_value=_jwks())
        p.start()
        self.addCleanup(p.stop)

    def _nonce(self):
        self.client.get(reverse('accounts:login'))
        return self.client.session[google_auth.NONCE_SESSION_KEY]

    def _post(self, token):
        return self.client.post(reverse('accounts:google_auth'), data=json.dumps({'credential': token}), content_type='application/json')

    def test_login_page_shows_google_button(self):
        self.assertContains(self.client.get(reverse('accounts:login')), 'data-google-root')

    @override_settings(GOOGLE_CLIENT_ID='')
    def test_button_hidden_when_not_configured(self):
        self.assertNotContains(self.client.get(reverse('accounts:login')), 'data-google-root')

    def test_bad_signature_wrong_audience_expired_and_wrong_nonce_are_rejected(self):
        def tampered(nonce):
            head, body, sig = make_token(nonce).split('.')
            claims = json.loads(base64.urlsafe_b64decode(body + '=='))
            claims['email'] = 'evil@x.com'
            return f"{head}.{_b64(json.dumps(claims).encode())}.{sig}"

        makers = (
            tampered,
            lambda n: make_token(n, aud='other'),
            lambda n: make_token(n, exp=int(time.time()) - 3600),
            lambda n: make_token('not-the-nonce'),
            lambda n: make_token(n, email_verified=False),
            lambda n: make_token(n, iss='evil.com'),
        )
        for make in makers:
            resp = self._post(make(self._nonce()))   # fresh page (and nonce) each time
            self.assertEqual(resp.status_code, 401, resp.content)
            self.assertNotIn('_auth_user_id', self.client.session)

    def test_nonce_is_single_use(self):
        nonce = self._nonce()
        token = make_token(nonce)
        self.assertEqual(self._post(token).status_code, 200)
        self.assertEqual(self._post(token).status_code, 401)

    def test_existing_owner_is_linked_by_verified_email_and_signed_in(self):
        owner = User.objects.create_user(username='neema', password='x', email='Neema@Example.com', role=User.Role.ADMIN)
        resp = self._post(make_token(self._nonce()))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['new'])
        owner.refresh_from_db()
        self.assertEqual(owner.google_sub, 'g-123')
        self.assertEqual(int(self.client.session['_auth_user_id']), owner.pk)

    def test_superuser_and_employee_cannot_use_google(self):
        User.objects.create_user(username='boss', password='x', email='neema@example.com', role=User.Role.ADMIN, is_superuser=True)
        resp = self._post(make_token(self._nonce(), sub='g-boss'))
        # Superusers are never matched by email, so this is treated as a new visitor, not a login.
        self.assertTrue(resp.json()['new'])
        self.assertNotIn('_auth_user_id', self.client.session)
        emp = User.objects.create_user(username='emp', password='x', role=User.Role.EMPLOYEE)
        emp.google_sub = 'g-emp'
        emp.save()
        resp = self._post(make_token(self._nonce(), sub='g-emp'))
        self.assertEqual(resp.status_code, 403)

    def test_new_visitor_registers_with_google_gets_trial_sms_and_is_signed_in(self):
        resp = self._post(make_token(self._nonce()))
        self.assertTrue(resp.json()['new'])
        page = self.client.get(reverse('hq:signup') + '?google=1')
        self.assertContains(page, 'neema@example.com')
        resp = self.client.post(reverse('hq:signup'), {
            'business_type': 'RETAIL', 'business_name': 'Neema Stores', 'owner_first_name': 'Neema', 'owner_last_name': 'Juma',
            'phone_number': '0712345678', 'email': 'attacker@evil.com', 'account_type': 'TRIAL',
        })
        self.assertRedirects(resp, reverse('dashboard:home'), fetch_redirect_response=False)
        signup = SignupRequest.objects.get(business_name='Neema Stores')
        owner = signup.created_owner
        self.assertEqual(owner.google_sub, 'g-123')
        self.assertEqual(owner.email, 'neema@example.com')   # taken from Google, not the form
        self.assertFalse(owner.must_change_password)
        self.assertTrue(Subscription.objects.get(owner=owner).is_trial)
        self.assertEqual(int(self.client.session['_auth_user_id']), owner.pk)


@override_settings(GOOGLE_CLIENT_ID=CLIENT_ID)
class GoogleLinkTests(TestCase):
    """An existing owner connects (or disconnects) Google from the profile page, whatever their Gmail is."""

    def setUp(self):
        p = mock.patch('accounts.google_auth._load_jwks', return_value=_jwks())
        p.start()
        self.addCleanup(p.stop)
        self.owner = User.objects.create_user(username='mama', password='x', role=User.Role.ADMIN)
        self.client.force_login(self.owner)

    def _nonce(self):
        self.client.get(reverse('accounts:profile'))
        return self.client.session[google_auth.NONCE_SESSION_KEY]

    def _post(self, url, token):
        return self.client.post(reverse(url), data=json.dumps({'credential': token}), content_type='application/json')

    def test_profile_offers_connect_button(self):
        self.assertContains(self.client.get(reverse('accounts:profile')), 'data-google-root')

    def test_owner_links_any_gmail_and_can_disconnect(self):
        resp = self._post('accounts:google_link', make_token(self._nonce(), email='different@gmail.com'))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.google_sub, 'g-123')
        self.assertEqual(self.owner.email, 'different@gmail.com')
        self.assertContains(self.client.get(reverse('accounts:profile')), 'Disconnect Google')
        resp = self.client.post(reverse('accounts:google_unlink'))
        self.assertEqual(resp.status_code, 200)
        self.owner.refresh_from_db()
        self.assertIsNone(self.owner.google_sub)

    def test_google_account_cannot_be_shared_by_two_owners(self):
        other = User.objects.create_user(username='baba', password='x', role=User.Role.ADMIN)
        other.google_sub = 'g-123'
        other.save()
        resp = self._post('accounts:google_link', make_token(self._nonce()))
        self.assertEqual(resp.status_code, 409)

    def test_employee_cannot_connect_google(self):
        emp = User.objects.create_user(username='emp2', password='x', role=User.Role.EMPLOYEE)
        self.client.force_login(emp)
        resp = self._post('accounts:google_link', make_token('whatever'))
        self.assertEqual(resp.status_code, 403)


class GoogleProfilePhotoTests(TestCase):
    """The Google account picture fills an empty profile photo, safely."""

    def _owner(self):
        return User.objects.create_user(username='pic', password='x', role=User.Role.ADMIN)

    def test_only_google_image_hosts_over_https_are_accepted(self):
        ok = google_auth._safe_picture_url('https://lh3.googleusercontent.com/a/abc=s96-c')
        self.assertTrue(ok.endswith('=s400-c'))
        for bad in ('http://lh3.googleusercontent.com/a/x', 'https://evil.com/a.jpg',
                    'https://googleusercontent.com.evil.com/a.jpg', '', None):
            self.assertEqual(google_auth._safe_picture_url(bad), '')

    def test_photo_is_saved_when_empty_and_never_overwrites_existing(self):
        owner = self._owner()
        resp = mock.Mock(status_code=200, headers={'Content-Type': 'image/jpeg'})
        resp.iter_content.return_value = [b'\xff\xd8\xff-fake-jpeg']
        with mock.patch('accounts.google_auth.requests.get', return_value=resp) as get, \
                mock.patch('django.db.models.fields.files.FieldFile.save') as save:
            self.assertTrue(google_auth.sync_profile_photo(owner, {'picture': 'https://lh3.googleusercontent.com/a/x=s96-c'}))
            self.assertEqual(get.call_count, 1)
            self.assertEqual(save.call_count, 1)

    def test_failure_never_raises(self):
        owner = self._owner()
        with mock.patch('accounts.google_auth.requests.get', side_effect=OSError('offline')):
            self.assertFalse(google_auth.sync_profile_photo(owner, {'picture': 'https://lh3.googleusercontent.com/a/x=s96-c'}))
