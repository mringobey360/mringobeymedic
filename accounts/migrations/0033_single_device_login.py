# Single-device sign-in: one active device per account.
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0032_accountdeletionrequest'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name='user',
            name='allow_multiple_devices',
            field=models.BooleanField(default=False, help_text='Exempt this account from the one-device-at-a-time sign-in rule.'),
        ),
        migrations.CreateModel(
            name='ActiveDeviceSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('device_hash', models.CharField(db_index=True, max_length=64)),
                ('session_key', models.CharField(blank=True, db_index=True, max_length=40)),
                ('label', models.CharField(blank=True, max_length=120)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_seen', models.DateTimeField(default=django.utils.timezone.now)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='active_device_session', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='DeviceSessionAudit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[('BLOCKED', 'Sign-in blocked (active on another device)'), ('RELEASED_BY_MANAGER', 'Released by manager'), ('RELEASED_BY_RECOVERY', 'Released by password recovery'), ('SUPERSEDED', 'Previous device signed out (took over after idle)')], max_length=32)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.CharField(blank=True, max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='device_session_actions', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='device_session_audits', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='devicesessionaudit',
            index=models.Index(fields=['user', '-created_at'], name='accounts_dsa_user_created_idx'),
        ),
    ]
