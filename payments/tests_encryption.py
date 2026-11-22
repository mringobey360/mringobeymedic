"""Encryption tests (V117.11).  Run:  python manage.py test payments.tests_encryption
No network is used."""
import logging

from django.core.exceptions import FieldError
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings

from core import crypto
from core.logfilters import RedactSensitiveFilter

from .models import PaymentGatewaySettings, PesapalSettings, PesapalTransaction
from .privacy import luhn_valid, redact_payload, redact_text
from .security import payment_csp

CTX = 'payments.pesapaltransaction.payer_phone'
VISA = '4111 1111 1111 1111'   # public test number


def _raw(table, column, pk):
    with connection.cursor() as cur:
        cur.execute(f'SELECT {column} FROM {table} WHERE id=%s', [pk])
        return cur.fetchone()[0]


class CryptoTests(SimpleTestCase):
    def setUp(self):
        crypto.reset_keyring()

    def test_roundtrip_and_randomised_ciphertext(self):
        a, b = crypto.encrypt('0712345678', CTX), crypto.encrypt('0712345678', CTX)
        self.assertNotEqual(a, b)
        self.assertNotIn('0712345678', a)
        self.assertEqual(crypto.decrypt(a, CTX), '0712345678')

    def test_bound_to_its_field(self):
        token = crypto.encrypt('0712345678', CTX)
        with self.assertRaises(crypto.DecryptionError):
            crypto.decrypt(token, 'payments.pesapaltransaction.payer_name')

    def test_tampering_is_detected(self):
        token = crypto.encrypt('secret', CTX)
        flipped = token[:-2] + ('AA' if not token.endswith('AA') else 'BB')
        with self.assertRaises(crypto.DecryptionError):
            crypto.decrypt(flipped, CTX)

    def test_legacy_plaintext_and_empty_values(self):
        self.assertEqual(crypto.decrypt('0712345678', CTX), '0712345678')
        self.assertEqual(crypto.encrypt('', CTX), '')
        self.assertIsNone(crypto.encrypt(None, CTX))

    def test_key_rotation(self):
        old = crypto.encrypt('abc', CTX)                      # derived key
        new_key = crypto.generate_key()
        with override_settings(FIELD_ENCRYPTION_KEYS=[new_key]):
            crypto.reset_keyring()
            self.assertEqual(crypto.decrypt(old, CTX), 'abc')  # old rows still readable
            self.assertTrue(crypto.needs_reencryption(old))
            fresh = crypto.encrypt('abc', CTX)
            self.assertFalse(crypto.needs_reencryption(fresh))
        crypto.reset_keyring()

    def test_bad_key_is_rejected(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=['short']):
            crypto.reset_keyring()
            with self.assertRaises(crypto.EncryptionConfigError):
                crypto.encrypt('x', CTX)
        crypto.reset_keyring()


class FieldTests(TestCase):
    def _txn(self, **kw):
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(PesapalSettings)
        return PesapalTransaction.objects.create(
            purpose='SUBSCRIPTION', content_type=ct, object_id=1, amount=15000, **kw)

    def test_database_holds_only_ciphertext(self):
        t = self._txn(payer_phone='0712345678', payer_name='Asha Juma', payer_email='asha@example.com')
        for column, plain in (('payer_phone', '0712345678'), ('payer_name', 'Asha Juma'), ('payer_email', 'asha@example.com')):
            stored = _raw('payments_pesapaltransaction', column, t.pk)
            self.assertTrue(stored.startswith('enc1$'), column)
            self.assertNotIn(plain, stored)
        t.refresh_from_db()
        self.assertEqual((t.payer_phone, t.payer_name, t.payer_email), ('0712345678', 'Asha Juma', 'asha@example.com'))

    def test_gateway_secrets_are_encrypted(self):
        cfg = PaymentGatewaySettings.load()
        cfg.abliner_api_key, cfg.abliner_webhook_secret = 'KEY-123', 'WHSEC-456'
        cfg.save()
        self.assertTrue(_raw('payments_paymentgatewaysettings', 'abliner_api_key', 1).startswith('enc1$'))
        self.assertEqual(PaymentGatewaySettings.load().abliner_webhook_secret, 'WHSEC-456')
        self.assertTrue(PaymentGatewaySettings.load().abliner_configured)

    def test_empty_stays_empty_and_isnull_exact_empty_lookups_work(self):
        t = self._txn()
        self.assertEqual(_raw('payments_pesapaltransaction', 'payer_phone', t.pk), '')
        self.assertTrue(PesapalTransaction.objects.filter(payer_phone='').exists())
        self.assertFalse(PesapalTransaction.objects.exclude(payer_phone='').exists())

    def test_searching_by_value_is_refused(self):
        with self.assertRaises(FieldError):
            list(PesapalTransaction.objects.filter(payer_phone='0712345678'))
        with self.assertRaises(FieldError):
            list(PesapalTransaction.objects.filter(payer_phone__icontains='0712'))

    def test_unreadable_value_is_not_destroyed_on_save(self):
        t = self._txn(payer_phone='0712345678')
        cipher = _raw('payments_pesapaltransaction', 'payer_phone', t.pk)
        other = crypto.generate_key()
        with override_settings(FIELD_ENCRYPTION_KEYS=[other], SECRET_KEY='z' * 60):
            crypto.reset_keyring()
            broken = PesapalTransaction.objects.get(pk=t.pk)
            self.assertEqual(broken.payer_phone, '')           # cannot read...
            broken.payer_name = 'x'
            broken.save()                                       # ...but must not overwrite
        crypto.reset_keyring()
        self.assertEqual(_raw('payments_pesapaltransaction', 'payer_phone', t.pk), cipher)
        self.assertEqual(PesapalTransaction.objects.get(pk=t.pk).payer_phone, '0712345678')

    def test_encrypt_payment_data_command_encrypts_legacy_rows(self):
        t = self._txn(payer_phone='0712345678')
        with connection.cursor() as cur:
            cur.execute("UPDATE payments_pesapaltransaction SET payer_phone='0712345678' WHERE id=%s", [t.pk])
        call_command('encrypt_payment_data', stdout=open('/dev/null', 'w'))
        self.assertTrue(_raw('payments_pesapaltransaction', 'payer_phone', t.pk).startswith('enc1$'))
        self.assertEqual(PesapalTransaction.objects.get(pk=t.pk).payer_phone, '0712345678')


class CardDataTests(SimpleTestCase):
    def test_luhn(self):
        self.assertTrue(luhn_valid('4111111111111111'))
        self.assertFalse(luhn_valid('4111111111111112'))

    def test_card_numbers_are_stripped_from_text(self):
        out = redact_text(f'{{"note": "customer typed {VISA} by mistake"}}')
        self.assertNotIn('4111', out)
        self.assertIn('[CARD REDACTED]', out)

    def test_timestamps_and_ids_are_left_alone(self):
        self.assertEqual(redact_text('order 1700000000123 ref 12345678901234'), 'order 1700000000123 ref 12345678901234')

    def test_secret_keys_are_never_kept(self):
        clean = redact_payload({'card_number': VISA, 'cvv': '123', 'PIN': '1234', 'status': 'ok', 'nested': {'password': 'x'}})
        self.assertEqual((clean['card_number'], clean['cvv'], clean['PIN']), ('[REDACTED]',) * 3)
        self.assertEqual(clean['nested']['password'], '[REDACTED]')
        self.assertEqual(clean['status'], 'ok')


class LogFilterTests(SimpleTestCase):
    def test_phone_and_card_never_reach_logs(self):
        record = logging.LogRecord('t', logging.INFO, __file__, 1, 'pay %s card %s', ('0712345678', VISA), None)
        RedactSensitiveFilter().filter(record)
        text = record.getMessage()
        self.assertNotIn('0712345678', text)
        self.assertNotIn('4111', text)


class HeaderTests(SimpleTestCase):
    def test_csp_is_restrictive(self):
        csp = payment_csp()
        for part in ("object-src 'none'", "frame-ancestors 'none'", "base-uri 'self'", 'https://*.pesapal.com'):
            self.assertIn(part, csp)
