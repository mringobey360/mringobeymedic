import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('sms_integration', '0003_payment_receipt_sms_cost_tracking'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AlterField(
            model_name='smslog', name='category',
            field=models.CharField(choices=[
                ('CREDENTIALS', 'Employee Credentials'), ('OTP', 'Password Reset OTP'),
                ('DAILY_SUMMARY', 'Daily Summary'), ('LOW_STOCK', 'Low Stock Alert'),
                ('EXPIRY', 'Expiry Alert'), ('DEBT', 'Debt Reminder'),
                ('RECEIPT', 'Receipt Download Link'), ('PAYMENT_RECEIPT', 'Subscription Payment Receipt'),
                ('ANNOUNCEMENT', 'Platform Announcement'), ('OTHER', 'Other')],
                default='OTHER', max_length=20),
        ),
        migrations.CreateModel(
            name='SMSAnnouncement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(help_text='Internal campaign name (customers never see this).', max_length=120)),
                ('message_type', models.CharField(choices=[
                    ('ANNOUNCEMENT', 'General announcement'), ('SYSTEM_UPDATE', 'System update'),
                    ('MAINTENANCE', 'Scheduled maintenance'), ('BILLING', 'Subscription / billing notice')],
                    default='ANNOUNCEMENT', max_length=20)),
                ('message', models.TextField(help_text='SMS text as composed. {name} is replaced with the customer first name.')),
                ('audience', models.CharField(choices=[
                    ('ACTIVE', 'Customers with an active subscription'), ('ALL', 'All active customer accounts'),
                    ('EXPIRING', 'Subscriptions expiring within 7 days'), ('EXPIRED', 'Customers with an expired subscription'),
                    ('SELECTED', 'Selected customers only')], default='ACTIVE', max_length=10)),
                ('status', models.CharField(choices=[
                    ('QUEUED', 'Queued'), ('SENDING', 'Sending'), ('COMPLETED', 'Completed'),
                    ('PARTIAL', 'Partially delivered'), ('FAILED', 'Failed')], default='QUEUED', max_length=10)),
                ('total_recipients', models.PositiveIntegerField(default=0)),
                ('sent_count', models.PositiveIntegerField(default=0)),
                ('failed_count', models.PositiveIntegerField(default=0)),
                ('skipped_count', models.PositiveIntegerField(default=0)),
                ('total_segments', models.PositiveIntegerField(default=0, help_text='SMS segments successfully sent (what the provider bills).')),
                ('also_in_app', models.BooleanField(default=False, help_text='A bell notification was also posted for each customer.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_activity_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sms_announcements', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='SMSAnnouncementRecipient',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('display_name', models.CharField(blank=True, max_length=150)),
                ('phone_number', models.CharField(blank=True, max_length=20)),
                ('message', models.TextField(blank=True, help_text='Final personalised text for this customer.')),
                ('segments', models.PositiveSmallIntegerField(default=1)),
                ('status', models.CharField(choices=[
                    ('PENDING', 'Pending'), ('SENT', 'Sent'), ('FAILED', 'Failed'), ('SKIPPED', 'Skipped')],
                    db_index=True, default='PENDING', max_length=10)),
                ('provider_status', models.CharField(blank=True, max_length=40)),
                ('error_message', models.TextField(blank=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('announcement', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recipients', to='sms_integration.smsannouncement')),
                ('owner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='received_sms_announcements', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['display_name', 'id']},
        ),
    ]
