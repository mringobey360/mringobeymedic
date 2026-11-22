from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):
    dependencies = [('daily_reports', '0001_initial')]
    operations = [
        migrations.CreateModel(
            name='DailyReportPackage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=30, unique=True)),
                ('name', models.CharField(max_length=100)),
                ('price', models.DecimalField(decimal_places=2, max_digits=12)),
                ('report_sms_credits', models.PositiveIntegerField(help_text='Number of Intelligent Report SMS deliveries included.')),
                ('duration_days', models.PositiveIntegerField(default=30)),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['sort_order', 'price', 'id']},
        ),
        migrations.CreateModel(
            name='DailyReportSubscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('report_sms_credits', models.PositiveIntegerField(default=0)),
                ('report_sms_used', models.PositiveIntegerField(default=0)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('status', models.CharField(choices=[('ACTIVE','Active'),('EXPIRED','Expired'),('CANCELLED','Cancelled')], default='ACTIVE', max_length=12)),
                ('amount_paid', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('payment_reference', models.CharField(blank=True, max_length=150)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='daily_report_subscriptions', to=settings.AUTH_USER_MODEL)),
                ('package', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='subscriptions', to='daily_reports.dailyreportpackage')),
            ],
            options={'ordering': ['-created_at'], 'indexes': [models.Index(fields=['owner','status','end_date'], name='daily_repor_owner_i_9d3c6b_idx')]},
        ),
        migrations.CreateModel(
            name='DailyReportPurchase',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('payment_reference', models.CharField(blank=True, max_length=150)),
                ('status', models.CharField(choices=[('PENDING','Pending Payment Verification'),('APPROVED','Approved'),('REJECTED','Rejected')], default='PENDING', max_length=12)),
                ('customer_note', models.TextField(blank=True)),
                ('admin_note', models.TextField(blank=True)),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='daily_report_purchases', to=settings.AUTH_USER_MODEL)),
                ('package', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchases', to='daily_reports.dailyreportpackage')),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_daily_report_purchases', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-requested_at']},
        ),
    ]
