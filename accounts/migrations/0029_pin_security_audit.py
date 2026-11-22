from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('accounts', '0028_trusteddevice_cleanup')]

    operations = [
        migrations.CreateModel(
            name='PinSecurityAudit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[
                    ('MANAGER_RESET', 'Manager reset'),
                    ('ACCOUNT_RECOVERY', 'Account recovery reset'),
                    ('LOCKED', 'PIN locked'),
                    ('CHANGED', 'PIN changed'),
                ], max_length=32)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.CharField(blank=True, max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='pin_security_actions', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='pin_security_audits', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['user', '-created_at'], name='accounts_pin_user_id_5f1c3a_idx'),
                    models.Index(fields=['action', '-created_at'], name='accounts_pin_action_1d9c34_idx'),
                ],
            },
        )
    ]
