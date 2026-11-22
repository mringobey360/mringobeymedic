import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='PesapalSettings',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_enabled', models.BooleanField(default=False, help_text='Turn ON to let customers pay instantly online with Pesapal (cards, M-Pesa, Tigo Pesa, Airtel Money, etc). When OFF, only the manual mobile-money (Lipa Namba) flow is shown, exactly as before.')),
                ('environment', models.CharField(choices=[('SANDBOX', 'Sandbox (testing only — no real money moves)'), ('LIVE', 'Live (real customer payments)')], default='SANDBOX', help_text='Use Sandbox to test end-to-end before going Live. Switch to Live only once a test payment has worked.', max_length=10)),
                ('consumer_key', models.CharField(blank=True, help_text='Consumer Key from Pesapal Developer Settings > API settings.', max_length=255)),
                ('consumer_secret', models.CharField(blank=True, help_text='Consumer Secret from Pesapal Developer Settings > API settings. Keep this confidential.', max_length=255)),
                ('ipn_id', models.CharField(blank=True, editable=False, help_text="Auto-registered with Pesapal the first time it's needed. Do not edit manually — use the 'Re-register IPN with Pesapal' admin action instead if it ever needs refreshing.", max_length=100)),
                ('site_base_url', models.URLField(blank=True, help_text="Your site's public HTTPS address, e.g. https://yourapp.up.railway.app (no trailing slash). Used to build the IPN and callback URLs Pesapal calls back on. Leave blank to auto-detect from the request, but setting it explicitly is more reliable behind Railway's proxy.")),
                ('cached_token', models.TextField(blank=True, editable=False)),
                ('cached_token_expires_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Pesapal Settings',
                'verbose_name_plural': 'Pesapal Settings',
            },
        ),
        migrations.CreateModel(
            name='PesapalTransaction',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('merchant_reference', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('order_tracking_id', models.CharField(blank=True, db_index=True, max_length=100)),
                ('purpose', models.CharField(choices=[('SUBSCRIPTION', 'New Subscription'), ('SUBSCRIPTION_RENEWAL', 'Subscription Renewal'), ('BRANCH_ADDON', 'Branch Add-on'), ('SMS_DEBT_PACKAGE', 'Premium Debt SMS Package'), ('SMS_RECEIPT_PACKAGE', 'Premium Receipt SMS Package')], max_length=30)),
                ('object_id', models.PositiveIntegerField()),
                ('payer_name', models.CharField(blank=True, max_length=150)),
                ('payer_email', models.EmailField(blank=True, max_length=254)),
                ('payer_phone', models.CharField(blank=True, max_length=20)),
                ('description', models.CharField(blank=True, max_length=255)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('currency', models.CharField(default='TZS', max_length=6)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('SUBMITTED', 'Awaiting Payment'), ('COMPLETED', 'Completed'), ('FAILED', 'Failed'), ('CANCELLED', 'Cancelled'), ('INVALID', 'Invalid')], default='PENDING', max_length=12)),
                ('redirect_url', models.URLField(blank=True, help_text='The Pesapal checkout page the customer was sent to.')),
                ('confirmation_code', models.CharField(blank=True, max_length=100)),
                ('payment_method', models.CharField(blank=True, help_text='e.g. M-Pesa, Visa, Airtel Money.', max_length=60)),
                ('status_description', models.CharField(blank=True, max_length=255)),
                ('raw_response', models.TextField(blank=True, help_text='Latest raw Pesapal API response — kept for support/debugging.')),
                ('error_message', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('processed_at', models.DateTimeField(blank=True, help_text='When the linked subscription/branch/SMS record was actually activated in our system.', null=True)),
                ('content_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype')),
                ('owner', models.ForeignKey(blank=True, help_text='The logged-in owner this payment belongs to, if any (blank for pre-account signups).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pesapal_transactions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='pesapaltransaction',
            index=models.Index(fields=['content_type', 'object_id'], name='payments_pe_content_1f5f8a_idx'),
        ),
    ]
