from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('sms_integration', '0009_announcement_assistant_managers'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(model_name='smsdebtpackage', name='custom_owner', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='custom_smsdebtpackage_packages', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='smsdebtpackage', name='is_custom', field=models.BooleanField(db_index=True, default=False)),
        migrations.AddField(model_name='smsreceiptpackage', name='custom_owner', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='custom_smsreceiptpackage_packages', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='smsreceiptpackage', name='is_custom', field=models.BooleanField(db_index=True, default=False)),
        migrations.AddField(model_name='smsmarketingpackage', name='custom_owner', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='custom_smsmarketingpackage_packages', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='smsmarketingpackage', name='is_custom', field=models.BooleanField(db_index=True, default=False)),
    ]
