from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('accounts', '0009_manager_assistant_branch_scope')]
    operations = [
        migrations.CreateModel(
            name='ManagerAssistantAudit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(max_length=32, choices=[('CREATED','Created'),('PERMISSIONS_CHANGED','Permissions changed'),('BRANCH_SCOPE_CHANGED','Branch scope changed'),('ACTIVATED','Activated'),('DEACTIVATED','Deactivated')])),
                ('details', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='manager_assistant_audit_events', to=settings.AUTH_USER_MODEL)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='audit_events', to='accounts.managerassistant')),
            ],
            options={'ordering':['-created_at']},
        ),
        migrations.AddIndex(model_name='managerassistantaudit', index=models.Index(fields=['profile','-created_at'], name='accounts_ma_profile_2cbb0b_idx')),
        migrations.AddIndex(model_name='managerassistantaudit', index=models.Index(fields=['actor','-created_at'], name='accounts_ma_actor_9d2db7_idx')),
    ]
