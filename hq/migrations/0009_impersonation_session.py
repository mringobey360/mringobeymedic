import django.utils.timezone
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('hq', '0008_signuprequest_business_type'),
    ]
    operations = [
        migrations.CreateModel(
            name='ImpersonationSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('admin_username', models.CharField(max_length=150)),
                ('target_username', models.CharField(max_length=150)),
                ('pharmacy_id_snapshot', models.PositiveIntegerField(blank=True, null=True)),
                ('pharmacy_name', models.CharField(blank=True, max_length=150)),
                ('mode', models.CharField(choices=[('VIEW', 'View only'), ('FULL', 'Full access')], default='VIEW', max_length=4)),
                ('reason', models.CharField(choices=[('SUPPORT', 'Customer support request'), ('INSPECTION', 'Routine inspection / review'), ('BILLING', 'Subscription / billing check'), ('TROUBLESHOOT', 'Troubleshooting a problem'), ('OTHER', 'Other')], default='SUPPORT', max_length=14)),
                ('note', models.CharField(blank=True, max_length=300)),
                ('started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('end_cause', models.CharField(blank=True, max_length=12)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.CharField(blank=True, max_length=300)),
                ('admin', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='impersonations_started', to=settings.AUTH_USER_MODEL)),
                ('target', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='impersonated_sessions', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-started_at']},
        ),
    ]
