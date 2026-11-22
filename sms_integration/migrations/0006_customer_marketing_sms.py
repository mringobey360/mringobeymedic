import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('sms_integration', '0005_announcement_link_tracking'),
    ]

    operations = [
        migrations.AddField(
            model_name='smssettings',
            name='send_customer_marketing',
            field=models.BooleanField(default=True, help_text='Allow paid Customer Marketing SMS campaigns.'),
        ),
        migrations.AlterField(
            model_name='smslog',
            name='category',
            field=models.CharField(
                choices=[
                    ('CREDENTIALS', 'Employee Credentials'), ('OTP', 'Password Reset OTP'),
                    ('DAILY_SUMMARY', 'Daily Summary'), ('LOW_STOCK', 'Low Stock Alert'),
                    ('EXPIRY', 'Expiry Alert'), ('DEBT', 'Debt Reminder'),
                    ('RECEIPT', 'Receipt Download Link'), ('PAYMENT_RECEIPT', 'Subscription Payment Receipt'),
                    ('CUSTOMER_MARKETING', 'Customer Marketing Campaign'),
                    ('ANNOUNCEMENT', 'Platform Announcement'), ('OTHER', 'Other'),
                ], default='OTHER', max_length=20,
            ),
        ),
        migrations.CreateModel(
            name='SMSMarketingPackage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=30, unique=True)),
                ('name', models.CharField(max_length=100)),
                ('price', models.DecimalField(decimal_places=2, max_digits=12)),
                ('sms_credits', models.PositiveIntegerField()),
                ('duration_days', models.PositiveIntegerField(default=30)),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['sort_order', 'price', 'id']},
        ),
        migrations.CreateModel(
            name='SMSMarketingSubscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sms_credits', models.PositiveIntegerField(default=0)),
                ('sms_used', models.PositiveIntegerField(default=0)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('status', models.CharField(choices=[('ACTIVE', 'Active'), ('EXPIRED', 'Expired'), ('CANCELLED', 'Cancelled')], default='ACTIVE', max_length=12)),
                ('amount_paid', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('payment_reference', models.CharField(blank=True, max_length=150)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='marketing_sms_subscriptions', to='accounts.user')),
                ('package', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='subscriptions', to='sms_integration.smsmarketingpackage')),
            ],
            options={'ordering': ['-created_at'], 'indexes': [models.Index(fields=['owner', 'status', 'end_date'], name='sms_integra_owner_s_7d2a9c_idx')]},
        ),
        migrations.CreateModel(
            name='SMSMarketingPurchase',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('payment_reference', models.CharField(blank=True, max_length=150)),
                ('status', models.CharField(choices=[('PENDING', 'Pending Payment Verification'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')], default='PENDING', max_length=12)),
                ('customer_note', models.TextField(blank=True)),
                ('admin_note', models.TextField(blank=True)),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='marketing_sms_purchases', to='accounts.user')),
                ('package', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchases', to='sms_integration.smsmarketingpackage')),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_marketing_sms_purchases', to='accounts.user')),
            ],
            options={'ordering': ['-requested_at']},
        ),
    ]
