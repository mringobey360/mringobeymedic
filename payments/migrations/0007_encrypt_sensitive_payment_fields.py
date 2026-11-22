"""V117.11 - store payer details and gateway secrets AES-256-GCM encrypted.

1. Columns become TEXT (ciphertext is longer than the old varchar limits).
2. Existing rows are encrypted in place. Reads tolerate legacy plaintext, so the upgrade
   is safe even if this step is interrupted; re-run `manage.py encrypt_payment_data`.
"""
from django.db import migrations

import core.encrypted_fields

ENC = core.encrypted_fields.EncryptedTextField

FIELDS = {
    'paymentgatewaysettings': ['abliner_api_key', 'abliner_webhook_secret'],
    'pesapalsettings': ['consumer_key', 'consumer_secret', 'cached_token'],
    'pesapaltransaction': ['payer_name', 'payer_email', 'payer_phone', 'payment_account', 'raw_response'],
}


def encrypt_existing_rows(apps, schema_editor):
    from payments.data_encryption import encrypt_model_rows
    for model_name, names in FIELDS.items():
        encrypt_model_rows(apps.get_model('payments', model_name), names)


class Migration(migrations.Migration):
    dependencies = [('payments', '0006_multi_gateway_control')]

    operations = [
        migrations.AlterField('paymentgatewaysettings', 'abliner_api_key', ENC(blank=True)),
        migrations.AlterField('paymentgatewaysettings', 'abliner_webhook_secret', ENC(blank=True)),
        migrations.AlterField('pesapalsettings', 'consumer_key', ENC(blank=True, help_text='Consumer Key from Pesapal Developer Settings > API settings.')),
        migrations.AlterField('pesapalsettings', 'consumer_secret', ENC(blank=True, help_text='Consumer Secret from Pesapal Developer Settings > API settings. Keep this confidential.')),
        migrations.AlterField('pesapalsettings', 'cached_token', ENC(blank=True, editable=False)),
        migrations.AlterField('pesapaltransaction', 'payer_name', ENC(blank=True)),
        migrations.AlterField('pesapaltransaction', 'payer_email', ENC(blank=True)),
        migrations.AlterField('pesapaltransaction', 'payer_phone', ENC(blank=True, help_text='Stored AES-256-GCM encrypted.')),
        migrations.AlterField('pesapaltransaction', 'payment_account', ENC(blank=True, editable=False, help_text='Masked payment account returned by Pesapal (stored encrypted).')),
        migrations.AlterField('pesapaltransaction', 'raw_response', ENC(blank=True, help_text='Latest raw gateway response (redacted, stored encrypted) — kept for support/debugging.')),
        migrations.RunPython(encrypt_existing_rows, migrations.RunPython.noop),
    ]
