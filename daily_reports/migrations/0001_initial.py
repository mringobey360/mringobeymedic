from django.db import migrations, models
import django.db.models.deletion
import uuid
from datetime import time

class Migration(migrations.Migration):
    initial = True
    dependencies = [('accounts', '0011_allow_multiple_assistants_per_branch'), ('hq', '0005_subscription_payment_receipt')]
    operations = [
        migrations.CreateModel(
            name='DailyReportSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=True)),
                ('delivery_time', models.TimeField(default=time(4, 0), help_text='Africa/Dar_es_Salaam local time.')),
                ('phone_number', models.CharField(blank=True, help_text='Optional override. Leave blank to use the manager account phone number.', max_length=20)),
                ('include_pdf_link', models.BooleanField(default=True)),
                ('report_retention_days', models.PositiveSmallIntegerField(default=30)),
                ('last_sent_for_date', models.DateField(blank=True, editable=False, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('owner', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='daily_report_settings', to='accounts.user')),
            ],
            options={'verbose_name': 'Daily Sales Intelligence Setting', 'verbose_name_plural': 'Daily Sales Intelligence Settings'},
        ),
        migrations.CreateModel(
            name='DailySalesReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('report_date', models.DateField()),
                ('access_token', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('expires_at', models.DateTimeField()),
                ('generated_at', models.DateTimeField(auto_now_add=True)),
                ('sms_sent_at', models.DateTimeField(blank=True, null=True)),
                ('sms_recipient', models.CharField(blank=True, max_length=20)),
                ('sms_message', models.TextField(blank=True)),
                ('sms_error', models.TextField(blank=True)),
                ('snapshot', models.JSONField(default=dict)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='daily_sales_reports', to='accounts.user')),
            ],
            options={'ordering': ['-report_date', '-generated_at'], 'indexes': [models.Index(fields=['owner', '-report_date'], name='daily_report_owner_date_idx'), models.Index(fields=['access_token'], name='daily_report_token_idx')], 'constraints': [models.UniqueConstraint(fields=('owner', 'report_date'), name='unique_daily_sales_report_owner_date')]},
        ),
        migrations.CreateModel(
            name='DailyReportDeliveryLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('report_date', models.DateField()),
                ('scheduled_time', models.TimeField()),
                ('recipient', models.CharField(blank=True, max_length=20)),
                ('status', models.CharField(choices=[('SENT','Sent'),('FAILED','Failed'),('SKIPPED','Skipped'),('PROCESSING','Processing')], max_length=10)),
                ('message', models.TextField(blank=True)),
                ('error_message', models.TextField(blank=True)),
                ('attempted_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='daily_report_delivery_logs', to='accounts.user')),
                ('report', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='delivery_logs', to='daily_reports.dailysalesreport')),
            ],
            options={'ordering': ['-attempted_at'], 'indexes': [models.Index(fields=['report_date', 'status'], name='daily_delivery_date_status_idx')], 'constraints': [models.UniqueConstraint(fields=('owner', 'report_date'), name='unique_daily_report_delivery_owner_date')]},
        ),
    ]
