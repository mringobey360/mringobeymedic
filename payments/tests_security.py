"""Security-hardening tests (V117.10).

Run with:  python manage.py test payments.tests_security
No network is used; the gateway signature check is patched where a webhook is posted.
"""
import io
import json
from decimal import Decimal
from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import User
from core import ratelimit
from customer_registry.models import CustomerContact, CustomerDataAccessLog
from customer_registry.templatetags.registry_privacy import phone_for
from pharmacies.models import Pharmacy

from . import abliner_client, services
from .models import PaymentEvent, PesapalSettings, PesapalTransaction
from .privacy import mask_phone, redact_payload, redact_text

PASSWORD = 'S3cret-pass!'


class PrivacyHelperTests(SimpleTestCase):
    def test_mask_phone_formats(self):
        for raw in ('255712345678', '+255712345678', '0712345678', '+255 712 345 678'):
            self.assertEqual(mask_phone(raw), '0712 *** 678', raw)

    def test_mask_is_idempotent(self):
        once = mask_phone('0712345678')
        self.assertEqual(mask_phone(once), once)

    def test_redact_payload_masks_keys_and_embedded_numbers(self):
        data = {'data': {'phone': '255712345678', 'amount': 1000, 'id': 'abc',
                         'note': 'sent to 0755123456 ok', 'rows': [{'msisdn': '255655000111'}]}}
        clean = redact_payload(data)
        blob = json.dumps(clean)
        for digits in ('712345678', '755123456', '655000111'):
            self.assertNotIn(digits, blob)
        self.assertEqual(clean['data']['amount'], 1000)       # non-phone values untouched
        self.assertEqual(clean['data']['id'], 'abc')

    def test_redact_text_leaves_timestamps_and_ids_alone(self):
        self.assertEqual(redact_text('ts 1760000000123 id 9f1c'), 'ts 1760000000123 id 9f1c')
        self.assertNotIn('712345678', redact_text('call 255712345678 now'))


class RateLimitTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_blocks_after_limit_and_reports_wait(self):
        for _ in range(3):
            self.assertTrue(ratelimit.hit('t', 'x', limit=3, window=60)[0])
        allowed, retry = ratelimit.hit('t', 'x', limit=3, window=60)
        self.assertFalse(allowed)
        self.assertGreater(retry, 0)

    def test_identities_are_independent_and_reset_works(self):
        for _ in range(4):
            ratelimit.hit('t', 'a', limit=3, window=60)
        self.assertTrue(ratelimit.hit('t', 'b', limit=3, window=60)[0])
        ratelimit.reset('t', 'a')
        self.assertTrue(ratelimit.hit('t', 'a', limit=3, window=60)[0])

    def test_fails_open_when_cache_is_down(self):
        with mock.patch('core.ratelimit.cache.get', side_effect=RuntimeError('redis down')):
            self.assertEqual(ratelimit.hit('t', 'x', limit=1, window=60), (True, 0))
            self.assertEqual(ratelimit.peek('t', 'x', limit=1), (False, 0))


class LoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='cashier', password=PASSWORD, first_name='Amina', role=User.Role.EMPLOYEE, is_superuser=True)

    def _login(self, password):
        return self.client.post(reverse('accounts:login'), {'username': 'cashier', 'password': password})

    def test_account_locks_after_repeated_wrong_passwords_even_for_correct_one(self):
        from accounts.views import LOGIN_MAX_FAILS_PER_USER
        for _ in range(LOGIN_MAX_FAILS_PER_USER):
            self.assertEqual(self._login('wrong-password').status_code, 200)
        resp = self._login(PASSWORD)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(resp, 'Too many failed sign-in attempts')

    def test_successful_login_clears_the_failure_counter(self):
        for _ in range(3):
            self._login('wrong-password')
        self.assertEqual(self._login(PASSWORD).status_code, 302)
        self.client.logout()
        for _ in range(3):
            self._login('wrong-password')
        self.assertEqual(self._login(PASSWORD).status_code, 302)


class WebhookRedactionTests(TestCase):
    def setUp(self):
        ct = ContentType.objects.get_for_model(PesapalSettings)
        self.txn = PesapalTransaction.objects.create(
            purpose='BRANCH_ADDON', content_type=ct, object_id=1, amount=Decimal('5000'),
            gateway='ABLINER', order_tracking_id='abc', status='SUBMITTED')

    def test_stored_event_payload_never_contains_the_full_number(self):
        body = {'event': 'transaction.completed', 'id': 'evt-1', 'data': {
            'customer_reference': str(self.txn.merchant_reference), 'status': 'completed',
            'phone': '255712345678', 'amount': 5000, 'id': 'abc'}}
        with mock.patch.object(abliner_client, 'verify_webhook', return_value=True), \
             mock.patch.object(services, '_apply_provider_status'):
            self.client.post(reverse('payments:abliner_webhook'), data=json.dumps(body),
                             content_type='application/json', HTTP_X_WEBHOOK_ID='evt-1')
        event = PaymentEvent.objects.get(provider_event_id='evt-1')
        self.assertNotIn('712345678', json.dumps(event.payload))

    def test_unsigned_webhook_is_rejected(self):
        resp = self.client.post(reverse('payments:abliner_webhook'), data='{}', content_type='application/json')
        self.assertEqual(resp.status_code, 401)


class PhonePromptThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        ct = ContentType.objects.get_for_model(PesapalSettings)
        self.txn = PesapalTransaction.objects.create(
            purpose='BRANCH_ADDON', content_type=ct, object_id=1, amount=Decimal('5000'),
            gateway='ABLINER', status='PENDING')

    def test_number_cannot_be_spammed_with_prompts_across_payments(self):
        for _ in range(services.PHONE_PROMPT_LIMIT):
            ratelimit.hit('abliner-phone', '255712345678', limit=services.PHONE_PROMPT_LIMIT, window=3600)
        with self.assertRaises(abliner_client.ABLinerError) as ctx:
            services.submit_to_abliner(self.txn, phone='0712345678')
        self.assertEqual(ctx.exception.code, 'phone_rate_limited')


class ScrubCommandTests(TestCase):
    def test_scrub_masks_payloads_and_old_finished_payer_phones(self):
        ct = ContentType.objects.get_for_model(PesapalSettings)
        txn = PesapalTransaction.objects.create(
            purpose='BRANCH_ADDON', content_type=ct, object_id=1, amount=Decimal('5000'),
            gateway='ABLINER', status='FAILED', payer_phone='255712345678',
            raw_response='{"phone": "255712345678"}')
        PesapalTransaction.objects.filter(pk=txn.pk).update(created_at='2020-01-01T00:00:00Z')
        PaymentEvent.objects.create(payment=txn, gateway='ABLINER', event_type='x', payload={'phone': '255712345678'})
        call_command('scrub_payment_pii', '--dry-run', stdout=io.StringIO())
        txn.refresh_from_db()
        self.assertEqual(txn.payer_phone, '255712345678')               # dry run changes nothing
        call_command('scrub_payment_pii', stdout=io.StringIO())
        txn.refresh_from_db()
        self.assertEqual(txn.payer_phone, '0712 *** 678')
        self.assertNotIn('712345678', txn.raw_response)
        self.assertNotIn('712345678', json.dumps(PaymentEvent.objects.get(payment=txn).payload))


class CustomerRegistryAccessTests(TestCase):
    def setUp(self):
        self.pharmacy = Pharmacy.objects.create(name='Test Branch')
        self.manager = User.objects.create_user(username='boss', password=PASSWORD, role=User.Role.ADMIN, is_superuser=True)
        self.employee = User.objects.create_user(username='cashier', password=PASSWORD, role=User.Role.EMPLOYEE)
        CustomerContact.objects.create(pharmacy=self.pharmacy, name='Asha', phone='+255712345678')

    def _export(self, user):
        from customer_registry import views
        request = RequestFactory().get('/customers/export/')
        request.user = user
        request.pharmacy = self.pharmacy
        return views.customer_export(request)

    def test_employee_cannot_export_and_attempt_is_logged(self):
        with self.assertRaises(PermissionDenied):
            self._export(self.employee)
        self.assertTrue(CustomerDataAccessLog.objects.filter(action='EXPORT_DENIED', username='cashier').exists())

    def test_manager_can_export_and_it_is_logged(self):
        resp = self._export(self.manager)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'+255712345678', resp.content)
        self.assertTrue(CustomerDataAccessLog.objects.filter(action='EXPORT', username='boss', record_count=1).exists())

    def test_phone_filter_masks_for_employees_only(self):
        self.assertEqual(phone_for('+255712345678', self.manager), '+255712345678')
        self.assertEqual(phone_for('+255712345678', self.employee), '0712 *** 678')
