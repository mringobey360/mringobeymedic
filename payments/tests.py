"""Run with:  python manage.py test payments
Pesapal is never called: network functions are patched."""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from . import pesapal_client
from .models import PesapalSettings, PesapalTransaction


class InterpretStatusTests(SimpleTestCase):
    S = PesapalTransaction.Status

    def test_codes(self):
        self.assertEqual(pesapal_client.interpret_status({'status_code': 1}), self.S.COMPLETED)
        self.assertEqual(pesapal_client.interpret_status({'status_code': '2'}), self.S.FAILED)
        self.assertEqual(pesapal_client.interpret_status({'status_code': 3}), self.S.REVERSED)

    def test_unpaid_invalid_is_not_final_inside_grace_window(self):
        young = timezone.now() - timedelta(minutes=2)
        old = timezone.now() - timedelta(hours=3)
        self.assertEqual(pesapal_client.interpret_status({'status_code': 0}, created_at=young), self.S.SUBMITTED)
        self.assertEqual(pesapal_client.interpret_status({'status_code': 0}, created_at=old), self.S.INVALID)

    def test_garbage_payload(self):
        self.assertEqual(pesapal_client.interpret_status(None), self.S.SUBMITTED)
        self.assertEqual(pesapal_client.interpret_status({'status_code': 'x'}), self.S.SUBMITTED)

    def test_expiry_parse_seven_digit_fraction(self):
        self.assertIsNotNone(pesapal_client._parse_expiry('2026-10-05T12:29:30.5177702Z'))


class SettingsResetTests(TestCase):
    def test_changing_environment_clears_token_and_ipn(self):
        s = PesapalSettings.load()
        s.consumer_key, s.consumer_secret = 'k', 'x'
        s.save()
        s.ipn_id, s.ipn_url, s.cached_token = 'ipn1', 'https://a/ipn', 'tok'
        s.save(update_fields=['ipn_id', 'ipn_url', 'cached_token'])
        s = PesapalSettings.load()
        s.environment = PesapalSettings.Environment.LIVE
        s.save()
        s = PesapalSettings.load()
        self.assertEqual((s.ipn_id, s.cached_token), ('', ''))


class VerificationTests(TestCase):
    def _txn(self, amount='5000'):
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(PesapalSettings)
        return PesapalTransaction.objects.create(
            purpose='BRANCH_ADDON', content_type=ct, object_id=1, amount=Decimal(amount),
            order_tracking_id='abc', status='SUBMITTED')

    def test_underpayment_is_not_activated(self):
        from . import services
        txn = self._txn('5000')
        data = {'status_code': 1, 'amount': 100, 'currency': 'TZS', 'payment_status_description': 'Completed'}
        with mock.patch.object(pesapal_client, 'get_transaction_status', return_value=data), \
             mock.patch.object(services, '_activate') as act:
            services.refresh_status(txn)
        txn.refresh_from_db()
        self.assertEqual(txn.status, 'INVALID')
        act.assert_not_called()

    def test_correct_payment_activates_once(self):
        from . import services
        txn = self._txn('5000')
        data = {'status_code': 1, 'amount': 5000, 'currency': 'TZS', 'payment_status_description': 'Completed'}
        with mock.patch.object(pesapal_client, 'get_transaction_status', return_value=data), \
             mock.patch.object(services, '_activate') as act:
            services.refresh_status(txn)
        txn.refresh_from_db()
        self.assertEqual(txn.status, 'COMPLETED')
        act.assert_called_once()

    def test_delayed_payment_moves_from_pending_to_completed_and_activates(self):
        from . import services
        txn = self._txn('5000')
        pending = {'status_code': 0, 'amount': 0, 'currency': 'TZS', 'payment_status_description': 'Pending'}
        paid = {'status_code': 1, 'amount': 5000, 'currency': 'TZS', 'payment_status_description': 'Completed'}
        with mock.patch.object(pesapal_client, 'get_transaction_status', side_effect=[pending, paid]), \
             mock.patch.object(services, '_activate') as act:
            services.refresh_status(txn)
            txn.refresh_from_db()
            self.assertNotEqual(txn.status, 'COMPLETED')
            services.refresh_status(txn)
        txn.refresh_from_db()
        self.assertEqual(txn.status, 'COMPLETED')
        act.assert_called_once()

    def test_verified_paid_but_failed_activation_can_be_retried_without_rechecking_gateway(self):
        from . import services
        txn = self._txn('5000')
        txn.status = PesapalTransaction.Status.COMPLETED
        txn.is_processed = False
        txn.save(update_fields=['status', 'is_processed'])
        with mock.patch.object(pesapal_client, 'get_transaction_status') as gateway, \
             mock.patch.object(services, '_activate', side_effect=[RuntimeError('temporary activation error'), None]) as act:
            with self.assertRaises(RuntimeError):
                services.refresh_status(txn)
            services.refresh_status(txn)
        gateway.assert_not_called()
        self.assertEqual(act.call_count, 2)


# =====================================================================
# ABLiner mobile-money flow (network picker, prompts, webhook, failover)
# ABLiner is never called: network functions are patched.
# =====================================================================
import hashlib
import hmac
import json
import time

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from . import abliner_client, networks, services
from .models import PaymentEvent, PaymentGatewaySettings


class NetworkHelperTests(SimpleTestCase):
    def test_normalize_accepts_common_formats(self):
        for raw in ('0712345678', '0712 345 678', '+255 712 345 678', '255712345678', '712345678'):
            self.assertEqual(networks.normalize_msisdn(raw), '255712345678', raw)

    def test_normalize_rejects_bad_numbers(self):
        for raw in ('', None, '071234567', '0512345678', '255 7123456789'):
            self.assertIsNone(networks.normalize_msisdn(raw), raw)

    def test_detect_network_by_prefix(self):
        self.assertEqual(networks.detect_network('255744123456')['key'], 'vodacom')
        self.assertEqual(networks.detect_network('255689123456')['key'], 'airtel')
        self.assertEqual(networks.detect_network('255712345678')['key'], 'yas')
        self.assertEqual(networks.detect_network('255622123456')['key'], 'halotel')
        self.assertIsNone(networks.detect_network('255790000000'))

    def test_masked_number_hides_the_middle(self):
        self.assertEqual(networks.mask_msisdn('255712345678'), '0712 ••• 678')


class PickVerdictTests(SimpleTestCase):
    def test_completed_beats_everything(self):
        row, verdict = services._abliner_pick([{'status': 'failed'}, {'status': 'completed'}])
        self.assertEqual(verdict, 'COMPLETED')

    def test_live_prompt_beats_an_expired_one(self):
        row, verdict = services._abliner_pick([{'status': 'failed'}, {'status': 'pending'}])
        self.assertEqual(verdict, 'SUBMITTED')

    def test_failed_only_when_every_prompt_failed(self):
        row, verdict = services._abliner_pick([{'status': 'failed'}, {'status': 'failed'}])
        self.assertEqual(verdict, 'FAILED')
        self.assertIsNone(services._abliner_pick([]))


class AbllinerFlowBase(TestCase):
    def setUp(self):
        cfg = PaymentGatewaySettings.load()
        cfg.is_enabled, cfg.active_gateway = True, 'ABLINER'
        cfg.abliner_enabled, cfg.abliner_api_key, cfg.abliner_webhook_secret = True, 'key', 'whsec_test'
        cfg.save()
        ps = PesapalSettings.load()
        ps.site_base_url = 'https://pos.example.com'
        ps.save()

    def _txn(self, status='PENDING', tracking='', phone=''):
        ct = ContentType.objects.get_for_model(PesapalSettings)
        return PesapalTransaction.objects.create(
            purpose='BRANCH_ADDON', content_type=ct, object_id=1, amount=Decimal('5000'),
            gateway='ABLINER', status=status, order_tracking_id=tracking, payer_phone=phone)

    def _signed_post(self, payload, event_id='evt_1'):
        body = json.dumps(payload)
        ts = str(int(time.time()))
        sig = hmac.new(b'whsec_test', ts.encode() + b'.' + body.encode(), hashlib.sha256).hexdigest()
        return self.client.post(
            reverse('payments:abliner_webhook'), data=body, content_type='application/json',
            HTTP_X_WEBHOOK_SIGNATURE=sig, HTTP_X_WEBHOOK_TIMESTAMP=ts, HTTP_X_WEBHOOK_ID=event_id)


class GatewayChoiceTests(AbllinerFlowBase):
    def test_unusable_gateway_falls_back_to_the_working_one(self):
        cfg = PaymentGatewaySettings.load()
        cfg.abliner_api_key = ''
        cfg.save()
        ps = PesapalSettings.load()
        ps.is_enabled, ps.consumer_key, ps.consumer_secret = True, 'ck', 'cs'
        ps.save()
        self.assertEqual(services.resolve_gateway(), 'PESAPAL')

    def test_abliner_pending_payment_is_resumable(self):
        txn = self._txn()
        found = services.find_resumable_transaction(
            'BRANCH_ADDON', PesapalSettings.load(), gateway='ABLINER')
        self.assertEqual(found.pk, txn.pk)

    def test_abliner_checkout_does_not_resume_old_pesapal_order(self):
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(PesapalSettings)
        PesapalTransaction.objects.create(
            purpose='BRANCH_ADDON', content_type=ct, object_id=1,
            amount=Decimal('5000'), gateway='PESAPAL', status='SUBMITTED',
            order_tracking_id='pesapal-track', redirect_url='https://pay.pesapal.test/order',
        )
        abliner_txn = self._txn()
        found = services.find_resumable_transaction(
            'BRANCH_ADDON', PesapalSettings.load(), gateway='ABLINER')
        self.assertEqual(found.pk, abliner_txn.pk)


class SubmitPromptTests(AbllinerFlowBase):
    def test_prompt_uses_normalised_number_and_keeps_network_label(self):
        txn = self._txn()
        with mock.patch.object(abliner_client, 'create_mobile_deposit',
                               return_value={'data': {'id': 'abl-1', 'message': 'sent'}}) as call:
            services.submit_to_abliner(txn, phone='0744 123 456', network_key='vodacom')
        self.assertEqual(call.call_args.kwargs['phone'], '255744123456')
        self.assertEqual(call.call_args.kwargs['idempotency_key'], str(txn.merchant_reference))
        txn.refresh_from_db()
        self.assertEqual((txn.status, txn.payment_method, txn.order_tracking_id), ('SUBMITTED', 'M-Pesa', 'abl-1'))

    def test_timeout_then_existing_deposit_is_adopted_not_failed(self):
        """V50_34: first call timed out but ABLiner created the prompt -> payment becomes SUBMITTED."""
        txn = self._txn()
        err = abliner_client.ABLinerError('x', 'timeout', code='network_error', retryable=True)
        with mock.patch.object(abliner_client, 'create_mobile_deposit', side_effect=err), \
                mock.patch.object(abliner_client, 'get_transactions', return_value=[{'id': 'abl-9', 'status': 'pending'}]):
            services.submit_to_abliner(txn, phone='0744 123 456', network_key='vodacom')
        txn.refresh_from_db()
        self.assertEqual((txn.status, txn.order_tracking_id), ('SUBMITTED', 'abl-9'))

    def test_request_in_progress_without_deposit_gives_wait_message(self):
        txn = self._txn()
        err = abliner_client.ABLinerError('x', '409', code='request_in_progress', retryable=True)
        with mock.patch.object(abliner_client, 'create_mobile_deposit', side_effect=err), \
                mock.patch.object(abliner_client, 'get_transactions', return_value=[]), \
                mock.patch.object(services.time, 'sleep'):
            with self.assertRaises(abliner_client.ABLinerError) as ctx:
                services.submit_to_abliner(txn, phone='0744 123 456', network_key='vodacom')
        self.assertIn('do not pay again', ctx.exception.detail)
        txn.refresh_from_db()
        self.assertEqual(txn.status, 'PENDING')

    def test_invalid_number_never_reaches_abliner(self):
        txn = self._txn()
        with mock.patch.object(abliner_client, 'create_mobile_deposit') as call:
            with self.assertRaises(abliner_client.ABLinerError):
                services.submit_to_abliner(txn, phone='12345')
        call.assert_not_called()

    def test_resend_is_throttled_then_uses_a_new_idempotency_key(self):
        txn = self._txn()
        with mock.patch.object(abliner_client, 'create_mobile_deposit', return_value={'data': {'id': 'abl-1'}}):
            services.submit_to_abliner(txn, phone='0744123456')
        with mock.patch.object(abliner_client, 'create_mobile_deposit') as call:
            with self.assertRaises(abliner_client.ABLinerError):
                services.submit_to_abliner(txn, phone='0744123456')
            call.assert_not_called()
        PaymentEvent.objects.filter(payment=txn).update(created_at=timezone.now() - timedelta(seconds=120))
        with mock.patch.object(abliner_client, 'create_mobile_deposit', return_value={'data': {'id': 'abl-2'}}) as call:
            services.submit_to_abliner(txn, phone='0744123456')
        self.assertEqual(call.call_args.kwargs['idempotency_key'], f'{txn.merchant_reference}-1')


class WebhookTests(AbllinerFlowBase):
    def _payload(self, txn, event='transaction.completed', status='completed', abl_id='abl-1'):
        return {'id': 'evt_1', 'event': event, 'data': {
            'id': abl_id, 'status': status, 'amount': 5000, 'currency': 'TZS', 'method': 'mobile',
            'customer_reference': str(txn.merchant_reference), 'reference': 'ABL1', 'provider_reference': 'PRV1'}}

    def test_completed_event_activates_once_and_duplicate_is_ignored(self):
        txn = self._txn('SUBMITTED', 'abl-1')
        with mock.patch.object(services, '_activate') as act:
            self.assertEqual(self._signed_post(self._payload(txn)).status_code, 200)
            self.assertTrue(self._signed_post(self._payload(txn)).json().get('duplicate'))
        txn.refresh_from_db()
        self.assertEqual(txn.status, 'COMPLETED')
        act.assert_called_once()

    def test_a_crash_is_retried_not_swallowed_as_duplicate(self):
        txn = self._txn('SUBMITTED', 'abl-1')
        real = services._apply_provider_status
        calls = {'n': 0}

        def flaky(*a, **kw):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('boom')
            return real(*a, **kw)

        with mock.patch.object(services, '_apply_provider_status', side_effect=flaky), \
             mock.patch.object(services, '_activate') as act:
            self.assertEqual(self._signed_post(self._payload(txn)).status_code, 500)
            self.assertEqual(self._signed_post(self._payload(txn)).status_code, 200)
        txn.refresh_from_db()
        self.assertEqual(txn.status, 'COMPLETED')
        act.assert_called_once()

    def test_older_prompt_expiring_does_not_fail_a_live_payment(self):
        txn = self._txn('SUBMITTED', 'abl-2')           # newest prompt is abl-2
        payload = self._payload(txn, event='transaction.failed', status='failed', abl_id='abl-1')
        self.assertEqual(self._signed_post(payload).status_code, 200)
        txn.refresh_from_db()
        self.assertEqual(txn.status, 'SUBMITTED')

    def test_bad_signature_is_rejected(self):
        txn = self._txn('SUBMITTED', 'abl-1')
        r = self.client.post(reverse('payments:abliner_webhook'), data=json.dumps(self._payload(txn)),
                             content_type='application/json', HTTP_X_WEBHOOK_SIGNATURE='nope',
                             HTTP_X_WEBHOOK_TIMESTAMP=str(int(time.time())))
        self.assertEqual(r.status_code, 401)


class ViewImportRegressionTests(SimpleTestCase):
    """V50_34: views.py called resolve_gateway() without importing it -> NameError on every online payment start."""

    def test_names_used_by_begin_payment_are_importable(self):
        from . import views
        for name in ('resolve_gateway', 'create_transaction', 'find_resumable_transaction',
                     'submit_to_abliner', 'submit_to_pesapal', 'switch_to_pesapal'):
            self.assertTrue(callable(getattr(views, name, None)), f'payments.views is missing {name}')


class CheckoutCopyTests(SimpleTestCase):
    """V50_35: customer-facing failure copy and activation summary never raise."""

    def _txn(self, status, desc=''):
        return PesapalTransaction(status=status, status_description=desc, purpose='SUBSCRIPTION_RENEWAL')

    def test_failure_copy_is_specific_and_english_only(self):
        from . import views
        S = PesapalTransaction.Status
        self.assertIn('expired', views._failure_copy(self._txn(S.FAILED, 'expired'))['title'].lower())
        self.assertIn('cancel', views._failure_copy(self._txn(S.CANCELLED))['title'].lower())
        generic = views._failure_copy(self._txn(S.FAILED, 'failed'))
        self.assertTrue(generic['body'])
        self.assertNotIn('body_sw', generic)

    def test_failure_copy_empty_for_non_failed(self):
        from . import views
        self.assertEqual(views._failure_copy(self._txn(PesapalTransaction.Status.SUBMITTED)), {})
        self.assertEqual(views._activation_summary(self._txn(PesapalTransaction.Status.SUBMITTED)), {})
